from typing import Literal

from langgraph.types import interrupt
from agents import DiagnosisAgent, RemediationAgent
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


def require_remediation_routing_node(state: OrchestratorState) -> Literal["human_approval_node", "end"]:
    diagnosis_result = state["diagnosis_result"]
    if diagnosis_result.diagnosis_success and not diagnosis_result.requires_remediation:
        return "end"
    return "human_approval_node"


def human_approval_node(state: OrchestratorState) -> dict:
    decision = interrupt({"diagnosis": state["diagnosis_result"]})
    return {
        "approved": decision.get("approved", False),
    }


def approval_routing(state: OrchestratorState) -> Literal["remediation_agent", "end"]:
    return "remediation_agent" if state["approved"] else "end"


def make_remediate_node(remediation_agent: RemediationAgent):
    async def remediate_node(state: OrchestratorState) -> dict:
        result = await remediation_agent.ainvoke({
            "messages": [],
            "diagnosis_result": state["diagnosis_result"],
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
