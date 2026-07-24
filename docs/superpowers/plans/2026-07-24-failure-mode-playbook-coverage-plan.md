# Failure-Mode Playbook Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every FAILURE_MODES.md error diagnosable by one of the 9 existing playbook
categories, fix the broken integration-test harness that currently prevents verifying any of
them against a real cluster, and add fixtures for every error that doesn't have one yet (except
those documented as out of scope).

**Architecture:** No new `playbook_id`/category is added. A new shared
`test/integration_test/conftest.py` centralizes the (currently duplicated-and-broken) agent/MCP-
server test fixtures so the constructor bug is fixed once, not seven times. New scenario
fixtures follow the exact existing convention: `manifest.yaml` + `expected_answer.json` per
scenario directory, applied to an ephemeral namespace on the real `kind` cluster.

**Tech Stack:** pytest (`-m integration`), `kubectl` (real `kind-kind` cluster, single node
`kind-control-plane`), FastMCP servers (k8s/prometheus/loki), `ChatOpenRouter`, real Prometheus
(`prometheus-kube-prometheus-prometheus` svc, `dev-monitoring` ns, port 9090) and Loki (`loki`
svc, `dev-monitoring` ns, port 3100).

## Global Constraints

- Keep the 9 existing playbook categories (`generic`, `network`, `rbac`, `autoscaling`,
  `scheduling`, `resource_governance`, `storage`, `secret_configmap`, `rollout`) — do not add a
  new `playbook_id`.
- `pytest` (unit, default `-m "not integration"`) must stay green after every task.
- Integration tests cost real LLM API calls and require the live cluster — run each new/fixed
  integration test file explicitly once per task (`pytest <file> -m integration`) as
  verification; do not re-run repeatedly.
- Node name for the live cluster is `kind-control-plane` (already used by
  `test_scheduling_failure.py`'s `NODE_NAME` constant).
- All new manifests use only built-in images already present in every existing fixture's style:
  `busybox:1.36`, `nginx:latest`. No custom image builds.
- `expected_answer.json` schema (read by `agents/helpers/scenario_evaluator.py::ScenarioEvaluator.evaluate`):
  `{"scenario_id": str, "expected_root_cause": str, "key_evidence": [str, ...], "should_not_conclude": [str, ...]}`.
- Scenario fixture layout: `test/integration_test/cases/<category>/<scenario-id>/{manifest.yaml,expected_answer.json}`.
- Out of scope, documented only (no fixture, added as a "Limitations" note in the relevant
  playbook): Node NotReady, Kubelet Stopped, Cluster Autoscaler Not Scaling, Invalid Manifest,
  Failed Admission Webhook, Storage Multi-Attach Error (gets a documented Checklist branch but no
  fixture — needs a second real node to reproduce reliably).

---

### Task 1: Shared integration-test fixtures (`conftest.py`)

**Files:**
- Create: `test/integration_test/conftest.py`

**Interfaces:**
- Produces (used by every test file in this directory, pytest auto-discovers `conftest.py` — no
  import needed): fixtures `anyio_backend`, `mcp_servers` (session-scoped, starts k8s+prometheus+
  loki MCP server subprocesses and Prometheus+Loki `kubectl port-forward` subprocesses, tears all
  down at session end), `kubernetes_agent` (session-scoped, depends on `mcp_servers`, builds a
  correctly-constructed `DiagnosisAgent`), `judge` (session-scoped). Also a module-level helper
  `_kubectl(*args, check=True) -> subprocess.CompletedProcess` that test files can import via
  `from conftest import _kubectl` is NOT needed — pytest fixtures are auto-available, but plain
  helper functions are not automatically shared; each test file keeps its own `_kubectl`,
  `_load_expected_answer`, `_apply_manifest` helpers (they're one-liners, not worth centralizing).

- [ ] **Step 1: Write `test/integration_test/conftest.py`**

```python
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

from agents import DiagnosisAgent
from agents import ScenarioEvaluator
from langchain_openrouter import ChatOpenRouter
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools, get_loki_mcp_tools

load_dotenv()

REPO_ROOT = Path(__file__).parents[2]
K8S_MCP_SERVER_DIR = REPO_ROOT / "mcp_servers" / "k8s_mcp_server"
PROMETHEUS_MCP_SERVER_DIR = REPO_ROOT / "mcp_servers" / "prometheus_mcp_server"
LOKI_MCP_SERVER_DIR = REPO_ROOT / "mcp_servers" / "loki_mcp_server"

SCOPE_TOOL_NAMES = {
    "list_namespaces",
    "list_resources",
    "get_events",
    "top_pods",
    "top_nodes",
    "rollout_status",
    "application_health",
    "error_rate",
    "cpu_saturation",
    "error_logs",
}


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


def _start_mcp_server(server_dir: Path, get_tools) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=server_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"MCP server in {server_dir} exited early:\n{output}")
        try:
            asyncio.run(get_tools())
            return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"MCP server in {server_dir} did not become reachable within 20s")


def _start_port_forward(namespace: str, target: str, local_port: int, remote_port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", namespace, target, f"{local_port}:{remote_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"port-forward for {target} exited early:\n{output}")
        try:
            with __import__("socket").create_connection(("localhost", local_port), timeout=1):
                return proc
        except OSError:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"port-forward for {target} did not become reachable within 20s")


@pytest.fixture(scope="session")
def mcp_servers():
    # Prometheus/Loki MCP tools hit http://localhost:9090 / :3100 directly (they run outside
    # the cluster), so the real in-cluster services must be port-forwarded first -- without
    # this, error_rate/cpu_saturation/error_logs/recent_logs would silently return empty
    # results for every scenario that needs them (resource_governance, network).
    procs = [
        _start_port_forward("dev-monitoring", "svc/prometheus-kube-prometheus-prometheus", 9090, 9090),
        _start_port_forward("dev-monitoring", "svc/loki", 3100, 3100),
        _start_mcp_server(K8S_MCP_SERVER_DIR, get_k8s_mcp_tools),
        _start_mcp_server(PROMETHEUS_MCP_SERVER_DIR, get_promql_mcp_tools),
        _start_mcp_server(LOKI_MCP_SERVER_DIR, get_loki_mcp_tools),
    ]
    try:
        yield
    finally:
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def kubernetes_agent(mcp_servers):
    async def _build() -> DiagnosisAgent:
        llm = ChatOpenRouter(
            model="qwen/qwen3-coder-next",
            temperature=0.1,
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        utility_llm = ChatOpenRouter(
            model="qwen/qwen3-coder-next",
            temperature=0.1,
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        k8s_tools = await get_k8s_mcp_tools()
        promql_tools = await get_promql_mcp_tools()
        loki_tools = await get_loki_mcp_tools()
        all_tools = k8s_tools + promql_tools + loki_tools
        scope_tools = [tool for tool in all_tools if tool.name in SCOPE_TOOL_NAMES]
        return DiagnosisAgent(
            llm=llm,
            investigate_tools=all_tools,
            scope_tools=scope_tools,
            utility_llm=utility_llm,
        )

    return asyncio.run(_build())


@pytest.fixture(scope="session")
def judge() -> ScenarioEvaluator:
    llm = ChatOpenRouter(
        model="openai/gpt-5.3-codex",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    return ScenarioEvaluator(llm)
```

Note: the two `ChatOpenRouter` instances (`llm`, `utility_llm`) intentionally use the same model
here (`qwen/qwen3-coder-next`, matching every existing test file's agent model) since the
integration suite has no separate "cheap utility model" requirement the way production
`graph/builder.py` does — the important fix is that `utility_llm` is no longer `None`, not that
it's a different model.

- [ ] **Step 2: Verify `conftest.py` imports cleanly and fixtures are collected**

Run: `pytest test/integration_test/ --collect-only -m integration -q`
Expected: no `TypeError`/`ImportError`/`fixture not found` errors; test IDs from all 7 existing
files listed (they still reference their own now-orphaned `mcp_server`/`kubernetes_agent`/`judge`
fixture definitions until Task 2 removes them — this step only proves `conftest.py` itself is
syntactically and semantically valid, e.g. via `python -m py_compile
test/integration_test/conftest.py`, since collection of the not-yet-migrated files may still show
their own duplicate fixtures shadowing the new ones, which is expected and fixed in Task 2).

- [ ] **Step 3: Commit**

```bash
git add test/integration_test/conftest.py
git commit -m "test: add shared integration-test fixtures with correct DiagnosisAgent wiring"
```

---

### Task 2: Migrate all 7 existing integration test files to the shared fixtures

**Files:**
- Modify: `test/integration_test/test_autoscaling.py`
- Modify: `test/integration_test/test_rbac.py`
- Modify: `test/integration_test/test_resource_governance.py`
- Modify: `test/integration_test/test_rollout_failure.py`
- Modify: `test/integration_test/test_scheduling_failure.py`
- Modify: `test/integration_test/test_secret_configmap_failure.py`
- Modify: `test/integration_test/test_storage_failure.py`

**Interfaces:**
- Consumes: `conftest.py`'s `anyio_backend`, `mcp_servers`, `kubernetes_agent`, `judge` fixtures
  from Task 1 (auto-available, no import).

This is the identical mechanical edit in all 7 files (confirmed byte-identical across every
file):

- [ ] **Step 1: In each of the 7 files, delete this exact block** (the duplicated, broken
  `mcp_server`/`kubernetes_agent`/`judge`/`anyio_backend` fixtures):

```python
@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
```
... (this one stays deleted, `conftest.py` now provides it) ...

then also delete:

```python
@pytest.fixture(scope="session")
def mcp_server():
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=MCP_SERVER_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 20
        reachable = False
        while time.time() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"MCP server exited early:\n{output}")
            try:
                asyncio.run(get_mcp_tools())
                reachable = True
                break
            except Exception:
                time.sleep(0.5)
        if not reachable:
            raise RuntimeError("MCP server did not become reachable within 20s")
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def kubernetes_agent(mcp_server):
    async def _build() -> DiagnosisAgent:
        llm = ChatOpenRouter(
            model="qwen/qwen3-coder-next",
            temperature=0.1,
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        tools = await get_mcp_tools()
        return DiagnosisAgent(llm, tools)

    return asyncio.run(_build())


@pytest.fixture(scope="session")
def judge() -> ScenarioEvaluator:
    llm = ChatOpenRouter(
        model="openai/gpt-5.3-codex",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    return ScenarioEvaluator(llm)
```

Also delete the now-unused module-level constant `MCP_SERVER_DIR = Path(__file__).parents[2] /
"mcp_servers" / "k8s_mcp_server"` in each file, and the now-unused imports: `from agents import
DiagnosisAgent`, `from langchain_openrouter import ChatOpenRouter`, `from
mcp_clients.k8s_client import get_mcp_tools`. Keep `from agents import ScenarioEvaluator`
(the `judge` fixture in `conftest.py` builds it, but if the file has no other reason to import
it directly, remove that import too — check each file: none of the 7 files reference
`ScenarioEvaluator`/`DiagnosisAgent`/`ChatOpenRouter` anywhere outside the deleted block, so all
three imports are safe to remove entirely). Keep `import asyncio`, `import subprocess`, `import
sys`, `import time`, `import os` only if the file's *remaining* code still uses them (e.g.
`test_scheduling_failure.py` and `test_autoscaling.py` keep `subprocess`/`time`/`threading` for
their own node-patching/HPA-polling fixtures; files with only a plain namespace-create/delete
scenario fixture, like `test_rbac.py`, no longer need `subprocess` directly since `_kubectl`
already wraps it — check: `_kubectl` itself is defined per-file and uses `subprocess.run`, so
`import subprocess` stays in every file; `import sys`/`import asyncio` become unused in files
whose only use was the deleted `mcp_server`/`kubernetes_agent` fixtures — remove those two
specifically from files that don't use `asyncio`/`sys` elsewhere).

- [ ] **Step 2: In each of the 7 files, remove the stale `"iteration_count": 0,` line from every
  `.ainvoke({...})` call**

Before:
```python
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"...",
        "iteration_count": 0,
    })
```
After:
```python
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"...",
    })
```
(`test_scheduling_failure.py` has 3 such calls, `test_autoscaling.py` has 2, the other 5 files
have 1 each — 10 total across the suite.)

- [ ] **Step 3: Verify each file collects and runs**

Run per file (this makes real LLM calls and touches the real cluster — run once per file, not
in a loop):
```bash
pytest test/integration_test/test_rbac.py -m integration -v
pytest test/integration_test/test_autoscaling.py -m integration -v
pytest test/integration_test/test_resource_governance.py -m integration -v
pytest test/integration_test/test_rollout_failure.py -m integration -v
pytest test/integration_test/test_scheduling_failure.py -m integration -v
pytest test/integration_test/test_secret_configmap_failure.py -m integration -v
pytest test/integration_test/test_storage_failure.py -m integration -v
```
Expected: all 10 test functions pass (or fail only on judge disagreement about diagnosis
quality — NOT on `TypeError`/`AttributeError`/fixture errors, which would indicate the migration
itself is broken).

- [ ] **Step 4: Run the unit suite to confirm nothing regressed**

Run: `pytest`
Expected: same pass count as before this task (this task touches no unit-tested code paths).

- [ ] **Step 5: Commit**

```bash
git add test/integration_test/test_autoscaling.py test/integration_test/test_rbac.py \
  test/integration_test/test_resource_governance.py test/integration_test/test_rollout_failure.py \
  test/integration_test/test_scheduling_failure.py test/integration_test/test_secret_configmap_failure.py \
  test/integration_test/test_storage_failure.py
git commit -m "test: migrate integration tests to shared conftest fixtures, fix DiagnosisAgent construction"
```

---

### Task 3: `scheduling.md` — PID Pressure branch + Node Health limitations note

**Files:**
- Modify: `agents/playbooks/scheduling.md`

- [ ] **Step 1: Replace the file's full content**

```markdown
---
playbook_id: scheduling
category: scheduling
trigger_conditions:
  - "one or more pods are stuck Pending"
  - "a FailedScheduling event is present"
  - "a node reports NotReady or a pressure condition"
---

# Scheduling / Pending Pod Failure

## Checklist
1. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
2. tool: get_events          params: {namespace}                    conclusive: true
   conclusion_criteria: >
     read the FailedScheduling reason: 'insufficient cpu/memory' → capacity (confirm with
     top_nodes); 'untolerated taint' → a node taint with no matching pod toleration; taints named
     node.kubernetes.io/disk-pressure, memory-pressure, or pid-pressure → node health pressure.
3. tool: get_node_conditions   params: {name}   conclusive: true
   conclusion_criteria: >
     DiskPressure/MemoryPressure/PIDPressure=True explains the matching auto-applied NoSchedule
     taint (node.kubernetes.io/disk-pressure, memory-pressure, pid-pressure respectively); or the
     node's taints field shows the specific untolerated taint. PIDPressure specifically means the
     node is close to exhausting available process IDs (too many processes/threads on the node),
     not disk or memory.
4. tool: top_nodes   conclusive: true
   note: only for the insufficient-resources branch — show allocatable < pod request.

## Conclusion
Name the specific scheduling barrier (insufficient CPU/memory vs. untolerated taint vs. node
pressure — disk, memory, or PID) and the node/pod involved.

## Limitations
If `get_node_conditions` shows Ready=False (Node NotReady), or you suspect the kubelet itself has
stopped reporting: state that the node is NotReady and name it, but do not speculate about *why*
the kubelet stopped — this toolset has no way to inspect kubelet process state, node system logs,
or restart anything. Recommend a human check the node directly.

## Do not conclude
- Image pull failure or CrashLoopBackOff.
- The pod's own resource requests are "misconfigured" when the real cause is a taint/capacity.
- An arbitrary/manual taint unrelated to node health when the taint is pressure-induced.
```

- [ ] **Step 2: Commit**

```bash
git add agents/playbooks/scheduling.md
git commit -m "docs: add PID Pressure branch and Node Health limitations to scheduling playbook"
```

---

### Task 4: `node-pid-pressure` fixture + test

**Files:**
- Create: `test/integration_test/cases/scheduling_failures/node-pid-pressure/manifest.yaml`
- Create: `test/integration_test/cases/scheduling_failures/node-pid-pressure/expected_answer.json`
- Modify: `test/integration_test/test_scheduling_failure.py`

**Interfaces:**
- Consumes: `NODE_NAME`, `_kubectl`, `CASES_DIR`, `_apply_manifest`, `_load_expected_answer`
  (already defined in `test_scheduling_failure.py`); `kubernetes_agent`/`judge` from `conftest.py`.

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/scheduling_failures/node-pid-pressure/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  containers:
    - name: app
      image: nginx:latest
      resources:
        requests:
          cpu: "50m"
          memory: "64Mi"
        limits:
          cpu: "100m"
          memory: "128Mi"
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/scheduling_failures/node-pid-pressure/expected_answer.json`:
```json
{
  "scenario_id": "node-pid-pressure",
  "expected_root_cause": "The node kind-control-plane is reporting a PIDPressure condition as True. Kubernetes automatically applies a NoSchedule taint (node.kubernetes.io/pid-pressure) in response, and the pod has no matching toleration, so it stays Pending. The root cause is the node running low on available process IDs, not the pod's own configuration.",
  "key_evidence": [
    "get_node_conditions shows a PIDPressure condition with status True on kind-control-plane",
    "A FailedScheduling event on the pod, or the node's taints field showing the auto-applied pid-pressure NoSchedule taint",
    "The pod spec has no toleration for the pid-pressure taint"
  ],
  "should_not_conclude": [
    "The pod's own resource requests are misconfigured",
    "An image pull or application-level crash caused the issue",
    "Insufficient CPU or memory capacity (DiskPressure/MemoryPressure) is the cause"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_scheduling_failure.py`**

Add this helper next to the existing `_pressure_conditions_patch`:
```python
def _pid_pressure_condition_patch(pressure: bool) -> str:
    return json.dumps({
        "status": {
            "conditions": [
                {
                    "type": "PIDPressure",
                    "status": "True" if pressure else "False",
                    "reason": "ScenarioInjected" if pressure else "KubeletHasSufficientPID",
                    "message": "Injected by integration test" if pressure else "kubelet has sufficient PID available",
                },
            ]
        }
    })
```

Add this fixture next to `node_disk_memory_pressure_scenario` (same re-patch-daemon pattern,
required for the same reason: the real kubelet overwrites a one-shot patch within ~8s):
```python
@pytest.fixture
def node_pid_pressure_scenario():
    namespace = "test-node-pid-pressure"
    _kubectl("create", "namespace", namespace)

    stop_event = threading.Event()

    def _keep_patching() -> None:
        while not stop_event.is_set():
            _kubectl(
                "patch", "node", NODE_NAME,
                "--subresource=status", "--type=merge",
                "-p", _pid_pressure_condition_patch(pressure=True),
                check=False,
            )
            stop_event.wait(3)

    _kubectl(
        "patch", "node", NODE_NAME,
        "--subresource=status", "--type=merge",
        "-p", _pid_pressure_condition_patch(pressure=True),
    )
    patcher = threading.Thread(target=_keep_patching, daemon=True)
    patcher.start()

    _apply_manifest("node-pid-pressure", namespace)

    try:
        yield namespace
    finally:
        stop_event.set()
        patcher.join(timeout=5)
        _kubectl(
            "patch", "node", NODE_NAME,
            "--subresource=status", "--type=merge",
            "-p", _pid_pressure_condition_patch(pressure=False),
            check=False,
        )
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)
```

Add this test at the end of the file:
```python
@pytest.mark.anyio
async def test_node_pid_pressure(kubernetes_agent, judge, node_pid_pressure_scenario):
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": "Pods are stuck in Pending state and not getting scheduled. Investigate and diagnose.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("node-pid-pressure")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

- [ ] **Step 4: Verify raw cluster behavior cheaply (no LLM cost) before running the full test**

```bash
kubectl create namespace test-node-pid-pressure-manual
kubectl patch node kind-control-plane --subresource=status --type=merge -p '{"status":{"conditions":[{"type":"PIDPressure","status":"True","reason":"ScenarioInjected","message":"manual check"}]}}'
kubectl apply -f test/integration_test/cases/scheduling_failures/node-pid-pressure/manifest.yaml -n test-node-pid-pressure-manual
sleep 5
kubectl get pod sample-app -n test-node-pid-pressure-manual   # expect Pending
kubectl describe node kind-control-plane | grep -A2 pid-pressure   # expect the taint present
kubectl patch node kind-control-plane --subresource=status --type=merge -p '{"status":{"conditions":[{"type":"PIDPressure","status":"False","reason":"KubeletHasSufficientPID","message":"reverted"}]}}'
kubectl delete namespace test-node-pid-pressure-manual --wait=true
```
Expected: pod stays Pending, node shows the `pid-pressure` `NoSchedule` taint while the condition
is True; both revert cleanly.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_scheduling_failure.py::test_node_pid_pressure -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/scheduling_failures/node-pid-pressure test/integration_test/test_scheduling_failure.py
git commit -m "test: add node-pid-pressure scheduling scenario"
```

---

### Task 5: `resource_governance.md` — OOMKilled / CPU Throttling branch

**Files:**
- Modify: `agents/playbooks/resource_governance.md`

- [ ] **Step 1: Replace the file's full content**

```markdown
---
playbook_id: resource_governance
category: resource_governance
trigger_conditions:
  - "a Deployment has fewer ready replicas than desired with no crashing pods"
  - "a FailedCreate / exceeded quota event is present"
  - "a pod was OOMKilled or is being CPU-throttled"
---

# Resource Governance Failure (Quota-Blocked Creation / Runtime OOM & CPU Pressure)

## Branch A: Quota-blocked creation

1. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: a FailedCreate event on the ReplicaSet mentioning 'exceeded quota'.
2. tool: get_resource   params: {kind: RESOURCEQUOTA, name, namespace}   conclusive: true
   conclusion_criteria: hard limit (e.g. pods=1) equals used, blocking further creation.
3. tool: get_resource   params: {kind: DEPLOYMENT, name, namespace}   conclusive: false
   note: desired replicas > ready/available confirms the shortfall is creation-blocked, not crashing.

### Conclusion (Branch A)
root_cause = a ResourceQuota caps a resource (e.g. pods) below what the Deployment requests, so
the ReplicaSet controller cannot create the remaining pods. Name the quota and the shortfall.

## Branch B: Runtime OOMKilled / CPU Throttling

Use this branch when the pod already exists and is Running or restarting — not when it was
never created (that's Branch A).

1. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
   note: check containerStatuses[].lastState.terminated for exitCode/reason, and restartCount.
2. tool: oom_killed_pods   params: {namespace}   conclusive: true
   conclusion_criteria: this pod/container is listed → confirmed OOMKilled. Go to Conclusion (B1).
3. tool: cpu_saturation   params: {app_label}   conclusive: true
   conclusion_criteria: >
     only relevant if the pod is Running (not restarting) but slow/underperforming. A value at or
     near 1 means the container is using all of its CPU limit — confirmed throttling. Go to
     Conclusion (B2).

### Conclusion (Branch B1 — OOMKilled)
root_cause = the container's memory limit is set below what its process actually needs, so the
kernel OOM-kills it (exitCode 137, reason OOMKilled). Name the container and its memory limit.

### Conclusion (Branch B2 — CPU Throttling)
root_cause = the container's CPU limit is set below what its workload actually demands, causing
heavy throttling. Name the container and its CPU limit. This is a performance issue, not a crash.

## Do not conclude
- The missing pods are crashlooping or failing health checks when they were never created (that's
  Branch A's mechanism, not Branch B's).
- Image pull failure.
- Insufficient node CPU or memory capacity (Branch A/B are about the container's own
  requests/limits, not node-level capacity — that's scheduling.md's territory).
- An application bug causing high CPU/memory usage when the limit itself is the constraint.
```

- [ ] **Step 2: Commit**

```bash
git add agents/playbooks/resource_governance.md
git commit -m "docs: add runtime OOMKilled/CPU Throttling branch to resource_governance playbook"
```

---

### Task 6: `oomkilled-low-memory-limit` fixture + test

**Files:**
- Create: `test/integration_test/cases/resource_governance/oomkilled-low-memory-limit/manifest.yaml`
- Create: `test/integration_test/cases/resource_governance/oomkilled-low-memory-limit/expected_answer.json`
- Modify: `test/integration_test/test_resource_governance.py`

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/resource_governance/oomkilled-low-memory-limit/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  restartPolicy: Always
  containers:
    - name: app
      image: busybox:1.36
      command: ["sh", "-c", "dd if=/dev/zero of=/dev/shm/fill bs=1M count=200"]
      resources:
        requests:
          cpu: "50m"
          memory: "16Mi"
        limits:
          cpu: "100m"
          memory: "16Mi"
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/resource_governance/oomkilled-low-memory-limit/expected_answer.json`:
```json
{
  "scenario_id": "oomkilled-low-memory-limit",
  "expected_root_cause": "The container's memory limit (16Mi) is far below what its process needs, so the kernel OOM-kills it almost immediately after each start, producing a CrashLoopBackOff. This is a resource limit misconfiguration, not an application bug, image pull issue, or probe failure.",
  "key_evidence": [
    "describe_resource on the pod shows containerStatuses[].lastState.terminated.reason == OOMKilled and exitCode == 137",
    "oom_killed_pods in the namespace lists this pod/container",
    "The container's resources.limits.memory (16Mi) is very low relative to what it needs"
  ],
  "should_not_conclude": [
    "An application code bug or unhandled exception caused the crash",
    "A failing liveness/readiness probe caused the restarts",
    "Image pull failure"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_resource_governance.py`**

Add near the existing `resourcequota_exhausted_scenario` fixture:
```python
@pytest.fixture
def oomkilled_low_memory_limit_scenario():
    namespace = "test-oomkilled-low-memory-limit"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("oomkilled-low-memory-limit", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)
```

Add at the end of the file:
```python
@pytest.mark.anyio
async def test_oomkilled_low_memory_limit(kubernetes_agent, judge, oomkilled_low_memory_limit_scenario):
    namespace = oomkilled_low_memory_limit_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The pod sample-app in namespace {namespace} keeps restarting. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("oomkilled-low-memory-limit")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

Note: `CASES_DIR` in this file is currently `Path(__file__).parent / "cases" /
"resource_governance"` — confirm this matches the new case directory path above (it does; no
change needed to `CASES_DIR`).

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-oomkilled-manual
kubectl apply -f test/integration_test/cases/resource_governance/oomkilled-low-memory-limit/manifest.yaml -n test-oomkilled-manual
sleep 15
kubectl get pod sample-app -n test-oomkilled-manual   # expect CrashLoopBackOff
kubectl get pod sample-app -n test-oomkilled-manual -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}'   # expect OOMKilled
kubectl delete namespace test-oomkilled-manual --wait=true
```
Expected: `OOMKilled` printed.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_resource_governance.py::test_oomkilled_low_memory_limit -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/resource_governance/oomkilled-low-memory-limit test/integration_test/test_resource_governance.py
git commit -m "test: add oomkilled-low-memory-limit resource_governance scenario"
```

---

### Task 7: `cpu-throttling-low-cpu-limit` fixture + test

**Files:**
- Create: `test/integration_test/cases/resource_governance/cpu-throttling-low-cpu-limit/manifest.yaml`
- Create: `test/integration_test/cases/resource_governance/cpu-throttling-low-cpu-limit/expected_answer.json`
- Modify: `test/integration_test/test_resource_governance.py`

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/resource_governance/cpu-throttling-low-cpu-limit/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  containers:
    - name: app
      image: busybox:1.36
      command: ["sh", "-c", "while true; do :; done"]
      resources:
        requests:
          cpu: "50m"
          memory: "32Mi"
        limits:
          cpu: "50m"
          memory: "64Mi"
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/resource_governance/cpu-throttling-low-cpu-limit/expected_answer.json`:
```json
{
  "scenario_id": "cpu-throttling-low-cpu-limit",
  "expected_root_cause": "The container's CPU limit (50m) is far below what its workload actually demands (a continuous busy loop), so it is heavily CPU-throttled. This is a resource limit misconfiguration, not a crash, scheduling problem, or application bug.",
  "key_evidence": [
    "cpu_saturation for this workload returns a value at or near 1 (usage close to or at its CPU limit)",
    "The container's resources.limits.cpu (50m) is very low relative to what a continuously-busy process needs",
    "The pod is Running and healthy (not crashing) -- this is a performance/throttling issue, not a crash"
  ],
  "should_not_conclude": [
    "The pod is crashlooping or failing to start",
    "Insufficient node-level CPU capacity (the node itself is fine; the container's own limit is the constraint)",
    "An application bug is causing the high CPU usage"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_resource_governance.py`**

```python
@pytest.fixture
def cpu_throttling_low_cpu_limit_scenario():
    namespace = "test-cpu-throttling-low-cpu-limit"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("cpu-throttling-low-cpu-limit", namespace)
    # Prometheus needs a few scrape cycles to have real container_cpu_usage_seconds_total
    # samples for this pod before cpu_saturation can return a meaningful ratio.
    time.sleep(45)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_cpu_throttling_low_cpu_limit(kubernetes_agent, judge, cpu_throttling_low_cpu_limit_scenario):
    namespace = cpu_throttling_low_cpu_limit_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The workload sample-app in namespace {namespace} seems slow. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("cpu-throttling-low-cpu-limit")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

This requires `import time` in the file — already present in every existing integration test file
via the shared style; confirm it's imported (it is, `test_resource_governance.py` already has
`import time` from its original, now-partially-removed header — verify after Task 2's edits that
`time` wasn't among the imports stripped; if it was, add it back).

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-cpu-throttling-manual
kubectl apply -f test/integration_test/cases/resource_governance/cpu-throttling-low-cpu-limit/manifest.yaml -n test-cpu-throttling-manual
sleep 10
kubectl get pod sample-app -n test-cpu-throttling-manual   # expect Running, not crashing
kubectl top pod sample-app -n test-cpu-throttling-manual   # expect CPU usage pegged near 50m
kubectl delete namespace test-cpu-throttling-manual --wait=true
```
Expected: pod stays Running, CPU usage sits at/near its 50m limit.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_resource_governance.py::test_cpu_throttling_low_cpu_limit -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/resource_governance/cpu-throttling-low-cpu-limit test/integration_test/test_resource_governance.py
git commit -m "test: add cpu-throttling-low-cpu-limit resource_governance scenario"
```

---

### Task 8: `storage.md` — Multi-Attach Error branch (documented, no fixture)

**Files:**
- Modify: `agents/playbooks/storage.md`

- [ ] **Step 1: Replace the file's full content**

```markdown
---
playbook_id: storage
category: storage
trigger_conditions:
  - "a PVC is stuck Pending or a pod cannot mount a volume"
  - "an event mentions a StorageClass could not be found or no volumes available"
  - "an event mentions a Multi-Attach error for a volume"
---

# Storage / PVC Failure

## Branch A: PVC Pending — missing StorageClass

1. tool: get_resource   params: {kind: PERSISTENTVOLUMECLAIM, name, namespace}   conclusive: false
   note: status Pending is the starting signal.
2. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: an event on the PVC/pod naming a StorageClass that could not be found,
   or 'no persistent volumes available'.
3. tool: list_resources   params: {kind: STORAGECLASS}   conclusive: true
   conclusion_criteria: the referenced StorageClass name is absent from the list.

### Conclusion (Branch A)
root_cause = the PVC references a StorageClass that does not exist, so no PV can be provisioned
or bound and the pod stays Pending. Name the PVC and the missing StorageClass.

## Branch B: Multi-Attach Error

1. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: >
     an event mentioning "Multi-Attach error for volume ... Volume is already exclusively
     attached to one node and can't be attached to another" — this occurs when a
     ReadWriteOnce PVC's pod is rescheduled to a different node before the volume detaches from
     the old one (e.g. after a node failure or a fast reschedule).
2. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
   note: confirm the pod is stuck in ContainerCreating and which node it's scheduled to now.

### Conclusion (Branch B)
root_cause = the PVC is ReadWriteOnce and still attached to its previous node; the new pod can't
mount it until the old attachment detaches (or the old node is confirmed gone). Name the PVC and
both the old and new node if visible in the event.

## Do not conclude
- Insufficient CPU or memory.
- Image pull failure.
- A node taint or scheduling issue unrelated to storage.
```

- [ ] **Step 2: Commit**

```bash
git add agents/playbooks/storage.md
git commit -m "docs: add documented Multi-Attach Error branch to storage playbook"
```

---

### Task 9: `network.md` — DNS Failure + LoadBalancer Pending branches

**Files:**
- Modify: `agents/playbooks/network.md`

- [ ] **Step 1: Insert two new numbered steps** (between the existing "### 9. Check Kubernetes
  events" section and "## Conclusion")

Insert after the `---` that follows step 9, before `## Conclusion`:

```markdown
### 10. Check DNS resolution

tool: list_resources
params: {kind: service, namespace}
conclusive: false

Check:

- does a Service with the exact hostname/name the client is trying to reach actually exist in
  this namespace (or the referenced namespace, if the client uses a fully-qualified name)?

tool: get_events
params: {namespace}
conclusive: false

Check:

- CoreDNS pods in kube-system Running and Ready (list_resources params: {kind: pod, namespace:
  kube-system, label_selector: k8s-app=kube-dns})

Possible findings:

- the target Service name does not exist at all -- DNS has nothing to resolve
- the client is using the wrong namespace suffix (cross-namespace DNS needs
  <service>.<namespace>.svc.cluster.local)
- CoreDNS itself is unhealthy (Pods not Ready) -- rare, check this last

---

### 11. Check LoadBalancer status (only if Service type is LoadBalancer)

tool: get_resource
params: {kind: service, name, namespace}
conclusive: true

Check:

- status.loadBalancer.ingress is empty/absent
- no cloud-controller-manager or LoadBalancer implementation exists in this cluster

Possible findings:

- the Service is otherwise correctly configured (selector matches, endpoints ready) but stuck
  Pending because nothing in this cluster can provision an external IP

---
```

- [ ] **Step 2: Extend the `## Conclusion` bullet list**

Add these two bullets to the existing "supported when the evidence identifies one of the
following" list:
```markdown
- The target Service name doesn't exist at all -- DNS has nothing to resolve
- A LoadBalancer Service has no controller available to provision an external IP
```

And to the "Examples" list:
```markdown
- DNS: target Service does not exist
- LoadBalancer stuck Pending: no controller available in this cluster
```

- [ ] **Step 3: Commit**

```bash
git add agents/playbooks/network.md
git commit -m "docs: add DNS Failure and LoadBalancer Pending branches to network playbook"
```

---

### Task 10: New `test_network.py` + `service-selector-mismatch` fixture

**Files:**
- Create: `test/integration_test/cases/network/service-selector-mismatch/manifest.yaml`
- Create: `test/integration_test/cases/network/service-selector-mismatch/expected_answer.json`
- Create: `test/integration_test/test_network.py`

**Interfaces:**
- Consumes: `kubernetes_agent`/`judge`/`anyio_backend` from `conftest.py` (Task 1).
- Produces: `CASES_DIR`, `_kubectl`, `_load_expected_answer`, `_apply_manifest` module-level
  helpers reused by Tasks 11-14 (all add to this same file).

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/network/service-selector-mismatch/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  containers:
    - name: app
      image: nginx:latest
      ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: sample-app-service
spec:
  selector:
    app: sample-app-wrong-label
  ports:
    - port: 80
      targetPort: 80
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/network/service-selector-mismatch/expected_answer.json`:
```json
{
  "scenario_id": "service-selector-mismatch",
  "expected_root_cause": "The Service sample-app-service selects pods with label app=sample-app-wrong-label, but the actual pod is labeled app=sample-app. The selector matches zero pods, so the Service has no endpoints and all traffic to it fails.",
  "key_evidence": [
    "get_resource on the Service shows selector app=sample-app-wrong-label",
    "check_service_connectivity (or the Service's Endpoints/EndpointSlices) shows zero ready endpoints",
    "The actual pod is Running and healthy with label app=sample-app, which does not match the Service selector"
  ],
  "should_not_conclude": [
    "CrashLoopBackOff or an unhealthy pod",
    "A NetworkPolicy is blocking traffic",
    "The Ingress is misconfigured"
  ]
}
```

- [ ] **Step 3: Write `test/integration_test/test_network.py`**

```python
"""
Networking integration tests for DiagnosisAgent (network.md playbook).

These run against a real, live Kubernetes cluster (kubectl's current context) and make
real LLM calls -- they are not part of the default `pytest test/` run. Run explicitly with:

    pytest test/integration_test/test_network.py -m integration
"""
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CASES_DIR = Path(__file__).parent / "cases" / "network"


def _kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=check)


def _load_expected_answer(scenario_id: str) -> dict:
    path = CASES_DIR / scenario_id / "expected_answer.json"
    return json.loads(path.read_text())


def _apply_manifest(scenario_id: str, namespace: str) -> None:
    manifest = CASES_DIR / scenario_id / "manifest.yaml"
    _kubectl("apply", "-f", str(manifest), "-n", namespace)


@pytest.fixture
def service_selector_mismatch_scenario():
    namespace = "test-service-selector-mismatch"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("service-selector-mismatch", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_service_selector_mismatch(kubernetes_agent, judge, service_selector_mismatch_scenario):
    namespace = service_selector_mismatch_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"Requests to sample-app-service in namespace {namespace} are failing. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("service-selector-mismatch")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-service-selector-manual
kubectl apply -f test/integration_test/cases/network/service-selector-mismatch/manifest.yaml -n test-service-selector-manual
kubectl get endpoints sample-app-service -n test-service-selector-manual   # expect no addresses
kubectl delete namespace test-service-selector-manual --wait=true
```
Expected: Endpoints object shows `<none>` for addresses.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_network.py -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/network/service-selector-mismatch test/integration_test/test_network.py
git commit -m "test: add test_network.py with service-selector-mismatch scenario"
```

---

### Task 11: `networkpolicy-deny-ingress` fixture + test

**Files:**
- Create: `test/integration_test/cases/network/networkpolicy-deny-ingress/manifest.yaml`
- Create: `test/integration_test/cases/network/networkpolicy-deny-ingress/expected_answer.json`
- Modify: `test/integration_test/test_network.py`

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/network/networkpolicy-deny-ingress/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  containers:
    - name: app
      image: nginx:latest
      ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: sample-app-service
spec:
  selector:
    app: sample-app
  ports:
    - port: 80
      targetPort: 80
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-ingress
spec:
  podSelector:
    matchLabels:
      app: sample-app
  policyTypes:
    - Ingress
  ingress: []
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/network/networkpolicy-deny-ingress/expected_answer.json`:
```json
{
  "scenario_id": "networkpolicy-deny-ingress",
  "expected_root_cause": "A NetworkPolicy (deny-all-ingress) selects the sample-app pod and specifies an empty ingress rule list with policyTypes: [Ingress], which denies all inbound traffic to it. The Service and Pod are otherwise healthy -- the NetworkPolicy is what's blocking connections.",
  "key_evidence": [
    "list_resources/get_resource for NetworkPolicy shows deny-all-ingress with podSelector matching app=sample-app",
    "The NetworkPolicy's ingress list is empty with policyTypes including Ingress, denying all inbound traffic",
    "The Service selector matches the pod and endpoints are ready -- ruling out a Service misconfiguration"
  ],
  "should_not_conclude": [
    "Service selector mismatch",
    "CrashLoopBackOff or an unhealthy pod",
    "DNS resolution failure"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_network.py`**

```python
@pytest.fixture
def networkpolicy_deny_ingress_scenario():
    namespace = "test-networkpolicy-deny-ingress"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("networkpolicy-deny-ingress", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_networkpolicy_deny_ingress(kubernetes_agent, judge, networkpolicy_deny_ingress_scenario):
    namespace = networkpolicy_deny_ingress_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"Requests to sample-app-service in namespace {namespace} are timing out. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("networkpolicy-deny-ingress")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-networkpolicy-manual
kubectl apply -f test/integration_test/cases/network/networkpolicy-deny-ingress/manifest.yaml -n test-networkpolicy-manual
kubectl get networkpolicy deny-all-ingress -n test-networkpolicy-manual -o yaml
kubectl delete namespace test-networkpolicy-manual --wait=true
```
Expected: the NetworkPolicy shows `ingress: []` and `policyTypes: [Ingress]`.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_network.py::test_networkpolicy_deny_ingress -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/network/networkpolicy-deny-ingress test/integration_test/test_network.py
git commit -m "test: add networkpolicy-deny-ingress network scenario"
```

---

### Task 12: `ingress-wrong-backend-port` fixture + test

**Files:**
- Create: `test/integration_test/cases/network/ingress-wrong-backend-port/manifest.yaml`
- Create: `test/integration_test/cases/network/ingress-wrong-backend-port/expected_answer.json`
- Modify: `test/integration_test/test_network.py`

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/network/ingress-wrong-backend-port/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  containers:
    - name: app
      image: nginx:latest
      ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: sample-app-service
spec:
  selector:
    app: sample-app
  ports:
    - port: 8080
      targetPort: 80
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: sample-app-ingress
spec:
  rules:
    - host: sample-app.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: sample-app-service
                port:
                  number: 80
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/network/ingress-wrong-backend-port/expected_answer.json`:
```json
{
  "scenario_id": "ingress-wrong-backend-port",
  "expected_root_cause": "The Ingress sample-app-ingress routes to sample-app-service on port 80, but the Service only exposes port 8080 -- there is no port 80 on that Service. Traffic through the Ingress fails because the backend port doesn't exist, even though the Service itself correctly selects the healthy pod.",
  "key_evidence": [
    "get_resource on the Ingress shows backend.service.port.number == 80",
    "get_resource on the Service shows it only exposes port 8080 (targetPort 80 on the container) -- no port 80 exists on the Service",
    "The Service's selector correctly matches the healthy pod, ruling out a selector mismatch"
  ],
  "should_not_conclude": [
    "Service selector mismatch",
    "A NetworkPolicy is blocking traffic",
    "CrashLoopBackOff or an unhealthy pod"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_network.py`**

```python
@pytest.fixture
def ingress_wrong_backend_port_scenario():
    namespace = "test-ingress-wrong-backend-port"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("ingress-wrong-backend-port", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_ingress_wrong_backend_port(kubernetes_agent, judge, ingress_wrong_backend_port_scenario):
    namespace = ingress_wrong_backend_port_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"Traffic through sample-app-ingress in namespace {namespace} is failing. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("ingress-wrong-backend-port")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-ingress-manual
kubectl apply -f test/integration_test/cases/network/ingress-wrong-backend-port/manifest.yaml -n test-ingress-manual
kubectl get ingress sample-app-ingress -n test-ingress-manual -o jsonpath='{.spec.rules[0].http.paths[0].backend.service.port.number}'
kubectl get service sample-app-service -n test-ingress-manual -o jsonpath='{.spec.ports[0].port}'
kubectl delete namespace test-ingress-manual --wait=true
```
Expected: first command prints `80`, second prints `8080` — confirming the mismatch exists as
designed.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_network.py::test_ingress_wrong_backend_port -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/network/ingress-wrong-backend-port test/integration_test/test_network.py
git commit -m "test: add ingress-wrong-backend-port network scenario"
```

---

### Task 13: `dns-nonexistent-service` fixture + test

**Files:**
- Create: `test/integration_test/cases/network/dns-nonexistent-service/manifest.yaml`
- Create: `test/integration_test/cases/network/dns-nonexistent-service/expected_answer.json`
- Modify: `test/integration_test/test_network.py`

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/network/dns-nonexistent-service/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: dns-test-client
  labels:
    app: dns-test-client
spec:
  restartPolicy: Always
  containers:
    - name: app
      image: busybox:1.36
      command:
        - sh
        - -c
        - "while true; do echo Attempting to reach nonexistent-backend-service; wget -T 3 -O- http://nonexistent-backend-service.default.svc.cluster.local/ || echo DNS lookup failed for nonexistent-backend-service; sleep 5; done"
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/network/dns-nonexistent-service/expected_answer.json`:
```json
{
  "scenario_id": "dns-nonexistent-service",
  "expected_root_cause": "The client pod dns-test-client is trying to reach nonexistent-backend-service, which does not exist as a Service in this cluster. DNS resolution fails because there is no Service (and therefore no DNS record) with that name -- the fix is to create the missing Service or correct the hostname the client is using, not a networking/connectivity problem with an existing Service.",
  "key_evidence": [
    "recent_logs/error_logs for dns-test-client show repeated DNS lookup failures / could not resolve host for nonexistent-backend-service",
    "list_resources for Service in the namespace shows no Service named nonexistent-backend-service exists",
    "The client pod itself is Running and healthy -- the failure is specifically DNS resolution of the target hostname"
  ],
  "should_not_conclude": [
    "A NetworkPolicy is blocking traffic",
    "The target Service exists but has a selector mismatch",
    "CrashLoopBackOff or an unhealthy client pod"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_network.py`**

```python
@pytest.fixture
def dns_nonexistent_service_scenario():
    namespace = "test-dns-nonexistent-service"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("dns-nonexistent-service", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_dns_nonexistent_service(kubernetes_agent, judge, dns_nonexistent_service_scenario):
    namespace = dns_nonexistent_service_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The pod dns-test-client in namespace {namespace} can't reach its dependency. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("dns-nonexistent-service")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-dns-manual
kubectl apply -f test/integration_test/cases/network/dns-nonexistent-service/manifest.yaml -n test-dns-manual
sleep 10
kubectl logs dns-test-client -n test-dns-manual --tail=20
kubectl delete namespace test-dns-manual --wait=true
```
Expected: log lines show `DNS lookup failed for nonexistent-backend-service` repeating.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_network.py::test_dns_nonexistent_service -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/network/dns-nonexistent-service test/integration_test/test_network.py
git commit -m "test: add dns-nonexistent-service network scenario"
```

---

### Task 14: `loadbalancer-pending-no-controller` fixture + test

**Files:**
- Create: `test/integration_test/cases/network/loadbalancer-pending-no-controller/manifest.yaml`
- Create: `test/integration_test/cases/network/loadbalancer-pending-no-controller/expected_answer.json`
- Modify: `test/integration_test/test_network.py`

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/network/loadbalancer-pending-no-controller/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  containers:
    - name: app
      image: nginx:latest
      ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: sample-app-service
spec:
  type: LoadBalancer
  selector:
    app: sample-app
  ports:
    - port: 80
      targetPort: 80
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/network/loadbalancer-pending-no-controller/expected_answer.json`:
```json
{
  "scenario_id": "loadbalancer-pending-no-controller",
  "expected_root_cause": "The Service sample-app-service is type LoadBalancer, but this cluster has no cloud provider / LoadBalancer controller to provision an external IP, so status.loadBalancer.ingress stays empty and the Service remains pending indefinitely. The Service, Pod, and selector are otherwise correctly configured -- the issue is environmental (no LoadBalancer implementation available), not a misconfiguration of the Service itself.",
  "key_evidence": [
    "get_resource on the Service shows type: LoadBalancer with an empty/absent status.loadBalancer.ingress (no external IP assigned)",
    "The Service selector correctly matches a healthy, Ready pod -- ruling out a selector or workload health problem",
    "No cloud-controller-manager / LoadBalancer controller is present in this cluster to satisfy the request"
  ],
  "should_not_conclude": [
    "Service selector mismatch",
    "CrashLoopBackOff or an unhealthy pod",
    "A NetworkPolicy is blocking traffic"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_network.py`**

```python
@pytest.fixture
def loadbalancer_pending_no_controller_scenario():
    namespace = "test-loadbalancer-pending-no-controller"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("loadbalancer-pending-no-controller", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_loadbalancer_pending_no_controller(kubernetes_agent, judge, loadbalancer_pending_no_controller_scenario):
    namespace = loadbalancer_pending_no_controller_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The Service sample-app-service in namespace {namespace} never gets an external IP. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("loadbalancer-pending-no-controller")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-lb-manual
kubectl apply -f test/integration_test/cases/network/loadbalancer-pending-no-controller/manifest.yaml -n test-lb-manual
sleep 5
kubectl get service sample-app-service -n test-lb-manual   # expect EXTERNAL-IP <pending>
kubectl delete namespace test-lb-manual --wait=true
```
Expected: `EXTERNAL-IP` column shows `<pending>`.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_network.py::test_loadbalancer_pending_no_controller -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/network/loadbalancer-pending-no-controller test/integration_test/test_network.py
git commit -m "test: add loadbalancer-pending-no-controller network scenario"
```

---

### Task 15: `autoscaling.md` — Cluster Autoscaler limitation note

**Files:**
- Modify: `agents/playbooks/autoscaling.md`

- [ ] **Step 1: Append a `## Limitations` section** (insert before the final `## Do not conclude`
  section)

```markdown
## Limitations
This toolset has no visibility into a cluster autoscaler (cloud-provider node provisioning). If
pods are Unschedulable due to insufficient node capacity and you'd expect the cluster to scale
up, state that node capacity is insufficient and recommend scaling (manually or via
cluster-autoscaler) — do not speculate about why an autoscaler failed to provision nodes; that
requires cloud-provider/autoscaler logs this agent cannot see.
```

- [ ] **Step 2: Commit**

```bash
git add agents/playbooks/autoscaling.md
git commit -m "docs: document Cluster Autoscaler Not Scaling as a tool-visibility limitation"
```

---

### Task 16: New `test_pod_lifecycle.py` + `crashloopbackoff-app-error` fixture

**Files:**
- Create: `test/integration_test/cases/generic/crashloopbackoff-app-error/manifest.yaml`
- Create: `test/integration_test/cases/generic/crashloopbackoff-app-error/expected_answer.json`
- Create: `test/integration_test/test_pod_lifecycle.py`

**Interfaces:**
- Consumes: `kubernetes_agent`/`judge`/`anyio_backend` from `conftest.py` (Task 1).
- Produces: `CASES_DIR`, `_kubectl`, `_load_expected_answer`, `_apply_manifest` reused by Tasks
  17-18.

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/generic/crashloopbackoff-app-error/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  restartPolicy: Always
  containers:
    - name: app
      image: busybox:1.36
      command: ["sh", "-c", "echo Fatal: unhandled exception in main loop; exit 1"]
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/generic/crashloopbackoff-app-error/expected_answer.json`:
```json
{
  "scenario_id": "crashloopbackoff-app-error",
  "expected_root_cause": "The container sample-app exits immediately with a fatal error ('unhandled exception in main loop') every time it starts, producing CrashLoopBackOff. This is an application-level crash, not an OOM kill, probe failure, image pull problem, or config error.",
  "key_evidence": [
    "The pod is in CrashLoopBackOff with a climbing restartCount",
    "error_logs/recent_logs (or lastState.terminated) show the fatal error message logged before each exit",
    "containerStatuses[].lastState.terminated.exitCode is a non-137 error code (not OOMKilled)"
  ],
  "should_not_conclude": [
    "OOMKilled",
    "A failing liveness/readiness probe",
    "Image pull failure"
  ]
}
```

- [ ] **Step 3: Write `test/integration_test/test_pod_lifecycle.py`**

```python
"""
Pod Lifecycle integration tests for DiagnosisAgent (generic.md playbook's Pod Lifecycle
branches: CrashLoopBackOff, ImagePullBackOff, CreateContainerError).

These run against a real, live Kubernetes cluster (kubectl's current context) and make
real LLM calls -- they are not part of the default `pytest test/` run. Run explicitly with:

    pytest test/integration_test/test_pod_lifecycle.py -m integration
"""
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CASES_DIR = Path(__file__).parent / "cases" / "generic"


def _kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=check)


def _load_expected_answer(scenario_id: str) -> dict:
    path = CASES_DIR / scenario_id / "expected_answer.json"
    return json.loads(path.read_text())


def _apply_manifest(scenario_id: str, namespace: str) -> None:
    manifest = CASES_DIR / scenario_id / "manifest.yaml"
    _kubectl("apply", "-f", str(manifest), "-n", namespace)


@pytest.fixture
def crashloopbackoff_app_error_scenario():
    namespace = "test-crashloopbackoff-app-error"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("crashloopbackoff-app-error", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_crashloopbackoff_app_error(kubernetes_agent, judge, crashloopbackoff_app_error_scenario):
    namespace = crashloopbackoff_app_error_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The pod sample-app in namespace {namespace} keeps restarting. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("crashloopbackoff-app-error")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

Note: the case directory is `cases/generic` (not `cases/pod_lifecycle`) — the playbook stays
`playbook_id: generic`; the test *file* is named `test_pod_lifecycle.py` purely for readability,
matching `FAILURE_MODES.md`'s own category name.

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-crashloop-manual
kubectl apply -f test/integration_test/cases/generic/crashloopbackoff-app-error/manifest.yaml -n test-crashloop-manual
sleep 15
kubectl get pod sample-app -n test-crashloop-manual   # expect CrashLoopBackOff
kubectl delete namespace test-crashloop-manual --wait=true
```
Expected: pod status shows `CrashLoopBackOff`.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_pod_lifecycle.py -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/generic/crashloopbackoff-app-error test/integration_test/test_pod_lifecycle.py
git commit -m "test: add test_pod_lifecycle.py with crashloopbackoff-app-error scenario"
```

---

### Task 17: `imagepullbackoff-bad-tag` fixture + test

**Files:**
- Create: `test/integration_test/cases/generic/imagepullbackoff-bad-tag/manifest.yaml`
- Create: `test/integration_test/cases/generic/imagepullbackoff-bad-tag/expected_answer.json`
- Modify: `test/integration_test/test_pod_lifecycle.py`

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/generic/imagepullbackoff-bad-tag/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  containers:
    - name: app
      image: busybox:this-tag-does-not-exist-v99
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/generic/imagepullbackoff-bad-tag/expected_answer.json`:
```json
{
  "scenario_id": "imagepullbackoff-bad-tag",
  "expected_root_cause": "The pod references image busybox:this-tag-does-not-exist-v99, which does not exist in the registry. The container can never start because the image tag itself is invalid -- this is not a registry authentication or connectivity problem.",
  "key_evidence": [
    "The pod's container status shows waiting.reason ImagePullBackOff (or ErrImagePull)",
    "get_events shows a pull error naming the image and a 'manifest unknown' / 'not found' style message",
    "describe_resource confirms the exact image:tag requested is busybox:this-tag-does-not-exist-v99"
  ],
  "should_not_conclude": [
    "Missing or incorrect imagePullSecret / registry authentication",
    "Registry unreachable / network connectivity problem",
    "CrashLoopBackOff caused by an application error"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_pod_lifecycle.py`**

```python
@pytest.fixture
def imagepullbackoff_bad_tag_scenario():
    namespace = "test-imagepullbackoff-bad-tag"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("imagepullbackoff-bad-tag", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_imagepullbackoff_bad_tag(kubernetes_agent, judge, imagepullbackoff_bad_tag_scenario):
    namespace = imagepullbackoff_bad_tag_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The pod sample-app in namespace {namespace} never starts. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("imagepullbackoff-bad-tag")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-imagepull-manual
kubectl apply -f test/integration_test/cases/generic/imagepullbackoff-bad-tag/manifest.yaml -n test-imagepull-manual
sleep 15
kubectl get pod sample-app -n test-imagepull-manual   # expect ImagePullBackOff / ErrImagePull
kubectl delete namespace test-imagepull-manual --wait=true
```
Expected: pod status shows `ImagePullBackOff` or `ErrImagePull`.

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_pod_lifecycle.py::test_imagepullbackoff_bad_tag -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/generic/imagepullbackoff-bad-tag test/integration_test/test_pod_lifecycle.py
git commit -m "test: add imagepullbackoff-bad-tag pod lifecycle scenario"
```

---

### Task 18: `createcontainererror-bad-entrypoint` fixture + test

**Files:**
- Create: `test/integration_test/cases/generic/createcontainererror-bad-entrypoint/manifest.yaml`
- Create: `test/integration_test/cases/generic/createcontainererror-bad-entrypoint/expected_answer.json`
- Modify: `test/integration_test/test_pod_lifecycle.py`

- [ ] **Step 1: Write the manifest**

`test/integration_test/cases/generic/createcontainererror-bad-entrypoint/manifest.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sample-app
  labels:
    app: sample-app
spec:
  containers:
    - name: app
      image: busybox:1.36
      command: ["/this-binary-does-not-exist"]
```

- [ ] **Step 2: Write the expected answer**

`test/integration_test/cases/generic/createcontainererror-bad-entrypoint/expected_answer.json`:
```json
{
  "scenario_id": "createcontainererror-bad-entrypoint",
  "expected_root_cause": "The pod's container specifies command /this-binary-does-not-exist, which does not exist in the busybox image's filesystem. The container runtime fails to start the container (CreateContainerError) because the entrypoint itself is invalid -- the image was pulled successfully, so this is not an image pull problem.",
  "key_evidence": [
    "The pod's container status shows waiting.reason CreateContainerError (or a similar container-creation failure)",
    "get_events shows a failure naming the command/entrypoint (e.g. 'exec: \"/this-binary-does-not-exist\": stat ... no such file or directory')",
    "The image itself was pulled successfully -- this rules out ImagePullBackOff"
  ],
  "should_not_conclude": [
    "ImagePullBackOff or ErrImagePull",
    "CrashLoopBackOff caused by an application error",
    "Insufficient CPU or memory"
  ]
}
```

- [ ] **Step 3: Add the scenario fixture and test to `test_pod_lifecycle.py`**

```python
@pytest.fixture
def createcontainererror_bad_entrypoint_scenario():
    namespace = "test-createcontainererror-bad-entrypoint"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("createcontainererror-bad-entrypoint", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_createcontainererror_bad_entrypoint(kubernetes_agent, judge, createcontainererror_bad_entrypoint_scenario):
    namespace = createcontainererror_bad_entrypoint_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The pod sample-app in namespace {namespace} never starts. Diagnose the root cause.",
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("createcontainererror-bad-entrypoint")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
```

- [ ] **Step 4: Verify raw cluster behavior cheaply before running the full test**

```bash
kubectl create namespace test-createcontainererror-manual
kubectl apply -f test/integration_test/cases/generic/createcontainererror-bad-entrypoint/manifest.yaml -n test-createcontainererror-manual
sleep 15
kubectl get pod sample-app -n test-createcontainererror-manual   # expect CreateContainerError / RunContainerError
kubectl delete namespace test-createcontainererror-manual --wait=true
```
Expected: pod status shows `CreateContainerError` (or a closely related container-creation
failure reason — cluster/runtime-dependent, but never `Running`).

- [ ] **Step 5: Run the real test**

Run: `pytest test/integration_test/test_pod_lifecycle.py::test_createcontainererror_bad_entrypoint -m integration -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add test/integration_test/cases/generic/createcontainererror-bad-entrypoint test/integration_test/test_pod_lifecycle.py
git commit -m "test: add createcontainererror-bad-entrypoint pod lifecycle scenario"
```

---

## Final verification (after all 18 tasks)

- [ ] Run `pytest` — unit suite green.
- [ ] Run every integration file once more in sequence to confirm nothing regressed across
  tasks (each starts/tears down its own namespaces, so order doesn't matter):
  ```bash
  pytest test/integration_test/ -m integration -v
  ```
- [ ] Confirm no leftover `test-*` namespaces remain: `kubectl get ns | grep test-` should be
  empty (every fixture's `finally:` block deletes its namespace).
