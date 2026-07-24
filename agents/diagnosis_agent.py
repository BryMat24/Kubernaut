import logging
from typing import Any, Literal

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from models import DiagnosisResult
from .helpers import (
    Classifier,
    DiagnosisEvaluator,
    HistoryCompactor,
    Hypothesizer,
    PlaybookLibrary,
    find_repeated_calls,
)
from graph.state import DiagnosisAgentState

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")

SCOPE_PROMPT = """
You are an experienced Site Reliability Engineer (SRE) performing the SCOPING phase of a
Kubernetes incident investigation.

Your goal is NOT to diagnose the root cause.
Your goal is ONLY to build situational awareness before hypothesis generation.

Collect enough information to answer these questions:

1. Which namespace and workload are affected?

2. What resources appear unhealthy?
   - Deployment
   - StatefulSet
   - Pod
   - Service

3. What is the current workload health?
   - Ready replicas
   - Pod phases
   - Restart counts
   - Failed rollouts
   - Service endpoint availability

4. Are there any notable recent Kubernetes events?

5. If the user's symptoms indicate an application issue (for example high latency,
5xx errors, crashes, or performance degradation), collect related lightweight
application signals from Prometheus and Loki, such as:
   - Error rate
   - Request latency (P95)
   - CPU saturation
   - OOMKilled indicator
   - Error logs from Loki

6. Classify the incident into one or more broad symptom domains:
   - Application failures
   - Resource pressure
   - Scheduling
   - Networking
   - Configuration
   - Storage
   - Unknown

Rules

- Stay broad.
- Do NOT diagnose the root cause.
- Do NOT investigate a specific hypothesis.
- Prefer Kubernetes discovery first.
- Use Prometheus only when it provides a quick high-level health signal.
- Use Loki only when Kubernetes state and metrics are insufficient.
- Never deep-dive logs or stack traces.
- Do NOT repeatedly inspect the same resource.
- Stop as soon as you have enough information for another engineer to begin a
  focused investigation.

When finished, return ONLY a concise scene summary (3–6 sentences) containing:

- affected namespace/workload
- observed symptoms
- overall workload health
- notable Kubernetes, Prometheus, and/or Loki signals
- likely investigation domains

Do NOT suggest a root cause.
Do NOT recommend a fix.

User query:
{query}
"""

INVESTIGATE_PROMPT = """
You are an SRE gathering evidence for a specific hypothesis, following a playbook.

user query:
{query}

scope summary:
{scope_summary}

current hypothesis:
{hypothesis}

hypotheses already ruled out (do not re-investigate these):
{ruled_out}

playbook to follow:
{playbook}

Work the playbook's checklist to confirm or reject the hypothesis. Reuse evidence already in the
conversation instead of re-fetching it. When the checklist's conclusion criteria are met (or you
can already reject the hypothesis), stop calling tools and state your finding in plain text.
"""


class DiagnosisAgent:
    def __init__(
        self,
        llm: BaseChatModel,
        investigate_tools: list[BaseTool],
        scope_tools: list[BaseTool],
        utility_llm: BaseChatModel | None = None,
    ) -> None:
        self.logger = logging.getLogger("diagnosisAgent")
        self.MAX_SCOPE_CALLS = 8
        self.MAX_INVESTIGATE_ITERATIONS = 8
        self.MAX_HYPOTHESES = 3

        self.scope_tool_executor = ToolNode(scope_tools)
        self.tool_executor = ToolNode(investigate_tools)

        self.llm = llm.bind_tools(investigate_tools)            # main model, used only in investigate
        self.scope_llm = utility_llm.bind_tools(scope_tools)    # cheap model, used only in scope
        self.playbooks = PlaybookLibrary()
        self.hypothesizer = Hypothesizer(utility_llm)
        self.evaluator = DiagnosisEvaluator(utility_llm)
        self.classifier = Classifier(utility_llm)
        self.history_compactor = HistoryCompactor(utility_llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        graph = StateGraph(state_schema=DiagnosisAgentState)
        graph.add_node("scope_node", self._scope_node)
        graph.add_node("hypothesize_node", self._hypothesize_node)
        graph.add_node("investigate_node", self._investigate_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("evaluate_node", self._evaluate_node)
        graph.add_node("finalize_node", self._finalize_node)

        graph.add_edge(START, "scope_node")
        graph.add_edge("scope_node", "hypothesize_node")
        graph.add_edge("hypothesize_node", "investigate_node")
        graph.add_conditional_edges(
            "investigate_node",
            self._investigate_routing,
            {"tool_node": "tool_node", "evaluate_node": "evaluate_node"},
        )
        graph.add_edge("tool_node", "investigate_node")
        graph.add_conditional_edges(
            "evaluate_node",
            self._evaluate_routing,
            {"hypothesize_node": "hypothesize_node", "finalize_node": "finalize_node"},
        )
        graph.add_edge("finalize_node", END)
        return graph.compile()

    async def _scope_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        self.logger.info("\n=== scope ===")
        messages: list[Any] = [SystemMessage(content=SCOPE_PROMPT.format(query=state["query"]))]
        summary = ""
        for _ in range(self.MAX_SCOPE_CALLS):
            response = await self.scope_llm.ainvoke(messages)
            messages.append(response)
            if not response.tool_calls:
                summary = str(response.content)
                break
            for call in response.tool_calls:
                self.logger.info(f"  scope -> {call['name']}({call['args']})")
            tool_result = await self.scope_tool_executor.ainvoke({"messages": messages})
            messages.extend(tool_result["messages"])
        else:
            # budget hit while still calling tools — summarize what we have
            final = await self.scope_llm.ainvoke(
                messages + [SystemMessage(content="Stop. Reply with the concise scene summary now, no tool calls.")]
            )
            summary = str(final.content)
        self.logger.info(f"  scope_summary: {self._preview(summary)}")
        return {
            "scope_summary": summary,
            "hypothesis_count": 0,
            "investigate_iterations": 0,
            "ruled_out": [],
        }

    async def _hypothesize_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        count = state.get("hypothesis_count", 0) + 1
        self.logger.info(f"\n=== hypothesize (attempt {count}/{self.MAX_HYPOTHESES}) ===")
        selection = await self.hypothesizer.select(
            state["query"],
            state.get("scope_summary", ""),
            self.playbooks.list_triggers(),
            state.get("ruled_out", []),
        )
        self.logger.info(f"  hypothesis: {self._preview(selection.hypothesis)} (playbook={selection.playbook_id})")
        return {
            "current_hypothesis": selection.hypothesis,
            "selected_playbook_id": selection.playbook_id,
            "hypothesis_count": count,
            "investigate_iterations": 0,
        }

    async def _investigate_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        iteration = state.get("investigate_iterations", 0) + 1
        self.logger.info(f"\n=== investigate {iteration}/{self.MAX_INVESTIGATE_ITERATIONS} ===")
        playbook = self.playbooks.get(state["selected_playbook_id"])
        ruled_out = "\n".join(
            f"- {r['hypothesis']}: {r['why_ruled_out']}" for r in state.get("ruled_out", [])
        ) or "(none)"
        system_prompt = INVESTIGATE_PROMPT.format(
            query=state["query"],
            scope_summary=state.get("scope_summary", ""),
            hypothesis=state.get("current_hypothesis", ""),
            ruled_out=ruled_out,
            playbook=playbook.body,
        )
        history, compaction_edits = self.history_compactor.compact(state["messages"])
        messages = [SystemMessage(content=system_prompt)] + history
        response = await self.llm.ainvoke(messages)
        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")
        return {"messages": [*compaction_edits, response], "investigate_iterations": iteration}

    async def _tool_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        messages = state["messages"]
        last_message = messages[-1]
        all_calls = last_message.tool_calls
        repeats = find_repeated_calls(messages)

        fresh_messages: list[Any] = []
        fresh_calls = [call for call in all_calls if call["id"] not in repeats]
        if fresh_calls:
            pruned_last = last_message.model_copy(update={"tool_calls": fresh_calls})
            fresh_state = {**state, "messages": [*messages[:-1], pruned_last]}
            fresh_result = await self._tool_executor.ainvoke(fresh_state)
            fresh_messages = fresh_result["messages"]

        repeat_messages = [
            ToolMessage(
                content=(
                    "[duplicate call suppressed] This exact call was already made earlier in "
                    f"this investigation and returned:\n{content}\n"
                    "Calling it again will not produce a different result -- use the evidence "
                    "you already have, or investigate something else."
                ),
                name=next(call["name"] for call in all_calls if call["id"] == call_id),
                tool_call_id=call_id,
            )
            for call_id, content in repeats.items()
        ]

        order = {call["id"]: i for i, call in enumerate(all_calls)}
        result = {"messages": sorted(fresh_messages + repeat_messages, key=lambda m: order[m.tool_call_id])}
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _investigate_routing(self, state: DiagnosisAgentState) -> Literal["tool_node", "evaluate_node"]:
        last_message = state["messages"][-1]
        has_calls = bool(getattr(last_message, "tool_calls", None))
        if has_calls and state.get("investigate_iterations", 0) < self.MAX_INVESTIGATE_ITERATIONS:
            return "tool_node"
        return "evaluate_node"

    async def _evaluate_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        self.logger.info("\n=== evaluate ===")
        messages = state["messages"]
        last_message = messages[-1]
        pending_calls = getattr(last_message, "tool_calls", None) or []
        stop_messages = [
            ToolMessage(
                content="[not executed] Investigation budget exhausted before this call could run.",
                name=call["name"],
                tool_call_id=call["id"],
            )
            for call in pending_calls
        ]
        if stop_messages:
            self.logger.info(
                f"  budget exhausted with {len(stop_messages)} pending tool call(s) -- synthesizing stop responses"
            )
        evaluation_messages = messages + stop_messages

        playbook = self.playbooks.get(state["selected_playbook_id"])
        verdict = await self.evaluator.evaluate(
            state["query"],
            state.get("current_hypothesis", ""),
            playbook.body,
            evaluation_messages,
            state.get("hypothesis_count", 0),
            self.MAX_HYPOTHESES,
        )
        self.logger.info(f"  verdict={verdict.verdict} ({self._preview(verdict.reasoning)})")
        update: dict[str, Any] = {
            "last_verdict": verdict.verdict,
            "requires_remediation_hint": verdict.requires_remediation,
        }
        if stop_messages:
            update["messages"] = stop_messages
        if verdict.verdict == "reformulate":
            update["ruled_out"] = state.get("ruled_out", []) + [
                {
                    "hypothesis": state.get("current_hypothesis", ""),
                    "why_ruled_out": verdict.why_ruled_out or verdict.reasoning,
                }
            ]
        return update

    def _evaluate_routing(self, state: DiagnosisAgentState) -> Literal["hypothesize_node", "finalize_node"]:
        if state.get("last_verdict") == "reformulate" and state.get("hypothesis_count", 0) < self.MAX_HYPOTHESES:
            return "hypothesize_node"
        return "finalize_node"

    async def _finalize_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        self.logger.info("\n=== finalize ===")
        if state.get("last_verdict") == "exhausted" or (
            state.get("last_verdict") == "reformulate" and state.get("hypothesis_count", 0) >= self.MAX_HYPOTHESES
        ):
            self.logger.warning("  investigation exhausted — escalating (diagnosis_success=False)")
            return {
                "diagnosis_result": DiagnosisResult(
                    summary=(
                        "Investigation did not reach a confident root cause within the hypothesis "
                        f"budget ({self.MAX_HYPOTHESES} hypotheses). Escalating to human review "
                        "rather than risking a false negative."
                    ),
                    root_cause=None,
                    requires_remediation=True,
                    diagnosis_success=False,
                )
            }
        parsed = await self.classifier.classify(state["query"], state["messages"])
        hint = state.get("requires_remediation_hint")
        if hint is not None and parsed.diagnosis_success:
            parsed = parsed.model_copy(update={"requires_remediation": hint})
        self.logger.info(f"  requires_remediation={parsed.requires_remediation} summary={self._preview(parsed.summary)}")
        return {"diagnosis_result": parsed}

    @staticmethod
    def _preview(text: Any, limit: int = 500) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, state: DiagnosisAgentState):
        return self.graph.invoke(state)

    async def ainvoke(self, state: DiagnosisAgentState):
        return await self.graph.ainvoke(state)
