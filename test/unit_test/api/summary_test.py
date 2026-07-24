from api.summary import build_summary_message
from models import DiagnosisResult, RemediationPlan, RemediationStep


def _diagnosis(requires_remediation=False, root_cause=None):
    return DiagnosisResult(
        summary="Investigated the dev namespace and found 3 deployments running normally.",
        root_cause=root_cause,
        requires_remediation=requires_remediation,
        diagnosis_success=True,
    )


def _plan(planning_success=True):
    return RemediationPlan(
        summary="Add a missing CPU request to the backend deployment",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="apps/backend/deployment.yaml",
                description="Add a CPU request so the HPA can compute utilization",
                new_content="requests:\n    cpu: 250m",
            ),
        ],
        planning_success=planning_success,
    )


def test_no_remediation_case():
    diagnosis = _diagnosis()

    message = build_summary_message(diagnosis)

    assert message == "Investigated the dev namespace and found 3 deployments running normally."


def test_includes_root_cause_when_present():
    diagnosis = _diagnosis(requires_remediation=True, root_cause="Missing CPU request causes HPA to stall")

    message = build_summary_message(diagnosis)

    assert "Root cause: Missing CPU request causes HPA to stall" in message


def test_pending_approval_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = _plan()

    message = build_summary_message(diagnosis, plan, approved=None)

    assert "Proposed fix: Add a missing CPU request to the backend deployment" in message
    assert "(Awaiting your approval)" in message


def test_approved_with_pr_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = _plan()

    message = build_summary_message(diagnosis, plan, approved=True, pr_url="https://github.com/org/repo/pull/1")

    assert "Approved — PR opened: https://github.com/org/repo/pull/1" in message
    assert "Awaiting" not in message


def test_approved_without_pr_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = _plan()

    message = build_summary_message(diagnosis, plan, approved=True, pr_url=None)

    assert "Approved, but no PR was opened." in message


def test_missing_info_cap_exhausted_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = RemediationPlan(
        summary="Still missing the image tag after 2 rounds of questions; cannot proceed.",
        steps=[],
        planning_success=False,
        missing_information="What image tag should be used?",
    )

    message = build_summary_message(diagnosis, plan, approved=None)

    assert "Still missing the image tag after 2 rounds of questions; cannot proceed." in message
    assert "Proposed fix:" not in message
    assert "Awaiting your approval" not in message


def test_rejected_case():
    diagnosis = _diagnosis(requires_remediation=True)
    plan = _plan()

    message = build_summary_message(diagnosis, plan, approved=False)

    assert "Not approved — no changes were made." in message
