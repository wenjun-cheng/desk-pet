"""How the pet is drawn: its body, feet and eyes, and the little marks over its head. A Look says what to draw;
draw() paints it into the pet's window (SIZE x SIZE, the screen's bottom edge at EDGE)."""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen

BODY = QColor("#d97757")
BODY_LIGHT = QColor("#e48c6c")
BODY_DARK = QColor("#c4663f")
EYE = QColor("#2b211c")
PAPER = QColor("#fffbf4")   # the marks' badge: the bubble's paper and ink, so they read on any background
SWEAT = QColor("#8ec8ec")

SIZE = 72                   # the window
EDGE = 64                   # where the bottom of the screen is, in this window
BODY_W, BODY_H, FEET = 40, 29, 4
EYE_GAP = 8.5               # each eye from the middle
PEEK = 13.0                 # sunk this far below all the way out, just its eyes and the top of its head show


@dataclass
class Look:
    sink: float = 0.0           # below all the way out: 0 out .. PEEK peeking
    lift: float = 0.0           # up in the air (jumping)
    squash: float = 0.0         # flatter and wider; negative: taller
    breathe: float = 0.0
    tilt: float = 0.0           # degrees, the whole pet leaning (a head tilt)
    carried: bool = False       # hanging from the mouse: not cut at the screen's edge, feet dangling
    kick: float = 0.0           # carried: its feet kicking, 0..1 through a kick
    # open (looking at `look`), wide (surprised), happy (^ ^), closed (- -), focus (narrowed),
    # cross (x x), squeeze (> <), spiral (dizzy)
    eyes: str = "open"
    look: tuple[float, float] = (0.0, 0.0)   # where open eyes look, -1..1 each way
    spin: float = 0.0           # spiral eyes' turn, radians
    mark: str = ""              # over its head: "!" or "?"
    mark_pop: float = 1.0       # 0..1 as the mark pops up
    bob: float = 0.0            # the mark floating up and down, -1..1
    mark_left: bool = True      # the mark's side (away from the bubble's tail)
    sweat: float = 0.0          # 0..1 a sweat drop sliding down the side of its head
    zzz: float | None = None    # asleep: the z's, 0..1 as they float away


def draw(p: QPainter, look: Look):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not look.carried:
        p.setClipRect(QRectF(0, 0, SIZE, EDGE))  # nothing past the screen's edge
    ground = EDGE + (0 if look.carried else look.sink) - look.lift - (3 if look.carried else 0)
    cx = SIZE / 2
    if look.tilt:
        p.translate(cx, ground)
        p.rotate(look.tilt)
        p.translate(-cx, -ground)
    w = BODY_W * (1 + look.squash * 0.6)
    h = (BODY_H + look.breathe) * (1 - look.squash)
    body = QRectF(cx - w / 2, ground - FEET - h, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BODY_DARK)
    for i, fx in enumerate((cx - 12, cx + 5)):  # feet
        drop = 2 if look.carried else 0
        if look.carried and look.kick:
            drop += 1.5 * math.sin(2 * math.pi * look.kick + i * math.pi)  # one up while the other's down
        p.drawRoundedRect(QRectF(fx, ground - FEET - 1 + drop, 7, FEET + 1), 2, 2)
    grad = QLinearGradient(body.topLeft(), body.bottomLeft())  # body: lighter on top
    grad.setColorAt(0, BODY_LIGHT)
    grad.setColorAt(1, BODY)
    p.setBrush(grad)
    p.drawRoundedRect(body, 11, 11)
    _eyes(p, body, look)
    if look.sweat:
        _sweat(p, body, look)
    if look.mark:
        _mark(p, body, look)
    if look.zzz is not None:
        _zzz(p, body, look.zzz)
    p.restore()


def _eyes(p: QPainter, body: QRectF, look: Look):
    ey = body.top() + body.height() * 0.45
    xs = (body.center().x() - EYE_GAP, body.center().x() + EYE_GAP)
    line = QPen(EYE, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    kind = look.eyes
    if kind in ("open", "wide", "focus"):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(EYE)
        ew, eh = {"open": (3.6, 7), "wide": (5, 5), "focus": (4.2, 7)}[kind]
        ox, oy = look.look[0] * 2.6, look.look[1] * 1.8
        for x in xs:
            eye = QRectF(x + ox - ew / 2, ey + oy - eh / 2, ew, eh)
            if kind == "focus":  # narrowed: the top of each eye cut off flat, like lids half down
                p.save()
                p.setClipRect(QRectF(eye.left() - 1, eye.top() + eh * 0.5, ew + 2, eh), Qt.ClipOperation.IntersectClip)
                p.drawRoundedRect(eye, ew / 2, ew / 2)
                p.restore()
            else:
                p.drawRoundedRect(eye, min(ew, eh) / 2, min(ew, eh) / 2)
        return
    p.setPen(line)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for side, x in zip((-1, 1), xs):
        if kind == "closed":
            p.drawLine(QPointF(x - 2.5, ey + 1), QPointF(x + 2.5, ey + 1))
        elif kind == "happy":  # ^
            path = QPainterPath(QPointF(x - 3, ey + 1.5))
            path.lineTo(x, ey - 1.5)
            path.lineTo(x + 3, ey + 1.5)
            p.drawPath(path)
        elif kind == "squeeze":  # > <, pointing inward
            path = QPainterPath(QPointF(x + 2 * side, ey - 2.5))
            path.lineTo(x - 2 * side, ey)
            path.lineTo(x + 2 * side, ey + 2.5)
            p.drawPath(path)
        elif kind == "cross":  # x
            p.setPen(QPen(EYE, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x - 2.2, ey - 2.2), QPointF(x + 2.2, ey + 2.2))
            p.drawLine(QPointF(x - 2.2, ey + 2.2), QPointF(x + 2.2, ey - 2.2))
        elif kind == "spiral":  # @, turning
            p.setPen(QPen(EYE, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            path = QPainterPath()
            for i in range(41):
                a = i / 40 * 4 * math.pi
                r = 0.4 + 3.3 * i / 40
                pt = QPointF(x + r * math.cos(a * side + look.spin), ey + r * math.sin(a * side + look.spin))
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.drawPath(path)


def _mark(p: QPainter, body: QRectF, look: Look):
    """A small paper badge with ! or ? over a top corner of its head, popping up, then floating gently."""
    k = max(0.0, min(1.0, look.mark_pop))
    scale = 1 + 2.4 * (k - 1) ** 3 + 1.4 * (k - 1) ** 2 if k < 1 else 1.0  # ease out with a little overshoot
    r = 6.2
    cx = body.center().x() + (-1 if look.mark_left else 1) * (body.width() / 2 - 2)
    cy = body.top() - 7 + look.bob * 1.2
    p.save()
    p.translate(cx, cy)
    p.scale(scale, scale)
    p.setPen(QPen(EYE, 1.3))
    p.setBrush(PAPER)
    p.drawEllipse(QPointF(0, 0), r, r)
    font = _bold()
    font.setPixelSize(10)
    p.setFont(font)
    p.setPen(EYE)
    p.drawText(QRectF(-r, -r - 0.5, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, look.mark)
    p.restore()


def _sweat(p: QPainter, body: QRectF, look: Look):
    """A sweat drop by the top of its head, on the side away from the bubble, sliding down a little."""
    side = -1 if look.mark_left else 1
    x = body.center().x() + side * (body.width() / 2 - 3)
    y = body.top() + 3 + 4 * look.sweat
    drop = QPainterPath(QPointF(x, y - 5.5))
    drop.cubicTo(QPointF(x + 4.4, y + 0.6), QPointF(x + 3.7, y + 4.4), QPointF(x, y + 4.4))
    drop.cubicTo(QPointF(x - 3.7, y + 4.4), QPointF(x - 4.4, y + 0.6), QPointF(x, y - 5.5))
    p.setPen(QPen(EYE, 0.9))
    p.setBrush(SWEAT)
    p.drawPath(drop)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 200))
    p.drawEllipse(QPointF(x - 1.2, y + 1.4), 1.1, 1.5)


def _zzz(p: QPainter, body: QRectF, phase: float):
    font = _bold()
    for i in range(2):
        k = (phase + i * 0.5) % 1.0  # 0 -> 1 as each z floats away
        font.setPixelSize(int(8 + 4 * k))
        p.setFont(font)
        color = QColor(BODY_LIGHT)
        color.setAlpha(int(230 * (1 - k)))
        p.setPen(color)
        p.drawText(QPointF(body.right() - 2 + 7 * k, body.top() - 2 - 12 * k), "z")


def _bold() -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "Helvetica Neue", "DejaVu Sans"])
    font.setBold(True)
    return font
