"""`python -m deskpet` (or run.py, or the built app): the pet. The helpers Claude Code runs for it, each in a
process of its own: `hook`, `mcp`, `statusline`, `relay <session>`; and `usage`, which prints the plan's usage."""
import sys


def main():
    mode, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if mode == "hook":
        from .claude import hook
        hook.main()
    elif mode == "mcp":
        from .claude import tools
        tools.main()
    elif mode == "statusline":
        from .claude import statusline
        statusline.main()
    elif mode == "relay" and args:
        from .claude import relay
        relay.main(args[0])
    elif mode == "usage":
        from . import paths
        from .claude import usage
        from pathlib import Path
        limits = usage.fetch(paths.DEFAULT_WORKSPACE if paths.DEFAULT_WORKSPACE.is_dir() else Path.home(),
                             paths.clean_env(), paths.claude_exe())
        if limits:
            usage.save(paths.USAGE, limits)
        print(usage.describe(limits) or "No usage found (is Claude Code signed in?)")
    else:
        from . import app
        app.main()


if __name__ == "__main__":
    main()
