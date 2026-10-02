"""Роль «данные»: каталог тегов, свежесть историана, журнал событий, ручки здоровья и инструменты
(DataService — service.py; HTTP /api/data/* — http_api.py; MCP /mcp — mcp_server.py; save_agent — save_agent.py).

Два фоновых цикла делят одно фоновое место ворот. Каталог грузится сразу при
старте и дальше раз в CATALOG_REFRESH_S, после неудачи — с растущей паузой.
Свежесть опрашивается после первой попытки каталога и дальше раз в
FRESH_POLL_S. Каждая попытка обоих — событие system в журнале событий. Журнал
открывается первым делом в lifespan, его запись проверяется в фоне раз в
EVENTS_CHECK_S. Состояние роли меняется только в цикле событий; SQLite — в
потоках asyncio.to_thread.

Учёт агентов студентов (AgentStore, Д7-R56) открывается в lifespan сразу после журнала событий. Его сбой —
agent_stats.store_failed (строка сторожа «Агенты студентов»), а не config_error: /healthz/data от агентов не
зависит (П13). Дальше учёт открывается заново и пробуется на запись раз в EVENTS_CHECK_S вместе с проверкой журнала
(итоговое ревью Д7а, I-1): флаг сбоя после одного SQLITE_BUSY у save_agent снимается сам, без перезапуска core.
"""
import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

from ..agents.stats import AgentStats
from ..agents.store import AgentStore, StoreError
from ..app import RoleBase
from ..hooks import hooks_of
from ..secrets import TokenTable
from ..settings import Settings
from .catalog import Catalog
from .events import EVENTS_CHECK_S, CallEvent, EventLog, Outcome, outcome_for
from .freshness import ERROR_TEXTS, FreshnessTracker
from .gate import GateRefused, GateResult, HistorianGate
from .historian import HistClock, HistorianError, QueryFn, parse_clock
from .http_api import data_router, validation_handler
from .mcp_server import MCP_PATH, build_mcp, mcp_auth
from .save_agent import SaveAgent
from .service import DataService
from .sql import allowed_literal, catalog_sql, clock_sql, live_all_sql, live_sql

FRESH_TAGS = 8
BACKOFF_BASE_S = 60.0
BACKOFF_MAX_S = 900.0
# причины «настройка с ошибкой» для /healthz/data: не длиннее 80 знаков, без строк файлов
CONFIG_ERRORS = {
    "bdrv_unreadable": "bdrv.env не читается (нет файла или прав)",
    "bdrv_invalid": "bdrv.env: не заданы BDRV_* или неверная строка",
    "whitelist_unreadable": "белый список не читается",
}
# core-tokens не читается: это не config_error — историан не под угрозой, фоновые циклы идут
TOKENS_ERROR = "токены не читаются — все вызовы получают 401"
EVENTS_ERROR = "журнал событий не пишется: "
EVENTS_ERROR_CHARS = 53                 # 27 + 53 ≤ 80 (F10)
STORE_PREFIX = "учёт агентов: "        # начало текста StoreError — в строке журнала проверки не повторяется

log = logging.getLogger("pcbk_core.data")


class DataRole(RoleBase):
    """config_error — настройка с ошибкой (bdrv.env или белый список не читаются): здоровье — 503 с
    этой причиной, фоновые циклы не запускаются, к историану ни одного входа.

    tokens — всегда TokenTable: не передан — пустая, всем 401 (F18); tokens_error — причина, если
    core-tokens не прочитан. Конструктор без ввода-вывода (F4): журнал событий только запоминает путь,
    служба (service) — кэш, single-flight и окно частоты в памяти, MCP (mcp, mcp_app) строится один
    раз — его session_manager.run() SDK даёт однажды, и его запускает lifespan.

    agent_stats — счётчики агентов студентов (Д7-R53, Д7-R60): один объект на процесс, его создаёт
    main.build_roles и отдаёт и шлюзу (/health/agents); не передан — свой (тесты). agents — учёт и файлы агентов
    (Д7-R56): не передан — AgentStore(settings.DB_PATH, settings.AGENTS_ROOT), конструктор только запоминает пути;
    save_agent — инструмент MCP save_agent (Д7-R57).
    """

    name = "data"

    def __init__(self, settings: Settings, query: QueryFn, whitelist: frozenset[str], *,
                 monotonic: Callable[[], float] = time.monotonic,
                 wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 config_error: str | None = None,
                 tokens: TokenTable | None = None, tokens_error: str | None = None,
                 agents: AgentStore | None = None, agent_stats: AgentStats | None = None):
        self.settings = settings
        self.config_error = config_error
        self.tokens = TokenTable.empty() if tokens is None else tokens
        self.tokens_error = tokens_error
        self.events = EventLog(settings.DB_PATH)
        self.whitelist = whitelist
        self.monotonic = monotonic
        self.wallclock = wallclock
        self.agent_stats = AgentStats(monotonic=monotonic) if agent_stats is None else agent_stats
        self.agents = AgentStore(settings.DB_PATH, settings.AGENTS_ROOT) if agents is None else agents
        # вне TagName IN (…) ворота пускают только даты сводки и закрытый набор ww-значений
        self.gate = HistorianGate(query, allowed=whitelist, literal_ok=allowed_literal, monotonic=monotonic)
        self.catalog = Catalog.empty()
        self.freshness = FreshnessTracker(freeze_stamps=settings.DRILL_FRESHNESS == "freeze_stamps")
        self.catalog_error: str | None = None
        self.catalog_failures = 0
        self.catalog_loaded_mono: float | None = None
        self.freshness_tags: tuple[str, ...] = ()
        # часы историана и момент их получения: «сейчас историана» для Д3б без лишнего запроса
        self.clock: HistClock | None = None
        self.clock_mono: float | None = None
        self.service = DataService(self)
        self.save_agent = SaveAgent(self)
        self.mcp = build_mcp(self)
        self.mcp_app = mcp_auth(self.mcp.streamable_http_app(), self)

    # --- каталог ---

    async def refresh_catalog(self) -> bool:
        """Каталог двумя вызовами фоновой полосы с общим сроком CATALOG_DEADLINE_S.

        Срок каждого вызова — остаток общего: драйвер держит место не дольше срока
        своего вызова, так что оба вместе держат фоновое место не дольше
        CATALOG_DEADLINE_S, и окно без опроса свежести укладывается в HIST_STALE_S.
        Неудача оставляет прежний каталог. Каждая попытка — событие system/catalog:
        SQL каталога и полного снимка Live без имён, sent пуст, summary — «без имён».
        """
        loop = asyncio.get_running_loop()
        began = loop.time()
        end = began + self.settings.CATALOG_DEADLINE_S
        sent: tuple[str, ...] = ()
        rows = 0
        try:
            first = await self.gate.run([clock_sql(), catalog_sql()], lane="background",
                                        deadline_s=end - loop.time())
            sent, rows = first.sent, _row_count(first.rows)
            clock_mono = self.monotonic()
            clock = parse_clock(first.rows[0])
            rest = end - loop.time()
            if rest <= 0:
                raise HistorianError("timeout", "срок каталога исчерпан до второго вызова")
            second = await self.gate.run([live_all_sql()], lane="background", deadline_s=rest)
            sent, rows = _union(sent, second.sent), rows + _row_count(second.rows)
            catalog = Catalog.from_rows(first.rows[1], second.rows[0], self.whitelist)
        except HistorianError as err:
            code, detail, sent = err.code, err.public(), _union(sent, err.sent)
        except GateRefused as err:
            # фон не дождался места к сроку: место держит вызов, который историан не дочитал, — это срок
            code, detail = ("timeout" if err.code == "busy" else err.code), f"ворота: {err.code}"
        except (ValueError, TypeError) as err:          # ответ не того вида; текст ответа — не в журнал
            code, detail = "query", f"ответ не того вида ({type(err).__name__})"
        else:
            self._catalog_loaded(catalog, clock, clock_mono)
            await self._system_event("catalog", "ok", f"без имён: {catalog.total} тегов, в белом списке "
                                     f"{catalog.whitelisted}, нет в историане {catalog.missing}, живых "
                                     f"{catalog.live}", sent, rows, began)
            return True
        self._catalog_failed(code, detail)
        summary = f"без имён: каталог не загружен — {code}" + ("" if detail == code else f" ({detail})")
        await self._system_event("catalog", outcome_for(code), summary, sent, rows, began)
        return False

    def _catalog_loaded(self, catalog: Catalog, clock: HistClock, clock_mono: float) -> None:
        self.catalog = catalog
        self.clock, self.clock_mono = clock, clock_mono
        self.freshness_tags = catalog.freshest(FRESH_TAGS)
        self.catalog_error = None
        self.catalog_failures = 0
        self.catalog_loaded_mono = self.monotonic()
        log.info("каталог: %d тегов, в белом списке %d, нет в историане %d, живых %d",
                 catalog.total, catalog.whitelisted, catalog.missing, catalog.live)

    def _catalog_failed(self, code: str, detail: str) -> bool:
        self.catalog_error = code
        self.catalog_failures += 1
        log.warning("каталог: не загружен (%s), неудач подряд %d%s", detail, self.catalog_failures,
                    "" if not self.catalog.loaded else " — остаётся прежний")
        return False

    def next_catalog_delay(self, ok: bool) -> float:
        if ok:
            return self.settings.CATALOG_REFRESH_S
        n = max(self.catalog_failures, 1)
        return float(min(BACKOFF_BASE_S * 2 ** min(n - 1, 10), BACKOFF_MAX_S))

    # --- свежесть ---

    async def poll_freshness(self) -> None:
        """Опрос меток по набору свежести; каждая попытка — событие system/freshness с sent из ворот.

        Исход — ok или outcome_for(код): код ошибки опроса, freshness.error после observe, у пропуска
        воротами — код ворот (busy). Сырой код — в summary.
        """
        began = asyncio.get_running_loop().time()
        tags = self.freshness_tags
        if not self.catalog.loaded:
            # у прочих отказов ворот (unlisted) своего текста нет: для людей это «каталог не загружен»
            code = self.catalog_error if self.catalog_error in ERROR_TEXTS else "catalog"
            self._fail(code, len(tags))
            outcome, summary, sent, rows = outcome_for(code), f"без SQL: {code}", (), 0
        elif not tags:
            self._fail("no_tags", 0)
            outcome, summary, sent, rows = outcome_for("no_tags"), "без SQL: no_tags", (), 0
        else:
            outcome, summary, sent, rows = await self._poll_tags(tags)
        await self._system_event("freshness", outcome, summary, sent, rows, began)

    async def _poll_tags(self, tags: tuple[str, ...]) -> tuple[Outcome, str, tuple[str, ...], int]:
        """(исход, summary, sent, строк Live); sent — только от ворот: GateResult или HistorianError."""
        try:
            result = await self.gate.run([clock_sql(), live_sql(tags)], lane="background")
        except HistorianError as err:
            self._fail(err.code, len(tags))
            return outcome_for(err.code), err.public(), err.sent, 0
        except GateRefused as err:
            # фоновое место занимает только загрузка каталога; её окно укладывается в HIST_STALE_S
            log.info("свежесть: опрос пропущен — ворота: %s", err.code)
            return outcome_for(err.code), f"опрос пропущен — ворота: {err.code}", (), 0
        try:
            return self._observe(tags, result)
        except Exception as exc:        # SQL уже ушёл: событие несёт sent ворот; текст исключения — не в журнал
            log.error("свежесть: непредвиденный сбой опроса (%s)", type(exc).__name__)
            return "error", f"непредвиденный сбой ({type(exc).__name__})", result.sent, 0

    def _observe(self, tags: tuple[str, ...], result: GateResult) -> tuple[Outcome, str, tuple[str, ...], int]:
        mono = self.monotonic()
        try:
            clock = parse_clock(result.rows[0])
        except ValueError:                              # часы не того вида
            self._fail("query", len(tags))
            return outcome_for("query"), "query: часы не того вида", result.sent, 0
        self.clock, self.clock_mono = clock, mono
        was = self.freshness.error
        self.freshness.observe(clock, result.rows[1], len(tags), mono, self.wallclock())
        self._note_transition(was)
        code = self.freshness.error
        rows = len(result.rows[1])
        if code is None:
            return "ok", f"тегов {len(tags)}, строк Live {rows}", result.sent, rows
        return outcome_for(code), code, result.sent, rows

    def _fail(self, code: str, tags: int) -> None:
        was = self.freshness.error
        self.freshness.fail(code, tags, self.monotonic(), self.wallclock())
        self._note_transition(was)

    def _note_transition(self, was: str | None) -> None:
        """В журнал — только смена состояния: опрос идёт раз в 30 с."""
        now = self.freshness.error
        if now == was:
            return
        if now is None:
            log.info("свежесть: метки снова читаются")
        else:
            log.warning("свежесть: %s (%s)", ERROR_TEXTS[now], now)

    # --- журнал событий ---

    async def _system_event(self, tool: str, outcome: Outcome, summary: str, sent: tuple[str, ...], rows: int,
                            began: float) -> None:
        """Событие служебного опроса; запись — в потоке, её ошибка опрос не ломает (health покажет).

        Опрос ждёт записи, но блокировку SQLite запись ждёт не дольше WRITE_TIMEOUT_S (0,5 с): дальше — сбой
        записи со счётчиком, а не задержка опроса.
        """
        took = asyncio.get_running_loop().time() - began
        try:
            event = CallEvent(ts=self.wallclock(), caller="system", agent=None, channel="system", tool=tool,
                              requested=(), tags=(), sent=sent, start=None, end=None, outcome=outcome,
                              summary=summary, cost=0.0, cache="none", rows=rows,
                              duration_ms=max(0, round(took * 1000)))
        except Exception as exc:            # зовётся и из except циклов: они не умирают из-за события
            log.error("журнал событий: событие %s не собрано (%s)", tool, type(exc).__name__)
            return
        await self._write_event(event)

    async def _write_event(self, event: CallEvent) -> None:
        try:
            await asyncio.to_thread(self.events.write, event)
        except Exception:
            # в журнал службы — только первая неудача подряд: опрос пишет раз в 30 с
            if self.events.consecutive_failures == 1:
                log.error("журнал событий: запись не удалась (%s)", self.events.last_error)

    async def check_events(self) -> None:
        """Проверка записи журнала — в потоке (F9); health() читает только её итог."""
        await asyncio.to_thread(self.events.check)

    def _agents_writable(self) -> str | None:
        """В потоке: open() (схема идемпотентна) и проба записи с откатом — на файле только для чтения open() проходит.
        None — учёт пишется, иначе причина без пути и значений."""
        if not self.agents.open():
            return self.agents.last_error or "Error"
        try:
            self.agents.probe_write()
        except StoreError as exc:
            return str(exc).removeprefix(STORE_PREFIX)
        return None

    async def check_agents(self) -> None:
        """Учёт агентов — снова open() и проба записи в потоке: удача — store_ok, сбой — store_failed (I-1). В журнал
        службы — только смена «пишется / не пишется». Прежнее состояние читается после потока: сбой save_agent, пришедший
        за время проверки, — тоже смена, и её строка журнала не теряется."""
        try:
            error = await asyncio.to_thread(self._agents_writable)
        except Exception as exc:            # страховка — только класс
            error = type(exc).__name__
        ok = error is None
        was = self.agent_stats.store_error
        if ok:
            self.agent_stats.store_ok()
            if was is not None:
                log.info("учёт агентов: запись снова проходит")
        else:
            self.agent_stats.store_failed(error)
            if was is None:
                log.error("учёт агентов: запись не проходит (%s)", error)

    # --- протокол роли ---

    def health(self) -> tuple[bool, str]:
        """Без ввода-вывода: config_error → пустой белый список → токены → журнал событий → каталог."""
        if self.config_error:
            return False, self.config_error
        if not self.whitelist:
            return False, "белый список пуст или не найден"
        if self.tokens_error:
            return False, self.tokens_error
        events = self.events
        if events.consecutive_failures > 0 or events.writable_ok is False:
            return False, EVENTS_ERROR + (events.last_error or "нет записи")[:EVENTS_ERROR_CHARS]
        if self.catalog.loaded:
            return True, f"каталог: {self.catalog.whitelisted} тегов в белом списке"
        return True, "каталог ещё не загружен"

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/health/historian")
        async def health_historian() -> JSONResponse:
            # без токена: только числа, коды и флаги — имён тегов нет, с Д4 ручку видят сети мест
            mono = self.monotonic()
            body = self.freshness.to_json(mono)
            body["catalog_age_s"] = (None if self.catalog_loaded_mono is None
                                     else round(max(0.0, mono - self.catalog_loaded_mono), 1))
            body["catalog_error"] = self.catalog_error
            body["gate"] = self.gate.stats()
            body["tools"] = self.service.tools_stats()      # из памяти службы, не из журнала событий (К7)
            return JSONResponse(body)

        router.include_router(data_router(self))
        return router

    def install(self, app: FastAPI) -> None:
        """Крючки уровня приложения — через реестр hooks_of(app): 422/401/503 для ошибок разбора /api/data/*.

        Предел тела /api/data/ и /mcp — по умолчанию (64 КиБ): общее звено BodyLimit ставит create_app.
        """
        hooks_of(app).validation("/api/data/", validation_handler(self))

    def mounts(self) -> list[tuple[str, ASGIApp]]:
        """Точный маршрут /mcp без Mount("/"): чужие пути ролей Д4/Д5 он не перехватит, /mcp/ — 404."""
        return [(MCP_PATH, self.mcp_app)]

    def lifespan(self):
        return self._lifespan()

    @asynccontextmanager
    async def _lifespan(self):
        # MCP — самым внешним и до ветки config_error (F8): без run() /mcp отвечает 500 «Task group is
        # not initialized»; run() SDK даёт один раз — роль входит в lifespan однажды
        async with self.mcp.session_manager.run():
            # первым делом — core.db и схема (F23), и при ошибке настройки; сбой — причина здоровья, не падение
            if not await asyncio.to_thread(self.events.open):
                log.error("журнал событий: не открыт (%s)", self.events.last_error)
            # учёт агентов — после журнала (П13): сбой — строка сторожа «Агенты студентов», не config_error
            if await asyncio.to_thread(self.agents.open):
                self.agent_stats.store_ok()
            else:
                self.agent_stats.store_failed(self.agents.last_error or "Error")
                log.error("учёт агентов: не открыт (%s)", self.agents.last_error)
            if self.config_error:
                log.error("служба данных: фоновые циклы не запущены — %s", self.config_error)
                yield
                return
            mode = self.settings.DRILL_FRESHNESS
            if mode:
                log.warning("УЧЕНИЯ: DRILL_FRESHNESS=%s", mode)
            tried = asyncio.Event()
            tasks = [asyncio.create_task(self._catalog_loop(tried), name="data-catalog"),
                     asyncio.create_task(self._freshness_loop(tried), name="data-freshness"),
                     asyncio.create_task(self._events_loop(), name="data-events")]
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _catalog_loop(self, tried: asyncio.Event) -> None:
        while True:
            try:
                ok = await self.refresh_catalog()
            except Exception as exc:        # цикл не умирает; текст исключения может нести имя тега
                log.error("каталог: непредвиденный сбой (%s)", type(exc).__name__)
                self.catalog_error = "query"
                self.catalog_failures += 1
                ok = False
                await self._system_event("catalog", outcome_for("query"),
                                         f"без имён: непредвиденный сбой ({type(exc).__name__})", (), 0,
                                         asyncio.get_running_loop().time())
            finally:
                tried.set()
            await asyncio.sleep(self.next_catalog_delay(ok))

    async def _freshness_loop(self, tried: asyncio.Event) -> None:
        await tried.wait()
        while True:
            try:
                await self.poll_freshness()
            except Exception as exc:
                log.error("свежесть: непредвиденный сбой опроса (%s)", type(exc).__name__)
                await self._system_event("freshness", "error", f"непредвиденный сбой ({type(exc).__name__})",
                                         (), 0, asyncio.get_running_loop().time())
            if self.settings.DRILL_FRESHNESS == "freeze_poll":
                log.warning("УЧЕНИЯ: опрос свежести остановлен после первого")
                return
            await asyncio.sleep(self.settings.FRESH_POLL_S)

    async def _events_loop(self) -> None:
        """Проверка записи журнала и учёта агентов (I-1) сразу и дальше раз в EVENTS_CHECK_S; в журнал службы — только
        смена."""
        while True:
            was = self.events.writable_ok
            try:
                await self.check_events()
            except Exception as exc:
                log.error("журнал событий: непредвиденный сбой проверки (%s)", type(exc).__name__)
            now = self.events.writable_ok
            if now is False and was is not False:
                log.error("журнал событий: запись не проходит (%s)", self.events.last_error)
            elif now and was is False:
                log.info("журнал событий: запись снова проходит")
            await self.check_agents()
            await asyncio.sleep(EVENTS_CHECK_S)


def _row_count(result: list[list]) -> int:
    return sum(len(rows) for rows in result)


def _union(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
    """Имена обоих вызовов без повторов, по порядку."""
    return tuple(dict.fromkeys((*a, *b)))
