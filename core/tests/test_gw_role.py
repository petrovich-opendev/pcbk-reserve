"""Роль gateway по договору Role (Д3а): настройки и база без падения core, lifespan не бросает, первый такт
уборщика фоном, health() без ввода-вывода, /health/gateway — только числа, охрана выхода знает sp-ctl и места."""
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from fake_places import FakeDocker, FakePlaces
from helpers import (BDRV_CFG, CORE_DIR, LLM_ENV, LLM_SETTINGS, LLM_TOKEN, SETTINGS, TOKEN, chat_body, gateway_role,
                     login, make_app, make_gateway, make_llm_role, wait_until, wait_until_async)
from pcbk_core.app import create_app
from pcbk_core.gateway import places
from pcbk_core.gateway.accounts import AccountStore, StoreError
from pcbk_core.main import PLACE_PORT, PLACES, build_roles, egress_targets, gateway_settings
from pcbk_core.settings import GW_ERROR_CHARS, GatewaySettings, Settings

BASE = "https://stand.test:8443"


def test_settings_gateway_validation():
    s = GatewaySettings.from_env({"GW_ORIGIN": "https://stand.example:8443", "STU_NET": "172.31", "GW_IDLE_STOP_S": "60"})
    assert (s.GW_IDLE_STOP_S, s.STU_NET) == (60.0, "172.31")
    for bad in ({"GW_ORIGIN": "http://x"}, {"GW_ORIGIN": "https://x/path"}, {"STU_NET": "172.31.0"},
                {"STU_NET": "300.1"}, {"GW_IDLE_STOP_S": "59"}, {"GW_REAPER_TICK_S": "1000"},
                {"GW_WAKE_TIMEOUT_S": "5"}, {"DRILL_GATEWAY": "x"}, {"SP_CTL_URL": "ftp://x"}):
        with pytest.raises(ValueError):
            GatewaySettings.from_env(bad)
    assert Settings.from_env({"GW_ORIGIN": "http://x"})                                # Settings поля шлюза не знает
    with pytest.raises(ValueError) as e:
        GatewaySettings.from_env({"GW_ORIGIN": "https://x/tok-SECRET"})
    assert "GW_ORIGIN" in str(e.value) and "SECRET" not in str(e.value)                # Ш9: без сырого значения


def test_gateway_settings_error_keeps_core(tmp_path):                                   # блокер р2; Review Focus 4
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "GW_ORIGIN": "https://x/path"})}   # не бросает
    assert roles["gateway"].config_error.startswith("настройки шлюза: GW_ORIGIN")


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
        db.chmod(0o444); tmp_path.chmod(0o555)                                         # том только для чтения
        try:
            r = login(c, "stu-03")
            assert r.status_code == 503, (r.status_code, store_state(role))            # вход пишет — сбой
            wait_store(role, lambda s: s.probe_failures >= 3)                          # такт за тактом — красное
            assert c.get("/healthz/gateway").json() == {"ok": False, "detail": "учётные записи недоступны"}
            assert c.get("/health/gateway").json()["store_ok"] is False
        finally:
            tmp_path.chmod(0o755); db.chmod(0o644)
        wait_store(role, lambda s: s.probe_failures == 0)
        assert c.get("/healthz/gateway").json()["ok"] is False, store_state(role)     # проба ок, сбой входа не снят
        assert login(c, "stu-03").status_code == 200
        assert c.get("/healthz/gateway").json() == {"ok": True, "detail": "принимает вход"}


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
                          "origin_refused_1h", "login_busy_1h", "cookies_duplicate_1h", "drill"}
        assert all(isinstance(j[k], int) for k in j if k.endswith(("_n", "_1h")))
        assert c.get("/healthz/gateway").json() == {"ok": True, "detail": "принимает вход"}
        assert "stu-03" not in json.dumps(j)


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
           {"DRILL_GATEWAY": "x"})
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
