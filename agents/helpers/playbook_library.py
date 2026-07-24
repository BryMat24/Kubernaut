import os
from dataclasses import dataclass

import yaml

_AGENTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAYBOOKS_DIR = os.path.join(_AGENTS_DIR, "playbooks")


@dataclass
class Playbook:
    playbook_id: str
    category: str
    trigger_conditions: list[str]
    body: str


class PlaybookLibrary:
    def __init__(self, playbooks_dir: str = PLAYBOOKS_DIR) -> None:
        self._playbooks: dict[str, Playbook] = {}
        for filename in sorted(os.listdir(playbooks_dir)):
            if not filename.endswith(".md"):
                continue
            playbook = self._parse(os.path.join(playbooks_dir, filename))
            self._playbooks[playbook.playbook_id] = playbook
        if "generic" not in self._playbooks:
            raise ValueError("PlaybookLibrary requires a 'generic' fallback playbook")

    @staticmethod
    def _parse(path: str) -> Playbook:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if not text.lstrip().startswith("---"):
            raise ValueError(f"Playbook {path} is missing YAML frontmatter")
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter) or {}
        if "playbook_id" not in meta:
            raise ValueError(f"Playbook {path} frontmatter missing playbook_id")
        return Playbook(
            playbook_id=meta["playbook_id"],
            category=meta.get("category", meta["playbook_id"]),
            trigger_conditions=meta.get("trigger_conditions") or [],
            body=body.strip(),
        )

    def list_triggers(self) -> list[dict]:
        return [
            {
                "playbook_id": p.playbook_id,
                "category": p.category,
                "trigger_conditions": p.trigger_conditions,
            }
            for p in self._playbooks.values()
        ]

    def get(self, playbook_id: str) -> Playbook:
        return self._playbooks.get(playbook_id, self._playbooks["generic"])
