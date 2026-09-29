"""MCP службы данных: те же три инструмента, что HTTP, поверх одной DataService.

Токен → ошибка настройки → аргументы → служба; /mcp — точный маршрут, GET — 405, Host любой.
"""
import json
import logging
import urllib.error
import urllib.request

import pytest

from helpers import TOKEN, http_post, make_role, mcp_session, run_python, serve_role
from pcbk_core.data import CONFIG_ERRORS
from pcbk_core.data.mcp_server import TOOL_DESCRIPTIONS
from pcbk_core.data.periods import PRESETS
from pcbk_core.logs import setup_logging

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
ACCEPT = "application/json, text/event-stream"
# мимо прокси окружения: запросы идут на 127.0.0.1
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def raw(url, method="POST", body=INIT, **headers):
    req = urllib.request.Request(url, json.dumps(body).encode() if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json", "Accept": ACCEPT, **headers})
    try:
        with OPENER.open(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        with e:
            return e.code


@pytest.mark.anyio
async def test_mcp_lists_three_readonly_tools(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == {"catalog_search", "tag_now", "tag_period"}
    assert all(t.annotations.readOnlyHint for t in tools.values())
    tags = tools["tag_now"].inputSchema["properties"]["tags"]
    assert (tags["type"], tags["maxItems"], tags["items"]["maxLength"]) == ("array", 16, 128)
    assert tools["tag_now"].description == TOOL_DESCRIPTIONS["tag_now"]


@pytest.mark.anyio
async def test_mcp_and_http_give_same_answer(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        sc = (await s.call_tool("tag_now", {"tags": ["20FAKE_001_PV"]})).structuredContent
    code, body = http_post(core_server.url + "/api/data/tag_now", {"tags": ["20FAKE_001_PV"]}, TOKEN)
    drop = lambda d: {k: v for k, v in d.items() if k not in ("cache", "asof")}
    assert code == 200 and sc is not None and drop(sc) == drop(json.loads(body))


@pytest.mark.anyio
async def test_mcp_bad_arg_types_are_refused_events(core_server):              # Review Focus 3
    bad = [("tag_now", {"tags": "20FAKE_001_PV"}), ("tag_now", {}),
           ("tag_period", {"tags": ["20FAKE_001_PV"], "period": "last_year"}),
           ("tag_now", {"tags": [f"20FAKE_{i:03d}_PV" for i in range(17)]})]
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        for tool, args in bad:
            sc = (await s.call_tool(tool, args)).structuredContent
            assert sc["status"] == "refused" and "неверные аргументы" in sc["message"], args
            e = core_server.role.events.recent(channel="mcp")[0]
            assert (e.outcome, e.channel, e.caller, e.sent) == ("refused", "mcp", "student-01", ())


def test_mcp_auth_host_and_get(core_server):                                    # Review Focus 5
    url = core_server.url + "/mcp"
    assert raw(url) == 401 and raw(url, Authorization="Bearer nope") == 401
    for host in ("core:8000", "172.31.1.2:8000"):
        assert raw(url, Authorization=f"Bearer {TOKEN}", Host=host) == 200
    assert raw(url, method="GET", Authorization=f"Bearer {TOKEN}") == 405
    assert raw(url + "/", Authorization=f"Bearer {TOKEN}") == 404                  # точный маршрут


@pytest.mark.anyio
async def test_lifespan_without_app_runs(tmp_path):                             # Review Focus 5
    role, _ = make_role(db_dir=tmp_path)
    async with role.lifespan():                       # MCP построен в конструкторе, run() — один раз
        pass
    assert not logging.getLogger("pytds").isEnabledFor(logging.INFO)
    assert role.mcp.settings.log_level == "WARNING"   # под pytest корень уже настроен — проверяем настройку


@pytest.mark.anyio
async def test_mcp_with_config_error_answers_not_500(tmp_path):                 # F8, Review Focus 5
    role, fake = make_role(db_dir=tmp_path, config_error=CONFIG_ERRORS["whitelist_unreadable"])
    with serve_role(role, wait_catalog=False) as url:
        async with mcp_session(url + "/mcp", TOKEN) as s:            # initialize проходит: run() уже идёт
            sc = (await s.call_tool("tag_now", {"tags": ["20FAKE_001_PV"]})).structuredContent
            bad = (await s.call_tool("tag_now", {"tags": "20FAKE_001_PV"})).structuredContent
    assert (sc["status"], sc["message"]) == ("unavailable", "белый список не читается")
    assert bad["status"] == "unavailable" and fake.calls == []        # и раньше разбора аргументов


# --- сверх брифа ---

@pytest.mark.anyio
async def test_mcp_three_tools_share_one_service(core_server):
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
        search = (await s.call_tool("catalog_search", {"query": "расход"})).structuredContent
        period = (await s.call_tool("tag_period", {"tags": ["20FAKE_001_PV"], "period": "prev_shift",
                                                   "start": None, "end": None})).structuredContent
        now = (await s.call_tool("tag_now", {"tags": ["20FAKE_004_PV"]})).structuredContent
        refused = (await s.call_tool("tag_now", {"tags": ["16FAKE_009_PV"]})).structuredContent
    _, body = http_post(core_server.url + "/api/data/tag_now", {"tags": ["20FAKE_004_PV"]}, TOKEN)
    assert (search["tool"], search["status"]) == ("catalog_search", "ok")
    assert period["items"][0]["status"] == "ok"                       # null — «не задан»
    assert (now["cache"], json.loads(body)["cache"]) == ("miss", "hit")    # кэш один: служба одна
    assert refused["status"] == "refused"
    assert [e.caller for e in core_server.role.events.recent(channel="mcp")] == ["student-01"] * 4
    period_schema = tools["tag_period"].inputSchema["properties"]
    assert period_schema["period"]["enum"] == list(PRESETS) and period_schema["start"]["type"] == "string"
    assert all(t.annotations.openWorldHint is False for t in tools.values())
    assert {t.name: t.description for t in tools.values()} == TOOL_DESCRIPTIONS


@pytest.mark.anyio
async def test_mcp_arg_reasons_are_russian(core_server):
    cases = [("tag_now", {"tags": "20FAKE_001_PV"}, "tags: нужен список"),
             ("tag_now", {}, "tags: обязательное поле"),
             ("tag_now", {"tags": ["x" * 129]}, "tags.0: длиннее 128 знаков"),
             ("tag_period", {"tags": ["20FAKE_001_PV"], "period": "last_year"}, "period: неизвестное значение"),
             ("catalog_search", {"query": "расход", "limit": 51}, "limit: не больше 50"),
             ("catalog_search", {"query": ["расход"]}, "query: нужна строка")]
    async with mcp_session(core_server.url + "/mcp", TOKEN) as s:
        for tool, args, reason in cases:
            result = await s.call_tool(tool, args)
            assert not result.isError and result.structuredContent["message"] == "неверные аргументы: " + reason
            e = core_server.role.events.recent(channel="mcp")[0]
            assert (e.tool, e.outcome, e.summary) == (tool, "refused", "args: " + reason)
    assert core_server.role.events.recent(channel="mcp")[-1].requested == ("20FAKE_001_PV",)   # сырой ввод


def test_mcp_401_is_logged_without_event(core_server, capfd, restore_logging):  # Review Focus 3
    setup_logging()
    url = core_server.url + "/mcp"
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "tag_now", "arguments": {"tags": ["20FAKE_001_PV"]}}}
    assert raw(url, body=call) == 401 and raw(url, body=call, Authorization="Bearer nope") == 401
    assert raw(url, method="DELETE", Authorization=f"Bearer {TOKEN}") == 405
    err = capfd.readouterr().err
    assert err.count("outcome=unauthorized channel=mcp") == 2 and "nope" not in err
    assert core_server.role.events.count(channel="mcp") == 0


@pytest.mark.anyio
async def test_mcp_call_outside_http_request_has_no_caller(tmp_path):
    role, fake = make_role(db_dir=tmp_path)
    _, sc = await role.mcp.call_tool("tag_now", {"tags": ["20FAKE_001_PV"]})    # без запроса — без заголовка
    assert (sc["status"], sc["message"]) == ("refused", "нужен токен")
    assert role.events.count(channel="mcp") == 0 and fake.calls == []


def test_mcp_keeps_root_logger_at_warning():
    """В свежем процессе корень не настроен: FastMCP настраивает его сам — не ниже WARNING."""
    out = run_python("import logging\n"
                     "from pcbk_core.data import DataRole\n"
                     "from pcbk_core.settings import Settings\n"
                     "DataRole(Settings(DB_PATH='/nonexistent/core.db'), lambda s, t: [], frozenset())\n"
                     "print(logging.getLogger().level, logging.getLogger('pytds').isEnabledFor(logging.INFO))")
    assert out.split() == [str(logging.WARNING), "False"]


def test_mcp_route_is_built_once(tmp_path):
    role, _ = make_role(db_dir=tmp_path)
    assert role.mounts() == [("/mcp", role.mcp_app)] and role.mounts()[0][1] is role.mcp_app
    assert not (tmp_path / "core.db").exists()                          # сборка MCP — без ввода-вывода (F4)
