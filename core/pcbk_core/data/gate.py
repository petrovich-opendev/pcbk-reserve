"""Ворота — единственная дверь к историану.

Полосы: у людей 2 места, у фоновых опросов (каталог, свежесть) 1 — всего не
больше 3 запросов одновременно. Место ждут в цикле asyncio, а не в потоке:
очередь людей не выбирает пул ворот, и фон не ждёт ни мест, ни потоков людей.
Место берётся, только если после этого остаётся не меньше min(5 с, срок / 3), и
держится до возврата драйвера, даже если вызов уже закончился по сроку. Общий
предел 300 запросов за 5 минут считается только для людей и только после
взятого места.

В SQL пускается только инструкция, целиком совпавшая с формой шаблона
(sql.TEMPLATE_FORMS); литералы в ней — имена белого списка внутри
TagName IN (…) и то, что пропускает literal_ok.

Учётка общая с Dify, её блокировка положит курс. Поэтому до первого удачного
вызова после запуска входит только один вызов за раз, остальные ждут его
исхода; отказ входа по учётным данным закрывает ворота до перезапуска службы.
После обрыва связи 30 с новые попытки сразу получают отказ.

Нагрузка полосы людей (Д4б-R22) — в памяти, для /health/historian: p95 длительности
вызовов за STATS_WINDOW_S, сроки и busy за то же окно. p95 — только у вызовов, дошедших
до драйвера; длительность — от входа в run() до исхода, с ожиданием места: это видит
студент. Отказы без SQL (busy, пауза, unlisted, rate, защёлка) p95 не разбавляют. После
PAUSE_AFTER_TIMEOUTS сроков подряд полоса людей стоит USER_PAUSE_S: новые вызовы сразу
получают busy, без места и SQL, и ждущие места или первого входа — тоже; фон паузы не знает.
Счёт обнуляет только исход, с которым SQL дошёл до историана (удача, ошибка query, connect,
auth); отказы без SQL его не трогают. Счёт ведётся в момент исхода, до того как отпущено
место: срок ворот — пока место держит драйвер, исход драйвера — в _finish.
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
from .sql import clock_sql, plain, scan_literals, template_of

GLOBAL_LIMIT = 300
RATE_WINDOW_S = 300.0
USER_SLOTS = 2
BACKGROUND_SLOTS = 1
CALL_DEADLINE_S = 15.0
Q_MIN_S = 5.0
CONNECT_COOLDOWN_S = 30.0
STATS_WINDOW_S = 300.0          # окно нагрузки полосы людей: p95, сроки, busy
P95_MIN_CALLS = 5               # меньше вызовов в окне — p95 не считается
PAUSE_AFTER_TIMEOUTS = 3        # сроков подряд у людей — и полоса людей на паузе
USER_PAUSE_S = 60.0
# 4060 («база недоступна») приходит вместе с 18456 и выглядит как отказ учётных данных
AUTH_LATCH_TEXT = ("историан отклонил учётные данные — обновите bdrv.env или проверьте, не была ли база "
                   "историана недоступна, и перезапустите службу")

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
    """Ворота не пустили вызов, SQL не ушёл: rate — общий предел, busy — нет места к сроку или полоса людей
    на паузе после сроков подряд, unlisted — SQL вне белого списка."""

    def __init__(self, code: Literal["rate", "busy", "unlisted"]):
        super().__init__(code)
        self.code = code


@dataclass
class _Attempt:
    """Вызов людей дошёл до драйвера — отмечает _call после передачи запроса в пул ворот."""

    reached: bool = False


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
        # до первого удачного вызова вход пробует один вызов за раз; замок держится до возврата драйвера
        self._first_login = asyncio.Lock()
        self._login_ok = False
        self._in_flight: dict[Lane, int] = {"user": 0, "background": 0}
        self._sent_names_total = 0
        self._refused_unlisted = 0
        self._auth_latched = False
        self._pause_until: float | None = None
        # исходы вызовов людей за окно: (monotonic() исхода, длительность в мс, код исхода, дошёл до драйвера)
        self._user_calls: deque[tuple[float, int, str, bool]] = deque()
        self._timeouts_in_row = 0
        self._user_pause_until: float | None = None

    def clear_pause(self) -> None:
        """Только для тестов: снимает защёлку входа, паузу после обрыва и паузу полосы людей. У людей их снимает
        перезапуск службы (паузы — и время)."""
        self._auth_latched = False
        self._pause_until = None
        self._timeouts_in_row = 0
        self._user_pause_until = None

    def stats(self) -> dict:
        """Только числа и флаги, без имён тегов; только память — ручку здоровья зовут в цикле событий."""
        now = self.monotonic()
        calls = self._user_window(now)
        took = sorted(ms for _, ms, _, reached in calls if reached)     # отказы без SQL p95 не разбавляют
        return {"sent_names_total": self._sent_names_total,
                "refused_unlisted": self._refused_unlisted,
                "in_flight": dict(self._in_flight),
                "auth_latched": self._auth_latched,
                # ближайший ранг: при пяти вызовах — самый долгий
                "p95_ms": took[math.ceil(0.95 * len(took)) - 1] if len(took) >= P95_MIN_CALLS else None,
                "timeouts_5m": sum(1 for _, _, code, _ in calls if code == "timeout"),
                "busy_5m": sum(1 for _, _, code, _ in calls if code == "busy"),
                "user_paused": self._user_paused(now)}

    async def run(self, statements: Sequence[str], *, lane: Lane = "user",
                  deadline_s: float | None = None) -> GateResult:
        loop = asyncio.get_running_loop()
        began = loop.time()                 # длительность вызова людей — от входа, с ожиданием места (К8)
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
        attempt = _Attempt()
        if lane != "user":
            return await self._call(statements, lane, budget, loop, began, attempt)
        # исход вызова людей — в окно нагрузки; отмена вызывающим — не исход
        try:
            result = await self._call(statements, lane, budget, loop, began, attempt)
        except Exception as exc:
            self._note_user(exc, loop.time() - began, attempt.reached)
            raise
        self._note_user(None, loop.time() - began, attempt.reached)
        return result

    async def _call(self, statements: tuple[str, ...], lane: Lane, budget: float,
                    loop: asyncio.AbstractEventLoop, began: float, attempt: _Attempt) -> GateResult:
        deadline = began + budget

        # 1. белый список и форма шаблона — до всего остального, без SQL
        sent = self._check(statements, lane)
        # 2. защёлка входа и пауза после обрыва — без входа
        self._raise_if_closed()
        # 2а. пауза полосы людей после сроков подряд — без места и SQL; unlisted остаётся unlisted (К8)
        if lane == "user" and self._user_paused(self.monotonic()):
            raise GateRefused("busy")
        # 3. место полосы: ждать в цикле asyncio не дольше срок − q_min
        q_min = min(self.q_min_s, budget / 3)
        slot = self._slots[lane]
        try:
            await asyncio.wait_for(slot.acquire(), timeout=deadline - q_min - loop.time())
        except TimeoutError:
            raise GateRefused("busy") from None
        probe = False
        try:
            # до первого удачного вызова — ждать исхода того, кто уже входит
            if not self._login_ok:
                wait = deadline - q_min - loop.time()
                if wait <= 0:
                    raise GateRefused("busy")
                try:
                    await asyncio.wait_for(self._first_login.acquire(), timeout=wait)
                except TimeoutError:
                    raise GateRefused("busy") from None
                if self._login_ok:
                    self._first_login.release()
                else:
                    probe = True
            # пока ждали места или первого входа, чужой вызов мог получить отказ входа или обрыв, а люди — паузу
            self._raise_if_closed()
            if lane == "user" and self._user_paused(self.monotonic()):
                raise GateRefused("busy")
            if deadline - loop.time() < q_min:
                raise GateRefused("busy")
            # 4. общий предел — только людям и только с местом
            if lane == "user" and not self.global_window.allow(WINDOW_KEY, self.monotonic()):
                raise GateRefused("rate")
            # 5. запрос — в пул ворот; место (и замок первого входа) отпустит колбэк после возврата драйвера
            waiter = self._submit(loop, lane, probe, statements, deadline - loop.time())
            attempt.reached = True
        except BaseException:
            if probe:
                self._first_login.release()
            slot.release()
            raise
        self._sent_names_total += len(sent)
        # 6. результат ждём не дольше остатка срока; по сроку запрос дорабатывает с местом.
        # 7. защёлку и паузу ставит _finish. Ошибка бросается после блока: без __context__
        failure: HistorianError | None = None
        try:
            rows = await asyncio.wait_for(waiter, timeout=deadline - loop.time())
        except TimeoutError:
            failure = HistorianError("timeout", "срок вызова истёк, запрос дорабатывает с местом", sent)
            if lane == "user":              # место ещё держит драйвер: ждущий, взяв его, увидит паузу
                self._count_timeouts("timeout")
        except HistorianError as err:
            failure = err.with_sent(sent)
        if failure is not None:
            raise failure
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
        # форма шаблона целиком: NOT, OR, вторая инструкция, комментарий не проходят и с именами из списка
        if not isinstance(sql, str) or not plain(sql) or template_of(sql) is None:
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

    def _user_window(self, now: float) -> deque[tuple[float, int, str, bool]]:
        """Исходы людей моложе STATS_WINDOW_S; старые уходят (граница — как у SlidingWindow)."""
        calls = self._user_calls
        while calls and calls[0][0] <= now - STATS_WINDOW_S:
            calls.popleft()
        return calls

    def _user_paused(self, now: float) -> bool:
        return self._user_pause_until is not None and now < self._user_pause_until

    def _note_user(self, exc: Exception | None, took_s: float, reached: bool) -> None:
        """Исход вызова людей — в окно нагрузки (p95, сроки, busy); счёт сроков подряд здесь не ведётся."""
        code = "ok" if exc is None else exc.code if isinstance(exc, (HistorianError, GateRefused)) else "error"
        now = self.monotonic()
        self._user_window(now).append((now, max(0, round(took_s * 1000)), code, reached))

    def _count_timeouts(self, code: str) -> None:
        """Счёт сроков подряд у людей по исходу вызова, дошедшего до историана: срок — +1, прочий исход — ноль.

        Зовут его в момент исхода, до того как отпущены место и замок первого входа (срок ворот, _finish):
        ждущий их, взяв, уже видит паузу. Отказы без SQL (busy, пауза, unlisted, rate, защёлка) сюда не
        приходят и счёт не трогают.
        """
        if code != "timeout":
            self._timeouts_in_row = 0
            return
        self._timeouts_in_row += 1
        if self._timeouts_in_row >= PAUSE_AFTER_TIMEOUTS:
            self._timeouts_in_row = 0           # следующая пауза — снова после стольких же сроков подряд
            self._user_pause_until = self.monotonic() + USER_PAUSE_S
            log.warning("ворота: сроки подряд у людей (%d) — пауза полосы людей %g с", PAUSE_AFTER_TIMEOUTS,
                        USER_PAUSE_S)

    def _raise_if_closed(self) -> None:
        if self._auth_latched:
            raise HistorianError("auth", f"защёлка: {AUTH_LATCH_TEXT}")
        if self._pause_until is not None and self.monotonic() < self._pause_until:
            raise HistorianError("connect", "пауза после обрыва")

    def _note_failure(self, err: HistorianError) -> None:
        if err.code == "auth" and not self._auth_latched:
            self._auth_latched = True
            log.error("ворота: %s (%s); до перезапуска входа не будет", AUTH_LATCH_TEXT, err.public())
        elif err.code == "connect":
            self._pause_until = self.monotonic() + CONNECT_COOLDOWN_S
            log.warning("ворота: нет связи с историаном (%s) — пауза %g с", err.public(), CONNECT_COOLDOWN_S)

    def _submit(self, loop: asyncio.AbstractEventLoop, lane: Lane, probe: bool, statements: tuple[str, ...],
                remaining_s: float) -> asyncio.Future:
        waiter = loop.create_future()
        future = self._pool.submit(self._query, statements, remaining_s)
        self._in_flight[lane] += 1

        def done(f: Future) -> None:        # в потоке драйвера (или сразу, если уже готово)
            try:
                loop.call_soon_threadsafe(self._finish, lane, probe, f, waiter)
            except RuntimeError:            # цикл закрыт: отпускать некому
                pass

        future.add_done_callback(done)
        return waiter

    def _finish(self, lane: Lane, probe: bool, future: Future, waiter: asyncio.Future) -> None:
        """В цикле событий после возврата драйвера.

        Удача, защёлка, пауза и счёт сроков подряд людей отмечаются до того, как отпущены
        замок первого входа и место: следующий, взяв их, уже видит исход — и так же, если
        вызов закончился по сроку раньше (тогда его срок уже посчитан, а исход драйвера — нет).
        """
        exc = None if future.cancelled() else future.exception()
        if isinstance(exc, HistorianError):
            self._note_failure(exc)
        elif exc is None and not future.cancelled():
            self._login_ok = True
        if lane == "user" and not waiter.done() and not future.cancelled():
            self._count_timeouts("ok" if exc is None else exc.code if isinstance(exc, HistorianError) else "error")
        if probe:
            self._first_login.release()
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
