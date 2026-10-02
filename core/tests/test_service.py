"""Служба данных: tag_now, tag_period, catalog_search — ответы, отказы, события, кэш, single-flight."""
import asyncio
import json
import sqlite3
import threading
from dataclasses import replace
from datetime import datetime

import pytest

from fakes import LIVE_ROWS
from helpers import SETTINGS, make_role, make_service, sent_of
from pcbk_core.data import CONFIG_ERRORS
from pcbk_core.data import service as service_module
from pcbk_core.data.gate import GateRefused, GateResult, SlidingWindow
from pcbk_core.data.historian import HistorianError
from pcbk_core.data.periods import Period
from pcbk_core.data.service import (AVG_KIND, CLAMP_NOTE, DRILL_SUMMARY, ERROR_MESSAGE, FAIL_STALE_S, FAIL_STREAK,
                                    ITEM_TEXTS, MESSAGES, NO_LAST_NOTE, OPEN_NOTE, SHIFT_NOTE, SIMILAR_NOTE,
                                    SOURCE_LIVE, SOURCE_SUMMARY, STD_KIND, TOOL_WINDOW)
from pcbk_core.data.sql import HAS_LAST, SUMMARY_COLUMNS, clock_sql, live_sql
from pcbk_core.logs import setup_logging

pytestmark = pytest.mark.anyio


@pytest.fixture
async def svc(tmp_path):
    s, fake = make_service(tmp_path)          # роль с загруженным каталогом; fake.calls очищены
    return s, fake


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
                                for n in range(1, 11)))
    assert sum("AnalogSummaryHistory" in q for c in fake.calls for q in c) == 1
    assert sorted(r["cache"] for r in rs) == ["miss"] + ["shared"] * 9
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


async def test_drill_tool_error_no_sql(tmp_path):                                 # Review Focus 3
    s, fake = make_service(tmp_path, settings=replace(SETTINGS, DRILL_DATA="tool_error"))
    for _ in range(FAIL_STREAK):
        r = await s.tag_now("ops", "http", ["20FAKE_001_PV"])
        assert (r["status"], r["message"]) == ("error", ERROR_MESSAGE) and "items" not in r
    assert fake.calls == [] and s.tools_stats()["failing"] == ["tag_now"]       # SQL не ушёл ни разу
    assert s.role.events.recent()[0].summary == DRILL_SUMMARY
    r = await s.catalog_search("ops", "http", "расход")                           # каталог в памяти — тоже без данных
    assert r["status"] == "error" and "matches" not in r


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
