"""Эталон сверки: статистика по сырым точкам Delta, посчитанная независимо от историана.

Вечерняя сверка сравнивает с ней сводку историана (AnalogSummaryHistory) и ответ
tag_period: какое среднее и какое σ отдаёт сводка. Службой (pcbk_core) модуль не
импортируется и сам её не импортирует — только stdlib: копия файла едет в
пробный образ pcbk-probe/tds:d3b.

Правила расчёта:
- точки обрезаются по окну [start, end], обе границы входят;
- хорошая точка — good=True и v не None; плохая открывает разрыв до следующей точки;
- ступенька: значение хорошей точки держится до следующей точки, у последней — до end;
- линейная: трапеция между соседними хорошими точками; отрезок перед плохой
  точкой и хвост после последней точки — ступенька;
- разрывы не входят ни в средние по времени, ни в covered_s; время от start до
  первой точки окна тоже не покрыто: значения там нет;
- n, min, max, last, mean_arith и std_arith — только по хорошим точкам (min и max
  по всем точкам со значением проба считает сама);
- std_arith — выборочное (n − 1), при n = 1 — nan; std_step — по времени вокруг mean_step;
- хороших точек нет — ValueError; хорошие есть, но покрытого времени нет
  (единственная хорошая точка стоит в end) — средние по времени и std_step — nan.
"""
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

# знаменатель gap не меньше этого: у ровного сигнала без него деление на ноль
GAP_FLOOR = 1e-9


@dataclass(frozen=True)
class Point:
    t: datetime
    v: float | None
    good: bool


@dataclass(frozen=True)
class Stats:
    n: int
    min: float
    max: float
    last: float
    mean_arith: float
    mean_step: float
    mean_linear: float
    std_arith: float
    std_step: float
    covered_s: float


def _good(p: Point) -> bool:
    return p.good and p.v is not None


def delta_stats(points: Sequence[Point], start: datetime, end: datetime) -> Stats:
    """Статистика точек окна [start, end]; хороших точек нет — ValueError."""
    if end < start:
        raise ValueError("конец окна раньше начала")
    # сортировка устойчива: точки с одной меткой идут в порядке историана, держится последняя
    window = sorted((p for p in points if start <= p.t <= end), key=lambda p: p.t)
    values = [float(p.v) for p in window if _good(p)]
    if not values:
        raise ValueError("в окне нет хороших точек")

    steps: list[tuple[float, float]] = []       # (значение, секунд) — ступенька
    linear: list[float] = []                    # площадь отрезка под линейной интерполяцией
    for i, p in enumerate(window):
        if not _good(p):
            continue                            # разрыв до следующей точки
        nxt = window[i + 1] if i + 1 < len(window) else None
        dt = ((nxt.t if nxt is not None else end) - p.t).total_seconds()
        if dt <= 0:
            continue                            # следующая точка с той же меткой
        v = float(p.v)
        steps.append((v, dt))
        if nxt is not None and _good(nxt):
            linear.append((v + float(nxt.v)) / 2 * dt)
        else:
            linear.append(v * dt)

    covered = math.fsum(dt for _, dt in steps)
    if covered > 0:
        mean_step = math.fsum(v * dt for v, dt in steps) / covered
        mean_linear = math.fsum(linear) / covered
        std_step = math.sqrt(math.fsum((v - mean_step) ** 2 * dt for v, dt in steps) / covered)
    else:
        mean_step = mean_linear = std_step = math.nan

    n = len(values)
    mean_arith = math.fsum(values) / n
    std_arith = math.sqrt(math.fsum((v - mean_arith) ** 2 for v in values) / (n - 1)) if n > 1 else math.nan
    return Stats(n=n, min=min(values), max=max(values), last=values[-1],
                 mean_arith=mean_arith, mean_step=mean_step, mean_linear=mean_linear,
                 std_arith=std_arith, std_step=std_step, covered_s=covered)


def gap(a: float, b: float, scale: float) -> float:
    """Относительное расхождение a с эталоном b: |a − b| / max(scale, |b|, 1e-9)."""
    return abs(a - b) / max(scale, abs(b), GAP_FLOOR)


def discrimination(st: Stats) -> float:
    """Насколько на этих точках различимы среднее по времени и арифметическое (d)."""
    return gap(st.mean_step, st.mean_arith, st.max - st.min)
