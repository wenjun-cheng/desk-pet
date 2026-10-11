"""The terminal the user opens the conversation in. It runs `claude attach <id>` in a pseudo-terminal and passes
everything through, so it looks and works like Claude Code in any terminal; and it lets the pet type into it and
read its screen (over a local pipe or socket), so the bubble keeps working while it's open.

The pet's side of that is RelayLink."""
from __future__ import annotations

import getpass
import os
import secrets
import sys
import threading
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path

from .. import paths
from .terminal import PtyTerminal

if sys.platform == "win32":
    ADDRESS, FAMILY = rf"\\.\pipe\deskpet-relay-{getpass.getuser()}", "AF_PIPE"
else:
    ADDRESS, FAMILY = str(paths.DATA / "relay.sock"), "AF_UNIX"
KEY = paths.DATA / "relay.key"  # only the pet (the same user) can connect


# ---------------------------------------------------------------- the terminal the relay runs in

class _WindowsConsole:
    """The console in raw mode: keys arrive as the terminal's own escape sequences, and output is drawn as
    escape sequences (both UTF-8)."""

    def __enter__(self):
        import ctypes
        from ctypes import wintypes
        self.k32 = k32 = ctypes.WinDLL("kernel32")
        self.wintypes = wintypes
        k32.GetStdHandle.restype = wintypes.HANDLE
        k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.ReadConsoleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                     ctypes.c_void_p]
        k32.WriteConsoleW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.inp, self.out = k32.GetStdHandle(-10), k32.GetStdHandle(-11)
        self.saved = []
        for handle, mode in ((self.inp, 0x0200),           # ENABLE_VIRTUAL_TERMINAL_INPUT, nothing processed
                             (self.out, 0x0001 | 0x0004 | 0x0008)):  # processed, VT processing, no auto return
            old = wintypes.DWORD()
            k32.GetConsoleMode(handle, ctypes.byref(old))
            self.saved.append((handle, old.value))
            k32.SetConsoleMode(handle, mode)
        self.cp = (k32.GetConsoleCP(), k32.GetConsoleOutputCP())
        k32.SetConsoleCP(65001)
        k32.SetConsoleOutputCP(65001)
        return self

    def __exit__(self, *_):
        for handle, mode in self.saved:
            self.k32.SetConsoleMode(handle, mode)
        self.k32.SetConsoleCP(self.cp[0])
        self.k32.SetConsoleOutputCP(self.cp[1])

    def read(self) -> str:
        import ctypes
        buf, n = ctypes.create_unicode_buffer(1024), self.wintypes.DWORD()
        if not self.k32.ReadConsoleW(self.inp, buf, 1024, ctypes.byref(n), None):
            raise EOFError
        return buf[:n.value]

    def write(self, text: str):
        import ctypes
        units = len(text.encode("utf-16-le")) // 2
        self.k32.WriteConsoleW(self.out, text, units, ctypes.byref(self.wintypes.DWORD()), None)

    def size(self) -> tuple[int, int]:
        size = os.get_terminal_size()
        return size.lines, size.columns


class _PosixTerminal:
    """The terminal in raw mode (keys passed on as they come)."""

    def __enter__(self):
        import termios
        import tty
        self.fd = sys.stdin.fileno()
        self.saved = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        import codecs
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        return self

    def __exit__(self, *_):
        import termios
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def read(self) -> str:
        data = os.read(self.fd, 1024)
        if not data:
            raise EOFError
        return self.decoder.decode(data)

    def write(self, text: str):
        sys.stdout.write(text)
        sys.stdout.flush()

    def size(self) -> tuple[int, int]:
        size = os.get_terminal_size()
        return size.lines, size.columns


def _serve(term: PtyTerminal):
    """The pet's requests: ("write", text) types into Claude; ("rows",) answers with what the screen shows."""
    key = secrets.token_bytes(32)
    if FAMILY == "AF_UNIX":
        if _taken():  # another relay has it: this one only shows the conversation
            return
        Path(ADDRESS).unlink(missing_ok=True)  # (left over)
    try:
        listener = Listener(ADDRESS, FAMILY, authkey=key)
    except OSError:  # (the same, on Windows)
        return
    paths.DATA.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    while term.alive():
        try:
            conn = listener.accept()
        except (OSError, EOFError):
            continue
        threading.Thread(target=_answer, args=(conn, term), daemon=True).start()


def _taken() -> bool:
    import socket
    with socket.socket(socket.AF_UNIX) as s:
        try:
            s.connect(ADDRESS)
            return True
        except OSError:
            return False


def _answer(conn, term: PtyTerminal):
    try:
        while True:
            request = conn.recv()
            if request[0] == "write":
                term.write(request[1])
            elif request[0] == "rows":
                conn.send(term.rows())
            elif request[0] == "ping":
                conn.send(True)
    except (EOFError, OSError):
        conn.close()


def main(background_id: str):
    """Runs in the terminal the pet opened, until Claude's attached client exits (Ctrl+Z, or another terminal
    taking the session over)."""
    console_type = _WindowsConsole if sys.platform == "win32" else _PosixTerminal
    with console_type() as console:
        rows, cols = console.size()
        term = PtyTerminal([paths.claude_exe() or "claude", "attach", background_id], Path.cwd(), dict(os.environ),
                           rows=rows, cols=cols, echo=console.write)

        def pump():
            while term.alive():
                try:
                    term.write(console.read())
                except (EOFError, OSError):
                    return

        threading.Thread(target=pump, name="keys", daemon=True).start()
        threading.Thread(target=_serve, args=(term,), name="pet", daemon=True).start()
        while term.alive():
            time.sleep(0.25)
            now = console.size()
            if now != (rows, cols):
                rows, cols = now
                term.resize(rows, cols)
    os._exit(0)  # (the threads blocked on reads would hold it open)


# ---------------------------------------------------------------- the pet's side

class RelayLink:
    """The pet's end of a relay: type into the terminal the user has open, read its screen."""
    START = 15  # seconds a just-opened relay has to start listening

    def __init__(self):
        self.opened_at = time.monotonic()
        self._conn = None
        self._lock = threading.Lock()

    def _connection(self):
        if self._conn is None:
            try:
                self._conn = Client(ADDRESS, FAMILY, authkey=KEY.read_bytes())
            except (OSError, EOFError, ValueError):
                self._conn = None
        return self._conn

    def _ask(self, request):
        with self._lock:
            conn = self._connection()
            if conn is None:
                raise EOFError
            try:
                conn.send(request)
                return conn.recv() if request[0] != "write" else None
            except (OSError, EOFError):
                self._conn = None
                raise EOFError

    def alive(self) -> bool:
        """Open and listening (or just opened, and still starting)."""
        try:
            return bool(self._ask(("ping",)))
        except EOFError:
            return time.monotonic() - self.opened_at < self.START

    def starting(self) -> bool:
        try:
            self._ask(("ping",))
            return False
        except EOFError:
            return time.monotonic() - self.opened_at < self.START

    def write(self, text: str):
        try:
            self._ask(("write", text))
        except EOFError:
            pass

    def rows(self) -> list[str]:
        try:
            return self._ask(("rows",))
        except EOFError:
            return []

    def close(self):
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
