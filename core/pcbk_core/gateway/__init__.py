"""Роль gateway (Д5а): вход студентов, рабочее место и его пробуждение, уборщик простоя, /health/gateway.

Договор Role Д3а: конструктор без ввода-вывода; core.db открывается в lifespan через asyncio.to_thread;
health() и health_json() читают только память; ошибка настройки — config_error (503 «Шлюз не настроен»,
без уборщика и без обращений к базе), процесс жив. lifespan не бросает никогда: иначе uvicorn выходит с
кодом 3, и core со службой данных и LLM-прокси уходит в цикл перезапусков. Уборщик — фоновая задача, его
первый такт не задерживает запуск других ролей (Д5-R27).

Крючки — только через реестр hooks_of(app) (Д4а): обработчик HTTPException на /pcbk/api/, /health/gateway и
/health/web (отказы вида OpenCode) и пределы тела /pcbk/api/ и /pcbk/api/oc/ (Д5б, GW_MAX_BODY_BYTES) с 413 того же
вида (Ш22) — вне pcbk-front 404.

Отчёты браузеров (Д6б, Д6-R50) — web_router: POST /pcbk/api/web/report и GET /health/web; счётчики WebReports —
в памяти роли (web_reports), окно — час.

Агенты студентов (Д7а, Д7-R60) — agents_router: GET /health/agents (зона front, тот же обработчик отказов из реестра) и
«Мои агенты» GET /pcbk/api/agents (Д7б, Д7-R64: зона, настройка и сессия — у маршрута, m24); счётчики AgentStats —
общие с ролью «данные» (main.build_roles), без переданного объекта роль создаёт свой.

Перезагрузка агентов (Д7-R58, задача А5): свой AgentStore (agents) на тот же core.db, воркер reloads (AgentReloads) и
крючок готовности workplaces.on_ready = reloads.place_ready; карантин при пробуждении (Д7-R59, задача А6 Д7б) —
workplaces.repair = reloads.repair. lifespan открывает учёт агентов в потоке; неудача — флаг
сбоя учёта воркера (строка «Агенты студентов»), не config_error (предпроверка П13): вход студентов от учёта агентов не
зависит. Воркер — фоновая задача рядом с уборщиком, при config_error не стартует; остановка роли его снимает.

Модули accounts, agents_api, api, proxy и web (а через них — accounts) здесь импортируются только внутри методов:
команда администратора идёт как python -m pcbk_core.gateway.accounts, и пакет, импортирующий её модуль при
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

from .. import zones
from ..agents.stats import AgentStats
from ..agents.store import AgentStore
from ..app import RoleBase
from ..hooks import hooks_of
from ..settings import GatewaySettings, Settings
from .places import PLACES, Workplaces
from .reload import AgentReloads
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
                 wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 stream_recheck_s: float = 10.0, agent_stats: AgentStats | None = None):
        from .accounts import LOCK_WINDOW_S
        from .api import LOGIN_BACKLOG, LOGIN_PARALLEL, LOGIN_QUEUE
        from .web import WebReports

        self.settings = settings
        self.gw = gw
        self.store = store
        self.workplaces = workplaces
        self.stats = stats
        self.config_error = config_error
        self.monotonic = monotonic
        self.wallclock = wallclock
        self.stream_recheck_s = stream_recheck_s    # сессия потока /event — не реже и не чаще раза за срок (Д5-R35)
        self.event_streams: set = set()             # открытые потоки /event (proxy._EventStream): закрыть при остановке
        self.started_at = wallclock()           # checked_at до первого такта — время запуска роли
        self.login_gate = LoginGate(LOGIN_PARALLEL, LOGIN_QUEUE, LOGIN_BACKLOG, clock=store.clock,
                                    window=LOCK_WINDOW_S)
        self.web_reports = WebReports()         # отчёты браузеров (Д6-R50): память, часы — monotonic
        # счётчики агентов студентов (Д7-R60): общий с ролью «данные» объект из build_roles, иначе свой
        self.agent_stats = AgentStats(monotonic=monotonic) if agent_stats is None else agent_stats
        # учёт агентов шлюза (Д7-R53: свой AgentStore на тот же core.db) и воркер перезагрузки (Д7-R58)
        self.agents = AgentStore(settings.DB_PATH, settings.AGENTS_ROOT)
        self.reloads = AgentReloads(workplaces, self.agents, self.agent_stats, monotonic=monotonic,
                                    wallclock=wallclock, drill=gw.DRILL_AGENTS)
        workplaces.on_ready = self.reloads.place_ready
        workplaces.repair = self.reloads.repair         # карантин кривого файла агента при пробуждении (Д7-R59)
        self._reaper: asyncio.Task | None = None
        self._reload_task: asyncio.Task | None = None

    # --- маршруты и крючки ---

    def router(self) -> APIRouter:
        from .agents_api import agents_router
        from .api import auth_router, catch_all_router, configured, front_only, workplace_router
        from .proxy import proxy_router
        from .web import web_router

        router = APIRouter()
        guarded = [Depends(front_only), Depends(configured(self))]
        router.include_router(auth_router(self), dependencies=guarded)
        router.include_router(workplace_router(self), dependencies=guarded)
        router.include_router(proxy_router(self))       # front_only и configured — у самого роутера прокси
        router.include_router(web_router(self))         # зона и настройка — у маршрутов отчётов; до ловящего
        router.include_router(agents_router(self))      # /health/agents (Д7а) и /pcbk/api/agents (Д7б): зона и
                                                        # настройка — у маршрутов (m24); до ловящего

        @router.get(HEALTH_PATH, dependencies=[Depends(front_only)])
        async def health_gateway() -> JSONResponse:
            # только числа и тексты: ни логинов, ни номеров мест
            return JSONResponse(self.health_json(self.wallclock()))

        router.include_router(catch_all_router())
        return router

    def install(self, app: FastAPI) -> None:
        """Только через реестр Д4а: отказы HTTPException и 413 под /pcbk/api/ — JSON вида OpenCode (Ш22), отказ
        front_only и 405 на /health/gateway — тоже; предел тела вне pcbk-front отвечает 404, как зона (ревью
        А4, M3), — и у /pcbk/api/oc/ с пределом GW_MAX_BODY_BYTES (Д5-R36): самый длинный префикс берёт его, и
        без respond= сеть места получила бы 413 и узнала бы ручку; у него же общий бюджет тел (H2, сверх — 503
        busy, вне pcbk-front — 404); сеть мест для сверки токенов служб (Ш25). /health/web (Д6б) и /health/agents
        (Д7а) — тот же обработчик, что /health/gateway: отказ зоны и 405 — 404 вида OpenCode без Allow."""
        from .agents_api import HEALTH_AGENTS_PATH
        from .api import PREFIX, http_exception_handler, too_large
        from .proxy import GW_BODY_BUDGET_BYTES, OC_PREFIX, body_busy
        from .web import HEALTH_WEB_PATH

        hooks = hooks_of(app)
        hooks.http_exception(PREFIX + "/", http_exception_handler)
        hooks.http_exception(HEALTH_PATH, http_exception_handler)
        hooks.http_exception(HEALTH_WEB_PATH, http_exception_handler)
        hooks.http_exception(HEALTH_AGENTS_PATH, http_exception_handler)
        hooks.body_limit(PREFIX + "/", BODY_LIMIT, respond=too_large)
        # общий бюджет тел до обработчика (H2); не меньше предела одного тела — иначе оно не поместилось бы; вне
        # pcbk-front бюджет не доступен: 404 без чтения тела (медленные тела из сети места его не займут)
        hooks.body_limit(OC_PREFIX + "/", self.gw.GW_MAX_BODY_BYTES, respond=too_large,
                         budget=max(GW_BODY_BUDGET_BYTES, self.gw.GW_MAX_BODY_BYTES), over_budget=body_busy(self),
                         budget_admits=zones.is_front)
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

    def agents_health_json(self, now: datetime) -> dict:
        """Договор сторожа /health/agents (Д7-R60): ровно 15 ключей, только числа и тексты — ни имён агентов, ни номеров
        мест. store_ok — учёт пишется и у роли «данные», и у воркера (оба флага None). checked_at — пульс воркера
        перезагрузки (до первого прохода — null: строка сторожа честно «неизвестно»); pending и pending_oldest_s — только
        по готовым местам очереди (reloads.pending_view(now)). Только память."""
        s, count = self.agent_stats, self.agent_stats.count_1h
        checked = self.reloads.checked_at
        pending, oldest = self.reloads.pending_view(now)
        return {
            "checked_at": None if checked is None else checked.isoformat(),
            "store_ok": s.store_error is None and s.worker_store_error is None,
            "dir_error": s.dir_error,
            "pending": pending,
            "pending_oldest_s": oldest,
            "saved_1h": count("saved"),
            "refused_1h": count("refused"),
            "unavailable_1h": count("unavailable"),
            "visible_1h": count("visible"),
            "not_visible_1h": count("not_visible"),
            "quarantined_1h": count("quarantined"),
            "reload_failed_1h": count("reload_failed"),
            "reload_delayed_1h": count("reload_delayed"),
            "image_config_1h": count("image_config"),
            "drill": self.gw.DRILL_AGENTS,
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
            await self._open_agents()               # не бросает: сбой — флаг воркера, не config_error (П13)
            await self.workplaces.spctl.open()
            await self.workplaces.open()
            if self.config_error:
                log.error("шлюз: ошибка настройки: %s", self.config_error)
            else:
                self._reaper = asyncio.create_task(self._reaper_loop(), name="gw-reaper")   # не ожидается
                self._reload_task = asyncio.create_task(self.reloads.run(), name="gw-agents-reload")
        except Exception as exc:                # запуск шлюза не роняет core: причина — на страницу
            self._add_config_error(f"запуск шлюза: {type(exc).__name__}")
            log.error("шлюз: ошибка настройки: %s", self.config_error)
        try:
            yield
        finally:
            await self._shutdown()

    async def _open_agents(self) -> None:
        """Таблицы учёта агентов (CREATE IF NOT EXISTS) в потоке. Неудача — флаг сбоя учёта воркера и строка журнала:
        строка «Агенты студентов» краснеет, а вход студентов и /healthz/gateway — нет (П13)."""
        try:
            opened = await asyncio.to_thread(self.agents.open)
        except Exception as exc:
            self.agents.last_error, opened = type(exc).__name__, False
        if not opened:
            self.agent_stats.worker_store_failed(self.agents.last_error or "Error")
            log.error("шлюз: учёт агентов не открыт (%s)", self.agents.last_error)

    async def _shutdown(self) -> None:
        for name in ("_reaper", "_reload_task"):
            task = getattr(self, name)
            setattr(self, name, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await self.reloads.drain()              # крючок готовности: метки — в учёт до выхода
        await self.login_gate.drain()           # неудачные входы — в замок до выхода
        # потоки /event — до клиентов мест: закрытие каждого (своё или начатое снятой задачей запроса) доводится здесь,
        # всех вместе — не дольше STREAM_SHUTDOWN_S (Б4-m2): остановка не ждёт застрявшее место по 11 с; недоведённые
        # закрывает конец процесса
        from . import proxy                     # лениво, как proxy_router: срок читается при остановке (тесты его меняют)
        closing = [asyncio.ensure_future(stream.shutdown()) for stream in list(self.event_streams)]
        if closing:
            _, pending = await asyncio.wait(closing, timeout=proxy.STREAM_SHUTDOWN_S)
            for task in closing:
                if task.done() and not task.cancelled():
                    task.exception()                # ошибка закрытия уже в журнале release(); не «never retrieved»
            if pending:
                log.warning("шлюз: потоки /event не закрылись при остановке за %g с: %d", proxy.STREAM_SHUTDOWN_S,
                            len(pending))
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
