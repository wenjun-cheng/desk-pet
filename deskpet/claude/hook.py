"""Claude Code's PermissionRequest hook in the bubble's sessions (see launch.py). It passes the request to the
running pet, which asks in the bubble, and answers Claude Code with the user's reply. If the pet isn't running,
or leaves it to the terminal (another conversation; the user went there), it prints nothing, and the terminal
shows its usual prompt."""
from __future__ import annotations

import json
import sys
import time

from .. import paths


def main():
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
    _app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841  (Qt's sockets want one)
    sock = QLocalSocket()
    sock.connectToServer(paths.SERVER_NAME)
    if not sock.waitForConnected(1000):
        return  # no pet
    sock.write(json.dumps({"type": "permission", **request}).encode("utf-8") + b"\n")
    sock.waitForBytesWritten(1000)
    answer, deadline = b"", time.monotonic() + 600
    while b"\n" not in answer and time.monotonic() < deadline:
        if sock.waitForReadyRead(1000):
            answer += bytes(sock.readAll())
        elif sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
            break
    try:
        decision = json.loads(answer.split(b"\n", 1)[0].decode("utf-8")).get("decision")
    except ValueError:
        return
    if decision:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                            "decision": decision}}))
