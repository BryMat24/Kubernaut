
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing import Any, Literal
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")

class KubernetesAgentState(MessagesState):
    query: str
    iteration_count: int

class KubernetesAgent:
    def __init__(self, llm: BaseChatModel, tools: list[BaseTool]) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.logger = logging.getLogger("kubernetesAgent")
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = f"""
            You are a Kubernetes diagnosis agent. You are read-only: you never modify
            cluster state, only inspect it.

            Answer only the user's query below. Call the minimum number of tools needed
            to answer it, and no more — do not run a full investigation unless the query
            actually asks for one. A simple factual question (e.g. "what namespaces exist")
            needs exactly one tool call and then a direct answer; do not follow up with
            deployments, pods, events, logs, or rollout checks unless the query or the
            evidence you've gathered so far calls for it.

            Available tools — this is the complete list, there is no shell or kubectl
            access beyond these: {", ".join(t.name for t in tools)}.

            General investigation strategy:
            - Start broad (discovery/state tools) before narrow (logs/events for one
            resource) unless the query already names a specific resource.
            - Prefer tools that explain WHY something happened (get_events) over tools
            that only show WHAT the current state is (get_resource) when you're
            trying to find a root cause, not just confirm a symptom.
            - A tool returning "healthy"/"ready"/"complete" is not proof the underlying
            problem is solved — cross-check with a second, independent tool before
            concluding an area is not the cause. (e.g. ready endpoints doesn't
            guarantee network reachability; a completed rollout doesn't guarantee
            the new version is functionally correct.)
            - If evidence points to a policy or governance object (quota, limit range,
            network policy, RBAC) rather than the workload itself, verify by
            inspecting that object directly rather than assuming from indirect symptoms.
            - For node health specifically, use `get_node_conditions` to check conditions
            (Ready, DiskPressure, MemoryPressure, PIDPressure, NetworkUnavailable), taints,
            and capacity vs. allocatable — this is the dedicated tool for diagnosing
            scheduling or eviction problems caused by node state, and is more direct than
            `top_nodes` (which only shows CPU/memory usage numbers, not conditions or
            taints). `get_resource` and `describe_resource` also work on cluster-scoped
            kinds like Node — they are not restricted to namespaced workload objects.
            - Stop investigating once you have a root cause backed by direct evidence
            from at least one tool call — do not keep calling tools "to be thorough"
            once the cause is established.
            - If two tools give apparently conflicting information, investigate the
            discrepancy before concluding — don't silently pick whichever result
            came first.
        """
        self.graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=KubernetesAgentState)
        graph.add_node("reasoning_node", self._reasoning_node)
        graph.add_node("tool_node", self._tool_node)

        graph.add_edge(START, "reasoning_node")
        graph.add_conditional_edges(
            "reasoning_node",
            self._tool_routing,
            {"tool_node": "tool_node", "end": END},
        )
        graph.add_edge("tool_node", "reasoning_node")
        return graph.compile()

    def _reasoning_node(self, state: KubernetesAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nquery:\n{state['query']}"

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = self.llm.invoke(messages)

        if response.tool_calls:
            for call in response.tool_calls:
                self.logger.info(f"  agent -> {call['name']}({call['args']})")

        return {
            "messages": [response],
            "iteration_count": iteration,
        }
    
    async def _tool_node(self, state: KubernetesAgentState) -> dict[str, Any]:
        result = await self._tool_executor.ainvoke(state)
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _tool_routing(self, state: KubernetesAgentState) -> Literal["tool_node", "end"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            self.logger.info(f"  hit MAX_ITERATIONS ({self.MAX_ITERATIONS}), stopping")
            return "end"
        
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "end"
    
    @staticmethod
    def _preview(text: Any, limit: int = 300) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, state: KubernetesAgentState):
        return self.graph.invoke(state)

    async def ainvoke(self, state: KubernetesAgentState):
        return await self.graph.ainvoke(state)