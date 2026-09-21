"""Browser layer: Playwright in a visible window with a persistent session.

Two modes:
- attach to the user's running Chrome over CDP (best: real profile, real logins);
- launch our own persistent profile in ./.profile (log in once by hand, it's remembered).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from playwright.sync_api import Browser, BrowserContext, Error as PWError, Frame, Page, Playwright, sync_playwright

SNAPSHOT_JS = (Path(__file__).parent / "snapshot.js").read_text(encoding="utf-8")


class ActionError(Exception):
    """An action failed in a way the model should see and adapt to."""


@dataclass
class BrowserSession:
    cdp_url: str | None = None
    profile_dir: str = ".profile"
    max_elements: int = 150
    max_text: int = 1500
    headless: bool = False  # only for tests; the task requires a visible browser
    on_dialog: Callable[[str, str], bool] | None = None  # (kind, message) -> bool accept

    _pw: Playwright | None = field(default=None, init=False)
    _browser: Browser | None = field(default=None, init=False)
    context: BrowserContext | None = field(default=None, init=False)
    page: Page | None = field(default=None, init=False)
    events: list[str] = field(default_factory=list, init=False)

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._pw = sync_playwright().start()
        if self.cdp_url:
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
            self.context = self._browser.contexts[0]
        else:
            def launch(channel: str | None) -> BrowserContext:
                return self._pw.chromium.launch_persistent_context(
                    self.profile_dir, channel=channel, headless=self.headless, no_viewport=True,
                    args=["--start-maximized"])
            try:
                self.context = launch("chrome")  # installed Google Chrome looks less like a bot
            except PWError:
                self.context = launch(None)  # bundled Chromium
        self.context.on("page", self._on_new_page)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        for p in self.context.pages:
            p.on("dialog", self._handle_dialog)

    def close(self) -> None:
        try:
            if self._browser:  # CDP: detach only, never close the user's Chrome
                self._browser.close()
            elif self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _on_new_page(self, page: Page) -> None:
        page.on("dialog", self._handle_dialog)
        self.page = page
        self.events.append(f"New tab opened and focused: {page.url}")

    def _handle_dialog(self, dialog) -> None:
        # alert() is harmless; confirm()/beforeunload may be destructive -> ask the human.
        accept = True
        if dialog.type != "alert" and self.on_dialog:
            accept = self.on_dialog(dialog.type, dialog.message)
        self.events.append(f"Browser {dialog.type} dialog: '{dialog.message}' -> {'accepted' if accept else 'dismissed'}")
        dialog.accept() if accept else dialog.dismiss()

    # ---------- helpers ----------

    def _settle(self, timeout: float = 3.0) -> None:
        """Wait until the page stops changing (SPA-friendly, no fixed sleeps)."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
        except PWError:
            pass
        end, last = time.time() + timeout, -1
        while time.time() < end:
            try:
                size = self.page.evaluate("document.body ? document.body.innerHTML.length : 0")
            except PWError:
                size = -2  # navigation in progress
            if size == last:
                return
            last = size
            time.sleep(0.3)

    def _frames(self) -> list[tuple[str, Frame]]:
        """Main frame + visible child frames. Frame ids prefix element ids: 'f1:23'."""
        out = [("", self.page.main_frame)]
        for i, fr in enumerate(self.page.frames[1:], start=1):
            try:
                el = fr.frame_element()
                if el.is_visible() and (el.bounding_box() or {}).get("height", 0) > 30:
                    out.append((f"f{i}:", fr))
            except PWError:
                continue
        return out

    def _locate(self, ref: str):
        prefix, _, num = ref.rpartition(":")
        frames = dict(self._frames())
        frame = frames.get(prefix + ":" if prefix else "")
        if frame is None or not num.isdigit():
            raise ActionError(f"Unknown element id '{ref}'. Take a fresh snapshot.")
        loc = frame.locator(f'[data-agent-id="{num}"]')
        if loc.count() == 0:
            raise ActionError(f"Element [{ref}] no longer exists (page changed). Take a fresh snapshot.")
        return loc.first

    def describe(self, ref: str) -> str:
        """Human-readable description of an element, for the safety guard."""
        loc = self._locate(ref)
        return loc.evaluate(
            """el => {
                const t = s => (s||'').replace(/\\s+/g,' ').trim();
                let p = el.parentElement, ctx = '';
                for (let i=0;i<5&&p;i++,p=p.parentElement){ const x=t(p.innerText); if (x.length>40){ctx=x.slice(0,200);break;} }
                return `${el.tagName.toLowerCase()} "${t(el.innerText||el.value||el.getAttribute('aria-label')).slice(0,80)}" type=${el.type||''} | context: ${ctx}`;
            }"""
        )

    # ---------- observation ----------

    def snapshot(self) -> str:
        self._settle()
        parts = [f"URL: {self.page.url}", f"Title: {self.page.title()}"]
        if len(self.context.pages) > 1:
            tabs = ", ".join(f"{i}:{p.title()[:30]}" + (" (active)" if p == self.page else "")
                             for i, p in enumerate(self.context.pages))
            parts.append(f"Tabs: {tabs}")
        if self.events:
            parts.append("Events since last step: " + " | ".join(self.events))
            self.events.clear()

        for prefix, frame in self._frames():
            try:
                snap = frame.evaluate(SNAPSHOT_JS, {"prefix": prefix, "maxElements": self.max_elements,
                                                    "maxText": self.max_text if not prefix else 300})
            except PWError as e:
                parts.append(f"[frame {prefix or 'main'} unreadable: {str(e)[:80]}]")
                continue
            label = f"--- frame {prefix[:-1]} ---" if prefix else ""
            if label:
                parts.append(label)
            if snap["dialogs"]:
                parts.append("OPEN DIALOG/POPUP: " + " || ".join(snap["dialogs"]))
            if not prefix:
                s = snap["scroll"]
                parts.append(f"Scroll: {s['y']}/{s['max']}px; interactive elements off-screen: "
                             f"{snap['offscreen']['above']} above, {snap['offscreen']['below']} below")
                if snap["headings"]:
                    parts.append("Headings: " + " / ".join(snap["headings"]))
            parts.append("Interactive elements (viewport):")
            parts.extend(snap["elements"] or ["(none)"])
            if not prefix:
                parts.append(f"Page text (truncated, use extract() for details): {snap['text']}")
        return "\n".join(parts)

    def page_text(self, limit: int = 60_000) -> str:
        self._settle()
        texts = []
        for prefix, frame in self._frames():
            try:
                texts.append(frame.evaluate("document.body ? document.body.innerText : ''"))
            except PWError:
                pass
        return "\n".join(texts)[:limit]

    def screenshot(self) -> bytes:
        return self.page.screenshot(type="jpeg", quality=60)

    # ---------- actions ----------

    def navigate(self, url: str) -> None:
        if "://" not in url:
            url = "https://" + url
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except PWError as e:
            raise ActionError(f"Navigation failed: {str(e).splitlines()[0]}")

    def click(self, ref: str) -> None:
        loc = self._locate(ref)
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=5000)
        except PWError as e:
            msg = str(e).splitlines()[0]
            if "intercepts pointer events" in str(e):
                raise ActionError("Click blocked by an overlay (popup/cookie banner?). Close it first.")
            raise ActionError(f"Click failed: {msg}")

    def type_text(self, ref: str, text: str, submit: bool) -> None:
        loc = self._locate(ref)
        try:
            loc.click(timeout=5000)
            loc.fill(text, timeout=5000)
        except PWError:
            # contenteditable / custom inputs: type like a human
            self.page.keyboard.press("ControlOrMeta+a")
            self.page.keyboard.type(text, delay=20)
        if submit:
            self.page.keyboard.press("Enter")

    def select(self, ref: str, option: str) -> None:
        try:
            self._locate(ref).select_option(label=option, timeout=5000)
        except PWError as e:
            raise ActionError(f"Select failed: {str(e).splitlines()[0]}")

    def press(self, key: str) -> None:
        self.page.keyboard.press(key)

    def scroll(self, direction: str, ref: str | None = None) -> None:
        if ref:
            self._locate(ref).scroll_into_view_if_needed(timeout=3000)
            return
        dy = {"down": 0.8, "up": -0.8}[direction]
        self.page.evaluate(f"window.scrollBy(0, innerHeight * {dy})")

    def go_back(self) -> None:
        self.page.go_back(wait_until="domcontentloaded")

    def switch_tab(self, index: int) -> None:
        pages = self.context.pages
        if not 0 <= index < len(pages):
            raise ActionError(f"No tab {index}; there are {len(pages)} tabs.")
        self.page = pages[index]
        self.page.bring_to_front()
