"""Роль gateway (Д5а): вход студентов, рабочее место и его пробуждение, уборщик простоя, /health/gateway.

Договор Role Д3а: конструктор без ввода-вывода; core.db открывается в lifespan через asyncio.to_thread;
health() и health_json() читают только память; ошибка настройки — config_error (503 «Шлюз не настроен»,
без уборщика и без обращений к базе), процесс жив. lifespan не бросает никогда: иначе uvicorn выходит с
кодом 3, и core со службой данных и LLM-прокси уходит в цикл перезапусков. Уборщик — фоновая задача, его
первый такт не задерживает запуск других ролей (Д5-R27).

Крючки — только через реестр hooks_of(app) (Д4а): обработчик HTTPException на /pcbk/api/ и /health/gateway
(отказы вида OpenCode) и предел тела /pcbk/api/ с 413 того же вида (Ш22).

Модули accounts и api (а через api — accounts) здесь импортируются только внутри методов: команда
администратора идёт как python -m pcbk_core.gateway.accounts, и пакет, импортирующий её модуль при
загрузке, дал бы RuntimeWarning runpy в stderr каждой команды.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, TypeVar

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse

from ..app import RoleBase
from ..hooks import hooks_of
from ..settings import GatewaySettings, Settings
from .places import PLACES, Workplaces
from .stats import GatewayStats

if TYPE_CHECKING:
    from .accounts import AccountStore

log = logging.getLogger("pcbk_core.gateway")

HEALTH_PATH = "/health/gateway"
BODY_LIMIT = 65536                          # тела шлюза Д5а — логин и пароль
CONFIG_ERROR_CHARS = 80                     # вся причина config_error, дописанная при запуске
HEALTH_REASON_CHARS = 60                    # причина в health()
SILENT_TICKS = 4                            # уборщик молчит дольше 4 тактов — сбой (GW_STALE_S сторожа)

_T = TypeVar("_T")


def clip(text: str, limit: int) -> str:
    """Текст не длиннее limit без разрыва слова: длинный режется по последнему пробелу и кончается «…»."""
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 0 else head).rstrip(" ;:,") + "…"


class LoginGate:
    """Сверки scrypt входа (Д5-R25): не больше parallel попыток разом, ждущих — не больше queue.

    full() и attempt() зовутся подряд без await между ними — решение и постановка в очередь атомарны в цикле
    событий. Попытка — своя задача, запрос её не отменяет: check(pending) (замок и сверка) идёт в потоке, и
    место освобождается сразу после неё. Неудача сверки (failed) получает метку времени — строго растущую — и
    ложится в память, pending[key], в той же задаче без await, до освобождения места: следующая попытка того же
    логина видит её в замке ещё до записи (ревью А4, M1), сверок одного логина при наплыве — не больше
    LOCK_FAILURES - 1 + parallel. Запись в замок с той же меткой и строка журнала (задание after(итог, метка))
    идут потом, по одному, в задаче-писателе вне места очереди (итоговое ревью Д5а, А4-m1): место
    освобождается одинаково быстро, куда бы ни писалась неудача — в таблицу (логин есть) или в память (нет), и
    ответ записи не ждёт. Метка уходит из pending после записи; замок считает метки таблицы и pending
    объединением — точно, без ожидания записи. Запись не удалась (задание вернуло False или бросило) — метка
    остаётся в pending, пока не выйдет из окна замка window: известный логин запирается и при сломанной записи,
    меток у логина не больше LOCK_FAILURES - 1 + parallel (дальше замок, сверок нет). Незаписанных заданий
    больше backlog — full(): при долгой чужой
    блокировке базы вход «перегружен» без scrypt, а память и пул потоков не растут. Отменённый запрос не
    открывает дорогу лишнему scrypt и не теряет неудачу; отменённая задача попытки отменяет и ответ — запрос не
    висит (А4-m2).
    """

    def __init__(self, parallel: int, queue: int, backlog: int, clock: Callable[[], float] = time.time,
                 window: float = 900.0):
        self._sem = asyncio.Semaphore(parallel)
        self._queue = queue
        self._backlog = backlog
        self._clock = clock
        self._window = window
        self._last_stamp = float("-inf")
        self._tasks: set[asyncio.Task] = set()
        self._jobs: deque[tuple[bytes, float | None, Callable[[], object] | None]] = deque()
        self._writer: asyncio.Task | None = None
        self.pending: dict[bytes, list[float]] = {}
        self.waiting = 0

    def full(self) -> bool:
        return (self._sem.locked() and self.waiting >= self._queue) or len(self._jobs) >= self._backlog

    def pending_of(self, key: bytes) -> tuple[float, ...]:
        """Метки незаписанных неудач логина — снимок; вызывающий берёт его до чтения замка (AccountStore.locked).
        Метки старше окна замка уходят: их неудача ни в какой замок уже не входит."""
        self._expire(key)
        return tuple(self.pending.get(key, ()))

    def _expire(self, key: bytes) -> None:
        stamps = self.pending.get(key)
        if stamps:
            horizon = self._clock() - self._window
            stamps[:] = [t for t in stamps if t > horizon]
        if not stamps:
            self.pending.pop(key, None)

    def _stamp(self) -> float:
        """Метка неудачи: время часов, строго больше прежней — метки двух неудач не совпадают никогда."""
        t = self._clock()
        if t <= self._last_stamp:
            t = math.nextafter(self._last_stamp, math.inf)
        self._last_stamp = t
        return t

    async def attempt(self, key: bytes, check: Callable[[tuple[float, ...]], _T], failed: Callable[[_T], bool],
                      after: Callable[[_T, float | None], Callable[[], object] | None]) -> _T:
        """key — ключ логина (login_key); check(pending) — в потоке, с местом; failed(итог) — неудача сверки: ей
        метка в pending; after(итог, метка) — задание писателю (в потоке, вне места) или None. Задание вернуло
        False — неудача не записана, метка остаётся до выхода из окна."""
        self.waiting += 1
        try:
            await self._sem.acquire()
        finally:
            self.waiting -= 1
        verdict: asyncio.Future = asyncio.get_running_loop().create_future()
        try:
            task = asyncio.create_task(self._run(key, check, failed, after, verdict), name="gw-login")
        except BaseException:
            self._sem.release()
            raise
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await asyncio.shield(verdict)

    async def _run(self, key: bytes, check: Callable[[tuple[float, ...]], _T], failed: Callable[[_T], bool],
                   after: Callable[[_T, float | None], Callable[[], object] | None], verdict: asyncio.Future) -> None:
        try:
            try:
                result = await asyncio.to_thread(check, self.pending_of(key))
            except Exception as exc:
                verdict.set_exception(exc)
                verdict.exception()     # забрано: ушедший запрос не оставит «исключение не получено»
                return
            stamp = self._stamp() if failed(result) else None
            job = after(result, stamp)
            if stamp is not None:       # без await и до освобождения места
                self.pending.setdefault(key, []).append(stamp)
            if stamp is not None or job is not None:
                self._enqueue(key, stamp, job)
            verdict.set_result(result)
        finally:
            if not verdict.done():      # задачу отменили (остановка): запрос получает отмену, а не ждёт вечно
                verdict.cancel()
            self._sem.release()

    def _enqueue(self, key: bytes, stamp: float | None, job: Callable[[], object] | None) -> None:
        self._jobs.append((key, stamp, job))
        if self._writer is None or self._writer.done():
            self._writer = asyncio.create_task(self._write(), name="gw-login-record")

    async def _write(self) -> None:
        """Писатель: задания по одному, в потоке. Метка уходит из pending, только если задание её записало (не
        вернуло False и не бросило); иначе ждёт выхода из окна замка."""
        while self._jobs:
            key, stamp, job = self._jobs.popleft()
            recorded = False
            try:
                recorded = job is None or await asyncio.to_thread(job) is not False
            except Exception as exc:
                log.warning("шлюз: после сверки входа — %s", type(exc).__name__)
            finally:
                if stamp is not None:
                    stamps = self.pending.get(key, [])
                    if recorded and stamp in stamps:
                        stamps.remove(stamp)
                    self._expire(key)

    async def drain(self) -> None:
        """Дождаться идущих попыток и записи их неудач — на остановке роли и в тестах."""
        await asyncio.gather(*self._tasks, return_exceptions=True)
        while self._writer is not None and not self._writer.done():
            await asyncio.gather(self._writer, return_exceptions=True)


class GatewayRole(RoleBase):
    """Шлюз входа. Ни один конструктор (роли, AccountStore, SpCtl, Workplaces) ничего не открывает."""

    name = "gateway"

    def __init__(self, settings: Settings, gw: GatewaySettings, *, store: AccountStore, workplaces: Workplaces,
                 stats: GatewayStats, config_error: str | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        from .accounts import LOCK_WINDOW_S
        from .api import LOGIN_BACKLOG, LOGIN_PARALLEL, LOGIN_QUEUE

        self.settings = settings
        self.gw = gw
        self.store = store
        self.workplaces = workplaces
        self.stats = stats
        self.config_error = config_error
        self.monotonic = monotonic
        self.wallclock = wallclock
        self.started_at = wallclock()           # checked_at до первого такта — время запуска роли
        self.login_gate = LoginGate(LOGIN_PARALLEL, LOGIN_QUEUE, LOGIN_BACKLOG, clock=store.clock,
                                    window=LOCK_WINDOW_S)
        self._reaper: asyncio.Task | None = None

    # --- маршруты и крючки ---

    def router(self) -> APIRouter:
        from .api import auth_router, catch_all_router, configured, front_only, workplace_router

        router = APIRouter()
        guarded = [Depends(front_only), Depends(configured(self))]
        router.include_router(auth_router(self), dependencies=guarded)
        router.include_router(workplace_router(self), dependencies=guarded)

        @router.get(HEALTH_PATH, dependencies=[Depends(front_only)])
        async def health_gateway() -> JSONResponse:
            # только числа и тексты: ни логинов, ни номеров мест
            return JSONResponse(self.health_json(self.wallclock()))

        router.include_router(catch_all_router())
        return router

    def install(self, app: FastAPI) -> None:
        """Только через реестр Д4а: отказы HTTPException и 413 под /pcbk/api/ — JSON вида OpenCode (Ш22), отказ
        front_only и 405 на /health/gateway — тоже; предел тела вне pcbk-front отвечает 404, как зона (ревью
        А4, M3); сеть мест для сверки токенов служб (Ш25)."""
        from .api import PREFIX, http_exception_handler, too_large

        hooks = hooks_of(app)
        hooks.http_exception(PREFIX + "/", http_exception_handler)
        hooks.http_exception(HEALTH_PATH, http_exception_handler)
        hooks.body_limit(PREFIX + "/", BODY_LIMIT, respond=too_large)
        app.state.pcbk_stu_net = self.gw.STU_NET

    # --- здоровье: только память ---

    def health(self) -> tuple[bool, str]:
        if self.config_error:
            return False, "ошибка настройки: " + clip(self.config_error, HEALTH_REASON_CHARS)
        if self.store.probe_failures > 0 or self.store.op_failures > 0:
            return False, "учётные записи недоступны"
        if self.workplaces.sp_ctl == "down":
            return False, "нет связи с прокси сокета — места не будятся"
        if self.monotonic() - self.workplaces.last_tick_mono > SILENT_TICKS * self.gw.GW_REAPER_TICK_S:
            return False, "уборщик простоя молчит"
        return True, "принимает вход"

    def health_json(self, now: datetime) -> dict:
        """Договор сторожа (задача А1; Б1 — четыре ключа прокси): ровно эти ключи, только числа и тексты."""
        w, count = self.workplaces, self.stats.count_1h
        checked = w.checked_at if w.checked_at is not None else self.started_at
        return {
            "checked_at": checked.isoformat(),
            "config_error": self.config_error,
            "store_ok": self.store.probe_failures == 0 and self.store.op_failures == 0,
            "sp_ctl": w.sp_ctl,
            "running_n": len(w.running),
            "ready_n": sum(1 for n in PLACES if w.is_ready(n)),
            "wake_ok_1h": count("wake_ok"),
            "wake_failed_1h": count("wake_failed"),
            "stop_failed_1h": count("stop_failed"),
            "place_auth_failed_1h": count("place_auth_failed"),
            "logins_failed_1h": count("login_failed"),
            "origin_refused_1h": count("origin_refused"),
            "login_busy_1h": count("login_busy"),
            "cookies_duplicate_1h": count("cookies_duplicate"),
            "drill": self.gw.DRILL_GATEWAY,
            # Д5б (Б1): сторож читает все четыре вместе или ни одного
            "refused_1h": count("refused"),
            "streams": self.stats.streams,
            "stream_broken_1h": count("stream_broken"),
            "upstream_failed_1h": count("upstream_failed"),
        }

    # --- жизненный цикл ---

    def _add_config_error(self, why: str) -> None:
        """Дописать причину запуска: она (сбой базы, класс ошибки) остаётся целиком, при нехватке места до
        CONFIG_ERROR_CHARS сокращается прежняя часть — без разрыва слова (ревью А4, M2)."""
        why = clip(why, CONFIG_ERROR_CHARS)
        if not self.config_error:
            self.config_error = why
            return
        room = CONFIG_ERROR_CHARS - len(why) - len("; ")
        head = clip(self.config_error, room) if room > 1 else ""
        self.config_error = f"{head}; {why}" if head else why

    def lifespan(self):
        return self._lifespan()

    @asynccontextmanager
    async def _lifespan(self):
        try:
            self.started_at = self.wallclock()
            if not await asyncio.to_thread(self.store.open):
                self._add_config_error(f"учётные записи не открываются: {self.store.last_error or 'сбой'}")
            await self.workplaces.spctl.open()
            await self.workplaces.open()
            if self.config_error:
                log.error("шлюз: ошибка настройки: %s", self.config_error)
            else:
                self._reaper = asyncio.create_task(self._reaper_loop(), name="gw-reaper")   # не ожидается
        except Exception as exc:                # запуск шлюза не роняет core: причина — на страницу
            self._add_config_error(f"запуск шлюза: {type(exc).__name__}")
            log.error("шлюз: ошибка настройки: %s", self.config_error)
        try:
            yield
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        task, self._reaper = self._reaper, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.login_gate.drain()           # неудачные входы — в замок до выхода
        for name, close in (("места", self.workplaces.aclose), ("прокси сокета", self.workplaces.spctl.aclose)):
            try:
                await close()
            except Exception as exc:
                log.warning("шлюз: клиент (%s) не закрыт (%s)", name, type(exc).__name__)

    async def _reaper_loop(self) -> None:
        """Строка запуска, первый такт сразу, дальше — через workplaces.next_tick_s(); каждый такт — ещё проба
        записи store.ping() в потоке. Исключение такта — строка журнала, цикл живёт. freeze_reaper — только
        первый такт."""
        await self._startup_line()
        while True:
            try:
                await self.workplaces.tick()
            except Exception as exc:
                log.warning("такт уборщика: %s", type(exc).__name__)
            try:
                await asyncio.to_thread(self.store.ping)
            except Exception as exc:
                log.warning("проба учётных записей: %s", type(exc).__name__)
            if self.gw.DRILL_GATEWAY == "freeze_reaper":
                log.warning("УЧЕНИЯ: уборщик простоя замер после первого такта")
                await asyncio.Event().wait()
            await asyncio.sleep(self.workplaces.next_tick_s())

    async def _startup_line(self) -> None:
        try:
            students: object = await asyncio.to_thread(self.store.students)
        except Exception:                       # StoreError и прочее — число неизвестно
            students = "?"
        log.info("шлюз: учётных записей студентов %s, простой до сна %g с", students, self.gw.GW_IDLE_STOP_S)
        if self.gw.DRILL_GATEWAY:
            log.warning("УЧЕНИЯ: DRILL_GATEWAY=%s", self.gw.DRILL_GATEWAY)
