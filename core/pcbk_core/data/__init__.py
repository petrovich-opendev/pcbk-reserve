"""Роль «данные»: каталог тегов, свежесть историана, ручки здоровья; Д3б добавит инструменты.

Два фоновых цикла делят одно фоновое место ворот. Каталог грузится сразу при
старте и дальше раз в CATALOG_REFRESH_S, после неудачи — с растущей паузой.
Свежесть опрашивается после первой попытки каталога и дальше раз в
FRESH_POLL_S. Всё состояние меняется только в цикле событий.
"""
import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..app import RoleBase
from ..settings import Settings
from .catalog import Catalog
from .freshness import ERROR_TEXTS, FreshnessTracker
from .gate import GateRefused, HistorianGate
from .historian import HistClock, HistorianError, QueryFn, parse_clock
from .sql import catalog_sql, clock_sql, live_all_sql, live_sql

FRESH_TAGS = 8
BACKOFF_BASE_S = 60.0
BACKOFF_MAX_S = 900.0
# причины «настройка с ошибкой» для /healthz/data: не длиннее 80 знаков, без строк файлов
CONFIG_ERRORS = {
    "bdrv_unreadable": "bdrv.env не читается (нет файла или прав)",
    "bdrv_invalid": "bdrv.env: не заданы BDRV_* или неверная строка",
    "whitelist_unreadable": "белый список не читается",
}

log = logging.getLogger("pcbk_core.data")


class DataRole(RoleBase):
    """config_error — настройка с ошибкой (bdrv.env или белый список не читаются): здоровье — 503 с
    этой причиной, фоновые циклы не запускаются, к историану ни одного входа."""

    name = "data"

    def __init__(self, settings: Settings, query: QueryFn, whitelist: frozenset[str], *,
                 monotonic: Callable[[], float] = time.monotonic,
                 wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 config_error: str | None = None):
        self.settings = settings
        self.config_error = config_error
        self.whitelist = whitelist
        self.monotonic = monotonic
        self.wallclock = wallclock
        self.gate = HistorianGate(query, allowed=whitelist, monotonic=monotonic)
        self.catalog = Catalog.empty()
        self.freshness = FreshnessTracker(freeze_stamps=settings.DRILL_FRESHNESS == "freeze_stamps")
        self.catalog_error: str | None = None
        self.catalog_failures = 0
        self.catalog_loaded_mono: float | None = None
        self.freshness_tags: tuple[str, ...] = ()
        # часы историана и момент их получения: «сейчас историана» для Д3б без лишнего запроса
        self.clock: HistClock | None = None
        self.clock_mono: float | None = None

    # --- каталог ---

    async def refresh_catalog(self) -> bool:
        """Каталог двумя вызовами фоновой полосы с общим сроком CATALOG_DEADLINE_S.

        Срок каждого вызова — остаток общего: драйвер держит место не дольше срока
        своего вызова, так что оба вместе держат фоновое место не дольше
        CATALOG_DEADLINE_S, и окно без опроса свежести укладывается в HIST_STALE_S.
        Неудача оставляет прежний каталог.
        """
        loop = asyncio.get_running_loop()
        end = loop.time() + self.settings.CATALOG_DEADLINE_S
        try:
            first = await self.gate.run([clock_sql(), catalog_sql()], lane="background",
                                        deadline_s=end - loop.time())
            clock_mono = self.monotonic()
            clock = parse_clock(first.rows[0])
            rest = end - loop.time()
            if rest <= 0:
                raise HistorianError("timeout", "срок каталога исчерпан до второго вызова")
            second = await self.gate.run([live_all_sql()], lane="background", deadline_s=rest)
            catalog = Catalog.from_rows(first.rows[1], second.rows[0], self.whitelist)
        except HistorianError as err:
            return self._catalog_failed(err.code, err.public())
        except GateRefused as err:
            # фон не дождался места к сроку: место держит вызов, который историан не дочитал, — это срок
            return self._catalog_failed("timeout" if err.code == "busy" else err.code, f"ворота: {err.code}")
        except (ValueError, TypeError) as err:          # ответ не того вида; текст ответа — не в журнал
            return self._catalog_failed("query", f"ответ не того вида ({type(err).__name__})")
        self.catalog = catalog
        self.clock, self.clock_mono = clock, clock_mono
        self.freshness_tags = catalog.freshest(FRESH_TAGS)
        self.catalog_error = None
        self.catalog_failures = 0
        self.catalog_loaded_mono = self.monotonic()
        log.info("каталог: %d тегов, в белом списке %d, нет в историане %d, живых %d",
                 catalog.total, catalog.whitelisted, catalog.missing, catalog.live)
        return True

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
        tags = self.freshness_tags
        if not self.catalog.loaded:
            # у прочих отказов ворот (unlisted) своего текста нет: для людей это «каталог не загружен»
            code = self.catalog_error if self.catalog_error in ERROR_TEXTS else "catalog"
            self._fail(code, len(tags))
            return
        if not tags:
            self._fail("no_tags", 0)
            return
        try:
            result = await self.gate.run([clock_sql(), live_sql(tags)], lane="background")
            mono = self.monotonic()
            clock = parse_clock(result.rows[0])
        except HistorianError as err:
            self._fail(err.code, len(tags))
            return
        except GateRefused as err:
            # фоновое место занимает только загрузка каталога; её окно укладывается в HIST_STALE_S
            log.info("свежесть: опрос пропущен — ворота: %s", err.code)
            return
        except ValueError:                              # часы не того вида
            self._fail("query", len(tags))
            return
        self.clock, self.clock_mono = clock, mono
        was = self.freshness.error
        self.freshness.observe(clock, result.rows[1], len(tags), mono, self.wallclock())
        self._note_transition(was)

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

    # --- протокол роли ---

    def health(self) -> tuple[bool, str]:
        if self.config_error:
            return False, self.config_error
        if not self.whitelist:
            return False, "белый список пуст или не найден"
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
            return JSONResponse(body)

        return router

    def lifespan(self):
        return self._lifespan()

    @asynccontextmanager
    async def _lifespan(self):
        if self.config_error:
            log.error("служба данных: фоновые циклы не запущены — %s", self.config_error)
            yield
            return
        mode = self.settings.DRILL_FRESHNESS
        if mode:
            log.warning("УЧЕНИЯ: DRILL_FRESHNESS=%s", mode)
        tried = asyncio.Event()
        tasks = [asyncio.create_task(self._catalog_loop(tried), name="data-catalog"),
                 asyncio.create_task(self._freshness_loop(tried), name="data-freshness")]
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
            if self.settings.DRILL_FRESHNESS == "freeze_poll":
                log.warning("УЧЕНИЯ: опрос свежести остановлен после первого")
                return
            await asyncio.sleep(self.settings.FRESH_POLL_S)
