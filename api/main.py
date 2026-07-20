from contextlib import asynccontextmanager
from uuid import UUID
import os
import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db_session, init_models
from api.orm import Chat, Message, MessageRole
from api.schemas import ApprovalDecision, ChatCreateRequest, DiagnoseRequest
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

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    result = await app.state.graph.ainvoke(
        {"query": body.query, "repo_url": chat.repo_url}, config=config
    )

    if "__interrupt__" in result:
        interrupt_payload = result["__interrupt__"][0].value
        diagnosis = interrupt_payload["diagnosis"]
        plan = interrupt_payload["plan"]
        summary = f"{diagnosis.summary}\n\nProposed plan: {plan.summary}\n\n(Awaiting your approval)"
        db.add(Message(chat_id=chat.id, role=MessageRole.ASSISTANT, content=summary, thread_id=thread_id))
        await db.commit()
        return {
            "status": "pending_approval",
            "thread_id": thread_id,
            "chat_id": str(chat.id),
            "plan": plan,
        }

    diagnosis_result = result.get("diagnosis_result")
    answer = diagnosis_result.summary if diagnosis_result else "(no diagnosis result)"
    db.add(Message(chat_id=chat.id, role=MessageRole.ASSISTANT, content=answer, thread_id=thread_id))
    await db.commit()

    return {"status": "complete", "thread_id": thread_id, "chat_id": str(chat.id), "result": result}


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
