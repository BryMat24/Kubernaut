from dotenv import load_dotenv
from tools import list_files_in_directory, read_file_content, grep, find, edit_file, write_file
from langchain_openrouter import ChatOpenRouter
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from typing import Literal, Annotated
import logging
import operator
import subprocess
import os

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")

GITOPS_REPO_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "Kubernaut-Gitops")
)

class CodingAgentState(MessagesState):
    test_result: str
    iteration_count: int
    modified_files: Annotated[list[str], operator.add]
    eval_passed: bool

class CodingAgent:
    def __init__(self, llm, tools):
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.graph = self._build_graph()
        self.logger = logging.getLogger("coding_agent")
        self.MAX_ITERATIONS = 10
        self.SYSTEM_PROMPT = f"""
            You are a GitOps repair agent. Your job is to find and fix the
            Kubernetes manifest responsible for a reported problem in the GitOps repo below,
            making the smallest correct change — nothing else.

            Repo working directory (always pass this exact string as `working_directory` to
            every tool call): {GITOPS_REPO_PATH}

            Workflow:
            1. Use `find` and `grep` to locate the manifest(s) relevant to the task before
            touching anything — do not guess a path. This will help narrow the file path
            2. Use `list_files_in_directory` to list files in directory
            3. Use `read_file_content` to see the current, exact content of a file before
            editing it. Never edit a file you haven't just read.
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

            When you are confident the fix has been applied correctly, stop calling tools and
            reply with a brief summary of what you changed and why — that ends the task.
        """
    
    def _build_graph(self):
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=CodingAgentState)
        graph.add_node("reasoning_node", self._reasoning_node)
        graph.add_node("tool_node", self._tool_node)

        graph.add_edge(START, "reasoning_node")
        graph.add_conditional_edges(
            "reasoning_node",
            self._tool_routing,
            {"tool_node": "tool_node", "evaluation_node": "evaluation_node"},
        )
        graph.add_conditional_edges(
            "evaluation_node",
            self._evaluation_routing,
            {"reasoning_node": "reasoning_node", "end": "end"}
        )
        graph.add_edge("tool_node", "reasoning_node")
        return graph.compile()

    def _reasoning_node(self, state: CodingAgentState):
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        messages = [SystemMessage(content=self.SYSTEM_PROMPT)] + state["messages"]
        response = self.llm.invoke(messages)

        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")

        return {
            "messages": [response],
            "iteration_count": iteration,
        }

    def _tool_node(self, state: CodingAgentState):
        result = self._tool_executor.invoke(state)
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _tool_routing(self, state: CodingAgentState) -> Literal["tool_node", "end"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            self.logger.info(f"  hit MAX_ITERATIONS ({self.MAX_ITERATIONS}), stopping")
            return "end"
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "end"
    
    def _evaluation_node(self):
        pass
    
    def _evaluation_routing(self, state: CodingAgentState):
        if state["eval_passed"]:
            return "end"
        return "reasoning_node"
    
    def _get_changed_files(self) -> list[str]:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=GITOPS_REPO_PATH,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            self.logger.warning(f"git diff failed: {result.stderr}")
            return []
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]

    def _get_diff_content(self) -> str:
        result = subprocess.run(
            ["git", "diff"],
            cwd=GITOPS_REPO_PATH,
            capture_output=True,
            text=True,
        )
        return result.stdout

    @staticmethod
    def _preview(text, limit=300):
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, initial_state: CodingAgentState):
        return self.graph.invoke(initial_state)


if __name__ == "__main__":
    model = ChatOpenRouter(
        model="qwen/qwen3-coder-next",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY")
    )
    tools = [list_files_in_directory, read_file_content, grep, find, edit_file, write_file]

    coding_agent = CodingAgent(model, tools)

    task = (
        "change the backend pod port to 6000 from 5678"
    )
    initial_state: CodingAgentState = {
        "messages": [HumanMessage(content=task)],
        "test_result": "",
        "iteration_count": 0
    }
    result = coding_agent.invoke(initial_state)
    print(result["messages"][-1].content)