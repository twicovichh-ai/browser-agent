"""The agent's browser tools as an MCP server (stdio).

Any MCP client (Claude Code, Claude Desktop, Cursor, our own agent) can drive the same
visible browser with the same snapshot/ids/context strategy:

    python mcp_server.py                         # launches its own Chrome with a persistent profile
    python mcp_server.py --cdp http://localhost:9222

Claude Code:
    claude mcp add browser-agent -- /path/to/.venv/bin/python /path/to/mcp_server.py

Playwright's sync API cannot run inside the server's asyncio loop, so the browser lives
in one dedicated thread and every tool call is executed there.
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from mcp.server.mcpserver import MCPServer

from agent.browser import ActionError, BrowserSession

server = MCPServer(
    name="browser-agent",
    instructions=(
        "Tools for a real, visible web browser. Every action returns a compact snapshot of the page: "
        "visible interactive elements as `[id] role \"name\" ⟨context⟩`. Refer to elements only by these ids. "
        "Use extract_text to read long content instead of scrolling. Irreversible actions (paying, deleting, "
        "sending) must be confirmed with the user first."
    ),
)

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser")
_browser: BrowserSession | None = None
_cdp: str | None = None


def _session() -> BrowserSession:
    global _browser
    if _browser is None:
        _browser = BrowserSession(cdp_url=_cdp, on_dialog=lambda kind, msg: False)  # no human here: dismiss
        _browser.start()
        if _cdp:
            _browser.page = _browser.context.pages[-1]
    return _browser


async def _run(action: Callable[[BrowserSession], object], observe: bool = True) -> str:
    def job() -> str:
        b = _session()
        try:
            out = action(b)
        except ActionError as e:
            return f"ERROR: {e}\n\nCurrent page:\n{b.snapshot()}"
        return b.snapshot() if observe else str(out)
    return await asyncio.get_running_loop().run_in_executor(_pool, job)


@server.tool()
async def navigate(url: str) -> str:
    """Open a URL in the current tab and return the page snapshot."""
    return await _run(lambda b: b.navigate(url))


@server.tool()
async def snapshot() -> str:
    """Return the current page snapshot: URL, open dialogs, visible interactive elements with ids."""
    return await _run(lambda b: None)


@server.tool()
async def click(ref: str) -> str:
    """Click an element by its id from the latest snapshot (e.g. '42' or 'f1:7')."""
    return await _run(lambda b: b.click(ref))


@server.tool()
async def type_text(ref: str, text: str, submit: bool = False) -> str:
    """Replace the content of an input with text; submit=true presses Enter afterwards."""
    return await _run(lambda b: b.type_text(ref, text, submit))


@server.tool()
async def select_option(ref: str, option: str) -> str:
    """Choose an option of a native <select> by its visible label."""
    return await _run(lambda b: b.select(ref, option))


@server.tool()
async def press_key(key: str) -> str:
    """Press a key in the page, e.g. 'Escape', 'Enter', 'Tab'."""
    return await _run(lambda b: b.press(key))


@server.tool()
async def scroll(direction: str = "down", ref: str | None = None) -> str:
    """Scroll one screen 'up'/'down', or scroll element `ref` into view."""
    return await _run(lambda b: b.scroll(direction, ref))


@server.tool()
async def go_back() -> str:
    """Browser Back button."""
    return await _run(lambda b: b.go_back())


@server.tool()
async def extract_text(max_chars: int = 20000) -> str:
    """Full visible text of the current page (all frames), for reading long content."""
    return await _run(lambda b: b.page_text(max_chars), observe=False)


def main() -> None:
    global _cdp
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", help="Attach to a running Chrome, e.g. http://localhost:9222")
    _cdp = ap.parse_args().cdp
    server.run("stdio")


if __name__ == "__main__":
    main()
