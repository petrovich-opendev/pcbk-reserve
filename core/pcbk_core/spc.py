"""Расчёт SPC и карт Шухарта (план Д21, Task 1).

Чистые функции без ввода-вывода, только stdlib (`statistics`, `math`). Чтение историана, кэш, ворота и MCP-
инструмент `calc_spc` — не здесь (план Д21, Task 2: `data/service.py`/`data/mcp_server.py`); этот модуль только
считает по уже готовому ряду чисел.

Пять функций:
- `individuals` — карта I-MR (индивидуальные значения + скользящий размах).
- `xbar_r` — карта X̄-R (подгруппы размера `n` = 2..10).
- `rules` — 8 правил Нельсона по готовым точкам карты (индивидуальным значениям ИЛИ средним подгрупп).
- `capability` — Cp/Cpk/Pp/Ppk по границам допуска технолога (не норматив ПЦБК — это решает вызывающий текст).
- `sparkline` — мини-график строкой символов (владелец 15.10: «мини-графики нужны»).

Константы подгрупп (ASTM/AIAG, n = 2..10) — `_CONSTANTS`; для `n = 2` d2 = 1.128 совпадает с константой
`individuals` (у индивидуальных значений «подгруппа» размера 1 по соседним точкам фактически и даёт тот же d2,
что подгруппа размера 2 в таблице — это совпадение таблицы ASTM, не ошибка)."""
from __future__ import annotations

import math
import statistics
from collections.abc import Collection, Iterable, Sequence
from typing import Any

# n -> (A2, D3, D4, d2), ASTM/AIAG, план Д21 Task 1 (дословно)
_CONSTANTS: dict[int, tuple[float, float, float, float]] = {
    2: (1.880, 0.0, 3.267, 1.128),
    3: (1.023, 0.0, 2.574, 1.693),
    4: (0.729, 0.0, 2.282, 2.059),
    5: (0.577, 0.0, 2.114, 2.326),
    6: (0.483, 0.0, 2.004, 2.534),
    7: (0.419, 0.076, 1.924, 2.704),
    8: (0.373, 0.136, 1.864, 2.847),
    9: (0.337, 0.184, 1.816, 2.970),
    10: (0.308, 0.223, 1.777, 3.078),
}

# I-MR: карта индивидуальных значений — буквальные множители плана (не выводятся из _CONSTANTS[2], хотя
# численно совпадают: 2.66 ~= 3/1.128, 3.267 = D4 при n=2).
_IMR_SIGMA_DIVISOR = 1.128
_IMR_LIMIT_MULT = 2.66
_IMR_MR_UCL_MULT = 3.267

_SPARK_CHARS = "▁▂▃▄▅▆▇█"

_RULE_NAMES_RU: dict[int, str] = {
    1: "точка за контрольной границей (3σ)",
    2: "9 точек подряд по одну сторону центральной линии",
    3: "6 точек подряд растут/падают",
    4: "14 точек подряд попеременно вверх-вниз",
    5: "2 из 3 точек подряд за 2σ по одну сторону",
    6: "4 из 5 точек подряд за 1σ по одну сторону",
    7: "15 точек подряд в пределах 1σ",
    8: "8 точек подряд вне 1σ по обе стороны",
}


def individuals(values: Sequence[float]) -> dict[str, Any]:
    """Карта I-MR: CL = среднее; MR_i = |x_i - x_{i-1}|; MR̄ = среднее MR; σ̂ = MR̄/1.128; UCL/LCL = CL ±
    2.66·MR̄. Вторичная карта размахов (ключ `secondary`): CL_MR = MR̄, UCL_MR = 3.267·MR̄, LCL_MR = 0."""
    values = list(values)
    if len(values) < 2:
        raise ValueError("нужно минимум 2 значения для карты I-MR (скользящий размах считается по соседним "
                         "точкам)")
    cl = statistics.mean(values)
    mr = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    mr_bar = statistics.mean(mr)
    sigma_hat = mr_bar / _IMR_SIGMA_DIVISOR
    limit = _IMR_LIMIT_MULT * mr_bar
    return {
        "chart": "I-MR",
        "points": values,
        "cl": cl,
        "ucl": cl + limit,
        "lcl": cl - limit,
        "sigma_hat": sigma_hat,
        "sigma_point": sigma_hat,
        "secondary": {
            "chart": "MR",
            "points": mr,
            "cl": mr_bar,
            "ucl": _IMR_MR_UCL_MULT * mr_bar,
            "lcl": 0.0,
        },
    }


def xbar_r(values: Sequence[float], n: int) -> dict[str, Any]:
    """Карта X̄-R: подгруппы подряд по `n` (2..10, хвост короче `n` отбрасывается — число отброшенных в ключе
    `discarded`); X̿ — среднее средних подгрупп, R̄ — среднее размахов; UCL/LCL = X̿ ± A2·R̄; вторичная карта
    размахов (ключ `secondary`): UCL = D4·R̄, LCL = D3·R̄; σ̂ = R̄/d2 (точка карты X̄ — σ̂/√n, ключ
    `sigma_point`)."""
    if n not in _CONSTANTS:
        raise ValueError(f"n должно быть целым числом от 2 до 10 (получено {n!r})")
    values = list(values)
    if len(values) < n:
        raise ValueError(f"недостаточно значений для подгруппы размера {n}: нужно минимум {n}, "
                         f"получено {len(values)}")
    full_len = len(values) - len(values) % n
    discarded = len(values) - full_len
    chunks = [values[i:i + n] for i in range(0, full_len, n)]
    means = [statistics.mean(chunk) for chunk in chunks]
    ranges = [max(chunk) - min(chunk) for chunk in chunks]
    xbb = statistics.mean(means)
    rbar = statistics.mean(ranges)
    a2, d3, d4, d2 = _CONSTANTS[n]
    limit = a2 * rbar
    sigma_hat = rbar / d2
    return {
        "chart": "X̄-R",
        "n": n,
        "discarded": discarded,
        "points": means,
        "cl": xbb,
        "ucl": xbb + limit,
        "lcl": xbb - limit,
        "sigma_hat": sigma_hat,
        "sigma_point": sigma_hat / math.sqrt(n),
        "secondary": {
            "chart": "R",
            "points": ranges,
            "cl": rbar,
            "ucl": d4 * rbar,
            "lcl": d3 * rbar,
        },
    }


def _zone(abs_z: float) -> str:
    """Зона точки по модулю её расстояния от CL в сигмах: C — в пределах 1σ, B — 1..2σ, A — 2..3σ, «вне» —
    свыше 3σ (контрольная граница)."""
    if abs_z > 3:
        return "вне"
    if abs_z > 2:
        return "A"
    if abs_z > 1:
        return "B"
    return "C"


def _violation(rule: int, index: int, abs_z: float) -> dict[str, Any]:
    return {"rule": rule, "name_ru": _RULE_NAMES_RU[rule], "index": index, "zone": _zone(abs_z)}


def rules(points: Sequence[float], cl: float, sigma: float) -> list[dict[str, Any]]:
    """8 правил Нельсона по готовым точкам карты (индивидуальным значениям или средним подгрупп); `sigma` — σ
    САМОЙ точки карты (для X̄ — σ̂/√n, план Д21 Task 1). Правило, описывающее окно из нескольких точек подряд
    (2..8), считается сработавшим на ПОСЛЕДНЕЙ точке окна (та точка, после которой образец становится виден) —
    `index` и `zone` в ответе относятся к ней, а не к точке(ам), которая(ые) конкретно нарушила(и) порог внутри
    окна. Список нарушений отсортирован по индексу точки, затем по номеру правила."""
    if sigma <= 0:
        raise ValueError("sigma должна быть больше нуля")
    n = len(points)
    violations: list[dict[str, Any]] = []
    if n == 0:
        return violations
    z = [(p - cl) / sigma for p in points]
    abs_z = [abs(v) for v in z]

    # 1 — точка за 3σ
    for i in range(n):
        if abs_z[i] > 3:
            violations.append(_violation(1, i, abs_z[i]))

    # 2 — 9 подряд по одну (ненулевую) сторону CL
    for i in range(n - 8):
        window = z[i:i + 9]
        if all(v > 0 for v in window) or all(v < 0 for v in window):
            idx = i + 8
            violations.append(_violation(2, idx, abs_z[idx]))

    # 3 — 6 подряд монотонно растут или падают
    for i in range(n - 5):
        window = points[i:i + 6]
        increasing = all(window[j] < window[j + 1] for j in range(5))
        decreasing = all(window[j] > window[j + 1] for j in range(5))
        if increasing or decreasing:
            idx = i + 5
            violations.append(_violation(3, idx, abs_z[idx]))

    # 4 — 14 подряд попеременно вверх-вниз
    for i in range(n - 13):
        window = points[i:i + 14]
        diffs = [window[j + 1] - window[j] for j in range(13)]
        alternating = all(d != 0 for d in diffs) and all(diffs[j] * diffs[j + 1] < 0 for j in range(12))
        if alternating:
            idx = i + 13
            violations.append(_violation(4, idx, abs_z[idx]))

    # 5 — 2 из 3 подряд за 2σ по одну сторону
    for i in range(n - 2):
        window = z[i:i + 3]
        if sum(1 for v in window if v > 2) >= 2 or sum(1 for v in window if v < -2) >= 2:
            idx = i + 2
            violations.append(_violation(5, idx, abs_z[idx]))

    # 6 — 4 из 5 подряд за 1σ по одну сторону
    for i in range(n - 4):
        window = z[i:i + 5]
        if sum(1 for v in window if v > 1) >= 4 or sum(1 for v in window if v < -1) >= 4:
            idx = i + 4
            violations.append(_violation(6, idx, abs_z[idx]))

    # 7 — 15 подряд в пределах 1σ
    for i in range(n - 14):
        window = abs_z[i:i + 15]
        if all(v <= 1 for v in window):
            idx = i + 14
            violations.append(_violation(7, idx, abs_z[idx]))

    # 8 — 8 подряд вне 1σ по обе стороны (смесь, без зоны C)
    for i in range(n - 7):
        window = z[i:i + 8]
        if all(abs(v) > 1 for v in window) and any(v > 0 for v in window) and any(v < 0 for v in window):
            idx = i + 7
            violations.append(_violation(8, idx, abs_z[idx]))

    violations.sort(key=lambda v: (v["index"], v["rule"]))
    return violations


def capability(values: Sequence[float], sigma_within: float, lsl: float | None = None,
               usl: float | None = None) -> dict[str, Any] | None:
    """Cp/Cpk (по `sigma_within` — внутригрупповой σ̂ карты) и Pp/Ppk (по σ_общ — выборочному СКО, n-1) для
    границ допуска ТЕХНОЛОГА (не норматив ПЦБК — текст об этом формирует вызывающий). Без обеих границ —
    `None`. С одной границей — только Cpk/Ppk ПО НЕЙ (Cp/Pp = `None`, т.к. у одностороннего допуска нет
    ширины поля для Cp/Pp)."""
    if lsl is None and usl is None:
        return None
    if len(values) < 2:
        raise ValueError("нужно минимум 2 значения для расчёта σ_общ (выборочного СКО)")
    if sigma_within <= 0:
        raise ValueError("sigma_within должна быть больше нуля")
    mu = statistics.mean(values)
    sigma_overall = statistics.stdev(values)
    if lsl is not None and usl is not None:
        cp: float | None = (usl - lsl) / (6 * sigma_within)
        cpk = min(usl - mu, mu - lsl) / (3 * sigma_within)
        pp: float | None = (usl - lsl) / (6 * sigma_overall)
        ppk = min(usl - mu, mu - lsl) / (3 * sigma_overall)
    elif usl is not None:
        cp = pp = None
        cpk = (usl - mu) / (3 * sigma_within)
        ppk = (usl - mu) / (3 * sigma_overall)
    else:
        cp = pp = None
        cpk = (mu - lsl) / (3 * sigma_within)          # type: ignore[operator]
        ppk = (mu - lsl) / (3 * sigma_overall)         # type: ignore[operator]
    return {
        "mu": mu,
        "sigma_within": sigma_within,
        "sigma_overall": sigma_overall,
        "cp": cp,
        "cpk": cpk,
        "pp": pp,
        "ppk": ppk,
    }


def sparkline(points: Sequence[float], lcl: float, ucl: float,
              flagged: Collection[int] | Iterable[int]) -> list[str]:
    """Мини-график строкой символов (владелец 15.10: «мини-графики нужны»). Строка 1 — точки символами
    «▁▂▃▄▅▆▇█» по шкале от `min(LCL, min(points))` до `max(UCL, max(points))` (8 равных по ширине уровней,
    верхняя граница диапазона — в самый верхний символ). Строка 2 — под точками: «▲» выше UCL, «▼» ниже LCL,
    «•» — иное нарушение (индекс в `flagged`, но сама точка в пределах контрольных границ), пробел иначе. Длина
    обеих строк = числу точек."""
    if not points:
        return ["", ""]
    flagged_set = set(flagged)
    lo = min(lcl, min(points))
    hi = max(ucl, max(points))
    span = hi - lo
    top_level = len(_SPARK_CHARS) - 1

    line1_chars: list[str] = []
    line2_chars: list[str] = []
    for i, p in enumerate(points):
        if span <= 0:
            level = 0
        else:
            level = int((p - lo) / span * len(_SPARK_CHARS))
            level = max(0, min(top_level, level))
        line1_chars.append(_SPARK_CHARS[level])

        if p > ucl:
            line2_chars.append("▲")
        elif p < lcl:
            line2_chars.append("▼")
        elif i in flagged_set:
            line2_chars.append("•")
        else:
            line2_chars.append(" ")

    return ["".join(line1_chars), "".join(line2_chars)]
