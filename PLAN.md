# DiagnosisAgent Token Cost Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the two highest-priority root causes from `REVIEW.md`'s `DiagnosisAgent` token-cost
investigation: (1) no history compaction, so per-turn cost grows unbounded across a run; (2)
`get_resource`/`list_resources` handing the LLM full raw `kubectl` manifests instead of the
minimal identity/discovery information a diagnosis actually needs.

**Architecture:** No new components. Task 1 wires the existing, already-generic
`HistoryCompactor` (`agents/helpers/compaction.py`, built during the `RemediationAgent` loop fix
specifically so other agents could reuse it) into `DiagnosisAgent`, the same way it's already
wired into `RemediationAgent`. Task 2 adds one small projection helper to
`mcp_servers/k8s_mcp_server/k8s_tools.py`, applied to both `get_resource` and `list_resources`:
instead of the full manifest, both now return only `{name, namespace, labels,
creationTimestamp, ownerReferences}`. Deep runtime/spec detail (image, replicas, env vars,
conditions) is no longer available from these two tools — `describe_resource` remains the tool
for that. Both tasks are independently testable.

**Tech Stack:** Python, LangGraph (`MessagesState`, `RemoveMessage`), `langchain_core.messages`,
pytest, `unittest.mock`.

## Global Constraints

- Scope is `agents/diagnosis_agent.py` and `mcp_servers/k8s_mcp_server/k8s_tools.py` only.
  `agents/planner_agent.py`, `agents/remediation_agent.py`, `describe_resource`,
  `get_pod_logs`/`get_previous_logs`, `get_events`, and
  `mcp_servers/prometheus_mcp_server/promql_tools.py` are all out of scope — do not touch them.
- Do not change any tool's public parameters (`kind`, `name`, `namespace`, `label_selector`) or
  `DiagnosisAgent`'s public `invoke`/`ainvoke` signatures — only the _content_ of what
  `get_resource`/`list_resources` return changes, never the interface.
- Task 2's reduction is a deliberate, fixed 5-field projection (`name`, `namespace`, `labels`,
  `creationTimestamp`, `ownerReferences`) — not a "strip a couple of noisy fields, keep the rest"
  approach. `spec`/`status`/all other manifest fields are dropped entirely from these two tools
  by design, per explicit instruction — this is a scope decision already made, not something to
  reconsider mid-implementation.
- After every task: the relevant test file must pass in full (existing tests + new ones), and
  `pytest` (full unit suite) must stay green.
- Follow existing test conventions: `test/unit_test/k8s_agent/k8s_tools_test.py`'s `mock_run`
  fixture (patches `k8s_tools.subprocess.run`) and calling `@mcp.tool`-decorated functions
  directly (e.g. `k8s_tools.get_resource(...)`, no `.func()` — FastMCP's decorator, unlike
  LangChain's `@tool` used in `tools/file_tools.py`, doesn't require it).
- The two tasks are independent of each other (different files) — order doesn't matter for
  correctness, but execute in the order below for a clean progress ledger.

---

## Task 1: Wire `HistoryCompactor` into `DiagnosisAgent`

**Files:**

- Modify: `agents/diagnosis_agent.py:13` (import), `agents/diagnosis_agent.py:92-93` (`__init__`), `agents/diagnosis_agent.py:113-129` (`_reasoning_node`)
- Test: `test/unit_test/diagnosis_agent/diagnosis_agent_test.py` (new file — no unit tests exist for `DiagnosisAgent` today, only integration tests)

**Interfaces:**

- Consumes: `HistoryCompactor.__init__(llm)` and `HistoryCompactor.compact(messages: list[BaseMessage]) -> tuple[list[BaseMessage], list[BaseMessage]]` (`agents/helpers/compaction.py` — already implemented, tested, and in production use by `RemediationAgent`; no changes needed to it).

`_reasoning_node` (`agents/diagnosis_agent.py:113-129`) currently resends `state["messages"]` in
full every turn — the exact pattern that caused `RemediationAgent`'s cost problem before
`HistoryCompactor` existed, per `REVIEW.md`'s evidence (input tokens growing 4,957 → 23,157 across
one run). Fix: adopt `HistoryCompactor` exactly the way `RemediationAgent` already does
(`agents/remediation_agent.py:144` constructs it from the raw, un-tool-bound `llm`;
`agents/remediation_agent.py:167-168,179` calls `compact()` before building the LLM call and
folds `compaction_edits` into the returned `"messages"`). This is a pure reuse of already-built,
already-tested code — no new logic.

- [ ] **Step 1: Write the failing tests**

Create `test/unit_test/diagnosis_agent/diagnosis_agent_test.py`:

```python
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage

from agents.diagnosis_agent import DiagnosisAgent


def _make_agent():
    raw_llm = MagicMock()
    bound_llm = MagicMock()
    raw_llm.bind_tools.return_value = bound_llm
    agent = DiagnosisAgent(llm=raw_llm, tools=[])
    return agent, bound_llm


def test_reasoning_node_sends_uncompacted_history_when_under_threshold():
    agent, bound_llm = _make_agent()
    bound_llm.invoke.return_value = AIMessage(content="done")

    history = [
        AIMessage(
            content="",
            tool_calls=[{"name": "list_namespaces", "args": {}, "id": "call-1"}],
            id="ai-1",
        ),
        ToolMessage(content="result", tool_call_id="call-1", id="tool-1"),
    ]
    state = {"query": "why is it broken", "messages": history, "iteration_count": 0}

    result = agent._reasoning_node(state)

    sent_messages = bound_llm.invoke.call_args.args[0]
    # system prompt + the 2 history messages, unchanged (well under the default threshold)
    assert len(sent_messages) == 3
    assert sent_messages[1].id == "ai-1"
    assert sent_messages[2].id == "tool-1"
    assert result["messages"] == [bound_llm.invoke.return_value]


def test_reasoning_node_compacts_history_and_persists_the_removals():
    agent, bound_llm = _make_agent()
    bound_llm.invoke.return_value = AIMessage(content="continuing")
    agent.history_compactor.threshold_chars = 50
    agent.history_compactor.keep_recent_pairs = 1
    agent.history_compactor.llm.invoke.return_value = AIMessage(
        content="Investigated namespaces and pods so far, no root cause yet."
    )

    history = []
    for i in (1, 2, 3):
        history.append(
            AIMessage(
                content="",
                tool_calls=[{"name": "list_resources", "args": {"kind": "pod"}, "id": f"call-{i}"}],
                id=f"ai-{i}",
            )
        )
        history.append(ToolMessage(content=f"result {i}" * 20, tool_call_id=f"call-{i}", id=f"tool-{i}"))

    state = {"query": "why is it broken", "messages": history, "iteration_count": 0}

    result = agent._reasoning_node(state)

    sent_messages = bound_llm.invoke.call_args.args[0]
    # system prompt + summary + the one kept turn (turn 3)
    assert len(sent_messages) == 4
    assert sent_messages[1].content.startswith("[Earlier progress summary]")
    assert sent_messages[2].id == "ai-3"
    assert sent_messages[3].id == "tool-3"

    returned_messages = result["messages"]
    removed_ids = {m.id for m in returned_messages if isinstance(m, RemoveMessage)}
    assert removed_ids == {"ai-1", "tool-1", "ai-2", "tool-2", "ai-3", "tool-3"}
    non_removals = [m for m in returned_messages if not isinstance(m, RemoveMessage)]
    assert non_removals[0].content.startswith("[Earlier progress summary]")
    assert non_removals[-1] is bound_llm.invoke.return_value
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/diagnosis_agent/diagnosis_agent_test.py -v`
Expected: FAIL — `agent.history_compactor` doesn't exist yet (`AttributeError`), since
`DiagnosisAgent.__init__` doesn't construct one.

- [ ] **Step 3: Implement**

In `agents/diagnosis_agent.py`, update the import at line 13 — replace:

```python
from .helpers import Classifier
```

with:

```python
from .helpers import Classifier, HistoryCompactor
```

In `__init__` (around line 92-93), replace:

```python
        self.classifier = Classifier(llm)
        self.graph = self._build_graph()
```

with:

```python
        self.classifier = Classifier(llm)
        self.history_compactor = HistoryCompactor(llm)
        self.graph = self._build_graph()
```

Replace `_reasoning_node` (`agents/diagnosis_agent.py:113-129`) with:

```python
    def _reasoning_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nquery:\n{state['query']}"

        history, compaction_edits = self.history_compactor.compact(state["messages"])
        messages = [SystemMessage(content=system_prompt)] + history
        response = self.llm.invoke(messages)

        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")

        if compaction_edits:
            self.logger.info(f"  compacted {len(compaction_edits) - 1} old messages into a summary")

        return {
            "messages": [*compaction_edits, response],
            "iteration_count": iteration,
        }
```

(`_finalize_node` needs no change — it already reads from `state["messages"]`, so once this is
in place it automatically sees the compacted history too, per `REVIEW.md`'s note that its cost
drops "for free.")

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/diagnosis_agent/diagnosis_agent_test.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agents/diagnosis_agent.py test/unit_test/diagnosis_agent/diagnosis_agent_test.py
git commit -m "fix: wire HistoryCompactor into DiagnosisAgent to bound per-turn token growth"
```

---


## Task 2: Project `get_resource`/`list_resources` down to a minimal identity/discovery shape

**Files:**

- Modify: `mcp_servers/k8s_mcp_server/k8s_tools.py:97-128` (`get_resource`), `mcp_servers/k8s_mcp_server/k8s_tools.py:131-164` (`list_resources`)
- Test: `test/unit_test/k8s_agent/k8s_tools_test.py`

**Interfaces:**

- Produces: `_project_resource_summary(manifest: dict) -> dict`, returning exactly
  `{"name": str | None, "namespace": str | None, "labels": dict, "creationTimestamp": str | None,
  "ownerReferences": list}` — used by both `get_resource` and `list_resources`.

Both tools currently return `kubectl -o json`'s output completely raw (a full manifest, or a list
of them). Per explicit instruction, reduce both to a fixed minimal shape useful for identifying
and orienting around a resource — name, namespace, labels, creation time, and its ownership chain
(e.g. a Pod's `ownerReferences` pointing at its ReplicaSet, which points at its Deployment) —
dropping `spec`/`status`/everything else. This is a deliberate capability change, not a "keep
everything but noise" trim: after this task, neither tool can show an image tag, replica count,
env var, or status condition — `describe_resource` (unchanged, out of scope for this plan) is the
tool for that.

- [ ] **Step 1: Write the failing tests**

Add to `test/unit_test/k8s_agent/k8s_tools_test.py`, a new section right before the `get_resource`
section:

```python
# ------------------------------------------------------------------
# _project_resource_summary
# ------------------------------------------------------------------

def test_project_resource_summary_extracts_minimal_fields():
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "my-pod",
            "namespace": "dev",
            "labels": {"app": "backend"},
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "ownerReferences": [{"kind": "ReplicaSet", "name": "backend-abc123"}],
            "managedFields": [{"manager": "kubectl"}],
            "annotations": {"kubectl.kubernetes.io/last-applied-configuration": "{...}"},
        },
        "spec": {"containers": [{"image": "backend:v2"}]},
        "status": {"phase": "Running"},
    }

    result = k8s_tools._project_resource_summary(manifest)

    assert result == {
        "name": "my-pod",
        "namespace": "dev",
        "labels": {"app": "backend"},
        "creationTimestamp": "2026-01-01T00:00:00Z",
        "ownerReferences": [{"kind": "ReplicaSet", "name": "backend-abc123"}],
    }


def test_project_resource_summary_defaults_missing_fields():
    manifest = {"metadata": {"name": "my-node"}}

    result = k8s_tools._project_resource_summary(manifest)

    assert result == {
        "name": "my-node",
        "namespace": None,
        "labels": {},
        "creationTimestamp": None,
        "ownerReferences": [],
    }


def test_project_resource_summary_handles_missing_metadata():
    result = k8s_tools._project_resource_summary({"kind": "Pod"})

    assert result == {
        "name": None,
        "namespace": None,
        "labels": {},
        "creationTimestamp": None,
        "ownerReferences": [],
    }
```

Then, in the existing `get_resource` section, **replace** the existing `test_get_resource_success`
with:

```python
def test_get_resource_success(mock_run):
    manifest = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "my-pod"}}
    mock_run.return_value = make_completed_process(stdout=json.dumps(manifest))

    result = k8s_tools.get_resource(ResourceKind.POD, "my-pod", "default")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "pod", "my-pod", "-n", "default", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == {
        "name": "my-pod",
        "namespace": None,
        "labels": {},
        "creationTimestamp": None,
        "ownerReferences": [],
    }
```

(All other `get_resource` tests — `test_get_resource_uses_default_namespace`,
`test_get_resource_not_found_propagates`, and the three cluster-scoped-kind tests — only assert
on `mock_run`'s call arguments, never on the return value, so they need no changes.)

In the existing `list_resources` section, **replace** `test_list_resources_success` with:

```python
def test_list_resources_success(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "kind": "List",
        "items": [
            {"metadata": {"name": "api"}},
            {"metadata": {"name": "worker"}},
        ],
    }))

    result = k8s_tools.list_resources(ResourceKind.DEPLOYMENT, "default")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "deployment", "-n", "default", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == [
        {"name": "api", "namespace": None, "labels": {}, "creationTimestamp": None, "ownerReferences": []},
        {"name": "worker", "namespace": None, "labels": {}, "creationTimestamp": None, "ownerReferences": []},
    ]
```

And **replace** `test_list_resources_with_label_selector` with:

```python
def test_list_resources_with_label_selector(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "items": [{"metadata": {"name": "api", "labels": {"app": "my-service"}}}],
    }))

    result = k8s_tools.list_resources(ResourceKind.POD, "default", label_selector="app=my-service")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "pod", "-n", "default", "-l", "app=my-service", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == [
        {
            "name": "api",
            "namespace": None,
            "labels": {"app": "my-service"},
            "creationTimestamp": None,
            "ownerReferences": [],
        },
    ]
```

(`test_list_resources_returns_list_not_envelope` and
`test_list_resources_missing_items_key_defaults_to_empty_list` both assert `result == []` for an
empty/missing `items` list — an empty list projects to an empty list either way, so these need no
changes. All other `list_resources` tests only assert on `mock_run`'s call arguments.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v -k "project_resource_summary or get_resource_success or list_resources_success or list_resources_with_label_selector"`
Expected: FAIL — the `_project_resource_summary` tests fail with `AttributeError` (doesn't exist
yet); the three updated tests fail because `get_resource`/`list_resources` still return full
manifests, not the projected shape.

- [ ] **Step 3: Implement**

In `mcp_servers/k8s_mcp_server/k8s_tools.py`, add right before `get_resource` (after the
`_CLUSTER_SCOPED_KINDS` block):

```python
def _project_resource_summary(manifest: dict) -> dict:
    """
    Reduce a manifest to the minimal fields useful for identifying and orienting around a
    resource: identity, labels, creation time, and its ownership chain (e.g. a Pod's
    ownerReferences pointing at its ReplicaSet). Deep spec/status detail (image, replicas,
    env vars, conditions) is intentionally not included here -- describe_resource is the
    tool for that.
    """
    metadata = manifest.get("metadata", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "creationTimestamp": metadata.get("creationTimestamp"),
        "ownerReferences": metadata.get("ownerReferences", []),
    }
```

In `get_resource` (`mcp_servers/k8s_mcp_server/k8s_tools.py:97-128`), replace the final line:

```python
    return json.loads(result.stdout)
```

with:

```python
    return _project_resource_summary(json.loads(result.stdout))
```

In `list_resources` (`mcp_servers/k8s_mcp_server/k8s_tools.py:131-164`), replace the final two
lines:

```python
    data = json.loads(result.stdout)
    return data.get("items", [])
```

with:

```python
    data = json.loads(result.stdout)
    return [_project_resource_summary(item) for item in data.get("items", [])]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest test/unit_test/k8s_agent/k8s_tools_test.py -v`
Expected: PASS (all tests, including the updated ones and the ones left unchanged).

- [ ] **Step 5: Run the full unit suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_servers/k8s_mcp_server/k8s_tools.py test/unit_test/k8s_agent/k8s_tools_test.py
git commit -m "fix: reduce get_resource/list_resources to a minimal identity/discovery shape"
```

---

## Self-Review Notes

- **Scope:** per explicit instruction, this plan covers exactly 2 tasks — `HistoryCompactor`
  wiring (Task 1) and `get_resource`/`list_resources` projection (Task 2).
  `describe_resource`, `get_pod_logs`/`get_previous_logs`, and `get_events` (all flagged in
  `REVIEW.md`) are explicitly out of scope for this plan.
- Task 2 is a deliberate, fixed 5-field reduction, not a "keep everything but strip noise"
  trim like the earlier draft of this plan — `spec`/`status` are gone from both tools by design.
  Confirmed with the human partner that this applies to both `get_resource` and
  `list_resources` identically, since it changes `get_resource`'s core capability (it can no
  longer show image/replicas/env vars/conditions — only `describe_resource` can now).
- Existing tests that asserted on the old full-manifest return shape
  (`test_get_resource_success`, `test_list_resources_success`,
  `test_list_resources_with_label_selector`) are explicitly called out for replacement, not left
  to bit-rot — a reviewer should treat a plan that left them unchanged as a spec gap.
- No task changes `agents/planner_agent.py`, `agents/remediation_agent.py`,
  `mcp_servers/prometheus_mcp_server/promql_tools.py`, or any other k8s tool besides
  `get_resource`/`list_resources` — out of scope per Global Constraints.
