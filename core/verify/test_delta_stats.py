"""Эталон сверки: среднее по времени и арифметическое по сырому Delta — на синтетических точках."""
import math
from datetime import datetime, timedelta

import pytest

from delta_stats import Point, delta_stats, discrimination, gap

S, H = datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 7)


def pts(*pairs):
    return [Point(S + timedelta(minutes=m), v, True) for m, v in pairs]


def test_step_vs_arith_discriminates():
    st = delta_stats(pts((0, 0.0), (50, 10.0)), S, H)
    assert st.mean_step == pytest.approx(10 * 10 / 60) and st.mean_arith == pytest.approx(5.0)
    assert st.mean_linear == pytest.approx(5 * 50 / 60 + 10 * 10 / 60)
    assert (st.min, st.max, st.last, st.n) == (0.0, 10.0, 10.0, 2)
    assert discrimination(st) == pytest.approx((5.0 - 10 / 6) / 10)


def test_constant_signal_and_zero_scale_gap():
    st = delta_stats(pts((0, 7.0), (20, 7.0), (40, 7.0)), S, H)
    assert st.mean_step == st.mean_linear == st.mean_arith == 7.0 and st.std_step == 0.0
    assert discrimination(st) == 0.0 and gap(7.0, 7.0 + 1e-15, 0.0) < 1e-12


def test_bad_points_open_gaps():
    ps = pts((0, 1.0)) + [Point(S + timedelta(minutes=30), None, False)] + pts((45, 3.0))
    st = delta_stats(ps, S, H)
    assert st.covered_s == pytest.approx(45 * 60) and st.mean_step == pytest.approx((30 + 45) / 45)
    assert (st.mean_arith, st.n) == (pytest.approx(2.0), 2)


def test_points_outside_window_ignored_and_empty_raises():
    assert delta_stats(pts((-10, 100.0), (0, 1.0), (70, 100.0)), S, H).max == 1.0
    with pytest.raises(ValueError):
        delta_stats([Point(S, None, False)], S, H)


def test_single_point_and_no_covered_time_are_nan():
    st = delta_stats(pts((0, 4.0)), S, H)
    assert (st.n, st.mean_step, st.std_step, st.covered_s) == (1, 4.0, 0.0, 3600.0) and math.isnan(st.std_arith)
    st = delta_stats(pts((60, 4.0)), S, H)          # единственная хорошая точка — в end
    assert st.covered_s == 0.0 and all(math.isnan(x) for x in (st.mean_step, st.mean_linear, st.std_step))
    assert (st.n, st.mean_arith, st.last) == (1, 4.0, 4.0)


def test_end_before_start_raises():
    with pytest.raises(ValueError):
        delta_stats(pts((0, 1.0)), H, S)


def test_linear_holds_value_before_bad_point():
    ps = pts((0, 0.0), (20, 6.0)) + [Point(S + timedelta(minutes=40), None, False)] + pts((50, 2.0))
    st = delta_stats(ps, S, H)
    # 0→20 — трапеция; 20→40 — ступенька: следом плохая точка; 40→50 — разрыв; 50→60 — хвост ступенькой
    assert st.covered_s == pytest.approx(50 * 60)
    assert st.mean_linear == pytest.approx((3 * 20 + 6 * 20 + 2 * 10) / 50)
    assert st.mean_step == pytest.approx((0 * 20 + 6 * 20 + 2 * 10) / 50)


def test_stats_over_good_points_only():
    bad = [Point(S + timedelta(minutes=10), 100.0, False), Point(S + timedelta(minutes=50), -100.0, False)]
    st = delta_stats(pts((0, 5.0), (30, 7.0)) + bad, S, H)
    assert (st.n, st.min, st.max, st.last, st.mean_arith) == (2, 5.0, 7.0, 7.0, 6.0)
    assert st.covered_s == pytest.approx(30 * 60) and st.mean_step == pytest.approx((5 * 10 + 7 * 20) / 30)
