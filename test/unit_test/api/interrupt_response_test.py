from api.main import _interrupt_response
from models import DiagnosisResult, RemediationPlan


def _diagnosis():
    return DiagnosisResult(
        summary="Image pull failure on cache-deployment",
        root_cause="Invalid image tag",
        diagnosis_success=True,
    )


def test_interrupt_response_for_missing_information():
    payload = {
        "type": "missing_information",
        "question": "What image tag should be used for brymat24/test-cache-app?",
        "diagnosis": _diagnosis(),
        "plan": RemediationPlan(summary="s", steps=[], planning_success=False),
    }

    status, message, extra = _interrupt_response(payload)

    assert status == "pending_info"
    assert "What image tag should be used for brymat24/test-cache-app?" in message
    assert extra == {"question": "What image tag should be used for brymat24/test-cache-app?"}


def test_interrupt_response_for_approval():
    plan = RemediationPlan(summary="Update the image tag", steps=[], planning_success=True)
    payload = {"type": "approval", "diagnosis": _diagnosis(), "plan": plan}

    status, message, extra = _interrupt_response(payload)

    assert status == "pending_approval"
    assert "Image pull failure on cache-deployment" in message
    assert extra == {"plan": plan}
