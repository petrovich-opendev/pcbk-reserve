"""Расчёт по формуле над рядами тегов (Д28) — чистые функции без ввода-вывода: модель или технолог пишет формулу,
считает код (DataService.calc_formula), не модель.

Решение владельца 16.10: модели отказывались считать производные показатели (сумму по тегам, отношение, корреляцию) —
«разрешить создавать дополнительные расчёты»; арифметика — кодом, как у прочих калькуляторов стенда.

Язык формулы:
- A, B, C, D — ряды тегов вызова по порядку (точки равномерной сетки историана; годная точка — значение есть и
  качество хорошее, иначе пусто);
- числа, + − × ÷ и ** (показатель — число до 10 по модулю), скобки, унарный минус; поточечно над рядами, ряд с числом —
  к каждой точке; пустая точка любого ряда даёт пустую точку результата; деление на ноль в точке — пустая точка
  (считается в dropped), в числе — ошибка;
- abs(x) — поточечно;
- свёртки ряда в число: mean, sum, min, max, std (выборочное σ), median, count, first, last;
- парные свёртки: corr(x, y) — коэффициент Пирсона; slope(y, x), intercept(y, x), r2(y, x) — линейная регрессия
  y = intercept + slope·x по точкам, где оба ряда не пусты (не меньше трёх);
- свёртки и числа сочетаются арифметикой: mean(A) / mean(B), max(A) - min(A).

Разбор — ast с белым списком узлов, без eval; имена — только A–D и функции списка; длина формулы до FORMULA_CHARS.
Результат — число (формула свёрнута) или ряд (поточечная формула).
"""
from __future__ import annotations

import ast
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

FORMULA_CHARS = 300
MAX_NODES = 200
MAX_POWER = 10
VARIABLES = ("A", "B", "C", "D")
MIN_PAIRS = 3

Series = list  # list[float | None] — по общим меткам времени


class FormulaError(ValueError):
    """Формула не разобрана или не считается; текст — русская причина для технолога."""


_REDUCE: dict[str, Callable[[list[float]], float]] = {
    "mean": statistics.fmean, "sum": math.fsum, "min": min, "max": max, "median": statistics.median,
    "count": lambda v: float(len(v)), "first": lambda v: v[0], "last": lambda v: v[-1],
}
_PAIR = ("corr", "slope", "intercept", "r2")
FUNCTIONS = frozenset({*_REDUCE, "std", "abs", *_PAIR})
_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.USub, ast.UAdd, ast.Constant, ast.Name, ast.Load, ast.Call,
            *_BINOPS)


def parse(formula: object) -> ast.Expression:
    """Формула → дерево; не та форма — FormulaError с русской причиной."""
    if not isinstance(formula, str) or not formula.strip():
        raise FormulaError("formula: нужна строка с формулой, например mean(A) / mean(B)")
    if len(formula) > FORMULA_CHARS:
        raise FormulaError(f"formula: не длиннее {FORMULA_CHARS} знаков")
    text = formula.strip().replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise FormulaError("formula: не разобрана — проверьте скобки и знаки (+ - * / **)") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise FormulaError("formula: слишком длинная")
    for node in nodes:
        if not isinstance(node, _ALLOWED):
            raise FormulaError(f"formula: недопустимая конструкция «{type(node).__name__}»")
        if isinstance(node, ast.Name) and node.id not in VARIABLES and node.id not in FUNCTIONS:
            raise FormulaError(f"formula: неизвестное имя «{node.id}» — ряды A, B, C, D, функции: "
                               + ", ".join(sorted(FUNCTIONS)))
        if isinstance(node, ast.Constant) and (isinstance(node.value, bool) or not isinstance(node.value, (int, float))
                                               or not math.isfinite(node.value)):
            raise FormulaError("formula: допустимы только числа")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                raise FormulaError("formula: функции вызываются по имени из списка, без именованных аргументов")
            want = 2 if node.func.id in _PAIR else 1
            if len(node.args) != want:
                raise FormulaError(f"formula: {node.func.id}() — аргументов {want}")
    return tree


def variables(tree: ast.Expression) -> list[str]:
    """Ряды, которые использует формула, по порядку A–D."""
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in VARIABLES}
    return [v for v in VARIABLES if v in used]


@dataclass(frozen=True)
class FormulaResult:
    kind: Literal["number", "series"]
    value: float | None                 # число — для kind == "number"
    series: tuple[float | None, ...]    # ряд — для kind == "series"
    dropped: int                        # точек, выпавших из-за деления на ноль


def _values(s: Series) -> list[float]:
    return [v for v in s if v is not None]


def _pairs(x: Series, y: Series) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for a, b in zip(x, y):
        if a is not None and b is not None:
            xs.append(a)
            ys.append(b)
    if len(xs) < MIN_PAIRS:
        raise FormulaError(f"парная функция: нужно не меньше {MIN_PAIRS} точек, где оба ряда не пусты, есть {len(xs)}")
    return xs, ys


def _regression(y: Series, x: Series) -> tuple[float, float, float]:
    xs, ys = _pairs(x, y)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = math.fsum((a - mx) ** 2 for a in xs)
    syy = math.fsum((b - my) ** 2 for b in ys)
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(xs, ys))
    if sxx == 0:
        raise FormulaError("регрессия: ряд x постоянен — наклон не определён")
    slope = sxy / sxx
    r2 = 1.0 if syy == 0 else sxy * sxy / (sxx * syy)
    return slope, my - slope * mx, r2


def evaluate(tree: ast.Expression, data: dict[str, Series]) -> FormulaResult:
    """Значение формулы над рядами data (A–D → значения по общим меткам; None — пустая точка)."""
    n = len(next(iter(data.values()))) if data else 0
    dropped = 0

    def binop(op: ast.operator, a, b):
        nonlocal dropped
        if isinstance(op, ast.Pow) and (isinstance(b, list) or abs(b) > MAX_POWER):
            raise FormulaError(f"formula: показатель степени — число до {MAX_POWER} по модулю")

        def one(x: float | None, y: float | None) -> float | None:
            nonlocal dropped
            if x is None or y is None:
                return None
            try:
                if isinstance(op, ast.Add):
                    r = x + y
                elif isinstance(op, ast.Sub):
                    r = x - y
                elif isinstance(op, ast.Mult):
                    r = x * y
                elif isinstance(op, ast.Div):
                    if y == 0:
                        dropped += 1
                        return None
                    r = x / y
                else:
                    r = x ** y
            except (OverflowError, ZeroDivisionError, ValueError):
                dropped += 1
                return None
            if isinstance(r, complex) or not math.isfinite(r):
                dropped += 1
                return None
            return float(r)

        if isinstance(a, list) or isinstance(b, list):
            aa = a if isinstance(a, list) else [a] * n
            bb = b if isinstance(b, list) else [b] * n
            return [one(x, y) for x, y in zip(aa, bb)]
        if isinstance(op, ast.Div) and b == 0:
            raise FormulaError("formula: деление на ноль")
        r = one(a, b)
        if r is None:
            raise FormulaError("formula: результат не число (переполнение или корень из отрицательного)")
        return r

    def ev(node: ast.AST):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in data:
                raise FormulaError(f"formula: ряда {node.id} нет — тегов в вызове меньше")
            return list(data[node.id])
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            if isinstance(node.op, ast.UAdd):
                return v
            return [None if x is None else -x for x in v] if isinstance(v, list) else -v
        if isinstance(node, ast.BinOp):
            return binop(node.op, ev(node.left), ev(node.right))
        if isinstance(node, ast.Call):
            name = node.func.id
            args = [ev(a) for a in node.args]
            if name == "abs":
                v = args[0]
                return [None if x is None else abs(x) for x in v] if isinstance(v, list) else abs(v)
            if any(not isinstance(a, list) for a in args):
                raise FormulaError(f"formula: {name}() ждёт ряд (A, B, … или формулу над ними), а не число")
            if name in _PAIR:
                if name == "corr":
                    xs, ys = _pairs(args[0], args[1])
                    if statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
                        raise FormulaError("corr: один из рядов постоянен — корреляция не определена")
                    return statistics.correlation(xs, ys)
                slope, intercept, r2 = _regression(args[0], args[1])
                return {"slope": slope, "intercept": intercept, "r2": r2}[name]
            values = _values(args[0])
            if not values:
                raise FormulaError(f"{name}(): в ряду нет ни одной годной точки")
            try:
                if name == "std":
                    if len(values) < 2:
                        raise FormulaError("std(): нужно не меньше двух точек")
                    out = statistics.stdev(values)
                else:
                    out = float(_REDUCE[name](values))
            except (OverflowError, ValueError) as exc:
                raise FormulaError(f"{name}(): результат не число (переполнение)") from exc
            if not math.isfinite(out):
                raise FormulaError(f"{name}(): результат не число (переполнение)")
            return out
        raise FormulaError(f"formula: недопустимая конструкция «{type(node).__name__}»")

    out = ev(tree)
    if isinstance(out, list):
        return FormulaResult("series", None, tuple(out), dropped)
    return FormulaResult("number", float(out), (), dropped)


def summary(series: Sequence[float | None]) -> dict[str, float | int | None]:
    """Сводка ряда-результата: число годных точек, среднее, минимум, максимум."""
    values = [v for v in series if v is not None]
    if not values:
        return {"count": 0, "mean": None, "min": None, "max": None}
    return {"count": len(values), "mean": statistics.fmean(values), "min": min(values), "max": max(values)}
