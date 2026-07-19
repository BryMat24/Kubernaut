from pydantic import BaseModel, Field

class DiagnosisResult(BaseModel):
    summary: str = Field(description="Plain-language explanation of findings")
    root_cause: str | None = Field(default=None, description="Root cause if one was found")
    requires_remediation: bool = Field(
        description="True only if the cluster is in a state that needs a fix. "
        "False for informational queries, or queries where investigation found no issue."
    )
    diagnosis_success: bool = Field(
        description="True if the investigation completed and reached an evidence-backed "
        "conclusion. False if the investigation was inconclusive, incomplete, or you are not "
        "confident in the findings — this forces escalation to a human regardless of "
        "requires_remediation."
    )