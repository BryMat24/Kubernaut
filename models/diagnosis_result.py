from pydantic import BaseModel, Field


class DiagnosisResult(BaseModel):
    summary: str = Field(description="Plain-language explanation of findings")
    root_cause: str | None = Field(default=None, description="Root cause if one was found")
    requires_remediation: bool = Field(
        default=True,
        description="Set deterministically by DiagnosisAgent, not the LLM: False when the "
        "detected intent was 'explain' (an informational answer, no live cluster diagnosis was "
        "attempted) -- routes straight to the user instead of the planner. True otherwise.",
    )
    diagnosis_success: bool = Field(
        description="True if the investigation completed and reached an evidence-backed "
        "conclusion. False if the investigation was inconclusive, incomplete, or you are not "
        "confident in the findings — this forces escalation to a human."
    )