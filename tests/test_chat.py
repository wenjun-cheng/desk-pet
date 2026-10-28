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
    assert empty <= 200 + 2 * w.bubble.ROOM
    w.bubble.line.setPlainText("Make the selected title dark blue")
    wider = w.bubble.width()
    assert wider > empty
    w.bubble.line.setPlainText("Make the selected title dark blue, a bit larger, and centered")
    assert w.bubble.width() == w.bubble.MAX_W + 2 * w.bubble.ROOM
    two_lines = w.bubble.height()
    w.bubble.line.setPlainText("long " * 400)
    assert w.bubble.height() > two_lines
    capped = w.bubble.height()
    w.bubble.line.setPlainText("long " * 800)
    assert w.bubble.height() == capped  # it scrolls from there


def test_the_tail_sits_by_the_bubbles_corner(world):
    """Wherever the pet is, the bubble reaches away from it, its tail by the corner over the pet."""
    from PySide6.QtGui import QGuiApplication
    w = world
    w.chat.toggle()
    wait(450)
    middle = QGuiApplication.primaryScreen().availableGeometry().center()
    for flipped in (False, True):
        w.bubble.point_at(middle, flipped)
        w.bubble._relayout()
        width = w.bubble.width() - 2 * w.bubble.ROOM
        assert w.bubble.tail_x == (width - w.bubble.TAIL_IN if flipped else w.bubble.TAIL_IN)


def test_scrolls_only_past_five_lines(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("\n".join("line" for _ in range(5)))
    assert w.bubble.scrolls == (False, False) and w.bubble.line.verticalScrollBar().maximum() == 0
    five = w.bubble.line.height()
    w.bubble.line.setPlainText("\n".join("line" for _ in range(6)))
    assert w.bubble.scrolls == (False, True) and w.bubble.line.height() == five
    w.chat._say("\n".join("reply" for _ in range(5)), "reply")
    assert w.bubble.scrolls[0] is False


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



def test_the_last_reply_is_there_after_a_restart(world):
    from deskpet.chat import Chat
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("hello")
    w.bubble._send()
    w.claude.answer("Hi!")
    again = Chat(w.pet, w.chat.settings, make_session=type(w.claude), make_voice=lambda: w.chat.voice)
    assert not again.bubble.isVisible()  # (it doesn't pop up by itself)
    again.talk()
    wait(450)
    assert again.bubble.isVisible() and again.bubble.message == "Hi!"
    again.bubble.close()
    again.new_topic()
    assert again.settings["reply"] is None

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


QUESTIONS = {"questions": [
    {"question": "Which kind?", "header": "Kind", "multiSelect": False,
     "options": [{"label": "Over-ear", "description": "Better sound"}, {"label": "In-ear", "description": "Small"}]},
    {"question": "Which colors?", "header": "Color", "multiSelect": True,
     "options": [{"label": "Black"}, {"label": "White"}, {"label": "Blue"}]},
    {"question": "Budget?", "header": "Budget", "multiSelect": False,
     "options": [{"label": "Under $100"}, {"label": "More"}]}]}


def _ask(w, answers):
    w.chat.ask_permission({"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                           "tool_input": QUESTIONS}, answers.append)


def test_questions_in_the_bubble(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("help me pick headphones")
    w.bubble._send()
    w.chat.toggle()  # away: questions pop it up anyway
    answers = []
    _ask(w, answers)
    wait(400)
    assert w.bubble.isVisible() and w.bubble.message == "Which kind?" and len(w.bubble.choices) == 2
    assert w.chat.waiting and not w.chat.thinking
    w.bubble.chosen.emit(0)  # clicked: ticked
    w.bubble.chosen.emit(1)  # (instead)
    assert w.bubble.message == "Which kind?" and w.bubble.picked == {1}
    w.bubble._send()  # Enter: that one
    assert w.bubble.message == "Which colors?" and w.bubble.multi
    from PySide6.QtTest import QTest
    QTest.keyClick(w.bubble.line, Qt.Key.Key_Down)  # ↓ to the first, Space ticks it
    QTest.keyClick(w.bubble.line, Qt.Key.Key_Space)
    QTest.keyClick(w.bubble.line, Qt.Key.Key_Up)  # ↑ round to the last
    QTest.keyClick(w.bubble.line, Qt.Key.Key_Space)
    assert w.bubble.picked == {0, 2} and w.bubble.line.toPlainText() == ""
    w.bubble.chosen.emit(0)  # unticked
    assert w.bubble.picked == {2}
    w.bubble._send()  # Enter, nothing typed: the ones ticked
    assert w.bubble.message == "Budget?" and not w.bubble.multi and not w.bubble.picked
    w.bubble.link.emit("back")  # back a question: what was picked is still ticked
    assert w.bubble.message == "Which colors?" and w.bubble.picked == {2}
    w.bubble.link.emit("back")
    assert w.bubble.message == "Which kind?" and w.bubble.picked == {1}
    w.bubble._send()  # Enter: the one picked before
    assert w.bubble.message == "Which colors?"
    w.bubble._send()
    width = w.bubble.width()
    w.bubble.line.setPlainText("about $150")
    assert w.bubble.width() == width  # (typing doesn't shrink it)
    w.bubble._send()  # one's own answer
    assert answers == [{"behavior": "allow", "updatedInput": {**QUESTIONS, "answers": {
        "Which kind?": "In-ear", "Which colors?": ["Blue"], "Budget?": "about $150"}}}]
    assert w.bubble.kind == "working" and not w.bubble.choices
    assert w.claude.sent == [("help me pick headphones", "")]  # the answers aren't new messages


def test_questions_go_to_the_terminal(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("help me pick")
    w.bubble._send()
    answers = []
    w.chat.ask_permission({"session_id": "s1", "hook_event_name": "PermissionRequest",
                           "tool_name": "AskUserQuestion", "tool_input": QUESTIONS}, answers.append)
    assert answers == [None]  # (asked through PreToolUse, never as a permission)
    _ask(w, answers)
    assert w.bubble.choices
    w.chat.open_conversation()  # the user went to the terminal: answered there
    assert answers == [None, None] and not w.bubble.choices and w.bubble.message == tr("in_terminal")


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
    w.bubble.line.setPlainText("half a thought")
    w.claude.asking.emit("waiting_for_input")
    assert w.chat.waiting and w.bubble.kind == "question" and w.bubble.message == tr("in_terminal")
    # the line is locked (answers go to the terminal), says how to get there, and what was typed waits
    assert w.bubble.line.isReadOnly() and w.bubble.line.toPlainText() == ""
    assert w.bubble.line.placeholderText() == tr("open_terminal")
    w.chat.mic()
    assert not w.voice.listening and len(w.claude.sent) == 1
    w.claude.asking.emit("")
    assert not w.chat.waiting and w.bubble.kind == "working"
    assert not w.bubble.line.isReadOnly() and w.bubble.line.toPlainText() == "half a thought"


def test_carried_bubble_comes_back(world):
    w = world
    w.chat.toggle()
    wait(450)
    w.pet.picked_up.emit()
    assert not w.bubble.isVisible()
    w.pet.landed.emit()
    wait(300)
    assert w.bubble.isVisible() and w.bubble.input_open


def test_skipping_a_question(world):
    from deskpet.chat import SKIPPED
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("help me pick")
    w.bubble._send()
    answers = []
    _ask(w, answers)
    assert [key for key, _ in w.bubble.links] == ["skip", "keys"]  # (no question before the first)
    w.bubble.link.emit("skip")
    assert w.bubble.message == "Which colors?" and [key for key, _ in w.bubble.links] == ["back", "skip", "keys"]
    from PySide6.QtTest import QTest
    QTest.keyClick(w.bubble.line, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)  # Shift+Tab: back
    assert w.bubble.message == "Which kind?"
    QTest.keyClick(w.bubble.line, Qt.Key.Key_Tab)  # Tab: skipped again
    assert w.bubble.message == "Which colors?"
    w.bubble.line.setPlainText("Skip")  # typed
    w.bubble._send()
    assert w.bubble.message == "Budget?" and w.bubble.line.toPlainText() == ""
    w.bubble.link.emit("skip")
    assert answers[0]["updatedInput"]["answers"] == dict.fromkeys(("Which kind?", "Which colors?", "Budget?"), SKIPPED)


def test_long_choices_scroll(world):
    from PySide6.QtTest import QTest
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("help me pick")
    w.bubble._send()
    long = "a long description that goes on and on, " * 6
    given = {"questions": [{"question": "Which?", "header": "Kind", "multiSelect": False,
                            "options": [{"label": f"Option {i}", "description": long} for i in range(4)]}]}
    w.chat.ask_permission({"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                           "tool_input": given}, [].append)
    wait(400)
    b = w.bubble
    assert b.choices_view.height() < b.choices_full and b.choice_scroll == 0  # capped: they scroll
    for _ in range(4):
        QTest.keyClick(b.line, Qt.Key.Key_Down)
    assert b.cursor == 3 and b.choice_scroll > 0  # (the last one, scrolled into view)
    QTest.keyClick(b.line, Qt.Key.Key_Down)  # round to the first: back at the top
    assert b.cursor == 0 and b.choice_scroll == 0


def test_pasting_images(world, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QColor, QImage
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.image_dir = tmp_path / "images"
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    pasted = QMimeData()
    pasted.setImageData(image)
    w.bubble.line.setPlainText("what's this ")
    w.bubble.line.moveCursor(w.bubble.line.textCursor().MoveOperation.End)
    assert w.bubble.line.canInsertFromMimeData(pasted)
    w.bubble.line.insertFromMimeData(pasted)  # Ctrl+V with an image
    dropped = QMimeData()
    dropped.setUrls([QUrl.fromLocalFile(str(tmp_path / "photo.jpg"))])
    w.bubble.line.insertFromMimeData(dropped)  # a file dropped on it
    assert w.bubble.line.toPlainText() == "what's this [Image #1][Image #2]"
    saved = list((tmp_path / "images").glob("*.png"))
    assert len(saved) == 1
    w.bubble._send()
    assert w.claude.sent == [("what's this", "", (str(saved[0]), str(tmp_path / "photo.jpg")))]
    w.claude.answer("A red box.")
    w.bubble.line.insertFromMimeData(pasted)  # an image alone
    w.bubble._send()
    assert w.claude.sent[-1][0] == tr("about_images") and len(w.claude.sent[-1][2]) == 1


def test_pasting_text_that_comes_with_a_picture(world, tmp_path):
    """Excel, Word and browsers copy a picture of what's copied too: the text is what's pasted."""
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QColor, QImage
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.image_dir = tmp_path / "images"
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("blue"))
    cells = QMimeData()
    cells.setImageData(image)
    cells.setText("A1\tB1")
    w.bubble.line.insertFromMimeData(cells)
    assert w.bubble.line.toPlainText() == "A1\tB1" and not w.bubble.images


def test_pasting_goes_in_the_way_claude_codes_prompt_takes_it(world, tmp_path):
    """An image file's path pasted alone: an image. Other files dropped: their paths. Typed spaces: kept."""
    from PySide6.QtCore import QMimeData, QUrl
    w = world
    w.chat.toggle()
    wait(450)
    picture = tmp_path / "a b.png"
    picture.write_bytes(b"")
    copied = QMimeData()
    copied.setText(f'"{picture}"')
    dropped = QMimeData()
    dropped.setUrls([QUrl.fromLocalFile(str(tmp_path / "notes.txt")), QUrl.fromLocalFile(str(tmp_path / "my notes.md"))])
    w.bubble.line.setPlainText("x  =  1 ")
    w.bubble.line.moveCursor(w.bubble.line.textCursor().MoveOperation.End)
    w.bubble.line.insertFromMimeData(copied)
    w.bubble.line.insertPlainText(" and ")
    w.bubble.line.insertFromMimeData(dropped)
    assert w.bubble.line.toPlainText() == f'x  =  1 [Image #1] and {tmp_path / "notes.txt"} "{tmp_path / "my notes.md"}"'
    w.bubble._send()
    assert w.claude.sent == [(f'x  =  1 and {tmp_path / "notes.txt"} "{tmp_path / "my notes.md"}"', "", (str(picture),))]


def test_images_in_an_answer_go_as_paths(world, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    w = world
    w.chat.toggle()
    wait(450)
    w.bubble.line.setPlainText("help me pick")
    w.bubble._send()
    answers = []
    _ask(w, answers)
    dropped = QMimeData()
    dropped.setUrls([QUrl.fromLocalFile(str(tmp_path / "mine.png"))])
    w.bubble.line.setPlainText("like this  ")
    w.bubble.line.moveCursor(w.bubble.line.textCursor().MoveOperation.End)
    w.bubble.line.insertFromMimeData(dropped)
    w.bubble.line.insertPlainText("  one")
    w.bubble._send()
    w.bubble.link.emit("skip")
    w.bubble.link.emit("skip")
    got = answers[0]["updatedInput"]["answers"]["Which kind?"]
    assert got == "like this one " + str(tmp_path / "mine.png") and not w.bubble.images
