from agents.remediation_agent import RemediationAgent
from models import RemediationPlan, RemediationStep
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage


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


def _make_agent():
    raw_llm = MagicMock()
    bound_llm = MagicMock()
    raw_llm.bind_tools.return_value = bound_llm
    llm_judge = MagicMock()
    agent = RemediationAgent(llm=raw_llm, tools=[], llm_judge=llm_judge)
    return agent, bound_llm


# ------------------------------------------------------------------
# _steps_completed_from_files — the mechanical, per-step content check
# ------------------------------------------------------------------

def test_steps_completed_requires_file_to_be_in_changed_set(tmp_path):
    (tmp_path / "deployment.yaml").write_text("resources:\n    limits:\n        memory: 256Mi\n")
    plan = RemediationPlan(
        summary="s",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="deployment.yaml",
                description="d",
                new_content="resources:\n    limits:\n        memory: 256Mi",
            ),
        ],
        planning_success=True,
    )

    # Content matches on disk, but the file isn't in the changed set (e.g. it already
    # looked like this before any edit) -- must not be reported as completed.
    result = RemediationAgent._steps_completed_from_files(str(tmp_path), [], plan)

    assert result == []


def test_steps_completed_distinguishes_two_steps_sharing_the_same_file(tmp_path):
    (tmp_path / "deployment.yaml").write_text("resources:\n    limits:\n        memory: 256Mi\n")
    plan = RemediationPlan(
        summary="Add memory limits and fix image tag",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="deployment.yaml",
                description="Add a resources block",
                new_content="resources:\n    limits:\n        memory: 256Mi",
            ),
            RemediationStep(
                step_number=2,
                file_path="deployment.yaml",
                description="Pin the image tag",
                new_content="image: backend:v2",
            ),
        ],
        planning_success=True,
    )

    result = RemediationAgent._steps_completed_from_files(str(tmp_path), ["deployment.yaml"], plan)

    assert result == [1]


def test_steps_completed_empty_new_content_is_never_trivially_matched(tmp_path):
    (tmp_path / "deployment.yaml").write_text("anything at all\n")
    plan = RemediationPlan(
        summary="s",
        steps=[
            RemediationStep(step_number=1, file_path="deployment.yaml", description="d", new_content="   "),
        ],
        planning_success=True,
    )

    result = RemediationAgent._steps_completed_from_files(str(tmp_path), ["deployment.yaml"], plan)

    assert result == []


# ------------------------------------------------------------------
# _reasoning_node wiring
# ------------------------------------------------------------------

def test_reasoning_node_marks_step_completed_when_file_content_matches(tmp_path):
    agent, bound_llm = _make_agent()
    bound_llm.invoke.return_value = AIMessage(content="looks done")

    (tmp_path / "deployment.yaml").write_text("resources:\n    limits:\n        memory: 256Mi\n")

    plan = RemediationPlan(
        summary="Add memory limits",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="deployment.yaml",
                description="Add a resources block",
                new_content="resources:\n    limits:\n        memory: 256Mi",
            ),
        ],
        planning_success=True,
    )
    state = {"plan": plan, "repo_path": str(tmp_path), "messages": [], "iteration_count": 0}

    with patch("agents.remediation_agent.get_changed_files", return_value=["deployment.yaml"]):
        result = agent._reasoning_node(state)

    assert result["completed_steps"] == [1]
    system_prompt = bound_llm.invoke.call_args.args[0][0].content
    assert "Steps completed: [1]. Steps remaining: []." in system_prompt


def test_reasoning_node_reports_step_not_completed_before_any_change(tmp_path):
    agent, bound_llm = _make_agent()
    bound_llm.invoke.return_value = AIMessage(content="working on it")

    plan = RemediationPlan(
        summary="Add memory limits",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="deployment.yaml",
                description="Add a resources block",
                new_content="resources:\n    limits:\n        memory: 256Mi",
            ),
        ],
        planning_success=True,
    )
    state = {"plan": plan, "repo_path": str(tmp_path), "messages": [], "iteration_count": 0}

    with patch("agents.remediation_agent.get_changed_files", return_value=[]):
        result = agent._reasoning_node(state)

    assert result["completed_steps"] == []
    system_prompt = bound_llm.invoke.call_args.args[0][0].content
    assert "Steps completed: []. Steps remaining: [1]." in system_prompt


def test_reasoning_node_compacts_history_and_persists_the_removals(tmp_path):
    agent, bound_llm = _make_agent()
    bound_llm.invoke.return_value = AIMessage(content="continuing")
    agent.history_compactor.threshold_chars = 50
    agent.history_compactor.keep_recent_pairs = 1
    agent.history_compactor.llm.invoke.return_value = AIMessage(
        content="Read 1.yaml and 2.yaml, no edits made yet."
    )

    plan = RemediationPlan(summary="s", steps=[], planning_success=True)
    history = []
    for i in (1, 2, 3):
        history.append(
            AIMessage(
                content="",
                tool_calls=[{"name": "read_file_content", "args": {"file_path": f"{i}.yaml"}, "id": f"call-{i}"}],
                id=f"ai-{i}",
            )
        )
        history.append(ToolMessage(content=f"result {i}" * 20, tool_call_id=f"call-{i}", id=f"tool-{i}"))

    state = {"plan": plan, "repo_path": str(tmp_path), "messages": history, "iteration_count": 0}

    with patch("agents.remediation_agent.get_changed_files", return_value=[]):
        result = agent._reasoning_node(state)

    sent_messages = bound_llm.invoke.call_args.args[0]
    # system prompt + summary + the one kept pair (pair 3)
    assert len(sent_messages) == 4
    assert sent_messages[1].content.startswith("[Earlier progress summary]")
    assert sent_messages[2].id == "ai-3"
    assert sent_messages[3].id == "tool-3"

    returned_messages = result["messages"]
    removed_ids = {m.id for m in returned_messages if isinstance(m, RemoveMessage)}
    assert removed_ids == {"ai-1", "tool-1", "ai-2", "tool-2", "ai-3", "tool-3"}
    non_removals = [m for m in returned_messages if not isinstance(m, RemoveMessage)]
    assert non_removals[0].content.startswith("[Earlier progress summary]")
    assert [m.id for m in non_removals[1:3]] == [None, None]
    assert non_removals[-1] is bound_llm.invoke.return_value


def test_reasoning_node_does_not_trust_disk_state_right_after_a_failed_evaluation(tmp_path):
    agent, bound_llm = _make_agent()
    bound_llm.invoke.return_value = AIMessage(content="")

    # The file already matches the step's intended content on disk -- but the most
    # recent evaluation rejected the diff, so nothing should be reported as completed
    # until a fresh evaluation confirms it, regardless of what's mechanically present.
    (tmp_path / "deployment.yaml").write_text("resources:\n    limits:\n        memory: 256Mi\n")

    plan = RemediationPlan(
        summary="Add memory limits",
        steps=[
            RemediationStep(
                step_number=1,
                file_path="deployment.yaml",
                description="Add a resources block",
                new_content="resources:\n    limits:\n        memory: 256Mi",
            ),
        ],
        planning_success=True,
    )
    state = {
        "plan": plan,
        "repo_path": str(tmp_path),
        "messages": [],
        "iteration_count": 3,
        "eval_passed": False,
    }

    with patch("agents.remediation_agent.get_changed_files") as mock_get_changed_files:
        result = agent._reasoning_node(state)
        mock_get_changed_files.assert_not_called()

    assert result["completed_steps"] == []
    system_prompt = bound_llm.invoke.call_args.args[0][0].content
    assert "Steps completed: []. Steps remaining: [1]." in system_prompt
