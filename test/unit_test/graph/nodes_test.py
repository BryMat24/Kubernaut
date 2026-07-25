import operator
from unittest.mock import patch

from graph.nodes import (
    MAX_MISSING_INFO_ROUNDS,
    missing_info_node,
    planning_outcome_routing,
    require_remediation_routing_node,
)
from graph.state import OrchestratorState
from models import DiagnosisResult, RemediationPlan


def _diagnosis():
    return DiagnosisResult(
        summary="Image pull failure",
        root_cause="Invalid image tag",
        diagnosis_success=True,
    )


def test_require_remediation_routing_node_ends_when_diagnosis_unsuccessful():
    diagnosis = DiagnosisResult(summary="Inconclusive", diagnosis_success=False)
    assert require_remediation_routing_node({"diagnosis_result": diagnosis}) == "end"


def test_require_remediation_routing_node_ends_when_explain_intent():
    # requires_remediation defaults to True on the model, but DiagnosisAgent's finalize_node
    # deterministically sets it False for explain-mode results -- routing must honor that even
    # though diagnosis_success is True (an explain answer can be "successful" on its own terms).
    diagnosis = DiagnosisResult(summary="An HPA scales replicas based on observed metrics.", diagnosis_success=True, requires_remediation=False)
    assert require_remediation_routing_node({"diagnosis_result": diagnosis}) == "end"


def test_require_remediation_routing_node_goes_to_planner_when_diagnosed_and_remediable():
    diagnosis = DiagnosisResult(summary="Pod crashlooping", diagnosis_success=True, requires_remediation=True)
    assert require_remediation_routing_node({"diagnosis_result": diagnosis}) == "planner_agent"


def test_planning_outcome_routing_goes_to_missing_info_when_set():
    plan = RemediationPlan(
        summary="s", steps=[], planning_success=False,
        missing_information="What image tag should be used?",
    )
    state = {"plan": plan, "missing_info_rounds": 0}
    assert planning_outcome_routing(state) == "missing_info_node"


def test_planning_outcome_routing_ends_when_rounds_exhausted():
    plan = RemediationPlan(
        summary="s", steps=[], planning_success=False,
        missing_information="What image tag should be used?",
    )
    state = {"plan": plan, "missing_info_rounds": MAX_MISSING_INFO_ROUNDS}
    assert planning_outcome_routing(state) == "end"


def test_planning_outcome_routing_ends_when_planning_failed_with_no_question():
    plan = RemediationPlan(summary="Could not find the relevant file", steps=[], planning_success=False)
    state = {"plan": plan, "missing_info_rounds": 0}
    assert planning_outcome_routing(state) == "end"


def test_planning_outcome_routing_goes_to_approval_when_successful():
    plan = RemediationPlan(
        summary="s",
        steps=[],
        planning_success=True,
    )
    state = {"plan": plan, "missing_info_rounds": 0}
    assert planning_outcome_routing(state) == "human_approval_node"


def test_missing_info_node_interrupts_with_the_question_and_records_the_answer():
    plan = RemediationPlan(
        summary="s", steps=[], planning_success=False,
        missing_information="What image tag should be used for brymat24/test-cache-app?",
    )
    diagnosis = _diagnosis()
    state = {"plan": plan, "diagnosis_result": diagnosis, "missing_info_rounds": 0}

    with patch("graph.nodes.interrupt", return_value={"answer": "v3"}) as mock_interrupt:
        result = missing_info_node(state)

    mock_interrupt.assert_called_once_with({
        "type": "missing_information",
        "question": "What image tag should be used for brymat24/test-cache-app?",
        "diagnosis": diagnosis,
        "plan": plan,
    })
    assert result == {"human_provided_info": ["v3"], "missing_info_rounds": 1}


def test_missing_info_node_increments_rounds_from_existing_state():
    plan = RemediationPlan(
        summary="s", steps=[], planning_success=False, missing_information="q?",
    )
    state = {"plan": plan, "diagnosis_result": _diagnosis(), "missing_info_rounds": 1}

    with patch("graph.nodes.interrupt", return_value={"answer": "v3"}):
        result = missing_info_node(state)

    assert result["missing_info_rounds"] == 2


def test_missing_info_node_answers_accumulate_across_rounds_via_reducer():
    """human_provided_info is Annotated[list[str], operator.add] on OrchestratorState, so
    LangGraph merges successive missing_info_node returns by appending rather than replacing.
    Simulate two rounds' worth of node returns and apply the same reducer LangGraph would use,
    to prove round 1's answer survives into round 2 instead of being overwritten."""
    reducer = OrchestratorState.__annotations__["human_provided_info"].__metadata__[0]
    assert reducer is operator.add

    plan_round_1 = RemediationPlan(
        summary="s", steps=[], planning_success=False, missing_information="What namespace?",
    )
    state = {"plan": plan_round_1, "diagnosis_result": _diagnosis(), "missing_info_rounds": 0}
    with patch("graph.nodes.interrupt", return_value={"answer": "prod"}):
        round_1_result = missing_info_node(state)

    accumulated = reducer(state.get("human_provided_info", []), round_1_result["human_provided_info"])
    assert accumulated == ["prod"]

    plan_round_2 = RemediationPlan(
        summary="s", steps=[], planning_success=False, missing_information="What image tag?",
    )
    state = {
        "plan": plan_round_2,
        "diagnosis_result": _diagnosis(),
        "missing_info_rounds": round_1_result["missing_info_rounds"],
        "human_provided_info": accumulated,
    }
    with patch("graph.nodes.interrupt", return_value={"answer": "v3"}):
        round_2_result = missing_info_node(state)

    accumulated = reducer(state["human_provided_info"], round_2_result["human_provided_info"])
    assert accumulated == ["prod", "v3"]
