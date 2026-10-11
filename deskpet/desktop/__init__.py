"""What differs between Windows, macOS and Linux: other programs' windows, the keyboard focus, staying on top,
the mouse button, idle time, the global shortcut, starting at login, opening a terminal, screenshots and
Illustrator. Each platform module has the same functions (see base.py); where one can't do something, it does
nothing, or answers "don't know"."""
import sys

if sys.platform == "win32":
    from .windows import *  # noqa: F401,F403
elif sys.platform == "darwin":
    from .macos import *  # noqa: F401,F403
else:
    from .linux import *  # noqa: F401,F403
