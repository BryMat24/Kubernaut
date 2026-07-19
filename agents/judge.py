from models import EvalResult

class DiffEvaluator:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(EvalResult, include_raw=True)

    def evaluate(self, task: str, changes: str) -> EvalResult:
        eval_prompt = f"""
        You are reviewing a proposed fix to a GitOps repo.

        Original task: {task}

        Diff of changes made:
        {changes}

        Judge whether this diff:
        1. Correctly and completely addresses the task
        2. Doesn't touch anything unrelated to the task
        3. If you are unsure whether a specific changed file or field is within scope
        — for example, it could plausibly be a required companion edit (like a
        kustomization.yaml resources: update, or a coupled Helm value) rather than
        an unrelated change, but you can't verify that from the diff alone — do
        NOT flag it as an issue. Only mark `correct: false` on clear, confident
        violations. State any uncertainty in `reasoning` instead of failing the
        check over it.

        Respond with structured output: correct (bool), reasoning (str), issues (list[str]).
        """

        response = self.llm.invoke(eval_prompt)
        parsed = response["parsed"]
        if parsed is None:
            raw_content = response["raw"].content
            return EvalResult(
                correct=False,
                reasoning=f"Judge model did not call the structured-output tool. Raw response: {raw_content!r}",
                issues=["evaluation_failed"],
            )
        return parsed