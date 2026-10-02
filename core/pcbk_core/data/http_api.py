"""HTTP-оболочка службы данных: POST /api/data/catalog_search, /api/data/tag_now, /api/data/tag_period.

Порядок обработчика: токен → ошибка настройки → инструмент.
- Нет или неверный Bearer — 401 {"detail": "нужен токен"} и строка журнала без токена, без события.
- Ошибка настройки роли — 503, тело — config_unavailable (F5): ворота не зовутся.
- Ответ инструмента — 200 при любом его статусе.

Тело разбирает FastAPI по моделям ниже; его ошибки (битый JSON, лишние теги, неизвестный период)
ловит validation_handler в том же порядке: 401, 503, иначе 422 с событием refused. DataRole.install
регистрирует его в реестре крючков приложения на префикс /api/data/ (hooks.py). Предел тела — общее звено
hooks.BodyLimit на всём приложении до маршрутизации: у /api/data/ и /mcp — по умолчанию, MAX_BODY_BYTES;
больше — 413 и строка журнала, в том числе тело без длины. BodyLimit, size_text и safe_path живут в
hooks.py, здесь — реэкспорт для прежних импортов.
"""
import logging
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StringConstraints
from starlette.types import Scope

from .. import zones
from ..hooks import DEFAULT_BODY_LIMIT, BodyLimit, safe_path, size_text  # noqa: F401 — реэкспорт Д3б
from ..secrets import TokenTable
from .periods import PRESETS
from .service import QUERY_CHARS, SEARCH_LIMIT, TAG_CHARS, requested_of
from .sql import MAX_NAMES

if TYPE_CHECKING:
    from . import DataRole

MAX_BODY_BYTES = DEFAULT_BODY_LIMIT
UNAUTHORIZED = {"detail": "нужен токен"}
REASON_ERRORS = 3                       # ошибок разбора в тексте 422 — не больше
# путь → инструмент
DATA_TOOLS = {"/api/data/catalog_search": "catalog_search", "/api/data/tag_now": "tag_now",
              "/api/data/tag_period": "tag_period"}

log = logging.getLogger("pcbk_core.data.http")

Tag = Annotated[str, StringConstraints(max_length=TAG_CHARS)]


class CatalogSearchArgs(BaseModel):
    query: Annotated[str, StringConstraints(min_length=1, max_length=QUERY_CHARS)]
    limit: Annotated[int, Field(ge=1, le=SEARCH_LIMIT)] = 10


class TagNowArgs(BaseModel):
    tags: Annotated[list[Tag], Field(min_length=1, max_length=MAX_NAMES)]


class TagPeriodArgs(BaseModel):
    tags: Annotated[list[Tag], Field(min_length=1, max_length=MAX_NAMES)]
    period: Literal[PRESETS] | None = None
    start: str | None = None
    end: str | None = None


def bearer_caller(authorization: str | None, tokens: TokenTable, *, scope: Scope) -> str | None:
    """id вызывающего по заголовку «Authorization: Bearer <токен>»; нет, чужой или не Bearer — None.

    Токен места student-NN — только из сети своего места или из ADMIN_NETS (Ш25, zones.caller_net_ok): из
    чужой сети — None, и вызов идёт тем же путём 401, что чужой токен (без события). scope обязателен — все
    места сверки токенов передают адрес источника.
    """
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    caller = tokens.caller(token.strip())
    if caller is None or not zones.caller_net_ok(scope, caller):
        return None
    return caller


def unauthorized(path: str, channel: str) -> JSONResponse:
    """401 и строка журнала — без токена и без события."""
    log.warning("outcome=unauthorized channel=%s path=%s", channel, safe_path(path))
    return JSONResponse(UNAUTHORIZED, status_code=401, headers={"WWW-Authenticate": "Bearer"})


def data_router(role: "DataRole") -> APIRouter:
    router = APIRouter()

    def authorize(request: Request) -> tuple[str | None, JSONResponse | None]:
        caller = bearer_caller(request.headers.get("authorization"), role.tokens, scope=request.scope)
        if caller is None:
            return None, unauthorized(request.scope["path"], "http")
        return caller, None

    @router.post("/api/data/catalog_search")
    async def catalog_search(args: CatalogSearchArgs, request: Request) -> JSONResponse:
        caller, refusal = authorize(request)
        if refusal is not None:
            return refusal
        if role.config_error:
            return JSONResponse(await role.service.config_unavailable(caller, "http", "catalog_search", args.query),
                                status_code=503)
        return JSONResponse(await role.service.catalog_search(caller, "http", args.query, args.limit))

    @router.post("/api/data/tag_now")
    async def tag_now(args: TagNowArgs, request: Request) -> JSONResponse:
        caller, refusal = authorize(request)
        if refusal is not None:
            return refusal
        if role.config_error:
            return JSONResponse(await role.service.config_unavailable(caller, "http", "tag_now", args.tags),
                                status_code=503)
        return JSONResponse(await role.service.tag_now(caller, "http", args.tags))

    @router.post("/api/data/tag_period")
    async def tag_period(args: TagPeriodArgs, request: Request) -> JSONResponse:
        caller, refusal = authorize(request)
        if refusal is not None:
            return refusal
        if role.config_error:
            return JSONResponse(await role.service.config_unavailable(caller, "http", "tag_period", args.tags),
                                status_code=503)
        return JSONResponse(await role.service.tag_period(caller, "http", args.tags, args.period, args.start,
                                                          args.end))

    return router


# --- ошибки разбора тела: русская причина — поле и короткий текст ---

_ERROR_TEXTS = {
    "missing": "обязательное поле",
    "json_invalid": "не JSON",
    "model_attributes_type": "нужен объект JSON", "dict_type": "нужен объект JSON", "model_type": "нужен объект JSON",
    "list_type": "нужен список",
    "string_type": "нужна строка",
    "int_type": "нужно целое число", "int_parsing": "нужно целое число", "int_from_float": "нужно целое число",
    "too_short": "нужен хотя бы один элемент",
    "string_too_short": "пустая строка",
    "literal_error": "неизвестное значение",
}


def _error_text(err: dict[str, Any]) -> str:
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "too_long":
        return f"не больше {ctx.get('max_length', MAX_NAMES)} элементов"
    if kind == "string_too_long":
        return f"длиннее {ctx.get('max_length', TAG_CHARS)} знаков"
    if kind == "less_than_equal":
        return f"не больше {ctx.get('le', '')}".rstrip()
    if kind == "greater_than_equal":
        return f"не меньше {ctx.get('ge', '')}".rstrip()
    return _ERROR_TEXTS.get(kind, "неверное значение")


def _error_field(err: dict[str, Any]) -> str:
    """Поле по loc: имена полей модели и номера в списке; у тела целиком и битого JSON — «тело»."""
    if err.get("type") == "json_invalid":
        return "тело"
    loc = tuple(err.get("loc") or ())
    if loc[:1] == ("body",):
        loc = loc[1:]
    return ".".join(str(part) for part in loc) or "тело"


def validation_reason(errors: list[dict[str, Any]]) -> str:
    """«tags: не больше 16 элементов; limit: не больше 50» — не больше REASON_ERRORS ошибок."""
    return "; ".join(f"{_error_field(e)}: {_error_text(e)}" for e in errors[:REASON_ERRORS]) or "тело: неверное"


def requested_from_body(body: object) -> tuple[str, ...]:
    """Сырой ввод для события из тела, которое не прошло разбор: tags, иначе query, иначе текст тела."""
    if isinstance(body, dict):
        raw = body.get("tags") if "tags" in body else body.get("query")
        return requested_of(raw)
    if isinstance(body, bytes):
        return requested_of(body.decode("utf-8", "replace"))
    return requested_of(body)


def validation_handler(role: "DataRole"):
    """Обработчик RequestValidationError: пути /api/data/* — токен → ошибка настройки → 422 с событием;
    прочие — обработчик FastAPI по умолчанию."""

    async def handle(request: Request, exc: RequestValidationError) -> Response:
        path = request.scope["path"]
        tool = DATA_TOOLS.get(path)
        if tool is None:
            return await request_validation_exception_handler(request, exc)
        caller = bearer_caller(request.headers.get("authorization"), role.tokens, scope=request.scope)
        if caller is None:              # битый JSON без токена не пишет мусор в журнал событий
            return unauthorized(path, "http")
        requested = requested_from_body(exc.body)
        if role.config_error:           # F5: и раньше 422
            return JSONResponse(await role.service.config_unavailable(caller, "http", tool, requested),
                                status_code=503)
        answer = await role.service.refuse_args(caller, "http", tool, requested,
                                                validation_reason(list(exc.errors())))
        return JSONResponse(answer, status_code=422)

    return handle
