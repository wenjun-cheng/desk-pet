"""How the pet starts Claude Code: the background session the bubble talks to. What the pet adds to it goes on
the command line, nothing in anyone's Claude Code settings is changed: its permission hook (questions in the
bubble), its tools (screenshot, Illustrator), its status line (which records what the menu shows, then shows
the user's own), and how to answer in a bubble."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .. import paths

INSTRUCTIONS = """\
# Desk Pet

You are the Claude behind Desk Pet, a little pet on the user's desktop. Their messages come from its speech bubble, \
and the bubble shows your final reply. They open this terminal when they want everything.

How to reply:
- The bubble is small and shows your final message as plain text. Lead with the point: what you did, or the direct \
answer, in the user's language. Usually a sentence or two. No preamble, no headings, tables or bold.
- If they may want more (code, long lists, details), add it after a blank line, briefly.
- Prefer doing the task over describing how to do it.

Context and tools:
- A message may start with "[Window: <title> (<program>)]": the window the user was in when they wrote it. "this", \
"here" and "the selected one" refer to it. Call mcp__pet__screenshot to see it.
- Adobe Illustrator: work in it directly with mcp__pet__illustrator_script, which runs ExtendScript in the open \
Illustrator. Read the document and selection first, then change them, in small focused scripts. Keep scripts ASCII \
only (write \\u escapes) and avoid unparenthesized nested ternaries. The user undoes with Ctrl+Z, so you don't need to \
ask before editing their artwork. If something can only be done through Illustrator's menus, give the steps briefly.
- Don't operate apps by clicking around their interface; use scripts, commands and files.
"""
TOOLS = ("mcp__pet__screenshot", "mcp__pet__illustrator_script")  # allowed without asking: looking is harmless,
# and Illustrator undoes
BACKGROUNDED = re.compile(r"backgrounded\s*\S\s*([0-9a-f]{6,})")  # what `claude --bg` prints: the session's id
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class NotTrusted(RuntimeError):
    """Claude Code hasn't been told yet that it can trust its folder: running `claude` there once asks."""


def config_args() -> list[str]:
    """The pet's additions to a session, written to files in its data folder (with the paths of this copy of the
    pet, so moving it keeps them right), as command-line options."""
    paths.DATA.mkdir(parents=True, exist_ok=True)
    settings = {
        "hooks": {"PermissionRequest": [{"matcher": "*", "hooks": [
            {"type": "command", "command": paths.shell_command(paths.helper_argv("hook")), "timeout": 600}]}]},
        "permissions": {"allow": list(TOOLS)},
        "statusLine": {"type": "command", "command": paths.shell_command(paths.helper_argv("statusline"))},
    }
    mcp = paths.helper_argv("mcp")
    files = {"claude-settings.json": json.dumps(settings, indent=1),
             "claude-mcp.json": json.dumps({"mcpServers": {"pet": {"type": "stdio", "command": mcp[0],
                                                                    "args": mcp[1:]}}}, indent=1),
             "claude-instructions.md": INSTRUCTIONS}
    for name, text in files.items():
        (paths.DATA / name).write_text(text, "utf-8")
    return ["--settings", str(paths.DATA / "claude-settings.json"),
            "--mcp-config", str(paths.DATA / "claude-mcp.json"),
            "--append-system-prompt-file", str(paths.DATA / "claude-instructions.md")]


def _run(args: list[str], cwd: Path, env: dict, timeout: float) -> subprocess.CompletedProcess:
    exe = paths.claude_exe()
    if not exe:
        raise RuntimeError("no_claude")
    # stdin too: under pythonw the inherited one can be an invalid handle, which subprocess can't pass on
    return subprocess.run([exe, *args], capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=str(cwd),
                          env=env, creationflags=NO_WINDOW)


def run_claude(args: list[str], cwd: Path, env: dict, timeout: float = 60) -> str:
    """`claude <args>` without a window; what it printed."""
    out = _run(args, cwd, env, timeout)
    return out.stdout.decode("utf-8", "replace") + out.stderr.decode("utf-8", "replace")


def start_background(cwd: Path, env: dict, session_id: str | None) -> str:
    """A new background session in cwd (resuming that conversation, if any); its id."""
    args = ["--bg", *(["--resume", session_id] if session_id else []), *config_args()]
    said = run_claude(args, cwd, env)
    if "not trusted" in said.lower():
        raise NotTrusted(said.strip())
    found = BACKGROUNDED.search(said)
    if not found:
        raise RuntimeError(f"claude --bg said: {said.strip()[:300]}")
    return found.group(1)


def agents(cwd: Path, env: dict) -> list[dict]:
    """Claude Code's sessions, from `claude agents --json` (documented for scripts)."""
    try:
        return json.loads(_run(["agents", "--json"], cwd, env, 20).stdout.decode("utf-8", "replace") or "[]")
    except (ValueError, OSError, subprocess.SubprocessError):
        return []


def _same_dir(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.normpath(a or "")) == os.path.normcase(os.path.normpath(str(b)))


def background_session(cwd: Path, env: dict, background_id: str | None) -> dict | None:
    """That background session's entry: id, sessionId, and pid while it runs."""
    if not background_id:
        return None
    return next((e for e in agents(cwd, env) if e.get("kind") == "background" and e.get("id") == background_id
                 and _same_dir(e.get("cwd"), cwd)), None)
