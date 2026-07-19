from pydantic import BaseModel

from models import RemediationPlan


class DiagnoseRequest(BaseModel):
    query: str
    repo_url: str


class ApprovalDecision(BaseModel):
    approved: bool
    edited_plan: RemediationPlan | None = None
