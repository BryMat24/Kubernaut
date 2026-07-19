from typing import TypedDict

from langgraph.graph import MessagesState
from models import DiagnosisResult, RemediationPlan

class OrchestratorState(TypedDict):
    query: str
    repo_url: str
    diagnosis_result: DiagnosisResult
    plan: RemediationPlan
    approved: bool
    pr_url: str
    eval_passed: bool
    eval_reasoning: str

class DiagnosisAgentState(MessagesState):
    query: str
    iteration_count: int
    diagnosis_result: DiagnosisResult

class PlannerAgentState(MessagesState):
    diagnosis_result: DiagnosisResult  # provided by caller
    iteration_count: int
    plan: RemediationPlan

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str

class RemediationAgentState(MessagesState):
    plan: RemediationPlan  # provided by caller
    iteration_count: int
    eval_passed: bool
    eval_reasoning: str
    pr_url: str

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str
