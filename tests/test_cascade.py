"""Offline check of the model cascade: the API client is faked, no money spent.

    python -m tests.test_cascade

Scenario: the cheap model clicks a missing element twice (2 errors) -> escalate to
the strong model -> 4 clean steps -> back to the cheap model -> done().
"""

from __future__ import annotations

import os
from types import SimpleNamespace as NS

os.environ.setdefault("ANTHROPIC_API_KEY", "offline-test")

from agent import llm  # noqa: E402
from agent.browser import ActionError  # noqa: E402
from agent.loop import Agent  # noqa: E402

SCRIPT = [("click", {"ref": "404"})] * 2 + [("scroll", {"direction": d}) for d in ("down", "up", "down", "up", "down")] + [("done", {"report": "ok"})]


class FakeBrowser:
    page = NS(url="about:blank")

    def snapshot(self):
        return "URL: about:blank\n(none)"

    def click(self, ref):
        raise ActionError(f"Element [{ref}] no longer exists")

    def scroll(self, direction, ref=None):
        pass

    def describe(self, ref):
        return "span"


class FakeUI:
    def __init__(self):
        self.switches = []

    def thinking(self, step):
        from contextlib import nullcontext
        return nullcontext()

    def model_switch(self, old, new, reason):
        self.switches.append((old, new, reason))
        print(f"switch {old} -> {new} ({reason})")

    def __getattr__(self, name):  # every other UI call is a no-op
        return lambda *a, **k: None


def main() -> None:
    calls = []
    steps = iter(SCRIPT)

    def fake_create(**kw):
        calls.append(kw["model"])
        name, args = next(steps)
        return NS(content=[NS(type="tool_use", id=f"t{len(calls)}", name=name, input=args)],
                  stop_reason="tool_use",
                  usage=NS(input_tokens=1000, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0))

    llm.client.beta.messages.create = fake_create
    llm.is_destructive = lambda *a, **k: {"destructive": False, "summary": ""}  # guard is not under test

    ui = FakeUI()
    report = Agent(FakeBrowser(), ui).run("test task")

    cheap, strong = llm.MAIN_MODEL, llm.ESCALATION_MODEL
    print("models per step:", calls)
    assert report == "ok", report
    assert calls[:2] == [cheap, cheap], calls
    assert calls[2:6] == [strong] * 4, calls
    assert calls[6:] == [cheap, cheap], calls
    assert [s[1] for s in ui.switches] == [strong, cheap]
    print(llm.usage.summary())
    print("CASCADE CHECK PASSED")


if __name__ == "__main__":
    main()
