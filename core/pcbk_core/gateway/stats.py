"""Счётчики шлюза за час (Д5-R29): строка «Шлюз и вход» показывает «k за час» по видам.

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

# Д5а; Д5б добавит refused, stream_broken, upstream_failed
KINDS = frozenset({"wake_ok", "wake_failed", "stop_failed", "place_auth_failed", "login_failed", "login_busy",
                   "origin_refused", "cookies_duplicate"})


class GatewayStats:
    """event(kind) — событие сейчас; count_1h(kind) — число событий за последние 3600 с. Потокобезопасно."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic):
        self._monotonic = monotonic
        self._lock = threading.Lock()
        # вид → записи [первое, последнее, число] по возрастанию времени
        self._events: dict[str, deque[list]] = {kind: deque() for kind in KINDS}

    def _queue(self, kind: str) -> deque[list]:
        try:
            return self._events[kind]
        except KeyError:
            raise ValueError(f"неизвестный вид события шлюза: {kind!r}") from None

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
