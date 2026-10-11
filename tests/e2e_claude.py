"""End to end with the real Claude Code. Not run by pytest: it starts real sessions and uses a little of the plan.

    python tests/e2e_claude.py

Needs Claude Code signed in, and no Desk Pet running (this answers the pet's permission questions itself). It
starts in a new folder Claude Code doesn't trust yet and says yes to trusting it in a terminal of its own, then
goes through: a message, the pet's tools and instructions, a permission question answered from here, a slash
command, a new conversation, the conversation on screen in a terminal (a window opens) typed into from here, and
that terminal closed. At the end it deletes the session and the folder's Claude Code state."""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtNetwork import QLocalServer  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from deskpet import desktop, paths  # noqa: E402
from deskpet.claude.relay import RelayLink  # noqa: E402
from deskpet.claude.session import ClaudeSession  # noqa: E402
from deskpet.claude.terminal import PtyTerminal  # noqa: E402

app = QApplication([])
WORK = Path(tempfile.mkdtemp(prefix="deskpet-e2e-"))
t0 = time.monotonic()
results = []
heard = {"replied": [], "failed": [], "asking": [], "progress": [], "started": [], "cleared": [], "trust": []}
permissions = []


def note(text):
    print(f"{time.monotonic() - t0:6.1f}s  {text}", flush=True)


def check(label, ok, detail=""):
    results.append(ok)
    note(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))


def until(test, seconds):
    """Let Qt run until test() (not with QTest.qWait: it holds Python's GIL, and the session's thread starves)."""
    deadline = time.monotonic() + seconds
    while not test() and time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
    return test()


def ask(text, seconds=180) -> str:
    heard["replied"].clear()
    heard["failed"].clear()
    note(f"> {text}")
    session.send(text)
    until(lambda: heard["replied"] or heard["failed"], seconds)
    if heard["failed"]:
        note(f"  failed: {heard['failed'][0]}")
        return ""
    reply = heard["replied"][0] if heard["replied"] else ""
    note(f"  < {reply[:200]!r}")
    return reply


# ------------------------------------------------------------ the pet's side: permission questions

server = QLocalServer()
QLocalServer.removeServer(paths.SERVER_NAME)
assert server.listen(paths.SERVER_NAME), "another Desk Pet is running: quit it first"


def on_connection():
    sock = server.nextPendingConnection()
    buf = bytearray()

    def read():
        buf.extend(bytes(sock.readAll()))
        if b"\n" in buf:
            request = json.loads(bytes(buf).split(b"\n")[0])
            permissions.append(request)
            note(f"  permission asked: {request.get('tool_name')} {json.dumps(request.get('tool_input'))[:100]}")
            sock.write(json.dumps({"decision": {"behavior": "allow"}}).encode() + b"\n")
            sock.flush()
    sock.readyRead.connect(read)


server.newConnection.connect(on_connection)


# ------------------------------------------------------------ the first time: trusting the folder

def trust():
    """What the user does in the terminal the pet opens: "Yes, I trust this folder" (the second choice; the
    first, which Enter alone picks, is "No, exit")."""
    heard["trust"].append(1)
    note("  the folder isn't trusted: saying yes in a terminal")

    def chosen(term) -> str:
        return next((r for r in term.rows() if r.lstrip().startswith("❯")), "")

    def run():
        term = PtyTerminal([paths.claude_exe()], WORK, paths.clean_env())
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and "I trust this folder" not in "".join(term.rows()):
            time.sleep(0.3)
        for _ in range(5):
            if "trust" in chosen(term):
                break
            term.write("\x1b[B")  # down
            time.sleep(0.4)
        note(f"  chose: {chosen(term).strip()}")
        term.write("\r")
        time.sleep(5)
        term.close()
    threading.Thread(target=run, daemon=True).start()


# Claude Code asks before running commands here (not in auto mode, if that's the user's default), so the
# permission hook gets a question
(WORK / ".claude").mkdir()
(WORK / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"defaultMode": "default"}}), "utf-8")
session = ClaudeSession(WORK, None, None)
for name in ("replied", "failed", "asking", "progress", "started", "cleared"):
    getattr(session, name).connect(lambda *a, name=name: heard[name].append(a if len(a) > 1 else a[0]))
session.needs_trust.connect(trust)

try:
    reply = ask("Reply with exactly the word PONG and nothing else.", 300)
    check("first message, in a folder that had to be trusted", "PONG" in reply and bool(heard["trust"]))
    check("a background session was started", bool(heard["started"]), str(heard["started"]))

    reply = ask("List the names of the tools you have from the MCP server named pet, comma-separated, nothing else.")
    check("the pet's tools are there (--mcp-config)", "screenshot" in reply and "illustrator" in reply)

    reply = ask("Per your instructions, what is the little desktop app whose speech bubble shows your replies "
                "called? Answer with just its name.")
    check("the pet's instructions are there (--append-system-prompt-file)", "desk pet" in reply.lower())

    reply = ask("Use a shell command (the Bash or PowerShell tool, not the Write tool) to create a file named "
                "hook.txt containing the word ok in the current folder. Then reply DONE.")
    check("a permission question went to the pet (--settings hook)", bool(permissions), f"{len(permissions)} asked")
    check("...and its answer was followed", (WORK / "hook.txt").exists())

    sid = session.session_id
    status = paths.STATUS / f"{sid}.json"
    check("the status line recorded the session (--settings statusLine)", status.exists())

    reply = ask("/context")
    check("a slash command's output comes back", bool(reply.strip()), reply[:60])

    heard["cleared"].clear()
    session.new_topic()
    until(lambda: heard["cleared"], 60)
    check("new conversation (/clear)", bool(heard["cleared"]) and heard["cleared"][0] != sid, str(heard["cleared"]))

    session.show()  # the conversation in a terminal window
    until(lambda: session._relay is not None and not session._relay.starting(), 30)
    check("the conversation opened in a terminal", session._relay is not None and session._relay.alive())
    reply = ask("Reply with exactly the word RELAY and nothing else.")
    check("typed into the terminal the user has open", "RELAY" in reply and isinstance(session._current(), RelayLink))

    hwnd = desktop.terminal_window(session._relay_process.pid)
    check("its window was found (to bring it to the front)", bool(hwnd), hex(hwnd))
    if hwnd:
        import ctypes
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: the user closes it
    until(lambda: not session._relay.alive(), 30)
    reply = ask("Reply with exactly the word BACK and nothing else.")
    check("after it's closed, the bubble still works", "BACK" in reply)
finally:
    session.close()
    claude = paths.claude_exe()
    if session.background_id:
        subprocess.run([claude, "rm", session.background_id], capture_output=True, cwd=str(WORK))
    subprocess.run([claude, "purge", "-y", str(WORK)], capture_output=True)
    shutil.rmtree(WORK, ignore_errors=True)
    note("cleaned up")
    print(f"\n{sum(results)}/{len(results)} passed")
