"""Where things are: the pet's own data, the folder Claude works in, the bundled assets, Claude Code itself, and
how Claude Code starts the pet's helpers (its permission hook, tools, status line and terminal relay)."""
from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

APP = "DeskPet"
# The running pet's local server (a named pipe or socket): a second launch makes it hop, the permission hook asks
# it, and nobody else's pet answers (DESKPET_SERVER instead, if set: for tests)
SERVER_NAME = os.environ.get("DESKPET_SERVER") or \
    f"DeskPet-{os.environ.get('USERNAME') or os.environ.get('USER') or 'user'}"
FROZEN = getattr(sys, "frozen", False)   # a PyInstaller build
PACKAGE = Path(__file__).resolve().parent
ASSETS = PACKAGE / "assets"
HELPER = "DeskPetHelper"                 # the build's console program for the helpers (the app has no console)


def data_dir() -> Path:
    """Settings, log and caches: %APPDATA%\\DeskPet, ~/Library/Application Support/DeskPet or ~/.local/share/DeskPet
    (DESKPET_DATA instead, if set: for tests)."""
    if os.environ.get("DESKPET_DATA"):
        return Path(os.environ["DESKPET_DATA"])
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP


DATA = data_dir()
USAGE = DATA / "usage.json"          # the plan's limits (claude/usage.py)
STATUS = DATA / "statusline"         # what Claude Code last gave the status line, per session (claude/statusline.py)
TARGET = DATA / "target.json"        # the window the user means by "this" (claude/tools.py)
# Where Claude works: a folder of its own. Not the home folder: Claude Code never remembers trusting that.
DEFAULT_WORKSPACE = Path.home() / "Claude"
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")  # Claude Code's own data


def claude_exe() -> str | None:
    """Claude Code. Started at login or from the Dock, the pet may not get the shell's PATH: then where its
    installers put it."""
    found = shutil.which("claude")
    if found or sys.platform == "win32":
        return found
    for place in (Path.home() / ".local" / "bin", Path.home() / ".claude" / "local", Path("/opt/homebrew/bin"),
                  Path("/usr/local/bin")):
        if (place / "claude").is_file():
            return str(place / "claude")
    return None


def clean_env() -> dict[str, str]:
    """Our environment, minus what a Claude Code session sets for its own commands (if the pet was started from
    inside one): NO_COLOR would turn Claude's colors off, and the CLAUDE_* variables would make a new Claude think
    it belongs to that session."""
    env = dict(os.environ)
    if "CLAUDECODE" in env:
        for key in list(env):
            if key.upper().startswith("CLAUDE") or key.upper() in ("NO_COLOR", "GIT_TERMINAL_PROMPT"):
                del env[key]
    return env


def launch_argv() -> list[str]:
    """How to start the pet (for starting at login)."""
    if FROZEN:
        return [sys.executable]
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "python.exe":
        python = python.with_name("pythonw.exe")  # (no console window)
    return [str(python), str(PACKAGE.parent / "run.py")]


def helper_argv(mode: str, *args: str) -> list[str]:
    """How to run one of the pet's helpers as its own process: hook, mcp, statusline, relay."""
    if FROZEN:
        exe = Path(sys.executable).with_name(HELPER + (".exe" if sys.platform == "win32" else ""))
        return [str(exe), mode, *args]
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")  # the helpers talk through stdin and stdout
    return [str(python), str(PACKAGE.parent / "run.py"), mode, *args]


def shell_command(argv: list[str]) -> str:
    """argv as one command line for the shell Claude Code runs hooks and status lines in. On Windows that can be
    PowerShell, cmd or Git Bash, which quote differently: paths there go in their short (8.3) form with forward
    slashes, so they need no quotes at all; only if the short form still has spaces is it quoted, PowerShell's
    way. Elsewhere it's sh."""
    if sys.platform != "win32":
        return " ".join(shlex.quote(a) for a in argv)
    parts = [_short(a).replace("\\", "/") for a in argv]
    if any(" " in p for p in parts):
        return "& " + " ".join(f'"{p}"' if " " in p else p for p in parts)
    return " ".join(parts)


def _short(path: str) -> str:
    if " " not in path or not os.path.exists(path):
        return path
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    return buf.value if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024) else path
