from datetime import timedelta

from helpers import T0
from pcbk_watchdog.checks import Check
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
