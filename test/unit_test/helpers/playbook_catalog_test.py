from agents.helpers import PlaybookLibrary

EXPECTED_IDS = {
    "generic", "network", "rbac", "autoscaling", "scheduling",
    "resource_governance", "storage", "secret_configmap", "rollout",
}


def test_all_expected_playbooks_present():
    lib = PlaybookLibrary()
    ids = {t["playbook_id"] for t in lib.list_triggers()}
    assert EXPECTED_IDS <= ids, f"missing: {EXPECTED_IDS - ids}"


def test_every_non_generic_playbook_has_triggers_and_body():
    lib = PlaybookLibrary()
    for t in lib.list_triggers():
        pb = lib.get(t["playbook_id"])
        assert pb.body.strip(), f"{pb.playbook_id} has empty body"
        if pb.playbook_id != "generic":
            assert pb.trigger_conditions, f"{pb.playbook_id} has no trigger_conditions"
