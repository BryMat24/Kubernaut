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
from graph.state import PlannerAgentState
from models import RemediationPlan
import logging
import uuid

logging.basicConfig(level=logging.INFO, format="%(message)s")


class PlannerAgent:
    def __init__(self, llm: BaseChatModel, tools: list[BaseTool]) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.logger = logging.getLogger("planner_agent")
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = f"""
            You are a read-only GitOps planning agent. You investigate a GitOps repo to
            produce a concrete, file-by-file remediation plan for a diagnosed Kubernetes
            problem — you never modify anything; a separate remediation agent performs
            the actual edits from your plan.

            The `working_directory` and the diagnosed issue are provided below on every turn.

            Available tools — this is the complete list, there is no shell, git, or terminal
            access, and no other tool exists: {", ".join(t.name for t in tools)}.

            Workflow:
            0. Use `read_file_content` on AGENT.md (if present) for repo/architecture context.
            1. Use `find` and `grep` to locate the manifest(s) relevant to the diagnosed issue
            — do not guess a path.
            2. Use `list_files_in_directory` to explore surrounding structure when needed.
            3. Use `read_file_content` to see the current, exact content of every file your
            plan will reference — every anchor you describe and every new_content snippet in
            your final plan must be grounded in a file you actually read in this investigation,
            never guessed.

            Constraints:
            - Do not propose changes outside what the diagnosed issue requires.
            - Preserve existing YAML structure, key ordering, and indentation style in any
            new_content you propose.
            - If you cannot find the relevant file(s) with reasonable confidence, stop and
            say so plainly — do not guess a plan from unread files.

            When you have gathered enough evidence to write concrete steps (or determined you
            cannot), stop calling tools — a separate step turns your findings into the final
            structured plan.
        """
        self.classifier = PlanClassifier(llm)
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
        self.logger.info("\n=== setup ===")
        branch = f"plan/{slugify(state['diagnosis_result'].summary)}-{uuid.uuid4().hex[:6]}"
        with repo_lock(state["repo_url"]):
            bare_path = ensure_base_clone(state["repo_url"])
            repo_path = create_task_worktree(bare_path, branch)
        self.logger.info(f"  repo: {bare_path}")
        self.logger.info(f"  worktree: {repo_path} (branch {branch})")
        return {"bare_path": bare_path, "repo_path": repo_path, "branch": branch}

    def _cleanup_node(self, state: PlannerAgentState) -> dict[str, Any]:
        self.logger.info("\n=== cleanup ===")
        try:
            remove_task_worktree(state["bare_path"], state["repo_path"])
            self.logger.info(f"  removed worktree: {state['repo_path']}")
        except Exception as e:
            self.logger.info(f"  failed to remove worktree: {e}")
        try:
            run(["git", "branch", "-D", state["branch"]], state["bare_path"])
            self.logger.info(f"  deleted branch: {state['branch']}")
        except Exception as e:
            self.logger.info(f"  failed to delete branch: {e}")
        return {}

    def _reasoning_node(self, state: PlannerAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        diagnosis_result = state["diagnosis_result"]
        root_cause_line = f"\nroot_cause: {diagnosis_result.root_cause}" if diagnosis_result.root_cause else ""
        issue_text = f"summary: {diagnosis_result.summary}{root_cause_line}"

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nworking_directory: {state['repo_path']}\n\ndiagnosed issue:\n{issue_text}"

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = self.llm.invoke(messages)

        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")

        return {
            "messages": [response],
            "iteration_count": iteration,
        }

    def _tool_node(self, state: PlannerAgentState) -> dict[str, Any]:
        result = self._tool_executor.invoke(state)
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _tool_routing(self, state: PlannerAgentState) -> Literal["tool_node", "finalize_node"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            self.logger.info(f"  hit MAX_ITERATIONS ({self.MAX_ITERATIONS}), stopping")
            return "finalize_node"

        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "finalize_node"

    async def _finalize_node(self, state: PlannerAgentState) -> dict[str, Any]:
        self.logger.info("\n=== finalize ===")
        hit_max_iterations = state.get("iteration_count", 0) >= self.MAX_ITERATIONS
        last_message = state["messages"][-1]
        incomplete = hit_max_iterations and bool(getattr(last_message, "tool_calls", None))

        if incomplete:
            self.logger.warning(
                f"  MAX_ITERATIONS ({self.MAX_ITERATIONS}) hit mid-investigation "
                f"— forcing a failed plan instead of trusting partial results"
            )
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
        self.logger.info(f"  planning_success={parsed.planning_success} steps={len(parsed.steps)} summary={self._preview(parsed.summary)}")
        return {"plan": parsed}

    @staticmethod
    def _preview(text: Any, limit: int = 300) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, state: PlannerAgentState):
        return self.graph.invoke(state)

    async def ainvoke(self, state: PlannerAgentState):
        return await self.graph.ainvoke(state)
