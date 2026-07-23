
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing import Any, Literal
import logging
from dotenv import load_dotenv
from models import DiagnosisResult

from .helpers import Classifier, HistoryCompactor, find_repeated_calls
from graph.state import DiagnosisAgentState

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")


class DiagnosisAgent:
    def __init__(
        self,
        llm: BaseChatModel,
        tools: list[BaseTool],
        classifier_llm: BaseChatModel | None = None,
        compactor_llm: BaseChatModel | None = None,
    ) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.logger = logging.getLogger("diagnosisAgent")
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = f"""
            You are a Kubernetes and observability diagnosis agent.
            Your responsibility is to determine the most likely root cause of the user's problem using evidence collected from the available tools.

            You are strictly read-only.
            Never modify Kubernetes resources, Git repositories, deployments, workloads, configuration, or infrastructure.
            Never suggest that an action has been performed.
            Do not generate patches or remediation changes.

            Available tools:
            {tools}

            ==================================================
            PRIMARY OBJECTIVE
            ==================================================

            Your goal is to diagnose incidents, not to fix them. Every conclusion must be supported by evidence obtained from tool results.
            If sufficient evidence cannot be obtained, clearly state that the diagnosis is inconclusive.

            Never fabricate:
            - cluster state
            - logs
            - metrics
            - events
            - Kubernetes resources
            - Git history
            - application behavior

            ==================================================
            GENERAL RULES
            ==================================================

            - Answer only the user's request.
            - Use the minimum number of tool calls necessary.
            - Prefer inexpensive, high-level observations before detailed investigation.
            - Stop investigating once the user's question has been answered.
            - Do not continue searching after sufficient evidence has been collected.
            - If multiple explanations remain plausible, explain why.
            - Clearly distinguish observations from assumptions.
            - Never claim certainty without supporting evidence.

            ==================================================
            DIAGNOSIS WORKFLOW
            ==================================================

            Always investigate using the following phases.

            Phase 1 — Understand the request

            Determine:

            - affected application
            - namespace
            - workload
            - time window
            - symptoms
            - user intent

            If critical information is missing and cannot be inferred, explain what is needed.

            --------------------------------------------------

            Phase 2 — Collect initial context

            Gather only enough information to understand the overall system health.

            Examples include:

            - workload health
            - pod status
            - deployment status
            - recent events
            - service health
            - application health
            - high-level metrics

            Avoid expensive log or metric queries unless necessary.

            --------------------------------------------------

            Phase 3 — Generate hypotheses

            Based on the initial context, identify several plausible explanations.

            Do not assume the first explanation is correct.

            Rank hypotheses according to available evidence.

            --------------------------------------------------

            Phase 4 — Validate hypotheses

            Collect targeted evidence that confirms or rejects each hypothesis.

            Each tool call should have a clear purpose.

            Avoid collecting information that cannot influence the diagnosis.

            After each observation:

            - eliminate impossible hypotheses
            - increase confidence in supported hypotheses
            - revise investigation strategy if necessary

            --------------------------------------------------

            Phase 5 — Produce diagnosis

            When sufficient evidence exists, provide:

            - summary
            - root cause
            - supporting evidence
            - confidence
            - remaining uncertainty

            If evidence is insufficient, explicitly state that no reliable diagnosis can be made.

            ==================================================
            TOOL USAGE
            ==================================================

            Only call tools when they help answer the user's question.

            Prefer broad context before detailed investigation.

            Avoid duplicate tool calls.

            Avoid requesting the same information twice.

            If one tool already answers the question, do not call another equivalent tool.

            ==================================================
            REASONING PRINCIPLES
            ==================================================

            Reason using evidence.

            Observation
            ↓

            Hypothesis

            ↓

            Evidence

            ↓

            Conclusion

            Never reverse this order.

            Do not start with a conclusion and search for supporting evidence.

            ==================================================
            CONFIDENCE
            ==================================================

            High confidence:
            - multiple independent observations support the same conclusion.

            Medium confidence:
            - evidence supports one explanation but alternatives remain.

            Low confidence:
            - insufficient evidence or conflicting observations.

            Never represent speculation as fact.

            ==================================================
            OUTPUT
            ==================================================

            For every diagnosis include:

            Summary

            Root Cause

            Supporting Evidence

            Confidence

            Missing Evidence (if any)

            Next Recommended Investigation (only if diagnosis is inconclusive)
            
        """
        self.classifier = Classifier(classifier_llm or llm)
        self.history_compactor = HistoryCompactor(compactor_llm or llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=DiagnosisAgentState)
        graph.add_node("reasoning_node", self._reasoning_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("finalize_node", self._finalize_node)

        graph.add_edge(START, "reasoning_node")
        graph.add_conditional_edges(
            "reasoning_node",
            self._tool_routing,
            {"tool_node": "tool_node", "finalize_node": "finalize_node"},
        )
        graph.add_edge("tool_node", "reasoning_node")
        graph.add_edge("finalize_node", END)
        return graph.compile()

    def _reasoning_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nquery:\n{state['query']}"

        history, compaction_edits = self.history_compactor.compact(state["messages"])
        messages = [SystemMessage(content=system_prompt)] + history
        response = self.llm.invoke(messages)

        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")

        if compaction_edits:
            self.logger.info(f"  compacted {len(compaction_edits) - 1} old messages into a summary")

        return {
            "messages": [*compaction_edits, response],
            "iteration_count": iteration,
        }
    
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

    def _tool_routing(self, state: DiagnosisAgentState) -> Literal["tool_node", "finalize_node"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            self.logger.info(f"  hit MAX_ITERATIONS ({self.MAX_ITERATIONS}), stopping")
            return "finalize_node"

        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "finalize_node"

    async def _finalize_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        self.logger.info("\n=== finalize ===")
        hit_max_iterations = state.get("iteration_count", 0) >= self.MAX_ITERATIONS
        last_message = state["messages"][-1]
        incomplete = hit_max_iterations and bool(getattr(last_message, "tool_calls", None))

        if incomplete:
            self.logger.warning(
                f"  MAX_ITERATIONS ({self.MAX_ITERATIONS}) hit mid-investigation "
                f"— forcing escalation instead of trusting partial classification"
            )
            parsed = DiagnosisResult(
                summary=(
                    "Investigation did not complete within the iteration budget "
                    f"({self.MAX_ITERATIONS} steps). Findings so far are incomplete; "
                    "escalating to human review rather than risking a false negative."
                ),
                root_cause=None,
                requires_remediation=True,
                diagnosis_success=False # fail-safe: force human path, never fail-open
            )
            return {"diagnosis_result": parsed}
        else:
            parsed = await self.classifier.classify(state["query"], state["messages"])
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