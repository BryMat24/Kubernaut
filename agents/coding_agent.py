from dotenv import load_dotenv
from tools import list_files_in_directory, read_file_content, grep, find, edit_file, write_file
from langchain_openrouter import ChatOpenRouter
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, MessagesState, START, END
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
from evaluator import DiffEvaluator
import logging
import yaml
import json
import uuid

import os

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")

class CodingAgentState(MessagesState):
    task: str # provided by caller
    iteration_count: int
    eval_passed: bool
    eval_reasoning: str
    pr_url: str

    repo_url: str       # provided by the caller, e.g. from frontend input
    bare_path: str       # set by _setup_node: shared bare clone for repo_url
    repo_path: str       # set by _setup_node: this task's worktree
    branch: str          # set by _setup_node: branch created for this task's worktree

class CodingAgent:
    def __init__(self, llm: BaseChatModel, tools: list[BaseTool], llm_judge: BaseChatModel) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.graph = self._build_graph()
        self.llm_as_judge = DiffEvaluator(llm_judge)
        self.logger = logging.getLogger("coding_agent")
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = f"""
            You are a GitOps repair agent. Your job is to find and fix the
            Kubernetes manifest responsible for a reported problem in the GitOps repo below,
            making the smallest correct change, nothing else.

            The `working_directory` to pass to every tool call is provided below, alongside
            the task, on every turn.

            Available tools — this is the complete list, there is no shell, git, or terminal
            access, and no other tool exists: {", ".join(t.name for t in tools)}.

            Workflow:
            0. use read_file_content on AGENT.md file which gives information regarding repository
            and overall architecture
            1. Use `find` and `grep` to locate the manifest(s) relevant to the task before
            touching anything — do not guess a path. This will help narrow the file path
            2. Use `list_files_in_directory` to list files in directory
            3. Use `read_file_content` to see the current, exact content of a file before
            editing it. Never edit a file you haven't just read. This is also how you check
            whether the task is already done: if the value already matches what the task asks
            for, stop immediately — do not call `edit_file`, do not keep searching other files
            — and reply with a summary saying no change was needed.
            4. Use `edit_file` for changes to existing files. `old_content` must be copied
            verbatim (exact whitespace/indentation) from what you just read, and must be
            unique in the file — include enough surrounding context to make it so.
            5. Use `write_file` only to create a genuinely new file. Never use it to rewrite
            an existing file wholesale.
            6. If you add or remove a manifest file, update the matching `kustomization.yaml`
            `resources:` list so it stays in sync.

            Constraints:
            - Only edit files inside the working directory above.
            - Fix only what the task describes. Do not refactor, reformat, or touch unrelated
            fields, files, or apps.
            - Preserve existing YAML structure, key ordering, and indentation style exactly.
            - If a manifest already reflects the desired end state, leave it unchanged and stop
            - For Helm charts, do not update the template files, but modify in its corresponding values.yaml file

            When you are confident the fix has been applied correctly, stop calling tools and
            reply with a brief summary of what you changed and why — that ends the task.

            If there are review feedback, please focus on fixing the issues pointed by the evaluation,
            inorder for the process to complete the both the task and review feedback needed to be solved
        """
    
    def _build_graph(self) -> CompiledStateGraph:
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=CodingAgentState)
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

    def _setup_node(self, state: CodingAgentState) -> dict[str, Any]:
        self.logger.info("\n=== setup ===")
        branch = f"agent/{slugify(state['task'])}-{uuid.uuid4().hex[:6]}"
        with repo_lock(state["repo_url"]):
            bare_path = ensure_base_clone(state["repo_url"])
            repo_path = create_task_worktree(bare_path, branch)
        self.logger.info(f"  repo: {bare_path}")
        self.logger.info(f"  worktree: {repo_path} (branch {branch})")
        return {"bare_path": bare_path, "repo_path": repo_path, "branch": branch}

    def _cleanup_node(self, state: CodingAgentState) -> dict[str, Any]:
        self.logger.info("\n=== cleanup ===")
        try:
            remove_task_worktree(state["bare_path"], state["repo_path"])
            self.logger.info(f"  removed worktree: {state['repo_path']}")
        except Exception as e:
            self.logger.info(f"  failed to remove worktree: {e}")
        return {}

    def _reasoning_node(self, state: CodingAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nworking_directory: {state['repo_path']}\n\ntask:\n{state['task']}"

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = self.llm.invoke(messages)

        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")

        return {
            "messages": [response],
            "iteration_count": iteration,
        }

    def _tool_node(self, state: CodingAgentState) -> dict[str, Any]:
        result = self._tool_executor.invoke(state)
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _tool_routing(self, state: CodingAgentState) -> Literal["tool_node", "evaluation_node", "end"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            self.logger.info(f"  hit MAX_ITERATIONS ({self.MAX_ITERATIONS}), stopping")
            return "end"
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "evaluation_node"
    
    def _evaluation_node(self, state: CodingAgentState) -> dict[str, Any]:
        self.logger.info("\n=== evaluation ===")

        files = get_changed_files(state["repo_path"])
        self.logger.info(f"  changed files: {files}")

        if not files:
            self.logger.info("  no files changed, eval_passed=True")
            return { "eval_passed": True }

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
                "messages": [AIMessage(content=f"Review feedback: Error in parsing yaml syntax\nIssues: {json.dumps(errorList)}\nPlease fix.")]
            }

        diff = get_diff_content(state["repo_path"])
        self.logger.info(f"  diff:\n{self._preview(diff, limit=1000)}")
        result = self.llm_as_judge.evaluate(state["task"], diff)
        self.logger.info(f"  judge result: correct={result.correct} reasoning={self._preview(result.reasoning)} issues={result.issues}")

        if not result.correct:
            self.logger.info("  eval_passed=False, sending feedback back to reasoning_node")
            return {"eval_passed": False, "messages": [AIMessage(content=f"Review feedback: {result.reasoning}\nIssues: {result.issues}\nPlease fix.")] }

        self.logger.info("  eval_passed=True")
        return {
            "eval_passed": True,
            "eval_reasoning": result.reasoning,
            "messages": [AIMessage(content="All evaluation has passed, proceeding to open PR")],
        }

    def _validate_yaml_syntax(self, file_path: str) -> tuple[bool, str | None]:
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                yaml.safe_load(file)
            return True, None
        except Exception as e:
            return False, f"Error in parsing yaml of file: {file_path}, error: {e}"
    
    def _evaluation_routing(self, state: CodingAgentState) -> Literal["reasoning_node", "pr_node"]:
        if state["eval_passed"]:
            self.logger.info("  routing: evaluation_node -> pr_node")
            return "pr_node"
        self.logger.info("  routing: evaluation_node -> reasoning_node")
        return "reasoning_node"

    def _pr_node(self, state: CodingAgentState) -> dict[str, Any]:
        self.logger.info("\n=== opening PR ===")
        files = get_changed_files(state["repo_path"])
        try:
            pr_url = open_pull_request(
                state["repo_path"],
                state["branch"],
                commit_message=f"fix: {state['task']}",
                title=state["task"][:72],
                body=self._build_pr_body(files, state.get("eval_reasoning", "")),
            )
            self.logger.info(f"  opened PR: {pr_url}")
            return {"pr_url": pr_url, "messages": [AIMessage(content=f"Opened PR: {pr_url}")]}
        except Exception as e:
            self.logger.info(f"  failed to open PR: {e}")
            return {"messages": [AIMessage(content=f"Failed to open PR: {e}")]}

    @staticmethod
    def _build_pr_body(files: list[str], reasoning: str) -> str:
        file_list = "\n".join(f"- {f}" for f in files) or "(no files changed)"
        return f"## Changed files\n{file_list}\n\n## Judge reasoning\n{reasoning}"

    @staticmethod
    def _preview(text: Any, limit: int = 300) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, initial_state: CodingAgentState) -> CodingAgentState:
        return self.graph.invoke(initial_state)