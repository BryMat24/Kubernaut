# Failure-Mode Playbook Coverage — Design

## Problem

Two independent problems compound each other today:

1. **The playbooks are too shallow to distinguish between related errors.** `FAILURE_MODES.md`
   defines ~27 distinct Kubernetes errors the diagnosis agent must be able to solve. Several of
   the 9 existing playbooks either don't cover their assigned errors in enough depth to
   discriminate between them (e.g. `generic.md` used to treat `CrashLoopBackOff` as a single
   stop-and-conclude string, with no way to tell an OOM-kill from an app crash from a bad probe),
   or don't cover them at all.
2. **All 7 integration test files are broken against the current `DiagnosisAgent` shape.** They
   all build `DiagnosisAgent(llm, tools)` — a 2-argument call from before the diagnosis-agent
   phase-graph rewrite. The current constructor is
   `DiagnosisAgent(llm, investigate_tools, scope_tools, utility_llm)`. Every one of the 9
   existing scenarios fails immediately with `TypeError`, independent of playbook content. This
   has to be fixed before any playbook improvement can be verified against a real cluster.

On top of that, several `FAILURE_MODES.md` errors have no test fixture at all, and one whole
category (Networking) has a fully-detailed playbook (`network.md`) with **zero** integration test
coverage.

## Scope

**Keep the existing 9 playbook categories** (`generic`, `network`, `rbac`, `autoscaling`,
`scheduling`, `resource_governance`, `storage`, `secret_configmap`, `rollout`) — no new
`playbook_id` is added. Every `FAILURE_MODES.md` error is mapped onto one of these 9.

### FAILURE_MODES.md → playbook mapping

| Category | Errors | Playbook | Status |
|---|---|---|---|
| Pod Lifecycle | CrashLoopBackOff, ImagePullBackOff, ErrImagePull, CreateContainerError | `generic.md` | Done this session (decision tree) |
| Pod Lifecycle | CreateContainerConfigError | `secret_configmap.md` | Already covers this well |
| Scheduling | Pending, FailedScheduling, Unschedulable Nodes | `scheduling.md` | Already good |
| Node Health | DiskPressure, MemoryPressure | `scheduling.md` | Already covered (existing fixture) |
| Node Health | PIDPressure | `scheduling.md` | **New branch + fixture** |
| Node Health | Node NotReady, Kubelet Stopped | — | **Skip** (see Out of scope) |
| Storage | PVC Pending | `storage.md` | Already covered |
| Storage | MountVolume Errors, Multi-Attach Error | `storage.md` | **New branch** (no fixture — needs a 2nd node/PV to reproduce Multi-Attach reliably; documented only) |
| Networking | Service Unreachable, NetworkPolicy Blocked, Ingress Failure | `network.md` | Already detailed — **needs its first ever fixtures/tests** |
| Networking | DNS Failure, LoadBalancer Pending | `network.md` | **New branches + fixtures** |
| Configuration | RBAC Forbidden | `rbac.md` | Already covered |
| Configuration | Invalid Manifest, Failed Admission Webhook | — | **Skip** (see Out of scope) |
| Security | Secret Missing | `secret_configmap.md` | Already covered |
| Security | ServiceAccount Issues | `rbac.md` | Already covered |
| Resource Management | OOMKilled, CPU Throttling | `resource_governance.md` | **New branch + fixtures** (currently only covers quota-blocked creation) |
| Autoscaling | HPA Not Scaling | `autoscaling.md` | Already covered |
| Autoscaling | Cluster Autoscaler Not Scaling | — | **Skip** (see Out of scope) |

### Out of scope (documented as a playbook limitation, no fixture)

- **Node NotReady** — injecting `Ready=False` auto-applies the `node.kubernetes.io/not-ready`
  **NoExecute** taint. Unlike the `NoSchedule` taints DiskPressure/MemoryPressure/PIDPressure use
  (which only block new scheduling), a NoExecute taint evicts already-running pods without a
  matching toleration once their grace period elapses. On this single-node `kind` cluster that
  risks cascading into core addons. Not worth the risk for a test fixture.
- **Kubelet Stopped** — same risk class as above (real kubelet stoppage on the only node), and
  produces the same observable signature as Node NotReady anyway.
- **Cluster Autoscaler Not Scaling** — no real cluster-autoscaler exists on a fixed-size `kind`
  cluster; nothing to diagnose against.
- **Invalid Manifest / Failed Admission Webhook** — both are apply-time rejections: the object
  never gets persisted, so there is nothing for a live-cluster, read-only investigation tool set
  (`get_events`/`describe_resource`/`list_resources`) to find. Doesn't fit this agent's
  investigate-after-the-fact model. Failed Admission Webhook would additionally require deploying
  a real (deliberately broken) webhook server just to simulate — disproportionate cost.
- **Storage: Multi-Attach Error** — reliably reproducing requires a second real node plus a
  ReadWriteOnce volume actually attached elsewhere; not practical on a single-node cluster. The
  playbook branch is written from Kubernetes semantics directly (documented, not fixture-backed).

## Test infrastructure fix (prerequisite for everything else)

All 7 files (`test_autoscaling.py`, `test_rbac.py`, `test_resource_governance.py`,
`test_rollout_failure.py`, `test_scheduling_failure.py`, `test_secret_configmap_failure.py`,
`test_storage_failure.py`) share one broken `kubernetes_agent` fixture pattern. Fix it in each to
mirror the real production wiring in `graph/builder.py::init_diagnosis_agent()`:

- Pull `k8s` + `promql` + `loki` MCP tools (not just k8s).
- Split into `scope_tools`/`investigate_tools` via the same `SCOPE_TOOL_NAMES` set
  `graph/builder.py` uses.
- Construct `DiagnosisAgent(llm=..., investigate_tools=all_tools, scope_tools=scope_tools,
  utility_llm=...)`.
- Fix `.ainvoke({...})` calls to the real `DiagnosisAgentState` shape: `{"query": ..., "messages":
  []}` (drop the stale `iteration_count`, which isn't a `DiagnosisAgentState` field at all).

This fix is identical across all 7 files — one shared pattern, applied per file (each file starts
its own `promql`/`loki` MCP server subprocess alongside the existing k8s one, following the same
`mcp_server` fixture style already in each file).

## New scenario fixtures (12)

Each follows the existing convention: `test/integration_test/cases/<category>/<scenario-id>/{manifest.yaml,expected_answer.json}`, applied via `kubectl apply -f manifest.yaml -n <ephemeral-namespace>`, torn down via namespace delete. All use plain built-in images (`busybox`, `nginx`) — no custom image builds, matching every existing fixture.

| Scenario ID | Category/file | Technique |
|---|---|---|
| `crashloopbackoff-app-error` | generic / `test_pod_lifecycle.py` | busybox pod, `command: sh -c "echo boom; exit 1"` — deterministic immediate crash loop |
| `imagepullbackoff-bad-tag` | generic / `test_pod_lifecycle.py` | Pod referencing `busybox:this-tag-does-not-exist-v99` |
| `createcontainererror-bad-entrypoint` | generic / `test_pod_lifecycle.py` | Pod `command: ["/nonexistent-binary"]` |
| `node-pid-pressure` | scheduling / `test_scheduling_failure.py` | Same `kubectl patch node --subresource=status` + background re-patch pattern as `node-disk-memory-pressure`, but `PIDPressure=True` (auto-applies `NoSchedule` taint, safe) |
| `oomkilled-low-memory-limit` | resource_governance / `test_resource_governance.py` | busybox pod, `resources.limits.memory: 16Mi`, `command: dd if=/dev/zero of=/dev/shm/fill bs=1M count=200` — deterministic OOMKill |
| `cpu-throttling-low-cpu-limit` | resource_governance / `test_resource_governance.py` | busybox pod, `resources.limits.cpu: 50m`, `command: sh -c "while true; do :; done"` — deterministic throttling, read via `cpu_saturation` |
| `service-selector-mismatch` | network / `test_network.py` (new) | Service `selector` doesn't match any Pod's labels |
| `networkpolicy-deny-ingress` | network / `test_network.py` (new) | NetworkPolicy denying ingress to a workload from another pod |
| `ingress-wrong-backend-port` | network / `test_network.py` (new) | Ingress `backend.service.port` doesn't match the Service's actual port (detectable via static inspection, no ingress controller required) |
| `dns-nonexistent-service` | network / `test_network.py` (new) | Client pod's target hostname resolves to a Service that doesn't exist |
| `loadbalancer-pending-no-controller` | network / `test_network.py` (new) | `type: LoadBalancer` Service, stuck `Pending` (no cloud LB controller on `kind`) |

`test_network.py` is entirely new — `network.md` currently has no integration coverage despite
being the most detailed playbook.

## Playbook content changes (beyond `generic.md`, already done)

- `scheduling.md` — add a PID Pressure branch (mirrors the existing Disk/Memory Pressure branch);
  add a one-line documented-limitation note for Node NotReady/Kubelet Stopped.
- `resource_governance.md` — add a second decision branch for runtime OOMKilled/CPU Throttling
  (via `oom_killed_pods`/`cpu_saturation`/`describe_resource` lastState), alongside the existing
  quota-blocked-creation branch. Retitle section headers so both branches are clearly separate
  mechanisms.
- `storage.md` — add a documented (non-fixture-backed) Multi-Attach Error branch.
- `network.md` — add DNS Failure and LoadBalancer Pending branches to the existing 9-step
  checklist/Conclusion/Do-not-conclude structure.
- `autoscaling.md` — add a one-line documented-limitation note for Cluster Autoscaler Not Scaling.
- `rbac.md`, `secret_configmap.md`, `rollout.md` — no content change needed.

## Verification

- `pytest` (unit, default) must stay green throughout (`pytest.ini`'s `-m "not integration"`
  default excludes all of the above).
- Each new/fixed integration test is run explicitly (`pytest test/integration_test/<file> -m
  integration`) against the real `kind` cluster + real LLM calls, one file at a time, before being
  considered done — these are not free to run, so each is verified once per task, not repeatedly.
- Node-condition-patching fixtures (PIDPressure) must reuse the exact teardown pattern already
  proven safe in `node-disk-memory-pressure` (`finally:` block reverting the condition, background
  re-patch thread stopped on teardown).
