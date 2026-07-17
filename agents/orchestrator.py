import asyncio
from typing import Literal, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

from agents import DiagnosisAgent, RemediationAgent
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools
from tools.file_tools import (
    list_files_in_directory,
    read_file_content,
    grep,
    find,
    edit_file,
    write_file,
)
from dotenv import load_dotenv
from utils import create_llm_model

load_dotenv()

llm = create_llm_model("qwen/qwen3-coder-next")
judge_llm = create_llm_model("qwen/qwen3-coder-next")


class OrchestratorState(TypedDict):
    query: str
    repo_url: str
    diagnosis_result: str
    approved: bool
    task: str
    pr_url: str
    eval_passed: bool
    eval_reasoning: str


async def init_diagnosis_agent() -> DiagnosisAgent:
    k8s_tools = await get_k8s_mcp_tools()
    promql_tools = await get_promql_mcp_tools()
    return DiagnosisAgent(llm, k8s_tools + promql_tools)


async def init_remediation_agent() -> RemediationAgent:
    file_tools = [
        find,
        grep,
        list_files_in_directory,
        read_file_content,
        edit_file,
        write_file,
    ]
    return RemediationAgent(llm, file_tools, judge_llm)


async def build_graph() -> CompiledStateGraph:
    diagnosis_agent = await init_diagnosis_agent()
    remediation_agent = await init_remediation_agent()

    async def diagnose_node(state: OrchestratorState) -> dict:
        result = await diagnosis_agent.ainvoke({
            "messages": [],
            "query": state["query"],
            "iteration_count": 0,
        })
        return {"diagnosis_result": result["messages"][-1].content}

    def human_approval_node(state: OrchestratorState) -> dict:
        decision = interrupt({"diagnosis": state["diagnosis_result"]})
        return {
            "approved": decision.get("approved", False),
            "task": decision.get("task") or state["diagnosis_result"],
        }

    def approval_routing(state: OrchestratorState) -> Literal["remediate_node", "end"]:
        return "remediate_node" if state["approved"] else "end"

    async def remediate_node(state: OrchestratorState) -> dict:
        result = await remediation_agent.ainvoke({
            "messages": [],
            "task": state["task"],
            "iteration_count": 0,
            "eval_passed": False,
            "eval_reasoning": "",
            "pr_url": "",
            "repo_url": state["repo_url"],
            "bare_path": "",
            "repo_path": "",
            "branch": "",
        })
        return {
            "pr_url": result.get("pr_url", ""),
            "eval_passed": result.get("eval_passed", False),
            "eval_reasoning": result.get("eval_reasoning", ""),
        }

    graph = StateGraph(state_schema=OrchestratorState)
    graph.add_node("diagnose_node", diagnose_node)
    graph.add_node("human_approval_node", human_approval_node)
    graph.add_node("remediate_node", remediate_node)

    graph.add_edge(START, "diagnose_node")
    graph.add_edge("diagnose_node", "human_approval_node")
    graph.add_conditional_edges(
        "human_approval_node",
        approval_routing,
        {"remediate_node": "remediate_node", "end": END},
    )
    graph.add_edge("remediate_node", END)

    return graph.compile(checkpointer=MemorySaver())


async def main() -> None:
    graph = await build_graph()
    config = {"configurable": {"thread_id": "demo-1"}}

    result = await graph.ainvoke({
        "query": "Why my application in dev namespace stopped working",
        "repo_url": "https://github.com/example-org/example-gitops-repo",
    }, config=config)

    interrupt_info = result["__interrupt__"][0].value
    print("=== diagnosis ===")
    print(interrupt_info["diagnosis"])

    approve = input("\nApprove remediation? [y/N] ").strip().lower() == "y"
    result = await graph.ainvoke(Command(resume={"approved": approve}), config=config)

    if approve:
        print("\n=== remediation ===")
        print(f"pr_url: {result.get('pr_url') or '(no PR opened)'}")
        print(f"eval_passed: {result.get('eval_passed')}")
        print(f"eval_reasoning: {result.get('eval_reasoning')}")
    else:
        print("\nRemediation not approved, diagnosis only.")


if __name__ == "__main__":
    asyncio.run(main())
