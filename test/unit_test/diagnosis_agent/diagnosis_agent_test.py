from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage

from agents.diagnosis_agent import DiagnosisAgent


def _make_agent():
    raw_llm = MagicMock()
    bound_llm = MagicMock()
    raw_llm.bind_tools.return_value = bound_llm
    agent = DiagnosisAgent(llm=raw_llm, tools=[])
    return agent, bound_llm


def test_reasoning_node_sends_uncompacted_history_when_under_threshold():
    agent, bound_llm = _make_agent()
    bound_llm.invoke.return_value = AIMessage(content="done")

    history = [
        AIMessage(
            content="",
            tool_calls=[{"name": "list_namespaces", "args": {}, "id": "call-1"}],
            id="ai-1",
        ),
        ToolMessage(content="result", tool_call_id="call-1", id="tool-1"),
    ]
    state = {"query": "why is it broken", "messages": history, "iteration_count": 0}

    result = agent._reasoning_node(state)

    sent_messages = bound_llm.invoke.call_args.args[0]
    # system prompt + the 2 history messages, unchanged (well under the default threshold)
    assert len(sent_messages) == 3
    assert sent_messages[1].id == "ai-1"
    assert sent_messages[2].id == "tool-1"
    assert result["messages"] == [bound_llm.invoke.return_value]


def test_reasoning_node_compacts_history_and_persists_the_removals():
    agent, bound_llm = _make_agent()
    bound_llm.invoke.return_value = AIMessage(content="continuing")
    agent.history_compactor.threshold_chars = 50
    agent.history_compactor.keep_recent_pairs = 1
    agent.history_compactor.llm.invoke.return_value = AIMessage(
        content="Investigated namespaces and pods so far, no root cause yet."
    )

    history = []
    for i in (1, 2, 3):
        history.append(
            AIMessage(
                content="",
                tool_calls=[{"name": "list_resources", "args": {"kind": "pod"}, "id": f"call-{i}"}],
                id=f"ai-{i}",
            )
        )
        history.append(ToolMessage(content=f"result {i}" * 20, tool_call_id=f"call-{i}", id=f"tool-{i}"))

    state = {"query": "why is it broken", "messages": history, "iteration_count": 0}

    result = agent._reasoning_node(state)

    sent_messages = bound_llm.invoke.call_args.args[0]
    # system prompt + summary + the one kept turn (turn 3)
    assert len(sent_messages) == 4
    assert sent_messages[1].content.startswith("[Earlier progress summary]")
    assert sent_messages[2].id == "ai-3"
    assert sent_messages[3].id == "tool-3"

    returned_messages = result["messages"]
    removed_ids = {m.id for m in returned_messages if isinstance(m, RemoveMessage)}
    assert removed_ids == {"ai-1", "tool-1", "ai-2", "tool-2", "ai-3", "tool-3"}
    non_removals = [m for m in returned_messages if not isinstance(m, RemoveMessage)]
    assert non_removals[0].content.startswith("[Earlier progress summary]")
    assert non_removals[-1] is bound_llm.invoke.return_value
