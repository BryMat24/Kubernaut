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
    IntentClassifier,
    PlaybookLibrary,
    find_repeated_calls,
)
from graph.state import DiagnosisAgentState
from .prompts import SCOPE_PROMPT, INVESTIGATE_PROMPT, EXPLAIN_PROMPT

load_dotenv()


class DiagnosisAgent:
    def __init__(
        self,
        llm: BaseChatModel,
        investigate_tools: list[BaseTool],
        scope_tools: list[BaseTool],
        utility_llm: BaseChatModel | None = None,
    ) -> None:
        self.MAX_SCOPE_CALLS = 8
        self.MAX_EXPLAIN_ITERATIONS = 5
        self.MAX_INVESTIGATE_ITERATIONS = 8
        self.MAX_HYPOTHESES = 3

        self.scope_tool_executor = ToolNode(scope_tools)
        self.tool_executor = ToolNode(investigate_tools)

        self.llm = llm.bind_tools(investigate_tools)
        self.scope_llm = utility_llm.bind_tools(scope_tools)
        self.utility_llm = utility_llm
        self.playbooks = PlaybookLibrary()
        self.intent_classifier = IntentClassifier(utility_llm)
        self.hypothesizer = Hypothesizer(utility_llm)
        self.evaluator = DiagnosisEvaluator(utility_llm)
        self.classifier = Classifier(utility_llm)
        self.history_compactor = HistoryCompactor(utility_llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        graph = StateGraph(state_schema=DiagnosisAgentState)
        graph.add_node("intent_node", self._intent_node)
        graph.add_node("_context_builder", self._context_builder)
        graph.add_node("explain_node", self._explain_node)
        graph.add_node("hypothesize_node", self._hypothesize_node)
        graph.add_node("investigate_node", self._investigate_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("evaluate_node", self._evaluate_node)
        graph.add_node("finalize_node", self._finalize_node)

        graph.add_edge(START, "intent_node")
        graph.add_conditional_edges(
            "intent_node",
            self._intent_routing,
            {"_context_builder": "_context_builder", "explain_node": "explain_node"},
        )
        graph.add_edge("_context_builder", "hypothesize_node")
        graph.add_edge("explain_node", "finalize_node")
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

    async def _intent_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        classification = await self.intent_classifier.classify(state["query"])
        return {"detected_intent": classification.intent}

    def _intent_routing(self, state: DiagnosisAgentState) -> Literal["_context_builder", "explain_node"]:
        if state.get("detected_intent") == "explain":
            return "explain_node"
        return "_context_builder"

    async def _explain_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        system_message = SystemMessage(content=EXPLAIN_PROMPT.format(query=state["query"]))
        turn: list[Any] = []
        for _ in range(self.MAX_EXPLAIN_ITERATIONS):
            response = await self.llm.ainvoke([system_message, *turn])
            turn.append(response)
            if not response.tool_calls:
                return {"messages": turn}
            tool_result = await self.scope_tool_executor.ainvoke({"messages": [system_message, *turn]})
            turn.extend(tool_result["messages"])
        final = await self.llm.ainvoke(
            [system_message, *turn, SystemMessage(content="Stop. Answer the user's question now, no tool calls. If no evidence is found, don't hallucinate")]
        )
        turn.append(final)
        return {"messages": turn}

    async def _context_builder(self, state: DiagnosisAgentState) -> dict[str, Any]:
        messages: list[Any] = [SystemMessage(content=SCOPE_PROMPT.format(query=state["query"]))]
        summary = ""
        for _ in range(self.MAX_SCOPE_CALLS):
            response = await self.scope_llm.ainvoke(messages)
            messages.append(response)
            if not response.tool_calls:
                summary = str(response.content)
                break
            tool_result = await self.scope_tool_executor.ainvoke({"messages": messages})
            messages.extend(tool_result["messages"])
        else:
            final = await self.scope_llm.ainvoke(
                messages + [SystemMessage(content="Stop. Reply with the concise scene summary now, no tool calls.")]
            )
            summary = str(final.content)

        return {
            "scope_summary": summary
        }

    async def _hypothesize_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        count = state.get("hypothesis_count", 0) + 1
        selection = await self.hypothesizer.select(
            state["query"],
            state.get("scope_summary", ""),
            self.playbooks.list_triggers(),
            state.get("ruled_out", []),
        )
        return {
            "current_hypothesis": selection.hypothesis,
            "selected_playbook_id": selection.playbook_id,
            "hypothesis_count": count,
            "investigate_iterations": 0,
        }

    async def _investigate_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        iteration = state.get("investigate_iterations", 0) + 1
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
            fresh_result = await self.tool_executor.ainvoke(fresh_state)
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
        return result

    def _investigate_routing(self, state: DiagnosisAgentState) -> Literal["tool_node", "evaluate_node"]:
        last_message = state["messages"][-1]
        has_calls = bool(getattr(last_message, "tool_calls", None))
        if has_calls and state.get("investigate_iterations", 0) < self.MAX_INVESTIGATE_ITERATIONS:
            return "tool_node"
        return "evaluate_node"

    async def _evaluate_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
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
        if state.get("last_verdict") == "exhausted" or (
            state.get("last_verdict") == "reformulate" and state.get("hypothesis_count", 0) >= self.MAX_HYPOTHESES
        ):
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
        return {"diagnosis_result": parsed}

    def invoke(self, state: DiagnosisAgentState):
        return self.graph.invoke(state)

    async def ainvoke(self, state: DiagnosisAgentState):
        return await self.graph.ainvoke(state)
