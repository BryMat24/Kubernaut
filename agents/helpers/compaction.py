from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage
from langsmith import traceable


class HistoryCompactor:
    """Summarizes old tool-call history once it grows past a size threshold, instead of
    dropping it outright -- keeps what's sent to the LLM bounded without losing the
    information older turns carried. Generic over any agent's `list[BaseMessage]`
    history (no knowledge of plans, steps, or repo paths), so any agent that resends
    its full message history every reasoning turn can reuse it.
    """

    def __init__(
        self,
        llm,
        threshold_chars: int = 24_000,
        keep_recent_pairs: int = 8,
    ) -> None:
        self.llm = llm
        self.threshold_chars = threshold_chars
        self.keep_recent_pairs = keep_recent_pairs

    def compact(self, messages: list[BaseMessage]) -> tuple[list[BaseMessage], list[BaseMessage]]:
        if self._char_count(messages) <= self.threshold_chars:
            return messages, []

        groups = self._group_by_turn(messages)
        if len(groups) <= self.keep_recent_pairs:
            return messages, []

        older_groups, recent_groups = groups[:-self.keep_recent_pairs], groups[-self.keep_recent_pairs:]
        older = [m for group in older_groups for m in group]
        recent = [m for group in recent_groups for m in group]

        summary_text = self._summarize(older)
        summary_message = AIMessage(content=f"[Earlier progress summary]\n{summary_text}")

        removals = [RemoveMessage(id=m.id) for m in older + recent if m.id is not None]
        refreshed_recent = [m.model_copy(update={"id": None}) for m in recent]
        state_edits = [*removals, summary_message, *refreshed_recent]
        return [summary_message] + recent, state_edits

    @staticmethod
    def _group_by_turn(messages: list[BaseMessage]) -> list[list[BaseMessage]]:
        groups: list[list[BaseMessage]] = []
        for message in messages:
            if isinstance(message, AIMessage) or not groups:
                groups.append([message])
            else:
                groups[-1].append(message)
        return groups

    @staticmethod
    def _char_count(messages: list[BaseMessage]) -> int:
        return sum(len(str(m.content)) for m in messages)

    @traceable(name="history_compaction", run_type="llm")
    def _summarize(self, messages: list[BaseMessage]) -> str:
        transcript = "\n".join(self._render(m) for m in messages)
        prompt = f"""
        Summarize the following tool-use transcript from an autonomous agent's session so
        far, so the agent can continue correctly without the raw transcript.

        Preserve every concrete fact a continuation would need -- this varies by agent, so
        include whichever of these actually appear in the transcript:
        - every file read, edited, or written, and the resulting/current content or the
          key change made to it
        - every piece of evidence gathered (tool outputs, observed state, error messages)
          and what it showed
        - every error or rejected action, and why it was rejected
        - any root cause, hypothesis, or decision already reached, stated exactly as found

        Do not add a closing judgment about whether the task is "done" or "concluded" --
        state only the facts gathered; the agent itself decides what they mean and whether
        more investigation is needed. Do not editorialize or add commentary beyond what's
        needed to continue correctly. Be concise, but do not drop any file path, value,
        root cause, or error message.

        Transcript:
        {transcript}
        """
        response = self.llm.invoke(prompt)
        return str(response.content)

    @staticmethod
    def _render(message: BaseMessage) -> str:
        role = message.__class__.__name__
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            calls = "; ".join(f"{c['name']}({c['args']})" for c in tool_calls)
            return f"{role}: {message.content} [tool_calls: {calls}]"
        return f"{role}: {message.content}"
