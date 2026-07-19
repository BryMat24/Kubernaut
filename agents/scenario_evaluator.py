from models import ScenarioEvalResult


class ScenarioEvaluator:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(ScenarioEvalResult, include_raw=True)

    def evaluate(self, expected: dict, actual_diagnosis: str) -> ScenarioEvalResult:
        eval_prompt = f"""
        You are grading a Kubernetes diagnostic agent's answer against an expected root cause.

        Expected root cause: {expected['expected_root_cause']}

        Evidence the answer should reference: {expected['key_evidence']}

        The answer should NOT conclude: {expected.get('should_not_conclude', [])}

        Agent's actual diagnosis:
        {actual_diagnosis}

        Judge whether the agent correctly identified the root cause, cited relevant
        evidence, and did not land on one of the listed wrong conclusions. List any
        expected evidence the answer failed to mention in missing_evidence.

        Respond with structured output: correct (bool), reasoning (str), missing_evidence (list[str]).
        """

        response = self.llm.invoke(eval_prompt)
        parsed = response["parsed"]
        if parsed is None:
            raw_content = response["raw"].content
            return ScenarioEvalResult(
                correct=False,
                reasoning=f"Judge model did not call the structured-output tool. Raw response: {raw_content!r}",
                missing_evidence=["evaluation_failed"],
            )
        return parsed