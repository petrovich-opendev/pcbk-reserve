"""Служба данных: инструменты catalog_search, tag_now, tag_period поверх ворот и каталога Д3а.

Порядок в каждом инструменте:
0. ошибка настройки (F5) — ответ unavailable с причиной; ни частоты, ни каталога, ни ворот;
1. частота вызывающего; 2. аргументы; 3. каталог загружен; 4. разбор имён (повторы схлопываются,
   порядок — как во вводе); 5. период и бюджет (tag_period); 6. кэш → single-flight → ворота;
7. ответ → событие → строка журнала.

Любой отказ до ворот SQL не шлёт. Событие пишется на каждый вызов: requested — сырой ввод, tags —
канонические имена со статусами ok/no_value/no_data, sent — только от ворот (GateResult.sent,
HistorianError.sent). У попадания в кэш, у ожидающего single-flight и у отказов sent пуст.

Кэш и single-flight делят один снимок ответа историана (Snapshot): он неизменяем, ответ каждый
вызов собирает заново — по своему разбору имён и своему периоду. Общий объект никто не правит.

«Сейчас историана» — последние часы (каталог, опрос свежести раз в 30 с) плюс прошедшее по
монотонным часам роли; часы старше CLOCK_STALE_S сначала обновляются запросом [clock_sql()].

Исходы последних TOOL_WINDOW вызовов каналов http и mcp служба держит в памяти (tools_stats): ручка
/health/historian идёт в цикле событий, журнал событий там не читается (F9, К7). После перезапуска
окно пустое.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from .budget import PER_CALLER_LIMIT, check_budget, cost_tag_days
from .cache import SingleFlight, TTLCache, cache_key, round_now, ttl_for
from .catalog import Catalog, Lookup, TagInfo
from .events import REQUESTED_LINES, CacheState, CallEvent, Channel, Outcome, outcome_for
from .gate import RATE_WINDOW_S, GateRefused, SlidingWindow
from .historian import HistClock, HistorianError, Row, parse_clock
from .periods import PRESETS, Period, PeriodError, resolve_period
from .sql import HAS_LAST, MAX_NAMES, SUMMARY_COLUMNS, clock_sql, live_sql, summary_sql

if TYPE_CHECKING:
    from . import DataRole

AvgKind = Literal["step", "linear", "arith", "unverified"]
StdKind = Literal["step", "arith", "unverified"]
# вид среднего и σ сводки — по пробе историана (задача 1, шаг 8; R8); до пробы — «не подтверждён»
AVG_KIND: AvgKind = "unverified"
STD_KIND: StdKind = "unverified"
KIND_TEXTS = {"step": "взвешено по времени (ступенчатая интерполяция)",
              "linear": "взвешено по времени (линейная интерполяция)",
              "arith": "арифметическое по сырым значениям",
              "unverified": "вид не подтверждён сверкой"}

SOURCE_LIVE = "текущее значение из таблицы Live историана"
SOURCE_SUMMARY = "сводка историана по сырым данным: AnalogSummaryHistory, одна корзина на весь период"
SHIFT_NOTE = "границы смен 06:00, 14:00, 22:00 — допущение, технологом не подтверждено"
OPEN_NOTE = "период ещё идёт — числа изменятся"
CLAMP_NOTE = "конец периода ограничен текущим временем историана"
NO_LAST_NOTE = "последнее значение за период пока не считается"
TRUNCATED_NOTE = "описания в историане обрезаны на 50 символах"
SIMILAR_NOTE = "точного совпадения нет — вот похожие, выберите вместе со студентом"
ITEM_TEXTS = {"no_value": "нет текущего значения",
              "no_data": "за период данных нет",
              "unlisted": "тег не входит в белый список стенда",
              "unknown": "такого тега нет в каталоге историана",
              "discrete": "дискретный тег: сводка не считается, наработка — в следующих слайсах"}
MESSAGES = {"rate": "слишком часто: не больше 60 вызовов за 5 минут — подождите минуту",
            "global_rate": "общий предел запросов к историану исчерпан — повторите через несколько минут",
            "busy": "историан занят другими запросами — повторите через минуту",
            "timeout": "историан не ответил за 15 с — сократите период или число тегов",
            "catalog": "каталог тегов ещё не загружен",
            "connect": "нет связи с историаном",
            "auth": "историан отклонил учётные данные — сообщите преподавателю",
            "query": "историан вернул ошибку — вызов записан в журнал",
            "none": "ни один тег нельзя запросить",
            "args": "неверные аргументы: "}
# подпись для 1 и для 0; в ответе — с пометкой «по суффиксу»
STATE_LABELS = {"_RUN": ("работает", "стоит"), "_OPN": ("открыт", "не открыт"), "_CLS": ("закрыт", "не закрыт"),
                "_ON": ("включён", "не включён"), "_OFF": ("выключен", "не выключен"),
                "_STOP": ("остановлен", "не остановлен"), "_FLT": ("неисправность", "исправен"),
                "_ALM": ("авария", "нет аварии")}
# непредвиденный сбой самой службы (и отказ ворот unlisted — его быть не должно): своего текста в MESSAGES нет
ERROR_MESSAGE = "сбой службы данных — вызов записан в журнал"
CONFIG_SUMMARY = "ошибка настройки"

CLOCK_STALE_S = 300.0           # часы старше — сначала [clock_sql()]
TAG_CHARS = 128                 # строка тега (Global Constraints)
QUERY_CHARS = 200
SEARCH_LIMIT = 50
TRUNCATED_CHARS = 50            # Description в историане режется на 50 знаках
AHEAD_NOTE_S = 60               # метка впереди часов историана — заметка
STILL_NOTE_S = 600              # значение не менялось — заметка
GOOD_PERCENT = 99.0
CALLER_LOG_CHARS = 64
TOOL_WINDOW = 50                # tools_stats: столько последних вызовов инструментов (http и mcp)

# код ошибки ворот или историана → текст для людей; исход — outcome_for (OUTCOME_BY_CODE, F11)
_CODE_MESSAGES = {"connect": MESSAGES["connect"], "auth": MESSAGES["auth"], "timeout": MESSAGES["timeout"],
                  "query": MESSAGES["query"], "busy": MESSAGES["busy"], "rate": MESSAGES["global_rate"]}
_DATA_STATUSES = frozenset({"ok", "no_value", "no_data"})
# исходы, которые строка журнала пишет на INFO: ответ дан или отказ по правилам; прочие — WARNING
_QUIET_OUTCOMES = frozenset({"ok", "partial", "refused", "budget", "rate"})
# «инструменты отвечают ошибкой» (Д4б-R22): ошибка, срок или недоступность; отказы по правилам — не ошибка
_TOOL_ERRORS = frozenset({"error", "timeout", "unavailable"})
_TOOL_CHANNELS = frozenset({"http", "mcp"})
_CLOCK_FLIGHT = "clock"         # ключ single-flight обновления часов; ключи кэша — sha256, не совпадут

log = logging.getLogger("pcbk_core.data.service")


@dataclass(frozen=True)
class Snapshot:
    """Ответ историана на один запрос — общий для кэша и всех ожидающих single-flight; не правится.

    rows — строка историана по имени тега в верхнем регистре (только для чтения); count — строк
    прочитано; sent — имена от ворот. asof и tz — у tag_now: «сейчас историана» в момент ответа;
    open — у tag_period: период ещё шёл, когда числа читались.
    """

    rows: Mapping[str, Row]
    sent: tuple[str, ...]
    count: int
    asof: datetime | None = None
    tz: str = ""
    open: bool = False


@dataclass(frozen=True)
class _Entry:
    shown: str                  # имя в ответе: каноническое у ok, иначе ввод без пробелов по краям
    lookup: Lookup


@dataclass
class _Call:
    """Поля события и строки журнала одного вызова."""

    caller: str
    channel: Channel
    tool: str
    requested: tuple[str, ...]
    count: int                  # сколько имён (строк) пришло — для строки журнала
    began: float
    tags: tuple[str, ...] = ()
    sent: tuple[str, ...] = ()
    start: datetime | None = None
    end: datetime | None = None
    cost: float = 0.0
    cache: CacheState = "none"
    rows: int = 0
    summary: str = ""
    returned: tuple[str, ...] = ()     # catalog_search: имена из matches ответа


class _Refusal(Exception):
    """Ответ без данных: статус, текст для людей, сырой код — в summary события; extra — поля ответа."""

    def __init__(self, status: Outcome, message: str, summary: str, *, extra: dict[str, Any] | None = None,
                 error: str | None = None):
        super().__init__(status)
        self.status = status
        self.message = message
        self.summary = summary
        self.extra = extra or {}
        self.error = error


class _AfterSend(Exception):
    """Сбой после ответа ворот (ответ не того вида, часы, кэш): SQL уже ушёл — событие ведущего несёт
    sent ворот. Текст исходной ошибки сюда не идёт, только её код или класс."""

    def __init__(self, kind: str, sent: tuple[str, ...]):
        super().__init__(kind)
        self.kind = kind
        self.sent = sent


def requested_of(raw: object) -> tuple[str, ...]:
    """Сырой ввод для события: строка — одна строка, список — строками (не больше 16), None — пусто."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        return tuple(x if isinstance(x, str) else str(x) for x in raw[:REQUESTED_LINES])
    return (str(raw),)


def _count(raw: object) -> int:
    if raw is None:
        return 0
    return len(raw) if isinstance(raw, (list, tuple)) else 1


def _refusal(err: HistorianError | GateRefused) -> _Refusal:
    message = _CODE_MESSAGES.get(err.code, ERROR_MESSAGE)
    # в summary — только код, класс и msg_no (public); detail с текстом SQL Server из процесса не выходит
    summary = err.public() if isinstance(err, HistorianError) else f"ворота: {err.code}"
    return _Refusal(outcome_for(err.code), message, summary)


def _args_refusal(reason: str) -> _Refusal:
    return _Refusal("refused", MESSAGES["args"] + reason, "args: " + reason)


def _tags_problem(tags: object) -> str | None:
    if not isinstance(tags, (list, tuple)):
        return "tags: нужен список имён тегов"
    if not tags:
        return "tags: нужен хотя бы один тег"
    if len(tags) > MAX_NAMES:
        return f"tags: не больше {MAX_NAMES} тегов за вызов"
    if not all(isinstance(t, str) for t in tags):
        return "tags: каждый тег — строка"
    if any(len(t) > TAG_CHARS for t in tags):
        return f"tags: строка тега длиннее {TAG_CHARS} знаков"
    return None


def _period_problem(period: object, start: object, end: object) -> str | None:
    if period is not None and (not isinstance(period, str) or period not in PRESETS):
        return "period: неизвестный готовый период"
    for name, value in (("start", start), ("end", end)):
        if value is not None and not isinstance(value, str):
            return f"{name}: нужна строка ISO 8601"
    return None


def _query_problem(query: object, limit: object) -> str | None:
    if not isinstance(query, str) or not 0 < len(query) <= QUERY_CHARS:
        return f"query: нужна строка от 1 до {QUERY_CHARS} знаков"
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= SEARCH_LIMIT:
        return f"limit: нужно целое от 1 до {SEARCH_LIMIT}"
    return None


def _resolve(catalog: Catalog, tags: Sequence[str]) -> list[_Entry]:
    """Разбор имён: повторы (без регистра и пробелов по краям) схлопываются, порядок — как во вводе."""
    seen: set[str] = set()
    out: list[_Entry] = []
    for raw in tags:
        key = raw.strip().upper()
        if key in seen:
            continue
        seen.add(key)
        found = catalog.lookup(raw)
        out.append(_Entry(found.info.name if found.info is not None else raw.strip(), found))
    return out


def _num(value: object) -> float | None:
    """Число для JSON: NULL, NaN и бесконечность — None (JSON их не держит)."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _aware(value: object, tz: tzinfo) -> datetime | None:
    """Метка историана (pytds отдаёт её без пояса) — с поясом историана; не метка — None."""
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat(timespec="seconds")


def _quality(code: object) -> str | None:
    number = _num(code)
    if number is None:
        return None
    return "хорошее" if number == 0 else f"недостоверное (код {int(number)})"


def _state_label(name: str, value: float | None) -> str | None:
    head, sep, tail = name.rpartition("_")
    labels = STATE_LABELS.get("_" + tail.upper()) if sep and head else None
    if labels is None or value not in (0, 1):
        return None
    return f"{labels[0] if value == 1 else labels[1]} (по суффиксу)"


def _percent(value: float) -> str:
    """Доля вниз до десятой, с запятой: 98,96 — «98,9», а не «99,0 % — ниже 99 %»."""
    return f"{math.floor(value * 10) / 10:g}".replace(".", ",")


class _BadAnswer(ValueError):
    """Ответ историана не того вида; code — для события и журнала (без строк ответа)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _rows_by_name(rows: object, width: int) -> Mapping[str, Row]:
    """Строки ответа по имени тега в верхнем регистре. Не список, строка не той ширины или повтор имени
    — _BadAnswer: какую из двух строк считать ответом, служба не выбирает."""
    if not isinstance(rows, list):
        raise _BadAnswer("not_list")
    out: dict[str, Row] = {}
    for row in rows:
        row = tuple(row)
        if len(row) != width or not isinstance(row[0], str):
            raise _BadAnswer("row_shape")
        key = row[0].upper()
        if key in out:
            raise _BadAnswer("duplicate_tag")
        out[key] = row
    return MappingProxyType(out)


def _after_send(result: Any, keep: Callable[[], Snapshot]) -> Snapshot:
    """Всё, что после ответа ворот (разбор, часы, кэш): сбой — _AfterSend с sent ворот, SQL уже ушёл.

    Ошибка бросается после блока: без исходного исключения в __context__; дальше идёт только код или класс.
    """
    try:
        return keep()
    except Exception as exc:
        failure = _AfterSend(exc.code if isinstance(exc, _BadAnswer) else type(exc).__name__,
                             tuple(getattr(result, "sent", ()) or ()))
    raise failure


# ошибки периода без «сейчас»: resolve_period с «сейчас» так далеко, что проверки против него не срабатывают
_FAR_NOW = datetime(9000, 1, 1)


def _period_syntax(preset: str | None, start: str | None, end: str | None, offset: timedelta) -> str | None:
    """Период задан двумя способами, дата не разобрана, конец раньше начала, короче минуты, раньше 2000
    года — текст отказа; иначе None. Проверки против «сейчас» (начало в будущем, отсечка) — позже."""
    if preset is not None and start is None and end is None:
        return None                         # готовый период: всё, что не зависит от «сейчас», уже проверено
    try:
        resolve_period(_FAR_NOW, offset, preset, start, end)
    except PeriodError as err:
        return str(err)
    return None


def _other_item(entry: _Entry) -> dict[str, Any]:
    """unknown и unlisted: о теге вне белого списка — ни описания, ни единицы, ни шкалы."""
    if entry.lookup.status == "unknown":
        return {"tag": entry.shown, "status": "unknown", "text": ITEM_TEXTS["unknown"],
                "similar": list(entry.lookup.similar)}
    return {"tag": entry.shown, "status": "unlisted", "text": ITEM_TEXTS["unlisted"]}


def _discrete_item(info: TagInfo) -> dict[str, Any]:
    return {"tag": info.name, "status": "discrete", "text": ITEM_TEXTS["discrete"]}


def _counts(items: list[dict[str, Any]]) -> str:
    order = ("ok", "no_value", "no_data", "discrete", "unlisted", "unknown")
    return ", ".join(f"{s} {n}" for s in order if (n := sum(1 for i in items if i["status"] == s)))


def _safe(text: str, limit: int) -> str:
    """Для строки журнала: управляющие и не-ASCII — экранами, не длиннее limit."""
    return ascii(text)[1:-1][:limit]


class DataService:
    """Три инструмента роли «данные». Ворота, каталог, часы, журнал событий и monotonic — из роли
    в момент вызова; конструктор без ввода-вывода."""

    def __init__(self, role: DataRole, *, cache: TTLCache | None = None, flight: SingleFlight | None = None,
                 per_caller: SlidingWindow | None = None):
        self.role = role
        self.cache: TTLCache[Snapshot] = TTLCache() if cache is None else cache
        self.flight: SingleFlight[Any] = SingleFlight() if flight is None else flight
        self.per_caller = SlidingWindow(PER_CALLER_LIMIT, RATE_WINDOW_S) if per_caller is None else per_caller
        # исходы последних вызовов инструментов — только в памяти (К7); пополняет _finish в цикле событий
        self._tool_outcomes: deque[Outcome] = deque(maxlen=TOOL_WINDOW)

    def tools_stats(self) -> dict[str, int]:
        """{"recent": n, "errors": k} по последним TOOL_WINDOW вызовам каналов http и mcp; без ввода-вывода."""
        return {"recent": len(self._tool_outcomes),
                "errors": sum(1 for outcome in self._tool_outcomes if outcome in _TOOL_ERRORS)}

    # --- инструменты ---

    async def catalog_search(self, caller: str, channel: Channel, query: str, limit: int = 10) -> dict[str, Any]:
        if self.role.config_error:          # F5: первая строка — ни частоты, ни каталога, ни ворот
            return await self.config_unavailable(caller, channel, "catalog_search", query)
        call = self._call(caller, channel, "catalog_search", query)
        return await self._run(call, self._catalog_search(call, query, limit))

    async def tag_now(self, caller: str, channel: Channel, tags: list[str]) -> dict[str, Any]:
        if self.role.config_error:          # F5
            return await self.config_unavailable(caller, channel, "tag_now", tags)
        call = self._call(caller, channel, "tag_now", tags)
        return await self._run(call, self._tag_now(call, tags))

    async def tag_period(self, caller: str, channel: Channel, tags: list[str], period: str | None = None,
                         start: str | None = None, end: str | None = None) -> dict[str, Any]:
        if self.role.config_error:          # F5
            return await self.config_unavailable(caller, channel, "tag_period", tags)
        call = self._call(caller, channel, "tag_period", tags)
        return await self._run(call, self._tag_period(call, tags, period, start, end))

    async def refuse_args(self, caller: str, channel: Channel, tool: str, requested: Sequence[str],
                          reason: str) -> dict[str, Any]:
        """Отказ по аргументам, найденный до инструмента (HTTP 422, MCP): событие refused.

        Сразу после ошибки настройки — частота вызывающего: отказ по аргументам — тоже вызов.
        """
        if self.role.config_error:          # F5
            return await self.config_unavailable(caller, channel, tool, requested)
        call = self._call(caller, channel, tool, requested)
        return await self._run(call, self._refuse_args(call, reason))

    async def config_unavailable(self, caller: str, channel: Channel, tool: str,
                                 requested: Sequence[str]) -> dict[str, Any]:
        """Ошибка настройки (F5): unavailable с причиной Д3а; историан не трогается, sent пуст."""
        call = self._call(caller, channel, tool, requested)
        call.summary = CONFIG_SUMMARY
        answer = {"tool": tool, "status": "unavailable", "message": self.role.config_error or "", "notes": [],
                  "cache": "none"}
        return await self._finish(call, answer)

    # --- тела инструментов: отказ — _Refusal, ответ собирает _run ---

    async def _refuse_args(self, call: _Call, reason: str) -> dict[str, Any]:
        self._check_rate(call)
        raise _args_refusal(reason)

    async def _catalog_search(self, call: _Call, query: str, limit: int) -> dict[str, Any]:
        self._check_rate(call)
        reason = _query_problem(query, limit)
        if reason:
            raise _args_refusal(reason)
        catalog = self._loaded_catalog()
        found = catalog.search(query, limit)
        matches = [{"tag": t.name, "description": t.description, "unit": t.unit, "kind": t.kind,
                    "min_eu": _num(t.min_eu), "max_eu": _num(t.max_eu), "live": t.live,
                    "description_truncated": len(t.description) >= TRUNCATED_CHARS} for t in found.matches]
        notes = []
        if found.similar and matches:           # «вот похожие» — только когда их есть что показать
            notes.append(SIMILAR_NOTE)
        if any(m["description_truncated"] for m in matches):
            notes.append(TRUNCATED_NOTE)
        call.summary = f"найдено {found.total}" + (" (похожие)" if found.similar else "")
        call.returned = tuple(m["tag"] for m in matches)
        return {"tool": "catalog_search", "status": "ok", "notes": notes, "cache": "none", "query": query,
                "matches": matches, "similar": found.similar, "total": found.total}

    async def _tag_now(self, call: _Call, tags: list[str]) -> dict[str, Any]:
        self._check_rate(call)
        reason = _tags_problem(tags)
        if reason:
            raise _args_refusal(reason)
        entries = _resolve(self._loaded_catalog(), tags)
        names = tuple(e.lookup.info.name for e in entries if e.lookup.status == "ok")
        if not names:
            items = [_other_item(e) for e in entries]
            raise _Refusal("refused", MESSAGES["none"], "none: " + _counts(items), extra={"items": items})
        key = cache_key("tag_now", names, None, None)
        snap = await self._fetch(call, key, lambda: self._read_live(names, key))
        # «сейчас» — один раз на вызов: у попадания в кэш возраст растёт, asof — момент чтения Live
        now = self.role.clock.aware(self._hist_now())
        items = [self._now_item(e, snap, now) for e in entries]
        call.tags = tuple(i["tag"] for i in items if i["status"] in _DATA_STATUSES)
        call.summary = _counts(items)
        return {"tool": "tag_now", "status": "ok" if all(i["status"] == "ok" for i in items) else "partial",
                "notes": [], "cache": call.cache, "asof": _iso(snap.asof), "tz": snap.tz, "source": SOURCE_LIVE,
                "items": items}

    async def _tag_period(self, call: _Call, tags: list[str], preset: str | None, start: str | None,
                          end: str | None) -> dict[str, Any]:
        self._check_rate(call)
        reason = _tags_problem(tags) or _period_problem(preset, start, end)
        if reason:
            raise _args_refusal(reason)
        catalog = self._loaded_catalog()
        # ошибки периода, которым не нужно «сейчас», — до запроса часов: такой отказ не шлёт SQL
        known = self.role.clock
        reason = _period_syntax(preset, start, end, known.offset if known is not None else timedelta(0))
        if reason:
            raise _args_refusal(reason)
        entries = _resolve(catalog, tags)
        names = tuple(e.lookup.info.name for e in entries
                      if e.lookup.status == "ok" and e.lookup.info.kind == "analog")
        if not names:
            items = [self._period_item(e, None, None) for e in entries]
            raise _Refusal("refused", MESSAGES["none"], "none: " + _counts(items), extra={"items": items})
        try:
            clock = await self._fresh_clock()
        except (HistorianError, GateRefused) as err:
            call.sent = err.sent if isinstance(err, HistorianError) else ()      # у часов имён нет
            raise _refusal(err) from None
        # «сейчас» округляется один раз: то же now_r — в resolve_period и в ttl_for (F21)
        now_r = round_now(self._hist_now())
        try:
            period = resolve_period(now_r, clock.offset, preset, start, end)
        except PeriodError as err:
            raise _args_refusal(str(err)) from None
        call.start, call.end = clock.aware(period.start), clock.aware(period.end)
        call.cost = cost_tag_days(len(names), period.start, period.end)
        # бюджет — до summary_sql на каждом пути: ворота сверяют форму SQL, а не длину окна
        problem = check_budget(len(names), period.start, period.end)
        if problem is not None:
            raise _Refusal("budget", problem, "budget",
                           extra={"period": self._period_json(period, clock, period.open),
                                  "cost_tag_days": round(call.cost, 3)})
        key = cache_key("tag_period", names, period.start, period.end)
        ttl = ttl_for("tag_period", period.end, now_r)
        snap = await self._fetch(call, key, lambda: self._read_summary(names, period, key, ttl))
        items = [self._period_item(e, snap, clock.tz) for e in entries]
        # числа читались, пока период шёл, — заметка остаётся и у попадания в кэш чуть позже
        is_open = period.open or snap.open
        notes = []
        if period.shift_assumed:
            notes.append(SHIFT_NOTE)
        if is_open:
            notes.append(OPEN_NOTE)
        if period.clamped:
            notes.append(CLAMP_NOTE)
        if not HAS_LAST:
            notes.append(NO_LAST_NOTE)
        call.tags = tuple(i["tag"] for i in items if i["status"] in _DATA_STATUSES)
        call.summary = f"{period.preset or 'свой период'}: {_counts(items)}"
        return {"tool": "tag_period", "status": "ok" if all(i["status"] == "ok" for i in items) else "partial",
                "notes": notes, "cache": call.cache, "period": self._period_json(period, clock, is_open),
                "source": SOURCE_SUMMARY, "avg_kind": {"code": AVG_KIND, "text": KIND_TEXTS[AVG_KIND]},
                "std_kind": {"code": STD_KIND, "text": KIND_TEXTS[STD_KIND]},
                "cost_tag_days": round(call.cost, 3), "items": items}

    # --- шаги ---

    def _check_rate(self, call: _Call) -> None:
        if not self.per_caller.allow(call.caller, self.role.monotonic()):
            raise _Refusal("rate", MESSAGES["rate"], "rate: частота вызывающего")

    def _loaded_catalog(self) -> Catalog:
        """До загрузки каталог отвечает unknown на любое имя — поэтому сначала loaded."""
        catalog = self.role.catalog
        if not catalog.loaded:
            raise _Refusal("unavailable", MESSAGES["catalog"], "catalog")
        return catalog

    async def _fresh_clock(self) -> HistClock:
        """Часы историана; старше CLOCK_STALE_S — сначала [clock_sql()], один запрос на всех ждущих.

        Ошибки ворот и историана идут наверх как есть: их переводит в ответ вызывающий.
        """
        role = self.role
        if role.clock is None or role.clock_mono is None or role.monotonic() - role.clock_mono > CLOCK_STALE_S:
            await self.flight.run(_CLOCK_FLIGHT, self._read_clock)
        return role.clock

    async def _read_clock(self) -> None:
        result = await self.role.gate.run([clock_sql()])
        try:
            clock = parse_clock(result.rows[0])
        except (ValueError, TypeError, IndexError):
            clock = None
        if clock is None:                   # ошибка — после блока: без ответа историана в __context__
            raise HistorianError("query", "часы историана не того вида", result.sent)
        self.role.clock, self.role.clock_mono = clock, self.role.monotonic()

    def _hist_now(self) -> datetime:
        """«Сейчас историана», местное без пояса: часы плюс прошедшее по monotonic роли."""
        role = self.role
        return role.clock.local + timedelta(seconds=max(0.0, role.monotonic() - role.clock_mono))

    async def _fetch(self, call: _Call, key: str, read: Callable[[], Awaitable[Snapshot]]) -> Snapshot:
        """Кэш → single-flight → ворота. sent и rows — только у ведущего: у попадания и у ожидающих пусто."""
        hit = self.cache.get(key, self.role.monotonic())
        if hit is not None:
            call.cache = "hit"
            return hit
        led = False

        def lead() -> Awaitable[Snapshot]:
            nonlocal led                    # зовёт только ведущий полёта
            led = True
            return read()

        try:
            snap, _ = await self.flight.run(key, lead)
        except (HistorianError, GateRefused) as err:
            # HistorianError.sent — имена, переданные драйверу и при сбое после передачи (F12)
            call.sent = err.sent if led and isinstance(err, HistorianError) else ()
            raise _refusal(err) from None
        except _AfterSend as err:
            call.sent = err.sent if led else ()
            raise _Refusal("error", ERROR_MESSAGE, f"сбой после ответа историана ({err.kind})",
                           error=err.kind) from None
        finally:
            call.cache = "miss" if led else "shared"
        if led:
            call.sent, call.rows = snap.sent, snap.count
        return snap

    async def _read_live(self, names: tuple[str, ...], key: str) -> Snapshot:
        """Работа полёта tag_now: Live по именам; удачный снимок — в кэш на TTL_NOW_S."""
        await self._fresh_clock()
        result = await self.role.gate.run([live_sql(names)])

        def keep() -> Snapshot:
            clock = self.role.clock                 # опрос свежести мог обновить часы, пока шёл запрос
            asof = clock.aware(self._hist_now())    # момент чтения Live
            snap = Snapshot(rows=_rows_by_name(result.rows[0], 4), sent=tuple(result.sent),
                            count=len(result.rows[0]), asof=asof, tz=clock.tz_label)
            self.cache.put(key, snap, ttl_for("tag_now", None, asof), self.role.monotonic())
            return snap

        return _after_send(result, keep)

    async def _read_summary(self, names: tuple[str, ...], period: Period, key: str, ttl: float) -> Snapshot:
        """Работа полёта tag_period: сводка одной корзиной; удачный снимок — в кэш на ttl."""
        if check_budget(len(names), period.start, period.end) is not None:   # защита: только после бюджета
            raise RuntimeError("сводка без проверки бюджета")
        result = await self.role.gate.run([summary_sql(names, period.start, period.end)])

        def keep() -> Snapshot:
            snap = Snapshot(rows=_rows_by_name(result.rows[0], 1 + len(SUMMARY_COLUMNS)), sent=tuple(result.sent),
                            count=len(result.rows[0]), open=period.open)
            self.cache.put(key, snap, ttl, self.role.monotonic())
            return snap

        return _after_send(result, keep)

    # --- сборка ответа ---

    @staticmethod
    def _now_item(entry: _Entry, snap: Snapshot, now: datetime) -> dict[str, Any]:
        """age_s и «не менялось» — от now этого вызова; «метка впереди» — от snap.asof, момента чтения."""
        if entry.lookup.status != "ok":
            return _other_item(entry)
        info = entry.lookup.info
        row = snap.rows.get(info.name.upper())
        tz = snap.asof.tzinfo
        when = _aware(row[1], tz) if row else None
        value = _num(row[2]) if row else None
        status = "ok" if value is not None else "no_value"
        item: dict[str, Any] = {"tag": info.name, "status": status}
        if status != "ok":
            item["text"] = ITEM_TEXTS["no_value"]
        age = None
        notes: list[str] = []
        if when is not None:
            age = max(0, int((now - when).total_seconds()))
            ahead = (when - snap.asof).total_seconds()
            if ahead > AHEAD_NOTE_S:
                notes.append(f"метка тега впереди часов историана на {round(ahead)} с")
            if status == "ok" and age > STILL_NOTE_S:
                notes.append(f"значение не менялось {age // 60} мин: это и ровный процесс, и возможное "
                             "залипание — смотрите качество")
        item.update(description=info.description, unit=info.unit, kind=info.kind, value=value, time=_iso(when),
                    age_s=age, quality=_quality(row[3]) if row else None)
        if info.kind == "discrete":
            item["state_label"] = _state_label(info.name, value)
        item["item_notes"] = notes
        return item

    @staticmethod
    def _period_item(entry: _Entry, snap: Snapshot | None, tz: tzinfo | None) -> dict[str, Any]:
        if entry.lookup.status != "ok":
            return _other_item(entry)
        info = entry.lookup.info
        if info.kind == "discrete":
            return _discrete_item(info)
        row = snap.rows.get(info.name.upper()) if snap is not None else None
        v = dict(zip(SUMMARY_COLUMNS, row[1:])) if row else {}
        avg, low, high = _num(v.get("Average")), _num(v.get("Minimum")), _num(v.get("Maximum"))
        good = _num(v.get("PercentGood"))
        if avg is None or low is None or high is None or good == 0:
            return {"tag": info.name, "status": "no_data", "text": ITEM_TEXTS["no_data"]}
        item: dict[str, Any] = {"tag": info.name, "status": "ok", "description": info.description,
                                "unit": info.unit, "avg": avg, "min": low, "max": high, "range": high - low,
                                "std": _num(v.get("StdDev")), "percent_good": good}
        if HAS_LAST:
            item["last"] = _num(v.get("Last"))
            item["last_time"] = _iso(_aware(v.get("LastDateTime"), tz)) if tz is not None else None
        if good is not None and good < GOOD_PERCENT:
            item["quality_note"] = f"доля достоверных {_percent(good)} % — ниже 99 %"
        return item

    @staticmethod
    def _period_json(period: Period, clock: HistClock, is_open: bool) -> dict[str, Any]:
        return {"start": _iso(clock.aware(period.start)), "end": _iso(clock.aware(period.end)),
                "tz": clock.tz_label, "preset": period.preset, "shift_grid_assumed": period.shift_assumed,
                "clamped": period.clamped, "open": is_open}

    # --- событие и строка журнала ---

    def _call(self, caller: str, channel: Channel, tool: str, raw: object) -> _Call:
        return _Call(caller=str(caller), channel=channel, tool=tool, requested=requested_of(raw),
                     count=_count(raw), began=time.monotonic())

    async def _run(self, call: _Call, body: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        """Тело инструмента → ответ; отказ и непредвиденное исключение — тоже ответ и событие."""
        error = None
        try:
            answer = await body
        except _Refusal as r:
            call.summary, error = r.summary, r.error
            answer = {"tool": call.tool, "status": r.status, "message": r.message, "notes": [],
                      "cache": call.cache, **r.extra}
        except Exception as exc:
            error = type(exc).__name__          # текст исключения может нести имя тега — только класс
            call.summary = f"непредвиденный сбой ({error})"
            answer = {"tool": call.tool, "status": "error", "message": ERROR_MESSAGE, "notes": [],
                      "cache": call.cache}
        return await self._finish(call, answer, error)

    async def _finish(self, call: _Call, answer: dict[str, Any], error: str | None = None) -> dict[str, Any]:
        """Событие — в потоке (asyncio.to_thread роли), SQLite не в цикле; сбой записи ответ не ломает."""
        if call.channel in _TOOL_CHANNELS:
            self._tool_outcomes.append(answer["status"])
        event = self._event(call, answer["status"])
        if event is not None:
            await self.role._write_event(event)
        self._log(call, answer["status"], error)
        return answer

    def _event(self, call: _Call, outcome: Outcome) -> CallEvent | None:
        try:
            return CallEvent(ts=self.role.wallclock(), caller=call.caller, agent=None, channel=call.channel,
                             tool=call.tool, requested=call.requested, tags=call.tags, sent=call.sent,
                             start=call.start, end=call.end, outcome=outcome, summary=call.summary,
                             cost=call.cost, cache=call.cache, rows=call.rows, duration_ms=self._ms(call),
                             returned=call.returned)
        except Exception as exc:
            log.error("журнал событий: событие %s не собрано (%s)", call.tool, type(exc).__name__)
            return None

    @staticmethod
    def _ms(call: _Call) -> int:
        return max(0, round((time.monotonic() - call.began) * 1000))

    def _log(self, call: _Call, outcome: str, error: str | None = None) -> None:
        """Строка журнала без значений и без имён тегов: только числа, коды и границы периода."""
        period = "-"
        if call.start is not None and call.end is not None:
            period = f"{call.start.isoformat()}..{call.end.isoformat()}"
        if error:
            level = logging.ERROR
        elif outcome in _QUIET_OUTCOMES:
            level = logging.INFO
        else:
            level = logging.WARNING
        log.log(level, "tool=%s caller=%s channel=%s tags=%d period=%s cost=%.3f cache=%s rows=%d ms=%d "
                "outcome=%s%s", call.tool, _safe(call.caller, CALLER_LOG_CHARS), call.channel, call.count,
                period, call.cost, call.cache, call.rows, self._ms(call), outcome,
                f" error={error}" if error else "")
