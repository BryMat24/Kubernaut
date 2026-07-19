from langchain_core.messages import SystemMessage

from models import DiagnosisResult


class Classifier:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(DiagnosisResult, include_raw=True)

    async def classify(self, query: str, messages) -> DiagnosisResult:
        finalize_prompt = f"""
            Based on the investigation above, produce the final structured diagnosis for
            the query below.

            query:
            {query}

            Respond with structured output: summary (plain-language explanation of
            findings), root_cause (root cause if one was found, otherwise null),
            requires_remediation (true only if the cluster is in a state that needs a
            fix — false for informational queries, or queries where investigation found
            no issue), and diagnosis_success (true if the investigation completed and
            reached an evidence-backed conclusion; false if it was inconclusive or you
            are not confident in the findings).
        """

        prompt_messages = messages + [SystemMessage(content=finalize_prompt)]
        response = await self.llm.ainvoke(prompt_messages)
        parsed = response["parsed"]
        if parsed is None:
            raw_content = response["raw"].content
            return DiagnosisResult(
                summary=raw_content or "Diagnosis agent did not return a structured result.",
                root_cause=None,
                requires_remediation=False,
                diagnosis_success=False,
            )
        return parsed
