"""What the pet says, in English (the default) and Chinese."""
from __future__ import annotations

from pathlib import Path

LANGUAGES = {"en": "English", "zh": "中文"}
_STRINGS = {  # key: (English, Chinese)
    "ask": ("Ask Claude…", "问 Claude…"),
    "answer": ("Yes / no, or what to do instead", "可以 / 不行，或者说说你想怎样"),
    "follow_up": ("Say more…", "接着说…"),
    "thinking": ("Thinking", "想一想"),
    "t_read": ("Reading {x}", "在读 {x}"),
    "t_edit": ("Editing {x}", "在改 {x}"),
    "t_bash": ("Running a command", "在跑命令"),
    "t_search": ("Searching", "在找"),
    "t_web": ("Searching the web", "在网上查"),
    "t_fetch": ("Reading a web page", "在看网页"),
    "t_shot": ("Looking at your screen", "在看你的屏幕"),
    "t_ai": ("Working in Illustrator", "在 Illustrator 里操作"),
    "t_other": ("Working on it", "在处理"),
    "in_box": ("Answer below", "在下面回答"),
    "permission": ("OK to use {tool}? {what}", "可以用 {tool} 吗？{what}"),
    "in_terminal": ("Claude is waiting for you in the terminal", "Claude 在终端里等你确认"),
    "click_me": ("Double-click the pet to go there", "双击桌宠过去"),
    "trust": ("First time: in the terminal that opened, Claude Code asks whether to trust its folder. Say yes there",
              "第一次使用：Claude Code 在刚打开的终端里问是否信任它的文件夹，在那里选“是”"),
    "no_claude": ("Claude Code isn't installed (or not on PATH): see claude.com/claude-code",
                  "没有找到 Claude Code（或不在 PATH 里）：见 claude.com/claude-code"),
    "failed": ("Something went wrong", "出了点问题"),
    "no_text": ("Done. No answer in words; the terminal has the details", "做完了，没有文字回复，详情在终端里"),
    "e_closed": ("Claude's session ended", "Claude 的会话结束了"),
    "e_start": ("Claude Code didn't start", "Claude Code 没能启动"),
    "e_deliver": ("The message didn't reach Claude", "消息没送到 Claude"),
    "e_type": ("Couldn't type into Claude's terminal", "没法往 Claude 的终端里输入"),
    "hide": ("Hide", "收起"),
    "open_cli": ("Open terminal", "打开终端"),
    "new_topic": ("New conversation", "开新话题"),
    "autostart": ("Start at login", "开机自动启动"),
    "language": ("Language", "语言"),
    "quit": ("Quit", "退出"),
}
_language = "en"


def set_language(code: str):
    global _language
    _language = code if code in LANGUAGES else "en"


def language() -> str:
    return _language


def tr(key: str, **kwargs) -> str:
    en, zh = _STRINGS[key]
    text = zh if _language == "zh" else en
    return text.format(**kwargs) if kwargs else text


def known(key: str) -> bool:
    return key in _STRINGS


def tool_label(name: str, detail: str) -> str | None:
    """What the bubble says while Claude uses a tool; None for tools not worth showing."""
    short = Path(detail).name if detail else ""
    if name in ("ToolSearch", "TodoWrite"):
        return None
    if name == "Read":
        return tr("t_read", x=short)
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return tr("t_edit", x=short)
    return tr({"Bash": "t_bash", "PowerShell": "t_bash", "Glob": "t_search", "Grep": "t_search",
               "WebSearch": "t_web", "WebFetch": "t_fetch", "mcp__pet__screenshot": "t_shot",
               "mcp__pet__illustrator_script": "t_ai"}.get(name, "t_other"))
