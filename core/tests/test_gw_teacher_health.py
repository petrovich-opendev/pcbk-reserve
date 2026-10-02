"""Наблюдение раньше функции (задача А1 Д10а, Д10-R96): счётчики кабинета TeacherStats за час, GET /health/teacher — ровно
13 ключей, только числа и флаги, только зона front, отказы — обработчиком шлюза из реестра; DRILL_TEACHER — в drill. До А2
долей и журнала действий нет: checked_at — null, shares_loaded и shares_ok — false, сумма — 0, store_ok — true, и строка
сторожа честно «неизвестно»."""
import pytest
from fastapi.testclient import TestClient

from helpers import FRONT, PLACE_NET, FakeMono, gateway_role, make_gateway, make_llm_role
from pcbk_core.gateway.stats import TEACHER_KINDS, TeacherStats
from pcbk_core.gateway.teacher_api import HEALTH_TEACHER_PATH
from pcbk_core.hourly import WINDOW_S
from pcbk_core.settings import LlmSettings

KEYS = {"checked_at", "shares_loaded", "shares_ok", "shares_drift", "shares_sum_usd", "total_usd", "store_ok",
        "reads_1h", "read_failed_1h", "changes_1h", "change_failed_1h", "role_refused_1h", "drill"}


def test_teacher_health_contract_before_shares(tmp_path):
    app, role = make_gateway(tmp_path)                                    # без LLM-прокси: долей ещё нет
    with TestClient(app, client=FRONT) as c:
        role.teacher_stats.event("read")
        role.teacher_stats.event("role_refused")
        body = c.get("/health/teacher").json()
    assert set(body) == KEYS
    assert (body["checked_at"], body["shares_loaded"], body["shares_ok"], body["shares_drift"], body["store_ok"],
            body["reads_1h"], body["role_refused_1h"], body["drill"]) == (None, False, False, 0, True, 1, 1, "")
    assert body["total_usd"] == 1000.0                                    # LlmSettings().LLM_TOTAL_USD


def test_teacher_health_front_only(tmp_path):
    app, _ = make_gateway(tmp_path)
    with TestClient(app, client=PLACE_NET) as c:                           # сеть места
        r = c.get("/health/teacher")
    assert (r.status_code, r.json()["name"]) == (404, "NotFound")


def test_teacher_health_drill(tmp_path):
    app, _ = make_gateway(tmp_path, DRILL_TEACHER="journal_down")          # поле GatewaySettings (m-7)
    with TestClient(app, client=FRONT) as c:
        assert c.get("/health/teacher").json()["drill"] == "journal_down"


# --- сверх брифа ---

def test_teacher_kinds_and_path():
    assert TEACHER_KINDS == {"read", "read_failed", "change", "change_failed", "role_refused"}
    assert HEALTH_TEACHER_PATH == "/health/teacher"
    assert len(KEYS) == 13
    counted = {"reads_1h": "read", "read_failed_1h": "read_failed", "changes_1h": "change",
               "change_failed_1h": "change_failed", "role_refused_1h": "role_refused"}
    assert set(counted) == {k for k in KEYS if k.endswith("_1h")} and set(counted.values()) == TEACHER_KINDS


def test_teacher_health_counts_each_kind_to_its_key(tmp_path):
    app, role = make_gateway(tmp_path)
    keys = {"read": "reads_1h", "read_failed": "read_failed_1h", "change": "changes_1h",
            "change_failed": "change_failed_1h", "role_refused": "role_refused_1h"}
    with TestClient(app, client=FRONT) as c:
        for i, kind in enumerate(sorted(TEACHER_KINDS), start=1):        # у каждого вида своё число
            for _ in range(i):
                role.teacher_stats.event(kind)
        body = c.get("/health/teacher").json()
    assert {kind: body[keys[kind]] for kind in TEACHER_KINDS} == \
           {kind: i for i, kind in enumerate(sorted(TEACHER_KINDS), start=1)}


def test_teacher_health_without_shares_is_honest_unknown(tmp_path):       # R96: без долей — «неизвестно», не «норма»
    app, role = make_gateway(tmp_path)
    assert role.llm is None                                               # шлюз без LLM-прокси (до А2 — всегда)
    with TestClient(app, client=FRONT) as c:
        body = c.get("/health/teacher").json()
    assert body == {"checked_at": None, "shares_loaded": False, "shares_ok": False, "shares_drift": 0,
                    "shares_sum_usd": 0, "total_usd": LlmSettings().LLM_TOTAL_USD, "store_ok": True, "reads_1h": 0,
                    "read_failed_1h": 0, "changes_1h": 0, "change_failed_1h": 0, "role_refused_1h": 0, "drill": ""}


def test_teacher_health_total_from_llm_role(tmp_path):                    # R96: LLM_TOTAL_USD роли llm, если она есть
    app, role = make_gateway(tmp_path / "gw")
    role.llm = make_llm_role(tmp_path / "llm", LLM_TOTAL_USD=250.0)
    with TestClient(app, client=FRONT) as c:
        assert c.get("/health/teacher").json()["total_usd"] == 250.0


def test_teacher_health_readable_with_config_error(tmp_path):             # как /health/agents: без configured
    app, role = make_gateway(tmp_path, config_error="GW_ORIGIN не задан")
    with TestClient(app, client=FRONT) as c:
        role.teacher_stats.event("role_refused")
        r = c.get("/health/teacher")
    assert (r.status_code, r.json()["role_refused_1h"], r.json()["checked_at"]) == (200, 1, None)


def test_teacher_health_other_methods_404_without_allow(tmp_path):        # отказ — через реестр, вид OpenCode
    app, _ = make_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        assert c.get("/health/teacher").status_code == 200                   # положительный контроль
        for method in ("POST", "PUT", "DELETE", "PROPFIND"):
            r = c.request(method, "/health/teacher")
            assert (r.status_code, r.json()) == (404, {"name": "NotFound", "data": {"message": "Не найдено"}}), method
            assert "allow" not in r.headers, method
    with TestClient(app, base_url="http://172.31.250.82:8000", client=("172.31.250.81", 40000)) as host:
        r = host.get("/health/teacher")                                      # pcbk-egress — не front
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")


def test_teacher_health_has_no_logins_or_places(tmp_path):                # только числа и флаги
    app, role = make_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        for kind in TEACHER_KINDS:
            role.teacher_stats.event(kind)
        body = c.get("/health/teacher").json()
    assert all(v is None or isinstance(v, (bool, int, float, str)) for v in body.values())
    assert not [k for k in body if "place" in k or "login" in k or "name" in k]
    assert [k for k, v in body.items() if isinstance(v, str)] == ["drill"]


def test_teacher_stats_hour_window_and_unknown_kind():
    mono = FakeMono(100.0)
    s = TeacherStats(monotonic=mono)
    s.event("read"); s.event("read"); mono.advance(10); s.event("change")
    assert (s.count_1h("read"), s.count_1h("change"), s.count_1h("role_refused")) == (2, 1, 0)
    mono.advance(WINDOW_S - 10)                                              # первые вышли из окна
    assert (s.count_1h("read"), s.count_1h("change")) == (0, 1)
    for bad in ("wake_ok", "saved", "", "READ", "reads"):                    # опечатка — не молчаливый ноль
        with pytest.raises(ValueError):
            s.event(bad)
        with pytest.raises(ValueError):
            s.count_1h(bad)


def test_role_teacher_stats_own_and_on_role_clock(tmp_path):
    a, b = gateway_role(tmp_path / "a"), gateway_role(tmp_path / "b")
    assert isinstance(a.teacher_stats, TeacherStats) and a.teacher_stats is not b.teacher_stats
    a.teacher_stats.event("read")
    a.mono.advance(WINDOW_S)                                                 # окно — по монотонным часам роли
    assert a.teacher_stats.count_1h("read") == 0


def test_drill_teacher_warning_in_gateway_lifespan(tmp_path, gwlog):      # R96: строка учений — в lifespan шлюза
    app, _ = make_gateway(tmp_path / "off")                                  # отрицательный контроль: без учения
    with TestClient(app, client=FRONT):
        pass
    assert "DRILL_TEACHER" not in gwlog.text
    app, _ = make_gateway(tmp_path / "on", DRILL_TEACHER="shares_stale")
    with TestClient(app, client=FRONT):
        pass
    assert "УЧЕНИЯ: DRILL_TEACHER=shares_stale" in gwlog.text
