"""Ручки шлюза (Д5-R25…R27, Д5-R31): вход, выход, «кто я», рабочее место и его пробуждение.

Все пути — под /pcbk/api/, ответы об ошибках — JSON вида OpenCode {"name", "data": {"message"}} без
WWW-Authenticate. Порядок проверок: зона (front_only, вне pcbk-front — 404) → ошибка настройки (configured,
503) → источник записи (Origin или Sec-Fetch-Site, 403) → сессия (current_account, 401). Зависимости
отказывают исключением GatewayRefusal с готовым ответом; его отдаёт обработчик HTTPException роли,
поставленный через реестр hooks_of(app).http_exception.

Сессия — cookie pcbk_session (HttpOnly; Secure; SameSite=Strict; Path=/pcbk/api/), в базе — sha256 значения.
Две и больше pcbk_session в запросе — все предъявленные сессии закрываются, 401 и набор удалений cookie
(clear_cookies): строки собираются здесь, короткие, сумма байт ограничена COOKIE_HEADERS_BUDGET при любом
Host. Номер места — только из учётки сессии.

Вход: замок (store.locked) → очередь сверок (не больше LOGIN_PARALLEL попыток разом, ждущих — не больше
LOGIN_QUEUE, иначе 429 без scrypt) → в одном задании потока повторная проверка замка и сверка. Неудача —
401 сразу; запись неудачи в замок и строка журнала — в задаче попытки после итога, ответ их не ждёт (время
ответа не выдаёт, существует ли логин), а место очереди освобождается только после записи (LoginGate).
Нет маршрута или метода под /pcbk/api/ и на /health/gateway — 404 без Allow в любой зоне; предел тела вне
pcbk-front — тоже 404. В журнале нет ни паролей, ни cookie, ни заголовков; логин — только существующий,
иначе «<неизвестный>».
"""
import asyncio
import ipaddress
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from fastapi.exception_handlers import http_exception_handler as fastapi_http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Receive, Scope, Send

from .. import zones
from ..hooks import safe_path
from . import clip
from .accounts import Account, AccountStore, StoreError

if TYPE_CHECKING:
    from . import GatewayRole

log = logging.getLogger("pcbk_core.gateway.api")

PREFIX = "/pcbk/api"
COOKIE = "pcbk_session"
COOKIE_PATH = PREFIX + "/"
LOGIN_PARALLEL = 2
LOGIN_QUEUE = 8

GW_MESSAGES = {
    "need_login": "Нужно войти",
    "bad_credentials": "Неверный логин или пароль",
    "locked": "Слишком много неудачных попыток — подождите 15 минут",
    "busy": "Вход перегружен — повторите через минуту",
    "origin": "Запрос не с нашей страницы — отклонён",
    "no_place": "У преподавателя нет рабочего места",
    "bad_body": "Неверный запрос",
    "not_found": "Не найдено",
    "unavailable": "Шлюз не настроен: ",
    "store": "Учётные записи недоступны — повторите позже",
}
ERROR_NAMES = {400: "BadRequest", 401: "Unauthorized", 403: "Forbidden", 404: "NotFound", 405: "MethodNotAllowed",
               429: "TooManyAttempts", 503: "Unavailable"}
CONFIG_ERROR_SHOWN = 60                 # знаков причины в ответе 503 «Шлюз не настроен: …»

# удаления cookie (Д5-R25, Д5-R30): пути не длиннее самого длинного пути белого списка, не больше трёх
# родительских доменов и только у допустимого имени DNS; сумма байт строк — не больше бюджета
COOKIE_PATH_MAX_LEN = 64
COOKIE_MAX_DOMAINS = 3
COOKIE_HEADERS_BUDGET = 16 * 1024
MAX_SESSION_COOKIES = 8
HOST_NAME = re.compile(r"[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63}){0,5}")
HOST_MAX_LEN = 253
# path-value RFC 6265: печатные ASCII без «;»; прочее в атрибут Path не ставится
_PATH_VALUE = re.compile(r"[!-:<-~]{1,64}")
_DELETE_HEAD = f"{COOKIE}=; Max-Age=0; Path="
_SECURE = "; Secure"
_DOMAIN = "; Domain="
_PARTITIONED = "; Partitioned"
_HEADER_OVERHEAD = len("set-cookie: ") + 2      # имя заголовка и CRLF — как считает бюджет edge

# --- ответы ---

def gw_error(status: int, name: str, message: str, headers=None) -> JSONResponse:
    """Ошибка вида OpenCode: {"name", "data": {"message"}}; WWW-Authenticate не ставится."""
    return JSONResponse({"name": name, "data": {"message": message}}, status_code=status, headers=headers)


def _error(status: int, key: str, *, background: BackgroundTask | None = None) -> JSONResponse:
    response = gw_error(status, ERROR_NAMES[status], GW_MESSAGES[key])
    response.background = background
    return response


def _with_cookies(response: Response, lines: list[str]) -> Response:
    """Несколько Set-Cookie — сырыми заголовками: словарь headers их не держит."""
    response.raw_headers.extend((b"set-cookie", line.encode("latin-1")) for line in lines)
    return response


class GatewayRefusal(StarletteHTTPException):
    """Отказ зависимости шлюза готовым ответом: обработчик HTTPException роли отдаёт response как есть."""

    def __init__(self, response: Response):
        super().__init__(response.status_code)
        self.response = response


def _refusal(status: int, key: str, cookies: list[str] | None = None) -> GatewayRefusal:
    return GatewayRefusal(_with_cookies(_error(status, key), cookies or []))


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """HTTPException под /pcbk/api/ и /health/gateway: отказ шлюза — как есть; 404 и 405 маршрутизации — 404
    not_found без Allow (список методов не подсказывает, какие ручки есть, в любой зоне — ревью А4, I1);
    прочие — JSON вида OpenCode с тем же кодом, без WWW-Authenticate."""
    if isinstance(exc, GatewayRefusal):
        return exc.response
    status = exc.status_code
    if status in (404, 405):
        return _error(404, "not_found")
    if status < 400:
        return await fastapi_http_exception_handler(request, exc)
    name = ERROR_NAMES.get(status, "Unavailable" if status >= 500 else "BadRequest")
    key = {401: "need_login", 404: "not_found"}.get(status, "bad_body")
    headers = {k: v for k, v in (exc.headers or {}).items() if k.lower() != "www-authenticate"}
    return gw_error(status, name, GW_MESSAGES[key], headers=headers or None)


def too_large(scope: Scope, limit: int) -> Response:
    """413 под /pcbk/api/ (предел тела — до маршрутизации): вне pcbk-front — 404, как отказ зоны, иначе
    размер тела выдавал бы ручки шлюза в сетях мест (ревью А4, M3)."""
    if not zones.is_front(scope):
        return _error(404, "not_found")
    return gw_error(413, "PayloadTooLarge", "Запрос слишком большой")


# --- источник записи (Д5-R26) ---

def same_origin(request: Request, origin: str) -> bool:
    """Запись — только со своей страницы: Sec-Fetch-Site: same-origin или Origin, в точности равный origin.
    Пустой origin по Origin не совпадает ни с чем, в том числе с отсутствующим заголовком."""
    if request.headers.get("sec-fetch-site") == "same-origin":
        return True
    return bool(origin) and request.headers.get("origin") == origin


def origin_checked(role: "GatewayRole") -> Callable[[Request], Awaitable[None]]:
    """Зависимость записей шлюза: чужой источник — 403, счётчик origin_refused и строка журнала."""
    async def dependency(request: Request) -> None:
        if not same_origin(request, role.gw.GW_ORIGIN):
            role.stats.event("origin_refused")
            log.warning("gw origin refused path=%s", safe_path(request.scope["path"]))
            raise _refusal(403, "origin")
    return dependency


# --- cookie ---

def session_cookie(token: str) -> str:
    """Set-Cookie сессии (Д5-R25): без Max-Age — сессионная cookie браузера, срок держит сервер."""
    return f"{COOKIE}={token}; Path={COOKIE_PATH}; Secure; HttpOnly; SameSite=Strict"


def session_tokens(request: Request) -> list[str]:
    """Все значения pcbk_session из сырых заголовков Cookie по порядку (Starlette оставил бы последнее)."""
    values = []
    for header in request.headers.getlist("cookie"):
        for part in header.split(";"):
            name, eq, value = part.partition("=")
            if eq and name.strip() == COOKIE:
                values.append(value.strip())
    return values


def _request_path(request: Request) -> str:
    raw = request.scope.get("raw_path")
    if isinstance(raw, bytes):
        return raw.decode("latin-1")
    return request.scope.get("path", "/")


def _cookie_paths(path: str) -> list[str]:
    """Пути cookie, совпадающие с путём запроса (RFC 6265 §5.1.4): «/», каждый предок с «/» на конце и без,
    сам путь; не длиннее COOKIE_PATH_MAX_LEN и только допустимые path-value."""
    if not path.startswith("/"):
        return ["/"]
    found = ["/"]
    for i in range(1, min(len(path), COOKIE_PATH_MAX_LEN + 1)):
        if path[i] == "/":
            found += [path[:i], path[:i + 1]]
    found.append(path[:COOKIE_PATH_MAX_LEN + 1])
    return [p for p in dict.fromkeys(found) if p == "/" or _PATH_VALUE.fullmatch(p)]


def _request_host(request: Request) -> str:
    """Хост запроса без порта: X-Forwarded-Host (его ставит edge), без него — Host; в нижнем регистре."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if not host.startswith("["):
        name, sep, port = host.rpartition(":")
        if sep and port.isascii() and port.isdigit():
            host = name
    return host.lower()


def _cookie_domains(host: str) -> list[str]:
    """Родительские домены не короче двух меток — не больше COOKIE_MAX_DOMAINS самых коротких; только у
    допустимого имени DNS (HOST_NAME, не длиннее 253 знаков), у адреса IP и прочего — ни одного."""
    if len(host) > HOST_MAX_LEN or HOST_NAME.fullmatch(host) is None:
        return []
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return []
    labels = host.split(".")
    return [".".join(labels[-k:]) for k in range(2, min(len(labels), COOKIE_MAX_DOMAINS + 1) + 1)]


def _deletes_size(paths: list[str], domains: list[str]) -> int:
    """Сумма байт строк удалений с именем заголовка — до сборки строк."""
    extras = [0] + [len(_DOMAIN) + len(d) for d in domains]
    total = 0
    for p in paths:
        for extra in extras:
            line = _HEADER_OVERHEAD + len(_DELETE_HEAD) + len(p) + len(_SECURE) + extra
            total += 2 * line + len(_PARTITIONED)
    return total


def clear_cookies(request: Request) -> list[str]:
    """Удаляющие Set-Cookie (Д5-R25): все сочетания путей, доменов (host-only и родительские) и раздела.
    Сумма больше COOKIE_HEADERS_BUDGET — только host-only (пути × 2)."""
    paths = _cookie_paths(_request_path(request))
    domains = _cookie_domains(_request_host(request))
    if domains and _deletes_size(paths, domains) > COOKIE_HEADERS_BUDGET:
        domains = []
    lines = []
    for p in paths:
        for domain in (None, *domains):
            line = _DELETE_HEAD + p + _SECURE + ("" if domain is None else _DOMAIN + domain)
            lines += [line, line + _PARTITIONED]
    return lines


# --- зависимости ---

async def front_only(request: Request) -> None:
    """Ручки шлюза — только на адресе core в pcbk-front и на петле (Д5-R31); иначе 404."""
    if not zones.is_front(request.scope):
        raise _refusal(404, "not_found")


def configured(role: "GatewayRole") -> Callable[[], Awaitable[None]]:
    """Ошибка настройки шлюза — 503 «Шлюз не настроен: <причина>»."""
    async def dependency() -> None:
        if role.config_error:
            reason = GW_MESSAGES["unavailable"] + clip(role.config_error, CONFIG_ERROR_SHOWN)
            raise GatewayRefusal(gw_error(503, ERROR_NAMES[503], reason))
    return dependency


def _close_sessions(store: AccountStore, tokens: list[str]) -> list[str]:
    """Логины действующих сессий среди tokens, затем закрытие каждой; в потоке."""
    live = []
    for token in tokens:
        account = store.session(token)
        if account is not None:
            live.append(account.login)
    for token in tokens:
        store.close_session(token)
    return live


def current_account(role: "GatewayRole") -> Callable[[Request], Awaitable[Account]]:
    """Учётка сессии. Нет cookie или сессии — 401; две и больше pcbk_session — все закрываются (первые
    MAX_SESSION_COOKIES), 401 и удаления cookie; сбой базы — 503."""
    async def dependency(request: Request) -> Account:
        tokens = session_tokens(request)
        if not tokens:
            raise _refusal(401, "need_login")
        if len(tokens) > 1:
            try:
                live = await asyncio.to_thread(_close_sessions, role.store, tokens[:MAX_SESSION_COOKIES])
            except StoreError:
                raise _refusal(503, "store") from None
            if live:                    # мусорные значения строку сторожа не красят
                role.stats.event("cookies_duplicate")
                log.warning("gw cookies duplicate n=%d live=%d logins=%s", len(tokens), len(live),
                            ",".join(dict.fromkeys(live)))
            raise _refusal(401, "need_login", clear_cookies(request))
        try:
            account = await asyncio.to_thread(role.store.session, tokens[0])
        except StoreError:
            raise _refusal(503, "store") from None
        if account is None:
            raise _refusal(401, "need_login")
        return account
    return dependency


# --- вход ---

def _credentials(raw: bytes) -> tuple[str, str] | None:
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if not isinstance(body, dict):
        return None
    login, password = body.get("login"), body.get("password")
    if not isinstance(login, str) or not isinstance(password, str):
        return None
    return login, password


# итог сверки в задании потока: логин заперт — сверки не было (F4)
LOCKED = object()


def _check_and_verify(store: AccountStore, login: str, password: str) -> Account | object | None:
    """Замок и сверка в одном задании потока, уже с местом в очереди: пока запрос ждал, неудачи соседних
    попыток того же логина могли его запереть (ревью А4, M1) — тогда LOCKED без scrypt."""
    if store.locked(login):
        return LOCKED
    return store.verify(login, password)


def _refused_line(store: AccountStore, login: str, kind: str) -> None:
    """В потоке, после ответа: неудача сверки — в замок; строка «gw login refused» — логин, только если он
    существует (точечный запрос store.status). kind: password (сверка не прошла), locked, busy."""
    if kind == "password":
        try:
            store.failure(login)
        except Exception as exc:
            log.warning("шлюз: неудачный вход не записан (%s)", type(exc).__name__)
    shown, reason = "<неизвестный>", "unknown" if kind == "password" else kind
    try:
        status = store.status(login)
    except Exception:                   # строка журнала — без логина
        status = None
    if status is not None:
        shown = login
        if kind == "password":
            reason = "disabled" if status == "disabled" else "password"
    log.warning("gw login refused login=%s reason=%s", shown, reason)


def _after_verify(store: AccountStore, login: str) -> Callable[[object], None]:
    """after попытки LoginGate: неудача сверки — в замок и в журнал, замок — в журнал; удача — ничего."""
    def after(result: object) -> None:
        if result is None:
            _refused_line(store, login, "password")
        elif result is LOCKED:
            _refused_line(store, login, "locked")
    return after


def _place_nn(place: int | None) -> str:
    return "-" if place is None else f"{place:02d}"


def auth_router(role: "GatewayRole") -> APIRouter:
    """POST /pcbk/api/auth/login, POST /pcbk/api/auth/logout, GET /pcbk/api/auth/me."""
    router = APIRouter()
    origin = Depends(origin_checked(role))
    account_of = current_account(role)

    @router.post(f"{PREFIX}/auth/login", dependencies=[origin])
    async def login(request: Request) -> Response:
        creds = _credentials(await request.body())
        if creds is None:
            return _error(400, "bad_body")
        name, password = creds
        store = role.store
        try:
            locked = await asyncio.to_thread(store.locked, name)
        except StoreError:
            return _error(503, "store")
        if locked:
            return _error(429, "locked", background=BackgroundTask(_refused_line, store, name, "locked"))
        gate = role.login_gate
        if gate.full():                  # без scrypt
            role.stats.event("login_busy")
            return _error(429, "busy", background=BackgroundTask(_refused_line, store, name, "busy"))
        try:
            # неудача пишется в замок после ответа, но до того, как место очереди отдано следующей попытке
            account = await gate.attempt(lambda: _check_and_verify(store, name, password),
                                         _after_verify(store, name))
        except StoreError:
            return _error(503, "store")
        if account is LOCKED:
            return _error(429, "locked")
        if account is None:
            role.stats.event("login_failed")
            return _error(401, "bad_credentials")
        try:
            token = await asyncio.to_thread(store.open_session, account.login)
        except StoreError:
            return _error(503, "store")
        except ValueError:              # учётку отключили между сверкой и сессией — как неверный вход
            role.stats.event("login_failed")
            return _error(401, "bad_credentials", background=BackgroundTask(_refused_line, store, name, "password"))
        log.info("gw login ok login=%s role=%s place=%s", account.login, account.role, _place_nn(account.place))
        return _with_cookies(JSONResponse(account.to_json()), [session_cookie(token)])

    @router.post(f"{PREFIX}/auth/logout", dependencies=[origin])
    async def logout(request: Request) -> Response:
        tokens = session_tokens(request)[:MAX_SESSION_COOKIES]
        try:
            await asyncio.to_thread(_close_sessions, role.store, tokens)
        except StoreError:
            return _error(503, "store")
        return _with_cookies(JSONResponse({}), clear_cookies(request))

    @router.get(f"{PREFIX}/auth/me")
    async def me(account: Account = Depends(account_of)) -> JSONResponse:
        return JSONResponse(account.to_json())

    return router


# --- рабочее место ---

def workplace_router(role: "GatewayRole") -> APIRouter:
    """GET /pcbk/api/workplace — состояние (не активность); POST /pcbk/api/workplace/wake — пробуждение.
    Номер места — только из учётки сессии; у преподавателя места нет — 403."""
    router = APIRouter()
    account_of = current_account(role)

    @router.get(f"{PREFIX}/workplace")
    async def workplace(account: Account = Depends(account_of)) -> JSONResponse:
        if account.place is None:
            return _error(403, "no_place")
        return JSONResponse(role.workplaces.view(account.place).to_json())

    @router.post(f"{PREFIX}/workplace/wake", dependencies=[Depends(origin_checked(role))])
    async def wake(account: Account = Depends(account_of)) -> JSONResponse:
        if account.place is None:
            return _error(403, "no_place")
        return JSONResponse((await role.workplaces.wake(account.place)).to_json(), status_code=202)

    return router


class _NotFound:
    """ASGI-приложение ловящего маршрута. Экземпляр класса, а не функция: Route Starlette 1.7 функции без
    списка методов даёт ["GET"] (прочие методы — 405 с Allow), а ASGI-приложение пускает на любой метод."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await _error(404, "not_found")(scope, receive, send)


def catch_all_router() -> APIRouter:
    """/pcbk/api/{rest:path} на любой метод — 404 JSON без Allow; ставится последним."""
    router = APIRouter()
    router.add_route(PREFIX + "/{rest:path}", _NotFound(), methods=None, include_in_schema=False)
    return router
