"""The pet's tools for Claude (an MCP server on stdio, see launch.py): see the window the user means by "this"
(the one they were in when they wrote from the bubble, saved in target.json), and work in Adobe Illustrator."""
import io
import json
import sys

from .. import desktop, paths

MAX_SHOT = 1568  # longest side of a screenshot sent to Claude


def _target() -> int:
    try:
        handle = int(json.loads(paths.TARGET.read_text("utf-8")).get("window") or 0)
    except (OSError, ValueError):
        return 0
    return handle if desktop.window_exists(handle) else 0


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels, the same ones the screen grab uses
    from mcp.server.mcpserver import Image, MCPServer  # (no `from __future__ import annotations` in this file:
    # the tools' annotations are read at run time, and Image is only imported here)
    server = MCPServer("pet", instructions="Desk Pet: see the user's screen, and work in Adobe Illustrator.")

    @server.tool()
    def screenshot(what: str = "window") -> Image:
        """See the user's screen. what="window" (default): the window they were working in when they wrote from
        the pet's speech bubble; what="screen": everything."""
        img = desktop.screenshot(_target() if what != "screen" else 0)
        img.thumbnail((MAX_SHOT, MAX_SHOT))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        return Image(data=buf.getvalue(), format="jpeg")

    @server.tool()
    def illustrator_script(script: str) -> str:
        """Run ExtendScript (JavaScript) in the open Adobe Illustrator and return the value of its last expression.
        Use it to read the document, layers and selection, and to change them."""
        return desktop.run_illustrator(script)

    server.run("stdio")
