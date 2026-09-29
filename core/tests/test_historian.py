"""Соединение python-tds: аргументы входа, прелюдия, срок, разбор ошибок; часы историана."""
import socket
from dataclasses import replace
from datetime import datetime, timezone

import pytds
import pytest

from fakes import RecordingConnect, auth_operational_error
from pcbk_core.data.historian import PRELUDE, HistorianError, parse_clock, tds_query
from pcbk_core.secrets import BdrvConfig

CFG = BdrvConfig("h", 1433, "Runtime", "u", "p")


def test_connect_args_prelude_one_argument_and_deadline_timeout():
    rec = RecordingConnect(results=[[(1,)], [(2,)]])
    assert tds_query(CFG, connect=rec)(["SELECT 1", "SELECT 2"], 12.0) == [[(1,)], [(2,)]]
    kw = rec.kwargs
    assert (kw["dsn"], kw["port"], kw["autocommit"], kw["login_timeout"], kw["timeout"]) == ("h", 1433, True, 10, 12.0)
    assert kw["disable_connect_retry"] is True
    assert "server" not in kw and "cafile" not in kw and rec.connect_count == 1
    assert rec.executed == [(PRELUDE,), ("SELECT 1",), ("SELECT 2",)] and rec.closed
    rec2 = RecordingConnect(results=[[(1,)]])
    tds_query(replace(CFG, host="h\\INST", port=None), connect=rec2)(["SELECT 1"], 99.0)
    assert "port" not in rec2.kwargs and rec2.kwargs["timeout"] == 45


@pytest.mark.parametrize("where,exc,code", [
    ("connect", pytds.LoginError("Login failed"), "auth"),
    ("connect", auth_operational_error(18456), "auth"),          # OperationalError с msg_no
    ("connect", auth_operational_error(18452), "auth"),
    ("connect", ConnectionRefusedError(), "connect"),
    ("connect", TimeoutError(), "connect"),                      # фаза входа — это связь, не срок чтения
    ("execute", socket.timeout(), "timeout"),
    ("execute", pytds.ProgrammingError("bad"), "query"),
])
def test_errors_are_classified(where, exc, code):
    rec = RecordingConnect(**{f"fail_on_{where}": exc})
    with pytest.raises(HistorianError) as e:
        tds_query(CFG, connect=rec)(["SELECT 1"], 15.0)
    assert e.value.code == code


def test_parse_clock():
    c = parse_clock([(datetime(2026, 10, 1, 12, 0, 7), datetime(2026, 10, 1, 7, 0, 3))])
    assert c.tz_label == "UTC+05:00"
    assert c.aware(datetime(2026, 10, 1, 11, 59, 18)).isoformat() == "2026-10-01T11:59:18+05:00"


# сверх брифа: текст SQL Server не выходит наружу, срок не бывает нулевым, соединение закрывается


def test_error_text_stays_in_detail_only():
    rec = RecordingConnect(fail_on_connect=auth_operational_error(18456, "Login failed for user 'FAKEUSER'"))
    with pytest.raises(HistorianError) as e:
        tds_query(CFG, connect=rec)(["SELECT 1"], 15.0)
    err = e.value
    assert "FAKEUSER" in err.detail and "OperationalError" in err.detail and "18456" in err.detail
    assert "FAKEUSER" not in str(err) and "FAKEUSER" not in repr(err) and err.__cause__ is None
    assert err.public() == "auth OperationalError msg_no=18456"


def test_read_errors_close_connection_and_other_classes():
    for exc, code in ((ConnectionResetError(), "connect"), (ValueError("x"), "query")):
        rec = RecordingConnect(fail_on_execute=exc)
        with pytest.raises(HistorianError) as e:
            tds_query(CFG, connect=rec)(["SELECT 1"], 15.0)
        assert e.value.code == code and rec.closed


def test_spent_deadline_does_not_connect():
    rec = RecordingConnect()
    for spent in (0.0, -1.0, float("nan")):
        with pytest.raises(HistorianError) as e:
            tds_query(CFG, connect=rec)(["SELECT 1"], spent)       # timeout=0 у pytds — «без предела»
        assert e.value.code == "timeout"
    assert rec.connect_count == 0


def test_clock_offsets_and_bad_rows():
    c = parse_clock([(datetime(2026, 10, 1, 3, 30, 2), datetime(2026, 10, 1, 7, 0, 0))])
    assert c.tz_label == "UTC-03:30"
    assert c.aware(datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)).isoformat() == "2026-10-01T03:30:00-03:30"
    assert parse_clock([(datetime(2026, 10, 1, 7), datetime(2026, 10, 1, 7))]).tz_label == "UTC+00:00"
    for bad in ([], [(1, 2)], [(datetime(2026, 10, 2, 7), datetime(2026, 10, 1, 7))]):
        with pytest.raises(ValueError):
            parse_clock(bad)
