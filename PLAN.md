# Persistent checkpointing (Postgres) + live progress streaming

## Part 1 — Swap `MemorySaver` for `PostgresSaver`

### Problem

`graph/builder.py`'s `build_graph()` hardcodes `graph.compile(checkpointer=MemorySaver())`. `MemorySaver`
is in-process and in-memory: state is lost on restart, and with more than one `uvicorn` worker
(or more than one pod), a thread created by worker A is invisible to worker B — `/approve`
hitting a different worker than `/diagnose` did would 404 on a thread that actually exists.

### Design

- Add `langgraph-checkpoint-postgres` and its async driver `psycopg[binary,pool]` to
  `requirements.txt` (the base `langgraph-checkpoint==4.1.1` already installed only provides the
  in-memory/interface layer, not the Postgres backend).
- `build_graph()` accepts an optional `checkpointer` instead of always constructing its own:

  ```python
  from langgraph.checkpoint.base import BaseCheckpointSaver
  from langgraph.checkpoint.memory import MemorySaver

  async def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
      ...
      return graph.compile(checkpointer=checkpointer or MemorySaver())
  ```

  This keeps `graph/builder.py`'s own CLI `main()` (`build_graph()`, no args) working exactly as
  today — ephemeral, no Postgres dependency for local ad-hoc runs. The API is the only caller
  that opts into persistence.

- `api/main.py`'s `lifespan` opens the Postgres connection for the app's full lifetime and runs
  the one-time table setup:

  ```python
  from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
  import os

  DATABASE_URL = os.getenv("DATABASE_URL")

  @asynccontextmanager
  async def lifespan(app: FastAPI):
      async with AsyncPostgresSaver.from_conn_string(DATABASE_URL) as checkpointer:
          await checkpointer.setup()
          app.state.graph = await build_graph(checkpointer=checkpointer)
          yield
  ```

  The `async with` spans the `yield`, so the connection pool stays open for every request and
  closes cleanly on shutdown — same lifecycle shape the `lifespan` already has today, just with
  Postgres opened around it. `checkpointer.setup()` is idempotent (creates tables if missing) so
  it's safe to call on every startup.

- New env var: `DATABASE_URL` (e.g. `postgresql://user:pass@host:5432/kubernaut`), loaded the
  same way `OPENROUTER_API_KEY` already is (`os.getenv` + `python-dotenv`).

### Not in scope here

- Migration tooling / schema versioning beyond what `checkpointer.setup()` handles itself.
- Provisioning the actual Postgres instance (docker-compose, managed DB, etc.) — assumed to
  already exist or be started separately; this plan only wires the app to talk to it.

---

## Part 2 — Streaming live progress to the UI

### Problem

`/diagnose` and `/approve/{thread_id}` currently `await graph.ainvoke(...)` and return once the
*entire* run finishes — which can be dozens of LLM calls and tool calls across three agents. The
UI has no visibility into "what's happening right now" (which agent is running, which tool it
just called, whether it's planning vs. executing) until the whole thing completes.

### Key architectural constraint (read this before picking an approach)

Each agent (`DiagnosisAgent`, `PlannerAgent`, `RemediationAgent`) is its **own separately
compiled `StateGraph`**, invoked from an orchestrator node via a plain `await agent.ainvoke(state)`
call (see `make_diagnose_node` etc. in `graph/nodes.py`) — the inner agent is *not* registered as
a LangGraph subgraph node (`graph.add_node("x", other_compiled_graph)`). That distinction matters:

- LangGraph's own nested-graph streaming (`graph.astream(..., subgraphs=True)`) only sees inner
  steps when a compiled graph is added *directly* as a node. Our node functions are opaque
  wrappers around a separate `.ainvoke()` call, so `subgraphs=True` on the outer graph will **not**
  surface `PlannerAgent`'s internal tool calls — it only sees "the `planner_agent` node started"
  and "...finished." Restructuring to true subgraph nodes would require unifying each agent's
  state schema with `OrchestratorState`'s (they currently share only some keys), which is a much
  larger refactor than this plan's scope.
- **LangChain's `astream_events()` (v2) is the better fit.** It's runnable-based, not
  graph-node-based — it surfaces `on_chain_start/end`, `on_tool_start/end`, `on_llm_start/end`
  from *any* nested `Runnable` call, regardless of whether the outer caller is a LangGraph graph
  or a plain coroutine. Since each agent's `self.llm.invoke(...)` and `self._tool_executor.invoke(...)`
  are themselves `Runnable`s, this should surface exactly the granularity wanted (agent-level
  *and* tool-level) without restructuring the graph topology.

**Open risk, needs a spike before committing to this design**: whether callback/event
propagation reaches three levels deep (outer `graph.astream_events()` → node wrapper's
`agent.ainvoke(state)` → agent's own `self.llm.invoke(...)`/`tool_executor.invoke(...)`)
*automatically* via LangChain's context-var-based propagation, or whether `config` (which
carries callbacks) needs to be threaded through explicitly at each level. If automatic
propagation doesn't reach that deep, the fallback is mechanical: pass `config` into every node
wrapper (`make_diagnose_node`, `make_planner_node`, `make_remediate_node` in `graph/nodes.py`)
and forward it into each agent's internal `self.llm.invoke(messages, config=config)` /
`self._tool_executor.invoke(state, config=config)` calls, plus the `Classifier`/`PlanClassifier`/
`DiffEvaluator` structured-output calls — roughly 8-10 call sites across
`agents/diagnosis_agent.py`, `agents/planner_agent.py`, `agents/remediation_agent.py`,
`agents/classifier.py`, `agents/plan_classifier.py`. Bounded, mechanical, but real work — spike
first to confirm whether it's actually needed before doing it everywhere.

### Design

- **Transport: Server-Sent Events (SSE), not WebSockets.** The UI only needs one-way
  server→client push; the human-approval decision already goes over a separate `POST
  /approve/{thread_id}` call, so there's no need for a bidirectional channel. SSE is plain HTTP
  (`StreamingResponse(media_type="text/event-stream")`), simpler than WebSockets, and
  `EventSource` gives the browser automatic reconnect for free.

- **`/diagnose` and `/approve/{thread_id}` become streaming endpoints.** This is a breaking
  change to the response contract of the endpoints built earlier this session (JSON body →
  event stream) — confirm before implementing, since any UI code already written against the
  current JSON responses would need to switch to consuming an event stream instead.

  ```python
  @app.post("/diagnose")
  async def start_diagnosis(body: DiagnoseRequest):
      thread_id = str(uuid.uuid4())
      config = {"configurable": {"thread_id": thread_id}}

      async def event_stream():
          yield sse({"type": "thread_started", "thread_id": thread_id})
          async for event in app.state.graph.astream_events(
              {"query": body.query, "repo_url": body.repo_url}, config=config, version="v2"
          ):
              normalized = normalize_event(event)
              if normalized:
                  yield sse(normalized)

      return StreamingResponse(event_stream(), media_type="text/event-stream")
  ```

  `/approve/{thread_id}` follows the same shape, streaming `astream_events()` over
  `Command(resume={...})` instead of the initial input.

- **`normalize_event(event)`** maps LangChain's raw event dicts (`event["event"]`,
  `event["name"]`, `event["data"]`, `event["tags"]`/`event["metadata"]` for identifying which
  node/agent it came from) into a small, stable, UI-facing shape — don't leak LangChain's
  internal event schema directly to the frontend:

  ```json
  {"type": "node_start", "node": "diagnosis_agent"}
  {"type": "tool_call", "node": "diagnosis_agent", "tool": "list_resources", "args": {...}}
  {"type": "tool_result", "node": "diagnosis_agent", "tool": "list_resources", "preview": "..."}
  {"type": "node_end", "node": "diagnosis_agent"}
  {"type": "interrupt", "diagnosis": {...}, "plan": {...}}
  {"type": "node_start", "node": "remediation_agent"}
  {"type": "complete", "result": {...}}
  ```

  `node` values line up with the orchestrator's own node names (`diagnosis_agent`,
  `planner_agent`, `human_approval_node`, `remediation_agent`) — the UI's "what agent is running"
  display and "what tool is it calling" display come from the same stream, just different event
  `type`s.

- **`/status/{thread_id}` is unaffected** — stays as a simple point-in-time REST poll (already
  built), useful for a client that reconnects after missing part of the stream, or that just
  wants a snapshot without holding a live connection open.

### Implementation sketch (in order)

1. Spike: confirm whether `astream_events()` on the outer graph surfaces inner-agent tool calls
   without explicit `config` forwarding. Decides whether step 2 is needed.
2. (If needed) Thread `config` through the ~8-10 call sites listed above.
3. Write `normalize_event()` and an `sse()` helper (`f"data: {json.dumps(payload)}\n\n"`).
4. Convert `/diagnose` to a `StreamingResponse` per the shape above.
5. Convert `/approve/{thread_id}` the same way (still 404s on unknown `thread_id` *before*
   opening the stream, same as today).
6. Manual test: run a diagnosis end-to-end against a real query, confirm the UI-facing event
   sequence matches what the terminal `self.logger.info(...)` trace already shows today.

### Not in scope here

- Token-level LLM output streaming (`on_llm_new_token`) — the ask is agent/tool-level progress,
  not live text generation; can be added later using the same `astream_events()` pipe if wanted.
- Replaying history for a client that connects *mid-run* (e.g. a second browser tab) — first
  version assumes one client streams from the start of a given `/diagnose` or `/approve` call.

---

## Priority

1. **Postgres checkpointer** — small, well-scoped, no open design questions, fixes a real
   multi-worker correctness gap.
2. **Streaming spike** — do this before committing to the full streaming implementation; it
   determines whether the design above needs the config-forwarding step or not.
3. **Streaming implementation** — depends on confirming the breaking response-contract change is
   acceptable, and on the spike's outcome.

Not yet implemented — this is the plan for review before touching `graph/builder.py`,
`api/main.py`, `agents/*.py`, or `requirements.txt`.
