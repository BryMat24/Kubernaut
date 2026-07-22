
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing import Any, Literal
import logging
from dotenv import load_dotenv
from models import DiagnosisResult

from .helpers import Classifier, HistoryCompactor, find_repeated_calls
from graph.state import DiagnosisAgentState

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")

class DiagnosisAgent:
    def __init__(
        self,
        llm: BaseChatModel,
        tools: list[BaseTool],
        classifier_llm: BaseChatModel | None = None,
        compactor_llm: BaseChatModel | None = None,
    ) -> None:
        self.llm = llm.bind_tools(tools)
        self.tools = tools
        self.logger = logging.getLogger("diagnosisAgent")
        self.MAX_ITERATIONS = 30
        self.SYSTEM_PROMPT = f"""
            You are a Kubernetes and observability diagnosis agent.

            You are strictly read-only.
            Never modify cluster state, workloads, or metrics.

            Answer only using evidence obtained from the available tools.
            Never invent cluster state. If evidence is insufficient, explicitly state what is missing.

            Available tools:
            {", ".join(t.name for t in tools)}

            General rules
            -------------
            - Answer only what the user asked.
            - Use the minimum number of tool calls necessary.
            - Stop investigating once the user's question has been answered or a root cause is supported by direct evidence.
            - Do not perform a full cluster investigation unless the user explicitly requests one.
            - Prefer confirming hypotheses with evidence instead of guessing.

            Loop prevention
            ----------------
            - Never call the same tool with the same arguments twice. If a tool result is already
              in the conversation, reuse it instead of re-fetching it.
            - A tool error (invalid argument, unsupported kind, etc.) is not a reason to retry the
              same call unchanged. Read the error, adjust your approach, or move on.
            - Treat an explicit failure string returned by any tool -- e.g. "Forbidden", "OOMKilled",
              "CrashLoopBackOff", "ImagePullBackOff", "Evicted", "FailedScheduling",
              "FailedGetResourceMetric" -- as direct, sufficient evidence of the failure mechanism.
              Once you see one, stop gathering further confirmation of it and move straight to
              answering. Do not keep checking unrelated metrics, services, or resources "to be
              thorough" once the mechanism is already named in evidence you've collected.

            Investigation strategy
            ----------------------

            Resource discovery
            - If the user already specifies the resource name, call get_resource or describe_resource directly when appropriate.
            - Otherwise, first call list_namespaces or list_resources to identify the target resource.
            - Never call describe_resource or get_resource before the target resource has been identified.

            Configuration questions
            - For questions about configuration (image, env vars, resource requests/limits, labels, selectors, volumes, replicas, etc.), call get_resource.
            - Only continue investigating if the configuration suggests a problem.

            Deployment or rollout issues
            - Call describe_resource on the Deployment first.
            - If unavailable replicas or rollout failures are found, identify the affected Pods using list_resources.
            - Call describe_resource on the affected Pod before retrieving logs.
            - Retrieve logs only if describe_resource indicates they are needed.

            Pod failures
            - Call describe_resource on the Pod first.
            - If the container is running, call get_pod_logs.
            - If the container has restarted, call get_previous_logs.

            Service connectivity
            - Call check_service_connectivity first.
            - If no ready endpoints exist, investigate the backing workload (Deployment/Pod).
            - If endpoints exist, do not assume the Service is healthy; continue only if more evidence is required.

            Scheduling or Pending Pods
            - Call describe_resource on the Pod first.
            - If scheduling failures reference node conditions, call get_node_conditions.
            - If the events indicate ResourceQuota, LimitRange, PVC, PV, or NetworkPolicy issues, retrieve those resources directly.

            Node issues
            - Call get_node_conditions before top_nodes.
            - Use top_nodes only to support evidence about resource utilization.

            Permission or RBAC errors
            - If any tool call, or the workload's own logs, returns a "Forbidden" / "cannot <verb>
              resource <resource>" API error, that error text already names the ServiceAccount (or
              user) and the denied verb/resource -- this is itself the root cause.
            - Do not enumerate ClusterRoles, ClusterRoleBindings, Roles, or RoleBindings one kind at
              a time looking for a match. There is no reliable stopping point in that search (the
              cluster has many pre-existing system roles), and the Forbidden error already tells
              you what's missing without it.
            - There is no dedicated tool for ServiceAccount; get_resource does not support it as a
              kind. Don't retry that call with a different kind hoping it works -- the Forbidden
              error text you already have is enough to answer.

            Metrics-driven investigations
            - Use metrics to identify the affected workload.
            - Then verify the Kubernetes resource with describe_resource, get_resource, or get_events.
            - Never conclude solely from metrics when Kubernetes evidence can confirm the cause.
            - HPA showing ScalingActive=False with reason FailedGetResourceMetric is usually NOT a
              metrics-server outage. Before concluding metrics-server is unavailable or misconfigured,
              check get_resource on the HPA's scale target (Deployment) for a missing
              resources.requests entry for that metric (e.g. no cpu request means CPU utilization
              can never be computed) -- this is the far more common cause. Only blame metrics-server
              itself if you have separate evidence it's actually broken (e.g. top_pods/top_nodes
              also failing).

            Cross-validation
            - Correlate evidence before eliminating a hypothesis.
            - Examples:
            - A completed rollout does not prove the application is healthy.
            - Ready Pods do not guarantee successful requests.
            - Service endpoints do not guarantee network connectivity.
            - Healthy metrics do not guarantee correct configuration.
            - If two tools disagree, investigate the discrepancy before concluding.

            Stopping criteria
            -----------------
            Stop as soon as one of the following is true:
            - The user's question has been answered.
            - A root cause is supported by direct evidence.
            - Additional tool calls are unlikely to increase confidence.

            Once a stopping criterion is met, answer immediately in that same turn. Do not spend
            further tool calls re-confirming a conclusion you can already support, checking
            adjacent-but-unrelated resources, or verifying that everything else looks fine -- that
            is how a correct early finding turns into a needlessly long investigation.

            If no conclusion can be reached, explain exactly what evidence is missing instead of guessing.

            Response format
            ---------------
            For investigations, provide:
            1. Evidence
            2. Root cause (or most likely cause)
            3. Reasoning

            For simple factual questions, answer directly without unnecessary sections.
        """
        self.classifier = Classifier(classifier_llm or llm)
        self.history_compactor = HistoryCompactor(compactor_llm or llm)
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
        messages = state["messages"]
        last_message = messages[-1]
        all_calls = last_message.tool_calls
        repeats = find_repeated_calls(messages)

        fresh_messages: list[Any] = []
        fresh_calls = [call for call in all_calls if call["id"] not in repeats]
        if fresh_calls:
            pruned_last = last_message.model_copy(update={"tool_calls": fresh_calls})
            fresh_state = {**state, "messages": [*messages[:-1], pruned_last]}
            fresh_result = await self._tool_executor.ainvoke(fresh_state)
            fresh_messages = fresh_result["messages"]

        repeat_messages = [
            ToolMessage(
                content=(
                    "[duplicate call suppressed] This exact call was already made earlier in "
                    f"this investigation and returned:\n{content}\n"
                    "Calling it again will not produce a different result -- use the evidence "
                    "you already have, or investigate something else."
                ),
                name=next(call["name"] for call in all_calls if call["id"] == call_id),
                tool_call_id=call_id,
            )
            for call_id, content in repeats.items()
        ]

        order = {call["id"]: i for i, call in enumerate(all_calls)}
        result = {"messages": sorted(fresh_messages + repeat_messages, key=lambda m: order[m.tool_call_id])}
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