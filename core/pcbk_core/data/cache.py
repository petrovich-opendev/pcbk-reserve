"""Кэш ответов и single-flight.

Срок записи: текущие значения — TTL_NOW_S; период — TTL_OPEN_S, пока он ещё
идёт (is_open из periods.py — то же правило, что у заметки «период ещё
идёт», F21), иначе TTL_CLOSED_S. «Сейчас» округляется вниз до NOW_ROUND_S
(round_now), так повторные вызовы получают один ключ. Ошибки и отказы в кэш
не кладутся — это решает вызывающий. Всё — в одном цикле событий, без замков.
"""
import asyncio
import hashlib
import heapq
import json
import math
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from typing import Generic, Literal, TypeVar

from .periods import CLOSED_AFTER_S, ROUND_S, is_open, round_now

__all__ = ["CLOSED_AFTER_S", "MAX_ENTRIES", "NOW_ROUND_S", "TTL_CLOSED_S", "TTL_NOW_S", "TTL_OPEN_S",
           "SingleFlight", "TTLCache", "cache_key", "is_open", "round_now", "ttl_for"]

TTL_NOW_S = 15
TTL_OPEN_S = 30
TTL_CLOSED_S = 3600
NOW_ROUND_S = ROUND_S            # 30 с — одно число с periods.round_now
MAX_ENTRIES = 256

Tool = Literal["tag_now", "tag_period"]
V = TypeVar("V")
T = TypeVar("T")


def ttl_for(tool: Tool, end: datetime | None, now: datetime) -> float:
    """Срок записи в секундах; у tag_period end обязателен."""
    if tool == "tag_now":
        return TTL_NOW_S
    if tool == "tag_period":
        if end is None:
            raise ValueError("у tag_period нужен конец периода")
        return TTL_OPEN_S if is_open(end, now) else TTL_CLOSED_S
    raise ValueError(f"неизвестный инструмент {tool!r}")


def cache_key(tool: str, tags: Iterable[str], start: datetime | None, end: datetime | None) -> str:
    """sha256 от инструмента, отсортированных тегов и границ периода: порядок тегов не важен."""
    parts = [tool, sorted(tags), None if start is None else start.isoformat(),
             None if end is None else end.isoformat()]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class TTLCache(Generic[V]):
    """Словарь со сроком записи; now — монотонные часы вызывающего.

    Переполнение при новой записи: сначала уходят протухшие, а если места всё
    равно нет — ⌈n/4⌉ записей с самым ранним сроком.
    """

    def __init__(self, max_entries: int = MAX_ENTRIES):
        if max_entries < 1:
            raise ValueError("max_entries — не меньше 1")
        self.max_entries = max_entries
        self._items: dict[str, tuple[float, V]] = {}        # ключ → (срок, значение)

    def get(self, key: str, now: float) -> V | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= now:
            del self._items[key]
            return None
        return value

    def put(self, key: str, value: V, ttl: float, now: float) -> None:
        if not ttl > 0:
            return
        if key not in self._items and len(self._items) >= self.max_entries:
            self._evict(now)
        self._items[key] = (now + ttl, value)

    def _evict(self, now: float) -> None:
        for key in [k for k, (expires, _) in self._items.items() if expires <= now]:
            del self._items[key]
        if len(self._items) < self.max_entries:
            return
        soonest = heapq.nsmallest(math.ceil(len(self._items) / 4), self._items,
                                  key=lambda k: self._items[k][0])
        for key in soonest:
            del self._items[key]

    def __len__(self) -> int:
        return len(self._items)


class SingleFlight(Generic[T]):
    """Одинаковые вызовы в полёте делят одну задачу: run → (результат, shared).

    Работа идёт своей задачей: отмена одного ожидающего (обрыв клиента) не
    отменяет её для остальных. Ошибка доходит до всех; ключ снимается, как
    только задача завершилась, — следующий вызов идёт заново.
    """

    def __init__(self) -> None:
        self._flights: dict[str, asyncio.Future[T]] = {}

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        flight = self._flights.get(key)
        shared = flight is not None and not flight.done()
        if not shared:
            flight = asyncio.ensure_future(factory())
            self._flights[key] = flight
            flight.add_done_callback(lambda done, key=key: self._land(key, done))
        return await asyncio.shield(flight), shared

    def _land(self, key: str, flight: asyncio.Future[T]) -> None:
        if self._flights.get(key) is flight:
            del self._flights[key]
        if not flight.cancelled():
            # ошибка помечена прочитанной: без ожидающих asyncio не пишет её текст в журнал
            flight.exception()
