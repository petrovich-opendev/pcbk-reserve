"""Наблюдение раньше функции (задача А1 Д7а, Д7-R60): счётчики AgentStats за час, GET /health/agents — ровно 15 ключей,
только числа и тексты, только зона front, отказы — обработчиком шлюза из реестра; DRILL_AGENTS — в drill. До воркера
перезагрузки (А5) checked_at — null, и строка сторожа честно «неизвестно»."""
import pytest
from fastapi.testclient import TestClient

from helpers import FakeMono, make_gateway, run_python
from pcbk_core.agents.stats import AGENT_KINDS, AgentStats
from pcbk_core.gateway.agents_api import HEALTH_AGENTS_PATH
from pcbk_core.gateway.stats import KINDS, GatewayStats
from pcbk_core.hourly import WINDOW_S

FRONT = ("172.31.250.35", 40000)            # edge в pcbk-front (предпроверка m2: в helpers адреса нет)
KEYS = {"checked_at", "store_ok", "dir_error", "pending", "pending_oldest_s", "saved_1h", "refused_1h", "unavailable_1h",
        "visible_1h", "not_visible_1h", "quarantined_1h", "reload_failed_1h", "reload_delayed_1h", "image_config_1h", "drill"}


def test_agents_health_contract(tmp_path):
    app, role = make_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        role.agent_stats.event("saved"); role.agent_stats.event("refused"); role.agent_stats.event("refused")
        body = c.get("/health/agents").json()
    assert set(body) == KEYS
    assert (body["saved_1h"], body["refused_1h"], body["store_ok"], body["dir_error"], body["drill"]) == (1, 2, True, None, "")


def test_agents_health_front_only(tmp_path):
    app, _ = make_gateway(tmp_path)
    with TestClient(app, client=("172.31.3.3", 5000)) as c:                       # сеть места
        r = c.get("/health/agents")
    assert (r.status_code, r.json()["name"]) == (404, "NotFound")


# --- сверх брифа ---

def test_agent_kinds_and_path():
    assert AGENT_KINDS == {"saved", "refused", "unavailable", "visible", "not_visible", "quarantined", "reload_failed",
                           "image_config", "reload_delayed"}
    assert HEALTH_AGENTS_PATH == "/health/agents"
    assert len(KEYS) == 15 and {k[:-3] for k in KEYS if k.endswith("_1h")} == AGENT_KINDS   # счётчик на каждый вид


def test_agents_health_counts_each_kind_to_its_key(tmp_path):
    app, role = make_gateway(tmp_path)
    kinds = sorted(AGENT_KINDS)
    with TestClient(app, client=FRONT) as c:
        for i, kind in enumerate(kinds, start=1):                                    # у каждого вида своё число
            for _ in range(i):
                role.agent_stats.event(kind)
        body = c.get("/health/agents").json()
    assert {k: body[f"{k}_1h"] for k in kinds} == {k: i for i, k in enumerate(kinds, start=1)}


def test_agents_health_before_reload_worker(tmp_path):                          # до А5: строка «неизвестно»
    app, role = make_gateway(tmp_path)
    assert role.reloads is None
    with TestClient(app, client=FRONT) as c:
        body = c.get("/health/agents").json()
    assert body == {**{k: 0 for k in KEYS}, "checked_at": None, "store_ok": True, "dir_error": None,
                    "pending_oldest_s": None, "drill": ""}


def test_agents_health_store_flags_and_dir_error(tmp_path):                    # раунд 3, I-р3-1: флаги порознь
    app, role = make_gateway(tmp_path)
    s = role.agent_stats
    with TestClient(app, client=FRONT) as c:
        def store_ok():
            return c.get("/health/agents").json()["store_ok"]
        s.store_failed("StoreError")                                                # роль «данные»
        assert (store_ok(), s.store_error) == (False, "StoreError")
        s.store_ok()
        assert (store_ok(), s.store_error) == (True, None)
        s.worker_store_failed("StoreError")                                         # воркер
        s.store_ok()                                                                # удача роли «данные» флаг воркера не стирает
        assert (store_ok(), s.worker_store_error) == (False, "StoreError")
        s.store_failed("OperationalError")
        s.worker_store_ok()
        assert store_ok() is False                                                  # и наоборот
        s.store_ok()
        assert store_ok() is True
        s.dir_error = "каталог агентов не пишется"                                  # ставит воркер (А5)
        assert c.get("/health/agents").json()["dir_error"] == "каталог агентов не пишется"


def test_agents_health_drill(tmp_path):
    app, _ = make_gateway(tmp_path, DRILL_AGENTS="freeze_reload")
    with TestClient(app, client=FRONT) as c:
        assert c.get("/health/agents").json()["drill"] == "freeze_reload"


def test_agents_health_readable_with_config_error(tmp_path):                    # как /health/gateway: без configured
    app, role = make_gateway(tmp_path, config_error="GW_ORIGIN не задан")
    with TestClient(app, client=FRONT) as c:
        role.agent_stats.event("unavailable")
        r = c.get("/health/agents")
    assert (r.status_code, r.json()["unavailable_1h"]) == (200, 1)


def test_agents_health_other_methods_404_without_allow(tmp_path):              # отказ — через реестр, вид OpenCode
    app, _ = make_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        assert c.get("/health/agents").status_code == 200                           # положительный контроль
        for method in ("POST", "PUT", "DELETE", "PROPFIND"):
            r = c.request(method, "/health/agents")
            assert (r.status_code, r.json()) == (404, {"name": "NotFound", "data": {"message": "Не найдено"}}), method
            assert "allow" not in r.headers, method
    with TestClient(app, base_url="http://172.31.250.82:8000", client=("172.31.250.81", 40000)) as host:
        r = host.get("/health/agents")                                              # pcbk-egress — не front
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")


def test_agents_health_has_no_names_or_places(tmp_path):                        # только числа и тексты
    app, role = make_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        for kind in AGENT_KINDS:
            role.agent_stats.event(kind)
        body = c.get("/health/agents").json()
    assert all(v is None or isinstance(v, (bool, int, str)) for v in body.values())
    assert not [k for k in body if "place" in k or "name" in k or "agent" in k]


def test_agent_stats_hour_window_and_unknown_kind():
    mono = FakeMono(100.0)
    s = AgentStats(monotonic=mono)
    s.event("saved"); s.event("saved"); mono.advance(10); s.event("visible")
    assert (s.count_1h("saved"), s.count_1h("visible"), s.count_1h("refused")) == (2, 1, 0)
    mono.advance(WINDOW_S - 10)                                                     # первые вышли из окна
    assert (s.count_1h("saved"), s.count_1h("visible")) == (0, 1)
    assert (s.store_error, s.worker_store_error, s.dir_error) == (None, None, None)
    for bad in ("wake_ok", "save", "", "SAVED"):                                    # опечатка — не молчаливый ноль
        with pytest.raises(ValueError):
            s.event(bad)
        with pytest.raises(ValueError):
            s.count_1h(bad)


def test_gateway_stats_kinds_parameter():
    assert GatewayStats()._events.keys() == KINDS                                  # по умолчанию — прежние виды
    with pytest.raises(ValueError):
        GatewayStats().event("saved")
    custom = GatewayStats(kinds=frozenset({"a"}))
    custom.event("a")
    assert custom.count_1h("a") == 1 and custom.stream_opened("stu-01")             # слоты потоков — те же
    with pytest.raises(ValueError):
        custom.event("wake_ok")


def test_role_without_stats_makes_its_own(tmp_path):                           # Д7-R53: в тестах роль создаёт свой
    _, a = make_gateway(tmp_path / "a")
    _, b = make_gateway(tmp_path / "b")
    assert isinstance(a.agent_stats, AgentStats) and a.agent_stats is not b.agent_stats


def test_hourly_and_agent_stats_are_stdlib_only():                             # П1: без петли импорта
    code = ("import sys, pcbk_core.agents.stats; "
            "print(sorted(m for m in ('fastapi', 'starlette', 'httpx', 'pydantic', 'pcbk_core.gateway', "
            "'pcbk_core.data') if m in sys.modules))")
    assert run_python(code).strip() == "[]"
    for order in ("pcbk_core.data, pcbk_core.gateway", "pcbk_core.gateway, pcbk_core.data",
                  "pcbk_core.agents.stats, pcbk_core.gateway, pcbk_core.data", "pcbk_core.main"):
        assert run_python(f"import {order}; print('ok')").strip() == "ok", order
    assert run_python("import pcbk_core.agents; print(sorted(vars(pcbk_core.agents)))").strip().count("stats") == 0
