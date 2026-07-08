from pydantic import BaseModel

class EvalResult(BaseModel):
    correct: bool
    reasoning: str
    issues: list[str] = []

class DiffEvaluator:
    def __init__(self, llm):
        self.llm = llm.with_structured_output(EvalResult)

    def evaluate(self, task: str, changes: str):
        eval_prompt = f"""
        You are reviewing a proposed fix to a GitOps repo.

        Original task: {task}

        Diff of changes made:
        {changes}

        Judge whether this diff:
        1. Correctly and completely addresses the task
        2. Doesn't touch anything unrelated to the task

        Respond with structured output: correct (bool), reasoning (str), issues (list[str]).
        """

        response = self.llm.invoke(eval_prompt)
        return response