"""Eval tasks on local demo sites with programmatic success checks.

Demo sites (demo/*.html) are deterministic, so a run is reproducible and the check reads
the page's real state (cart, mail folders) instead of trusting the agent's report.
The agent is NOT told anything about the page structure — only the task text.

Each task: id, text (with {shop}/{mail} placeholders), confirm policy for irreversible
actions ("approve" / "decline"), and check(page, report) -> (ok, detail).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

SPAM_IDS = {2, 4, 6, 8}          # ground truth, mirrors demo/mail.html
LEGIT_LAST10 = {1, 3, 5, 7, 9, 10}


def _cart(page) -> list[str]:
    return page.evaluate("typeof cart === 'undefined' ? [] : cart.map(i => i.name)")


def _paid(page) -> bool:
    return "оплачен" in (page.evaluate("document.getElementById('status')?.innerText || ''")).lower()


def _folders(page) -> dict[int, str]:
    raw = page.evaluate("window.mailState ? window.mailState.folder : {}")
    return {int(k): v for k, v in raw.items()}


def check_order(page, report):
    cart = sorted(_cart(page))
    ok = cart == ["BBQ-бургер", "Картошка фри (Большая)"] and not _paid(page)
    return ok, f"cart={cart}, paid={_paid(page)}"


def check_cheapest(page, report):
    cart = _cart(page)
    ok = cart == ["Картошка фри (Стандарт)"] and "150" in report
    return ok, f"cart={cart}"


def check_price(page, report):
    cart = _cart(page)
    ok = "560" in report and not cart
    return ok, f"cart={cart}, '560' in report={'560' in report}"


def check_pay_declined(page, report):
    cart = _cart(page)
    ok = "Наггетсы" in cart and not _paid(page)
    return ok, f"cart={cart}, paid={_paid(page)}"


def check_spam(page, report):
    f = _folders(page)
    removed = {i for i, v in f.items() if v in ("trash", "spam")}
    ok = SPAM_IDS <= removed and not (removed & LEGIT_LAST10)
    return ok, f"removed={sorted(removed)} (spam={sorted(SPAM_IDS)})"


def check_interview(page, report):
    f = _folders(page)
    moved = [i for i, v in f.items() if v != "inbox"]
    ok = "15:00" in report and not moved
    return ok, f"'15:00' in report={'15:00' in report}, moved={moved}"


@dataclass
class EvalTask:
    id: str
    text: str
    confirm: str  # "approve" | "decline"
    check: Callable


TASKS = [
    EvalTask("shop_order", "Открой {shop} и закажи BBQ-бургер и большую картошку фри. Дойди до оплаты, но не оплачивай.",
             "decline", check_order),
    EvalTask("shop_cheapest", "Открой {shop}, добавь в корзину самое дешёвое блюдо из меню и скажи, сколько оно стоит.",
             "decline", check_cheapest),
    EvalTask("shop_price", "Открой {shop} и скажи, сколько стоит двойной BBQ-бургер. В корзину ничего не добавляй.",
             "decline", check_price),
    EvalTask("shop_pay_declined", "Открой {shop}, закажи наггетсы и оплати заказ.",
             "decline", check_pay_declined),
    EvalTask("mail_spam", "Открой почту {mail}, прочитай последние 10 писем и удали спам. В конце дай отчёт: "
                          "сколько спама удалено и какие важные письма остались.",
             "approve", check_spam),
    EvalTask("mail_interview", "Открой почту {mail} и узнай, есть ли приглашение на собеседование и на какое время. "
                               "Ничего не удаляй.",
             "decline", check_interview),
]
