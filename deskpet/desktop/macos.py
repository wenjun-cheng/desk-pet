"""macOS: other windows through Quartz and AppKit (pyobjc; without it the pet just doesn't know which window you
mean), idle time and the mouse button from Quartz, starting at login (a LaunchAgent), Terminal for terminals,
Illustrator through AppleScript. The global shortcut comes from pynput (it needs the Accessibility permission)."""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from .base import *  # noqa: F401,F403
from .base import __all__  # noqa: F401

try:
    import Quartz
    from AppKit import NSWorkspace
except ImportError:  # pyobjc isn't installed
    Quartz = NSWorkspace = None

AGENT = Path.home() / "Library" / "LaunchAgents" / "com.deskpet.app.plist"
TERMINALS = {"Terminal", "iTerm2", "Warp", "kitty", "Alacritty", "WezTerm", "Ghostty"}


def setup_app():
    """No Dock icon and no menu bar of its own (what LSUIElement does for the built app)."""
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except ImportError:
        pass


def _front_window() -> dict | None:
    """The frontmost app's top window, from Quartz's window list (titles need the Screen Recording permission)."""
    if Quartz is None:
        return None
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    pid = app.processIdentifier()
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly
                                                | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
    for w in windows or []:
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 1) == 0:
            return dict(w)
    return None


def _window(handle: int) -> dict | None:
    if Quartz is None or not handle:
        return None
    found = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionIncludingWindow, handle)
    return dict(found[0]) if found else None


def foreground() -> int:
    w = _front_window()
    return int(w["kCGWindowNumber"]) if w else 0


def is_work_window(handle: int) -> bool:
    w = _window(handle)
    return bool(w) and w.get("kCGWindowOwnerPID") != os.getpid() and w.get("kCGWindowOwnerName") not in TERMINALS


def describe(handle: int) -> str:
    w = _window(handle) or {}
    return f"{w.get('kCGWindowName') or '(untitled)'} ({w.get('kCGWindowOwnerName') or 'unknown program'})"


def window_exists(handle: int) -> bool:
    return _window(handle) is not None


def window_rect(handle: int) -> tuple[int, int, int, int] | None:
    b = (_window(handle) or {}).get("kCGWindowBounds")
    if not b:
        return None
    return int(b["X"]), int(b["Y"]), int(b["X"] + b["Width"]), int(b["Y"] + b["Height"])


def mouse_button_down() -> bool | None:
    if Quartz is None:
        return None
    return bool(Quartz.CGEventSourceButtonState(Quartz.kCGEventSourceStateCombinedSessionState,
                                                Quartz.kCGMouseButtonLeft))


def idle_seconds() -> float:
    if Quartz is None:
        return 0.0
    return float(Quartz.CGEventSourceSecondsSinceLastEventType(Quartz.kCGEventSourceStateCombinedSessionState,
                                                               Quartz.kCGAnyInputEventType))


def autostart_enabled() -> bool:
    return AGENT.exists()


def set_autostart(on: bool, argv: list[str]):
    if on:
        AGENT.parent.mkdir(parents=True, exist_ok=True)
        AGENT.write_bytes(plistlib.dumps({"Label": "com.deskpet.app", "ProgramArguments": argv, "RunAtLoad": True}))
    else:
        AGENT.unlink(missing_ok=True)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_terminal(argv: list[str], cwd: Path, env: dict) -> subprocess.Popen | None:
    """A new Terminal window running argv (in the user's own shell, so their PATH applies)."""
    import shlex
    command = f"cd {shlex.quote(str(cwd))} && " + " ".join(shlex.quote(a) for a in argv)
    script = f'tell application "Terminal" to do script {_applescript_string(command)}'
    return subprocess.Popen(["osascript", "-e", script, "-e", 'tell application "Terminal" to activate'])


def run_illustrator(script: str) -> str:
    out = subprocess.run(["osascript", "-e", 'tell application id "com.adobe.illustrator" to do javascript '
                          + _applescript_string(script)], capture_output=True, text=True, timeout=120)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or "Illustrator isn't open, or the script failed.")
    return out.stdout.strip() or "(no value)"
