"""A program running in a pseudo-terminal the pet owns (ConPTY through pywinpty on Windows, a Unix pty elsewhere),
its screen kept by pyte so it can be read the way a terminal window shows it; and typing a message into Claude
Code's prompt, in such a terminal or in any other "console" that can be written to and read."""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Protocol

import pyte

log = logging.getLogger("pet.claude")
PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"  # bracketed paste: line breaks stay line breaks


class Console(Protocol):
    """Somewhere Claude Code's terminal can be typed into and read."""

    def write(self, text: str) -> None: ...

    def rows(self) -> list[str]: ...

    def alive(self) -> bool: ...


class PtyTerminal:
    def __init__(self, argv: list[str], cwd: Path, env: dict, rows: int = 40, cols: int = 120,
                 echo: Callable[[str], None] | None = None):
        """echo: also gets everything the program prints (the relay shows it in a real terminal)."""
        if sys.platform == "win32":
            from winpty import PtyProcess
            from ..desktop.windows import console_for_pty
            with console_for_pty():
                self.proc = PtyProcess.spawn(argv, cwd=str(cwd), env=env, dimensions=(rows, cols))
        else:
            from ptyprocess import PtyProcessUnicode
            self.proc = PtyProcessUnicode.spawn(argv, cwd=str(cwd), env=env, dimensions=(rows, cols))
        self.screen = pyte.Screen(cols, rows)
        self._stream = pyte.Stream(self.screen)
        self._lock = threading.Lock()
        self.echo = echo
        threading.Thread(target=self._read, name="pty", daemon=True).start()

    def _read(self):
        while True:
            try:
                data = self.proc.read(4096)
            except (EOFError, OSError):
                return
            if not data:
                if not self.proc.isalive():
                    return
                continue
            with self._lock:
                self._stream.feed(data)
            if self.echo:
                self.echo(data)

    def write(self, text: str):
        self.proc.write(text)

    def rows(self) -> list[str]:
        with self._lock:
            return [line.rstrip() for line in self.screen.display]

    def alive(self) -> bool:
        return self.proc.isalive()

    def resize(self, rows: int, cols: int):
        with self._lock:
            self.screen.resize(rows, cols)
        self.proc.setwinsize(rows, cols)

    def close(self):
        try:
            self.proc.terminate(force=True)
        except Exception:  # (already gone)
            pass


# ---------------------------------------------------------------- Claude Code's prompt

def prompt_box(rows: list[str]) -> list[str]:
    """The rows of Claude's prompt box: from its ❯ to the rule under it ([] if it isn't up: a menu or dialog)."""
    start = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].startswith("❯")), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(rows)) if rows[i].startswith("──")), len(rows))
    return rows[start:end]


def prompt_up(console: Console) -> bool:
    return bool(prompt_box(console.rows()))


def wait_for_prompt(console: Console, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not prompt_up(console):
        if not console.alive() or time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def clear_prompt(console: Console):
    """Empty the prompt wherever the cursor is: per round, Ctrl+K and Ctrl+U clear the line, Backspace and Delete
    join it with the lines around it. One key at a time: in a burst they'd count as a paste, which ignores them.
    On an empty prompt they do nothing. (Claude Code's Ctrl+Y brings back what they cleared.)"""
    rounds = max(len(prompt_box(console.rows())), 4) // 2 + 2
    for _ in range(rounds):
        for key in ("\x0b", "\x15", "\x7f", "\x1b[3~"):
            console.write(key)
            time.sleep(0.025)


def _squeezed(text: str) -> str:
    return "".join(text.split())


def _in_prompt(console: Console, text: str) -> bool:
    """Whether the prompt shows the text (its start), or Claude Code's stand-in for a long paste."""
    box = _squeezed(" ".join(prompt_box(console.rows())))
    start = _squeezed(text)[:12]
    return bool(start) and start in box or "[Pastedtext" in box


def type_message(console: Console, text: str, images: tuple[str, ...] = ()) -> bool:
    """Type a message into Claude's prompt and send it. Whatever was half typed there goes first (what's sent
    wins). The text goes in as one paste, so line breaks stay line breaks, and Enter comes a moment later on its
    own (in the same burst, Claude Code takes it as part of the paste and the message sits there unsent). If the
    text doesn't show up in the prompt (keys can get lost right after a terminal attaches), it's typed again.
    images: files, each pasted after the text on its own (Claude Code attaches a pasted image path as an image,
    [Image #n]; several in one paste stay text)."""
    text = text.replace("\r\n", "\n")
    for _ in range(3):
        clear_prompt(console)
        console.write(PASTE_START + text + PASTE_END)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not _in_prompt(console, text):
            time.sleep(0.1)
        if _in_prompt(console, text):
            break
    else:
        return False
    before = _attached(console)  # (the text may hold [Image #n] itself)
    for i, path in enumerate(images, 1):
        console.write(PASTE_START + " " + path + PASTE_END)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _attached(console) < before + i:
            time.sleep(0.1)
        if _attached(console) < before + i:  # (left as a path: Claude can still read the file)
            log.warning("An image didn't attach: %s", path)
    time.sleep(0.4)
    console.write("\r")
    return True


def _attached(console: Console) -> int:
    """How many images the prompt has (Claude Code's [Image #n])."""
    return _squeezed(" ".join(prompt_box(console.rows()))).count("[Image#")


def press_enter(console: Console):
    console.write("\r")
