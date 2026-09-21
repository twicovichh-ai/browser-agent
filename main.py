"""Entry point: opens a visible browser and a chat with the agent in the terminal.

    python main.py                       # own persistent profile in ./.profile
    python main.py --cdp http://localhost:9222   # attach to your running Chrome
"""

from __future__ import annotations

import argparse

from rich.prompt import Prompt

from agent import llm
from agent.browser import BrowserSession
from agent.loop import Agent
from agent.ui import UI, console


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", help="CDP URL of a running Chrome, e.g. http://localhost:9222")
    ap.add_argument("--profile", default=".profile", help="Profile dir for the launched browser")
    ap.add_argument("-v", "--verbose", action="store_true", help="Print full snapshots")
    args = ap.parse_args()

    ui = UI(verbose=args.verbose)
    browser = BrowserSession(
        cdp_url=args.cdp,
        profile_dir=args.profile,
        on_dialog=lambda kind, msg: ui.confirm(f"Сайт показал {kind}-диалог: «{msg}». Подтвердить?"),
    )
    browser.start()
    agent = Agent(browser, ui)
    console.print(f"[bold]Browser agent[/bold] · main: {llm.MAIN_MODEL} · "
                  f"escalation: {llm.ESCALATION_MODEL} · worker: {llm.WORKER_MODEL}")
    console.print("[dim]Войдите в нужные аккаунты в открывшемся браузере, затем пишите задачу. "
                  "Ctrl+C — прервать задачу, 'exit' — выход.[/dim]")
    try:
        while True:
            task = Prompt.ask("\n[bold green]Задача[/bold green]").strip()
            if task.lower() in {"exit", "quit", "выход"}:
                break
            if not task:
                continue
            try:
                report = agent.run(task)
            except KeyboardInterrupt:
                report = "Прервано пользователем."
            ui.report(report, llm.usage.summary())
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        browser.close()


if __name__ == "__main__":
    main()
