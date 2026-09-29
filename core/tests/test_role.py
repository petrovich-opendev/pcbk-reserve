"""Роль «данные»: каталог со своим сроком и паузой, опрос свежести, ручки здоровья, учения, build_roles,
журнал событий и токены."""
import asyncio
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta

import anyio
import pytest
from fastapi.testclient import TestClient

from fakes import CATALOG_ROWS, CLOCK_ROWS, LIVE_ROWS, SUMMARY, WHITELIST, FakeHistorian
from helpers import EVENT as E
from helpers import SETTINGS, TEST_TOKENS, TOKEN, make_role, non_root, wait_until, wait_until_async, write
from pcbk_core.app import create_app
from pcbk_core.data import CONFIG_ERRORS, TOKENS_ERROR, DataRole
from pcbk_core.data.catalog import Catalog
from pcbk_core.data.events import WRITE_TIMEOUT_S
from pcbk_core.data.gate import GateResult, SlidingWindow
from pcbk_core.data.historian import HistorianError
from pcbk_core.data.sql import SUMMARY_COLUMNS, clock_sql, live_all_sql, live_sql, names_in, summary_sql
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


async def test_refresh_catalog_logs_counts_without_names(capfd, restore_logging):
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
    assert (role.catalog_error, role.catalog_failures) == ("timeout", 1)   # фон не дождался места — как срок
    await role.poll_freshness()
    j = role.freshness.to_json(role.monotonic())
    assert (j["error"], j["error_text"]) == ("timeout", "историан не ответил вовремя")
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


async def test_lifespan_drill_freeze_stamps_logs_and_cancels_loops(capfd, restore_logging):
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


async def test_lifespan_without_drill_polls_after_catalog_attempt(capfd, restore_logging):
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


def test_build_roles_without_whitelist_file_is_empty(tmp_path, capfd, restore_logging):
    setup_logging()
    settings = replace(SETTINGS, BDRV_ENV_FILE=write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<p>\n"),
                       WHITELIST_PATH=str(tmp_path / "нет.txt"))
    [role] = build_roles(settings)
    assert role.gate.allowed == frozenset() and role.health() == (False, "белый список пуст или не найден")
    assert "белый список не найден" in capfd.readouterr().err


async def test_unexpected_catalog_failure_is_query_and_loop_lives(capfd, restore_logging):
    setup_logging()
    role, fake = make_role(settings=replace(SETTINGS, FRESH_POLL_S=0.05))
    attempts = []

    async def broken():
        attempts.append(1)
        raise KeyError("20FAKE_001_PV")                                  # текст исключения — не в журнал

    role.refresh_catalog = broken
    async with role.lifespan():
        await anyio.sleep(0.2)
    assert (attempts, role.catalog_error, role.catalog_failures) == ([1], "query", 1)
    j = role.freshness.to_json(role.monotonic())
    assert (j["error"], j["error_text"]) == ("query", "историан вернул ошибку")
    err = capfd.readouterr().err
    assert "KeyError" in err and "FAKE" not in err


def test_page_texts_fit_80_chars():                                     # сторож показывает 80 знаков
    role, _ = make_role()
    details = [role.health()[1], make_role(whitelist=frozenset())[0].health()[1], *CONFIG_ERRORS.values(),
               "; ".join(CONFIG_ERRORS[k] for k in ("bdrv_invalid", "whitelist_unreadable"))]
    role.catalog = Catalog({}, frozenset(), loaded=True)
    role.catalog.whitelisted = 99999
    details.append(role.health()[1])
    assert details[-1] == "каталог: 99999 тегов в белом списке"
    details.append(TOKENS_ERROR)                                        # Д3б, F10
    role.events.consecutive_failures = 1
    role.events.last_error = "x" * 200
    details.append(role.health()[1])
    assert details[-1].startswith("журнал событий не пишется: x")
    assert max(len(d) for d in details) <= 80


async def test_config_error_role_starts_no_loops():
    fake = FakeHistorian()
    role = DataRole(replace(SETTINGS, FRESH_POLL_S=0.05), fake, WHITELIST, config_error=CONFIG_ERRORS["bdrv_invalid"])
    assert role.health() == (False, "bdrv.env: не заданы BDRV_* или неверная строка")
    async with role.lifespan():
        await anyio.sleep(0.2)
    assert fake.calls == [] and role.catalog_failures == 0
    assert role.freshness.to_json(role.monotonic())["checked_at"] is None


def bdrv(tmp_path, text="BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<p>\n"):
    return write(tmp_path, text)


def listed(tmp_path, text="20FAKE_001_PV\n"):
    return write(tmp_path, text)


def raw(tmp_path, data: bytes) -> str:
    path = tmp_path / "raw.bin"
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize("make_env, make_list, reason", [
    (lambda t: str(t / "нет.env"), listed, "bdrv.env не читается (нет файла или прав)"),
    (lambda t: str(t), listed, "bdrv.env не читается (нет файла или прав)"),                    # каталог
    (lambda t: bdrv(t, "BDRV_HOST=h\nBDRV_USER=u\n"), listed, "bdrv.env: не заданы BDRV_* или неверная строка"),
    (lambda t: bdrv(t, "BDRV_HOST=h\nсекрет-строка\n"), listed, "bdrv.env: не заданы BDRV_* или неверная строка"),
    (lambda t: raw(t, b"BDRV_PW=\xff\n"), listed, "bdrv.env: не заданы BDRV_* или неверная строка"),
    (bdrv, lambda t: listed(t, "20FAKE BAD\n"), "белый список не читается"),                   # недопустимое имя
    (bdrv, lambda t: raw(t, b"\xff\xfe20FAKE_001_PV\n"), "белый список не читается"),         # не UTF-8
    (bdrv, lambda t: str(t), "белый список не читается"),                                         # каталог
    (lambda t: str(t / "нет.env"), lambda t: str(t),
     "bdrv.env не читается (нет файла или прав); белый список не читается"),
])
def test_build_roles_bad_config_is_503_without_loops(tmp_path, capfd, restore_logging, make_env, make_list, reason):
    setup_logging()
    (tmp_path / "d").mkdir()
    settings = replace(SETTINGS, BDRV_ENV_FILE=make_env(tmp_path / "d"), WHITELIST_PATH=make_list(tmp_path),
                       FRESH_POLL_S=0.05)
    [role] = build_roles(settings)
    assert role.health() == (False, reason) and len(reason) <= 80
    with TestClient(create_app(settings, [role])) as c:
        time.sleep(0.2)
        r = c.get("/healthz/data")
        assert (r.status_code, r.json()["detail"]) == (503, reason)
        assert c.get("/healthz").status_code == 503
        j = c.get("/health/historian").json()
        assert (j["checked_at"], j["catalog_error"], j["gate"]["sent_names_total"]) == (None, None, 0)
    assert role.catalog_failures == 0 and role.gate.stats()["in_flight"] == {"user": 0, "background": 0}
    err = capfd.readouterr().err
    assert "ERROR" in err and "секрет" not in err and "FAKE" not in err and "0xff" not in err


# Д3б, задача 2 (сверх брифа): ворота роли пускают сводку — literal_ok=allowed_literal


async def test_role_gate_passes_summary():
    role, _ = make_role()
    t6 = datetime(2026, 10, 1, 6)
    r = await role.gate.run([summary_sql(["20FAKE_001_PV", "20FAKE_003_PV"], t6, t6 + timedelta(hours=8))])
    assert r.sent == ("20FAKE_001_PV", "20FAKE_003_PV") and role.gate.stats()["refused_unlisted"] == 0
    assert r.rows == [[("20FAKE_001_PV", *(SUMMARY["20FAKE_001_PV"][c] for c in SUMMARY_COLUMNS))]]


# --- Д3б, задача 4: журнал событий, токены, здоровье ---

async def test_freshness_poll_writes_sent():                                    # Review Focus 2
    role, _ = make_role()
    await role.refresh_catalog()
    await role.poll_freshness()
    fresh, catalog = role.events.recent()[:2]
    assert (fresh.caller, fresh.channel, fresh.tool, fresh.outcome) == ("system", "system", "freshness", "ok")
    assert set(fresh.sent) == {"20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP"} <= WHITELIST
    assert (catalog.tool, catalog.sent) == ("catalog", ()) and catalog.summary.startswith("без имён")


async def test_system_event_outcome_is_outcome_not_raw_code():                  # F11
    role, fake = make_role()
    fake.fail = HistorianError("connect", "обрыв")
    await role.refresh_catalog()
    e = role.events.recent()[0]
    assert (e.tool, e.outcome) == ("catalog", "unavailable") and "connect" in e.summary


def test_events_disk_full_turns_health_red(monkeypatch):                       # Review Focus 4
    role, _ = make_role()
    def full(e): raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(role.events, "_insert", full)
    with pytest.raises(sqlite3.OperationalError):
        role.events.write(E)
    ok, detail = role.health()
    assert ok is False and detail.startswith("журнал событий не пишется") and "disk is full" in detail


@non_root
async def test_events_check_off_loop_and_health_without_io(tmp_path, monkeypatch):   # F9, Review Focus 4
    d = tmp_path / "ro"; d.mkdir(); d.chmod(0o500)
    role, _ = make_role(db_dir=d)
    threads, real = [], role.events.writable
    monkeypatch.setattr(role.events, "writable", lambda: threads.append(threading.get_ident()) or real())
    await role.check_events()                                   # в фоне: asyncio.to_thread
    assert len(threads) == 1 and threads[0] != threading.get_ident()      # проба — не в потоке цикла
    monkeypatch.setattr(role.events, "writable", lambda: pytest.fail("health() не делает ввода-вывода"))
    ok, detail = role.health()
    assert ok is False and detail.startswith("журнал событий не пишется: ") and len(detail) <= 80


def test_constructor_without_io_and_empty_tokens(tmp_path):                     # F4, F18
    role = DataRole(replace(SETTINGS, DB_PATH=str(tmp_path / "нет" / "core.db")), FakeHistorian(), WHITELIST)
    assert role.tokens.ids == frozenset() and role.tokens.caller("x") is None
    assert role.health() == (True, "каталог ещё не загружен") and not (tmp_path / "нет").exists()
    assert make_role(tokens=TEST_TOKENS)[0].tokens.caller(TOKEN) == "student-01"


# --- сверх брифа ---

async def test_catalog_event_fields_and_duration():
    role, _ = make_role()
    await role.refresh_catalog()
    [e] = role.events.recent()
    assert (e.caller, e.agent, e.channel, e.requested, e.tags, e.cache, e.cost) == \
           ("system", None, "system", (), (), "none", 0.0)
    assert e.summary == "без имён: 13 тегов, в белом списке 6, нет в историане 0, живых 4"
    assert e.rows == 1 + 13 + 5 and e.duration_ms >= 0 and e.ts.utcoffset() is not None


async def test_catalog_gate_busy_event_is_timeout_with_raw_code():
    role, fake = make_role(settings=replace(SETTINGS, CATALOG_DEADLINE_S=0.3))
    fake.delay_s, fake.slow = 0.6, r"Value IS NOT NULL$"
    hold = asyncio.ensure_future(role.gate.run([live_all_sql()], lane="background"))   # фоновое место занято
    await anyio.sleep(0.05)
    await role.refresh_catalog()
    e = role.events.recent()[0]
    assert (e.tool, e.outcome, e.sent) == ("catalog", "timeout", ()) and "busy" in e.summary
    await hold


@pytest.mark.parametrize("prepare, code, outcome", [
    ("none", "catalog", "unavailable"),                                   # каталог не пробовали — без SQL
    ("no_tags", "no_tags", "unavailable"),
    ("query", "query", "error"),
    ("no_rows", "no_rows", "unavailable"),
])
async def test_freshness_failures_are_events(prepare, code, outcome):
    role, fake = make_role(whitelist=frozenset({"25FAKE_007_CLS"}) if prepare == "no_tags" else WHITELIST)
    if prepare != "none":
        await role.refresh_catalog()
    if prepare == "query":
        fake.fail = HistorianError("query", "Invalid object name")
    if prepare == "no_rows":
        fake.live.clear()
    await role.poll_freshness()
    e = role.events.recent()[0]
    assert (e.tool, e.outcome, role.freshness.error) == ("freshness", outcome, code) and code in e.summary
    assert e.sent == (() if prepare in ("none", "no_tags") else role.freshness_tags)


async def test_freshness_skipped_by_gate_is_busy_event():
    role, fake = make_role()
    await role.refresh_catalog()
    role.gate.deadline_s = 0.3                                          # опрос ждёт место не дольше 0,2 с
    fake.delay_s, fake.slow = 0.6, r"FROM Tag t "
    reload = asyncio.ensure_future(role.refresh_catalog())             # каталог держит фоновое место
    await anyio.sleep(0.05)
    await role.poll_freshness()
    e = role.events.recent()[0]
    assert (e.tool, e.outcome, e.sent) == ("freshness", "busy", ()) and "busy" in e.summary
    assert await reload is True


async def test_event_write_failure_does_not_break_poll(monkeypatch):
    role, _ = make_role()
    def full(e): raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(role.events, "_insert", full)
    assert await role.refresh_catalog() is True
    await role.poll_freshness()
    assert role.freshness.error is None and role.events.consecutive_failures == 2
    assert role.health() == (False, "журнал событий не пишется: OperationalError: database or disk is full")


async def test_unexpected_loop_failures_are_error_events():
    role, _ = make_role(settings=replace(SETTINGS, FRESH_POLL_S=0.05))

    async def broken():
        raise KeyError("20FAKE_001_PV")

    role.refresh_catalog = broken
    role.poll_freshness = broken
    async with role.lifespan():
        await wait_until_async(lambda: role.events.count("system") >= 2)       # каталог и хотя бы один опрос
    catalog = role.events.recent(channel="system")[-1]
    assert (catalog.tool, catalog.outcome, catalog.sent) == ("catalog", "error", ())
    assert catalog.summary == "без имён: непредвиденный сбой (KeyError)"               # текст исключения — нет
    fresh = role.events.recent()[0]
    assert (fresh.tool, fresh.outcome) == ("freshness", "error") and "FAKE" not in fresh.summary


async def test_lifespan_opens_db_first_and_checks_in_background(tmp_path):     # F23, F9
    role, _ = make_role(db_dir=tmp_path)
    async with role.lifespan():
        assert (tmp_path / "core.db").exists()                                  # сразу после старта
        await wait_until_async(lambda: role.events.writable_ok is not None)
        assert role.events.writable_ok is True
    bad = DataRole(replace(SETTINGS, DB_PATH=str(tmp_path / "cfg" / "core.db")), FakeHistorian(), WHITELIST,
                   config_error=CONFIG_ERRORS["bdrv_invalid"])
    (tmp_path / "cfg").mkdir()
    async with bad.lifespan():
        assert (tmp_path / "cfg" / "core.db").exists()                          # и при ошибке настройки
    assert bad.events.writable_ok is None                                       # фоновых циклов нет


@non_root
async def test_lifespan_with_unopenable_db_lives_with_reason(tmp_path, capfd, restore_logging):   # F4
    setup_logging()
    d = tmp_path / "ro"; d.mkdir(); d.chmod(0o500)
    role, _ = make_role(db_dir=d, settings=replace(SETTINGS, FRESH_POLL_S=0.05))
    async with role.lifespan():
        await wait_until_async(lambda: role.catalog.loaded)
        ok, detail = role.health()
        assert ok is False and detail.startswith("журнал событий не пишется: ") and role.catalog.loaded
    assert "журнал событий" in capfd.readouterr().err


def test_health_order_config_whitelist_tokens_events():
    role, _ = make_role()
    role.events.consecutive_failures = 1
    role.tokens_error = TOKENS_ERROR
    assert role.health() == (False, TOKENS_ERROR)
    role.whitelist = frozenset()
    assert role.health() == (False, "белый список пуст или не найден")
    role.config_error = CONFIG_ERRORS["bdrv_invalid"]
    assert role.health() == (False, CONFIG_ERRORS["bdrv_invalid"])
    assert len(TOKENS_ERROR) == 44
    fresh, _ = make_role()
    fresh.events.writable_ok = False
    assert fresh.health() == (False, "журнал событий не пишется: нет записи")


# --- доводка по ревью: sent — только от ворот (I2, M2), запись не держит опрос (M1) ---

GATE_SENT = ("20FAKE_003_PV",)          # не из набора свежести: отличить «от ворот» от «из запроса»


def stub_gate(role: DataRole, answer) -> list[list[str]]:
    """Подменяет role.gate.run: answer(statements) отдаёт GateResult или бросает; возвращает список вызовов."""
    calls: list[list[str]] = []

    async def run(statements, *, lane="user", deadline_s=None):
        calls.append(list(statements))
        return answer(list(statements))

    role.gate.run = run
    return calls


def _timeout_after_send(statements):
    raise HistorianError("timeout", "долго", GATE_SENT)


@pytest.mark.parametrize("answer, outcome, summary", [
    (lambda st: GateResult(rows=[list(CLOCK_ROWS), [LIVE_ROWS[0]]], sent=GATE_SENT), "ok", "тегов 3, строк Live 1"),
    (_timeout_after_send, "timeout", "timeout"),                                  # сбой после передачи (F12)
    (lambda st: GateResult(rows=[[("не часы",)], []], sent=GATE_SENT), "error", "query: часы не того вида"),
    (lambda st: GateResult(rows=[list(CLOCK_ROWS), []], sent=GATE_SENT), "unavailable", "no_rows"),
    (lambda st: GateResult(rows=[list(CLOCK_ROWS), None], sent=GATE_SENT), "error",                    # M2
     "непредвиденный сбой (TypeError)"),
])
async def test_freshness_event_sent_is_gates_not_requested(answer, outcome, summary):   # I2
    role, _ = make_role()
    await role.refresh_catalog()
    assert set(role.freshness_tags).isdisjoint(GATE_SENT)
    calls = stub_gate(role, answer)
    await role.poll_freshness()                                                   # не бросает и при M2
    e = role.events.recent()[0]
    assert (e.tool, e.sent, e.outcome, e.summary) == ("freshness", GATE_SENT, outcome, summary)
    assert calls == [[clock_sql(), live_sql(role.freshness_tags)]]


async def test_catalog_event_sent_is_gates_union():                               # I2
    role, _ = make_role()

    def ok(statements):
        if statements == [live_all_sql()]:
            return GateResult(rows=[list(LIVE_ROWS)], sent=("20FAKE_001_PV",))
        return GateResult(rows=[list(CLOCK_ROWS), list(CATALOG_ROWS)], sent=GATE_SENT)

    stub_gate(role, ok)
    assert await role.refresh_catalog() is True
    e = role.events.recent()[0]
    assert (e.outcome, e.sent) == ("ok", (*GATE_SENT, "20FAKE_001_PV"))

    def second_fails_after_send(statements):
        if statements == [live_all_sql()]:
            raise HistorianError("timeout", "долго", ("20FAKE_001_PV", *GATE_SENT))
        return GateResult(rows=[list(CLOCK_ROWS), list(CATALOG_ROWS)], sent=GATE_SENT)

    stub_gate(role, second_fails_after_send)
    assert await role.refresh_catalog() is False
    e = role.events.recent()[0]
    assert (e.outcome, e.sent) == ("timeout", (*GATE_SENT, "20FAKE_001_PV"))      # без повторов, по порядку


async def test_locked_journal_holds_poll_at_most_write_timeout(tmp_path):         # M1
    role, _ = make_role(db_dir=tmp_path)
    await role.refresh_catalog()
    holder = sqlite3.connect(tmp_path / "core.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")                                             # блокировку держит другой
    try:
        t = time.monotonic()
        await role.poll_freshness()
        took = time.monotonic() - t
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert took < WRITE_TIMEOUT_S + 1.0 and role.freshness.error is None
    assert role.events.consecutive_failures == 1 and "locked" in role.events.last_error


# --- Д3б, задача 5: токены в build_roles (F4) ---

def test_build_roles_unreadable_tokens_is_red_not_crash(tmp_path, capfd, restore_logging):
    setup_logging()
    settings = replace(SETTINGS, BDRV_ENV_FILE=write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<p>\n"),
                       WHITELIST_PATH=write(tmp_path, "20FAKE_001_PV\n"), TOKENS_FILE=str(tmp_path / "нет"))
    [role] = build_roles(settings)
    assert role.config_error is None and role.tokens.ids == frozenset()
    assert role.health() == (False, TOKENS_ERROR) and "токены" in capfd.readouterr().err


# --- сверх брифа ---

def test_build_roles_reads_tokens_and_bad_line_is_named_without_content(tmp_path, capfd, restore_logging):
    setup_logging()
    base = dict(BDRV_ENV_FILE=write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<p>\n"),
                WHITELIST_PATH=write(tmp_path, "20FAKE_001_PV\n"))
    [role] = build_roles(replace(SETTINGS, **base))                       # файл сессии тестов: student-01
    assert role.tokens.caller(TOKEN) == "student-01" and role.tokens_error is None
    [bad] = build_roles(replace(SETTINGS, **base, TOKENS_FILE=write(tmp_path, "student-01 секрет-строка\n")))
    assert bad.tokens.ids == frozenset() and bad.health() == (False, TOKENS_ERROR) and bad.config_error is None
    err = capfd.readouterr().err
    assert "строка 1" in err and "секрет" not in err


async def test_tokens_error_keeps_background_loops(tmp_path):
    role, fake = make_role(db_dir=tmp_path, tokens=None, settings=replace(SETTINGS, FRESH_POLL_S=0.05))
    role.tokens_error = TOKENS_ERROR
    async with role.lifespan():
        await wait_until_async(lambda: role.catalog.loaded)                # это не ошибка настройки
    assert role.health() == (False, TOKENS_ERROR) and fake.calls
