from pydantic import BaseModel, Field


class HypothesisSelection(BaseModel):
    hypothesis: str = Field(description="One-sentence leading hypothesis for the root cause.")
    playbook_id: str = Field(
        description="The playbook_id to investigate under. Use 'generic' if no playbook fits."
    )
    reasoning: str = Field(description="Why this hypothesis and playbook were chosen.")
