"""The agent loop: observe -> think -> act, until done() or a step limit.

Context management, in layers:
1. Snapshot, not HTML: only visible interactive elements + a short text excerpt.
2. Long reading is delegated to the extract() sub-agent; only its answer enters context.
3. Server-side context editing: old tool results (stale snapshots) are cleared once the
   prompt grows past a threshold; the last few are kept. History is never edited locally.
4. Prompt caching: tools + system prompt + history prefix are cached between steps.

Model cascade: every step runs on the cheap MAIN_MODEL. When it struggles (repeated
errors or a detected loop) the next steps go to ESCALATION_MODEL; after a few clean
steps control returns to the cheap model. Trade-off: the prompt cache is per model,
so each switch pays one cache write.
"""

from __future__ import annotations

import json
import os

import anthropic

from . import llm
from .tools import TOOLS, ToolRunner

SYSTEM = """You are an autonomous browser agent. You control a real, visible web browser \
in which the user is already logged in to their accounts. You receive a task in natural \
language and complete it end-to-end on your own.

How you work:
- Every action returns a snapshot of the page: interactive elements as `[id] role "name" ⟨context⟩`. \
Refer to elements only by these ids. Ids come from the latest snapshot; if an action fails \
because the page changed, look at the new snapshot and retry with a fresh id.
- Explore like a person: look at what is on the page, use site search and navigation, open \
items to see details. Do not assume URLs or button texts — find them.
- To read long content (an email, a vacancy, a menu, a profile) call extract() with a precise \
question instead of scrolling through it.
- Popups, cookie banners and overlays: close them (close button or Escape) and continue.
- If something fails twice the same way, change approach (different element, search instead \
of navigation, scroll, go back) instead of repeating it.
- Irreversible actions (paying, deleting, sending, applying) are checked by a safety layer; \
the user may be asked to confirm. If the user declines, do not retry the same action.
- Call ask_user only when you truly cannot continue: missing information that matters, \
login/2FA/captcha that a human must handle, or an ambiguous choice with real consequences.
- Keep your visible reasoning short: one line on what you see and what you do next.
- Finish with done(report). The report is for the user, in the user's language: what was \
done, key results (names, numbers), what was not done and why."""

CONTEXT_MANAGEMENT = {
    "edits": [
        # clear_thinking must come first when both are used
        {"type": "clear_thinking_20251015", "keep": {"type": "thinking_turns", "value": 3}},
        {
            "type": "clear_tool_uses_20250919",
            "trigger": {"type": "input_tokens", "value": 40_000},
            "keep": {"type": "tool_uses", "value": 4},
            "clear_at_least": {"type": "input_tokens", "value": 15_000},  # make cache invalidation worth it
            "exclude_tools": ["ask_user", "extract"],  # user answers and extracted facts stay
        },
    ]
}

MAX_STEPS = 60
MAX_TASK_COST = float(os.getenv("MAX_TASK_COST", "1.0"))  # $ hard stop per task
REPEAT_LIMIT = 3
ERRORS_TO_ESCALATE = 2   # consecutive failed tool calls
CLEAN_STEPS_TO_RETURN = 4  # error-free steps on the strong model before going back


class Agent:
    def __init__(self, browser, ui):
        self.browser = browser
        self.ui = ui
        self.messages: list = []
        self.stop_requested = False  # set from another thread (web UI "Stop" button)

    def run(self, task: str) -> str:
        self._repair_history()
        runner = ToolRunner(self.browser, task, ask_user=self.ui.ask, confirm=self.ui.confirm)
        start_cost = llm.usage.cost()
        # A follow-up task continues the same conversation (the agent remembers what it did).
        self.messages.append({"role": "user", "content": f"Task: {task}\n\nCurrent page:\n{self.browser.snapshot()}"})
        recent: list[str] = []
        model = llm.MAIN_MODEL
        error_streak = clean_streak = 0

        self.stop_requested = False
        self.steps = 0
        for step in range(1, MAX_STEPS + 1):
            self.steps = step
            if self.stop_requested:
                return "Остановлено пользователем."
            spent = llm.usage.cost() - start_cost
            if spent >= MAX_TASK_COST:
                return f"Остановлено: достигнут лимит расходов на задачу (${spent:.2f} из ${MAX_TASK_COST:.2f})."
            with self.ui.thinking(step):
                try:
                    resp = llm.client.beta.messages.create(
                        model=model,
                        max_tokens=16_000,
                        system=SYSTEM,
                        tools=TOOLS,
                        messages=self.messages,
                        thinking={"type": "adaptive", "display": "summarized"},
                        output_config={"effort": "medium"},
                        cache_control={"type": "ephemeral"},
                        context_management=CONTEXT_MANAGEMENT,
                        betas=["context-management-2025-06-27"],
                    )
                except anthropic.RateLimitError:
                    self.ui.error("Rate limit — SDK retries exhausted, stopping.")
                    return "Stopped: rate limited."
                except anthropic.APIStatusError as e:
                    self.ui.error(f"API error {e.status_code}: {e.message}")
                    return "Stopped: API error."
                except anthropic.APIConnectionError:
                    self.ui.error("Network error talking to the API.")
                    return "Stopped: network error."
                except anthropic.AnthropicError as e:  # e.g. expired `ant auth login` token
                    self.ui.error(f"Auth/config error: {str(e)[:200]}\n"
                                  "Run `ant auth login` or set ANTHROPIC_API_KEY.")
                    return "Stopped: no valid API credentials."
            llm.usage.add(model, resp.usage)
            self.messages.append({"role": "assistant", "content": resp.content})

            for block in resp.content:
                if block.type == "thinking" and block.thinking:
                    self.ui.thought(block.thinking)
                elif block.type == "text" and block.text.strip():
                    self.ui.say(block.text)

            if resp.stop_reason == "refusal":
                return "The model declined this task."
            if resp.stop_reason != "tool_use":
                # Model answered in plain text without done(): treat as final.
                return next((b.text for b in resp.content if b.type == "text"), "")

            results = []
            final = None
            looped = False
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                self.ui.tool_call(block.name, block.input)
                if block.name == "done":
                    final = block.input.get("report", "")
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": "ok"})
                    continue
                content, is_error = runner.run(block.name, block.input)

                # Loop detection: the same call over and over means the approach is not working.
                sig = f"{block.name}:{json.dumps(block.input, sort_keys=True)}"
                recent = (recent + [sig])[-REPEAT_LIMIT:]
                if len(recent) == REPEAT_LIMIT and len(set(recent)) == 1:
                    looped = True
                    note = (f"\n\nNOTE: you have called {block.name} with the same arguments "
                            f"{REPEAT_LIMIT} times. It is not working — try a different approach.")
                    content = content + note if isinstance(content, str) else content + [{"type": "text", "text": note}]

                error_streak = error_streak + 1 if is_error else 0
                self.ui.tool_result(content, is_error)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": content, "is_error": is_error})
            # All results of one assistant turn go back in ONE user message.
            self.messages.append({"role": "user", "content": results})
            self.ui.cost(llm.usage.cost() - start_cost, step, model)
            model, clean_streak = self._pick_model(model, error_streak, looped, clean_streak)
            if final is not None:
                return final

        return f"Stopped after {MAX_STEPS} steps without finishing."

    def _pick_model(self, model: str, error_streak: int, looped: bool, clean_streak: int) -> tuple[str, int]:
        """Escalate to the strong model when the cheap one is stuck; come back once things go smoothly."""
        strong = llm.ESCALATION_MODEL
        if model != strong and (looped or error_streak >= ERRORS_TO_ESCALATE):
            reason = "loop detected" if looped else f"{error_streak} errors in a row"
            self.ui.model_switch(model, strong, reason)
            return strong, 0
        if model == strong and strong != llm.MAIN_MODEL:
            clean_streak = 0 if (looped or error_streak) else clean_streak + 1
            if clean_streak >= CLEAN_STEPS_TO_RETURN:
                self.ui.model_switch(model, llm.MAIN_MODEL, f"{clean_streak} clean steps")
                return llm.MAIN_MODEL, 0
        return model, clean_streak

    def _repair_history(self) -> None:
        """After Ctrl+C mid-step, close dangling tool_use blocks so the next task is valid."""
        if not self.messages or self.messages[-1]["role"] != "assistant":
            return
        dangling = [b for b in self.messages[-1]["content"] if getattr(b, "type", None) == "tool_use"]
        if dangling:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": "Interrupted by the user.", "is_error": True}
                for b in dangling]})
