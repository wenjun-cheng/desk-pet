"""What every platform module provides, with what works anywhere (or does nothing where nothing does).
Windows, macOS and Linux override what they can do better."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal

__all__ = ["setup_app", "own_window", "foreground", "is_work_window", "describe", "window_exists", "window_rect", "screenshot",
           "bring_to_front", "give_focus", "restore_focus", "keep_on_top", "mouse_button_down", "idle_seconds",
           "pid_alive", "Hotkey", "autostart_enabled", "set_autostart", "open_terminal", "terminal_window",
           "run_illustrator", "AUTOSTART_LABEL"]

AUTOSTART_LABEL = "autostart"


def setup_app():
    """Once the app has started: whatever the platform needs for a little app that lives on the desktop."""


def own_window(window_id: int):
    """One of the pet's windows (the pet, its bubble), once shown: whatever keeps the system from treating it as
    an ordinary window."""


# ---------------------------------------------------------------- other programs' windows

def foreground() -> int:
    """The window in front (a handle only the functions here understand); 0 if it can't tell."""
    return 0


def is_work_window(handle: int) -> bool:
    """Whether it's a window the user works in: not the pet's own, a terminal, or the taskbar and the like."""
    return False


def describe(handle: int) -> str:
    """'<title> (<program>)'."""
    return ""


def window_exists(handle: int) -> bool:
    return False


def window_rect(handle: int) -> tuple[int, int, int, int] | None:
    """Where it is on the screen, in the pixels a screenshot has."""
    return None


def screenshot(handle: int = 0):
    """That window as a PIL image, or every screen if it can't be found."""
    from PIL import ImageGrab
    rect = window_rect(handle) if handle else None
    return ImageGrab.grab(bbox=rect, all_screens=True) if rect else ImageGrab.grab(all_screens=True)


def bring_to_front(handle: int):
    pass


# ---------------------------------------------------------------- the pet's own windows

def give_focus(window_id: int):
    """Our window takes the keyboard (Qt's activateWindow has already asked for it)."""


def restore_focus(handle: int):
    """The keyboard back to the window that had it."""


def keep_on_top(window_ids: list[int]):
    """Called now and then: always-on-top windows of others may have got above ours."""


# ---------------------------------------------------------------- the user

def mouse_button_down() -> bool | None:
    """Whether the main mouse button is down right now, whatever window has the mouse; None: can't tell."""
    return None


def idle_seconds() -> float:
    """Since the last mouse or keyboard input (0 if it can't tell, so the pet never falls asleep)."""
    return 0.0


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


class Hotkey(QObject):
    """Ctrl+Alt+Space from anywhere: `pressed`, then `released` when Space is let go (in Qt's thread). Through
    pynput where it's installed."""
    pressed = Signal()
    released = Signal()

    def __init__(self):
        super().__init__()
        self.ok = False
        self._down: set = set()
        self._held = False
        try:
            from pynput import keyboard
            self._keys = keyboard.Key
            self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
            self._listener.daemon = True
            self._listener.start()
            self.ok = True
        except Exception:  # not installed, or no way to listen (Wayland; macOS without permission)
            self._listener = None

    def _press(self, key):
        key = self._listener.canonical(key)
        self._down.add(key)
        if key == self._keys.space and {self._keys.ctrl, self._keys.alt} <= self._down and not self._held:
            self._held = True
            self.pressed.emit()

    def _release(self, key):
        key = self._listener.canonical(key)
        self._down.discard(key)
        if key == self._keys.space and self._held:
            self._held = False
            self.released.emit()


# ---------------------------------------------------------------- starting at login

def autostart_enabled() -> bool:
    return False


def set_autostart(on: bool, argv: list[str]):
    pass


# ---------------------------------------------------------------- terminals and apps

def open_terminal(argv: list[str], cwd: Path, env: dict) -> subprocess.Popen | None:
    """A terminal window on screen running argv. On Linux, the first terminal program found."""
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"],
                 ["xfce4-terminal", "-x"], ["kitty"], ["alacritty", "-e"], ["xterm", "-e"]):
        if shutil.which(term[0]):
            return subprocess.Popen([*term, *argv], cwd=str(cwd), env=env)
    return None


def terminal_window(pid: int) -> int:
    """The window of the terminal running process pid (to bring it to the front); 0 if it can't tell."""
    return 0


def run_illustrator(script: str) -> str:
    """Run ExtendScript in the open Adobe Illustrator and return its last expression's value."""
    raise RuntimeError(f"Adobe Illustrator scripting isn't available on {sys.platform}.")
