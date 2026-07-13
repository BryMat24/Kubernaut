# Integration tests for PLAN.json categories 2-9 (remaining after category_1)

## Context

`test/integration_test/k8s_agent/test_scheduling_failure.py` (category_1, "Scheduling
failures") established a proven pattern: per-scenario `manifest.yaml` + `expected_answer.json`
under `cases/<category>/<scenario-id>/`, pytest fixtures that create an isolated namespace,
apply manifests, invoke the real `KubernetesAgent` against a real MCP server and live `kind`
cluster, grade the answer via `ScenarioEvaluator` (LLM judge), and clean up afterward. That
category needed three real bugs fixed only discoverable by actually running it (subprocess
`python` vs `sys.executable`, judge model unreliability, kubelet reconciling away an injected
node condition before the agent could observe it) plus one prompt fix (the agent not knowing
`get_node_conditions` existed). This spec extends that pattern to the remaining categories.

Checked live cluster capabilities before designing: metrics-server is installed (category_4 is
fully buildable), a StorageClass exists (category_2 is buildable). The CNI (`kindnet`) does not
enforce NetworkPolicy, and no ingress controller is installed — category_5 (NetworkPolicy) and
category_9 (Ingress) are out of scope for this spec, left as documented gaps like
`pending-nodeselector-mismatch` already is, rather than requiring cluster infrastructure changes
these tests shouldn't make unprompted.

Scope: the 7 "eval"/"eval+unit" priority scenarios across categories 2, 3, 4, 6, 7, 8 per
`PLAN.json`. `limitrange-rejection` (category_3, priority "unit") is out of scope — it needs a
mocked pytest test, not an integration test, and is a separate piece of work.

## Shared conventions (proven in category_1, reused as-is)

- Pod name `sample-app` in every scenario — no hints from naming (per the earlier fix to
  `oversized-pod`/`untolerated-pod`/`affected-workload`).
- Namespace per scenario: `test-<scenario-id>`, created/deleted per-test via a pytest fixture;
  any cluster-scoped mutation (taints, ClusterRoleBindings, node conditions) gets its own
  explicit revert in the fixture's `finally` block, since namespace deletion alone doesn't
  undo those.
- Query sent to the agent is deliberately vague and answer-neutral: "The workload `sample-app`
  in namespace `{namespace}` is not working as expected. Diagnose the root cause." — reused
  verbatim across scenarios where a single named workload is the entry point.
- Grading: `evaluator.ScenarioEvaluator`, session-scoped `judge` fixture using
  `openai/gpt-5.3-codex` (the model that proved reliable for structured-output grading in
  category_1; `qwen/qwen3-coder-next`, used for the agent itself, was not reliable as a judge).
- `mcp_server` and `kubernetes_agent` fixtures are copied verbatim per test file (session-scoped,
  starts `mcp_servers/k8s_mcp_server/server.py` via `sys.executable`, polls `get_mcp_tools()`
  until reachable) — matching category_1 exactly, not factored into a shared conftest for this
  pass, to keep each category's test file self-contained and independently runnable, same as
  `test_scheduling_failure.py` is today.
- Standing rule for this whole effort: when a scenario's eval fails during verification, the
  fix is refining `agents/kubernetes_agent.py`'s `SYSTEM_PROMPT` or the relevant tool's
  docstring in `mcp_servers/k8s_mcp_server/k8s_tools.py` — never loosening `expected_answer.json`
  or the test assertion itself, unless the scenario's own manifest/fixture turns out to not
  actually reproduce the intended failure (as happened with the node-pressure fixture ordering
  in category_1 — that's a scenario-design bug, not an eval being "too strict").

## Directory / file layout

Renaming the existing empty stub directories to match `PLAN.json` category names (also fixing
the `storage_failture` typo), and adding `rollout_failures/` (no existing stub for category_7):

| PLAN.json category | `cases/` folder | Test file |
|---|---|---|
| 2 Storage failures | `storage_failures/` | `test_storage_failure.py` |
| 3 Resource governance | `resource_governance/` | `test_resource_governance.py` |
| 4 Autoscaling | `autoscaling/` | `test_autoscaling.py` |
| 6 RBAC / cluster-scoped | `rbac/` | `test_rbac.py` |
| 7 Rollout-specific | `rollout_failures/` | `test_rollout_failure.py` |
| 8 Secret/ConfigMap | `secret_configmap_failures/` | `test_secret_configmap_failure.py` |

Existing unused stubs (`hpa_failure/`, `ingress_routing_failure/`, `network_failure/`,
`rbac_failure/`, `resource_limits_failure/`, `wrong_key_failure/`) are removed as part of the
rename — they're empty, no data loss. `ingress_routing_failure/` and `network_failure/` are
removed without replacement (documented gaps per the infra decision above).

## Per-scenario design

**`pvc-pending-no-storageclass`** (category_2): a PVC with
`storageClassName: nonexistent-storage-class` plus a Pod mounting it. Stays Pending; event
cites the missing StorageClass. `expected_root_cause` centers on the nonexistent StorageClass
name; `should_not_conclude` rules out insufficient resources / image pull / node issues.

**`resourcequota-exhausted`** (category_3): `ResourceQuota{hard: {pods: "1"}}` in the namespace
plus a `Deployment{replicas: 3}` with small resource requests. Only 1 pod gets created; the
`FailedCreate` event lands on the **ReplicaSet**, not any pod — matching PLAN.json's explicit
"tricky: root cause only visible in ReplicaSet events" note. `should_not_conclude` rules out
crashlooping/image-pull explanations for the "missing" replicas.

**`hpa-metrics-unavailable`** (category_4): a Deployment whose container has **no CPU
`resources.requests`**, plus an HPA targeting it. HPA cannot compute utilization % without a
CPU request baseline, so its current-metric shows unknown/missing — reproduces the observable
symptom (HPA stuck, can't read its target metric) without taking down the cluster's actually-
working metrics-server, which PLAN.json's literal "metrics-server unavailable" framing would
require. Flagged explicitly as a deliberate substitution, same observable failure mode.

**`hpa-capped-at-max-replicas`** (category_4): Deployment with real CPU requests, HPA with
`averageUtilization: 1` (i.e. always over target — deterministic without a load generator) and
`maxReplicas: 2`. HPA's own `status.conditions` (`ScalingLimited: True`, `reason:
TooManyReplicas`) is the smoking gun that autoscaling itself is working, just capped.
`should_not_conclude` rules out "autoscaling is broken/misconfigured" as the framing — the
correct framing is a deliberate ceiling.

**`rbac-forbidden-in-app`** (category_6, eval+unit): a bare `ServiceAccount` with zero RBAC
bindings, and a Pod (`bitnami/kubectl` image, `command: ["kubectl", "get", "pods"]`) running
under it. Logs show a `Forbidden` error from the API server. Also adds one unit test to the
existing `test/unit_test/k8s_agent/k8s_tools_test.py` exercising the `CLUSTERROLEBINDING`
cluster-scoped code path (mirroring the existing `CLUSTERROLE` test already there) — the "unit"
half of this scenario's "eval+unit" priority.

**`rollout-complete-but-broken`** (category_7): a Deployment with no readiness/liveness probes
(so Kubernetes considers it Ready immediately, rollout completes normally) running a busybox
loop that logs `FATAL: unable to connect to upstream dependency` continuously. `rollout_status`
correctly reports success — the discriminator this scenario tests is that the agent must not
treat that as proof of health, and must check `get_pod_logs` too.

**`secret-wrong-key-reference`** (category_8): a Secret with key `password`, and a Pod's
`env[].valueFrom.secretKeyRef.key: db_password` (mismatched key name, not a missing Secret).
Produces `CreateContainerConfigError` / "couldn't find key db_password in Secret ...".
`should_not_conclude` explicitly rules out "the Secret doesn't exist" to keep this distinct
from a missing-object scenario.

## Execution plan

1. Rename/create the `cases/` directories and remove unused stubs (mechanical, done directly,
   not delegated).
2. Dispatch one subagent per category (6 subagents, run in parallel) to author that category's
   `manifest.yaml`(s), `expected_answer.json`(s), and test file, given: this spec's per-scenario
   design above, the full content of `test_scheduling_failure.py` as the structural template,
   and the shared-conventions section verbatim. Pure file authoring, no cluster contact — safe
   to parallelize.
3. Sequentially, for each category: run its test file against the live cluster
   (`pytest <file> -m integration -v`), fix any real bugs in the scenario's manifest/fixture,
   and — per the standing rule above — fix genuine agent-reasoning gaps by editing
   `KubernetesAgent`'s system prompt or the relevant tool docstring, re-running until it passes
   or the failure is understood and accepted as a documented finding (as with a prior session's
   node-disk-memory-pressure iteration).
4. After all categories pass (or are resolved), run the full test suite
   (`pytest test/ -q` for unit, `pytest test/integration_test -m integration -v` for all
   integration tests together) and confirm live-cluster cleanup (no leftover namespaces,
   taints, or ClusterRoleBindings).

## Files touched

- `test/integration_test/k8s_agent/cases/{storage_failures,resource_governance,autoscaling,
  rbac,rollout_failures,secret_configmap_failures}/<scenario-id>/{manifest.yaml,
  expected_answer.json}` — new, one pair per scenario (8 manifest+answer pairs across 7
  scenarios; `hpa-metrics-unavailable` and `hpa-capped-at-max-replicas` both live under
  `autoscaling/`).
- `test/integration_test/k8s_agent/test_{storage_failure,resource_governance,autoscaling,
  rbac,rollout_failure,secret_configmap_failure}.py` — new, one per category.
- `test/unit_test/k8s_agent/k8s_tools_test.py` — one new unit test for `CLUSTERROLEBINDING`.
- `agents/kubernetes_agent.py` / `mcp_servers/k8s_mcp_server/k8s_tools.py` — edited only as
  needed to fix genuine agent-reasoning gaps surfaced during verification, per the standing
  rule.
- Removed: the 6 unused empty stub directories listed above.

## Verification

Covered by step 3-4 of the execution plan above: every scenario is actually run against the
live cluster (not just written), cleanup is confirmed after each run, and the full test suite
(unit + all integration) passes together at the end.
