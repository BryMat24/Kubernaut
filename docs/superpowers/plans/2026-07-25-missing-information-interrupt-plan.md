# Missing-Information Interrupt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `PlannerAgent` needs one specific piece of information it cannot determine itself
(e.g. a valid replacement image tag), ask the human that specific question via a distinct
interrupt — instead of presenting an unexecutable plan to `human_approval_node` for an
approve/reject decision that can't meaningfully be made.

**Architecture:** `RemediationPlan` gains a `missing_information` field. A new graph node
(`missing_info_node`) sits between `planner_agent` and `human_approval_node`, interrupting to ask
the question and looping back into `planner_agent` with the human's answer as added context for a
full re-plan (not placeholder substitution). The API and UI get a parallel new
question/answer round-trip alongside the existing approve/reject one.

**Tech Stack:** LangGraph (`interrupt()`/`Command(resume=...)`), Pydantic, FastAPI SSE streaming,
Next.js/React/TypeScript.

## Global Constraints

- `missing_information: str | None` on `RemediationPlan` is set *only* when the plan is otherwise
  fully investigated except for one undiscoverable concrete value. When set, `planning_success`
  must be `False` and `steps` must be empty (per the model's existing documented invariant).
- Bounded retry: at most 2 rounds of missing-information questions per remediation attempt
  (`MAX_MISSING_INFO_ROUNDS = 2`), matching this codebase's existing bounded-retry pattern
  (`DiagnosisAgent.MAX_HYPOTHESES`, `MAX_INVESTIGATE_ITERATIONS`).
- Every interrupt payload is self-describing via a `"type"` key: `"approval"` for the existing
  `human_approval_node` interrupt, `"missing_information"` for the new one.
- `pytest` (unit, default) must stay green after every task.
- No existing tests currently cover `models/remediation_plan.py`, `graph/nodes.py`,
  `agents/helpers/plan_classifier.py`, or `api/main.py`'s endpoints — this plan adds first
  coverage for the new pieces in each, following the mocking conventions already established in
  `test/unit_test/diagnosis_agent/phase_routing_test.py` and `test/unit_test/helpers/*_test.py`
  (patch heavy collaborators with `MagicMock`, use `asyncio.run(...)` for async methods — this
  repo has no async test infrastructure).
- The `ui/` package has no test framework configured (no `.test.ts` files, no jest/vitest in
  `package.json`) — UI tasks are verified via `npx tsc --noEmit` (type check) and manual
  functional check, not automated tests.

---

### Task 1: `RemediationPlan.missing_information` field + `PlanClassifier` prompt

**Files:**
- Modify: `models/remediation_plan.py`
- Modify: `agents/helpers/plan_classifier.py`
- Test: `test/unit_test/models/phase_models_test.py`

**Interfaces:**
- Produces: `RemediationPlan.missing_information: str | None` (default `None`) — consumed by
  Task 3's `missing_info_node`/`planning_outcome_routing` and Task 4's API interrupt handling.

- [ ] **Step 1: Write the failing test**

Add to `test/unit_test/models/phase_models_test.py`:

```python
def test_remediation_plan_missing_information_defaults_to_none():
    plan = RemediationPlan(summary="s", steps=[], planning_success=False)
    assert plan.missing_information is None


def test_remediation_plan_accepts_missing_information():
    plan = RemediationPlan(
        summary="Found the exact file and fix, but the correct value isn't known",
        steps=[],
        planning_success=False,
        missing_information="What image tag should be used for brymat24/test-cache-app?",
    )
    assert plan.missing_information == "What image tag should be used for brymat24/test-cache-app?"
```

Check the top of `test/unit_test/models/phase_models_test.py` for its existing import line (it
already imports from `models`) and add `RemediationPlan` to that import if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/unit_test/models/phase_models_test.py -k missing_information -v`
Expected: FAIL with `TypeError: RemediationPlan() got an unexpected keyword argument 'missing_information'` (or the first test fails with `AttributeError`).

- [ ] **Step 3: Add the field**

In `models/remediation_plan.py`, add to `RemediationPlan` (after the `planning_success` field):

```python
    missing_information: str | None = Field(
        default=None,
        description=(
            "Set ONLY when the plan is otherwise fully investigated and correct except for one "
            "concrete value that cannot be determined from repository evidence (e.g. a valid "
            "image tag, an external IP, a secret's real value) -- state the exact question to "
            "ask a human, e.g. 'What image tag should be used for brymat24/test-cache-app?'. "
            "When set, planning_success must be False and steps must be empty -- this is not an "
            "executable plan yet."
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/unit_test/models/phase_models_test.py -k missing_information -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Update `PlanClassifier`'s prompt**

In `agents/helpers/plan_classifier.py`, replace the `finalize_prompt` f-string's structured-output
instructions (the `- planning_success: ...` bullet and everything after it) with:

```python
            Respond with structured output:
            - summary: plain-language explanation of the overall fix.
            - steps: ordered list of step_number, file_path, description, new_content —
            one entry per file change. Leave this EMPTY if missing_information is set below.
                - new_content is ONLY the changed/inserted lines themselves, not the whole
                file but include surrounding related block. Copy any lines you keep
                unchanged verbatim from what you actually read — do not paraphrase or
                reformat existing content.
                - description must state exactly where the change goes, referencing a
                line or field that appears verbatim in the file you read (e.g. "insert
                after the `env:` block in the backend container spec") — this anchor is
                used to locate the edit automatically, so it must be precise and unique
                within the file.
                - For a brand-new file, set file_path to the new path and description to
                state clearly that this is a new file.
            - planning_success: true if you found the relevant file(s) and produced a
            concrete, evidence-backed plan; false if you could not find them, are not
            confident in the plan, or missing_information is set below.
            - missing_information: leave null in the common case. Set it ONLY when you found
            the exact file and fix mechanism, but the fix needs one concrete value that is
            fundamentally impossible to determine from this repository — not just "no
            established convention" (which still gets a best-effort value noted in
            description as today), but genuinely unknowable, like which valid image tag
            exists in a registry you cannot query, or a secret's real external value. When
            you set this, state the exact question to ask a human (e.g. "What image tag
            should be used for brymat24/test-cache-app?"), leave steps empty, and set
            planning_success to false.

            Every proposed value must be grounded in evidence you actually read in this
            repo (e.g. a sibling container's existing resource limits) wherever such
            evidence exists. If no repo convention exists for a value you're proposing,
            say so explicitly in description rather than inventing a number silently.
        """
```

(This keeps every existing instruction and adds the new `missing_information` bullet plus a
qualifier on `steps`/`planning_success`.)

- [ ] **Step 6: Run the full unit suite**

Run: `pytest`
Expected: same pass count as before, plus the 2 new tests.

- [ ] **Step 7: Commit**

```bash
git add models/remediation_plan.py agents/helpers/plan_classifier.py test/unit_test/models/phase_models_test.py
git commit -m "feat: add RemediationPlan.missing_information field"
```

---

### Task 2: `PlannerAgentState.human_provided_info` threading

**Files:**
- Modify: `graph/state.py`
- Modify: `agents/planner_agent.py`
- Test: `test/unit_test/planner_agent/planner_agent_test.py` (new file)

**Interfaces:**
- Consumes: `RemediationPlan.missing_information` (Task 1, for context only — this task doesn't
  branch on it, Task 3 does).
- Produces: `PlannerAgentState.human_provided_info: str` — consumed by Task 3's `planner_node`
  wrapper, which will pass it through on `planner_agent.ainvoke(...)`.

- [ ] **Step 1: Write the failing test**

Create `test/unit_test/planner_agent/planner_agent_test.py`:

```python
from unittest.mock import MagicMock

from agents.planner_agent import PlannerAgent
from models import DiagnosisResult


def _agent():
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    agent = PlannerAgent.__new__(PlannerAgent)
    agent.llm = llm
    agent.SYSTEM_PROMPT = "SYSTEM"
    return agent, llm


def test_reasoning_node_includes_human_provided_info_when_present():
    agent, llm = _agent()
    llm.invoke.return_value = MagicMock(tool_calls=[])

    diagnosis = DiagnosisResult(
        summary="Image pull failure on cache-deployment",
        root_cause="Invalid image tag",
        requires_remediation=True,
        diagnosis_success=True,
    )
    state = {
        "diagnosis_result": diagnosis,
        "repo_path": "/tmp/repo",
        "messages": [],
        "iteration_count": 0,
        "human_provided_info": "Use tag v3, it was just published",
    }

    agent._reasoning_node(state)

    system_prompt = llm.invoke.call_args.args[0][0].content
    assert "Use tag v3, it was just published" in system_prompt


def test_reasoning_node_omits_human_provided_info_section_when_absent():
    agent, llm = _agent()
    llm.invoke.return_value = MagicMock(tool_calls=[])

    diagnosis = DiagnosisResult(
        summary="Image pull failure on cache-deployment",
        root_cause=None,
        requires_remediation=True,
        diagnosis_success=True,
    )
    state = {
        "diagnosis_result": diagnosis,
        "repo_path": "/tmp/repo",
        "messages": [],
        "iteration_count": 0,
    }

    agent._reasoning_node(state)

    system_prompt = llm.invoke.call_args.args[0][0].content
    assert "human_provided_info" not in system_prompt.lower().replace("_", " ").replace(" ", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/unit_test/planner_agent/planner_agent_test.py -v`
Expected: FAIL on `test_reasoning_node_includes_human_provided_info_when_present` — the string
`"Use tag v3, it was just published"` is not in the current system prompt (the `state` key is
simply never read).

- [ ] **Step 3: Add the field to `PlannerAgentState`**

In `graph/state.py`, add to `PlannerAgentState` (after `diagnosis_result`):

```python
class PlannerAgentState(MessagesState):
    diagnosis_result: DiagnosisResult  # provided by caller
    human_provided_info: str  # optional: a human's answer to a prior missing-information question
    iteration_count: int
    plan: RemediationPlan

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str
```

- [ ] **Step 4: Thread it into `_reasoning_node`'s system prompt**

In `agents/planner_agent.py`, replace `_reasoning_node`'s body:

```python
    def _reasoning_node(self, state: PlannerAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1

        diagnosis_result = state["diagnosis_result"]
        root_cause_line = f"\nroot_cause: {diagnosis_result.root_cause}" if diagnosis_result.root_cause else ""
        issue_text = f"summary: {diagnosis_result.summary}{root_cause_line}"

        human_info = state.get("human_provided_info")
        human_info_line = f"\n\nAdditional information supplied by a human:\n{human_info}" if human_info else ""

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nworking_directory: {state['repo_path']}\n\ndiagnosed issue:\n{issue_text}{human_info_line}"

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = self.llm.invoke(messages)

        return {
            "messages": [response],
            "iteration_count": iteration,
        }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest test/unit_test/planner_agent/planner_agent_test.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the full unit suite**

Run: `pytest`
Expected: same pass count as before, plus the 2 new tests.

- [ ] **Step 7: Commit**

```bash
git add graph/state.py agents/planner_agent.py test/unit_test/planner_agent/planner_agent_test.py
git commit -m "feat: thread human-provided answers into PlannerAgent's reasoning prompt"
```

---

### Task 3: `missing_info_node` + `planning_outcome_routing` + graph wiring

**Files:**
- Modify: `graph/state.py`
- Modify: `graph/nodes.py`
- Modify: `graph/builder.py`
- Test: `test/unit_test/graph/nodes_test.py` (new file)

**Interfaces:**
- Consumes: `RemediationPlan.missing_information` (Task 1), `PlannerAgentState.human_provided_info`
  (Task 2, via `planner_node`'s call to `planner_agent.ainvoke(...)`).
- Produces: `missing_info_node(state) -> dict`, `planning_outcome_routing(state) -> Literal[...]`,
  `MAX_MISSING_INFO_ROUNDS: int` — all in `graph/nodes.py`, wired into `graph/builder.py`.

- [ ] **Step 1: Write the failing tests**

Create `test/unit_test/graph/nodes_test.py`:

```python
from unittest.mock import patch

from graph.nodes import (
    MAX_MISSING_INFO_ROUNDS,
    missing_info_node,
    planning_outcome_routing,
)
from models import DiagnosisResult, RemediationPlan


def _diagnosis():
    return DiagnosisResult(
        summary="Image pull failure",
        root_cause="Invalid image tag",
        requires_remediation=True,
        diagnosis_success=True,
    )


def test_planning_outcome_routing_goes_to_missing_info_when_set():
    plan = RemediationPlan(
        summary="s", steps=[], planning_success=False,
        missing_information="What image tag should be used?",
    )
    state = {"plan": plan, "missing_info_rounds": 0}
    assert planning_outcome_routing(state) == "missing_info_node"


def test_planning_outcome_routing_ends_when_rounds_exhausted():
    plan = RemediationPlan(
        summary="s", steps=[], planning_success=False,
        missing_information="What image tag should be used?",
    )
    state = {"plan": plan, "missing_info_rounds": MAX_MISSING_INFO_ROUNDS}
    assert planning_outcome_routing(state) == "end"


def test_planning_outcome_routing_ends_when_planning_failed_with_no_question():
    plan = RemediationPlan(summary="Could not find the relevant file", steps=[], planning_success=False)
    state = {"plan": plan, "missing_info_rounds": 0}
    assert planning_outcome_routing(state) == "end"


def test_planning_outcome_routing_goes_to_approval_when_successful():
    plan = RemediationPlan(
        summary="s",
        steps=[],
        planning_success=True,
    )
    state = {"plan": plan, "missing_info_rounds": 0}
    assert planning_outcome_routing(state) == "human_approval_node"


def test_missing_info_node_interrupts_with_the_question_and_records_the_answer():
    plan = RemediationPlan(
        summary="s", steps=[], planning_success=False,
        missing_information="What image tag should be used for brymat24/test-cache-app?",
    )
    diagnosis = _diagnosis()
    state = {"plan": plan, "diagnosis_result": diagnosis, "missing_info_rounds": 0}

    with patch("graph.nodes.interrupt", return_value={"answer": "v3"}) as mock_interrupt:
        result = missing_info_node(state)

    mock_interrupt.assert_called_once_with({
        "type": "missing_information",
        "question": "What image tag should be used for brymat24/test-cache-app?",
        "diagnosis": diagnosis,
        "plan": plan,
    })
    assert result == {"human_provided_info": "v3", "missing_info_rounds": 1}


def test_missing_info_node_increments_rounds_from_existing_state():
    plan = RemediationPlan(
        summary="s", steps=[], planning_success=False, missing_information="q?",
    )
    state = {"plan": plan, "diagnosis_result": _diagnosis(), "missing_info_rounds": 1}

    with patch("graph.nodes.interrupt", return_value={"answer": "v3"}):
        result = missing_info_node(state)

    assert result["missing_info_rounds"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/unit_test/graph/nodes_test.py -v`
Expected: FAIL with `ImportError: cannot import name 'missing_info_node' from 'graph.nodes'`.

- [ ] **Step 3: Add the new state fields**

In `graph/state.py`, add to `OrchestratorState`:

```python
class OrchestratorState(TypedDict):
    query: str
    repo_url: str
    diagnosis_result: DiagnosisResult
    plan: RemediationPlan
    approved: bool
    pr_url: str
    eval_passed: bool
    eval_reasoning: str
    human_provided_info: str
    missing_info_rounds: int
```

- [ ] **Step 4: Add `missing_info_node`, `planning_outcome_routing`, and fix `planner_node`**

In `graph/nodes.py`, add near the top (module-level constant, alongside imports):

```python
MAX_MISSING_INFO_ROUNDS = 2
```

`graph/nodes.py` currently has this function (unchanged, keep it exactly as-is):

```python
def require_remediation_routing_node(state: OrchestratorState) -> Literal["planner_agent", "end"]:
    diagnosis_result = state["diagnosis_result"]
    if not diagnosis_result.diagnosis_success or not diagnosis_result.requires_remediation:
        return "end"
    return "planner_agent"
```

Insert the following two new functions immediately after it (before `def make_planner_node`):

```python
def planning_outcome_routing(state: OrchestratorState) -> Literal["missing_info_node", "human_approval_node", "end"]:
    plan = state["plan"]
    if plan.missing_information and state.get("missing_info_rounds", 0) < MAX_MISSING_INFO_ROUNDS:
        return "missing_info_node"
    if not plan.planning_success:
        return "end"
    return "human_approval_node"


def missing_info_node(state: OrchestratorState) -> dict:
    plan = state["plan"]
    rounds = state.get("missing_info_rounds", 0) + 1
    answer_payload = interrupt({
        "type": "missing_information",
        "question": plan.missing_information,
        "diagnosis": state["diagnosis_result"],
        "plan": plan,
    })
    return {
        "human_provided_info": answer_payload.get("answer", ""),
        "missing_info_rounds": rounds,
    }
```

Replace `make_planner_node`'s body:

```python
def make_planner_node(planner_agent: PlannerAgent):
    async def planner_node(state: OrchestratorState) -> dict:
        writer = get_stream_writer()
        writer({"phase": "planner", "status": "started", "message": "Building a remediation plan…"})
        result = await planner_agent.ainvoke({
            "messages": [],
            "diagnosis_result": state["diagnosis_result"],
            "human_provided_info": state.get("human_provided_info", ""),
            "iteration_count": 0,
            "repo_url": state["repo_url"],
            "bare_path": "",
            "repo_path": "",
            "branch": "",
        })
        plan = result["plan"]
        if plan.missing_information:
            completed_message = f"Need more information: {plan.missing_information}"
        else:
            completed_message = plan.summary
        writer({"phase": "planner", "status": "completed", "message": completed_message})
        return {"plan": plan}

    return planner_node
```

Add `"type": "approval"` to `human_approval_node`'s interrupt payload:

```python
def human_approval_node(state: OrchestratorState) -> dict:
    decision = interrupt({
        "type": "approval",
        "diagnosis": state["diagnosis_result"],
        "plan": state["plan"],
    })
    update = {"approved": decision.get("approved", False)}
    edited_plan = decision.get("edited_plan")
    if edited_plan is not None:
        update["plan"] = edited_plan
    return update
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest test/unit_test/graph/nodes_test.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Wire the new node/routing into the graph**

In `graph/builder.py`, update the `graph.nodes` import to include the two new names:

```python
from graph.nodes import (
    make_diagnose_node,
    make_planner_node,
    human_approval_node,
    require_remediation_routing_node,
    planning_outcome_routing,
    missing_info_node,
    approval_routing,
    make_remediate_node,
)
```

Replace the graph-construction block from `graph.add_node("human_approval_node", ...)` through
`graph.add_edge("planner_agent", "human_approval_node")`:

```python
    graph.add_node("planner_agent", make_planner_node(planner_agent))
    graph.add_node("missing_info_node", missing_info_node)
    graph.add_node("human_approval_node", human_approval_node)
    graph.add_node("remediation_agent", make_remediate_node(remediation_agent))

    graph.add_edge(START, "diagnosis_agent")
    graph.add_conditional_edges(
        "diagnosis_agent",
        require_remediation_routing_node,
        {"planner_agent": "planner_agent", "end": END},
    )
    graph.add_conditional_edges(
        "planner_agent",
        planning_outcome_routing,
        {"missing_info_node": "missing_info_node", "human_approval_node": "human_approval_node", "end": END},
    )
    graph.add_edge("missing_info_node", "planner_agent")
    graph.add_conditional_edges(
        "human_approval_node",
        approval_routing,
        {"remediation_agent": "remediation_agent", "end": END},
    )
    graph.add_edge("remediation_agent", END)
```

- [ ] **Step 7: Run the full unit suite**

Run: `pytest`
Expected: same pass count as before, plus the 7 new tests.

- [ ] **Step 8: Commit**

```bash
git add graph/state.py graph/nodes.py graph/builder.py test/unit_test/graph/nodes_test.py
git commit -m "feat: add missing-information interrupt node and routing"
```

---

### Task 4: API — `MissingInfoAnswer` schema, `_interrupt_response` helper, `/diagnose` fix, new `/answer/{thread_id}`

**Files:**
- Modify: `api/schemas.py`
- Modify: `api/main.py`
- Test: `test/unit_test/api/interrupt_response_test.py` (new file)

**Interfaces:**
- Consumes: interrupt payloads shaped `{"type": "approval", "diagnosis":..., "plan":...}` or
  `{"type": "missing_information", "question":..., "diagnosis":..., "plan":...}` (Task 3).
- Produces: `_interrupt_response(payload: dict) -> tuple[str, str, dict]` (status, db message text,
  extra SSE fields) in `api/main.py`, used by both `/diagnose` and the new `/answer/{thread_id}`.

- [ ] **Step 1: Write the failing test**

Create `test/unit_test/api/interrupt_response_test.py`:

```python
from api.main import _interrupt_response
from models import DiagnosisResult, RemediationPlan


def _diagnosis():
    return DiagnosisResult(
        summary="Image pull failure on cache-deployment",
        root_cause="Invalid image tag",
        requires_remediation=True,
        diagnosis_success=True,
    )


def test_interrupt_response_for_missing_information():
    payload = {
        "type": "missing_information",
        "question": "What image tag should be used for brymat24/test-cache-app?",
        "diagnosis": _diagnosis(),
        "plan": RemediationPlan(summary="s", steps=[], planning_success=False),
    }

    status, message, extra = _interrupt_response(payload)

    assert status == "pending_info"
    assert "What image tag should be used for brymat24/test-cache-app?" in message
    assert extra == {"question": "What image tag should be used for brymat24/test-cache-app?"}


def test_interrupt_response_for_approval():
    plan = RemediationPlan(summary="Update the image tag", steps=[], planning_success=True)
    payload = {"type": "approval", "diagnosis": _diagnosis(), "plan": plan}

    status, message, extra = _interrupt_response(payload)

    assert status == "pending_approval"
    assert "Image pull failure on cache-deployment" in message
    assert extra == {"plan": plan}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/unit_test/api/interrupt_response_test.py -v`
Expected: FAIL with `ImportError: cannot import name '_interrupt_response' from 'api.main'`.

- [ ] **Step 3: Add the `MissingInfoAnswer` schema**

In `api/schemas.py`, add:

```python
class MissingInfoAnswer(BaseModel):
    answer: str
```

- [ ] **Step 4: Add the `_interrupt_response` helper**

In `api/main.py`, add this function near `_sse_event` (after it, before the route handlers):

```python
def _interrupt_response(payload: dict) -> tuple[str, str, dict]:
    """Given an interrupt() payload (Task 3's "type"-tagged shape), return
    (status, message_for_chat_history, extra_sse_fields)."""
    if payload.get("type") == "missing_information":
        question = payload["question"]
        message = f"I need more information to build a safe plan: {question}"
        return "pending_info", message, {"question": question}

    diagnosis = payload["diagnosis"]
    plan = payload["plan"]
    message = build_summary_message(diagnosis, plan, approved=None)
    return "pending_approval", message, {"plan": plan}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest test/unit_test/api/interrupt_response_test.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Use the helper in `/diagnose`'s interrupt handling**

In `api/main.py`, replace the `if "__interrupt__" in final_values:` block inside
`start_diagnosis`'s `event_generator`:

```python
        async with AsyncSessionLocal() as gen_db:
            if "__interrupt__" in final_values:
                interrupt_payload = final_values["__interrupt__"][0].value
                status, summary, extra = _interrupt_response(interrupt_payload)
                gen_db.add(Message(chat_id=chat_id, role=MessageRole.ASSISTANT, content=summary, thread_id=thread_id))
                await gen_db.commit()
                yield _sse_event({
                    "type": "final",
                    "status": status,
                    "thread_id": thread_id,
                    "chat_id": str(chat_id),
                    **extra,
                })
                return

            diagnosis_result = final_values.get("diagnosis_result")
            answer = build_summary_message(diagnosis_result) if diagnosis_result else "(no diagnosis result)"
            gen_db.add(Message(chat_id=chat_id, role=MessageRole.ASSISTANT, content=answer, thread_id=thread_id))
            await gen_db.commit()
            yield _sse_event({
                "type": "final",
                "status": "complete",
                "thread_id": thread_id,
                "chat_id": str(chat_id),
                "result": final_values,
                "message": answer,
            })
```

(This moves the `async with AsyncSessionLocal()` to wrap both branches — previously it only
wrapped the non-interrupt branch; the interrupt branch already opened its own `async with
AsyncSessionLocal()` in the original code, so this is a like-for-like restructure, not new
behavior, just using the shared helper instead of inline destructuring.)

- [ ] **Step 7: Add the `MissingInfoAnswer` import and the new `/answer/{thread_id}` endpoint**

In `api/main.py`, update the schemas import:

```python
from api.schemas import ApprovalDecision, ChatCreateRequest, DiagnoseRequest, MissingInfoAnswer
```

Add this endpoint after `approve` (after its closing `return StreamingResponse(...)` line):

```python
@app.post("/answer/{thread_id}")
async def answer_missing_info(thread_id: str, body: MissingInfoAnswer, db: AsyncSession = Depends(get_db_session)):
    config = {"configurable": {"thread_id": thread_id}}
    graph = app.state.graph

    state = await graph.aget_state(config)
    if not state.values:
        raise HTTPException(status_code=404, detail=f"unknown thread_id: {thread_id}")

    pending_message = (
        await db.execute(
            select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at.desc())
        )
    ).scalars().first()
    pending_message_id = pending_message.id if pending_message else None

    async def event_generator():
        final_values: dict = {}
        try:
            async for mode, chunk in graph.astream(
                Command(resume={"answer": body.answer}),
                config=config,
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    yield _sse_event({"type": "progress", **chunk})
                else:
                    final_values = chunk
        except Exception as exc:
            yield _sse_event({"type": "error", "message": str(exc)})
            return

        if "__interrupt__" in final_values:
            interrupt_payload = final_values["__interrupt__"][0].value
            status, summary, extra = _interrupt_response(interrupt_payload)
            if pending_message_id is not None:
                async with AsyncSessionLocal() as gen_db:
                    msg = await gen_db.get(Message, pending_message_id)
                    if msg is not None:
                        msg.content = summary
                        await gen_db.commit()
            yield _sse_event({
                "type": "final",
                "status": status,
                "thread_id": thread_id,
                **extra,
            })
            return

        message_text = None
        if pending_message_id is not None:
            diagnosis = final_values.get("diagnosis_result")
            plan = final_values.get("plan")
            if diagnosis is not None:
                message_text = build_summary_message(diagnosis, plan, approved=None)
                async with AsyncSessionLocal() as gen_db:
                    msg = await gen_db.get(Message, pending_message_id)
                    if msg is not None:
                        msg.content = message_text
                        await gen_db.commit()

        yield _sse_event({
            "type": "final",
            "status": "complete",
            "thread_id": thread_id,
            "result": final_values,
            "message": message_text,
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=SSE_HEADERS)
```

- [ ] **Step 8: Run the full unit suite**

Run: `pytest`
Expected: same pass count as before, plus the 2 new tests.

- [ ] **Step 9: Commit**

```bash
git add api/schemas.py api/main.py test/unit_test/api/interrupt_response_test.py
git commit -m "feat: add /answer endpoint and shared interrupt-response handling"
```

---

### Task 5: UI — `ui/lib/api.ts` types and `streamAnswer`

**Files:**
- Modify: `ui/lib/api.ts`

**Interfaces:**
- Consumes: `/diagnose` and `/answer/{thread_id}` SSE responses shaped
  `{"type": "final", "status": "pending_info", "thread_id":..., "question": "..."}` or
  `{"type": "final", "status": "pending_approval", ..., "plan": {...}}` (Task 4).
- Produces: `streamAnswer(threadId: string, answer: string, onProgress) -> Promise<AnswerResponse>`
  — consumed by Task 6's `MissingInfoCard.tsx`. Extended `DiagnoseResponse`/new `AnswerResponse`
  types — consumed by Task 6's `ChatView.tsx`.

- [ ] **Step 1: Extend the response types**

In `ui/lib/api.ts`, replace `DiagnoseResponse` and add a new `AnswerResponse` type right after it:

```typescript
export interface DiagnoseResponse {
  status: "pending_approval" | "pending_info" | "complete";
  thread_id: string;
  chat_id: string;
  plan?: RemediationPlan;
  question?: string;
  message?: string;
  result?: { diagnosis_result?: { summary: string }; pr_url?: string };
}

export interface AnswerResponse {
  status: "pending_approval" | "pending_info" | "complete";
  thread_id: string;
  plan?: RemediationPlan;
  question?: string;
  message?: string;
  result?: { pr_url?: string; [key: string]: unknown };
}
```

- [ ] **Step 2: Add `streamAnswer`**

In `ui/lib/api.ts`, add after `streamApprove`:

```typescript
export function streamAnswer(
  threadId: string,
  answer: string,
  onProgress: (event: ProgressEvent) => void
): Promise<AnswerResponse> {
  return streamRequest<AnswerResponse>(`/answer/${threadId}`, { answer }, onProgress);
}
```

- [ ] **Step 3: Type-check**

Run: `cd ui && npx tsc --noEmit`
Expected: no new errors (there may be pre-existing errors unrelated to this file — confirm none
are newly introduced by this change specifically, e.g. by running `git stash` + the same command
to compare, then `git stash pop`).

- [ ] **Step 4: Commit**

```bash
git add ui/lib/api.ts
git commit -m "feat: add pending_info status and streamAnswer to the API client"
```

---

### Task 6: `MissingInfoCard.tsx` + `ChatView.tsx` wiring

**Files:**
- Create: `ui/components/MissingInfoCard.tsx`
- Modify: `ui/components/ChatView.tsx`

**Interfaces:**
- Consumes: `streamAnswer` (Task 5), `RemediationPlan`/`ProgressEvent`/`ApiError` types
  (`ui/lib/api.ts`).
- Produces: `MissingInfoCard` React component with props
  `{ threadId: string; question: string; onResolved: (outcome: MissingInfoOutcome) => void }`,
  where `MissingInfoOutcome` is consumed by `ChatView.tsx`'s timeline-item replacement logic.

- [ ] **Step 1: Create `MissingInfoCard.tsx`**

```tsx
"use client";

import { useState } from "react";
import { ProgressEvent, RemediationPlan, streamAnswer, ApiError } from "@/lib/api";
import ProgressTimeline from "@/components/ProgressTimeline";

export type MissingInfoOutcome =
  | { kind: "message"; content: string }
  | { kind: "pending_plan"; plan: RemediationPlan }
  | { kind: "pending_info"; question: string };

interface MissingInfoCardProps {
  threadId: string;
  question: string;
  onResolved: (outcome: MissingInfoOutcome) => void;
}

export default function MissingInfoCard({ threadId, question, onResolved }: MissingInfoCardProps) {
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!answer.trim()) return;
    setSubmitting(true);
    setProgressEvents([]);
    setError(null);
    try {
      const response = await streamAnswer(threadId, answer, (event) => {
        setProgressEvents((prev) => [...prev, event]);
      });
      if (response.status === "pending_info" && response.question) {
        onResolved({ kind: "pending_info", question: response.question });
      } else if (response.status === "pending_approval" && response.plan) {
        onResolved({ kind: "pending_plan", plan: response.plan });
      } else {
        onResolved({ kind: "message", content: response.message ?? "Done." });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to submit answer");
      setSubmitting(false);
      setProgressEvents([]);
    }
  }

  return (
    <section
      aria-label="Additional information requested"
      aria-busy={submitting}
      className="hud-frame rounded-md border border-caution/30 bg-caution/[0.06] px-4 py-3 text-sm text-foreground"
    >
      <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-caution">
        <span aria-hidden="true">»</span>
        <span>Input required</span>
      </div>

      <p className="mt-1.5 leading-relaxed text-foreground/90">{question}</p>

      {submitting && (
        <div className="mt-3 rounded-md border border-border bg-background px-3 py-2">
          <ProgressTimeline events={progressEvents} />
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-critical">
          {error}
        </p>
      )}

      <form onSubmit={handleSubmit} className="mt-3 flex gap-2">
        <input
          type="text"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          disabled={submitting}
          aria-label="Your answer"
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-signal/50"
          placeholder="Type your answer…"
        />
        <button
          type="submit"
          disabled={submitting || !answer.trim()}
          className="rounded-md bg-nominal px-3 py-2 text-sm font-medium text-nominal-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-signal/50"
        >
          {submitting ? "Submitting…" : "Submit"}
        </button>
      </form>
    </section>
  );
}
```

- [ ] **Step 2: Wire `TimelineItem` and rendering in `ChatView.tsx`**

In `ui/components/ChatView.tsx`, update the imports:

```typescript
import {
  ChatMessage,
  ProgressEvent,
  RemediationPlan,
  streamDiagnose,
  getChatMessages,
  ApiError,
} from "@/lib/api";
import MessageBubble from "@/components/MessageBubble";
import Composer from "@/components/Composer";
import PlanCard from "@/components/PlanCard";
import MissingInfoCard, { MissingInfoOutcome } from "@/components/MissingInfoCard";
import ProgressTimeline from "@/components/ProgressTimeline";
```

Update the `TimelineItem` union:

```typescript
export type TimelineItem =
  | { kind: "message"; id: string; message: ChatMessage }
  | { kind: "pending_plan"; id: string; threadId: string; plan: RemediationPlan }
  | { kind: "pending_info"; id: string; threadId: string; question: string };
```

Update `handleSend`'s response branching (replace the `if (response.status === "pending_approval" && response.plan) {`
block):

```typescript
      if (response.status === "pending_approval" && response.plan) {
        setItems((prev) => [
          ...prev,
          {
            kind: "pending_plan",
            id: response.thread_id,
            threadId: response.thread_id,
            plan: response.plan!,
          },
        ]);
      } else if (response.status === "pending_info" && response.question) {
        setItems((prev) => [
          ...prev,
          {
            kind: "pending_info",
            id: response.thread_id,
            threadId: response.thread_id,
            question: response.question!,
          },
        ]);
      } else {
        const summary = response.message ?? response.result?.diagnosis_result?.summary ?? "(no diagnosis result)";
        setItems((prev) => [
          ...prev,
          {
            kind: "message",
            id: `assistant-${response.thread_id}`,
            message: {
              id: `assistant-${response.thread_id}`,
              role: "assistant",
              content: summary,
              thread_id: response.thread_id,
              created_at: new Date().toISOString(),
            },
          },
        ]);
      }
```

Add a helper function above the `return` statement in the component, and use it to replace a
timeline item by id with the resolved outcome:

```typescript
  function replaceItem(id: string, outcome: MissingInfoOutcome) {
    setItems((prev) =>
      prev.map((i): TimelineItem => {
        if (i.id !== id) return i;
        if (outcome.kind === "message") {
          return {
            kind: "message",
            id,
            message: {
              id,
              role: "assistant",
              content: outcome.content,
              thread_id: i.kind === "pending_info" || i.kind === "pending_plan" ? i.threadId : null,
              created_at: new Date().toISOString(),
            },
          };
        }
        if (outcome.kind === "pending_plan") {
          return { kind: "pending_plan", id, threadId: (i as { threadId: string }).threadId, plan: outcome.plan };
        }
        return { kind: "pending_info", id, threadId: (i as { threadId: string }).threadId, question: outcome.question };
      })
    );
  }
```

Update the `items.map(...)` render block to add the `pending_info` case:

```tsx
        {items.map((item) =>
          item.kind === "message" ? (
            <MessageBubble key={item.id} message={item.message} />
          ) : item.kind === "pending_plan" ? (
            <PlanCard
              key={item.id}
              threadId={item.threadId}
              plan={item.plan}
              onResolved={(outcome) => replaceItem(item.id, { kind: "message", content: outcome })}
            />
          ) : (
            <MissingInfoCard
              key={item.id}
              threadId={item.threadId}
              question={item.question}
              onResolved={(outcome) => replaceItem(item.id, outcome)}
            />
          )
        )}
```

(`PlanCard`'s existing `onResolved` prop takes a plain `string` — its call site above wraps that
into a `MissingInfoOutcome`-shaped `{ kind: "message", content: outcome }` so both cards can share
`replaceItem`. `PlanCard.tsx` itself is unchanged.)

- [ ] **Step 3: Type-check**

Run: `cd ui && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Manual functional check**

Run: `cd ui && npm run dev`, and separately start the API (`uvicorn api.main:app --reload` from
repo root, with `DATABASE_URL` set) and a diagnosis flow that reaches a `missing_information`
plan (or temporarily stub `PlanClassifier.classify` to return one, to check the UI without needing
a real ImagePullBackOff scenario). Confirm: the question renders in a `MissingInfoCard`, typing an
answer and submitting shows progress events, and the result correctly transitions to either
another `MissingInfoCard`, a `PlanCard`, or a final message.

- [ ] **Step 5: Commit**

```bash
git add ui/components/MissingInfoCard.tsx ui/components/ChatView.tsx
git commit -m "feat: add MissingInfoCard UI and wire it into ChatView"
```

---

## Final verification (after all 6 tasks)

- [ ] Run `pytest` — unit suite green.
- [ ] Run `cd ui && npx tsc --noEmit` — no type errors.
- [ ] Manually run the full flow end-to-end once against a real repo with an `ErrImagePull`
  scenario (or a stubbed `missing_information` plan) to confirm: diagnose → planner asks a
  question → answer submitted → planner re-plans → either asks again (bounded by
  `MAX_MISSING_INFO_ROUNDS`), or reaches a normal approve/reject `PlanCard`.
