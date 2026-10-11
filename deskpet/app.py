"""Starting the pet: one per user (launched again, the running one hops), its log, its local server (for the
permission hook), the global shortcut, and the plan's usage, kept fresh for the menu."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from . import __version__, paths

log = logging.getLogger("pet")
USAGE_EVERY = 5 * 60 * 1000   # ms between readings of the plan's usage


def _send_to_running_pet(command: bytes) -> bool:
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(paths.SERVER_NAME)
    if not sock.waitForConnected(1000):
        return False
    sock.write(command)
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def _listen(pet, chat):
    """The pet's local server (a named pipe or socket): b"hop" from a second launch, or a permission request from
    the hook (claude/hook.py), which waits for the answer."""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    server = QLocalServer()
    QLocalServer.removeServer(paths.SERVER_NAME)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def on_connection():
        while sock := server.nextPendingConnection():
            received = bytearray()

            def answer(decision, sock=sock):
                if sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
                    sock.write(json.dumps({"decision": decision}).encode("utf-8") + b"\n")
                    sock.flush()
                    sock.disconnectFromServer()

            def read(sock=sock, received=received, answer=answer):
                received.extend(bytes(sock.readAll()))
                if bytes(received).strip() == b"hop":
                    received.clear()
                    pet.hop()
                    sock.disconnectFromServer()
                elif b"\n" in received:
                    line = bytes(received).split(b"\n", 1)[0]
                    received.clear()
                    try:
                        request = json.loads(line)
                    except ValueError:
                        sock.disconnectFromServer()
                        return
                    sock.disconnected.connect(lambda: chat.permission_gone(answer))
                    chat.ask_permission(request, answer)

            sock.readyRead.connect(read)
            sock.disconnected.connect(sock.deleteLater)
            read()  # what already arrived

    server.newConnection.connect(on_connection)
    if not server.listen(paths.SERVER_NAME):
        log.warning("The pet's local server didn't start: %s", server.errorString())
    return server


def _refresh_usage(cwd):
    """The plan's limits, from Claude Code's /usage (a few seconds, in the background)."""
    from .claude import usage

    def run():
        try:
            limits = usage.fetch(cwd, paths.clean_env(), paths.claude_exe())
            if limits:
                usage.save(paths.USAGE, limits)
        except Exception:
            log.warning("Couldn't read the plan's usage", exc_info=True)
    threading.Thread(target=run, name="usage", daemon=True).start()


def main():
    # Started from inside a Claude Code session: without the variables it sets for its own commands, so the
    # Claude behind the pet and the terminals it opens don't inherit them
    clean = paths.clean_env()
    for key in set(os.environ) - set(clean):
        del os.environ[key]
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"  # (Wayland doesn't let a window place itself; XWayland does)
    paths.DATA.mkdir(parents=True, exist_ok=True)
    for old in paths.STATUS.glob("*.json"):  # status line records of sessions not seen for a week
        if time.time() - old.stat().st_mtime > 7 * 86400:
            old.unlink(missing_ok=True)
    handler = RotatingFileHandler(paths.DATA / "app.log", maxBytes=500_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.excepthook = lambda *exc: log.critical("Unhandled exception", exc_info=exc)

    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtGui import QFontDatabase, QIcon
    from PySide6.QtWidgets import QApplication

    from . import desktop
    from .chat import Chat
    from .menu import PetMenu
    from .pet import Pet
    from .settings import Settings
    from .strings import set_language

    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName(paths.APP)
    qapp.setWindowIcon(QIcon(str(paths.ASSETS / "icon.ico")))
    lock = QLockFile(str(paths.DATA / "app.lock"))
    if not lock.tryLock(100):
        for _ in range(10):  # already running: make it hop (it may still be starting up)
            if _send_to_running_pet(b"hop"):
                break
            time.sleep(0.3)
        return
    desktop.setup_app()
    for font in (paths.ASSETS / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font))

    settings = Settings(paths.DATA / "settings.json")
    set_language(settings["language"])
    pet = Pet(settings)
    chat = Chat(pet, settings)
    chat.workspace.mkdir(parents=True, exist_ok=True)
    menu = PetMenu(chat, lambda: _refresh_usage(chat.workspace))
    pet.menu_requested.connect(menu.popup)
    pet.keep_out = lambda: chat.bubble.isVisible() or menu.isVisible()
    server = _listen(pet, chat)  # noqa: F841  (held here so it lives as long as the app)
    hotkey = desktop.Hotkey()
    hotkey.pressed.connect(chat.toggle)
    if not hotkey.ok:
        log.warning("Ctrl+Alt+Space isn't available (taken by another program, or not allowed here)")
    qapp.aboutToQuit.connect(chat.close)

    def warm_up():
        """The bubble and the menu each take half a second the first time they show (styles and fonts), which
        froze the pet on the first click or right-click: done now instead."""
        chat.bubble.warm_up()
        menu.warm_up()
    QTimer.singleShot(0, warm_up)
    usage_timer = QTimer(interval=USAGE_EVERY, timeout=lambda: _refresh_usage(chat.workspace))
    usage_timer.start()
    QTimer.singleShot(3000, lambda: _refresh_usage(chat.workspace))
    pet.show()
    log.info("Started v%s", __version__)
    sys.exit(qapp.exec())
