"""Калькуляторы страницы (Д9а, Д9-R87): POST /pcbk/api/calc/<tool> — тот же DataService, что у /api/data/ и MCP.

Место — только из учётки сессии (caller_of), канал — web; отказы шлюза — JSON вида OpenCode в порядке R87: зона →
настройка → сессия → источник → инструмент → преподаватель → служба → тело → аргументы. Служба — make_service на
двойнике историана (core.db службы — в tmp_path, I1), шлюз — make_gateway(tmp_path / "gw", calc=svc): у них разные
core.db."""
import asyncio
import re
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from helpers import (BASE, FRONT, LLM_ENV, ORIGIN, PLACE_NET, REPO, Gw, make_gateway, make_service, park_reload_worker,
                     run_python)
from pcbk_core.gateway.api import GW_MESSAGES
from pcbk_core.gateway.calc_api import CALC_DOWN_TEXT, CALC_PREFIX, CALC_TOOLS, TEACHER_CALC_TEXT, caller_of
from pcbk_core.main import build_roles
from pcbk_core.settings import Settings

BODY = {"tags": ["20FAKE_001_PV"]}


@pytest.fixture
def gwc(tmp_path):
    svc, _ = make_service(tmp_path)                                               # I1: core.db службы — в tmp_path
    app, role = make_gateway(tmp_path / "gw", calc=svc)
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT):
        g = Gw(app, role)
        g.svc = svc
        yield g


def post(c, tool, body, origin=ORIGIN):
    return c.post(CALC_PREFIX + tool, json=body, headers={"Origin": origin})


def test_calc_route_answers_from_service(gwc):                                    # Review Focus 4
    r = post(gwc.as_("stu-03"), "tag_period", {**BODY, "period": "prev_shift"})
    assert r.status_code == 200 and (r.json()["tool"], r.json()["items"][0]["avg"]) == ("tag_period", 12.4)
    e = gwc.svc.role.events.recent()[0]
    assert (e.caller, e.channel, e.tool) == ("student-03", "web", "tag_period")
    assert post(gwc.as_("stu-03"), "tag_series", {**BODY, "period": "last_8h", "points": 3}).json()["points"] == 3


def test_calc_route_refusals(gwc):                                                # Review Focus 4
    before = len(gwc.svc.role.events.recent())                                    # ревью А4, m-1
    assert post(gwc.anon(), "tag_now", BODY).status_code == 401
    r = post(gwc.as_("teach"), "tag_now", BODY)
    assert (r.status_code, r.json()) == (403, {"name": "Forbidden", "data": {"message": TEACHER_CALC_TEXT}})
    s = gwc.as_("stu-03")
    assert post(s, "tag_now", BODY, origin="https://evil.test").status_code == 403
    assert post(s, "save_agent", BODY).status_code == 404
    assert post(gwc.as_("stu-03", client=PLACE_NET), "tag_now", BODY).status_code == 404     # вне pcbk-front
    assert len(gwc.svc.role.events.recent()) == before          # m-1: на отказ шлюза службу не зовут — ни события
    bad = s.post(CALC_PREFIX + "tag_now", content=b"{", headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    assert (bad.status_code, bad.json()["name"]) == (400, "BadRequest")
    r = post(s, "tag_series", {**BODY, "period": "last_8h", "points": 1})
    assert (r.status_code, r.json()["status"]) == (200, "refused") and "points" in r.json()["message"]
    e = gwc.svc.role.events.recent()[0]
    assert (e.caller, e.channel, e.outcome) == ("student-03", "web", "refused")
    assert post(s, "tag_now", {**BODY, "place": 5}).status_code == 200          # лишний ключ места не действует
    assert gwc.svc.role.events.recent()[0].caller == "student-03"


def test_calc_route_without_service(tmp_path):
    app, role = make_gateway(tmp_path)
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT):
        r = post(Gw(app, role).as_("stu-03"), "tag_now", BODY)
    assert (r.status_code, r.json()["data"]["message"]) == (503, CALC_DOWN_TEXT)


def test_build_roles_wires_calc(tmp_path):                       # как test_gateway_settings_error_keeps_core
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    roles = {r.name: r for r in build_roles(settings, LLM_ENV)}
    assert roles["gateway"].calc is roles["data"].service


# --- сверх брифа ---

def test_calc_contract_texts():                                                  # R87 дословно
    assert CALC_PREFIX == "/pcbk/api/calc/"
    assert CALC_TOOLS == ("catalog_search", "tag_now", "tag_period", "tag_series", "calc_runtime")
    assert TEACHER_CALC_TEXT == "Калькуляторы для преподавателя — в следующих выпусках стенда"
    assert CALC_DOWN_TEXT == "Калькуляторы недоступны — служба данных не подключена"
    assert (caller_of(3), caller_of(10)) == ("student-03", "student-10")


def test_calc_route_place_from_session_only(gwc):                               # Review Focus 4: место — только учётка
    for login, caller in (("stu-03", "student-03"), ("stu-05", "student-05")):
        r = gwc.as_(login).post(CALC_PREFIX + "tag_now?place=01", json={**BODY, "place": 1, "caller": "student-01"},
                                headers={"Origin": ORIGIN, "X-Pcbk-Place": "01"})
        assert r.status_code == 200, login
        assert gwc.svc.role.events.recent()[0].caller == caller, login


def test_calc_route_every_tool_through_service(gwc):                             # пять инструментов — пять событий web
    s = gwc.as_("stu-03")
    bodies = {"catalog_search": {"query": "FAKE", "limit": 5}, "tag_now": BODY,
              "tag_period": {**BODY, "period": "prev_shift"}, "tag_series": {**BODY, "period": "last_8h"},
              "calc_runtime": {"tags": ["25FAKE_007_CLS"], "period": "prev_shift"}}
    for tool in CALC_TOOLS:
        r = post(s, tool, bodies[tool])
        assert (r.status_code, r.json()["tool"]) == (200, tool), (tool, r.text[:200])
        e = gwc.svc.role.events.recent()[0]
        assert (e.caller, e.channel, e.tool) == ("student-03", "web", tool)
    assert post(s, "tag_series", {**BODY, "period": "last_8h"}).json()["points"] == 24    # умолчание модели


def test_calc_route_body_not_object(gwc):                                        # R87, шаг 8: 400 bad_body без события
    s = gwc.as_("stu-03")
    before = len(gwc.svc.role.events.recent())
    for raw in (b"[]", b'"x"', b"7", b"null", b"\xff", b"", b"[" * 30000 + b"]" * 30000):   # ревью А4, m-3: глубина
        r = s.post(CALC_PREFIX + "tag_now", content=raw, headers={"Origin": ORIGIN, "Content-Type": "application/json"})
        assert (r.status_code, r.json()) == (400, {"name": "BadRequest",
                                                   "data": {"message": GW_MESSAGES["bad_body"]}}), raw
    assert len(gwc.svc.role.events.recent()) == before


def test_calc_route_own_period_and_limit_same_as_service(gwc):    # ревью А7, I-2 и А4, m-2: свой период и limit
    """Свой закрытый период (start, end), точки ряда и limit поиска доходят до службы как есть: ответ шлюза (кроме
    cache) равен ответу службы на тех же аргументах; перестановка start и end или потерянный limit — красный."""
    s = gwc.as_("stu-03")
    own = {"start": "2026-10-01T04:00", "end": "2026-10-01T10:00"}
    svc, caller = gwc.svc, "student-03"
    cases = (("tag_period", {**BODY, **own}, lambda: svc.tag_period(caller, "http", BODY["tags"], None, **own)),
             ("tag_series", {**BODY, **own, "points": 5},
              lambda: svc.tag_series(caller, "http", BODY["tags"], None, **own, points=5)),
             ("calc_runtime", {"tags": ["25FAKE_007_CLS"], **own},
              lambda: svc.calc_runtime(caller, "http", ["25FAKE_007_CLS"], None, **own)),
             ("catalog_search", {"query": "FAKE", "limit": 2}, lambda: svc.catalog_search(caller, "http", "FAKE", 2)))
    drop = lambda d: {k: v for k, v in d.items() if k != "cache"}
    for tool, body, direct in cases:
        page = post(s, tool, body).json()
        assert page["status"] == "ok", (tool, page)
        assert drop(page) == drop(asyncio.run(direct())), tool
    assert len(page["matches"]) == 2 and page["total"] > 2                       # limit дошёл, а не умолчание
    assert gwc.svc.role.events.recent()[1].channel == "web"


def test_calc_route_student_role_only(gwc):                     # итоговое ревью Д9а, m-5: роль — явно, не «нет места»
    """Связь «студент ↔ место» держит только AccountStore.add: учётка преподавателя с местом и учётка чужой роли
    (правка базы мимо CHECK) — 403 TEACHER_CALC_TEXT без вызова службы, как у прокси."""
    with closing(sqlite3.connect(gwc.role.store.path)) as db, db:
        db.execute("UPDATE gw_accounts SET place = 7 WHERE login = 'teach'")
        db.execute("PRAGMA ignore_check_constraints = ON")
        db.execute("INSERT INTO gw_accounts (login, role, place, pw_hash, disabled, created_at) SELECT 'oper',"
                   " 'operator', 8, pw_hash, 0, created_at FROM gw_accounts WHERE login = 'stu-05'")
    before = len(gwc.svc.role.events.recent())
    for login in ("teach", "oper"):
        r = post(gwc.as_(login), "tag_now", BODY)
        assert (r.status_code, r.json()["data"]["message"]) == (403, TEACHER_CALC_TEXT), login
    assert len(gwc.svc.role.events.recent()) == before


def test_calc_route_bad_args_refused_with_web_event(gwc):        # R87, шаг 9: тело не идёт ни в ответ, ни в журнал
    s = gwc.as_("stu-03")
    r = post(s, "tag_now", {"tags": []})
    assert (r.status_code, r.json()["status"], r.json()["tool"]) == (200, "refused", "tag_now")
    assert "tags" in r.json()["message"]
    r = post(s, "catalog_search", {"query": "x", "limit": 999, "password": "секрет-тела"})
    assert (r.status_code, r.json()["status"]) == (200, "refused") and "limit" in r.json()["message"]
    assert "секрет-тела" not in r.text
    e = gwc.svc.role.events.recent()[0]
    assert (e.caller, e.channel, e.tool, e.outcome, e.requested) == ("student-03", "web", "catalog_search", "refused",
                                                                     ("x",))


def test_calc_route_order_tool_before_teacher_and_service(gwc, tmp_path):       # R87: шаги 5 → 6 → 7
    assert post(gwc.as_("teach"), "save_agent", BODY).status_code == 404          # инструмент — раньше преподавателя
    app, role = make_gateway(tmp_path / "nosvc")
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT):
        g = Gw(app, role)
        assert post(g.as_("stu-03"), "save_agent", BODY).status_code == 404       # инструмент — раньше службы
        r = post(g.as_("teach"), "tag_now", BODY)                                 # преподаватель — раньше службы
        assert (r.status_code, r.json()["data"]["message"]) == (403, TEACHER_CALC_TEXT)
        bad = g.as_("stu-03").post(CALC_PREFIX + "tag_now", content=b"{", headers={"Origin": ORIGIN})
        assert (bad.status_code, bad.json()["data"]["message"]) == (503, CALC_DOWN_TEXT)   # служба — раньше тела


def test_calc_route_session_before_origin_and_zone_first(gwc):                  # R87: шаги 1 → 3 → 4
    r = post(gwc.anon(), "tag_now", BODY, origin="https://evil.test")             # без учётки — 401, не 403
    assert (r.status_code, r.json()["data"]["message"]) == (401, GW_MESSAGES["need_login"])
    assert gwc.role.stats.count_1h("origin_refused") == 0
    assert post(gwc.anon(PLACE_NET), "tag_now", BODY).status_code == 404          # зона — раньше сессии
    gwc.role.config_error = "GW_ORIGIN не задан"                                  # настройка — раньше сессии
    r = post(gwc.anon(), "tag_now", BODY)
    assert (r.status_code, r.json()["data"]["message"]) == (503, "Шлюз не настроен: GW_ORIGIN не задан")
    assert post(gwc.anon(PLACE_NET), "tag_now", BODY).status_code == 404          # зона — раньше настройки


def test_calc_route_other_methods_and_subpaths_404(gwc):                         # обработчик шлюза: без Allow
    s = gwc.as_("stu-03")
    for method in ("GET", "PUT", "DELETE", "PATCH"):
        r = s.request(method, CALC_PREFIX + "tag_now", headers={"Origin": ORIGIN})
        assert (r.status_code, r.json()["name"]) == (404, "NotFound"), method
        assert "allow" not in r.headers, method
    for path in (CALC_PREFIX + "tag_now/x", CALC_PREFIX, CALC_PREFIX.rstrip("/")):   # подпуть, пустое имя, без «/»
        r = s.post(path, json=BODY, headers={"Origin": ORIGIN})
        assert (r.status_code, r.json()["name"]) == (404, "NotFound"), path


def test_admin_command_does_not_import_calc():                   # m12: команда администратора без mcp и pytds
    code = ("import sys, pcbk_core.gateway.accounts; "
            "print(sorted(m for m in ('mcp', 'pytds', 'pcbk_core.data', 'pcbk_core.gateway.calc_api') "
            "if m in sys.modules))")
    assert run_python(code).strip() == "[]"


# --- сверка с вебом: набор инструментов шлюза = тип CalcTool (web/src/api.ts) ---

def web_calc_tools(api_ts: str) -> list[str]:
    """Значения типа CalcTool из web/src/api.ts: `export type CalcTool = "a" | "b";` — только строки через «|»
    (ведущая «|» многострочной записи допустима); иное в типе — AssertionError, а не тихий пропуск."""
    found = re.search(r"export type CalcTool\s*=\s*([^;]*);", api_ts)
    assert found is not None, "CalcTool не найден в web/src/api.ts"
    union = found.group(1)
    assert re.fullmatch(r'\s*(\|\s*)?"[a-z_]+"(\s*\|\s*"[a-z_]+")*\s*', union), f"CalcTool не того вида: {union!r}"
    return re.findall(r'"([a-z_]+)"', union)


def test_web_calc_tools_parser_self_check():                                     # положительный контроль разборщика
    assert web_calc_tools('export type X = "a";\nexport type CalcTool = "b_c" | "d";\n') == ["b_c", "d"]
    assert web_calc_tools('export type CalcTool =\n  | "b_c"\n  | "d";\n') == ["b_c", "d"]
    for bad in ('export type CalcTool = "a" | string;', 'export type Other = "a";'):
        with pytest.raises(AssertionError):
            web_calc_tools(bad)


def test_calc_tools_match_web():                                  # решение контроллера: расхождение — красный
    web = REPO / "web"
    if not web.is_dir():
        pytest.skip("в копии нет web/ — сверка с вебом идёт в копии с web")
    tools = web_calc_tools((web / "src/api.ts").read_text("utf-8"))
    assert len(tools) == len(set(tools)) and set(tools) == set(CALC_TOOLS), (tools, CALC_TOOLS)
