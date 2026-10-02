"""Вход и места в компоновке (Д5а): вход через edge, пробуждение места шлюзом через sp-ctl, строка «Шлюз и
вход», ручки шлюза не видны из сетей мест, edge пропускает в core только пути шлюза, учения шлюза.

Места этого модуля — 1, 3, 4, 5 (test_gateway Д5б — 6, 7, 8). Модули идут по очереди, и каждый гасит свои
места к концу, в том числе в finally фикстуры accounts (Ш16): модули прежних дней берут 01, 02, 03 и 10.
"""
import json
import re
import secrets
import time

import pytest

from conftest import STAND_ORIGIN, secret_forms
from gwclient import GwClient

# пароли — только в запросах: в assert сверяются коды и индексы, иначе при падении pytest напечатал бы значения
PW = {"stu-01": secrets.token_urlsafe(12), "stu-03": secrets.token_urlsafe(12), "stu-04": secrets.token_urlsafe(12),
      "stu-05": secrets.token_urlsafe(12), "teach": secrets.token_urlsafe(12)}
MONEY = ("account_remaining_usd", "key_usage_usd", "key_spent_usd")
OWN_PLACES = (1, 3, 4, 5)


@pytest.fixture(scope="module")
def accounts(stack):
    try:
        for login, place in (("stu-01", 1), ("stu-03", 3), ("stu-04", 4), ("stu-05", 5), ("teach", None)):
            stack.add_account(login, "teacher" if place is None else "student", place, PW[login])
        yield
    finally:                                     # Ш16: места модуля — в сон, даже при сбое
        for n in OWN_PLACES:
            if stack.inspect(f"pcbk-student-{n:02d}")["State"]["Running"]:
                stack.stop(f"pcbk-student-{n:02d}")


def row(data, cid):
    return next((c["state"], c["detail"], c.get("note", "")) for c in data["checks"] if c["component"] == cid)


def as_user(stack, login):
    c = stack.gw_client()
    c.login(login, PW[login])
    return c


def test_login_wake_ready_and_row(stack, accounts):                                   # Review Focus 5
    c = as_user(stack, "stu-01")
    assert c.wake_and_wait(90)["state"] == "ready"
    data = stack.wait_status(lambda d: row(d, "student-01")[:2] == ("ok", "работает"), timeout=60)
    assert row(data, "gateway")[:2] == ("ok", "принимает вход")
    assert stack.inspect("pcbk-student-02")["State"]["Running"] is False                # чужое место не тронуто


def test_place_sees_no_gateway(stack, accounts):                                       # Review Focus 2 (Д4б-R23)
    assert stack.gw_client().call("GET", "/pcbk/api/auth/me")[0] == 401               # положительный контроль: через edge
    for path in ("/pcbk/api/auth/me", "/pcbk/api/workplace", "/health/gateway"):
        code, body = stack.from_place(1, path)                                         # место 01 работает с прошлого теста
        assert (code, json.loads(body)["name"]) == (404, "NotFound"), path
    code, body = stack.from_place(1, "/health/llm")
    assert code == 200 and not set(MONEY) & set(json.loads(body))
    assert set(MONEY) <= set(stack.llm_health())                                       # администратор (pcbk-egress) видит
    assert stack.http_from_core("GET", "http://127.0.0.1:8000/health/gateway") == 200  # петля — своя зона и по источнику
    stack.stop("pcbk-student-01")


def test_edge_passes_only_gateway_paths(stack):                                       # Review Focus 2
    c = stack.gw_client()
    for path in ("/pcbk/api/data/tag_now", "/pcbk/api/healthz", "/pcbk/api/llm/v1/chat/completions",
                 "/pcbk/api", "/pcbk/api/auth"):
        code, body, _ = c.call("GET", path)
        assert (code, body["name"]) == (404, "NotFound"), path                        # JSON, не 301
    code, body, _ = c.call("GET", "/pcbk/api/oc")
    assert (code, body["data"]["message"]) == (403, "Вызов не разрешён")
    code, body, _ = c.call("GET", "/pcbk/api/oc/session")                             # Д5б: путь шлюза, без cookie — вход
    assert (code, body["name"]) == (401, "Unauthorized")
    for path in ("/pcbk/api/auth/%2e%2e/data/tag_now", "/pcbk/api/auth/..%2Fdata%2Ftag_now", "/pcbk/api/auth//me",
                 "/pcbk/api/data/%2e%2e/auth/me", "/pcbk/api/auth/./me", "/pcbk/api/auth/me%00"):
        code, body, _ = c.call("GET", path)
        assert (code, body["name"]) == (400, "BadRequest"), path                      # %00 — JSON, не 500 HTML
    for path in ("/pcbk/api/oc/session", "/pcbk/api/auth/me"):
        code, body, _ = c.call("TRACE", path)                                          # отвергается до выбора location
        assert (code, body["name"]) == (405, "MethodNotAllowed"), path
    for path in ("/api/auth/me", "/api/data/tag_now", "/healthz/data", "/health/historian", "/health/gateway", "/mcp",
                 "/pcbk-errors/503.json"):                                             # внутренние location снаружи не видны
        assert stack.https(path)[0] == 404, path
    code, body, _ = c.call("POST", "/pcbk/api/auth/login", {"login": "x", "password": "я" * 70000})
    assert (code, body["name"]) == (413, "PayloadTooLarge")


def test_edge_big_request_errors_are_json(stack):                                     # заметка А5: 414 и 494 по HTTP/1.1
    """Длинная строка запроса (414) и заголовок больше буфера (494 → 400) — JSON, а не страница nginx.

    Тело — по механизму Д5-R30: внутренний переход error_page повторяет серверную проверку Host. У 414 Host ещё
    не прочитан ($host = «_»), и тело — host.json при сохранённом коде 414; у большого Host — тоже host.json;
    большой Cookie после Host — 400.json.
    """
    big = "a" * 9000                                                                   # больше буфера заголовка 8 КиБ
    cases = [(stack.gw_client(), "/pcbk/api/auth/" + big, None, (414, "BadRequest", "Неверное имя сервера")),
             (stack.gw_client(), "/pcbk/api/auth/me", {"Cookie": "x=" + big}, (400, "BadRequest", "Неверный запрос")),
             (GwClient("https://127.0.0.1:18443", host="a." * 4500 + "ru", origin=STAND_ORIGIN), "/pcbk/api/auth/me",
              None, (400, "BadRequest", "Неверное имя сервера"))]
    for client, path, headers, expected in cases:
        code, body, h = client.call("GET", path, headers=headers)
        assert h.get_content_type() == "application/json" and isinstance(body, dict), (path[:24], code, body)
        assert (code, body["name"], body["data"]["message"]) == expected, (path[:24], headers and "Cookie")


def test_bad_host_refused_by_edge_and_bounded_in_core(stack, accounts):               # р4; Д5-R25, Д5-R30
    restarts = stack.inspect("pcbk-core")["RestartCount"]
    junk = {"Cookie": "pcbk_session=aaaa; pcbk_session=bbbb"}                          # без входа
    for host in ("a." * 4000 + "ru", "a.b.c.d.e.f.g", "x_y.pcbk.test"):
        c = GwClient("https://127.0.0.1:18443", host=host, origin=STAND_ORIGIN)
        code, body, _ = c.call("GET", "/pcbk/api/workplace", headers=junk)
        assert (code, body["name"]) == (400, "BadRequest"), host[:20]                  # edge — до core
    code = stack.http_front("GET", "/pcbk/api/workplace", headers={**junk, "X-Forwarded-Host": "a." * 4000 + "ru"})
    assert code == 401                                                                 # мимо edge: core держит бюджет сам
    info = stack.inspect("pcbk-core")
    assert info["RestartCount"] == restarts and info["State"]["OOMKilled"] is False


def test_login_page_headers(stack):
    code, headers = stack.https_headers("/login")
    assert code == 200 and "script-src 'self'" in headers["Content-Security-Policy"]
    assert (headers["Cache-Control"], headers["X-Content-Type-Options"], headers["Referrer-Policy"]) == \
           ("no-store", "nosniff", "no-referrer")


def test_place_from_account_only(stack, accounts):
    c = as_user(stack, "stu-03")
    code, body, _ = c.call("POST", "/pcbk/api/workplace/wake?place=04", headers={"X-Pcbk-Place": "04"})
    assert code == 202 and body["place"] == "03"
    assert stack.inspect("pcbk-student-04")["State"]["Running"] is False
    try:
        assert c.wake_and_wait(90)["state"] == "ready"                                 # пробуждение фоном: ждём исход
        assert stack.inspect("pcbk-student-04")["State"]["Running"] is False            # и после него 04 не запущено
    finally:
        stack.stop("pcbk-student-03")


def test_teacher_and_lockout(stack, accounts):
    t = as_user(stack, "teach")
    assert t.call("GET", "/pcbk/api/workplace")[0] == 403
    assert t.call("POST", "/pcbk/api/workplace/wake")[0] == 403
    bad = stack.gw_client()
    for _ in range(5):
        assert bad.call("POST", "/pcbk/api/auth/login", {"login": "stu-05", "password": "неверный-1"})[0] == 401
    code = bad.call("POST", "/pcbk/api/auth/login", {"login": "stu-05", "password": PW["stu-05"]})[0]
    assert code == 429
    stack._docker("exec", "pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "unlock", "--login", "stu-05")
    code = bad.call("POST", "/pcbk/api/auth/login", {"login": "stu-05", "password": PW["stu-05"]})[0]
    assert code == 200


def test_origin_required_and_counted(stack, accounts):
    raw = GwClient("https://127.0.0.1:18443")                                         # без Origin и Sec-Fetch-Site
    code = raw.call("POST", "/pcbk/api/auth/login", {"login": "stu-01", "password": PW["stu-01"]})[0]
    assert code == 403
    evil = GwClient("https://127.0.0.1:18443", host="stand.pcbk.test:18443", origin="https://stand.pcbk.test")   # хост без порта — Dify
    code = evil.call("POST", "/pcbk/api/auth/login", {"login": "stu-01", "password": PW["stu-01"]})[0]
    assert code == 403
    stack.wait_status(lambda d: row(d, "gateway")[:2] ==
                      ("warn", "вход отклоняется проверкой источника — проверьте GW_ORIGIN"), timeout=60)


def test_secrets_not_in_core_logs(stack, accounts):                                    # Review Focus 5
    c = as_user(stack, "stu-01")
    logs = stack.logs("pcbk-core")
    read = "gw login ok login=stu-01" in logs                                          # журнал прочитан
    assert read                                                                        # без печати журнала
    # пароли всех мест модуля — во всех видах (сырой, base64 и Basic): шлюз знает их все, будил 01 и 03
    forbidden = [*PW.values(), c.cookie, *(f for n in OWN_PLACES for f in secret_forms(stack.password(n)))]
    complete = len(forbidden) == len(PW) + 1 + 3 * len(OWN_PLACES) and all(forbidden)   # ни одного пустого
    leaked = [i for i, s in enumerate(forbidden) if s in logs]                          # индексы, не значения
    assert (complete, leaked) == (True, [])


def test_duplicate_cookies_through_edge(stack, accounts):                              # Review Focus 2; Д5-R25, Д5-R30
    own, other = as_user(stack, "stu-03"), as_user(stack, "stu-05")                   # после строки origin: она ниже
    both = {"Cookie": f"pcbk_session={own.cookie}; pcbk_session={other.cookie}"}
    code, body, headers = own.call("POST", "/pcbk/api/workplace/wake", headers=both)
    deletes = headers.get_all("Set-Cookie") or []
    assert (code, body["name"]) == (401, "Unauthorized")                               # не 503 «Серверный слой не отвечает»
    assert {re.search(r"Domain=([^;]+)", d)[1] for d in deletes if "Domain=" in d} == {"stand.pcbk.test", "pcbk.test"}
    assert sum("Partitioned" in d for d in deletes) * 2 == len(deletes) == 8 * 3 * 2    # 8 путей × 3 домена × 2
    assert other.call("GET", "/pcbk/api/auth/me")[0] == 401                            # подброшенная закрыта на сервере
    assert "too big header" not in stack.logs("pcbk-edge")
    stack.wait_status(lambda d: row(d, "gateway")[:2] == ("warn", "подброшена cookie сессии"), timeout=60)
    c = as_user(stack, "stu-03")
    code, _, headers = c.call("POST", "/pcbk/api/auth/logout")                        # штатный выход через edge
    assert code == 200 and len(headers.get_all("Set-Cookie") or []) == 8 * 3 * 2


# учения — последними в модуле: пересоздание core сбрасывает счётчики сбоев
def test_idle_place_sleeps_while_polled(stack, accounts):                             # Review Focus 3
    stack.recreate("core", {"GW_IDLE_STOP_S": "60", "GW_REAPER_TICK_S": "5"})
    try:
        c = as_user(stack, "stu-01")
        assert c.wake_and_wait(90)["state"] == "ready"
        deadline = time.monotonic() + 100
        while stack.inspect("pcbk-student-01")["State"]["Running"] and time.monotonic() < deadline:
            assert c.call("GET", "/pcbk/api/workplace")[0] == 200                     # опрос не держит место
            time.sleep(2)
        assert stack.inspect("pcbk-student-01")["State"]["Running"] is False
        assert c.call("GET", "/pcbk/api/workplace")[1]["state"] == "sleeping"
        stack.wait_status(lambda d: row(d, "student-01")[:2] == ("ok", "спит"), timeout=30)
        assert "gw stop place=01" in stack.logs("pcbk-core")
    finally:
        stack.recreate("core", {})


def test_sp_ctl_down_turns_row_red(stack, accounts):
    stack.stop("pcbk-sp-ctl")
    try:
        stack.wait_status(lambda d: row(d, "gateway")[:2] ==
                          ("fail", "нет связи с прокси сокета — места не будятся"), timeout=60)
        v = as_user(stack, "stu-04").wake_and_wait(30)
        assert v == {"place": "04", "state": "failed",
                     "detail": "Нет связи с прокси сокета — рабочее место не поднять. Сообщите преподавателю."}
    finally:
        stack.start("pcbk-sp-ctl")
    data = stack.wait_status(lambda d: row(d, "gateway")[0] != "fail", timeout=60)
    assert row(data, "gateway") == ("warn", "рабочее место не поднялось по входу", "1 за час")


def test_bad_place_password_drill(stack, accounts):
    stack.recreate("core", {"DRILL_GATEWAY": "bad_place_pw"})
    try:
        v = as_user(stack, "stu-05").wake_and_wait(90)
        assert v["detail"] == "Рабочее место не приняло пароль шлюза — сообщите администратору."
        assert stack.inspect("pcbk-student-05")["State"]["Running"] is False           # не полуживое
        stack.wait_status(lambda d: row(d, "gateway") ==
                          ("warn", "рабочее место не приняло пароль шлюза", "учения: bad_place_pw; 1 за час"),
                          timeout=60)
    finally:
        stack.recreate("core", {})


def test_reaper_freeze_drill(stack):                                                   # Review Focus 3
    stack.recreate("core", {"DRILL_GATEWAY": "freeze_reaper", "GW_REAPER_TICK_S": "5"})
    stack.recreate("watchdog", {"GW_STALE_S": "20"})
    try:
        stack.wait_status(lambda d: row(d, "gateway")[:2] ==
                          ("fail", "уборщик простоя молчит — места не засыпают"), timeout=90)
        assert "УЧЕНИЯ: DRILL_GATEWAY=freeze_reaper" in stack.logs("pcbk-core")
    finally:
        stack.recreate("core", {}); stack.recreate("watchdog", {})


def test_gateway_config_error_live(stack):                                             # Review Focus 4
    stack.recreate("core", {"GW_ORIGIN": "https://x/path"})
    try:
        data = stack.wait_status(lambda d: row(d, "gateway")[0] == "fail", timeout=60)
        assert row(data, "gateway")[1].startswith("ошибка настройки: настройки шлюза: GW_ORIGIN")
        assert row(data, "core")[0] == "ok"                                            # служба данных жива
        code, body, _ = stack.gw_client().call("GET", "/pcbk/api/auth/me")
        assert (code, body["name"]) == (503, "Unavailable")
    finally:
        stack.recreate("core", {})


def test_core_down_login_json_503(stack, accounts):
    stack.stop("pcbk-core")
    try:
        code, body, _ = stack.gw_client().call("POST", "/pcbk/api/auth/login", {"login": "stu-01", "password": PW["stu-01"]})
        assert (code, body) == (503, {"name": "Unavailable", "data": {"message": "Серверный слой не отвечает"}})
    finally:
        stack.start("pcbk-core")
