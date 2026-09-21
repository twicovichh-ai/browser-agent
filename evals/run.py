"""Run the eval suite: real model calls, real browser, programmatic checks.

    python -m evals.run                       # all tasks, visible browser
    python -m evals.run --only shop_order,mail_spam --headless
    AGENT_MODEL=claude-opus-5 python -m evals.run   # compare models

Writes evals/results.jsonl (one line per task run) and evals/RESULTS.md (table).
Costs real API money — every run is shown per task and in total.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from agent import llm
from agent.browser import BrowserSession
from agent.loop import Agent

from .tasks import TASKS

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent


class EvalUI:
    """Non-interactive UI: fixed policy for confirmations, logs a compact trace."""

    def __init__(self, confirm_policy: str):
        self.policy = confirm_policy
        self.switches = 0
        self.confirms = 0

    def thinking(self, step):
        return nullcontext()

    def tool_call(self, name, args):
        print(f"    → {name} {json.dumps(args, ensure_ascii=False)[:120]}")

    def tool_result(self, content, is_error):
        if is_error:
            first = content.splitlines()[0] if isinstance(content, str) else "[image]"
            print(f"      ✗ {first[:120]}")

    def model_switch(self, old, new, reason):
        self.switches += 1
        print(f"    ⇅ {old} → {new} ({reason})")

    def confirm(self, summary):
        self.confirms += 1
        ok = self.policy == "approve"
        print(f"    ⚠ confirm: {summary} -> {'approve' if ok else 'decline'}")
        return ok

    def ask(self, question):
        print(f"    ? ask_user: {question}")
        return "Решай сам, дополнительной информации нет."

    def __getattr__(self, _):  # thought/say/cost/error/report: not needed in the trace
        return lambda *a, **k: None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated task ids")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    tasks = [t for t in TASKS if not args.only or t.id in args.only.split(",")]

    urls = {"shop": (ROOT / "demo" / "shop.html").as_uri(), "mail": (ROOT / "demo" / "mail.html").as_uri()}
    run_id = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for t in tasks:
        browser = BrowserSession(profile_dir=tempfile.mkdtemp(prefix="eval-"), headless=args.headless)
        browser.start()
        ui = EvalUI(t.confirm)
        agent = Agent(browser, ui)
        cost0, t0 = llm.usage.cost(), time.time()
        print(f"\n▶ {t.id}")
        try:
            report = agent.run(t.text.format(**urls))
            ok, detail = t.check(browser.page, report)
        except Exception as e:  # noqa: BLE001 — a crash is a failed task, not a crashed suite
            report, ok, detail = f"crash: {e}", False, type(e).__name__
        finally:
            row = {
                "run": run_id, "task": t.id, "model": llm.MAIN_MODEL, "ok": ok, "detail": detail,
                "steps": getattr(agent, "steps", 0), "cost": round(llm.usage.cost() - cost0, 4),
                "seconds": round(time.time() - t0), "escalations": ui.switches, "confirms": ui.confirms,
                "report": report[:500],
            }
            browser.close()
        rows.append(row)
        print(f"  {'PASS' if ok else 'FAIL'} · {row['steps']} steps · ${row['cost']:.4f} · {row['seconds']}s · {detail}")

    with open(OUT / "results.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    passed = sum(r["ok"] for r in rows)
    lines = [f"## Run {run_id} · main model `{llm.MAIN_MODEL}`", "",
             "| Task | Result | Steps | Cost | Time | Escalations | Check |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['task']} | {'✅' if r['ok'] else '❌'} | {r['steps']} | ${r['cost']:.3f} | {r['seconds']}s | "
              f"{r['escalations']} | {r['detail']} |" for r in rows]
    lines += ["", f"**{passed}/{len(rows)} passed · total ${sum(r['cost'] for r in rows):.3f}**", ""]
    with open(OUT / "RESULTS.md", "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    print(llm.usage.summary())


if __name__ == "__main__":
    main()
