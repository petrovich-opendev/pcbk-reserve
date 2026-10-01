"""Счётчики шлюза за час (Д5-R29): строка «Шлюз и вход» показывает «k за час» по видам.

Окно часа — pcbk_core.hourly (Д7а, П1: общее со счётчиками агентов студентов, только stdlib); WINDOW_S оттуда же —
его берёт и web.py.

Д5б (Б1): слоты потоков событий /event по учёткам — не больше MAX_STREAMS_PER_LOGIN на логин; под тем же
замком, что и счётчики. Учётка без открытых потоков в словаре не лежит.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from ..hourly import WINDOW_S, HourlyCounts  # noqa: F401 — реэкспорт WINDOW_S: его берёт web.py

MAX_STREAMS_PER_LOGIN = 3                   # потоков /event одной учётки разом (Б1, Б4)

# Д5а и виды прокси Д5б (Б1): отказ прокси по правилам, обрыв потока /event, место не ответило шлюзу
KINDS = frozenset({"wake_ok", "wake_failed", "stop_failed", "place_auth_failed", "login_failed", "login_busy",
                   "origin_refused", "cookies_duplicate", "refused", "stream_broken", "upstream_failed"})


class GatewayStats(HourlyCounts):
    """event(kind) — событие сейчас; count_1h(kind) — число событий за последние 3600 с; stream_opened(login) и
    stream_closed(login) — слоты потоков учётки, streams_of(login) и streams — открытые. Потокобезопасно.
    kinds — виды счётчиков (по умолчанию KINDS шлюза, Д7а: параметр — для окна часа других счётчиков)."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic, kinds: frozenset[str] = KINDS):
        super().__init__(kinds, monotonic, what="шлюза")
        # логин → открытых потоков (> 0); ноль — ключа нет
        self._streams: dict[str, int] = {}

    def streams_of(self, login: str) -> int:
        with self._lock:
            return self._streams.get(login, 0)

    def stream_opened(self, login: str) -> bool:
        """Занять слот потока учётки: False — у неё уже MAX_STREAMS_PER_LOGIN, слот не занят."""
        with self._lock:
            n = self._streams.get(login, 0)
            if n >= MAX_STREAMS_PER_LOGIN:
                return False
            self._streams[login] = n + 1
            return True

    def stream_closed(self, login: str) -> None:
        """Освободить слот: ниже нуля не уходит, учётка без потоков из словаря уходит."""
        with self._lock:
            n = self._streams.get(login, 0)
            if n > 1:
                self._streams[login] = n - 1
            else:
                self._streams.pop(login, None)

    @property
    def streams(self) -> int:
        """Всего открытых потоков — число для /health/gateway, без логинов."""
        with self._lock:
            return sum(self._streams.values())
