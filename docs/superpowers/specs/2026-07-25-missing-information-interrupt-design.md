# Missing-Information Interrupt — Design

## Problem

`PlannerAgent` sometimes correctly identifies the exact file and mechanism of a fix, but the fix
requires a concrete value it has no way to know — most commonly a valid replacement image tag for
an `ErrImagePull`/`ImagePullBackOff` fix. `PlannerAgent`'s tools are local, read-only git-repo
tools only (`find`, `grep`, `list_files_in_directory`, `read_file_content`) — there is no registry
access, so it cannot discover which tags actually exist. Even if it could, *which* tag is correct
is often a release decision, not a mechanical lookup.

Today this produces a plan that names the impossible fix ("change the image tag to a valid one")
without a valid value, still marked in a way that reaches `human_approval_node` for an
approve/reject decision — a decision that cannot meaningfully be made, since the proposed change
isn't actually executable. Root cause, traced in code:

- `graph/builder.py`'s `planner_agent → human_approval_node` edge is **unconditional**. Unlike the
  diagnosis→planner transition (gated by `require_remediation_routing_node` on
  `diagnosis_success`/`requires_remediation`), nothing checks `plan.planning_success` before
  routing to human approval.
- `PlanClassifier`'s prompt doesn't clearly distinguish "no established repo convention for this
  value" (fine, note it and proceed) from "this value is fundamentally undiscoverable from this
  repo" (should not produce an executable plan at all).
- `graph/nodes.py`'s `planner_node` discards the LLM's own explanation on failure, replacing it
  with a generic `"Could not produce a safe plan."` message — so even the status text doesn't say
  what's missing.

## Goal

When `PlannerAgent` needs one specific piece of information it cannot determine itself, the
system should ask the human that specific question — not present an unexecutable plan for
approve/reject, and not silently give up either.

## Design

### Data model

`RemediationPlan` (`models/remediation_plan.py`) gains:

```python
missing_information: str | None = Field(
    default=None,
    description="Set ONLY when the plan is otherwise fully investigated and correct except for "
    "one concrete value that cannot be determined from repository evidence (e.g. a valid image "
    "tag, an external IP, a secret's real value) -- state the exact question to ask a human, "
    "e.g. 'What image tag should be used for brymat24/test-cache-app?'. When set, "
    "planning_success must be False and steps must be empty -- this is not an executable plan."
)
```

`PlanClassifier`'s prompt is extended to teach the distinction between two `planning_success=False`
cases: (1) investigation genuinely couldn't locate the relevant file(s), or was inconclusive —
`missing_information` stays `None`, behavior unchanged; (2) the exact file and fix mechanism were
found, but one concrete value is fundamentally unknowable from this repo (not just "no established
convention," which still gets a best-effort value per the existing prompt) — set
`missing_information` to the precise question.

### Agent / graph changes

**Chosen approach: re-invoke the planner with the human's answer as context** (not placeholder
substitution) — more robust, since "missing information" can in general require re-deriving parts
of the plan, not just filling in one string. `PlannerAgentState` gains
`human_provided_info: str`, threaded into `_reasoning_node`'s system prompt when present, so the
retry actually incorporates the answer during a fresh investigation.

`OrchestratorState` gains `human_provided_info: str` and `missing_info_rounds: int` (a bounded
retry counter — capped at 2 rounds, matching the existing bounded-retry pattern used elsewhere in
this codebase, e.g. `DiagnosisAgent.MAX_HYPOTHESES`/`MAX_INVESTIGATE_ITERATIONS`, so a confused
loop can't ask forever).

New node `missing_info_node` sits between `planner_agent` and `human_approval_node`:

```python
MAX_MISSING_INFO_ROUNDS = 2

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

New routing function replaces the current unconditional edge:

```python
def planning_outcome_routing(state: OrchestratorState) -> Literal["missing_info_node", "human_approval_node", "end"]:
    plan = state["plan"]
    if plan.missing_information and state.get("missing_info_rounds", 0) < MAX_MISSING_INFO_ROUNDS:
        return "missing_info_node"
    if not plan.planning_success:
        return "end"
    return "human_approval_node"
```

Edges: `planner_agent → (planning_outcome_routing) → {missing_info_node, human_approval_node,
END}`; `missing_info_node → planner_agent` (loop, re-plan with the new context).

`human_approval_node`'s own interrupt payload gains an explicit `"type": "approval"` key, for
symmetry with the new interrupt type — both interrupt payloads are now self-describing.

Bundled fix: `planner_node`'s status message currently discards the LLM's own explanation on
failure (hardcoded `"Could not produce a safe plan."`). It will use `plan.summary` instead (which
`PlanClassifier` already populates with a real explanation on failure), and a distinct message
when `missing_information` is set.

### API changes

`POST /diagnose`'s interrupt handling currently assumes every interrupt is the approval shape
(destructures `interrupt_payload["diagnosis"]`/`["plan"]` unconditionally) — this breaks once the
*first* interrupt reached is often `missing_information` instead. It will branch on
`interrupt_payload["type"]` first.

New endpoint `POST /answer/{thread_id}` (new `MissingInfoAnswer{answer: str}` schema in
`api/schemas.py`), structurally mirroring `/approve/{thread_id}`: resumes with
`Command(resume={"answer": body.answer})`; its own interrupt-handling branches the same way as
`/diagnose`'s (a re-plan can land on another `missing_information` interrupt — loop the UI back
to asking — a resolved plan — `pending_approval` — or, if it exhausted the round cap, a terminal
`complete` message explaining that).

`/approve/{thread_id}` needs no changes — its interrupt handling already forwards any interrupt
payload generically (an existing comment already anticipates this: "e.g. planner_agent hits
another interrupt downstream, or a second approval gate"). In this design `missing_info_node` is
always resolved *before* `human_approval_node` is ever reached, so this path is currently unused
by this feature, but is already correct if that ever changes.

### UI changes

`ui/lib/api.ts`: extend the diagnose/answer response status union with `"pending_info"` and a
`question` field; add `streamAnswer(threadId, answer, onProgress)`.

New `MissingInfoCard.tsx` component, structurally mirroring `PlanCard.tsx`: renders the question,
a text input, and a submit button; calls `streamAnswer`; resolves into one of: another
`MissingInfoCard` (another round), a `PlanCard` (ready for approval), or a terminal message —
matching the same resolution pattern `PlanCard` already uses via its `onResolved` callback, just
broadened to accept more than one outcome kind.

`ChatView.tsx`: `TimelineItem` union gains a `pending_info` variant; `handleSend` branches on
`response.status === "pending_info"` the same way it already branches on `pending_approval`.

## Out of scope

- Placeholder-substitution (Option A from the original discussion) — rejected in favor of the
  more robust re-invoke-the-planner approach.
- Any registry-query tool that would let `PlannerAgent` discover valid image tags itself — even
  with that capability, *which* tag is correct is a release decision a human should make, not
  something to guess from "any tag that exists."
- Multiple simultaneous missing-information questions per plan — this design asks one question at
  a time, bounded to 2 rounds; not building support for a batch of questions.
