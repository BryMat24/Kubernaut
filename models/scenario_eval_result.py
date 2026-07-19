from pydantic import BaseModel

class ScenarioEvalResult(BaseModel):
    correct: bool
    reasoning: str
    missing_evidence: list[str] = []