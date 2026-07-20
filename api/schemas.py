from uuid import UUID

from pydantic import BaseModel

from models import RemediationPlan


class DiagnoseRequest(BaseModel):
    query: str
    repo_url: str
    chat_id: UUID | None = None


class ApprovalDecision(BaseModel):
    approved: bool
    edited_plan: RemediationPlan | None = None