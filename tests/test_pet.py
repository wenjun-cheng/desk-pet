"""The pet's body with a fake mouse: carried and dropped, asleep and awake, what its face shows."""
import time

from PySide6.QtCore import QPoint, Qt

from conftest import wait


class _Press:
    def __init__(self, at):
        self.at = at

    def button(self):
        return Qt.MouseButton.LeftButton

    def globalPosition(self):
        from PySide6.QtCore import QPointF
        return QPointF(self.at)


def test_carried_and_dropped_from_high(world):
    w, pet = world, world.pet
    home = pet.pos()
    start = pet.mapToGlobal(QPoint(36, 56))
    w.mouse.at, w.mouse.down = start, True
    pet.mousePressEvent(_Press(start))
    landed = []
    pet.landed.connect(lambda: landed.append(time.monotonic()))
    w.mouse.at = start + QPoint(-120, -300)  # up and to the left, faster than any mouse event
    wait(80)
    assert pet.carried() and pet.drag_offset is not None
    assert pet.pos() == w.mouse.at - QPoint(pet.SIZE // 2, pet.SIZE // 2)  # held by the middle
    assert pet.mark == "" and pet._look(time.monotonic()).eyes == "wide"
    w.mouse.down = False  # let go (no release event reaches the pet)
    wait(600)
    assert landed and not pet.carried()
    assert pet.y() == home.y() and abs(pet.x() - (home.x() - 120)) < 5
    assert pet.settings["x"] == pet.x() + pet.SIZE // 2
    assert pet._look(time.monotonic()).eyes == "spiral" or time.monotonic() > pet.dizzy_until


def test_low_drop_isnt_dizzy(world):
    w, pet = world, world.pet
    start = pet.mapToGlobal(QPoint(36, 56))
    w.mouse.at, w.mouse.down = start, True
    pet.mousePressEvent(_Press(start))
    w.mouse.at = start + QPoint(60, -40)
    wait(80)
    w.mouse.down = False
    wait(300)
    assert not pet.carried() and pet.dizzy_until < time.monotonic()


def test_a_click_isnt_a_drag(world):
    w, pet = world, world.pet
    clicks = []
    pet.clicked.connect(lambda: clicks.append(1))
    start = pet.mapToGlobal(QPoint(36, 56))
    w.mouse.at, w.mouse.down = start, True
    pet.mousePressEvent(_Press(start))
    wait(50)
    w.mouse.down = False
    pet.mouseReleaseEvent(_Press(start))
    assert clicks == [1] and not pet.carried()


def test_sleeps_and_wakes(world, monkeypatch):
    from deskpet import desktop
    pet = world.pet
    monkeypatch.setattr(desktop, "idle_seconds", lambda: 3600.0)
    pet._check()
    assert pet.asleep and pet._look(time.monotonic()).eyes == "closed"
    world.chat.pending = 1  # not while Claude works
    pet._check()
    assert not pet.asleep
    world.chat.pending = 0
    pet._check()
    monkeypatch.setattr(desktop, "idle_seconds", lambda: 1.0)
    pet._check()
    assert not pet.asleep and pet._look(time.monotonic()).eyes == "squeeze"  # a stretch


def test_faces(world):
    pet, chat = world.pet, world.chat
    wait(500)
    now = time.monotonic()
    chat.pending = 1
    assert pet._look(now).eyes in ("open", "focus", "closed")  # working faces
    chat.in_terminal = True
    look = pet._look(time.monotonic())
    assert look.mark == "?"
    chat.in_terminal, chat.pending = False, 0
    chat.error_until = time.monotonic() + 0.6
    assert pet._look(time.monotonic()).eyes == "cross"


def test_bubble_tail_by_the_eyes(world):
    w = world
    tip, flipped = w.pet.tail_tip()
    eye_gap = w.pet.EYE_GAP - 2
    middle = w.pet.x() + w.pet.SIZE / 2
    assert abs(tip.x() - (middle + (-eye_gap if flipped else eye_gap))) <= 1
