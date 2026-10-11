"""Talking to Claude through the pet: the bubble, the Claude session behind it, permission questions asked in the
bubble, and what the pet's face shows meanwhile (it's the pet's Mood)."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from . import desktop, paths
from .bubble import SpeechBubble
from .claude.session import ClaudeSession
from .pet import Pet
from .settings import Settings
from .strings import known, tool_label, tool_name, tr
from .voice import Voice, joined

log = logging.getLogger("pet")
HOLD_MS = 250   # Ctrl+Alt+Space held this long listens; let go sooner, it's a press (the bubble out, or put away)
NEW_CONVERSATION = ("/clear", "/reset", "/new")   # typed in the bubble: what New conversation does
# Answers to a permission question, in plain words: a yes starts with one of these and has no "but" or "no" in it;
# anything else is a no, and what was said goes to Claude ("不要删，改名成 old.bak")
YES_START = re.compile(r"(y|yes|yep|yeah|ok|okay|sure|go|allow|do it|fine|可以|好|行|嗯|是|对|允许|同意|继续|没问题|执行|确认|批准|"
                       r"去吧|做吧|弄吧|删吧|搞吧|来吧|跑吧|改吧)", re.IGNORECASE)
NOT_QUITE = re.compile(r"(不|别|否|停|取消|算了|但|改成|换成|instead|but|don'?t|not|stop|cancel|\bno\b)", re.IGNORECASE)


def is_yes(answer: str) -> bool:
    text = answer.strip()
    if text.startswith("没问题"):  # 没 is a no everywhere else
        text = text[3:]
    elif not YES_START.match(text):
        return False
    return not NOT_QUITE.search(text)


def request_what(given: dict) -> str:
    """What a permission request is for, in a few words: its description, command, file…; for a Fusion script, its
    first comment line (Claude is told to write one), else what kind of thing it does there."""
    what = next((given[k] for k in ("description", "command", "file_path", "url", "pattern")
                 if isinstance(given.get(k), str) and given[k]), "")
    inner = given.get("object") if isinstance(given.get("object"), dict) else {}
    if not what and isinstance(inner.get("script"), str):
        what = next((line.strip().lstrip("#").strip() for line in inner["script"].splitlines()
                     if line.strip().startswith("#") and line.strip().lstrip("#").strip()), "")
    if not what and isinstance(given.get("featureType"), str):
        what = " ".join(str(x) for x in (given["featureType"], inner.get("operation")) if x)
    return what[:160]


class Chat(QObject):
    """Click the pet or press Ctrl+Alt+Space: it jumps out with the bubble to type in (or puts it away). Double
    click: the conversation in a terminal. Answers, questions and errors wait in the bubble; only a permission
    question pops it up by itself."""

    def __init__(self, pet: Pet, settings: Settings, make_session=ClaudeSession, make_voice=Voice):
        super().__init__()
        self.pet, self.settings = pet, settings
        self._make_session = make_session
        # the pet's mood (see pet.Mood): waiting and thinking are worked out below
        self.waiting_since = 0.0
        self.done = False
        self.happy_until = self.error_until = 0.0
        self.sweat_since: float | None = None
        self.pending = 0            # messages sent to Claude and not answered yet
        self.in_terminal = False    # Claude waits for the user in a terminal (the bubble's own: _permission)
        self._permission = None     # replies to the permission question shown in the bubble (see ask_permission)
        self.open_before_click: bool | None = None   # (a double click undoes what its first click did)
        self.bubble_on_drag: bool | None = None      # the bubble showed when the pet was picked up (with its line?)
        self.target = 0             # the window the user was working in last (what "this" means to Claude)
        self._session: ClaudeSession | None = None

        self.bubble = SpeechBubble()
        self.bubble.submitted.connect(self._submit)
        self.bubble.shown.connect(self._seen)
        self.bubble.follow_up = tr("follow_up")
        pet.mood = self
        pet.companions.append(self.bubble)
        pet.clicked.connect(self.click)
        pet.double_clicked.connect(self.double_click)
        pet.picked_up.connect(self._picked_up)
        pet.landed.connect(self._landed)
        pet.moved.connect(self._point)
        self.target_timer = QTimer(self, interval=300, timeout=self._track)
        self.target_timer.start()

        self.voice = make_voice()
        self.voice.heard.connect(self._on_heard)
        self.voice.failed.connect(self._on_voice_failed)
        self.voice.limit.timeout.connect(self.stop_listening)  # (talked for MAX_SECONDS: written down as is)
        self.bubble.mic_tip = tr("mic")
        self.bubble.mic_clicked.connect(self.mic)
        self.bubble.voice_cancelled.connect(lambda: self.stop_listening(keep=False))
        self.held = False           # Ctrl+Alt+Space is down
        self.hold_opened = False    # ...and its press brought the bubble out
        self.hold_listens = False   # ...and it has been held long enough to listen
        self.hold_timer = QTimer(self, singleShot=True, interval=HOLD_MS, timeout=self._held_long)
        self.before_model = None    # what the bubble said before it said the speech model is on its way

    # ------------------------------------------------------------ the pet's mood

    @property
    def waiting(self) -> bool:
        """Claude is waiting for the user: a question in the bubble, or one in a terminal."""
        return self._permission is not None or self.in_terminal

    @property
    def thinking(self) -> bool:
        """Claude is working on something the user asked (and isn't waiting for them), or what the user said is
        being written down."""
        return (self.pending > 0 or self.voice.writing) and not self.waiting

    @property
    def listening(self) -> float | None:
        """The user is talking to the pet: how loud."""
        return self.voice.level() if self.voice.listening else None

    def _seen(self):
        """The bubble came up: what ! or the sweat drop was about shows."""
        self.done, self.sweat_since = False, None

    # ------------------------------------------------------------ Claude

    @property
    def workspace(self):
        return Path(self.settings["workspace"]) if self.settings["workspace"] else paths.DEFAULT_WORKSPACE

    @property
    def session(self) -> ClaudeSession:
        """The Claude behind the pet, created on first use."""
        if self._session is None:
            self._session = self._make_session(self.workspace, self.settings["session"], self.settings["background"])
            self._session.progress.connect(self._on_progress)
            self._session.replied.connect(self._on_reply)
            self._session.asking.connect(self._on_question)
            self._session.failed.connect(self._on_failure)
            self._session.located.connect(desktop.bring_to_front)
            self._session.cleared.connect(self._remember_session)
            self._session.started.connect(self._on_started)
            self._session.needs_trust.connect(self._on_trust)
        return self._session

    def session_id(self) -> str | None:
        """The bubble's conversation (without starting Claude for it)."""
        return self._session.session_id if self._session is not None else self.settings["session"]

    def close(self):
        if self._session is not None:
            self._session.close()

    def _track(self):
        """The window the user works in (not the pet's, a terminal or the taskbar): what "this" means to Claude."""
        fg = desktop.foreground()
        if fg != self.target and desktop.is_work_window(fg):
            self.target = fg

    # ------------------------------------------------------------ the bubble

    def _point(self):
        self.bubble.point_at(*self.pet.tail_tip())

    def _placeholder(self) -> str:
        if self._permission is not None:
            return tr("answer")
        return tr("follow_up") if self.bubble.message else tr("ask")

    def talk(self):
        """Out with the bubble open to type in. It shows what it showed when it was put away."""
        if self.bubble.input_open:
            self.bubble.focus_line()
            return
        self._track()  # (before the bubble takes the keyboard)
        placeholder = self._placeholder()
        self.pet.jump_out(lambda: self._open_bubble(placeholder))

    def _open_bubble(self, placeholder: str):
        if QApplication.activePopupWidget() is not None:  # right-clicked while it jumped out: the menu, not this
            return
        self._point()
        self.bubble.open_input(placeholder)

    def toggle(self):
        """Ctrl+Alt+Space or a click: the bubble out, or put away (and the pet back behind the edge)."""
        if self.bubble.isVisible() or self.pet.jumping():
            self.put_away()
        else:
            self.talk()

    def put_away(self):
        self.pet.call_off_jump()  # (a bubble still on its way doesn't open)
        if self.voice.listening:
            self.stop_listening(keep=False)
        self.bubble.collapse()

    def click(self):
        self.open_before_click = self.bubble.isVisible()
        self.toggle()

    def double_click(self):
        """The conversation in its terminal. The bubble stays as it was before the first click."""
        if self.open_before_click is True and not self.bubble.isVisible():  # the first click put it away
            self.pet.be_out()  # (back out at once, no second jump)
            self.talk()
        elif self.open_before_click is False:  # the first click brought it out
            self.put_away()
        self.open_before_click = None
        self.open_conversation()

    def _picked_up(self):
        if self.bubble.isVisible():  # out of the way while the pet is carried; back when it lands
            self.bubble_on_drag = self.bubble.input_open
            self.bubble.collapse(give_back_focus=False)

    def _landed(self):
        self._point()  # (a reply may have come on the way down)
        if self.bubble_on_drag is not None:
            with_line, self.bubble_on_drag = self.bubble_on_drag, None
            if with_line:
                self._open_bubble(self._placeholder())
            else:
                self.bubble.come_back()

    def _say(self, text: str, kind: str, hint: str = "", pop: bool = True):
        """pop: show the bubble if it was put away."""
        self._point()
        self.bubble.say(text, kind, hint, pop)

    def language_changed(self):
        self.bubble.follow_up = tr("follow_up")
        self.bubble.mic_tip = tr("mic")

    # ------------------------------------------------------------ talking instead of typing

    def hotkey_down(self):
        """Ctrl+Alt+Space pressed: the bubble comes out at once (if it wasn't); held, it listens until let go
        (from when the pet is out and the bubble up: see _held_long)."""
        self.held = True
        self.hold_listens = False
        self.hold_opened = not (self.bubble.isVisible() or self.pet.jumping())
        if self.hold_opened:
            self.talk()
        self.hold_timer.start()

    def _held_long(self):
        """Held long enough: it listens, once it has jumped out, landed and the bubble has popped up."""
        if not self.held or self.voice.listening:
            return
        if self.pet.jumping() or not self.bubble.isVisible() or self.bubble.popping():
            QTimer.singleShot(30, self._held_long)
            return
        self.hold_listens = self.listen()

    def hotkey_up(self):
        """Let go: what was said gets written down; a short press that didn't bring the bubble out puts it away."""
        self.held = False
        self.hold_timer.stop()
        if self.hold_listens:
            self.hold_listens = False
            self.stop_listening()
        elif not self.hold_opened:
            self.put_away()

    def mic(self):
        """The bubble's mic: listen; clicked again (the stop button by then), done."""
        if self.voice.listening:
            self.stop_listening()
        elif not self.voice.writing:
            self.listen()

    def listen(self) -> bool:
        """The bubble out (if it isn't), a stop button at the end of its line; the pet listening (how loud shows
        on it). The first time, the bubble says the speech model is being downloaded meanwhile (why it takes a
        while)."""
        if not self.voice.start():
            return False
        if not self.bubble.isVisible() and not self.pet.jumping():
            self.talk()
        self.bubble.set_placeholder(self._placeholder())  # (not "didn't catch that" from last time)
        self.bubble.set_voice("listening")
        if not self.voice.transcriber.downloaded() and self.before_model is None:
            self.before_model = (self.bubble.message, self.bubble.kind, self.bubble.hint)
            self._say(tr("getting_model"), "working")
        return True

    def stop_listening(self, keep: bool = True):
        """keep: what was said goes to be written down; else it's dropped."""
        if not keep:
            self.voice.cancel()
            self.bubble.set_voice("")
            self._model_here()
            return
        self.voice.stop()
        self.bubble.set_voice("writing" if self.voice.writing else "")

    def _model_here(self):
        """The first time's note about the model goes, and what the bubble said before comes back (unless
        something else came meanwhile)."""
        if self.before_model is None:
            return
        (message, kind, hint), self.before_model = self.before_model, None
        if self.bubble.kind == "working" and self.bubble.message == tr("getting_model"):
            if message:
                self.bubble.say(message, kind, hint, pop=False)
            else:
                self.bubble.clear_message()

    def _on_heard(self, text: str):
        """Written down: after what was typed in the line, to check and send (Enter), or nothing heard."""
        self.bubble.set_voice("")
        self._model_here()
        if text:
            self.bubble.put_back(joined(self.bubble.line.toPlainText(), text))
        elif not self.bubble.has_text():
            self.bubble.set_placeholder(tr("heard_nothing"))
        if self.bubble.input_open and self.bubble.isVisible():
            self.bubble.focus_line()

    def _on_voice_failed(self, problem: str):
        self.bubble.set_voice("")
        self.before_model = None
        self._say(tr(problem) if known(problem) else problem, "error")

    # ------------------------------------------------------------ talking

    def _submit(self, text: str):
        if self._permission is not None:  # the answer to the permission question in the bubble
            reply, self._permission = self._permission, None
            reply({"behavior": "allow"} if is_yes(text) else
                  {"behavior": "deny", "message": f"The user didn't allow it. They said: {text}"})
            self._say(tr("thinking"), "working")
            return
        if text.split()[0].lower() in NEW_CONVERSATION:
            self.new_topic()
            return
        if self.bubble.kind == "question":  # a question waits in a terminal: answers go there, not as a message
            self.bubble.put_back(text)
            return
        self.pending += 1
        self.done, self.sweat_since = False, None
        log.info("Asked Claude")
        self._say(tr("thinking"), "working")
        # which window "this" is: on the message's first line, and for the screenshot tool (claude/tools.py)
        about = self.target if desktop.window_exists(self.target) else 0
        try:
            paths.TARGET.write_text(json.dumps({"window": about}), "utf-8")
        except OSError:
            log.warning("Couldn't save the target window", exc_info=True)
        self.session.send(text, desktop.describe(about) if about else "")

    def open_conversation(self):
        """Double click or Open terminal: the conversation in a terminal, like any Claude terminal (attached to
        the bubble's session; one already open comes to the front)."""
        self._release_permission(to_terminal=True)
        self.pet.hop()
        self.session.show()

    def new_topic(self):
        """New conversation: /clear in the bubble's session (if there's none yet, the next message starts one)."""
        self.bubble.clear_message()
        self.bubble.set_placeholder(tr("ask"))
        self._remember_session(None)
        self.session.new_topic()

    # ------------------------------------------------------------ permission questions

    def ask_permission(self, request: dict, reply):
        """Claude Code is about to ask for permission (its PermissionRequest hook, claude/hook.py, passed it on).
        In the conversation the bubble is following, ask here and reply({...}) with the user's answer;
        otherwise reply(None) at once, and the terminal shows its usual prompt."""
        c = self._session
        if c is None or not c.busy or request.get("session_id") != c.session_id or self._permission is not None:
            log.info("Permission request for %s: left to its terminal", request.get("tool_name"))
            reply(None)
            return
        log.info("Permission request for %s: asking in the bubble", request.get("tool_name"))
        self._permission = reply
        self.waiting_since = time.monotonic()
        given = request.get("tool_input") or {}
        self.pet.hop()
        self._say(tr("permission", tool=tool_name(request.get("tool_name", "")), what=request_what(given)), "question",
                  tr("in_box"))
        self.bubble.open_input(tr("answer"))

    def permission_gone(self, reply):
        """The hook stopped waiting (it timed out, or the request was dealt with elsewhere)."""
        if self._permission is reply:
            self._permission = None
            if self.bubble.kind == "question":
                self._say(tr("thinking"), "working", pop=False)

    def _release_permission(self, to_terminal: bool = False):
        """Nothing to answer in the bubble any more; to_terminal: the user went to the terminal, so it shows its
        own prompt there instead, and the bubble says so."""
        if self._permission is not None:
            reply, self._permission = self._permission, None
            reply(None)
            if to_terminal:  # (it waits there now)
                self.in_terminal = True
                self._say(tr("in_terminal"), "question", tr("click_me"), pop=False)

    # ------------------------------------------------------------ from the session

    def _on_progress(self, name: str, detail: str):
        label = tool_label(name, detail)
        if label and self.pending:
            self._say(label, "working", pop=False)

    def _remember_session(self, session: str | None):
        """The bubble's conversation carries on across restarts: only New conversation starts another one."""
        self.settings.set("session", session)

    def _on_started(self, background_id: str):
        """A background session for the bubble's Claude (to attach to again after a restart). If the workspace
        had to be trusted first, it has been."""
        self.settings.set("background", background_id)
        if self.in_terminal and self.bubble.kind == "question":
            self.in_terminal = False
            self._say(tr("thinking"), "working", pop=False)

    def _on_trust(self):
        """The first time: Claude Code wants the user to trust its folder, which only an interactive Claude asks.
        One opens in a terminal; the session starts once they've said yes there."""
        exe = paths.claude_exe()
        if exe:
            desktop.open_terminal([exe], self.session.cwd, self.session.env)
        self.in_terminal = True
        self.waiting_since = time.monotonic()
        self._say(tr("trust"), "question")

    def _on_reply(self, text: str):
        """The answer: in the bubble if it's open (^ ^ for a moment); if it's put away, it waits there, and the
        pet shows ! until the bubble is opened (it doesn't pop up by itself)."""
        self.pending = max(0, self.pending - 1)
        self._release_permission()  # the turn is over: nothing left to answer
        self.in_terminal = False
        self._remember_session(self.session.session_id)
        if self.bubble.isVisible():
            self.happy_until = time.monotonic() + 1.2
        else:
            self.done = True
        self._say(text or tr("no_text"), "reply", pop=False)

    def _on_question(self, state: str):
        """Claude is waiting for the user in the terminal (a dialog the bubble isn't asking about itself): that's
        where to answer. state "": it got its answer there."""
        if state and not self.in_terminal:
            self.waiting_since = time.monotonic()
        self.in_terminal = bool(state)
        if self._permission is not None:
            return
        if state:
            self._say(tr("in_terminal"), "question", tr("click_me"))
        elif self.bubble.kind == "question":
            self._say(tr("thinking"), "working", pop=False)

    def _on_failure(self, message: str):
        """x x, then a sweat drop; what went wrong waits in the bubble (it doesn't pop up by itself)."""
        self.pending = max(0, self.pending - 1)
        self._release_permission()
        self.in_terminal = False
        self._remember_session(self.session.session_id)
        now = time.monotonic()
        self.error_until, self.sweat_since = now + 0.6, now
        self._say(tr(message) if known(message) else message or tr("failed"), "error", pop=False)
