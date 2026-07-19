from typing import TypedDict

from langgraph.graph import MessagesState


class OrchestratorState(TypedDict):
    query: str
    repo_url: str
    diagnosis_result: str
    approved: bool
    task: str
    pr_url: str
    eval_passed: bool
    eval_reasoning: str


class DiagnosisAgentState(MessagesState):
    query: str
    iteration_count: int


class RemediationAgentState(MessagesState):
    task: str
    iteration_count: int
    eval_passed: bool
    eval_reasoning: str
    pr_url: str

    repo_url: str
    bare_path: str
    repo_path: str
    branch: str
