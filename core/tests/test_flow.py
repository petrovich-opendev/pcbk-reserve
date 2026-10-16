"""Суммарный расход за период по мгновенным показаниям (Д26, pcbk_core.flow): трапеции по фактическим интервалам,
единицы, недостоверные точки и разрывы не интегрируются, края не достраиваются, счётчик не интегрируется."""
from datetime import datetime, timedelta, timezone

import pytest
from pcbk_core.flow import Sample, integrate, parse_unit

TZ = timezone(timedelta(hours=5))
T0 = datetime(2026, 10, 15, 8, 0, tzinfo=TZ)
HOUR = 3600


def at(minutes: float, value: float | None, good: bool = True) -> Sample:
    return Sample(T0 + timedelta(minutes=minutes), value, good)


def test_constant_10_t_per_h_for_3_hours_is_30_t():
    u = parse_unit("т/ч")
    assert (u.kind, u.quantity, u.per_s) == ("rate", "т", HOUR)
    samples = [at(m, 10.0) for m in range(0, 181, 15)]                       # 13 точек, шаг 15 мин
    r = integrate(samples, T0, T0 + timedelta(hours=3), u.per_s, max_gap_s=15 * 60 * 1.5)
    assert r.total == pytest.approx(30.0) and r.complete and r.uncovered == () and r.covered_s == 3 * HOUR


def test_uneven_intervals_use_actual_dt_not_sum_of_readings():
    # 0 мин: 10, 30 мин: 20, 120 мин: 20, 180 мин: 10 т/ч — трапеции: 0,5·15 + 1,5·20 + 1·15 = 52,5 т
    samples = [at(0, 10.0), at(30, 20.0), at(120, 20.0), at(180, 10.0)]
    r = integrate(samples, T0, T0 + timedelta(hours=3), HOUR, max_gap_s=2 * HOUR)
    assert r.total == pytest.approx(52.5) and r.complete
    assert sum(s.value for s in samples) == 60.0 and r.total != 60.0           # не сумма показаний


def test_bad_quality_and_empty_points_are_not_bridged_and_not_zero():
    # 0..60 мин хорошие по 10 т/ч; 75 мин — недостоверная; 90 мин — пусто; 105..180 мин хорошие по 10 т/ч
    samples = [at(m, 10.0) for m in (0, 15, 30, 45, 60)] + [at(75, 999.0, good=False), at(90, None)] + \
              [at(m, 10.0) for m in (105, 120, 135, 150, 165, 180)]
    r = integrate(samples, T0, T0 + timedelta(hours=3), HOUR, max_gap_s=15 * 60 * 1.5)
    assert r.total == pytest.approx(10.0 * (1.0 + 1.25))                      # 60 мин + 75 мин покрыты, 999 не вошло
    assert not r.complete and r.uncovered == ((T0 + timedelta(minutes=60), T0 + timedelta(minutes=105)),)
    assert r.covered_s == pytest.approx((60 + 75) * 60) and (r.points, r.usable) == (13, 11)


def test_gap_longer_than_allowed_interval_is_not_integrated():
    samples = [at(0, 10.0), at(15, 10.0), at(120, 10.0), at(135, 10.0)]     # разрыв 105 мин при шаге 15
    r = integrate(samples, T0, T0 + timedelta(minutes=135), HOUR, max_gap_s=15 * 60 * 1.5)
    assert r.total == pytest.approx(5.0) and r.gaps == 1 and not r.complete


def test_edges_are_not_extrapolated():
    samples = [at(30, 10.0), at(45, 10.0), at(60, 10.0)]                     # период 0..120 мин
    r = integrate(samples, T0, T0 + timedelta(hours=2), HOUR, max_gap_s=15 * 60 * 1.5)
    assert r.total == pytest.approx(5.0) and not r.complete
    assert r.uncovered == ((T0, T0 + timedelta(minutes=30)), (T0 + timedelta(minutes=60), T0 + timedelta(hours=2)))


def test_no_usable_data_gives_unknown_not_zero():
    r = integrate([at(0, None), at(15, 5.0, good=False)], T0, T0 + timedelta(minutes=15), HOUR, max_gap_s=900)
    assert r.total is None and not r.complete and r.covered_s == 0


def test_kg_per_s_integrates_in_seconds_result_kg():
    u = parse_unit("кг/с")
    assert (u.kind, u.quantity, u.per_s) == ("rate", "кг", 1)
    samples = [at(0, 2.0), at(10, 2.0)]                                      # 2 кг/с × 600 с = 1200 кг
    r = integrate(samples, T0, T0 + timedelta(minutes=10), u.per_s, max_gap_s=900)
    assert r.total == pytest.approx(1200.0)


@pytest.mark.parametrize("unit,kind,quantity,per_s", [
    ("т/ч", "rate", "т", 3600), ("кг/с", "rate", "кг", 1), ("л/час", "rate", "л", 3600), ("л/мин", "rate", "л", 60),
    ("м3/ч", "rate", "м3", 3600), ("м³/ч", "rate", "м³", 3600), ("т / ч", "rate", "т", 3600),
    ("т", "counter", None, None), ("м3", "counter", None, None),
    ("м/мин", "other", None, None), ("г/м2", "other", None, None), ("мбар", "other", None, None),
    ("%", "other", None, None), ("°C", "other", None, None),
    (None, "unknown", None, None), ("None", "unknown", None, None), ("  ", "unknown", None, None),
])
def test_unit_kinds(unit, kind, quantity, per_s):
    u = parse_unit(unit)
    assert (u.kind, u.quantity, u.per_s) == (kind, quantity, per_s)


def test_points_outside_period_are_ignored():
    samples = [at(-15, 100.0), at(0, 10.0), at(60, 10.0), at(75, 100.0)]
    r = integrate(samples, T0, T0 + timedelta(hours=1), HOUR, max_gap_s=2 * HOUR)
    assert r.total == pytest.approx(10.0) and r.complete
