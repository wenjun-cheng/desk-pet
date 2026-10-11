"""What doesn't need a screen: reading answers, tool labels, /usage, transcripts, command lines."""
import sys
import time

import pytest

from deskpet import paths
from deskpet.chat import is_yes
from deskpet.claude import transcript, usage
from deskpet.strings import set_language, tool_label, tr


@pytest.mark.parametrize("answer", ["y", "yes", "OK", "sure, go", "可以", "好的", "行", "嗯嗯", "没问题", "删吧", "允许"])
def test_yes(answer):
    assert is_yes(answer)


@pytest.mark.parametrize("answer", ["no", "不行", "不要删，改名成 old.bak", "可以，但是先备份", "好，不过换成 b.txt",
                                    "ok but don't delete it", "没有", "wait", "stop", ""])
def test_not_yes(answer):
    assert not is_yes(answer)


def test_tool_labels():
    set_language("en")
    assert tool_label("Read", "C:/x/notes.md") == "Reading notes.md"
    assert tool_label("Bash", "ls") == tr("t_bash")
    assert tool_label("mcp__pet__screenshot", "") == tr("t_shot")
    assert tool_label("TodoWrite", "") is None
    set_language("zh")
    assert tool_label("Edit", "/a/b.py") == "在改 b.py"
    set_language("en")


USAGE = """\
 Settings:  Status   Config   Usage   Stats

 Current session: 32% used · resets 3:19pm (America/New_York)
 Current week (all models): 57% used · resets Oct 15, 9:59pm (America/New_York)
 Current week (Fable): 3% used · resets Oct 15, 9:59pm (America/New_York)
"""


def test_usage_parse():
    limits = usage.parse(USAGE)
    assert [(l["name"], l["pct"]) for l in limits] == [("5h", 32.0), ("7d", 57.0), ("Fable", 3.0)]
    assert all(isinstance(l["resets_at"], float) for l in limits)
    assert limits[1]["resets_at"] == limits[2]["resets_at"]


def test_usage_describe():
    soon, later = time.time() + 3 * 3600 + 17 * 60 + 30, time.time() + 4 * 86400 + 9 * 3600 + 30
    text = usage.describe([{"name": "5h", "pct": 32, "resets_at": soon},
                           {"name": "7d", "pct": 57, "resets_at": later},
                           {"name": "Fable", "pct": 3, "resets_at": later}])
    assert text == "5h 32% (3h17min) | 7d 57% (4d9h) | Fable 3%"


def test_usage_cache(tmp_path):
    path = tmp_path / "usage.json"
    limits = usage.parse(USAGE)
    usage.save(path, limits)
    assert usage.load(path) == limits
    assert usage.age(path) < 5


def test_command_output():
    entry = {"type": "system", "content": "<local-command-stdout>\x1b[1mContext\x1b[0m ⛁⛁ 12k/200k\n\n`ok`"
                                          "</local-command-stdout>"}
    assert transcript.command_output(entry) == "Context  12k/200k\nok"
    assert transcript.command_output({"type": "user", "message": {"content": "<command-name>/clear</command-name>"}}) \
        == ""
    assert transcript.command_output({"type": "user", "message": {"content": "hello"}}) is None


def test_waiting_states():
    assert not transcript.waiting("busy") and not transcript.waiting("idle") and not transcript.waiting("")
    assert transcript.waiting("waiting_for_permission")


def test_shell_command():
    argv = [sys.executable, "run.py", "hook"]
    line = paths.shell_command(argv)
    if sys.platform == "win32":
        assert "\\" not in line and ("& " in line) == (" " in paths._short(sys.executable))
    else:
        assert line.endswith("run.py hook")


def test_helper_argv():
    argv = paths.helper_argv("relay", "abc")
    assert argv[-2:] == ["relay", "abc"]
    if not paths.FROZEN:
        assert argv[1].endswith("run.py")
