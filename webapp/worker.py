"""Agent thread for the web UI.

Playwright's sync API is bound to the thread that created it, so the browser session
and the agent live together in this one thread. The web server talks to it through
queues; WebUI implements the same interface as the terminal UI, but sends events to
the browser page and waits for the human's answers from it.
"""

from __future__ import annotations

import itertools
import json
import queue
import threading
from contextlib import contextmanager
from typing import Callable

from agent import llm
from agent.browser import BrowserSession
from agent.loop import Agent

Emit = Callable[[dict], None]


class WebUI:
    def __init__(self, emit: Emit):
        self.emit = emit
        self.replies: dict[int, queue.Queue] = {}
        self._ids = itertools.count(1)
        self.on_step: Callable[[], None] = lambda: None

    def _wait(self, kind: str, **payload):
        rid = next(self._ids)
        q: queue.Queue = queue.Queue(maxsize=1)
        self.replies[rid] = q
        self.emit({"type": kind, "id": rid, **payload})
        try:
            return q.get()
        finally:
            self.replies.pop(rid, None)
            self.emit({"type": "resolved", "id": rid})

    def reply(self, rid: int, value) -> None:
        if rid in self.replies:
            self.replies[rid].put(value)

    def cancel_pending(self) -> None:
        for q in list(self.replies.values()):
            q.put(None)

    # ---- same interface as agent.ui.UI ----

    @contextmanager
    def thinking(self, step: int):
        self.emit({"type": "status", "text": f"шаг {step}: модель думает…"})
        yield

    def thought(self, text: str) -> None:
        self.emit({"type": "thought", "text": text.strip()[:600]})

    def say(self, text: str) -> None:
        self.emit({"type": "say", "text": text.strip()})

    def tool_call(self, name: str, args: dict) -> None:
        self.emit({"type": "tool_call", "name": name, "args": json.dumps(args, ensure_ascii=False)[:400]})

    def tool_result(self, content, is_error: bool) -> None:
        text = content if isinstance(content, str) else "[screenshot]"
        self.emit({"type": "tool_result", "ok": not is_error, "text": text[:4000]})
        self.on_step()

    def cost(self, task_cost: float, step: int, model: str) -> None:
        self.emit({"type": "cost", "cost": round(task_cost, 4), "step": step, "model": model})

    def model_switch(self, old: str, new: str, reason: str) -> None:
        self.emit({"type": "model_switch", "old": old, "new": new, "reason": reason})

    def ask(self, question: str) -> str:
        return self._wait("ask", question=question) or "(пользователь не ответил)"

    def confirm(self, summary: str):
        answer = self._wait("confirm", summary=summary)
        return "all" if answer == "all" else bool(answer)

    def error(self, text: str) -> None:
        self.emit({"type": "error", "text": text})

    def report(self, text: str, usage_summary: str) -> None:
        self.emit({"type": "report", "text": text, "usage": usage_summary})


class AgentWorker(threading.Thread):
    def __init__(self, cdp_url: str, emit: Emit, demo_url: str = ""):
        super().__init__(daemon=True, name="agent")
        self.cdp_url = cdp_url
        self.demo_url = demo_url
        self.emit = emit
        self.ui = WebUI(emit)
        self.tasks: queue.Queue[str] = queue.Queue()
        self.busy = False
        self.active_url: str | None = None
        self.agent: Agent | None = None

    def stop_task(self) -> None:
        if self.agent:
            self.agent.stop_requested = True
        self.ui.cancel_pending()

    def run(self) -> None:
        browser = BrowserSession(
            cdp_url=self.cdp_url,
            on_dialog=lambda kind, msg: self.ui.confirm(f"Сайт показал {kind}-диалог: «{msg}». Подтвердить?"),
        )
        browser.start()
        browser.page = browser.context.pages[-1]
        self.agent = Agent(browser, self.ui)
        self.ui.on_step = lambda: setattr(self, "active_url", browser.page.url)
        self.emit({"type": "ready", "models": {"main": llm.MAIN_MODEL, "escalation": llm.ESCALATION_MODEL,
                                               "worker": llm.WORKER_MODEL},
                   "demo_url": self.demo_url})
        while True:
            task = self.tasks.get()
            self.busy = True
            self.emit({"type": "busy", "busy": True})
            try:
                report = self.agent.run(task)
            except Exception as e:  # noqa: BLE001 — keep the worker alive
                report = f"Ошибка агента: {type(e).__name__}: {str(e)[:300]}"
            self.ui.report(report, llm.usage.summary())
            self.busy = False
            self.emit({"type": "busy", "busy": False})
