"""Talking through the pet, with a fake mouse and a fake Claude: what a click, a double click and the shortcut do,
what the bubble shows and when it pops up by itself, the face's mood, permission questions."""
from PySide6.QtCore import QPoint, Qt

from conftest import wait
from deskpet.strings import tr


class _Release:
    def button(self):
        return Qt.MouseButton.LeftButton


def click(w):
    w.pet.press = QPoint(0, 0)
    w.pet.mouseReleaseEvent(_Release())


def double_click(w):
    click(w)
    w.pet.mouseDoubleClickEvent(_Release())


def test_click_opens_and_puts_away(world):
    w = world
    wait(100)
    assert abs(w.pet.sink - w.pet.PEEK) < 0.5 and not w.bubble.isVisible()
    click(w)
    assert w.pet.jumping()
    wait(450)
    assert w.bubble.isVisible() and w.bubble.input_open
    assert w.pet.sink < 0.5  # out
    click(w)
    assert not w.bubble.isVisible()
    wait(500)
    assert w.pet.sink > w.pet.PEEK - 1  # back behind the edge


def test_shortcut_while_jumping_calls_it_off(world):
    w = world
    w.chat.toggle()
    wait(60)
    w.chat.toggle()  # before the bubble came up
    wait(450)
    assert not w.bubble.isVisible()


def test_bubble_grows_wider_then_taller(world):
    w = world
    w.chat.toggle()
    wait(450)
    empty = w.bubble.width()
    assert empty <= 200
    w.bubble.line.setPlainText("Make the selected title dark blue")
    wider = w.bubble.width()
    assert wider > empty
    w.bubble.line.setPlainText("Make the selected title dark blue, a bit larger, and centered")
    assert w.bubble.width() == w.bubble.MAX_W
    two_lines = w.bubble.height()
    w.bubble.line.setPlainText("long " * 400)
    assert w.bubble.height() > two_lines
    capped = w.bubble.height()
    w.bubble.line.setPlainText("long " * 800)
    assert w.bubble.height() == capped  # it scrolls from there


def test_message_reply_and_mood(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("hello")
    w.bubble._send()
    assert w.claude.sent == [("hello", "")]
    assert w.chat.thinking and w.bubble.kind == "working"
    w.claude.answer("Hi!")
    assert w.bubble.message == "Hi!" and w.bubble.kind == "reply"
    assert not w.chat.done and w.chat.happy_until > 0  # seen at once: ^ ^
    assert not w.chat.thinking


def test_reply_while_away_waits(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("do it")
    w.bubble._send()
    w.chat.toggle()  # put away
    assert not w.bubble.isVisible()
    w.claude.answer("Done.")
    assert not w.bubble.isVisible()  # no popping up by itself
    assert w.chat.done  # !
    wait(50)
    assert w.pet.mark == "!"
    w.chat.toggle()
    wait(450)
    assert w.bubble.isVisible() and w.bubble.message == "Done."
    assert not w.chat.done


def test_failure_waits_with_a_sweat_drop(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("go")
    w.bubble._send()
    w.chat.toggle()
    w.claude.failed.emit("e_deliver")
    assert not w.bubble.isVisible() and w.bubble.kind == "error"
    assert w.bubble.message == tr("e_deliver")
    assert w.chat.sweat_since is not None
    w.chat.toggle()
    wait(450)
    assert w.chat.sweat_since is None


def test_double_click_opens_the_conversation_and_keeps_the_bubble(world):
    w = world
    double_click(w)  # the first click brings the bubble out; the double click undoes that
    wait(450)
    assert w.claude.shown == 1 and not w.bubble.isVisible()
    w.chat.toggle()
    wait(450)
    assert w.bubble.isVisible()
    double_click(w)  # the first click puts it away; the double click brings it back
    wait(450)
    assert w.claude.shown == 2 and w.bubble.isVisible()


def test_window_goes_with_the_message(world, monkeypatch):
    from deskpet import desktop
    w = world
    monkeypatch.setattr(desktop, "foreground", lambda: 42)
    monkeypatch.setattr(desktop, "is_work_window", lambda h: h == 42)
    monkeypatch.setattr(desktop, "window_exists", lambda h: h == 42)
    monkeypatch.setattr(desktop, "describe", lambda h: "Report.docx (WINWORD.EXE)")
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("fix this")
    w.bubble._send()
    assert w.claude.sent == [("fix this", "Report.docx (WINWORD.EXE)")]


def test_new_conversation(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.chat._say("old answer", "reply")
    w.bubble.line.setPlainText("/clear")
    w.bubble._send()
    assert w.claude.new_topics == 1 and not w.claude.sent
    assert w.bubble.message == "" and w.chat.settings["session"] is None


def test_permission_in_the_bubble(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("delete old.txt")
    w.bubble._send()
    w.chat.toggle()  # away: a question pops it up anyway
    answers = []
    w.chat.ask_permission({"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "rm old.txt"}},
                          answers.append)
    wait(300)
    assert w.bubble.isVisible() and w.bubble.kind == "question" and "rm old.txt" in w.bubble.message
    assert w.chat.waiting and not w.chat.thinking
    w.bubble.line.setPlainText("不要删，改名成 old.bak")
    w.bubble._send()
    assert answers == [{"behavior": "deny", "message": "The user didn't allow it. They said: 不要删，改名成 old.bak"}]
    assert w.claude.sent == [("delete old.txt", "")]  # the answer isn't a new message
    w.chat.ask_permission({"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "mv"}}, answers.append)
    w.bubble.line.setPlainText("可以")
    w.bubble._send()
    assert answers[-1] == {"behavior": "allow"}


def test_permission_left_to_the_terminal(world):
    w = world
    answers = []
    w.chat.ask_permission({"session_id": "s1", "tool_name": "Bash"}, answers.append)  # no Claude yet
    assert answers == [None]
    w.chat.session.busy = True
    w.chat.ask_permission({"session_id": "other", "tool_name": "Bash"}, answers.append)  # another conversation
    assert answers == [None, None]


def test_question_in_the_terminal(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("/model")
    w.bubble._send()
    w.claude.asking.emit("waiting_for_input")
    assert w.chat.waiting and w.bubble.kind == "question" and w.bubble.message == tr("in_terminal")
    w.bubble.line.setPlainText("opus")
    w.bubble._send()  # answers go to the terminal: it stays in the line
    assert w.bubble.line.toPlainText() == "opus" and len(w.claude.sent) == 1
    w.claude.asking.emit("")
    assert not w.chat.waiting and w.bubble.kind == "working"


def test_carried_bubble_comes_back(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.pet.picked_up.emit()
    assert not w.bubble.isVisible()
    w.pet.landed.emit()
    wait(300)
    assert w.bubble.isVisible() and w.bubble.input_open
