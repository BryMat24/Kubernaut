import asyncio

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools
from tools.file_tools import (
    list_files_in_directory,
    read_file_content,
    grep,
    find,
    edit_file,
    write_file,
)
from graph.nodes import (
    make_diagnose_node,
    human_approval_node,
    approval_routing,
    make_remediate_node,
)
from graph.state import OrchestratorState
from agents import DiagnosisAgent, RemediationAgent
from utils import create_llm_model

llm = create_llm_model("qwen/qwen3-coder-next")
judge_llm = create_llm_model("qwen/qwen3-coder-next")


load_dotenv()

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

    graph = StateGraph(state_schema=OrchestratorState)
    graph.add_node("diagnose_node", make_diagnose_node(diagnosis_agent))
    graph.add_node("human_approval_node", human_approval_node)
    graph.add_node("remediate_node", make_remediate_node(remediation_agent))

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

    mermaid_syntax = graph.get_graph().draw_mermaid()
    print(mermaid_syntax)

    # 2. Render directly to PNG (uses mermaid.ink API by default)
    png_bytes = graph.get_graph().draw_mermaid_png()
    with open("graph.png", "wb") as f:
        f.write(png_bytes)

    # result = await graph.ainvoke({
    #     "query": "How many deployments in dev namespace",
    #     "repo_url": "https://github.com/example-org/example-gitops-repo",
    # }, config=config)

    # interrupt_info = result["__interrupt__"][0].value
    # print("=== diagnosis ===")
    # print(interrupt_info["diagnosis"])

    # approve = input("\nApprove remediation? [y/N] ").strip().lower() == "y"
    # result = await graph.ainvoke(Command(resume={"approved": approve}), config=config)

    # if approve:
    #     print("\n=== remediation ===")
    #     print(f"pr_url: {result.get('pr_url') or '(no PR opened)'}")
    #     print(f"eval_passed: {result.get('eval_passed')}")
    #     print(f"eval_reasoning: {result.get('eval_reasoning')}")
    # else:
    #     print("\nRemediation not approved, diagnosis only.")


if __name__ == "__main__":
    asyncio.run(main())
