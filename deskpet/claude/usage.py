"""The plan's usage limits as Claude Code's /usage shows them: the 5-hour session, the week, and weekly limits of
their own for some models (Fable). `claude -p /usage --no-session-persistence` prints them without opening a
session or using any tokens. They're cached in a file that the pet's menu and status lines read."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# "Current session: 30% used · resets Oct 11, 3:19pm (America/New_York)"
# "Current week (all models): 57% used · resets Oct 15, 9:59pm (America/New_York)"
# "Current week (Fable): 3% used · resets Oct 15, 9:59pm (America/New_York)"
LIMIT = re.compile(r"^[ \t]*Current (?:session|week \((?P<scope>[^)]+)\)):\s*(?P<pct>\d+(?:\.\d+)?)% used"
                   r"(?:\s*\S\s*resets (?P<when>.+?))?\s*$", re.M)
WHEN = re.compile(r"^(?:(?P<month>[A-Z][a-z]{2}) (?P<day>\d{1,2}),?\s*)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
                  r"\s*(?P<ampm>[ap]m)\s*\((?P<tz>[^)]+)\)$", re.I)
MAX_AGE = 30 * 60  # seconds a cached reading is still shown


def _resets_at(text: str | None, now: datetime | None = None) -> float | None:
    """'Oct 15, 9:59pm (America/New_York)' (or without the date: today) as a Unix time; None if it can't tell."""
    found = WHEN.match((text or "").strip())
    if not found:
        return None
    try:
        zone = ZoneInfo(found["tz"])
    except (ZoneInfoNotFoundError, ValueError):
        return None
    now = now or datetime.now(zone)
    hour = int(found["hour"]) % 12 + (12 if found["ampm"].lower() == "pm" else 0)
    minute = int(found["minute"] or 0)
    if found["month"]:
        month = datetime.strptime(found["month"], "%b").month
        when = datetime(now.year, month, int(found["day"]), hour, minute, tzinfo=zone)
        if when < now - timedelta(days=180):  # (it's next year's)
            when = when.replace(year=now.year + 1)
    else:
        when = now.astimezone(zone).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return when.timestamp()


def parse(output: str) -> list[dict]:
    """The limits in /usage's output, in its order:
    [{"name": "5h" | "7d" | the model ("Fable"), "pct", "resets_at"}]."""
    limits = []
    for found in LIMIT.finditer(output):
        scope = found["scope"]
        name = "5h" if scope is None else "7d" if scope.lower() == "all models" else scope
        limits.append({"name": name, "pct": float(found["pct"]), "resets_at": _resets_at(found["when"])})
    return limits


def fetch(cwd: Path, env: dict | None = None, claude: str | None = None) -> list[dict]:
    """Ask Claude Code (a few seconds)."""
    exe = claude or shutil.which("claude") or "claude"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    out = subprocess.run([exe, "-p", "/usage", "--no-session-persistence"], capture_output=True, timeout=60,
                         stdin=subprocess.DEVNULL, cwd=str(cwd), env=env, creationflags=flags)
    return parse(out.stdout.decode("utf-8", "replace"))


def save(path: Path, limits: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": time.time(), "limits": limits}, indent=1), "utf-8")
    tmp.replace(path)


def load(path: Path) -> list[dict]:
    """The cached limits, if they're recent enough to show; else []."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("limits", []) if time.time() - data.get("updated", 0) < MAX_AGE else []


def age(path: Path) -> float:
    """Seconds since the cache was written (a lot if there's none)."""
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return 1e9


def time_left(resets_at) -> str:
    """Until a limit resets: 2d5h, 1h3min, 45min ('' if unknown or past)."""
    try:
        mins = int((float(resets_at) - time.time()) // 60)
    except (TypeError, ValueError):
        return ""
    if mins < 1:
        return ""
    days, hours = mins // 1440, mins % 1440 // 60
    return f"{days}d{hours}h" if days else f"{hours}h{mins % 60}min" if hours else f"{mins}min"


def describe(limits: list[dict]) -> str:
    """'5h 30% (3h2min) | 7d 57% (4d23h) | Fable 3%': the time left only where it differs from the line before."""
    parts, previous = [], None
    for limit in limits:
        left = time_left(limit.get("resets_at"))
        text = f"{limit['name']} {round(limit['pct'])}%"
        if left and limit.get("resets_at") != previous:
            text += f" ({left})"
        previous = limit.get("resets_at")
        parts.append(text)
    return " | ".join(parts)
