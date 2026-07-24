import asyncio
from unittest.mock import AsyncMock, MagicMock

from agents.helpers import Hypothesizer
from models import HypothesisSelection


def _mock_llm(parsed, raw_content=""):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value={"parsed": parsed, "raw": MagicMock(content=raw_content)})
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


def test_select_returns_parsed_selection():
    parsed = HypothesisSelection(hypothesis="pvc pending", playbook_id="storage", reasoning="pending pvc")
    h = Hypothesizer(_mock_llm(parsed))
    result = asyncio.run(
        h.select(
            "why pending",
            "a pvc is Pending",
            [{"playbook_id": "storage", "category": "storage", "trigger_conditions": ["pvc pending"]}],
            [],
        )
    )
    assert result.playbook_id == "storage"


def test_select_falls_back_to_generic_on_null_parse():
    h = Hypothesizer(_mock_llm(None, raw_content="unparseable"))
    result = asyncio.run(h.select("q", "scope", [], []))
    assert result.playbook_id == "generic"
