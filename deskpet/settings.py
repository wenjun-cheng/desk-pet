"""The pet's settings: settings.json in its data folder."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    "x": None,            # where along the bottom edge (the middle of the pet), None: near the right
    "language": "en",
    "workspace": None,    # the folder Claude works in, None: ~/Claude
    "session": None,      # the bubble's conversation, kept across restarts until New conversation
    "background": None,   # the Claude Code background session it runs in
}


class Settings(dict):
    def __init__(self, path: Path):
        super().__init__(DEFAULTS)
        self.path = path
        try:
            self.update(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)

    def set(self, key: str, value):
        """Change one setting and save, if it changed."""
        if self.get(key) != value:
            self[key] = value
            self.save()
