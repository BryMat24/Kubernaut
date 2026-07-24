from pydantic import BaseModel, Field


class RemediationStep(BaseModel):
    step_number: int = Field(description="1-based order in which steps should be performed.")
    file_path: str = Field(description="Path to the file to change, relative to the repo root.")
    description: str = Field(description="What this step does and why, in plain language.")
    new_content: str = Field(
        description="The new changes that should be made on a file"
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
    missing_information: str | None = Field(
        default=None,
        description=(
            "Set ONLY when the plan is otherwise fully investigated and correct except for one "
            "concrete value that cannot be determined from repository evidence (e.g. a valid "
            "image tag, an external IP, a secret's real value) -- state the exact question to "
            "ask a human, e.g. 'What image tag should be used for brymat24/test-cache-app?'. "
            "When set, planning_success must be False and steps must be empty -- this is not an "
            "executable plan yet."
        ),
    )
