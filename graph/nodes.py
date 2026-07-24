from typing import Literal

from langgraph.config import get_stream_writer
from langgraph.types import interrupt
from agents import DiagnosisAgent, PlannerAgent, RemediationAgent
from graph.state import OrchestratorState

MAX_MISSING_INFO_ROUNDS = 2


def make_diagnose_node(diagnosis_agent: DiagnosisAgent):
    async def diagnose_node(state: OrchestratorState) -> dict:
        writer = get_stream_writer()
        writer({"phase": "diagnosis", "status": "started", "message": "Investigating the cluster…"})
        result = await diagnosis_agent.ainvoke({
            "messages": [],
            "query": state["query"],
            "detected_intent": "",
            "scope_summary": "",
            "current_hypothesis": "",
            "selected_playbook_id": "",
            "ruled_out": [],
            "hypothesis_count": 0,
            "investigate_iterations": 0,
            "last_verdict": "",
            "requires_remediation_hint": None,
        })
        diagnosis_result = result["diagnosis_result"]
        writer({"phase": "diagnosis", "status": "completed", "message": diagnosis_result.summary})
        return {"diagnosis_result": diagnosis_result}

    return diagnose_node


def require_remediation_routing_node(state: OrchestratorState) -> Literal["planner_agent", "end"]:
    diagnosis_result = state["diagnosis_result"]
    if not diagnosis_result.diagnosis_success or not diagnosis_result.requires_remediation:
        return "end"
    return "planner_agent"


def planning_outcome_routing(state: OrchestratorState) -> Literal["missing_info_node", "human_approval_node", "end"]:
    plan = state["plan"]
    if plan.missing_information and state.get("missing_info_rounds", 0) < MAX_MISSING_INFO_ROUNDS:
        return "missing_info_node"
    if not plan.planning_success:
        return "end"
    return "human_approval_node"


def missing_info_node(state: OrchestratorState) -> dict:
    plan = state["plan"]
    rounds = state.get("missing_info_rounds", 0) + 1
    answer_payload = interrupt({
        "type": "missing_information",
        "question": plan.missing_information,
        "diagnosis": state["diagnosis_result"],
        "plan": plan,
    })
    return {
        "human_provided_info": answer_payload.get("answer", ""),
        "missing_info_rounds": rounds,
    }


def make_planner_node(planner_agent: PlannerAgent):
    async def planner_node(state: OrchestratorState) -> dict:
        writer = get_stream_writer()
        writer({"phase": "planner", "status": "started", "message": "Building a remediation plan…"})
        result = await planner_agent.ainvoke({
            "messages": [],
            "diagnosis_result": state["diagnosis_result"],
            "human_provided_info": state.get("human_provided_info", ""),
            "iteration_count": 0,
            "repo_url": state["repo_url"],
            "bare_path": "",
            "repo_path": "",
            "branch": "",
        })
        plan = result["plan"]
        if plan.missing_information:
            completed_message = f"Need more information: {plan.missing_information}"
        else:
            completed_message = plan.summary
        writer({"phase": "planner", "status": "completed", "message": completed_message})
        return {"plan": plan}

    return planner_node


def human_approval_node(state: OrchestratorState) -> dict:
    decision = interrupt({
        "type": "approval",
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
        writer = get_stream_writer()
        writer({"phase": "remediation", "status": "started", "message": "Applying the fix…"})
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
        eval_passed = result.get("eval_passed", False)
        completed_message = "Fix applied." if eval_passed else "Fix did not pass evaluation."
        writer({"phase": "remediation", "status": "completed", "message": completed_message})
        return {
            "pr_url": result.get("pr_url", ""),
            "eval_passed": eval_passed,
            "eval_reasoning": result.get("eval_reasoning", ""),
        }

    return remediate_node
