"""report_event службы данных (Д13): агент студента записывает найденное — отклонение измерения от задания
регулятора или заметку — в новую таблицу agent_findings (agents/findings.py); GET /pcbk/api/events шлюза читает те
же строки. Только MCP, без HTTP-ручки, как save_agent.

Порядок вызова (как у save_agent, Д7-R57):
1. ошибка настройки роли (F5) — unavailable с причиной; ни частоты, ни проверок полей, ни записи;
2. частота: не больше REPORT_LIMIT вызовов за REPORT_WINDOW_S на вызывающего — считаются все вызовы, и отказы
   тоже (тот же принцип, что у save_agent); сверх — rate;
2а. вызывающий не student-01…10 — refused;
3. поля: agent — имя живой (не archived) строки учёта места этого вызывающего, иначе agent_unknown; kind —
   deviation или note; title и text — одной строкой в пределах (та же проверка, что у полей черновика,
   agents.draft.one_line_problem); tags — 1…4 тега белого списка/каталога (как catalog.lookup у инструментов
   данных, status == "ok"), каталог не загружен — unavailable; period_start/period_end — None или ISO 8601 с
   поясом; value/setpoint — None или конечное число (не NaN/inf, не bool). Первое найденное замечание — отказ
   (одно действие агента, не многошаговая форма — списка problems, в отличие от save_agent, нет);
4. запись agent_findings (AgentFindings.add) в потоке; событие agent_events (tool=report_event, agent=…) и строка
   журнала — как у прочих инструментов, без текста находки (title, text, теги — только в agent_findings).
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..agents.draft import clean_string, one_line_problem
from ..agents.findings import FindingsError
from ..agents.store import StoreError, place_of_caller
from .events import CallEvent, Outcome
from .gate import SlidingWindow
from .service import ERROR_MESSAGE, MESSAGES as DATA_MESSAGES

if TYPE_CHECKING:
    from . import DataRole

TOOL = "report_event"
REPORT_LIMIT = 30
REPORT_WINDOW_S = 600.0
KINDS = ("deviation", "note")
TITLE_LIMITS = (3, 120)
TEXT_LIMITS = (10, 600)
MIN_TAGS, MAX_TAGS = 1, 4
# текст читает модель
REPORT_DESCRIPTION = ("Записывает событие, найденное агентом: deviation — отклонение измерения от задания "
                      "регулятора, note — заметка. agent — ваше имя в системе (его дала служба после сохранения "
                      "агента, см. файл агента). tags — теги узла, к которым относится событие (1–4, точно как их "
                      "вернул catalog_search). period_start/period_end — ISO 8601 с часовым поясом, если событие "
                      "относится к периоду. value/setpoint — число или null.")
REPORT_PROPERTIES: dict[str, dict[str, Any]] = {
    "agent": {"type": "string", "description": "Ваше имя в системе (как в my_agents и в файле агента)"},
    "kind": {"type": "string", "enum": list(KINDS), "description": "deviation — отклонение от задания, note — заметка"},
    "title": {"type": "string", "minLength": TITLE_LIMITS[0], "maxLength": TITLE_LIMITS[1],
              "description": "Заголовок события одной строкой"},
    "tags": {"type": "array", "minItems": MIN_TAGS, "maxItems": MAX_TAGS, "items": {"type": "string"},
             "description": "Теги узла (1–4), к которым относится событие — точно как их вернул catalog_search"},
    "period_start": {"type": "string", "description": "Начало периода события, ISO 8601 с часовым поясом"},
    "period_end": {"type": "string", "description": "Конец периода события, ISO 8601 с часовым поясом"},
    "value": {"type": "number", "description": "Измерение в точке наибольшего отклонения, если есть"},
    "setpoint": {"type": "number", "description": "Задание регулятора там же, если есть"},
    "text": {"type": "string", "minLength": TEXT_LIMITS[0], "maxLength": TEXT_LIMITS[1],
             "description": "Текст события одной строкой"},
}
REPORT_REQUIRED = ("agent", "kind", "title", "tags", "text")
REPORT_MESSAGES = {
    "ok": "Событие записано.",
    "not_student": "Событие записывает только рабочее место студента",
    "rate": "Слишком часто — подождите несколько минут",
    "agent_unknown": "Такого агента у вас нет",
    "kind": 'kind — "deviation" или "note"',
    "title": f"Заголовок — одной строкой, от {TITLE_LIMITS[0]} до {TITLE_LIMITS[1]} знаков",
    "tags_count": f"Тегов — от {MIN_TAGS} до {MAX_TAGS}, список строк",
    "tag_unknown": "Тег не входит в белый список стенда",
    "period": "period_start и period_end — ISO 8601 с часовым поясом или null",
    "number": "value и setpoint — число или null",
    "text": f"Текст — одной строкой, от {TEXT_LIMITS[0]} до {TEXT_LIMITS[1]} знаков",
    "catalog": DATA_MESSAGES["catalog"],
    "store": "Находки агентов недоступны — сообщите преподавателю",
    "error": ERROR_MESSAGE,
}
_LEVELS = {"ok": logging.INFO, "refused": logging.INFO, "rate": logging.INFO, "unavailable": logging.WARNING,
          "error": logging.ERROR}

log = logging.getLogger("pcbk_core.data.report_event")


def _finite(value: object) -> bool:
    """value/setpoint: None или конечное число (bool — не число, NaN/inf — отказ)."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _iso_with_tz(value: object) -> bool:
    """period_start/period_end: None или строка ISO 8601 с часовым поясом."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


@dataclass
class _Outcome:
    status: Outcome
    code: str | None
    message: str
    extra: dict[str, Any] = field(default_factory=dict)
    agent: str | None = None
    tags: tuple[str, ...] = ()

    def answer(self) -> dict[str, Any]:
        out = {"tool": TOOL, "status": self.status, "message": self.message, "notes": []}
        out.update(self.extra)
        return out

    def summary(self) -> str:
        if self.status == "ok":
            return f"ok: id={self.extra.get('id')}"
        if self.status == "rate":
            return "rate"
        return f"{self.status}: {self.code}"


class ReportEvent:
    """Инструмент report_event роли «данные». Каталог, учёт агентов и находки (role.catalog/agents/findings) — из
    роли в момент вызова; конструктор без ввода-вывода. window — частота на вызывающего."""

    def __init__(self, role: "DataRole", *, window: SlidingWindow | None = None):
        self.role = role
        self.window = SlidingWindow(REPORT_LIMIT, REPORT_WINDOW_S) if window is None else window

    async def call(self, caller: str, raw: Mapping[str, object]) -> dict[str, Any]:
        began = self.role.monotonic()
        caller = str(caller)
        try:
            result = await self._call(caller, raw)
        except Exception as exc:                 # текст исключения может нести значения полей — только класс
            result = _Outcome("error", type(exc).__name__, REPORT_MESSAGES["error"])
        await self._finish(caller, result, began)
        return result.answer()

    async def _call(self, caller: str, raw: Mapping[str, object]) -> _Outcome:
        role = self.role
        if role.config_error:                                          # 1. F5
            return _Outcome("unavailable", "config", role.config_error)
        if not self.window.allow(caller, role.monotonic()):            # 2. частота — все вызовы, и отказы тоже
            return _Outcome("rate", "rate", REPORT_MESSAGES["rate"])
        place = place_of_caller(caller)                                 # 2а. место — только из токена
        if place is None:
            return _Outcome("refused", "not_student", REPORT_MESSAGES["not_student"])
        raw_agent = raw.get("agent")
        agent = raw_agent if isinstance(raw_agent, str) else None

        def refuse(code: str) -> _Outcome:
            return _Outcome("refused", code, REPORT_MESSAGES[code], agent=agent)

        try:
            rows = await asyncio.to_thread(role.agents.list_place, place)
        except StoreError:
            return _Outcome("unavailable", "store_error", REPORT_MESSAGES["store"], agent=agent)
        if agent is None or agent not in {row.name for row in rows}:
            return refuse("agent_unknown")
        kind = raw.get("kind")
        if kind not in KINDS:
            return refuse("kind")
        title = clean_string(raw.get("title"))
        if title is None:
            return refuse("title")
        title = title.strip()
        if one_line_problem(title, *TITLE_LIMITS):
            return refuse("title")
        tags_raw = raw.get("tags")
        if (not isinstance(tags_raw, list) or not MIN_TAGS <= len(tags_raw) <= MAX_TAGS
                or not all(isinstance(t, str) for t in tags_raw)):
            return refuse("tags_count")
        catalog = role.catalog
        if not catalog.loaded:
            return _Outcome("unavailable", "catalog_not_loaded", REPORT_MESSAGES["catalog"], agent=agent)
        tags: list[str] = []
        for item in tags_raw:
            found = catalog.lookup(item)
            if found.status != "ok":
                return refuse("tag_unknown")
            if found.info.name not in tags:
                tags.append(found.info.name)
        period_start, period_end = raw.get("period_start"), raw.get("period_end")
        if not _iso_with_tz(period_start) or not _iso_with_tz(period_end):
            return refuse("period")
        value, setpoint = raw.get("value"), raw.get("setpoint")
        if not _finite(value) or not _finite(setpoint):
            return refuse("number")
        text = clean_string(raw.get("text"))
        if text is None:
            return refuse("text")
        text = text.strip()
        if one_line_problem(text, *TEXT_LIMITS):
            return refuse("text")
        now = role.wallclock()
        try:
            row_id = await asyncio.to_thread(role.findings.add, place, agent, kind, title, tags, period_start,
                                             period_end, _as_float(value), _as_float(setpoint), text, now)
        except FindingsError:
            return _Outcome("unavailable", "store_error", REPORT_MESSAGES["store"], agent=agent, tags=tuple(tags))
        return _Outcome("ok", None, REPORT_MESSAGES["ok"], {"id": row_id}, agent=agent, tags=tuple(tags))

    async def _finish(self, caller: str, result: _Outcome, began: float) -> None:
        role = self.role
        ms = max(0, round((role.monotonic() - began) * 1000))
        try:
            event = CallEvent(ts=role.wallclock(), caller=caller, agent=result.agent, channel="mcp", tool=TOOL,
                              requested=(), tags=result.tags, sent=(), start=None, end=None, outcome=result.status,
                              summary=result.summary(), cost=0.0, cache="none", rows=0, duration_ms=ms)
        except Exception as exc:
            log.error("журнал событий: событие %s не собрано (%s)", TOOL, type(exc).__name__)
        else:
            await role._write_event(event)
        log.log(_LEVELS.get(result.status, logging.WARNING),
                "tool=%s caller=%s channel=mcp outcome=%s code=%s ms=%d", TOOL, caller, result.status,
                result.code or "-", ms)


def _as_float(value: object) -> float | None:
    return None if value is None else float(value)
