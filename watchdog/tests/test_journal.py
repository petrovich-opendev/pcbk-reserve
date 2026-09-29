import sqlite3
from datetime import timedelta

import pytest

from helpers import T0
from pcbk_watchdog.checks import Check, check_memory
from pcbk_watchdog.journal import Journal


def test_journal_writes_only_transitions(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    ok, bad = Check("edge", "Входной прокси", "ok", ""), Check("edge", "Входной прокси", "fail", "код 1")
    assert len(j.record([ok], T0)) == 1
    assert j.record([ok], T0 + timedelta(seconds=10)) == []
    assert [e.state for e in j.record([bad], T0 + timedelta(seconds=20))] == ["fail"]
    assert [e.state for e in j.record([ok], T0 + timedelta(seconds=30))] == ["ok"]


def test_journal_restart_keeps_last_states(tmp_path):
    p = str(tmp_path / "j.db")
    Journal(p).record([Check("edge", "Входной прокси", "ok", "")], T0)
    j2 = Journal(p)
    assert j2.started(T0 + timedelta(minutes=1)).component == "watchdog"
    assert j2.record([Check("edge", "Входной прокси", "ok", "")], T0 + timedelta(minutes=1)) == []
    assert [e.component for e in j2.recent()] == ["watchdog", "edge"]   # новые сверху


def test_journal_detail_change_is_event_only_in_warn_fail(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    j.record([Check("edge", "Входной прокси", "ok", "a")], T0)
    assert j.record([Check("edge", "Входной прокси", "ok", "b")], T0 + timedelta(seconds=10)) == []
    for n, state in enumerate(("warn", "fail"), start=1):
        t = T0 + timedelta(minutes=n)
        assert len(j.record([Check("edge", "Входной прокси", state, "a")], t)) == 1
        assert len(j.record([Check("edge", "Входной прокси", state, "b")], t + timedelta(seconds=10))) == 1


def test_memory_pressure_writes_one_event(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    meminfo = "MemAvailable: {} kB\n"
    events = (j.record([check_memory(meminfo.format(1_500 * 1024))], T0)
              + j.record([check_memory(meminfo.format(1_400 * 1024))], T0 + timedelta(seconds=10)))
    assert [(e.component, e.state) for e in events] == [("memory", "warn")]


def test_probe_leaves_no_rows_and_fails_on_readonly(tmp_path):
    j = Journal(str(tmp_path / "j.db"))
    j.probe()
    assert j.recent() == []                 # проба откатывается
    (tmp_path / "j.db").chmod(0o400)
    with pytest.raises(sqlite3.OperationalError):
        j.probe()
