"""Чат в браузере (Д6а): вход ведёт в чат, страница чата под CSP в настоящем Chrome (cdp.py); ответ через шлюз, поток по
HTTP/2, сон места и конец сессии, ошибка модели, преподаватель без чата, баннер и строка «Веб-чат» сторожа.

Место модуля — 09: модули Д5 берут 1, 3–8, прежние дни — 01, 02, 03 и 10. Модуль идёт последним по алфавиту и
гасит своё место к концу, в том числе в finally фикстуры web_users. Учётки: web-a (студент, место 9) и web-t
(преподаватель, своя вкладка с отдельными cookie — isolated_page). Вкладка — на тест (фикстура page): шлюз держит не
больше трёх потоков на логин и трёх живых сессий на учётку, а вкладки одного профиля делят cookie. Поток /event есть
только у открытого разговора (Д6-R43), поэтому тесты потока сначала задают вопрос двойнику OpenRouter.

Снимки для владельца (preflight M17): при PCBK_SHOTS=<каталог> тесты кладут туда 01–04 в своих точках — к концу
прогона стенд уже опущен, снимать потом не на чем. Данные на снимках — тестовые (тег 20FAKE_…, учётка web-a).
"""
import html
import json
import os
import re
import secrets
import time
from pathlib import Path

import pytest

from conftest import REPO, STAND_ORIGIN
from gateway_cases import ALLOWED, OC, SES_FAKE, TWINS

STAND = "https://stand.pcbk.test:18443"
assert STAND == STAND_ORIGIN                    # Origin шлюза тестового стенда (GW_ORIGIN) — тот же адрес
PLACE = 9
# предупреждение «учебный стенд» — текст .stand страницы входа: читается оттуда, а не копируется
STAND_WARNING = html.unescape(re.search(r'<div class="stand" role="note">(.*?)</div>',
                                        (REPO / "edge/static/login.html").read_text(encoding="utf-8"), re.S)[1])
Q = "что сейчас по тегу 20FAKE_001_PV?"
ANSWER = r"Готово: .+"                          # ответ двойника OpenRouter: «Готово: » и начало вывода инструмента
TOOL_DONE = r"Запрос к данным: текущее значение — готово"
# сообщения агента в разговоре (ChatView: один узел на ход — адаптер склеивает шаги хода) и идущий ход («Стоп»)
ASSISTANT_TEXTS_JS = "[...document.querySelectorAll('.msg[data-role=\"assistant\"]')].map((e) => e.innerText)"
RUNNING_JS = "!!document.querySelector('.composer .stop')"
# один круг переподключения адаптера: поток /event и пять GET разговора (сам разговор, сообщения, session/status,
# permission, question), все — разом. Остановленная обёртка дальше в сеть не ходит, так что отказов (401, 503) — не больше
# одного круга, иначе это шторм (Review Focus 2)
RECONNECT_ROUND = 6
# форма входа видна: /me ответил 401 (без сессии); при живой сессии форма так и остаётся скрытой
LOGIN_FORM_JS = "(() => { const e = document.getElementById('login'); return !!e && e.getClientRects().length > 0; })()"
# строка «Веб-чат» страницы состояния — в середину экрана (для снимка 04)
WEB_ROW_INTO_VIEW_JS = ("[...document.querySelectorAll('body *')].find((e) => e.children.length === 0 "
                        "&& e.textContent.trim() === 'Веб-чат').scrollIntoView({block: 'center'}), 1")
PROMPT_PATH = re.compile(rf"{re.escape(OC)}/session/(ses_[0-9A-Za-z]{{26}})/prompt_async")
# сверка путей браузера с правилами шлюза в образе core (preflight F1: gateway_cases.py — без импорта pcbk_core, на хосте
# нет fastapi): argv — пары [метод, путь после /pcbk/api/oc без «?…»], ответ — имя правила или null на каждую
RULES_PY = """\
import json, sys
from pcbk_core.gateway.rules import Refused, match
names = []
for method, path in json.loads(sys.argv[1]):
    try:
        names.append(match(method, path)[0].name)
    except Refused:
        names.append(None)
print(json.dumps(names))
"""
# контроль сверки: 15 разрешённых пар — правило есть, «близнецы» (удалить разговор, прочие методы) — нет
RULES_CONTROL_OK = [[m, p[len(OC):]] for m, p in ALLOWED]
RULES_CONTROL_NO = [["DELETE", f"/session/{SES_FAKE}"], *([m, p[len(OC):]] for m, p in TWINS[:5])]


class Passwords(dict):
    """Пароли учёток модуля: pytest печатает аргументы упавшего теста — repr без значений (в журналах их нет)."""

    def __repr__(self) -> str:
        return "{" + ", ".join(f"{k!r}: '<скрыт>'" for k in self) + "}"


@pytest.fixture(scope="module")
def web_users(stack):
    pw = Passwords({"web-a": secrets.token_urlsafe(12), "web-t": secrets.token_urlsafe(12)})
    try:
        stack.add_account("web-a", "student", PLACE, pw["web-a"])
        stack.add_account("web-t", "teacher", None, pw["web-t"])
        yield pw                                 # test_signed_out_goes_to_login меняет пароль web-a здесь же
    finally:                                     # место модуля — в сон, даже при сбое
        name = f"pcbk-student-{PLACE:02d}"
        if stack.inspect(name)["State"]["Running"]:
            stack.stop(name)


def login_to_chat(page, login, password):
    """Вход на /login; «Рабочее место готово» ведёт в чат сама страница входа. «Вы вошли как» не годится признаком
    чата: этот текст есть и на странице входа, и в шапке чата на экранах места и предупреждения (раунд 2, Minor 7) —
    он сверяет только учётку, когда поле ввода чата уже видно.

    Сессия этой вкладки уже есть (cookie вкладок одного контекста общие, браузер — на модуль) — страница входа сама
    будит место и ведёт в чат, формы нет: второй вход не нужен. Каждый вход — новая сессия, а их у учётки не больше
    трёх (новая вытесняет старую), так что тесты входят как можно реже."""
    page.goto(STAND + "/login")
    how = page.wait(lambda: "chat" if page.url().split("?")[0].endswith("/chat/")
                    else "form" if page.eval(LOGIN_FORM_JS) else None, 90, "форма входа или переход в чат")
    if how == "form":
        page.type("#login", login)
        page.type("#password", password)
        page.press_enter("#password")
    page.wait_url("/chat/", 90)
    page.wait_selector("textarea", 90)
    page.wait_text(rf"Вы вошли как {re.escape(login)}\b", 10)


def ask(page, text: str, timeout: float = 120) -> str:
    """Вопрос в поле ввода и Enter; ждёт новое сообщение агента — их стало на одно больше, чем до отправки (preflight
    F4: иначе второй вопрос вернул бы уже показанный ответ), в нём «Готово: …», и ход кончился. Текст этого сообщения
    (innerText: строка вызова инструмента и ответ; свёрнутый вывод под строкой в него не входит)."""
    before = len(page.eval(ASSISTANT_TEXTS_JS))
    page.type("textarea", text)
    page.press_enter("textarea")

    def answered():
        texts = page.eval(ASSISTANT_TEXTS_JS)
        if len(texts) == before + 1 and re.search(ANSWER, texts[-1]) and not page.eval(RUNNING_JS):
            return texts[-1]
        return None
    return page.wait(answered, timeout, f"новое сообщение агента с /{ANSWER}/ (до вопроса их было {before})")


def norm(s: str) -> str:
    """Пробелы схлопнуты (preflight F2): FastMCP пишет вывод JSON с отступами, markdown в браузере склеивает строки."""
    return " ".join(s.split())


def page_sessions(page) -> list[str]:
    """Разговоры, в которые вкладка отправляла вопросы (prompt_async), по порядку отправки."""
    return [m[1] for r in page.requests(OC + "/session/") if (m := PROMPT_PATH.fullmatch(r["path"]))]


def last_session(stack, n: int) -> str:
    """Разговор места n, изменённый последним, — из сети места мимо шлюза (истина о месте)."""
    code, body = stack.oc(n, "GET", "/session", n)
    assert code == 200, code
    return max(json.loads(body), key=lambda s: s["time"]["updated"])["id"]


def tool_output(stack, n: int, sid: str, tool: str) -> str:
    """Вывод последнего завершённого вызова инструмента tool в разговоре sid места n — из места мимо шлюза."""
    code, body = stack.oc(n, "GET", f"/session/{sid}/message", n)
    assert code == 200, code
    outs = [p["state"]["output"] for m in json.loads(body) for p in m["parts"]
            if p.get("type") == "tool" and p.get("tool") == tool and p["state"].get("status") == "completed"]
    assert outs, f"в разговоре нет завершённого вызова {tool}"
    return outs[-1]


def not_allowed(stack, reqs: list[dict]) -> list[tuple[str, str]]:
    """Запросы вкладки под /pcbk/api/oc/, которым нет правила белого списка шлюза, — одной сверкой в образе core
    (rules.match, те же 15 правил; путь без /pcbk/api/oc и без «?…»). Контроль в той же сверке: разрешённые пары
    проходят, «близнецы» — нет, иначе пустой ответ ничего бы не значил."""
    pairs = [[r["method"], r["path"][len(OC):]] for r in reqs]
    assert pairs, "вкладка не ходила в /pcbk/api/oc/ — сверять нечего"
    names = json.loads(stack._docker("exec", "pcbk-core", "python", "-c", RULES_PY,
                                     json.dumps(pairs + RULES_CONTROL_OK + RULES_CONTROL_NO)).stdout)
    ok, no = names[len(pairs):len(pairs) + len(RULES_CONTROL_OK)], names[len(pairs) + len(RULES_CONTROL_OK):]
    assert all(ok) and len(set(ok)) == 15 and not any(no), (ok, no)
    return [(m, p) for (m, p), name in zip(pairs, names) if name is None]


def refusals(page) -> list[tuple[str, int]]:
    """Ответы-отказы (4xx, 5xx) на запросы вкладки под /pcbk/api/oc/: путь и код."""
    return [(r["path"], r["status"]) for r in page.requests(OC + "/") if (r["status"] or 0) >= 400]


def row(d: dict, component: str) -> tuple[str, str, str]:
    return next((c["state"], c["detail"], c.get("note", "")) for c in d["checks"] if c["component"] == component)


def shot(page, name: str) -> None:
    """Снимок вкладки в PCBK_SHOTS (снимки для владельца, M17); без переменной — ничего."""
    where = os.environ.get("PCBK_SHOTS")
    if where:
        page.screenshot(Path(where) / name)


def test_login_leads_to_chat(stack, page, web_users):                                  # Review Focus 1
    login_to_chat(page, "web-a", web_users["web-a"])
    assert page.wait_text(re.escape(STAND_WARNING), 5) and page.wait_text(r"Стенд работает|Внимание|Сбой", 20)
    assert page.wait_text(r"Вы вошли как web-a", 5)
    assert page.csp() == [] and page.console_errors() == []
    assets = [(r["path"], r["status"]) for r in page.requests("/chat/assets/")]           # без заголовков: в них cookie
    assert assets and all(status == 200 for _, status in assets), assets
    assert [r["status"] for r in page.requests("/chat/") if r["path"] == "/chat/"] == [200]
    # положительный контроль наблюдателей (доводка 1, I1): без него тихо сломанный слушатель CSP или разбор консоли
    # (обновление Chrome, правка cdp.py) оставил бы проверки выше зелёными навсегда. Пробы — после них
    page.eval("fetch('https://example.invalid/').catch(() => 0), 1")               # connect-src 'self' — отказ
    page.wait(lambda: page.csp(), 5, "отчёт CSP о пробном нарушении")
    assert [v["effective"] for v in page.csp()] == ["connect-src"], page.csp()
    assert page.console_errors() == []
    page.eval("console.error('pcbk-probe'), 1")
    page.wait(lambda: page.console_errors(), 5, "console.error пробы")
    assert page.console_errors() == ["pcbk-probe"]


def test_chat_answers_through_gateway(stack, page, web_users):                            # Review Focus 1, 4
    before = stack.gw_health()["refused_1h"]
    login_to_chat(page, "web-a", web_users["web-a"])
    answer = ask(page, Q)
    assert page.wait_text(TOOL_DONE, 5)
    sid = last_session(stack, PLACE)
    assert page_sessions(page) == [sid]                                                    # вопрос ушёл в этот разговор
    tool = tool_output(stack, PLACE, sid, "pcbk_tag_now")                                   # вывод инструмента из места
    assert norm(tool)[:40] in norm(answer), (norm(tool)[:40], norm(answer))                # ответ построен на выводе
    shot(page, "01-chat-answer.png")
    assert page.csp() == [] and page.console_errors() == []
    assert stack.gw_health()["refused_1h"] == before
    bad = not_allowed(stack, page.requests("/pcbk/api/oc/"))   # вне assert: pytest показал бы запросы с заголовками
    assert bad == [], bad                                                                  # те же 15 правил


def test_browser_headers_and_h2_stream(stack, page, web_users):                           # хвост Д5б [?]
    login_to_chat(page, "web-a", web_users["web-a"]); ask(page, Q)                         # разговор открыт — поток есть
    posts = [(r["path"], r["headers"].get("sec-fetch-site"), r["headers"].get("origin"))  # не весь запрос: в нём cookie
             for r in page.requests("/pcbk/api/") if r["method"] == "POST"]
    assert posts and all(site == "same-origin" for _, site, _ in posts), posts
    assert all(origin == STAND for _, _, origin in posts), posts                            # при Referrer-Policy no-referrer
    ev = [r["protocol"] for r in page.requests("/pcbk/api/oc/event") if r["status"] == 200]
    assert ev and ev[-1] == "h2", ev
    n0 = page.chunks("/pcbk/api/oc/event"); time.sleep(35)
    assert page.chunks("/pcbk/api/oc/event") - n0 >= 3                                     # пульс 10 с идёт через edge
    assert page.csp() == []


def test_place_asleep_shows_wake_and_goes_quiet(stack, page, web_users):                  # Review Focus 2
    login_to_chat(page, "web-a", web_users["web-a"]); ask(page, Q)
    sid = last_session(stack, PLACE)
    stack.stop("pcbk-student-09")
    page.wait_text(r"Рабочее место спит", 90)
    shot(page, "03-place-asleep.png")
    n = len(page.requests("/pcbk/api/")); time.sleep(30)
    assert [r["path"] for r in page.requests("/pcbk/api/")[n:]] == []                      # остановлен до действия студента
    assert 1 <= len(refusals(page)) <= RECONNECT_ROUND, refusals(page)                     # один круг, не шторм
    page.click_text("Разбудить"); page.wait_gone(r"Рабочее место спит", 90)
    assert "Готово:" in page.wait_text(ANSWER, 30)                                         # тот же разговор (initialSessionId)
    ask(page, Q)
    assert page_sessions(page) == [sid, sid] and last_session(stack, PLACE) == sid
    # консоль не сверяется: на отказ списка разговоров (сон, 401) адаптер пишет console.error сам — это не ошибка чата
    assert page.csp() == []


def test_signed_out_goes_to_login(stack, page, web_users):                                # Review Focus 2
    login_to_chat(page, "web-a", web_users["web-a"]); ask(page, Q)
    assert [r["status"] for r in page.requests("/pcbk/api/oc/event") if r["status"] == 200]  # поток был (M18)
    web_users["web-a"] = stack.passwd("web-a")                                             # passwd закрывает сессии
    page.wait_url("/login", 45)
    n = len(page.requests("/pcbk/api/oc/")); time.sleep(10)
    assert [r["path"] for r in page.requests("/pcbk/api/oc/")[n:]] == []                   # после ухода — ни одного
    unauth = [f for f in refusals(page) if f[1] == 401]
    assert unauth and len(unauth) <= RECONNECT_ROUND, unauth                               # до ухода — один круг 401 (M18)
    assert page.csp() == []                     # консоль не сверяется — как у сна места: console.error самого адаптера


def test_model_error_visible_in_chat(stack, page, web_users):
    login_to_chat(page, "web-a", web_users["web-a"])
    page.type("textarea", "FAKE402 что сейчас?"); page.press_enter("textarea")
    page.wait_text(r"На счёте OpenRouter кончились деньги", 90)
    assert page.csp() == [] and page.console_errors() == []


def test_teacher_sees_no_chat(stack, isolated_page, web_users):                           # свои cookie: web-a не задет
    page = isolated_page
    page.goto(f"{STAND}/login")
    page.type("#login", "web-t"); page.type("#password", web_users["web-t"]); page.press_enter("#password")
    page.wait_text(r"У преподавателя нет рабочего места", 30)                              # страница входа, не чат
    page.goto(f"{STAND}/chat/"); page.wait_url("/chat/", 10); page.wait_text(r"У преподавателя пока нет чата", 20)
    assert [r["path"] for r in page.requests("/pcbk/api/oc/")] == []
    assert page.csp() == [] and page.console_errors() == []


def test_banner_follows_watchdog(stack, page, web_users):                                 # Review Focus 5; учение — ближе к концу
    login_to_chat(page, "web-a", web_users["web-a"])
    stack.stop("pcbk-watchdog")                                                            # preflight M14: stop/start стенда
    try:
        page.wait_text(r"Состояние стенда неизвестно — сторож не отвечает", 30 + 15)
        shot(page, "02-banner-unknown.png")
    finally:
        stack.start("pcbk-watchdog")
    page.wait_gone(r"Состояние стенда неизвестно", 60)
    assert page.csp() == [] and page.console_errors() == []


def test_web_row_and_drill(stack, page):                                                  # учение строки «Веб-чат» — последним
    build = json.loads((stack.static_dir / "chat/build.json").read_text())
    stack.wait_status(lambda d: row(d, "web") == ("ok", "страница чата отдаётся", f"сборка {build['build']}"), timeout=60)
    asset = stack.static_dir / "chat" / next(p for p in build["files"] if p.endswith(".css"))
    hidden = asset.with_suffix(".hidden")
    asset.rename(hidden)
    try:
        stack.wait_status(lambda d: row(d, "web")[:2] == ("fail", "сборка чата неполная"), timeout=60)
    finally:
        hidden.rename(asset)
    stack.wait_status(lambda d: row(d, "web")[:2] == ("ok", "страница чата отдаётся"), timeout=60)
    page.goto(f"{STAND}/status")                                                           # строка на странице владельца
    page.wait_text(r"Веб-чат", 20); page.wait_text(r"страница чата отдаётся", 20)
    page.eval(WEB_ROW_INTO_VIEW_JS)                                                        # строка — последняя, ниже экрана
    shot(page, "04-status-web-row.png")
