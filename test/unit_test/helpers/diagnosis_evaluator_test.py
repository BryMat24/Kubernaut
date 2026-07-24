import asyncio
from unittest.mock import AsyncMock, MagicMock

from agents.helpers import DiagnosisEvaluator
from models import EvaluationVerdict


def _mock_llm(parsed, raw_content=""):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value={"parsed": parsed, "raw": MagicMock(content=raw_content)})
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


def test_evaluate_returns_parsed_verdict():
    parsed = EvaluationVerdict(verdict="conclusive", reasoning="pvc missing sc", requires_remediation=True)
    ev = DiagnosisEvaluator(_mock_llm(parsed))
    result = asyncio.run(ev.evaluate("q", "pvc pending", "checklist", [], 1, 3))
    assert result.verdict == "conclusive"
    assert result.requires_remediation is True


def test_evaluate_falls_back_to_exhausted_on_null_parse():
    ev = DiagnosisEvaluator(_mock_llm(None, raw_content="garbage"))
    result = asyncio.run(ev.evaluate("q", "h", "cl", [], 3, 3))
    assert result.verdict == "exhausted"
