import json

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def _signature(name: str, args: dict) -> tuple[str, str]:
    return name, json.dumps(args, sort_keys=True, default=str)


def find_repeated_calls(messages: list[BaseMessage]) -> dict[str, str]:
    if not messages:
        return {}
    last = messages[-1]
    pending_calls = getattr(last, "tool_calls", None) or []
    if not pending_calls:
        return {}

    signature_by_call_id: dict[str, tuple[str, str]] = {}
    last_result_by_signature: dict[tuple[str, str], str] = {}

    for msg in messages[:-1]:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                signature_by_call_id[call["id"]] = _signature(call["name"], call["args"])
        elif isinstance(msg, ToolMessage):
            sig = signature_by_call_id.get(msg.tool_call_id)
            if sig is not None:
                last_result_by_signature[sig] = msg.content

    repeats: dict[str, str] = {}
    for call in pending_calls:
        sig = _signature(call["name"], call["args"])
        if sig in last_result_by_signature:
            repeats[call["id"]] = last_result_by_signature[sig]
    return repeats
