"""HTTP службы данных: токен → ошибка настройки → инструмент; 401, 422, 413 и 503 при ошибке настройки."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from helpers import (LLM_TOKEN, SETTINGS, TOKEN, chat_body, gateway_role, make_app, make_llm_role, make_role,
                     wait_until, wait_until_async)
from pcbk_core.app import create_app
from pcbk_core.data import CONFIG_ERRORS
from pcbk_core.data.http_api import (MAX_BODY_BYTES, BodyLimit, CalcRuntimeArgs, TagNowArgs, TagSeriesArgs,
                                     validation_reason)
from pcbk_core.data.service import _points_problem, _tags_problem
from pcbk_core.data.sql import SERIES_MAX_TAGS
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


# --- Ш25: токен места — только из его сети (защита в глубину) ---

@pytest.mark.anyio
async def test_place_token_only_from_its_network(tmp_path):
    # адрес источника — scope["client"]; TestClient(client=…) на каждый адрес заходил бы в lifespan заново, а
    # run() сессий MCP роли «данные» SDK даёт один раз — клиенты httpx на одном lifespan
    app, role = make_app(tmp_path)
    auth, body = {"Authorization": f"Bearer {TOKEN}"}, {"tags": ["20FAKE_001_PV"]}   # токен student-01
    async with app.router.lifespan_context(app):
        await wait_until_async(lambda: role.catalog.loaded)
        for client, code in ((("172.31.1.3", 40000), 200), (("172.31.2.3", 40000), 401),  # своё место / чужое
                             (("172.31.250.81", 40000), 200), (("10.9.9.9", 40000), 401)):  # хост в pcbk-egress / прочие
            async with from_address(app, client) as c:
                before = len(role.events.recent(channel="http"))
                assert (await c.post("/api/data/tag_now", json=body, headers=auth)).status_code == code, client
                # как чужой токен — без события; положительный контроль: у 200 событие есть (ревью B, M5)
                assert len(role.events.recent(channel="http")) == before + (code == 200), client


def from_address(app, client: tuple[str, int]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=client), base_url="http://testserver")


def shape(r):
    try:
        body = r.json()
    except ValueError:
        return r.status_code, r.headers.get("content-type"), r.text
    return r.status_code, r.headers.get("content-type"), sorted(body) if isinstance(body, dict) else type(body).__name__


def test_error_shapes_unchanged_with_gateway(tmp_path, fake_or):                         # чужие ответы не тронуты
    def responses(extra):
        (tmp_path / f"d{len(extra)}").mkdir()                                          # make_app каталог не создаёт
        llm = make_llm_role(tmp_path / f"l{len(extra)}", fake_or)                     # П3: без сети (Ш7)
        app, _ = make_app(tmp_path / f"d{len(extra)}", extra_roles=[llm, *extra])
        with TestClient(app) as c:
            return [shape(r) for r in (
                c.post("/api/data/tag_now", json={"tags": "не список"}, headers={"Authorization": f"Bearer {TOKEN}"}),
                c.post("/api/data/tag_now", json={"tags": ["x"]}),                     # без токена
                c.post("/llm/v1/chat/completions", json=chat_body()),                   # без токена
                c.get("/nothing/here"),
                c.request("PUT", "/healthz"))]
    assert responses([]) == responses([gateway_role(tmp_path / "gw")])


# --- сверх брифа ---

@pytest.mark.anyio
async def test_place_token_network_checked_on_mcp_and_llm(tmp_path, fake_or):          # все места сверки (Ш25)
    """/mcp (звено mcp_auth и инструмент) и /llm/ — та же сверка сети. STU_NET шлюза не по умолчанию: транспорт
    MCP заменяет scope["app"] собой, и инструмент берёт STU_NET из scope, куда его кладёт mcp_auth."""
    llm = make_llm_role(tmp_path / "llm", fake_or)
    (tmp_path / "d").mkdir()
    app, role = make_app(tmp_path / "d", extra_roles=[llm, gateway_role(tmp_path / "gw", STU_NET="10.50")])
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json, text/event-stream"}
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "tag_now", "arguments": {"tags": ["20FAKE_001_PV"]}}}
    async with app.router.lifespan_context(app):
        await wait_until_async(lambda: role.catalog.loaded)
        for client, ok in ((("10.50.1.3", 40000), True), (("10.50.2.3", 40000), False),
                           (("172.31.1.3", 40000), False)):                             # сеть места 01 по умолчанию
            async with from_address(app, client) as c:
                r = await c.post("/mcp", json=call, headers=headers)
                assert r.status_code == (200 if ok else 401), client
                if ok:
                    assert r.json()["result"]["structuredContent"]["status"] == "ok"   # инструмент узнал вызывающего
                r = await c.post("/llm/v1/chat/completions", json=chat_body(),
                                 headers={"Authorization": f"Bearer {LLM_TOKEN}"})
                assert (r.status_code != 401) is ok, client


# Д9а, задача А2: калькуляторы по HTTP (Д9-R85)


def test_http_series_and_runtime(tmp_path):
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        r = c.post("/api/data/tag_series", json={"tags": ["20FAKE_001_PV"], "period": "last_8h", "points": 3},
                   headers=auth)
        assert r.status_code == 200 and r.json()["tool"] == "tag_series"
        r = c.post("/api/data/calc_runtime", json={"tags": ["25FAKE_007_CLS"], "period": "yesterday"}, headers=auth)
        assert r.status_code == 200 and r.json()["items"][0]["states"]
        r = c.post("/api/data/tag_series", json={"tags": ["20FAKE_001_PV"], "points": 1, "period": "last_8h"},
                   headers=auth)
        assert r.status_code == 422 and r.json()["status"] == "refused" and "points" in r.json()["message"]


# сверх брифа: пределы моделей, 422 с событием, точки по умолчанию, ошибка настройки — 503


def test_http_calculator_models(tmp_path):
    app, role = make_app(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    cases = [("tag_series", {"tags": [f"20FAKE_00{i}_PV" for i in range(5)], "period": "last_8h"},
              "tags: не больше 4 тегов за вызов"),
             ("tag_series", {"tags": ["20FAKE_001_PV"], "period": "last_8h", "points": 97}, "points: от 2 до 96"),
             ("tag_series", {"tags": ["20FAKE_001_PV"], "period": "last_8h", "points": "много"},
              "points: нужно целое число"),
             ("calc_runtime", {"tags": [f"25FAKE_{i:03d}_CLS" for i in range(17)], "period": "yesterday"},
              "tags: не больше 16 тегов за вызов"),
             ("calc_runtime", {"tags": ["25FAKE_007_CLS"], "period": "last_year"}, "period: неизвестное значение")]
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        for tool, body, reason in cases:
            r = c.post(f"/api/data/{tool}", json=body, headers=auth)
            assert (r.status_code, r.json()["message"]) == (422, "неверные аргументы: " + reason), body
            e = role.events.recent(channel="http")[0]
            assert (e.tool, e.outcome, e.sent, e.caller) == (tool, "refused", (), "student-01")
        r = c.post("/api/data/tag_series", json={"tags": ["20FAKE_001_PV"], "period": "last_8h"}, headers=auth)
        assert (r.status_code, r.json()["points"], r.json()["status"]) == (200, 24, "ok")
        unauthorized = c.post("/api/data/calc_runtime", json={"tags": ["25FAKE_007_CLS"], "period": "yesterday"})
        assert unauthorized.status_code == 401


def test_model_refusal_texts_same_as_service():                 # итоговая доводка Д9а, сомнение 2 исполнителя А2
    """Отказ модели (HTTP 422, MCP, вкладка) по тегам и точкам — тем же текстом, что у прямого вызова службы."""
    from pydantic import ValidationError
    cases = ((TagSeriesArgs, {"tags": ["A"], "points": 97}, _points_problem(97)),
             (TagSeriesArgs, {"tags": ["A"], "points": 1}, _points_problem(1)),
             (TagSeriesArgs, {"tags": ["A"] * 5}, _tags_problem(["A"] * 5, SERIES_MAX_TAGS)),
             (CalcRuntimeArgs, {"tags": ["A"] * 17}, _tags_problem(["A"] * 17)),
             (TagNowArgs, {"tags": []}, _tags_problem([])))
    for model, body, direct in cases:
        with pytest.raises(ValidationError) as exc:
            model.model_validate(body)
        assert direct and validation_reason(list(exc.value.errors())) == direct, body


def test_http_calculators_config_error_is_503(tmp_path):
    role, fake = make_role(db_dir=tmp_path, config_error=CONFIG_ERRORS["whitelist_unreadable"])
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(SETTINGS, [role])) as c:
        for tool, body in (("tag_series", {"tags": ["20FAKE_001_PV"], "period": "last_8h"}),
                           ("calc_runtime", {"tags": ["25FAKE_007_CLS"], "period": "yesterday"}),
                           ("tag_series", {"tags": ["20FAKE_001_PV"], "points": 1})):
            r = c.post(f"/api/data/{tool}", json=body, headers=auth)
            assert (r.status_code, r.json()["status"], r.json()["tool"]) == (503, "unavailable", tool), body
    assert fake.calls == []
