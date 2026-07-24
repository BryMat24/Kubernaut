from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing import Any, Literal
from utils import (
    get_diff_content,
    get_changed_files,
    open_pull_request,
    slugify,
    ensure_base_clone,
    create_task_worktree,
    remove_task_worktree,
    repo_lock,
)
from .helpers import DiffEvaluator, HistoryCompactor
from .prompts import REMEDIATION_SYSTEM_PROMPT
from graph.state import RemediationAgentState
from models import RemediationPlan
import logging
import yaml
import json
import uuid

import os

logging.basicConfig(level=logging.INFO, format="%(message)s")


class RemediationAgent:
    def __init__(
        self,
        llm: BaseChatModel,
        tools: list[BaseTool],
        llm_judge: BaseChatModel,
        compactor_llm: BaseChatModel | None = None,
    ) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.graph = self._build_graph()
        self.llm_as_judge = DiffEvaluator(llm_judge)
        self.history_compactor = HistoryCompactor(compactor_llm or llm)
        self.logger = logging.getLogger("remediation_agent")
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = REMEDIATION_SYSTEM_PROMPT.format(tool_names=", ".join(t.name for t in tools))
    
    def _build_graph(self) -> CompiledStateGraph:
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=RemediationAgentState)
        graph.add_node("setup_node", self._setup_node)
        graph.add_node("reasoning_node", self._reasoning_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("evaluation_node", self._evaluation_node)
        graph.add_node("pr_node", self._pr_node)
        graph.add_node("cleanup_node", self._cleanup_node)

        graph.add_edge(START, "setup_node")
        graph.add_edge("setup_node", "reasoning_node")
        graph.add_conditional_edges(
            "reasoning_node",
            self._tool_routing,
            {"tool_node": "tool_node", "evaluation_node": "evaluation_node", "end": "cleanup_node"},
        )
        graph.add_edge("tool_node", "reasoning_node")
        graph.add_conditional_edges(
            "evaluation_node",
            self._evaluation_routing,
            {"reasoning_node": "reasoning_node", "pr_node": "pr_node"},
        )
        graph.add_edge("pr_node", "cleanup_node")
        graph.add_edge("cleanup_node", END)
        return graph.compile()

    def _setup_node(self, state: RemediationAgentState) -> dict[str, Any]:
        self.logger.info("\n=== setup ===")
        branch = f"agent/{slugify(state['plan'].summary)}-{uuid.uuid4().hex[:6]}"
        with repo_lock(state["repo_url"]):
            bare_path = ensure_base_clone(state["repo_url"])
            repo_path = create_task_worktree(bare_path, branch)
        self.logger.info(f"  repo: {bare_path}")
        self.logger.info(f"  worktree: {repo_path} (branch {branch})")
        return {"bare_path": bare_path, "repo_path": repo_path, "branch": branch}

    def _cleanup_node(self, state: RemediationAgentState) -> dict[str, Any]:
        self.logger.info("\n=== cleanup ===")
        try:
            remove_task_worktree(state["bare_path"], state["repo_path"])
            self.logger.info(f"  removed worktree: {state['repo_path']}")
        except Exception as e:
            self.logger.info(f"  failed to remove worktree: {e}")
        return {}

    def _reasoning_node(self, state: RemediationAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        plan = state["plan"]
        if state.get("eval_passed") is False:
            # The most recent evaluation rejected the diff as a whole -- the judge doesn't
            # attribute failures to individual steps, so nothing is trusted as confirmed
            # complete until a fresh evaluation runs again, regardless of disk content.
            completed: set[int] = set()
        else:
            files = get_changed_files(state["repo_path"])
            completed = set(self._steps_completed_from_files(state["repo_path"], files, plan))
        remaining = [s.step_number for s in plan.steps if s.step_number not in completed]
        progress = f"\n\nSteps completed: {sorted(completed)}. Steps remaining: {remaining}." if plan.steps else ""

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nworking_directory: {state['repo_path']}\n\ntask:\n{self._task_description(plan)}{progress}"

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
            "completed_steps": sorted(completed),
        }

    def _tool_node(self, state: RemediationAgentState) -> dict[str, Any]:
        result = self._tool_executor.invoke(state)
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _tool_routing(self, state: RemediationAgentState) -> Literal["tool_node", "evaluation_node", "end"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            self.logger.info(f"  hit MAX_ITERATIONS ({self.MAX_ITERATIONS}), stopping")
            return "end"
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "evaluation_node"
    
    def _evaluation_node(self, state: RemediationAgentState) -> dict[str, Any]:
        self.logger.info("\n=== evaluation ===")

        files = get_changed_files(state["repo_path"])
        self.logger.info(f"  changed files: {files}")

        completed_steps = self._steps_completed_from_files(state["repo_path"], files, state["plan"])
        self.logger.info(f"  completed steps: {completed_steps}")

        if not files:
            self.logger.info("  no files changed, eval_passed=True")
            return { "eval_passed": True, "completed_steps": completed_steps }

        # check yaml structure valid or not
        errorList = []
        structureValid = True

        for file_path in files:
            passed, err = self._validate_yaml_syntax(os.path.join(state["repo_path"], file_path))
            if not passed:
                errorList.append(err)
                structureValid = False

        if not structureValid:
            self.logger.info(f"  yaml syntax invalid, eval_passed=False: {errorList}")
            return {
                "eval_passed": False,
                "completed_steps": completed_steps,
                "messages": [AIMessage(content=f"Review feedback: Error in parsing yaml syntax\nIssues: {json.dumps(errorList)}\nPlease fix.")]
            }

        diff = get_diff_content(state["repo_path"])
        self.logger.info(f"  diff:\n{self._preview(diff, limit=1000)}")
        result = self.llm_as_judge.evaluate(self._task_description(state["plan"]), diff)
        self.logger.info(f"  judge result: correct={result.correct} reasoning={self._preview(result.reasoning)} issues={result.issues}")

        if not result.correct:
            self.logger.info("  eval_passed=False, sending feedback back to reasoning_node")
            return {
                "eval_passed": False,
                "completed_steps": completed_steps,
                "messages": [AIMessage(content=f"Review feedback: {result.reasoning}\nIssues: {result.issues}\nPlease fix.")],
            }

        self.logger.info("  eval_passed=True")
        return {
            "eval_passed": True,
            "eval_reasoning": result.reasoning,
            "completed_steps": completed_steps,
            "messages": [AIMessage(content="All evaluation has passed, proceeding to open PR")],
        }

    def _validate_yaml_syntax(self, file_path: str) -> tuple[bool, str | None]:
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                yaml.safe_load(file)
            return True, None
        except Exception as e:
            return False, f"Error in parsing yaml of file: {file_path}, error: {e}"
    
    def _evaluation_routing(self, state: RemediationAgentState) -> Literal["reasoning_node", "pr_node"]:
        if state["eval_passed"]:
            self.logger.info("  routing: evaluation_node -> pr_node")
            return "pr_node"
        self.logger.info("  routing: evaluation_node -> reasoning_node")
        return "reasoning_node"

    def _pr_node(self, state: RemediationAgentState) -> dict[str, Any]:
        self.logger.info("\n=== opening PR ===")
        files = get_changed_files(state["repo_path"])
        try:
            plan_summary = state["plan"].summary
            pr_url = open_pull_request(
                state["repo_path"],
                state["branch"],
                commit_message=f"fix: {plan_summary}",
                title=plan_summary[:72],
                body=self._build_pr_body(files, state.get("eval_reasoning", "")),
            )
            self.logger.info(f"  opened PR: {pr_url}")
            return {"pr_url": pr_url, "messages": [AIMessage(content=f"Opened PR: {pr_url}")]}
        except Exception as e:
            self.logger.info(f"  failed to open PR: {e}")
            return {"messages": [AIMessage(content=f"Failed to open PR: {e}")]}

    @staticmethod
    def _steps_completed_from_files(repo_path: str, files: list[str], plan: RemediationPlan) -> list[int]:
        changed = set(files)
        completed: list[int] = []
        file_cache: dict[str, str] = {}
        for step in plan.steps:
            if step.file_path not in changed:
                continue
            if step.file_path not in file_cache:
                try:
                    with open(os.path.join(repo_path, step.file_path), "r", encoding="utf-8") as f:
                        file_cache[step.file_path] = f.read()
                except OSError:
                    file_cache[step.file_path] = ""
            target = step.new_content.strip()
            if target and target in file_cache[step.file_path]:
                completed.append(step.step_number)
        return completed

    @staticmethod
    def _task_description(plan: RemediationPlan) -> str:
        lines = [plan.summary]
        for step in plan.steps:
            lines.append(f"\n{step.step_number}. {step.file_path}: {step.description}")
            lines.append(f"   new_content:\n{step.new_content}")
        return "\n".join(lines)

    @staticmethod
    def _build_pr_body(files: list[str], reasoning: str) -> str:
        file_list = "\n".join(f"- {f}" for f in files) or "(no files changed)"
        return f"## Changed files\n{file_list}\n\n## Judge reasoning\n{reasoning}"

    @staticmethod
    def _preview(text: Any, limit: int = 300) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, initial_state: RemediationAgentState) -> RemediationAgentState:
        return self.graph.invoke(initial_state)

    async def ainvoke(self, initial_state: RemediationAgentState) -> RemediationAgentState:
        return await self.graph.ainvoke(initial_state)