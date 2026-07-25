from unittest.mock import MagicMock

from agents.planner_agent import PlannerAgent
from models import DiagnosisResult


def _agent():
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    agent = PlannerAgent.__new__(PlannerAgent)
    agent.llm = llm
    agent.SYSTEM_PROMPT = "SYSTEM"
    return agent, llm


def test_reasoning_node_includes_human_provided_info_when_present():
    agent, llm = _agent()
    llm.invoke.return_value = MagicMock(tool_calls=[])

    diagnosis = DiagnosisResult(
        summary="Image pull failure on cache-deployment",
        root_cause="Invalid image tag",
        diagnosis_success=True,
    )
    state = {
        "diagnosis_result": diagnosis,
        "repo_path": "/tmp/repo",
        "messages": [],
        "iteration_count": 0,
        "human_provided_info": "Use tag v3, it was just published",
    }

    agent._reasoning_node(state)

    system_prompt = llm.invoke.call_args.args[0][0].content
    assert "Use tag v3, it was just published" in system_prompt


def test_reasoning_node_omits_human_provided_info_section_when_absent():
    agent, llm = _agent()
    llm.invoke.return_value = MagicMock(tool_calls=[])

    diagnosis = DiagnosisResult(
        summary="Image pull failure on cache-deployment",
        root_cause=None,
        diagnosis_success=True,
    )
    state = {
        "diagnosis_result": diagnosis,
        "repo_path": "/tmp/repo",
        "messages": [],
        "iteration_count": 0,
    }

    agent._reasoning_node(state)

    system_prompt = llm.invoke.call_args.args[0][0].content
    assert "human_provided_info" not in system_prompt.lower().replace("_", " ").replace(" ", "")
