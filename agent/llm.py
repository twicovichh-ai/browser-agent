"""Model cascade: a strong model drives the agent, a cheap one does sub-tasks.

- MAIN_MODEL   decides what to do next (the agent loop).
- ESCALATION_MODEL takes over for a few steps when MAIN_MODEL gets stuck.
- WORKER_MODEL reads long page text (extract sub-agent) and judges whether an
  action is destructive (security guard). Cheap, fast, narrow prompts.
Every call goes through Usage so the terminal shows real cost per task.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import anthropic

MAIN_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-5")
WORKER_MODEL = os.getenv("WORKER_MODEL", "claude-haiku-4-5")
ESCALATION_MODEL = os.getenv("ESCALATION_MODEL", "claude-opus-5")  # used only when MAIN_MODEL is stuck

# $ per 1M tokens: (input, output). Cache write = 1.25x input, cache read = 0.1x input.
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

client = anthropic.Anthropic()


@dataclass
class Usage:
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, model: str, u) -> None:
        m = self.by_model.setdefault(model, {"in": 0, "out": 0, "cache_w": 0, "cache_r": 0, "calls": 0})
        m["in"] += u.input_tokens or 0
        m["out"] += u.output_tokens or 0
        m["cache_w"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        m["cache_r"] += getattr(u, "cache_read_input_tokens", 0) or 0
        m["calls"] += 1

    def cost(self) -> float:
        total = 0.0
        for model, m in self.by_model.items():
            pin, pout = PRICES.get(model, (5.0, 25.0))
            total += (m["in"] * pin + m["cache_w"] * pin * 1.25 + m["cache_r"] * pin * 0.1 + m["out"] * pout) / 1e6
        return total

    def summary(self) -> str:
        rows = [f"{k}: {v['calls']} calls, in {v['in']:,} (+cache w {v['cache_w']:,} / r {v['cache_r']:,}), out {v['out']:,}"
                for k, v in self.by_model.items()]
        return "\n".join(rows) + f"\nTotal: ${self.cost():.4f}"


usage = Usage()


def _json_call(system: str, user: str, schema: dict, max_tokens: int = 1024) -> dict:
    resp = client.messages.create(
        model=WORKER_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    usage.add(WORKER_MODEL, resp.usage)
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return json.loads(text)


# ---------- sub-agent: read a long page and answer one question ----------

EXTRACT_SYSTEM = (
    "You read the raw text of a web page and answer the question about it. "
    "Quote exact values (names, prices, senders, subjects) — do not invent anything. "
    "If the answer is not in the text, say so explicitly. Be concise and structured."
)


def extract(question: str, page_text: str, url: str) -> str:
    resp = client.messages.create(
        model=WORKER_MODEL,
        max_tokens=2048,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": f"<page url=\"{url}\">\n{page_text}\n</page>\n\nQuestion: {question}"}],
    )
    usage.add(WORKER_MODEL, resp.usage)
    return next((b.text for b in resp.content if b.type == "text"), "(empty)")


# ---------- security layer: is this action irreversible? ----------

GUARD_SYSTEM = (
    "You are a safety reviewer for a browser automation agent. Decide whether the action is "
    "DESTRUCTIVE or IRREVERSIBLE for the user: paying or placing an order, deleting or moving to "
    "trash/spam, sending a message/email/application/reply, publishing, changing account or "
    "security settings, unsubscribing, accepting legal terms. Opening, searching, reading, "
    "navigating, filtering, adding to cart, filling a field without submitting are NOT destructive. "
    "Judge by what the element will do in its context, in any language."
)

GUARD_SCHEMA = {
    "type": "object",
    "properties": {
        "destructive": {"type": "boolean"},
        "summary": {"type": "string", "description": "What will happen, one short sentence in Russian"},
    },
    "required": ["destructive", "summary"],
    "additionalProperties": False,
}


def is_destructive(task: str, action: str, element: str, url: str) -> dict:
    return _json_call(
        GUARD_SYSTEM,
        f"User task: {task}\nPage: {url}\nAction: {action}\nTarget element: {element}",
        GUARD_SCHEMA,
        max_tokens=300,
    )
