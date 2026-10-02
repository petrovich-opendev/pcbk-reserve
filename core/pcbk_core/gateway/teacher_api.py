"""Кабинет преподавателя у шлюза (Д10а). Задача А1 — только наблюдение: GET /health/teacher (Д10-R96); ручки кабинета
/pcbk/api/teacher/ — задача А3.

GET /health/teacher — только зона front (front_only), как /health/gateway и /health/agents (Д5-R31): сторож ходит из
pcbk-front. Отказы (зона, неизвестный метод) — обработчиком HTTPException шлюза из реестра hooks_of(app): роль ставит его
на этот путь в install. Тело — GatewayRole.teacher_health_json: ровно 13 ключей, только числа и флаги, без логинов и
номеров мест. Ручка читается и при config_error шлюза (без configured): тогда сверки долей нет, и строка сторожа —
«неизвестно», причина — в строке «Шлюз и вход» (m-13). На core старше Д10а ручки нет — 404, и сторож пишет «кабинет
преподавателя ещё не выложен».

Модуль импортируется лениво, как api, proxy и agents_api (команда администратора accounts).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .api import front_only

if TYPE_CHECKING:
    from . import GatewayRole

HEALTH_TEACHER_PATH = "/health/teacher"


def teacher_router(role: "GatewayRole") -> APIRouter:
    """GET /health/teacher (зона front); ставится в GatewayRole.router до catch_all_router."""
    router = APIRouter()

    @router.get(HEALTH_TEACHER_PATH, dependencies=[Depends(front_only)])
    async def health_teacher() -> JSONResponse:
        # только числа и флаги: ни логинов, ни номеров мест
        return JSONResponse(role.teacher_health_json(role.wallclock()))

    return router
