"""Роль «данные»: каталог со своим сроком и паузой, опрос свежести, ручки здоровья, учения, build_roles."""
import asyncio
import time
from dataclasses import replace

import anyio
import pytest
from fastapi.testclient import TestClient

from fakes import WHITELIST
from helpers import SETTINGS, make_role, wait_until, write
from pcbk_core.app import create_app
from pcbk_core.data import DataRole
from pcbk_core.data.gate import SlidingWindow
from pcbk_core.data.historian import HistorianError
from pcbk_core.data.sql import live_all_sql, live_sql, names_in
from pcbk_core.logs import setup_logging
from pcbk_core.main import build_roles

pytestmark = pytest.mark.anyio


async def test_refresh_catalog_loads_and_picks_freshest():
    role, fake = make_role()
    assert await role.refresh_catalog() is True
    assert role.freshness_tags == ("20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP")
    await role.poll_freshness()
    j = role.freshness.to_json(role.monotonic())
    assert (j["age_s"], j["tags"], j["error"]) == (10.0, 3, None)
    assert set(names_in(fake.calls[-1][1])) <= WHITELIST
    assert role.clock.tz_label == "UTC+05:00" and role.clock_mono == role.monotonic()


async def test_catalog_backoff_and_reason():
    role, fake = make_role()
    fake.fail = HistorianError("timeout", "долго")
    delays = []
    for _ in range(6):
        ok = await role.refresh_catalog()
        delays.append(role.next_catalog_delay(ok))
    assert delays == [60, 120, 240, 480, 900, 900]
    await role.poll_freshness()
    assert role.freshness.to_json(0.0)["error_text"] == "историан не ответил вовремя"


async def test_catalog_uses_own_deadline():
    role, fake = make_role(settings=replace(SETTINGS, CATALOG_DEADLINE_S=0.3))
    fake.delay_s = 0.6
    assert await role.refresh_catalog() is False and role.catalog_error == "timeout"


async def test_poll_updates_under_user_saturation():                           # Review Focus 2
    role, fake = make_role()
    await role.refresh_catalog()
    role.gate.global_window = SlidingWindow(2, 300)
    fake.delay_s, fake.slow = 1.0, r"IN \('20FAKE_001_PV'\)$"          # медленны только запросы людей
    users = [asyncio.ensure_future(role.gate.run([live_sql(["20FAKE_001_PV"])])) for _ in range(2)]
    await anyio.sleep(0.05)                          # оба места людей заняты, окно выбрано
    t = time.monotonic()
    await role.poll_freshness()
    assert time.monotonic() - t < 0.3 and role.gate.stats()["in_flight"]["user"] == 2
    assert role.freshness.to_json(role.monotonic())["checked_at"] is not None
    await asyncio.gather(*users)


async def test_drill_freeze_poll_stops_loop():
    role, fake = make_role(settings=replace(SETTINGS, FRESH_POLL_S=0.05, DRILL_FRESHNESS="freeze_poll"))
    async with role.lifespan():
        await anyio.sleep(0.4)
    polls = sum(1 for call in fake.calls if len(call) == 2 and "FROM Live WHERE TagName IN" in call[1])
    assert polls == 1


def test_health_and_routes(tmp_path):
    role, _ = make_role(whitelist=frozenset())
    assert role.health() == (False, "белый список пуст или не найден")
    role, _ = make_role()
    with TestClient(create_app(SETTINGS, [role])) as c:
        wait_until(lambda: role.catalog.loaded)
        r = c.get("/health/historian")
        j = r.json()
        assert {"checked_at", "age_s", "skew_s", "error", "error_text", "tags",
                "catalog_age_s", "catalog_error", "gate"} <= set(j)
        assert not any(n in r.text for n in WHITELIST)                   # имён тегов мимо токена нет
        assert c.get("/healthz/data").json()["ok"] is True


# --- сверх брифа ---

def poll_calls(fake) -> int:
    return sum(1 for call in fake.calls if len(call) == 2 and "FROM Live WHERE TagName IN" in call[1])


async def test_refresh_catalog_logs_counts_without_names(capfd):
    setup_logging()                                    # обработчик берёт sys.stderr в момент записи
    role, fake = make_role()
    assert role.health() == (True, "каталог ещё не загружен")
    await role.refresh_catalog()
    err = capfd.readouterr().err
    assert "каталог: 13 тегов, в белом списке 6, нет в историане 0, живых 4" in err
    assert "FAKE_" not in err
    assert role.health() == (True, "каталог: 6 тегов в белом списке")
    assert [len(c) for c in fake.calls] == [2, 1]                  # часы и Tag, затем Live
    assert fake.calls[1] == [live_all_sql()]
    assert (role.catalog_error, role.catalog_failures, role.catalog_loaded_mono) == (None, 0, 1000.0)


async def test_catalog_failure_keeps_previous_catalog_and_success_resets():
    role, fake = make_role()
    fake.fail = HistorianError("timeout", "долго")
    for _ in range(2):
        await role.refresh_catalog()
    assert role.next_catalog_delay(False) == 120
    fake.fail = None
    assert await role.refresh_catalog() is True and role.next_catalog_delay(True) == SETTINGS.CATALOG_REFRESH_S
    tags = role.freshness_tags
    fake.fail = HistorianError("timeout", "долго")
    assert await role.refresh_catalog() is False
    assert (role.catalog.loaded, role.freshness_tags, role.catalog_error, role.catalog_failures) == \
           (True, tags, "timeout", 1)
    assert role.next_catalog_delay(False) == 60                       # счёт пауз — заново после удачи


async def test_catalog_second_call_gets_rest_of_deadline():
    role, fake = make_role(settings=replace(SETTINGS, CATALOG_DEADLINE_S=2.0))
    fake.delay_s, fake.slow = 0.5, r"FROM Tag t "                      # медленна только первая часть
    assert await role.refresh_catalog() is True
    first, second = fake.timeouts
    assert 1.9 <= first <= 2.0 and 1.3 <= second <= 1.55


async def test_catalog_second_call_times_out_within_shared_deadline():
    role, fake = make_role(settings=replace(SETTINGS, CATALOG_DEADLINE_S=1.2))
    fake.delay_s, fake.slow = 0.4, r"FROM (Tag t |Live WHERE Value)"   # обе части по 0,4 с
    t = time.monotonic()
    assert await role.refresh_catalog() is True                        # 0,8 с укладываются в 1,2
    fake.delay_s = 0.7                                                  # каждая часть — да, обе — нет
    assert await role.refresh_catalog() is False and role.catalog_error == "timeout"
    assert time.monotonic() - t < 0.8 + 1.2 + 0.3 and role.catalog.loaded   # прежний каталог остался


async def test_catalog_refused_by_gate_counts_as_failure():
    role, fake = make_role(settings=replace(SETTINGS, CATALOG_DEADLINE_S=0.3))
    fake.delay_s, fake.slow = 0.6, r"Value IS NOT NULL$"
    hold = asyncio.ensure_future(role.gate.run([live_all_sql()], lane="background"))   # фоновое место занято
    await anyio.sleep(0.05)
    assert await role.refresh_catalog() is False
    assert (role.catalog_error, role.catalog_failures) == ("busy", 1)
    await role.poll_freshness()                                         # у «busy» нет текста — «каталог не загружен»
    j = role.freshness.to_json(role.monotonic())
    assert (j["error"], j["error_text"]) == ("catalog", "каталог тегов не загружен")
    await hold


async def test_poll_before_catalog_attempt_is_catalog_error():
    role, fake = make_role()
    await role.poll_freshness()
    j = role.freshness.to_json(role.monotonic())
    assert (j["error"], j["error_text"], j["tags"]) == ("catalog", "каталог тегов не загружен", 0)
    assert fake.calls == []


async def test_poll_without_fresh_tags_is_no_tags():
    role, fake = make_role(whitelist=frozenset({"25FAKE_007_CLS"}))    # дискретный — в набор не идёт
    assert await role.refresh_catalog() is True and role.freshness_tags == ()
    await role.poll_freshness()
    assert role.freshness.to_json(role.monotonic())["error"] == "no_tags"
    assert poll_calls(fake) == 0


async def test_poll_historian_error_keeps_stamps():
    role, fake = make_role()
    await role.refresh_catalog()
    await role.poll_freshness()
    fake.fail = HistorianError("query", "Invalid object name")
    role.monotonic.advance(30)
    await role.poll_freshness()
    j = role.freshness.to_json(role.monotonic())
    assert (j["error"], j["error_text"], j["age_s"]) == ("query", "историан вернул ошибку", 40.0)


async def test_poll_refused_by_gate_changes_nothing():
    role, fake = make_role()
    await role.refresh_catalog()
    role.gate.deadline_s = 0.3                                          # опрос ждёт место не дольше 0,2 с
    fake.delay_s, fake.slow = 0.6, r"FROM Tag t "
    reload = asyncio.ensure_future(role.refresh_catalog())             # каталог держит фоновое место
    await anyio.sleep(0.05)
    await role.poll_freshness()
    assert role.freshness.to_json(role.monotonic()) == {"checked_at": None, "age_s": None, "skew_s": None,
                                                         "error": None, "error_text": None, "tags": 0}
    assert await reload is True


async def test_poll_updates_clock_fields():
    role, fake = make_role()
    await role.refresh_catalog()
    role.monotonic.advance(30)
    fake.clock = [(fake.clock[0][0].replace(minute=1), fake.clock[0][1].replace(minute=1))]
    await role.poll_freshness()
    assert (role.clock.local.minute, role.clock_mono) == (1, 1030.0)


async def test_lifespan_drill_freeze_stamps_logs_and_cancels_loops(capfd):
    setup_logging()
    role, fake = make_role(settings=replace(SETTINGS, FRESH_POLL_S=0.05, DRILL_FRESHNESS="freeze_stamps"))
    assert role.freshness.freeze_stamps is True
    async with role.lifespan():
        await anyio.sleep(0.3)
    assert "УЧЕНИЯ: DRILL_FRESHNESS=freeze_stamps" in capfd.readouterr().err
    polls = poll_calls(fake)
    assert polls >= 2 and len([c for c in fake.calls if c == [live_all_sql()]]) == 1
    await anyio.sleep(0.2)
    assert poll_calls(fake) == polls                                    # на выходе циклы отменены


async def test_lifespan_without_drill_polls_after_catalog_attempt(capfd):
    setup_logging()
    role, fake = make_role(settings=replace(SETTINGS, FRESH_POLL_S=0.05))
    fake.fail = HistorianError("connect", "обрыв")
    assert role.freshness.freeze_stamps is False
    async with role.lifespan():
        await anyio.sleep(0.2)
    assert "УЧЕНИЯ" not in capfd.readouterr().err
    assert len(fake.calls) == 1                                         # один вход; опросы — без SQL
    j = role.freshness.to_json(role.monotonic())
    assert (j["error"], j["error_text"]) == ("connect", "нет связи с историаном")
    assert j["checked_at"] is not None


def test_health_historian_before_catalog():
    role, fake = make_role(settings=replace(SETTINGS, FRESH_POLL_S=3600.0))
    fake.fail = HistorianError("auth", "Login failed for user 'FAKEUSER'", msg_no=18456)
    with TestClient(create_app(SETTINGS, [role])) as c:
        wait_until(lambda: role.catalog_failures == 1 and role.freshness.error is not None)
        j = c.get("/health/historian").json()
        assert (j["catalog_age_s"], j["catalog_error"], j["error"]) == (None, "auth", "auth")
        assert j["gate"]["auth_latched"] is True
        assert c.get("/healthz/data").json() == {"ok": True, "detail": "каталог ещё не загружен"}
        assert c.get("/health/historian/").status_code == 404


def test_build_roles_reads_whitelist_and_credentials(tmp_path):
    settings = replace(SETTINGS, BDRV_ENV_FILE=write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<p>\n"),
                       WHITELIST_PATH=write(tmp_path, "# список\n20FAKE_001_PV\n"))
    [role] = build_roles(settings)
    assert isinstance(role, DataRole) and role.name == "data"
    assert role.gate.allowed == frozenset({"20FAKE_001_PV"}) and role.settings is settings


def test_build_roles_without_whitelist_file_is_empty(tmp_path, capfd):
    setup_logging()
    settings = replace(SETTINGS, BDRV_ENV_FILE=write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<p>\n"),
                       WHITELIST_PATH=str(tmp_path / "нет.txt"))
    [role] = build_roles(settings)
    assert role.gate.allowed == frozenset() and role.health() == (False, "белый список пуст или не найден")
    assert "белый список не найден" in capfd.readouterr().err
