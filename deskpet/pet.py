"""The pet itself: where it sits, how it moves and what its face shows. It lives on the bottom edge of the screen
with only the top of its head and its eyes showing. It jumps out to talk, hops, can be carried along the bottom
and dropped, falls asleep when nobody's around, and its face shows what Claude is up to (see Mood). What a click,
a double click or the right button do is up to the chat and the menu (its signals)."""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from . import desktop, face
from .settings import Settings

SLEEP_AFTER = 5 * 60   # seconds without mouse or keyboard input
HOP_MS = 380
JUMP_MS = 380          # jumping out from behind the edge to talk
LEAVE_DELAY = 0.08     # seconds after it stops having a reason to be out before it goes back behind the edge
FALL = 14000           # px/s², dropping back to the edge after being carried (from 800 px up: 0.34 s)
LAND_MS = 160
STRETCH = 1.3          # seconds of a stretch when it wakes up
HIGH_DROP = 150        # px: dropped from higher, it lands dizzy
WORK_FACES = ("think", "focus", "scan", "blink")   # while Claude works, in turn (see _working_face)


@dataclass
class Mood:
    """What Claude is up to, as far as the face goes (the chat keeps its own up to date)."""
    waiting: bool = False         # for the user: a question in the bubble, or a dialog in the terminal
    waiting_since: float = 0.0    # (its head tilts now and then while waiting)
    thinking: bool = False        # working on something the user asked (and not waiting for them)
    done: bool = False            # a reply came while the bubble was put away: ! until the bubble is opened
    happy_until: float = 0.0      # a reply came with the bubble open: ^ ^ for a moment
    error_until: float = 0.0      # something went wrong: x x until then, then a sweat drop...
    sweat_since: float | None = None   # ...until the bubble is opened or a message is sent


def work_area(screen) -> QRect:
    """The screen minus the taskbar or dock. Qt's availableGeometry can run past the screen's edge (seen with an
    auto-hiding taskbar plus a bar docked at the top), so keep it within the screen."""
    return screen.availableGeometry().intersected(screen.geometry())


class Pet(QWidget):
    """Only the top of its head and its eyes show; it jumps out to talk (the mouse on it only makes it smile).
    Drag it along the bottom; let go anywhere and it falls back to the edge."""
    clicked = Signal()
    double_clicked = Signal()
    menu_requested = Signal(QPoint)
    picked_up = Signal()           # carried away from the edge...
    landed = Signal()              # ...and back on it
    moved = Signal()               # put somewhere else on the edge (the screens changed)
    SIZE, EDGE = face.SIZE, face.EDGE   # the window; where the bottom of the screen is in it
    BODY_W, BODY_H, FEET, EYE_GAP = face.BODY_W, face.BODY_H, face.FEET, face.EYE_GAP
    OUT, PEEK = 0.0, face.PEEK     # how far below "all the way out" it sits: all the way out / just the eyes show

    def __init__(self, settings: Settings):
        super().__init__(None)
        self.settings = settings
        self.mood = Mood()                                  # (the chat's, once there is one)
        self.keep_out: Callable[[], bool] = lambda: False   # whether something it's showing keeps it out
        self.companions: list[QWidget] = []                 # its other windows, kept on top with it

        self.setWindowTitle("Desk Pet")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)  # (macOS hides tool windows otherwise)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.t0 = time.monotonic()
        self.blink_at = self.t0 + random.uniform(2, 5)   # next blink
        self.hop_start: float | None = None
        self.sink = self.PEEK       # OUT .. PEEK
        self.left_at = 0.0          # when it last had a reason to be out (see keep_out, a hop)
        self.hovering = False       # the mouse is on it: happy eyes (it doesn't come out for that)
        self.press: QPoint | None = None   # where a left press started, while it may still be a click
        self.button_up_since: float | None = None   # pressed, yet the button is up (see _follow_mouse)
        self.drag_offset: QPoint | None = None
        self.asleep = False
        self.jump_start: float | None = None   # jumping out from behind the edge since
        self.jump_from = self.PEEK
        self.jumps = 0              # counts jumps, so what one would do halfway can be called off
        self.fall: tuple[float, int, QPoint] | None = None   # let go after a drag: (since, from y, landing spot)
        self.landed_at = 0.0
        self.high_drop = False
        self.dizzy_until = 0.0      # dropped from high up
        self.stretch_start = -9.0   # woke up
        self.work_face, self.work_face_since, self.work_face_for = WORK_FACES[0], 0.0, 0.0
        self.glance_at = self.t0 + random.uniform(60, 150)   # now and then, idle, it looks around
        self.glance_start = -9.0
        self.mark, self.mark_since = "", 0.0
        self.raised_at = 0.0

        self.frame_timer = QTimer(self, interval=33, timeout=self._tick)   # ~30 fps
        self.frame_timer.start()
        self.check_timer = QTimer(self, interval=1000, timeout=self._check)  # falling asleep
        self.check_timer.start()
        self._place()
        app = QGuiApplication.instance()
        for screen in app.screens():
            self._watch_screen(screen)
        app.screenAdded.connect(self._watch_screen)
        app.screenRemoved.connect(lambda _: QTimer.singleShot(0, self._settle_on_screen))

    # ------------------------------------------------------------ where it sits

    def _spot(self, screen=None) -> QPoint:
        """Where the window goes on the bottom edge, at its spot along it."""
        area = work_area(screen or QGuiApplication.primaryScreen())
        x = self.settings["x"] if self.settings["x"] is not None else area.left() + area.width() * 0.85
        x = int(min(max(x, area.left() + 30), area.right() - 30))
        self.settings["x"] = x
        return QPoint(x - self.SIZE // 2, area.bottom() + 1 - self.EDGE)

    def _place(self, screen=None):
        self.move(self._spot(screen))

    def _watch_screen(self, screen):
        screen.geometryChanged.connect(self._settle_on_screen)
        screen.availableGeometryChanged.connect(self._settle_on_screen)

    def _settle_on_screen(self, *_):
        """The screens changed (resolution, scaling, taskbar, a monitor unplugged): back on the edge."""
        if self.drag_offset is None and self.fall is None:
            self._place(QGuiApplication.screenAt(self.geometry().center()))
            self.moved.emit()

    def tail_tip(self) -> tuple[QPoint, bool]:
        """(where the bubble's tail ends, whether the bubble is mirrored): just over the pet's head, a little in
        from its right eye, with the bubble reaching right; on the right half of the screen, mirrored: by its left
        eye, the bubble reaching left."""
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        flipped = self.x() + self.SIZE / 2 > work_area(screen).center().x()
        eye = self.SIZE / 2 + (self.EYE_GAP - 2) * (-1 if flipped else 1)
        return QPoint(self.x() + round(eye), self.y() + int(self.EDGE - self.FEET - self.BODY_H)), flipped

    def carried(self) -> bool:
        return self.drag_offset is not None or self.fall is not None

    # ------------------------------------------------------------ moves

    def hop(self):
        self.hop_start = time.monotonic()
        self.left_at = self.hop_start  # come out for it

    def jump_out(self, then: Callable[[], None]):
        """Out to talk: from behind the edge, a jump, and `then` as it comes down; if it's out already, a hop,
        and `then` at once."""
        self.jumps += 1
        if self.sink > self.OUT + 1 and self.jump_start is None:
            self.jump_start, self.jump_from = time.monotonic(), self.sink
            self.left_at = self.jump_start
            jump = self.jumps
            QTimer.singleShot(int(JUMP_MS * 0.5), lambda: jump == self.jumps and then())
        else:
            self.hop()
            then()

    def call_off_jump(self):
        """What a jump on its way would do halfway (see jump_out), it doesn't."""
        self.jumps += 1

    def jumping(self) -> bool:
        return self.jump_start is not None

    def be_out(self):
        """All the way out at once (no jump)."""
        self.sink = self.OUT

    def _drop(self):
        """Let go after a drag: it falls back down to the bottom edge, below where it was let go."""
        center = QPoint(self.x() + self.SIZE // 2, self.y() + self.SIZE // 2)
        self.settings["x"] = center.x()
        spot = self._spot(QGuiApplication.screenAt(center))
        self.settings.save()
        self.high_drop = spot.y() - self.y() > HIGH_DROP
        if spot.y() - self.y() > 2:
            self.fall = (time.monotonic(), self.y(), spot)
        else:
            self.move(spot)
            self._landed()

    def _landed(self):
        """Down on the edge: all the way out (then back behind the edge, unless something keeps it out)."""
        self.frame_timer.setInterval(100 if self.asleep else 33)  # (faster while carried and falling)
        self.fall = None
        self.landed_at = self.left_at = time.monotonic()
        if self.high_drop:
            self.dizzy_until, self.high_drop = self.landed_at + 0.6, False
        self.sink = self.OUT
        self.landed.emit()

    # ------------------------------------------------------------ every frame

    def _check(self):
        """Asleep after SLEEP_AFTER without the mouse or keyboard, unless Claude is working or waiting."""
        asleep = desktop.idle_seconds() > SLEEP_AFTER and not self.mood.thinking and not self.mood.waiting
        if asleep and not self.asleep:
            self.asleep = True
            self.frame_timer.setInterval(100)  # slow breathing needs fewer frames
        elif self.asleep and not asleep:
            self._wake()

    def _wake(self):
        self.asleep = False
        self.frame_timer.setInterval(33)
        self.stretch_start = time.monotonic()  # and a stretch

    def _pet_rect(self, sink: float) -> QRectF:
        """Where the pet is (head to the edge), when it sits `sink` below all the way out."""
        top = self.EDGE - self.FEET - self.BODY_H + sink
        return QRectF(self.SIZE / 2 - self.BODY_W / 2 - 3, top - 3, self.BODY_W + 6, max(0.0, self.EDGE - top + 3))

    def _mouse_over(self) -> bool:
        """Whether the mouse is on the part of the pet that shows (all of it once it's mostly out)."""
        pos = QPointF(self.mapFromGlobal(QCursor.pos()))
        return self._pet_rect(self.OUT if self.sink < self.PEEK / 2 else self.sink).contains(pos)

    def _tick(self):
        now = time.monotonic()
        if self.fall is not None:
            since, from_y, spot = self.fall
            y = from_y + FALL / 2 * (now - since) ** 2
            if y >= spot.y():
                self.move(spot)
                self._landed()
            else:
                self.move(spot.x(), int(y))
        over = self._mouse_over()
        if over and self.asleep:  # someone's here
            self._wake()
        self._follow_mouse(now)
        carried = self.carried()
        self.hovering = over and not carried
        if self.keep_out():
            self.left_at = now  # out while talking and while its menu is open (the mouse alone doesn't do it)
        if now - self.raised_at > 0.25:
            self.raised_at = now
            self._stay_on_top()
        if self.jump_start is not None:
            self.sink = self._jump_motion(now)[0]
        else:
            out = carried or self.hop_start is not None or now - self.left_at < LEAVE_DELAY
            target = self.OUT if out else self.PEEK
            self.sink += (target - self.sink) * (0.3 if target < self.sink else 0.38)
            if abs(target - self.sink) < 0.1:
                self.sink = target
        self.update()

    def _follow_mouse(self, now: float):
        """Pressed or carried: go by where the pointer is and whether the button is down, not by mouse events
        alone. The pet never takes the keyboard, so it's never the window in front, and Windows sends such a
        window the mouse's moves and release only while the pointer is over a visible part of it. Dragged quickly
        (above all upward), the pointer got ahead of it, the rest went to the window underneath, and the pet
        hung in the air."""
        if self.press is None and self.drag_offset is None:
            self.button_up_since = None
            return
        down, pos = desktop.mouse_button_down(), QCursor.pos()
        if down is None:  # (can't tell here: by the mouse events, then)
            down = bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        if self.press is not None and down and (pos - self.press).manhattanLength() > 4:
            self._start_drag()
        if self.drag_offset is not None:
            if down:
                self.move(pos - self.drag_offset)
            else:  # let go (its release may not reach the pet)
                self.drag_offset = None
                self._drop()
        elif down:
            self.button_up_since = None
        elif self.button_up_since is None:  # a click: its release, if it reaches the pet, comes in a moment
            self.button_up_since = now
        elif now - self.button_up_since > 0.2:  # it went elsewhere: no click
            self.press = self.button_up_since = None

    def _start_drag(self):
        self.drag_offset, self.press = QPoint(self.SIZE // 2, self.SIZE // 2), None  # held by the middle
        self.frame_timer.setInterval(15)  # it follows the pointer frame by frame: smoothly (until it lands)
        self.fall = None
        self.picked_up.emit()

    def _stay_on_top(self):
        """Over the taskbar too (see desktop.keep_on_top), but not while a menu of the pet's is open (it would
        end up under them)."""
        if QApplication.activePopupWidget() is not None:
            return
        shown = [w for w in (*self.companions, self) if w.isVisible()]
        desktop.keep_on_top([int(w.winId()) for w in shown])

    def _jump_motion(self, now: float) -> tuple[float, float, float]:
        """(sink, lift, squash) while jumping out: a crouch, then up from behind the edge in an arc, landing
        on it. Negative squash stretches it tall."""
        k = (now - self.jump_start) * 1000 / JUMP_MS
        if k >= 1:
            self.jump_start = None
            self.left_at = now
            return self.OUT, 0.0, 0.0
        if k < 0.18:
            return self.jump_from, 0.0, 0.14 * math.sin(math.pi * k / 0.18)
        u = (k - 0.18) / 0.82
        rise = 1 - (1 - min(1.0, u * 1.3)) ** 3
        sink = self.jump_from + (self.OUT - self.jump_from) * rise
        lift = 12 * math.sin(math.pi * u)
        if u < 0.35:
            squash = -0.07 * math.sin(math.pi * u / 0.35)
        elif u > 0.85:
            squash = 0.1 * math.sin(math.pi * (u - 0.85) / 0.15)
        else:
            squash = 0.0
        return sink, lift, squash

    # ------------------------------------------------------------ drawing

    def paintEvent(self, _):
        p = QPainter(self)
        if self.drag_offset is not None:  # all of the window takes the mouse while it's carried (see _follow_mouse)
            p.fillRect(QRectF(0, 0, self.SIZE, self.SIZE), QColor(0, 0, 0, 1))
        elif self.fall is None:
            # where it can be clicked, all of it takes the mouse: a window lets clicks through where it's fully
            # transparent, which between its eyes and around its head would reach what's underneath
            p.setClipRect(QRectF(0, 0, self.SIZE, self.EDGE))
            p.fillRect(self._pet_rect(self.OUT if self.sink < self.PEEK / 2 else self.sink), QColor(0, 0, 0, 1))
            p.setClipping(False)
        face.draw(p, self._look(time.monotonic()))

    def _look(self, t: float) -> face.Look:
        """What it looks like now: how it moves, and its face, from what's going on (the first that applies:
        carried, dizzy, an error, jumping, waiting, working, stretching, asleep, happy, idle)."""
        mood = self.mood
        look = face.Look(sink=self.sink, carried=self.carried())
        if self.jump_start is not None:
            look.sink, look.lift, look.squash = self._jump_motion(t)
        elif t - self.landed_at < LAND_MS / 1000:  # squashed for a moment by the landing
            look.squash = 0.16 * math.sin(math.pi * (t - self.landed_at) * 1000 / LAND_MS)
        elif self.hop_start is not None:
            k = (t - self.hop_start) * 1000 / HOP_MS
            if k >= 1:
                self.hop_start = None
            else:
                look.lift = 11 * 4 * k * (1 - k)
                look.squash = 0.12 if k < 0.15 or k > 0.85 else 0.0  # crouch before jumping and when landing
        look.breathe = math.sin((t - self.t0) * (1.3 if self.asleep else 2.0)) * (1.3 if self.asleep else 0.8)
        look.mark_left = not self.tail_tip()[1]  # marks on the side away from the bubble's tail
        blink = self._blinking(t)
        if look.carried:
            look.eyes, look.kick = "wide", (t * 3) % 1.0
        elif t < self.dizzy_until:
            look.eyes, look.spin = "spiral", t * 9
        elif t < mood.error_until:
            look.eyes = "cross"
        elif self.jump_start is not None or self.hop_start is not None:
            look.eyes = "happy"
        elif mood.waiting:  # looking at you, the head tilting now and then
            look.eyes, look.look = ("closed", (0.0, 0.0)) if blink else ("open", self._toward_mouse())
            phase = (t - mood.waiting_since) % 4.0
            if phase < 1.1:
                look.tilt = 8 * math.sin(math.pi * phase / 1.1) * (-1 if look.mark_left else 1)
        elif mood.thinking:
            self._working_face(t, look)
        elif t - self.stretch_start < STRETCH:
            k = math.sin(math.pi * (t - self.stretch_start) / STRETCH)
            look.eyes, look.squash, look.lift = "squeeze", -0.12 * k, 1.5 * k
        elif self.asleep:
            look.eyes, look.zzz = "closed", ((t - self.t0) / 2.4) % 1.0
        elif mood.done or t < mood.happy_until or t - self.left_at < LEAVE_DELAY or self.hovering:
            look.eyes = "happy"
        else:
            look.eyes, look.look = ("closed", (0.0, 0.0)) if blink else \
                ("open", self._glance(t) or self._toward_mouse())
        # over its head: ? while it waits for you, else ! for a reply you haven't seen
        mark = "?" if mood.waiting else "!" if mood.done else ""
        if mark != self.mark:
            self.mark, self.mark_since = mark, t
        look.mark, look.mark_pop, look.bob = mark, (t - self.mark_since) / 0.35, math.sin(t * 2.4)
        if mood.sweat_since is not None and t >= mood.error_until and not look.carried:
            look.sweat = max(0.01, min(1.0, (t - mood.error_until) / 0.8))  # sliding down a little, then staying
        return look

    def _blinking(self, t: float) -> bool:
        if t < self.blink_at:
            return False
        if t >= self.blink_at + 0.13:
            self.blink_at = t + random.uniform(2.5, 6) if random.random() > 0.2 else t + 0.25  # sometimes twice
        return True

    def _toward_mouse(self) -> tuple[float, float]:
        """Where open eyes look: towards the mouse (all the way once it's 160 px off)."""
        eyes = QPoint(self.SIZE // 2, int(self.EDGE - self.FEET - self.BODY_H * 0.55 + self.sink))
        d = QCursor.pos() - self.mapToGlobal(eyes)
        dist = math.hypot(d.x(), d.y()) or 1
        reach = min(1.0, dist / 160)
        return d.x() / dist * reach, d.y() / dist * reach

    def _glance(self, t: float) -> tuple[float, float] | None:
        """Now and then, idle, a look around: to one side, then the other."""
        if t >= self.glance_at:
            self.glance_start, self.glance_at = t, t + random.uniform(60, 150)
        k = t - self.glance_start
        return (-1.0, -0.3) if k < 0.9 else (1.0, -0.3) if k < 1.9 else None

    def _working_face(self, t: float, look: face.Look):
        """While Claude works: a few faces in turn (whatever it's doing), each about 3 s, a blink between."""
        k = t - self.work_face_since
        if k >= self.work_face_for:
            self.work_face = random.choice([f for f in WORK_FACES if f != self.work_face])
            self.work_face_since, self.work_face_for, k = t, random.uniform(2.6, 3.4), 0.0
        if k < 0.1 or self.work_face == "blink" and (0.5 < k < 0.62 or 0.8 < k < 0.92):
            look.eyes = "closed"
        elif self.work_face == "think":  # looking up and around
            look.eyes, look.look = "open", (0.85 * math.sin(t * 1.3), -1.0)
        elif self.work_face == "focus":  # narrowed, looking down a little
            look.eyes, look.look = "focus", (0.0, 0.4)
        elif self.work_face == "scan":  # looking from side to side
            look.eyes, look.look = "open", (math.sin(t * 2.2), 0.1)
        else:  # looking ahead, blinking twice
            look.eyes, look.look = "open", (0.0, 0.0)

    # ------------------------------------------------------------ mouse

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press = e.globalPosition().toPoint()
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        pos = e.globalPosition().toPoint()
        if self.press is not None and (pos - self.press).manhattanLength() > 4:
            self._start_drag()
        if self.drag_offset is not None:  # (between frames; see _follow_mouse)
            self.move(pos - self.drag_offset)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.drag_offset is not None:
            self.drag_offset = None
            self._drop()
        elif self.press is not None:
            self.press = None
            self.clicked.emit()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
