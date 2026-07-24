import asyncio
from unittest.mock import AsyncMock, MagicMock

from agents.helpers import IntentClassifier
from models import IntentClassification


def _mock_llm(parsed, raw_content=""):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value={"parsed": parsed, "raw": MagicMock(content=raw_content)})
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


def test_classify_returns_parsed_intent():
    parsed = IntentClassification(intent="explain", reasoning="conceptual question")
    c = IntentClassifier(_mock_llm(parsed))
    result = asyncio.run(c.classify("what does an HPA do"))
    assert result.intent == "explain"


def test_classify_falls_back_to_diagnose_on_null_parse():
    c = IntentClassifier(_mock_llm(None, raw_content="unparseable"))
    result = asyncio.run(c.classify("q"))
    assert result.intent == "diagnose"
