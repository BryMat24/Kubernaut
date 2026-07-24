from uuid import UUID

from pydantic import BaseModel

from models import RemediationPlan


class ChatCreateRequest(BaseModel):
    title: str
    repo_url: str


class DiagnoseRequest(BaseModel):
    query: str
    chat_id: UUID


class ApprovalDecision(BaseModel):
    approved: bool
    edited_plan: RemediationPlan | None = None


class MissingInfoAnswer(BaseModel):
    answer: str
