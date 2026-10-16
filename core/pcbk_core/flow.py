"""Суммарный расход за период по мгновенным показаниям расходомера (Д26) — чистые функции без ввода-вывода: считает
служба (DataService.calc_flow_total), не модель.

Правила (задание владельца 16.10.2026):
- интеграл — методом трапеций по фактическим интервалам между соседними точками; простая сумма показаний — нет;
- единица мгновенного расхода — «количество/время»: время переводится в единицу знаменателя (т/ч — часы, итог в т;
  кг/с — секунды, итог в кг), итог — в единице числителя;
- единица количества без «/время» (т, кг, м3, л…) — накопительный счётчик: его показания уже накоплены, повторно не
  интегрируются; прочие единицы (°C, %, мбар, м/мин, г/м2…) — не расход;
- годная точка — значение есть и качество хорошее; отрезок между соседними годными точками интегрируется, только
  если он не длиннее допустимого интервала источника (max_gap_s: шаг сетки историана с допуском); иначе — разрыв:
  не интегрируется, пропуск не считается нулевым расходом;
- края периода до первой и после последней годной точки не покрыты — крайнее значение на них не распространяется;
- покрытие — сумма длин проинтегрированных отрезков; неполное покрытие — частичный итог за покрытые интервалы,
  полный расход за период неизвестен.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

# знаменатель единицы → секунд в нём
TIME_UNITS: dict[str, int] = {"ч": 3600, "час": 3600, "h": 3600, "hr": 3600,
                              "мин": 60, "min": 60,
                              "с": 1, "сек": 1, "s": 1, "sec": 1,
                              "сут": 86400, "сутки": 86400, "d": 86400}
# числитель — количество: масса, объём, энергия (на «/время» — расход, без него — накопительный счётчик)
QUANTITY_UNITS = frozenset({"т", "кг", "г", "м3", "нм3", "л", "гкал", "мвт·ч", "квт·ч", "мвтч", "квтч"})
_SPACES = re.compile(r"\s+")
COVERAGE_TOLERANCE_S = 1.0       # покрытие «полное», если не покрыто меньше секунды (округление меток историана)

UnitKind = Literal["rate", "counter", "other", "unknown"]


def _norm(text: str) -> str:
    return _SPACES.sub("", text).lower().replace("³", "3").replace("^3", "3").rstrip(".")


@dataclass(frozen=True)
class FlowUnit:
    """Разбор единицы тега: rate — мгновенный расход (quantity — единица итога, per_s — секунд в знаменателе);
    counter — накопительный счётчик; other — не расход; unknown — единицы нет."""

    kind: UnitKind
    text: str | None
    quantity: str | None = None
    per_s: int | None = None


def parse_unit(unit: str | None) -> FlowUnit:
    """Единица тега → вид. Числитель итога сохраняется как в каталоге («т», «л»), сверка — без регистра и пробелов."""
    if unit is None or not unit.strip() or unit.strip() == "None":
        return FlowUnit("unknown", None)
    text = unit.strip()
    parts = text.split("/")
    if len(parts) == 2:
        top, bottom = parts[0].strip(), _norm(parts[1])
        if bottom in TIME_UNITS and _norm(top) in QUANTITY_UNITS:
            return FlowUnit("rate", text, top, TIME_UNITS[bottom])
        return FlowUnit("other", text)
    if len(parts) == 1 and _norm(text) in QUANTITY_UNITS:
        return FlowUnit("counter", text)
    return FlowUnit("other", text)


@dataclass(frozen=True)
class Sample:
    """Точка ряда: метка с поясом, значение (None — пусто), хорошее ли качество."""

    t: datetime
    value: float | None
    good: bool


@dataclass(frozen=True)
class FlowTotal:
    total: float | None                          # None — ни одного проинтегрированного отрезка: расход неизвестен
    covered: tuple[tuple[datetime, datetime], ...]
    uncovered: tuple[tuple[datetime, datetime], ...]
    covered_s: float
    period_s: float
    complete: bool
    points: int                                  # точек в периоде
    usable: int                                  # годных точек (значение есть, качество хорошее)
    gaps: int                                    # отрезков между годными точками длиннее допустимого интервала
    negative: int                                # годных точек с отрицательным показанием (проинтегрированы как есть)


def _usable(s: Sample) -> bool:
    return s.good and s.value is not None and math.isfinite(s.value)


def integrate(samples: Sequence[Sample], start: datetime, end: datetime, per_s: float,
              max_gap_s: float) -> FlowTotal:
    """Интеграл мгновенного расхода за [start, end] методом трапеций по фактическим интервалам.

    Отрезок (a, b) соседних точек входит в интеграл, только если обе точки годные и 0 < b − a ≤ max_gap_s; вклад —
    (va + vb) / 2 × (b − a) / per_s. Точки вне [start, end] не берутся; края до первой и после последней годной точки
    — непокрытые интервалы (без распространения крайнего значения)."""
    if per_s <= 0 or max_gap_s <= 0 or end <= start:
        raise ValueError("per_s и max_gap_s — положительные, конец периода позже начала")
    pts = sorted((s for s in samples if start <= s.t <= end), key=lambda s: s.t)
    total = 0.0
    covered: list[tuple[datetime, datetime]] = []
    gaps = 0
    for a, b in zip(pts, pts[1:]):
        if not (_usable(a) and _usable(b)):
            continue
        dt = (b.t - a.t).total_seconds()
        if dt <= 0:
            continue
        if dt > max_gap_s:
            gaps += 1
            continue
        total += (a.value + b.value) / 2 * dt / per_s
        if covered and covered[-1][1] == a.t:
            covered[-1] = (covered[-1][0], b.t)
        else:
            covered.append((a.t, b.t))
    covered_s = sum((b - a).total_seconds() for a, b in covered)
    period_s = (end - start).total_seconds()
    uncovered: list[tuple[datetime, datetime]] = []
    cursor = start
    for a, b in covered:
        if a > cursor:
            uncovered.append((cursor, a))
        cursor = b
    if cursor < end:
        uncovered.append((cursor, end))
    uncovered = [(a, b) for a, b in uncovered if (b - a) >= timedelta(seconds=COVERAGE_TOLERANCE_S)]
    usable = [s for s in pts if _usable(s)]
    return FlowTotal(total=total if covered else None, covered=tuple(covered), uncovered=tuple(uncovered),
                     covered_s=covered_s, period_s=period_s, complete=not uncovered, points=len(pts),
                     usable=len(usable), gaps=gaps, negative=sum(1 for s in usable if s.value < 0))
