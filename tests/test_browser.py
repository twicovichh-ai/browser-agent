"""Offline check of the browser layer — no LLM calls, no money spent.

    python -m tests.test_browser
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agent.browser import ActionError, BrowserSession

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Test shop</title></head><body>
<div id="cookie" role="dialog" style="position:fixed;inset:0;background:#0008">
  <div style="background:#fff;margin:100px;padding:20px">Мы используем cookies
    <button onclick="document.getElementById('cookie').remove()">Принять</button></div></div>
<h1>Бургерная</h1>
<input placeholder="Поиск по меню" onkeydown="if(event.key==='Enter')document.getElementById('q').innerText='Искали: '+this.value">
<p id="q"></p>
<div class="card"><h3>Классический бургер</h3><span>350 ₽</span>
  <div class="btn" style="cursor:pointer" onclick="add('Классический')">Добавить</div></div>
<div class="card"><h3>BBQ-бургер</h3><span>420 ₽</span>
  <div class="btn" style="cursor:pointer" onclick="add('BBQ')">Добавить</div></div>
<div class="card"><h3>Картошка фри</h3><span>150 ₽</span>
  <div class="btn" style="cursor:pointer" onclick="add('Фри')">Добавить</div></div>
<p>Корзина: <span id="cart"></span></p>
<button onclick="if(confirm('Оплатить заказ?'))document.getElementById('cart').innerText+=' [ОПЛАЧЕНО]'">Оплатить</button>
<iframe srcdoc="<button onclick=&quot;this.innerText='нажато'&quot;>Кнопка в iframe</button>" style="height:60px"></iframe>
<script>function add(x){document.getElementById('cart').innerText+=x+';'}</script>
</body></html>"""


def find_id(snapshot: str, needle: str, name: str = "") -> str:
    for line in snapshot.splitlines():
        if needle in line and name in line and line.startswith("["):
            return line[1:line.index("]")]
    raise AssertionError(f"'{needle}' not in snapshot:\n{snapshot}")


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "shop.html").write_text(PAGE, encoding="utf-8")
    dialogs = []
    b = BrowserSession(profile_dir=str(tmp / "profile"), headless=True,
                       on_dialog=lambda kind, msg: dialogs.append((kind, msg)) or False)
    b.start()
    try:
        b.navigate((tmp / "shop.html").as_uri())
        s = b.snapshot()
        print(s, "\n" + "-" * 60)
        assert "OPEN DIALOG/POPUP" in s, "cookie popup not reported"

        # Overlay blocks clicks -> recoverable error, then close it.
        try:
            b.click(find_id(s, "BBQ-бургер", '"Добавить"'))
            raise AssertionError("click through overlay should fail")
        except ActionError as e:
            print("expected error:", e)
        b.click(find_id(s, '"Принять"'))

        s = b.snapshot()
        assert "OPEN DIALOG" not in s
        # Three identical "Добавить" buttons — context tells them apart.
        b.click(find_id(s, "BBQ-бургер", '"Добавить"'))
        b.click(find_id(s, "Картошка фри", '"Добавить"'))
        cart = b.page.inner_text("#cart")
        assert cart == "BBQ;Фри;", cart

        b.type_text(find_id(s, "Поиск по меню"), "бургер", submit=True)
        assert "Искали: бургер" in b.page.inner_text("#q")

        # confirm() goes to the human; here the test "user" declines.
        b.click(find_id(s, '"Оплатить"'))
        assert dialogs and dialogs[0][0] == "confirm"
        assert "ОПЛАЧЕНО" not in b.page.inner_text("#cart")

        # iframe elements are addressable with a frame prefix.
        iframe_id = find_id(s, "Кнопка в iframe")
        assert iframe_id.startswith("f"), iframe_id
        b.click(iframe_id)

        # Stale id -> clear error for the model.
        try:
            b.click("99999")
            raise AssertionError("stale id should fail")
        except ActionError as e:
            print("expected error:", e)

        print("-" * 60, "\nFinal snapshot size:", len(b.snapshot()), "chars")
        print("ALL CHECKS PASSED")
    finally:
        b.close()


if __name__ == "__main__":
    main()
