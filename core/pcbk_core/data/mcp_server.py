"""MCP службы данных: catalog_search, tag_now, tag_period — вторая тонкая оболочка над той же DataService.

mcp 1.30.0 (FastMCP), Streamable HTTP без сессий (stateless), ответ JSON, точный маршрут /mcp.

Порядок, как у HTTP:
1. токен — mcp_auth до транспорта: не POST — 405; нет или неверный Bearer — 401 и строка журнала
   outcome=unauthorized channel=mcp, без события. Вызывающего инструмент берёт по тому же заголовку;
2. ошибка настройки (F5) — config_unavailable раньше разбора аргументов. Транспорт отвечает на
   tools/call 200 (так устроен JSON-RPC), отказ — в самом результате: status unavailable, причина в
   message; историан не трогается;
3. аргументы — теми же моделями, что HTTP; ошибка — refuse_args: событие refused с русской причиной;
4. инструмент службы (событие и строка журнала — её).

Аннотации аргументов свободные (Any): иначе строку вместо списка, пустой вызов, неизвестный период и
17 тегов FastMCP отверг бы по-английски до функции, без события. Схему для модели задаёт
json_schema_extra. null равен «не задан»: модели шлют null вместо пропуска необязательного поля.
Строку с JSON внутри FastMCP разбирает сам, до функции: '["A"]' станет списком, 'null' — «не задан».
"""
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .http_api import (UNAUTHORIZED, CatalogSearchArgs, TagNowArgs, TagPeriodArgs, bearer_caller, unauthorized,
                       validation_reason)
from .periods import PRESETS
from .service import QUERY_CHARS, TAG_CHARS, requested_of
from .sql import MAX_NAMES

if TYPE_CHECKING:
    from . import DataRole

MCP_PATH = "/mcp"
NOT_POST = {"detail": "метод не поддерживается: только POST"}
# их читает модель; текст постоянный
TOOL_DESCRIPTIONS = {
    "catalog_search": "Поиск тегов БДРВ участка по словам в имени и описании. Описания в историане обрезаны на "
                      "50 символах: если точного совпадения нет, вернутся похожие — покажите их студенту на "
                      "выбор, не угадывайте.",
    "tag_now": "Текущее значение тегов БДРВ (до 16 за вызов): значение, единица, время с поясом, возраст и "
               "качество. Имена берите точно из catalog_search.",
    "tag_period": "Сводка по тегам БДРВ за период (до 16 тегов, окно до 31 суток, бюджет 24 тего-суток): "
                  "среднее, минимум, максимум, размах, σ — посчитаны историаном по сырым данным. Период — "
                  "готовый (period) или начало и конец (start, end, ISO 8601). В ответе сказано, какое это "
                  "среднее, в каком поясе время и что смены 06/14/22 — допущение.",
}
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

Tags = Annotated[Any, Field(json_schema_extra={"type": "array", "items": {"type": "string", "maxLength": TAG_CHARS},
                                               "minItems": 1, "maxItems": MAX_NAMES})]
Preset = Annotated[Any, Field(json_schema_extra={"type": "string", "enum": list(PRESETS)})]
IsoTime = Annotated[Any, Field(json_schema_extra={"type": "string"})]
Query = Annotated[Any, Field(json_schema_extra={"type": "string", "minLength": 1, "maxLength": QUERY_CHARS})]

log = logging.getLogger("pcbk_core.data.mcp")


def _caller(ctx: Context, role: "DataRole") -> str | None:
    """id вызывающего по заголовку Authorization запроса HTTP; вне запроса HTTP — None."""
    try:
        request = ctx.request_context.request
    except ValueError:                  # вне запроса MCP контекста нет
        return None
    if request is None:
        return None
    return bearer_caller(request.headers.get("authorization"), role.tokens)


def build_mcp(role: "DataRole") -> FastMCP:
    """Сервер MCP роли: три инструмента только для чтения; служба — role.service в момент вызова.

    log_level="WARNING": FastMCP настраивает корневой логгер (basicConfig), на INFO pytds писал бы
    адрес историана и начало SQL. Защита от DNS rebinding выключена: Host разный (core:8000, адрес
    сети места, туннель), защищает токен на каждом запросе.
    """
    mcp = FastMCP("pcbk-data", stateless_http=True, json_response=True, streamable_http_path=MCP_PATH,
                  log_level="WARNING",
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    async def call(ctx: Context, tool: str, raw: object, model: type[BaseModel], fields: dict[str, Any],
                   run: Callable[[str, Any], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        """Токен → ошибка настройки → аргументы → служба; raw — сырой ввод для события."""
        caller = _caller(ctx, role)
        if caller is None:              # мимо mcp_auth (вызов без запроса HTTP): отказ без события
            log.warning("outcome=unauthorized channel=mcp tool=%s", tool)
            return {"tool": tool, "status": "refused", "message": UNAUTHORIZED["detail"], "notes": [],
                    "cache": "none"}
        if role.config_error:           # F5: раньше разбора аргументов
            return await role.service.config_unavailable(caller, "mcp", tool, requested_of(raw))
        try:
            args = model.model_validate({k: v for k, v in fields.items() if v is not None})
        except ValidationError as err:  # текст ошибки pydantic несёт ввод — дальше только русская причина
            reason = validation_reason(list(err.errors()))
        else:
            return await run(caller, args)
        return await role.service.refuse_args(caller, "mcp", tool, requested_of(raw), reason)

    @mcp.tool(name="catalog_search", description=TOOL_DESCRIPTIONS["catalog_search"], annotations=READ_ONLY)
    async def catalog_search(ctx: Context, query: Query = None, limit: Any = 10) -> dict[str, Any]:
        return await call(ctx, "catalog_search", query, CatalogSearchArgs, {"query": query, "limit": limit},
                          lambda caller, a: role.service.catalog_search(caller, "mcp", a.query, a.limit))

    @mcp.tool(name="tag_now", description=TOOL_DESCRIPTIONS["tag_now"], annotations=READ_ONLY)
    async def tag_now(ctx: Context, tags: Tags = None) -> dict[str, Any]:
        return await call(ctx, "tag_now", tags, TagNowArgs, {"tags": tags},
                          lambda caller, a: role.service.tag_now(caller, "mcp", a.tags))

    @mcp.tool(name="tag_period", description=TOOL_DESCRIPTIONS["tag_period"], annotations=READ_ONLY)
    async def tag_period(ctx: Context, tags: Tags = None, period: Preset = None, start: IsoTime = None,
                         end: IsoTime = None) -> dict[str, Any]:
        return await call(ctx, "tag_period", tags, TagPeriodArgs,
                          {"tags": tags, "period": period, "start": start, "end": end},
                          lambda caller, a: role.service.tag_period(caller, "mcp", a.tags, a.period, a.start,
                                                                    a.end))

    return mcp


def mcp_auth(app: ASGIApp, role: "DataRole") -> ASGIApp:
    """Токен до транспорта MCP — чистое ASGI (BaseHTTPMiddleware ломает потоковые ответы).

    Не POST — 405: GET открыл бы поток SSE, в который без сессий никто не пишет. Нет или неверный
    Bearer — 401 {"detail": "нужен токен"} и строка журнала без токена, без события.
    """

    async def guard(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            if scope["method"] != "POST":
                await JSONResponse(NOT_POST, status_code=405, headers={"Allow": "POST"})(scope, receive, send)
                return
            if bearer_caller(Headers(scope=scope).get("authorization"), role.tokens) is None:
                await unauthorized(scope.get("path", ""), "mcp")(scope, receive, send)
                return
        await app(scope, receive, send)

    return guard
