from pydantic import BaseModel


class EvalResult(BaseModel):
    correct: bool
    reasoning: str
    issues: list[str] = []