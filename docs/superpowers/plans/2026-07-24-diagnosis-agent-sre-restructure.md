# DiagnosisAgent SRE-Style Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `DiagnosisAgent`'s flat ReAct loop with an explicit SRE-style phase graph (scope → hypothesize → investigate ⇄ tool → evaluate → reformulate/finalize) driven by markdown playbooks, with per-phase budgets and model tiering for cost control.

**Architecture:** A new phase graph in `agents/diagnosis_agent.py`, a `PlaybookLibrary` loading `playbooks/*.md`, two structured-output helpers (`Hypothesizer`, `DiagnosisEvaluator`) mirroring the existing `Classifier` pattern, two new pydantic models, and additional `DiagnosisAgentState` fields. `DiagnosisResult` is unchanged, so nothing downstream (`PlannerAgent`, orchestrator, API, UI) changes.

**Tech Stack:** Python 3.11, LangGraph, LangChain, pydantic, PyYAML (`6.0.3`, already installed), pytest. Cheap model = existing `classifier_llm` (gpt-4.1-nano); main model = `diagnosis_llm`.

## Global Constraints

- v1 uses **only the 18 tools that exist today** (k8s: `list_namespaces get_resource list_resources describe_resource get_events get_pod_logs get_previous_logs top_pods top_nodes get_node_conditions rollout_status check_service_connectivity`; prometheus: `error_rate latency_p95 oom_killed_pods cpu_saturation`; loki: `recent_logs error_logs`). No new MCP tools.
- `DiagnosisResult` (`models/diagnosis_result.py`) shape is **unchanged**. Do not modify it, `Classifier`'s contract, `graph/nodes.py::require_remediation_routing_node`, `PlannerAgent`, the API, or the UI.
- Budgets are constants on the agent: `MAX_SCOPE_CALLS = 3`, `MAX_INVESTIGATE_ITERATIONS = 8`, `MAX_HYPOTHESES = 3`. The old `MAX_ITERATIONS = 30` and `iteration_count` state field are removed.
- Model tiering: scope, hypothesize, evaluate, and finalize run on the cheap model (`classifier_llm or llm`); only `investigate_node` uses the main `self.llm`.
- Structured-output helpers follow the existing `Classifier`/`PlanClassifier` pattern exactly: `llm.with_structured_output(Model, include_raw=True)`, and a null-`parsed` fallback path (see `agents/helpers/classifier.py:29-37`).
- Playbooks live in `playbooks/*.md` at repo root, YAML frontmatter (`playbook_id`, `category`, `trigger_conditions`) + markdown body. A `generic` playbook MUST exist (fallback); `PlaybookLibrary` raises if it's missing.
- Playbook checklists are derived from `test/integration_test/cases/<category>/<scenario>/expected_answer.json` — `key_evidence` → checklist steps, `should_not_conclude` → "Do not conclude" section.
- New helpers/models must be exported from `agents/helpers/__init__.py` and `models/__init__.py` respectively.
- Unit tests go under `test/unit_test/` and must not require a live cluster or real LLM (mock the LLM). Integration tests (`pytest -m integration`) are the acceptance bar but are run manually (live cluster + real API cost), not as an automated step in this plan.

---

## Task 1: New structured-output models

**Files:**
- Create: `models/hypothesis.py`
- Create: `models/evaluation.py`
- Modify: `models/__init__.py`
- Test: `test/unit_test/models/phase_models_test.py`

**Interfaces:**
- Produces: `HypothesisSelection` (fields `hypothesis: str`, `playbook_id: str`, `reasoning: str`) and `EvaluationVerdict` (fields `verdict: Literal["conclusive","reformulate","exhausted"]`, `reasoning: str`, `requires_remediation: bool | None`, `next_hypothesis: str | None`, `why_ruled_out: str | None`) — consumed by Tasks 4, 5, 6.

- [ ] **Step 1: Write the failing test**

Create `test/unit_test/models/phase_models_test.py`:
```python
import pytest
from pydantic import ValidationError

from models import HypothesisSelection, EvaluationVerdict


def test_hypothesis_selection_roundtrip():
    h = HypothesisSelection(hypothesis="pod OOMKilled", playbook_id="generic", reasoning="mem spike")
    assert h.playbook_id == "generic"


def test_evaluation_verdict_accepts_valid_verdicts():
    for v in ("conclusive", "reformulate", "exhausted"):
        assert EvaluationVerdict(verdict=v, reasoning="x").verdict == v


def test_evaluation_verdict_rejects_unknown_verdict():
    with pytest.raises(ValidationError):
        EvaluationVerdict(verdict="maybe", reasoning="x")


def test_evaluation_verdict_optional_fields_default_none():
    e = EvaluationVerdict(verdict="exhausted", reasoning="no lead")
    assert e.requires_remediation is None
    assert e.next_hypothesis is None
    assert e.why_ruled_out is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `source .venv/bin/activate && pytest test/unit_test/models/phase_models_test.py -q`
Expected: FAIL with `ImportError: cannot import name 'HypothesisSelection' from 'models'`

- [ ] **Step 3: Create `models/hypothesis.py`**

```python
from pydantic import BaseModel, Field


class HypothesisSelection(BaseModel):
    hypothesis: str = Field(description="One-sentence leading hypothesis for the root cause.")
    playbook_id: str = Field(
        description="The playbook_id to investigate under. Use 'generic' if no playbook fits."
    )
    reasoning: str = Field(description="Why this hypothesis and playbook were chosen.")
```

- [ ] **Step 4: Create `models/evaluation.py`**

```python
from typing import Literal

from pydantic import BaseModel, Field


class EvaluationVerdict(BaseModel):
    verdict: Literal["conclusive", "reformulate", "exhausted"] = Field(
        description=(
            "conclusive = the evidence supports a confident root cause; "
            "reformulate = the current hypothesis was ruled out but a different one is worth trying; "
            "exhausted = no confident conclusion and no new hypothesis worth pursuing."
        )
    )
    reasoning: str = Field(description="Brief justification for the verdict.")
    requires_remediation: bool | None = Field(
        default=None,
        description=(
            "Set only when verdict is 'conclusive'. True if the root cause is fixable via a "
            "Kubernetes config/manifest change; False if it is an application-level bug or an "
            "informational finding with nothing to fix."
        ),
    )
    next_hypothesis: str | None = Field(
        default=None, description="Set only when verdict is 'reformulate': the next hypothesis to investigate."
    )
    why_ruled_out: str | None = Field(
        default=None, description="Set only when verdict is 'reformulate': why the current hypothesis was ruled out."
    )
```

- [ ] **Step 5: Update `models/__init__.py`**

Current:
```python
from .diagnosis_result import DiagnosisResult
from .scenario_eval_result import ScenarioEvalResult
from .eval_result import EvalResult
from .remediation_plan import RemediationPlan, RemediationStep
```
Add two lines at the end:
```python
from .hypothesis import HypothesisSelection
from .evaluation import EvaluationVerdict
```

- [ ] **Step 6: Run the tests to confirm they pass**

Run: `pytest test/unit_test/models/phase_models_test.py -q`
Expected: `4 passed`

- [ ] **Step 7: Commit**

```bash
git add models/hypothesis.py models/evaluation.py models/__init__.py test/unit_test/models/phase_models_test.py
git commit -m "$(cat <<'EOF'
feat: add HypothesisSelection and EvaluationVerdict models

EOF
)"
```

---

## Task 2: PlaybookLibrary + generic & network playbooks

**Files:**
- Create: `agents/helpers/playbook_library.py`
- Create: `playbooks/generic.md`
- Create: `playbooks/network.md`
- Modify: `agents/helpers/__init__.py`
- Test: `test/unit_test/helpers/playbook_library_test.py`

**Interfaces:**
- Produces: `PlaybookLibrary` with `list_triggers() -> list[dict]` (each `{playbook_id, category, trigger_conditions}`) and `get(playbook_id) -> Playbook` (falls back to `generic`); `Playbook` dataclass (`playbook_id`, `category`, `trigger_conditions: list[str]`, `body: str`). Consumed by Tasks 4, 5, 6.

- [ ] **Step 1: Write the failing test**

Create `test/unit_test/helpers/playbook_library_test.py`:
```python
import pytest

from agents.helpers import PlaybookLibrary


def test_loads_generic_and_network(tmp_path):
    lib = PlaybookLibrary()
    ids = {t["playbook_id"] for t in lib.list_triggers()}
    assert "generic" in ids
    assert "network" in ids


def test_get_returns_requested_playbook():
    lib = PlaybookLibrary()
    pb = lib.get("network")
    assert pb.playbook_id == "network"
    assert pb.trigger_conditions  # non-empty
    assert pb.body.strip()


def test_get_unknown_id_falls_back_to_generic():
    lib = PlaybookLibrary()
    pb = lib.get("does-not-exist")
    assert pb.playbook_id == "generic"


def test_missing_generic_raises(tmp_path):
    (tmp_path / "network.md").write_text(
        "---\nplaybook_id: network\ncategory: network\ntrigger_conditions:\n  - x\n---\nbody"
    )
    with pytest.raises(ValueError):
        PlaybookLibrary(playbooks_dir=str(tmp_path))


def test_parses_frontmatter_and_body(tmp_path):
    (tmp_path / "generic.md").write_text(
        "---\nplaybook_id: generic\ncategory: fallback\ntrigger_conditions: []\n---\n# Body\nchecklist here"
    )
    lib = PlaybookLibrary(playbooks_dir=str(tmp_path))
    pb = lib.get("generic")
    assert pb.category == "fallback"
    assert "checklist here" in pb.body
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest test/unit_test/helpers/playbook_library_test.py -q`
Expected: FAIL with `ImportError: cannot import name 'PlaybookLibrary' from 'agents.helpers'`

- [ ] **Step 3: Create `agents/helpers/playbook_library.py`**

```python
import os
from dataclasses import dataclass

import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLAYBOOKS_DIR = os.path.join(_REPO_ROOT, "playbooks")


@dataclass
class Playbook:
    playbook_id: str
    category: str
    trigger_conditions: list[str]
    body: str


class PlaybookLibrary:
    def __init__(self, playbooks_dir: str = PLAYBOOKS_DIR) -> None:
        self._playbooks: dict[str, Playbook] = {}
        for filename in sorted(os.listdir(playbooks_dir)):
            if not filename.endswith(".md"):
                continue
            playbook = self._parse(os.path.join(playbooks_dir, filename))
            self._playbooks[playbook.playbook_id] = playbook
        if "generic" not in self._playbooks:
            raise ValueError("PlaybookLibrary requires a 'generic' fallback playbook")

    @staticmethod
    def _parse(path: str) -> Playbook:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if not text.lstrip().startswith("---"):
            raise ValueError(f"Playbook {path} is missing YAML frontmatter")
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter) or {}
        if "playbook_id" not in meta:
            raise ValueError(f"Playbook {path} frontmatter missing playbook_id")
        return Playbook(
            playbook_id=meta["playbook_id"],
            category=meta.get("category", meta["playbook_id"]),
            trigger_conditions=meta.get("trigger_conditions") or [],
            body=body.strip(),
        )

    def list_triggers(self) -> list[dict]:
        return [
            {
                "playbook_id": p.playbook_id,
                "category": p.category,
                "trigger_conditions": p.trigger_conditions,
            }
            for p in self._playbooks.values()
        ]

    def get(self, playbook_id: str) -> Playbook:
        return self._playbooks.get(playbook_id, self._playbooks["generic"])
```

- [ ] **Step 4: Create `playbooks/generic.md`**

```markdown
---
playbook_id: generic
category: fallback
trigger_conditions: []
---

# Generic Investigation

Use this when no specialized playbook matches the scope signals.

## Checklist
1. If a specific resource/workload is named, `describe_resource` it first; otherwise
   `list_resources` in the namespace to identify the affected workload.
2. For a failing or not-ready workload, `describe_resource` the Pod, then `get_events` in the
   namespace for the failure reason.
3. If the container is running, `get_pod_logs`; if it has restarted, `get_previous_logs`.
4. Treat an explicit failure string in events or logs — "Forbidden", "OOMKilled",
   "CrashLoopBackOff", "ImagePullBackOff", "Evicted", "FailedScheduling",
   "FailedGetResourceMetric", "CreateContainerConfigError" — as sufficient evidence of the
   mechanism. Stop confirming it further and conclude.

## Conclusion
Name the concrete mechanism and the specific resource that causes it. If evidence is
insufficient, say exactly what is missing rather than guessing.

## Do not conclude
- Do not assert a cause behind a missing/misreferenced resource you cannot see the source of.
- Do not blame infrastructure (metrics-server, node capacity) when a workload-level cause fits.
```

- [ ] **Step 5: Create `playbooks/network.md`**

```markdown
---
playbook_id: network
category: network
trigger_conditions:
  - "5xx or connection-refused/timeout errors present"
  - "no matching application-level error logs in the same window"
---

# Network / Connectivity Failure

## Checklist
1. tool: check_service_connectivity   params: {service, namespace}   conclusive: false
   note: confirm the Service name first with list_resources(kind=service) — a Service name is
   not necessarily its Deployment name.
2. tool: error_rate                    params: {app, window}          conclusive: false
   note: near-uniform errors across all pods point to network/service, not a single bad pod.
3. tool: get_events                    params: {namespace}            conclusive: true
   conclusion_criteria: >
     endpoints empty or all-not-ready AND no matching app-level error logs in the window
     AND a Service/NetworkPolicy change is visible in events within the window.

## Conclusion
root_cause = network iff endpoints are unhealthy/empty while errors are near-uniform across
pods (not isolated to one) and a correlated recent change is present.

## Do not conclude
- A single crashing pod — that is an application/workload failure (use the rollout or generic
  playbook), not a network fault.
```

- [ ] **Step 6: Update `agents/helpers/__init__.py`**

Add after the existing imports:
```python
from .playbook_library import PlaybookLibrary, Playbook
```

- [ ] **Step 7: Run the tests to confirm they pass**

Run: `pytest test/unit_test/helpers/playbook_library_test.py -q`
Expected: `5 passed`

- [ ] **Step 8: Commit**

```bash
git add agents/helpers/playbook_library.py agents/helpers/__init__.py playbooks/generic.md playbooks/network.md test/unit_test/helpers/playbook_library_test.py
git commit -m "$(cat <<'EOF'
feat: add PlaybookLibrary with generic and network playbooks

EOF
)"
```

---

## Task 3: Remaining 7 scenario playbooks

**Files:**
- Create: `playbooks/rbac.md`, `playbooks/autoscaling.md`, `playbooks/scheduling.md`, `playbooks/resource_governance.md`, `playbooks/storage.md`, `playbooks/secret_configmap.md`, `playbooks/rollout.md`
- Test: `test/unit_test/helpers/playbook_catalog_test.py`

**Interfaces:**
- Consumes: `PlaybookLibrary` from Task 2.
- Produces: 7 more playbooks (total 9 with generic+network) — every one loadable with non-empty triggers and body. Consumed by Task 6's `hypothesize_node`.

- [ ] **Step 1: Write the failing test**

Create `test/unit_test/helpers/playbook_catalog_test.py`:
```python
from agents.helpers import PlaybookLibrary

EXPECTED_IDS = {
    "generic", "network", "rbac", "autoscaling", "scheduling",
    "resource_governance", "storage", "secret_configmap", "rollout",
}


def test_all_expected_playbooks_present():
    lib = PlaybookLibrary()
    ids = {t["playbook_id"] for t in lib.list_triggers()}
    assert EXPECTED_IDS <= ids, f"missing: {EXPECTED_IDS - ids}"


def test_every_non_generic_playbook_has_triggers_and_body():
    lib = PlaybookLibrary()
    for t in lib.list_triggers():
        pb = lib.get(t["playbook_id"])
        assert pb.body.strip(), f"{pb.playbook_id} has empty body"
        if pb.playbook_id != "generic":
            assert pb.trigger_conditions, f"{pb.playbook_id} has no trigger_conditions"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest test/unit_test/helpers/playbook_catalog_test.py -q`
Expected: FAIL on `test_all_expected_playbooks_present` (missing the 7 new ids)

- [ ] **Step 3: Create `playbooks/rbac.md`**

```markdown
---
playbook_id: rbac
category: rbac
trigger_conditions:
  - "a workload's logs or an API call return a Forbidden / cannot <verb> resource error"
  - "the pod runs under a non-default ServiceAccount"
---

# RBAC / Permission Failure

## Checklist
1. tool: get_pod_logs (or get_previous_logs if restarted)   conclusive: true
   conclusion_criteria: >
     logs contain a Forbidden error naming the ServiceAccount and the denied verb/resource,
     e.g. 'pods is forbidden: User "system:serviceaccount:<ns>:<sa>" cannot list resource'.
     The Forbidden text is itself the root cause.
2. tool: get_events   params: {namespace}   conclusive: false
   note: only if logs are unavailable.

## Conclusion
root_cause = the ServiceAccount lacks the RBAC permission named in the Forbidden error. Do NOT
enumerate ClusterRoles/RoleBindings one kind at a time — the error already names what's missing.
There is no ServiceAccount kind in get_resource; do not retry that call.

## Do not conclude
- Image pull failure.
- An application code bug unrelated to permissions.
- Insufficient CPU or memory.
```

- [ ] **Step 4: Create `playbooks/autoscaling.md`**

```markdown
---
playbook_id: autoscaling
category: autoscaling
trigger_conditions:
  - "an HPA is not scaling, or is stuck at min/max replicas"
  - "reports of load not being handled, or HPA metric unknown/missing"
---

# HorizontalPodAutoscaler Failure

## Checklist
1. tool: get_resource   params: {kind: HORIZONTALPODAUTOSCALER, name, namespace}   conclusive: true
   conclusion_criteria: >
     If a condition shows ScalingLimited=True reason TooManyReplicas AND currentReplicas ==
     maxReplicas → the HPA works but maxReplicas is too low. If the current CPU metric is
     unknown/missing or a condition says it could not compute the resource metric → check step 2.
2. tool: get_resource   params: {kind: DEPLOYMENT, name, namespace}   conclusive: true
   conclusion_criteria: >
     container has no resources.requests.cpu set → HPA cannot compute CPU utilization. This is
     the cause, NOT a metrics-server outage.

## Conclusion
Either maxReplicas is below observed demand (HPA healthy), or the target Deployment is missing a
CPU request so utilization can't be computed. State which, and name the HPA and Deployment.

## Do not conclude
- The Deployment is unhealthy or crashlooping.
- The cluster's metrics-server is down or uninstalled (unless top_pods/top_nodes also fail).
- Insufficient node resources.
- Autoscaling is "broken" when it is capped and working correctly.
```

- [ ] **Step 5: Create `playbooks/scheduling.md`**

```markdown
---
playbook_id: scheduling
category: scheduling
trigger_conditions:
  - "one or more pods are stuck Pending"
  - "a FailedScheduling event is present"
---

# Scheduling / Pending Pod Failure

## Checklist
1. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
2. tool: get_events          params: {namespace}                    conclusive: true
   conclusion_criteria: >
     read the FailedScheduling reason: 'insufficient cpu/memory' → capacity (confirm with
     top_nodes); 'untolerated taint' → a node taint with no matching pod toleration; taints named
     node.kubernetes.io/disk-pressure or memory-pressure → node health pressure.
3. tool: get_node_conditions   params: {name}   conclusive: true
   conclusion_criteria: >
     DiskPressure/MemoryPressure=True explains auto-applied NoSchedule taints; or the node's
     taints field shows the specific untolerated taint.
4. tool: top_nodes   conclusive: true
   note: only for the insufficient-resources branch — show allocatable < pod request.

## Conclusion
Name the specific scheduling barrier (insufficient CPU/memory vs. untolerated taint vs. node
pressure) and the node/pod involved.

## Do not conclude
- Image pull failure or CrashLoopBackOff.
- The pod's own resource requests are "misconfigured" when the real cause is a taint/capacity.
- An arbitrary/manual taint unrelated to node health when the taint is pressure-induced.
```

- [ ] **Step 6: Create `playbooks/resource_governance.md`**

```markdown
---
playbook_id: resource_governance
category: resource_governance
trigger_conditions:
  - "a Deployment has fewer ready replicas than desired with no crashing pods"
  - "a FailedCreate / exceeded quota event is present"
---

# ResourceQuota / Governance Failure

## Checklist
1. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: a FailedCreate event on the ReplicaSet mentioning 'exceeded quota'.
2. tool: get_resource   params: {kind: RESOURCEQUOTA, name, namespace}   conclusive: true
   conclusion_criteria: hard limit (e.g. pods=1) equals used, blocking further creation.
3. tool: get_resource   params: {kind: DEPLOYMENT, name, namespace}   conclusive: false
   note: desired replicas > ready/available confirms the shortfall is creation-blocked, not crashing.

## Conclusion
root_cause = a ResourceQuota caps a resource (e.g. pods) below what the Deployment requests, so
the ReplicaSet controller cannot create the remaining pods. Name the quota and the shortfall.

## Do not conclude
- The missing pods are crashlooping or failing health checks (they were never created).
- Image pull failure.
- Insufficient node CPU or memory capacity.
```

- [ ] **Step 7: Create `playbooks/storage.md`**

```markdown
---
playbook_id: storage
category: storage
trigger_conditions:
  - "a PVC is stuck Pending or a pod cannot mount a volume"
  - "an event mentions a StorageClass could not be found or no volumes available"
---

# Storage / PVC Failure

## Checklist
1. tool: get_resource   params: {kind: PERSISTENTVOLUMECLAIM, name, namespace}   conclusive: false
   note: status Pending is the starting signal.
2. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: an event on the PVC/pod naming a StorageClass that could not be found,
   or 'no persistent volumes available'.
3. tool: list_resources   params: {kind: STORAGECLASS}   conclusive: true
   conclusion_criteria: the referenced StorageClass name is absent from the list.

## Conclusion
root_cause = the PVC references a StorageClass that does not exist, so no PV can be provisioned
or bound and the pod stays Pending. Name the PVC and the missing StorageClass.

## Do not conclude
- Insufficient CPU or memory.
- Image pull failure.
- A node taint or scheduling issue unrelated to storage.
```

- [ ] **Step 8: Create `playbooks/secret_configmap.md`**

```markdown
---
playbook_id: secret_configmap
category: secret_configmap
trigger_conditions:
  - "a pod is stuck at CreateContainerConfigError or cannot start due to a missing env/volume source"
  - "an event mentions couldn't find key ... in Secret/ConfigMap"
---

# Secret / ConfigMap Reference Failure

## Checklist
1. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
   note: identify the referenced Secret/ConfigMap name and key from env/envFrom/volumes.
2. tool: get_events   params: {namespace}   conclusive: true
   conclusion_criteria: an event mentioning "couldn't find key <k> in Secret/ConfigMap <name>",
   or CreateContainerConfigError.
3. tool: get_resource   params: {kind: SECRET or CONFIGMAP, name, namespace}   conclusive: true
   conclusion_criteria: the object exists but the referenced key name is not present (mismatch).

## Conclusion
Report the missing reference as the root cause: "<pod> references key <k> in <kind> <name> which
does not exist" (name mismatch vs. genuinely absent object). Do not speculate about the GitOps
source — you cannot see it here.

## Do not conclude
- The Secret/ConfigMap itself does not exist (when it exists but the key is wrong).
- Image pull failure.
- Insufficient CPU or memory.
```

- [ ] **Step 9: Create `playbooks/rollout.md`**

```markdown
---
playbook_id: rollout
category: rollout
trigger_conditions:
  - "a Deployment rollout completed but the application still misbehaves"
  - "5xx/errors despite Running, ready pods with no restarts"
---

# Rollout-Complete-But-Broken Failure

## Checklist
1. tool: rollout_status   params: {deployment, namespace}   conclusive: false
   note: a COMPLETE rollout does NOT prove the app is healthy — keep going.
2. tool: describe_resource   params: {kind: POD, name, namespace}   conclusive: false
   note: Running with no restarts rules out crashloop/scheduling/image-pull.
3. tool: get_pod_logs (or recent_logs / error_logs by app)   conclusive: true
   conclusion_criteria: repeated FATAL / "unable to connect to upstream dependency" style
   application errors on an otherwise-Running pod.

## Conclusion
root_cause = the rollout succeeded but the application is functionally broken (e.g. cannot reach
a dependency). This is an application-level fault, not a rollout/scheduling/image failure — so it
is typically NOT remediable via a Kubernetes manifest change.

## Do not conclude
- The rollout itself failed, is stuck, or is still in progress.
- The pod is crashlooping or failing to start.
- An image pull or scheduling failure occurred.
```

- [ ] **Step 10: Run the tests to confirm they pass**

Run: `pytest test/unit_test/helpers/playbook_catalog_test.py test/unit_test/helpers/playbook_library_test.py -q`
Expected: `7 passed` (2 from catalog + 5 from library, all still green)

- [ ] **Step 11: Commit**

```bash
git add playbooks/rbac.md playbooks/autoscaling.md playbooks/scheduling.md playbooks/resource_governance.md playbooks/storage.md playbooks/secret_configmap.md playbooks/rollout.md test/unit_test/helpers/playbook_catalog_test.py
git commit -m "$(cat <<'EOF'
feat: add scenario playbooks for all integration-test failure classes

EOF
)"
```

---

## Task 4: Hypothesizer helper

**Files:**
- Create: `agents/helpers/hypothesizer.py`
- Modify: `agents/helpers/__init__.py`
- Test: `test/unit_test/helpers/hypothesizer_test.py`

**Interfaces:**
- Consumes: `HypothesisSelection` (Task 1), `PlaybookLibrary.list_triggers()` shape (Task 2).
- Produces: `Hypothesizer(llm)` with `async select(query: str, scope_summary: str, triggers: list[dict], ruled_out: list[dict]) -> HypothesisSelection`. Consumed by Task 6's `hypothesize_node`.

- [ ] **Step 1: Write the failing test**

Create `test/unit_test/helpers/hypothesizer_test.py` (synchronous, driving the async method with
`asyncio.run` — the repo has no async-test infrastructure, so do NOT use `@pytest.mark.anyio`):
```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

from agents.helpers import Hypothesizer
from models import HypothesisSelection


def _mock_llm(parsed, raw_content=""):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value={"parsed": parsed, "raw": MagicMock(content=raw_content)})
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


def test_select_returns_parsed_selection():
    parsed = HypothesisSelection(hypothesis="pvc pending", playbook_id="storage", reasoning="pending pvc")
    h = Hypothesizer(_mock_llm(parsed))
    result = asyncio.run(
        h.select(
            "why pending",
            "a pvc is Pending",
            [{"playbook_id": "storage", "category": "storage", "trigger_conditions": ["pvc pending"]}],
            [],
        )
    )
    assert result.playbook_id == "storage"


def test_select_falls_back_to_generic_on_null_parse():
    h = Hypothesizer(_mock_llm(None, raw_content="unparseable"))
    result = asyncio.run(h.select("q", "scope", [], []))
    assert result.playbook_id == "generic"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest test/unit_test/helpers/hypothesizer_test.py -q`
Expected: FAIL with `ImportError: cannot import name 'Hypothesizer'`

- [ ] **Step 3: Create `agents/helpers/hypothesizer.py`**

```python
from langchain_core.messages import SystemMessage

from models import HypothesisSelection


class Hypothesizer:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(HypothesisSelection, include_raw=True)

    async def select(
        self,
        query: str,
        scope_summary: str,
        triggers: list[dict],
        ruled_out: list[dict],
    ) -> HypothesisSelection:
        triggers_text = "\n".join(
            f"- {t['playbook_id']} ({t['category']}): " + "; ".join(t["trigger_conditions"])
            for t in triggers
        ) or "(no playbooks available)"
        ruled_out_text = "\n".join(
            f"- {r['hypothesis']} — ruled out because: {r['why_ruled_out']}" for r in ruled_out
        ) or "(none yet)"

        prompt = f"""
            You are triaging a Kubernetes incident like an SRE. Based on the scope summary,
            pick the single most likely leading hypothesis and the playbook to investigate it
            under.

            user query:
            {query}

            scope summary (initial cluster signals gathered):
            {scope_summary}

            available playbooks (playbook_id: trigger conditions):
            {triggers_text}

            hypotheses already ruled out (do NOT re-propose these or their playbooks):
            {ruled_out_text}

            Respond with structured output: hypothesis (one sentence), playbook_id (choose the
            best-matching playbook_id from the list above, or 'generic' if none clearly fits),
            and reasoning. Never pick a playbook whose hypothesis was already ruled out.
        """

        response = await self.llm.ainvoke([SystemMessage(content=prompt)])
        parsed = response["parsed"]
        if parsed is None:
            return HypothesisSelection(
                hypothesis="Could not form a specific hypothesis; using generic investigation.",
                playbook_id="generic",
                reasoning=response["raw"].content or "structured output unavailable",
            )
        return parsed
```

- [ ] **Step 4: Update `agents/helpers/__init__.py`**

Add:
```python
from .hypothesizer import Hypothesizer
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `pytest test/unit_test/helpers/hypothesizer_test.py -q`
Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add agents/helpers/hypothesizer.py agents/helpers/__init__.py test/unit_test/helpers/hypothesizer_test.py
git commit -m "$(cat <<'EOF'
feat: add Hypothesizer helper for playbook selection

EOF
)"
```

---

## Task 5: DiagnosisEvaluator helper

**Files:**
- Create: `agents/helpers/evaluator.py`
- Modify: `agents/helpers/__init__.py`
- Test: `test/unit_test/helpers/diagnosis_evaluator_test.py`

**Interfaces:**
- Consumes: `EvaluationVerdict` (Task 1).
- Produces: `DiagnosisEvaluator(llm)` with `async evaluate(query: str, hypothesis: str, playbook_body: str, messages: list, hypothesis_count: int, max_hypotheses: int) -> EvaluationVerdict`. Consumed by Task 6's `evaluate_node`.

- [ ] **Step 1: Write the failing test**

Create `test/unit_test/helpers/diagnosis_evaluator_test.py` (synchronous, driving the async
method with `asyncio.run` — do NOT use `@pytest.mark.anyio`):
```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

from agents.helpers import DiagnosisEvaluator
from models import EvaluationVerdict


def _mock_llm(parsed, raw_content=""):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value={"parsed": parsed, "raw": MagicMock(content=raw_content)})
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


def test_evaluate_returns_parsed_verdict():
    parsed = EvaluationVerdict(verdict="conclusive", reasoning="pvc missing sc", requires_remediation=True)
    ev = DiagnosisEvaluator(_mock_llm(parsed))
    result = asyncio.run(ev.evaluate("q", "pvc pending", "checklist", [], 1, 3))
    assert result.verdict == "conclusive"
    assert result.requires_remediation is True


def test_evaluate_falls_back_to_exhausted_on_null_parse():
    ev = DiagnosisEvaluator(_mock_llm(None, raw_content="garbage"))
    result = asyncio.run(ev.evaluate("q", "h", "cl", [], 3, 3))
    assert result.verdict == "exhausted"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest test/unit_test/helpers/diagnosis_evaluator_test.py -q`
Expected: FAIL with `ImportError: cannot import name 'DiagnosisEvaluator'`

- [ ] **Step 3: Create `agents/helpers/evaluator.py`**

```python
from langchain_core.messages import BaseMessage, SystemMessage

from models import EvaluationVerdict


class DiagnosisEvaluator:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(EvaluationVerdict, include_raw=True)

    async def evaluate(
        self,
        query: str,
        hypothesis: str,
        playbook_body: str,
        messages: list[BaseMessage],
        hypothesis_count: int,
        max_hypotheses: int,
    ) -> EvaluationVerdict:
        budget_note = (
            "This is the final hypothesis allowed; if the evidence is not conclusive you must "
            "return 'exhausted', not 'reformulate'."
            if hypothesis_count >= max_hypotheses
            else f"{max_hypotheses - hypothesis_count} more hypothesis attempt(s) remain if needed."
        )
        prompt = f"""
            You are evaluating whether the investigation above has reached a confident root cause
            for the current hypothesis.

            user query:
            {query}

            current hypothesis:
            {hypothesis}

            playbook conclusion criteria used:
            {playbook_body}

            budget:
            {budget_note}

            Decide one verdict:
            - conclusive: the evidence supports a confident root cause. Set requires_remediation
              true if the cause is fixable via a Kubernetes config/manifest change, false if it is
              an application-level bug or an informational finding with nothing to fix.
            - reformulate: the current hypothesis is ruled out but a different, specific hypothesis
              is worth trying. Set next_hypothesis and why_ruled_out. Only use this if attempts remain.
            - exhausted: no confident conclusion and no new hypothesis worth pursuing (or the budget
              is spent).

            Respond with structured output.
        """
        response = await self.llm.ainvoke(messages + [SystemMessage(content=prompt)])
        parsed = response["parsed"]
        if parsed is None:
            return EvaluationVerdict(
                verdict="exhausted",
                reasoning=response["raw"].content or "structured output unavailable",
            )
        return parsed
```

- [ ] **Step 4: Update `agents/helpers/__init__.py`**

Add:
```python
from .evaluator import DiagnosisEvaluator
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `pytest test/unit_test/helpers/diagnosis_evaluator_test.py -q`
Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add agents/helpers/evaluator.py agents/helpers/__init__.py test/unit_test/helpers/diagnosis_evaluator_test.py
git commit -m "$(cat <<'EOF'
feat: add DiagnosisEvaluator helper for the 3-way verdict

EOF
)"
```

---

## Task 6: Rewrite DiagnosisAgent as the phase graph

**Files:**
- Modify: `graph/state.py` (DiagnosisAgentState fields)
- Modify: `agents/diagnosis_agent.py` (full rewrite of graph + nodes)
- Modify: `graph/nodes.py` (seed new fields in `make_diagnose_node`)
- Test: `test/unit_test/diagnosis_agent/phase_routing_test.py`

**Interfaces:**
- Consumes: `PlaybookLibrary`, `Hypothesizer`, `DiagnosisEvaluator` (Tasks 2,4,5); `Classifier`, `HistoryCompactor`, `find_repeated_calls` (existing).
- Produces: a `DiagnosisAgent` whose `ainvoke` still returns `{"diagnosis_result": DiagnosisResult}` — unchanged external contract.

- [ ] **Step 1: Update `graph/state.py`'s `DiagnosisAgentState`**

Replace:
```python
class DiagnosisAgentState(MessagesState):
    query: str
    iteration_count: int
    diagnosis_result: DiagnosisResult
```
with:
```python
class DiagnosisAgentState(MessagesState):
    query: str
    diagnosis_result: DiagnosisResult
    # SRE phase machinery
    scope_summary: str
    current_hypothesis: str
    selected_playbook_id: str
    ruled_out: list[dict]
    hypothesis_count: int
    investigate_iterations: int
    last_verdict: str
    requires_remediation_hint: bool | None
```

- [ ] **Step 2: Write the failing routing test**

Create `test/unit_test/diagnosis_agent/phase_routing_test.py`:
```python
from unittest.mock import MagicMock

from agents.diagnosis_agent import DiagnosisAgent


def _agent():
    # Build an agent without touching the network: patch the heavy collaborators.
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    agent = DiagnosisAgent.__new__(DiagnosisAgent)
    agent.MAX_INVESTIGATE_ITERATIONS = 8
    agent.MAX_HYPOTHESES = 3
    return agent


def _ai(tool_calls):
    m = MagicMock()
    m.tool_calls = tool_calls
    return m


def test_investigate_routing_goes_to_tool_when_calls_and_budget_left():
    agent = _agent()
    state = {"messages": [_ai([{"id": "1"}])], "investigate_iterations": 2}
    assert agent._investigate_routing(state) == "tool_node"


def test_investigate_routing_evaluates_when_budget_exhausted():
    agent = _agent()
    state = {"messages": [_ai([{"id": "1"}])], "investigate_iterations": 8}
    assert agent._investigate_routing(state) == "evaluate_node"


def test_investigate_routing_evaluates_when_no_tool_calls():
    agent = _agent()
    state = {"messages": [_ai([])], "investigate_iterations": 1}
    assert agent._investigate_routing(state) == "evaluate_node"


def test_evaluate_routing_reformulate_loops_when_budget_left():
    agent = _agent()
    state = {"last_verdict": "reformulate", "hypothesis_count": 1}
    assert agent._evaluate_routing(state) == "hypothesize_node"


def test_evaluate_routing_reformulate_finalizes_at_cap():
    agent = _agent()
    state = {"last_verdict": "reformulate", "hypothesis_count": 3}
    assert agent._evaluate_routing(state) == "finalize_node"


def test_evaluate_routing_conclusive_finalizes():
    agent = _agent()
    state = {"last_verdict": "conclusive", "hypothesis_count": 1}
    assert agent._evaluate_routing(state) == "finalize_node"
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `pytest test/unit_test/diagnosis_agent/phase_routing_test.py -q`
Expected: FAIL with `AttributeError: 'DiagnosisAgent' object has no attribute '_investigate_routing'`

- [ ] **Step 4: Rewrite `agents/diagnosis_agent.py`**

Replace the entire file with:
```python
import logging
from typing import Any, Literal

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from models import DiagnosisResult
from .helpers import (
    Classifier,
    DiagnosisEvaluator,
    HistoryCompactor,
    Hypothesizer,
    PlaybookLibrary,
    find_repeated_calls,
)
from graph.state import DiagnosisAgentState

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")

SCOPE_PROMPT = """
You are an SRE scoping a Kubernetes incident. Gather ONLY enough high-level signal to
understand the scene: which namespace/workload is involved, pod phases, notable recent events,
and overall health. Stay broad and shallow — do NOT deep-dive a single hypothesis yet.

You may make at most a few discovery calls. When you have enough to describe the scene, STOP
calling tools and reply with a concise scene summary (2-5 sentences): the affected workload,
observed symptoms, and the most notable signals. That summary is your only output.

user query:
{query}
"""

INVESTIGATE_PROMPT = """
You are an SRE gathering evidence for a specific hypothesis, following a playbook.

user query:
{query}

scope summary:
{scope_summary}

current hypothesis:
{hypothesis}

hypotheses already ruled out (do not re-investigate these):
{ruled_out}

playbook to follow:
{playbook}

Work the playbook's checklist to confirm or reject the hypothesis. Reuse evidence already in the
conversation instead of re-fetching it. When the checklist's conclusion criteria are met (or you
can already reject the hypothesis), stop calling tools and state your finding in plain text.
"""


class DiagnosisAgent:
    def __init__(
        self,
        llm: BaseChatModel,
        tools: list[BaseTool],
        classifier_llm: BaseChatModel | None = None,
        compactor_llm: BaseChatModel | None = None,
    ) -> None:
        self.tools = tools
        self.logger = logging.getLogger("diagnosisAgent")
        self.MAX_SCOPE_CALLS = 3
        self.MAX_INVESTIGATE_ITERATIONS = 8
        self.MAX_HYPOTHESES = 3

        cheap = classifier_llm or llm
        self.llm = llm.bind_tools(tools)            # main model, used only in investigate
        self.scope_llm = cheap.bind_tools(tools)    # cheap model, used only in scope
        self.playbooks = PlaybookLibrary()
        self.hypothesizer = Hypothesizer(cheap)
        self.evaluator = DiagnosisEvaluator(cheap)
        self.classifier = Classifier(cheap)
        self.history_compactor = HistoryCompactor(compactor_llm or llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=DiagnosisAgentState)
        graph.add_node("scope_node", self._scope_node)
        graph.add_node("hypothesize_node", self._hypothesize_node)
        graph.add_node("investigate_node", self._investigate_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("evaluate_node", self._evaluate_node)
        graph.add_node("finalize_node", self._finalize_node)

        graph.add_edge(START, "scope_node")
        graph.add_edge("scope_node", "hypothesize_node")
        graph.add_edge("hypothesize_node", "investigate_node")
        graph.add_conditional_edges(
            "investigate_node",
            self._investigate_routing,
            {"tool_node": "tool_node", "evaluate_node": "evaluate_node"},
        )
        graph.add_edge("tool_node", "investigate_node")
        graph.add_conditional_edges(
            "evaluate_node",
            self._evaluate_routing,
            {"hypothesize_node": "hypothesize_node", "finalize_node": "finalize_node"},
        )
        graph.add_edge("finalize_node", END)
        return graph.compile()

    async def _scope_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        self.logger.info("\n=== scope ===")
        messages: list[Any] = [SystemMessage(content=SCOPE_PROMPT.format(query=state["query"]))]
        summary = ""
        for _ in range(self.MAX_SCOPE_CALLS):
            response = await self.scope_llm.ainvoke(messages)
            messages.append(response)
            if not response.tool_calls:
                summary = str(response.content)
                break
            for call in response.tool_calls:
                self.logger.info(f"  scope -> {call['name']}({call['args']})")
            tool_result = await self._tool_executor.ainvoke({"messages": messages})
            messages.extend(tool_result["messages"])
        else:
            # budget hit while still calling tools — summarize what we have
            final = await self.scope_llm.ainvoke(
                messages + [SystemMessage(content="Stop. Reply with the concise scene summary now, no tool calls.")]
            )
            summary = str(final.content)
        self.logger.info(f"  scope_summary: {self._preview(summary)}")
        return {
            "scope_summary": summary,
            "hypothesis_count": 0,
            "investigate_iterations": 0,
            "ruled_out": [],
        }

    async def _hypothesize_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        count = state.get("hypothesis_count", 0) + 1
        self.logger.info(f"\n=== hypothesize (attempt {count}/{self.MAX_HYPOTHESES}) ===")
        selection = await self.hypothesizer.select(
            state["query"],
            state.get("scope_summary", ""),
            self.playbooks.list_triggers(),
            state.get("ruled_out", []),
        )
        self.logger.info(f"  hypothesis: {self._preview(selection.hypothesis)} (playbook={selection.playbook_id})")
        return {
            "current_hypothesis": selection.hypothesis,
            "selected_playbook_id": selection.playbook_id,
            "hypothesis_count": count,
            "investigate_iterations": 0,
        }

    async def _investigate_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        iteration = state.get("investigate_iterations", 0) + 1
        self.logger.info(f"\n=== investigate {iteration}/{self.MAX_INVESTIGATE_ITERATIONS} ===")
        playbook = self.playbooks.get(state["selected_playbook_id"])
        ruled_out = "\n".join(
            f"- {r['hypothesis']}: {r['why_ruled_out']}" for r in state.get("ruled_out", [])
        ) or "(none)"
        system_prompt = INVESTIGATE_PROMPT.format(
            query=state["query"],
            scope_summary=state.get("scope_summary", ""),
            hypothesis=state.get("current_hypothesis", ""),
            ruled_out=ruled_out,
            playbook=playbook.body,
        )
        history, compaction_edits = self.history_compactor.compact(state["messages"])
        messages = [SystemMessage(content=system_prompt)] + history
        response = await self.llm.ainvoke(messages)
        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")
        return {"messages": [*compaction_edits, response], "investigate_iterations": iteration}

    async def _tool_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        messages = state["messages"]
        last_message = messages[-1]
        all_calls = last_message.tool_calls
        repeats = find_repeated_calls(messages)

        fresh_messages: list[Any] = []
        fresh_calls = [call for call in all_calls if call["id"] not in repeats]
        if fresh_calls:
            pruned_last = last_message.model_copy(update={"tool_calls": fresh_calls})
            fresh_state = {**state, "messages": [*messages[:-1], pruned_last]}
            fresh_result = await self._tool_executor.ainvoke(fresh_state)
            fresh_messages = fresh_result["messages"]

        repeat_messages = [
            ToolMessage(
                content=(
                    "[duplicate call suppressed] This exact call was already made earlier in "
                    f"this investigation and returned:\n{content}\n"
                    "Calling it again will not produce a different result -- use the evidence "
                    "you already have, or investigate something else."
                ),
                name=next(call["name"] for call in all_calls if call["id"] == call_id),
                tool_call_id=call_id,
            )
            for call_id, content in repeats.items()
        ]

        order = {call["id"]: i for i, call in enumerate(all_calls)}
        result = {"messages": sorted(fresh_messages + repeat_messages, key=lambda m: order[m.tool_call_id])}
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _investigate_routing(self, state: DiagnosisAgentState) -> Literal["tool_node", "evaluate_node"]:
        last_message = state["messages"][-1]
        has_calls = bool(getattr(last_message, "tool_calls", None))
        if has_calls and state.get("investigate_iterations", 0) < self.MAX_INVESTIGATE_ITERATIONS:
            return "tool_node"
        return "evaluate_node"

    async def _evaluate_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        self.logger.info("\n=== evaluate ===")
        playbook = self.playbooks.get(state["selected_playbook_id"])
        verdict = await self.evaluator.evaluate(
            state["query"],
            state.get("current_hypothesis", ""),
            playbook.body,
            state["messages"],
            state.get("hypothesis_count", 0),
            self.MAX_HYPOTHESES,
        )
        self.logger.info(f"  verdict={verdict.verdict} ({self._preview(verdict.reasoning)})")
        update: dict[str, Any] = {
            "last_verdict": verdict.verdict,
            "requires_remediation_hint": verdict.requires_remediation,
        }
        if verdict.verdict == "reformulate":
            update["ruled_out"] = state.get("ruled_out", []) + [
                {
                    "hypothesis": state.get("current_hypothesis", ""),
                    "why_ruled_out": verdict.why_ruled_out or verdict.reasoning,
                }
            ]
        return update

    def _evaluate_routing(self, state: DiagnosisAgentState) -> Literal["hypothesize_node", "finalize_node"]:
        if state.get("last_verdict") == "reformulate" and state.get("hypothesis_count", 0) < self.MAX_HYPOTHESES:
            return "hypothesize_node"
        return "finalize_node"

    async def _finalize_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        self.logger.info("\n=== finalize ===")
        if state.get("last_verdict") == "exhausted" or (
            state.get("last_verdict") == "reformulate" and state.get("hypothesis_count", 0) >= self.MAX_HYPOTHESES
        ):
            self.logger.warning("  investigation exhausted — escalating (diagnosis_success=False)")
            return {
                "diagnosis_result": DiagnosisResult(
                    summary=(
                        "Investigation did not reach a confident root cause within the hypothesis "
                        f"budget ({self.MAX_HYPOTHESES} hypotheses). Escalating to human review "
                        "rather than risking a false negative."
                    ),
                    root_cause=None,
                    requires_remediation=True,
                    diagnosis_success=False,
                )
            }
        parsed = await self.classifier.classify(state["query"], state["messages"])
        hint = state.get("requires_remediation_hint")
        if hint is not None and parsed.diagnosis_success:
            parsed = parsed.model_copy(update={"requires_remediation": hint})
        self.logger.info(f"  requires_remediation={parsed.requires_remediation} summary={self._preview(parsed.summary)}")
        return {"diagnosis_result": parsed}

    @staticmethod
    def _preview(text: Any, limit: int = 500) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, state: DiagnosisAgentState):
        return self.graph.invoke(state)

    async def ainvoke(self, state: DiagnosisAgentState):
        return await self.graph.ainvoke(state)
```

- [ ] **Step 5: Seed the new fields in `graph/nodes.py::make_diagnose_node`**

Replace the `ainvoke` call in `diagnose_node`:
```python
        result = await diagnosis_agent.ainvoke({
            "messages": [],
            "query": state["query"],
            "iteration_count": 0,
        })
```
with:
```python
        result = await diagnosis_agent.ainvoke({
            "messages": [],
            "query": state["query"],
            "scope_summary": "",
            "current_hypothesis": "",
            "selected_playbook_id": "",
            "ruled_out": [],
            "hypothesis_count": 0,
            "investigate_iterations": 0,
            "last_verdict": "",
            "requires_remediation_hint": None,
        })
```

- [ ] **Step 6: Run the routing tests to confirm they pass**

Run: `pytest test/unit_test/diagnosis_agent/phase_routing_test.py -q`
Expected: `6 passed`

- [ ] **Step 7: Confirm the graph compiles and the module imports cleanly**

Run:
```bash
python3 -c "import ast; ast.parse(open('agents/diagnosis_agent.py').read())" && echo PARSE_OK
python3 -c "
from unittest.mock import MagicMock
from agents.diagnosis_agent import DiagnosisAgent
llm = MagicMock(); llm.bind_tools.return_value = llm
agent = DiagnosisAgent(llm, [], classifier_llm=llm, compactor_llm=llm)
print('nodes:', sorted(agent.graph.get_graph().nodes.keys()))
"
```
Expected: `PARSE_OK`, then a node list containing `scope_node`, `hypothesize_node`, `investigate_node`, `tool_node`, `evaluate_node`, `finalize_node` (plus `__start__`/`__end__`). This constructs a real `DiagnosisAgent` (which loads the 9 real playbooks via `PlaybookLibrary`), proving construction + graph compilation work end-to-end with a mocked LLM.

- [ ] **Step 8: Run the full unit suite for zero regressions**

Run: `pytest -q 2>&1 | tail -6`
Expected: all green; count = previous baseline (158) + the new tests added across Tasks 1-6 (4 + 5 + 2 + 2 + 2 + 6 = 21) = `179 passed` (deselected count unchanged at 10). If the exact total differs, confirm the delta is only the newly added tests and no prior test regressed.

- [ ] **Step 9: Commit**

```bash
git add graph/state.py agents/diagnosis_agent.py graph/nodes.py test/unit_test/diagnosis_agent/phase_routing_test.py
git commit -m "$(cat <<'EOF'
feat: restructure DiagnosisAgent into SRE phase graph

Replace the flat ReAct loop with scope -> hypothesize -> investigate <-> tool
-> evaluate -> {reformulate | finalize}, driven by playbooks, with per-phase
budgets and cheap-model tiering. DiagnosisResult contract unchanged.

EOF
)"
```

---

## Task 7: Final validation

**Files:** none (validation only).

- [ ] **Step 1: Confirm no downstream contract broke (grep for removed symbols)**

Run:
```bash
grep -rn "iteration_count" graph/ agents/diagnosis_agent.py || echo "iteration_count fully removed from diagnosis path"
grep -rn "MAX_ITERATIONS" agents/diagnosis_agent.py || echo "MAX_ITERATIONS removed"
```
Expected: both echo their "removed" message (no stray references in the diagnosis path). Note: `PlannerAgent`/`RemediationAgent` legitimately keep their own `iteration_count`/`MAX_ITERATIONS` — this grep is scoped to `graph/` and the diagnosis agent only.

- [ ] **Step 2: Syntax-check every file touched by this plan**

Run:
```bash
source .venv/bin/activate
for f in models/hypothesis.py models/evaluation.py models/__init__.py \
         agents/helpers/playbook_library.py agents/helpers/hypothesizer.py \
         agents/helpers/evaluator.py agents/helpers/__init__.py \
         agents/diagnosis_agent.py graph/state.py graph/nodes.py; do
  python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK: $f"
done
```
Expected: `OK: <path>` for all 10 files.

- [ ] **Step 3: Confirm all 9 playbooks load through the real library**

Run:
```bash
python3 -c "
from agents.helpers import PlaybookLibrary
lib = PlaybookLibrary()
ids = sorted(t['playbook_id'] for t in lib.list_triggers())
print(ids)
assert len(ids) == 9, ids
assert lib.get('nonsense').playbook_id == 'generic'
print('OK')
"
```
Expected: the sorted list of 9 ids, then `OK`.

- [ ] **Step 4: Full unit suite**

Run: `pytest -q 2>&1 | tail -6`
Expected: `179 passed, 10 deselected` (or the confirmed total from Task 6 Step 8), zero failures.

- [ ] **Step 5: Record the integration acceptance bar (manual, not run here)**

This plan's automated verification stops at unit tests. The **acceptance bar** is the existing
integration suite, run manually against a live cluster once this branch is deployed:
```bash
pytest -m integration -s -v
```
Expected when run: the 10 scenarios across 7 categories still pass via `ScenarioEvaluator`, with
lower per-scenario iteration/token counts than the pre-restructure baseline. Do NOT run this as
part of this plan (it needs a live cluster + real API spend); note it for the reviewer/operator.
```
```

- [ ] **Step 6: No commit (validation only).**
