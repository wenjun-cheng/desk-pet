"""The Claude behind the bubble: a Claude Code background session (`claude --bg`) in the pet's workspace, running
with no window at all. The bubble's messages are typed into a terminal attached to it (`claude attach`), as if
typed there: the pet's own, which nobody sees, or the one the user opened (the relay), once they have. A session
takes one attached terminal at a time, so opening it on screen takes it over. What Claude does and says comes from
the session's transcript."""
from __future__ import annotations

import logging
from dataclasses import dataclass
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import desktop, paths
from . import launch, transcript
from .relay import RelayLink
from .terminal import Console, PtyTerminal, press_enter, type_message, wait_for_prompt

log = logging.getLogger("pet.claude")
START_TIMEOUT = 40      # seconds for Claude to be ready in a new terminal or session
TRUST_TIMEOUT = 300     # seconds for the user to trust the workspace, the first time
ENTER_AGAIN = 4         # seconds without the message in the transcript before pressing Enter once more
RETYPE_AFTER = 9        # ...before typing it again
DELIVERY_TIMEOUT = 16   # ...before giving up
IDLE_DONE = 3           # seconds of "idle" after the message arrived that also count as the end of the turn
WAIT_NOTICE = 2         # seconds Claude has been waiting on the user before the bubble says so (by then a
                        # permission question the hook passed to the bubble is already showing there)
_SHOW, _CLEAR = object(), object()   # jobs besides messages: see show() and new_topic()



@dataclass(frozen=True)
class _Message:
    """A message to send, and the images (files) that go with it."""
    text: str
    images: tuple[str, ...] = ()

class ClaudeSession(QObject):
    progress = Signal(str, str)   # tool name, its input summary: what it's doing right now
    replied = Signal(str)         # Claude's answer (its last message of the turn)
    asking = Signal(str)          # it's waiting for the user in the terminal (a permission prompt, a menu); ""
                                  # once it isn't any more
    failed = Signal(str)          # what went wrong: a key of the pet's strings (e_..., no_claude) or the error itself
    located = Signal(int)         # show(): the conversation's window, already open, to bring to the front
    needs_trust = Signal()        # the workspace isn't trusted yet: the user has to say so, in a terminal
    cleared = Signal(str)         # new_topic(): the new conversation's session id
    started = Signal(str)         # a background session was started: its id (to keep)

    def __init__(self, cwd: Path, session_id: str | None, background_id: str | None):
        """session_id: the bubble's conversation from before (a new background session resumes it);
        background_id: its background session, if one was started before."""
        super().__init__()
        self.cwd = cwd
        self.env = paths.clean_env()
        self.session_id = session_id if session_id and transcript.transcript(session_id) else None
        self.background_id = background_id
        self.pid: int | None = None          # the background session's process (its status file)
        self.busy = False                    # on a message
        self._working = False                # on any job
        self._hidden: PtyTerminal | None = None      # the pet's own attached terminal
        self._relay: RelayLink | None = None         # the one the user opened
        self._relay_process: subprocess.Popen | None = None
        self._asked_trust = False
        self._fusion_up = False              # Fusion's MCP server was there when last looked
        self._lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()      # messages (str), _SHOW and _CLEAR, one at a time
        threading.Thread(target=self._work, name="claude", daemon=True).start()

    # ------------------------------------------------------------ from the pet

    def send(self, text: str, window: str = "", images: tuple[str, ...] = ()):
        """window: the window the user was in ('title (program)'), told to Claude on the first line (not for a
        slash command: it has to come first, or Claude Code takes it as text). images: files to attach."""
        self._jobs.put(_Message(f"[Window: {window}]\n{text}" if window and not text.startswith("/") else text,
                                tuple(images)))

    def new_topic(self):
        """A new conversation: /clear in the session (once it's done with what it's doing)."""
        self.session_id = None
        self._jobs.put(_CLEAR)

    def show(self):
        """The conversation on screen, in a terminal like any Claude terminal: brought to the front if it's open,
        else opened. At once, even while a job waits on the session (for an answer in it, say)."""
        if self._relay is not None and not self._relay_closed() and self._relay.alive():
            hwnd = desktop.terminal_window(self._relay_process.pid) if self._relay_process else 0
            if hwnd:
                self.located.emit(hwnd)
            return
        if self.background_id:
            self._open_relay()
        else:  # no session yet: started first
            self._jobs.put(_SHOW)

    def _relay_closed(self) -> bool:
        """Its terminal was closed: the relay's process ended (on Windows, where it's the relay itself; elsewhere
        it's what opened the terminal, which ends at once). Until then a just-opened relay counts as starting for
        a while, so double-clicking right after closing it did nothing, and messages went to it and were lost."""
        proc = self._relay_process
        return sys.platform == "win32" and proc is not None and proc.poll() is not None

    def close(self):
        """The pet is quitting: its own terminal goes (the session and the user's terminal stay)."""
        if self._hidden is not None:
            self._hidden.close()

    # ------------------------------------------------------------ terminals attached to the session

    def _open_relay(self):
        with self._lock:
            if self._hidden is not None:  # (it would be pushed off anyway)
                self._hidden.close()
                self._hidden = None
            argv = paths.helper_argv("relay", self.background_id)
            self._relay_process = desktop.open_terminal(argv, self.cwd, self.env)
            self._relay = RelayLink()
            log.info("Opened the conversation on screen (%s)", self.background_id)

    def _current(self) -> Console | None:
        """The attached terminal right now, if any (it changes when the user opens the conversation)."""
        if self._relay is not None and not self._relay_closed() and self._relay.alive():
            return self._relay
        if self._hidden is not None and self._hidden.alive():
            return self._hidden
        return None

    def _console(self) -> Console:
        """The terminal to type into, once Claude's prompt is up in it: the user's, if open, else the pet's own
        (attached now if there's none)."""
        with self._lock:
            console = self._current()
            if console is None:
                exe = paths.claude_exe()
                if not exe:
                    raise RuntimeError("no_claude")
                self._hidden = console = PtyTerminal([exe, "attach", self.background_id], self.cwd, self.env)
        if not wait_for_prompt(console, START_TIMEOUT):
            raise RuntimeError("e_start")
        return console

    # ------------------------------------------------------------ the background session

    def _background(self) -> dict:
        """The background session's entry in `claude agents`, after starting one (resuming the bubble's
        conversation) if there's none. The first time, the workspace has to be trusted, in a terminal."""
        entry = launch.background_session(self.cwd, self.env, self.background_id)
        if entry is not None and not launch.config_current():  # from an older pet: started again, to have it all
            log.info("The pet's additions to Claude changed: restarting the background session %s",
                     self.background_id)
            with self._lock:
                if self._hidden is not None:
                    self._hidden.close()
                    self._hidden = None
            launch.stop_background(self.cwd, self.env, self.background_id)
            self.session_id = entry.get("sessionId") or self.session_id  # (the conversation carries on)
            entry = None
        if entry is not None:
            return entry
        self.cwd.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + TRUST_TIMEOUT
        while True:
            try:
                self.background_id = launch.start_background(self.cwd, self.env, self.session_id)
                break
            except launch.NotTrusted:
                if time.monotonic() > deadline:
                    self._asked_trust = False  # (asked again next time)
                    raise RuntimeError("trust")
                if not self._asked_trust:
                    self._asked_trust = True
                    self.needs_trust.emit()
                time.sleep(3)
        log.info("Started the background session %s", self.background_id)
        self.started.emit(self.background_id)
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            entry = launch.background_session(self.cwd, self.env, self.background_id)
            if entry is not None:
                return entry
            time.sleep(0.5)
        raise TimeoutError("e_start")

    def _session(self) -> dict:
        """The background session's entry, running: a new or stopped one runs once a terminal attaches."""
        entry = self._background()
        deadline = time.monotonic() + START_TIMEOUT
        while not entry.get("pid") or not desktop.pid_alive(entry["pid"]):
            self._console()
            if time.monotonic() > deadline:
                raise TimeoutError("e_start")
            time.sleep(0.5)
            entry = launch.background_session(self.cwd, self.env, self.background_id) or entry
        return entry

    # ------------------------------------------------------------ in the worker thread

    def _work(self):
        while True:
            job = self._jobs.get()
            self.busy, self._working = isinstance(job, _Message), True
            try:
                if job is _SHOW:
                    self._background()
                    self._open_relay()
                elif job is _CLEAR:
                    self._clear()
                else:
                    self._ask(job.text, job.images)
            except Exception as e:
                log.exception("Talking to Claude failed")
                if isinstance(job, _Message):
                    self.failed.emit(str(e)[:160])
            finally:
                self.busy = self._working = False

    def _clear(self):
        if launch.background_session(self.cwd, self.env, self.background_id) is None:
            return  # nothing to clear: with no session id, the next message starts a new one
        entry = self._session()
        pid, old = entry["pid"], entry.get("sessionId")
        deadline = time.monotonic() + 600
        while transcript.status(pid) == "busy" or transcript.waiting(transcript.status(pid)):  # let it finish
            if not desktop.pid_alive(pid) or time.monotonic() > deadline:
                return
            time.sleep(0.5)
        type_message(self._console(), "/clear")
        for _ in range(20):
            time.sleep(0.5)
            now = launch.background_session(self.cwd, self.env, self.background_id)
            if now and now.get("sessionId") != old:
                self.pid, self.session_id = pid, now["sessionId"]
                self.cleared.emit(self.session_id)
                return
        log.warning("/clear didn't start a new conversation")

    def _connect_fusion(self):
        """Fusion's MCP server, if it has come up since the pet last looked: Claude Code gives up on a server that
        wasn't there when the session started, so it's told to connect again (before the message, quietly)."""
        up = launch.fusion_open()
        if up and not self._fusion_up:
            log.info("Fusion is open: reconnecting its MCP server")
            follower = transcript.Follower(transcript.transcript(self.session_id))
            if type_message(self._console(), "/mcp reconnect fusion"):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not any(
                        transcript.command_output(e) for e in follower.new_entries(self.session_id)):
                    time.sleep(0.3)
        self._fusion_up = up

    def _ask(self, text: str, images: tuple[str, ...] = ()):
        """Type the message into the session's terminal and follow the transcript until Claude has answered. A
        slash command's answer is what it prints; one that opens a menu in the terminal waits for the user there."""
        command = text.startswith("/")
        entry = self._session()
        self.pid, self.session_id = entry["pid"], entry["sessionId"]
        noticed = False
        while transcript.waiting(transcript.status(self.pid)):  # a dialog is up: wait until it's answered there
            if not noticed:
                self.asking.emit(transcript.status(self.pid))
                noticed = True
            if not desktop.pid_alive(self.pid):
                raise RuntimeError("e_closed")
            time.sleep(0.5)
        if noticed:
            self.asking.emit("")
        if not command:
            self._connect_fusion()
        follower = transcript.Follower(transcript.transcript(self.session_id))
        if not type_message(self._console(), text, images):
            raise RuntimeError("e_type")
        sent_at = time.monotonic()
        entered = retyped = arrived = asked = False
        idle_since = waiting_since = None
        reply: list[str] = []
        printed: list[str] = []  # a slash command's output
        while True:
            time.sleep(0.3)
            for e in follower.new_entries(self.session_id):
                kind, message = e.get("type"), e.get("message") or {}
                content = message.get("content")
                output = transcript.command_output(e) if command else None
                if output is not None:  # the command ran (it's in the transcript once it has)
                    arrived = True
                    if output:
                        printed.append(output)
                    continue
                if kind == "user":
                    if isinstance(content, str) or any(isinstance(b, dict) and b.get("type") == "text"
                                                       for b in content or []):
                        arrived = True  # the message (or one typed in the terminal meanwhile)
                    elif arrived:
                        reply = []      # a tool result: what was said before it was on the way, not the answer
                elif kind == "assistant" and arrived:
                    for block in content or []:
                        if block.get("type") == "tool_use":
                            self.progress.emit(block.get("name", ""), transcript.summary(block.get("input") or {}))
                        elif block.get("type") == "text" and block.get("text", "").strip():
                            reply.append(block["text"].strip())
                elif kind == "system" and e.get("subtype") == "turn_duration" and arrived:
                    self.replied.emit("\n\n".join(printed + reply))
                    return
            if not desktop.pid_alive(self.pid):
                raise RuntimeError("e_closed")
            waited = time.monotonic() - sent_at
            state = transcript.status(self.pid)
            if not arrived:
                if transcript.waiting(state):  # never press keys into a dialog
                    if command and not asked:  # the command's menu, in the terminal: the user picks there
                        self.asking.emit(state)
                        asked = True
                    continue
                if asked:  # the menu closed
                    self.asking.emit("")
                    asked = False
                if command:  # (pressing Enter again or retyping could run it twice)
                    if waited > DELIVERY_TIMEOUT:
                        self.replied.emit("")
                        return
                    continue
                if waited > ENTER_AGAIN and not entered:
                    console = self._current()
                    if console is not None:
                        press_enter(console)  # the Enter may have been taken as part of the text
                    entered = True
                elif waited > RETYPE_AFTER and not retyped:
                    log.info("The message hasn't arrived; typing it again")
                    type_message(self._console(), text, images)
                    retyped = True
                elif waited > DELIVERY_TIMEOUT:
                    console = self._current()
                    log.warning("The message didn't arrive. The terminal shows:\n%s",
                                "\n".join(r for r in (console.rows() if console else []) if r.strip())[-2000:])
                    raise TimeoutError("e_deliver")
                continue
            if transcript.waiting(state):
                waiting_since = waiting_since or time.monotonic()
                if time.monotonic() - waiting_since > WAIT_NOTICE and not asked:
                    self.asking.emit(state)  # e.g. a permission prompt in the terminal
                    asked = True
            else:
                if asked:
                    self.asking.emit("")  # answered in the terminal: back to work
                waiting_since = None
                asked = False
            if state == "idle":
                idle_since = idle_since or time.monotonic()
                # ended without the usual marker (a command that only prints: right after it has)
                if time.monotonic() - idle_since > (0.6 if command and printed and not reply else IDLE_DONE):
                    self.replied.emit("\n\n".join(printed + reply))
                    return
            else:
                idle_since = None
