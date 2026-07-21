# Streaming Progress and Summarized Chat Messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream live phase/status progress from the LangGraph orchestrator to the chat UI during `/diagnose` and `/approve`, and persist exactly one always-current summary message per conversational turn instead of a stale-plus-appended pair.

**Architecture:** LangGraph's `get_stream_writer()` custom stream mode lets orchestrator node functions emit phase progress without touching sub-agent internals; `POST /diagnose` and `POST /approve/{thread_id}` switch from `ainvoke()` to iterating `astream(..., stream_mode=["custom", "values"])` and forward each `"custom"` chunk to the client as a `text/event-stream` (SSE) event, using the final `"values"` chunk exactly as `ainvoke()`'s old return value. A shared `build_summary_message` helper produces the persisted message text; `/approve` updates the existing pending message in place instead of inserting a new row.

**Tech Stack:** FastAPI `StreamingResponse`, LangGraph `get_stream_writer`/`astream(stream_mode=[...])`, native `fetch` + `ReadableStream` on the frontend (no new dependency).

## Global Constraints

- Progress is phase/status-level only (`diagnosis`, `planner`, `remediation`), never token-by-token LLM text streaming.
- No live interactive Approve/Reject card reconstruction from chat history after a reload — unaffected by this plan, unchanged from the existing behavior.
- No resilience to a dropped client connection mid-run — an accepted, documented limitation, not solved in this plan.
- No new automated tests for the SSE wire format or the frontend streaming UI (no test infra exists for either layer today). `build_summary_message` is pure logic and gets unit tests under `test/unit_test/api/`.
- Exact progress event `message` text per phase (implementer must match verbatim):

  | Phase | `started` message | `completed` message |
  |---|---|---|
  | `diagnosis` | `"Investigating the cluster…"` | `diagnosis_result.summary` |
  | `planner` | `"Building a remediation plan…"` | `plan.summary` if `plan.planning_success` else `"Could not produce a safe plan."` |
  | `remediation` | `"Applying the fix…"` | `"Fix applied."` if `eval_passed` else `"Fix did not pass evaluation."` |

- SSE terminal event types: `{"type": "final", ...}` (mirrors the old JSON response shape) and `{"type": "error", "message": "..."}` on failure — never a bare stream close.
- `get_stream_writer()` requires Python >= 3.11 for async contextvar propagation — the repo venv is verified at 3.11.2, no action needed, just don't downgrade.
- Requires `DATABASE_URL` pointing at a real (throwaway is fine) Postgres for backend task verification — same convention as the prior chat-UI plan.

---

### Task 1: `build_summary_message` helper

**Files:**
- Create: `api/summary.py`
- Test: `test/unit_test/api/summary_test.py`

**Interfaces:**
- Produces: `build_summary_message(diagnosis: DiagnosisResult, plan: RemediationPlan | None = None, approved: bool | None = None, pr_url: str | None = None) -> str` — used by Tasks 3 and 4.

- [ ] **Step 1: Write the failing test**

Create `test/unit_test/api/summary_test.py`:

```python
from api.summary import build_summary_message
from models import DiagnosisResult, RemediationPlan, RemediationStep


def _diagnosis(requires_remediation=False, root_cause=None):
    return DiagnosisResult(
        summary="Investigated the dev namespace and found 3 deployments running normally.",
        root_cause=root_cause,
        requires_remediation=requires_remediation,
        diagnosis_success=True,
    )


def _plan(planning_success=True):
    return RemediationPlan(
        summary="Add a missing CPU request to the backend deployment",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="apps/backend/deployment.yaml",
                description="Add a CPU request so the HPA can compute utilization",
                new_content="requests:\n    cpu: 250m",
            ),
        ],
        planning_success=planning_success,
    )


def test_no_remediation_case():
    diagnosis = _diagnosis()

    message = build_summary_message(diagnosis)

    assert message == "Investigated the dev namespace and found 3 deployments running normally."


def test_includes_root_cause_when_present():
    diagnosis = _diagnosis(requires_remediation=True, root_cause="Missing CPU request causes HPA to stall")

    message = build_summary_message(diagnosis)

    assert "Root cause: Missing CPU request causes HPA to stall" in message


def test_pending_approval_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = _plan()

    message = build_summary_message(diagnosis, plan, approved=None)

    assert "Proposed fix: Add a missing CPU request to the backend deployment" in message
    assert "(Awaiting your approval)" in message


def test_approved_with_pr_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = _plan()

    message = build_summary_message(diagnosis, plan, approved=True, pr_url="https://github.com/org/repo/pull/1")

    assert "Approved — PR opened: https://github.com/org/repo/pull/1" in message
    assert "Awaiting" not in message


def test_approved_without_pr_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = _plan()

    message = build_summary_message(diagnosis, plan, approved=True, pr_url=None)

    assert "Approved, but no PR was opened." in message


def test_rejected_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = _plan()

    message = build_summary_message(diagnosis, plan, approved=False)

    assert "Not approved — no changes were made." in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest test/unit_test/api/summary_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.summary'`

- [ ] **Step 3: Write the implementation**

Create `api/summary.py`:

```python
from models import DiagnosisResult, RemediationPlan


def build_summary_message(
    diagnosis: DiagnosisResult,
    plan: RemediationPlan | None = None,
    approved: bool | None = None,
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
            if pr_url:
                lines.append(f"\nApproved — PR opened: {pr_url}")
            else:
                lines.append("\nApproved, but no PR was opened.")
        else:
            lines.append("\nNot approved — no changes were made.")

    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest test/unit_test/api/summary_test.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add api/summary.py test/unit_test/api/summary_test.py
git commit -m "Add build_summary_message helper for single-message turn summaries"
```

---

### Task 2: Progress event instrumentation in `graph/nodes.py`

**Files:**
- Modify: `graph/nodes.py` (full file, ~83 lines)

**Interfaces:**
- Consumes: nothing new.
- Produces: custom stream-mode events of shape `{"phase": "diagnosis" | "planner" | "remediation", "status": "started" | "completed", "message": str}`, emitted via `get_stream_writer()` from `make_diagnose_node`, `make_planner_node`, `make_remediate_node`. Task 3/4 forward these verbatim over SSE.

- [ ] **Step 1: Add progress instrumentation**

Replace the full contents of `graph/nodes.py`:

```python
from typing import Literal

from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from agents import DiagnosisAgent, PlannerAgent, RemediationAgent
from graph.state import OrchestratorState


def make_diagnose_node(diagnosis_agent: DiagnosisAgent):
    async def diagnose_node(state: OrchestratorState) -> dict:
        writer = get_stream_writer()
        writer({"phase": "diagnosis", "status": "started", "message": "Investigating the cluster…"})
        result = await diagnosis_agent.ainvoke({
            "messages": [],
            "query": state["query"],
            "iteration_count": 0,
        })
        diagnosis_result = result["diagnosis_result"]
        writer({"phase": "diagnosis", "status": "completed", "message": diagnosis_result.summary})
        return {"diagnosis_result": diagnosis_result}

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
        writer = get_stream_writer()
        writer({"phase": "planner", "status": "started", "message": "Building a remediation plan…"})
        result = await planner_agent.ainvoke({
            "messages": [],
            "diagnosis_result": state["diagnosis_result"],
            "iteration_count": 0,
            "repo_url": state["repo_url"],
            "bare_path": "",
            "repo_path": "",
            "branch": "",
        })
        plan = result["plan"]
        completed_message = plan.summary if plan.planning_success else "Could not produce a safe plan."
        writer({"phase": "planner", "status": "completed", "message": completed_message})
        return {"plan": plan}

    return planner_node


def human_approval_node(state: OrchestratorState) -> dict:
    decision = interrupt({
        "diagnosis": state["diagnosis_result"],
        "plan": state["plan"],
    })
    update = {"approved": decision.get("approved", False)}
    edited_plan = decision.get("edited_plan")
    if edited_plan is not None:
        update["plan"] = edited_plan
    return update


def approval_routing(state: OrchestratorState) -> Literal["remediation_agent", "end"]:
    return "remediation_agent" if state["approved"] else "end"


def make_remediate_node(remediation_agent: RemediationAgent):
    async def remediate_node(state: OrchestratorState) -> dict:
        writer = get_stream_writer()
        writer({"phase": "remediation", "status": "started", "message": "Applying the fix…"})
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
        eval_passed = result.get("eval_passed", False)
        completed_message = "Fix applied." if eval_passed else "Fix did not pass evaluation."
        writer({"phase": "remediation", "status": "completed", "message": completed_message})
        return {
            "pr_url": result.get("pr_url", ""),
            "eval_passed": eval_passed,
            "eval_reasoning": result.get("eval_reasoning", ""),
        }

    return remediate_node
```

- [ ] **Step 2: Verify with a fake-agent script**

`get_stream_writer()` is a no-op unless the graph is invoked with `stream_mode` including `"custom"`, so this instrumentation is safe by construction — but confirm the exact event shapes and ordering with a real `StateGraph` wired to a fake agent (no real LLM/cluster/DB needed):

```bash
cd /Users/brymat24/repos/Kubernaut
python3 - <<'PYEOF'
import asyncio
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from graph.nodes import make_diagnose_node
from graph.state import OrchestratorState
from models import DiagnosisResult


class FakeDiagnosisAgent:
    async def ainvoke(self, _input):
        return {"diagnosis_result": DiagnosisResult(
            summary="No issues found in namespace dev",
            root_cause=None,
            requires_remediation=False,
            diagnosis_success=True,
        )}


async def main():
    g = StateGraph(OrchestratorState)
    g.add_node("diagnosis_agent", make_diagnose_node(FakeDiagnosisAgent()))
    g.add_edge(START, "diagnosis_agent")
    g.add_edge("diagnosis_agent", END)
    graph = g.compile(checkpointer=MemorySaver())

    events = []
    config = {"configurable": {"thread_id": "verify-task-2"}}
    async for mode, chunk in graph.astream(
        {"query": "how many deployments", "repo_url": ""}, config=config, stream_mode=["custom", "values"]
    ):
        if mode == "custom":
            events.append(chunk)

    expected = [
        {"phase": "diagnosis", "status": "started", "message": "Investigating the cluster…"},
        {"phase": "diagnosis", "status": "completed", "message": "No issues found in namespace dev"},
    ]
    assert events == expected, events
    print("OK:", events)


asyncio.run(main())
PYEOF
```

Expected: `OK: [{'phase': 'diagnosis', 'status': 'started', ...}, {'phase': 'diagnosis', 'status': 'completed', ...}]`

- [ ] **Step 3: Commit**

```bash
git add graph/nodes.py
git commit -m "Emit phase progress events from orchestrator nodes via get_stream_writer"
```

---

### Task 3: Convert `POST /diagnose` to SSE

**Files:**
- Modify: `api/main.py` (full file)

**Interfaces:**
- Consumes: Task 1's `build_summary_message`; Task 2's custom progress events (forwarded verbatim).
- Produces: `_sse_event(payload: dict) -> str`, `_json_default`, `SSE_HEADERS` helpers in `api/main.py`, reused unchanged by Task 4. `POST /diagnose` now returns `text/event-stream`: zero or more `{"type": "progress", "phase", "status", "message"}` events, then exactly one terminal `{"type": "final", "status": "complete" | "pending_approval", ...}` or `{"type": "error", "message"}` event.

- [ ] **Step 1: Implement the SSE conversion**

Replace the full contents of `api/main.py`:

```python
from contextlib import asynccontextmanager
from uuid import UUID
import json
import os
import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import AsyncSessionLocal, get_db_session, init_models
from api.orm import Chat, Message, MessageRole
from api.schemas import ApprovalDecision, ChatCreateRequest, DiagnoseRequest
from api.summary import build_summary_message
from graph.builder import build_graph
from models import DiagnosisResult, RemediationPlan
from models.eval_result import EvalResult
from models.scenario_eval_result import ScenarioEvalResult

DATABASE_URL = os.getenv("DATABASE_URL")

CHECKPOINT_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[DiagnosisResult, RemediationPlan, EvalResult, ScenarioEvalResult]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    async with AsyncPostgresSaver.from_conn_string(DATABASE_URL, serde=CHECKPOINT_SERDE) as checkpointer:
        await checkpointer.setup()
        app.state.graph = await build_graph(checkpointer=checkpointer)
        yield


app = FastAPI(title="Kubernaut API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _json_default(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=_json_default)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.post("/chats")
async def create_chat(body: ChatCreateRequest, db: AsyncSession = Depends(get_db_session)):
    chat = Chat(title=body.title, repo_url=body.repo_url)
    db.add(chat)
    await db.commit()
    await db.refresh(chat)

    return {
        "id": str(chat.id),
        "title": chat.title,
        "repo_url": chat.repo_url,
        "created_at": chat.created_at.isoformat(),
    }


@app.post("/diagnose")
async def start_diagnosis(body: DiagnoseRequest, db: AsyncSession = Depends(get_db_session)):
    chat = await db.get(Chat, body.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=f"unknown chat_id: {body.chat_id}")

    db.add(Message(chat_id=chat.id, role=MessageRole.USER, content=body.query))
    await db.commit()

    thread_id = str(uuid.uuid4())
    chat_id = chat.id
    repo_url = chat.repo_url
    query = body.query
    graph = app.state.graph

    async def event_generator():
        config = {"configurable": {"thread_id": thread_id}}
        final_values: dict = {}
        try:
            async for mode, chunk in graph.astream(
                {"query": query, "repo_url": repo_url}, config=config, stream_mode=["custom", "values"]
            ):
                if mode == "custom":
                    yield _sse_event({"type": "progress", **chunk})
                else:
                    final_values = chunk
        except Exception as exc:
            yield _sse_event({"type": "error", "message": str(exc)})
            return

        # A fresh session, not the request-scoped `db`: this generator runs after the
        # endpoint function has already returned the StreamingResponse, potentially
        # minutes later — the injected dependency's lifetime shouldn't be relied on here.
        async with AsyncSessionLocal() as gen_db:
            if "__interrupt__" in final_values:
                interrupt_payload = final_values["__interrupt__"][0].value
                diagnosis = interrupt_payload["diagnosis"]
                plan = interrupt_payload["plan"]
                summary = build_summary_message(diagnosis, plan, approved=None)
                gen_db.add(Message(chat_id=chat_id, role=MessageRole.ASSISTANT, content=summary, thread_id=thread_id))
                await gen_db.commit()
                yield _sse_event({
                    "type": "final",
                    "status": "pending_approval",
                    "thread_id": thread_id,
                    "chat_id": str(chat_id),
                    "plan": plan,
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
            })

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/approve/{thread_id}")
async def approve(thread_id: str, decision: ApprovalDecision, db: AsyncSession = Depends(get_db_session)):
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
    chat_id = pending_message.chat_id if pending_message else None

    result = await graph.ainvoke(
        Command(resume={"approved": decision.approved, "edited_plan": decision.edited_plan}),
        config=config,
    )

    if "__interrupt__" in result:
        # e.g. planner_agent hits another interrupt downstream, or a second approval gate
        payload = result["__interrupt__"][0].value
        if chat_id is not None:
            db.add(Message(
                chat_id=chat_id,
                role=MessageRole.ASSISTANT,
                content="Another approval is required.",
                thread_id=thread_id,
            ))
            await db.commit()
        return {"status": "pending_approval", "thread_id": thread_id, "payload": payload}

    if chat_id is not None:
        if decision.approved:
            pr_url = result.get("pr_url")
            content = f"Opened PR: {pr_url}" if pr_url else "Approved, but no PR was opened — see eval_reasoning."
        else:
            content = "Remediation was not approved."
        db.add(Message(chat_id=chat_id, role=MessageRole.ASSISTANT, content=content, thread_id=thread_id))
        await db.commit()

    return {"status": "complete", "thread_id": thread_id, "result": result}


@app.get("/chats/{chat_id}/messages")
async def get_chat_messages(chat_id: UUID, db: AsyncSession = Depends(get_db_session)):
    chat = await db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail=f"unknown chat_id: {chat_id}")

    result = await db.execute(select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at))
    messages = result.scalars().all()

    return [
        {
            "id": str(m.id),
            "role": m.role.value,
            "content": m.content,
            "thread_id": m.thread_id,
            "created_at": m.created_at.isoformat(),
        }
        for m in messages
    ]


@app.get("/chats")
async def get_chats(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Chat).order_by(Chat.created_at.desc()))
    chats = result.scalars().all()

    return [
        {
            "id": str(c.id),
            "title": c.title,
            "repo_url": c.repo_url,
            "created_at": c.created_at.isoformat(),
        }
        for c in chats
    ]
```

Note: `/approve/{thread_id}` is untouched in this task (still `ainvoke`-based) — Task 4 converts it.

- [ ] **Step 2: Verify against a real throwaway Postgres with a fake graph**

Requires `DATABASE_URL` set to a real reachable Postgres (tables must already exist — run the app once via `uvicorn api.main:app` first if this is a brand new database, or rely on the existing `chats`/`messages` tables from prior work). This script never touches a real cluster or LLM — it swaps `app.state.graph` for a fake before making requests, so the FastAPI `lifespan` (which builds the real graph) is deliberately never triggered.

```bash
cd /Users/brymat24/repos/Kubernaut
python3 - <<'PYEOF'
import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

import api.main as main_module
from api.database import AsyncSessionLocal
from api.orm import Chat, Message
from models import DiagnosisResult


class FakeGraph:
    def __init__(self, custom_events=None, final_values=None):
        self._custom_events = custom_events or []
        self._final_values = final_values or {}

    async def astream(self, *_args, **_kwargs):
        for event in self._custom_events:
            yield ("custom", event)
        yield ("values", self._final_values)


diagnosis = DiagnosisResult(
    summary="Found 3 deployments in dev, all healthy.",
    root_cause=None,
    requires_remediation=False,
    diagnosis_success=True,
)


async def seed_chat():
    async with AsyncSessionLocal() as db:
        chat = Chat(title="verify-task-3", repo_url="https://github.com/example/repo")
        db.add(chat)
        await db.commit()
        await db.refresh(chat)
        return chat.id


chat_id = asyncio.run(seed_chat())

main_module.app.state.graph = FakeGraph(
    custom_events=[
        {"phase": "diagnosis", "status": "started", "message": "Investigating the cluster…"},
        {"phase": "diagnosis", "status": "completed", "message": diagnosis.summary},
    ],
    final_values={"diagnosis_result": diagnosis},
)

client = TestClient(main_module.app)
response = client.post("/diagnose", json={"query": "how many deployments", "chat_id": str(chat_id)})
assert response.status_code == 200, response.text
assert response.headers["content-type"].startswith("text/event-stream"), response.headers["content-type"]

lines = [line for line in response.text.split("\n\n") if line.strip()]
events = [json.loads(line[len("data: "):]) for line in lines]

assert events[0] == {"type": "progress", "phase": "diagnosis", "status": "started", "message": "Investigating the cluster…"}, events
assert events[1] == {"type": "progress", "phase": "diagnosis", "status": "completed", "message": diagnosis.summary}, events
assert events[2]["type"] == "final" and events[2]["status"] == "complete", events
print("Events OK:", events)


async def fetch_messages():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at))
        return result.scalars().all()


messages = asyncio.run(fetch_messages())
assert len(messages) == 2, messages
assert messages[0].role.value == "user" and messages[0].content == "how many deployments", messages[0].content
assert messages[1].role.value == "assistant" and messages[1].content == diagnosis.summary, messages[1].content
print("Persisted messages OK:", [(m.role.value, m.content) for m in messages])
PYEOF
```

Expected: `Events OK: [...]` then `Persisted messages OK: [('user', 'how many deployments'), ('assistant', 'Found 3 deployments in dev, all healthy.')]`

- [ ] **Step 3: Commit**

```bash
git add api/main.py
git commit -m "Convert POST /diagnose to a streaming SSE response"
```

---

### Task 4: Convert `POST /approve/{thread_id}` to SSE + update-in-place persistence

**Files:**
- Modify: `api/main.py` (full file)

**Interfaces:**
- Consumes: Task 1's `build_summary_message`; Task 2's custom progress events; Task 3's `_sse_event`/`_json_default`/`SSE_HEADERS` helpers (same file, unchanged).
- Produces: `POST /approve/{thread_id}` now returns `text/event-stream` with the same `progress`/`final`/`error` event shapes as `/diagnose`, and **updates** the chat's pending message row in place instead of inserting a new one.

- [ ] **Step 1: Implement the SSE conversion and in-place update**

In `api/main.py`, replace the `@app.post("/approve/{thread_id}")` handler (everything between it and the next `@app.get("/chats/{chat_id}/messages")` route) with:

```python
@app.post("/approve/{thread_id}")
async def approve(thread_id: str, decision: ApprovalDecision, db: AsyncSession = Depends(get_db_session)):
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
    approved = decision.approved
    edited_plan = decision.edited_plan

    async def event_generator():
        final_values: dict = {}
        try:
            async for mode, chunk in graph.astream(
                Command(resume={"approved": approved, "edited_plan": edited_plan}),
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
            # e.g. planner_agent hits another interrupt downstream, or a second approval gate
            payload = final_values["__interrupt__"][0].value
            if pending_message_id is not None:
                async with AsyncSessionLocal() as gen_db:
                    msg = await gen_db.get(Message, pending_message_id)
                    if msg is not None:
                        msg.content = "Another approval is required."
                        await gen_db.commit()
            yield _sse_event({
                "type": "final",
                "status": "pending_approval",
                "thread_id": thread_id,
                "payload": payload,
            })
            return

        if pending_message_id is not None:
            diagnosis = final_values.get("diagnosis_result")
            plan = final_values.get("plan")
            pr_url = final_values.get("pr_url")
            if diagnosis is not None:
                async with AsyncSessionLocal() as gen_db:
                    msg = await gen_db.get(Message, pending_message_id)
                    if msg is not None:
                        msg.content = build_summary_message(diagnosis, plan, approved=approved, pr_url=pr_url)
                        await gen_db.commit()

        yield _sse_event({
            "type": "final",
            "status": "complete",
            "thread_id": thread_id,
            "result": final_values,
        })

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=SSE_HEADERS)
```

- [ ] **Step 2: Verify against a real throwaway Postgres with a fake graph**

```bash
cd /Users/brymat24/repos/Kubernaut
python3 - <<'PYEOF'
import asyncio
import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

import api.main as main_module
from api.database import AsyncSessionLocal
from api.orm import Chat, Message, MessageRole
from models import DiagnosisResult, RemediationPlan, RemediationStep


class FakeState:
    def __init__(self, values):
        self.values = values


class FakeGraph:
    def __init__(self, state_values=None, custom_events=None, final_values=None):
        self._state_values = state_values or {}
        self._custom_events = custom_events or []
        self._final_values = final_values or {}

    async def aget_state(self, _config):
        return FakeState(self._state_values)

    async def astream(self, *_args, **_kwargs):
        for event in self._custom_events:
            yield ("custom", event)
        yield ("values", self._final_values)


diagnosis = DiagnosisResult(
    summary="Backend deployment is missing a CPU request, causing HPA to stall.",
    root_cause="Missing CPU request",
    requires_remediation=True,
    diagnosis_success=True,
)
plan = RemediationPlan(
    summary="Add a CPU request to the backend deployment",
    steps=[
        RemediationStep(
            step_number=1,
            file_path="apps/backend/deployment.yaml",
            description="Add CPU request",
            new_content="requests:\n  cpu: 250m",
        )
    ],
    planning_success=True,
)


async def seed():
    thread_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        chat = Chat(title="verify-task-4", repo_url="https://github.com/example/repo")
        db.add(chat)
        await db.commit()
        await db.refresh(chat)

        pending = Message(
            chat_id=chat.id,
            role=MessageRole.ASSISTANT,
            content="stale pending text",
            thread_id=thread_id,
        )
        db.add(pending)
        await db.commit()
        await db.refresh(pending)
        return chat.id, thread_id, pending.id


chat_id, thread_id, pending_id = asyncio.run(seed())

main_module.app.state.graph = FakeGraph(
    state_values={"diagnosis_result": diagnosis, "plan": plan},
    custom_events=[
        {"phase": "remediation", "status": "started", "message": "Applying the fix…"},
        {"phase": "remediation", "status": "completed", "message": "Fix applied."},
    ],
    final_values={
        "diagnosis_result": diagnosis,
        "plan": plan,
        "pr_url": "https://github.com/example/repo/pull/7",
        "eval_passed": True,
        "eval_reasoning": "Matches the plan.",
    },
)

client = TestClient(main_module.app)
response = client.post(f"/approve/{thread_id}", json={"approved": True})
assert response.status_code == 200, response.text
assert response.headers["content-type"].startswith("text/event-stream")

lines = [line for line in response.text.split("\n\n") if line.strip()]
events = [json.loads(line[len("data: "):]) for line in lines]
assert events[0]["type"] == "progress" and events[0]["phase"] == "remediation" and events[0]["status"] == "started", events
assert events[1]["type"] == "progress" and events[1]["status"] == "completed", events
assert events[2]["type"] == "final" and events[2]["status"] == "complete", events
print("Events OK:", events)


async def fetch_messages():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Message).where(Message.chat_id == chat_id))
        return result.scalars().all()


messages = asyncio.run(fetch_messages())
assert len(messages) == 1, messages  # updated in place, never appended
assert messages[0].id == pending_id, messages[0].id
assert "Approved — PR opened: https://github.com/example/repo/pull/7" in messages[0].content, messages[0].content
print("Message updated in place OK:", messages[0].content)
PYEOF
```

Expected: `Events OK: [...]` then `Message updated in place OK: ...Approved — PR opened: https://github.com/example/repo/pull/7`

- [ ] **Step 3: Run the full backend unit suite to confirm no regressions**

Run: `pytest -q`
Expected: all tests pass (91 passed: 85 pre-existing + 6 from Task 1)

- [ ] **Step 4: Commit**

```bash
git add api/main.py
git commit -m "Convert POST /approve to SSE and update the pending message in place"
```

---

### Task 5: Streaming client functions in `ui/lib/api.ts`

**Files:**
- Modify: `ui/lib/api.ts` (full file)

**Interfaces:**
- Consumes: backend SSE event shapes from Tasks 3/4 (`{"type": "progress", "phase", "status", "message"}`, `{"type": "final", ...}`, `{"type": "error", "message"}`).
- Produces: `ProgressEvent` type; `streamDiagnose(query: string, chatId: string, onProgress: (event: ProgressEvent) => void): Promise<DiagnoseResponse>`; `streamApprove(threadId: string, approved: boolean, onProgress: (event: ProgressEvent) => void): Promise<ApproveResponse>` — used by Tasks 6 and 7. The old `diagnose`/`approve` exports are removed (no other file imports them once Tasks 6/7 land).

- [ ] **Step 1: Implement the streaming client**

Replace the full contents of `ui/lib/api.ts`:

```ts
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:9000";

export interface ChatSummary {
  id: string;
  title: string;
  repo_url: string;
  created_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  thread_id: string | null;
  created_at: string;
}

export interface RemediationStep {
  step_number: number;
  file_path: string;
  description: string;
  new_content: string;
}

export interface RemediationPlan {
  summary: string;
  steps: RemediationStep[];
  planning_success: boolean;
}

export interface ProgressEvent {
  phase: "diagnosis" | "planner" | "remediation";
  status: "started" | "completed";
  message: string;
}

export interface DiagnoseResponse {
  status: "pending_approval" | "complete";
  thread_id: string;
  chat_id: string;
  plan?: RemediationPlan;
  result?: { diagnosis_result?: { summary: string }; pr_url?: string };
}

export interface ApproveResponse {
  status: "pending_approval" | "complete";
  thread_id: string;
  payload?: { diagnosis: unknown; plan: RemediationPlan };
  result?: { pr_url?: string; [key: string]: unknown };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }

  return response.json() as Promise<T>;
}

type StreamEvent<T> =
  | ({ type: "progress" } & ProgressEvent)
  | ({ type: "final" } & T)
  | { type: "error"; message: string };

async function streamRequest<T>(
  path: string,
  body: unknown,
  onProgress: (event: ProgressEvent) => void
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || response.statusText);
  }
  if (!response.body) {
    throw new ApiError(response.status, "No response body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload: T | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separatorIndex: number;
    while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);

      const line = rawEvent.startsWith("data: ") ? rawEvent.slice(6) : rawEvent;
      if (!line.trim()) continue;

      const event = JSON.parse(line) as StreamEvent<T>;

      if (event.type === "progress") {
        onProgress({ phase: event.phase, status: event.status, message: event.message });
      } else if (event.type === "error") {
        throw new ApiError(500, event.message);
      } else if (event.type === "final") {
        finalPayload = event;
      }
    }
  }

  if (finalPayload === null) {
    throw new ApiError(500, "Stream ended without a final result");
  }
  return finalPayload;
}

export function listChats(): Promise<ChatSummary[]> {
  return request<ChatSummary[]>("/chats");
}

export function createChat(title: string, repoUrl: string): Promise<ChatSummary> {
  return request<ChatSummary>("/chats", {
    method: "POST",
    body: JSON.stringify({ title, repo_url: repoUrl }),
  });
}

export function getChatMessages(chatId: string): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/chats/${chatId}/messages`);
}

export function streamDiagnose(
  query: string,
  chatId: string,
  onProgress: (event: ProgressEvent) => void
): Promise<DiagnoseResponse> {
  return streamRequest<DiagnoseResponse>("/diagnose", { query, chat_id: chatId }, onProgress);
}

export function streamApprove(
  threadId: string,
  approved: boolean,
  onProgress: (event: ProgressEvent) => void
): Promise<ApproveResponse> {
  return streamRequest<ApproveResponse>(`/approve/${threadId}`, { approved }, onProgress);
}
```

- [ ] **Step 2: Verify with build and lint**

Run: `cd /Users/brymat24/repos/Kubernaut/ui && npm run build`
Expected: `✓ Compiled successfully`, no TypeScript errors (note: `ChatView.tsx`/`PlanCard.tsx` still import the now-removed `diagnose`/`approve` until Tasks 6/7 land — if this task is done standalone, the build will fail on those two files' imports; that's expected and resolved by Task 6/7, not a regression to fix here. If dispatched via subagent-driven-development, note this in your report rather than treating it as a Task 5 defect.)

Run: `npm run lint`
Expected: no errors in `lib/api.ts` itself (unrelated pre-existing errors in files this task didn't touch, if any, are out of scope)

- [ ] **Step 3: Commit**

```bash
git add ui/lib/api.ts
git commit -m "Replace diagnose/approve with streaming SSE client functions"
```

---

### Task 6: `ProgressTimeline` component + `ChatView` integration

**Files:**
- Create: `ui/components/ProgressTimeline.tsx`
- Modify: `ui/components/ChatView.tsx` (full file)

**Interfaces:**
- Consumes: Task 5's `ProgressEvent` type and `streamDiagnose`.
- Produces: `ProgressTimeline({ events: ProgressEvent[] })` default export, reused unchanged by Task 7.

This task reuses the existing dark-console design tokens from `ui/app/globals.css` (`--accent`, `--muted`, `--foreground`) established in the original chat-UI build — no new tokens or visual system needed, this is a small addition following the established pattern, not a new design surface.

- [ ] **Step 1: Create the ProgressTimeline component**

Create `ui/components/ProgressTimeline.tsx`:

```tsx
"use client";

import { ProgressEvent } from "@/lib/api";

interface ProgressTimelineProps {
  events: ProgressEvent[];
}

const PHASE_LABELS: Record<ProgressEvent["phase"], string> = {
  diagnosis: "Diagnosis",
  planner: "Planning",
  remediation: "Remediation",
};

interface PhaseRow {
  phase: ProgressEvent["phase"];
  message: string;
  done: boolean;
}

function reduceToPhaseRows(events: ProgressEvent[]): PhaseRow[] {
  const order: ProgressEvent["phase"][] = [];
  const byPhase = new Map<ProgressEvent["phase"], PhaseRow>();

  for (const event of events) {
    if (!byPhase.has(event.phase)) {
      order.push(event.phase);
    }
    byPhase.set(event.phase, {
      phase: event.phase,
      message: event.message,
      done: event.status === "completed",
    });
  }

  return order.map((phase) => byPhase.get(phase)!);
}

export default function ProgressTimeline({ events }: ProgressTimelineProps) {
  const rows = reduceToPhaseRows(events);

  if (rows.length === 0) {
    return <p className="font-mono text-xs text-muted animate-pulse">kubernaut is thinking…</p>;
  }

  return (
    <ol aria-live="polite" className="space-y-1.5 font-mono text-xs">
      {rows.map((row) => (
        <li key={row.phase} className="flex items-start gap-2">
          <span aria-hidden="true" className={row.done ? "text-accent" : "text-accent animate-pulse"}>
            {row.done ? "✓" : "⋯"}
          </span>
          <span className="text-muted">
            <span className="text-foreground/70">{PHASE_LABELS[row.phase]}:</span> {row.message}
          </span>
        </li>
      ))}
    </ol>
  );
}
```

- [ ] **Step 2: Wire ChatView to the streaming API and ProgressTimeline**

Replace the full contents of `ui/components/ChatView.tsx`:

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
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
import ProgressTimeline from "@/components/ProgressTimeline";

export type TimelineItem =
  | { kind: "message"; id: string; message: ChatMessage }
  | { kind: "pending_plan"; id: string; threadId: string; plan: RemediationPlan };

interface ChatViewProps {
  chatId: string;
}

export default function ChatView({ chatId }: ChatViewProps) {
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setHistoryLoading(true);
      try {
        const messages = await getChatMessages(chatId);
        if (cancelled) return;
        setItems(messages.map((m) => ({ kind: "message", id: m.id, message: m })));
        setError(null);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          router.push("/");
          return;
        }
        setError(err instanceof ApiError ? err.message : "Failed to load chat");
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [chatId, router]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, progressEvents]);

  async function handleSend(query: string) {
    const localId = `local-${Date.now()}`;
    setItems((prev) => [
      ...prev,
      {
        kind: "message",
        id: localId,
        message: {
          id: localId,
          role: "user",
          content: query,
          thread_id: null,
          created_at: new Date().toISOString(),
        },
      },
    ]);
    setSending(true);
    setProgressEvents([]);
    setError(null);
    try {
      const response = await streamDiagnose(query, chatId, (event) => {
        setProgressEvents((prev) => [...prev, event]);
      });
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
      } else {
        const summary = response.result?.diagnosis_result?.summary ?? "(no diagnosis result)";
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
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to send message");
    } finally {
      setSending(false);
      setProgressEvents([]);
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border bg-panel/60 px-4 py-2.5">
        <span className="font-mono text-xs text-muted">session</span>
        <span className="truncate font-mono text-xs text-muted/70">{chatId}</span>
      </div>

      <div
        className="flex-1 overflow-y-auto px-4 py-5 space-y-5"
        aria-live="polite"
        aria-busy={sending}
      >
        {historyLoading && (
          <p className="font-mono text-xs text-muted animate-pulse">loading history…</p>
        )}

        {!historyLoading && items.length === 0 && !error && (
          <p className="text-sm text-muted">
            No messages yet. Ask a question about your cluster below to get started.
          </p>
        )}

        {items.map((item) =>
          item.kind === "message" ? (
            <MessageBubble key={item.id} message={item.message} />
          ) : (
            <PlanCard
              key={item.id}
              threadId={item.threadId}
              plan={item.plan}
              onResolved={(outcome) => {
                setItems((prev) =>
                  prev.map((i) =>
                    i.id === item.id
                      ? {
                          kind: "message",
                          id: item.id,
                          message: {
                            id: item.id,
                            role: "assistant",
                            content: outcome,
                            thread_id: item.threadId,
                            created_at: new Date().toISOString(),
                          },
                        }
                      : i
                  )
                );
              }}
            />
          )
        )}

        {sending && <ProgressTimeline events={progressEvents} />}

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        <div ref={bottomRef} />
      </div>

      <Composer onSend={handleSend} disabled={sending || historyLoading} />
    </div>
  );
}
```

- [ ] **Step 3: Verify**

Run: `cd /Users/brymat24/repos/Kubernaut/ui && npm run build && npm run lint`
Expected: both clean (this is the first task where `ChatView.tsx` no longer imports the removed `diagnose`, so the Task 5 build failure noted there is now resolved for this file — `PlanCard.tsx` still imports the removed `approve` until Task 7, so `npm run build` will still fail on `PlanCard.tsx` until Task 7 lands; note this in your report as expected, not a Task 6 defect).

If a live backend and browser are available in your environment, manually trigger a diagnosis and confirm the progress list appears and updates live in place of the old static "kubernaut is thinking…" text. If not available, verify by code review that the state transitions (`progressEvents` reset on send start/finish, appended via the `onProgress` callback, rendered only while `sending`) are wired correctly, and say so explicitly in your report.

- [ ] **Step 4: Commit**

```bash
git add ui/components/ProgressTimeline.tsx ui/components/ChatView.tsx
git commit -m "Add ProgressTimeline and wire ChatView to streaming diagnose"
```

---

### Task 7: `PlanCard` streaming integration

**Files:**
- Modify: `ui/components/PlanCard.tsx` (full file)

**Interfaces:**
- Consumes: Task 5's `ProgressEvent` type and `streamApprove`; Task 6's `ProgressTimeline` component.
- Produces: nothing new for later tasks — this is the last task in the plan.

- [ ] **Step 1: Wire PlanCard to the streaming API and ProgressTimeline**

Replace the full contents of `ui/components/PlanCard.tsx`:

```tsx
"use client";

import { useState } from "react";
import { ProgressEvent, RemediationPlan, streamApprove, ApiError } from "@/lib/api";
import ProgressTimeline from "@/components/ProgressTimeline";

interface PlanCardProps {
  threadId: string;
  plan: RemediationPlan;
  onResolved: (outcome: string) => void;
}

type PendingAction = "approve" | "reject" | null;

export default function PlanCard({ threadId, plan, onResolved }: PlanCardProps) {
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function handleDecision(approved: boolean) {
    setPendingAction(approved ? "approve" : "reject");
    setProgressEvents([]);
    setError(null);
    try {
      const response = await streamApprove(threadId, approved, (event) => {
        setProgressEvents((prev) => [...prev, event]);
      });
      if (response.status === "complete") {
        const prUrl = response.result?.pr_url ?? null;
        onResolved(
          approved
            ? prUrl
              ? `Opened PR: ${prUrl}`
              : "Approved, but no PR was opened."
            : "Remediation was not approved."
        );
      } else {
        onResolved("Another approval is required.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to submit decision");
      setPendingAction(null);
      setProgressEvents([]);
    }
  }

  const submitting = pendingAction !== null;

  return (
    <section
      aria-label="Remediation plan pending approval"
      aria-busy={submitting}
      className="rounded-md border border-amber-500/30 bg-amber-500/[0.08] px-4 py-3 text-sm text-amber-100"
    >
      <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wide text-amber-300/80">
        <span aria-hidden="true">»</span>
        <span>Awaiting approval</span>
      </div>

      <p className="mt-1.5 leading-relaxed text-amber-100">{plan.summary}</p>

      {plan.steps.length === 0 ? (
        <p className="mt-3 font-mono text-xs text-amber-300/70">
          No file changes are included in this plan.
        </p>
      ) : (
        <ol className="mt-3 space-y-2">
          {plan.steps.map((step) => (
            <li
              key={step.step_number}
              className="overflow-hidden rounded-md border border-border bg-background"
            >
              <div className="flex items-center gap-2 border-b border-border bg-panel px-3 py-2">
                <span
                  aria-hidden="true"
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-accent/40 font-mono text-[10px] text-accent"
                >
                  {step.step_number}
                </span>
                <span className="truncate font-mono text-xs text-foreground">{step.file_path}</span>
              </div>
              <p className="px-3 pt-2 text-xs leading-relaxed text-muted">{step.description}</p>
              <CodeBlock content={step.new_content} filePath={step.file_path} />
            </li>
          ))}
        </ol>
      )}

      {submitting && (
        <div className="mt-3 rounded-md border border-border bg-background px-3 py-2">
          <ProgressTimeline events={progressEvents} />
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => handleDecision(true)}
          disabled={submitting}
          aria-label="Approve remediation plan"
          className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-accent-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-accent/50"
        >
          {pendingAction === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          type="button"
          onClick={() => handleDecision(false)}
          disabled={submitting}
          aria-label="Reject remediation plan"
          className="rounded-md border border-border px-3 py-2 text-sm font-medium text-muted hover:border-danger/40 hover:text-danger disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-danger/40"
        >
          {pendingAction === "reject" ? "Rejecting…" : "Reject"}
        </button>
      </div>
    </section>
  );
}

/**
 * Renders a step's proposed file contents as a line-numbered code pane
 * (editor-style, not a bare <pre>) so a human reviewer can actually read
 * the diff they're about to approve without it dominating the timeline.
 * `display: contents` lets each line's number/code pair drop directly into
 * the parent grid, which is what produces the two-column layout without a
 * syntax-highlighting dependency.
 */
function CodeBlock({ content, filePath }: { content: string; filePath: string }) {
  const lines = content.length > 0 ? content.split("\n") : [""];

  return (
    <pre
      aria-label={`Proposed contents of ${filePath}`}
      className="m-3 mt-2 max-h-72 overflow-y-auto rounded-md border border-border bg-background p-0"
    >
      <code className="grid grid-cols-[auto_1fr] gap-x-3 px-3 py-2 font-mono text-[11px] leading-5 text-foreground/90">
        {lines.map((line, i) => (
          <span key={i} className="contents">
            <span aria-hidden="true" className="select-none text-right text-muted/40">
              {i + 1}
            </span>
            <span className="whitespace-pre-wrap break-all">{line.length > 0 ? line : " "}</span>
          </span>
        ))}
      </code>
    </pre>
  );
}
```

- [ ] **Step 2: Verify**

Run: `cd /Users/brymat24/repos/Kubernaut/ui && npm run build && npm run lint`
Expected: both clean — this is the last file referencing the removed `diagnose`/`approve` exports, so the build should now pass end-to-end with no leftover references anywhere in `/ui`.

If a live backend and browser are available, manually trigger a full remediation round-trip (diagnosis → plan → Approve) and confirm the progress list appears inside the plan card while the request is in flight, then resolves into the outcome message. If not available, verify by code review and say so explicitly in your report.

- [ ] **Step 3: Commit**

```bash
git add ui/components/PlanCard.tsx
git commit -m "Wire PlanCard to streaming approve and ProgressTimeline"
```
