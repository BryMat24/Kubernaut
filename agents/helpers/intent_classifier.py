from langchain_core.messages import SystemMessage

from models import IntentClassification


class IntentClassifier:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(IntentClassification, include_raw=True)

    async def classify(self, query: str) -> IntentClassification:
        prompt = f"""
            Classify the user's request below into exactly one intent:

            - diagnose: the user is reporting or asking about a problem/incident (something is
              broken, erroring, slow, not scaling, pending, etc.) and expects a live
              investigation of the actual cluster to find a root cause.
            - explain: an informational or conceptual question (e.g. "what does an HPA do",
              "how does this agent decide what to investigate", "what is a NetworkPolicy") that
              can be answered directly from general knowledge, with no cluster investigation
              needed.

            user query:
            {query}

            Respond with structured output: intent and reasoning. If genuinely ambiguous,
            prefer 'diagnose' -- treating an explain question as diagnose only costs a bit of
            extra investigation, while treating a real incident as explain would skip
            investigating it entirely.
        """

        response = await self.llm.ainvoke([SystemMessage(content=prompt)])
        parsed = response["parsed"]
        if parsed is None:
            return IntentClassification(
                intent="diagnose",
                reasoning=response["raw"].content or "structured output unavailable, defaulting to diagnose",
            )
        return parsed
