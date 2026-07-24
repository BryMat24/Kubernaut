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
    diagnosis_result: DiagnosisResult
    detected_intent: str
    # SRE phase machinery
    scope_summary: str
    current_hypothesis: str
    selected_playbook_id: str
    ruled_out: list[dict]
    hypothesis_count: int
    investigate_iterations: int
    last_verdict: str
    requires_remediation_hint: bool | None

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
    completed_steps: list[int]
    pr_url: str

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str
