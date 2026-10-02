"""Ручки агентов студентов у шлюза: GET /health/agents (Д7а, Д7-R60); Д7б добавит сюда GET /pcbk/api/agents.

GET /health/agents — только зона front (front_only), как /health/gateway и /health/web (Д5-R31): сторож ходит из
pcbk-front. Отказы (зона, неизвестный метод) — обработчиком HTTPException шлюза из реестра hooks_of(app): роль ставит
его на этот путь в install. Тело — GatewayRole.agents_health_json: ровно 15 ключей, только числа и тексты, без имён
агентов и номеров мест. На core старше Д7а ручки нет — 404, и сторож пишет «служба агентов ещё не выложена».

Модуль импортируется лениво, как api, proxy и web (команда администратора accounts).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .api import front_only

if TYPE_CHECKING:
    from . import GatewayRole

HEALTH_AGENTS_PATH = "/health/agents"


def agents_router(role: "GatewayRole") -> APIRouter:
    """GET /health/agents (зона front); ставится в GatewayRole.router до catch_all_router."""
    router = APIRouter()

    @router.get(HEALTH_AGENTS_PATH, dependencies=[Depends(front_only)])
    async def health_agents() -> JSONResponse:
        # только числа и тексты: ни имён агентов, ни номеров мест
        return JSONResponse(role.agents_health_json(role.wallclock()))

    return router
