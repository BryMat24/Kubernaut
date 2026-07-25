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
    parsed = EvaluationVerdict(verdict="conclusive", reasoning="pvc missing sc")
    ev = DiagnosisEvaluator(_mock_llm(parsed))
    result = asyncio.run(ev.evaluate("q", "pvc pending", [], 1, 3))
    assert result.verdict == "conclusive"


def test_evaluate_falls_back_to_exhausted_on_null_parse():
    ev = DiagnosisEvaluator(_mock_llm(None, raw_content="garbage"))
    result = asyncio.run(ev.evaluate("q", "h", [], 3, 3))
    assert result.verdict == "exhausted"


def test_evaluate_prompt_gives_confident_vs_unconfident_examples_not_playbook():
    # Regression test for a false-negative bug: the evaluator used to be handed the (possibly
    # wrong) selected playbook's conclusion criteria and anchor its verdict to whether the
    # investigation matched THAT playbook -- so a correct pivot to a different, well-evidenced
    # mechanism (e.g. the hypothesizer picks secret_configmap, but the real cause turns out to be
    # a bad `command:` field) got misgraded as "reformulate" and discarded. The fix judges the
    # response's own evidence quality via few-shot GOOD/BAD examples instead of a playbook.
    parsed = EvaluationVerdict(verdict="conclusive", reasoning="bad command, not a secret issue")
    llm = _mock_llm(parsed)
    ev = DiagnosisEvaluator(llm)
    asyncio.run(ev.evaluate("q", "missing secret key", [], 1, 3))

    structured_llm = llm.with_structured_output.return_value
    sent_messages = structured_llm.ainvoke.call_args.args[0]
    prompt_text = sent_messages[-1].content

    assert "playbook" not in prompt_text.lower()
    assert "BAD (not confident) example" in prompt_text
    assert "GOOD (confident) example" in prompt_text
