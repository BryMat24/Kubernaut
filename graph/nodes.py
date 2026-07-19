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
        return {"diagnosis_result": result["messages"][-1].content}

    return diagnose_node


def human_approval_node(state: OrchestratorState) -> dict:
    decision = interrupt({"diagnosis": state["diagnosis_result"]})
    return {
        "approved": decision.get("approved", False),
        "task": decision.get("task") or state["diagnosis_result"],
    }


def approval_routing(state: OrchestratorState) -> Literal["remediate_node", "end"]:
    return "remediate_node" if state["approved"] else "end"


def make_remediate_node(remediation_agent: RemediationAgent):
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

    return remediate_node
