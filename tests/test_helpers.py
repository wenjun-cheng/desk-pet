"""The helpers Claude Code runs for the pet, each as its own process the way Claude Code starts it: the tools (an
MCP server), the status line, the permission hook (with a stand-in for the pet answering it)."""
import json
import subprocess
import threading

from PySide6.QtNetwork import QLocalServer

from conftest import wait
from deskpet import paths


def _run(mode: str, given: bytes, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(paths.helper_argv(mode), input=given, capture_output=True, timeout=timeout)


def test_tools():
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    out = _run("mcp", b"".join(json.dumps(m).encode() + b"\n" for m in messages))
    answers = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    tools = next(a for a in answers if a.get("id") == 2)["result"]["tools"]
    assert sorted(t["name"] for t in tools) == ["illustrator_script", "screenshot"], out.stderr.decode()


def test_status_line():
    given = {"session_id": "status-test", "model": {"display_name": "Opus 5.5"}, "effort": {"level": "high"},
             "context_window": {"used_percentage": 12.4}}
    out = _run("statusline", json.dumps(given).encode())
    assert out.stdout.decode("utf-8").startswith("Opus 5.5 · high · ctx 12%"), out.stderr.decode()
    assert json.loads((paths.STATUS / "status-test.json").read_text("utf-8"))["session_id"] == "status-test"


def _hook(qapp, decide):
    """Runs the hook with a permission request, the pet's server answering with decide(request)."""
    server = QLocalServer()
    QLocalServer.removeServer(paths.SERVER_NAME)
    assert server.listen(paths.SERVER_NAME)
    got = []

    def on_connection():
        sock = server.nextPendingConnection()

        def read():
            data = bytes(sock.readAll())
            if b"\n" in data:
                request = json.loads(data.split(b"\n")[0])
                got.append(request)
                sock.write(json.dumps({"decision": decide(request)}).encode() + b"\n")
                sock.flush()
        sock.readyRead.connect(read)
    server.newConnection.connect(on_connection)
    request = {"session_id": "s1", "hook_event_name": "PermissionRequest", "tool_name": "Bash",
               "tool_input": {"command": "rm old.txt"}}
    result = {}
    thread = threading.Thread(target=lambda: result.update(out=_run("hook", json.dumps(request).encode())))
    thread.start()
    while thread.is_alive():
        wait(50)
    server.close()
    return got, result["out"]


def test_hook_asks_the_pet(qapp):
    got, out = _hook(qapp, lambda r: {"behavior": "deny", "message": "rename it instead"})
    assert got and got[0]["type"] == "permission" and got[0]["tool_name"] == "Bash"
    answer = json.loads(out.stdout)
    assert answer == {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                             "decision": {"behavior": "deny", "message": "rename it instead"}}}


def test_hook_leaves_it_to_the_terminal(qapp):
    got, out = _hook(qapp, lambda r: None)
    assert got and out.stdout == b""


def test_hook_without_a_pet():
    out = _run("hook", json.dumps({"tool_name": "Bash"}).encode())
    assert out.returncode == 0 and out.stdout == b""
