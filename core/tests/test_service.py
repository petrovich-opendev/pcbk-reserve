"""Служба данных: tag_now, tag_period, catalog_search и калькуляторы tag_series, calc_runtime — ответы, отказы,
события, кэш, single-flight."""
import asyncio
import json
import sqlite3
import statistics
import threading
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from fakes import CLOCK_ROWS, LIVE_ROWS, FakeHistorian
from helpers import SETTINGS, make_role, make_service, sent_of
from pcbk_core import spc as spc_lib
from pcbk_core.data import CONFIG_ERRORS
from pcbk_core.data import service as service_module
from pcbk_core.data.catalog import Catalog
from pcbk_core.data.gate import GateRefused, GateResult, SlidingWindow
from pcbk_core.data.historian import HistorianError, parse_clock
from pcbk_core.data.periods import Period
from pcbk_core.data.service import (AVG_KIND, CLAMP_NOTE, DRILL_SUMMARY, EQUIPMENT_MENTIONS_CAP,
                                    EQUIPMENT_MENTION_NOTE, EQUIPMENT_NOTES, EQUIPMENT_RUN_CAP,
                                    EQUIPMENT_RUN_LIMIT_MESSAGE, EQUIPMENT_STALE_NOTE, EQUIPMENT_UNITS_CAP,
                                    ERROR_MESSAGE, FAIL_STALE_S, FAIL_STREAK, GRID_NOTE, ITEM_TEXTS, MESSAGES,
                                    NO_LAST_NOTE, OPEN_NOTE, SHIFT_NOTE, SIMILAR_NOTE, SOURCE_LIVE, SOURCE_SERIES,
                                    SOURCE_STATE, SOURCE_SUMMARY, SPC_BOUNDS_NOTE, SPC_CONTROL_NOTE,
                                    SPC_FEW_POINTS_NOTE, SPC_GRID_NOTE, SPC_NOT_ENOUGH_NOTE, STD_KIND, TOOL_WINDOW)
from pcbk_core.data.sql import HAS_LAST, SUMMARY_COLUMNS, clock_sql, live_sql
from pcbk_core.logs import setup_logging
from pcbk_core.places import PLACES

pytestmark = pytest.mark.anyio


@pytest.fixture
async def svc(tmp_path):
    s, fake = make_service(tmp_path)          # роль с загруженным каталогом; fake.calls очищены
    return s, fake


def _equipment_service(tmp_path, rows, whitelist, live=None):
    """Роль со своим каталогом (вымышленные теги Д19, не из fakes.CATALOG_ROWS) — для equipment: live задаёт
    строки Live тегов _RUN, читаемые тем же путём, что tag_now."""
    fake = FakeHistorian()
    if live:
        fake.live.update(live)
    role, fake = make_role(db_dir=tmp_path, fake=fake, whitelist=frozenset(whitelist))
    role._catalog_loaded(Catalog.from_rows(rows, [], role.whitelist), parse_clock(CLOCK_ROWS), role.monotonic())
    fake.calls.clear()
    fake.timeouts.clear()
    return role.service, fake


async def test_tag_now_answer_says_what_and_when(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20fake_001_pv"])
    it = r["items"][0]
    assert (r["status"], r["tz"], r["source"], r["cache"]) == ("ok", "UTC+05:00", SOURCE_LIVE, "miss")
    assert (it["tag"], it["value"], it["age_s"], it["quality"], it["time"]) == \
           ("20FAKE_001_PV", 12.5, 42, "хорошее", "2026-10-01T11:59:18+05:00")


async def test_events_requested_vs_sent(svc):                                  # Review Focus 2
    s, fake = svc
    await s.tag_now("student-01", "http", ["20FAKE_001_PV", "16FAKE_009_PV", "20FAKE_001_PV' OR 1=1--",
                                           "20FAKE_005_LMN", "20FAKE_001_PV\n"])
    e = s.role.events.recent()[0]
    assert "16FAKE_009_PV" in e.requested and "20FAKE_001_PV' OR 1=1--" in e.requested
    assert e.sent == ("20FAKE_001_PV",) and sent_of(fake) == ["20FAKE_001_PV"]


async def test_tag_now_statuses_distinct_and_no_foreign_metadata(svc):
    s, _ = svc
    r = await s.tag_now("student-01", "http", ["20FAKE_003_PV", "16FAKE_009_PV", "20FAKE_404_PV"])
    assert r["status"] == "partial" and [i["status"] for i in r["items"]] == ["no_value", "unlisted", "unknown"]
    assert not {"description", "unit", "kind", "min_eu", "max_eu"} & set(r["items"][1])


async def test_tag_now_null_value_is_no_value(svc):                             # Review Focus 1
    s, fake = svc
    fake.live["20FAKE_002_SP"] = (datetime(2026, 10, 1, 11, 0), None, 0)
    it = (await s.tag_now("student-01", "http", ["20FAKE_002_SP"]))["items"][0]
    assert (it["status"], it["text"], it["time"]) == ("no_value", "нет текущего значения", "2026-10-01T11:00:00+05:00")


async def test_tag_now_future_timestamp_clamped(svc):
    s, fake = svc
    fake.live["20FAKE_001_PV"] = (datetime(2026, 10, 1, 12, 0, 31), 12.5, 0)
    fake.live["20FAKE_004_PV"] = (datetime(2026, 10, 1, 12, 1, 30), 3.2, 0)
    a, b = (await s.tag_now("student-01", "http", ["20FAKE_001_PV", "20FAKE_004_PV"]))["items"]
    assert (a["age_s"], a.get("item_notes", [])) == (0, [])
    assert b["age_s"] == 0 and "впереди часов историана на 90 с" in b["item_notes"][0]


async def test_quality_and_discrete_labels(svc):
    s, _ = svc
    a, b = (await s.tag_now("student-01", "http", ["20FAKE_004_PV", "25FAKE_007_CLS"]))["items"]
    assert a["quality"] == "недостоверное (код 64)" and b["state_label"].startswith("закрыт")


async def test_tag_period_answer_says_what_was_computed(svc):
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    it = r["items"][0]
    assert r["status"] == "ok" and it["range"] == pytest.approx(5.0) == it["max"] - it["min"]
    assert (r["source"], r["avg_kind"]["code"], r["std_kind"]["code"]) == (SOURCE_SUMMARY, AVG_KIND, STD_KIND)
    assert r["period"]["start"] == "2026-09-30T22:00:00+05:00" and r["period"]["shift_grid_assumed"] is True
    assert SHIFT_NOTE in r["notes"] and ("last" in it) is HAS_LAST
    assert r["period"]["open"] is False and OPEN_NOTE not in r["notes"]


async def test_open_note_follows_period_open(svc):                             # F21
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="last_hour")
    assert r["period"]["open"] is True and OPEN_NOTE in r["notes"]


async def test_config_error_never_enters_historian(tmp_path):                  # F5, Review Focus 6
    role, fake = make_role(db_dir=tmp_path, config_error=CONFIG_ERRORS["whitelist_unreadable"])
    s = role.service
    outs = [await s.catalog_search("student-01", "http", "расход"),
            await s.tag_now("student-01", "http", ["20FAKE_001_PV"]),
            await s.tag_period("student-01", "mcp", ["20FAKE_001_PV"], period="prev_shift"),
            await s.refuse_args("student-01", "mcp", "tag_now", ["x"], "tags: нужен список")]
    assert {(o["status"], o["message"]) for o in outs} == {("unavailable", "белый список не читается")}
    assert fake.calls == [] and role.gate.stats()["sent_names_total"] == 0      # ноль вызовов QueryFn
    events = role.events.recent()
    assert [e.outcome for e in events] == ["unavailable"] * 4 and {e.sent for e in events} == {()}


async def test_tag_period_null_row_is_no_data(svc):                             # Review Focus 1
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_002_SP"], period="prev_shift")
    assert (r["items"][0]["status"], r["items"][0]["text"]) == ("no_data", "за период данных нет")


async def test_tag_period_four_distinct_refusals(svc):
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_004_PV", "16FAKE_009_PV",
                                                  "20FAKE_404_PV", "25FAKE_007_CLS"], period="prev_shift")
    assert [i["status"] for i in r["items"]] == ["ok", "no_data", "unlisted", "unknown", "discrete"]
    assert len({i["text"] for i in r["items"][1:]}) == 4


async def test_refusals_send_no_sql_and_are_events(svc):
    s, fake = svc
    outs = [(await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_002_SP"],
                                start="2026-09-01 00:00", end="2026-09-14 00:00"))["status"],
            (await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift",
                                start="2026-10-01 10:00", end="2026-10-01 11:00"))["status"],
            (await s.tag_now("student-01", "http", ["16FAKE_009_PV"]))["status"],
            (await s.tag_now("student-01", "http", [f"20FAKE_{i:03d}_PV" for i in range(17)]))["status"]]
    assert outs == ["budget", "refused", "refused", "refused"] and fake.calls == []
    assert [e.outcome for e in s.role.events.recent()[:4]] == ["refused", "refused", "refused", "budget"]


async def test_rate_and_single_flight(svc):
    s, fake = svc
    fake.delay_s = 0.1
    rs = await asyncio.gather(*(s.tag_period(f"student-{n:02d}", "http", ["20FAKE_001_PV"], period="prev_shift")
                                for n in PLACES))
    assert sum("AnalogSummaryHistory" in q for c in fake.calls for q in c) == 1
    assert sorted(r["cache"] for r in rs) == ["miss"] + ["shared"] * (len(PLACES) - 1)
    for _ in range(59):
        await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "rate"


async def test_historian_errors_are_named(svc):
    s, fake = svc
    for code, want in (("connect", "нет связи с историаном"), ("auth", MESSAGES["auth"])):
        fake.fail = HistorianError(code, "x")
        r = await s.tag_now(f"student-{code}", "http", ["20FAKE_004_PV"])
        assert (r["status"], r["message"]) == ("unavailable", want)
        s.role.gate.clear_pause()          # сброс паузы и защёлки между случаями


async def test_timeout_event_keeps_sent(svc):                                   # Review Focus 2
    s, fake = svc
    fake.fail = HistorianError("timeout", "долго")      # ворота добавят sent — SQL уже ушёл
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    e = s.role.events.recent()[0]
    assert (r["status"], e.outcome, e.sent) == ("timeout", "timeout", ("20FAKE_001_PV",))


async def test_cache_hit_event_has_tags(svc):
    s, _ = svc
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    await s.tag_now("student-02", "http", ["20fake_001_pv"])
    e = s.role.events.recent()[0]
    assert (e.cache, e.tags, e.sent) == ("hit", ("20FAKE_001_PV",), ())


async def test_unexpected_error_is_error_event(svc):
    s, fake = svc
    fake.fail = RuntimeError("boom")
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert r["status"] == "error" and s.role.events.recent()[0].outcome == "error"


async def test_tool_answers_when_events_fail(svc, monkeypatch):                 # Review Focus 4
    s, _ = svc
    def full(e): raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(s.role.events, "_insert", full)
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "ok"
    assert s.role.health()[0] is False


async def test_call_log_line_without_values(svc, capfd, restore_logging):
    setup_logging()                                    # F13: как test_role.py Д3а, без autouse
    s, _ = svc
    await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    err = capfd.readouterr().err
    assert "tool=tag_period" in err and "cost=0.333" in err and "12.4" not in err


async def test_error_detail_never_leaves_process(svc, capfd, restore_logging):
    setup_logging()                                    # иначе проверка журнала холостая
    s, fake = svc
    fake.fail = HistorianError("auth", "Login failed for user 'FAKEUSER'")
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    e = s.role.events.recent(channel="http")[0]
    assert "FAKEUSER" not in e.summary and "FAKEUSER" not in capfd.readouterr().err


# --- сверх брифа: заметки ревью задач 2–4 ---

GATE_SENT = ("20FAKE_003_PV",)          # не то, что спросили: отличить «от ворот» от «из запроса»


def stub_gate(s, answer) -> list[list[str]]:
    """Подменяет ворота роли: answer(statements) отдаёт GateResult или бросает; возвращает вызовы."""
    calls: list[list[str]] = []

    async def run(statements, *, lane="user", deadline_s=None):
        calls.append(list(statements))
        return answer(list(statements))

    s.role.gate.run = run
    return calls


def _live_answer(statements):
    return GateResult(rows=[[LIVE_ROWS[0]]], sent=GATE_SENT)


def _timeout_after_send(statements):
    raise HistorianError("timeout", "долго", GATE_SENT)


def _bad_rows(statements):
    return GateResult(rows=[[("20FAKE_001_PV",)]], sent=GATE_SENT)       # строка не той ширины


def _busy(statements):
    raise GateRefused("busy")


@pytest.mark.parametrize("answer, outcome", [
    (_live_answer, "ok"), (_timeout_after_send, "timeout"), (_bad_rows, "error"), (_busy, "busy")])
async def test_event_sent_is_gates_not_requested(svc, answer, outcome):         # sent — только от ворот
    s, _ = svc
    calls = stub_gate(s, answer)
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    e = s.role.events.recent()[0]
    assert calls == [[live_sql(["20FAKE_001_PV"])]] and (r["status"], e.outcome) == (outcome, outcome)
    assert e.sent == (() if outcome == "busy" else GATE_SENT)


@pytest.mark.parametrize("error, status, message", [
    (GateRefused("busy"), "busy", MESSAGES["busy"]),
    (GateRefused("rate"), "rate", MESSAGES["global_rate"]),
    (GateRefused("unlisted"), "error", ERROR_MESSAGE),
    (HistorianError("timeout", "x"), "timeout", MESSAGES["timeout"]),
    (HistorianError("query", "x"), "error", MESSAGES["query"]),
])
async def test_gate_and_historian_codes_to_statuses(svc, error, status, message):     # F11
    s, _ = svc

    def fail(statements):
        raise error

    stub_gate(s, fail)
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    assert (r["status"], r["message"], s.role.events.recent()[0].outcome) == (status, message, status)


async def test_budget_checked_before_summary_sql(svc, monkeypatch):            # ворота не мерят окно
    s, fake = svc
    order: list[str] = []
    real_budget, real_sql = service_module.check_budget, service_module.summary_sql
    monkeypatch.setattr(service_module, "check_budget", lambda *a: order.append("budget") or real_budget(*a))
    monkeypatch.setattr(service_module, "summary_sql", lambda *a: order.append("sql") or real_sql(*a))
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV", "20FAKE_002_SP"],
                           start="2026-09-01T00:00", end="2026-09-14T00:00")
    assert r["status"] == "budget" and "sql" not in order and fake.calls == []
    assert r["cost_tag_days"] == 26.0 and r["period"]["start"] == "2026-09-01T00:00:00+05:00"
    order.clear()
    assert (await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift"))["status"] == "ok"
    assert order.index("budget") < order.index("sql")


async def test_shared_snapshot_is_never_mutated(svc):                          # заметка ревью задачи 3
    s, fake = svc
    fake.delay_s = 0.05
    a, b = await asyncio.gather(s.tag_now("student-01", "http", ["20FAKE_001_PV"]),
                                s.tag_now("student-02", "http", ["20FAKE_001_PV", "20FAKE_404_PV"]))
    assert {a["cache"], b["cache"]} == {"miss", "shared"} and a["items"][0] is not b["items"][0]
    a["items"][0]["value"] = -1.0
    a["items"].append("мусор")
    a["notes"].append("мусор")
    c = await s.tag_now("student-03", "http", ["20FAKE_001_PV"])
    assert c["cache"] == "hit" and c["items"][0]["value"] == 12.5 and c["notes"] == [] and len(c["items"]) == 1
    assert [i["status"] for i in b["items"]] == ["ok", "unknown"] and b["items"][0]["value"] == 12.5


async def test_cache_hit_keeps_callers_own_period_fields(svc):
    s, fake = svc
    today = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="today")
    custom = await s.tag_period("student-02", "http", ["20FAKE_001_PV"], start="2026-10-01T00:00",
                                end="2026-10-01T13:00")               # тот же период после отсечки «сейчас»
    assert (today["cache"], custom["cache"]) == ("miss", "hit") and len(fake.calls) == 1
    assert (today["period"]["preset"], today["period"]["clamped"]) == ("today", False)
    assert (custom["period"]["preset"], custom["period"]["clamped"]) == (None, True)
    assert CLAMP_NOTE in custom["notes"] and CLAMP_NOTE not in today["notes"]
    assert OPEN_NOTE in custom["notes"] and (NO_LAST_NOTE in custom["notes"]) is not HAS_LAST


async def test_waiters_of_failed_flight_send_nothing(svc):
    s, fake = svc
    fake.delay_s, fake.fail = 0.05, HistorianError("timeout", "долго")
    rs = await asyncio.gather(*(s.tag_now(f"student-{n:02d}", "http", ["20FAKE_001_PV"]) for n in (1, 2, 3)))
    assert {r["status"] for r in rs} == {"timeout"} and len(fake.calls) == 1
    sent = sorted((e.cache, e.sent) for e in s.role.events.recent())
    assert sent == [("miss", ("20FAKE_001_PV",)), ("shared", ()), ("shared", ())]


async def test_catalog_not_loaded_is_named_not_unknown(tmp_path):              # заметка ревью задачи 4
    role, fake = make_role(db_dir=tmp_path)
    s = role.service
    outs = [await s.tag_now("student-01", "http", ["20FAKE_001_PV"]),
            await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift"),
            await s.catalog_search("student-01", "http", "расход")]
    assert {(o["status"], o["message"]) for o in outs} == {("unavailable", MESSAGES["catalog"])}
    assert all("items" not in o for o in outs) and fake.calls == []


async def test_stale_clock_is_read_first(svc):
    s, fake = svc
    s.role.monotonic.advance(301)                       # опрос свежести молчит дольше 300 с
    r = await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert fake.calls == [[clock_sql()], [live_sql(["20FAKE_001_PV"])]]
    assert (r["asof"], r["items"][0]["age_s"]) == ("2026-10-01T12:00:00+05:00", 42)
    s.role.monotonic.advance(20)                        # часы свежие — без второго запроса часов
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert fake.calls[2:] == [[live_sql(["20FAKE_001_PV"])]]


async def test_catalog_search_answer_and_notes(svc):
    s, fake = svc
    r = await s.catalog_search("student-01", "http", "расход массы")
    assert (r["status"], r["similar"], r["total"], r["notes"]) == ("ok", False, 2, [])
    first = r["matches"][0]
    assert first == {"tag": "20FAKE_001_PV", "description": "Факт. знач. - расход массы", "unit": "л/час",
                     "kind": "analog", "min_eu": 0.0, "max_eu": 100.0, "live": True, "description_truncated": False}
    none = await s.catalog_search("student-01", "http", "20FAKE_001_XX")
    assert none["similar"] is True and SIMILAR_NOTE in none["notes"] and none["matches"]
    assert all(m["tag"] != "16FAKE_009_PV" for m in none["matches"]) and fake.calls == []
    empty = await s.catalog_search("student-01", "http", "   ")
    assert (empty["matches"], empty["total"], empty["notes"]) == ([], 0, [])     # похожих нет — и заметки нет
    e = s.role.events.recent()[1]
    assert (e.tool, e.outcome, e.sent, e.requested) == ("catalog_search", "ok", (), ("20FAKE_001_XX",))


async def test_answers_are_strict_json_and_nan_is_no_value(svc):
    s, fake = svc
    fake.live["20FAKE_001_PV"] = (datetime(2026, 10, 1, 11, 59), float("nan"), 0)
    fake.summary["20FAKE_001_PV"]["StdDev"] = float("inf")
    fake.summary["20FAKE_001_PV"]["PercentGood"] = 97.55
    now = await s.tag_now("student-01", "http", ["20FAKE_001_PV", "25FAKE_007_CLS"])
    period = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    search = await s.catalog_search("student-01", "http", "клапан")
    for answer in (now, period, search):
        json.dumps(answer, allow_nan=False)                       # только str, float, int, bool, null
    assert now["items"][0]["status"] == "no_value" and period["items"][0]["std"] is None
    assert period["items"][0]["quality_note"] == "доля достоверных 97,5 % — ниже 99 %"


async def test_argument_refusals_are_events(svc):
    s, fake = svc
    outs = [await s.tag_now("student-01", "mcp", "20FAKE_001_PV"),
            await s.tag_now("student-01", "mcp", []),
            await s.tag_now("student-01", "mcp", ["x" * 129]),
            await s.tag_period("student-01", "mcp", ["20FAKE_001_PV"], period="last_year"),
            await s.tag_period("student-01", "mcp", ["20FAKE_001_PV"], start="2026-10-02T00:00",
                               end="2026-10-02T06:00"),
            await s.catalog_search("student-01", "mcp", "", 10),
            await s.catalog_search("student-01", "mcp", "расход", 51),
            await s.refuse_args("student-01", "mcp", "tag_now", ["x"], "tags: нужен список")]
    assert all(o["status"] == "refused" and o["message"].startswith(MESSAGES["args"]) for o in outs)
    assert "позже текущего времени историана" in outs[4]["message"] and fake.calls == []
    events = s.role.events.recent()
    assert len(events) == 8 and {(e.outcome, e.channel, e.sent) for e in events} == {("refused", "mcp", ())}


async def test_none_queryable_refuses_with_items(svc):
    s, fake = svc
    r = await s.tag_period("student-01", "http", ["25FAKE_007_CLS", "16FAKE_009_PV", "25fake_007_cls "],
                           period="prev_shift")
    assert (r["status"], r["message"]) == ("refused", MESSAGES["none"]) and fake.calls == []
    assert [(i["tag"], i["status"], i["text"]) for i in r["items"]] == [
        ("25FAKE_007_CLS", "discrete", ITEM_TEXTS["discrete"]), ("16FAKE_009_PV", "unlisted", ITEM_TEXTS["unlisted"])]


async def test_percent_good_zero_is_no_data_even_with_numbers(svc):               # R9
    s, fake = svc
    fake.summary["20FAKE_001_PV"]["PercentGood"] = 0.0             # числа есть, достоверных — ноль
    r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    assert (r["status"], r["items"][0]["status"]) == ("partial", "no_data")
    assert s.role.events.recent()[0].tags == ("20FAKE_001_PV",)       # no_data — среди tags события


async def test_flight_itself_refuses_summary_over_budget(svc):                # защита на каждом пути
    s, fake = svc
    over = Period(start=datetime(2026, 9, 1), end=datetime(2026, 9, 14), preset=None, shift_assumed=False,
                  clamped=False, open=False)
    with pytest.raises(RuntimeError):
        await s._read_summary(("20FAKE_001_PV", "20FAKE_002_SP"), over, "k", 3600)
    assert fake.calls == [] and len(s.cache) == 0


# --- доводка по ревью задачи 5 ---

async def test_cache_hit_age_is_per_call_asof_is_read_moment(svc):             # I1
    s, fake = svc
    fake.live["20FAKE_004_PV"] = (datetime(2026, 10, 1, 12, 1, 30), 3.2, 0)      # впереди часов на 90 с
    miss = await s.tag_now("student-01", "http", ["20FAKE_001_PV", "20FAKE_004_PV", "25FAKE_007_CLS"])
    s.role.monotonic.advance(10)
    hit = await s.tag_now("student-02", "http", ["20FAKE_001_PV", "20FAKE_004_PV", "25FAKE_007_CLS"])
    assert (miss["cache"], hit["cache"], hit["asof"]) == ("miss", "hit", miss["asof"])
    assert [i["age_s"] for i in miss["items"]] == [42, 0, 1200]
    assert [i["age_s"] for i in hit["items"]] == [52, 0, 1210]
    assert hit["items"][1]["item_notes"] == ["метка тега впереди часов историана на 90 с"]   # от момента чтения
    assert "не менялось 20 мин" in hit["items"][2]["item_notes"][0] and len(fake.calls) == 1


def _duplicate_live(statements):
    return GateResult(rows=[[LIVE_ROWS[0], LIVE_ROWS[0]]], sent=GATE_SENT)


def _duplicate_summary(statements):
    row = ("20FAKE_001_PV", *(1.0 for _ in SUMMARY_COLUMNS))
    return GateResult(rows=[[row, ("20fake_001_pv", *row[1:])]], sent=GATE_SENT)    # повтор и без регистра


@pytest.mark.parametrize("tool, answer", [("tag_now", _duplicate_live), ("tag_period", _duplicate_summary)])
async def test_duplicate_tag_in_answer_is_error_after_send(svc, tool, answer):   # M1
    s, _ = svc
    stub_gate(s, answer)
    kwargs = {"period": "prev_shift"} if tool == "tag_period" else {}
    r = await getattr(s, tool)("student-01", "http", ["20FAKE_001_PV"], **kwargs)
    e = s.role.events.recent()[0]
    assert (r["status"], r["message"], e.outcome, e.sent) == ("error", ERROR_MESSAGE, "error", GATE_SENT)
    assert "duplicate_tag" in e.summary and len(s.cache) == 0


async def test_argument_refusals_count_as_calls(svc):                          # M2
    s, _ = svc
    s.per_caller = SlidingWindow(2, 300)
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "ok"
    assert (await s.refuse_args("student-01", "http", "tag_now", ["x"], "tags: нужен список"))["status"] == "refused"
    over = await s.refuse_args("student-01", "http", "tag_now", ["x"], "tags: нужен список")
    assert (over["status"], over["message"]) == ("rate", MESSAGES["rate"])
    assert (await s.tag_now("student-01", "http", ["20FAKE_001_PV"]))["status"] == "rate"
    assert [e.outcome for e in s.role.events.recent()] == ["rate", "rate", "refused", "ok"]


async def test_period_syntax_errors_send_no_sql_even_with_stale_clock(svc):    # M3
    s, fake = svc
    s.role.monotonic.advance(301)                       # часы устарели: любой путь к периоду спросил бы их
    cases = [dict(period="prev_shift", start="2026-10-01T10:00", end="2026-10-01T11:00"),
             dict(start="вчера", end="2026-10-01T11:00"),
             dict(start="2026-10-01T10:00"),
             dict(start="2026-10-01T11:00", end="2026-10-01T10:00"),
             dict(start="2026-10-01T10:00:00+05:00", end="2026-10-01T09:00"),      # с поясом и без — одна шкала
             dict(start="2026-10-01T10:00", end="2026-10-01T10:00:30")]
    for kwargs in cases:
        r = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], **kwargs)
        assert r["status"] == "refused" and r["message"].startswith(MESSAGES["args"]), kwargs
    assert fake.calls == []
    future = await s.tag_period("student-01", "http", ["20FAKE_001_PV"], start="2026-10-02T00:00",
                                end="2026-10-02T06:00")                          # нужно «сейчас» — часы спрошены
    assert "позже текущего времени историана" in future["message"] and fake.calls == [[clock_sql()]]


@pytest.mark.parametrize("tool", ["tag_now", "tag_period"])
async def test_failure_after_gate_keeps_sent(svc, monkeypatch, tool):            # M4
    s, fake = svc

    def broken(*args, **kwargs):
        raise RuntimeError("кэш сломан")

    monkeypatch.setattr(s.cache, "put", broken)
    kwargs = {"period": "prev_shift"} if tool == "tag_period" else {}
    r = await getattr(s, tool)("student-01", "http", ["20FAKE_001_PV"], **kwargs)
    e = s.role.events.recent()[0]
    assert (r["status"], e.outcome, e.sent, e.cache) == ("error", "error", ("20FAKE_001_PV",), "miss")
    assert "RuntimeError" in e.summary and sent_of(fake) == ["20FAKE_001_PV"]


async def test_refusal_events_are_written_off_loop(svc, tmp_path, monkeypatch):  # I2
    s, _ = svc
    broken, _ = make_role(db_dir=tmp_path / "cfg", config_error=CONFIG_ERRORS["bdrv_invalid"])
    threads: list[int] = []
    for events in (s.role.events, broken.events):
        real = events.write
        monkeypatch.setattr(events, "write", lambda e, real=real: threads.append(threading.get_ident()) or real(e))
    await s.refuse_args("student-01", "http", "tag_now", ["x"], "tags: нужен список")
    await broken.service.config_unavailable("student-01", "http", "tag_now", ["x"])
    assert len(threads) == 2 and threading.get_ident() not in threads           # SQLite — не в потоке цикла


# Д4б, задача 1: ошибки инструментов на странице (Д4б-R22, К7) и returned в событиях поиска


async def test_tools_stats_window(svc):
    s, fake = svc
    fake.fail = HistorianError("connect", "x")
    for n in range(1, 4):
        await s.tag_now(f"student-0{n}", "http", ["20FAKE_004_PV"])
        s.role.gate.clear_pause()
    fake.fail = None
    await s.tag_now("student-09", "mcp", ["20FAKE_001_PV"])
    assert s.tools_stats() == {"recent": 4, "errors": 3, "failing": []}


async def test_catalog_search_event_has_returned(svc):
    s, _ = svc
    await s.catalog_search("student-01", "http", "расход массы")
    assert s.role.events.recent()[0].returned == ("20FAKE_001_PV", "20FAKE_002_SP")


# сверх брифа: окно — последние 50 вызовов в памяти, служебные опросы не в счёт, SQLite не читается (К7)


async def test_tools_stats_last_fifty_from_memory(svc, monkeypatch):
    s, fake = svc
    for _ in range(TOOL_WINDOW + 5):
        await s.refuse_args("student-01", "mcp", "tag_now", ["x"], "tags: нужен список")
    await s.role.poll_freshness()                                               # событие system — не инструмент
    fake.fail = HistorianError("timeout", "долго")
    await s.tag_now("student-02", "http", ["20FAKE_001_PV"])
    r = await s.tag_now("student-03", "http", ["20FAKE_404_PV"])                # отказ без SQL — не ошибка
    assert r["status"] == "refused" and s.role.events.count("system") == 1

    def no_sqlite(*args, **kwargs):
        raise AssertionError("tools_stats не читает журнал событий")

    monkeypatch.setattr(s.role.events, "recent", no_sqlite)
    monkeypatch.setattr(s.role.events, "count", no_sqlite)
    assert s.tools_stats() == {"recent": TOOL_WINDOW, "errors": 1, "failing": []}


# Д9а, задача А1: серия ошибок по инструменту, канал web, учение DRILL_DATA (Д9-R83)


async def test_failing_streak_per_tool(svc):                                     # Review Focus 3
    s, fake = svc
    fake.fail = HistorianError("query", "x")
    for _ in range(FAIL_STREAK - 1):
        await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert s.tools_stats()["failing"] == []
    await s.tag_now("student-01", "mcp", ["20FAKE_001_PV"])
    assert s.tools_stats()["failing"] == ["tag_now"]
    fake.fail = None
    await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    assert s.tools_stats()["failing"] == ["tag_now"]                              # серия — своя у каждого инструмента
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert s.tools_stats()["failing"] == []


async def test_failing_streak_counts_timeout_and_unavailable(tmp_path):         # Д9-R83: молчание, не только падение
    for code, outcome in (("timeout", "timeout"), ("connect", "unavailable")):
        d = tmp_path / code
        d.mkdir()
        s, fake = make_service(d)
        fake.fail = HistorianError(code, "x")
        for _ in range(FAIL_STREAK):
            await s.tag_now("student-01", "web", ["20FAKE_001_PV"])
            s.role.gate.clear_pause()                     # пауза людей после сроков подряд — не мешает серии
        assert [e.outcome for e in s.role.events.recent()] == [outcome] * FAIL_STREAK
        assert s.tools_stats()["failing"] == ["tag_now"], code


async def test_failing_streak_mixed_outcomes(svc):                               # ревью А1, I-1: смесь — одна серия
    s, fake = svc
    for code in ("query", "timeout", "connect"):
        fake.fail = HistorianError(code, "x")
        await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
        s.role.gate.clear_pause()
    assert [e.outcome for e in s.role.events.recent()] == ["unavailable", "timeout", "error"]
    assert s.tools_stats()["failing"] == ["tag_now"]


async def test_refusals_do_not_count_as_failing(svc):
    s, _ = svc
    for _ in range(FAIL_STREAK + 1):
        await s.tag_now("student-01", "http", ["20FAKE_404_PV"])                 # refused — без SQL
        await s.refuse_args("student-01", "mcp", "tag_period", ["x"], "tags: нужен список")
    assert s.tools_stats()["failing"] == []


async def test_web_channel_counted(svc):
    s, fake = svc
    fake.fail = HistorianError("query", "x")
    for _ in range(FAIL_STREAK):
        await s.tag_now("student-03", "web", ["20FAKE_001_PV"])
    assert s.tools_stats() == {"recent": FAIL_STREAK, "errors": FAIL_STREAK, "failing": ["tag_now"]}
    assert s.role.events.recent()[0].channel == "web"


async def test_failing_expires_without_calls(svc):                                # m3 критики р1
    s, fake = svc
    fake.fail = HistorianError("query", "x")
    for _ in range(FAIL_STREAK):
        await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    s.role.monotonic.advance(FAIL_STALE_S - 1)                                    # FakeMono роли make_service
    assert s.tools_stats()["failing"] == ["tag_now"]
    s.role.monotonic.advance(2)
    assert s.tools_stats()["failing"] == []


async def test_failing_streak_reset_by_refusal_midway(svc):                      # ревью А1, M-1: «любой другой исход»
    s, fake = svc
    fake.fail = HistorianError("query", "x")
    for _ in range(FAIL_STREAK - 1):
        await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert (await s.tag_now("student-01", "http", ["20FAKE_404_PV"]))["status"] == "refused"      # без SQL — обнуляет
    await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert [e.outcome for e in s.role.events.recent()][:2] == ["error", "refused"]
    assert s.tools_stats()["failing"] == []                                       # R83: серия — снова с одной ошибки


async def test_failing_sorted_by_name(svc):                                      # ревью А1, M-2: по алфавиту, не по порядку
    s, fake = svc
    fake.fail = HistorianError("query", "x")
    for tool in ("tag_period", "tag_now"):                                        # порядок вставки дал бы обратный
        for _ in range(FAIL_STREAK):
            if tool == "tag_period":
                await s.tag_period("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
            else:
                await s.tag_now("student-01", "http", ["20FAKE_001_PV"])
    assert s.tools_stats()["failing"] == ["tag_now", "tag_period"]


async def test_drill_before_args_and_catalog(tmp_path):                          # ревью А1, M-3: сразу после частоты
    s, fake = make_service(tmp_path, settings=replace(SETTINGS, DRILL_DATA="tool_error"))
    for tags in (["20FAKE_404_PV"], [" "], []):                                  # отказ каталога и аргументов — не раньше
        r = await s.tag_now("ops", "http", tags)
        assert (r["status"], r["message"]) == ("error", ERROR_MESSAGE) and "items" not in r, tags
    assert fake.calls == [] and s.role.events.recent()[0].summary == DRILL_SUMMARY


async def test_drill_tool_error_no_sql(tmp_path):                                 # Review Focus 3
    s, fake = make_service(tmp_path, settings=replace(SETTINGS, DRILL_DATA="tool_error"))
    for _ in range(FAIL_STREAK):
        r = await s.tag_now("ops", "http", ["20FAKE_001_PV"])
        assert (r["status"], r["message"]) == ("error", ERROR_MESSAGE) and "items" not in r
    assert fake.calls == [] and s.tools_stats()["failing"] == ["tag_now"]       # SQL не ушёл ни разу
    assert s.role.events.recent()[0].summary == DRILL_SUMMARY
    r = await s.catalog_search("ops", "http", "расход")                           # каталог в памяти — тоже без данных
    assert r["status"] == "error" and "matches" not in r
    # решение контроллера А2: калькуляторы под учением — тоже ошибка без SQL и без данных, серия набирается
    for _ in range(FAIL_STREAK):
        for r in (await s.tag_series("ops", "http", ["20FAKE_001_PV"], period="last_8h"),
                  await s.calc_runtime("ops", "mcp", ["25FAKE_007_CLS"], period="yesterday")):
            assert (r["status"], r["message"]) == ("error", ERROR_MESSAGE), r
            assert not {"items", "period", "points", "source", "cost_tag_days"} & set(r)
    assert fake.calls == [] and s.role.gate.stats()["sent_names_total"] == 0
    assert s.tools_stats()["failing"] == ["calc_runtime", "tag_now", "tag_series"]
    events = s.role.events.recent()[:2]
    assert [(e.tool, e.outcome, e.summary, e.sent, e.rows) for e in events] == [
        ("calc_runtime", "error", DRILL_SUMMARY, (), 0), ("tag_series", "error", DRILL_SUMMARY, (), 0)]


# сверх брифа: учение и у tag_period, отказ по аргументам остаётся отказом, строка журнала «error=drill» (Д9-R90)


async def test_drill_tag_period_and_log_line(tmp_path, capfd, restore_logging):
    setup_logging()
    s, fake = make_service(tmp_path, settings=replace(SETTINGS, DRILL_DATA="tool_error"))
    r = await s.tag_period("ops", "http", ["20FAKE_001_PV"], period="last_8h")
    assert (r["status"], r["message"]) == ("error", ERROR_MESSAGE) and "items" not in r and "period" not in r
    a = await s.refuse_args("ops", "mcp", "tag_now", ["x"], "tags: нужен список")
    assert a["status"] == "refused" and fake.calls == []
    e = s.role.events.recent()[1]
    assert (e.tool, e.outcome, e.summary, e.sent, e.rows) == ("tag_period", "error", DRILL_SUMMARY, (), 0)
    err = capfd.readouterr().err
    assert "tool=tag_period caller=ops channel=http" in err and "rows=0" in err and "outcome=error error=drill" in err


# Д9а, задача А2: калькуляторы tag_series и calc_runtime (Д9-R84, Д9-R85); часы историана 2026-10-01 12:00
# (UTC+05:00), SERIES и STATES — fakes.py


async def test_tag_series_answer(svc):                                            # Д9-R84
    s, fake = svc
    r = await s.tag_series("student-01", "http", ["20FAKE_001_PV"], period="last_8h", points=3)
    it = r["items"][0]
    assert (r["status"], r["source"], r["points"], r["period"]["preset"]) == ("ok", SOURCE_SERIES, 3, "last_8h")
    assert GRID_NOTE in r["notes"] and OPEN_NOTE in r["notes"]
    assert it["points"] == [["2026-10-01T04:00:00+05:00", 10.0], ["2026-10-01T08:00:00+05:00", 15.0],
                            ["2026-10-01T12:00:00+05:00", 12.5]]
    assert it["profile"] == {"first": 10.0, "first_time": "2026-10-01T04:00:00+05:00", "last": 12.5,
                             "last_time": "2026-10-01T12:00:00+05:00", "change": 2.5,
                             "min": 10.0, "min_time": "2026-10-01T04:00:00+05:00",
                             "max": 15.0, "max_time": "2026-10-01T08:00:00+05:00"}
    assert "wwCycleCount = 3" in fake.calls[-1][-1] and sent_of(fake)[-1] == "20FAKE_001_PV"


async def test_tag_series_null_and_quality(svc):                                  # Review Focus 2
    s, fake = svc
    it = (await s.tag_series("student-01", "http", ["20FAKE_004_PV"], period="last_8h", points=3))["items"][0]
    assert (it["profile"]["first"], it["profile"]["first_time"]) == (3.2, "2026-10-01T08:00:00+05:00")
    assert it["points"][0] == ["2026-10-01T04:00:00+05:00", None]
    assert it["quality_note"] == "точек с недостоверным качеством: 1 из 3"
    fake.series["20FAKE_004_PV"] = [(datetime(2026, 10, 1, 4), None, 0), (datetime(2026, 10, 1, 12), None, 0)]
    r = await s.tag_series("student-01", "http", ["20FAKE_004_PV"], period="last_8h", points=2)
    assert r["items"][0]["status"] == "no_data"


async def test_tag_series_limits_and_kinds(svc):                                  # Review Focus 2
    s, fake = svc
    five = ["20FAKE_001_PV", "20FAKE_002_SP", "20FAKE_003_PV", "20FAKE_004_PV", "QFAKE_010"]
    r = await s.tag_series("student-01", "http", five, period="last_8h")
    assert (r["status"], r["message"]) == ("refused", MESSAGES["args"] + "tags: не больше 4 тегов за вызов")
    r = await s.tag_series("student-01", "http", five[:4], period="last_8h", points=96)   # 4 × 96 = 384 > 288
    assert r["status"] == "budget" and "288 точек" in r["message"]
    r = await s.tag_series("student-01", "http", ["20FAKE_001_PV"], period="last_8h", points=97)
    assert (r["status"], r["message"]) == ("refused", MESSAGES["args"] + "points: от 2 до 96")
    fake.calls.clear()
    r = await s.tag_series("student-01", "http", ["25FAKE_007_CLS", "20FAKE_001_PV"], period="last_8h")
    assert r["status"] == "partial" and r["items"][0] == {"tag": "25FAKE_007_CLS", "status": "discrete",
                                                          "text": ITEM_TEXTS["discrete_series"]}
    assert "25FAKE_007_CLS" not in sent_of(fake)


async def test_calc_runtime_answer(svc):                                          # Д9-R85
    s, fake = svc
    r = await s.calc_runtime("student-01", "mcp", ["25FAKE_007_CLS"], period="yesterday")
    it = r["items"][0]
    assert (r["status"], r["source"]) == ("ok", SOURCE_STATE)
    assert (r["period"]["start"], r["period"]["end"]) == ("2026-09-30T00:00:00+05:00", "2026-10-01T00:00:00+05:00")
    assert it["states"] == [{"value": 1, "label": "закрыт (по суффиксу)", "hours": 18.0, "percent": 75.0, "count": 3},
                            {"value": 0, "label": "не закрыт (по суффиксу)", "hours": 6.0, "percent": 25.0,
                             "count": 3}]
    assert (it["covered_percent"], it["period_hours"]) == (100.0, 24.0) and "coverage_note" not in it


async def test_calc_runtime_coverage_kinds_noise(svc):                            # Review Focus 2
    s, fake = svc
    fake.states["25FAKE_007_CLS"] = [(1.0, 43_200_000, 50.0, 1)]
    r = await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS", "20FAKE_001_PV"], period="yesterday")
    a, b = r["items"]
    assert r["status"] == "partial" and a["covered_percent"] == 50.0
    assert a["coverage_note"] == "данные покрывают 50 % периода — часы состояний в сумме меньше периода"
    assert b == {"tag": "20FAKE_001_PV", "status": "analog", "text": ITEM_TEXTS["analog"]}
    fake.states["25FAKE_007_CLS"] = [(None, 0, 0.0, 0)]                          # шум — как нет строк
    r = await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="last_7d")
    assert r["items"][0]["status"] == "no_data"
    fake.states["25FAKE_007_CLS"] = [(None, 3_600_000, 12.5, 1), (0.0, 25_200_000, 87.5, 2)]   # m2: «нет данных»
    st = (await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="last_8h"))["items"][0]
    assert [x["value"] for x in st["states"]] == [0, None]
    assert (st["states"][-1]["label"], st["states"][-1]["hours"]) == ("нет данных", 1.0)
    assert st["covered_percent"] == 87.5 and "87,5 %" in st["coverage_note"]


async def test_tag_period_discrete_points_to_runtime(svc):
    s, _ = svc
    r = await s.tag_period("student-01", "http", ["25FAKE_007_CLS"], period="yesterday")
    assert r["items"][0]["text"] == "дискретный тег: сводка не считается — наработку даёт calc_runtime"


# сверх брифа: события и кэш калькуляторов, порядок точек, отказы без SQL, защита полёта, ответы не того вида,
# серия ошибок по новым инструментам (решение контроллера А2)


async def test_tag_series_sorts_points_events_and_cache(svc):
    s, fake = svc
    fake.series["20FAKE_001_PV"] = list(reversed(fake.series["20FAKE_001_PV"]))   # двойник отдаёт строки как есть
    r = await s.tag_series("student-01", "mcp", ["20fake_001_pv", "20FAKE_404_PV", "16FAKE_009_PV"],
                           period="last_8h", points=3)
    assert [p[0][11:13] for p in r["items"][0]["points"]] == ["04", "08", "12"]      # порядок задаёт служба
    assert (r["status"], [i["status"] for i in r["items"]]) == ("partial", ["ok", "unknown", "unlisted"])
    assert (r["cache"], r["cost_tag_days"], r["period"]["open"]) == ("miss", 0.333, True)
    e = s.role.events.recent()[0]
    assert (e.tool, e.outcome, e.sent, e.tags, e.rows) == ("tag_series", "partial", ("20FAKE_001_PV",),
                                                            ("20FAKE_001_PV",), 3)
    again = await s.tag_series("student-02", "http", ["20FAKE_001_PV"], period="last_8h", points=3)
    other = await s.tag_series("student-02", "http", ["20FAKE_001_PV"], period="last_8h", points=4)
    assert (again["cache"], other["cache"]) == ("hit", "miss") and len(fake.calls) == 2   # точки — в ключе кэша
    assert again["items"][0]["points"] == r["items"][0]["points"]
    shift = await s.tag_series("student-01", "http", ["20FAKE_001_PV"], period="prev_shift")
    assert SHIFT_NOTE in shift["notes"] and GRID_NOTE in shift["notes"] and OPEN_NOTE not in shift["notes"]
    assert shift["points"] == 24 and "wwCycleCount = 24" in fake.calls[-1][-1]          # по умолчанию — 24


async def test_calc_runtime_states_order_labels_and_event(svc):
    s, fake = svc
    fake.states["25FAKE_007_CLS"] = [(0.0, 7_200_000, 8.0, 2), (None, 1_800_000, 2.0, 1), (2.0, 18_000_000, 20.0, 1),
                                     (1.0, 59_400_000, 70.0, 3), (None, None, None, None)]
    r = await s.calc_runtime("student-01", "http", ["25fake_007_cls"], period="yesterday")
    it = r["items"][0]
    assert [(x["value"], x["label"], x["hours"]) for x in it["states"]] == [
        (2, None, 5.0), (1, "закрыт (по суффиксу)", 16.5), (0, "не закрыт (по суффиксу)", 2.0),
        (None, "нет данных", 0.5)]
    assert all(type(x["value"]) is int and type(x["count"]) is int for x in it["states"][:3])
    assert (it["tag"], it["description"], it["covered_percent"]) == ("25FAKE_007_CLS", "Клапан подачи закрыт", 98.0)
    assert it["coverage_note"] == "данные покрывают 98 % периода — часы состояний в сумме меньше периода"
    assert (r["cost_tag_days"], r["cache"], r["period"]["open"], r["notes"]) == (1.0, "miss", False, [])
    e = s.role.events.recent()[0]
    assert (e.tool, e.outcome, e.sent, e.tags, e.rows) == ("calc_runtime", "ok", ("25FAKE_007_CLS",),
                                                            ("25FAKE_007_CLS",), 5)
    assert "StateSummaryHistory" in fake.calls[-1][-1]
    assert (await s.calc_runtime("student-02", "http", ["25FAKE_007_CLS"], period="yesterday"))["cache"] == "hit"


async def test_calculators_refuse_without_sql(svc):
    s, fake = svc
    outs = [await s.tag_series("student-01", "http", ["25FAKE_007_CLS", "16FAKE_009_PV"], period="last_8h"),
            await s.calc_runtime("student-01", "http", ["20FAKE_001_PV", "20FAKE_404_PV"], period="yesterday"),
            await s.calc_runtime("student-01", "http", [f"25FAKE_{i:03d}_CLS" for i in range(17)], period="yesterday"),
            await s.tag_series("student-01", "http", ["20FAKE_001_PV"], period="last_8h", points="24"),
            await s.tag_series("student-01", "http", ["20FAKE_001_PV"], period="last_8h", points=True),
            await s.tag_series("student-01", "http", ["20FAKE_001_PV"], start="2026-08-01T00:00",
                               end="2026-09-05T00:00"),
            await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS", "20FAKE_004_PV"], start="2026-08-01T00:00",
                                 end="2026-09-05T00:00"),
            await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="yesterday",
                                 start="2026-10-01T00:00", end="2026-10-01T06:00")]
    assert [o["status"] for o in outs] == ["refused"] * 5 + ["budget", "budget", "refused"]
    assert [(i["tag"], i["status"], i["text"]) for i in outs[0]["items"]] == [
        ("25FAKE_007_CLS", "discrete", ITEM_TEXTS["discrete_series"]),
        ("16FAKE_009_PV", "unlisted", ITEM_TEXTS["unlisted"])]
    assert outs[0]["message"] == outs[1]["message"] == MESSAGES["none"]
    assert [i["status"] for i in outs[1]["items"]] == ["analog", "unknown"]
    assert outs[2]["message"] == MESSAGES["args"] + "tags: не больше 16 тегов за вызов"
    assert outs[3]["message"] == outs[4]["message"] == MESSAGES["args"] + "points: от 2 до 96"
    assert outs[5]["message"] == outs[6]["message"] == "период длиннее 31 суток" and outs[5]["cost_tag_days"] == 35.0
    assert "items" not in outs[6] and outs[7]["message"].startswith(MESSAGES["args"] + "задайте период одним способом")
    assert fake.calls == [] and {e.sent for e in s.role.events.recent()} == {()}


async def test_calculator_flights_refuse_over_budget(svc):                      # защита на каждом пути
    s, fake = svc
    over = Period(start=datetime(2026, 9, 1), end=datetime(2026, 9, 14), preset=None, shift_assumed=False,
                  clamped=False, open=False)
    short = replace(over, start=datetime(2026, 10, 1, 4), end=datetime(2026, 10, 1, 12))
    four = ("20FAKE_001_PV", "20FAKE_002_SP", "20FAKE_003_PV", "20FAKE_004_PV")
    with pytest.raises(RuntimeError):
        await s._read_series(four[:2], over, 24, "k", 3600)
    with pytest.raises(RuntimeError):
        await s._read_series(four, short, 96, "k", 3600)                       # 384 точки > 288
    with pytest.raises(RuntimeError):
        await s._read_states(("25FAKE_007_CLS", "20FAKE_001_PV"), over, "k", 3600)
    assert fake.calls == [] and len(s.cache) == 0


async def test_series_budget_and_points_before_sql(svc, monkeypatch):           # ворота не мерят окно и точки
    s, fake = svc
    order: list[str] = []
    real = {name: getattr(service_module, name) for name in ("check_budget", "check_points", "series_sql")}
    for name, tag in (("check_budget", "budget"), ("check_points", "points"), ("series_sql", "sql")):
        monkeypatch.setattr(service_module, name, lambda *a, _t=tag, _f=real[name]: order.append(_t) or _f(*a))
    four = ["20FAKE_001_PV", "20FAKE_002_SP", "20FAKE_003_PV", "20FAKE_004_PV"]
    r = await s.tag_series("student-01", "http", four, period="last_8h", points=96)
    assert r["status"] == "budget" and order == ["budget", "points"] and fake.calls == []
    assert (r["period"]["start"], r["cost_tag_days"]) == ("2026-10-01T04:00:00+05:00", 1.333)
    assert r["message"] == "за один вызов — не больше 288 точек ряда (теги × точки): сократите число тегов или точек"
    assert s.role.events.recent()[0].outcome == "budget"
    order.clear()
    assert (await s.tag_series("student-01", "http", four, period="last_8h", points=72))["status"] == "partial"
    assert order.index("budget") < order.index("points") < order.index("sql")      # 4 × 72 = 288 — в пределе


def _text_time(statements):
    return GateResult(rows=[[("20FAKE_001_PV", "2026-10-01 04:00", 1.0, 0)]], sent=GATE_SENT)   # время — не метка


def _short_state(statements):
    return GateResult(rows=[[("25FAKE_007_CLS", 1.0, 3_600_000)]], sent=GATE_SENT)


def _duplicate_state(statements):
    row = ("25FAKE_007_CLS", 1.0, 3_600_000, 50.0, 1)
    return GateResult(rows=[[row, ("25fake_007_cls", *row[1:])]], sent=GATE_SENT)


def _text_state(statements):
    return GateResult(rows=[[("25FAKE_007_CLS", "on", 3_600_000, 50.0, 1)]], sent=GATE_SENT)    # текст не из цифр


def _digit_text_state(statements):
    """Живой историан ПЦБК (окно 1, 12.10): vValue — sql_variant, состояние приходит строкой '0'/'1'."""
    return GateResult(rows=[[("25FAKE_007_CLS", "1", 3_600_000, 50.0, 1), ("25FAKE_007_CLS", "0", 0, 0.0, 0)]],
                      sent=GATE_SENT)


async def test_calc_runtime_accepts_state_value_as_digit_text(svc):
    s, _ = svc
    stub_gate(s, _digit_text_state)
    r = await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="last_8h")
    assert r["status"] in ("ok", "partial") and s.role.events.recent()[0].outcome != "error", r
    assert "row_shape" not in s.role.events.recent()[0].summary


@pytest.mark.parametrize("text", ["١", "1e3", "1 ", "0x1", ""])
async def test_calc_runtime_state_text_not_ascii_digits_is_row_shape(svc, text):
    s, _ = svc
    stub_gate(s, lambda st: GateResult(rows=[[("25FAKE_007_CLS", text, 3_600_000, 50.0, 1)]], sent=GATE_SENT))
    r = await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="last_8h")
    assert r["status"] == "error" and "row_shape" in s.role.events.recent()[0].summary


@pytest.mark.parametrize("tool, answer, code", [("tag_series", _text_time, "row_shape"),
                                                ("calc_runtime", _short_state, "row_shape"),
                                                ("calc_runtime", _duplicate_state, "duplicate_state"),
                                                ("calc_runtime", _text_state, "row_shape")])
async def test_calculator_bad_answer_is_error_after_send(svc, tool, answer, code):
    s, _ = svc
    calls = stub_gate(s, answer)
    tag = "20FAKE_001_PV" if tool == "tag_series" else "25FAKE_007_CLS"
    r = await getattr(s, tool)("student-01", "http", [tag], period="last_8h")
    e = s.role.events.recent()[0]
    assert len(calls) == 1 and (r["status"], r["message"], e.outcome, e.sent) == ("error", ERROR_MESSAGE, "error",
                                                                                  GATE_SENT)
    assert code in e.summary and len(s.cache) == 0 and "items" not in r


async def test_calculator_answers_are_strict_json(svc):
    s, fake = svc
    fake.series["20FAKE_001_PV"][1] = (datetime(2026, 10, 1, 8), float("nan"), 0)
    fake.states["25FAKE_007_CLS"] = [(1.0, 64_800_000, 75.0, 3), (0.0, 21_600_000, float("inf"), 3)]
    series = await s.tag_series("student-01", "http", ["20FAKE_001_PV"], period="last_8h", points=3)
    runtime = await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="yesterday")
    for answer in (series, runtime):
        json.dumps(answer, allow_nan=False)
    it = series["items"][0]
    assert it["points"][1] == ["2026-10-01T08:00:00+05:00", None] and (it["profile"]["max"], it["profile"]["min"]) == (
        12.5, 10.0)
    assert runtime["items"][0]["states"][1]["percent"] is None
    it = runtime["items"][0]
    assert it["covered_percent"] is None and "coverage_note" not in it          # ревью А2, m-2: доли нет


async def test_failing_streak_counts_calculators(svc):                           # решение контроллера А2
    s, fake = svc
    fake.fail = HistorianError("query", "x")
    for _ in range(FAIL_STREAK):
        await s.tag_series("student-01", "web", ["20FAKE_001_PV"], period="last_8h")
        await s.calc_runtime("student-02", "http", ["25FAKE_007_CLS"], period="yesterday")
    assert s.tools_stats() == {"recent": 2 * FAIL_STREAK, "errors": 2 * FAIL_STREAK,
                               "failing": ["calc_runtime", "tag_series"]}
    fake.fail = None
    assert (await s.tag_series("student-01", "mcp", ["20FAKE_001_PV"], period="last_8h"))["status"] == "ok"
    assert s.tools_stats()["failing"] == ["calc_runtime"]
    r = await s.calc_runtime("student-02", "http", ["20FAKE_001_PV"], period="yesterday")   # отказ — не ошибка
    assert r["status"] == "refused" and s.tools_stats()["failing"] == []


# итоговая доводка Д9а, ревью А2: покрытие без ложного утверждения (m-1, m-2) и пробелы тестов полей ответа (m-4)


async def test_calc_runtime_coverage_unknown_without_percent(svc):                # m-2: «0 %» при 24 ч часов — ложь
    s, fake = svc
    fake.states["25FAKE_007_CLS"] = [(1.0, 64_800_000, None, 3), (0.0, 21_600_000, None, 3)]
    it = (await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="yesterday"))["items"][0]
    assert [x["hours"] for x in it["states"]] == [18.0, 6.0]
    assert it["covered_percent"] is None and "coverage_note" not in it
    fake.states["25FAKE_007_CLS"] = [(1.0, 86_400_000, 100.0, 1), (0.0, 0, None, 0)]   # без доли, но и без часов
    it = (await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="last_24h"))["items"][0]
    assert it["covered_percent"] == 100.0 and "coverage_note" not in it


async def test_calc_runtime_zero_hours_ok_with_coverage_note(svc):               # m-1: «ok», но о покрытии — сказано
    s, fake = svc
    fake.states["25FAKE_007_CLS"] = [(1.0, 0, 0.0, 0), (0.0, 0, 0.0, 0)]
    it = (await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="yesterday"))["items"][0]
    assert (it["status"], [x["hours"] for x in it["states"]], it["covered_percent"]) == ("ok", [0.0, 0.0], 0.0)
    assert it["coverage_note"] == "данные покрывают 0 % периода — часы состояний в сумме меньше периода"


async def test_calc_runtime_period_hours_and_notes(svc):                         # m-4: длина периода, заметки периода
    s, _ = svc
    r = await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="last_8h")
    assert (r["items"][0]["period_hours"], r["notes"]) == (8.0, [OPEN_NOTE])
    r = await s.calc_runtime("student-01", "http", ["25FAKE_007_CLS"], period="current_shift")
    assert r["notes"] == [SHIFT_NOTE, OPEN_NOTE] and r["items"][0]["period_hours"] == 6.0


async def test_tag_series_clamped_own_period_notes(svc):                         # m-4: конец ограничен «сейчас»
    s, _ = svc
    r = await s.tag_series("student-01", "http", ["20FAKE_001_PV"], start="2026-10-01T04:00", end="2026-10-01T13:00",
                           points=3)
    assert r["notes"] == [GRID_NOTE, OPEN_NOTE, CLAMP_NOTE]
    assert (r["period"]["end"], r["period"]["clamped"], r["period"]["preset"]) == ("2026-10-01T12:00:00+05:00", True,
                                                                                   None)


async def test_tag_series_open_from_cache(svc):                                  # m-4: числа читались, пока период шёл
    s, _ = svc
    own = {"start": "2026-10-01T04:00", "end": "2026-10-01T11:55:30", "points": 3}
    s.role.monotonic.advance(29)                          # 12:00:29: конец 4 мин 59 с назад — период ещё идёт
    first = await s.tag_series("student-01", "http", ["20FAKE_001_PV"], **own)
    s.role.monotonic.advance(2)                           # 12:00:31: по часам период закрыт, запись кэша жива
    again = await s.tag_series("student-02", "http", ["20FAKE_001_PV"], **own)
    fresh = await s.tag_series("student-02", "http", ["20FAKE_001_PV"], **{**own, "points": 4})
    assert (first["cache"], again["cache"], fresh["cache"]) == ("miss", "hit", "miss")
    assert first["period"]["open"] is again["period"]["open"] is True and OPEN_NOTE in again["notes"]
    assert fresh["period"]["open"] is False and OPEN_NOTE not in fresh["notes"]    # контроль: без кэша — закрыт


async def test_tag_series_first_of_equal_extremes(svc):                          # m-4: из равных — первый
    s, fake = svc
    fake.series["20FAKE_001_PV"] = [(datetime(2026, 10, 1, h), v, 0) for h, v in ((4, 10.0), (6, 15.0), (8, 10.0),
                                                                                    (12, 15.0))]
    p = (await s.tag_series("student-01", "http", ["20FAKE_001_PV"], period="last_8h", points=4))["items"][0]["profile"]
    assert (p["min_time"], p["max_time"]) == ("2026-10-01T04:00:00+05:00", "2026-10-01T06:00:00+05:00")
    assert (p["min"], p["max"], p["change"]) == (10.0, 15.0, 5.0)


# Д13: calc_deviation — контрольная точка считается службой (не агентом «в уме»); чтение — тем же путём, что
# tag_series (ворота, белый список, бюджет, точки, кэш); двойник историана отдаёт fake.series как есть, вне
# зависимости от period/points — время точек в тестах подбирается напрямую


async def test_calc_deviation_one_interval(svc):
    s, fake = svc
    times = [datetime(2026, 10, 1, h) for h in (4, 6, 8, 10)]
    fake.series["20FAKE_001_PV"] = [(t, v, 0) for t, v in zip(times, [10.0, 20.0, 21.0, 11.0])]
    fake.series["20FAKE_002_SP"] = [(t, v, 0) for t, v in zip(times, [10.0, 10.0, 10.0, 10.5])]
    r = await s.calc_deviation("student-01", "http", "20FAKE_001_PV", "20FAKE_002_SP", 2.0, period="last_8h",
                               points=10)
    assert (r["tool"], r["status"], r["measure_tag"], r["setpoint_tag"], r["threshold"]) == (
        "calc_deviation", "ok", "20FAKE_001_PV", "20FAKE_002_SP", 2.0)
    assert (r["points"], r["over_count"]) == (4, 2)
    assert r["max_abs_dev"] == pytest.approx(11.0) and r["mean_abs_dev"] == pytest.approx(5.375)
    assert len(r["intervals"]) == 1
    iv = r["intervals"][0]
    assert (iv["start"], iv["end"], iv["points"]) == ("2026-10-01T06:00:00+05:00", "2026-10-01T08:00:00+05:00", 2)
    assert (iv["at"], iv["value"], iv["setpoint"], iv["max_abs_dev"]) == (
        "2026-10-01T08:00:00+05:00", 21.0, 10.0, pytest.approx(11.0))
    assert GRID_NOTE in r["notes"]


async def test_calc_deviation_several_intervals(svc):
    s, fake = svc
    times = [datetime(2026, 10, 1, h) for h in (4, 6, 8, 10, 12)]
    fake.series["20FAKE_001_PV"] = [(t, v, 0) for t, v in zip(times, [20.0, 10.0, 20.0, 10.0, 20.0])]
    fake.series["20FAKE_002_SP"] = [(t, 10.0, 0) for t in times]
    r = await s.calc_deviation("student-01", "http", "20FAKE_001_PV", "20FAKE_002_SP", 5.0, period="last_8h",
                               points=10)
    assert (r["points"], r["over_count"], len(r["intervals"])) == (5, 3, 3)
    assert [(iv["start"], iv["end"], iv["points"]) for iv in r["intervals"]] == [
        ("2026-10-01T04:00:00+05:00", "2026-10-01T04:00:00+05:00", 1),
        ("2026-10-01T08:00:00+05:00", "2026-10-01T08:00:00+05:00", 1),
        ("2026-10-01T12:00:00+05:00", "2026-10-01T12:00:00+05:00", 1)]


async def test_calc_deviation_no_intervals(svc):
    s, fake = svc
    fake.series["20FAKE_001_PV"] = [(datetime(2026, 10, 1, 4), 10.0, 0), (datetime(2026, 10, 1, 8), 11.0, 0)]
    fake.series["20FAKE_002_SP"] = [(datetime(2026, 10, 1, 4), 10.0, 0), (datetime(2026, 10, 1, 8), 10.5, 0)]
    r = await s.calc_deviation("student-01", "http", "20FAKE_001_PV", "20FAKE_002_SP", 5.0, period="last_8h",
                               points=10)
    assert (r["status"], r["points"], r["over_count"], r["intervals"]) == ("ok", 2, 0, [])
    assert r["max_abs_dev"] == pytest.approx(0.5)


async def test_calc_deviation_missing_values_are_skipped(svc):                   # пропуски значений — не пара
    s, fake = svc
    times = [datetime(2026, 10, 1, h) for h in (4, 8, 12)]
    fake.series["20FAKE_001_PV"] = [(times[0], 10.0, 0), (times[1], None, 0), (times[2], 12.0, 0)]
    fake.series["20FAKE_002_SP"] = [(times[0], None, 0), (times[1], 9.0, 0), (times[2], 10.0, 0)]
    r = await s.calc_deviation("student-01", "http", "20FAKE_001_PV", "20FAKE_002_SP", 1.0, period="last_8h",
                               points=10)
    assert (r["points"], r["over_count"]) == (1, 1)                              # только 12:00 — пара с обоими
    assert r["intervals"][0]["at"] == "2026-10-01T12:00:00+05:00"


@pytest.mark.parametrize("bad", [0, -1, "5", True, False, float("nan"), float("inf"), None, [5]])
async def test_calc_deviation_bad_threshold_is_args_refusal(svc, bad):
    s, _ = svc
    r = await s.calc_deviation("student-01", "http", "20FAKE_001_PV", "20FAKE_002_SP", bad, period="last_8h")
    assert (r["status"], r["message"]) == ("refused", MESSAGES["args"] + "threshold: число больше 0")


@pytest.mark.parametrize("kwargs,reason", [
    ({"measure_tag": 5, "setpoint_tag": "20FAKE_002_SP", "threshold": 1.0}, "measure_tag: нужен тег строкой"),
    ({"measure_tag": "20FAKE_001_PV", "setpoint_tag": "x" * 129, "threshold": 1.0},
     "setpoint_tag: строка тега длиннее 128 знаков"),
    ({"measure_tag": "20FAKE_001_PV", "setpoint_tag": "20FAKE_002_SP", "threshold": 1.0, "points": 97},
     "points: от 2 до 96"),
])
async def test_calc_deviation_argument_errors(svc, kwargs, reason):
    s, _ = svc
    r = await s.calc_deviation("student-01", "http", period="last_8h", **kwargs)
    assert (r["status"], r["message"]) == ("refused", MESSAGES["args"] + reason)


@pytest.mark.parametrize("measure,setpoint", [
    ("16FAKE_009_PV", "20FAKE_002_SP"),           # measure вне белого списка (тег есть, но не в нём)
    ("20FAKE_001_PV", "16FAKE_009_PV"),           # setpoint вне белого списка
    ("20FAKE_404_PV", "20FAKE_002_SP"),           # measure неизвестен историану
    ("25FAKE_007_CLS", "20FAKE_002_SP"),          # measure — тег состояния, не аналоговый
])
async def test_calc_deviation_whitelist_violation_is_refused_like_series(svc, measure, setpoint):
    s, fake = svc
    r = await s.calc_deviation("student-01", "http", measure, setpoint, 1.0, period="last_8h")
    assert (r["status"], r["message"]) == ("refused", MESSAGES["none"])
    assert fake.calls == []                                   # до ворот SQL не шлёт — как «none» у tag_series


async def test_calc_deviation_event_has_both_tags(svc):
    s, fake = svc
    times = [datetime(2026, 10, 1, h) for h in (4, 8, 12)]
    fake.series["20FAKE_002_SP"] = [(t, 10.0, 0) for t in times]
    r = await s.calc_deviation("ops", "mcp", "20FAKE_001_PV", "20FAKE_002_SP", 1.0, period="last_8h", points=3)
    assert r["status"] == "ok"
    e = s.role.events.recent()[0]
    assert (e.tool, e.caller, e.channel, e.outcome) == ("calc_deviation", "ops", "mcp", "ok")
    assert tuple(e.tags) == ("20FAKE_001_PV", "20FAKE_002_SP")
    assert set(e.sent) == {"20FAKE_001_PV", "20FAKE_002_SP"}


# Д19: equipment — служба сама группирует теги каталога в агрегаты и считает их (и «сколько в работе»)


async def test_equipment_without_state_sends_no_sql_like_catalog_search(svc):
    s, fake = svc
    r = await s.equipment("student-01", "http", "клапан")             # 25FAKE_007_CLS — единственный агрегат
    assert (r["tool"], r["status"], r["cache"]) == ("equipment", "ok", "none")
    assert (r["units_total"], r["tags_total"], r["measure_points_total"], r["truncated"]) == (1, 1, 0, False)
    assert r["units"] == [{"code": "25FAKE_007", "description": "Клапан подачи закрыт", "signals": ["CLS"],
                          "group": "25"}]
    assert r["groups"] == {"25": 1} and "state_counts" not in r
    assert (r["mentions_total"], r["mentions"]) == (0, [])             # Д19б: ни одного упоминания — без заметки
    assert set(EQUIPMENT_NOTES) <= set(r["notes"])
    assert fake.calls == []                                           # каталог в памяти — SQL не шлёт
    e = s.role.events.recent()[0]
    assert (e.tool, e.outcome, e.summary) == ("equipment", "ok", "агрегатов 1, тегов 1")


async def test_equipment_measure_point_counted_separately_from_units(svc):
    s, _ = svc
    r = await s.equipment("student-01", "http", "качество")           # QFAKE_010 — точка измерения, не агрегат
    assert (r["units_total"], r["measure_points_total"], r["units"]) == (0, 1, [])


async def test_equipment_no_match_is_ok_with_zeros(svc):
    s, _ = svc
    r = await s.equipment("student-01", "http", "zzzz-нет-такого")
    assert (r["status"], r["units_total"], r["tags_total"], r["measure_points_total"], r["units"], r["groups"]) == (
        "ok", 0, 0, 0, [], {})


@pytest.mark.parametrize("kwargs,reason", [
    ({"query": ""}, "query: нужна строка от 1 до 200 знаков"),
    ({"query": "x" * 201}, "query: нужна строка от 1 до 200 знаков"),
    ({"query": 5}, "query: нужна строка от 1 до 200 знаков"),
    ({"query": "насос", "with_state": "yes"}, "with_state: нужен bool"),
    ({"query": "насос", "with_state": 1}, "with_state: нужен bool"),
])
async def test_equipment_argument_errors(svc, kwargs, reason):
    s, fake = svc
    r = await s.equipment("student-01", "http", **kwargs)
    assert (r["status"], r["message"]) == ("refused", MESSAGES["args"] + reason)
    assert fake.calls == []


async def test_equipment_76_tags_full_list_not_truncated(tmp_path):      # Д19: было ≤ 50 у catalog_search
    rows = [(f"40FAKE_{i:03d}_RUN", f"Насос {i} работа", 2, None, None, None) for i in range(1, 77)]
    whitelist = {r[0] for r in rows}
    s, fake = _equipment_service(tmp_path, rows, whitelist)
    r = await s.equipment("student-01", "http", "насос")
    assert (r["status"], r["units_total"], r["tags_total"], len(r["units"]), r["truncated"]) == (
        "ok", 76, 76, 76, False)
    assert fake.calls == []


async def test_equipment_with_state_four_counters(tmp_path):
    rows = [("50FAKE_001_RUN", "Агрегат 1 работа", 2, None, None, None),
            ("50FAKE_002_RUN", "Агрегат 2 работа", 2, None, None, None),
            ("50FAKE_003_RUN", "Агрегат 3 работа", 2, None, None, None),
            ("50FAKE_004_OPN", "Агрегат 4 открыт", 2, None, None, None)]
    whitelist = {r[0] for r in rows}
    live = {"50FAKE_001_RUN": (datetime(2026, 10, 1, 11, 0), 1.0, 0),       # работает
            "50FAKE_002_RUN": (datetime(2026, 10, 1, 11, 0), 0.0, 0),       # стоит
            "50FAKE_003_RUN": (datetime(2026, 10, 1, 11, 0), 1.0, 64)}      # недостоверное качество
    s, fake = _equipment_service(tmp_path, rows, whitelist, live=live)
    r = await s.equipment("student-01", "http", "агрегат", with_state=True)
    by_code = {u["code"]: u for u in r["units"]}
    assert (by_code["50FAKE_001"]["state"], by_code["50FAKE_002"]["state"], by_code["50FAKE_003"]["state"],
            by_code["50FAKE_004"]["state"]) == ("running", "stopped", "no_data", "no_run_tag")
    assert by_code["50FAKE_004"]["time"] is None
    assert r["state_counts"] == {"running": 1, "stopped": 1, "no_data": 1, "no_run_tag": 1}
    assert set(sent_of(fake)) == {"50FAKE_001_RUN", "50FAKE_002_RUN", "50FAKE_003_RUN"}       # без тега 004 — SQL
    e = s.role.events.recent()[0]
    assert tuple(e.tags) == ("50FAKE_001_RUN", "50FAKE_002_RUN", "50FAKE_003_RUN")
    assert set(e.sent) == {"50FAKE_001_RUN", "50FAKE_002_RUN", "50FAKE_003_RUN"}


async def test_equipment_with_state_stale_note_is_a_day_not_ten_minutes(tmp_path):
    rows = [("60FAKE_001_RUN", "Насос 60 работа", 2, None, None, None)]
    live = {"60FAKE_001_RUN": (datetime(2026, 9, 29, 10, 0), 1.0, 0)}       # больше суток до «сейчас» 01.10 12:00
    s, fake = _equipment_service(tmp_path, rows, {"60FAKE_001_RUN"}, live=live)
    r = await s.equipment("student-01", "http", "насос", with_state=True)
    unit = r["units"][0]
    assert unit["state"] == "running" and unit["item_notes"] == [EQUIPMENT_STALE_NOTE]


async def test_equipment_with_state_batches_by_max_names(tmp_path):
    rows = [(f"80FAKE_{i:03d}_RUN", f"Агрегат {i}", 2, None, None, None) for i in range(1, 21)]   # 20 > MAX_NAMES
    whitelist = {r[0] for r in rows}
    live = {name: (datetime(2026, 10, 1, 11, 0), 1.0, 0) for name in whitelist}
    s, fake = _equipment_service(tmp_path, rows, whitelist, live=live)
    r = await s.equipment("student-01", "http", "агрегат", with_state=True)
    assert r["state_counts"] == {"running": 20, "stopped": 0, "no_data": 0, "no_run_tag": 0}
    assert len(fake.calls) == 2                                      # 16 + 4 — пачки по пределу tag_now
    assert len(set(sent_of(fake))) == 20


async def test_equipment_with_state_over_run_cap_is_refused_before_sql(tmp_path):
    rows = [(f"70FAKE_{i:03d}_RUN", f"Агрегат {i}", 2, None, None, None) for i in range(1, EQUIPMENT_RUN_CAP + 2)]
    whitelist = {r[0] for r in rows}
    s, fake = _equipment_service(tmp_path, rows, whitelist)
    r = await s.equipment("student-01", "http", "агрегат", with_state=True)
    assert (r["status"], r["message"]) == ("refused", EQUIPMENT_RUN_LIMIT_MESSAGE)
    assert fake.calls == []                                           # до ворот SQL не шлёт — сузьте запрос сами


async def test_equipment_units_cap_sets_truncated(tmp_path):
    rows = [(f"90FAKE_{i:04d}_LOD", f"Агрегат {i}", 1, 0.0, 100.0, "%") for i in range(1, EQUIPMENT_UNITS_CAP + 5)]
    whitelist = {r[0] for r in rows}
    s, fake = _equipment_service(tmp_path, rows, whitelist)
    r = await s.equipment("student-01", "http", "агрегат")
    assert (r["units_total"], len(r["units"]), r["truncated"]) == (EQUIPMENT_UNITS_CAP + 4, EQUIPMENT_UNITS_CAP, True)
    assert any("сузьте запрос" in n for n in r["notes"])


# --- Д19б: слово запроса должно стоять в начале названия — иначе упоминание, не агрегат по числу ---

async def test_equipment_mentions_separated_from_units_with_note(tmp_path):
    rows = [("95FAKE_001_RUN", "Насос циркуляционный 1", 2, None, None, None),
            ("95FAKE_002_RUN", "Клапан подачи массы после насоса", 2, None, None, None),
            ("95FAKE_003_RUN", "Вентилятор охлаждения двигателя насоса", 2, None, None, None)]
    whitelist = {r[0] for r in rows}
    s, fake = _equipment_service(tmp_path, rows, whitelist)
    r = await s.equipment("student-01", "http", "насос")
    assert (r["units_total"], [u["code"] for u in r["units"]]) == (1, ["95FAKE_001"])
    assert (r["mentions_total"], [m["code"] for m in r["mentions"]]) == (2, ["95FAKE_002", "95FAKE_003"])
    assert r["mentions"][0] == {"code": "95FAKE_002", "description": "Клапан подачи массы после насоса",
                               "signals": ["RUN"]}
    assert EQUIPMENT_MENTION_NOTE.format(n=2) in r["notes"]
    assert fake.calls == []                                           # упоминания — тоже из каталога в памяти


async def test_equipment_no_mentions_means_no_mention_note(svc):
    s, _ = svc
    r = await s.equipment("student-01", "http", "клапан")              # 25FAKE_007_CLS — голова, упоминаний нет
    assert r["mentions_total"] == 0
    assert not any(n.startswith("число —") for n in r["notes"])


async def test_equipment_with_state_counts_only_head_not_mentions(tmp_path):
    rows = [("96FAKE_001_RUN", "Насос циркуляционный 1", 2, None, None, None),
            ("96FAKE_002_RUN", "Клапан после насоса", 2, None, None, None)]
    whitelist = {r[0] for r in rows}
    live = {"96FAKE_001_RUN": (datetime(2026, 10, 1, 11, 0), 1.0, 0),
            "96FAKE_002_RUN": (datetime(2026, 10, 1, 11, 0), 1.0, 0)}
    s, fake = _equipment_service(tmp_path, rows, whitelist, live=live)
    r = await s.equipment("student-01", "http", "насос", with_state=True)
    assert (r["units_total"], [u["code"] for u in r["units"]]) == (1, ["96FAKE_001"])
    assert (r["mentions_total"], [m["code"] for m in r["mentions"]]) == (1, ["96FAKE_002"])
    assert r["state_counts"] == {"running": 1, "stopped": 0, "no_data": 0, "no_run_tag": 0}
    assert set(sent_of(fake)) == {"96FAKE_001_RUN"}                    # упоминание не читает _RUN


async def test_equipment_mentions_capped_at_100(tmp_path):
    rows = [(f"97FAKE_{i:03d}_RUN", f"Клапан {i} после насоса", 2, None, None, None) for i in range(1, 103)]
    whitelist = {r[0] for r in rows}
    s, fake = _equipment_service(tmp_path, rows, whitelist)
    r = await s.equipment("student-01", "http", "насос")
    assert (r["units_total"], r["mentions_total"], len(r["mentions"])) == (0, 102, EQUIPMENT_MENTIONS_CAP)
    assert fake.calls == []


# Д21: calc_spc — карта Шухарта и SPC по одному аналоговому тегу; чтение — тем же путём, что tag_series/
# calc_deviation (ворота, белый список, бюджет, точки, кэш, tool "tag_series"); считает служба (pcbk_core.spc),
# не агент. Ряд из 24 вымышленных точек (_SPC_RAW): индекс 5 — NULL, индекс 13 — недостоверное качество (64,
# значение есть) — оба пропускаются (`skipped` = 2), остаются 22 валидных значения (_SPC_VALUES); выброс на
# индексе 3 (50.0) даёт заметные нарушения правил Нельсона. Эталоны — прямой вызов pcbk_core.spc (та же
# библиотека, что внутри службы): здесь проверяется ТОЛЬКО сборка ответа (чтение, пропуск, подгруппы, время,
# заметки), арифметика самих формул уже проверена test_spc.py (план Д21 Task 1).

_SPC_RAW = [10.1, 9.8, 10.3, 50.0, 10.0, None, 9.9, 10.2, 9.7, 10.4, 9.6, 10.1, 9.9, 10.0, 10.2, 9.8, 10.3, 9.9,
           10.1, 10.0, 9.8, 10.2, 9.9, 10.0]
_SPC_QUAL = [64 if i == 13 else 0 for i in range(len(_SPC_RAW))]
_SPC_BASE = datetime(2026, 10, 2, 0, 0)
_SPC_VALID_IDX = [i for i in range(len(_SPC_RAW)) if not (_SPC_RAW[i] is None or _SPC_QUAL[i] != 0)]
_SPC_VALUES = [_SPC_RAW[i] for i in _SPC_VALID_IDX]            # 22 валидных значения


def _spc_iso(i: int) -> str:
    return (_SPC_BASE + timedelta(hours=i)).isoformat(timespec="seconds") + "+05:00"


def _spc_series() -> list[tuple]:
    return [(_SPC_BASE + timedelta(hours=i), v, q) for i, (v, q) in enumerate(zip(_SPC_RAW, _SPC_QUAL))]


async def test_calc_spc_individuals_answer_matches_spc_module(svc):
    s, fake = svc
    fake.series["20FAKE_001_PV"] = _spc_series()
    r = await s.calc_spc("student-01", "http", "20FAKE_001_PV", period="last_8h", points=24)

    expected = spc_lib.individuals(_SPC_VALUES)
    expected_violations = spc_lib.rules(expected["points"], expected["cl"], expected["sigma_point"])
    flagged = {v["index"] for v in expected_violations}
    expected_sparkline = spc_lib.sparkline(expected["points"], expected["lcl"], expected["ucl"], flagged)
    expected_sigma_overall = statistics.stdev(_SPC_VALUES)

    assert (r["tool"], r["status"], r["cache"]) == ("calc_spc", "ok", "miss")
    assert (r["tag"], r["unit"], r["chart"]) == ("20FAKE_001_PV", "л/час", "I-MR")
    assert (r["n_points"], r["skipped"]) == (22, 2)
    assert r["cl"] == pytest.approx(expected["cl"])
    assert r["ucl"] == pytest.approx(expected["ucl"])
    assert r["lcl"] == pytest.approx(expected["lcl"])
    assert r["sigma_within"] == pytest.approx(expected["sigma_hat"])
    assert r["sigma_overall"] == pytest.approx(expected_sigma_overall)
    sec = r["secondary"]
    assert sec["chart"] == "MR"
    assert sec["cl"] == pytest.approx(expected["secondary"]["cl"])
    assert sec["ucl"] == pytest.approx(expected["secondary"]["ucl"])
    assert sec["lcl"] == pytest.approx(expected["secondary"]["lcl"])
    assert r["capability"] is None
    assert len(expected_violations) > 0 and r["in_control"] is False       # выброс даёт нарушения — не пустой тест
    # ночь 16.10: нарушения — интервалами по правилам (поточечный список обрезался в чате), ряда в ответе нет
    by_rule = {}
    for viol in expected_violations:
        by_rule.setdefault(str(viol["rule"]), []).append(viol["index"])
    assert r["rule_counts"] == {k: len(v) for k, v in by_rule.items()}
    assert sum(iv["points"] for iv in r["violations"]) == len(expected_violations)
    valid_times = {_spc_iso(i) for i in _SPC_VALID_IDX}
    for iv in r["violations"]:
        idxs = sorted(by_rule[str(iv["rule"])])
        assert iv["start"] in valid_times and iv["end"] in valid_times and iv["points"] >= 1
        assert iv["name_ru"] and iv["value_min"] <= iv["value_max"]
    assert r["sparkline"] == expected_sparkline
    assert "points" not in r
    # Д24: числа для графика в чате — последним полем, 5 значащих цифр; MR — на одну точку меньше
    plot = r["plot"]
    assert list(r)[-1] == "plot"
    assert plot["points"] == [float(f"{v:.5g}") for v in expected["points"]]
    assert plot["secondary"] == [float(f"{v:.5g}") for v in expected["secondary"]["points"]]
    assert len(plot["secondary"]) == len(plot["points"]) - 1 == 21
    assert plot["flagged"] == sorted(flagged) and plot["flagged"]
    assert (plot["lsl"], plot["usl"]) == (None, None)
    assert (plot["start"], plot["end"]) == (_spc_iso(_SPC_VALID_IDX[0]), _spc_iso(_SPC_VALID_IDX[-1]))
    assert {SPC_GRID_NOTE, SPC_CONTROL_NOTE} <= set(r["notes"])
    assert SPC_BOUNDS_NOTE not in r["notes"] and SPC_FEW_POINTS_NOTE not in r["notes"]


async def test_calc_spc_xbar_r_subgroup4(svc):
    s, fake = svc
    fake.series["20FAKE_001_PV"] = _spc_series()
    r = await s.calc_spc("student-01", "http", "20FAKE_001_PV", period="last_8h", points=24, subgroup=4)

    expected = spc_lib.xbar_r(_SPC_VALUES, 4)
    expected_violations = spc_lib.rules(expected["points"], expected["cl"], expected["sigma_point"])
    chunk_last = [3, 8, 12, 17, 21]            # последняя точка каждой из 5 полных подгрупп по 4 (22 // 4)

    assert (r["status"], r["chart"], r["skipped"]) == ("ok", "X̄-R", 2)
    assert r["n_points"] == len(expected["points"]) == 5
    assert r["cl"] == pytest.approx(expected["cl"])
    assert r["ucl"] == pytest.approx(expected["ucl"])
    assert r["lcl"] == pytest.approx(expected["lcl"])
    assert r["sigma_within"] == pytest.approx(expected["sigma_hat"])
    sec = r["secondary"]
    assert sec["chart"] == "R"
    assert sec["cl"] == pytest.approx(expected["secondary"]["cl"])
    assert sec["ucl"] == pytest.approx(expected["secondary"]["ucl"])
    assert sec["lcl"] == pytest.approx(expected["secondary"]["lcl"])
    assert "points" not in r
    assert sum(iv["points"] for iv in r["violations"]) == len(expected_violations)
    chunk_times = {_spc_iso(i) for i in chunk_last}            # момент подгруппы — её последняя точка
    for iv in r["violations"]:
        assert iv["start"] in chunk_times and iv["end"] in chunk_times
    assert any("хвост короче 4" in n for n in r["notes"])      # 2 точки хвоста (22 % 4) не вошли в подгруппу
    assert r["plot"]["points"] == [float(f"{v:.5g}") for v in expected["points"]]
    assert len(r["plot"]["secondary"]) == 5 and r["plot"]["end"] == _spc_iso(21)     # R — по подгруппам
    assert SPC_FEW_POINTS_NOTE in r["notes"]                    # 5 подгрупп < 20 — границы ориентировочные


async def test_calc_spc_capability_with_bounds(svc):
    s, fake = svc
    fake.series["20FAKE_001_PV"] = _spc_series()
    r = await s.calc_spc("student-01", "http", "20FAKE_001_PV", period="last_8h", points=24, lsl=5.0, usl=60.0)

    expected_chart = spc_lib.individuals(_SPC_VALUES)
    expected_cap = spc_lib.capability(_SPC_VALUES, expected_chart["sigma_hat"], 5.0, 60.0)

    assert r["status"] == "ok"
    assert r["capability"] == pytest.approx(expected_cap)
    assert SPC_BOUNDS_NOTE in r["notes"]
    assert (r["plot"]["lsl"], r["plot"]["usl"]) == (5.0, 60.0)            # Д24: допуск — линиями на графике


async def test_calc_spc_refuses_discrete_tag(svc):
    s, fake = svc
    r = await s.calc_spc("student-01", "http", "25FAKE_007_CLS", period="last_8h")
    assert (r["status"], r["message"]) == ("refused", MESSAGES["none"])
    assert r["items"] == [{"tag": "25FAKE_007_CLS", "status": "discrete", "text": ITEM_TEXTS["discrete_series"]}]
    assert fake.calls == []


async def test_calc_spc_refuses_points_below_min(svc):
    s, fake = svc
    r = await s.calc_spc("student-01", "http", "20FAKE_001_PV", period="last_8h", points=2)
    assert (r["status"], r["message"]) == ("refused", MESSAGES["args"] + "points: от 20 до 96")
    assert fake.calls == []


async def test_calc_spc_partial_when_too_few_valid_points(svc):             # данные читались, но карты не хватило
    s, fake = svc
    base = datetime(2026, 10, 3, 0, 0)
    rows = [(base, 10.0, 0)] + [(base + timedelta(hours=i), None, 0) for i in range(1, 20)]
    fake.series["20FAKE_001_PV"] = rows
    r = await s.calc_spc("student-01", "http", "20FAKE_001_PV", period="last_8h", points=20)
    assert (r["status"], r["chart"], r["n_points"], r["skipped"]) == ("partial", "I-MR", 0, 19)
    assert r["cl"] is None and r["capability"] is None and r["secondary"] is None
    assert r["violations"] == [] and r["in_control"] is None and r["sparkline"] == ["", ""] and r["plot"] is None
    assert SPC_NOT_ENOUGH_NOTE in r["notes"]


@pytest.mark.parametrize("kwargs,reason", [
    ({"tag": "20FAKE_001_PV", "subgroup": 0}, "subgroup: целое от 1 до 10"),
    ({"tag": "20FAKE_001_PV", "subgroup": 11}, "subgroup: целое от 1 до 10"),
    ({"tag": "20FAKE_001_PV", "lsl": "x"}, "lsl: число"),
    ({"tag": "20FAKE_001_PV", "usl": float("nan")}, "usl: число"),
    ({"tag": "20FAKE_001_PV", "lsl": 10.0, "usl": 5.0}, "usl: должен быть больше lsl"),
    ({"tag": "20FAKE_001_PV", "points": 97}, "points: от 20 до 96"),
    ({"tag": 5}, "tag: нужен тег строкой"),
])
async def test_calc_spc_argument_errors(svc, kwargs, reason):
    s, _ = svc
    r = await s.calc_spc("student-01", "http", period="last_8h", **kwargs)
    assert (r["status"], r["message"]) == ("refused", MESSAGES["args"] + reason)


async def test_calc_spc_event_has_tag(svc):
    s, fake = svc
    fake.series["20FAKE_001_PV"] = _spc_series()
    r = await s.calc_spc("ops", "mcp", "20FAKE_001_PV", period="last_8h", points=24)
    assert r["status"] == "ok"
    e = s.role.events.recent()[0]
    assert (e.tool, e.caller, e.channel, e.outcome) == ("calc_spc", "ops", "mcp", "ok")
    assert tuple(e.tags) == ("20FAKE_001_PV",) and set(e.sent) == {"20FAKE_001_PV"}


def test_spc_intervals_merge_consecutive_points_per_rule():                       # ночь 16.10
    from pcbk_core.data.service import _spc_intervals
    times = [f"t{i}" for i in range(10)]
    points = [float(i) for i in range(10)]
    viol = [{"rule": 1, "name_ru": "за 3σ", "index": i, "zone": "вне"} for i in (0, 1, 2, 5, 6)]
    viol += [{"rule": 2, "name_ru": "9 подряд", "index": 9, "zone": "C"}]
    out, counts = _spc_intervals(viol, times, points)
    assert counts == {"1": 5, "2": 1}
    assert [(x["rule"], x["start"], x["end"], x["points"]) for x in out] == [
        (1, "t0", "t2", 3), (1, "t5", "t6", 2), (2, "t9", "t9", 1)]
    assert (out[0]["value_min"], out[0]["value_max"]) == (0.0, 2.0)
