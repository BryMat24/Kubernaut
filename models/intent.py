from typing import Literal

from pydantic import BaseModel, Field


class IntentClassification(BaseModel):
    intent: Literal["diagnose", "explain"] = Field(
        description=(
            "diagnose = the user is reporting or asking about a problem/incident that needs "
            "live cluster investigation; explain = an informational/conceptual question that "
            "can be answered directly, with no investigation needed."
        )
    )
    reasoning: str = Field(description="Brief justification for the classification.")
