"""The status line of the bubble's sessions (see launch.py). It records what Claude Code gives it (the pet's menu
shows the model, effort and context from that), then shows the user's own status line, if they have one, with
the weekly limits of single models (Fable) added when it doesn't show them; or a short one of its own."""
from __future__ import annotations

import json
import subprocess
import sys

from .. import paths
from . import usage


def _users_command() -> str:
    """The user's own status line command (in their Claude Code settings), unless it's this one."""
    try:
        line = json.loads((paths.CLAUDE_HOME / "settings.json").read_text("utf-8")).get("statusLine") or {}
    except (OSError, ValueError):
        return ""
    command = line.get("command") or "" if line.get("type") == "command" else ""
    return "" if paths.HELPER in command or "statusline" in command and "run.py" in command else command


def _own_line(data: dict) -> str:
    parts = [(data.get("model") or {}).get("display_name"), (data.get("effort") or {}).get("level")]
    used = (data.get("context_window") or {}).get("used_percentage")
    if used is not None:
        parts.append(f"ctx {round(float(used))}%")
    return " · ".join(p for p in parts if p)


def main():
    raw = sys.stdin.buffer.read()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        data = {}
    if data.get("session_id"):
        paths.STATUS.mkdir(parents=True, exist_ok=True)
        (paths.STATUS / f"{data['session_id']}.json").write_bytes(raw)
    text, command = "", _users_command()
    if command:
        shell = ["powershell", "-NoProfile", "-Command", command] if sys.platform == "win32" else ["sh", "-c", command]
        try:
            out = subprocess.run(shell, input=raw, capture_output=True, timeout=10,
                                 creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            text = out.stdout.decode("utf-8", "replace").rstrip("\r\n")
        except (OSError, subprocess.SubprocessError):
            text = ""
    if not text:
        text = _own_line(data)
        limits = usage.load(paths.USAGE)
        if limits:
            text += (" · " if text else "") + usage.describe(limits).replace(" | ", " · ")
    else:
        extra = [f"{l['name'].lower()} {round(l['pct'])}%" for l in usage.load(paths.USAGE)
                 if l["name"] not in ("5h", "7d") and l["name"].lower() not in text.lower()]
        text += "".join(f" | {e}" for e in extra)
    sys.stdout.buffer.write(text.encode("utf-8"))
