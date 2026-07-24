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

            Respond with structured output:
            - summary: plain-language explanation of the overall fix.
            - steps: ordered list of step_number, file_path, description, new_content —
            one entry per file change. Leave this EMPTY if missing_information is set below.
                - new_content is ONLY the changed/inserted lines themselves, not the whole
                file but include surrounding related block. Copy any lines you keep
                unchanged verbatim from what you actually read — do not paraphrase or
                reformat existing content.
                - description must state exactly where the change goes, referencing a
                line or field that appears verbatim in the file you read (e.g. "insert
                after the `env:` block in the backend container spec") — this anchor is
                used to locate the edit automatically, so it must be precise and unique
                within the file.
                - For a brand-new file, set file_path to the new path and description to
                state clearly that this is a new file.
            - planning_success: true if you found the relevant file(s) and produced a
            concrete, evidence-backed plan; false if you could not find them, are not
            confident in the plan, or missing_information is set below.
            - missing_information: leave null in the common case. Set it ONLY when you found
            the exact file and fix mechanism, but the fix needs one concrete value that is
            fundamentally impossible to determine from this repository — not just "no
            established convention" (which still gets a best-effort value noted in
            description as today), but genuinely unknowable, like which valid image tag
            exists in a registry you cannot query, or a secret's real external value. When
            you set this, state the exact question to ask a human (e.g. "What image tag
            should be used for brymat24/test-cache-app?"), leave steps empty, and set
            planning_success to false.

            Every proposed value must be grounded in evidence you actually read in this
            repo (e.g. a sibling container's existing resource limits) wherever such
            evidence exists. If no repo convention exists for a value you're proposing,
            say so explicitly in description rather than inventing a number silently.
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
