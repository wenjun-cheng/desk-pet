"""What Claude Code writes down as a session goes: its transcript (one JSON entry per line, in
~/.claude/projects/<project>/<session id>.jsonl) and its status file (~/.claude/sessions/<pid>.json)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..paths import CLAUDE_HOME

PROJECTS = CLAUDE_HOME / "projects"
SESSIONS = CLAUDE_HOME / "sessions"
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
STDOUT = re.compile(r"<local-command-stdout>(.*?)</local-command-stdout>", re.S)
GLYPHS = re.compile("[▀-▟⛀-⛿]")  # block elements and the ⛁ ⛀ ⛶ squares of usage charts


def transcript(session_id: str) -> Path | None:
    return next(PROJECTS.glob(f"*/{session_id}.jsonl"), None)


def status(pid: int) -> str:
    """'busy', 'idle', or what else Claude Code says the session is doing ('' if unknown)."""
    try:
        return json.loads((SESSIONS / f"{pid}.json").read_text("utf-8")).get("status") or ""
    except (OSError, ValueError):
        return ""


def waiting(state: str) -> bool:
    """Claude is waiting on the user in the terminal (a permission prompt, a menu, another dialog). Typing then
    would answer it: Enter picks its first choice."""
    return state not in ("busy", "idle", "")


def summary(data: dict) -> str:
    """A tool call's input in a few words: its file, pattern, address, command…"""
    for key in ("file_path", "path", "pattern", "url", "query", "command", "description"):
        if isinstance(data.get(key), str):
            return data[key][:80]
    return ""


def command_output(entry: dict) -> str | None:
    """What a slash command printed, if this entry has it (a system or a user entry), without its terminal
    colours or charts; "" for a command run without output; None if it isn't a command's entry."""
    content = entry.get("content") if entry.get("type") == "system" else (entry.get("message") or {}).get("content")
    if not isinstance(content, str):
        return None
    found = STDOUT.search(content)
    if found:
        text = GLYPHS.sub("", ANSI.sub("", found.group(1)).replace("`", ""))
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return "" if "<command-name>" in content else None


class Follower:
    """Reads a transcript as it grows, entry by entry, from where it was when this started."""

    def __init__(self, path: Path | None):
        self.path = path
        self.offset = path.stat().st_size if path and path.exists() else 0

    def new_entries(self, session_id: str) -> list[dict]:
        if self.path is None:
            self.path = transcript(session_id)  # a new session's transcript appears with its first message
            if self.path is None:
                return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return []
        end = chunk.rfind(b"\n") + 1  # only whole lines; the rest is still being written
        self.offset += end
        entries = []
        for line in chunk[:end].splitlines():
            try:
                entries.append(json.loads(line))
            except ValueError:
                pass
        return entries
