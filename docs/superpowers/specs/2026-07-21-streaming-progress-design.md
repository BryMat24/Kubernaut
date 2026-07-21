# Streaming progress and summarized chat messages

Date: 2026-07-21
Status: approved, pending implementation

## Problem

`plan.md` raised two UX gaps in the chat UI (`docs/superpowers/specs/2026-07-20-chat-ui-design.md`):

1. `POST /diagnose` and `POST /approve/{thread_id}` are synchronous — they can block for
   minutes while the diagnosis/planner/remediation sub-agents loop through tool calls — and the
   UI shows nothing but a static "kubernaut is thinking…" the entire time.
2. The persisted chat history contains messy, stale-looking text. Root cause found while
   investigating: `POST /diagnose` persists a pending-approval message ending in `"(Awaiting your
   approval)"`, and `POST /approve` **appends a separate new message** for the outcome instead of
   updating the first one. On reload, a resolved turn shows two disjointed messages — one
   permanently frozen saying "awaiting approval" even after it was resolved, plus a disconnected
   outcome message.

## Goal

Live phase/status progress during a run (e.g. "investigating the cluster…" → "root cause found"
→ "building remediation plan" → "applying the fix"), streamed to the UI as it happens. Chat
history that shows exactly one clean, always-current summary message per turn: what was
investigated, the root cause, the proposed fix, and the PR link (or rejection) once resolved.

## Non-goals

- No token-by-token LLM text streaming — progress is phase/status-level only (see Backend
  architecture), not a live-typing effect of the model's raw output.
- No live interactive Approve/Reject card reconstruction from history after a reload — this is
  an existing, explicit limitation from the chat-UI spec and is unaffected by this change (only
  the message *content* changes, not the "history is plain text" model).
- No resilience to a dropped client connection mid-run (see Error handling) — a long remediation
  run isn't decoupled into a background job in this pass.
- No new automated test infrastructure for the SSE wire format or frontend streaming UI (matches
  this repo's existing pragmatic testing culture — see Testing).

## Backend architecture

`POST /diagnose` and `POST /approve/{thread_id}` keep their existing routes, methods, and request
bodies, but switch their response from a single JSON body to `text/event-stream` (SSE): a stream
of newline-delimited JSON events ending in one terminal event carrying the same payload shape
they return today. This is a breaking change to those two endpoints' response contract, which is
fine — the only consumer is `/ui`, updated in the same pass.

**Producing progress events:** LangGraph's `get_stream_writer()` (`langgraph.config`) gives any
node function a writer callable that is a no-op unless the graph invocation requested
`stream_mode="custom"` — safe to add without touching the CLI path in `graph/builder.py`'s
`main()` (which still calls `.ainvoke()`/ uses no custom stream mode). Two `writer(...)` calls
(`started` before, `completed` after the sub-agent's `.ainvoke()`) are added to each of
`make_diagnose_node`, `make_planner_node`, and `make_remediate_node` in `graph/nodes.py`. No
changes inside the sub-agents themselves (`DiagnosisAgent`, `PlannerAgent`, `RemediationAgent`
stay untouched) — this is orchestrator-level instrumentation only, at exactly the granularity of
the 4 top-level orchestrator nodes (diagnosis → planner → human-approval → remediation).

Event shape emitted by each node:

```json
{"phase": "diagnosis", "status": "started", "message": "Investigating the cluster…"}
{"phase": "diagnosis", "status": "completed", "message": "<diagnosis_result.summary>"}
```

Phases: `diagnosis`, `planner`, `remediation`. `human_approval_node` emits nothing itself (the
interrupt is immediate and carried by the terminal event, not a progress event).

Exact `message` text per event, so the implementer isn't guessing:

| Phase | `started` message | `completed` message |
|---|---|---|
| `diagnosis` | `"Investigating the cluster…"` | `diagnosis_result.summary` |
| `planner` | `"Building a remediation plan…"` | `plan.summary` if `plan.planning_success` else `"Could not produce a safe plan."` |
| `remediation` | `"Applying the fix…"` | `"Fix applied."` if `eval_passed` else `"Fix did not pass evaluation."` |

**In `api/main.py`:** `await app.state.graph.ainvoke(...)` is replaced with iterating
`app.state.graph.astream(input, config=config, stream_mode=["custom", "values"])`. Each
`"custom"` chunk is forwarded to the client immediately as an SSE `progress` event. The last
`"values"` chunk is the same dict `ainvoke()` used to return (including `__interrupt__` when
present), so the existing interrupt-detection / message-building / result-shaping logic is
reused, just moved to run after the loop instead of after a single await. The same conversion
applies to `POST /approve/{thread_id}`'s `graph.ainvoke(Command(resume=...), ...)` call.

The stream ends with exactly one terminal event:

```json
{"type": "final", "status": "complete", "thread_id": "...", "chat_id": "...", "result": {...}}
{"type": "final", "status": "pending_approval", "thread_id": "...", "chat_id": "...", "plan": {...}}
{"type": "error", "message": "..."}
```

matching today's JSON response shapes for `"complete"`/`"pending_approval"`, plus a new `"error"`
terminal type (see Error handling).

## Message persistence redesign

New helper, `api/summary.py::build_summary_message`:

```python
def build_summary_message(
    diagnosis: DiagnosisResult,
    plan: RemediationPlan | None = None,
    approved: bool | None = None,   # None = still pending
    pr_url: str | None = None,
) -> str:
    lines = [diagnosis.summary]
    if diagnosis.root_cause:
        lines.append(f"\nRoot cause: {diagnosis.root_cause}")
    if plan is not None:
        lines.append(f"\nProposed fix: {plan.summary}")
        if approved is None:
            lines.append("\n(Awaiting your approval)")
        elif approved:
            lines.append(f"\nApproved — PR opened: {pr_url}" if pr_url else "\nApproved, but no PR was opened.")
        else:
            lines.append("\nNot approved — no changes were made.")
    return "\n".join(lines)
```

- `POST /diagnose`: builds this message once (pending-approval or complete case), persisted as
  before — no behavior change here beyond calling the shared helper.
- `POST /approve/{thread_id}`: instead of inserting a new `Message` row for the outcome, **updates
  the existing pending message's `content` in place** (look it up the same way as today, by
  `thread_id`; set `.content = build_summary_message(...)` using the `diagnosis_result`/`plan`
  still present in the resumed graph's final state; commit). Result: exactly one message per turn,
  always reflecting the true current state — pending, approved+PR link, or rejected.

`GET /chats/{chat_id}/messages` is unchanged (same query, same response shape) — it now simply
returns the corrected, single, always-current message per turn instead of the old
stale-plus-appended pair.

## Frontend

**`ui/lib/api.ts`**: `diagnose()`/`approve()` are replaced with streaming versions that read the
SSE response body incrementally via `response.body.getReader()` — no new dependency, `fetch`
supports streaming response bodies natively:

```ts
streamDiagnose(
  query: string,
  chatId: string,
  onProgress: (event: ProgressEvent) => void
): Promise<DiagnoseResponse>

streamApprove(
  threadId: string,
  approved: boolean,
  onProgress: (event: ProgressEvent) => void
): Promise<ApproveResponse>
```

Both call `onProgress` for each `progress` event as it arrives and resolve with the terminal
event's payload (same `DiagnoseResponse`/`ApproveResponse` shapes as today), so the rest of the
calling code barely changes. An `"error"` terminal event rejects the returned promise with an
`ApiError`-like object, handled by the existing catch blocks in `ChatView`/`PlanCard`.

**New component `ui/components/ProgressTimeline.tsx`**: replaces the static "kubernaut is
thinking…" line. Renders the phase events received so far as a small growing checklist (e.g. "✓
Investigated the cluster" / "⋯ Building remediation plan…"), most recent shown as in-progress.
Local, ephemeral component state during an in-flight call only — discarded once the terminal
event arrives and replaced by the real message/`PlanCard`, matching the "live-only" precedent
already established by `PlanCard` itself.

**`ChatView.handleSend`** and **`PlanCard`'s approve/reject handlers** switch from
`await diagnose(...)`/`await approve(...)` to the streaming versions, passing a callback that
appends to a local `progressEvents` array rendered via `ProgressTimeline`. `Composer`/buttons stay
disabled for the whole duration exactly as today.

## Data flow

1. User sends a query → `ChatView` optimistically appends the user bubble, calls
   `streamDiagnose(query, chatId, onProgress)`.
2. Backend: `POST /diagnose` looks up the chat, persists the user message, starts
   `graph.astream(..., stream_mode=["custom", "values"])`.
3. As `diagnosis_agent` runs: `progress` events (`started` → `completed`) stream to the client;
   `ProgressTimeline` grows live.
4. Graph reaches `require_remediation_routing_node`:
   - No remediation needed → terminal `final` event, `status: "complete"`; backend persists
     `build_summary_message(diagnosis)`.
   - Remediation needed → `planner_agent` runs (more progress events) → `human_approval_node`
     interrupts → backend persists `build_summary_message(diagnosis, plan, approved=None)` →
     terminal `final` event, `status: "pending_approval"`, carrying the plan.
5. Frontend: on `"complete"` → append assistant `MessageBubble`. On `"pending_approval"` → append
   a live `PlanCard`, discard `ProgressTimeline`.
6. User clicks Approve/Reject on `PlanCard` → `streamApprove(threadId, approved, onProgress)`.
   Backend resumes via `Command(resume=...)`; if approved, `remediation_agent` runs with its own
   streamed progress events. Terminal event carries the outcome; backend **updates** (not
   inserts) the pending message in place.
7. Frontend: `PlanCard`'s `onResolved` callback fires as today, converting the timeline item into
   a resolved assistant message using the terminal event's outcome text.
8. Chat reload (`GET /chats/{id}/messages`): unaffected by streaming — same endpoint, now always
   returns the single up-to-date summary message per turn.

## Error handling

- **Mid-stream backend error** (agent throws, unrecoverable tool failure): emit a terminal
  `{"type": "error", "message": "..."}` SSE event instead of a bare stream close, so the frontend
  can distinguish "finished with an error" from "connection dropped". Rendered the same way the
  frontend already renders `ApiError` failures — inline `role="alert"` danger text — and
  `PlanCard`'s buttons re-enable on this path exactly as on a failed `approve()` today.
- **Client disconnects mid-run** (tab closed, network drop): FastAPI/uvicorn cancels the request
  handler, cancelling the in-flight `graph.astream()` call — the same behavior today's synchronous
  `ainvoke()` already has if the tab closes mid-request, so this is not a regression. Explicitly
  documented as an accepted limitation: a long remediation run is not resilient to a dropped
  connection in this pass (decoupling into a background job is real scope creep beyond "add
  progress visibility").
- **SSE parse failure** on a malformed/partial chunk: treated as a fatal stream error (same
  `"error"` UI path above) rather than silently dropping progress — matches the project's
  existing fail-closed philosophy (`require_remediation_routing_node`'s "never fail-open" comment
  in `graph/nodes.py`).

## Testing

Same pragmatic, no-new-test-infra approach as the rest of this project: no automated tests for the
SSE wire format or the frontend streaming UI (no test infra exists for either layer today).
`build_summary_message` is pure logic with clear inputs/outputs, so it gets unit tests under
`test/unit_test/api/` (matching the existing pure string-building test pattern in
`test/unit_test/remediation_agent/remediation_agent_test.py::test_task_description_*`), covering:
no-remediation case, pending-approval case, approved-with-PR, approved-without-PR, rejected.
Manual verification: run both servers, walk a full remediation round-trip in a real browser
watching the progress timeline update live, plus a reload mid-flow to confirm the persisted
message reflects the correct state at each stage.

## Summary of files touched

- `graph/nodes.py` — `get_stream_writer()` progress calls added to `make_diagnose_node`,
  `make_planner_node`, `make_remediate_node`
- `api/summary.py` — new, `build_summary_message`
- `api/main.py` — `POST /diagnose` and `POST /approve/{thread_id}` converted to SSE
  (`StreamingResponse`, `astream(..., stream_mode=["custom", "values"])`); `/approve` updates the
  pending message in place instead of inserting a new one
- `test/unit_test/api/summary_test.py` — new, unit tests for `build_summary_message`
- `ui/lib/api.ts` — `diagnose()`/`approve()` replaced with `streamDiagnose()`/`streamApprove()`
- `ui/components/ProgressTimeline.tsx` — new
- `ui/components/ChatView.tsx`, `ui/components/PlanCard.tsx` — updated to use the streaming API
  functions and render `ProgressTimeline` during an in-flight call
