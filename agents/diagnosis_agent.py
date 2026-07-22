
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing import Any, Literal
import logging
from dotenv import load_dotenv
from models import DiagnosisResult

from .helpers import Classifier, HistoryCompactor
from graph.state import DiagnosisAgentState

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")

class DiagnosisAgent:
    def __init__(self, llm: BaseChatModel, tools: list[BaseTool]) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.logger = logging.getLogger("diagnosisAgent")
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = f"""
            You are a Kubernetes and observability diagnosis agent. You are read-only:
            you never modify cluster state or metrics, only inspect them.

            Answer only the user's query below. Call the minimum number of tools needed
            to answer it, and no more — do not run a full investigation unless the query
            actually asks for one. A simple factual question (e.g. "what namespaces exist")
            needs exactly one tool call and then a direct answer; do not follow up with
            deployments, pods, events, logs, or rollout checks unless the query or the
            evidence you've gathered so far calls for it.

            Available tools — this is the complete list, there is no direct cluster,
            shell, or metrics-backend access beyond these: {", ".join(t.name for t in tools)}.

            General investigation strategy:
            - Start broad (discovery/state tools) before narrow (logs/events for one
            resource) unless the query already names a specific resource.
            - Prefer tools that explain WHY something happened (get_events) over tools
            that only show WHAT the current state is (get_resource) when you're
            trying to find a root cause, not just confirm a symptom.
            - When both Kubernetes state tools and observability/metrics tools (e.g.
            error rate, latency, OOM indicators, restart counts, CPU throttling) are
            available, cross-correlate them: use metrics to detect an anomaly and
            narrow down the affected app/pod/namespace, then use Kubernetes tools to
            confirm the underlying resource state or event trail behind it. Don't
            conclude from metrics alone if a corresponding Kubernetes-side tool can
            confirm the cause.
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
            - For HorizontalPodAutoscaler (HPA) issues, inspect both the HPA's own
            status/conditions (get_resource or describe_resource, kind=hpa) AND the
            scale target's container resources.requests (get_resource or
            list_resources, kind=deployment). An HPA reporting an unknown or missing
            current metric is frequently NOT caused by the metrics-server being down
            — it is very often caused by the target container missing a
            resources.requests entry for the metric being scaled on (e.g. no CPU
            request means the HPA cannot compute a CPU utilization percentage, even
            though the cluster's metrics pipeline is otherwise healthy). Do not
            conclude the metrics-server or metrics pipeline is broken unless you have
            directly checked the target's resource requests and confirmed they are
            set. Conversely, an HPA condition of type ScalingLimited with reason
            TooManyReplicas, and currentReplicas equal to maxReplicas, means
            autoscaling IS working correctly and is intentionally capped by
            configuration — that is not a malfunction.

            Once the root cause is found, provide the explanation of the root cause
        """
        self.classifier = Classifier(llm)
        self.history_compactor = HistoryCompactor(llm)
        self.graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph:
        self._tool_executor = ToolNode(self.tools)

        graph = StateGraph(state_schema=DiagnosisAgentState)
        graph.add_node("reasoning_node", self._reasoning_node)
        graph.add_node("tool_node", self._tool_node)
        graph.add_node("finalize_node", self._finalize_node)

        graph.add_edge(START, "reasoning_node")
        graph.add_conditional_edges(
            "reasoning_node",
            self._tool_routing,
            {"tool_node": "tool_node", "finalize_node": "finalize_node"},
        )
        graph.add_edge("tool_node", "reasoning_node")
        graph.add_edge("finalize_node", END)
        return graph.compile()

    def _reasoning_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        iteration = state.get("iteration_count", 0) + 1
        self.logger.info(f"\n=== iteration {iteration}/{self.MAX_ITERATIONS}: reasoning ===")

        system_prompt = f"{self.SYSTEM_PROMPT}\n\nquery:\n{state['query']}"

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
        }
    
    async def _tool_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        result = await self._tool_executor.ainvoke(state)
        for msg in result["messages"]:
            self.logger.info(f"  {msg.name} <- {self._preview(msg.content)}")
        return result

    def _tool_routing(self, state: DiagnosisAgentState) -> Literal["tool_node", "finalize_node"]:
        if state.get("iteration_count", 0) >= self.MAX_ITERATIONS:
            self.logger.info(f"  hit MAX_ITERATIONS ({self.MAX_ITERATIONS}), stopping")
            return "finalize_node"

        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tool_node"
        return "finalize_node"

    async def _finalize_node(self, state: DiagnosisAgentState) -> dict[str, Any]:
        self.logger.info("\n=== finalize ===")
        hit_max_iterations = state.get("iteration_count", 0) >= self.MAX_ITERATIONS
        last_message = state["messages"][-1]
        incomplete = hit_max_iterations and bool(getattr(last_message, "tool_calls", None))

        if incomplete:
            self.logger.warning(
                f"  MAX_ITERATIONS ({self.MAX_ITERATIONS}) hit mid-investigation "
                f"— forcing escalation instead of trusting partial classification"
            )
            parsed = DiagnosisResult(
                summary=(
                    "Investigation did not complete within the iteration budget "
                    f"({self.MAX_ITERATIONS} steps). Findings so far are incomplete; "
                    "escalating to human review rather than risking a false negative."
                ),
                root_cause=None,
                requires_remediation=True,
                diagnosis_success=False # fail-safe: force human path, never fail-open
            )
            return {"diagnosis_result": parsed}
        else:
            parsed = await self.classifier.classify(state["query"], state["messages"])
            self.logger.info(f"  requires_remediation={parsed.requires_remediation} summary={self._preview(parsed.summary)}")
            return {"diagnosis_result": parsed}
    
    @staticmethod
    def _preview(text: Any, limit: int = 500) -> str:
        text = str(text)
        return text if len(text) <= limit else text[:limit] + "... [truncated]"

    def invoke(self, state: DiagnosisAgentState):
        return self.graph.invoke(state)

    async def ainvoke(self, state: DiagnosisAgentState):
        return await self.graph.ainvoke(state)