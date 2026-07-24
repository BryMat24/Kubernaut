import pytest

from agents.helpers import PlaybookLibrary


def test_loads_generic_and_network(tmp_path):
    lib = PlaybookLibrary()
    ids = {t["playbook_id"] for t in lib.list_triggers()}
    assert "generic" in ids
    assert "network" in ids


def test_get_returns_requested_playbook():
    lib = PlaybookLibrary()
    pb = lib.get("network")
    assert pb.playbook_id == "network"
    assert pb.trigger_conditions  # non-empty
    assert pb.body.strip()


def test_get_unknown_id_falls_back_to_generic():
    lib = PlaybookLibrary()
    pb = lib.get("does-not-exist")
    assert pb.playbook_id == "generic"


def test_missing_generic_raises(tmp_path):
    (tmp_path / "network.md").write_text(
        "---\nplaybook_id: network\ncategory: network\ntrigger_conditions:\n  - x\n---\nbody"
    )
    with pytest.raises(ValueError):
        PlaybookLibrary(playbooks_dir=str(tmp_path))


def test_parses_frontmatter_and_body(tmp_path):
    (tmp_path / "generic.md").write_text(
        "---\nplaybook_id: generic\ncategory: fallback\ntrigger_conditions: []\n---\n# Body\nchecklist here"
    )
    lib = PlaybookLibrary(playbooks_dir=str(tmp_path))
    pb = lib.get("generic")
    assert pb.category == "fallback"
    assert "checklist here" in pb.body
