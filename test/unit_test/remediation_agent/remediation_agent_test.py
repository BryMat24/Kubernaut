from agents.remediation_agent import RemediationAgent
from models import RemediationPlan, RemediationStep


def test_task_description_includes_summary_and_steps():
    plan = RemediationPlan(
        summary="Fix missing CPU request on the backend deployment",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="apps/backend/deployment.yaml",
                description="Add a CPU request so the HPA can compute utilization",
                old_content="resources:\n  limits:\n    cpu: 500m",
                new_content="resources:\n  requests:\n    cpu: 250m\n  limits:\n    cpu: 500m",
            ),
        ],
        planning_success=True,
    )

    description = RemediationAgent._task_description(plan)

    assert "Fix missing CPU request on the backend deployment" in description
    assert "1. apps/backend/deployment.yaml: Add a CPU request so the HPA can compute utilization" in description
    assert "old_content:\nresources:\n  limits:\n    cpu: 500m" in description
    assert "new_content:\nresources:\n  requests:\n    cpu: 250m\n  limits:\n    cpu: 500m" in description


def test_task_description_new_file_has_no_old_content_block():
    plan = RemediationPlan(
        summary="Add a missing NetworkPolicy",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="apps/backend/network-policy.yaml",
                description="Create the missing NetworkPolicy allowing ingress from frontend",
                old_content=None,
                new_content="apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n",
            ),
        ],
        planning_success=True,
    )

    description = RemediationAgent._task_description(plan)

    assert "old_content:" not in description
    assert "new_content:\napiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n" in description


def test_task_description_empty_steps_renders_summary_only():
    plan = RemediationPlan(
        summary="Could not locate the relevant manifest for this issue",
        steps=[],
        planning_success=False,
    )

    description = RemediationAgent._task_description(plan)

    assert description == "Could not locate the relevant manifest for this issue"
