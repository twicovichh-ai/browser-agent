"""Terminal UI: shows every tool call with arguments, results, thoughts and running cost."""

from __future__ import annotations

import json
from contextlib import contextmanager

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()


class UI:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    @contextmanager
    def thinking(self, step: int):
        with console.status(f"[dim]step {step}: model is thinking…[/dim]"):
            yield

    def thought(self, text: str) -> None:
        console.print(f"[dim italic]💭 {text.strip()[:400]}[/dim italic]")

    def say(self, text: str) -> None:
        console.print(f"[white]{text.strip()}[/white]")

    def tool_call(self, name: str, args: dict) -> None:
        shown = json.dumps(args, ensure_ascii=False)
        console.print(f"[bold cyan]→ {name}[/bold cyan] [cyan]{shown[:300]}[/cyan]")

    def tool_result(self, content, is_error: bool) -> None:
        text = content if isinstance(content, str) else "[image]"
        first = text.strip().splitlines()[0] if text.strip() else ""
        if is_error:
            console.print(f"  [red]✗ {first[:200]}[/red]")
        elif self.verbose:
            console.print(Panel(text[:3000], border_style="dim"))
        else:
            console.print(f"  [green]✓[/green] [dim]{first[:160]} ({len(text):,} chars)[/dim]")

    def cost(self, task_cost: float, step: int, model: str) -> None:
        console.print(f"  [dim]step {step} · {model} · task cost so far ${task_cost:.4f}[/dim]")

    def model_switch(self, old: str, new: str, reason: str) -> None:
        console.print(f"[bold magenta]⇅ {old} → {new}[/bold magenta] [magenta]({reason})[/magenta]")

    def ask(self, question: str) -> str:
        console.print(Panel(question, title="Агент спрашивает", border_style="yellow"))
        return Prompt.ask("[yellow]Ваш ответ[/yellow]")

    def confirm(self, summary: str):
        console.print(Panel(summary, title="⚠ Необратимое действие", border_style="red"))
        answer = Prompt.ask("[red]Разрешить?[/red] y — да, a — да для всей задачи, n — нет",
                            choices=["y", "a", "n"], default="n")
        return "all" if answer == "a" else answer == "y"

    def error(self, text: str) -> None:
        console.print(f"[bold red]{text}[/bold red]")

    def report(self, text: str, usage_summary: str) -> None:
        console.print(Panel(text, title="Результат", border_style="green"))
        console.print(f"[dim]{usage_summary}[/dim]")
