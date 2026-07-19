from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from graph.builder import build_graph
import uuid

from api.schemas import ApprovalDecision, DiagnoseRequest
from models import DiagnosisResult, RemediationPlan
from models.eval_result import EvalResult
from models.scenario_eval_result import ScenarioEvalResult

DATABASE_URL = os.getenv("DATABASE_URL")

# Custom pydantic models stored in graph state must be explicitly allowlisted for msgpack
# deserialization — otherwise every checkpoint read logs a deprecation warning today and will
# raise outright in a future langgraph-checkpoint version. MemorySaver never needed this since
# it keeps live Python objects in memory instead of round-tripping through serialization.
CHECKPOINT_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[DiagnosisResult, RemediationPlan, EvalResult, ScenarioEvalResult]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncPostgresSaver.from_conn_string(DATABASE_URL, serde=CHECKPOINT_SERDE) as checkpointer:
        await checkpointer.setup()
        app.state.graph = await build_graph(checkpointer=checkpointer)
        yield


app = FastAPI(title="Kubernaut API", lifespan=lifespan)


@app.post("/diagnose")
async def start_diagnosis(body: DiagnoseRequest):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    result = await app.state.graph.ainvoke(
        {"query": body.query, "repo_url": body.repo_url}, config=config
    )

    if "__interrupt__" in result:
        interrupt_payload = result["__interrupt__"][0].value
        return {"status": "pending_approval", "thread_id": thread_id, "plan": interrupt_payload["plan"]}

    return {"status": "complete", "thread_id": thread_id, "result": result}


@app.post("/approve/{thread_id}")
async def approve(thread_id: str, decision: ApprovalDecision):
    config = {"configurable": {"thread_id": thread_id}}
    graph = app.state.graph

    state = await graph.aget_state(config)
    if not state.values:
        raise HTTPException(status_code=404, detail=f"unknown thread_id: {thread_id}")

    result = await graph.ainvoke(
        Command(resume={"approved": decision.approved, "edited_plan": decision.edited_plan}),
        config=config,
    )

    if "__interrupt__" in result:
        # e.g. planner_agent hits another interrupt downstream, or a second approval gate
        return {"status": "pending_approval", "thread_id": thread_id, "payload": result["__interrupt__"][0].value}

    return {"status": "complete", "thread_id": thread_id, "result": result}
