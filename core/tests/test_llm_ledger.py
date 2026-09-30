"""Журнал вызовов LLM: память синхронно, SQLite — один поток-писатель; вызов без цены — по верхней оценке."""
import logging
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from helpers import wait_until
from pcbk_core.llm.ledger import STATUSES, WRITE_RETRY_S, WRITE_TIMEOUT_S, Budget, Ledger, Usage


def lg(path):                                        # журнал, открытый как в lifespan
    led = Ledger(path)
    assert led.open() is True
    return led

T = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
U = Usage(0.0123, 100, 20, 60, 10, 5)

def close_ok(led, cid, usage=U, source="stream"):
    led.close_call(cid, status="ok", code="", http_status=200, provider="Fireworks", provider_ok=True,
                   generation_id="gen-1", usage=usage, cost_source=source, duration_ms=900)

def test_usage_from_openrouter():
    u = Usage.from_openrouter({"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0123,
                               "prompt_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 10},
                               "completion_tokens_details": {"reasoning_tokens": 5}})
    assert u == U and Usage.from_openrouter({"prompt_tokens": 1}).cost is None

def opened(led, caller="student-01", est=0.5):
    return led.open_call(ts=T, caller=caller, session=None, model="m", step=1, estimate=est)

def test_ledger_sums_survive_reopen(tmp_path):
    p = str(tmp_path / "core.db")
    led = lg(p)
    close_ok(led, opened(led))
    led.refused(ts=T, caller="student-02", session=None, model="x", code="pcbk_turn_limit", http_status=403)
    led.flush()                                                                # строки у писателя — в файле
    again = lg(p)
    assert (again.spent(), again.spent("student-01"), again.spent("student-02")) == (pytest.approx(0.0123),
                                                                                     pytest.approx(0.0123), 0.0)
    assert [r.status for r in again.recent()] == ["refused", "ok"]

def test_open_call_counts_estimate_until_closed(tmp_path):                        # Review Focus 6
    led = lg(str(tmp_path / "core.db"))
    cid = opened(led, est=0.5)
    assert led.spent("student-01") == pytest.approx(0.5) and led.estimated() == pytest.approx(0.5)
    assert (led.reserved_open(), led.has_open_or_pending()) == (pytest.approx(0.5), True)
    close_ok(led, cid)
    assert led.spent("student-01") == pytest.approx(0.0123) and led.estimated() == 0.0
    assert (led.reserved_open(), led.has_open_or_pending()) == (0.0, False)

def test_constructor_does_no_io_and_failed_open_keeps_memory(tmp_path):         # раунд 2: договор Role Д3а
    path = tmp_path / "nope" / "core.db"                                      # каталога нет
    led = Ledger(str(path))
    assert not path.parent.exists()                                           # конструктор файлов не трогает
    assert led.open() is False and led.opened is False and led.last_error
    opened(led, est=0.5)
    assert led.spent() == pytest.approx(0.5)                                  # память работает и без файла

def test_second_open_does_not_double(tmp_path):                                  # П11
    led = lg(str(tmp_path / "core.db"))
    close_ok(led, opened(led))
    assert led.open() is True and led.spent() == pytest.approx(0.0123)        # как make_llm_role, затем lifespan

def test_sums_are_all_time_counters_24h(tmp_path):                               # П11: бюджет без сброса
    p = str(tmp_path / "core.db")
    old = Ledger(p, wallclock=lambda: T - timedelta(days=3)); assert old.open()
    cid = old.open_call(ts=T - timedelta(days=3), caller="ops", session=None, model="m", step=1, estimate=0.5)
    old.close_call(cid, status="ok", code="", http_status=200, provider="Fireworks", provider_ok=False,
                   generation_id="gen-1", usage=replace(U, cost=2.0), cost_source="stream", duration_ms=900)
    old.flush()
    again = lg(p)
    assert again.spent("ops") == pytest.approx(2.0) and again.totals(T)["provider_mismatch_24h"] == 0

def test_writes_leave_loop_thread(tmp_path, monkeypatch):                        # П5: SQLite — только писатель
    led = lg(str(tmp_path / "core.db"))
    threads, gate, real = [], threading.Event(), led._connect
    def slow(*a, **k):
        threads.append(threading.current_thread().name)
        gate.wait(5)                                                             # писатель занят
        return real(*a, **k)
    monkeypatch.setattr(led, "_connect", slow)
    t = time.monotonic()
    cid = opened(led, est=0.5); close_ok(led, cid); led.meta_set("k", 1.0)
    assert time.monotonic() - t < 0.1                                            # вызывающий не ждёт SQLite
    assert led.spent() == pytest.approx(0.0123) and led.meta_get("k") == 1.0    # память — сразу
    gate.set(); led.flush()
    assert set(threads) == {"pcbk-ledger-writer"} and led.consecutive_failures == 0
    assert [(r.id, r.status) for r in led.recent()] == [(cid, "ok")]           # порядок INSERT → REPLACE

def test_locked_db_write_is_counted_not_waited(tmp_path):                        # П5: WRITE_TIMEOUT_S
    led = lg(str(tmp_path / "core.db"))
    holder = sqlite3.connect(tmp_path / "core.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")                                            # блокировку держит другой
    try:
        t = time.monotonic()
        opened(led, est=0.5)
        assert time.monotonic() - t < 0.1
        wait_until(lambda: led.consecutive_failures >= 1)                        # ревью I1: сбой — счётчик,
        assert time.monotonic() - t < WRITE_TIMEOUT_S + 1.0                      # а не ожидание в 5 с
        assert "locked" in led.last_error and led.spent() == pytest.approx(0.5) and led.queue_size == 1
    finally:
        holder.execute("ROLLBACK"); holder.close()
    led.flush()                                                                  # блокировку сняли — строка дошла
    assert led.consecutive_failures == 0 and [r.status for r in led.recent()] == ["open"]

def test_open_call_after_crash_is_lost_at_estimate(tmp_path):                    # Review Focus 2
    p = str(tmp_path / "core.db")
    first = lg(p)
    opened(first, est=0.5)
    first.flush()                                                              # «падение» после записи строки open
    again = lg(p)
    t = again.totals(T + timedelta(minutes=1))
    assert (t["cost_unknown_24h"], t["pending"], again.recent(1)[0].status) == (1, 0, "lost")
    assert again.spent("student-01") == pytest.approx(0.5)                     # не ноль

def test_pending_then_generation_cost(tmp_path):                                 # Review Focus 2
    led = lg(str(tmp_path / "core.db"))
    cid = opened(led, caller="ops", est=0.5)
    close_ok(led, cid, usage=None, source="pending")
    assert led.totals(T)["pending"] == 1 and led.spent("ops") == pytest.approx(0.5)
    led.set_cost(cid, 0.004, "generation")
    assert led.totals(T)["pending"] == 0 and led.spent("ops") == pytest.approx(0.004)
    cid2 = opened(led, caller="ops", est=0.5)
    close_ok(led, cid2, usage=None, source="pending"); led.set_cost(cid2, None, "unknown")
    assert led.spent("ops") == pytest.approx(0.504) and led.totals(T)["cost_unknown_24h"] == 1

def test_pending_survives_restart_for_resolve_and_stuck(tmp_path):              # раунд 2: возраст — от дозапроса
    p = str(tmp_path / "core.db")
    led = lg(p)
    cid = opened(led, est=0.5)
    led.close_call(cid, status="cancelled", code="", http_status=200, provider=None, provider_ok=None,
                   generation_id="gen-9", usage=None, cost_source="pending", duration_ms=10, now=T)
    assert led.totals(T + timedelta(minutes=16))["pending_stuck"] == 1
    led.flush()
    again = Ledger(p, wallclock=lambda: T + timedelta(minutes=10))              # перезапуск через 10 мин
    assert again.open() is True
    assert again.pending_to_resolve() == [(cid, "gen-9")] and again.spent() == pytest.approx(0.5)
    assert again.totals(T + timedelta(minutes=20))["pending_stuck"] == 0      # отсчёт — от open() (П11)
    again.resolve_started(cid, T + timedelta(minutes=20))
    assert again.totals(T + timedelta(minutes=26))["pending_stuck"] == 0      # дозапрос идёт
    assert again.totals(T + timedelta(minutes=36))["pending_stuck"] == 1

def test_write_failure_keeps_memory_and_flags(tmp_path, monkeypatch):
    led = lg(str(tmp_path / "core.db"))
    monkeypatch.setattr(led, "_connect", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("database or disk is full")))
    led.retry_pause_s = 0.01
    opened(led, est=0.5)
    assert led.spent() == pytest.approx(0.5)                                   # память — сразу
    wait_until(lambda: led.consecutive_failures >= 2)                          # ревью I1: повтор, счётчик растёт
    assert "disk is full" in led.last_error and led.write_failures_total >= 2
    assert (led.queue_size, led.writer_alive) == (1, True)
    monkeypatch.undo(); led.flush()
    assert led.consecutive_failures == 0 and [r.id for r in led.recent()] == [1]

def test_meta_roundtrip(tmp_path):
    p = str(tmp_path / "core.db")
    a = lg(p)
    a.meta_set("key_base_usd", 12.5)
    a.flush()
    assert (lg(p).meta_get("key_base_usd"), lg(p).meta_get("nope")) == (12.5, None)

def test_budget_total_before_share_and_key(tmp_path):
    led = lg(str(tmp_path / "core.db"))
    for caller, cost in (("student-01", 100.0), ("student-02", 5.0)):
        close_ok(led, opened(led, caller=caller), usage=replace(U, cost=cost))
    b = Budget(led, total_usd=1000.0, share_usd=100.0, ops_share_usd=80.0)
    assert (b.refusal("student-01"), b.refusal("student-02"), b.share_of("ops")) == ("share_spent", None, 80.0)
    assert Budget(led, total_usd=100.0, share_usd=100.0, ops_share_usd=80.0).refusal("student-02") == "budget_spent"
    by_key = Budget(led, total_usd=1000.0, share_usd=100.0, ops_share_usd=80.0, key_spent=lambda: 1000.0)
    assert (by_key.total_spent(), by_key.refusal("student-02")) == (1000.0, "budget_spent")   # OpenRouter насчитал больше
    opened(led, caller="student-03", est=0.5)                                  # идущий вызов
    drift = Budget(led, total_usd=1000.0, share_usd=100.0, ops_share_usd=80.0, key_spent=lambda: 107.0)
    assert drift.total_spent() == pytest.approx(107.5)                        # по ключу + резерв, не только max
    assert drift.page_spent() == pytest.approx(107.0)                         # число строки — без резерва

def test_no_content_columns(tmp_path):                                           # П10: таблица создана open()
    lg(str(tmp_path / "core.db"))
    cols = {r[1] for r in sqlite3.connect(tmp_path / "core.db").execute("PRAGMA table_info(llm_calls)")}
    assert {"cost_est", "cost_source", "generation_id"} <= cols                  # положительный контроль
    assert not cols & {"messages", "content", "body", "prompt", "answer", "text"}


# --- сверх брифа ---

NO_USAGE = Usage(None, 0, 0, 0, 0, 0)


def test_names_and_values_match_event_log():                                     # П5: имена и значения — как в Д3б
    from pcbk_core.data import events
    from pcbk_core.llm import ledger
    assert (ledger.WRITE_TIMEOUT_S, ledger.SQLITE_TIMEOUT_S, ledger.ERROR_CHARS) == \
           (events.WRITE_TIMEOUT_S, events.SQLITE_TIMEOUT_S, events.ERROR_CHARS) == (0.5, 5.0, 200)
    assert (ledger.LEDGER_CHECK_S, ledger.PENDING_STUCK_S, WRITE_RETRY_S) == (30.0, 900, 0.25)
    assert STATUSES == ("open", "ok", "upstream_error", "money", "refused", "cancelled", "lost", "unknown")  # N3


def test_table_has_exactly_the_brief_columns(tmp_path):
    lg(str(tmp_path / "core.db"))
    db = sqlite3.connect(tmp_path / "core.db")
    cols = [r[1] for r in db.execute("PRAGMA table_info(llm_calls)")]
    assert cols == ["id", "ts", "caller", "session", "model", "step", "status", "code", "http_status", "provider",
                    "provider_ok", "generation_id", "cost", "cost_est", "cost_source", "prompt_tokens",
                    "completion_tokens", "cached_tokens", "cache_write_tokens", "reasoning_tokens", "duration_ms"]
    assert [r[1] for r in db.execute("PRAGMA table_info(llm_meta)")] == ["key", "value"]
    assert any("ts" in [c[2] for c in db.execute(f"PRAGMA index_info('{r[1]}')")]
               for r in db.execute("PRAGMA index_list(llm_calls)"))
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_usage_wrong_types_are_absent_and_never_raise():                         # задача 5, П21
    bad = {"cost": "0.1", "prompt_tokens": True, "completion_tokens": "5", "prompt_tokens_details": [60],
           "completion_tokens_details": "x"}
    assert Usage.from_openrouter(bad) == NO_USAGE
    assert Usage.from_openrouter({"cost": True, "prompt_tokens_details": {"cached_tokens": 2.0}}) == NO_USAGE
    assert Usage.from_openrouter(None) == NO_USAGE and Usage.from_openrouter([1, 2]) == NO_USAGE
    for cost in (float("nan"), float("inf"), -0.5):                             # ни NaN, ни минус в сумме
        assert Usage.from_openrouter({"cost": cost}).cost is None
    assert Usage.from_openrouter({"cost": 0}).cost == 0.0                       # настоящий ноль — цена
    assert isinstance(Usage.from_openrouter({"cost": 1}).cost, float)


def test_priced_source_without_cost_stays_at_estimate(tmp_path):                 # учёт не знает нуля
    led = lg(str(tmp_path / "core.db"))
    cid = opened(led, est=0.5)
    close_ok(led, cid, usage=NO_USAGE, source="stream")                         # «stream», а цены нет
    assert led.spent() == pytest.approx(0.5) and led.estimated() == pytest.approx(0.5)
    assert led.totals(T)["cost_unknown_24h"] == 1 and led.recent(1)[0].cost_source == "unknown"
    cid2 = opened(led, est=0.25)
    close_ok(led, cid2, usage=None, source="pending")
    led.set_cost(cid2, None, "generation")                                      # «generation», а цены нет
    led.set_cost(cid2, 0.001, "generation")                                     # уже не pending — не меняет
    assert led.spent() == pytest.approx(0.75) and led.totals(T)["cost_unknown_24h"] == 2
    cid3 = opened(led, est=0.25)
    close_ok(led, cid3, usage=replace(U, cost=float("nan")))                    # NaN не отравляет сумму
    assert led.spent() == pytest.approx(1.0)


def test_none_source_is_zero_and_unknown_source_is_estimate(tmp_path):
    led = lg(str(tmp_path / "core.db"))
    cid = opened(led, est=0.5)
    led.close_call(cid, status="upstream_error", code="pcbk_upstream_rejected", http_status=404, provider=None,
                   provider_ok=None, generation_id=None, usage=None, cost_source="none", duration_ms=5)
    assert led.spent() == 0.0 and led.recent(1)[0].cost == 0.0                  # денег нет наверняка
    cid = opened(led, est=0.5)
    led.close_call(cid, status="upstream_error", code="pcbk_upstream_timeout", http_status=504, provider=None,
                   provider_ok=None, generation_id=None, usage=None, cost_source="unknown", duration_ms=5)
    assert led.spent() == pytest.approx(0.5) and led.estimated() == pytest.approx(0.5)


def test_sql_sums_match_memory(tmp_path):                                       # charge() в памяти = SUM при open()
    p = str(tmp_path / "core.db")
    led = lg(p)
    close_ok(led, opened(led, caller="a", est=0.5))                             # цена 0.0123
    close_ok(led, opened(led, caller="a", est=0.7), usage=None, source="pending")   # 0.7 — оценка
    led.close_call(opened(led, caller="b", est=0.3), status="money", code="pcbk_upstream_money", http_status=402,
                   provider=None, provider_ok=None, generation_id=None, usage=None, cost_source="none",
                   duration_ms=1)                                               # 0
    led.close_call(opened(led, caller="b", est=0.3), status="upstream_error", code="pcbk_upstream_error",
                   http_status=200, provider=None, provider_ok=None, generation_id=None,
                   usage=replace(U, cost=0.02), cost_source="none", duration_ms=1)   # none с ценой — цена
    led.close_call(opened(led, caller="b", est=0.2), status="cancelled", code="", http_status=200, provider=None,
                   provider_ok=None, generation_id=None, usage=None, cost_source="unknown", duration_ms=1)  # 0.2
    cid = opened(led, caller="c", est=0.9); close_ok(led, cid, usage=None, source="pending")
    led.set_cost(cid, 0.05, "generation")                                       # 0.05
    led.refused(ts=T, caller="c", session="ses_1", model="m", code="pcbk_share_spent", http_status=402)
    opened(led, caller="c", est=0.4)                                            # идёт: 0.4, после перезапуска — lost
    mem = {c: led.spent(c) for c in "abc"}, {c: led.estimated(c) for c in "abc"}
    assert mem[0] == {"a": pytest.approx(0.7123), "b": pytest.approx(0.22), "c": pytest.approx(0.45)}
    led.flush()
    again = lg(p)
    assert ({c: again.spent(c) for c in "abc"}, {c: again.estimated(c) for c in "abc"}) == \
           (pytest.approx(mem[0]), pytest.approx(mem[1]))
    assert again.totals(T)["by_caller"] == pytest.approx(mem[0])


def test_pending_without_generation_id_is_unknown_after_reopen(tmp_path):
    p = str(tmp_path / "core.db")
    led = lg(p)
    close_ok(led, opened(led, est=0.5), usage=None, source="pending")
    led.close_call(opened(led, est=0.3), status="cancelled", code="", http_status=200, provider=None,
                   provider_ok=None, generation_id=None, usage=None, cost_source="pending", duration_ms=1)
    assert led.totals(T)["pending"] == 2
    led.flush()
    again = lg(p)
    assert again.pending_to_resolve() == [(1, "gen-1")] and again.totals(T)["pending"] == 1
    assert [r.cost_source for r in again.recent()] == ["unknown", "pending"] and again.spent() == pytest.approx(0.8)


def test_counters_window_follows_now_not_clock_at_open(tmp_path):               # 24 ч — от now у totals()
    p = str(tmp_path / "core.db")
    first = lg(p)
    first.close_call(opened(first), status="ok", code="", http_status=200, provider="X", provider_ok=False,
                     generation_id="g", usage=U, cost_source="stream", duration_ms=1)
    opened(first)
    first.flush()
    later = Ledger(p, wallclock=lambda: T + timedelta(days=30)); assert later.open()
    assert (later.totals(T + timedelta(hours=1))["cost_unknown_24h"],
            later.totals(T + timedelta(hours=1))["provider_mismatch_24h"]) == (1, 1)
    assert (later.totals(T + timedelta(hours=25))["cost_unknown_24h"],
            later.totals(T + timedelta(hours=25))["provider_mismatch_24h"]) == (0, 0)


def test_totals_keys_and_values(tmp_path):
    led = lg(str(tmp_path / "core.db"))
    close_ok(led, opened(led, caller="ops"))
    opened(led, caller="student-01", est=0.5)
    t = led.totals(T)
    assert set(t) == {"spent_usd", "estimated_usd", "reserved_usd", "by_caller", "pending", "pending_stuck",
                      "cost_unknown_24h", "provider_mismatch_24h"}
    assert (t["spent_usd"], t["estimated_usd"], t["reserved_usd"]) == \
           (pytest.approx(0.5123), pytest.approx(0.5), pytest.approx(0.5))
    assert t["by_caller"] == {"ops": pytest.approx(0.0123), "student-01": pytest.approx(0.5)}


def test_double_close_and_unknown_ids_do_not_change_sums(tmp_path):
    led = lg(str(tmp_path / "core.db"))
    cid = opened(led, est=0.5)
    close_ok(led, cid); close_ok(led, cid)                                      # второй close — не удваивает
    close_ok(led, 999); led.set_cost(999, 5.0, "generation"); led.resolve_started(999, T)
    assert led.spent() == pytest.approx(0.0123) and led.totals(T)["pending"] == 0
    led.flush()
    assert [r.id for r in led.recent()] == [cid]


def test_bad_estimate_is_refused_before_memory(tmp_path):                        # NaN/минус не ставится в резерв
    led = lg(str(tmp_path / "core.db"))
    for est in (float("nan"), float("inf"), -0.1):
        with pytest.raises(ValueError):
            opened(led, est=est)
    assert (led.spent(), led.has_open_or_pending()) == (0.0, False)
    led.flush()
    assert led.recent() == []


def test_writer_calls_connect_without_arguments(tmp_path, monkeypatch):          # точка подмены задачи 6
    led = lg(str(tmp_path / "core.db"))
    boom = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database or disk is full"))
    monkeypatch.setattr(led, "_connect", boom)
    led.retry_pause_s = 0.01
    opened(led); opened(led)
    wait_until(lambda: led.consecutive_failures >= 2)
    assert led.last_error == "OperationalError: database or disk is full" and led.queue_size == 2
    monkeypatch.undo()
    opened(led); led.flush()                                                    # удача сбрасывает счётчик
    assert led.consecutive_failures == 0 and [r.id for r in led.recent()] == [3, 2, 1]


def test_flush_times_out_and_writer_survives(tmp_path, monkeypatch):
    led = lg(str(tmp_path / "core.db"))
    gate, real = threading.Event(), led._connect
    monkeypatch.setattr(led, "_connect", lambda *a: (gate.wait(5), real(*a))[1])
    opened(led)
    with pytest.raises(TimeoutError):
        led.flush(timeout=0.1)
    gate.set(); led.flush()
    assert [r.status for r in led.recent()] == ["open"]


def test_writable_check_and_close(tmp_path):
    missing = Ledger(str(tmp_path / "nope" / "core.db"))
    assert missing.writable_ok is None
    missing.check()
    assert missing.writable_ok is False and missing.last_error
    missing.flush()                                                              # писатель не запущен — сразу
    before = set(threading.enumerate())
    led = lg(str(tmp_path / "core.db"))
    mine = [t for t in threading.enumerate() if t not in before and t.name == "pcbk-ledger-writer"]
    assert len(mine) == 1 and mine[0].daemon
    led.check()
    assert led.writable_ok is True and led.recent() == []                       # проба откатилась
    opened(led)
    led.close()
    assert led.opened is False and not mine[0].is_alive()                       # писатель остановлен
    assert [r.status for r in led.recent()] == ["open"]                         # close() дописал очередь
    opened(led)                                                                  # после close — только память
    assert led.spent() == pytest.approx(1.0) and len(led.recent()) == 1
    assert led.open() is True and led.spent() == pytest.approx(0.5)              # новый open() — с нуля, по файлу
    assert led.recent(1)[0].status == "lost"


# --- доводка по ревью ---

def test_failed_write_is_retried_not_dropped(tmp_path):                          # I1: иначе после перезапуска $0
    p = str(tmp_path / "core.db")
    led = lg(p)
    led.retry_pause_s = 0.02
    holder = sqlite3.connect(p, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        opened(led, est=0.5)                                                    # INSERT упирается в блокировку
        wait_until(lambda: led.consecutive_failures >= 2)                       # повторяет, счётчик растёт
        opened(led, caller="student-02", est=0.25)                              # следующая операция — за ней
        assert (led.queue_size, led.writer_alive) == (2, True)
    finally:
        holder.execute("ROLLBACK"); holder.close()
    led.flush()
    total = led.write_failures_total
    assert (led.consecutive_failures, led.queue_size) == (0, 0) and total >= 2  # удача сбросила только «подряд»
    again = lg(p)                                                               # «падение» без close()
    assert again.spent("student-01") == pytest.approx(0.5) and again.spent() >= 0.75
    assert led.write_failures_total == total                                    # счётчик не убывает


def test_retry_after_landed_write_is_idempotent(tmp_path, monkeypatch):          # I1: каждая операция повторяема
    led = lg(str(tmp_path / "core.db"))
    led.retry_pause_s = 0.01
    real, seen = led._connect, set()

    class LandedButFailed:                                                      # запись легла, а ответ — сбой:
        def __init__(self, db):                                                 # у каждой операции первая попытка
            self.db = db
        def execute(self, sql, params=()):
            cur = self.db.execute(sql, params)
            if sql.startswith("INSERT") and params not in seen:
                seen.add(params)
                raise sqlite3.OperationalError("disk I/O error")
            return cur
        def close(self):
            self.db.close()

    monkeypatch.setattr(led, "_connect", lambda *a: LandedButFailed(real(*a)))
    cid = opened(led)
    led.refused(ts=T, caller="student-02", session=None, model="x", code="pcbk_turn_limit", http_status=403)
    led.meta_set("k", 1.0)
    led.flush()                                                                 # повтор не застрял на UNIQUE
    assert (led.write_failures_total, led.consecutive_failures) == (3, 0)
    assert [r.id for r in led.recent()] == [cid + 1, cid]


def test_close_on_broken_disk_gives_up_and_counts(tmp_path, monkeypatch):        # I1: что теряется при остановке
    led = lg(str(tmp_path / "core.db"))
    led.retry_pause_s = 0.01
    monkeypatch.setattr(led, "_connect", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    opened(led); opened(led)
    wait_until(lambda: led.consecutive_failures >= 1)
    t = time.monotonic()
    led.close(timeout=0.2)                                                      # не бросает: lifespan выходит
    assert time.monotonic() - t < 2.0 and led.opened is False
    wait_until(lambda: not led.writer_alive)                                    # остановка прервала повторы
    assert (led.queue_size, led.writes_dropped_total) == (0, 2)


def test_fresh_says_the_table_was_just_created(tmp_path):                        # ревью: пропавшая core.db
    p = str(tmp_path / "core.db")
    led = Ledger(p)
    assert led.fresh is None
    assert led.open() is True and led.fresh is True
    assert led.open() is True and led.fresh is True                             # второй open() — тот же ответ
    assert lg(p).fresh is False
    other = tmp_path / "other.db"                                               # файл есть, таблицы нет
    db = sqlite3.connect(other); db.execute("CREATE TABLE x (a)"); db.commit(); db.close()
    assert lg(str(other)).fresh is True


def test_close_call_unknown_status_is_stored_as_unknown(tmp_path):               # M1
    led = lg(str(tmp_path / "core.db"))
    for status in ("weird", "open"):                                            # open у закрытой строки — тоже
        led.close_call(opened(led), status=status, code="", http_status=200, provider="X", provider_ok=True,
                       generation_id="g", usage=U, cost_source="stream", duration_ms=1)
    led.close_call(opened(led), status="cancelled", code="", http_status=200, provider="X", provider_ok=True,
                   generation_id="g", usage=U, cost_source="stream", duration_ms=1)
    assert [r.status for r in led.recent()] == ["cancelled", "unknown", "unknown"]
    assert led.spent() == pytest.approx(3 * 0.0123)


def test_ts_is_stored_in_utc_so_sql_order_holds(tmp_path):                       # M2
    p = tmp_path / "core.db"
    led = lg(str(p))
    msk = timezone(timedelta(hours=3))
    a = led.open_call(ts=datetime(2026, 10, 3, 10, tzinfo=msk), caller="ops", session=None, model="m", step=1,
                      estimate=0.1)                                             # 07:00 UTC
    b = led.open_call(ts=datetime(2026, 10, 3, 8, tzinfo=timezone.utc), caller="ops", session=None, model="m",
                      step=1, estimate=0.1)
    led.flush()
    db = sqlite3.connect(p)
    assert [r[0] for r in db.execute("SELECT id FROM llm_calls ORDER BY ts")] == [a, b]
    rows = led.recent()
    assert {r.ts.utcoffset() for r in rows} == {timedelta(0)}
    assert rows[1].ts == datetime(2026, 10, 3, 7, tzinfo=timezone.utc)


def test_only_open_creates_the_file(tmp_path):                                   # M5
    p = tmp_path / "core.db"
    led = Ledger(str(p))
    assert led.writable() is False and not p.exists()
    with pytest.raises(sqlite3.OperationalError):
        led.recent()
    assert not p.exists() and led.writer_alive is False and led.queue_size == 0
    assert led.open() is True and p.exists()
    for f in tmp_path.glob("core.db*"):                                         # файл пропал на ходу
        f.unlink()
    opened(led)
    wait_until(lambda: led.consecutive_failures >= 1)
    assert not p.exists()                                                       # писатель пустую базу не заводит
    led.close(timeout=0.1)


def test_open_joins_previous_writer(tmp_path, monkeypatch):                      # M6
    led = lg(str(tmp_path / "core.db"))
    gate, real = threading.Event(), led._connect
    monkeypatch.setattr(led, "_connect", lambda *a: (gate.wait(5), real(*a))[1])
    opened(led, est=0.5)                                                        # писатель застрял в записи
    led.close(timeout=0.1)
    assert led.opened is False and led.writer_alive                             # прежний писатель ещё жив
    monkeypatch.undo()
    threading.Timer(0.3, gate.set).start()
    assert led.open() is True                                                   # сначала дождался прежнего
    assert led.spent() == pytest.approx(0.5) and led.recent(1)[0].status == "lost"
    assert sum(t.name == "pcbk-ledger-writer" and t.is_alive() for t in threading.enumerate()) >= 1


# --- доводка по ревью, раунд 2 ---

INT_MAX = 2**63 - 1


def test_poisoned_values_land_sanitized_and_queue_moves(tmp_path):              # N1: ни одна строка не встаёт
    led = lg(str(tmp_path / "core.db"))
    assert Usage.from_openrouter({"prompt_tokens": 10**20}).prompt_tokens == INT_MAX
    cid = led.open_call(ts=T, caller="student-01", session="ses_\udfff", model="m\ud800", step=10**20,
                        estimate=0.5)
    led.close_call(cid, status="ok", code="", http_status=200, provider="Fire\ud800works", provider_ok=True,
                   generation_id="gen-\ud800", usage=replace(U, prompt_tokens=10**20, reasoning_tokens=-5),
                   cost_source="stream", duration_ms=10**30)
    nxt = opened(led, caller="student-02")
    t = time.monotonic()
    led.flush(timeout=2.0)
    assert time.monotonic() - t < 1.0                                           # очередь не встала
    assert (led.write_failures_total, led.writes_dropped_total, led.queue_size) == (0, 0, 0)
    rows = led.recent()
    assert [r.id for r in rows] == [nxt, cid]
    row = rows[1]
    assert (row.session, row.model, row.provider, row.generation_id) == ("ses_?", "m?", "Fire?works", "gen-?")
    assert (row.step, row.prompt_tokens, row.reasoning_tokens, row.duration_ms) == (INT_MAX, INT_MAX, 0, INT_MAX)
    assert led.spent("student-01") == pytest.approx(0.0123) and row.cost == pytest.approx(0.0123)


def test_non_operational_error_drops_the_op_and_moves_on(tmp_path, monkeypatch):  # N1: повтор — только OperationalError
    led = lg(str(tmp_path / "core.db"))
    led.retry_pause_s = 0.01
    real, fired, records = led._connect, [], []

    class Poison:
        def __init__(self, db):
            self.db = db
        def execute(self, sql, params=()):
            if sql.startswith("INSERT") and not fired:
                fired.append(1)
                raise OverflowError("Python int too large to convert to SQLite INTEGER ses_SECRET")
            return self.db.execute(sql, params)
        def close(self):
            self.db.close()

    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("pcbk_core.llm.ledger")
    logger.addHandler(handler)
    try:
        monkeypatch.setattr(led, "_connect", lambda *a: Poison(real(*a)))
        a = opened(led)
        b = opened(led, caller="student-02")
        t = time.monotonic()
        led.flush(timeout=2.0)
        assert time.monotonic() - t < 1.0                                       # брошена сразу, без повторов
    finally:
        logger.removeHandler(handler)
    assert (led.writes_dropped_total, led.write_failures_total, led.consecutive_failures) == (1, 1, 0)
    assert [r.id for r in led.recent()] == [b] and a < b and led.spent() == pytest.approx(1.0)
    assert led.last_error == "OverflowError"                                    # только класс
    errors = [r for r in records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "OverflowError" in errors[0].getMessage()
    assert not any("SECRET" in r.getMessage() for r in records)


def test_flush_returns_once_a_transient_lock_is_gone(tmp_path):                  # к задаче 6: flush перед отправкой
    p = tmp_path / "core.db"
    led = lg(str(p))
    holder = sqlite3.connect(p, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.7, lambda: (holder.execute("ROLLBACK"), holder.close()))
    release.start()
    t = time.monotonic()
    opened(led)
    led.flush(timeout=3.0)                                                      # не ждёт весь срок
    assert time.monotonic() - t < 0.7 + WRITE_TIMEOUT_S + WRITE_RETRY_S + 0.3
    release.join()
    assert led.write_failures_total >= 1 and led.consecutive_failures == 0
    assert led.recent(1)[0].status == "open"
