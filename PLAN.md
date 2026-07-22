# Per-Kind Resource Summarizers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the optimization bug in `ISSUE.md`: `list_resources`'s generic 5-field projection
(`_project_resource_summary` — name/namespace/labels/creationTimestamp/ownerReferences) strips
away status/health signals for every kind, forcing the agent into extra follow-up tool calls
(`get_resource`/`describe_resource` per item) to learn things a list-level summary should have
already shown — increasing total loop count and token cost, the opposite of the projection's
purpose. Give the kinds that actually carry a distinct status/health shape their own summarizer;
keep `_project_resource_summary` as the fallback for everything else.

**Architecture:** No new files. Each task adds one or more small, pure `_summarize_<kind>(manifest:
dict) -> dict` functions to `mcp_servers/k8s_mcp_server/k8s_tools.py`, grouped by how similar their
underlying Kubernetes API shape is (workload-controller replica counts, storage phases, etc.). The
final task adds a `kind -> summarizer` dispatch table and wires it into `list_resources`, replacing
the single hard-coded `_project_resource_summary(item)` call with a per-kind lookup that falls back
to it for any kind without a dedicated summarizer. Kinds covered, per the human partner's explicit
list plus additions justified below: `Pod`, `Deployment`, `ReplicaSet`, `StatefulSet`, `DaemonSet`,
`Service`, `NetworkPolicy`, `PersistentVolumeClaim`, `PersistentVolume`, `ConfigMap`, `Secret`,
`ResourceQuota`, `HorizontalPodAutoscaler`, `Job`, `Node`. Kinds left on the generic fallback
(`Ingress`, `StorageClass`, `LimitRange`, `ClusterRole`, `ClusterRoleBinding`): they either carry
no meaningful status subresource beyond identity, or (RBAC list volume) have a different problem
(too many items, not the wrong fields per item) that a per-kind field selection can't fix — noted
in Self-Review, not solved here.

**Tech Stack:** Python, pytest, `unittest.mock` (existing `mock_run` fixture in
`test/unit_test/k8s_agent/k8s_tools_test.py`).

## Global Constraints

- Scope is `mcp_servers/k8s_mcp_server/k8s_tools.py` only. `get_resource`, `describe_resource`,
  `get_events`, `get_pod_logs`/`get_previous_logs`, `get_node_conditions`, `agents/diagnosis_agent.py`,
  `agents/remediation_agent.py`, `agents/planner_agent.py`, and `agents/helpers/compaction.py` are
  all out of scope — do not touch them.
- Every summarizer must be a pure function of one `manifest: dict` argument, returning a `dict`,
  with no side effects and no `subprocess`/`kubectl` calls of its own — `list_resources` already
  did the one `kubectl` call; summarizers only reshape what came back.
- Do not change `list_resources`'s public parameters (`kind`, `namespace`, `label_selector`) or its
  return type (`list[dict]`).
- Every summarizer must degrade gracefully — use `.get(...)` with sensible defaults throughout, so
  a manifest missing an expected field (a real possibility across Kubernetes versions) returns a
  dict with `None`/`{}`/`[]` for that field rather than raising `KeyError`.
- After every task: `test/unit_test/k8s_agent/k8s_tools_test.py` must pass in full (existing tests
  + new ones), and `pytest` (full unit suite) must stay green.
- Follow existing test conventions: `make_completed_process`/`mock_run` for tool-level integration
  tests; call summarizer functions directly (they are plain module-level functions, not
  `@mcp.tool`-decorated, so no special invocation is needed).
- Tasks 1-7 are independent of each other and of Task 8 for authoring (each just adds functions),
  but Task 8 depends on every summarizer from Tasks 1-7 existing — execute in the order below.

---

## Task 1: `Pod` summarizer

**Files:**
- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py` (add functions after `_project_resource_summary`)
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**
- Produces: `_summarize_container_state(state: dict) -> dict`, `_summarize_pod(manifest: dict) -> dict`.

`Pod` is the single most frequently listed/diagnosed kind, and its triage signal (is it crash
looping, and why) lives entirely in `status.containerStatuses`, which the generic projection
drops completely.

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section right after the
`_strip_manifest_noise` section (before the `get_resource` section):

```python
# ------------------------------------------------------------------
# _summarize_pod
# ------------------------------------------------------------------

def test_summarize_container_state_waiting():
    assert k8s_tools._summarize_container_state({"waiting": {"reason": "CrashLoopBackOff"}}) == {
        "status": "waiting",
        "reason": "CrashLoopBackOff",
    }


def test_summarize_container_state_terminated():
    state = {"terminated": {"reason": "OOMKilled", "exitCode": 137}}
    assert k8s_tools._summarize_container_state(state) == {
        "status": "terminated",
        "reason": "OOMKilled",
        "exitCode": 137,
    }


def test_summarize_container_state_running():
    assert k8s_tools._summarize_container_state({"running": {"startedAt": "2026-01-01T00:00:00Z"}}) == {
        "status": "running",
    }


def test_summarize_container_state_unknown_when_empty():
    assert k8s_tools._summarize_container_state({}) == {"status": "unknown"}


def test_summarize_pod_extracts_phase_and_container_health():
    manifest = {
        "metadata": {"name": "backend-6qzrs", "namespace": "dev", "labels": {"app": "backend"}},
        "spec": {"containers": [{"name": "backend", "image": "backend:v2"}]},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "backend",
                    "ready": False,
                    "restartCount": 269,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                }
            ],
        },
    }
    result = k8s_tools._summarize_pod(manifest)
    assert result == {
        "name": "backend-6qzrs",
        "namespace": "dev",
        "labels": {"app": "backend"},
        "ownerReferences": [],
        "phase": "Running",
        "containerStatuses": [
            {"name": "backend", "ready": False, "restartCount": 269, "state": {"status": "waiting", "reason": "CrashLoopBackOff"}}
        ],
    }
    # spec (image, env, resources) is intentionally not part of the pod summary
    assert "spec" not in result


def test_summarize_pod_defaults_missing_fields():
    assert k8s_tools._summarize_pod({"metadata": {"name": "my-pod"}}) == {
        "name": "my-pod",
        "namespace": None,
        "labels": {},
        "ownerReferences": [],
        "phase": None,
        "containerStatuses": [],
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k "summarize_pod or summarize_container_state"`
Expected: FAIL — `k8s_tools._summarize_pod`/`_summarize_container_state` don't exist yet
(`AttributeError`).

- [ ] **Step 3: Implement**

Add right after `_project_resource_summary` in `mcp_servers/k8s_mcp_server/k8s_tools.py`:

```python
def _summarize_container_state(state: dict) -> dict:
    """Reduce a container's status.state (exactly one of waiting/running/terminated) to
    its phase name plus reason/exitCode, if present."""
    if "waiting" in state:
        return {"status": "waiting", "reason": state["waiting"].get("reason")}
    if "terminated" in state:
        return {
            "status": "terminated",
            "reason": state["terminated"].get("reason"),
            "exitCode": state["terminated"].get("exitCode"),
        }
    if "running" in state:
        return {"status": "running"}
    return {"status": "unknown"}


def _summarize_pod(manifest: dict) -> dict:
    """Pod triage summary: phase plus per-container ready/restartCount/state -- the
    crash-loop/OOM signal the generic identity-only projection drops entirely."""
    metadata = manifest.get("metadata", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "ownerReferences": metadata.get("ownerReferences", []),
        "phase": status.get("phase"),
        "containerStatuses": [
            {
                "name": c.get("name"),
                "ready": c.get("ready"),
                "restartCount": c.get("restartCount"),
                "state": _summarize_container_state(c.get("state", {})),
            }
            for c in status.get("containerStatuses", [])
        ],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "feat: add a Pod-specific resource summarizer (phase, container health)"
```

---

## Task 2: Workload-controller summarizers (`Deployment`, `ReplicaSet`, `StatefulSet`, `DaemonSet`)

**Files:**
- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py`
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**
- Produces: `_summarize_deployment`, `_summarize_replicaset`, `_summarize_statefulset`,
  `_summarize_daemonset` (each `(manifest: dict) -> dict`).

These four kinds share the same underlying question ("how many replicas do I want vs. actually
have, and why") but three different field-naming schemes. `Deployment`'s fields are exactly the
ones named in `ISSUE.md` (`replicas`, `updatedReplicas`, `readyReplicas`, `availableReplicas`,
`unavailableReplicas`, plus `status.conditions` for the `MinimumReplicasUnavailable`-style reason).
`ReplicaSet` is the same shape minus `updatedReplicas`/`conditions`. `StatefulSet` adds
`currentReplicas`/`serviceName`. `DaemonSet` uses an entirely different field-naming scheme
(`desiredNumberScheduled`/`numberReady`/`numberAvailable`/`numberUnavailable`, no `replicas` field
at all).

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section after the `_summarize_pod`
section:

```python
# ------------------------------------------------------------------
# workload-controller summarizers
# ------------------------------------------------------------------

def test_summarize_deployment_extracts_replica_health_and_conditions():
    manifest = {
        "metadata": {"name": "backend-deployment", "namespace": "dev", "labels": {"app": "backend"}},
        "spec": {"replicas": 3},
        "status": {
            "replicas": 3,
            "updatedReplicas": 3,
            "readyReplicas": 1,
            "availableReplicas": 1,
            "unavailableReplicas": 2,
            "conditions": [
                {"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"},
            ],
        },
    }
    result = k8s_tools._summarize_deployment(manifest)
    assert result == {
        "name": "backend-deployment",
        "namespace": "dev",
        "labels": {"app": "backend"},
        "ownerReferences": [],
        "desiredReplicas": 3,
        "replicas": 3,
        "updatedReplicas": 3,
        "readyReplicas": 1,
        "availableReplicas": 1,
        "unavailableReplicas": 2,
        "conditions": [{"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"}],
    }
    assert "spec" not in result


def test_summarize_deployment_defaults_missing_fields():
    result = k8s_tools._summarize_deployment({"metadata": {"name": "d"}})
    assert result["desiredReplicas"] is None
    assert result["conditions"] == []


def test_summarize_replicaset_extracts_replica_health():
    manifest = {
        "metadata": {"name": "backend-57b96fdb87", "namespace": "dev", "labels": {}},
        "spec": {"replicas": 3},
        "status": {"replicas": 3, "readyReplicas": 1, "availableReplicas": 1},
    }
    result = k8s_tools._summarize_replicaset(manifest)
    assert result == {
        "name": "backend-57b96fdb87",
        "namespace": "dev",
        "labels": {},
        "ownerReferences": [],
        "desiredReplicas": 3,
        "replicas": 3,
        "readyReplicas": 1,
        "availableReplicas": 1,
    }


def test_summarize_statefulset_extracts_replica_health_and_service_name():
    manifest = {
        "metadata": {"name": "cache", "namespace": "dev", "labels": {}},
        "spec": {"replicas": 3, "serviceName": "cache-headless"},
        "status": {"replicas": 3, "readyReplicas": 3, "currentReplicas": 3, "updatedReplicas": 3},
    }
    result = k8s_tools._summarize_statefulset(manifest)
    assert result == {
        "name": "cache",
        "namespace": "dev",
        "labels": {},
        "ownerReferences": [],
        "serviceName": "cache-headless",
        "desiredReplicas": 3,
        "replicas": 3,
        "readyReplicas": 3,
        "currentReplicas": 3,
        "updatedReplicas": 3,
    }


def test_summarize_daemonset_extracts_scheduling_health():
    manifest = {
        "metadata": {"name": "log-agent", "namespace": "kube-system", "labels": {}},
        "status": {
            "desiredNumberScheduled": 3,
            "currentNumberScheduled": 3,
            "numberReady": 2,
            "numberAvailable": 2,
            "numberUnavailable": 1,
        },
    }
    result = k8s_tools._summarize_daemonset(manifest)
    assert result == {
        "name": "log-agent",
        "namespace": "kube-system",
        "labels": {},
        "ownerReferences": [],
        "desiredNumberScheduled": 3,
        "currentNumberScheduled": 3,
        "numberReady": 2,
        "numberAvailable": 2,
        "numberUnavailable": 1,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k "summarize_deployment or summarize_replicaset or summarize_statefulset or summarize_daemonset"`
Expected: FAIL — none of the four functions exist yet (`AttributeError`).

- [ ] **Step 3: Implement**

Add after `_summarize_pod`:

```python
def _summarize_deployment(manifest: dict) -> dict:
    """Deployment replica-health summary: desired vs. actual replica counts and the
    Available/Progressing conditions that explain a mismatch (e.g. MinimumReplicasUnavailable)."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "ownerReferences": metadata.get("ownerReferences", []),
        "desiredReplicas": spec.get("replicas"),
        "replicas": status.get("replicas"),
        "updatedReplicas": status.get("updatedReplicas"),
        "readyReplicas": status.get("readyReplicas"),
        "availableReplicas": status.get("availableReplicas"),
        "unavailableReplicas": status.get("unavailableReplicas"),
        "conditions": [
            {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason")}
            for c in status.get("conditions", [])
        ],
    }


def _summarize_replicaset(manifest: dict) -> dict:
    """ReplicaSet replica-health summary -- same idea as Deployment, without conditions."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "ownerReferences": metadata.get("ownerReferences", []),
        "desiredReplicas": spec.get("replicas"),
        "replicas": status.get("replicas"),
        "readyReplicas": status.get("readyReplicas"),
        "availableReplicas": status.get("availableReplicas"),
    }


def _summarize_statefulset(manifest: dict) -> dict:
    """StatefulSet replica-health summary, plus serviceName (the headless Service backing
    stable network identity, a common StatefulSet-specific investigation target)."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "ownerReferences": metadata.get("ownerReferences", []),
        "serviceName": spec.get("serviceName"),
        "desiredReplicas": spec.get("replicas"),
        "replicas": status.get("replicas"),
        "readyReplicas": status.get("readyReplicas"),
        "currentReplicas": status.get("currentReplicas"),
        "updatedReplicas": status.get("updatedReplicas"),
    }


def _summarize_daemonset(manifest: dict) -> dict:
    """DaemonSet scheduling-health summary -- DaemonSet uses its own field-naming scheme
    (desired/current/ready/available/unavailable *Number*Scheduled), not `replicas`."""
    metadata = manifest.get("metadata", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "ownerReferences": metadata.get("ownerReferences", []),
        "desiredNumberScheduled": status.get("desiredNumberScheduled"),
        "currentNumberScheduled": status.get("currentNumberScheduled"),
        "numberReady": status.get("numberReady"),
        "numberAvailable": status.get("numberAvailable"),
        "numberUnavailable": status.get("numberUnavailable"),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "feat: add replica-health summarizers for Deployment/ReplicaSet/StatefulSet/DaemonSet"
```

---

## Task 3: Networking summarizers (`Service`, `NetworkPolicy`)

**Files:**
- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py`
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**
- Produces: `_summarize_service`, `_summarize_networkpolicy` (each `(manifest: dict) -> dict`).

`Service`'s `spec.selector` is the single most common source of "service has no backing pods" bugs
(label-selector mismatch with the target Pods — already called out in `check_service_connectivity`'s
own docstring), and `status.loadBalancer` shows whether an external IP was actually provisioned.
`NetworkPolicy` is explicitly named in `DiagnosisAgent`'s own `SYSTEM_PROMPT` as a governance object
worth inspecting directly when evidence points at it — its `podSelector`/`policyTypes`/rules are
what determine whether it's actually blocking the traffic in question.

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section after the workload-controller
summarizers section:

```python
# ------------------------------------------------------------------
# networking summarizers
# ------------------------------------------------------------------

def test_summarize_service_extracts_type_selector_ports_and_load_balancer():
    manifest = {
        "metadata": {"name": "backend-service", "namespace": "dev", "labels": {}},
        "spec": {
            "type": "ClusterIP",
            "clusterIP": "10.96.0.5",
            "selector": {"app": "backend"},
            "ports": [{"port": 80, "targetPort": 8000, "protocol": "TCP"}],
        },
        "status": {"loadBalancer": {}},
    }
    result = k8s_tools._summarize_service(manifest)
    assert result == {
        "name": "backend-service",
        "namespace": "dev",
        "labels": {},
        "type": "ClusterIP",
        "clusterIP": "10.96.0.5",
        "selector": {"app": "backend"},
        "ports": [{"port": 80, "targetPort": 8000, "protocol": "TCP"}],
        "loadBalancer": {},
    }


def test_summarize_service_defaults_missing_fields():
    result = k8s_tools._summarize_service({"metadata": {"name": "s"}})
    assert result["selector"] == {}
    assert result["ports"] == []
    assert result["loadBalancer"] == {}


def test_summarize_networkpolicy_extracts_selector_types_and_rules():
    manifest = {
        "metadata": {"name": "deny-all-except-frontend", "namespace": "dev", "labels": {}},
        "spec": {
            "podSelector": {"matchLabels": {"app": "backend"}},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": "frontend"}}}]}],
        },
    }
    result = k8s_tools._summarize_networkpolicy(manifest)
    assert result == {
        "name": "deny-all-except-frontend",
        "namespace": "dev",
        "labels": {},
        "podSelector": {"matchLabels": {"app": "backend"}},
        "policyTypes": ["Ingress"],
        "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": "frontend"}}}]}],
        "egress": [],
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k "summarize_service or summarize_networkpolicy"`
Expected: FAIL — neither function exists yet (`AttributeError`).

- [ ] **Step 3: Implement**

Add after `_summarize_daemonset`:

```python
def _summarize_service(manifest: dict) -> dict:
    """Service summary: type, selector (the #1 source of "no backing pods" bugs), ports,
    and whether a load balancer IP was actually provisioned."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "type": spec.get("type"),
        "clusterIP": spec.get("clusterIP"),
        "selector": spec.get("selector", {}),
        "ports": [
            {"port": p.get("port"), "targetPort": p.get("targetPort"), "protocol": p.get("protocol")}
            for p in spec.get("ports", [])
        ],
        "loadBalancer": status.get("loadBalancer", {}),
    }


def _summarize_networkpolicy(manifest: dict) -> dict:
    """NetworkPolicy summary: podSelector, policyTypes, and the actual ingress/egress
    rules -- without these, an agent can't tell whether the policy blocks the traffic
    it's investigating."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "podSelector": spec.get("podSelector", {}),
        "policyTypes": spec.get("policyTypes", []),
        "ingress": spec.get("ingress", []),
        "egress": spec.get("egress", []),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "feat: add Service and NetworkPolicy resource summarizers"
```

---

## Task 4: Storage summarizers (`PersistentVolumeClaim`, `PersistentVolume`)

**Files:**
- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py`
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**
- Produces: `_summarize_pvc`, `_summarize_pv` (each `(manifest: dict) -> dict`).

Both are the "volumes" example from `ISSUE.md`. `status.phase` (`Pending`/`Bound`/`Lost` for PVC;
`Available`/`Bound`/`Released`/`Failed` for PV) is the single most important field for either —
without it, diagnosing "pod stuck Pending because its PVC never bound" requires a follow-up
`get_resource` call the summary should have avoided. `PersistentVolume` is cluster-scoped (no
`namespace` on its metadata) and additionally carries `claimRef` (which PVC, if any, it's bound to).

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section after the networking summarizers
section:

```python
# ------------------------------------------------------------------
# storage summarizers
# ------------------------------------------------------------------

def test_summarize_pvc_extracts_phase_and_storage_request():
    manifest = {
        "metadata": {"name": "backend-data", "namespace": "dev", "labels": {}},
        "spec": {
            "storageClassName": "standard",
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "10Gi"}},
        },
        "status": {"phase": "Pending", "capacity": {}},
    }
    result = k8s_tools._summarize_pvc(manifest)
    assert result == {
        "name": "backend-data",
        "namespace": "dev",
        "labels": {},
        "phase": "Pending",
        "storageClassName": "standard",
        "accessModes": ["ReadWriteOnce"],
        "requestedStorage": "10Gi",
        "capacity": {},
    }


def test_summarize_pvc_defaults_missing_fields():
    result = k8s_tools._summarize_pvc({"metadata": {"name": "p"}})
    assert result["phase"] is None
    assert result["requestedStorage"] is None
    assert result["accessModes"] == []


def test_summarize_pv_extracts_phase_capacity_and_claim_ref():
    manifest = {
        "metadata": {"name": "pv-0001", "labels": {}},
        "spec": {
            "capacity": {"storage": "10Gi"},
            "storageClassName": "standard",
            "persistentVolumeReclaimPolicy": "Delete",
            "claimRef": {"namespace": "dev", "name": "backend-data"},
        },
        "status": {"phase": "Bound"},
    }
    result = k8s_tools._summarize_pv(manifest)
    assert result == {
        "name": "pv-0001",
        "labels": {},
        "phase": "Bound",
        "capacity": {"storage": "10Gi"},
        "storageClassName": "standard",
        "reclaimPolicy": "Delete",
        "claimRef": {"namespace": "dev", "name": "backend-data"},
    }


def test_summarize_pv_defaults_missing_claim_ref():
    result = k8s_tools._summarize_pv({"metadata": {"name": "pv-0002"}})
    assert result["claimRef"] == {"namespace": None, "name": None}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k "summarize_pvc or summarize_pv"`
Expected: FAIL — neither function exists yet (`AttributeError`).

- [ ] **Step 3: Implement**

Add after `_summarize_networkpolicy`:

```python
def _summarize_pvc(manifest: dict) -> dict:
    """PersistentVolumeClaim summary: binding phase and the storage actually requested --
    the key signal for diagnosing a pod stuck Pending on an unbound claim."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "phase": status.get("phase"),
        "storageClassName": spec.get("storageClassName"),
        "accessModes": spec.get("accessModes", []),
        "requestedStorage": spec.get("resources", {}).get("requests", {}).get("storage"),
        "capacity": status.get("capacity", {}),
    }


def _summarize_pv(manifest: dict) -> dict:
    """PersistentVolume summary: binding phase, capacity, and which claim (if any) it's
    bound to. Cluster-scoped -- no namespace on its own metadata."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    status = manifest.get("status", {})
    claim_ref = spec.get("claimRef") or {}
    return {
        "name": metadata.get("name"),
        "labels": metadata.get("labels", {}),
        "phase": status.get("phase"),
        "capacity": spec.get("capacity", {}),
        "storageClassName": spec.get("storageClassName"),
        "reclaimPolicy": spec.get("persistentVolumeReclaimPolicy"),
        "claimRef": {"namespace": claim_ref.get("namespace"), "name": claim_ref.get("name")},
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "feat: add PersistentVolumeClaim and PersistentVolume resource summarizers"
```

---

## Task 5: Config/limits summarizers (`ConfigMap`, `Secret`, `ResourceQuota`)

**Files:**
- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py`
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**
- Produces: `_summarize_configmap`, `_summarize_secret`, `_summarize_resourcequota` (each
  `(manifest: dict) -> dict`).

`ConfigMap` has no status subresource; its diagnostic value is entirely in which keys exist and
what they contain (e.g. this system's own `DOWNSTREAM_URL` env-var-from-configmap pattern), so its
summary includes `data`, value-truncated defensively so one oversized config value can't blow up
the summary the way a raw manifest field could. **`Secret` deliberately never includes values** —
only key names and `type` — `data` values are base64-encoded but trivially decodable, and an
agent's job is to diagnose, not to have secret material pass through its context/logs; this is a
security-hygiene fix as much as a token-cost one. `ResourceQuota` has a genuine `status.used` vs.
`status.hard` comparison — the direct signal for "pod creation blocked by quota," not covered by
any other tool.

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section after the storage summarizers
section:

```python
# ------------------------------------------------------------------
# config/limits summarizers
# ------------------------------------------------------------------

def test_summarize_configmap_extracts_data_with_value_truncation():
    manifest = {
        "metadata": {"name": "backend-config", "namespace": "dev", "labels": {}},
        "data": {"DOWNSTREAM_URL": "http://cache-service:6379", "BIG": "x" * 300},
    }
    result = k8s_tools._summarize_configmap(manifest)
    assert result["dataKeys"] == ["DOWNSTREAM_URL", "BIG"]
    assert result["data"]["DOWNSTREAM_URL"] == "http://cache-service:6379"
    assert result["data"]["BIG"] == "x" * 200 + "...[truncated]"


def test_summarize_configmap_defaults_missing_data():
    result = k8s_tools._summarize_configmap({"metadata": {"name": "c"}})
    assert result["dataKeys"] == []
    assert result["data"] == {}


def test_summarize_secret_never_includes_values():
    manifest = {
        "metadata": {"name": "backend-tls", "namespace": "dev", "labels": {}},
        "type": "kubernetes.io/tls",
        "data": {"tls.crt": "base64stuff==", "tls.key": "base64secret=="},
    }
    result = k8s_tools._summarize_secret(manifest)
    assert result == {
        "name": "backend-tls",
        "namespace": "dev",
        "labels": {},
        "type": "kubernetes.io/tls",
        "dataKeys": ["tls.crt", "tls.key"],
    }
    assert "data" not in result
    assert "base64secret==" not in str(result)


def test_summarize_resourcequota_extracts_used_vs_hard():
    manifest = {
        "metadata": {"name": "dev-quota", "namespace": "dev", "labels": {}},
        "status": {
            "hard": {"pods": "10", "requests.cpu": "4"},
            "used": {"pods": "10", "requests.cpu": "2"},
        },
    }
    result = k8s_tools._summarize_resourcequota(manifest)
    assert result == {
        "name": "dev-quota",
        "namespace": "dev",
        "labels": {},
        "hard": {"pods": "10", "requests.cpu": "4"},
        "used": {"pods": "10", "requests.cpu": "2"},
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k "summarize_configmap or summarize_secret or summarize_resourcequota"`
Expected: FAIL — none of the three functions exist yet (`AttributeError`).

- [ ] **Step 3: Implement**

Add after `_summarize_pv`:

```python
def _summarize_configmap(manifest: dict) -> dict:
    """ConfigMap summary: every key, with each value truncated defensively at 200
    characters -- ConfigMap has no status subresource, so its content IS the summary."""
    metadata = manifest.get("metadata", {})
    data = manifest.get("data") or {}
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "dataKeys": list(data.keys()),
        "data": {
            k: (v if len(v) <= 200 else v[:200] + "...[truncated]")
            for k, v in data.items()
        },
    }


def _summarize_secret(manifest: dict) -> dict:
    """Secret summary: key names and type only -- values are deliberately never
    included. They're base64-encoded but trivially decodable, and there's no
    diagnostic need for an agent to have secret material pass through its context."""
    metadata = manifest.get("metadata", {})
    data = manifest.get("data") or {}
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "type": manifest.get("type"),
        "dataKeys": list(data.keys()),
    }


def _summarize_resourcequota(manifest: dict) -> dict:
    """ResourceQuota summary: used vs. hard limits -- the direct signal for diagnosing
    "pod/object creation blocked by quota", not surfaced by any other tool."""
    metadata = manifest.get("metadata", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "hard": status.get("hard", {}),
        "used": status.get("used", {}),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "feat: add ConfigMap, Secret (keys-only), and ResourceQuota resource summarizers"
```

---

## Task 6: Controller-status summarizers (`HorizontalPodAutoscaler`, `Job`)

**Files:**
- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py`
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**
- Produces: `_summarize_hpa`, `_summarize_job` (each `(manifest: dict) -> dict`).

`HorizontalPodAutoscaler`'s `status.conditions`/`currentReplicas`/`desiredReplicas` are exactly
what `DiagnosisAgent`'s own `SYSTEM_PROMPT` HPA-diagnosis paragraph already instructs investigating
via `describe_resource` — a list-level summary with this status means the agent can often decide
whether an HPA is misbehaving without a `describe_resource` round-trip per HPA. `Job`'s
`status.succeeded`/`failed`/`active` plus `Complete`/`Failed` conditions is the direct pass/fail
signal for batch workloads, analogous to Deployment's replica health for long-running ones.

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section after the config/limits
summarizers section:

```python
# ------------------------------------------------------------------
# controller-status summarizers
# ------------------------------------------------------------------

def test_summarize_hpa_extracts_replica_counts_and_conditions():
    manifest = {
        "metadata": {"name": "backend-hpa", "namespace": "dev", "labels": {}},
        "spec": {
            "scaleTargetRef": {"kind": "Deployment", "name": "backend-deployment"},
            "minReplicas": 1,
            "maxReplicas": 5,
        },
        "status": {
            "currentReplicas": 3,
            "desiredReplicas": 3,
            "conditions": [{"type": "ScalingActive", "status": "False", "reason": "FailedGetResourceMetric"}],
        },
    }
    result = k8s_tools._summarize_hpa(manifest)
    assert result == {
        "name": "backend-hpa",
        "namespace": "dev",
        "labels": {},
        "scaleTargetRef": {"kind": "Deployment", "name": "backend-deployment"},
        "minReplicas": 1,
        "maxReplicas": 5,
        "currentReplicas": 3,
        "desiredReplicas": 3,
        "conditions": [{"type": "ScalingActive", "status": "False", "reason": "FailedGetResourceMetric"}],
    }


def test_summarize_job_extracts_completion_counts_and_conditions():
    manifest = {
        "metadata": {"name": "backup-job", "namespace": "dev", "labels": {}},
        "spec": {"completions": 1, "backoffLimit": 3},
        "status": {
            "active": 0,
            "succeeded": 0,
            "failed": 3,
            "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
        },
    }
    result = k8s_tools._summarize_job(manifest)
    assert result == {
        "name": "backup-job",
        "namespace": "dev",
        "labels": {},
        "ownerReferences": [],
        "completions": 1,
        "backoffLimit": 3,
        "active": 0,
        "succeeded": 0,
        "failed": 3,
        "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
    }


def test_summarize_job_defaults_missing_fields():
    result = k8s_tools._summarize_job({"metadata": {"name": "j"}})
    assert result["active"] is None
    assert result["conditions"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k "summarize_hpa or summarize_job"`
Expected: FAIL — neither function exists yet (`AttributeError`).

- [ ] **Step 3: Implement**

Add after `_summarize_resourcequota`:

```python
def _summarize_hpa(manifest: dict) -> dict:
    """HorizontalPodAutoscaler summary: scale target, min/max bounds, current vs.
    desired replicas, and conditions (AbleToScale/ScalingActive/ScalingLimited) --
    exactly what DiagnosisAgent's own HPA-diagnosis guidance needs."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "scaleTargetRef": spec.get("scaleTargetRef", {}),
        "minReplicas": spec.get("minReplicas"),
        "maxReplicas": spec.get("maxReplicas"),
        "currentReplicas": status.get("currentReplicas"),
        "desiredReplicas": status.get("desiredReplicas"),
        "conditions": [
            {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason")}
            for c in status.get("conditions", [])
        ],
    }


def _summarize_job(manifest: dict) -> dict:
    """Job summary: completion target, active/succeeded/failed counts, and
    Complete/Failed conditions -- the pass/fail signal for a batch workload."""
    metadata = manifest.get("metadata", {})
    spec = manifest.get("spec", {})
    status = manifest.get("status", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "ownerReferences": metadata.get("ownerReferences", []),
        "completions": spec.get("completions"),
        "backoffLimit": spec.get("backoffLimit"),
        "active": status.get("active"),
        "succeeded": status.get("succeeded"),
        "failed": status.get("failed"),
        "conditions": [
            {"type": c.get("type"), "status": c.get("status"), "reason": c.get("reason")}
            for c in status.get("conditions", [])
        ],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "feat: add HorizontalPodAutoscaler and Job resource summarizers"
```

---

## Task 7: `Node` summarizer

**Files:**
- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py`
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**
- Produces: `_summarize_node(manifest: dict) -> dict`.

`list_resources(kind=node)` on a multi-node cluster should let the agent see at a glance which
nodes are `NotReady` without an individual `get_node_conditions` call per node. This is
deliberately a *lighter* summary than `get_node_conditions` itself (which returns full conditions,
taints, and capacity/allocatable for one named node) — `get_node_conditions` remains the tool for
deep-diving the specific node this summary flags as a problem.

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section after the controller-status
summarizers section:

```python
# ------------------------------------------------------------------
# _summarize_node
# ------------------------------------------------------------------

def test_summarize_node_extracts_ready_condition_and_schedulability():
    manifest = {
        "metadata": {"name": "minikube", "labels": {"kubernetes.io/hostname": "minikube"}},
        "spec": {"unschedulable": False},
        "status": {
            "conditions": [
                {"type": "MemoryPressure", "status": "False"},
                {"type": "Ready", "status": "True"},
            ]
        },
    }
    result = k8s_tools._summarize_node(manifest)
    assert result == {
        "name": "minikube",
        "labels": {"kubernetes.io/hostname": "minikube"},
        "ready": "True",
        "unschedulable": False,
    }


def test_summarize_node_defaults_when_no_ready_condition_present():
    result = k8s_tools._summarize_node({"metadata": {"name": "n"}})
    assert result["ready"] is None
    assert result["unschedulable"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k summarize_node`
Expected: FAIL — `k8s_tools._summarize_node` doesn't exist yet (`AttributeError`).

- [ ] **Step 3: Implement**

Add after `_summarize_job`:

```python
def _summarize_node(manifest: dict) -> dict:
    """Node summary: just the Ready condition and schedulability, so listing all nodes
    quickly flags which ones are NotReady. get_node_conditions remains the tool for a
    specific node's full conditions/taints/capacity."""
    metadata = manifest.get("metadata", {})
    status = manifest.get("status", {})
    conditions = status.get("conditions", [])
    ready_condition = next((c for c in conditions if c.get("type") == "Ready"), {})
    return {
        "name": metadata.get("name"),
        "labels": metadata.get("labels", {}),
        "ready": ready_condition.get("status"),
        "unschedulable": manifest.get("spec", {}).get("unschedulable", False),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "feat: add a Node resource summarizer (Ready condition, schedulability)"
```

---

## Task 8: Wire the per-kind dispatch table into `list_resources`

**Files:**
- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py:177-215` (`list_resources`)
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**
- Produces: `_KIND_SUMMARIZERS: dict[ResourceKind, Callable[[dict], dict]]`,
  `_summarize_resource(kind: ResourceKind, manifest: dict) -> dict`.
- Consumes: every summarizer produced by Tasks 1-7, plus the existing `_project_resource_summary`
  as the fallback.

This is the task that actually fixes `ISSUE.md` — until this lands, `list_resources` still calls
the generic `_project_resource_summary` for every kind regardless of the 15 dedicated summarizers
now sitting unused in the module.

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section right before the `list_resources`
section:

```python
# ------------------------------------------------------------------
# _summarize_resource dispatch
# ------------------------------------------------------------------

def test_summarize_resource_dispatches_to_the_kind_specific_summarizer():
    manifest = {
        "metadata": {"name": "backend-deployment", "namespace": "dev", "labels": {}},
        "spec": {"replicas": 3},
        "status": {"replicas": 3, "readyReplicas": 1, "availableReplicas": 1, "unavailableReplicas": 2},
    }
    result = k8s_tools._summarize_resource(ResourceKind.DEPLOYMENT, manifest)
    assert result["readyReplicas"] == 1
    assert "spec" not in result


def test_summarize_resource_falls_back_to_generic_projection_for_unmapped_kinds():
    manifest = {
        "metadata": {"name": "my-role", "labels": {}, "creationTimestamp": "2026-01-01T00:00:00Z"},
        "rules": [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]}],
    }
    result = k8s_tools._summarize_resource(ResourceKind.CLUSTERROLE, manifest)
    assert result == k8s_tools._project_resource_summary(manifest)
    assert "rules" not in result
```

Then, in the existing `list_resources` section, add:

```python
def test_list_resources_uses_pod_summarizer_for_pod_kind(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "items": [
            {
                "metadata": {"name": "backend-6qzrs", "namespace": "dev", "labels": {}},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {"name": "backend", "ready": False, "restartCount": 269, "state": {"waiting": {"reason": "CrashLoopBackOff"}}}
                    ],
                },
            }
        ],
    }))

    result = k8s_tools.list_resources(ResourceKind.POD, "dev")

    assert result[0]["phase"] == "Running"
    assert result[0]["containerStatuses"][0]["state"] == {"status": "waiting", "reason": "CrashLoopBackOff"}


def test_list_resources_falls_back_to_generic_projection_for_unmapped_kinds(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "items": [{"metadata": {"name": "my-role", "labels": {}}, "rules": []}],
    }))

    result = k8s_tools.list_resources(ResourceKind.CLUSTERROLE, "dev")

    assert result == [{"name": "my-role", "namespace": None, "labels": {}, "creationTimestamp": None, "ownerReferences": []}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k "summarize_resource or uses_pod_summarizer or falls_back_to_generic"`
Expected: FAIL — `k8s_tools._summarize_resource`/`_KIND_SUMMARIZERS` don't exist yet
(`AttributeError`), and `list_resources` still returns the generic projection for `Pod`.

- [ ] **Step 3: Implement**

Add right before `list_resources` (after `_summarize_node`):

```python
_KIND_SUMMARIZERS = {
    ResourceKind.POD: _summarize_pod,
    ResourceKind.DEPLOYMENT: _summarize_deployment,
    ResourceKind.REPLICASET: _summarize_replicaset,
    ResourceKind.STATEFULSET: _summarize_statefulset,
    ResourceKind.DAEMONSET: _summarize_daemonset,
    ResourceKind.SERVICE: _summarize_service,
    ResourceKind.NETWORKPOLICY: _summarize_networkpolicy,
    ResourceKind.PERSISTENTVOLUMECLAIM: _summarize_pvc,
    ResourceKind.PERSISTENTVOLUME: _summarize_pv,
    ResourceKind.CONFIGMAP: _summarize_configmap,
    ResourceKind.SECRET: _summarize_secret,
    ResourceKind.RESOURCEQUOTA: _summarize_resourcequota,
    ResourceKind.HORIZONTALPODAUTOSCALER: _summarize_hpa,
    ResourceKind.JOB: _summarize_job,
    ResourceKind.NODE: _summarize_node,
}


def _summarize_resource(kind: ResourceKind, manifest: dict) -> dict:
    """Dispatch to the kind-specific summarizer if one exists, otherwise fall back to
    the generic identity-only projection."""
    summarizer = _KIND_SUMMARIZERS.get(kind, _project_resource_summary)
    return summarizer(manifest)
```

In `list_resources` (`mcp_servers/k8s_mcp_server/k8s_tools.py:177-215`), replace the final two
lines:

```python
    data = json.loads(result.stdout)
    return [_project_resource_summary(item) for item in data.get("items", [])]
```

with:

```python
    data = json.loads(result.stdout)
    return [_summarize_resource(kind, item) for item in data.get("items", [])]
```

Also update `list_resources`'s docstring — replace:

```
    List Kubernetes resources of a given kind, for discovery before inspecting individual
    resources. Returns a minimal identity projection per resource — name, namespace, labels,
    creationTimestamp, ownerReferences — not the full manifest. For spec/status detail (image,
    replicas, conditions) on a specific resource, use describe_resource once you've found it
    here.
```

with:

```
    List Kubernetes resources of a given kind, for discovery before inspecting individual
    resources. Returns a per-kind summary tuned to that kind's health signal (e.g. replica
    counts and conditions for Deployment/StatefulSet/DaemonSet, phase and container state
    for Pod, binding phase for PersistentVolumeClaim/PersistentVolume) rather than the full
    manifest -- for kinds without a dedicated summary, falls back to a minimal identity
    projection (name, namespace, labels, creationTimestamp, ownerReferences). For full
    spec/status detail on one specific resource, use get_resource or describe_resource once
    you've found it here.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS (all tests, including every pre-existing `list_resources` test — none of them use
a kind with a dedicated summarizer in their fixtures, so they still exercise the
`_project_resource_summary` fallback path and pass unchanged).

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "feat: dispatch list_resources to per-kind summarizers, falling back to the generic projection"
```

---

## Self-Review Notes

- **Scope, as agreed:** the human partner's own list (Pod, Deployment, Service, ConfigMap,
  ReplicaSet, StatefulSet, DaemonSet, PVC, Node) is fully covered (Tasks 1, 2, 3, 4, 5, 7). Added,
  with justification tied to real usage in this codebase: `PersistentVolume` (the other half of
  `ISSUE.md`'s "volumes" example, paired with PVC), `HorizontalPodAutoscaler` (directly serves
  `DiagnosisAgent`'s own `SYSTEM_PROMPT` HPA-diagnosis guidance), `Job` (batch-workload pass/fail,
  same shape of problem as Deployment's replica health), `Secret` (parallels `ConfigMap`, and fixes
  a real data-hygiene gap — values are never surfaced), `ResourceQuota` (has genuine
  used-vs-hard status), `NetworkPolicy` (explicitly named in `SYSTEM_PROMPT`'s
  governance-object guidance).
- **Deliberately left on the generic fallback:** `Ingress`, `StorageClass`, `LimitRange` — mostly
  static config with thin-to-no status payoff relative to the effort of a dedicated summarizer.
  `ClusterRole`/`ClusterRoleBinding` — a real trace analyzed for this investigation showed
  `list_resources(kind=clusterrole)` returning ~16k characters even under the *current* 5-field
  projection, because a typical cluster has dozens-to-hundreds of built-in system RBAC objects.
  That's an item-*count* problem (no per-item field selection fixes it — RBAC objects are just
  identity + rules, there's no smaller "health" subset to extract), not a field-*selection*
  problem, so it's out of scope for this plan and noted here rather than silently ignored.
- **Every summarizer was designed from Kubernetes' own stable, well-documented API schema** for
  each kind's `status` shape (these fields have been unchanged across Kubernetes versions for
  years) — not from a single captured example the way `get_resource`'s noise-stripping was, since
  no captured JSON sample exists yet for most of these kinds in this repo.
- Every new function is a pure `dict -> dict` transform with no I/O, matching the Global
  Constraints' "no `subprocess`/`kubectl` calls of its own" rule, and every one degrades via
  `.get(...)` defaults rather than raising on a missing field.
