"""Бюджет вызова: сколько тегов и какой период можно спросить за раз.

Тего-сутки — число тегов × длина окна в сутках; окно короче часа считается
за час. Предел вызовов на вызывающего — PER_CALLER_LIMIT за окно ворот
(SlidingWindow Д3а, 5 минут). Точки ряда (Д9-R84) — теги × точки за вызов,
не больше max_points; проверяются после окна и тего-суток.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Limits:
    max_tags: int = 16
    max_window_days: float = 31.0
    max_points: int = 288           # точек ряда за вызов (Д9)
    max_tag_days: float = 24.0
    single_tag_days: float = 31.0   # у одного тега предел — вся длина окна
    min_window: timedelta = timedelta(hours=1)


LIMITS = Limits()
PER_CALLER_LIMIT = 60


def _counted(start: datetime, end: datetime, limits: Limits) -> timedelta:
    return max(end - start, limits.min_window)


def cost_tag_days(n: int, start: datetime, end: datetime, limits: Limits = LIMITS) -> float:
    """Цена запроса в тего-сутках: n × max(окно, min_window) в сутках."""
    return _counted(start, end, limits) * n / _DAY


def check_budget(n: int, start: datetime, end: datetime, limits: Limits = LIMITS) -> str | None:
    """Текст первой нарушенной проверки (теги, длина окна, тего-сутки) или None — запрос в бюджете.

    Сравнение — в целых микросекундах timedelta: ровно 24 тего-суток проходят.
    """
    if n > limits.max_tags:
        return f"за один вызов — не больше {limits.max_tags} тегов"
    if end - start > timedelta(days=limits.max_window_days):
        return f"период длиннее {limits.max_window_days:g} суток"
    cap = limits.single_tag_days if n == 1 else limits.max_tag_days
    if _counted(start, end, limits) * n > timedelta(days=cap):
        cost = f"{cost_tag_days(n, start, end, limits):.2f}".replace(".", ",")
        return (f"запрос стоит {cost} тего-суток при пределе {limits.max_tag_days:g} "
                f"(для одного тега — {limits.single_tag_days:g}): сократите период или число тегов")
    return None


def check_points(n: int, points: int, limits: Limits = LIMITS) -> str | None:
    """Текст отказа, если точек ряда за вызов (n тегов × points) больше limits.max_points, иначе None."""
    if n * points > limits.max_points:
        return (f"за один вызов — не больше {limits.max_points} точек ряда (теги × точки): "
                "сократите число тегов или точек")
    return None
