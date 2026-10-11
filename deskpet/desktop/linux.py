"""Linux: other windows through xdotool (X11 only; on Wayland, or without it, the pet just doesn't know which
window you mean), idle time from xprintidle, starting at login (an XDG autostart entry), the first terminal
program found. The global shortcut comes from pynput (X11)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import *  # noqa: F401,F403
from .base import __all__  # noqa: F401

AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "deskpet.desktop"
TERMINALS = ("terminal", "konsole", "xterm", "kitty", "alacritty", "tilix", "wezterm", "ghostty")


def _xdotool(*args: str) -> str:
    if not shutil.which("xdotool") or not os.environ.get("DISPLAY"):
        return ""
    try:
        return subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=1).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _program(handle: int) -> str:
    pid = _xdotool("getwindowpid", str(handle))
    try:
        return Path(f"/proc/{int(pid)}/comm").read_text().strip()
    except (OSError, ValueError):
        return ""


def foreground() -> int:
    found = _xdotool("getactivewindow")
    return int(found) if found.isdigit() else 0


def is_work_window(handle: int) -> bool:
    if not handle:
        return False
    pid = _xdotool("getwindowpid", str(handle))
    program = _program(handle).lower()
    return bool(handle) and pid != str(os.getpid()) and not any(t in program for t in TERMINALS)


def describe(handle: int) -> str:
    return f"{_xdotool('getwindowname', str(handle)) or '(untitled)'} ({_program(handle) or 'unknown program'})"


def window_exists(handle: int) -> bool:
    return bool(handle) and bool(_xdotool("getwindowname", str(handle)))


def window_rect(handle: int) -> tuple[int, int, int, int] | None:
    values = dict(line.split("=", 1) for line in _xdotool("getwindowgeometry", "--shell", str(handle)).splitlines()
                  if "=" in line)
    try:
        x, y, w, h = (int(values[k]) for k in ("X", "Y", "WIDTH", "HEIGHT"))
    except (KeyError, ValueError):
        return None
    return x, y, x + w, y + h


def bring_to_front(handle: int):
    _xdotool("windowactivate", str(handle))


def idle_seconds() -> float:
    if not shutil.which("xprintidle"):
        return 0.0
    try:
        return int(subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=1).stdout) / 1000
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def autostart_enabled() -> bool:
    return AUTOSTART.exists()


def set_autostart(on: bool, argv: list[str]):
    import shlex
    if on:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text("[Desktop Entry]\nType=Application\nName=Desk Pet\n"
                             f"Exec={' '.join(shlex.quote(a) for a in argv)}\nX-GNOME-Autostart-enabled=true\n")
    else:
        AUTOSTART.unlink(missing_ok=True)
