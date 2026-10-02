"""MCP службы данных: catalog_search, tag_now, tag_period — вторая тонкая оболочка над той же DataService;
save_agent (Д7а, Д7-R57) — только здесь, без HTTP-ручки: тонкая оболочка над SaveAgent роли.

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

save_agent: токен — тем же mcp_auth; ошибку настройки, частоту, «только место студента» и проверку черновика делает
SaveAgent (Д7-R57). Все восемь полей обязательны (SAVE_REQUIRED): по умолчанию None, как у трёх инструментов, и
required вписывается в схему после регистрации — FastMCP ставил бы его только полям без умолчания. Отказа по схеме нет
(call_tool(validate_input=False)): нет поля — замечание wrong_type службы. Лишние ключи FastMCP отбрасывает до функции.
"""
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import zones
from .http_api import (UNAUTHORIZED, CatalogSearchArgs, TagNowArgs, TagPeriodArgs, bearer_caller, unauthorized,
                       validation_reason)
from .periods import PRESETS
from .save_agent import SAVE_DESCRIPTION, SAVE_PROPERTIES, SAVE_REQUIRED
from .service import QUERY_CHARS, SEARCH_LIMIT, TAG_CHARS, requested_of
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
    "save_agent": SAVE_DESCRIPTION,         # одно место правды — save_agent.py (research/09 §4.1, дословно)
}
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
# Д7-R57: пишет, но не разрушает; повтор того же черновика — already, не второй агент
SAVE_ANNOTATIONS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

Tags = Annotated[Any, Field(json_schema_extra={"type": "array", "items": {"type": "string", "maxLength": TAG_CHARS},
                                               "minItems": 1, "maxItems": MAX_NAMES})]
Preset = Annotated[Any, Field(json_schema_extra={"type": "string", "enum": list(PRESETS)})]
IsoTime = Annotated[Any, Field(json_schema_extra={"type": "string"})]
Query = Annotated[Any, Field(json_schema_extra={"type": "string", "minLength": 1, "maxLength": QUERY_CHARS})]
Limit = Annotated[Any, Field(json_schema_extra={"type": "integer", "minimum": 1, "maximum": SEARCH_LIMIT})]
AgentName = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["name"])]
AgentTitle = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["title"])]
AgentNode = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["node"])]
AgentPurpose = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["purpose"])]
AgentTools = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["tools"])]
AgentTags = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["tags"])]
AgentPrompt = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["prompt"])]
StudentReply = Annotated[Any, Field(json_schema_extra=SAVE_PROPERTIES["student_reply"])]

log = logging.getLogger("pcbk_core.data.mcp")


def _caller(ctx: Context, role: "DataRole") -> str | None:
    """id вызывающего по заголовку Authorization запроса HTTP; вне запроса HTTP — None."""
    try:
        request = ctx.request_context.request
    except ValueError:                  # вне запроса MCP контекста нет
        return None
    if request is None:
        return None
    return bearer_caller(request.headers.get("authorization"), role.tokens, scope=request.scope)


def build_mcp(role: "DataRole") -> FastMCP:
    """Сервер MCP роли: три инструмента только для чтения (служба — role.service в момент вызова) и save_agent
    (role.save_agent, Д7-R57).

    log_level="WARNING": без корневого обработчика (setup_logging его ставит) FastMCP настроил бы корень
    сам (basicConfig), и на INFO pytds писал бы адрес историана и начало SQL.

    Защита от DNS rebinding выключена. Она нужна серверу, которого страница в браузере может вызвать без
    секрета. У нас токен — заголовок Bearer, а не cookie: браузер сам его не подставит, а странице
    злоумышленника он неизвестен. К тому же Host разный: core:8000, адрес сети места, туннель.

    Настройки FastMCP (pydantic-settings) читали бы ./.env при каждой сборке: нечитаемый файл уронил
    бы конструктор роли, FIFO повесил бы его (F4). Все поля FastMCP передаются явно, файл не нужен —
    чтение выключено до сборки (mcp закреплён на 1.30.0).
    """
    FastMCPSettings.model_config["env_file"] = None
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
    async def catalog_search(ctx: Context, query: Query = None, limit: Limit = 10) -> dict[str, Any]:
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

    @mcp.tool(name="save_agent", description=TOOL_DESCRIPTIONS["save_agent"], annotations=SAVE_ANNOTATIONS)
    async def save_agent(ctx: Context, name: AgentName = None, title: AgentTitle = None, node: AgentNode = None,
                         purpose: AgentPurpose = None, tools: AgentTools = None, tags: AgentTags = None,
                         prompt: AgentPrompt = None, student_reply: StudentReply = None) -> dict[str, Any]:
        caller = _caller(ctx, role)
        if caller is None:              # мимо mcp_auth (вызов без запроса HTTP): отказ без события
            log.warning("outcome=unauthorized channel=mcp tool=save_agent")
            return {"tool": "save_agent", "status": "refused", "message": UNAUTHORIZED["detail"], "problems": [],
                    "notes": []}
        # место — только из токена: в аргументах его нет, лишние ключи FastMCP уже отбросил
        return await role.save_agent.call(caller, {"name": name, "title": title, "node": node, "purpose": purpose,
                                                   "tools": tools, "tags": tags, "prompt": prompt,
                                                   "student_reply": student_reply})

    # m8: все восемь полей обязательны — в схеме для модели; FastMCP по ней не отказывает (validate_input=False)
    mcp._tool_manager.get_tool("save_agent").parameters["required"] = list(SAVE_REQUIRED)
    return mcp


def mcp_auth(app: ASGIApp, role: "DataRole") -> ASGIApp:
    """Токен до транспорта MCP — чистое ASGI (BaseHTTPMiddleware ломает потоковые ответы).

    Только http: веб-сокет закрывается (1008), прочие виды (lifespan) внутрь не идут — второй run()
    сессий SDK не даёт. Не POST — 405: GET открыл бы поток SSE, в который без сессий никто не пишет.
    Нет или неверный Bearer (в том числе токен места из чужой сети, Ш25) — 401 {"detail": "нужен токен"} и
    строка журнала без токена, без события.
    """

    async def guard(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if scope["method"] != "POST":
            await JSONResponse(NOT_POST, status_code=405, headers={"Allow": "POST"})(scope, receive, send)
            return
        if bearer_caller(Headers(scope=scope).get("authorization"), role.tokens, scope=scope) is None:
            await unauthorized(scope.get("path", ""), "mcp")(scope, receive, send)
            return
        # транспорт MCP — своё приложение Starlette: оно заменит scope["app"], и сверка сети токена в
        # инструменте (_caller) не нашла бы STU_NET приложения core — кладём его в scope (Ш25)
        scope[zones.STU_NET_KEY] = zones.stu_net(scope)
        await app(scope, receive, send)

    return guard
