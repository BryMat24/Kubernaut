from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from dotenv import load_dotenv
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools, get_loki_mcp_tools
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
    make_planner_node,
    human_approval_node,
    require_remediation_routing_node,
    approval_routing,
    make_remediate_node,
)
from graph.state import OrchestratorState
from agents import DiagnosisAgent, PlannerAgent, RemediationAgent
from utils import create_llm_model

# diangosis agent
diagnosis_llm = create_llm_model("openai/gpt-5.4-mini")

# planner agent
planner_llm = create_llm_model("openai/gpt-5.4-mini")

# remediation agent
remediation_llm = create_llm_model("qwen/qwen3-coder-next")
judge_llm = create_llm_model("openai/gpt-5.4-mini")

# multi use across agents
utility_llm = create_llm_model("openai/gpt-5.4-nano")


load_dotenv()




async def init_diagnosis_agent() -> DiagnosisAgent:
    k8s_tools = await get_k8s_mcp_tools()
    promql_tools = await get_promql_mcp_tools()
    loki_tools = await get_loki_mcp_tools()
    all_tools = k8s_tools + promql_tools + loki_tools

    SCOPE_TOOL_NAMES = {
        "list_namespaces",
        "list_resources",
        "get_events",
        "top_pods",
        "top_nodes",
        "rollout_status",
        "application_health",
        "error_rate",
        "cpu_saturation",
        "error_logs"
    }

    scope_tools = [
        tool
        for tool in all_tools
        if tool.name in SCOPE_TOOL_NAMES
    ]

    return DiagnosisAgent(
        llm=diagnosis_llm,
        investigate_tools=all_tools,
        scope_tools=scope_tools,
        utility_llm=utility_llm
    )


async def init_planner_agent() -> PlannerAgent:
    file_tools = [
        find,
        grep,
        list_files_in_directory,
        read_file_content,
    ]
    return PlannerAgent(planner_llm, file_tools, utility_llm)


async def init_remediation_agent() -> RemediationAgent:
    file_tools = [
        find,
        grep,
        list_files_in_directory,
        read_file_content,
        edit_file,
        write_file,
    ]
    return RemediationAgent(remediation_llm, file_tools, judge_llm, compactor_llm=utility_llm)


async def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    diagnosis_agent = await init_diagnosis_agent()
    planner_agent = await init_planner_agent()
    remediation_agent = await init_remediation_agent()

    graph = StateGraph(state_schema=OrchestratorState)
    graph.add_node("diagnosis_agent", make_diagnose_node(diagnosis_agent))
    graph.add_node("planner_agent", make_planner_node(planner_agent))
    graph.add_node("human_approval_node", human_approval_node)
    graph.add_node("remediation_agent", make_remediate_node(remediation_agent))

    graph.add_edge(START, "diagnosis_agent")
    graph.add_conditional_edges(
        "diagnosis_agent",
        require_remediation_routing_node,
        {"planner_agent": "planner_agent", "end": END},
    )
    graph.add_edge("planner_agent", "human_approval_node")
    graph.add_conditional_edges(
        "human_approval_node",
        approval_routing,
        {"remediation_agent": "remediation_agent", "end": END},
    )
    graph.add_edge("remediation_agent", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())