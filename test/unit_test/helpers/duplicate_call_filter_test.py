from langchain_core.messages import AIMessage, ToolMessage

from agents.helpers import find_repeated_calls


def test_no_repeats_when_no_prior_calls():
    messages = [
        AIMessage(content="", tool_calls=[{"name": "list_resources", "args": {"kind": "pod"}, "id": "call-1"}]),
    ]
    assert find_repeated_calls(messages) == {}


def test_no_repeats_when_args_differ():
    messages = [
        AIMessage(content="", tool_calls=[{"name": "list_resources", "args": {"kind": "pod"}, "id": "call-1"}]),
        ToolMessage(content="[]", tool_call_id="call-1"),
        AIMessage(content="", tool_calls=[{"name": "list_resources", "args": {"kind": "service"}, "id": "call-2"}]),
    ]
    assert find_repeated_calls(messages) == {}


def test_detects_exact_repeat_of_name_and_args():
    messages = [
        AIMessage(content="", tool_calls=[{"name": "list_resources", "args": {"kind": "service"}, "id": "call-1"}]),
        ToolMessage(content="[]", tool_call_id="call-1"),
        AIMessage(content="", tool_calls=[{"name": "list_resources", "args": {"kind": "service"}, "id": "call-2"}]),
    ]
    assert find_repeated_calls(messages) == {"call-2": "[]"}


def test_repeat_detection_is_independent_of_arg_key_order():
    messages = [
        AIMessage(
            content="",
            tool_calls=[{"name": "get_resource", "args": {"kind": "pod", "name": "x"}, "id": "call-1"}],
        ),
        ToolMessage(content="manifest", tool_call_id="call-1"),
        AIMessage(
            content="",
            tool_calls=[{"name": "get_resource", "args": {"name": "x", "kind": "pod"}, "id": "call-2"}],
        ),
    ]
    assert find_repeated_calls(messages) == {"call-2": "manifest"}


def test_no_pending_tool_calls_returns_empty():
    messages = [AIMessage(content="final answer")]
    assert find_repeated_calls(messages) == {}


def test_multiple_pending_calls_only_repeat_flagged():
    messages = [
        AIMessage(content="", tool_calls=[{"name": "list_resources", "args": {"kind": "pod"}, "id": "call-1"}]),
        ToolMessage(content="[pod1]", tool_call_id="call-1"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "list_resources", "args": {"kind": "pod"}, "id": "call-2"},
                {"name": "list_resources", "args": {"kind": "service"}, "id": "call-3"},
            ],
        ),
    ]
    assert find_repeated_calls(messages) == {"call-2": "[pod1]"}
