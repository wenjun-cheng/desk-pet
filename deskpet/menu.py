"""The pet's right-click menu: what the bubble's Claude runs on (model, effort, context, the plan's usage), and
what you can do (hide the bubble, open the conversation in a terminal, start a new one, start at login, language,
quit)."""
from __future__ import annotations

import json
from functools import partial
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QApplication, QMenu

from . import desktop, paths
from .bubble import ui_font
from .chat import Chat
from .claude import usage
from .strings import LANGUAGES, language, set_language, tr

STYLE = """
QMenu { background: #fffbf4; border: 1.5px solid #2b211c; border-radius: 8px; padding: 4px; }
QMenu::item { color: #2b211c; padding: 3px 10px 3px 22px; border-radius: 4px; }
QMenu::item:selected { background: #d97757; color: #fffbf4; }
QMenu::item:disabled { color: #8c7b6e; background: transparent; }
QMenu::separator { height: 1px; background: rgba(43, 33, 28, 45); margin: 3px 6px; }
QMenu::indicator { width: 12px; height: 12px; left: 5px; }
QMenu::right-arrow { width: 8px; height: 8px; right: 6px; }
"""


def _style(menu: QMenu):
    """The bubble's look: warm paper, ink outline, rounded corners (so no square frame or system shadow)."""
    menu.setStyleSheet(STYLE)
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)


def status_of(session: str | None) -> dict:
    """What the status line last got in a session of the bubble's (the newest of any, if None), or {}."""
    try:
        path = paths.STATUS / f"{session}.json" if session else \
            max(paths.STATUS.glob("*.json"), key=lambda f: f.stat().st_mtime)
        return json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError):
        return {}


def plan_usage() -> str:
    """'5h 30% (3h2min) | 7d 57% (4d23h) | Fable 3%': from Claude Code's /usage (cached), else from the latest
    status line (without the weekly limits of single models), or ''."""
    cached = usage.load(paths.USAGE)
    if cached:
        return usage.describe(cached)
    limits = status_of(None).get("rate_limits") or {}
    found = [{"name": label, "pct": float(limits[key]["used_percentage"]), "resets_at": limits[key].get("resets_at")}
             for key, label in (("five_hour", "5h"), ("seven_day", "7d"))
             if (limits.get(key) or {}).get("used_percentage") is not None]
    return usage.describe(found)


def effort(workspace) -> str:
    """The reasoning effort Claude Code uses there (the pet doesn't set its own)."""
    for path in (workspace / ".claude" / "settings.json", paths.CLAUDE_HOME / "settings.json"):
        try:
            level = json.loads(path.read_text("utf-8")).get("effortLevel")
        except (OSError, ValueError):
            continue
        if level:
            return {"medium": "med"}.get(level, level)
    return ""


class PetMenu(QMenu):
    def __init__(self, chat: Chat, refresh_usage: Callable[[], None]):
        super().__init__()
        self.chat, self.refresh_usage = chat, refresh_usage
        self.setFont(ui_font(12.5))  # the bubble's terminal font (the language submenu inherits it)
        _style(self)
        self._labels = []
        # what the status line shows for the bubble's conversation: model | effort | context, then the plan's usage
        self.act_model = self.addAction("")
        self.act_usage = self.addAction("")
        for a in (self.act_model, self.act_usage):
            a.setEnabled(False)
        self.sep_status = self.addSeparator()
        self.act_hide = self._add("hide", chat.put_away)  # only while the bubble shows
        self._add("open_cli", chat.open_conversation)
        self._add("new_topic", chat.new_topic)
        self.addSeparator()
        self.act_autostart = self._add("autostart", self.toggle_autostart, checkable=True)
        langs = self.addMenu(tr("language"))
        _style(langs)
        self._labels.append((langs.menuAction(), "language"))
        group = QActionGroup(langs)
        self.act_langs = {}
        for code, name in LANGUAGES.items():
            a = langs.addAction(name)
            a.setCheckable(True)
            a.triggered.connect(partial(self.change_language, code))
            group.addAction(a)
            self.act_langs[code] = a
        self._add("quit", QApplication.quit)
        self.aboutToShow.connect(self._about_to_show)

    def _add(self, key: str, slot, checkable: bool = False):
        a = self.addAction(tr(key), slot)
        a.setCheckable(checkable)
        self._labels.append((a, key))
        return a

    def refresh(self):
        for a, key in self._labels:
            a.setText(tr(key))
        for code, a in self.act_langs.items():
            a.setChecked(code == language())
        self.act_autostart.setChecked(desktop.autostart_enabled())
        self.act_hide.setVisible(self.chat.bubble.isVisible())
        model = " | ".join(x for x in self._model_status() if x)
        plan = plan_usage()
        self.act_model.setText(model)
        self.act_usage.setText(plan)
        self.act_model.setVisible(bool(model))
        self.act_usage.setVisible(bool(plan))
        self.sep_status.setVisible(bool(model or plan))

    def _about_to_show(self):
        if usage.age(paths.USAGE) > 60:  # (shown as it is now; the menu shows the new one next time)
            self.refresh_usage()
        self.refresh()

    def warm_up(self):
        """The menu takes half a second the first time it shows (styles and fonts): done at startup instead."""
        self.refresh()
        self.ensurePolished()
        self.adjustSize()

    def _model_status(self) -> tuple[str, str, str]:
        """(model, effort, context) of the bubble's conversation, as its status line last saw it."""
        session = self.chat.session_id()
        status = status_of(session) if session else {}
        latest = status or status_of(None)  # before the bubble's first message: the model Claude uses lately
        model = (latest.get("model") or {}).get("display_name", "")
        level = (latest.get("effort") or {}).get("level") or effort(self.chat.workspace)
        used = (status.get("context_window") or {}).get("used_percentage")
        return model, level, f"context {round(float(used))}%" if used is not None else ""

    def change_language(self, code: str, *_):
        set_language(code)
        self.chat.settings.set("language", code)
        self.chat.language_changed()

    def toggle_autostart(self):
        desktop.set_autostart(not desktop.autostart_enabled(), paths.launch_argv())
