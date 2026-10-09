"""A tiny desktop pet that peeks up from the bottom of the screen. Click it to go to Claude Code (opening it if
needed); double-click for a new Claude window.
Run with `pythonw app.py`."""
from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import random
import subprocess
import sys
import time
import winreg
from ctypes import wintypes
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QLockFile, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QActionGroup, QColor, QCursor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter, \
    QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QWidget

__version__ = "0.1.0"
ROOT = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)  # running as a PyInstaller exe
DATA = Path(os.environ.get("APPDATA", str(Path.home()))) / "DeskPet"  # settings and log
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "DeskPet"
# Launching the app again makes the running pet hop instead
SERVER_NAME = f"DeskPet-{os.environ.get('USERNAME', 'user')}"
SLEEP_AFTER = 5 * 60   # seconds without mouse or keyboard input
HOP_MS = 380
# Where Claude starts. Not the home folder itself: Claude Code never remembers trusting that, so it would ask
# every time. A .claude/settings.json in this folder can point autoMemoryDirectory and additionalDirectories
# back at the home folder's, if you want it to work like a session started there.
CLAUDE_DIR = Path.home() / "Claude"
LEAVE_DELAY = 0.08     # seconds after the mouse leaves before it hides again
EDGE_REACH = 80        # in full-screen apps: how near the pet (px, sideways) the mouse must touch the bottom edge
EDGE_LINGER = 1.2      # ... and how long it keeps peeking after that, to give time to reach it
log = logging.getLogger("pet")

BODY = QColor("#d97757")
BODY_LIGHT = QColor("#e48c6c")
BODY_DARK = QColor("#c4663f")
EYE = QColor("#2b211c")

LANGUAGES = {"en": "English", "zh": "中文"}
_STRINGS = {  # key: (English, Chinese)
    "go": ("Go to Claude", "切到 Claude"),
    "new": ("New Claude window", "新开 Claude 窗口"),
    "autostart": ("Start with Windows", "开机自动启动"),
    "language": ("Language / 语言", "Language / 语言"),
    "quit": ("Quit", "退出"),
}
_lang = "en"


def tr(key: str) -> str:
    en, zh = _STRINGS[key]
    return zh if _lang == "zh" else en


# ---------------------------------------------------------------- Windows bits

class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


_u32 = ctypes.WinDLL("user32")
_k32 = ctypes.WinDLL("kernel32")
_k32.GetTickCount.restype = wintypes.DWORD
_u32.GetForegroundWindow.restype = wintypes.HWND
_u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.MonitorFromWindow.restype = wintypes.HMONITOR
_u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_u32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
_u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.SetForegroundWindow.argtypes = [wintypes.HWND]
_u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.IsIconic.argtypes = [wintypes.HWND]
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
TERMINAL_CLASSES = ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass")  # Windows Terminal, old console
# Claude Code titles its terminal "✳ <topic>" while idle, with a spinning ◐◓◑◒ in front while working
CLAUDE_MARKS = "✳◐◓◑◒"


def _idle_seconds() -> float:
    info = _LastInput(cbSize=ctypes.sizeof(_LastInput))
    if not _u32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((_k32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def _fullscreen_app_over(hwnd: int) -> bool:
    """Whether the foreground window fills the whole monitor that the window hwnd is on."""
    fg = _u32.GetForegroundWindow()
    if not fg or fg == hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    _u32.GetClassNameW(fg, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False  # the desktop or the taskbar
    mon = _u32.MonitorFromWindow(fg, 2)
    if mon != _u32.MonitorFromWindow(hwnd, 2):
        return False
    info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
    r = wintypes.RECT()
    if not _u32.GetMonitorInfoW(mon, ctypes.byref(info)) or not _u32.GetWindowRect(fg, ctypes.byref(r)):
        return False
    m = info.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def _claude_windows() -> list[int]:
    """Terminal windows showing a Claude Code session, front-most first."""
    found = []

    def check(hwnd, _):
        if _u32.IsWindowVisible(hwnd):
            cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
            _u32.GetClassNameW(hwnd, cls, 64)
            if cls.value in TERMINAL_CLASSES and _u32.GetWindowTextW(hwnd, title, 256):
                if title.value[0] in CLAUDE_MARKS or "Claude Code" in title.value:
                    found.append(hwnd)
        return True

    _u32.EnumWindows(_EnumProc(check), 0)  # goes from the top of the window stack down
    return found


def _bring_to_front(hwnd: int):
    if _u32.IsIconic(hwnd):
        _u32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # allowed, since we just got the user's click (the last input); if Windows still says no, tapping Alt
    # first lifts the foreground lock
    if not _u32.SetForegroundWindow(hwnd):
        _u32.keybd_event(0x12, 0, 0, 0)
        _u32.keybd_event(0x12, 0, 2, 0)
        _u32.SetForegroundWindow(hwnd)


def _launch_command() -> str:
    if FROZEN:
        return f'"{sys.executable}"'
    return f'"{Path(sys.executable).with_name("pythonw.exe")}" "{ROOT / "app.py"}"'


def _autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def _clean_env() -> dict[str, str]:
    """Our environment for the new terminal, minus what a Claude Code session sets for its own commands
    (if the pet was started from inside one): NO_COLOR would turn Claude's colors off, and the CLAUDE_*
    variables would make the new Claude think it belongs to that session."""
    env = dict(os.environ)
    if "CLAUDECODE" in env:
        for key in list(env):
            if key.upper().startswith("CLAUDE") or key.upper() in ("NO_COLOR", "GIT_TERMINAL_PROMPT"):
                del env[key]
    return env


def work_area(screen) -> QRect:
    """The screen minus the taskbar. Qt's availableGeometry can run past the screen's edge (seen with an
    auto-hiding taskbar plus a bar docked at the top), so keep it within the screen."""
    return screen.availableGeometry().intersected(screen.geometry())


# ---------------------------------------------------------------- the pet


class Pet(QWidget):
    """Lives on the bottom edge of the screen. Normally only the top of its head and its eyes show; it comes
    out when the mouse is over it. While a full-screen app is in front it hides completely, and peeks out when
    the mouse touches the bottom edge near it, like the Dock. Drag it along the bottom; let go anywhere and it
    goes back to the edge."""
    SIZE = 72              # the window
    EDGE = 64              # where the bottom of the screen is, in this window
    BODY_W, BODY_H, FEET = 40, 29, 4
    # how far below "all the way out" it sits
    OUT, PEEK, GONE = 0.0, 13.0, 36.0   # all the way out / just the eyes show / out of sight

    def __init__(self):
        super().__init__(None)
        self.settings_path = DATA / "settings.json"
        self.s = {"x": None, "language": "en"}  # x: the middle of the pet
        try:
            self.s.update(json.loads(self.settings_path.read_text("utf-8")))
        except (OSError, ValueError):
            pass
        global _lang
        _lang = self.s["language"] if self.s["language"] in LANGUAGES else "en"

        self.setWindowTitle("Desk Pet")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.t0 = time.monotonic()
        self.blink_at = self.t0 + random.uniform(2, 5)   # next blink
        self.hop_start: float | None = None
        self.sink = self.PEEK       # OUT .. GONE
        self.left_at = 0.0          # when the mouse last left it
        self.edge_at = 0.0          # when the mouse last touched the bottom edge near it (full-screen apps)
        self.press: QPoint | None = None   # where a left press started, while it may still be a click
        self.drag_offset: QPoint | None = None
        self.asleep = False
        self.fullscreen = False     # a full-screen app is in front
        self.last_launch = 0.0

        self.menu = self._build_menu()
        self.frame_timer = QTimer(self, interval=33, timeout=self._tick)   # ~30 fps
        self.frame_timer.start()
        self.check_timer = QTimer(self, interval=1000, timeout=self._check)  # sleep, full-screen apps
        self.check_timer.start()
        self._place()

    # ------------------------------------------------------------ where it sits

    def _place(self, screen=None):
        """On the bottom edge, at its spot along it."""
        area = work_area(screen or QGuiApplication.primaryScreen())
        x = self.s["x"] if self.s["x"] is not None else area.left() + area.width() * 0.85
        x = int(min(max(x, area.left() + 30), area.right() - 30))
        self.s["x"] = x
        self.move(x - self.SIZE // 2, area.bottom() + 1 - self.EDGE)

    def _snap(self):
        """After being dragged: back down to the bottom edge, where it was let go."""
        center = self.geometry().center()
        self.s["x"] = center.x()
        self._place(QGuiApplication.screenAt(center))
        self.save()

    def save(self):
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.settings_path)

    # ------------------------------------------------------------ behavior

    def _check(self):
        asleep = _idle_seconds() > SLEEP_AFTER
        if asleep != self.asleep:
            self.asleep = asleep
            self.frame_timer.setInterval(100 if asleep else 33)  # slow breathing needs fewer frames
        fullscreen = _fullscreen_app_over(int(self.winId()))
        if fullscreen != self.fullscreen:
            self.fullscreen = fullscreen
            log.info("Full-screen app %s", "in front: hiding" if fullscreen else "gone")

    def _pet_rect(self, sink: float) -> QRectF:
        """Where the pet is (head to the edge), when it sits `sink` below all the way out."""
        top = self.EDGE - self.FEET - self.BODY_H + sink
        return QRectF(self.SIZE / 2 - self.BODY_W / 2 - 3, top - 3, self.BODY_W + 6, max(0.0, self.EDGE - top + 3))

    def _mouse_over(self) -> bool:
        """Whether the mouse is on the part of the pet that shows (all of it once it's mostly out)."""
        pos = QPointF(self.mapFromGlobal(QCursor.pos()))
        return self._pet_rect(self.OUT if self.sink < self.PEEK / 2 else self.sink).contains(pos)

    def _at_edge(self) -> bool:
        """Whether the mouse is down at the bottom edge, near the pet."""
        pos = QCursor.pos()
        return pos.y() >= self.y() + self.EDGE - 3 and abs(pos.x() - (self.x() + self.SIZE // 2)) <= EDGE_REACH

    def _tick(self):
        now = time.monotonic()
        if self._mouse_over():
            self.left_at = now
            if self.asleep:  # someone's here
                self.asleep = False
                self.frame_timer.setInterval(33)
        if self.fullscreen and self._at_edge():
            if now - self.edge_at > EDGE_LINGER:
                self.raise_()  # in front of the full-screen app, which may have come up over us
            self.edge_at = now
        if self.drag_offset is not None or self.hop_start is not None or now - self.left_at < LEAVE_DELAY:
            target = self.OUT
        elif not self.fullscreen or now - self.edge_at < EDGE_LINGER:
            target = self.PEEK
        else:
            target = self.GONE
        self.sink += (target - self.sink) * (0.3 if target < self.sink else 0.38)
        if abs(target - self.sink) < 0.1:
            self.sink = target
        self.update()

    def hop(self):
        self.hop_start = time.monotonic()
        self.left_at = self.hop_start  # come out for it

    def go_to_claude(self):
        """Single click: to the Claude window used last; if it's already in front, the next one. None open: a new one."""
        windows = _claude_windows()
        if not windows:
            self.new_claude()
            return
        self.hop()
        target = windows[1] if len(windows) > 1 and windows[0] == _u32.GetForegroundWindow() else windows[0]
        _bring_to_front(target)

    def new_claude(self):
        """A new terminal running Claude Code in CLAUDE_DIR. It closes when Claude exits, so a window is a session."""
        if time.monotonic() - self.last_launch < 1:  # the click before a double click may have just opened one
            return
        self.last_launch = time.monotonic()
        self.hop()
        try:
            CLAUDE_DIR.mkdir(exist_ok=True)
            subprocess.Popen(["cmd.exe", "/c", "claude"], cwd=str(CLAUDE_DIR), env=_clean_env(),
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
            log.info("Opened Claude")
        except OSError:
            log.exception("Couldn't open Claude")

    # ------------------------------------------------------------ drawing

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = time.monotonic()
        dragged = self.drag_offset is not None
        hovered = (t - self.left_at < LEAVE_DELAY) and not dragged

        lift, squash = 0.0, 0.0
        if self.hop_start is not None:
            k = (t - self.hop_start) * 1000 / HOP_MS
            if k >= 1:
                self.hop_start = None
            else:
                lift = 11 * 4 * k * (1 - k)
                if k < 0.15 or k > 0.85:
                    squash = 0.12  # crouch before jumping and when landing
        breathe = math.sin((t - self.t0) * (1.3 if self.asleep else 2.0)) * (1.3 if self.asleep else 0.8)
        blinking = False
        if not self.asleep and t >= self.blink_at:
            blinking = True
            if t >= self.blink_at + 0.13:
                self.blink_at = t + random.uniform(2.5, 6) if random.random() > 0.2 else t + 0.25  # sometimes twice

        if not dragged:
            p.setClipRect(QRectF(0, 0, self.SIZE, self.EDGE))  # nothing past the screen's edge
        ground = self.EDGE + (0 if dragged else self.sink) - lift - (3 if dragged else 0)

        w = self.BODY_W * (1 + squash * 0.6)
        h = (self.BODY_H + breathe) * (1 - squash)
        cx = self.SIZE / 2
        body = QRectF(cx - w / 2, ground - self.FEET - h, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BODY_DARK)
        for fx in (cx - 12, cx + 5):  # feet
            p.drawRoundedRect(QRectF(fx, ground - self.FEET - 1 + (2 if dragged else 0), 7, self.FEET + 1), 2, 2)
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())  # body: lighter on top
        grad.setColorAt(0, BODY_LIGHT)
        grad.setColorAt(1, BODY)
        p.setBrush(grad)
        p.drawRoundedRect(body, 11, 11)

        self._eyes(p, body, hovered, blinking, dragged)
        if self.asleep and self.sink < self.GONE - 4:  # no z's floating over nothing
            self._zzz(p, body, t)

    def _eyes(self, p: QPainter, body: QRectF, hovered: bool, blinking: bool, dragged: bool):
        ey = body.top() + body.height() * 0.45
        gap = 8.5
        if self.asleep or (blinking and not hovered):  # closed: short lines
            p.setPen(QPen(EYE, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for dx in (-gap, gap):
                p.drawLine(QPointF(body.center().x() + dx - 2.5, ey + 1), QPointF(body.center().x() + dx + 2.5, ey + 1))
            return
        if hovered or self.hop_start is not None:  # happy: ^ ^
            p.setPen(QPen(EYE, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for dx in (-gap, gap):
                x = body.center().x() + dx
                path = QPainterPath(QPointF(x - 3, ey + 1.5))
                path.lineTo(x, ey - 1.5)
                path.lineTo(x + 3, ey + 1.5)
                p.drawPath(path)
            return
        # open, looking towards the mouse
        d = QCursor.pos() - self.mapToGlobal(QPoint(int(body.center().x()), int(ey)))
        dist = math.hypot(d.x(), d.y()) or 1
        reach = min(1.0, dist / 160)
        ox, oy = d.x() / dist * 2.6 * reach, d.y() / dist * 1.8 * reach
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(EYE)
        ew, eh = (5, 5) if dragged else (3.6, 7)  # surprised while being carried
        for dx in (-gap, gap):
            p.drawRoundedRect(QRectF(body.center().x() + dx + ox - ew / 2, ey + oy - eh / 2, ew, eh), 1.8, 1.8)

    def _zzz(self, p: QPainter, body: QRectF, t: float):
        font = QFont("Segoe UI")
        font.setBold(True)
        for i in range(2):
            k = ((t - self.t0) / 2.4 + i * 0.5) % 1.0  # 0 → 1 as each z floats away
            font.setPixelSize(int(8 + 4 * k))
            p.setFont(font)
            color = QColor(BODY_LIGHT)
            color.setAlpha(int(230 * (1 - k)))
            p.setPen(color)
            p.drawText(QPointF(body.right() - 2 + 7 * k, body.top() - 2 - 12 * k), "z")

    # ------------------------------------------------------------ mouse

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.press = e.globalPosition().toPoint()
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu.popup(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        pos = e.globalPosition().toPoint()
        if self.press is not None and (pos - self.press).manhattanLength() > 4:
            self.drag_offset, self.press = QPoint(self.SIZE // 2, self.SIZE // 2), None  # held by the middle
        if self.drag_offset is not None:
            self.move(pos - self.drag_offset)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.drag_offset is not None:
            self.drag_offset = None
            self._snap()
        elif self.press is not None:
            self.press = None
            self.go_to_claude()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.new_claude()

    # ------------------------------------------------------------ menu

    def _build_menu(self) -> QMenu:
        m = QMenu()
        self._labels = []

        def add(key: str, slot, checkable: bool = False):
            a = m.addAction(tr(key), slot)
            a.setCheckable(checkable)
            self._labels.append((a, key))
            return a

        add("go", self.go_to_claude)
        add("new", self.new_claude)
        m.addSeparator()
        self.act_autostart = add("autostart", self.toggle_autostart, checkable=True)
        langs = m.addMenu(tr("language"))
        group = QActionGroup(langs)
        self.act_langs = {}
        for code, name in LANGUAGES.items():
            a = langs.addAction(name)
            a.setCheckable(True)
            a.triggered.connect(partial(self.change_language, code))
            group.addAction(a)
            self.act_langs[code] = a
        add("quit", QApplication.quit)
        m.aboutToShow.connect(self._refresh_menu)
        return m

    def _refresh_menu(self):
        for a, key in self._labels:
            a.setText(tr(key))
        for code, a in self.act_langs.items():
            a.setChecked(code == _lang)
        self.act_autostart.setChecked(_autostart_enabled())

    def change_language(self, code: str, *_):
        global _lang
        _lang = self.s["language"] = code
        self.save()

    def toggle_autostart(self):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if _autostart_enabled():
                winreg.DeleteValue(k, RUN_NAME)
            else:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, _launch_command())


def make_icon() -> QIcon:
    """The pet's face, for the shortcut."""
    pm = QPixmap(256, 256)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BODY)
    p.drawRoundedRect(QRectF(20, 44, 216, 168), 60, 60)
    p.setBrush(EYE)
    for x in (88, 148):
        p.drawRoundedRect(QRectF(x, 100, 20, 40), 10, 10)
    p.end()
    return QIcon(pm)


def _send_to_running_instance(command: bytes) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(1000):
        return False
    sock.write(command)
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(DATA / "app.log", maxBytes=500_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.excepthook = lambda *exc: log.critical("Unhandled exception", exc_info=exc)

    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(DATA / "app.lock"))
    if not lock.tryLock(100):
        for _ in range(10):  # already running: make it hop (it may still be starting up)
            if _send_to_running_instance(b"hop"):
                break
            time.sleep(0.3)
        return
    pet = Pet()
    server = QLocalServer()
    QLocalServer.removeServer(SERVER_NAME)

    def on_connection():
        while sock := server.nextPendingConnection():
            sock.waitForReadyRead(500)
            if bytes(sock.readAll()).strip() == b"hop":
                pet.hop()
            sock.disconnectFromServer()
            sock.deleteLater()

    server.newConnection.connect(on_connection)
    server.listen(SERVER_NAME)
    pet.show()
    log.info("Started v%s", __version__)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
