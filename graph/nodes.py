from typing import Literal

from langgraph.types import interrupt
from agents import DiagnosisAgent, PlannerAgent, RemediationAgent
from graph.state import OrchestratorState


def make_diagnose_node(diagnosis_agent: DiagnosisAgent):
    async def diagnose_node(state: OrchestratorState) -> dict:
        result = await diagnosis_agent.ainvoke({
            "messages": [],
            "query": state["query"],
            "iteration_count": 0,
        })
        return {"diagnosis_result": result["diagnosis_result"]}

    return diagnose_node


def require_remediation_routing_node(state: OrchestratorState) -> Literal["planner_agent", "end"]:
    diagnosis_result = state["diagnosis_result"]
    # End only on a confident diagnosis that found nothing to fix. Every other case —
    # a confirmed issue, or an inconclusive/incomplete investigation — goes to a human;
    # never fail-open by defaulting an uncertain result to "end".
    if diagnosis_result.diagnosis_success and not diagnosis_result.requires_remediation:
        return "end"
    return "planner_agent"


def make_planner_node(planner_agent: PlannerAgent):
    async def planner_node(state: OrchestratorState) -> dict:
        result = await planner_agent.ainvoke({
            "messages": [],
            "diagnosis_result": state["diagnosis_result"],
            "iteration_count": 0,
            "repo_url": state["repo_url"],
            "bare_path": "",
            "repo_path": "",
            "branch": "",
        })
        return {"plan": result["plan"]}

    return planner_node


def human_approval_node(state: OrchestratorState) -> dict:
    decision = interrupt({
        "diagnosis": state["diagnosis_result"],
        "plan": state["plan"],
    })
    update = {"approved": decision.get("approved", False)}
    edited_plan = decision.get("edited_plan")
    if edited_plan is not None:
        update["plan"] = edited_plan
    return update


def approval_routing(state: OrchestratorState) -> Literal["remediation_agent", "end"]:
    return "remediation_agent" if state["approved"] else "end"


def make_remediate_node(remediation_agent: RemediationAgent):
    async def remediate_node(state: OrchestratorState) -> dict:
        result = await remediation_agent.ainvoke({
            "messages": [],
            "plan": state["plan"],
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

    return remediate_node
