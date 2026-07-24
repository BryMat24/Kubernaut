import pytest
from pydantic import ValidationError

from models import HypothesisSelection, EvaluationVerdict


def test_hypothesis_selection_roundtrip():
    h = HypothesisSelection(hypothesis="pod OOMKilled", playbook_id="generic", reasoning="mem spike")
    assert h.playbook_id == "generic"


def test_evaluation_verdict_accepts_valid_verdicts():
    for v in ("conclusive", "reformulate", "exhausted"):
        assert EvaluationVerdict(verdict=v, reasoning="x").verdict == v


def test_evaluation_verdict_rejects_unknown_verdict():
    with pytest.raises(ValidationError):
        EvaluationVerdict(verdict="maybe", reasoning="x")


def test_evaluation_verdict_optional_fields_default_none():
    e = EvaluationVerdict(verdict="exhausted", reasoning="no lead")
    assert e.requires_remediation is None
    assert e.next_hypothesis is None
    assert e.why_ruled_out is None
