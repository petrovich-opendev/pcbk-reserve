"""Чат в браузере (Д6а): вход ведёт в чат, страница чата под CSP в настоящем Chrome (cdp.py).

Место модуля — 09: модули Д5 берут 1, 3–8, прежние дни — 01, 02, 03 и 10. Модуль идёт последним по алфавиту и
гасит своё место к концу, в том числе в finally фикстуры web_users. Учётки: web-a (студент, место 9) и web-t
(преподаватель). Вкладка — на тест (фикстура page): шлюз держит не больше трёх потоков на логин и трёх живых
сессий на учётку, а вкладки одного профиля делят cookie.
"""
import html
import re
import secrets

import pytest

from conftest import REPO, STAND_ORIGIN

STAND = "https://stand.pcbk.test:18443"
assert STAND == STAND_ORIGIN                    # Origin шлюза тестового стенда (GW_ORIGIN) — тот же адрес
PLACE = 9
# предупреждение «учебный стенд» — текст .stand страницы входа: читается оттуда, а не копируется
STAND_WARNING = html.unescape(re.search(r'<div class="stand" role="note">(.*?)</div>',
                                        (REPO / "edge/static/login.html").read_text(encoding="utf-8"), re.S)[1])
# пароли — только в полях ввода: в assert их нет, иначе при падении pytest напечатал бы значения
PW = {"web-a": secrets.token_urlsafe(12), "web-t": secrets.token_urlsafe(12)}


@pytest.fixture(scope="module")
def web_users(stack):
    try:
        stack.add_account("web-a", "student", PLACE, PW["web-a"])
        stack.add_account("web-t", "teacher", None, PW["web-t"])
        yield dict(PW)
    finally:                                     # место модуля — в сон, даже при сбое
        name = f"pcbk-student-{PLACE:02d}"
        if stack.inspect(name)["State"]["Running"]:
            stack.stop(name)


def login_to_chat(page, login, password):
    """Вход на /login; «Рабочее место готово» ведёт в чат сама страница входа. «Вы вошли как» не годится: этот
    текст есть и на странице входа, и в шапке чата на экранах места и предупреждения (раунд 2, Minor 7)."""
    page.goto(STAND + "/login")
    page.type("#login", login)
    page.type("#password", password)
    page.press_enter("#password")
    page.wait_url("/chat/", 90)
    page.wait_selector("textarea", 90)


def test_login_leads_to_chat(stack, page, web_users):                                  # Review Focus 1
    login_to_chat(page, "web-a", web_users["web-a"])
    assert page.wait_text(re.escape(STAND_WARNING), 5) and page.wait_text(r"Стенд работает|Внимание|Сбой", 20)
    assert page.wait_text(r"Вы вошли как web-a", 5)
    assert page.csp() == [] and page.console_errors() == []
    assets = page.requests("/chat/assets/")
    assert [r["status"] for r in assets] and all(r["status"] == 200 for r in assets), assets
    assert [r["status"] for r in page.requests("/chat/") if r["path"] == "/chat/"] == [200]
