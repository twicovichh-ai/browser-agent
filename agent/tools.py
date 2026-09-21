"""Tool definitions the model sees, and their execution.

Design rules (see README):
- Tools are generic browser primitives. Nothing here knows about any site.
- Elements are addressed by ids from the latest snapshot, never by CSS selectors.
- Every action returns a fresh snapshot, so the model always acts on the current page
  and we save a round-trip per step.
- Errors come back as is_error tool results; the model reads them and adapts.
"""

from __future__ import annotations

import base64
from typing import Callable

from . import llm
from .browser import ActionError, BrowserSession

REF = {"type": "string", "description": "Element id from the latest snapshot, e.g. '42' or 'f1:7'"}

TOOLS: list[dict] = [
    {
        "name": "navigate",
        "description": "Open a URL in the current tab. Use when you know or can guess the site; "
                       "prefer clicking links once you are on the site.",
        "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "snapshot",
        "description": "Re-read the current page: URL, open dialogs, visible interactive elements with ids, "
                       "short page text. Actions already return a snapshot; call this only after waiting "
                       "or when unsure the page is current.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "Click an element by id. Irreversible clicks (pay, delete, send) are checked by a "
                       "safety layer and may require user confirmation.",
        "input_schema": {"type": "object", "properties": {"ref": REF}, "required": ["ref"]},
    },
    {
        "name": "type",
        "description": "Replace the content of an input/textarea/contenteditable with text. "
                       "submit=true presses Enter afterwards (search boxes, forms).",
        "input_schema": {
            "type": "object",
            "properties": {"ref": REF, "text": {"type": "string"}, "submit": {"type": "boolean", "default": False}},
            "required": ["ref", "text"],
        },
    },
    {
        "name": "select_option",
        "description": "Choose an option in a native <select> by its visible label.",
        "input_schema": {"type": "object", "properties": {"ref": REF, "option": {"type": "string"}},
                         "required": ["ref", "option"]},
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key or chord in the page, e.g. 'Escape' to close a popup, 'Enter', 'Tab'.",
        "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    },
    {
        "name": "scroll",
        "description": "Scroll the page up/down by ~one screen to reveal more elements, "
                       "or scroll a specific element into view with ref.",
        "input_schema": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down"]},
                                                          "ref": REF}, "required": ["direction"]},
    },
    {
        "name": "go_back",
        "description": "Browser Back button.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "switch_tab",
        "description": "Make another open tab active (index from the 'Tabs' line of the snapshot).",
        "input_schema": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]},
    },
    {
        "name": "extract",
        "description": "Ask a reading sub-agent a question about the FULL text of the current page "
                       "(including parts not in the snapshot). Use to read emails, vacancy descriptions, "
                       "menus, profiles, long lists — instead of scrolling through them yourself.",
        "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    },
    {
        "name": "screenshot",
        "description": "Look at the page as an image. Use only when the text snapshot is not enough "
                       "(canvas, icons without labels, visual layout, captcha check).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ask_user",
        "description": "Ask the human and wait for the answer. Use when information is missing or ambiguous, "
                       "or when a human must act in the browser: login, 2FA, captcha, payment details.",
        "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    },
    {
        "name": "done",
        "description": "Finish the task and give the user a final report: what was done, results, "
                       "what was not done and why.",
        "input_schema": {"type": "object", "properties": {"report": {"type": "string"}}, "required": ["report"]},
    },
]

GUARDED = {"click", "type", "press_key"}  # actions that can trigger something irreversible


class ToolRunner:
    def __init__(self, browser: BrowserSession, task: str,
                 ask_user: Callable[[str], str], confirm: Callable[[str], bool]):
        self.b = browser
        self.task = task
        self.ask_user = ask_user
        self.confirm = confirm

    def _guard(self, name: str, args: dict) -> str | None:
        """Security layer. Returns a refusal message, or None if the action may proceed."""
        if name == "type" and not args.get("submit"):
            return None
        if name == "press_key" and args.get("key", "").lower() != "enter":
            return None
        element = self.b.describe(args["ref"]) if "ref" in args else "(focused element)"
        verdict = llm.is_destructive(self.task, f"{name} {args}", element, self.b.page.url)
        if not verdict.get("destructive"):
            return None
        if self.confirm(verdict.get("summary", element)):
            return None
        return "The user DECLINED this action. Do not retry it; choose another way or ask the user."

    def run(self, name: str, args: dict) -> tuple[list[dict] | str, bool]:
        """Execute a tool. Returns (content for tool_result, is_error)."""
        b = self.b
        try:
            if name in GUARDED:
                refusal = self._guard(name, args)
                if refusal:
                    return refusal, True

            match name:
                case "navigate":
                    b.navigate(args["url"])
                case "snapshot":
                    pass
                case "click":
                    b.click(args["ref"])
                case "type":
                    b.type_text(args["ref"], args["text"], bool(args.get("submit")))
                case "select_option":
                    b.select(args["ref"], args["option"])
                case "press_key":
                    b.press(args["key"])
                case "scroll":
                    b.scroll(args.get("direction", "down"), args.get("ref"))
                case "go_back":
                    b.go_back()
                case "switch_tab":
                    b.switch_tab(int(args["index"]))
                case "extract":
                    return llm.extract(args["question"], b.page_text(), b.page.url), False
                case "screenshot":
                    data = base64.standard_b64encode(b.screenshot()).decode()
                    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                            {"type": "text", "text": f"URL: {b.page.url}"}], False
                case "ask_user":
                    return f"User answered: {self.ask_user(args['question'])}", False
                case _:
                    return f"Unknown tool {name}", True
            return b.snapshot(), False
        except ActionError as e:
            # Recoverable: tell the model what went wrong + where we are now.
            try:
                return f"ERROR: {e}\n\nCurrent page:\n{b.snapshot()}", True
            except Exception:
                return f"ERROR: {e}", True
        except Exception as e:  # noqa: BLE001 — never crash the loop on a browser hiccup
            return f"ERROR ({type(e).__name__}): {str(e).splitlines()[0][:300]}", True
