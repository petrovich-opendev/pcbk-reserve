"""Отчёты браузеров (Д6-R50, Review Focus 2 Д6б): POST /pcbk/api/web/report — только четыре вида, не чаще раза в
минуту на пару (логин, вид), тело до 256 байт, без сессии — 401, сессия раньше источника; GET /health/web — только
зона front. Учётки — встроенные make_gateway (stu-03, stu-05, teach); база открывается в lifespan, поэтому клиент с
with — один, прочие — без with (preflight Ф1)."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from helpers import ORIGIN, login, make_gateway
from pcbk_core.gateway.web import REPORT_KINDS, WEB_REPORT_EVERY_S, WEB_REPORT_MAX_BYTES, WebReports, _read_capped

BASE = "https://stand.test:8443"                                                     # зона front, cookie Secure уходит
REPORT = "/pcbk/api/web/report"
JSON_TYPE = {"content-type": "application/json"}


def test_web_report_rules(tmp_path, gwlog):
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        assert login(c, "stu-03").status_code == 200
        post = lambda body, origin=ORIGIN: c.post(REPORT, content=body, headers={"origin": origin, **JSON_TYPE})
        assert post(b'{"kind":"turn_silent"}').status_code == 204
        assert post(b'{"kind":"turn_silent"}').status_code == 204                    # второй за минуту — без счёта и строки
        assert post(b'{"kind":"ui_crash"}').status_code == 204
        assert post(b'{"kind":"boot_failed"}').status_code == 204
        for bad in (b'{"kind":"x"}', b'{"kind":"ui_crash","text":"<script>"}', b'[]', b'{"kind":"' + b"a" * 300 + b'"}',
                    b'{"kind":"<script>"}', b'{"kind":1}', b'{"kind":"ui_crash","kind":"ui_crash"}', b"{bad", b""):
            r = post(bad)
            assert (r.status_code, r.json()["name"]) == (400, "BadRequest"), bad
        assert post(b'{"kind":"ui_crash"}', origin="https://evil").status_code == 403
        assert role.stats.count_1h("origin_refused") == 1                             # чужой источник со входом — как wake
        assert c.get("/health/web").json() == {"boot_failed_1h": 1, "ui_crash_1h": 1, "turn_silent_1h": 1,
                                                "stream_down_1h": 0}
        anon = TestClient(app, base_url=BASE)                                         # без cookie
        for origin in (ORIGIN, "https://evil"):                                       # Ф10: сессия раньше источника
            r = anon.post(REPORT, content=b'{"kind":"ui_crash"}', headers={"origin": origin, **JSON_TYPE})
            assert (r.status_code, r.json()["name"]) == (401, "Unauthorized"), origin
        assert role.stats.count_1h("origin_refused") == 1                             # аноним строку сторожа не красит
    text = gwlog.text
    assert text.count("gw web report login=stu-03 kind=turn_silent") == 1           # положительный контроль журнала
    assert "gw web report login=stu-03 kind=ui_crash" in text
    assert "<script>" not in text and "aaaa" not in text                              # ввод клиента в журнал не попал


def test_web_report_rate_and_size(tmp_path, gwlog):                                  # Review Focus 2: поток и размер
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE) as c:
        assert login(c, "stu-05").status_code == 200
        post = lambda body: c.post(REPORT, content=body, headers={"origin": ORIGIN, **JSON_TYPE})
        assert {post(b'{"kind":"stream_down"}').status_code for _ in range(100)} == {204}   # сотня за минуту
        assert c.get("/health/web").json()["stream_down_1h"] == 1
        role.mono.advance(WEB_REPORT_EVERY_S)                                         # минута прошла — снова счёт
        assert post(b'{"kind":"stream_down"}').status_code == 204
        assert c.get("/health/web").json()["stream_down_1h"] == 2
        exact = b'{"kind":"ui_crash"}'.ljust(WEB_REPORT_MAX_BYTES)                    # пробелы JSON до самого предела
        assert len(exact) == 256 and post(exact).status_code == 204
        assert post(exact + b" ").status_code == 400                                  # 257 байт — отказ
        r = post(b'{"kind":"ui_crash"}' + b" " * 70000)                               # больше предела /pcbk/api/ — 413 до шлюза
        assert (r.status_code, r.json()["name"]) == (413, "PayloadTooLarge")
        assert c.get("/health/web").json() == {"boot_failed_1h": 0, "ui_crash_1h": 1, "turn_silent_1h": 0,
                                                "stream_down_1h": 2}
        assert c.get(REPORT).status_code == 404                                       # только POST, без Allow
        role.mono.advance(3600)                                                       # окно — час
        assert set(c.get("/health/web").json().values()) == {0}
    assert gwlog.text.count("gw web report login=stu-05 kind=stream_down") == 2


def test_web_report_size_without_content_length(tmp_path):                          # итоговая доводка Д6б, Б1-m2
    """Тело без Content-Length (chunked) — предел считает сам поток: 256 байт — 204, 257 — 400 до разбора JSON."""
    app, _ = make_gateway(tmp_path)

    def chunked(body: bytes, size: int):
        for i in range(0, len(body), size):
            yield body[i:i + size]
    exact = b'{"kind":"ui_crash"}'.ljust(WEB_REPORT_MAX_BYTES)
    with TestClient(app, base_url=BASE) as c:
        assert login(c, "stu-03").status_code == 200
        for size in (1, 100, WEB_REPORT_MAX_BYTES + 1):
            r = c.post(REPORT, content=chunked(exact, size), headers={"origin": ORIGIN, **JSON_TYPE})
            assert "content-length" not in r.request.headers and r.request.headers["transfer-encoding"] == "chunked"
            assert r.status_code == 204, size                                          # положительный контроль
            r = c.post(REPORT, content=chunked(exact + b" ", size), headers={"origin": ORIGIN, **JSON_TYPE})
            assert "content-length" not in r.request.headers
            assert (r.status_code, r.json()["name"]) == (400, "BadRequest"), size


def test_read_capped_stops_at_first_byte_over_limit():                               # Б1-m2: куски, без Content-Length
    """_read_capped без Content-Length читает поток до первого байта сверх предела и дальше не читает."""
    class Stream:
        def __init__(self, parts):
            self.headers, self.parts, self.read = {}, parts, 0

        async def stream(self):
            for part in self.parts:
                self.read += 1
                yield part
    exact = Stream([b"x"] * WEB_REPORT_MAX_BYTES)
    assert asyncio.run(_read_capped(exact, WEB_REPORT_MAX_BYTES)) == b"x" * WEB_REPORT_MAX_BYTES
    over = Stream([b"x"] * (WEB_REPORT_MAX_BYTES + 1) + [b"y"] * 1000)               # 257-й байт — отказ
    assert asyncio.run(_read_capped(over, WEB_REPORT_MAX_BYTES)) is None
    assert over.read == WEB_REPORT_MAX_BYTES + 1                                       # хвост не читался
    big = Stream([b"x" * 200, b"x" * 57, b"x" * 70000])                                # куски разной длины
    assert asyncio.run(_read_capped(big, WEB_REPORT_MAX_BYTES)) is None and big.read == 2
    declared = Stream([b"x"]); declared.headers = {"content-length": str(WEB_REPORT_MAX_BYTES + 1)}
    assert asyncio.run(_read_capped(declared, WEB_REPORT_MAX_BYTES)) is None and declared.read == 0   # без чтения


def test_health_web_front_only(tmp_path):                                            # Д5-R31, как test_no_route_…_in_any_zone
    app, _ = make_gateway(tmp_path)
    with TestClient(app, base_url="http://172.31.6.2:8000") as c:                    # адрес core в сети места 06
        r = c.get("/health/web")
        assert (r.status_code, r.json()["name"]) == (404, "NotFound") and "allow" not in r.headers
        r = c.post(REPORT, json={"kind": "ui_crash"}, headers={"Origin": ORIGIN})
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")
        r = c.post(REPORT, content=b"x" * 70000, headers={"Origin": ORIGIN, **JSON_TYPE})
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")                 # предел тела вне зоны — как нет ручки
    front = TestClient(app, base_url="http://172.31.250.34:8000")                    # адрес core в pcbk-front
    assert front.get("/health/web").status_code == 200                               # положительный контроль
    for method in ("POST", "PUT", "DELETE", "PROPFIND"):                              # Н1: отказ — через реестр, вид OpenCode
        r = front.request(method, "/health/web")
        assert (r.status_code, r.json()) == (404, {"name": "NotFound", "data": {"message": "Не найдено"}}), method
        assert "allow" not in r.headers, method


def test_web_report_needs_configured_gateway(tmp_path):                             # ошибка настройки — 503, как у ручек входа
    app, _ = make_gateway(tmp_path, config_error="GW_ORIGIN не задан")
    with TestClient(app, base_url=BASE) as c:
        r = c.post(REPORT, content=b'{"kind":"ui_crash"}', headers={"origin": ORIGIN, **JSON_TYPE})
        assert (r.status_code, r.json()["name"]) == (503, "Unavailable")
        assert c.get("/health/web").json() == {f"{k}_1h": 0 for k in REPORT_KINDS}   # счётчики читаются и так


def test_web_reports_counts():                                                       # без ввода-вывода, часы — извне
    w = WebReports()
    assert w.counts_1h(0.0) == {"boot_failed_1h": 0, "ui_crash_1h": 0, "turn_silent_1h": 0, "stream_down_1h": 0}
    assert w.report("stu-03", "ui_crash", 100.0) is True
    assert w.report("stu-03", "ui_crash", 159.9) is False                             # та же пара — раз в минуту
    assert w.report("stu-03", "turn_silent", 110.0) is True                           # другой вид — своя пара
    assert w.report("stu-05", "ui_crash", 120.0) is True                              # другой логин — своя пара
    assert w.report("stu-03", "ui_crash", 160.0) is True
    assert w.counts_1h(160.0) == {"boot_failed_1h": 0, "ui_crash_1h": 3, "turn_silent_1h": 1, "stream_down_1h": 0}
    assert w.counts_1h(100.0 + 3600.0)["ui_crash_1h"] == 2                            # первый вышел из окна
    assert w.counts_1h(160.0 + 3600.0) == {f"{k}_1h": 0 for k in REPORT_KINDS}
    with pytest.raises(ValueError):
        w.report("stu-03", "other", 200.0)                                            # опечатка в виде — не молча
