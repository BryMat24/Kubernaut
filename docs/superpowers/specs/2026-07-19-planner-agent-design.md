# PlannerAgent design

Date: 2026-07-19
Status: approved, pending implementation
Source request: `PLAN.md` at repo root

## Problem

`DiagnosisAgent` produces a `DiagnosisResult` (summary, root cause, whether remediation is
needed). When a fix is needed, that raw diagnosis is currently handed straight to
`RemediationAgent` as its task description. `RemediationAgent` then has to do its own
from-scratch repo investigation (locate the relevant manifest(s), read them, figure out what to
change) before it can make any edit — duplicating investigation work and giving the human
approving the fix nothing concrete to review beyond a paragraph of diagnosis text.

## Goal

Insert a new read-only `PlannerAgent` between `DiagnosisAgent` and the human-approval gate. It
takes the diagnosed root cause, investigates the GitOps repo (read-only), and produces a
structured, file-by-file `RemediationPlan` — precise enough that `RemediationAgent` can execute
it without re-investigating, and concrete enough that a human can review *exactly* what will
change before approving.

## Non-goals

- `PlannerAgent` never writes to the repo. All edits remain `RemediationAgent`'s job.
- No new integration-test infrastructure. `RemediationAgent`, the closest existing analog, has
  none either — see Testing section.
- No changes to `DiagnosisAgent` itself.

## Architecture

### PlannerAgent's own graph

Structurally the union of `RemediationAgent`'s setup/cleanup pattern and `DiagnosisAgent`'s
reasoning/tool/finalize loop:

```
START → setup_node → reasoning_node ⇄ tool_node → finalize_node → cleanup_node → END
```

- **`setup_node` / `cleanup_node`**: own `ensure_base_clone` / `create_task_worktree` /
  `repo_lock` / `remove_task_worktree` calls from `utils/git_utils.py` — a **separate, isolated
  worktree** from whatever `RemediationAgent` later creates for the same `repo_url` (branch
  prefix `plan/...`, vs. `RemediationAgent`'s `agent/...`, so they're distinguishable in
  `git worktree list`). `RemediationAgent` is not modified to share state with `PlannerAgent`.

  *Alternative considered and rejected*: have `PlannerAgent`'s setup create the worktree that
  `RemediationAgent` then reuses (guarantees byte-identical state between planning and editing,
  but requires restructuring `RemediationAgent`'s setup flow to accept a pre-existing worktree —
  risk to an already-working, tested agent, for a benefit — avoiding a second cheap
  cached-bare-repo checkout — that doesn't justify it).

- **`reasoning_node` ⇄ `tool_node`**: ReAct loop bound to **read-only tools only** —
  `find`, `grep`, `list_files_in_directory`, `read_file_content`. No `edit_file`/`write_file`.

- **`finalize_node`**: calls `PlanClassifier` (new, mirrors `agents/classifier.py`'s
  `Classifier`) to turn the investigation into a structured `RemediationPlan`. If
  `MAX_ITERATIONS` is hit mid-investigation (same `incomplete` check `DiagnosisAgent` uses),
  skips the classifier call and constructs `RemediationPlan(summary=..., steps=[],
  planning_success=False)` directly — same fail-safe shape as `DiagnosisAgent`'s incomplete
  branch.

### Orchestrator graph change

`planner_agent` is inserted only on the branch that already leads to a human — informational
queries that route straight to `end` never trigger a repo clone:

```
diagnosis_agent --require_remediation_routing_node--> {planner_agent, end}
planner_agent --> human_approval_node
human_approval_node --approval_routing--> {remediation_agent, end}
remediation_agent --> end
```

`human_approval_node`'s interrupt payload gains the plan: `{"diagnosis": ..., "plan": ...}`.
`remediate_node` passes `state["plan"]` to `RemediationAgent` instead of
`state["diagnosis_result"]`.

*Alternative considered and rejected*: keep both `diagnosis_result` and `plan` on
`RemediationAgentState`. Rejected — leaves it ambiguous which one governs if they disagree, and
duplicates information the plan should already summarize (via `RemediationPlan.summary`).

## Data model

**`models/remediation_plan.py`** (new):

```python
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

Field descriptions are mandatory here, not optional polish — an earlier review of
`DiagnosisResult.diagnosis_success` found that a required structured-output field with no
description leaves the LLM with zero grounding for a value that gates real behavior. Every field
above gets one.

**`graph/state.py` changes**:

```python
class OrchestratorState(TypedDict):
    query: str
    repo_url: str
    diagnosis_result: DiagnosisResult
    plan: RemediationPlan          # new
    approved: bool
    pr_url: str
    eval_passed: bool
    eval_reasoning: str

class PlannerAgentState(MessagesState):   # new
    diagnosis_result: DiagnosisResult     # provided by caller (issue to plan for)
    iteration_count: int
    plan: RemediationPlan

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str

class RemediationAgentState(MessagesState):
    plan: RemediationPlan          # replaces diagnosis_result
    iteration_count: int
    eval_passed: bool
    eval_reasoning: str
    pr_url: str

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str
```

`RemediationAgent._task_description` is rewritten to format from `RemediationPlan` (numbered
steps with file/old_content/new_content/description) instead of `DiagnosisResult` — this is the
actual payoff PLAN.md asks for ("so the agentic search will be much faster for the remediation
agent"). Branch names / PR titles / commit messages use `plan.summary[:72]` directly (short,
human-readable) rather than slugifying the full multi-step dump.

## Error handling & fail-safes

- **`PlannerAgent._finalize_node`** incomplete branch: see Architecture section above.
- **`PlanClassifier`**: same `with_structured_output(..., include_raw=True)` +
  `parsed is None` fallback as `Classifier`/`DiffEvaluator`/`ScenarioEvaluator`, falling back to
  `planning_success=False` with the raw model output as `summary`.
- **`planning_success=False` is not wired into a routing gate.** `planner_agent →
  human_approval_node` stays an unconditional edge — we're already past the "does this need a
  human" decision by the time planning runs. The human sees `planning_success: false` and an
  empty `steps` list in the interrupt payload and can approve/reject based on the raw diagnosis
  alone. `RemediationAgent` doesn't special-case an empty plan; `_task_description` on an empty
  `steps` list just renders `summary` with no step list, and the ReAct loop investigates from
  that description the same way it does today when given a thin task.
- **Cleanup guarantee**: `cleanup_node` keeps the `try/except`-and-log pattern (never raises), so
  a worktree-removal failure can't crash the graph after a plan was already produced — identical
  to `RemediationAgent._cleanup_node`.
- **`setup_node` failure** (e.g. bad `repo_url`): unhandled, matching `RemediationAgent`'s
  existing (accepted) behavior. Not something new to solve here.

## Testing

Following existing precedent: `DiagnosisAgent`/`RemediationAgent`'s own reasoning/tool loops have
no unit tests (only `tools/file_tools.py`'s pure functions do), and `RemediationAgent` has zero
integration tests despite being the closest analog to `PlannerAgent`. So:

- **Add** a unit test for `RemediationAgent._task_description(plan: RemediationPlan)` in
  `test/unit_test/remediation_agent/` — new pure function, and the concrete payoff this feature
  is meant to deliver, worth locking down.
- **No new integration-test infrastructure** for `PlannerAgent`'s investigate loop.
- **Self-verify** the new graph wiring and `PlanClassifier` message-shape with a mocked-LLM dry
  run before calling implementation done (not committed as a test file).
- **Run the full `pytest` suite** after implementation to confirm no regressions.

## Summary of files touched

- `models/remediation_plan.py` — new: `RemediationPlan`, `RemediationStep`
- `agents/plan_classifier.py` — new: `PlanClassifier`
- `agents/planner_agent.py` — new: `PlannerAgent`
- `graph/state.py` — `PlannerAgentState` new; `OrchestratorState`/`RemediationAgentState` updated
- `graph/nodes.py` — `make_planner_node` new; `human_approval_node` shows the plan;
  `remediate_node` passes `plan`; `require_remediation_routing_node`'s target string renamed
  from `"human_approval_node"` to `"planner_agent"`
- `graph/builder.py` — `init_planner_agent` new; edges rewired
- `agents/remediation_agent.py` — system prompt + `_task_description` rewritten for
  `RemediationPlan`
- `agents/__init__.py` / `models/__init__.py` — new exports
- `test/unit_test/remediation_agent/` — new test for `_task_description`
