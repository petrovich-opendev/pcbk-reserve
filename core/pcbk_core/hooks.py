"""Крючки уровня приложения: один обработчик 422, один обработчик HTTPException и один предел тела на
приложение, разбор по префиксу пути.

Роли наполняют реестр AppHooks в install(app) через hooks_of(app): обработчик ошибок разбора тела,
обработчик HTTPException (Д5) и предел тела (с ответом 413 или своим ответом по области запроса) — на свой
префикс пути. create_app после install всех ролей ставит один обработчик RequestValidationError, один
обработчик HTTPException Starlette и одно звено BodyLimit — самым внешним внутри ServerErrorMiddleware, так
что 413 выходит раньше звеньев ролей. Выбор — по самому длинному подходящему префиксу; путь без префикса —
обработчик FastAPI по умолчанию и предел DEFAULT_BODY_LIMIT. Модуль не импортирует pcbk_core.data: реестр
общий для всех ролей.
"""
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_BODY_LIMIT = 65536
PATH_LOG_CHARS = 100

ValidationHandler = Callable[[Request, RequestValidationError], Awaitable[Response]]
HttpExceptionHandler = Callable[[Request, StarletteHTTPException], Awaitable[Response]]
# тело ответа 413 по пределу префикса: у /llm/ — в форме OpenAI (П19)
Refusal = Callable[[int], dict]
# ответ целиком по области запроса и пределу: шлюз вне своей зоны отвечает 404, а не 413 (Д5)
Respond = Callable[[Scope, int], Response]

log = logging.getLogger("pcbk_core.hooks")

_T = TypeVar("_T")


def size_text(n: int) -> str:
    """Предел тела словами: 65536 — «64 КиБ», не кратное КиБ — в байтах."""
    return f"{n // 1024} КиБ" if n % 1024 == 0 else f"{n} байт"


def safe_path(path: str) -> str:
    """Путь для строки журнала: управляющие и не-ASCII — экранами, не длиннее PATH_LOG_CHARS."""
    return ascii(path)[1:-1][:PATH_LOG_CHARS]


def _longest(table: dict[str, _T], path: str) -> _T | None:
    prefix = max((p for p in table if path.startswith(p)), key=len, default=None)
    return None if prefix is None else table[prefix]


def _check_prefix(prefix: str, table: dict, what: str) -> None:
    if not prefix.startswith("/"):
        raise ValueError(f"{what}: префикс {prefix!r} — с «/» в начале, иначе он не совпадёт ни с одним путём")
    if prefix in table:
        raise ValueError(f"{what}: префикс {prefix!r} уже занят")


class AppHooks:
    """Реестр крючков приложения по префиксу пути: обработчики 422 и HTTPException, пределы тела с ответом 413."""

    def __init__(self) -> None:
        self._handlers: dict[str, ValidationHandler] = {}
        self._http_handlers: dict[str, HttpExceptionHandler] = {}
        self._limits: dict[str, tuple[int, Refusal | None, Respond | None]] = {}

    def validation(self, prefix: str, handler: ValidationHandler) -> None:
        """Обработчик RequestValidationError для путей с этим префиксом; повтор префикса — ValueError."""
        _check_prefix(prefix, self._handlers, "обработчик 422")
        self._handlers[prefix] = handler

    def http_exception(self, prefix: str, handler: HttpExceptionHandler) -> None:
        """Обработчик HTTPException Starlette (404 и 405 маршрутизации, отказы зависимостей) для путей с этим
        префиксом; повтор префикса — ValueError."""
        _check_prefix(prefix, self._http_handlers, "обработчик HTTPException")
        self._http_handlers[prefix] = handler

    def http_handler_for(self, path: str) -> HttpExceptionHandler | None:
        return _longest(self._http_handlers, path)

    def body_limit(self, prefix: str, max_bytes: int, *, refusal: Refusal | None = None,
                   respond: Respond | None = None) -> None:
        """Предел тела для путей с этим префиксом; refusal(предел) — тело ответа 413 вместо {"detail": …};
        respond(scope, предел) — ответ целиком (код и тело по области запроса). Оба сразу — ValueError."""
        _check_prefix(prefix, self._limits, "предел тела")
        if max_bytes < 1:
            raise ValueError(f"предел тела {prefix!r}: нужно не меньше 1 байта, получено {max_bytes}")
        if refusal is not None and respond is not None:
            raise ValueError(f"предел тела {prefix!r}: refusal или respond, не оба")
        self._limits[prefix] = (max_bytes, refusal, respond)

    def handler_for(self, path: str) -> ValidationHandler | None:
        return _longest(self._handlers, path)

    def limit_for(self, path: str) -> int:
        found = _longest(self._limits, path)
        return DEFAULT_BODY_LIMIT if found is None else found[0]

    def refusal_for(self, path: str) -> Refusal | None:
        found = _longest(self._limits, path)
        return None if found is None else found[1]

    def respond_for(self, path: str) -> Respond | None:
        found = _longest(self._limits, path)
        return None if found is None else found[2]


def hooks_of(app: FastAPI) -> AppHooks:
    """Реестр крючков приложения: его ставит create_app до роутеров ролей."""
    return app.state.pcbk_hooks


class BodyLimit:
    """Предел тела запроса — чистое ASGI-звено до маршрутизации (по образцу RequestBodyLimitMiddleware mcp).

    Без hooks — постоянный предел max_bytes (как в Д3б); с hooks — предел hooks.limit_for(путь), а
    max_bytes не действует. Сначала Content-Length; затем тело читается целиком, но не больше предела + 1
    байта, и только потом зовётся приложение с тем же телом заново. Так 413 выходит и на теле без длины, и
    на любом пути: исключение из receive FastAPI превратил бы в 400, а транспорт MCP — в 500. После
    выданного буфера receive передаёт вызовы исходному: иначе потоковый ответ не узнает об обрыве
    клиента (http.disconnect).
    """

    def __init__(self, app: ASGIApp, hooks: AppHooks | None = None, max_bytes: int = DEFAULT_BODY_LIMIT):
        self.app = app
        self.hooks = hooks
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.max_bytes if self.hooks is None else self.hooks.limit_for(scope.get("path", ""))
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                size = None
            if size is not None and size > limit:
                await self._too_large(scope, receive, send, limit)
                return
        body = bytearray()
        got_request = complete = False
        trailing: Message | None = None
        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailing = message
                break
            got_request = True
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await self._too_large(scope, receive, send, limit)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                complete = True
                break
        replay: deque[Message] = deque()
        if got_request:
            replay.append({"type": "http.request", "body": bytes(body), "more_body": not complete})
        if trailing is not None:
            replay.append(trailing)

        async def again() -> Message:
            if replay:
                return replay.popleft()
            return await receive()

        await self.app(scope, again, send)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        path = scope.get("path", "")
        log.warning("outcome=too_large path=%s limit=%d", safe_path(path), limit)
        respond = None if self.hooks is None else self.hooks.respond_for(path)
        if respond is not None:
            await respond(scope, limit)(scope, receive, send)
            return
        refusal = None if self.hooks is None else self.hooks.refusal_for(path)
        body = refusal(limit) if refusal is not None else {"detail": f"тело запроса больше {size_text(limit)}"}
        await JSONResponse(body, status_code=413)(scope, receive, send)
