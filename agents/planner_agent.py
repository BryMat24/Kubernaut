from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing import Any, Literal
from utils import (
    slugify,
    ensure_base_clone,
    create_task_worktree,
    remove_task_worktree,
    repo_lock,
    run,
)
from .helpers import PlanClassifier
from .prompts import PLANNER_SYSTEM_PROMPT
from graph.state import PlannerAgentState
from models import RemediationPlan
import uuid


class PlannerAgent:
    def __init__(self, llm: BaseChatModel, tools: list[BaseTool], utility_llm: BaseChatModel) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = PLANNER_SYSTEM_PROMPT.format(tool_names=", ".join(t.name for t in tools))
        self.classifier = PlanClassifier(utility_llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=PlannerAgentState)
        graph.add_node("setup_node", self._setup_node)
        graph.add_node("reasoning_node", self._reasoning_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("finalize_node", self._finalize_node)
        graph.add_node("cleanup_node", self._cleanup_node)

        graph.add_edge(START, "setup_node")
        graph.add_edge("setup_node", "reasoning_node")
        graph.add_conditional_edges(
            "reasoning_node",
            self._tool_routing,
            {"tool_node": "tool_node", "finalize_node": "finalize_node"},
        )
        graph.add_edge("tool_node", "reasoning_node")
        graph.add_edge("finalize_node", "cleanup_node")
        graph.add_edge("cleanup_node", END)
        return graph.compile()

    def _setup_node(self, state: PlannerAgentState) -> dict[str, Any]:
        branch = f"plan/{slugify(state['diagnosis_result'].summary)}-{uuid.uuid4().hex[:6]}"
        with repo_lock(state["repo_url"]):
            bare_path = ensure_base_clone(state["repo_url"])
            repo_path = create_task_worktree(bare_path, branch)
        return {"bare_path": bare_path, "repo_path": repo_path, "branch": branch}

    def _cleanup_node(self, state: PlannerAgentState) -> dict[str, Any]:
        try:
            remove_task_worktree(state["bare_path"], state["repo_path"])
        except Exception:
            pass
        try:
            run(["git", "branch", "-D", state["branch"]], state["bare_path"])
        except Exception:
            pass
        return {}

    def _reasoning_node(self, state: PlannerAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1

        diagnosis_result = state["diagnosis_result"]
        root_cause_line = f"\nroot_cause: {diagnosis_result.root_cause}" if diagnosis_result.root_cause else ""
        issue_text = f"summary: {diagnosis_result.summary}{root_cause_line}"

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nworking_directory: {state['repo_path']}\n\ndiagnosed issue:\n{issue_text}"

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = self.llm.invoke(messages)

        return {
            "messages": [response],
            "iteration_count": iteration,
        }

    def _tool_node(self, state: PlannerAgentState) -> dict[str, Any]:
        return self._tool_executor.invoke(state)

    def _tool_routing(self, state: PlannerAgentState) -> Literal["tool_node", "finalize_node"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            return "finalize_node"

        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "finalize_node"

    async def _finalize_node(self, state: PlannerAgentState) -> dict[str, Any]:
        hit_max_iterations = state.get("iteration_count", 0) >= self.MAX_ITERATIONS
        last_message = state["messages"][-1]
        incomplete = hit_max_iterations and bool(getattr(last_message, "tool_calls", None))

        if incomplete:
            diagnosis_result = state["diagnosis_result"]
            root_cause_line = (
                f"\n\nRoot cause: {diagnosis_result.root_cause}" if diagnosis_result.root_cause else ""
            )
            parsed = RemediationPlan(
                summary=(
                    "Investigation did not complete within the iteration budget "
                    f"({self.MAX_ITERATIONS} steps). No concrete plan was produced.\n\n"
                    f"Diagnosed issue: {diagnosis_result.summary}{root_cause_line}"
                ),
                steps=[],
                planning_success=False,
            )
            return {"plan": parsed}

        parsed = await self.classifier.classify(state["diagnosis_result"], state["messages"])
        return {"plan": parsed}

    def invoke(self, state: PlannerAgentState):
        return self.graph.invoke(state)

    async def ainvoke(self, state: PlannerAgentState):
        return await self.graph.ainvoke(state)
