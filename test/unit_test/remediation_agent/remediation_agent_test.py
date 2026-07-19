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
                new_content="requests:\n    cpu: 250m",
            ),
        ],
        planning_success=True,
    )

    description = RemediationAgent._task_description(plan)

    assert "Fix missing CPU request on the backend deployment" in description
    assert "1. apps/backend/deployment.yaml: Add a CPU request so the HPA can compute utilization" in description
    assert "new_content:\nrequests:\n    cpu: 250m" in description
    assert "old_content:" not in description


def test_task_description_multiple_steps_are_all_included():
    plan = RemediationPlan(
        summary="Add a missing NetworkPolicy and label its target namespace",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="apps/backend/network-policy.yaml",
                description="Create the missing NetworkPolicy allowing ingress from frontend",
                new_content="apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n",
            ),
            RemediationStep(
                step_number=2,
                file_path="apps/backend/namespace.yaml",
                description="Add the team label required by the policy selector",
                new_content="  team: backend",
            ),
        ],
        planning_success=True,
    )

    description = RemediationAgent._task_description(plan)

    assert "1. apps/backend/network-policy.yaml: Create the missing NetworkPolicy allowing ingress from frontend" in description
    assert "2. apps/backend/namespace.yaml: Add the team label required by the policy selector" in description
    assert "new_content:\napiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\n" in description
    assert "new_content:\n  team: backend" in description


def test_task_description_empty_steps_renders_summary_only():
    plan = RemediationPlan(
        summary="Could not locate the relevant manifest for this issue",
        steps=[],
        planning_success=False,
    )

    description = RemediationAgent._task_description(plan)

    assert description == "Could not locate the relevant manifest for this issue"
