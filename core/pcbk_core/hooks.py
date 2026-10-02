"""Крючки уровня приложения: один обработчик 422, один обработчик HTTPException и один предел тела на
приложение, разбор по префиксу пути.

Роли наполняют реестр AppHooks в install(app) через hooks_of(app): обработчик ошибок разбора тела,
обработчик HTTPException (Д5) и предел тела (с ответом 413 или своим ответом по области запроса) — на свой
префикс пути. create_app после install всех ролей ставит один обработчик RequestValidationError, один
обработчик HTTPException Starlette и одно звено BodyLimit — самым внешним внутри ServerErrorMiddleware, так
что 413 выходит раньше звеньев ролей. Выбор — по самому длинному подходящему префиксу; путь без префикса —
обработчик FastAPI по умолчанию и предел DEFAULT_BODY_LIMIT. Модуль не импортирует pcbk_core.data: реестр
общий для всех ролей.

Общий бюджет байтов тел на префикс (Д5б, доводка Б3, H2) — необязательный: сумма байтов тел, которые сейчас
читаются или ждут обработки по этому префиксу, не больше бюджета. Запрос, чья объявленная длина (или уже
прочитанное) не помещается в остаток, получает over_budget(scope) без дальнейшего чтения; байты возвращаются в
бюджет в finally, когда приложение завершило запрос (или он оборван). budget_admits(scope) — кому бюджет вообще
доступен: прочие получают over_budget(scope) сразу, не читая тела и не занимая бюджета (шлюз: вне pcbk-front — 404,
иначе медленные тела из сети места держали бы бюджет всех студентов). Префикс без budget= ведёт себя как прежде.
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
# ответ на тело сверх общего бюджета префикса — по области запроса (Д5б, H2)
OverBudget = Callable[[Scope], Response]

log = logging.getLogger("pcbk_core.hooks")

_T = TypeVar("_T")


def size_text(n: int) -> str:
    """Предел тела словами: 65536 — «64 КиБ», не кратное КиБ — в байтах."""
    return f"{n // 1024} КиБ" if n % 1024 == 0 else f"{n} байт"


def safe_path(path: str) -> str:
    """Путь для строки журнала: управляющие и не-ASCII — экранами, не длиннее PATH_LOG_CHARS."""
    return ascii(path)[1:-1][:PATH_LOG_CHARS]


def _longest_prefix(table: dict[str, object], path: str) -> str | None:
    return max((p for p in table if path.startswith(p)), key=len, default=None)


def _longest(table: dict[str, _T], path: str) -> _T | None:
    prefix = _longest_prefix(table, path)
    return None if prefix is None else table[prefix]


def _check_prefix(prefix: str, table: dict, what: str) -> None:
    if not prefix.startswith("/"):
        raise ValueError(f"{what}: префикс {prefix!r} — с «/» в начале, иначе он не совпадёт ни с одним путём")
    if prefix in table:
        raise ValueError(f"{what}: префикс {prefix!r} уже занят")


class BodyBudget:
    """Общий бюджет байтов тел префикса (H2): take(n) — занять, если помещается (без await: решение и запись
    атомарны в цикле событий), give(n) — вернуть. used — занято сейчас; admits(scope) — кому бюджет доступен."""

    def __init__(self, total: int, over_budget: OverBudget, admits: Callable[[Scope], bool] | None = None):
        self.total = total
        self.over_budget = over_budget
        self.admits = admits
        self.used = 0

    def take(self, n: int) -> bool:
        if self.used + n > self.total:
            return False
        self.used += n
        return True

    def give(self, n: int) -> None:
        """Вернуть n байт. Вернуть больше занятого — ошибка учёта (двойной возврат): строка WARNING, а не тихая
        обрезка (Б3-m2); занятое — не ниже нуля, иначе бюджет вырос бы сверх total."""
        if n > self.used:
            log.warning("outcome=body_budget_over_return used=%d returned=%d total=%d", self.used, n, self.total)
        self.used = max(0, self.used - n)


class AppHooks:
    """Реестр крючков приложения по префиксу пути: обработчики 422 и HTTPException, пределы тела с ответом 413 и
    необязательным общим бюджетом байтов."""

    def __init__(self) -> None:
        self._handlers: dict[str, ValidationHandler] = {}
        self._http_handlers: dict[str, HttpExceptionHandler] = {}
        self._limits: dict[str, tuple[int, Refusal | None, Respond | None]] = {}
        self._budgets: dict[str, BodyBudget] = {}

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
                   respond: Respond | None = None, budget: int | None = None,
                   over_budget: OverBudget | None = None,
                   budget_admits: Callable[[Scope], bool] | None = None) -> None:
        """Предел тела для путей с этим префиксом; refusal(предел) — тело ответа 413 вместо {"detail": …};
        respond(scope, предел) — ответ целиком (код и тело по области запроса). Оба сразу — ValueError.
        budget — общий бюджет байтов тел префикса (не меньше max_bytes), over_budget(scope) — ответ сверх него;
        одно без другого — ValueError. budget_admits(scope) — кому бюджет доступен (только вместе с budget)."""
        _check_prefix(prefix, self._limits, "предел тела")
        if max_bytes < 1:
            raise ValueError(f"предел тела {prefix!r}: нужно не меньше 1 байта, получено {max_bytes}")
        if refusal is not None and respond is not None:
            raise ValueError(f"предел тела {prefix!r}: refusal или respond, не оба")
        if (budget is None) != (over_budget is None):
            raise ValueError(f"предел тела {prefix!r}: budget и over_budget — только вместе")
        if budget_admits is not None and budget is None:
            raise ValueError(f"предел тела {prefix!r}: budget_admits — только с budget")
        if budget is not None and budget < max_bytes:
            raise ValueError(f"предел тела {prefix!r}: бюджет меньше предела тела — тело предела не поместится")
        self._limits[prefix] = (max_bytes, refusal, respond)
        if budget is not None:
            self._budgets[prefix] = BodyBudget(budget, over_budget, budget_admits)

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

    def budget_for(self, path: str) -> BodyBudget | None:
        """Бюджет префикса, выбранного для пути (самый длинный), или None — у префикса бюджета нет."""
        prefix = _longest_prefix(self._limits, path)
        return None if prefix is None else self._budgets.get(prefix)


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

    У префикса с бюджетом (hooks.budget_for) запрос, которому бюджет не доступен (admits), сразу получает
    over_budget(scope); объявленная длина занимается в бюджете до чтения, тело без длины — по мере чтения; не
    помещается — over_budget(scope) без дальнейшего чтения. Занятое возвращается в finally после приложения: и на
    обрыве, и на отмене.
    """

    def __init__(self, app: ASGIApp, hooks: AppHooks | None = None, max_bytes: int = DEFAULT_BODY_LIMIT):
        self.app = app
        self.hooks = hooks
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        limit = self.max_bytes if self.hooks is None else self.hooks.limit_for(path)
        budget = None if self.hooks is None else self.hooks.budget_for(path)
        declared = Headers(scope=scope).get("content-length")
        size = None
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                size = None
            if size is not None and size > limit:
                await self._too_large(scope, receive, send, limit)
                return
        held = 0                                        # занято в бюджете этим запросом
        try:
            if budget is not None and budget.admits is not None and not budget.admits(scope):
                await budget.over_budget(scope)(scope, receive, send)    # бюджет не для этой зоны: тело не читается
                return
            if budget is not None and size is not None and size > 0:
                if not budget.take(size):
                    await self._over_budget(budget, scope, receive, send)
                    return
                held = size
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
                if budget is not None and len(body) + len(chunk) > held:
                    extra = len(body) + len(chunk) - held
                    if not budget.take(extra):
                        await self._over_budget(budget, scope, receive, send)
                        return
                    held += extra
                body.extend(chunk)
                if not message.get("more_body", False):
                    complete = True
                    break
            replay: deque[Message] = deque()
            if got_request:
                replay.append({"type": "http.request", "body": bytes(body), "more_body": not complete})
            if trailing is not None:
                replay.append(trailing)
            del body

            async def again() -> Message:
                if replay:
                    return replay.popleft()
                return await receive()

            await self.app(scope, again, send)
        finally:
            if budget is not None and held:
                budget.give(held)

    async def _over_budget(self, budget: BodyBudget, scope: Scope, receive: Receive, send: Send) -> None:
        log.warning("outcome=body_budget path=%s used=%d total=%d", safe_path(scope.get("path", "")), budget.used,
                    budget.total)
        await budget.over_budget(scope)(scope, receive, send)

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
