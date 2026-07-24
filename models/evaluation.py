from typing import Literal

from pydantic import BaseModel, Field


class EvaluationVerdict(BaseModel):
    verdict: Literal["conclusive", "reformulate", "exhausted"] = Field(
        description=(
            "conclusive = the evidence supports a confident root cause -- either confirming the "
            "current hypothesis, or a different specific mechanism the investigation found "
            "concrete evidence for while ruling the current hypothesis out; "
            "reformulate = the current hypothesis was ruled out and no confident alternative "
            "conclusion was reached -- a different hypothesis is worth trying next; "
            "exhausted = no confident conclusion and no new hypothesis worth pursuing."
        )
    )
    reasoning: str = Field(description="Brief justification for the verdict.")
    requires_remediation: bool | None = Field(
        default=None,
        description=(
            "Set only when verdict is 'conclusive'. True if the root cause is fixable via a "
            "Kubernetes config/manifest change; False if it is an application-level bug or an "
            "informational finding with nothing to fix."
        ),
    )
    next_hypothesis: str | None = Field(
        default=None, description="Set only when verdict is 'reformulate': the next hypothesis to investigate."
    )
    why_ruled_out: str | None = Field(
        default=None, description="Set only when verdict is 'reformulate': why the current hypothesis was ruled out."
    )
