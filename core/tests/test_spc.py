"""Тесты модуля расчёта SPC и карт Шухарта (план Д21, Task 1: `pcbk_core/spc.py`).

Эталоны — числа, посчитанные вручную (не повторный вызов проверяемой функции): для `individuals`/`xbar_r` —
прямая арифметика по формулам плана (среднее, скользящий размах, A2/D3/D4/d2 по таблице ASTM/AIAG); для `rules`
— построенные ряды, где только одно из восьми правил Нельсона может сработать (остальные семь заведомо не
подходят по длине ряда или по модулю z — см. комментарии у каждого теста); для `capability` — Cp/Cpk/Pp/Ppk по
формулам плана на выборке [8, 9, 10, 11, 12] (среднее 10, выборочное СКО sqrt(2.5)). Тегов предприятия здесь
нет — только вымышленные ряды чисел."""
import math

import pytest

from pcbk_core.spc import capability, individuals, rules, sparkline, xbar_r


# ============================================================================================================
# individuals (I-MR)
# ============================================================================================================

def test_individuals_known_series():
    # Ряд: 10, 12, 11, 13, 9, 14, 10, 12, 11, 13 (вручную: CL = среднее = 11.5)
    values = [10, 12, 11, 13, 9, 14, 10, 12, 11, 13]
    # MR_i = |x_i - x_{i-1}|: 2, 1, 2, 4, 5, 4, 2, 1, 2 -> сумма 23, MR_bar = 23/9
    mr_bar = 23 / 9
    sigma_hat = mr_bar / 1.128
    ucl = 11.5 + 2.66 * mr_bar
    lcl = 11.5 - 2.66 * mr_bar
    ucl_mr = 3.267 * mr_bar

    result = individuals(values)

    assert result["chart"] == "I-MR"
    assert result["points"] == values
    assert result["cl"] == pytest.approx(11.5)
    assert result["ucl"] == pytest.approx(ucl)
    assert result["lcl"] == pytest.approx(lcl)
    assert result["sigma_hat"] == pytest.approx(sigma_hat)
    assert result["sigma_point"] == pytest.approx(sigma_hat)  # у I-MR точка карты = индивидуальное значение

    secondary = result["secondary"]
    assert secondary["chart"] == "MR"
    assert secondary["points"] == [2, 1, 2, 4, 5, 4, 2, 1, 2]
    assert secondary["cl"] == pytest.approx(mr_bar)
    assert secondary["ucl"] == pytest.approx(ucl_mr)
    assert secondary["lcl"] == 0.0


def test_individuals_requires_at_least_two_values():
    with pytest.raises(ValueError, match="минимум 2"):
        individuals([5])


# ============================================================================================================
# xbar_r (X̄-R)
# ============================================================================================================

def test_xbar_r_known_series_n4():
    # 10 значений, подгруппы по n=4: [10,12,14,16] (среднее 13, размах 6), [11,13,15,17] (среднее 14, размах
    # 6); хвост [99, 100] (2 значения) отбрасывается -> discarded = 2.
    values = [10, 12, 14, 16, 11, 13, 15, 17, 99, 100]
    xbb = (13 + 14) / 2        # 13.5
    rbar = (6 + 6) / 2          # 6
    a2, d3, d4, d2 = 0.729, 0.0, 2.282, 2.059
    ucl = xbb + a2 * rbar
    lcl = xbb - a2 * rbar
    sigma_hat = rbar / d2

    result = xbar_r(values, 4)

    assert result["chart"] == "X̄-R"
    assert result["n"] == 4
    assert result["discarded"] == 2
    assert result["points"] == [13, 14]
    assert result["cl"] == pytest.approx(xbb)
    assert result["ucl"] == pytest.approx(ucl)
    assert result["lcl"] == pytest.approx(lcl)
    assert result["sigma_hat"] == pytest.approx(sigma_hat)
    assert result["sigma_point"] == pytest.approx(sigma_hat / math.sqrt(4))

    secondary = result["secondary"]
    assert secondary["chart"] == "R"
    assert secondary["points"] == [6, 6]
    assert secondary["cl"] == pytest.approx(rbar)
    assert secondary["ucl"] == pytest.approx(d4 * rbar)
    assert secondary["lcl"] == pytest.approx(d3 * rbar)


def test_xbar_r_rejects_n_out_of_range():
    with pytest.raises(ValueError):
        xbar_r([1, 2, 3], 1)
    with pytest.raises(ValueError):
        xbar_r([1, 2, 3], 11)


def test_xbar_r_requires_at_least_one_full_subgroup():
    with pytest.raises(ValueError, match="недостаточно"):
        xbar_r([1, 2, 3], 4)


# ============================================================================================================
# rules — 8 правил Нельсона, каждое на своём построенном ряду (изолированно: ряд подобран так, что ни одно
# из семи ОСТАЛЬНЫХ правил заведомо не срабатывает — см. пояснение в каждом тесте), плюс ровный ряд без
# нарушений.
# ============================================================================================================

def test_rule1_point_beyond_3_sigma():
    # Единственная точка (индекс 4, значение 4) выходит за 3σ (сигма=1); ряд короче 9/15, не монотонный —
    # остальные правила не подходят.
    points = [0, 1, -1, 0, 4, 0]
    violations = rules(points, cl=0, sigma=1)
    assert violations == [{"rule": 1, "name_ru": "точка за контрольной границей (3σ)", "index": 4, "zone": "вне"}]


def test_rule2_nine_in_a_row_same_side():
    # 9 точек подряд строго положительные (z=1, зона C) — ровно минимальная длина серии правила 2.
    points = [1] * 9
    violations = rules(points, cl=0, sigma=1)
    assert violations == [{"rule": 2, "name_ru": "9 точек подряд по одну сторону центральной линии",
                           "index": 8, "zone": "C"}]


def test_rule3_six_in_a_row_monotonic():
    # 6 строго возрастающих точек; большая сигма (10) держит все точки в зоне C — иначе не сработает.
    points = [0, 1, 2, 3, 4, 5]
    violations = rules(points, cl=0, sigma=10)
    assert violations == [{"rule": 3, "name_ru": "6 точек подряд растут/падают", "index": 5, "zone": "C"}]


def test_rule4_fourteen_in_a_row_alternating():
    # 14 точек, попеременно 0/1 вокруг CL=0.5; малая амплитуда (сигма=10) держит все точки в зоне C и не
    # даёт сработать правилу 2 (стороны всё равно чередуются).
    points = [0, 1] * 7
    violations = rules(points, cl=0.5, sigma=10)
    assert violations == [{"rule": 4, "name_ru": "14 точек подряд попеременно вверх-вниз",
                           "index": 13, "zone": "C"}]


def test_rule5_two_of_three_beyond_2_sigma():
    # Из трёх точек подряд две (индексы 1, 2) строго за 2σ по одну (положительную) сторону.
    points = [0, 2.5, 2.5]
    violations = rules(points, cl=0, sigma=1)
    assert violations == [{"rule": 5, "name_ru": "2 из 3 точек подряд за 2σ по одну сторону",
                           "index": 2, "zone": "A"}]


def test_rule6_four_of_five_beyond_1_sigma():
    # Из пяти точек подряд четыре (индексы 1..4) строго за 1σ по одну (положительную) сторону; 1.5σ не
    # дотягивает до 2σ, так что правило 5 не срабатывает.
    points = [0, 1.5, 1.5, 1.5, 1.5]
    violations = rules(points, cl=0, sigma=1)
    assert violations == [{"rule": 6, "name_ru": "4 из 5 точек подряд за 1σ по одну сторону",
                           "index": 4, "zone": "B"}]


def test_rule7_fifteen_in_a_row_within_1_sigma():
    # 15 точек точно на центральной линии (z=0) — минимальная длина серии правила 7.
    points = [0] * 15
    violations = rules(points, cl=0, sigma=1)
    assert violations == [{"rule": 7, "name_ru": "15 точек подряд в пределах 1σ", "index": 14, "zone": "C"}]


def test_rule8_eight_in_a_row_outside_1_sigma_both_sides():
    # 8 точек, все строго за 1σ (|z|=1.5), чередуются по обе стороны от CL — минимальная длина серии правила 8.
    points = [1.5, -1.5] * 4
    violations = rules(points, cl=0, sigma=1)
    assert violations == [{"rule": 8, "name_ru": "8 точек подряд вне 1σ по обе стороны", "index": 7, "zone": "B"}]


def test_rules_flat_series_no_violations():
    # 10 точек ровно на центральной линии — короче серии правила 7 (нужно 15) и не выходит за 1σ (правило 8
    # не применяется вовсе), так что ни одно из восьми правил не срабатывает.
    points = [5] * 10
    assert rules(points, cl=5, sigma=1) == []


# ============================================================================================================
# capability
# ============================================================================================================

def test_capability_both_boundaries():
    # Выборка [8, 9, 10, 11, 12]: среднее 10, выборочное СКО (n-1) = sqrt(2.5). sigma_within — заданный
    # параметр карты (не из выборки), допуски симметричны вокруг среднего.
    values = [8, 9, 10, 11, 12]
    sigma_overall = math.sqrt(2.5)
    sigma_within = 1.2
    result = capability(values, sigma_within, lsl=5, usl=15)
    assert result["cp"] == pytest.approx((15 - 5) / (6 * sigma_within))
    assert result["cpk"] == pytest.approx(5 / (3 * sigma_within))
    assert result["pp"] == pytest.approx((15 - 5) / (6 * sigma_overall))
    assert result["ppk"] == pytest.approx(5 / (3 * sigma_overall))
    assert result["sigma_overall"] == pytest.approx(sigma_overall)


def test_capability_single_upper_boundary():
    values = [8, 9, 10, 11, 12]
    sigma_overall = math.sqrt(2.5)
    sigma_within = 1.2
    result = capability(values, sigma_within, lsl=None, usl=15)
    assert result["cp"] is None
    assert result["pp"] is None
    assert result["cpk"] == pytest.approx(5 / (3 * sigma_within))
    assert result["ppk"] == pytest.approx(5 / (3 * sigma_overall))


def test_capability_single_lower_boundary():
    values = [8, 9, 10, 11, 12]
    sigma_overall = math.sqrt(2.5)
    sigma_within = 1.2
    result = capability(values, sigma_within, lsl=5, usl=None)
    assert result["cp"] is None
    assert result["pp"] is None
    assert result["cpk"] == pytest.approx(5 / (3 * sigma_within))
    assert result["ppk"] == pytest.approx(5 / (3 * sigma_overall))


def test_capability_without_boundaries_is_none():
    assert capability([8, 9, 10, 11, 12], 1.2, lsl=None, usl=None) is None


def test_capability_requires_at_least_two_values_when_boundaries_given():
    with pytest.raises(ValueError, match="минимум 2"):
        capability([5], 1.2, lsl=1, usl=10)


# ============================================================================================================
# sparkline
# ============================================================================================================

def test_sparkline_levels_and_markers():
    # Шкала строки 1: от min(LCL, min(points)) = -5 до max(UCL, max(points)) = 80, диапазон 85 -> 8 точек
    # равного шага (-5, 10, 20, ..., 80) ровно попадают в 8 уровней символов подряд.
    points = [-5, 10, 20, 30, 40, 50, 60, 80]
    lcl, ucl = 0, 70
    flagged = {2, 5}

    line1, line2 = sparkline(points, lcl, ucl, flagged)

    assert line1 == "▁▂▃▄▅▆▇█"
    expected_markers = ["▼", " ", "•", " ", " ", "•", " ", "▲"]
    assert line2 == "".join(expected_markers)
    assert len(line1) == len(points)
    assert len(line2) == len(points)


def test_sparkline_no_flags_no_breaches():
    points = [1, 2, 3]
    line1, line2 = sparkline(points, lcl=0, ucl=4, flagged=())
    assert len(line1) == 3
    assert line2 == "   "
