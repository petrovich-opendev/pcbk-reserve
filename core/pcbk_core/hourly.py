"""Счётчики событий за час по видам — общее окно шлюза (Д5-R29) и агентов студентов (Д7-R60).

Только stdlib (предпроверка Д7а, П1): модуль импортируют и пакет агентов (только stdlib и SQLite, Д7-R53), и
gateway.stats. Наследуй пакет агентов счётчики от gateway.stats, импорт исполнил бы gateway/__init__ (fastapi, httpx,
места), и цепочка data → agents.stats → gateway → agents.stats упёрлась бы в недогруженный модуль.

Окно — 3600 с по монотонным часам. Память ограничена при любом потоке событий: события одного вида в
пределах секунды лежат одной записью «первое, последнее, число», записей на вид — не больше ~3600. Запись
уходит из окна, когда из него вышло её последнее событие: счёт завышается не больше чем на секунду событий.
Неизвестный вид — ValueError: опечатка в имени не проходит молча нулём.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

WINDOW_S = 3600.0
_BUCKET_S = 1.0


class HourlyCounts:
    """event(kind) — событие сейчас; count_1h(kind) — число событий за последние WINDOW_S. Потокобезопасно; замок
    _lock наследники берут и для своих полей. what — чьи события, для текста ошибки («шлюза», «агентов»)."""

    def __init__(self, kinds: frozenset[str], monotonic: Callable[[], float] = time.monotonic, *, what: str):
        self._monotonic = monotonic
        self._what = what
        self._lock = threading.Lock()
        # вид → записи [первое, последнее, число] по возрастанию времени
        self._events: dict[str, deque[list]] = {kind: deque() for kind in kinds}

    def _queue(self, kind: str) -> deque[list]:
        try:
            return self._events[kind]
        except KeyError:
            raise ValueError(f"неизвестный вид события {self._what}: {kind!r}") from None

    @staticmethod
    def _trim(queue: deque[list], now: float) -> None:
        while queue and now - queue[0][1] >= WINDOW_S:
            queue.popleft()

    def event(self, kind: str) -> None:
        queue = self._queue(kind)
        with self._lock:
            now = self._monotonic()
            self._trim(queue, now)
            if queue and now - queue[-1][0] < _BUCKET_S:
                queue[-1][1] = now
                queue[-1][2] += 1
            else:
                queue.append([now, now, 1])

    def count_1h(self, kind: str) -> int:
        queue = self._queue(kind)
        with self._lock:
            self._trim(queue, self._monotonic())
            return sum(entry[2] for entry in queue)
