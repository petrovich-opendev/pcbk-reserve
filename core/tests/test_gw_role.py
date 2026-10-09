"""Роль gateway по договору Role (Д3а): настройки и база без падения core, lifespan не бросает, первый такт
уборщика фоном, health() без ввода-вывода, /health/gateway — только числа, охрана выхода знает sp-ctl и места."""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from fake_places import FakeDocker, FakePlaces
from helpers import (BDRV_CFG, CORE_DIR, LLM_ENV, LLM_SETTINGS, LLM_TOKEN, SETTINGS, TOKEN, chat_body, full_disk,
                     gateway_role, login, make_app, make_gateway, make_llm_role, wait_until, wait_until_async)
from pcbk_core.app import create_app
from pcbk_core.gateway import places
from pcbk_core.gateway.accounts import AccountStore, StoreError
from pcbk_core.gateway.stats import MAX_STREAMS_PER_LOGIN, GatewayStats
from pcbk_core.main import PLACE_PORT, PLACES, build_roles, egress_targets, gateway_settings
from pcbk_core.settings import GW_ERROR_CHARS, GatewaySettings, Settings

BASE = "https://stand.test:8443"


def test_settings_gateway_validation():
    s = GatewaySettings.from_env({"GW_ORIGIN": "https://stand.example:8443", "STU_NET": "172.31", "GW_IDLE_STOP_S": "60"})
    assert (s.GW_IDLE_STOP_S, s.STU_NET) == (60.0, "172.31")
    for bad in ({"GW_ORIGIN": "http://x"}, {"GW_ORIGIN": "https://x/path"}, {"STU_NET": "172.31.0"},
                {"STU_NET": "300.1"}, {"GW_IDLE_STOP_S": "59"}, {"GW_REAPER_TICK_S": "1000"},
                {"GW_WAKE_TIMEOUT_S": "5"}, {"DRILL_GATEWAY": "x"}, {"SP_CTL_URL": "ftp://x"},
                {"DRILL_AGENTS": "x"}, {"DRILL_AGENTS": "freeze_reaper"}, {"DRILL_AGENTS": "FREEZE_RELOAD"}):   # Д7а (m18)
        with pytest.raises(ValueError):
            GatewaySettings.from_env(bad)
    assert (GatewaySettings().DRILL_AGENTS, GatewaySettings.from_env({"DRILL_AGENTS": "freeze_reload"}).DRILL_AGENTS) == \
           ("", "freeze_reload")
    assert Settings().AGENTS_ROOT == "/srv/agents" and Settings.from_env({"AGENTS_ROOT": "/x"}).AGENTS_ROOT == "/x"
    assert Settings.from_env({"GW_ORIGIN": "http://x"})                                # Settings поля шлюза не знает
    with pytest.raises(ValueError) as e:
        GatewaySettings.from_env({"GW_ORIGIN": "https://x/tok-SECRET"})
    assert "GW_ORIGIN" in str(e.value) and "SECRET" not in str(e.value)                # Ш9: без сырого значения


def test_gateway_settings_error_keeps_core(tmp_path):                                   # блокер р2; Review Focus 4
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "GW_ORIGIN": "https://x/path"})}   # не бросает
    assert roles["gateway"].config_error.startswith("настройки шлюза: GW_ORIGIN")
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "GW_MAX_BODY_BYTES": "4194305"})}  # N8: сверх 4 МиБ
    assert roles["gateway"].config_error.startswith("настройки шлюза: GW_MAX_BODY_BYTES")


@pytest.mark.skipif(os.geteuid() == 0, reason="root пишет и в каталог 0500")
def test_gateway_db_failure_keeps_core(tmp_path, gwlog):                              # блокер р3; Review Focus 4
    ro = tmp_path / "ro"
    ro.mkdir(mode=0o500)                                                               # настоящий сбой, без подмен
    gw = gateway_role(tmp_path / "gw", db_path=ro / "sub" / "core.db", accounts=False)
    (tmp_path / "data").mkdir()                                                        # make_app каталог не создаёт
    app, _ = make_app(tmp_path / "data", extra_roles=[gw])                             # рядом служба данных Д3б
    try:
        with TestClient(app, base_url=BASE) as c:                                      # lifespan не бросил
            assert c.get("/healthz/live").status_code == 200
            assert c.get("/healthz/data").status_code == 200
            assert gw.config_error.startswith("учётные записи не открываются")
            assert len(gw.config_error) <= 80 and str(ro) not in gw.config_error
            j = c.get("/healthz/gateway")
            assert j.status_code == 503 and j.json()["detail"].startswith("ошибка настройки: учётные записи")
            r = login(c, "stu-03")
            assert (r.status_code, r.json()["name"]) == (503, "Unavailable")
            assert c.get("/health/gateway").json()["config_error"] == gw.config_error
            assert gw.workplaces.ticks == 0                                             # при config_error уборщика нет
        assert "шлюз: ошибка настройки" in gwlog.text                                  # положительный контроль журнала
        assert "учётных записей студентов" not in gwlog.text                          # students() не звали
    finally:
        ro.chmod(0o700)


def test_lifespan_never_raises(tmp_path, monkeypatch, gwlog):                         # блокер р3
    app, role = make_gateway(tmp_path)
    monkeypatch.setattr(AccountStore, "students", lambda self: (_ for _ in ()).throw(StoreError("sqlite")))
    monkeypatch.setattr(role.workplaces, "tick", AsyncMock(side_effect=RuntimeError("такт")))
    with TestClient(app, base_url=BASE) as c:                                          # приложение поднялось
        assert c.get("/healthz/live").status_code == 200
        assert login(c, "stu-03").status_code == 200                                   # база исправна — вход работает
        wait_until(lambda: "такт уборщика: RuntimeError" in gwlog.text, timeout=2)   # задача уборщика — фоном
        assert c.get("/health/gateway").json()["checked_at"] is not None              # время запуска, не null
        role.mono.advance(4 * 30 + 1)                                                  # такт падает всегда
        assert c.get("/healthz/gateway").json() == {"ok": False, "detail": "уборщик простоя молчит"}
    assert "шлюз: учётных записей студентов ?" in gwlog.text


def test_first_tick_does_not_block_startup(tmp_path):                                  # решение контролёра р3; Д5-R27
    gw = gateway_role(tmp_path / "gw", docker=FakeDocker(running={3}), oc=FakePlaces(agent_delay=1.5))  # < READY_ATTEMPT_S
    (tmp_path / "data").mkdir()
    app, _ = make_app(tmp_path / "data", extra_roles=[gw])                             # рядом служба данных Д3б
    t = time.monotonic()
    with TestClient(app, base_url=BASE) as c:
        assert time.monotonic() - t < 1.0                                              # lifespan не ждал такта
        assert c.get("/healthz/data").status_code == 200                               # первый такт висит на GET /agent
        assert gw.workplaces.ticks == 0 and not gw.workplaces.is_ready(3)
        assert c.get("/healthz/gateway").json() == {"ok": True, "detail": "принимает вход"}
        wait_until(lambda: gw.workplaces.ticks == 1, timeout=6)
        assert gw.workplaces.is_ready(3)                                               # «готово» вернул первый такт


@pytest.mark.skipif(os.geteuid() == 0, reason="root пишет и в файл 0444")
def test_reaper_probe_keeps_row_red(tmp_path):                                          # Д5-R28, р4; без подмен
    app, role = make_gateway(tmp_path, GW_REAPER_TICK_S=0.05)
    db = tmp_path / "core.db"
    with TestClient(app, base_url=BASE) as c:
        # том только для чтения: каталог закрывается первым и открывается последним — иначе SQLite соседнего
        # соединения (воркер перезагрузки агентов Д7а читает тот же core.db) успел бы создать core.db-shm с правами
        # файла 0444 в ещё пишущемся каталоге, и база не ожила бы после возврата прав (доводка А5 р1, В1)
        tmp_path.chmod(0o555); db.chmod(0o444)
        try:
            r = login(c, "stu-03")
            assert r.status_code == 503, (r.status_code, store_state(role))            # вход пишет — сбой
            wait_store(role, lambda s: s.probe_failures >= 3)                          # такт за тактом — красное
            assert c.get("/healthz/gateway").json() == {"ok": False, "detail": "учётные записи недоступны"}
            assert c.get("/health/gateway").json()["store_ok"] is False
        finally:
            db.chmod(0o644); tmp_path.chmod(0o755)
        wait_store(role, lambda s: s.probe_failures == 0)
        assert c.get("/healthz/gateway").json()["ok"] is False, store_state(role)     # проба ок, сбой входа не снят
        assert login(c, "stu-03").status_code == 200
        assert c.get("/healthz/gateway").json() == {"ok": True, "detail": "принимает вход"}


def test_full_disk_row_not_greened_by_reads(tmp_path):                                # итоговое ревью Д5а, M2 (R3)
    """Переполненный диск (COMMIT падает, чтения идут): строка store не зеленеет от чтений — ни пока диск полон,
    ни после удачного пульса; зеленеет после удачной записи (вход)."""
    app, role = make_gateway(tmp_path)
    junk = {"Cookie": "pcbk_session=junk-session"}                                     # /auth/me читает базу
    with TestClient(app, base_url=BASE) as c:
        wait_until(lambda: role.workplaces.ticks == 1)                                 # пульс первого такта — позади
        with full_disk(role.store):
            assert role.store.ping() is False                                          # пульс пишет с COMMIT
            assert login(c, "stu-03").status_code == 503, store_state(role)            # сессия не записана
            for _ in range(3):
                assert c.get("/pcbk/api/auth/me", headers=junk).status_code == 401     # чтения идут
                assert c.get("/health/gateway").json()["store_ok"] is False
        assert role.store.ping() is True                                               # диск освободился
        assert c.get("/pcbk/api/auth/me", headers=junk).status_code == 401
        assert c.get("/healthz/gateway").json() == {"ok": False, "detail": "учётные записи недоступны"}, \
            store_state(role)
        assert login(c, "stu-03").status_code == 200                                   # удачная запись
        assert c.get("/healthz/gateway").json() == {"ok": True, "detail": "принимает вход"}
        assert c.get("/health/gateway").json()["store_ok"] is True


def store_state(role) -> str:
    s = role.store
    return f"probe_failures={s.probe_failures} op_failures={s.op_failures} last_error={s.last_error} ticks=" \
           f"{role.workplaces.ticks}"


def wait_store(role, pred, timeout: float = 10) -> None:
    """Ждёт состояния хранилища (такты уборщика — фоном, под нагрузкой машины медленнее); не дождался —
    AssertionError с состоянием хранилища в тексте (ревью B, M4)."""
    try:
        wait_until(lambda: pred(role.store), timeout=timeout)
    except AssertionError:
        raise AssertionError(f"хранилище не пришло в состояние за {timeout} с: {store_state(role)}") from None


def test_health_does_no_io(tmp_path, monkeypatch):                                     # договор Role
    app, role = make_gateway(tmp_path)
    with TestClient(app):
        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("health() открыл базу"))
        assert role.health() == (True, "принимает вход")


def test_egress_targets_cover_gateway():                                               # Review Focus 5
    t = egress_targets(LLM_SETTINGS, GatewaySettings.from_env({"STU_NET": "172.31"}), BDRV_CFG)
    assert ("pcbk-sp-ctl", 2375) in t and all((f"172.31.{n}.3", 4096) in t for n in range(1, 11))
    assert len(t) == 13


def test_health_json_only_numbers(tmp_path):
    app, role = make_gateway(tmp_path)
    with TestClient(app) as c:
        j = c.get("/health/gateway").json()
        assert set(j) == {"checked_at", "config_error", "store_ok", "sp_ctl", "running_n", "ready_n", "wake_ok_1h",
                          "wake_failed_1h", "stop_failed_1h", "place_auth_failed_1h", "logins_failed_1h",
                          "origin_refused_1h", "login_busy_1h", "cookies_duplicate_1h", "drill",
                          "refused_1h", "streams", "stream_broken_1h", "upstream_failed_1h"}          # Д5б (Б1)
        assert all(isinstance(j[k], int) for k in j if k.endswith(("_n", "_1h")) or k == "streams")
        assert c.get("/healthz/gateway").json() == {"ok": True, "detail": "принимает вход"}
        assert "stu-03" not in json.dumps(j)


def test_health_json_has_proxy_counters_and_stream_cap(tmp_path):
    app, role = make_gateway(tmp_path)
    role.stats.event("refused"); role.stats.event("stream_broken")
    assert [role.stats.stream_opened("stu-03") for _ in range(4)] == [True, True, True, False]
    role.stats.stream_closed("stu-05")                                                 # чужой логин — не ниже нуля
    with TestClient(app) as c:
        j = c.get("/health/gateway").json()
    assert (j["refused_1h"], j["streams"], j["stream_broken_1h"], j["upstream_failed_1h"]) == (1, 3, 1, 0)


# --- Б1, сверх брифа: слоты потоков (решение контроллера 3) ---

def test_stream_slots_per_login_and_no_zero_entries():
    s = GatewayStats()
    assert (MAX_STREAMS_PER_LOGIN, s.streams_of("stu-01"), s.streams) == (3, 0, 0)          # незнакомый логин — 0
    assert [s.stream_opened("stu-01") for _ in range(3)] + [s.stream_opened("stu-02")] == [True] * 4
    assert (s.streams_of("stu-01"), s.streams_of("stu-02"), s.streams) == (3, 1, 4)        # предел — на учётку
    assert s.stream_opened("stu-01") is False and s.streams_of("stu-01") == 3              # отказ слота не берёт
    s.stream_closed("stu-01")
    assert s.stream_opened("stu-01") is True and s.stream_opened("stu-01") is False        # закрытый слот — снова
    for _ in range(5):                                                                     # лишние закрытия
        s.stream_closed("stu-01")
    s.stream_closed("stu-09")                                                              # незнакомый логин
    assert (s.streams_of("stu-01"), s.streams_of("stu-09"), s.streams) == (0, 0, 1)
    s.stream_closed("stu-02")
    assert s.streams == 0 and s._streams == {}                                             # учётки с нулём не копятся
    for kind in ("refused", "stream_broken", "upstream_failed"):                           # виды Д5б
        s.event(kind)
        assert s.count_1h(kind) == 1


class _SlowDict(dict):
    """Словарь слотов, который после каждого чтения отдаёт GIL: без замка гонка потоков неизбежна. Медленны все пути
    чтения (get, [], in) — тест не привязан к тому, как GatewayStats читает словарь (Б1-m2); reads — число чтений,
    тест сверяет, что медленный путь пройден."""

    reads = 0

    def _slow(self, value):
        type(self).reads += 1
        time.sleep(0.01)
        return value

    def get(self, *args):
        return self._slow(super().get(*args))

    def __getitem__(self, key):
        return self._slow(super().__getitem__(key))

    def __contains__(self, key):
        return self._slow(super().__contains__(key))


def _all_at_once(fn, n: int = 8) -> None:
    start = threading.Barrier(n)

    def run() -> None:
        start.wait()
        fn()

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_stream_slots_thread_safe():
    """Слоты — под замком GatewayStats: восемь потоков разом берут слот одной учётки — удач ровно
    MAX_STREAMS_PER_LOGIN; восемь закрытий разом — ноль, и учётка из словаря ушла."""
    s = GatewayStats()
    s._streams = _SlowDict()
    _SlowDict.reads = 0
    got: list[bool] = []
    _all_at_once(lambda: got.append(s.stream_opened("stu-01")))
    assert (got.count(True), s.streams_of("stu-01")) == (MAX_STREAMS_PER_LOGIN, MAX_STREAMS_PER_LOGIN)
    opened_reads = _SlowDict.reads
    _all_at_once(lambda: s.stream_closed("stu-01"))
    assert (s.streams, dict(s._streams)) == (0, {})
    assert opened_reads >= 8 and _SlowDict.reads >= opened_reads + 8                     # каждый вызов читал медленно


def test_freeze_reaper_drill_stops_ticks(tmp_path):                                    # Review Focus 3
    app, role = make_gateway(tmp_path, DRILL_GATEWAY="freeze_reaper", GW_REAPER_TICK_S=0.05)
    with TestClient(app) as c:
        wait_until(lambda: role.workplaces.ticks == 1, timeout=2)                      # первый такт — фоном
        time.sleep(0.3)
        assert role.workplaces.ticks == 1
        role.mono.advance(10)                                      # срок молчания — 4 такта
        assert c.get("/healthz/gateway").json() == {"ok": False, "detail": "уборщик простоя молчит"}


# --- сверх брифа ---

def test_egress_places_follow_workplaces():
    """Места охраны выхода — те же, куда ходит Workplaces: main не импортирует places (httpx), числа — свои."""
    assert (list(PLACES), PLACE_PORT) == (list(places.PLACES), places.PLACE_PORT)
    gw = GatewaySettings.from_env({"STU_NET": "10.50", "SP_CTL_URL": "http://127.0.0.1:2376"})
    t = egress_targets(LLM_SETTINGS, gw, None)
    assert t[1:] == [("127.0.0.1", 2376)] + [(f"10.50.{n}.3", 4096) for n in range(1, 11)] and len(t) == 12


def test_install_only_through_registry(tmp_path):
    """install шлюза не ставит обработчиков и звеньев напрямую; сеть мест — в app.state для сверки токенов."""
    app = create_app(SETTINGS, [gateway_role(tmp_path, STU_NET="10.50")])
    assert app.state.pcbk_stu_net == "10.50"
    assert {m.cls.__name__ for m in app.user_middleware} == {"BodyLimit"}


def test_admin_command_has_no_runpy_warning(tmp_path):
    """python -m pcbk_core.gateway.accounts: пакет шлюза модуль команды при загрузке не импортирует."""
    p = subprocess.run([sys.executable, "-m", "pcbk_core.gateway.accounts", "list"], cwd=CORE_DIR,
                       env={**os.environ, "DB_PATH": str(tmp_path / "core.db")}, capture_output=True, text=True)
    assert p.returncode == 0 and p.stderr == "", p.stderr[-300:]


# --- доводка, раунд 1 (task-А4-fix1.md) ---

def test_stu_net_survives_other_gw_errors(tmp_path, fake_or):                         # F2 (оба ревью, I2)
    """Опечатка в GW_ORIGIN не сбрасывает STU_NET: служба данных и LLM-прокси принимают токен места 01 из его
    сети 10.50.1.3; роль шлюза — с config_error."""
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    env = {**LLM_ENV, "STU_NET": "10.50", "GW_ORIGIN": "https://x/path", "SP_CTL_URL": "http://127.0.0.1:9"}
    gw = {r.name: r for r in build_roles(settings, env)}["gateway"]
    assert gw.config_error.startswith("настройки шлюза: GW_ORIGIN") and gw.gw.STU_NET == "10.50"
    assert gw.gw.SP_CTL_URL == "http://127.0.0.1:9"
    assert gateway_settings(env)[0] == gw.gw                                          # то же берёт serve()
    (tmp_path / "data").mkdir()
    app, data = make_app(tmp_path / "data", extra_roles=[make_llm_role(tmp_path / "llm", fake_or), gw])

    async def calls():
        async with app.router.lifespan_context(app):
            await wait_until_async(lambda: data.catalog.loaded)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("10.50.1.3", 40000)),
                                         base_url="http://testserver") as c:
                tag = await c.post("/api/data/tag_now", json={"tags": ["20FAKE_001_PV"]},
                                   headers={"Authorization": f"Bearer {TOKEN}"})
                llm = await c.post("/llm/v1/chat/completions", json=chat_body(),
                                   headers={"Authorization": f"Bearer {LLM_TOKEN}"})
        return tag.status_code, llm.status_code
    tag, llm = asyncio.run(calls())
    assert tag == 200 and llm != 401


def test_gateway_settings_fallback_keeps_valid_fields():                               # F2
    env = {"STU_NET": "10.50", "SP_CTL_URL": "http://sp:2376", "GW_IDLE_STOP_S": "600", "GW_REAPER_TICK_S": "400",
           "GW_ORIGIN": "https://x/path", "DRILL_GATEWAY": "freeze_reaper"}
    gw = GatewaySettings.valid_from_env(env)
    assert (gw.STU_NET, gw.SP_CTL_URL, gw.GW_IDLE_STOP_S, gw.DRILL_GATEWAY) == ("10.50", "http://sp:2376", 600.0,
                                                                               "freeze_reaper")
    assert (gw.GW_ORIGIN, gw.GW_REAPER_TICK_S) == ("", 30.0)                          # не прошедшие — умолчания
    assert GatewaySettings.valid_from_env({"STU_NET": "300.1"}).STU_NET == "172.31"    # неверный STU_NET — умолчание
    assert gateway_settings({"STU_NET": "300.1"})[1].startswith("STU_NET")


@pytest.mark.parametrize("broken", ["store.open", "workplaces.spctl.open"])
def test_lifespan_exception_becomes_config_error(tmp_path, gwlog, broken):            # F3 (ревью B, I3)
    gw = gateway_role(tmp_path / "gw")
    owner, name = broken.rsplit(".", 1)
    target = gw.store if owner == "store" else gw.workplaces.spctl

    def boom(*a, **k):
        raise RuntimeError("сбой запуска")
    setattr(target, name, boom)
    (tmp_path / "data").mkdir()
    app, _ = make_app(tmp_path / "data", extra_roles=[gw])                             # рядом служба данных Д3б
    with TestClient(app, base_url=BASE) as c:                                          # lifespan не бросил
        assert c.get("/healthz/live").status_code == 200
        assert c.get("/healthz/data").status_code == 200
        assert gw.config_error == "запуск шлюза: RuntimeError"
        assert c.get("/healthz/gateway").json()["detail"] == "ошибка настройки: запуск шлюза: RuntimeError"
        assert gw.workplaces.ticks == 0                                                 # уборщика нет
    assert "шлюз: ошибка настройки: запуск шлюза: RuntimeError" in gwlog.text
    assert "сбой запуска" not in gwlog.text                                            # только класс


@pytest.mark.skipif(os.geteuid() == 0, reason="root пишет и в каталог 0500")
def test_settings_errors_short_and_db_part_kept(tmp_path):                            # F10 (ревью B, M2)
    bad = ({"GW_ORIGIN": "http://x"}, {"GW_IDLE_STOP_S": "59"}, {"GW_IDLE_STOP_S": "x"}, {"GW_REAPER_TICK_S": "0"},
           {"GW_REAPER_TICK_S": "1000"}, {"GW_WAKE_TIMEOUT_S": "inf"}, {"GW_SESSION_TTL_S": "1"},
           {"SP_CTL_URL": "ftp://x"}, {"SP_CTL_URL": "http://x:99999"}, {"STU_NET": "1.2.3"},
           {"DRILL_GATEWAY": "x"}, {"DRILL_AGENTS": "x"})
    for env in bad:
        with pytest.raises(ValueError) as e:
            GatewaySettings.from_env(env)
        assert len(str(e.value)) <= GW_ERROR_CHARS, str(e.value)
    ro = tmp_path / "ro"
    ro.mkdir(mode=0o500)
    long_error = "настройки шлюза: GW_REAPER_TICK_S: не больше GW_IDLE_STOP_S / 2"
    gw = gateway_role(tmp_path / "gw", db_path=ro / "sub" / "core.db", accounts=False, config_error=long_error)
    try:
        with TestClient(create_app(SETTINGS, [gw])):
            pass
    finally:
        ro.chmod(0o700)
    db_part = "учётные записи не открываются: OperationalError"
    assert len(gw.config_error) <= 80 and gw.config_error.endswith("; " + db_part), gw.config_error
    kept = gw.config_error[:-len("; " + db_part)]
    assert kept.endswith("…") and long_error.startswith(kept[:-1])                    # сокращена первая часть
    assert long_error[len(kept) - 1] in " :;,"                                          # по границе слова


def test_gw_origin_port_rules():                                                         # F11 (ревью B, M3)
    for bad in ("https://stand.example:443", "https://stand.example:08443", "https://stand.example:0",
                "https://stand.example:65536", "http://stand.example:80"):
        with pytest.raises(ValueError, match="GW_ORIGIN"):
            GatewaySettings.from_env({"GW_ORIGIN": bad})
    for good in ("https://stand.example", "https://stand.example:8443", "https://127.0.0.1:28443"):
        assert GatewaySettings.from_env({"GW_ORIGIN": good}).GW_ORIGIN == good


# --- задача А1 Д10а: учения кабинета преподавателя ---

def test_drill_teacher_gateway_setting():                                               # m-7: опечатка — config_error шлюза
    assert GatewaySettings.from_env({"DRILL_TEACHER": "shares_stale"}).DRILL_TEACHER == "shares_stale"
    assert GatewaySettings.from_env({}).DRILL_TEACHER == ""
    with pytest.raises(ValueError, match="DRILL_TEACHER: пусто, shares_stale или journal_down"):
        GatewaySettings.from_env({"DRILL_TEACHER": "drop"})
    gw, problem = gateway_settings({"DRILL_TEACHER": "drop"})
    assert (gw.DRILL_TEACHER, problem.startswith("DRILL_TEACHER:")) == ("", True)
    assert Settings.from_env({"DRILL_TEACHER": "drop"}).DB_PATH                      # ядро Settings её не видит


def test_drill_teacher_modes_and_text():                                                # сверх брифа
    from pcbk_core.settings import DRILL_TEACHER_MODES, _GW_FIELD_CHECKS

    assert DRILL_TEACHER_MODES == {"", "shares_stale", "journal_down"}
    assert "DRILL_TEACHER" in _GW_FIELD_CHECKS and GatewaySettings().DRILL_TEACHER == ""
    for mode in ("shares_stale", "journal_down"):
        assert GatewaySettings.from_env({"DRILL_TEACHER": mode}).DRILL_TEACHER == mode
    for bad in ("SHARES_STALE", "shares_stale ", "freeze_reload", "journal", "tool_error", "x" * 200):
        with pytest.raises(ValueError) as e:
            GatewaySettings.from_env({"DRILL_TEACHER": bad})
        assert str(e.value) == "DRILL_TEACHER: пусто, shares_stale или journal_down"     # без сырого значения (Ш9)
        assert len(str(e.value)) <= GW_ERROR_CHARS
    gw = GatewaySettings.valid_from_env({"DRILL_TEACHER": "drop", "DRILL_AGENTS": "freeze_reload"})
    assert (gw.DRILL_TEACHER, gw.DRILL_AGENTS) == ("", "freeze_reload")                # прочие поля — прошедшие
    errors = GatewaySettings._parse({"GW_ORIGIN": "http://x", "DRILL_TEACHER": "drop"})[1]
    assert any(e.startswith("DRILL_TEACHER:") for e in errors)                          # признак виден и не первым (m-1 р2)


def test_drill_teacher_typo_closes_gateway_keeps_core(tmp_path):                        # m-7: процесс жив
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "DRILL_TEACHER": "drop"})}   # не бросает
    gw = roles["gateway"]
    assert gw.config_error == "настройки шлюза: DRILL_TEACHER: пусто, shares_stale или journal_down"
    assert gw.gw.DRILL_TEACHER == "" and "data" in roles


# --- Д10а, задача А2: m-7 — опечатка в учении закрывает шлюз и LLM-прокси, процесс жив ---

def test_bad_drill_teacher_closes_gateway_and_llm(tmp_path):
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "DRILL_TEACHER": "drop"})}
    assert roles["gateway"].config_error.startswith("настройки шлюза: DRILL_TEACHER:")
    assert "учения DRILL_TEACHER с ошибкой" in roles["llm"].config_error
    assert roles["llm"].shares.drill == ""                                   # учение не включилось молча
    # m-1 р2: ошибка раньше по полям (GW_ORIGIN) не прячет опечатку в учении — признак из всего списка _parse
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "GW_ORIGIN": "https://x/path",
                                                        "DRILL_TEACHER": "drop"})}
    assert roles["gateway"].config_error.startswith("настройки шлюза: GW_ORIGIN:")
    assert "учения DRILL_TEACHER с ошибкой" in roles["llm"].config_error


def test_good_drill_teacher_reaches_shares_and_journal(tmp_path):         # сверх брифа: учение дошло до обоих
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    for mode in ("shares_stale", "journal_down"):
        roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "DRILL_TEACHER": mode})}
        assert (roles["llm"].shares.drill, roles["gateway"].teacher_log.drill) == (mode, mode)
        assert roles["gateway"].config_error is None and roles["llm"].config_error is None
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "GW_ORIGIN": "https://x/path"})}
    assert "DRILL_TEACHER" not in (roles["llm"].config_error or "")         # чужая ошибка шлюза доли не закрывает


# --- Д11а, задача А2: тормоз прогона (Д11-R117) — крючок мест и задача наблюдения, как у уборщика ---

def test_build_roles_wires_brake_into_gateway(tmp_path):                  # роль llm build_roles — та же, что у шлюза
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    env = {**LLM_ENV, "LLM_RUN_ID": "d11-test", "LLM_RUN_USD": "5", "LLM_RUN_CALLERS": "student-02,ops",
           "LLM_RUN_MEM_FAIL_MIB": "2048", "LLM_RUN_PSI_FULL_MAX": "7.5"}
    roles = {r.name: r for r in build_roles(settings, env)}
    llm, gw = roles["llm"], roles["gateway"]
    assert llm.config_error is None and gw.llm is llm and (llm.brake.fail_mib, llm.brake.psi_full_max) == (2048, 7.5)
    assert gw.workplaces.halted() == frozenset()
    llm.brake.clear()                                                        # память процесса; мета не тронута
    llm.brake.halt("memory")
    assert gw.workplaces.halted() == frozenset({2})                          # ops — не место
    plain = {r.name: r for r in build_roles(settings, LLM_ENV)}
    assert plain["llm"].brake is None and plain["gateway"].workplaces.halted() == frozenset()


def test_halt_watch_starts_even_with_config_error(tmp_path):               # остановка снимает; config_error — не помеха
    run = {"LLM_RUN_ID": "d11-test", "LLM_RUN_USD": 5.0, "LLM_RUN_CALLERS": "student-03"}
    llm = make_llm_role(tmp_path / "a", key=None, **run)
    app, role = make_gateway(tmp_path / "a", llm=llm)
    with TestClient(app):
        task = role._halt_task
        assert task is not None and not task.done() and task.get_name() == "gw-halt-watch"
    assert role._halt_task is None and task.done()
    app, role = make_gateway(tmp_path / "b", llm=llm, config_error="проба")
    with TestClient(app):                                                   # уборщика нет, тормоз смотрит (m-3 ревью А2)
        task = role._halt_task
        assert task is not None and not task.done() and role._reaper is None
    assert role._halt_task is None and task.done()
