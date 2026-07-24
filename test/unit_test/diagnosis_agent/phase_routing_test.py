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


import asyncio


def test_evaluate_node_synthesizes_stop_messages_for_dangling_tool_calls():
    agent = _agent()
    agent.logger = MagicMock()
    agent.playbooks = MagicMock()
    agent.playbooks.get.return_value = MagicMock(body="playbook body")

    captured = {}

    async def fake_evaluate(query, hypothesis, playbook_body, messages, hypothesis_count, max_hypotheses):
        captured["messages"] = messages
        verdict = MagicMock()
        verdict.verdict = "exhausted"
        verdict.reasoning = "budget exhausted"
        verdict.requires_remediation = None
        verdict.why_ruled_out = None
        return verdict

    agent.evaluator = MagicMock()
    agent.evaluator.evaluate = fake_evaluate

    dangling_call = {"id": "call-1", "name": "get_events", "args": {}}
    state = {
        "query": "why is my pod crashing",
        "messages": [_ai([dangling_call])],
        "selected_playbook_id": "generic",
        "current_hypothesis": "h",
        "hypothesis_count": 1,
        "ruled_out": [],
    }

    result = asyncio.run(agent._evaluate_node(state))

    # the evaluator's LLM call must never see a dangling tool_call with no response
    sent_messages = captured["messages"]
    assert len(sent_messages) == 2
    assert sent_messages[-1].tool_call_id == "call-1"
    assert sent_messages[-1].name == "get_events"

    # the synthetic ToolMessage must also be persisted into the state update
    assert len(result["messages"]) == 1
    assert result["messages"][0].tool_call_id == "call-1"


def test_evaluate_node_does_not_synthesize_messages_when_no_pending_calls():
    agent = _agent()
    agent.logger = MagicMock()
    agent.playbooks = MagicMock()
    agent.playbooks.get.return_value = MagicMock(body="playbook body")

    captured = {}

    async def fake_evaluate(query, hypothesis, playbook_body, messages, hypothesis_count, max_hypotheses):
        captured["messages"] = messages
        verdict = MagicMock()
        verdict.verdict = "conclusive"
        verdict.reasoning = "done"
        verdict.requires_remediation = True
        verdict.why_ruled_out = None
        return verdict

    agent.evaluator = MagicMock()
    agent.evaluator.evaluate = fake_evaluate

    state = {
        "query": "why is my pod crashing",
        "messages": [_ai([])],
        "selected_playbook_id": "generic",
        "current_hypothesis": "h",
        "hypothesis_count": 1,
        "ruled_out": [],
    }

    result = asyncio.run(agent._evaluate_node(state))

    assert len(captured["messages"]) == 1
    assert "messages" not in result
