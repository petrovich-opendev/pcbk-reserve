"""HTTP службы данных: токен → ошибка настройки → инструмент; 401, 422, 413 и 503 при ошибке настройки."""
import json

from fastapi.testclient import TestClient

from helpers import SETTINGS, TOKEN, make_app, make_role, wait_until
from pcbk_core.app import create_app
from pcbk_core.data import CONFIG_ERRORS
from pcbk_core.data.http_api import MAX_BODY_BYTES, BodyLimit
from pcbk_core.logs import setup_logging


def test_401_is_logged(tmp_path, capfd, restore_logging):                       # Review Focus 3
    setup_logging()
    app, _ = make_app(tmp_path)
    with TestClient(app) as c:
        assert c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}).status_code == 401
        assert c.post("/api/data/tag_now", json={"tags": ["x"]}, headers={"Authorization": "Bearer nope"}).status_code == 401
    err = capfd.readouterr().err
    assert err.count("outcome=unauthorized channel=http") == 2 and "nope" not in err


def test_http_422_is_refused_event(tmp_path):                                   # Review Focus 3
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        r = c.post("/api/data/tag_now", json={"tags": ["x"] * 17}, headers=auth)
        assert r.status_code == 422 and r.json()["status"] == "refused" and "неверные аргументы" in r.json()["message"]
        e = role.events.recent(channel="http")[0]
        assert (e.outcome, e.channel, e.tool) == ("refused", "http", "tag_now")
        assert c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}, headers=auth).json()["status"] == "ok"


def test_bad_json_without_token_is_401(tmp_path):                               # Review Focus 3
    app, role = make_app(tmp_path)
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        before = role.events.count(channel="http")
        r = c.post("/api/data/tag_now", content=b"{bad", headers={"Content-Type": "application/json"})
        assert r.status_code == 401 and role.events.count(channel="http") == before   # мусора в журнале нет


def test_chunked_body_over_limit_is_413(tmp_path, capfd, restore_logging):      # Review Focus 3
    setup_logging()
    app, _ = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    with TestClient(app) as c:
        chunks = (b"x" * 10_000 for _ in range(7))          # без Content-Length
        assert c.post("/api/data/tag_now", content=chunks, headers=auth).status_code == 413
        assert c.post("/api/data/tag_now", content=b"x" * 70_000, headers=auth).status_code == 413   # по длине
        assert c.post("/mcp", content=b"x" * 70_000, headers=auth).status_code == 413   # до маршрутизации
    assert "outcome=too_large" in capfd.readouterr().err


def test_config_error_http_is_503_with_reason(tmp_path):                       # F5, Review Focus 6
    role, fake = make_role(db_dir=tmp_path, config_error=CONFIG_ERRORS["whitelist_unreadable"])
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(SETTINGS, [role])) as c:
        r = c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]}, headers=auth)
        assert (r.status_code, r.json()["status"], r.json()["message"]) == (503, "unavailable", "белый список не читается")
        assert c.post("/api/data/tag_now", json={"tags": ["x"] * 17}, headers=auth).status_code == 503   # и до 422
        assert c.post("/api/data/tag_now", json={"tags": ["x"]}).status_code == 401                        # токен — раньше
    assert fake.calls == []


# --- сверх брифа ---

def test_three_tools_answer_200(tmp_path):
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"bearer  {TOKEN}"}                     # схема без учёта регистра, пробелы
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        search = c.post("/api/data/catalog_search", json={"query": "расход"}, headers=auth)
        period = c.post("/api/data/tag_period", json={"tags": ["20FAKE_001_PV"], "period": "prev_shift"},
                        headers=auth)
        refused = c.post("/api/data/tag_now", json={"tags": ["16FAKE_009_PV"]}, headers=auth)
    assert (search.status_code, search.json()["tool"], search.json()["status"]) == (200, "catalog_search", "ok")
    assert (period.status_code, period.json()["items"][0]["status"]) == (200, "ok")
    assert (refused.status_code, refused.json()["status"]) == (200, "refused")      # отказ инструмента — тоже 200
    assert [e.caller for e in role.events.recent(channel="http")] == ["student-01"] * 3


def test_422_reasons_are_russian_fields(tmp_path):
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    cases = [("tag_period", {"tags": ["20FAKE_001_PV"], "period": "last_year"}, "period: неизвестное значение"),
             ("tag_now", {"tags": "20FAKE_001_PV"}, "tags: нужен список"),
             ("tag_now", {}, "tags: обязательное поле"),
             ("tag_now", {"tags": ["x" * 129]}, "tags.0: длиннее 128 знаков"),
             ("catalog_search", {"query": "расход", "limit": 51}, "limit: не больше 50")]
    with TestClient(app) as c:
        for tool, body, reason in cases:
            r = c.post(f"/api/data/{tool}", json=body, headers=auth)
            assert (r.status_code, r.json()["message"]) == (422, "неверные аргументы: " + reason), body
            e = role.events.recent(channel="http")[0]
            assert (e.tool, e.outcome, e.sent) == (tool, "refused", ())
        bad = c.post("/api/data/tag_now", content=b"{bad", headers={**auth, "Content-Type": "application/json"})
    assert (bad.status_code, bad.json()["message"]) == (422, "неверные аргументы: тело: не JSON")
    assert role.events.recent(channel="http")[0].requested == ("{bad",)


def test_body_exactly_at_limit_passes(tmp_path):
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    head = json.dumps({"tags": ["20FAKE_001_PV"], "pad": ""})[:-2]
    body = (head + "x" * (MAX_BODY_BYTES - len(head) - 2) + '"}').encode()
    assert len(body) == MAX_BODY_BYTES
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        assert c.post("/api/data/tag_now", content=body, headers=auth).status_code == 200
        over = c.post("/api/data/tag_now", content=body + b" ", headers=auth)
        assert (over.status_code, over.json()) == (413, {"detail": "тело запроса больше 64 КиБ"})
        assert c.get("/healthz/data").status_code == 200                    # GET без тела проходит звено


def test_unknown_data_path_and_get_are_not_tools(tmp_path):
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        assert c.post("/api/data/tag_history", json={"tags": ["x"]}, headers=auth).status_code == 404
        assert c.get("/api/data/tag_now", headers=auth).status_code == 405
        assert c.post("/api/data/tag_now/", json={"tags": ["x"]}, headers=auth).status_code == 404
    assert role.events.count(channel="http") == 0


async def _echo(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_413_text_follows_limit():                                               # M5
    for limit, text in ((2048, "2 КиБ"), (100, "100 байт")):
        c = TestClient(BodyLimit(_echo, max_bytes=limit))                 # без lifespan: звено без приложения
        assert c.post("/x", content=b"x" * limit).status_code == 200
        r = c.post("/x", content=b"x" * (limit + 1))
        assert (r.status_code, r.json()) == (413, {"detail": f"тело запроса больше {text}"})
