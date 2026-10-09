"""Калькуляторы страницы (Д9а, Д9-R87): POST /pcbk/api/calc/<tool> — вкладка «Калькуляторы» чата зовёт тот же
DataService, что HTTP /api/data/ и MCP, в процессе, без HTTP к самому себе (Д9-R82).

Порядок проверок (R87):
1. зона front_only — вне pcbk-front 404;
2. настройка configured — 503 «Шлюз не настроен: …»;
3. сессия current_account — 401 (сбой базы учёток — 503 store);
4. источник origin_checked — 403; после сессии, как у wake: запрос без учётки — 401 без строки журнала и счётчика;
5. инструмент не из CALC_TOOLS — 404 not_found (save_agent и прочее службе не передаются);
6. вызывающий — по роли учётки сессии, явно (caller_for): преподаватель — TEACHER_CALLER (Д10-R109: прежний отказ
   преподавателю снят), студент с местом — caller_of(место); иначе (чужая роль или студент без места — только правкой
   базы мимо AccountStore.add: связь «студент ↔ место» держит он, не схема) — 403 ROLE_CALC_TEXT, как у прокси;
7. службы нет (role.calc is None) — 503 CALC_DOWN_TEXT;
8. тело не объект JSON — 400 bad_body, события нет;
9. аргументы — модели http_api (те же, что у /api/data/): ошибка — refuse_args службы с каналом web (событие refused) и
   200 с её ответом, как отказ MCP;
10. иначе — инструмент службы, ответ 200 при любом его статусе.

Тело читается вручную (await request.body()): разбор FastAPI дал бы 422 мимо отказов вида OpenCode. Ключи тела, которых
нет в модели (place, caller), модель отбрасывает: вызывающий — только caller_for(учётка сессии), канал — web; место
преподавателя (даже правкой базы) не действует. Предел частоты службы на вызывающего (PER_CALLER_LIMIT) у
преподавателя общий для всех его вкладок: вызывающий один. Ни тело, ни cookie маршрут в журнал не пишет; строку вызова
пишет служба, как для http и mcp. Предел тела — общий предел шлюза под /pcbk/api/ (BODY_LIMIT, 64 КиБ), как у edge.

DataService здесь и в gateway/__init__.py — только под TYPE_CHECKING (m12); модуль импортируется лениво из
GatewayRole.router, поэтому команда администратора python -m pcbk_core.gateway.accounts не тянет mcp и pytds.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from ..data.http_api import (CalcRuntimeArgs, CatalogSearchArgs, TagNowArgs, TagPeriodArgs, TagSeriesArgs,
                             requested_from_body, validation_reason)
from .accounts import Account
from .api import ERROR_NAMES, GW_MESSAGES, PREFIX, configured, current_account, front_only, gw_error, origin_checked

if TYPE_CHECKING:
    from ..data.service import DataService
    from . import GatewayRole

CALC_PREFIX = PREFIX + "/calc/"
# инструменты вкладки — пять инструментов чтения службы (save_agent — нет); веб: тип CalcTool (web/src/api.ts)
CALC_TOOLS = ("catalog_search", "tag_now", "tag_period", "tag_series", "calc_runtime")
TEACHER_CALLER = "teacher"              # вызывающий службы у преподавателя (Д10-R109): caller=teacher, channel=web
ROLE_CALC_TEXT = "Калькуляторы — только для студента с рабочим местом и преподавателя"
CALC_DOWN_TEXT = "Калькуляторы недоступны — служба данных не подключена"
CHANNEL: Literal["web"] = "web"

# модели аргументов — те же, что у HTTP /api/data/<tool>
_ARGS: dict[str, type[BaseModel]] = {"catalog_search": CatalogSearchArgs, "tag_now": TagNowArgs,
                                     "tag_period": TagPeriodArgs, "tag_series": TagSeriesArgs,
                                     "calc_runtime": CalcRuntimeArgs}


def caller_of(place: int) -> str:
    """Вызывающий для службы — место учётки сессии: student-NN (как токен места у /api/data/ и MCP)."""
    return f"student-{place:02d}"


def caller_for(account: Account) -> str | None:
    """Вызывающий по роли учётки, явно (итоговое ревью Д9а, m-5): преподаватель — TEACHER_CALLER, его место (если оно
    есть правкой базы) не действует; студент с местом — caller_of(место); прочее — None (отказ 403)."""
    if account.role == "teacher":
        return TEACHER_CALLER
    if account.role == "student" and account.place is not None:
        return caller_of(account.place)
    return None


def _body_object(raw: bytes) -> dict[str, Any] | None:
    """Тело — объект JSON; битый JSON, не UTF-8, массив, строка, число и null — None."""
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):    # в том числе UnicodeDecodeError и пустое тело
        return None
    return body if isinstance(body, dict) else None


async def _call(svc: DataService, caller: str, tool: str, args: Any) -> dict[str, Any]:
    """Инструмент службы с аргументами модели — те же вызовы, что у http_api.data_router, с каналом web."""
    if tool == "catalog_search":
        return await svc.catalog_search(caller, CHANNEL, args.query, args.limit)
    if tool == "tag_now":
        return await svc.tag_now(caller, CHANNEL, args.tags)
    if tool == "tag_period":
        return await svc.tag_period(caller, CHANNEL, args.tags, args.period, args.start, args.end)
    if tool == "tag_series":
        return await svc.tag_series(caller, CHANNEL, args.tags, args.period, args.start, args.end, args.points)
    return await svc.calc_runtime(caller, CHANNEL, args.tags, args.period, args.start, args.end)


def calc_router(role: GatewayRole) -> APIRouter:
    """POST /pcbk/api/calc/{tool}; ставится в GatewayRole.router до catch_all_router. Зона и настройка — у самого
    маршрута (как agents_router, m24): зависимости маршрута идут раньше параметров, затем сессия."""
    router = APIRouter()
    account_of = current_account(role)
    check_origin = origin_checked(role)

    @router.post(CALC_PREFIX + "{tool}", dependencies=[Depends(front_only), Depends(configured(role))])
    async def calc(tool: str, request: Request, account: Account = Depends(account_of)) -> JSONResponse:
        await check_origin(request)         # после сессии: без учётки — 401, ни строки, ни счётчика origin_refused
        if tool not in CALC_TOOLS:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        caller = caller_for(account)                # роль — явно (m-5 Д9а); преподаватель — свой вызывающий (R109)
        if caller is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_CALC_TEXT)
        svc = role.calc
        if svc is None:
            return gw_error(503, ERROR_NAMES[503], CALC_DOWN_TEXT)
        body = _body_object(await request.body())
        if body is None:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            args = _ARGS[tool].model_validate(body)
        except ValidationError as exc:
            return JSONResponse(await svc.refuse_args(caller, CHANNEL, tool, requested_from_body(body),
                                                      validation_reason(list(exc.errors()))))
        return JSONResponse(await _call(svc, caller, tool, args))

    return router
