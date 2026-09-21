"""Live view of the browser for the web UI.

Runs in its own thread with its OWN Playwright connection to the same Chrome over CDP,
so the picture keeps updating while the agent thread is blocked on a model call.
Streams CDP screencast frames out and forwards the viewer's mouse/keyboard in,
so a human can log in or solve a captcha right in the web page.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from playwright.sync_api import Error as PWError, sync_playwright

SPECIAL_KEYS = {
    "Enter": (13, "\r"), "Backspace": (8, ""), "Tab": (9, ""), "Escape": (27, ""), "Delete": (46, ""),
    "ArrowLeft": (37, ""), "ArrowUp": (38, ""), "ArrowRight": (39, ""), "ArrowDown": (40, ""),
    "Home": (36, ""), "End": (35, ""), "PageUp": (33, ""), "PageDown": (34, ""),
}


class Viewer(threading.Thread):
    def __init__(self, cdp_url: str, on_frame: Callable[[dict], None], active_url: Callable[[], str | None]):
        super().__init__(daemon=True, name="viewer")
        self.cdp_url = cdp_url
        self.on_frame = on_frame
        self.active_url = active_url  # URL of the page the agent works on
        self.inputs: queue.Queue[dict] = queue.Queue()
        self._page = None
        self._cdp = None
        self._meta: dict = {}

    # ---------- thread ----------

    def run(self) -> None:
        while True:  # reconnect forever: Chrome may restart
            try:
                with sync_playwright() as p:
                    browser = p.chromium.connect_over_cdp(self.cdp_url)
                    self._loop(browser)
            except Exception as e:  # noqa: BLE001
                self.on_frame({"type": "viewer_error", "text": str(e).splitlines()[0][:200]})
                self._page = self._cdp = None
                time.sleep(2)

    def _loop(self, browser) -> None:
        while browser.is_connected():
            self._follow_active_page(browser)
            self._drain_inputs()
            if self._page:
                self._page.wait_for_timeout(60)  # pumps CDP events -> frames arrive
            else:
                time.sleep(0.2)

    def _follow_active_page(self, browser) -> None:
        pages = [pg for ctx in browser.contexts for pg in ctx.pages if not pg.is_closed()]
        if not pages:
            self._page = None
            return
        want = self.active_url()
        target = next((pg for pg in pages if want and pg.url == want), pages[-1])
        if target is self._page:
            return
        if self._cdp:
            try:
                self._cdp.send("Page.stopScreencast")
                self._cdp.detach()
            except PWError:
                pass
        self._page = target
        self._cdp = target.context.new_cdp_session(target)
        self._cdp.on("Page.screencastFrame", self._frame)
        self._cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60,
                                                "maxWidth": 1400, "maxHeight": 1000})

    def _frame(self, ev: dict) -> None:
        self._meta = ev["metadata"]
        try:
            self._cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except PWError:
            pass
        self.on_frame({"type": "frame", "data": ev["data"], "url": self._page.url if self._page else ""})

    # ---------- input from the web page ----------

    def _drain_inputs(self) -> None:
        while self._cdp:
            try:
                ev = self.inputs.get_nowait()
            except queue.Empty:
                return
            try:
                self._apply(ev)
            except PWError:
                pass

    def _apply(self, ev: dict) -> None:
        w, h = self._meta.get("deviceWidth", 0), self._meta.get("deviceHeight", 0)
        send = self._cdp.send
        if ev["kind"] in ("click", "wheel") and w and h:
            x, y = ev["x"] * w, ev["y"] * h
            if ev["kind"] == "click":
                for t in ("mousePressed", "mouseReleased"):
                    send("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
            else:
                send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y,
                                                  "deltaX": 0, "deltaY": ev.get("dy", 0)})
        elif ev["kind"] == "text":
            send("Input.insertText", {"text": ev["text"][:500]})
        elif ev["kind"] == "key" and ev["key"] in SPECIAL_KEYS:
            code, text = SPECIAL_KEYS[ev["key"]]
            base = {"key": ev["key"], "code": ev["key"], "windowsVirtualKeyCode": code}
            send("Input.dispatchKeyEvent", {"type": "keyDown", **base, **({"text": text} if text else {})})
            send("Input.dispatchKeyEvent", {"type": "keyUp", **base})
