"""The session's side of a message, against a fake Claude Code: a console with a prompt box to type into, and a
transcript and status file it writes the way Claude Code does."""
import json
import os
import threading
import time

import pytest

from conftest import TMP, wait
from deskpet.claude import session as session_module, transcript
from deskpet.claude.terminal import PASTE_END, PASTE_START

PID = os.getpid()


class FakeClaude:
    """Types land in the prompt box; Enter sends what's there, and the turn is written down as Claude Code does:
    the message, a tool call, the answer, the end of the turn. A slash command prints instead."""

    def __init__(self, sid: str, eat_first_enter: bool = False, dialog: str = ""):
        self.sid, self.typed, self.received = sid, "", []
        self.eat_first_enter, self.dialog = eat_first_enter, dialog
        transcript.SESSIONS.mkdir(parents=True, exist_ok=True)
        self.path = transcript.PROJECTS / "fake-project" / f"{sid}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", "utf-8")
        self.state("idle")

    def state(self, status: str):
        (transcript.SESSIONS / f"{PID}.json").write_text(json.dumps({"status": status}), "utf-8")

    def log(self, *entries):
        with open(self.path, "a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    # the console
    def write(self, data: str):
        if data.startswith(PASTE_START):
            self.typed = data[len(PASTE_START):-len(PASTE_END)]
        elif data in ("\x0b", "\x15"):
            self.typed = ""
        elif data == "\r":
            if self.eat_first_enter:
                self.eat_first_enter = False
                return
            text, self.typed = self.typed, ""
            self.received.append(text)
            threading.Thread(target=self._turn, args=(text,), daemon=True).start()

    def rows(self):
        return ["❯ " + self.typed, "─" * 40]

    def alive(self):
        return True

    def _turn(self, text: str):
        if text.startswith("/"):
            self.log({"type": "system", "content": f"<command-name>{text}</command-name>"},
                     {"type": "system", "content": "<local-command-stdout>Context 12k/200k</local-command-stdout>"})
            return
        self.state("busy")
        self.log({"type": "user", "message": {"role": "user", "content": text}})
        time.sleep(0.2)
        self.log({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "C:/notes/todo.md"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "..."}]}})
        if self.dialog:
            self.state(self.dialog)
            time.sleep(session_module.WAIT_NOTICE + 2)
            self.state("busy")
        time.sleep(0.2)
        self.log({"type": "assistant", "message": {"content": [{"type": "text", "text": "Three things to do."}]}},
                 {"type": "system", "subtype": "turn_duration"})
        self.state("idle")


@pytest.fixture
def claude_session(qapp, monkeypatch):
    from deskpet import desktop
    monkeypatch.setattr(desktop, "pid_alive", lambda pid: True)
    monkeypatch.setattr(session_module, "IDLE_DONE", 30)  # (ends by turn_duration in these tests)

    def make(fake: FakeClaude):
        s = session_module.ClaudeSession(TMP / "work", None, "bg1")
        monkeypatch.setattr(s, "_session", lambda: {"pid": PID, "sessionId": fake.sid})
        monkeypatch.setattr(s, "_console", lambda: fake)
        monkeypatch.setattr(s, "_current", lambda: fake)
        heard = {"progress": [], "replied": [], "asking": [], "failed": []}
        for name, items in heard.items():
            getattr(s, name).connect(lambda *a, items=items: items.append(a if len(a) > 1 else a[0]))
        return s, heard
    return make


def _until(check, seconds: float):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        wait(50)
    return check()


def test_message_and_answer(claude_session):
    fake = FakeClaude("sess-a")
    s, heard = claude_session(fake)
    s.send("what's on my list?", "todo.md (Notepad)")
    assert _until(lambda: heard["replied"], 10), heard
    assert fake.received == ["[Window: todo.md (Notepad)]\nwhat's on my list?"]
    assert heard["replied"] == ["Three things to do."]  # what came before a tool call isn't the answer
    assert heard["progress"] == [("Read", "C:/notes/todo.md")]
    assert s.session_id == "sess-a" and not s.busy


def test_enter_again_when_it_was_lost(claude_session, monkeypatch):
    monkeypatch.setattr(session_module, "ENTER_AGAIN", 0.5)
    fake = FakeClaude("sess-b", eat_first_enter=True)
    s, heard = claude_session(fake)
    s.send("hello")
    assert _until(lambda: heard["replied"], 10), heard
    assert fake.received == ["hello"]


def test_slash_command_prints(claude_session):
    fake = FakeClaude("sess-c")
    s, heard = claude_session(fake)
    s.send("/context", "ignored (for commands)")
    assert _until(lambda: heard["replied"], 10), heard
    assert fake.received == ["/context"]
    assert heard["replied"] == ["Context 12k/200k"]


def test_waiting_in_the_terminal(claude_session, monkeypatch):
    fake = FakeClaude("sess-d", dialog="waiting_for_permission")
    s, heard = claude_session(fake)
    s.send("tidy up")
    assert _until(lambda: heard["replied"], 12), heard
    assert heard["asking"] == ["waiting_for_permission", ""]


def test_failure_is_reported(claude_session, monkeypatch):
    fake = FakeClaude("sess-e")
    s, heard = claude_session(fake)
    monkeypatch.setattr(s, "_session", lambda: (_ for _ in ()).throw(RuntimeError("no_claude")))
    s.send("hi")
    assert _until(lambda: heard["failed"], 5)
    assert heard["failed"] == ["no_claude"] and not s.busy
