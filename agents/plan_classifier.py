from langchain_core.messages import BaseMessage, SystemMessage

from models import DiagnosisResult, RemediationPlan


class PlanClassifier:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(RemediationPlan, include_raw=True)

    async def classify(self, diagnosis_result: DiagnosisResult, messages: list[BaseMessage]) -> RemediationPlan:
        root_cause_line = f"\nroot_cause: {diagnosis_result.root_cause}" if diagnosis_result.root_cause else ""
        finalize_prompt = f"""
            Based on the investigation above, produce the final structured remediation plan
            for the diagnosed issue below.

            diagnosed issue:
            summary: {diagnosis_result.summary}{root_cause_line}

            Respond with structured output: summary (plain-language explanation of the overall
            fix), steps (ordered list of step_number, file_path, description, old_content,
            new_content — one entry per file change; old_content must be copied verbatim from
            a file you actually read, and is null only for a brand-new file), and
            planning_success (true if you found the relevant file(s) and produced a concrete,
            evidence-backed plan; false if you could not find them or are not confident in
            the plan).
        """

        prompt_messages = messages + [SystemMessage(content=finalize_prompt)]
        response = await self.llm.ainvoke(prompt_messages)
        parsed = response["parsed"]
        if parsed is None:
            raw_content = response["raw"].content
            fallback_summary = raw_content or "Planner agent did not return a structured result."
            return RemediationPlan(
                summary=(
                    f"{fallback_summary}\n\nDiagnosed issue: {diagnosis_result.summary}{root_cause_line}"
                ),
                steps=[],
                planning_success=False,
            )
        return parsed
