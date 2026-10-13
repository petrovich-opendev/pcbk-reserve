"""my_agents службы данных (Д13): живые агенты места вызывающего с их инструкцией — для Конструктора («правка
агента»). Только MCP, вызывающий — только student-01…11, как save_agent; без аргументов.

Порядок вызова (как у save_agent, без частоты — только чтение):
1. ошибка настройки роли (F5) — unavailable с причиной;
2. вызывающий не student-01…11 — refused;
3. живые строки учёта места (AgentStore.cards), без archived и quarantined, старые первыми (порядок записи); для
   каждой — текст файла и из него инструкция (agents.draft.extract_prompt) без служебных строк шаблона.

Событие agent_events (tool=my_agents) без текста инструкций, тегов и имён агентов — как у save_agent, в журнал
службы идёт только число агентов и исход.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..agents.draft import extract_prompt
from ..agents.store import AgentMissing, DirUnavailable, StoreError, place_of_caller
from .events import CallEvent
from .service import ERROR_MESSAGE

if TYPE_CHECKING:
    from . import DataRole

TOOL = "my_agents"
# карточки живых строк (cards) кроме quarantined — их Конструктор не должен предлагать на правку (п. 2 DESIGN)
SHOWN_STATES = frozenset({"saved", "visible", "not_visible"})
MESSAGES = {
    "not_student": "Агентов читает только рабочее место студента",
    "store": "Учёт агентов недоступен — сообщите преподавателю",
    "error": ERROR_MESSAGE,
}
_LEVELS = {"ok": logging.INFO, "refused": logging.INFO, "unavailable": logging.WARNING, "error": logging.ERROR}

log = logging.getLogger("pcbk_core.data.my_agents")


class MyAgents:
    """Инструмент my_agents роли «данные». Каталог агентов (role.agents) — из роли в момент вызова; конструктор
    без ввода-вывода."""

    def __init__(self, role: "DataRole"):
        self.role = role

    async def call(self, caller: str) -> dict[str, Any]:
        began = self.role.monotonic()
        caller = str(caller)
        role = self.role
        status, message, agents = "ok", "", []
        try:
            if role.config_error:                                       # F5
                status, message = "unavailable", role.config_error
            else:
                place = place_of_caller(caller)
                if place is None:
                    status, message = "refused", MESSAGES["not_student"]
                else:
                    try:
                        agents = await asyncio.to_thread(self._agents, place)
                    except StoreError:
                        status, message = "unavailable", MESSAGES["store"]
        except Exception as exc:          # текст исключения может нести значения полей — только класс
            status, message = "error", MESSAGES["error"]
            log.error("my_agents: непредвиденный сбой (%s)", type(exc).__name__)
        await self._finish(caller, status, len(agents), began)
        out = {"tool": TOOL, "status": status, "message": message, "notes": []}
        if status == "ok":
            out["agents"] = agents
        return out

    def _agents(self, place: int) -> list[dict[str, Any]]:
        """В потоке: карточки живых строк (без quarantined) + инструкция из файла. Сбой учёта — StoreError
        (наверх, ответ — unavailable); файл не читается (ручная правка, старый шаблон) — пустая инструкция."""
        out: list[dict[str, Any]] = []
        for card in self.role.agents.cards(place):
            if card.state not in SHOWN_STATES:
                continue
            try:
                text = self.role.agents.read_file(place, card.name)
                prompt = extract_prompt(text, card.name)
            except (AgentMissing, DirUnavailable):
                prompt = ""
            out.append({"name": card.name, "title": card.title, "node": card.node, "purpose": card.purpose,
                       "tools": list(card.tools), "tags": list(card.tags), "prompt": prompt})
        return out

    async def _finish(self, caller: str, status: str, count: int, began: float) -> None:
        role = self.role
        ms = max(0, round((role.monotonic() - began) * 1000))
        try:
            event = CallEvent(ts=role.wallclock(), caller=caller, agent=None, channel="mcp", tool=TOOL,
                              requested=(), tags=(), sent=(), start=None, end=None, outcome=status,
                              summary=f"agents {count}" if status == "ok" else status, cost=0.0, cache="none",
                              rows=count, duration_ms=ms)
        except Exception as exc:
            log.error("журнал событий: событие %s не собрано (%s)", TOOL, type(exc).__name__)
        else:
            await role._write_event(event)
        log.log(_LEVELS.get(status, logging.WARNING), "tool=%s caller=%s channel=mcp outcome=%s ms=%d", TOOL,
                caller, status, ms)
