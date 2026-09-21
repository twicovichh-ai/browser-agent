"""The eval checks themselves must be right: drive the demo pages into known good/bad
states by hand (no LLM) and assert each check says PASS/FAIL accordingly.

    python -m tests.test_eval_checks
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agent.browser import BrowserSession
from evals import tasks as T

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    b = BrowserSession(profile_dir=tempfile.mkdtemp(), headless=True)
    b.start()
    p = b.page
    try:
        shop, mail = (ROOT / "demo" / "shop.html").as_uri(), (ROOT / "demo" / "mail.html").as_uri()

        p.goto(shop)
        assert not T.check_order(p, "")[0], "empty cart must fail"
        p.evaluate("add('BBQ-бургер',420); document.getElementById('size').value='Большая'; "
                   "add('Картошка фри ('+document.getElementById('size').value+')',150)")
        assert T.check_order(p, "")[0], T.check_order(p, "")
        p.evaluate("pay()")
        assert not T.check_order(p, "")[0], "paid order must fail"

        p.goto(shop)
        p.evaluate("add('Картошка фри (Стандарт)',150)")
        assert T.check_cheapest(p, "Самое дешёвое — картошка фри, 150 ₽")[0]
        assert not T.check_cheapest(p, "не знаю")[0]

        p.goto(shop)
        assert T.check_price(p, "Двойной BBQ-бургер стоит 560 ₽")[0]
        p.evaluate("add('Наггетсы',220)")
        assert not T.check_price(p, "560 ₽")[0], "cart must stay empty"
        assert T.check_pay_declined(p, "")[0]

        p.goto(mail)
        assert not T.check_spam(p, "")[0], "nothing removed must fail"
        for i in T.SPAM_IDS:
            p.evaluate(f"move({i}, 'trash')")
        assert T.check_spam(p, "")[0], T.check_spam(p, "")
        assert not T.check_interview(p, "в 15:00")[0], "moved mails must fail the read-only task"
        p.evaluate("move(1, 'spam')")
        assert not T.check_spam(p, "")[0], "removing a legit mail must fail"

        p.goto(mail)
        assert T.check_interview(p, "Да, приглашение на 15:00 в четверг")[0]
        print("EVAL CHECKS PASSED")
    finally:
        b.close()


if __name__ == "__main__":
    main()
