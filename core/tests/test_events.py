"""Журнал событий вызовов: SQLite без ввода-вывода в конструкторе, requested и sent, таблица исходов."""
import json
import sqlite3
import threading
import time
from dataclasses import replace
from typing import get_args

from helpers import EVENT as E            # пример события — в helpers: его берёт и test_role.py (F16)
from helpers import non_root
from pcbk_core.data.events import (AGENT_CHARS, EVENTS_CHECK_S, OUTCOME_BY_CODE, RETURNED_NAMES, SUMMARY_CHARS,
                                   WRITE_TIMEOUT_S, CallEvent, EventLog, Outcome, outcome_for)
from pcbk_core.data.freshness import ERROR_TEXTS
from pcbk_core.data.historian import ErrorCode


def test_event_roundtrip_newest_first(tmp_path):
    log = EventLog(str(tmp_path / "core.db"))
    assert not (tmp_path / "core.db").exists()                  # F4: конструктор без ввода-вывода
    log.write(E); log.write(replace(E, outcome="refused", sent=()))
    assert [e.outcome for e in log.recent()] == ["refused", "partial"] and log.recent()[1] == E
    assert log.writable() is True


@non_root
def test_readonly_db_is_not_writable(tmp_path):
    d = tmp_path / "ro"; d.mkdir()
    log = EventLog(str(d / "core.db"))
    assert log.open() is True                                   # файл есть — теперь отнять права
    (d / "core.db").chmod(0o400); d.chmod(0o500)
    assert log.writable() is False and log.last_error


@non_root
def test_open_failure_is_recorded_not_raised(tmp_path):         # F4, F23: том от root — причина, не падение
    d = tmp_path / "ro"; d.mkdir(); d.chmod(0o500)
    log = EventLog(str(d / "core.db"))
    assert log.open() is False and log.writable_ok is False and log.last_error


def test_outcome_table_covers_all_codes():                      # F11: только значения Outcome
    codes = [*get_args(ErrorCode), "busy", "rate", "unlisted", *ERROR_TEXTS]
    assert {outcome_for(c) for c in codes} <= set(get_args(Outcome)) and outcome_for("что-то") == "error"
    assert (outcome_for("connect"), outcome_for("no_rows"), outcome_for("busy")) == ("unavailable", "unavailable", "busy")


# --- сверх брифа ---

def test_outcome_table_is_the_brief_table():
    assert OUTCOME_BY_CODE == {"connect": "unavailable", "auth": "unavailable", "timeout": "timeout",
                               "query": "error", "unlisted": "error", "busy": "busy", "rate": "rate",
                               "no_rows": "unavailable", "no_tags": "unavailable", "catalog": "unavailable"}
    assert EVENTS_CHECK_S == 30.0


def test_requested_is_cut_to_16_lines_of_128():
    e = replace(E, requested=[f"{i:02d}" + "x" * 300 for i in range(20)])
    assert len(e.requested) == 16 and {len(r) for r in e.requested} == {128} and e.requested[0].startswith("00")
    assert replace(E, requested="20FAKE_001_PV").requested == ("20FAKE_001_PV",)   # строка — одна строка, не буквы


def test_json_columns_open_idempotent_and_wal(tmp_path):
    path = tmp_path / "core.db"
    log = EventLog(str(path))
    assert log.open() is True and log.open() is True and log.writable_ok is None
    log.write(E)
    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        row = db.execute("SELECT requested, tags, sent, caller, channel, tool, outcome"
                         " FROM agent_events").fetchone()
    finally:
        db.close()
    assert [json.loads(x) for x in row[:3]] == [list(E.requested), list(E.tags), list(E.sent)]
    assert row[3:] == ("student-01", "http", "tag_now", "partial")


def test_recent_and_count_by_channel(tmp_path):
    log = EventLog(str(tmp_path / "core.db"))
    assert (log.recent(), log.count()) == ([], 0)               # схема — при первой операции
    for channel in ("http", "system", "mcp", "system"):
        log.write(replace(E, channel=channel))
    assert (log.count(), log.count("system"), log.count("mcp")) == (4, 2, 1)
    assert [e.channel for e in log.recent(limit=3)] == ["system", "mcp", "system"]
    assert [e.channel for e in log.recent(channel="http")] == ["http"]


def test_write_failure_counts_raises_and_success_resets(tmp_path, monkeypatch):
    log = EventLog(str(tmp_path / "core.db"))
    real = log._insert

    def full(e: CallEvent) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(log, "_insert", full)
    for _ in range(2):
        try:
            log.write(E)
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("ошибка записи должна идти дальше")
    assert (log.consecutive_failures, log.last_error) == (2, "OperationalError: database or disk is full")
    monkeypatch.setattr(log, "_insert", real)
    log.write(E)
    assert log.consecutive_failures == 0 and log.count() == 1


@non_root
def test_check_sets_writable_ok_and_recovers(tmp_path):
    d = tmp_path / "ro"; d.mkdir(); d.chmod(0o500)
    log = EventLog(str(d / "core.db"))
    assert log.writable_ok is None
    log.check()
    assert log.writable_ok is False and "unable to open" in log.last_error
    d.chmod(0o700)                                              # права вернули — схема создаётся проверкой
    log.check()
    assert log.writable_ok is True and log.count() == 0         # проба откатилась


def test_threads_share_log_without_check_same_thread(tmp_path):
    log = EventLog(str(tmp_path / "core.db"))
    log.open()
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(5):
                log.write(E)
            log.recent()
        except BaseException as exc:                             # noqa: BLE001 — собрать для проверки
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and log.count() == 20


# --- доводка по ревью ---

def test_lone_surrogate_is_escaped_not_lost(tmp_path):                          # I1
    log = EventLog(str(tmp_path / "core.db"))
    e = replace(E, requested=("20FAKE_001_PV", "x\ud800y"), agent="агент\udfff", summary="причина \ud800")
    assert (e.requested[1], e.agent, e.summary) == ("x\\ud800y", "агент\\udfff", "причина \\ud800")
    log.write(e)
    assert log.recent() == [e] and log.consecutive_failures == 0


def test_free_text_caps():                                                      # M3
    e = replace(E, agent="a" * 300, summary="s" * 900, requested=["\ud800" * 40])
    assert (len(e.agent), len(e.summary), AGENT_CHARS, SUMMARY_CHARS) == (128, 500, 128, 500)
    assert e.requested == ("\\ud800" * 21 + "\\u",)                          # срез — после экранирования
    assert replace(E, agent=None).agent is None


def test_locked_db_fails_write_fast(tmp_path):                                  # M1: не дольше WRITE_TIMEOUT_S
    log = EventLog(str(tmp_path / "core.db"))
    log.open()
    holder = sqlite3.connect(tmp_path / "core.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        t = time.monotonic()
        try:
            log.write(E)
        except sqlite3.OperationalError:
            pass
        took = time.monotonic() - t
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert WRITE_TIMEOUT_S == 0.5 and took < WRITE_TIMEOUT_S + 1.0
    assert log.consecutive_failures == 1 and "locked" in log.last_error


# Д4б, задача 1: returned — имена, которые вернул catalog_search (по нему Д7 ловит tag_not_seen)

def test_old_table_gets_returned_column(tmp_path):
    db = str(tmp_path / "core.db")
    assert EventLog(db).open()                                                   # конструктор без ввода-вывода (F4)
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE agent_events DROP COLUMN returned")                 # таблица, как у Д3б
    con.execute("INSERT INTO agent_events (ts, caller, channel, tool, requested, tags, sent, outcome, summary,"
                " cost, cache, row_count, duration_ms) VALUES ('2026-09-01T00:00:00+00:00', 'x', 'http',"
                " 'tag_now', '[]', '[]', '[]', 'ok', '', 0, 'none', 0, 0)")          # строка Д3б без returned
    con.commit(); con.close()
    log = EventLog(db)
    log.write(replace(E, returned=("20FAKE_001_PV",)))
    assert log.recent()[0].returned == ("20FAKE_001_PV",)
    assert log.recent()[1].returned == ()                                       # старая строка: NULL → ()


# сверх брифа: до 50 имён, столбец добавляется один раз, строки Д3б не трогаются (журнал Д3б:42)

def test_returned_capped_and_migration_once(tmp_path):
    e = replace(E, returned=[f"20FAKE_{i:03d}_PV" for i in range(RETURNED_NAMES + 10)])
    assert RETURNED_NAMES == 50 and e.returned == tuple(f"20FAKE_{i:03d}_PV" for i in range(50))
    db = str(tmp_path / "core.db")
    old = EventLog(db)
    assert old.open()
    old.write(E)
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE agent_events DROP COLUMN returned")
    con.commit(); con.close()
    for _ in range(2):                                                           # второй раз столбец уже есть
        log = EventLog(db)
        assert log.open() is True, log.last_error
    log.write(e)
    con = sqlite3.connect(db)
    try:
        columns = [row[1] for row in con.execute("PRAGMA table_info(agent_events)")]
        assert columns[-1] == "returned" and columns.count("returned") == 1
    finally:
        con.close()
    assert [x.returned for x in log.recent()] == [e.returned, ()] and log.count() == 2
