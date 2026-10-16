"""Расчёт по формуле над рядами тегов (Д28, pcbk_core.formula): разбор без eval, поточечная арифметика, свёртки,
корреляция и регрессия, пустые точки и деление на ноль."""
import math
import statistics

import pytest
from pcbk_core.formula import FORMULA_CHARS, FormulaError, evaluate, parse, summary, variables

A = [1.0, 2.0, 3.0, 4.0, 5.0]
B = [2.0, 4.0, 6.0, 8.0, 10.0]
C = [5.0, None, 5.0, 5.0, 5.0]


def calc(formula: str, **data):
    return evaluate(parse(formula), {k: list(v) for k, v in data.items()})


def test_sum_of_loads_pointwise_and_mean():                    # «суммарная нагрузка» трёх агрегатов
    r = calc("A + B + C", A=A, B=B, C=C)
    assert r.kind == "series" and r.series == (8.0, None, 14.0, 17.0, 20.0)
    assert calc("mean(A + B + C)", A=A, B=B, C=C).value == pytest.approx(statistics.fmean([8, 14, 17, 20]))


def test_ratio_of_means_is_specific_consumption():             # удельный расход = mean(ресурс) / mean(выпуск)
    assert calc("mean(B) / mean(A)", A=A, B=B).value == pytest.approx(2.0)
    assert calc("sum(B) / sum(A)", A=A, B=B).value == pytest.approx(2.0)


def test_corr_slope_intercept_r2():
    assert calc("corr(A, B)", A=A, B=B).value == pytest.approx(1.0)
    assert calc("slope(B, A)", A=A, B=B).value == pytest.approx(2.0)
    assert calc("intercept(B, A)", A=A, B=B).value == pytest.approx(0.0, abs=1e-12)
    assert calc("r2(B, A)", A=A, B=B).value == pytest.approx(1.0)
    noisy = [2.1, 3.9, 6.2, 7.8, 10.1]
    assert calc("corr(A, B)", A=A, B=noisy).value == pytest.approx(statistics.correlation(A, noisy))


def test_reductions():
    assert [calc(f"{f}(A)", A=A).value for f in ("sum", "min", "max", "median", "count", "first", "last")] == \
           [15.0, 1.0, 5.0, 3.0, 5.0, 1.0, 5.0]
    assert calc("std(A)", A=A).value == pytest.approx(statistics.stdev(A))
    assert calc("max(A) - min(A)", A=A).value == 4.0


def test_empty_points_are_skipped_not_zero():
    r = calc("C * 2", C=C)
    assert r.series == (10.0, None, 10.0, 10.0, 10.0) and summary(r.series)["count"] == 4
    assert calc("mean(C)", C=C).value == 5.0                    # пустая точка не входит, не ноль


def test_division_by_zero_drops_point_or_refuses_number():
    r = calc("A / (A - 3)", A=A)
    assert r.series[2] is None and r.dropped == 1
    with pytest.raises(FormulaError, match="деление на ноль"):
        calc("mean(A) / (mean(A) - 3)", A=A)


@pytest.mark.parametrize("formula,reason", [
    ("__import__('os')", "функции вызываются по имени из списка"),
    ("A.real", "недопустимая конструкция"),
    ("A[0]", "недопустимая конструкция"),
    ("open('x')", "функции вызываются по имени из списка"),
    ("mean(A, B)", "аргументов 1"),
    ("corr(A)", "аргументов 2"),
    ("mean(x=A)", "без именованных аргументов"),
    ("lambda: 1", "недопустимая конструкция"),
    ("A if B else C", "недопустимая конструкция"),
    ("E + 1", "неизвестное имя"),
    ("(A", "не разобрана"),
    ("A ** 100", "показатель степени"),
    ("", "нужна строка"),
    ("A + " + "1 + " * 200 + "1", "не длиннее"),
])
def test_parse_refusals(formula, reason):
    with pytest.raises(FormulaError, match=reason):
        calc(formula, A=A, B=B, C=C)


def test_reduction_needs_series_and_enough_points():
    with pytest.raises(FormulaError, match="ждёт ряд"):
        calc("mean(5)", A=A)
    with pytest.raises(FormulaError, match="не меньше 3"):
        calc("corr(A, B)", A=[1.0, None, 2.0], B=[1.0, 2.0, None])
    with pytest.raises(FormulaError, match="постоянен"):
        calc("corr(A, B)", A=A, B=[1.0] * 5)
    with pytest.raises(FormulaError, match="нет ни одной годной точки"):
        calc("mean(A)", A=[None, None])


def test_variables_and_unicode_operators():
    assert variables(parse("corr(B, D) + mean(A)")) == ["A", "B", "D"]
    assert calc("mean(A) × 2 − 1", A=A).value == 5.0 and calc("A^2", A=A).series[-1] == 25.0
    assert len("x" * FORMULA_CHARS) == FORMULA_CHARS


def test_overflow_is_not_a_number():
    with pytest.raises(FormulaError, match="не число"):
        calc("max(A) * 1e300", A=[1e300])
    assert calc("A * 1e300", A=[1e300, 1.0]).series == (None, 1e300)        # точка с переполнением — пустая
    assert math.isfinite(calc("mean(A)", A=[1e300, 1e300]).value)
