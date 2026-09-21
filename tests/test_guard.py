"""Security layer modes, offline (guard model and browser are faked).

    python -m tests.test_guard
"""

from __future__ import annotations

import os
from types import SimpleNamespace as NS

os.environ.setdefault("ANTHROPIC_API_KEY", "offline-test")

from agent import llm, tools  # noqa: E402

guard_calls = []
llm.is_destructive = lambda *a, **k: guard_calls.append(1) or {"destructive": True, "summary": "Оплатить заказ"}


class FakeBrowser:
    page = NS(url="about:blank")

    def describe(self, ref):
        return "button Оплатить"


def runner(answers):
    it = iter(answers)
    asked = []

    def confirm(summary):
        asked.append(summary)
        return next(it)
    return tools.ToolRunner(FakeBrowser(), "task", ask_user=lambda q: "", confirm=confirm), asked


def main() -> None:
    click = {"ref": "1"}

    tools.CONFIRM_MODE = "task"
    r, asked = runner(["all"])
    assert r._guard("click", click) is None and r._guard("click", click) is None
    assert len(asked) == 1, "approve-all must silence further questions in this task"
    r2, asked2 = runner([False])
    assert r2._guard("click", click), "a new task starts asking again"
    assert len(asked2) == 1

    tools.CONFIRM_MODE = "ask"
    r, asked = runner(["all", True])
    r._guard("click", click); r._guard("click", click)
    assert len(asked) == 2, "'ask' mode ignores approve-all"

    tools.CONFIRM_MODE = "off"
    guard_calls.clear()
    r, asked = runner([])
    assert r._guard("click", click) is None and not asked and not guard_calls, "'off' skips the guard model too"
    print("GUARD CHECK PASSED")


if __name__ == "__main__":
    main()
