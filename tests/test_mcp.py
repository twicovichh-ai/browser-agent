"""Talk to mcp_server.py over real MCP stdio — no LLM, no money spent.

    python -m tests.test_mcp [--cdp http://localhost:9222] [--url http://127.0.0.1:8080/demo/shop.html]

Without --cdp the server launches its own Chrome; the demo page is then opened from disk.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


def text(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def find_id(snap: str, *needles: str) -> str:
    for line in snap.splitlines():
        if line.startswith("[") and all(n in line for n in needles):
            return line[1:line.index("]")]
    raise AssertionError(f"{needles} not in snapshot:\n{snap}")


async def main(cdp: str | None, url: str) -> None:
    args = [str(ROOT / "mcp_server.py")] + (["--cdp", cdp] if cdp else [])
    params = StdioServerParameters(command=sys.executable, args=args, cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print("tools:", tools)
            assert {"navigate", "snapshot", "click", "type_text", "extract_text"} <= set(tools)

            snap = text(await s.call_tool("navigate", {"url": url}))
            assert "Бургерная" in snap, snap[:500]
            if "OPEN DIALOG" in snap:
                snap = text(await s.call_tool("click", {"ref": find_id(snap, '"Понятно"')}))
            snap = text(await s.call_tool("click", {"ref": find_id(snap, "BBQ-бургер Говядина", "В корзину")}))
            assert '"🛒 1"' in snap or "🛒 1" in snap, snap[:800]
            err = text(await s.call_tool("click", {"ref": "999999"}))
            assert err.startswith("ERROR"), err[:200]
            print("snapshot excerpt:\n" + "\n".join(snap.splitlines()[:8]))
            print("MCP CHECK PASSED")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp")
    ap.add_argument("--url", default=(ROOT / "demo" / "shop.html").as_uri())
    a = ap.parse_args()
    asyncio.run(main(a.cdp, a.url))
