"""The comic speech bubble above the pet's head. You type into it, and the same bubble shows what Claude is doing
and what it says back."""
from __future__ import annotations

import math
import time
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPalette, QPen, \
    QTextCursor, QTextDocument, QTextLayout, QTextOption
from PySide6.QtWidgets import QFrame, QTextEdit, QToolTip, QWidget

from . import desktop

PAPER = QColor("#fffbf4")       # the bubble
INK = QColor("#2b211c")         # outline and text, the same as the pet's eyes
POP = QColor("#d97757")         # the pet's color: the working dot, a question's hint
FAINT = QColor("#a8978a")       # hints and placeholder
ERROR = QColor("#c0392b")


def ui_font(px: float, bold: bool = False) -> QFont:
    """A sci-fi terminal look: Share Tech Mono (bundled in assets/fonts); what it lacks (Chinese) comes from the
    system's own fonts."""
    f = QFont()
    f.setFamilies(["Share Tech Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "Microsoft YaHei UI", "PingFang SC",
                   "Noto Sans CJK SC"])
    f.setPixelSize(round(px))
    f.setBold(bold)
    return f


def _mic(p: QPainter, c: QPointF, color: QColor):
    """A microphone in outline, 14 px tall, centred on c."""
    p.save()
    p.translate(c)
    p.setPen(QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(-3, -7, 6, 9.5), 3, 3)
    stand = QPainterPath(QPointF(-5.5, -1.5))
    stand.arcTo(QRectF(-5.5, -6, 11, 9.5), 180, 180)
    p.drawPath(stand)
    p.drawLine(QPointF(0, 3.5), QPointF(0, 6.5))
    p.drawLine(QPointF(-3, 6.5), QPointF(3, 6.5))
    p.restore()


def _clip(text: str, font: QFont, width: float, lines: int) -> tuple[str, float, bool]:
    """text wrapped at width, cut to its first `lines` lines (the last one ending in …): (the text, how tall, whether
    it was cut)."""
    text = text.replace("\n", "\u2028")  # (a line break, to QTextLayout)
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(WRAP)
    layout.setTextOption(option)
    layout.beginLayout()
    kept, height = [], 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        if len(kept) < lines:
            height += line.height()
        kept.append(line.textStart())
    layout.endLayout()
    if len(kept) <= lines:
        return text, height, False
    start = kept[lines - 1]
    last = QFontMetricsF(font).elidedText(text[start:].replace("\n", " "), Qt.TextElideMode.ElideRight, width)
    return text[:start] + last, height, True


TEXT_STYLE = (
    "QTextEdit { background: transparent; border: none; padding: 0; color: %s;"
    " selection-background-color: #d97757; selection-color: white; }"
    "QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }"
    "QScrollBar::handle:vertical { background: rgba(43, 33, 28, 70); border-radius: 3px; min-height: 18px; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }")
WRAP = QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
SCROLL_W = 6                    # the scroll bar's width (TEXT_STYLE)


class _Text(QTextEdit):
    """Plain text that wraps at the bubble's width and scrolls once it's taller than the bubble allows. The
    message and the line you type in are both one of these, so they grow the same way."""
    submitted = Signal()   # Enter (Shift+Enter starts a new line)

    def __init__(self, parent: QWidget, editable: bool):
        super().__init__(parent)
        self.setFont(ui_font(14.5))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setWordWrapMode(WRAP)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.key_hook: Callable[[object], bool] | None = None   # sees keys first (True: it used the key)
        self.set_color(INK)
        if not editable:
            self.setReadOnly(True)
            self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)  # to copy from it
        self.hide()

    def set_color(self, color: QColor):
        self.setStyleSheet(TEXT_STYLE % color.name())
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, FAINT)
        self.setPalette(palette)

    def natural_width(self, text: str) -> float:
        """How wide the text would be without wrapping."""
        fm = QFontMetricsF(self.font())
        return max(fm.horizontalAdvance(line) for line in text.split("\n"))

    def height_for(self, width: float, text: str | None = None, lines: int | None = None) -> float:
        """How tall the text is when it wraps at `width`: all of it, or its first `lines` lines (as they are:
        a Chinese line, in the fallback font, is taller than a Latin one)."""
        doc = QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setDocumentMargin(0)
        option = QTextOption()
        option.setWrapMode(WRAP)
        doc.setDefaultTextOption(option)
        doc.setPlainText(self.toPlainText() if text is None else text)
        doc.setTextWidth(width)
        height = doc.size().height()
        if lines is None:
            return height
        block, seen = doc.begin(), 0
        while block.isValid():
            layout = block.layout()
            for i in range(layout.lineCount()):
                seen += 1
                if seen == lines:
                    line = layout.lineAt(i)
                    return doc.documentLayout().blockBoundingRect(block).top() + line.y() + line.height()
            block = block.next()
        return height

    def event(self, e):
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) \
                and self.key_hook is not None and self.key_hook(e):
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
        elif self.key_hook is not None and self.key_hook(e):
            pass
        else:
            super().keyPressEvent(e)


class SpeechBubble(QWidget):
    """A comic speech bubble whose tail points down at the pet. It can show a message (kind: working, reply,
    question or error), a line to type into, or both: the message on top, the line under it (with a mic at its
    end, to talk instead: see set_voice). Both grow wider with their text up to MAX_W, then taller up to
    MAX_LINES, then scroll. Nothing closes it by itself, and neither do clicks or Esc: only Ctrl+Alt+Space, a
    click on the pet or Hide in its menu (see collapse). Put away, it keeps what it shows, and shows it again
    when it comes back."""
    submitted = Signal(str)
    chosen = Signal(int)          # one of the choices (see set_choices): clicked, or Space on it
    link = Signal(str)            # one of the links at the end of the hint clicked (see set_choices): its key;
    # "back" also by Backspace in the empty line
    shown = Signal()              # it came up (what it shows can be seen)
    mic_clicked = Signal()        # the mic at the end of the line (or the stop button there, while listening)
    voice_cancelled = Signal()    # Esc while listening
    MIN_INPUT, MAX_W, MIN_W = 150, 288, 96   # the line starts short and grows with what's typed
    PAD, TAIL, GAP = 14, 15, 12
    MIC_W = 22                    # room for the mic (or the stop button), right of the line
    MAX_LINES = 5                 # for the message and for the line alike; past that they scroll
    TAIL_IN = 82                  # how far in from its end (nearest the pet) the tail sits: at most a third
    POP_MS = 180
    CHOICE_IN = 24                # a choice's text, in from its number
    CHOICE_LINES = 2              # a choice's label, and its description, at most (but the one in focus)
    CHOICES_H = 230               # the choices, at most; past that they scroll (the wheel; ↑/↓ keep in view)
    ROOM = 40                     # clear room above and on both sides, for the pop's overshoot (clicks go through)

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.message, self.kind, self.hint = "", "", ""
        self.input_open = False
        self.away = False         # put away (collapse): hidden, with what it shows kept for when it comes back
        self.anchor = QPoint()
        self.tail_x = 0.0
        self.flipped = False      # the pet is on the right half: the bubble reaches left (mirrored)
        self.pop_start = 0.0
        self.dots = 0
        self.previous_window = 0  # to give the keyboard back when the line closes
        self.early_keys = ""      # typed while the bubble was still popping up
        self.follow_up = ""       # the line's placeholder once a conversation has started
        self.voice = ""           # "listening" or "writing": what the voice input is doing (see set_voice)
        self.mic_tip = ""
        self.mic_hover = False
        self.scrolls = (False, False)   # the message, the line: past MAX_LINES (see _fit)
        self.choices: list[tuple[str, str]] = []   # (label, description): a question's options, under it
        self.multi = False        # ...any number of them can be picked (they're ticked), else one answers
        self.picked: set[int] = set()
        self.choice_hover = -1
        self.cursor = -1          # the choice ↑/↓ (or a click) went to: Space picks it
        self.choice_rects: list[QRectF] = []       # where they are, in the bubble (see _layout_choices)
        self.choices_view = QRectF()  # the part of the bubble they show in (they scroll past CHOICES_H)
        self.choices_full = 0.0   # how tall they are, all of them
        self.choice_scroll = 0.0
        self.links: list[tuple[str, str]] = []   # at the end of the hint, to click: (key, text)
        self.link_rects: dict[str, QRectF] = {}
        self.link_hover = ""
        self.links_below = False  # the links don't fit at the end of the hint: under it
        self.keys_help = ""       # the keys for the choices, shown under the hint while keys_open (the ? link)
        self.keys_open = False
        self.keys_text = ("", 0.0)   # as laid out: the text, how tall
        self.clipped: list[tuple[str, float, str, float, bool]] = []   # each choice as shown (see _layout_choices)
        self.blank = False        # paint nothing (see setVisible)
        self.set_aside = ""       # what was typed in the line before it was locked (see set_locked)
        self.setMouseTracking(True)

        self.text = _Text(self, editable=False)
        self.line = _Text(self, editable=True)
        self.line.submitted.connect(self._send)
        self.line.key_hook = self._choice_key
        self.line.textChanged.connect(self._relayout)  # grow with the text

        self.anim_timer = QTimer(self, interval=15, timeout=self._animate)
        self.dot_timer = QTimer(self, interval=350, timeout=self._tick_dots)
        self.voice_timer = QTimer(self, interval=40, timeout=self.update)  # (the dots while it's written down)

    # ------------------------------------------------------------ what it shows

    def open_input(self, placeholder: str, take_focus: bool = True):
        self.line.setPlaceholderText(placeholder)
        self.input_open = True
        self.away = False
        self._relayout()
        if take_focus:
            fg = desktop.foreground()
            if fg and fg != int(self.winId()):
                self.previous_window = fg
            self.focus_line()

    def focus_line(self):
        self.activateWindow()
        desktop.give_focus(int(self.winId()))
        self.line.setFocus()

    def close_input(self, give_back_focus: bool = True):
        if not self.input_open:
            return
        self.input_open = False
        self.line.clearFocus()
        if give_back_focus and self.previous_window:
            desktop.restore_focus(self.previous_window)
        self._relayout()

    def collapse(self, give_back_focus: bool = True):
        """Ctrl+Alt+Space or Hide: the bubble goes away. The message and what was typed in the line stay for
        when it comes back."""
        self.away = True
        self.close_input(give_back_focus)
        self.hide()

    def come_back(self):
        """After being put away: as it was, without the line."""
        self.away = False
        self._relayout()

    def say(self, text: str, kind: str, hint: str = "", pop: bool = True):
        """pop: come back if it was put away (otherwise the message waits for when it does)."""
        self.message, self.kind, self.hint = text, kind, hint.upper()  # hints read like HUD labels
        if pop:
            self.away = False
        self.dots = 0
        self.text.set_color(ERROR if kind == "error" else INK)
        self.text.setPlainText(self._shown())
        if kind == "working":
            self.dot_timer.start()
        else:
            self.dot_timer.stop()
        self._relayout()

    def clear_message(self):
        self.message, self.kind, self.hint = "", "", ""
        self.text.clear()
        self.dot_timer.stop()
        self._relayout()

    def popping(self) -> bool:
        """Still popping up."""
        return self.anim_timer.isActive()

    def has_text(self) -> bool:
        return bool(self.line.toPlainText().strip())

    def warm_up(self):
        """Do once, at startup, what makes the first opening slow (half a second: styles, fonts, the Chinese
        fallback font's metrics), so the first bubble pops up as quickly as the rest."""
        for box in (self.text, self.line):
            box.ensurePolished()
        sample = "Ask Claude… 问 Claude"
        self.line.natural_width(sample)
        self.line.height_for(300, sample + "\nX")
        self.text.height_for(300, sample)
        self.winId()

    def put_back(self, text: str):
        """A message that couldn't be sent goes back in the line (and what was said, after what was typed)."""
        self.line.setPlainText(text)
        self.line.moveCursor(QTextCursor.MoveOperation.End)

    def set_choices(self, choices: list[tuple[str, str]], multi: bool = False, picked: set[int] | None = None,
                    links: list[tuple[str, str]] | None = None, keys: str = ""):
        """A question's options, numbered, between it and the line (none: []); links: (key, text) to click, at the
        end of the hint (back to the question before, skip it); keys: what the keys do, behind a ? there."""
        if list(choices) != self.choices:  # (another question: from the top)
            self.choice_scroll = 0.0
        self.choices, self.multi, self.picked = list(choices), multi, set(picked or ())
        self.keys_help = keys
        self.links = [link for link in links or [] if link[0] != "keys"] + ([("keys", "?")] if keys else [])
        self.choice_hover, self.link_hover = -1, ""
        self.cursor = min(self.picked) if self.picked else -1
        if self.isVisible():
            self._relayout()

    def set_locked(self, locked: bool):
        """The line can't be typed into (nor the mic used): what's typed is set aside meanwhile, and comes back."""
        if locked == self.line.isReadOnly():
            return
        if locked:
            self.set_aside = self.line.toPlainText()
            self.line.clear()
        self.line.setReadOnly(locked)
        if not locked:
            self.put_back(self.set_aside)
            self.set_aside = ""
        self.update()

    def set_placeholder(self, text: str):
        self.line.setPlaceholderText(text)
        if self.isVisible():
            self._relayout()  # (the line's width goes by it when it's empty)

    def set_voice(self, state: str):
        """Where the mic is, at the end of the line: "listening" (a stop button), "writing" (dots while it's
        written down) or "" (the mic). The line stays as it is: what was said goes after it."""
        self.voice = state
        if state == "writing":
            self.voice_timer.start()
        else:
            self.voice_timer.stop()
        self.update()

    def point_at(self, tip: QPoint, flipped: bool = False):
        """Where the tail ends (by the pet's eye), and whether the bubble reaches left of it (mirrored, when the
        pet is on the right half of the screen) instead of right."""
        self.anchor, self.flipped = tip, flipped
        if self.isVisible():
            self._relayout()

    # ------------------------------------------------------------ layout

    def _small(self) -> QFont:
        small = ui_font(11.5)
        small.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        return small

    def _shown(self) -> str:
        return self.message + ("." * self.dots if self.kind == "working" else "")

    def _lead(self) -> int:
        return 18 if self.kind == "working" else 0  # room for the pulsing dot

    def _sizes(self) -> tuple[int, float, float, float, float]:
        """Width; heights of the message, its hint, the line; the height of the bubble's body."""
        small = QFontMetricsF(self._small())
        lead = self._lead()
        want = 0.0
        if self.message:
            want = self.text.natural_width(self.message + ("..." if self.kind == "working" else "")) + lead
            if self.hint:
                links = self._links_width()
                want = max(want, small.horizontalAdvance(self.hint) + links)
            if self.choices and self.keys_open:  # (what the keys do: a line each, if it can)
                want = max([want, *(small.horizontalAdvance(line) for line in self.keys_help.split("\n"))])
        for label, about in self.choices:
            want = max(want, self.CHOICE_IN + 8 + max(QFontMetricsF(self.text.font()).horizontalAdvance(label),
                                                      small.horizontalAdvance(about) if about else 0))
        if self.input_open:
            typed = self.line.toPlainText() or self.line.placeholderText()
            if self.choices:  # (under a question it keeps its width: typing an answer of one's own doesn't shrink it)
                typed = max(typed, self.line.placeholderText(), key=self.line.natural_width)
            want = max(want, self.line.natural_width(typed) + 8 + self.MIC_W, self.MIN_INPUT - 2 * self.PAD)
        width = int(min(self.MAX_W, max(self.MIN_W, want + 2 * self.PAD + 1)))
        inner = width - 2 * self.PAD
        msg_h = hint_h = line_h = 0.0
        text_scrolls = line_scrolls = False
        if self.message:
            measured = self.message + ("..." if self.kind == "working" else "")
            msg_h, text_scrolls = self._fit(self.text, inner - lead, measured)
            hint_h = small.height() + 4 if self.hint else 0.0
            links = self._links_width()
            self.links_below = bool(links) and small.horizontalAdvance(self.hint) + links > inner
            if self.links_below:  # (no room for them at the end of the hint: on a line of their own, under it)
                hint_h += small.height() + 4
            self.keys_text = ("", 0.0)
            if self.choices and self.keys_open and self.keys_help:
                text, height, _ = _clip(self.keys_help, self._small(), inner, 99)
                self.keys_text = (text, height + 6)
                hint_h += height + 6
        if self.input_open:
            line_h, line_scrolls = self._fit(self.line, inner - self.MIC_W)
        self.scrolls = (text_scrolls, line_scrolls)
        hint_h += self._layout_choices(inner, self.PAD + msg_h + hint_h)  # (the choices go with the hint)
        body = 2 * self.PAD + msg_h + hint_h + line_h + (self.GAP if self.message and self.input_open else 0)
        return width, msg_h, hint_h, line_h, body

    def _link_font(self) -> QFont:
        return ui_font(17)

    def _links_width(self) -> float:
        fm = QFontMetricsF(self._link_font())
        return sum((15 if key == "keys" else fm.horizontalAdvance(text)) + 16 for key, text in self.links) \
            if self.choices else 0.0

    def _choice_fonts(self) -> tuple[QFont, QFont]:
        return self.text.font(), ui_font(12)

    def _layout_choices(self, inner: float, top: float) -> float:
        """Where each choice goes (its label, and its description under it, both wrapped), from `top`; how tall
        they all are."""
        self.choice_rects, self.clipped = [], []
        if not self.choices:
            return 0.0
        font, about_font = self._choice_fonts()
        w = inner - self.CHOICE_IN
        y = top + 6
        focus = self.focused()
        for i, (label, about) in enumerate(self.choices):  # (at most CHOICE_LINES lines each, but the one in focus)
            lines = 999 if i == focus else self.CHOICE_LINES
            label, label_h, cut = _clip(label, font, w, lines)
            about, about_h, cut_about = _clip(about, about_font, w, lines) if about else ("", 0.0, False)
            self.clipped.append((label, label_h, about, about_h, cut or cut_about))
            self.choice_rects.append(QRectF(self.PAD - 4, y, inner + 8, label_h + about_h + 6))
            y += label_h + about_h + 8
        self.choices_full = y - top
        shown = min(self.choices_full, self.CHOICES_H)
        self.choices_view = QRectF(self.PAD - 4, top + 3, inner + 8, shown - 3)
        self.choice_scroll = min(max(self.choice_scroll, 0.0), self.choices_full - shown)
        return shown

    def _scroll_room(self) -> float:
        return max(0.0, self.choices_full - 3 - self.choices_view.height())

    def go_to(self, i: int):
        """Choice i in focus: shown in full, and scrolled to."""
        self.cursor = i
        if self.isVisible():
            self._relayout()
        self._scroll_to(i)

    def _scroll_to(self, i: int):
        """Scrolled so that choice i shows (as much of it as fits)."""
        if not 0 <= i < len(self.choice_rects):
            return
        r, view = self.choice_rects[i], self.choices_view
        if r.top() - self.choice_scroll < view.top():
            self.choice_scroll = r.top() - view.top() - 4
        elif r.bottom() - self.choice_scroll > view.bottom():
            self.choice_scroll = min(r.bottom() - view.bottom() + 4, r.top() - view.top() - 4)
        self.choice_scroll = min(max(self.choice_scroll, 0.0), self._scroll_room())
        self.update()

    def _fit(self, box: _Text, width: float, text: str | None = None) -> tuple[float, bool]:
        """How tall a box is, and whether it scrolls: all of its text, or past MAX_LINES lines, the first
        MAX_LINES (laid out a scroll bar narrower: it takes room, which would wrap the text into more lines)."""
        full = box.height_for(width, text)
        if full <= box.height_for(width, text, self.MAX_LINES) + 0.5:
            return full, False
        return box.height_for(width - SCROLL_W, text, self.MAX_LINES), True

    def _relayout(self):
        if self.away or not self.message and not self.input_open:
            self.hide()
            return
        width, msg_h, hint_h, line_h, body = self._sizes()
        height = int(math.ceil(body + self.TAIL))
        screen = QGuiApplication.screenAt(self.anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry().intersected(screen.geometry())
        margin = 20  # keep clear of the screen's edges
        # over the pet, the tail near its end by the pet; the tail's base is 9 px aside from its tip (it hooks)
        d = -1 if self.flipped else 1
        base = self.anchor.x() + 9 * d
        tail_in = min(width // 3, self.TAIL_IN)
        x = base - (width - tail_in if self.flipped else tail_in)
        x = min(max(x, area.left() + margin), area.right() - width - margin)
        y = max(self.anchor.y() - height - 2, area.top() + margin)
        r = self.ROOM
        self.setGeometry(QRect(x - r, y - r, width + 2 * r, height + r))
        self.tail_x = min(max(base - x, 30 if self.flipped else 22), width - (22 if self.flipped else 30))
        inner = width - 2 * self.PAD
        lead = self._lead()
        for box, scrolls in zip((self.text, self.line), self.scrolls):  # (a scroll bar only past MAX_LINES)
            box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if scrolls
                                           else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text.setGeometry(r + self.PAD + lead, r + self.PAD, inner - lead, math.ceil(msg_h))
        line_top = self.PAD + msg_h + hint_h + (self.GAP if self.message else 0)
        self.line.setGeometry(r + self.PAD, r + int(line_top), inner - self.MIC_W, math.ceil(line_h))
        if not self.isVisible():
            self.pop_start = time.monotonic()
            self.text.hide()
            self.line.hide()
            self.show()
            self.raise_()
            self.anim_timer.start()
        elif not self.anim_timer.isActive():
            self.text.setVisible(bool(self.message))
            self.line.setVisible(self.input_open)
        self.update()

    # ------------------------------------------------------------ motion

    def _pop_scale(self) -> float:
        k = min(1.0, (time.monotonic() - self.pop_start) * 1000 / self.POP_MS)
        c = 1.9  # ease out with a little overshoot
        return 1 + (c + 1) * (k - 1) ** 3 + c * (k - 1) ** 2 if k < 1 else 1.0

    def _animate(self):
        if time.monotonic() - self.pop_start >= self.POP_MS / 1000:
            self.anim_timer.stop()
            self.text.setVisible(bool(self.message))
            if self.input_open:
                self.line.show()
                self.line.setFocus()
                if self.early_keys:
                    self.line.insertPlainText(self.early_keys)
                    self.early_keys = ""
        self.update()

    def _tick_dots(self):
        self.dots = (self.dots + 1) % 4
        self.text.setPlainText(self._shown())

    # ------------------------------------------------------------ drawing

    def _shape(self, body_h: float) -> QPainterPath:
        """Rounded body plus a curved tail that hooks down toward the pet."""
        w = self.width() - 2 * self.ROOM
        body = QPainterPath()
        body.addRoundedRect(QRectF(1, 1, w - 2, body_h - 2), 16, 16)
        tx, base = self.tail_x, body_h - 2
        d = -1 if self.flipped else 1  # which way it hooks
        tail = QPainterPath(QPointF(tx - 4 * d, base - 6))
        tail.quadTo(QPointF(tx - d, base + self.TAIL * 0.55), QPointF(tx - 9 * d, base + self.TAIL - 1))
        tail.quadTo(QPointF(tx + 9 * d, base + self.TAIL * 0.45), QPointF(tx + 13 * d, base - 6))
        tail.closeSubpath()
        return body.united(tail)

    def paintEvent(self, _):
        scale = self._pop_scale()
        if self.blank or scale < 0.05:  # (scaled to nothing, Qt drew the text at full size)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.ROOM, self.ROOM)  # (from here on, the bubble's own coordinates)
        width, msg_h, hint_h, line_h, body_h = self._sizes()
        popping = self.anim_timer.isActive()
        if scale != 1.0:  # pop out of the pet's head
            tip = QPointF(self.tail_x + (9 if self.flipped else -9), body_h + self.TAIL)
            p.translate(tip)
            p.scale(scale, scale)
            p.translate(-tip)
        shape = self._shape(body_h)
        p.setPen(QPen(INK, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(PAPER)
        p.drawPath(shape)

        font = self.text.font()
        top = self.PAD
        if self.message:
            if self.kind == "working":  # a pulsing dot in front of what it's doing
                p.setPen(Qt.PenStyle.NoPen)
                c = QColor(POP)
                c.setAlphaF(0.55 + 0.15 * self.dots)
                p.setBrush(c)
                p.drawEllipse(QPointF(self.PAD + 5, top + QFontMetricsF(font).height() / 2), 4.5, 4.5)
            if popping:  # the text boxes show once it has popped; until then, draw them
                self._draw_box(p, self.text)
            small_h = QFontMetricsF(self._small()).height() + 4
            if self.hint:
                p.setFont(self._small())
                p.setPen(POP if self.kind == "question" else FAINT)
                p.drawText(QRectF(self.PAD, top + msg_h + 2, width - 2 * self.PAD, small_h),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.hint)
            self.link_rects = {}
            if self.choices:  # the links, right to left
                p.setFont(self._link_font())
                row = QRectF(self.PAD, top + msg_h + 2 + (small_h if self.links_below else 0), width - 2 * self.PAD,
                             small_h)
                for key, text in reversed(self.links):
                    color = INK if self.link_hover == key or key == "keys" and self.keys_open else FAINT
                    right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    if key == "keys":  # a small ? in a circle
                        d = 15.0
                        circle = QRectF(row.right() - d, row.center().y() - d / 2, d, d)
                        p.setPen(QPen(color, 1.1))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawEllipse(circle)
                        p.setFont(self._small())
                        p.drawText(circle, Qt.AlignmentFlag.AlignCenter, "?")
                        p.setFont(self._link_font())
                        self.link_rects[key] = circle.adjusted(-4, -4, 4, 4)
                    else:
                        p.setPen(color)
                        self.link_rects[key] = p.boundingRect(row, right, text).adjusted(-4, -2, 4, 2)
                        p.drawText(row, right, text)
                    row.setRight(self.link_rects[key].left() - 8)
                if self.keys_text[0]:  # what the keys do, faint, under the hint
                    p.setFont(self._small())
                    p.setPen(FAINT)
                    wrap = QTextOption()
                    wrap.setWrapMode(WRAP)
                    p.drawText(QRectF(self.PAD, top + msg_h + 2 + small_h * (2 if self.links_below else 1),
                                      width - 2 * self.PAD, self.keys_text[1]), self.keys_text[0], wrap)
            self._draw_choices(p)
            if self.input_open:  # a dashed rule between the message and the line
                y = top + msg_h + hint_h + self.GAP / 2
                p.setPen(QPen(QColor(43, 33, 28, 60), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(self.PAD, y), QPointF(width - self.PAD, y))
        if self.input_open and popping:
            self._draw_box(p, self.line)
        if self.input_open and not self.line.isReadOnly():
            self._draw_voice(p)

    def _draw_choices(self, p: QPainter):
        """Like the rest of the bubble: the text in ink, its number and description faint (like the hints); the
        one under the mouse faintly shaded; picked ones ticked (in ink) instead of numbered."""
        if not self.choices:
            return
        font, about_font = self._choice_fonts()
        wrap = QTextOption()
        wrap.setWrapMode(WRAP)
        view = self.choices_view
        p.save()
        room = self._scroll_room()
        if room > 0.5:  # a thin bar, like the text boxes' scroll bars
            bar = view.height() * view.height() / (view.height() + room)
            y = view.top() + (view.height() - bar) * self.choice_scroll / room
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(43, 33, 28, 70))
            p.drawRoundedRect(QRectF(view.right() + 2, y, SCROLL_W - 2, bar), 2, 2)
        p.setClipRect(view, Qt.ClipOperation.IntersectClip)
        p.translate(0, -self.choice_scroll)
        for i, ((label, label_h, about, _, _), r) in enumerate(zip(self.clipped, self.choice_rects)):
            picked, hover = i in self.picked, i in (self.choice_hover, self.cursor)
            if hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(43, 33, 28, 14))
                p.drawRoundedRect(r, 7, 7)
            row = QFontMetricsF(font).height()
            number = QRectF(r.left() + 4, r.top() + 3, self.CHOICE_IN - 8, row)
            p.setFont(font if picked else self._small())
            p.setPen(INK if picked or hover else FAINT)
            p.drawText(number, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "✓" if picked else str(i + 1))
            text = QRectF(r.left() + 4 + self.CHOICE_IN, r.top() + 3, r.width() - 8 - self.CHOICE_IN, r.height())
            p.setFont(font)
            p.setPen(INK)
            p.drawText(text, label, wrap)
            if about:
                p.setFont(about_font)
                p.setPen(FAINT)
                p.drawText(text.adjusted(0, label_h, 0, 0), about, wrap)
        p.restore()
        fade = 14.0  # where they go on past the edge, they fade out into the paper
        for edge, shown in ((view.top(), self.choice_scroll > 0.5), (view.bottom() - fade, self.choice_scroll < room - 0.5)):
            if room > 0.5 and shown:
                top = edge == view.top()
                gradient = QLinearGradient(0, edge, 0, edge + fade)
                solid, clear = QColor(PAPER), QColor(PAPER)
                clear.setAlpha(0)
                gradient.setColorAt(0, solid if top else clear)
                gradient.setColorAt(1, clear if top else solid)
                p.fillRect(QRectF(view.left(), edge, view.width(), fade), gradient)

    def _link_at(self, pos: QPointF) -> str:
        pos = pos - QPointF(self.ROOM, self.ROOM)
        return next((key for key, r in self.link_rects.items() if r.contains(pos)), "")

    def focused(self) -> int:
        """The choice shown in full: the one ↑/↓ or a click went to last (or the one picked before)."""
        return self.cursor if self.cursor >= 0 else min(self.picked, default=-1)

    def _choice_at(self, pos: QPointF) -> int:
        """Which choice is at pos (in the window); -1: none."""
        pos = pos - QPointF(self.ROOM, self.ROOM)
        if not self.choices_view.contains(pos):
            return -1
        pos += QPointF(0, self.choice_scroll)
        return next((i for i, r in enumerate(self.choice_rects) if r.contains(pos)), -1)

    def wheelEvent(self, e):
        """Over the choices, when they scroll: scrolls them."""
        pos = e.position() - QPointF(self.ROOM, self.ROOM)
        room = self._scroll_room()
        if self.choices_view.contains(pos) and room > 0.5:
            self.choice_scroll = min(max(self.choice_scroll - e.angleDelta().y() / 120 * 40, 0.0), room)
            self.choice_hover = self._choice_at(e.position())
            self.update()
            return
        super().wheelEvent(e)

    def _inside(self, box: QWidget) -> QRect:
        """Where a text box is in the bubble (in the window, it's ROOM further right and down)."""
        return box.geometry().translated(-self.ROOM, -self.ROOM)

    def _draw_box(self, p: QPainter, box: QWidget):
        """A text box (hidden while it pops up) as it will look: wrapped and scrolled the same. Drawn apart, a long
        message showed from its start, then jumped to where the box was scrolled."""
        p.save()
        box.render(p, self._inside(box).topLeft(), renderFlags=QWidget.RenderFlag.DrawChildren)
        p.restore()

    def _mic_center(self) -> QPointF:
        """Right of the line's first row (in the bubble)."""
        row = QFontMetricsF(self.line.font()).height()
        return QPointF(self.width() - 2 * self.ROOM - self.PAD - 4, self._inside(self.line).top() + row / 2 + 0.5)

    def _on_mic(self, pos: QPointF) -> bool:
        pos = pos - QPointF(self.ROOM, self.ROOM)
        c = self._mic_center()
        return self.input_open and not self.line.isReadOnly() and self.voice != "writing" \
            and abs(pos.x() - c.x()) <= 11 \
            and abs(pos.y() - c.y()) <= 11

    def _draw_voice(self, p: QPainter):
        """At the end of the line: the mic; while listening, a stop button (how loud shows on the pet); while
        it's written down, three dots."""
        c = self._mic_center()
        p.setPen(Qt.PenStyle.NoPen)
        if self.voice == "listening":
            p.setBrush(INK)
            p.drawRoundedRect(QRectF(c.x() - 4.5, c.y() - 4.5, 9, 9), 2, 2)
        elif self.voice == "writing":
            t = time.monotonic()
            for k in range(3):
                color = QColor(POP)
                color.setAlphaF(0.35 + 0.65 * max(0.0, math.sin(t * 6 - k * 0.9)))
                p.setBrush(color)
                p.drawEllipse(QPointF(c.x() - 8 + k * 6, c.y()), 2, 2)
        else:
            _mic(p, c, INK if self.mic_hover else FAINT)

    def mouseMoveEvent(self, e):
        hover = self._choice_at(e.position())
        on_link = self._link_at(e.position())
        if hover != self.choice_hover or on_link != self.link_hover:
            self.choice_hover, self.link_hover = hover, on_link
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover >= 0 or on_link else Qt.CursorShape.ArrowCursor)
            self.update()
        over = self._on_mic(e.position())
        if over != self.mic_hover:
            self.mic_hover = over
            self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
            if over and not self.voice and self.mic_tip:
                QToolTip.showText(e.globalPosition().toPoint(), self.mic_tip, self)
            else:
                QToolTip.hideText()
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.choice_hover >= 0 or self.link_hover:
            self.choice_hover, self.link_hover = -1, ""
            self.unsetCursor()
            self.update()
        if self.mic_hover:
            self.mic_hover = False
            self.unsetCursor()
            self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._on_mic(e.position()):
            QToolTip.hideText()
            self.mic_clicked.emit()
            return
        link = self._link_at(e.position()) if e.button() == Qt.MouseButton.LeftButton else ""
        if link == "keys":  # (the bubble's own: what the keys do, shown or hidden)
            self.keys_open = not self.keys_open
            self._relayout()
            return
        if link and not self.popping():
            self.link.emit(link)
            return
        choice = self._choice_at(e.position()) if e.button() == Qt.MouseButton.LeftButton else -1
        if choice >= 0 and not self.popping():
            self.chosen.emit(choice)
            return
        super().mousePressEvent(e)

    def setVisible(self, visible: bool):
        """Hidden empty: Windows shows a window again as it last looked, before it's painted anew, so the bubble
        flashed up whole before popping."""
        if not visible and self.isVisible():
            self.text.hide()  # (they show again once it has popped up)
            self.line.hide()
            self.blank = True
            self.repaint()
            self.blank = False
        super().setVisible(visible)

    def showEvent(self, e):
        super().showEvent(e)
        desktop.own_window(int(self.winId()))
        self.shown.emit()

    # ------------------------------------------------------------ input

    def keyPressEvent(self, e):
        """Keys typed while the bubble is still popping up (the line shows when it's done); Esc while listening
        (from the line too: it doesn't take Esc)."""
        if e.key() == Qt.Key.Key_Escape and self.voice == "listening":
            self.voice_cancelled.emit()
        elif self.input_open and not self.line.isVisible() and e.text().isprintable() and e.text():
            self.early_keys += e.text()
        else:
            super().keyPressEvent(e)

    def _choice_key(self, e) -> bool:
        """Under choices: Tab skips the question, Shift+Tab goes back to the one before. Into the empty line: ↑/↓
        go from one choice to the next, Space picks it (or, where several can be picked, ticks or unticks it)."""
        if not self.choices:
            return False
        if e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            key = "back" if e.key() == Qt.Key.Key_Backtab or e.modifiers() & Qt.KeyboardModifier.ShiftModifier \
                else "skip"
            if any(k == key for k, _ in self.links):
                self.link.emit(key)
            return True
        if e.modifiers() & ~Qt.KeyboardModifier.KeypadModifier or self.line.toPlainText():
            return False
        if e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 1 if e.key() == Qt.Key.Key_Down else -1
            at = self.cursor
            self.go_to((at + step) % len(self.choices) if at >= 0 else (0 if step > 0 else len(self.choices) - 1))
            return True
        if e.key() == Qt.Key.Key_Space and self.cursor >= 0:
            self.chosen.emit(self.cursor)
            return True
        return False

    def _send(self):
        """Enter: send, and keep the line open (and the keyboard) for whatever comes next. With nothing typed,
        under choices: the ones ticked ("")."""
        text = self.line.toPlainText().strip()
        if not text and self.choices and self.picked:
            self.submitted.emit("")
        elif text:
            self.line.clear()
            if self.follow_up:
                self.line.setPlaceholderText(self.follow_up)
            self.submitted.emit(text)
