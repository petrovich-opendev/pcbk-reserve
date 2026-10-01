"""save_agent службы данных (Д7-R57): черновик агента студента → проверка (А2) → файл по шаблону v1 в каталоге места
вызывающего и строка учёта (А3) → событие и счётчики (А1). Только MCP, без HTTP-ручки (Д7-R53).

Порядок вызова (Д7-R57, m24):
1. ошибка настройки роли (F5) — unavailable с причиной;
2. частота: не больше SAVE_LIMIT вызовов за SAVE_WINDOW_S на вызывающего — считаются все вызовы, и отказы тоже;
   сверх — rate;
2а. вызывающий не student-01…10 (ops и прочие) — refused «Агента сохраняет только рабочее место студента»;
3. check_draft (все замечания сразу); теги при незагруженном каталоге — unavailable;
4. файл по шаблону — вне замка; замок места (asyncio.Lock, создаётся при первом вызове места);
5. AgentStore.save в потоке под замком: повтор того же черновика — already, другое имя занято — name_taken.

Место — только из вызывающего (токен, Ш25): в аргументах места нет, лишние ключи FastMCP отбрасывает до функции.
Отказ — обычный результат, не isError (research/09 §4.3). Каждый вызов — событие agent_events и строка журнала
«tool=save_agent caller=… channel=mcp outcome=… codes=… ms=N»: без имени агента, тегов, текста полей и путей —
имя агента хранится только в SQLite (agents, agent_events.agent).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..agents.draft import (AGENT_TOOLS, MAX_TAGS, NAME_MAX, NAME_MIN, PROMPT_MAX, PROMPT_MIN, TEXT_LIMITS, Draft,
                            Problem, check_draft, render_agent_file)
from ..agents.store import DirUnavailable, NameTaken, StoreError, TooManyAgents, place_of_caller
from .events import CallEvent, Outcome
from .gate import SlidingWindow
from .service import ERROR_MESSAGE, MESSAGES, TAG_CHARS, requested_of

if TYPE_CHECKING:
    from . import DataRole

TOOL = "save_agent"
SAVE_LIMIT = 20
SAVE_WINDOW_S = 600.0
# research/09 §4.1, дословно; его читает модель, текст постоянный
SAVE_DESCRIPTION = ("Сохраняет агента студента после его явного согласия на последний показанный черновик. Служба "
                    "проверяет черновик и пишет файл агента сама; права и правила стенда добавляет она же. Агент "
                    "появится в „Мои агенты“ после окончания этого ответа. При отказе вернётся список problems — "
                    "исправьте и повторите не больше двух раз.")
SAVE_REQUIRED = ("name", "title", "node", "purpose", "tools", "tags", "prompt", "student_reply")
# схема аргументов для модели — research/09 §4.1 без mode и steps (Д7-R54): пределы она только показывает, проверяет
# служба (check_draft); числа — из draft.py, одно место правды
SAVE_PROPERTIES: dict[str, dict[str, Any]] = {
    "name": {"type": "string", "minLength": NAME_MIN, "maxLength": NAME_MAX, "pattern": "^[a-z][a-z0-9]*(-[a-z0-9]+)*$",
             "description": "Имя для системы: строчные латинские буквы, цифры, дефисы; 2–4 слова, "
                            "например sushka-bdm2"},
    "title": {"type": "string", "minLength": TEXT_LIMITS["title"][0], "maxLength": TEXT_LIMITS["title"][1],
              "description": "Название по-русски для списка «Мои агенты»"},
    "node": {"type": "string", "minLength": TEXT_LIMITS["node"][0], "maxLength": TEXT_LIMITS["node"][1],
             "description": "Узел или участок, как его назвал студент"},
    "purpose": {"type": "string", "minLength": TEXT_LIMITS["purpose"][0], "maxLength": TEXT_LIMITS["purpose"][1],
                "description": "Назначение агента одной фразой"},
    "tools": {"type": "array", "minItems": 1, "maxItems": len(AGENT_TOOLS), "uniqueItems": True,
              "items": {"type": "string", "enum": list(AGENT_TOOLS)},
              "description": "Что агент умеет; только эти имена"},
    "tags": {"type": "array", "minItems": 0, "maxItems": MAX_TAGS, "uniqueItems": True,
             "items": {"type": "string", "minLength": 1, "maxLength": TAG_CHARS},
             "description": "Теги узла точно как их вернул catalog_search"},
    "prompt": {"type": "string", "minLength": PROMPT_MIN, "maxLength": PROMPT_MAX,
               "description": "Инструкция агента по-русски; правила стенда и описания тегов служба добавит сама"},
    "student_reply": {"type": "string", "minLength": TEXT_LIMITS["student_reply"][0],
                      "maxLength": TEXT_LIMITS["student_reply"][1],
                      "description": "Дословный ответ студента с согласием на последний черновик"},
}
# тексты ответа (Д7-R57); их читает модель. refused собирает refused_message, каталог — MESSAGES["catalog"] службы
SAVE_MESSAGES = {
    "ok": "Агент сохранён. Он появится в «Мои агенты» после окончания этого ответа.",
    "refused": "Черновик не сохранён: {n} {word}",
    "not_student": "Агента сохраняет только рабочее место студента",
    "rate": "Слишком часто — подождите несколько минут",
    "dir": "Каталог агентов недоступен — сообщите преподавателю",
    "store": "Учёт агентов недоступен — сообщите преподавателю",
    "error": ERROR_MESSAGE,
}
# уровень строки журнала: ответ дан или отказ по правилам — INFO, недоступность — WARNING, сбой — ERROR (как служба)
_LEVELS = {"ok": logging.INFO, "refused": logging.INFO, "rate": logging.INFO, "unavailable": logging.WARNING,
           "error": logging.ERROR}
# счётчик AgentStats по исходу (у ok — только без already; у rate и error счётчика нет, Д7-R57)
_COUNTERS = {"refused": "refused", "unavailable": "unavailable"}
CALLER_LOG_CHARS = 64

log = logging.getLogger("pcbk_core.data.save_agent")


def refused_message(n: int) -> str:
    """«Черновик не сохранён: N замечание/замечания/замечаний» — по-русски с числом."""
    if n % 10 == 1 and n % 100 != 11:
        word = "замечание"
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = "замечания"
    else:
        word = "замечаний"
    return SAVE_MESSAGES["refused"].format(n=n, word=word)


@dataclass
class _Result:
    """Итог вызова: статус ответа, коды (в summary события и в строку журнала), текст и замечания для модели."""

    status: Outcome
    codes: tuple[str, ...]
    message: str
    problems: list[Problem] = field(default_factory=list)
    draft: Draft | None = None
    already: bool = False

    def answer(self) -> dict[str, object]:
        out: dict[str, object] = {"tool": TOOL, "status": self.status, "message": self.message,
                                  "problems": [p.to_json() for p in self.problems], "notes": []}
        if self.status == "ok" and self.draft is not None:
            out.update(name=self.draft.name, title=self.draft.title, already=self.already)
        return out

    def summary(self) -> str:
        """saved | already | refused: <коды> | <исход>: <код>; признаки черновика — через «; flags: …» (Д7-R57)."""
        if self.status == "ok":
            text = "already" if self.already else "saved"
        elif self.status == "rate":
            text = "rate"
        else:
            text = f"{self.status}: {', '.join(self.codes)}"
        flags = self.draft.flags if self.draft is not None else ()
        return text + (f"; flags: {', '.join(flags)}" if flags else "")

    def log_codes(self) -> str:
        codes = (("already" if self.already else "saved"),) if self.status == "ok" else self.codes
        flags = self.draft.flags if self.draft is not None else ()
        return ",".join((*codes, *flags)) or "-"


def _refused(problems: list[Problem], draft: Draft | None = None) -> _Result:
    codes = tuple(dict.fromkeys(p.code for p in problems))
    return _Result("refused", codes, refused_message(len(problems)), problems, draft)


class SaveAgent:
    """Инструмент save_agent роли «данные». Каталог, учёт агентов, счётчики, часы и журнал событий — из роли в момент
    вызова; конструктор без ввода-вывода. window — частота на вызывающего; замки мест — по номеру места."""

    def __init__(self, role: DataRole, *, window: SlidingWindow | None = None):
        self.role = role
        self.window = SlidingWindow(SAVE_LIMIT, SAVE_WINDOW_S) if window is None else window
        self._locks: dict[int, asyncio.Lock] = {}

    async def call(self, caller: str, raw: Mapping[str, object]) -> dict[str, object]:
        """Д7-R57: ответ research/09 §4.3; отказ и сбой — тоже ответ, событие и строка журнала."""
        began = self.role.monotonic()
        try:
            result = await self._call(str(caller), raw)
        except Exception as exc:            # текст исключения может нести имя агента или тега — только класс
            result = _Result("error", (type(exc).__name__,), SAVE_MESSAGES["error"])
        await self._finish(str(caller), raw, result, began)
        return result.answer()

    async def _call(self, caller: str, raw: Mapping[str, object]) -> _Result:
        role = self.role
        if role.config_error:                                           # 1. F5
            return _Result("unavailable", ("config",), role.config_error)
        if not self.window.allow(caller, role.monotonic()):            # 2. частота — все вызовы, и отказы тоже
            return _Result("rate", ("rate",), SAVE_MESSAGES["rate"])
        place = place_of_caller(caller)                                 # 2а. место — только из токена
        if place is None:
            return _Result("refused", ("not_student",), SAVE_MESSAGES["not_student"])
        catalog = role.catalog                                          # 3. один снимок на проверку и шаблон
        draft, problems = check_draft(raw, catalog.lookup if catalog.loaded else None)
        if any(p.code == "catalog_not_loaded" for p in problems):
            others = [p for p in problems if p.code != "catalog_not_loaded"]
            return _Result("unavailable", ("catalog_not_loaded",), MESSAGES["catalog"], others)
        if problems or draft is None:
            return _refused(problems)
        infos = {tag: info for tag in draft.tags if (info := catalog.lookup(tag).info) is not None}
        now = role.wallclock()
        try:                                                            # 4. файл — вне замка
            text = render_agent_file(draft, infos, caller=caller, created_at=now)
        except ValueError:                                  # больше MAX_FILE_BYTES и т. п. — не запись (Д7-R55)
            return _Result("error", ("render_failed",), SAVE_MESSAGES["error"], draft=draft)
        lock = self._locks.setdefault(place, asyncio.Lock())
        try:
            async with lock:                                            # 5. учёт и файл — в потоке под замком места
                done = await asyncio.to_thread(role.agents.save, place, draft, text, now)
        except NameTaken as exc:
            return _refused([Problem.make("name", "name_taken", draft.name, existing=exc.existing)], draft)
        except TooManyAgents:
            return _refused([Problem.make("name", "too_many_agents")], draft)
        except DirUnavailable:
            return _Result("unavailable", ("dir_unavailable",), SAVE_MESSAGES["dir"], draft=draft)
        except StoreError as exc:
            role.agent_stats.store_failed(str(exc))                     # текст StoreError — без пути и значений
            return _Result("unavailable", ("store_error",), SAVE_MESSAGES["store"], draft=draft)
        if done == "saved":
            role.agent_stats.store_ok()
        return _Result("ok", (), SAVE_MESSAGES["ok"], draft=draft, already=done == "already")

    async def _finish(self, caller: str, raw: Mapping[str, object], result: _Result, began: float) -> None:
        """Счётчик → событие (запись в потоке; сбой записи ответ не ломает) → строка журнала."""
        role = self.role
        kind = "saved" if result.status == "ok" and not result.already else _COUNTERS.get(result.status)
        if kind is not None:
            role.agent_stats.event(kind)
        ms = max(0, round((role.monotonic() - began) * 1000))
        name = raw.get("name")
        try:
            event = CallEvent(ts=role.wallclock(), caller=caller,
                              agent=result.draft.name if result.draft is not None else (name if isinstance(name, str)
                                                                                        else None),
                              channel="mcp", tool=TOOL, requested=requested_of(name),
                              tags=result.draft.tags if result.draft is not None else (), sent=(), start=None, end=None,
                              outcome=result.status, summary=result.summary(), cost=0.0, cache="none", rows=0,
                              duration_ms=ms)
        except Exception as exc:
            log.error("журнал событий: событие %s не собрано (%s)", TOOL, type(exc).__name__)
        else:
            await role._write_event(event)
        log.log(_LEVELS.get(result.status, logging.WARNING), "tool=%s caller=%s channel=mcp outcome=%s codes=%s ms=%d",
                TOOL, ascii(caller)[1:-1][:CALLER_LOG_CHARS], result.status, result.log_codes(), ms)
