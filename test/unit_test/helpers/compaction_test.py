from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage

from agents.helpers import HistoryCompactor


def _pair(i: int) -> list:
    return [
        AIMessage(
            content="",
            tool_calls=[{"name": "read_file_content", "args": {"file_path": f"{i}.yaml"}, "id": f"call-{i}"}],
            id=f"ai-{i}",
        ),
        ToolMessage(content=f"result {i}" * 20, tool_call_id=f"call-{i}", id=f"tool-{i}"),
    ]


def test_compact_is_a_noop_under_the_threshold():
    llm = MagicMock()
    compactor = HistoryCompactor(llm, threshold_chars=10_000, keep_recent_pairs=8)
    messages = _pair(1) + _pair(2)

    history, state_edits = compactor.compact(messages)

    assert history == messages
    assert state_edits == []
    llm.invoke.assert_not_called()


def test_compact_summarizes_and_prunes_once_over_threshold():
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="Files read: 1.yaml, 2.yaml. No edits made yet.")
    compactor = HistoryCompactor(llm, threshold_chars=50, keep_recent_pairs=1)

    messages = _pair(1) + _pair(2) + _pair(3)  # 3 pairs, well over the 50-char threshold

    history, state_edits = compactor.compact(messages)

    # keep_recent_pairs=1 -> only the last pair (pair 3) is kept verbatim, plus the new summary
    assert len(history) == 3
    assert history[0].content == "[Earlier progress summary]\nFiles read: 1.yaml, 2.yaml. No edits made yet."
    assert history[1].id == "ai-3"
    assert history[2].id == "tool-3"

    removed_ids = {edit.id for edit in state_edits if isinstance(edit, RemoveMessage)}
    # both the summarized-away pairs AND the retained pair get removed by their original
    # id -- the retained pair is re-added with a fresh id right after, so it ends up
    # appended (in order) after the summary instead of staying in its old position.
    assert removed_ids == {"ai-1", "tool-1", "ai-2", "tool-2", "ai-3", "tool-3"}

    non_removals = [edit for edit in state_edits if not isinstance(edit, RemoveMessage)]
    # the summary must be the first appended entry, so it lands before the refreshed
    # retained pair once `add_messages` appends everything in this order
    assert non_removals[0] is history[0]
    refreshed = non_removals[1:]
    assert [m.id for m in refreshed] == [None, None]
    assert [m.content for m in refreshed] == [history[1].content, history[2].content]

    llm.invoke.assert_called_once()


def test_compact_keeps_pair_boundaries_intact():
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="summary")
    compactor = HistoryCompactor(llm, threshold_chars=50, keep_recent_pairs=2)

    messages = _pair(1) + _pair(2) + _pair(3) + _pair(4)

    history, _ = compactor.compact(messages)

    # keep_recent_pairs=2 -> pairs 3 and 4 kept verbatim; each pair's AIMessage (with the
    # tool call) must stay adjacent to its own ToolMessage, never split.
    assert [m.id for m in history[1:]] == ["ai-3", "tool-3", "ai-4", "tool-4"]


def test_summarize_prompt_does_not_request_a_completion_judgment():
    # Regression test: the summarizer prompt used to ask for "conclusions already
    # reached (e.g. 'step 2 is already correct, do not repeat it')" -- a RemediationAgent
    # step-completion framing that doesn't exist for other agents (e.g. DiagnosisAgent,
    # which has no "steps"). Given a transcript with nothing step-shaped to report, the
    # summarizer defaulted to a literal "No conclusions reached yet" closing line, even
    # when the same summary had just stated a full root cause -- misleading the agent
    # into believing nothing was found and looping past the point it already had an
    # answer. The prompt must not ask for this judgment at all.
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="summary")
    compactor = HistoryCompactor(llm, threshold_chars=50, keep_recent_pairs=1)

    messages = _pair(1) + _pair(2) + _pair(3)
    compactor.compact(messages)

    prompt = llm.invoke.call_args.args[0]
    assert "coding agent" not in prompt
    assert "step 2 is already correct" not in prompt
    assert "Do not add a closing judgment" in prompt


def test_compact_never_splits_a_parallel_tool_call_turn():
    # A turn with parallel tool calls produces 1 AIMessage + 2 ToolMessages (3 messages,
    # not 2), so a fixed message-count slice from the end can land inside it and split
    # the AIMessage from one of its sibling ToolMessages. Turn-based grouping must not.
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="summary")
    compactor = HistoryCompactor(llm, threshold_chars=50, keep_recent_pairs=2)

    parallel_turn = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "read_file_content", "args": {"file_path": "2a.yaml"}, "id": "call-2a"},
                {"name": "read_file_content", "args": {"file_path": "2b.yaml"}, "id": "call-2b"},
            ],
            id="ai-2",
        ),
        ToolMessage(content="result 2a" * 20, tool_call_id="call-2a", id="tool-2a"),
        ToolMessage(content="result 2b" * 20, tool_call_id="call-2b", id="tool-2b"),
    ]
    # 4 turns total: pair 0, pair 1, the parallel turn (turn "2"), pair 3.
    # keep_recent_pairs=2 -> the last 2 turns (parallel turn + pair 3) are kept verbatim.
    messages = _pair(0) + _pair(1) + parallel_turn + _pair(3)

    history, state_edits = compactor.compact(messages)

    assert [m.id for m in history[1:]] == ["ai-2", "tool-2a", "tool-2b", "ai-3", "tool-3"]

    removed_ids = {edit.id for edit in state_edits if isinstance(edit, RemoveMessage)}
    parallel_ids = {"ai-2", "tool-2a", "tool-2b"}
    # the parallel turn's AIMessage and both its ToolMessages must be removed together
    # as a whole group (they're part of `recent`, removed-then-refreshed so they
    # re-append after the summary) -- never split across the two buckets.
    assert parallel_ids <= removed_ids
    assert removed_ids == {"ai-0", "tool-0", "ai-1", "tool-1", "ai-2", "tool-2a", "tool-2b", "ai-3", "tool-3"}

    non_removals = [edit for edit in state_edits if not isinstance(edit, RemoveMessage)]
    assert non_removals[0].content.startswith("[Earlier progress summary]")
    refreshed = non_removals[1:]
    assert [m.id for m in refreshed] == [None] * 5
    assert [m.content for m in refreshed] == [m.content for m in history[1:]]
