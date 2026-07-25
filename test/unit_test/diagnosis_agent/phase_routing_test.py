import asyncio
from unittest.mock import AsyncMock, MagicMock

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


def test_intent_routing_goes_to_explain_when_explain_detected():
    agent = _agent()
    state = {"detected_intent": "explain"}
    assert agent._intent_routing(state) == "explain_node"


def test_intent_routing_defaults_to_scope_when_not_explain():
    agent = _agent()
    for intent in ("diagnose", "", None):
        assert agent._intent_routing({"detected_intent": intent}) == "_context_builder"


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


def test_evaluate_node_synthesizes_stop_messages_for_dangling_tool_calls():
    agent = _agent()
    agent.logger = MagicMock()

    captured = {}

    async def fake_evaluate(query, hypothesis, messages, hypothesis_count, max_hypotheses):
        captured["messages"] = messages
        verdict = MagicMock()
        verdict.verdict = "exhausted"
        verdict.reasoning = "budget exhausted"
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

    captured = {}

    async def fake_evaluate(query, hypothesis, messages, hypothesis_count, max_hypotheses):
        captured["messages"] = messages
        verdict = MagicMock()
        verdict.verdict = "conclusive"
        verdict.reasoning = "done"
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


def test_explain_node_answers_directly_when_no_tool_calls_needed():
    agent = _agent()
    agent.logger = MagicMock()
    agent.MAX_EXPLAIN_ITERATIONS = 5

    final_answer = _ai([])
    final_answer.content = "An HPA scales replicas based on observed metrics."

    agent.llm = MagicMock()
    agent.llm.ainvoke = AsyncMock(return_value=final_answer)
    agent.scope_tool_executor = MagicMock()
    agent.scope_tool_executor.ainvoke = AsyncMock()

    result = asyncio.run(agent._explain_node({"query": "what does an HPA do"}))

    assert result["messages"] == [final_answer]
    agent.scope_tool_executor.ainvoke.assert_not_called()


def test_explain_node_calls_tools_then_answers():
    agent = _agent()
    agent.logger = MagicMock()
    agent.MAX_EXPLAIN_ITERATIONS = 5

    tool_call_msg = _ai([{"id": "call-1", "name": "get_resource", "args": {"kind": "hpa"}}])
    final_answer = _ai([])
    final_answer.content = "The HPA sample-app-hpa targets 70% CPU utilization."
    tool_result_msg = MagicMock(name="tool_result")

    agent.llm = MagicMock()
    agent.llm.ainvoke = AsyncMock(side_effect=[tool_call_msg, final_answer])
    agent.scope_tool_executor = MagicMock()
    agent.scope_tool_executor.ainvoke = AsyncMock(return_value={"messages": [tool_result_msg]})

    result = asyncio.run(agent._explain_node({"query": "what does the HPA target"}))

    assert result["messages"] == [tool_call_msg, tool_result_msg, final_answer]
    agent.scope_tool_executor.ainvoke.assert_called_once()


def test_explain_node_forces_final_answer_when_budget_exhausted():
    agent = _agent()
    agent.logger = MagicMock()
    agent.MAX_EXPLAIN_ITERATIONS = 2

    always_calls_tools = _ai([{"id": "call-1", "name": "get_resource", "args": {}}])
    forced_final = _ai([])
    forced_final.content = "Based on what I found so far: ..."
    tool_result_msg = MagicMock(name="tool_result")

    agent.llm = MagicMock()
    # ainvoke is called MAX_EXPLAIN_ITERATIONS times inside the loop (always requesting a tool
    # call), then once more after the loop to force a final answer with no tool calls.
    agent.llm.ainvoke = AsyncMock(side_effect=[always_calls_tools, always_calls_tools, forced_final])
    agent.scope_tool_executor = MagicMock()
    agent.scope_tool_executor.ainvoke = AsyncMock(return_value={"messages": [tool_result_msg]})

    result = asyncio.run(agent._explain_node({"query": "q"}))

    assert result["messages"][-1] is forced_final
    assert agent.llm.ainvoke.call_count == 3
    assert agent.scope_tool_executor.ainvoke.call_count == 2
