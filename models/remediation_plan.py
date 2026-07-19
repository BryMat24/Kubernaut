from pydantic import BaseModel, Field


class RemediationStep(BaseModel):
    step_number: int = Field(description="1-based order in which steps should be performed.")
    file_path: str = Field(description="Path to the file to change, relative to the repo root.")
    description: str = Field(description="What this step does and why, in plain language.")
    old_content: str | None = Field(
        default=None,
        description="Exact existing text this step replaces, copied verbatim from a file "
        "actually read during investigation. Null only if this step creates a brand-new file."
    )
    new_content: str = Field(
        description="The exact text old_content should become, or the full content of a new "
        "file if old_content is null."
    )


class RemediationPlan(BaseModel):
    summary: str = Field(description="Plain-language explanation of the overall fix.")
    steps: list[RemediationStep] = Field(
        description="Ordered, file-by-file steps to apply. Empty if planning_success is False."
    )
    planning_success: bool = Field(
        description="True if the repo was searched and a concrete, evidence-backed plan was "
        "produced. False if the relevant files couldn't be found or the investigation was "
        "inconclusive — this signals the plan isn't safe to hand to the remediation agent."
    )
