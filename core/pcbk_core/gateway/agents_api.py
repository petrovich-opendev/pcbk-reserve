"""Ручки агентов студентов у шлюза: GET /health/agents (Д7а, Д7-R60) и «Мои агенты» GET /pcbk/api/agents (Д7б, Д7-R64).

GET /health/agents — только зона front (front_only), как /health/gateway и /health/web (Д5-R31): сторож ходит из
pcbk-front. Отказы (зона, неизвестный метод) — обработчиком HTTPException шлюза из реестра hooks_of(app): роль ставит
его на этот путь в install. Тело — GatewayRole.agents_health_json: ровно 15 ключей, только числа и тексты, без имён
агентов и номеров мест. На core старше Д7а ручки нет — 404, и сторож пишет «служба агентов ещё не выложена».

GET /pcbk/api/agents — панель «Агенты» чата (Д7-R64, R65): {"agents": [...], "own": k, "limit": MAX_AGENTS}.
- Проверки — как у GET /pcbk/api/workplace (Д5а): зона (вне pcbk-front — 404) → ошибка настройки (503 «Шлюз не
  настроен: …») → сессия (401, сбой базы учёток — 503 store); проверки источника у GET нет. agents_router включён в
  GatewayRole.router без общих guarded, поэтому front_only и configured(role) стоят у самого маршрута (предпроверка
  Д7б, m24): иначе при config_error ручка отвечала бы. Неизвестный метод — 404 без Allow тем же обработчиком.
- У преподавателя места нет — 403 «Агенты студентов — в кабинете преподавателя, только на чтение» (Д10-R100: агентов
  студентов преподаватель читает в кабинете, /pcbk/api/teacher/), учёт не читается. Место — только account.place учётки
  сессии; параметров и заголовков места у ручки нет (Review Focus 2).
- Сначала IMAGE_AGENTS — встроенные агенты образа места (student/config/agent/*.md), оба ready; названия совпадают с
  BUILTIN_AGENT_TITLES веба (web/src/texts.ts) и с description файлов образа. Затем строки учёта места
  (AgentStore.list_place — без archived, старые первыми): visible → ready, saved → pending, not_visible и quarantined
  (и состояние, которого шлюз не знает) → broken. Сбой учёта агентов — 503 store.
- own — живые (неархивные) строки учёта места, quarantined тоже. Предел save (MAX_AGENTS) считает строки и файлы
  **/*.md каталога места вместе, поэтому при файлах без строки (учение, ручная правка) own меньше того, что считает
  save, и отказ too_many_agents приходит раньше own == limit (предпроверка Д7б, m12; docs/checks/D7b.md). Веб own и
  limit проверяет, но не показывает (R65).

GET /pcbk/api/events (Д13) — вкладка «События»: {"events": [...], "limit": EVENTS_LIMIT}, новые первыми, не больше
EVENTS_LIMIT. Проверки те же, что у GET /pcbk/api/agents (зона → настройка → сессия, преподаватель — 403 тем же
текстом TEACHER_TEXT); только место учётки сессии. Источник строк — AgentFindings.recent (role.findings, agent_findings
в core.db, пишет report_event службы данных). agent_title — AgentStore.title_of места (role.agents): архивный агент —
прежнее название, имени не нашли вовсе — имя агента как есть.

Модуль импортируется лениво, как api, proxy и web (команда администратора accounts).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..agents.findings import Finding, FindingsError
from ..agents.store import MAX_AGENTS, AgentRow, StoreError
from .accounts import Account
from .api import ERROR_NAMES, GW_MESSAGES, PREFIX, configured, current_account, front_only, gw_error

if TYPE_CHECKING:
    from . import GatewayRole

HEALTH_AGENTS_PATH = "/health/agents"
AGENTS_PATH = PREFIX + "/agents"
EVENTS_PATH = PREFIX + "/events"               # GET /pcbk/api/events (Д13): события агентов места студента
EVENTS_LIMIT = 100

# встроенные агенты образа места (Д7-R64): (имя файла без .md, название, назначение) — в порядке панели
IMAGE_AGENTS: tuple[tuple[str, str, str], ...] = (
    ("pomoshnik", "Помощник по данным", "текущие значения и сводки тегов БДРВ"),
    ("konstruktor", "Конструктор агентов", "за один разговор собирает вашего агента по узлу"),
)
# состояние строки учёта → состояние панели; archived list_place не отдаёт, прочее — broken (не выбирается)
OWN_STATES = {"visible": "ready", "saved": "pending", "not_visible": "broken", "quarantined": "broken"}
TEACHER_TEXT = "Агенты студентов — в кабинете преподавателя, только на чтение"      # Д10-R100


def agents_json(rows: list[AgentRow]) -> dict:
    """Ответ «Мои агенты»: встроенные агенты образа, затем строки учёта места в их порядке."""
    agents = [{"name": name, "title": title, "purpose": purpose, "kind": "builtin", "state": "ready"}
              for name, title, purpose in IMAGE_AGENTS]
    agents += [{"name": row.name, "title": row.title, "purpose": row.purpose, "kind": "own",
                "state": OWN_STATES.get(row.state, "broken")} for row in rows]
    return {"agents": agents, "own": len(rows), "limit": MAX_AGENTS}


def events_json(findings: list[Finding], titles: dict[str, str | None]) -> dict:
    """Ответ GET /pcbk/api/events (Д13): новые первыми (findings уже отданы recent() так), не больше EVENTS_LIMIT.
    agent_title — из учёта (titles[agent]); агента не нашли вовсе — имя агента (titles не держит такого ключа или
    он None)."""
    events = [{"id": f.id, "ts": f.ts, "agent": f.agent, "agent_title": titles.get(f.agent) or f.agent,
              "kind": f.kind, "title": f.title, "tags": list(f.tags), "period_start": f.period_start,
              "period_end": f.period_end, "value": f.value, "setpoint": f.setpoint, "text": f.text}
             for f in findings]
    return {"events": events, "limit": EVENTS_LIMIT}


def agents_router(role: "GatewayRole") -> APIRouter:
    """GET /health/agents (зона front) и GET /pcbk/api/agents (зона, настройка, сессия); ставится в GatewayRole.router
    до catch_all_router."""
    router = APIRouter()
    account_of = current_account(role)

    @router.get(HEALTH_AGENTS_PATH, dependencies=[Depends(front_only)])
    async def health_agents() -> JSONResponse:
        # только числа и тексты: ни имён агентов, ни номеров мест
        return JSONResponse(role.agents_health_json(role.wallclock()))

    # порядок как у guarded ручек места: зона → настройка → сессия (зависимости маршрута — раньше параметров)
    @router.get(AGENTS_PATH, dependencies=[Depends(front_only), Depends(configured(role))])
    async def my_agents(account: Account = Depends(account_of)) -> JSONResponse:
        if account.place is None:
            return gw_error(403, ERROR_NAMES[403], TEACHER_TEXT)
        try:
            rows = await asyncio.to_thread(role.agents.list_place, account.place)
        except StoreError:
            return gw_error(503, ERROR_NAMES[503], GW_MESSAGES["store"])
        return JSONResponse(agents_json(rows))

    # Д13: та же зона, настройка и сессия, что у /pcbk/api/agents (m24); преподаватель — тот же текст 403
    @router.get(EVENTS_PATH, dependencies=[Depends(front_only), Depends(configured(role))])
    async def my_events(account: Account = Depends(account_of)) -> JSONResponse:
        if account.place is None:
            return gw_error(403, ERROR_NAMES[403], TEACHER_TEXT)
        try:
            findings = await asyncio.to_thread(role.findings.recent, account.place, EVENTS_LIMIT)
        except FindingsError:
            return gw_error(503, ERROR_NAMES[503], GW_MESSAGES["store"])
        names = sorted({f.agent for f in findings})

        def titles_of() -> dict[str, str | None]:
            return {name: role.agents.title_of(account.place, name) for name in names}
        try:
            titles = await asyncio.to_thread(titles_of)
        except StoreError:
            return gw_error(503, ERROR_NAMES[503], GW_MESSAGES["store"])
        return JSONResponse(events_json(findings, titles))

    return router
