"""Прокси вызовов адаптера OpenCode /pcbk/api/oc/* к месту студента (Д5-R32…R37, задача Б3).

Порядок проверок (Д5-R32): зона (front_only, вне pcbk-front — 404) → ошибка настройки (configured, 503) → вход
(current_account, 401) — зависимости роутера и маршрута, до обработчика; затем в обработчике: преподаватель — 403;
источник (не-GET — same_origin, у GET — Sec-Fetch-Site вне same-origin/none) — 403 reason=origin; сырой путь и
правило белого списка (rules.match) — 403; готовность места — 503 «спит», место не будится; тело — 400.

Сырой путь — scope["raw_path"]: он обязан начинаться ровно с байт /pcbk/api/oc, за которыми «/» или конец.
Starlette ведёт маршрут по раскодированному path, и /pcbk/api/o%63/… тоже приходит сюда — такой путь отказ
(reason=bad_path), остаток сверяет rules.match. Путь к месту собирается правилом из проверенных id, строка
запроса — только ключи правила (filter_query), тело — сборщиком правила из разобранного JSON (не JSON, целое
длиннее 4300 цифр, вложенность глубже предела разбора, не UTF-8 — 400 bad_body постоянным текстом). Как есть к
месту не уходит ничего: заголовки ставит Workplaces (Authorization — пароль места, известный только шлюзу).

Особые правила: summarize — 200 true без места; prompt_async с agent — сначала GET /agent своего места
(Д5-R33); permission_reply с once — сначала GET /permission (Д5-R34); event — поток задачи Б4, до неё 503
WorkplaceNotReady. Не-GET — активность места (touch) до пересылки. Проверка агента или разрешения и пересылка —
под одним сроком PROXY_DEADLINE_S.

Ответ места: 401 — 502 place_auth (place_auth_failed); нет ответа (любая httpx.HTTPError, OSError, срок хода) —
503 no_answer, место неготово, upstream_failed; ответ больше UPSTREAM_MAX_BYTES — 502 too_big (читается кусками,
дальше предела не идёт); иначе тот же код и тело, из заголовков — только Content-Type.

Журнал (Д5-R37): отказ — строка «gw refused» (login, place, method, path до LOG_PATH_CHARS без строки запроса,
reason, status) и счётчик refused; отказы not_ready — одна строка на место за NOT_READY_LOG_S с числом, без
счётчика; вызов места — строка «gw oc». Ни строки запроса, ни тел, ни заголовков, ни имени агента; исключения —
только классом (Ш11, Ш12).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .accounts import Account
from .api import GW_MESSAGES, PREFIX, configured, current_account, front_only, gw_error, same_origin
from .places import PlaceAnswer, PlaceAnswerTooLarge
from .rules import AGENT_NAME, APPROVABLE, METHODS, RULE_MESSAGES, Refused, Rule, filter_query, match

if TYPE_CHECKING:
    from . import GatewayRole

log = logging.getLogger("pcbk_core.gateway.proxy")

OC_PREFIX = PREFIX + "/oc"
_RAW_PREFIX = OC_PREFIX.encode("ascii")
UPSTREAM_MAX_BYTES = 4 * 1024 * 1024        # ответ места в памяти шлюза (Д5-R36)
PROXY_DEADLINE_S = 25.0                     # весь ход вызова; edge на /pcbk/api/oc/ ждёт 45 с (Д5-R30)
NOT_READY_LOG_S = 60.0
LOG_PATH_CHARS = 120
AGENT_MODES = ("primary", "all")            # Д5-R33
GET_SITES = ("same-origin", "none")         # Sec-Fetch-Site, с которым GET пропускается (Д5-R26, р4)
CONTENT_TYPE_CHARS = 200

PROXY_MESSAGES = {
    "not_whitelisted": RULE_MESSAGES["not_whitelisted"],        # тот же объект, что у правил Б2
    "teacher": "Просмотр разговоров студентов — в следующих выпусках стенда",
    "not_ready": "Рабочее место спит — разбудите его на странице входа",
    "no_answer": "Рабочее место не отвечает — разбудите его на странице входа",
    "place_auth": "Рабочее место не приняло пароль шлюза — сообщите администратору",
    "agent": "Агент «{agent}» недоступен",
    "permission": "Это разрешение даёт только преподаватель",
    "too_big": "Ответ рабочего места слишком большой",
    "streams": "Открыто слишком много вкладок чата — закройте лишние",
}

_BAD_JSON = object()


class _PlaceAuth(Exception):
    """Место ответило 401 на запрос шлюза: пароль места не тот."""


class _Call(NamedTuple):
    """Поля строк журнала вызова: логин, место «NN» (у преподавателя «-»), метод, путь для журнала."""
    login: str
    place: str
    method: str
    path: str


class _NotReadyLog:
    """Отказы not_ready места — одна строка журнала за NOT_READY_LOG_S с числом отказов с прошлой строки (Д5-R37):
    цикл переподключения адаптера даёт до 6 запросов в секунду на вкладку."""

    def __init__(self, monotonic: Callable[[], float]):
        self._monotonic = monotonic
        self._last: dict[int, float] = {}
        self._count: dict[int, int] = {}

    def refused(self, n: int) -> int | None:
        """Учесть отказ места n; число для строки журнала или None — строку не писать."""
        self._count[n] = self._count.get(n, 0) + 1
        now = self._monotonic()
        last = self._last.get(n)
        if last is not None and now - last < NOT_READY_LOG_S:
            return None
        self._last[n] = now
        count, self._count[n] = self._count[n], 0
        return count


# --- путь, источник, тело ---

def _raw_rest(scope: dict) -> str | None:
    """Сырой путь после /pcbk/api/oc: raw_path начинается ровно с этих байт, за ними «/» или конец; иначе (в том
    числе /pcbk/api/o%63/…, который Starlette привёл сюда по раскодированному path) и у не-ASCII — None."""
    raw = scope.get("raw_path")
    if not isinstance(raw, bytes):
        return None
    raw = raw.split(b"?", 1)[0]
    if not raw.startswith(_RAW_PREFIX):
        return None
    rest = raw[len(_RAW_PREFIX):]
    if rest and not rest.startswith(b"/"):
        return None
    try:
        return rest.decode("ascii")
    except UnicodeDecodeError:
        return None


def _log_path(scope: dict) -> str:
    """Путь для строки журнала: сырой, без строки запроса, после /pcbk/api/oc (если начинается с него), управляющие
    и не-ASCII — экранами, не длиннее LOG_PATH_CHARS."""
    raw = scope.get("raw_path")
    raw = raw.split(b"?", 1)[0] if isinstance(raw, bytes) else b""
    if raw.startswith(_RAW_PREFIX + b"/"):
        raw = raw[len(_RAW_PREFIX):]
    return ascii(raw.decode("latin-1"))[1:-1][:LOG_PATH_CHARS]


def _check_source(request: Request, origin: str) -> None:
    """Д5-R26: запись — только со своей страницы (same_origin); GET с Sec-Fetch-Site вне same-origin/none — тоже
    отказ (р4). Без заголовка GET проходит: его шлёт не всякий клиент."""
    if request.method == "GET":
        site = request.headers.get("sec-fetch-site")
        if site is None or site in GET_SITES:
            return
    elif same_origin(request, origin):
        return
    raise Refused(403, "Forbidden", GW_MESSAGES["origin"], "origin")


async def _body(request: Request, rule: Rule) -> dict | None:
    """Тело к месту — от сборщика правила; у правила без сборщика — None (тело браузера не читается). Пустое тело —
    None для сборщика; не разобранный JSON — 400 bad_body постоянным текстом (текст исключения не держится)."""
    if rule.body is None:
        return None
    raw = await request.body()
    try:
        parsed: Any = json.loads(raw) if raw.strip() else None
    except (ValueError, RecursionError, UnicodeDecodeError):       # UnicodeDecodeError — подкласс ValueError
        parsed = _BAD_JSON
    if parsed is _BAD_JSON:
        raise Refused(400, "BadRequest", RULE_MESSAGES["bad_body"], "bad_body")
    return rule.body(parsed)


# --- проверки у места ---

async def _place_list(role: GatewayRole, n: int, path: str) -> list:
    """GET списка своего места (/agent, /permission): 401 — _PlaceAuth; не 200, не JSON, не список — пустой."""
    answer = await role.workplaces.fetch(n, "GET", path, max_bytes=UPSTREAM_MAX_BYTES)
    if answer.status == 401:
        raise _PlaceAuth()
    if answer.status != 200:
        return []
    try:
        items = json.loads(answer.body)
    except (ValueError, RecursionError):
        return []
    return items if isinstance(items, list) else []


async def _check_agent(role: GatewayRole, n: int, name: str) -> None:
    """Д5-R33: агент есть в GET /agent своего места, native строго false, mode — primary или all; иначе 400.
    Имя в текст отказа — только прошедшее AGENT_NAME (Ш12); в журнал оно не идёт."""
    if not isinstance(name, str) or AGENT_NAME.fullmatch(name) is None:      # сборщик уже сверил — на всякий случай
        raise Refused(400, "BadRequest", RULE_MESSAGES["agent_name"], "bad_body")
    agents = await _place_list(role, n, "/agent")
    if not any(isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"] == name
               and a.get("native") is False and isinstance(a.get("mode"), str) and a["mode"] in AGENT_MODES
               for a in agents):
        raise Refused(400, "BadRequest", PROXY_MESSAGES["agent"].format(agent=name), "agent")


async def _check_permission(role: GatewayRole, n: int, per: str) -> None:
    """Д5-R34: once — только висящему запросу своего места с permission из APPROVABLE; иначе 403."""
    pending = await _place_list(role, n, "/permission")
    if not any(isinstance(p, dict) and p.get("id") == per and isinstance(p.get("permission"), str)
               and p["permission"] in APPROVABLE for p in pending):
        raise Refused(403, "Forbidden", PROXY_MESSAGES["permission"], "permission")


# --- ответы и журнал ---

def _refused(role: GatewayRole, call: _Call, refusal: Refused) -> JSONResponse:
    role.stats.event("refused")
    log.warning("gw refused login=%s place=%s method=%s path=%s reason=%s status=%d", call.login, call.place,
                call.method, call.path, refusal.reason, refusal.status)
    return gw_error(refusal.status, refusal.name, refusal.message)


def _oc_line(call: _Call, rule: Rule, status: int, started: float, tail: str = "", *, warn: bool = False) -> None:
    ms = round((time.monotonic() - started) * 1000)
    (log.warning if warn else log.info)("gw oc login=%s place=%s rule=%s status=%d ms=%d%s", call.login,
                                        call.place, rule.name, status, ms, tail)


def _answer(answer: PlaceAnswer) -> Response:
    """Ответ места браузеру: тот же код, тело как есть, из заголовков — только Content-Type (печатный ASCII)."""
    ct = answer.content_type
    ok = ct is not None and len(ct) <= CONTENT_TYPE_CHARS and ct.isascii() and ct.isprintable()
    return Response(answer.body, status_code=answer.status, headers={"content-type": ct} if ok else None)


# --- маршрут ---

def proxy_router(role: GatewayRole) -> APIRouter:
    """/pcbk/api/oc/{rest:path} и /pcbk/api/oc на методы METHODS: зависимости front_only и configured — у роутера,
    вход — у обработчика; ставится до catch_all_router."""
    router = APIRouter(dependencies=[Depends(front_only), Depends(configured(role))])
    account_of = current_account(role)
    not_ready = _NotReadyLog(role.monotonic)

    async def oc(request: Request, account: Account = Depends(account_of)) -> Response:
        return await _proxy(role, request, account, not_ready)

    for path in (OC_PREFIX + "/{rest:path}", OC_PREFIX):
        router.add_api_route(path, oc, methods=list(METHODS), include_in_schema=False)
    return router


async def _proxy(role: GatewayRole, request: Request, account: Account, not_ready: _NotReadyLog) -> Response:
    method, n = request.method, account.place
    call = _Call(account.login, "-" if n is None else f"{n:02d}", method, _log_path(request.scope))
    rest = _raw_rest(request.scope)
    try:
        if account.role != "student" or n is None:
            raise Refused(403, "Forbidden", PROXY_MESSAGES["teacher"], "teacher")
        _check_source(request, role.gw.GW_ORIGIN)
        if rest is None:
            raise Refused(403, "Forbidden", PROXY_MESSAGES["not_whitelisted"], "bad_path")
        rule, upstream = match(method, rest)
    except Refused as refusal:
        return _refused(role, call, refusal)

    if rule.kind == "event":                        # поток /event — задача Б4; до неё место не трогается
        return gw_error(503, "WorkplaceNotReady", PROXY_MESSAGES["no_answer"])
    if not role.workplaces.is_ready(n):             # не будит: будит только страница входа
        count = not_ready.refused(n)
        if count is not None:
            log.info("gw refused login=%s place=%s method=%s path=%s reason=not_ready status=503 n=%d",
                     call.login, call.place, call.method, call.path, count)
        return gw_error(503, "WorkplaceNotReady", PROXY_MESSAGES["not_ready"])
    if rule.kind == "summarize":                    # сжатие разговора на стенде не нужно: ответ без места
        _oc_line(call, rule, 200, time.monotonic())
        return JSONResponse(True)
    try:
        body = await _body(request, rule)
    except Refused as refusal:
        return _refused(role, call, refusal)
    params = filter_query(rule, request.query_params.multi_items())

    if method != "GET":                             # действие студента — активность места до пересылки (Д5-R28)
        role.workplaces.touch(n)
    started = time.monotonic()
    try:
        async with asyncio.timeout(PROXY_DEADLINE_S):
            if rule.name == "prompt_async" and body is not None and "agent" in body:
                await _check_agent(role, n, body["agent"])
            elif rule.name == "permission_reply" and body is not None and body.get("reply") == "once":
                await _check_permission(role, n, rule.pattern.fullmatch(rest)["PER"])
            answer = await role.workplaces.fetch(n, method, upstream, params=params, json=body,
                                                 max_bytes=UPSTREAM_MAX_BYTES)
    except Refused as refusal:
        return _refused(role, call, refusal)
    except _PlaceAuth:
        answer = PlaceAnswer(401, None, b"")
    except PlaceAnswerTooLarge:
        _oc_line(call, rule, 502, started, " reason=too_big", warn=True)
        return gw_error(502, "BadGateway", PROXY_MESSAGES["too_big"])
    except (httpx.HTTPError, OSError) as exc:       # TimeoutError срока хода — подкласс OSError (Ш11)
        role.workplaces.mark_unready(n)
        role.stats.event("upstream_failed")
        _oc_line(call, rule, 503, started, f" reason=no_answer error={type(exc).__name__}", warn=True)
        return gw_error(503, "WorkplaceNotReady", PROXY_MESSAGES["no_answer"])
    if answer.status == 401:                        # без WWW-Authenticate места
        role.stats.event("place_auth_failed")
        _oc_line(call, rule, 502, started, " reason=place_auth", warn=True)
        return gw_error(502, "BadGateway", PROXY_MESSAGES["place_auth"])
    _oc_line(call, rule, answer.status, started)
    return _answer(answer)
