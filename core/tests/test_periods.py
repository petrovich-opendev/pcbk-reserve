"""Периоды сводки: готовые периоды и смены, свой период в ISO 8601, «сейчас» вниз до 30 с."""
from datetime import datetime, timedelta

import pytest

from pcbk_core.data.periods import CLOSED_AFTER_S, PRESETS, PeriodError, is_open, resolve_period, round_now

N, OFF = datetime(2026, 10, 1, 12, 0, 45), timedelta(hours=5)


def p(now=N, **kw):
    return resolve_period(now, OFF, **kw)


def test_presets_basic():
    assert (p(preset="last_hour").start, p(preset="last_hour").end) == \
           (datetime(2026, 10, 1, 11, 0, 30), datetime(2026, 10, 1, 12, 0, 30))
    assert (p(preset="prev_shift").start, p(preset="prev_shift").end) == (datetime(2026, 9, 30, 20), datetime(2026, 10, 1, 8))
    assert (p(preset="yesterday").start, p(preset="today").start) == (datetime(2026, 9, 30), datetime(2026, 10, 1))
    assert p(preset="prev_shift").shift_assumed and not p(preset="last_hour").shift_assumed
    assert p(preset="last_hour").open and not p(preset="prev_shift").open          # F21: правило кэша


def test_open_uses_closed_after():
    assert is_open(N - timedelta(seconds=CLOSED_AFTER_S - 1), N) and not is_open(N - timedelta(seconds=CLOSED_AFTER_S), N)


def test_shift_presets_cross_midnight():
    at = lambda h, m, preset: p(now=datetime(2026, 10, 1, h, m), preset=preset)
    assert (at(5, 59, "current_shift").start, at(5, 59, "prev_shift").start, at(5, 59, "prev_shift").end) == \
           (datetime(2026, 9, 30, 20), datetime(2026, 9, 30, 8), datetime(2026, 9, 30, 20))
    assert (at(23, 10, "current_shift").start, at(6, 10, "prev_shift").start) == \
           (datetime(2026, 10, 1, 20), datetime(2026, 9, 30, 8))


def test_period_shorter_than_minute_refused():                                  # F19: end == start
    with pytest.raises(PeriodError, match="короче минуты"):
        p(now=datetime(2026, 10, 1, 8, 0, 20), preset="current_shift")
    with pytest.raises(PeriodError, match="раньше начала"):
        p(start="2026-10-01 10:00", end="2026-10-01 09:59")


def test_custom_period_offset_and_clamp():
    q = p(start="2026-10-01T01:00:00+00:00", end="2026-10-01T03:00:00+00:00")
    assert (q.start, q.end, q.clamped) == (datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 8), False)
    assert p(start="2026-10-01 10:00", end="2026-10-01 13:00").clamped is True


@pytest.mark.parametrize("kw", [{"start": "2026-10-01 10:00", "end": "2026-10-01 09:00"},
                                {"start": "1999-12-31 00:00", "end": "2000-01-02 00:00"},
                                {"start": "вчера", "end": "сегодня"}, {"preset": "last_year"}, {},
                                {"preset": "prev_shift", "start": "2026-10-01 10:00", "end": "2026-10-01 11:00"}])
def test_bad_periods(kw):
    with pytest.raises(PeriodError):
        p(**kw)


# сверх брифа


def test_every_preset_ends_by_now_and_is_not_clamped():
    now_r = datetime(2026, 10, 1, 12, 0, 30)
    spans = {"last_hour": timedelta(hours=1), "last_8h": timedelta(hours=8), "last_24h": timedelta(days=1),
             "last_7d": timedelta(days=7), "last_30d": timedelta(days=30)}
    for preset in PRESETS:
        q = p(preset=preset)
        assert q.preset == preset and not q.clamped and q.end <= now_r
        if preset in spans:
            assert (q.start, q.end) == (now_r - spans[preset], now_r)
    assert (p(preset="today").end, p(preset="current_shift").start) == (now_r, datetime(2026, 10, 1, 8))


def test_round_now_floors_to_30_seconds():
    assert round_now(datetime(2026, 10, 1, 12, 0, 29, 999_999)) == datetime(2026, 10, 1, 12, 0)
    assert round_now(datetime(2026, 10, 1, 12, 0, 59)) == datetime(2026, 10, 1, 12, 0, 30)


def test_custom_period_texts():
    q = p(start="2026-10-01T01:00:00Z", end="2026-10-01 07:30:15.5")                 # Z — это UTC; доли — вон
    assert (q.start, q.end, q.preset, q.shift_assumed) == (datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 7, 30, 15),
                                                            None, False)
    with pytest.raises(PeriodError, match="начало и конец"):
        p(start="2026-10-01 10:00")
    with pytest.raises(PeriodError, match="позже текущего времени"):              # не «конец раньше начала»
        p(start="2026-10-01 13:00", end="2026-10-01 14:00")
    with pytest.raises(PeriodError, match="раньше 2000-01-01"):
        p(start="1999-12-31 00:00", end="2000-01-02 00:00")
    with pytest.raises(PeriodError):                                              # перевод в местное за 1-й год
        p(start="0001-01-01T00:00:00+14:00", end="2026-10-01 10:00")
