"""Windows: other windows through user32, the keyboard focus, staying above the taskbar, the global shortcut
(RegisterHotKey), starting at login (the Run key), terminals (Windows Terminal or the console), Illustrator (COM)."""
from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import threading
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QTimer, Signal

from .base import *  # noqa: F401,F403  (what isn't overridden here)
from .base import __all__  # noqa: F401

_u32 = ctypes.WinDLL("user32")
_k32 = ctypes.WinDLL("kernel32")
_dwm = ctypes.WinDLL("dwmapi")
H = wintypes.HWND
_u32.GetForegroundWindow.restype = H
_u32.GetClassNameW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowTextW.argtypes = [H, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.SetForegroundWindow.argtypes = [H]
_u32.BringWindowToTop.argtypes = [H]
_u32.ShowWindow.argtypes = [H, ctypes.c_int]
_u32.IsWindow.argtypes = [H]
_u32.IsIconic.argtypes = [H]
_u32.IsWindowVisible.argtypes = [H]
_u32.GetWindow.restype = H
_u32.GetWindow.argtypes = [H, wintypes.UINT]
_u32.GetWindowRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
_u32.SetWindowPos.argtypes = [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
_u32.MonitorFromWindow.restype = wintypes.HANDLE
_u32.MonitorFromWindow.argtypes = [H, wintypes.DWORD]
_u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_u32.GetAsyncKeyState.restype = ctypes.c_short
_k32.GetCurrentThreadId.restype = wintypes.DWORD
_k32.GetTickCount.restype = wintypes.DWORD
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD)]
_k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.AttachConsole.argtypes = [wintypes.DWORD]
_k32.GetConsoleWindow.restype = H

TERMINAL_CLASSES = ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass")  # Windows Terminal, the old console
# Windows the user can be in that aren't what they're working on: the taskbar, Start and search, Alt+Tab
NOT_WORK = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",
            "ForegroundStaging", "MultitaskingViewFrame", "TopLevelWindowForOverflowXamlIsland",
            "NotifyIconOverflowWindow")
HWND_TOPMOST = H(-1)
SWP_STAY = 0x1 | 0x2 | 0x10 | 0x200  # no size, no move, no activation, owner order unchanged
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "DeskPet"
AUTOSTART_LABEL = "autostart"
_console_lock = threading.Lock()  # a process can be attached to one console at a time


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_u32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]


def _class_name(hwnd: int) -> str:
    cls = ctypes.create_unicode_buffer(64)
    _u32.GetClassNameW(hwnd, cls, 64)
    return cls.value


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _program(pid: int) -> str:
    handle = _k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf, size = ctypes.create_unicode_buffer(512), wintypes.DWORD(512)
        return Path(buf.value).name if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        _k32.CloseHandle(handle)


def _rect(hwnd) -> wintypes.RECT:
    r = wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


# ---------------------------------------------------------------- other programs' windows

def foreground() -> int:
    return _u32.GetForegroundWindow() or 0


def is_work_window(handle: int) -> bool:
    return bool(handle) and _pid(handle) != os.getpid() and _class_name(handle) not in TERMINAL_CLASSES + NOT_WORK


def describe(handle: int) -> str:
    title = ctypes.create_unicode_buffer(256)
    _u32.GetWindowTextW(handle, title, 256)
    return f"{title.value or '(untitled)'} ({_program(_pid(handle)) or 'unknown program'})"


def window_exists(handle: int) -> bool:
    return bool(handle) and bool(_u32.IsWindow(handle))


def window_rect(handle: int) -> tuple[int, int, int, int] | None:
    """Its visible frame (without the invisible resize border), if it's open and not minimized."""
    if not window_exists(handle) or _u32.IsIconic(handle):
        return None
    r = wintypes.RECT()
    if _dwm.DwmGetWindowAttribute(H(handle), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:  # DWMWA_EXTENDED_FRAME_BOUNDS
        r = _rect(handle)
    return r.left, r.top, r.right, r.bottom


def bring_to_front(handle: int):
    if not _u32.IsWindowVisible(handle):
        _u32.ShowWindow(handle, 1)  # SW_SHOWNORMAL
    elif _u32.IsIconic(handle):
        _u32.ShowWindow(handle, 9)  # SW_RESTORE
    # allowed, since the user just clicked (the last input); if Windows still says no, tapping Alt first lifts the
    # foreground lock
    if not _u32.SetForegroundWindow(handle):
        _u32.keybd_event(0x12, 0, 0, 0)
        _u32.keybd_event(0x12, 0, 2, 0)
        _u32.SetForegroundWindow(handle)


# ---------------------------------------------------------------- the pet's own windows

def give_focus(window_id: int):
    """Windows won't hand the keyboard to a window when the pet's last input wasn't a click (a shortcut, a
    question popping up). Sharing input with the window that has it first does it without sending anything
    anywhere; only if that fails, a tap of Alt (which some apps notice: a menu or ribbon hint can flash)."""
    fg = _u32.GetForegroundWindow()
    if fg == window_id:
        return
    fg_thread = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = _k32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me and _u32.AttachThreadInput(me, fg_thread, True))
    try:
        _u32.SetForegroundWindow(window_id)
        _u32.BringWindowToTop(window_id)
    finally:
        if attached:
            _u32.AttachThreadInput(me, fg_thread, False)
    if _u32.GetForegroundWindow() != window_id:
        _u32.keybd_event(0x12, 0, 0, 0)
        _u32.keybd_event(0x12, 0, 2, 0)
        _u32.SetForegroundWindow(window_id)


def restore_focus(handle: int):
    if window_exists(handle):
        _u32.SetForegroundWindow(handle)


def _shown(hwnd) -> bool:
    """Visible, and not cloaked (hidden by the system, as Start and Store apps are when not in use)."""
    cloaked = wintypes.DWORD()
    _dwm.DwmGetWindowAttribute(H(hwnd), 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
    return bool(_u32.IsWindowVisible(hwnd)) and not cloaked.value


IME_CLASSES = ("candidate", "msctfime", ".ime.", "ime ui", "sopy", "qqpinyin", "baiduime")  # (in the class name)
IME_PROGRAMS = {"textinputhost.exe", "chsime.exe", "ctfmon.exe", "inputapp.exe"}  # (Windows' own input methods)


def _input_method(hwnd: int) -> bool:
    """A window of an input method (its candidate list, say)."""
    cls = _class_name(hwnd).lower()
    return cls == "ime" or any(k in cls for k in IME_CLASSES) or _program(_pid(hwnd)).lower() in IME_PROGRAMS


def _covered(hwnd: int, ours: set[int]) -> str:
    """What's over one of our windows: "" nothing; "screen" something over the whole screen it's on (a
    screenshot tool's overlay, a full-screen video); "ime" an input method's window (its candidate list:
    typing in the bubble, it has to stay over it); "drag" the picture of what's being dragged (a file, on its
    way to the bubble, say: it has to stay over it too); "window" something else (the taskbar, say)."""
    me = _rect(hwnd)
    info = _MonitorInfo(cbSize=ctypes.sizeof(_MonitorInfo))
    _u32.GetMonitorInfoW(_u32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))  # MONITOR_DEFAULTTONEAREST
    screen, found = info.rcMonitor, ""
    above = _u32.GetWindow(hwnd, 3)  # GW_HWNDPREV: up the window stack
    while above:
        if above not in ours and _shown(above):
            r = _rect(above)
            if r.left <= screen.left and r.top <= screen.top and r.right >= screen.right and r.bottom >= screen.bottom:
                return "screen"
            if r.left < me.right and r.right > me.left and r.top < me.bottom and r.bottom > me.top:
                if _input_method(above):
                    return "ime"
                if _class_name(above) == "SysDragImage":  # (the shell's, for a drag from Explorer, a browser…)
                    return "drag"
                found = "window"
        above = _u32.GetWindow(above, 3)
    return found


def keep_on_top(window_ids: list[int]):
    """Above everything, the taskbar included (it comes up over the pet when the mouse reaches the bottom of the
    screen): when something gets over our windows, they go back on top. But not over what covers the whole
    screen (a screenshot tool's overlay, a full-screen video: those are meant to be on top), nor while an input
    method's candidate list or the picture of something being dragged is over them."""
    ours = set(window_ids)
    covers = {_covered(w, ours) for w in window_ids}
    if "window" in covers and not covers & {"screen", "ime", "drag"}:
        for w in window_ids:
            _u32.SetWindowPos(w, HWND_TOPMOST, 0, 0, 0, 0, SWP_STAY)


# ---------------------------------------------------------------- the user

def mouse_button_down() -> bool | None:
    vk = 0x02 if _u32.GetSystemMetrics(23) else 0x01  # SM_SWAPBUTTON (left-handed): the right one is the main one
    return bool(_u32.GetAsyncKeyState(vk) & 0x8000)


def idle_seconds() -> float:
    info = _LastInput(cbSize=ctypes.sizeof(_LastInput))
    if not _u32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((_k32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def pid_alive(pid: int) -> bool:
    handle = _k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = wintypes.DWORD()
    ok = _k32.GetExitCodeProcess(handle, ctypes.byref(code))
    _k32.CloseHandle(handle)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


class Hotkey(QObject):
    """Ctrl+Alt+Space from anywhere (RegisterHotKey): `pressed`, then `released` once Space is let go (a hotkey
    only says when it's pressed: from then, Space is looked at until it's up)."""
    pressed = Signal()
    released = Signal()
    WM_HOTKEY, ID = 0x0312, 1

    class _Filter(QAbstractNativeEventFilter):
        def __init__(self, hotkey):
            super().__init__()
            self.hotkey = hotkey

        def nativeEventFilter(self, event_type, message):
            name = event_type.data() if hasattr(event_type, "data") else bytes(event_type)
            if name == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == Hotkey.WM_HOTKEY and msg.wParam == Hotkey.ID:
                    self.hotkey.pressed.emit()
                    self.hotkey._watch.start()
                    return True, 0
            return False, 0

    def __init__(self):
        super().__init__()
        self.ok = bool(_u32.RegisterHotKey(None, self.ID, 0x2 | 0x1 | 0x4000, 0x20))  # Ctrl, Alt, no repeat; Space
        self._filter = self._Filter(self)
        QCoreApplication.instance().installNativeEventFilter(self._filter)
        self._watch = QTimer(self, interval=30, timeout=self._space_up)

    def _space_up(self):
        if not _u32.GetAsyncKeyState(0x20) & 0x8000:
            self._watch.stop()
            self.released.emit()


# ---------------------------------------------------------------- starting at login

def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool, argv: list[str]):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(argv))
        elif autostart_enabled():
            winreg.DeleteValue(k, RUN_NAME)


# ---------------------------------------------------------------- terminals and apps

def open_terminal(argv: list[str], cwd: Path, env: dict) -> subprocess.Popen | None:
    """A console program in a new console: Windows opens it in the default terminal (Windows Terminal)."""
    return subprocess.Popen(argv, cwd=str(cwd), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)


def terminal_window(pid: int) -> int:
    """Windows Terminal's window (it owns the console's hidden pseudo-window), or the old console's own; 0: none.
    (This lets go of the pet's own console: see console_for_pty.)"""
    with _console_lock:
        _k32.FreeConsole()
        if not _k32.AttachConsole(pid):
            return 0
        try:
            hwnd = _k32.GetConsoleWindow()
        finally:
            _k32.FreeConsole()
    return (_u32.GetWindow(hwnd, 4) or hwnd) if hwnd else 0  # GW_OWNER (a hidden pseudo-window can be what's
    # found: shown, Windows Terminal brings its own to the front)


@contextlib.contextmanager
def console_for_pty():
    """While starting a program in a pseudo-console: the pet has a console of its own. ConPTY needs one, and the
    pet has none (pythonw, or the built app), so Windows made one, with a window, which flashed up each time the
    pet started Claude's terminal. Instead it borrows one with no window: a short-lived child's, made with
    CREATE_NO_WINDOW (it stays once attached; terminal_window lets go of it, so it may be needed again)."""
    with _console_lock:
        if not _k32.GetConsoleCP():  # (no console)
            child = subprocess.Popen(["cmd.exe", "/c", "ping -n 5 127.0.0.1 >nul"],
                                     creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(200):
                    if _k32.AttachConsole(child.pid):
                        _k32.SetConsoleCtrlHandler(None, True)  # (no Ctrl+C from it)
                        break
                    time.sleep(0.01)
            finally:
                child.kill()
        yield


def run_illustrator(script: str) -> str:
    import pythoncom
    import pywintypes
    import win32com.client
    pythoncom.CoInitialize()
    try:
        try:
            app = win32com.client.GetActiveObject("Illustrator.Application")
        except pywintypes.com_error:
            raise RuntimeError("Illustrator isn't open. Ask the user to open it.")
        try:
            return str(app.DoJavaScript(script)) or "(no value)"
        except pywintypes.com_error as e:
            raise RuntimeError(f"The script failed: {e.excepinfo[2] if e.excepinfo and e.excepinfo[2] else e}")
    finally:
        pythoncom.CoUninitialize()
