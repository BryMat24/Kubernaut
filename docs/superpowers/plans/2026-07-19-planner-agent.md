# PlannerAgent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a read-only `PlannerAgent` between `DiagnosisAgent` and the human-approval gate that turns a diagnosed root cause into a structured, file-by-file `RemediationPlan`, so `RemediationAgent` executes a concrete plan instead of re-investigating from scratch.

**Architecture:** `PlannerAgent` is a new `StateGraph`-based agent (`setup_node → reasoning_node ⇄ tool_node → finalize_node → cleanup_node → END`) mirroring `RemediationAgent`'s git-worktree setup/cleanup and `DiagnosisAgent`'s ReAct-loop-plus-classifier finalize pattern. It's read-only (`find`/`grep`/`list_files_in_directory`/`read_file_content` only) and produces a new `RemediationPlan` pydantic model that `RemediationAgent` consumes in place of the raw `DiagnosisResult` it uses today.

**Tech Stack:** Python 3.11, LangGraph (`StateGraph`, `MessagesState`, `ToolNode`), LangChain (`with_structured_output`), Pydantic v2, pytest.

## Global Constraints

- Every `RemediationPlan`/`RemediationStep` field must have a `Field(description=...)` — a required structured-output field with no description leaves the LLM with no grounding for a value that gates behavior (this bit `DiagnosisResult.diagnosis_success` earlier in this project).
- `PlannerAgent`'s only bound LLM tools are `find`, `grep`, `list_files_in_directory`, `read_file_content` — never `edit_file`/`write_file`. It is read-only.
- `PlannerAgent` gets its own git worktree, fully separate from `RemediationAgent`'s. Branch prefix `plan/...` (vs. `RemediationAgent`'s `agent/...`).
- `RemediationAgentState.plan: RemediationPlan` replaces `RemediationAgentState.diagnosis_result: DiagnosisResult` entirely — not both.
- Only `RemediationAgent._task_description` gets a committed pytest test (`test/unit_test/remediation_agent/`). `PlanClassifier` and `PlannerAgent`'s graph/loop are self-verified with a mocked dry-run script per task, not committed as test files — this matches the already-approved spec and the existing precedent that `DiagnosisAgent`/`RemediationAgent`'s own reasoning loops have no unit tests either.
- No new integration-test infrastructure.

---

### Task 1: RemediationPlan / RemediationStep models

**Files:**
- Create: `models/remediation_plan.py`
- Modify: `models/__init__.py`

**Interfaces:**
- Produces: `RemediationStep(step_number: int, file_path: str, description: str, old_content: str | None = None, new_content: str)` and `RemediationPlan(summary: str, steps: list[RemediationStep], planning_success: bool)`, both importable as `from models import RemediationPlan, RemediationStep`.

- [ ] **Step 1: Create `models/remediation_plan.py`**

```python
from pydantic import BaseModel, Field


class RemediationStep(BaseModel):
    step_number: int = Field(description="1-based order in which steps should be performed.")
    file_path: str = Field(description="Path to the file to change, relative to the repo root.")
    description: str = Field(description="What this step does and why, in plain language.")
    old_content: str | None = Field(
        default=None,
        description="Exact existing text this step replaces, copied verbatim from a file "
        "actually read during investigation. Null only if this step creates a brand-new file."
    )
    new_content: str = Field(
        description="The exact text old_content should become, or the full content of a new "
        "file if old_content is null."
    )


class RemediationPlan(BaseModel):
    summary: str = Field(description="Plain-language explanation of the overall fix.")
    steps: list[RemediationStep] = Field(
        description="Ordered, file-by-file steps to apply. Empty if planning_success is False."
    )
    planning_success: bool = Field(
        description="True if the repo was searched and a concrete, evidence-backed plan was "
        "produced. False if the relevant files couldn't be found or the investigation was "
        "inconclusive — this signals the plan isn't safe to hand to the remediation agent."
    )
```

- [ ] **Step 2: Add the export to `models/__init__.py`**

Current content:
```python
from .diagnosis_result import DiagnosisResult
from .scenario_eval_result import ScenarioEvalResult
from .eval_result import EvalResult
```

New content:
```python
from .diagnosis_result import DiagnosisResult
from .scenario_eval_result import ScenarioEvalResult
from .eval_result import EvalResult
from .remediation_plan import RemediationPlan, RemediationStep
```

- [ ] **Step 3: Verify the models import and validate correctly**

Run:
```bash
cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && python3 -c "
from models import RemediationPlan, RemediationStep

step = RemediationStep(step_number=1, file_path='a.yaml', description='d', new_content='x')
plan = RemediationPlan(summary='s', steps=[step], planning_success=True)
print(plan.steps[0].old_content is None)
print(plan.planning_success)
"
```
Expected output:
```
True
True
```

- [ ] **Step 4: Commit**

```bash
git add models/remediation_plan.py models/__init__.py
git commit -m "$(cat <<'EOF'
Add RemediationPlan/RemediationStep models

Structured output for the new PlannerAgent: a file-by-file remediation
plan that RemediationAgent will consume instead of a raw diagnosis.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: PlanClassifier

**Files:**
- Create: `agents/plan_classifier.py`
- Modify: `agents/__init__.py`

**Interfaces:**
- Consumes: `RemediationPlan` from Task 1 (`from models import RemediationPlan`), `DiagnosisResult` from `models`.
- Produces: `PlanClassifier(llm)` with `async def classify(self, diagnosis_result: DiagnosisResult, messages: list[BaseMessage]) -> RemediationPlan`, importable as `from agents import PlanClassifier`.

- [ ] **Step 1: Create `agents/plan_classifier.py`**

```python
from langchain_core.messages import BaseMessage, SystemMessage

from models import DiagnosisResult, RemediationPlan


class PlanClassifier:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(RemediationPlan, include_raw=True)

    async def classify(self, diagnosis_result: DiagnosisResult, messages: list[BaseMessage]) -> RemediationPlan:
        root_cause_line = f"\nroot_cause: {diagnosis_result.root_cause}" if diagnosis_result.root_cause else ""
        finalize_prompt = f"""
            Based on the investigation above, produce the final structured remediation plan
            for the diagnosed issue below.

            diagnosed issue:
            summary: {diagnosis_result.summary}{root_cause_line}

            Respond with structured output: summary (plain-language explanation of the overall
            fix), steps (ordered list of step_number, file_path, description, old_content,
            new_content — one entry per file change; old_content must be copied verbatim from
            a file you actually read, and is null only for a brand-new file), and
            planning_success (true if you found the relevant file(s) and produced a concrete,
            evidence-backed plan; false if you could not find them or are not confident in
            the plan).
        """

        prompt_messages = messages + [SystemMessage(content=finalize_prompt)]
        response = await self.llm.ainvoke(prompt_messages)
        parsed = response["parsed"]
        if parsed is None:
            raw_content = response["raw"].content
            return RemediationPlan(
                summary=raw_content or "Planner agent did not return a structured result.",
                steps=[],
                planning_success=False,
            )
        return parsed
```

- [ ] **Step 2: Add the export to `agents/__init__.py`**

Current content:
```python
from .remediation_agent import RemediationAgent
from .diagnosis_agent import DiagnosisAgent
from .judge import DiffEvaluator
from .scenario_evaluator import ScenarioEvaluator
from .classifier import Classifier
```

New content:
```python
from .remediation_agent import RemediationAgent
from .diagnosis_agent import DiagnosisAgent
from .judge import DiffEvaluator
from .scenario_evaluator import ScenarioEvaluator
from .classifier import Classifier
from .plan_classifier import PlanClassifier
```

- [ ] **Step 3: Self-verify with a mocked-LLM dry run**

Run:
```bash
cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && python3 -c "
import asyncio
from langchain_core.messages import HumanMessage, AIMessage
from agents.plan_classifier import PlanClassifier
from models import DiagnosisResult, RemediationPlan, RemediationStep

class FakeLLM:
    def with_structured_output(self, model, include_raw=True):
        return self
    async def ainvoke(self, messages):
        assert isinstance(messages, list), f'expected list, got {type(messages)}'
        assert len(messages) == 3, f'expected 3 messages, got {len(messages)}'
        plan = RemediationPlan(
            summary='ok',
            steps=[RemediationStep(step_number=1, file_path='a.yaml', description='d', new_content='x')],
            planning_success=True,
        )
        return {'parsed': plan, 'raw': None}

async def main():
    c = PlanClassifier(FakeLLM())
    diagnosis = DiagnosisResult(summary='pod crashlooping', root_cause='OOMKilled', requires_remediation=True, diagnosis_success=True)
    history = [HumanMessage(content='q'), AIMessage(content='investigating...')]
    result = await c.classify(diagnosis, history)
    print('classify OK:', result.planning_success, len(result.steps))

asyncio.run(main())
"
```
Expected output:
```
classify OK: True 1
```

- [ ] **Step 4: Commit**

```bash
git add agents/plan_classifier.py agents/__init__.py
git commit -m "$(cat <<'EOF'
Add PlanClassifier

Structured-output wrapper that turns a PlannerAgent investigation into
a RemediationPlan, mirroring agents/classifier.py's Classifier pattern.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: State schema update + RemediationAgent switched to consume RemediationPlan

**Files:**
- Modify: `graph/state.py`
- Modify: `agents/remediation_agent.py`
- Create: `test/unit_test/remediation_agent/remediation_agent_test.py`

**Interfaces:**
- Consumes: `RemediationPlan`, `RemediationStep` from Task 1.
- Produces: `PlannerAgentState(MessagesState)` with fields `diagnosis_result: DiagnosisResult, iteration_count: int, plan: RemediationPlan, repo_url: str, bare_path: str, repo_path: str, branch: str` — consumed by Task 4. `OrchestratorState.plan: RemediationPlan` — consumed by Task 5. `RemediationAgentState.plan: RemediationPlan` (replacing `diagnosis_result`) — consumed by Task 5. `RemediationAgent._task_description(plan: RemediationPlan) -> str` (signature changed from `DiagnosisResult` to `RemediationPlan`).

- [ ] **Step 1: Update `graph/state.py`**

Full new content:

```python
from typing import TypedDict

from langgraph.graph import MessagesState
from models import DiagnosisResult, RemediationPlan

class OrchestratorState(TypedDict):
    query: str
    repo_url: str
    diagnosis_result: DiagnosisResult
    plan: RemediationPlan
    approved: bool
    pr_url: str
    eval_passed: bool
    eval_reasoning: str

class DiagnosisAgentState(MessagesState):
    query: str
    iteration_count: int
    diagnosis_result: DiagnosisResult

class PlannerAgentState(MessagesState):
    diagnosis_result: DiagnosisResult  # provided by caller
    iteration_count: int
    plan: RemediationPlan

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str

class RemediationAgentState(MessagesState):
    plan: RemediationPlan  # provided by caller
    iteration_count: int
    eval_passed: bool
    eval_reasoning: str
    pr_url: str

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str
```

- [ ] **Step 2: Write the failing test for the new `_task_description`**

Create `test/unit_test/remediation_agent/remediation_agent_test.py`:

```python
from agents.remediation_agent import RemediationAgent
from models import RemediationPlan, RemediationStep


def test_task_description_includes_summary_and_steps():
    plan = RemediationPlan(
        summary="Fix missing CPU request on the backend deployment",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="apps/backend/deployment.yaml",
                description="Add a CPU request so the HPA can compute utilization",
                old_content="resources:\n  limits:\n    cpu: 500m",
                new_content="resources:\n  requests:\n    cpu: 250m\n  limits:\n    cpu: 500m",
            ),
        ],
        planning_success=True,
    )

    description = RemediationAgent._task_description(plan)

    assert "Fix missing CPU request on the backend deployment" in description
    assert "1. apps/backend/deployment.yaml: Add a CPU request so the HPA can compute utilization" in description
    assert "old_content:\nresources:\n  limits:\n    cpu: 500m" in description
    assert "new_content:\nresources:\n  requests:\n    cpu: 250m\n  limits:\n    cpu: 500m" in description


def test_task_description_new_file_has_no_old_content_block():
    plan = RemediationPlan(
        summary="Add a missing NetworkPolicy",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="apps/backend/network-policy.yaml",
                description="Create the missing NetworkPolicy allowing ingress from frontend",
                old_content=None,
                new_content="apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n",
            ),
        ],
        planning_success=True,
    )

    description = RemediationAgent._task_description(plan)

    assert "old_content:" not in description
    assert "new_content:\napiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n" in description


def test_task_description_empty_steps_renders_summary_only():
    plan = RemediationPlan(
        summary="Could not locate the relevant manifest for this issue",
        steps=[],
        planning_success=False,
    )

    description = RemediationAgent._task_description(plan)

    assert description == "Could not locate the relevant manifest for this issue"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && pytest test/unit_test/remediation_agent/remediation_agent_test.py -v`
Expected: FAIL — `RemediationAgent._task_description` still expects a `DiagnosisResult` and reads `.root_cause`, so calling it with a `RemediationPlan` raises `AttributeError: 'RemediationPlan' object has no attribute 'root_cause'`.

- [ ] **Step 4: Rewrite `agents/remediation_agent.py` to consume `RemediationPlan`**

Change the import on line 20 from:
```python
from models import DiagnosisResult
```
to:
```python
from models import RemediationPlan
```

Change `_setup_node` (currently uses `self._task_description(state['diagnosis_result'])` for the branch slug) to use the short summary instead of the full multi-step dump:

Old:
```python
    def _setup_node(self, state: RemediationAgentState) -> dict[str, Any]:
        self.logger.info("\n=== setup ===")
        branch = f"agent/{slugify(self._task_description(state['diagnosis_result']))}-{uuid.uuid4().hex[:6]}"
```
New:
```python
    def _setup_node(self, state: RemediationAgentState) -> dict[str, Any]:
        self.logger.info("\n=== setup ===")
        branch = f"agent/{slugify(state['plan'].summary)}-{uuid.uuid4().hex[:6]}"
```

Change `_reasoning_node`'s prompt line from:
```python
        system_prompt = f"{self.SYSTEM_PROMPT}\n\nworking_directory: {state['repo_path']}\n\ntask:\n{self._task_description(state['diagnosis_result'])}"
```
to:
```python
        system_prompt = f"{self.SYSTEM_PROMPT}\n\nworking_directory: {state['repo_path']}\n\ntask:\n{self._task_description(state['plan'])}"
```

Change `_evaluation_node`'s judge call from:
```python
        result = self.llm_as_judge.evaluate(self._task_description(state["diagnosis_result"]), diff)
```
to:
```python
        result = self.llm_as_judge.evaluate(self._task_description(state["plan"]), diff)
```

Change `_pr_node` from:
```python
    def _pr_node(self, state: RemediationAgentState) -> dict[str, Any]:
        self.logger.info("\n=== opening PR ===")
        files = get_changed_files(state["repo_path"])
        try:
            task_description = self._task_description(state["diagnosis_result"])
            pr_url = open_pull_request(
                state["repo_path"],
                state["branch"],
                commit_message=f"fix: {task_description}",
                title=task_description[:72],
                body=self._build_pr_body(files, state.get("eval_reasoning", "")),
            )
```
to:
```python
    def _pr_node(self, state: RemediationAgentState) -> dict[str, Any]:
        self.logger.info("\n=== opening PR ===")
        files = get_changed_files(state["repo_path"])
        try:
            plan_summary = state["plan"].summary
            pr_url = open_pull_request(
                state["repo_path"],
                state["branch"],
                commit_message=f"fix: {plan_summary}",
                title=plan_summary[:72],
                body=self._build_pr_body(files, state.get("eval_reasoning", "")),
            )
```

Change `_task_description` from:
```python
    @staticmethod
    def _task_description(diagnosis_result: DiagnosisResult) -> str:
        if diagnosis_result.root_cause:
            return f"{diagnosis_result.summary}\n\nRoot cause: {diagnosis_result.root_cause}"
        return diagnosis_result.summary
```
to:
```python
    @staticmethod
    def _task_description(plan: RemediationPlan) -> str:
        lines = [plan.summary]
        for step in plan.steps:
            lines.append(f"\n{step.step_number}. {step.file_path}: {step.description}")
            if step.old_content is not None:
                lines.append(f"   old_content:\n{step.old_content}")
            lines.append(f"   new_content:\n{step.new_content}")
        return "\n".join(lines)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && pytest test/unit_test/remediation_agent/remediation_agent_test.py -v`
Expected: `3 passed`

- [ ] **Step 6: Run the full unit test suite to confirm no regressions**

Run: `cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && pytest -q`
Expected: all tests pass (85 passed — the 82 already in the suite plus the 3 new ones), `10 deselected` (integration tests, unaffected).

- [ ] **Step 7: Commit**

```bash
git add graph/state.py agents/remediation_agent.py test/unit_test/remediation_agent/remediation_agent_test.py
git commit -m "$(cat <<'EOF'
Switch RemediationAgent to consume RemediationPlan instead of DiagnosisResult

Adds PlannerAgentState and OrchestratorState.plan to graph/state.py.
RemediationAgentState.plan replaces diagnosis_result — RemediationAgent's
task description, branch name, and PR title/commit message now come
from the structured plan instead of the raw diagnosis.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: PlannerAgent

**Files:**
- Create: `agents/planner_agent.py`
- Modify: `agents/__init__.py`

**Interfaces:**
- Consumes: `PlannerAgentState` from Task 3 (`from graph.state import PlannerAgentState`), `RemediationPlan` from Task 1, `PlanClassifier` from Task 2 (`from .plan_classifier import PlanClassifier`), git helpers from `utils` (`slugify`, `ensure_base_clone`, `create_task_worktree`, `remove_task_worktree`, `repo_lock`).
- Produces: `PlannerAgent(llm, tools)` with `async def ainvoke(self, state: PlannerAgentState) -> PlannerAgentState` and `def invoke(self, state: PlannerAgentState) -> PlannerAgentState`, importable as `from agents import PlannerAgent`. Calling `ainvoke` with `{"messages": [], "diagnosis_result": ..., "iteration_count": 0, "repo_url": ..., "bare_path": "", "repo_path": "", "branch": ""}` returns a dict containing `"plan": RemediationPlan(...)`.

- [ ] **Step 1: Create `agents/planner_agent.py`**

```python
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing import Any, Literal
from utils import (
    slugify,
    ensure_base_clone,
    create_task_worktree,
    remove_task_worktree,
    repo_lock,
)
from .plan_classifier import PlanClassifier
from graph.state import PlannerAgentState
from models import RemediationPlan
import logging
import uuid

logging.basicConfig(level=logging.INFO, format="%(message)s")


class PlannerAgent:
    def __init__(self, llm: BaseChatModel, tools: list[BaseTool]) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.logger = logging.getLogger("planner_agent")
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = f"""
            You are a read-only GitOps planning agent. You investigate a GitOps repo to
            produce a concrete, file-by-file remediation plan for a diagnosed Kubernetes
            problem — you never modify anything; a separate remediation agent performs
            the actual edits from your plan.

            The `working_directory` and the diagnosed issue are provided below on every turn.

            Available tools — this is the complete list, there is no shell, git, or terminal
            access, and no other tool exists: {", ".join(t.name for t in tools)}.

            Workflow:
            0. Use `read_file_content` on AGENT.md (if present) for repo/architecture context.
            1. Use `find` and `grep` to locate the manifest(s) relevant to the diagnosed issue
            — do not guess a path.
            2. Use `list_files_in_directory` to explore surrounding structure when needed.
            3. Use `read_file_content` to see the current, exact content of every file your
            plan will reference — every old_content value in your final plan must be copied
            verbatim from a file you actually read in this investigation.

            Constraints:
            - Do not propose changes outside what the diagnosed issue requires.
            - Preserve existing YAML structure, key ordering, and indentation style in any
            new_content you propose.
            - If you cannot find the relevant file(s) with reasonable confidence, stop and
            say so plainly — do not guess a plan from unread files.

            When you have gathered enough evidence to write concrete steps (or determined you
            cannot), stop calling tools — a separate step turns your findings into the final
            structured plan.
        """
        self.classifier = PlanClassifier(llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=PlannerAgentState)
        graph.add_node("setup_node", self._setup_node)
        graph.add_node("reasoning_node", self._reasoning_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("finalize_node", self._finalize_node)
        graph.add_node("cleanup_node", self._cleanup_node)

        graph.add_edge(START, "setup_node")
        graph.add_edge("setup_node", "reasoning_node")
        graph.add_conditional_edges(
            "reasoning_node",
            self._tool_routing,
            {"tool_node": "tool_node", "finalize_node": "finalize_node"},
        )
        graph.add_edge("tool_node", "reasoning_node")
        graph.add_edge("finalize_node", "cleanup_node")
        graph.add_edge("cleanup_node", END)
        return graph.compile()

    def _setup_node(self, state: PlannerAgentState) -> dict[str, Any]:
        self.logger.info("\n=== setup ===")
        branch = f"plan/{slugify(state['diagnosis_result'].summary)}-{uuid.uuid4().hex[:6]}"
        with repo_lock(state["repo_url"]):
            bare_path = ensure_base_clone(state["repo_url"])
            repo_path = create_task_worktree(bare_path, branch)
        self.logger.info(f"  repo: {bare_path}")
        self.logger.info(f"  worktree: {repo_path} (branch {branch})")
        return {"bare_path": bare_path, "repo_path": repo_path, "branch": branch}

    def _cleanup_node(self, state: PlannerAgentState) -> dict[str, Any]:
        self.logger.info("\n=== cleanup ===")
        try:
            remove_task_worktree(state["bare_path"], state["repo_path"])
            self.logger.info(f"  removed worktree: {state['repo_path']}")
        except Exception as e:
            self.logger.info(f"  failed to remove worktree: {e}")
        return {}

    def _reasoning_node(self, state: PlannerAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        diagnosis_result = state["diagnosis_result"]
        root_cause_line = f"\nroot_cause: {diagnosis_result.root_cause}" if diagnosis_result.root_cause else ""
        issue_text = f"summary: {diagnosis_result.summary}{root_cause_line}"

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nworking_directory: {state['repo_path']}\n\ndiagnosed issue:\n{issue_text}"

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = self.llm.invoke(messages)

        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")

        return {
            "messages": [response],
            "iteration_count": iteration,
        }

    def _tool_node(self, state: PlannerAgentState) -> dict[str, Any]:
        result = self._tool_executor.invoke(state)
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _tool_routing(self, state: PlannerAgentState) -> Literal["tool_node", "finalize_node"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            self.logger.info(f"  hit MAX_ITERATIONS ({self.MAX_ITERATIONS}), stopping")
            return "finalize_node"

        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "finalize_node"

    async def _finalize_node(self, state: PlannerAgentState) -> dict[str, Any]:
        self.logger.info("\n=== finalize ===")
        hit_max_iterations = state.get("iteration_count", 0) >= self.MAX_ITERATIONS
        last_message = state["messages"][-1]
        incomplete = hit_max_iterations and bool(getattr(last_message, "tool_calls", None))

        if incomplete:
            self.logger.warning(
                f"  MAX_ITERATIONS ({self.MAX_ITERATIONS}) hit mid-investigation "
                f"— forcing a failed plan instead of trusting partial results"
            )
            parsed = RemediationPlan(
                summary=(
                    "Investigation did not complete within the iteration budget "
                    f"({self.MAX_ITERATIONS} steps). No concrete plan was produced."
                ),
                steps=[],
                planning_success=False,
            )
            return {"plan": parsed}

        parsed = await self.classifier.classify(state["diagnosis_result"], state["messages"])
        self.logger.info(f"  planning_success={parsed.planning_success} steps={len(parsed.steps)} summary={self._preview(parsed.summary)}")
        return {"plan": parsed}

    @staticmethod
    def _preview(text: Any, limit: int = 300) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, state: PlannerAgentState):
        return self.graph.invoke(state)

    async def ainvoke(self, state: PlannerAgentState):
        return await self.graph.ainvoke(state)
```

- [ ] **Step 2: Add the export to `agents/__init__.py`**

Current content (after Task 2):
```python
from .remediation_agent import RemediationAgent
from .diagnosis_agent import DiagnosisAgent
from .judge import DiffEvaluator
from .scenario_evaluator import ScenarioEvaluator
from .classifier import Classifier
from .plan_classifier import PlanClassifier
```

New content:
```python
from .remediation_agent import RemediationAgent
from .diagnosis_agent import DiagnosisAgent
from .judge import DiffEvaluator
from .scenario_evaluator import ScenarioEvaluator
from .classifier import Classifier
from .plan_classifier import PlanClassifier
from .planner_agent import PlannerAgent
```

- [ ] **Step 3: Self-verify the graph builds and the fail-safe branch works, with git and the LLM mocked**

Run:
```bash
cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && python3 -c "
import asyncio
from unittest.mock import patch
from langchain_core.messages import AIMessage
from agents.planner_agent import PlannerAgent
from models import DiagnosisResult, RemediationPlan, RemediationStep

class FakeLLM:
    def bind_tools(self, tools):
        return self
    def with_structured_output(self, model, include_raw=True):
        return self

diagnosis = DiagnosisResult(summary='pod crashlooping', root_cause='OOMKilled', requires_remediation=True, diagnosis_success=True)

async def main():
    with patch('agents.planner_agent.ensure_base_clone', return_value='/tmp/fake-bare'), \
         patch('agents.planner_agent.create_task_worktree', return_value='/tmp/fake-worktree'), \
         patch('agents.planner_agent.remove_task_worktree'), \
         patch('agents.planner_agent.repo_lock') as mock_lock:
        mock_lock.return_value.__enter__ = lambda self: None
        mock_lock.return_value.__exit__ = lambda self, *a: None

        agent = PlannerAgent(FakeLLM(), tools=[])
        agent.classifier.classify = lambda diagnosis_result, messages: asyncio.sleep(0, result=RemediationPlan(
            summary='ok', steps=[RemediationStep(step_number=1, file_path='a.yaml', description='d', new_content='x')], planning_success=True,
        ))
        # force straight to finalize: no tool_calls on the first reasoning response
        agent.llm.invoke = lambda messages: AIMessage(content='done', tool_calls=[])

        result = await agent.ainvoke({
            'messages': [], 'diagnosis_result': diagnosis, 'iteration_count': 0,
            'repo_url': 'https://example.com/repo.git', 'bare_path': '', 'repo_path': '', 'branch': '',
        })
        print('plan.planning_success:', result['plan'].planning_success)
        print('plan.steps:', len(result['plan'].steps))
        print('branch starts with plan/:', result['branch'].startswith('plan/') if 'branch' in result else 'n/a')

asyncio.run(main())
"
```
Expected output:
```
plan.planning_success: True
plan.steps: 1
branch starts with plan/: True
```

- [ ] **Step 4: Run the full unit test suite to confirm no regressions**

Run: `cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && pytest -q`
Expected: `85 passed, 10 deselected`

- [ ] **Step 5: Commit**

```bash
git add agents/planner_agent.py agents/__init__.py
git commit -m "$(cat <<'EOF'
Add PlannerAgent

Read-only ReAct agent that investigates a GitOps repo and produces a
structured RemediationPlan for a diagnosed issue, mirroring
RemediationAgent's git setup/cleanup and DiagnosisAgent's
reasoning/tool/finalize loop.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Wire PlannerAgent into graph/nodes.py

**Files:**
- Modify: `graph/nodes.py`

**Interfaces:**
- Consumes: `PlannerAgent` from Task 4 (`from agents import PlannerAgent`).
- Produces: `make_planner_node(planner_agent: PlannerAgent)` returning an async node function, consumed by Task 6. `require_remediation_routing_node` now returns `Literal["planner_agent", "end"]` (was `Literal["human_approval_node", "end"]`).

- [ ] **Step 1: Replace `graph/nodes.py` in full**

```python
from typing import Literal

from langgraph.types import interrupt
from agents import DiagnosisAgent, PlannerAgent, RemediationAgent
from graph.state import OrchestratorState


def make_diagnose_node(diagnosis_agent: DiagnosisAgent):
    async def diagnose_node(state: OrchestratorState) -> dict:
        result = await diagnosis_agent.ainvoke({
            "messages": [],
            "query": state["query"],
            "iteration_count": 0,
        })
        return {"diagnosis_result": result["diagnosis_result"]}

    return diagnose_node


def require_remediation_routing_node(state: OrchestratorState) -> Literal["planner_agent", "end"]:
    diagnosis_result = state["diagnosis_result"]
    # End only on a confident diagnosis that found nothing to fix. Every other case —
    # a confirmed issue, or an inconclusive/incomplete investigation — goes to a human;
    # never fail-open by defaulting an uncertain result to "end".
    if diagnosis_result.diagnosis_success and not diagnosis_result.requires_remediation:
        return "end"
    return "planner_agent"


def make_planner_node(planner_agent: PlannerAgent):
    async def planner_node(state: OrchestratorState) -> dict:
        result = await planner_agent.ainvoke({
            "messages": [],
            "diagnosis_result": state["diagnosis_result"],
            "iteration_count": 0,
            "repo_url": state["repo_url"],
            "bare_path": "",
            "repo_path": "",
            "branch": "",
        })
        return {"plan": result["plan"]}

    return planner_node


def human_approval_node(state: OrchestratorState) -> dict:
    decision = interrupt({
        "diagnosis": state["diagnosis_result"],
        "plan": state["plan"],
    })
    return {
        "approved": decision.get("approved", False),
    }


def approval_routing(state: OrchestratorState) -> Literal["remediation_agent", "end"]:
    return "remediation_agent" if state["approved"] else "end"


def make_remediate_node(remediation_agent: RemediationAgent):
    async def remediate_node(state: OrchestratorState) -> dict:
        result = await remediation_agent.ainvoke({
            "messages": [],
            "plan": state["plan"],
            "iteration_count": 0,
            "eval_passed": False,
            "eval_reasoning": "",
            "pr_url": "",
            "repo_url": state["repo_url"],
            "bare_path": "",
            "repo_path": "",
            "branch": "",
        })
        return {
            "pr_url": result.get("pr_url", ""),
            "eval_passed": result.get("eval_passed", False),
            "eval_reasoning": result.get("eval_reasoning", ""),
        }

    return remediate_node
```

- [ ] **Step 2: Verify the module imports and the routing truth table is unchanged except for the target name**

Run:
```bash
cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && python3 -c "
from graph.nodes import require_remediation_routing_node, make_planner_node
from models import DiagnosisResult

for success in (True, False):
    for remediation in (True, False):
        dr = DiagnosisResult(summary='s', requires_remediation=remediation, diagnosis_success=success)
        route = require_remediation_routing_node({'diagnosis_result': dr})
        print(f'success={success!s:5} requires_remediation={remediation!s:5} -> {route}')
"
```
Expected output:
```
success=True  requires_remediation=True  -> planner_agent
success=True  requires_remediation=False -> end
success=False requires_remediation=True  -> planner_agent
success=False requires_remediation=False -> planner_agent
```

- [ ] **Step 3: Commit**

```bash
git add graph/nodes.py
git commit -m "$(cat <<'EOF'
Wire PlannerAgent into the orchestrator's node functions

require_remediation_routing_node now routes to planner_agent instead
of straight to human_approval_node. human_approval_node's interrupt
payload includes the plan; remediate_node passes plan instead of
diagnosis_result.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire PlannerAgent into graph/builder.py

**Files:**
- Modify: `graph/builder.py`

**Interfaces:**
- Consumes: `make_planner_node`, `require_remediation_routing_node` (updated) from Task 5; `PlannerAgent` from Task 4.
- Produces: `init_planner_agent() -> PlannerAgent`, and the compiled graph now has a `planner_agent` node between `diagnosis_agent` and `human_approval_node`.

- [ ] **Step 1: Update the imports and add `init_planner_agent` in `graph/builder.py`**

Change the imports block from:
```python
from graph.nodes import (
    make_diagnose_node,
    human_approval_node,
    require_remediation_routing_node,
    approval_routing,
    make_remediate_node,
)
from graph.state import OrchestratorState
from agents import DiagnosisAgent, RemediationAgent
from utils import create_llm_model
```
to:
```python
from graph.nodes import (
    make_diagnose_node,
    make_planner_node,
    human_approval_node,
    require_remediation_routing_node,
    approval_routing,
    make_remediate_node,
)
from graph.state import OrchestratorState
from agents import DiagnosisAgent, PlannerAgent, RemediationAgent
from utils import create_llm_model
```

Add `init_planner_agent` right after `init_diagnosis_agent`:
```python
async def init_diagnosis_agent() -> DiagnosisAgent:
    k8s_tools = await get_k8s_mcp_tools()
    promql_tools = await get_promql_mcp_tools()
    return DiagnosisAgent(llm, k8s_tools + promql_tools)


async def init_planner_agent() -> PlannerAgent:
    file_tools = [
        find,
        grep,
        list_files_in_directory,
        read_file_content,
    ]
    return PlannerAgent(llm, file_tools)
```

- [ ] **Step 2: Rewire `build_graph()`**

Change from:
```python
async def build_graph() -> CompiledStateGraph:
    diagnosis_agent = await init_diagnosis_agent()
    remediation_agent = await init_remediation_agent()

    graph = StateGraph(state_schema=OrchestratorState)
    graph.add_node("diagnosis_agent", make_diagnose_node(diagnosis_agent))
    graph.add_node("human_approval_node", human_approval_node)
    graph.add_node("remediation_agent", make_remediate_node(remediation_agent))

    graph.add_edge(START, "diagnosis_agent")
    graph.add_conditional_edges(
        "diagnosis_agent",
        require_remediation_routing_node,
        {"human_approval_node": "human_approval_node", "end": END},
    )
    graph.add_conditional_edges(
        "human_approval_node",
        approval_routing,
        {"remediation_agent": "remediation_agent", "end": END},
    )
    graph.add_edge("remediation_agent", END)

    return graph.compile(checkpointer=MemorySaver())
```
to:
```python
async def build_graph() -> CompiledStateGraph:
    diagnosis_agent = await init_diagnosis_agent()
    planner_agent = await init_planner_agent()
    remediation_agent = await init_remediation_agent()

    graph = StateGraph(state_schema=OrchestratorState)
    graph.add_node("diagnosis_agent", make_diagnose_node(diagnosis_agent))
    graph.add_node("planner_agent", make_planner_node(planner_agent))
    graph.add_node("human_approval_node", human_approval_node)
    graph.add_node("remediation_agent", make_remediate_node(remediation_agent))

    graph.add_edge(START, "diagnosis_agent")
    graph.add_conditional_edges(
        "diagnosis_agent",
        require_remediation_routing_node,
        {"planner_agent": "planner_agent", "end": END},
    )
    graph.add_edge("planner_agent", "human_approval_node")
    graph.add_conditional_edges(
        "human_approval_node",
        approval_routing,
        {"remediation_agent": "remediation_agent", "end": END},
    )
    graph.add_edge("remediation_agent", END)

    return graph.compile(checkpointer=MemorySaver())
```

`main()` is unchanged — leave it exactly as-is (including the commented-out demo block).

- [ ] **Step 3: Verify the module imports from both entry points**

Run:
```bash
cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && python3 -c "
from agents import DiagnosisAgent, PlannerAgent, RemediationAgent
print('agents-first OK')
"
python3 -c "
import graph
from graph.builder import build_graph
print('graph-first OK')
"
```
Expected output:
```
agents-first OK
graph-first OK
```

- [ ] **Step 4: Verify the compiled graph has the expected node/edge shape, with all three agents mocked (no real LLM or MCP calls)**

Run:
```bash
cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && python3 -c "
import asyncio
from unittest.mock import patch, AsyncMock
import graph.builder as builder

async def main():
    with patch.object(builder, 'init_diagnosis_agent', new=AsyncMock(return_value=object())), \
         patch.object(builder, 'init_planner_agent', new=AsyncMock(return_value=object())), \
         patch.object(builder, 'init_remediation_agent', new=AsyncMock(return_value=object())), \
         patch.object(builder, 'make_diagnose_node', return_value=lambda state: {}), \
         patch.object(builder, 'make_planner_node', return_value=lambda state: {}), \
         patch.object(builder, 'make_remediate_node', return_value=lambda state: {}):
        compiled = await builder.build_graph()
        nodes = set(compiled.get_graph().nodes.keys())
        print('planner_agent in graph:', 'planner_agent' in nodes)
        print('nodes:', sorted(n for n in nodes if n not in ('__start__', '__end__')))

asyncio.run(main())
"
```
Expected output:
```
planner_agent in graph: True
nodes: ['diagnosis_agent', 'human_approval_node', 'planner_agent', 'remediation_agent']
```

- [ ] **Step 5: Run the full unit test suite one final time**

Run: `cd /Users/brymat24/repos/Kubernaut && source .venv/bin/activate && pytest -q`
Expected: `85 passed, 10 deselected`

- [ ] **Step 6: Commit**

```bash
git add graph/builder.py
git commit -m "$(cat <<'EOF'
Wire PlannerAgent into the orchestrator graph

diagnosis_agent now routes to planner_agent (instead of straight to
human_approval_node) whenever a fix might be needed; planner_agent
always proceeds to human_approval_node afterward.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- "take in the root cause and the issue from the diagnosis agent" → Task 4, `PlannerAgentState.diagnosis_result` + `_reasoning_node`'s `issue_text`. ✓
- "search and see the repo (make sure latest change)... refer to how remediation_agent sets git repo up" → Task 4 `_setup_node`, reuses `ensure_base_clone`/`create_task_worktree`/`repo_lock` (and `ensure_base_clone` fetches `origin/main` on every call when the bare clone already exists, so "latest" is enforced by that shared helper — no new logic needed). ✓
- "This is a read only agent" → Task 4, tool list is `find`/`grep`/`list_files_in_directory`/`read_file_content` only (Task 6, `init_planner_agent`). ✓
- "A new model that outputs the remediation steps... clear and detailed in which file... old and expected new output" → Task 1, `RemediationStep`. ✓
- "modify the nodes.py and builder.py" → Tasks 5 and 6. ✓
- Runs "before human_approval interrupt and after the diagnosis agent" → Task 5/6 graph wiring: `diagnosis_agent → planner_agent → human_approval_node`. ✓

**Placeholder scan:** No TBD/TODO; every step has complete, runnable code or an exact command with expected output.

**Type consistency:** `PlannerAgentState` (Task 3) fields match `_setup_node`/`_reasoning_node`/`_finalize_node`'s usage in Task 4 (`diagnosis_result`, `iteration_count`, `plan`, `repo_url`, `bare_path`, `repo_path`, `branch`). `RemediationAgentState.plan` (Task 3) matches `make_remediate_node`'s `"plan": state["plan"]` (Task 5) and `RemediationAgent._task_description(plan: RemediationPlan)` (Task 3). `require_remediation_routing_node`'s return value `"planner_agent"` (Task 5) matches the node name registered in `build_graph()` (Task 6) and the conditional-edges mapping key. `PlanClassifier.classify(diagnosis_result, messages)` (Task 2) matches `PlannerAgent._finalize_node`'s call `self.classifier.classify(state["diagnosis_result"], state["messages"])` (Task 4).
