"""The tests run the pet without touching the real desktop or Claude Code: its data and Claude Code's go to a
temporary folder, Qt draws off screen (set DESKPET_TEST_SCREEN=1 to watch), and the mouse, the other windows,
the microphone, Whisper and Claude are fakes."""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="deskpet-test-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ["DESKPET_DATA"] = str(_TMP / "data")
os.environ["CLAUDE_CONFIG_DIR"] = str(_TMP / "claude")
os.environ["DESKPET_SERVER"] = f"DeskPet-test-{os.getpid()}"
if not os.environ.get("DESKPET_TEST_SCREEN"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from PySide6.QtCore import QEventLoop, QObject, QPoint, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from deskpet import paths  # noqa: E402

TMP = _TMP


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    for font in (paths.ASSETS / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font))
    return app


def wait(ms: int):
    """Let Qt run for a while. (Not QTest.qWait: it holds Python's GIL, so the pet's own threads barely run.)"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class Mouse:
    """The pointer and its main button, as the pet sees them."""

    def __init__(self):
        self.at, self.down = QPoint(5, 5), False

    def pos(self):
        return QPoint(self.at)


class FakeSession(QObject):
    """ClaudeSession's signals and calls, recorded."""
    progress = Signal(str, str)
    replied = Signal(str)
    asking = Signal(str)
    failed = Signal(str)
    located = Signal(int)
    needs_trust = Signal()
    cleared = Signal(str)
    started = Signal(str)

    def __init__(self, cwd, session_id, background_id):
        super().__init__()
        self.cwd, self.env = cwd, {}
        self.session_id = session_id or "s1"
        self.busy = False
        self.sent, self.shown, self.new_topics = [], 0, 0

    def send(self, text, window="", images=()):
        self.sent.append((text, window) if not images else (text, window, images))
        self.busy = True

    def show(self):
        self.shown += 1

    def new_topic(self):
        self.new_topics += 1

    def close(self):
        pass

    def answer(self, text):
        self.busy = False
        self.replied.emit(text)


class FakeRecorder:
    """The microphone: a second of sound at a steady level (or no microphone at all: fails)."""
    fails = False

    def start(self):
        if FakeRecorder.fails:
            raise RuntimeError("Error querying device -1")

    def level(self):
        return 0.6

    def stop(self):
        import numpy as np
        return np.zeros(16000, np.float32)


class FakeTranscriber:
    """Whisper: hears `text`."""

    def __init__(self):
        self.text = "make it wider"
        self.ready = True

    def downloaded(self):
        return self.ready

    def load(self):
        pass

    def forget_if_unused(self):
        pass

    def __call__(self, audio):
        return self.text


class World:
    def __init__(self, pet, chat, mouse):
        self.pet, self.chat, self.mouse = pet, chat, mouse
        self.bubble = chat.bubble
        self.voice = chat.voice

    @property
    def claude(self) -> FakeSession:
        return self.chat.session


@pytest.fixture
def world(qapp, monkeypatch, tmp_path):
    """A pet with its bubble, a fake mouse and a fake Claude. Nothing outside the pet's windows is touched."""
    from deskpet import chat as chat_module, desktop, pet as pet_module
    from deskpet.chat import Chat
    from deskpet.pet import Pet
    from deskpet.settings import Settings
    from deskpet.strings import set_language
    from deskpet.voice import Voice

    mouse = Mouse()
    monkeypatch.setattr(pet_module, "QCursor", mouse)
    monkeypatch.setattr(desktop, "mouse_button_down", lambda: mouse.down)
    monkeypatch.setattr(desktop, "foreground", lambda: 0)
    monkeypatch.setattr(desktop, "give_focus", lambda w: None)
    monkeypatch.setattr(desktop, "restore_focus", lambda w: None)
    monkeypatch.setattr(desktop, "keep_on_top", lambda ws: None)
    monkeypatch.setattr(desktop, "idle_seconds", lambda: 0.0)
    monkeypatch.setattr(chat_module.paths, "claude_exe", lambda: None)
    set_language("en")
    settings = Settings(tmp_path / "settings.json")
    settings["workspace"] = str(tmp_path / "work")
    pet = Pet(settings)
    FakeRecorder.fails = False
    chat = Chat(pet, settings, make_session=FakeSession, make_voice=lambda: Voice(FakeRecorder, FakeTranscriber()))
    pet.keep_out = lambda: chat.bubble.isVisible()
    pet.show()
    yield World(pet, chat, mouse)
    chat.bubble.close()
    pet.close()
    pet.frame_timer.stop()
    chat.target_timer.stop()
