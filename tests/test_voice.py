"""Talking instead of typing, with a fake microphone and a fake Whisper: holding Ctrl+Alt+Space, the bubble's mic,
what the bubble and the face show meanwhile, and how what was heard is written into the line."""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from conftest import FakeRecorder, wait
from deskpet.strings import tr
from deskpet.voice import _problem, joined, tidy


def held(w, ms):
    w.chat.hotkey_down()
    wait(ms)


def test_hold_the_shortcut_to_talk(world):
    w = world
    held(w, 300)  # (held long enough, but the pet is still jumping out)
    assert w.pet.jumping() and not w.voice.listening
    wait(400)  # (landed, and the bubble has popped up: now it listens)
    assert w.bubble.isVisible() and w.bubble.input_open and w.bubble.line.isVisible()
    assert w.voice.listening and w.bubble.voice == "listening"
    assert w.chat.listening == 0.6
    look = w.pet._look(time.monotonic())
    assert look.eyes == "closed" and look.sound == 0.6  # eyes shut, listening hard
    w.chat.hotkey_up()
    assert not w.voice.listening
    wait(150)
    assert w.bubble.voice == "" and w.bubble.line.toPlainText() == "make it wider"
    assert w.bubble.isVisible() and w.bubble.line.isVisible()
    assert w.chat.listening is None and not w.chat.thinking


def test_what_was_typed_stays_in_view(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("please")
    geometry = w.bubble.line.geometry()
    w.bubble.mic_clicked.emit()
    assert w.bubble.line.isVisible() and w.bubble.line.toPlainText() == "please"
    assert w.bubble.line.geometry() == geometry  # (only the mic turned into a stop button)
    w.bubble.mic_clicked.emit()
    wait(150)
    assert w.bubble.line.toPlainText() == "please make it wider"


def test_a_press_still_opens_and_puts_away(world):
    w = world
    held(w, 60)
    w.chat.hotkey_up()
    wait(450)
    assert w.bubble.isVisible() and not w.voice.listening  # opened, and stays open
    held(w, 60)
    w.chat.hotkey_up()
    assert not w.bubble.isVisible() and not w.voice.listening  # put away


def test_holding_with_the_bubble_open_keeps_it(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("please")
    held(w, 350)
    w.chat.hotkey_up()
    wait(150)
    assert w.bubble.isVisible() and w.bubble.line.toPlainText() == "please make it wider"


def test_the_mic_starts_and_stops(world):
    w = world
    w.chat.toggle()
    wait(450)
    mic = w.bubble._mic_center()
    assert w.bubble._on_mic(mic) and not w.bubble._on_mic(mic + type(mic)(0, 40))
    w.bubble.line.setPlainText("把它")
    w.voice.transcriber.text = "改宽一点"
    w.bubble.mic_clicked.emit()
    assert w.voice.listening and w.bubble.voice == "listening"
    w.bubble.mic_clicked.emit()  # (the stop button by then)
    wait(150)
    assert w.bubble.line.toPlainText() == "把它改宽一点"  # no space between Chinese characters


def test_escape_drops_it(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("keep this")
    w.bubble.mic_clicked.emit()
    QTest.keyClick(w.bubble.line, Qt.Key.Key_Escape)  # (typed in the line, it reaches the bubble)
    wait(150)
    assert not w.voice.listening and w.bubble.voice == "" and w.bubble.line.toPlainText() == "keep this"


def test_the_first_time_it_says_why_it_takes_a_while(world):
    w = world
    w.voice.transcriber.ready = False
    w.chat.toggle()
    wait(450)
    w.chat._say("Hi!", "reply")
    w.bubble.mic_clicked.emit()
    assert w.bubble.message == tr("getting_model") and w.bubble.kind == "working"
    w.voice.transcriber.ready = True
    w.bubble.mic_clicked.emit()
    wait(150)
    assert w.bubble.message == "Hi!" and w.bubble.kind == "reply"  # back as it was
    assert w.bubble.line.toPlainText() == "make it wider"


def test_let_go_while_jumping_is_a_press(world):
    w = world
    held(w, 300)
    w.chat.hotkey_up()
    wait(500)
    assert w.bubble.isVisible() and not w.voice.listening and w.bubble.voice == ""


def test_nothing_heard(world):
    w = world
    w.voice.transcriber.text = ""
    held(w, 700)
    w.chat.hotkey_up()
    wait(150)
    assert w.bubble.line.toPlainText() == "" and w.bubble.line.placeholderText() == tr("heard_nothing")


def test_no_microphone(world):
    w = world
    FakeRecorder.fails = True
    w.chat.toggle()
    wait(450)
    w.bubble.mic_clicked.emit()
    assert not w.voice.listening and w.bubble.voice == ""
    assert w.bubble.kind == "error" and w.bubble.message == tr("no_mic")


def test_putting_it_away_stops_listening(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.mic_clicked.emit()
    w.chat.toggle()
    assert not w.voice.listening and not w.bubble.isVisible()


@pytest.mark.parametrize("raw, language, script, expected", [
    ("Make it wider .", "en", "Hans", "Make it wider."),
    ("幫我 把這個 改寬一點, 好嗎?", "zh", "Hans", "帮我把这个改宽一点，好吗？"),
    ("帮我把这个改宽一点", "zh", "Hant", "幫我把這個改寬一點"),
    ("把 M5 孔 改成 M6", "zh", "Hans", "把 M5 孔改成 M6"),
])
def test_tidy(raw, language, script, expected):
    assert tidy(raw, language, script) == expected


def test_joined():
    assert joined("", "hi") == "hi"
    assert joined("make it", "wider") == "make it wider"
    assert joined("make it ", "wider") == "make it wider"
    assert joined("把它", "改宽") == "把它改宽"
    assert joined("use", "改宽") == "use改宽"


def test_problems():
    assert _problem(ModuleNotFoundError("faster_whisper"), "e_voice") == "no_voice"
    assert _problem(OSError("PortAudio library not found"), "no_mic") == "no_portaudio"
    assert _problem(OSError("Connection error, can't reach huggingface.co"), "e_voice") == "e_model"
    assert _problem(RuntimeError("Error querying device -1"), "no_mic") == "no_mic"
