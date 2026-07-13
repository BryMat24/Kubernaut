
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

            Use them as needed, for example:
            - `list_namespaces` to discover namespaces.
            - `list_resources` to find resources of a given kind.
            - `get_resource` / `describe_resource` to inspect configuration and runtime
            state (conditions, restart counts, scheduling, recent events).
            - `get_events` to understand why Kubernetes took or failed to take an action.
            - `get_pod_logs` / `get_previous_logs` for application behavior, especially
            CrashLoopBackOff or failed startups.
            - `top_pods` / `top_nodes` for resource exhaustion (OOMKilled, high CPU,
            MemoryPressure).
            - `rollout_status` to check whether a Deployment rollout has completed.

            As soon as you can answer the query, stop calling tools and reply directly.
            If you were asked to diagnose a problem, give a concise root cause, the
            evidence you found, and a suggested fix.
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