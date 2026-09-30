"""Ручки шлюза (Д5-R25…R27, Д5-R31): вход и сессия в cookie, две cookie — обе сессии закрыты, удаления cookie в
пределах бюджета, запись только со своей страницы, scrypt в очереди, место — только по учётке, ручки — только с
pcbk-front. Места и sp-ctl — двойники задачи А3."""
import asyncio
import re
import sqlite3
import threading
import time
from contextlib import closing

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from helpers import ORIGIN, PW, login, make_gateway, serve_app, wait_until
from pcbk_core.gateway import accounts
from pcbk_core.gateway.api import (COOKIE_HEADERS_BUDGET, COOKIE_PATH_MAX_LEN, GW_MESSAGES, MAX_SESSION_COOKIES,
                                   clear_cookies)

pytestmark = pytest.mark.anyio
BASE = "https://stand.test:8443"


def make_request(path: str, headers: dict[str, str]) -> Request:
    """Request Starlette на словаре scope: GET, путь и сырой путь, заголовки в нижнем регистре."""
    return Request({"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


def test_login_sets_strict_cookie_and_me(tmp_path):
    app, _ = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        r = login(c, "stu-03")
        assert (r.status_code, r.json()) == (200, {"login": "stu-03", "role": "student", "place": "03"})
        cookie = r.headers["set-cookie"]
        assert all(f in cookie for f in ("pcbk_session=", "HttpOnly", "Secure", "SameSite=Strict", "Path=/pcbk/api/"))
        assert c.get("/pcbk/api/auth/me").json()["place"] == "03"
        assert c.post("/pcbk/api/auth/logout", headers={"Origin": ORIGIN}).status_code == 200
        assert c.get("/pcbk/api/auth/me").status_code == 401


def test_two_session_cookies_refused(tmp_path):                                         # Review Focus 2
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        own = login(c, "stu-03").cookies["pcbk_session"]
        other = login(TestClient(app, base_url=BASE), "stu-05").cookies["pcbk_session"]   # «подброшенная»
        c.cookies.clear()
        r = c.get("/pcbk/api/auth/me", headers={"Cookie": f"pcbk_session={own}; pcbk_session={other}"})
        assert (r.status_code, r.json()["name"]) == (401, "Unauthorized")
        deletes = r.headers.get_list("set-cookie")
        assert all(d.startswith("pcbk_session=; Max-Age=0; Path=") and "Secure" in d for d in deletes)
        assert not any(a in d.lower() for d in deletes for a in ("expires", "httponly", "samesite"))   # короткие строки
        assert {p for d in deletes for p in re.findall(r"Path=([^;]+)", d)} == set(rfc_paths("/pcbk/api/auth/me"))
        assert {re.search(r"Domain=([^;]+)", d)[1] if "Domain=" in d else None for d in deletes} == {None, "stand.test"}
        assert sum("Partitioned" in d for d in deletes) * 2 == len(deletes) == 8 * 2 * 2
        assert role.stats.count_1h("cookies_duplicate") == 1
    assert role.store.session(own) is None and role.store.session(other) is None        # обе закрыты на сервере


def rfc_paths(path):                                                                    # RFC 6265 §5.1.4
    parts = path.strip("/").split("/")
    heads = ["/" + "/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    return ["/"] + [v for h in heads[:-1] for v in (h, h + "/")] + [heads[-1]]


def test_clear_cookies_size_budget():                                                   # Д5-R25, Д5-R30
    long_path = "/pcbk/api/oc/session/ses_" + "A" * 26 + "/prompt_async"             # самый длинный путь белого списка
    assert len(long_path) == COOKIE_PATH_MAX_LEN == 64
    host = "reserve-stand.ai-lab.customer-domain.test"                                 # четыре метки, длиннее боевого
    for path, xfh in ((long_path, host), (long_path + "/x" * 400, host), ("/pcbk/api/auth/me", None)):
        headers = {"host": "pcbk-core:8000", "x-forwarded-host": xfh} if xfh else {"host": f"{host}:8443"}   # Host от edge — имя службы
        lines = clear_cookies(make_request(path, headers))
        assert sum(len("set-cookie: ") + len(v) + 2 for v in lines) <= COOKIE_HEADERS_BUDGET, path[:40]
        domains = {re.search(r"Domain=([^;]+)", v)[1] for v in lines if "Domain=" in v}
        assert domains == {host, "ai-lab.customer-domain.test", "customer-domain.test"}
    assert len(clear_cookies(make_request(long_path, {"host": host}))) == 12 * 4 * 2
    assert not any("Domain=" in v for v in clear_cookies(make_request("/pcbk/api/auth/me", {"host": "127.0.0.1:18443"})))
    six = "a1.b2.c3.d4.e5.test"                                                        # шесть меток — не больше трёх доменов
    assert {re.search(r"Domain=([^;]+)", v)[1] for v in clear_cookies(make_request(long_path, {"host": six}))
            if "Domain=" in v} == {"e5.test", "d4.e5.test", "c3.d4.e5.test"}
    for bad in ("a." * 4000 + "ru", "a" * 8000, "a.b.c.d.e.f.g", "x_y.test", "[::1]", "a." * 60 + "ru"):
        t = time.monotonic()
        lines = clear_cookies(make_request(long_path, {"host": "pcbk-core:8000", "x-forwarded-host": bad}))
        assert time.monotonic() - t < 0.05, bad[:20]                                   # р4: не сотни МБ
        assert not any("Domain=" in v for v in lines) and len(lines) == 12 * 2, bad[:20]
        assert sum(len("set-cookie: ") + len(v) + 2 for v in lines) <= COOKIE_HEADERS_BUDGET


def test_junk_cookie_pair_not_counted(tmp_path, monkeypatch):                          # р4: сигнал не подделать мусором
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        r = c.get("/pcbk/api/workplace", headers={"Cookie": "pcbk_session=aaaa; pcbk_session=bbbb"})
        assert r.status_code == 401 and r.headers.get_list("set-cookie")               # удаления — как всегда
        assert role.stats.count_1h("cookies_duplicate") == 0
        closed, real = [], role.store.close_session
        monkeypatch.setattr(role.store, "close_session", lambda t: (closed.append(t), real(t))[1])
        many = "; ".join(f"pcbk_session=x{i}" for i in range(500))
        assert c.get("/pcbk/api/workplace", headers={"Cookie": many}).status_code == 401
        assert len(closed) == MAX_SESSION_COOKIES                                      # смотрятся первые 8 значений


def test_writes_need_origin(tmp_path):
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        for hdrs, code in (({"Origin": ""}, 403), ({"Origin": "https://evil.example"}, 403),
                           ({"Origin": "https://stand.test"}, 403),                    # тот же хост на 443 — Dify
                           ({"Origin": ORIGIN}, 200), ({"Origin": "", "Sec-Fetch-Site": "same-origin"}, 200),
                           ({"Origin": "https://127.0.0.1:28443", "Sec-Fetch-Site": "same-origin"}, 200),
                           ({"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}, 403)):
            assert login(c, "stu-03", **hdrs).status_code == code, hdrs
        assert role.stats.count_1h("origin_refused") == 4
    app, _ = make_gateway(tmp_path / "empty", GW_ORIGIN="")
    with TestClient(app, base_url=BASE) as c:
        assert login(c, "stu-03", Origin="").status_code == 403                        # пустой GW_ORIGIN — ни с чем


def test_errors_are_opencode_json_without_www_authenticate(tmp_path):
    app, _ = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        r = c.get("/pcbk/api/auth/me")
        assert r.status_code == 401 and r.json() == {"name": "Unauthorized", "data": {"message": "Нужно войти"}}
        assert "www-authenticate" not in r.headers
        assert c.get("/pcbk/api/auth/nothing").json() == {"name": "NotFound", "data": {"message": "Не найдено"}}
        assert c.request("PROPFIND", "/pcbk/api/auth/me").json()["name"] in ("NotFound", "MethodNotAllowed")
        for _ in range(5):
            assert login(c, "stu-03", pw="неверный-1").status_code == 401
        r = login(c, "stu-03")
        assert (r.status_code, r.json()["name"]) == (429, "TooManyAttempts")
        r = c.post("/pcbk/api/auth/login", content=b"x" * 70000, headers={"Origin": ORIGIN, "Content-Type": "application/json"})
        assert (r.status_code, r.json()["name"]) == (413, "PayloadTooLarge")          # Ш22: и мимо edge — вид OpenCode


async def test_login_scrypt_bounded(tmp_path, monkeypatch):                             # Review Focus 5 Д5а (память)
    app, role = make_gateway(tmp_path)
    active, peak, guard, real = 0, 0, threading.Lock(), accounts.verify_password

    def slow(pw, stored):
        nonlocal active, peak
        with guard:
            active += 1; peak = max(peak, active)
        time.sleep(0.2)
        with guard:
            active -= 1
        return real(pw, stored)
    monkeypatch.setattr(accounts, "verify_password", slow)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        rs = await asyncio.gather(*(c.post("/pcbk/api/auth/login", json={"login": f"nobody{i}", "password": "x" * 8},
                                           headers={"Origin": ORIGIN}) for i in range(20)))
    codes = [r.status_code for r in rs]
    assert peak <= 2 and set(codes) <= {401, 429} and codes.count(429) >= 10
    assert role.stats.count_1h("login_busy") >= 10


def test_unknown_login_not_logged(tmp_path, gwlog):                                   # Review Focus 5
    app, _ = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        login(c, "Мой-Пароль-123", pw="x")
        login(c, "sunflower7", pw="x")                                                 # проходит формат логина
    assert gwlog.text.count("login=<неизвестный>") == 2
    assert "Мой-Пароль-123" not in gwlog.text and "sunflower7" not in gwlog.text


def test_workplace_by_account_only(tmp_path):
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        r = c.post("/pcbk/api/workplace/wake?place=05", headers={"Origin": ORIGIN, "X-Pcbk-Place": "05"})
        assert r.status_code == 202 and r.json()["place"] == "03"
        wait_until(lambda: ("start", 3) in role.fake_docker.calls)                    # пробуждение идёт фоном
        assert [call for call in role.fake_docker.calls if call[0] == "start"] == [("start", 3)]


def test_teacher_has_no_place(tmp_path):
    app, _ = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        assert login(c, "teach").json()["place"] is None
        for method in ("GET", "POST"):
            path = "/pcbk/api/workplace" + ("/wake" if method == "POST" else "")
            r = c.request(method, path, headers={"Origin": ORIGIN})
            assert (r.status_code, r.json()["data"]["message"]) == (403, "У преподавателя нет рабочего места")


def test_front_only_routes(tmp_path):                                                   # Review Focus 2 (Д4б-R23)
    app, _ = make_gateway(tmp_path)
    with TestClient(app, base_url="http://172.31.6.2:8000") as place:                  # адрес core в сети места 06
        for path in ("/pcbk/api/auth/me", "/pcbk/api/workplace", "/health/gateway"):
            r = place.get(path)
            assert (r.status_code, r.json()["name"]) == (404, "NotFound"), path
        assert place.post("/pcbk/api/auth/login", json={"login": "stu-03", "password": PW},
                          headers={"Origin": ORIGIN}).status_code == 404
    with TestClient(app, base_url="http://172.31.250.34:8000") as front:               # адрес core в pcbk-front
        assert front.get("/pcbk/api/auth/me").status_code == 401
        assert front.get("/health/gateway").status_code == 200


# --- сверх брифа: заметки контроллера для А4 ---

def test_wrong_password_answer_does_not_wait_for_failure_record(tmp_path, monkeypatch):   # журнал: Note for А4
    """401 уходит клиенту, пока запись неудачи в замок ещё идёт: по времени ответа не видно, есть ли логин."""
    app, role = make_gateway(tmp_path)
    entered, release, real = threading.Event(), threading.Event(), role.store.failure

    def blocked(name):
        entered.set()
        release.wait(10)
        real(name)
    monkeypatch.setattr(role.store, "failure", blocked)
    with serve_app(app) as url:                                                        # uvicorn: ответ — до фона
        try:
            with httpx.Client(trust_env=False, timeout=5) as c:
                r = c.post(url + "/pcbk/api/auth/login", json={"login": "stu-03", "password": "неверный-1"},
                           headers={"Origin": ORIGIN})
            assert r.status_code == 401 and r.json()["data"]["message"] == GW_MESSAGES["bad_credentials"]
            assert entered.wait(5) and not release.is_set()                           # запись шла после ответа
        finally:
            release.set()
        wait_until(lambda: failures(tmp_path) == 1)                                     # неудача всё же записана
    assert role.stats.count_1h("login_failed") == 1


def failures(tmp_path) -> int:
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        return db.execute("SELECT count(*) FROM gw_login_failures WHERE login = 'stu-03'").fetchone()[0]


def test_account_disabled_between_verify_and_session_is_401(tmp_path, monkeypatch, gwlog):   # Note for А4
    app, role = make_gateway(tmp_path)
    real = role.store.verify

    def verify_then_disable(name, pw):
        account = real(name, pw)
        role.store.disable(name)                                                       # администратор успел раньше
        return account
    monkeypatch.setattr(role.store, "verify", verify_then_disable)
    with TestClient(app, base_url=BASE) as c:
        r = login(c, "stu-03")
        assert (r.status_code, r.json()) == (401, {"name": "Unauthorized",
                                                   "data": {"message": "Неверный логин или пароль"}})
        assert "set-cookie" not in r.headers
    assert "gw login refused login=stu-03 reason=disabled" in gwlog.text


def test_log_lines_carry_no_secrets(tmp_path, gwlog):                                  # Review Focus 5
    app, _ = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        token = login(c, "stu-03").cookies["pcbk_session"]
        login(c, "stu-05", pw="неверный-пароль-7")
        c.get("/pcbk/api/auth/me", headers={"Cookie": f"pcbk_session={token}; pcbk_session=junk"})
    text = gwlog.text
    assert "gw login ok login=stu-03 role=student place=03" in text                    # положительный контроль
    assert "gw login refused login=stu-05 reason=password" in text
    assert "gw cookies duplicate n=2 live=1 logins=stu-03" in text
    assert token not in text and PW not in text and "неверный-пароль-7" not in text and "junk" not in text


def test_bad_body_and_logout_without_session(tmp_path):
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        for body in (b"{bad", b"[]", b'{"login": "stu-03"}', b'{"login": 3, "password": "x"}'):
            r = c.post("/pcbk/api/auth/login", content=body, headers={"Origin": ORIGIN})
            assert (r.status_code, r.json()["name"]) == (400, "BadRequest"), body
        r = c.post("/pcbk/api/auth/logout", headers={"Origin": ORIGIN})            # без сессии — те же удаления
        assert (r.status_code, r.json()) == (200, {}) and r.headers.get_list("set-cookie")
        assert c.post("/pcbk/api/auth/logout").status_code == 403                     # запись — только со своей страницы
    assert role.stats.count_1h("login_failed") == 0
