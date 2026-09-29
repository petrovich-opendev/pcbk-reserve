"""Ворота — единственная дверь к историану.

Полосы: у людей 2 места, у фоновых опросов (каталог, свежесть) 1 — всего не
больше 3 запросов одновременно. Место ждут в цикле asyncio, а не в потоке:
очередь людей не выбирает пул ворот, и фон не ждёт ни мест, ни потоков людей.
Место берётся, только если после этого остаётся не меньше min(5 с, срок / 3), и
держится до возврата драйвера, даже если вызов уже закончился по сроку. Общий
предел 300 запросов за 5 минут считается только для людей и только после
взятого места. В SQL пускаются лишь литералы-имена белого списка внутри
TagName IN (…) и то, что пропускает literal_ok. Отказ входа по учётным данным
закрывает ворота до перезапуска службы: учётка общая с Dify, её блокировка
положит курс. После обрыва связи 30 с новые попытки сразу получают отказ.
"""
import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from .historian import HistorianError, QueryFn, Row
from .sql import clock_sql, plain, scan_literals

GLOBAL_LIMIT = 300
RATE_WINDOW_S = 300.0
USER_SLOTS = 2
BACKGROUND_SLOTS = 1
CALL_DEADLINE_S = 15.0
Q_MIN_S = 5.0
CONNECT_COOLDOWN_S = 30.0

Lane = Literal["user", "background"]
LANES: tuple[Lane, ...] = ("user", "background")
WINDOW_KEY = "historian"

log = logging.getLogger("pcbk_core.data.gate")


class SlidingWindow:
    """Скользящее окно: не больше limit разрешённых вызовов за window_s по ключу."""

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float) -> bool:
        """Разрешённый вызов записывается; отказ — нет."""
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


class GateRefused(Exception):
    """Ворота не пустили вызов, SQL не ушёл: rate — общий предел, busy — нет места к сроку,
    unlisted — SQL вне белого списка."""

    def __init__(self, code: Literal["rate", "busy", "unlisted"]):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GateResult:
    rows: list[list[Row]]
    sent: tuple[str, ...]       # имена из всех TagName IN (…) запроса, без повторов, по порядку


class HistorianGate:
    def __init__(self, query: QueryFn, *, allowed: frozenset[str],
                 literal_ok: Callable[[str], bool] = lambda text: False,
                 user_slots: int = USER_SLOTS, background_slots: int = BACKGROUND_SLOTS,
                 deadline_s: float = CALL_DEADLINE_S, q_min_s: float = Q_MIN_S,
                 global_window: SlidingWindow | None = None,
                 monotonic: Callable[[], float] = time.monotonic):
        self._query = query
        self.allowed = allowed
        self._literal_ok = literal_ok
        self.deadline_s = deadline_s
        self.q_min_s = q_min_s
        self.global_window = global_window if global_window is not None else SlidingWindow(GLOBAL_LIMIT,
                                                                                            RATE_WINDOW_S)
        # часы окна и паузы после обрыва; срок вызова идёт по часам цикла событий — по ним же asyncio
        # отмеряет ожидание
        self.monotonic = monotonic
        self._slots: dict[Lane, asyncio.Semaphore] = {"user": asyncio.Semaphore(user_slots),
                                                      "background": asyncio.Semaphore(background_slots)}
        # потоков ровно столько, сколько мест: место держится до возврата драйвера, поток всегда свободен
        self._pool = ThreadPoolExecutor(max_workers=user_slots + background_slots,
                                        thread_name_prefix="historian")
        self._in_flight: dict[Lane, int] = {"user": 0, "background": 0}
        self._sent_names_total = 0
        self._refused_unlisted = 0
        self._auth_latched = False
        self._pause_until: float | None = None

    def stats(self) -> dict:
        """Только числа и флаги, без имён тегов."""
        return {"sent_names_total": self._sent_names_total,
                "refused_unlisted": self._refused_unlisted,
                "in_flight": dict(self._in_flight),
                "auth_latched": self._auth_latched}

    async def run(self, statements: Sequence[str], *, lane: Lane = "user",
                  deadline_s: float | None = None) -> GateResult:
        if isinstance(statements, str):
            raise TypeError("statements — список инструкций, не строка")
        statements = tuple(statements)
        if not statements:
            raise ValueError("нет инструкций")
        if lane not in LANES:
            raise ValueError(f"нет такой полосы: {lane!r}")
        budget = self.deadline_s if deadline_s is None else deadline_s
        if not (budget > 0 and math.isfinite(budget)):
            raise ValueError("срок вызова — положительное число секунд")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget

        # 1. белый список — до всего остального, без SQL
        sent = self._check(statements, lane)
        # 2. защёлка входа и пауза после обрыва — без входа
        self._raise_if_closed()
        # 3. место полосы: ждать в цикле asyncio не дольше срок − q_min
        q_min = min(self.q_min_s, budget / 3)
        slot = self._slots[lane]
        try:
            await asyncio.wait_for(slot.acquire(), timeout=deadline - q_min - loop.time())
        except TimeoutError:
            raise GateRefused("busy") from None
        try:
            # пока ждали места, чужой вызов мог получить отказ входа или обрыв
            self._raise_if_closed()
            if deadline - loop.time() < q_min:
                raise GateRefused("busy")
            # 4. общий предел — только людям и только с местом
            if lane == "user" and not self.global_window.allow(WINDOW_KEY, self.monotonic()):
                raise GateRefused("rate")
            # 5. запрос — в пул ворот; место отпустит колбэк после возврата драйвера
            waiter = self._submit(loop, lane, statements, deadline - loop.time())
        except BaseException:
            slot.release()
            raise
        self._sent_names_total += len(sent)
        # 6. результат ждём не дольше остатка срока; по сроку запрос дорабатывает с местом
        try:
            rows = await asyncio.wait_for(waiter, timeout=deadline - loop.time())
        except TimeoutError:
            raise HistorianError("timeout", "срок вызова истёк, запрос дорабатывает с местом", sent) from None
        except HistorianError as err:       # 7. защёлку и паузу уже поставил _finish
            raise err.with_sent(sent) from None
        return GateResult(rows=rows, sent=sent)

    def _check(self, statements: tuple[str, ...], lane: Lane) -> tuple[str, ...]:
        sent: dict[str, None] = {}
        for sql in statements:
            if not self._statement_ok(sql, lane):
                self._refused_unlisted += 1
                log.warning("ворота: SQL вне белого списка — отказ без SQL (полоса %s)", lane)
                raise GateRefused("unlisted")
            sent.update((text, None) for text, in_names in scan_literals(sql) if in_names)
        return tuple(sent)

    def _statement_ok(self, sql: str, lane: Lane) -> bool:
        if not isinstance(sql, str) or not plain(sql):
            return False
        found = scan_literals(sql)
        for text, in_names in found:
            # в списке TagName IN (…) — только имя белого списка; вне его — только то, что пускает literal_ok
            if not (text in self.allowed if in_names else self._literal_ok(text)):
                return False
        # людям — только часы или SQL с именами: полный снимок Live или каталог забрали бы всё при sent=()
        if lane == "user" and sql != clock_sql() and not any(in_names for _, in_names in found):
            return False
        return True

    def _raise_if_closed(self) -> None:
        if self._auth_latched:
            raise HistorianError("auth", "защёлка: историан отклонил учётные данные — "
                                         "обновите bdrv.env и перезапустите службу")
        if self._pause_until is not None and self.monotonic() < self._pause_until:
            raise HistorianError("connect", "пауза после обрыва")

    def _note_failure(self, err: HistorianError) -> None:
        if err.code == "auth" and not self._auth_latched:
            self._auth_latched = True
            log.error("ворота: историан отклонил учётные данные (%s) — входа не будет до перезапуска службы",
                      err.public())
        elif err.code == "connect":
            self._pause_until = self.monotonic() + CONNECT_COOLDOWN_S
            log.warning("ворота: нет связи с историаном (%s) — пауза %g с", err.public(), CONNECT_COOLDOWN_S)

    def _submit(self, loop: asyncio.AbstractEventLoop, lane: Lane, statements: tuple[str, ...],
                remaining_s: float) -> asyncio.Future:
        waiter = loop.create_future()
        future = self._pool.submit(self._query, statements, remaining_s)
        self._in_flight[lane] += 1

        def done(f: Future) -> None:        # в потоке драйвера (или сразу, если уже готово)
            try:
                loop.call_soon_threadsafe(self._finish, lane, f, waiter)
            except RuntimeError:            # цикл закрыт: отпускать некому
                pass

        future.add_done_callback(done)
        return waiter

    def _finish(self, lane: Lane, future: Future, waiter: asyncio.Future) -> None:
        """В цикле событий после возврата драйвера.

        Защёлка и пауза ставятся до того, как место отпущено: следующий в очереди,
        взяв его, уже видит отказ — и так же, если вызов закончился по сроку раньше.
        """
        exc = None if future.cancelled() else future.exception()
        if isinstance(exc, HistorianError):
            self._note_failure(exc)
        self._in_flight[lane] -= 1
        self._slots[lane].release()
        if waiter.done():                   # вызов уже закончился по сроку или отменён
            return
        if future.cancelled():
            waiter.cancel()
        elif exc is None:
            waiter.set_result(future.result())
        else:
            waiter.set_exception(exc)
