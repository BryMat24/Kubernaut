from unittest.mock import MagicMock

from agents.diagnosis_agent import DiagnosisAgent


def _agent():
    # Build an agent without touching the network: patch the heavy collaborators.
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    agent = DiagnosisAgent.__new__(DiagnosisAgent)
    agent.MAX_INVESTIGATE_ITERATIONS = 8
    agent.MAX_HYPOTHESES = 3
    return agent


def _ai(tool_calls):
    m = MagicMock()
    m.tool_calls = tool_calls
    return m


def test_investigate_routing_goes_to_tool_when_calls_and_budget_left():
    agent = _agent()
    state = {"messages": [_ai([{"id": "1"}])], "investigate_iterations": 2}
    assert agent._investigate_routing(state) == "tool_node"


def test_investigate_routing_evaluates_when_budget_exhausted():
    agent = _agent()
    state = {"messages": [_ai([{"id": "1"}])], "investigate_iterations": 8}
    assert agent._investigate_routing(state) == "evaluate_node"


def test_investigate_routing_evaluates_when_no_tool_calls():
    agent = _agent()
    state = {"messages": [_ai([])], "investigate_iterations": 1}
    assert agent._investigate_routing(state) == "evaluate_node"


def test_evaluate_routing_reformulate_loops_when_budget_left():
    agent = _agent()
    state = {"last_verdict": "reformulate", "hypothesis_count": 1}
    assert agent._evaluate_routing(state) == "hypothesize_node"


def test_evaluate_routing_reformulate_finalizes_at_cap():
    agent = _agent()
    state = {"last_verdict": "reformulate", "hypothesis_count": 3}
    assert agent._evaluate_routing(state) == "finalize_node"


def test_evaluate_routing_conclusive_finalizes():
    agent = _agent()
    state = {"last_verdict": "conclusive", "hypothesis_count": 1}
    assert agent._evaluate_routing(state) == "finalize_node"
