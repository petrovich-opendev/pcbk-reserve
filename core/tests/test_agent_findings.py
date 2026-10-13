"""Находки агентов (agent_findings, Д13): запись, обрезка до FINDINGS_KEEP на место, чтение новых первыми."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from pcbk_core.agents.findings import FINDINGS_KEEP, RECENT_MAX, AgentFindings, Finding, FindingsError

NOW = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = AgentFindings(str(tmp_path / "core.db"))
    assert s.open(), s.last_error
    return s


def test_add_and_recent_round_trip(store):
    rid = store.add(3, "sushka-bdm2", "deviation", "Заголовок", ["20FAKE_001_PV", "20FAKE_002_SP"],
                    "2026-10-07T06:00:00+00:00", "2026-10-07T06:30:00+00:00", 85.0, 90.0, "Текст находки.", NOW)
    rows = store.recent(3)
    assert rows == [Finding(rid, "2026-10-07T09:00:00+00:00", "sushka-bdm2", "deviation", "Заголовок",
                            ("20FAKE_001_PV", "20FAKE_002_SP"), "2026-10-07T06:00:00+00:00",
                            "2026-10-07T06:30:00+00:00", 85.0, 90.0, "Текст находки.")]


def test_recent_is_new_first_and_scoped_to_place(store):
    for i in range(3):
        store.add(3, "a", "note", f"t{i}", [], None, None, None, None, "текст текст текст", NOW + timedelta(seconds=i))
    store.add(5, "b", "note", "другое место", [], None, None, None, None, "текст текст текст", NOW)
    assert [f.title for f in store.recent(3)] == ["t2", "t1", "t0"]
    assert [f.title for f in store.recent(5)] == ["другое место"]


def test_keeps_only_last_findings_keep_per_place(store):
    for i in range(FINDINGS_KEEP + 10):
        store.add(3, "a", "note", f"t{i:05d}", [], None, None, None, None, "текст текст текст",
                 NOW + timedelta(seconds=i))
    with sqlite3.connect(store.db_path) as db:
        count = db.execute("SELECT count(*) FROM agent_findings WHERE place = 3").fetchone()[0]
        oldest_title = db.execute("SELECT title FROM agent_findings WHERE place = 3"
                                  " ORDER BY id LIMIT 1").fetchone()[0]
    assert count == FINDINGS_KEEP and oldest_title == "t00010"              # первые 10 ушли
    newest = store.recent(3, 1)
    assert newest[0].title == f"t{FINDINGS_KEEP + 9:05d}"


def test_keeps_other_places_independent_of_trim(store):
    for i in range(FINDINGS_KEEP + 5):
        store.add(3, "a", "note", f"t{i}", [], None, None, None, None, "текст текст текст", NOW)
    store.add(5, "b", "note", "цел", [], None, None, None, None, "текст текст текст", NOW)
    assert [f.title for f in store.recent(5)] == ["цел"]


def test_recent_limit_bounds(store):
    with pytest.raises(ValueError):
        store.recent(3, 0)
    with pytest.raises(ValueError):
        store.recent(3, RECENT_MAX + 1)
    store.add(3, "a", "note", "t", [], None, None, None, None, "текст текст текст", NOW)
    assert len(store.recent(3, 1)) == 1


def test_place_bounds(store):
    with pytest.raises(ValueError):
        store.add(0, "a", "note", "t", [], None, None, None, None, "текст текст текст", NOW)
    with pytest.raises(ValueError):
        store.recent(12)                                                  # Д15б: 12 — первое недопустимое место


def test_open_failure_without_io(tmp_path):
    s = AgentFindings(str(tmp_path / "нет" / "core.db"))
    assert not (tmp_path / "нет").exists()                                 # конструктор — без ввода-вывода (F4)
    assert s.open() is False and s.last_error


def test_write_failure_is_findings_error(store, monkeypatch):
    def broken(create=False):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(store, "_connect", broken)
    with pytest.raises(FindingsError):
        store.add(3, "a", "note", "t", [], None, None, None, None, "текст текст текст", NOW)
