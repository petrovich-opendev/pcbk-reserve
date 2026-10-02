"""Журнал действий преподавателя (Д10-R99): строки teacher_actions только из кодов, чисел и логинов, новые первыми;
пульс teacher_heartbeat — настоящая запись; учение journal_down — сбой записи до SQL, store_ok — ложь до удачной
записи."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from pcbk_core.teacher_log import (ACTIONS, JOURNAL_DRILL, OUTCOMES, TEACHER_SCHEMA, ActionRow, TeacherLog,
                                   TeacherLogError, insert_action)

AT = datetime(2026, 10, 11, 9, tzinfo=timezone.utc)


def test_teacher_log_rows_and_ping(tmp_path):                             # Review Focus 4
    log = TeacherLog(str(tmp_path / "core.db"), clock=lambda: 1_760_000_000.0)
    assert log.open() and log.store_ok
    log.requested("teach", "read", 3, "ses_" + "A" * 26)
    rid = log.started("teach", "account_add", 4, "d10-probe-n")
    log.finish(rid, "ok", {"place": "04"})
    rows = log.recent(10)
    assert [(r.action, r.place, r.outcome) for r in rows] == [("account_add", 4, "ok"), ("read", 3, "requested")]
    assert log.ping() and log.store_ok


def test_teacher_log_drill_fails_and_is_seen(tmp_path):
    log = TeacherLog(str(tmp_path / "core.db"), drill=JOURNAL_DRILL)
    assert log.open()
    with pytest.raises(TeacherLogError):
        log.requested("teach", "read", 3, "ses_" + "A" * 26)
    assert log.store_ok is False and log.ping() is False
    assert log.recent(10) == []


# --- сверх брифа ---

def test_vocabulary():
    assert ACTIONS == {"sessions", "read", "agents", "agent", "wake", "shares"}
    assert OUTCOMES == {"requested", "ok", "refused", "error", "started"}
    assert JOURNAL_DRILL == "journal_down"


def test_constructor_does_no_io(tmp_path):                               # договор Role: файл — только open()
    log = TeacherLog(str(tmp_path / "нет" / "core.db"))
    assert not (tmp_path / "нет").exists() and log.store_ok is False
    assert log.open() is False and log.store_ok is False and log.last_error


def test_rows_on_disk_are_codes_only(tmp_path):
    log = TeacherLog(str(tmp_path / "core.db"), clock=lambda: 1_760_000_000.0)
    assert log.open()
    rid = log.requested("teach", "sessions", 10, None)
    log.refused("teach", "shares", None, {"kind": "below_spent"})
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        rows = db.execute("SELECT id, at, login, action, place, target, detail, outcome FROM teacher_actions"
                          " ORDER BY id").fetchall()
    assert rows == [(rid, "2025-10-09T08:53:20+00:00", "teach", "sessions", 10, None, None, "requested"),
                    (rid + 1, "2025-10-09T08:53:20+00:00", "teach", "shares", None, None, '{"kind": "below_spent"}',
                     "refused")]
    row = log.recent(2)[1]
    assert row == ActionRow(rid, "2025-10-09T08:53:20+00:00", "teach", "sessions", 10, None, None, "requested")


def test_texts_and_bad_values_never_reach_the_table(tmp_path):           # в журнале — только коды и числа
    log = TeacherLog(str(tmp_path / "core.db"))
    assert log.open()
    for bad in (dict(login="Teach", action="read", place=3, target=None),          # логин не того вида
                dict(login="teach", action="прочитал", place=3, target=None),      # действие — код
                dict(login="teach", action="read", place=11, target=None),
                dict(login="teach", action="read", place=True, target=None),
                dict(login="teach", action="read", place=3, target="текст разговора"),
                dict(login="teach", action="read", place=3, target="a\tb"),
                dict(login="teach", action="read", place=3, target="x" * 129)):
        with pytest.raises(ValueError):
            log.requested(**bad)
    for detail in ({"text": "Привет, студент"}, {"n": 1.5}, {"n": True}, {"x": {"y": "a b"}}, ["03"]):
        with pytest.raises(ValueError):
            log.refused("teach", "read", 3, detail)
    rid = log.started("teach", "account_add", 4, "d10-probe-n")
    with pytest.raises(ValueError):
        log.finish(rid, "requested")                                         # итог — ok, refused или error
    with pytest.raises(ValueError):
        log.finish(rid + 100, "ok")                                          # строки нет — ошибка кода, не тишина
    assert [r.id for r in log.recent(50)] == [rid] and log.store_ok


def test_finish_keeps_detail_without_new_one(tmp_path):
    log = TeacherLog(str(tmp_path / "core.db"))
    assert log.open()
    rid = log.started("teach", "account_passwd", 5, "stu-05")
    log.finish(rid, "error")
    assert (log.recent(1)[0].outcome, log.recent(1)[0].detail) == ("error", None)


def test_failure_counts_until_any_good_write(tmp_path, monkeypatch):     # store_ok возвращается сам — пульсом
    log = TeacherLog(str(tmp_path / "core.db"))
    assert log.open()
    real = log._connect
    monkeypatch.setattr(log, "_connect", lambda create=False, readonly=False: (_ for _ in ()).throw(
        sqlite3.OperationalError("database is locked")))
    with pytest.raises(TeacherLogError) as e:
        log.requested("teach", "read", 3, "ses_" + "A" * 26)
    assert str(e.value) == "журнал действий: OperationalError" and log.store_ok is False
    assert log.refused("teach", "shares", None, {"kind": "bad"}) is False        # без гарантии: не бросает
    assert log.ping() is False and (log.failures, log.store_ok) == (3, False)
    monkeypatch.setattr(log, "_connect", real)
    assert log.ping() is True and (log.failures, log.store_ok) == (0, True)


def test_drill_never_resets_by_itself(tmp_path):                          # учение — нет: пульс тоже не пишется
    log = TeacherLog(str(tmp_path / "core.db"), drill=JOURNAL_DRILL)
    assert log.open() and log.store_ok                                       # до первой записи — честно «пишется»
    assert [log.ping() for _ in range(3)] == [False] * 3 and log.store_ok is False
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        assert db.execute("SELECT count(*) FROM teacher_heartbeat").fetchone()[0] == 0


def test_ping_is_a_real_write(tmp_path):
    log = TeacherLog(str(tmp_path / "core.db"), clock=lambda: 123.5)
    assert log.open() and log.ping()
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        assert db.execute("SELECT id, at FROM teacher_heartbeat").fetchall() == [(1, 123.5)]


def test_insert_action_in_foreign_transaction(tmp_path):                  # строку пишет и транзакция долей
    log = TeacherLog(str(tmp_path / "core.db"))
    assert log.open()
    with closing(sqlite3.connect(tmp_path / "core.db", isolation_level=None)) as db:
        db.execute("BEGIN IMMEDIATE")
        rid = insert_action(db, at=AT, login="teach", action="shares", place=None, target=None,
                            detail={"03": [10000, 9000]}, outcome="ok", drill="")
        db.execute("ROLLBACK")                                               # чужая транзакция откатилась — строки нет
        assert isinstance(rid, int) and db.execute("SELECT count(*) FROM teacher_actions").fetchone()[0] == 0
        with pytest.raises(TeacherLogError, match="учения: journal_down"):
            insert_action(db, at=AT, login="teach", action="shares", place=None, target=None, detail=None,
                          outcome="ok", drill=JOURNAL_DRILL)
    with closing(sqlite3.connect(tmp_path / "other.db", isolation_level=None)) as db:   # таблицы нет — сбой журнала
        with pytest.raises(TeacherLogError):
            insert_action(db, at=AT, login="teach", action="shares", place=None, target=None, detail=None,
                          outcome="ok", drill="")


def test_schema_is_idempotent_and_has_no_action_check(tmp_path):         # Д10б не переделывает таблицу
    with closing(sqlite3.connect(tmp_path / "core.db")) as db, db:
        for _ in range(2):
            for statement in TEACHER_SCHEMA:
                db.execute(statement)
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name = 'teacher_actions'").fetchone()[0]
    assert "CHECK" not in sql.replace("CHECK (id = 1)", "")


def test_recent_reads_only(tmp_path):                                     # mode=ro: чтение ничего не создаёт
    with pytest.raises(TeacherLogError):
        TeacherLog(str(tmp_path / "core.db")).recent(5)
    assert not (tmp_path / "core.db").exists()
    log = TeacherLog(str(tmp_path / "core.db"))
    assert log.open()
    for bad in (0, -1, 1001, True, 2.0):
        with pytest.raises(ValueError):
            log.recent(bad)
