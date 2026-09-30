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

Занятость (доводка Б3, G1): к одному месту разом — не больше PROXY_PARALLEL вызовов (сверка, пересылка и отправка
ответа браузеру — один вызов: слот отдаёт _PlaceResponse, когда ответ отправлен), ждущих — не больше PROXY_QUEUE;
сверх очереди, не дождавшийся слота за PROXY_QUEUE_WAIT_S и PoolTimeout клиента места — 503 WorkplaceBusy «busy»:
отказ со счётчиком refused, место остаётся готовым, upstream_failed не растёт. Ожидание слота — под своим сроком
внутри срока хода (H1): месту после взятия слота остаётся не меньше PLACE_READ_S, и очередь не превращает
занятость в «место не отвечает». Тела запросов /pcbk/api/oc/ до обработчика — в общем бюджете
GW_BODY_BUDGET_BYTES (H2, hooks.body_limit budget=): сверх него — 503 busy без чтения (вне pcbk-front — 404). Так ответы мест в памяти прокси — не больше
10 мест × PROXY_PARALLEL, а потоп одного студента не трогает соседа: у места свой клиент и свой пул соединений
(places.PLACE_CONNECTIONS).

Ответ места: 401 — 502 place_auth (place_auth_failed); нет ответа (любая иная httpx.HTTPError, OSError, срок
хода у места) — 503 no_answer, место неготово, upstream_failed; ответ больше UPSTREAM_MAX_BYTES — 502 too_big
(читается кусками, дальше предела не идёт); иначе тот же код и тело, из заголовков — только Content-Type: как
есть — application/json и text/plain (G7), прочий — application/octet-stream.

Журнал (Д5-R37): отказ — строка «gw refused» (login, place, method, path до LOG_PATH_CHARS без строки запроса,
reason, status; у сверки с не-200 места — place_status) и счётчик refused; отказы not_ready — одна строка на
место за NOT_READY_LOG_S с числом и since_s (сколько секунд копились), без счётчика; вызов места — строка
«gw oc». Ни строки запроса, ни тел, ни заголовков, ни имени агента; исключения — только классом (Ш11, Ш12).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from starlette.types import Receive, Scope, Send

from .accounts import Account
from .. import zones
from .api import ERROR_NAMES, GW_MESSAGES, PREFIX, configured, current_account, front_only, gw_error, same_origin
from .places import PLACE_READ_S, PROXY_PARALLEL, PlaceAnswer, PlaceAnswerTooLarge
from .rules import AGENT_NAME, APPROVABLE, METHODS, RULE_MESSAGES, Refused, Rule, filter_query, match

if TYPE_CHECKING:
    from . import GatewayRole

log = logging.getLogger("pcbk_core.gateway.proxy")

OC_PREFIX = PREFIX + "/oc"
_RAW_PREFIX = OC_PREFIX.encode("ascii")
UPSTREAM_MAX_BYTES = 4 * 1024 * 1024        # ответ места в памяти шлюза (Д5-R36)
SEND_CHUNK = 64 * 1024                      # кусок отправки ответа места браузеру (G1)
PROXY_DEADLINE_S = 25.0                     # весь ход вызова; edge на /pcbk/api/oc/ ждёт 45 с (Д5-R30)
# ожидание слота места внутри срока хода: месту после слота — не меньше PLACE_READ_S (H1)
PROXY_QUEUE_WAIT_S = PROXY_DEADLINE_S - PLACE_READ_S
# тел /pcbk/api/oc/ в памяти до обработчика разом (H2): 32 тела по 512 КиБ
GW_BODY_BUDGET_BYTES = 16 * 1024 * 1024
NOT_READY_LOG_S = 60.0
PROXY_QUEUE = 8                             # ждущих вызовов на место сверх PROXY_PARALLEL (G1)
LOG_PATH_CHARS = 120
AGENT_MODES = ("primary", "all")            # Д5-R33
GET_SITES = ("same-origin", "none")         # Sec-Fetch-Site, с которым GET пропускается (Д5-R26, р4)
CONTENT_TYPE_CHARS = 200
# Content-Type ответа места как есть — только эти виды (с параметрами); прочий — OCTET_STREAM (G7)
PASSED_TYPES = ("application/json", "text/plain")
OCTET_STREAM = "application/octet-stream"

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
    "busy": "Слишком много одновременных запросов — повторите позже",
}

_BAD_JSON = object()


class _PlaceAuth(Exception):
    """Место ответило 401 на запрос шлюза: пароль места не тот."""


class _CheckRefused(Refused):
    """Отказ сверки агента или разрешения; place_status — код ответа места на сверке, если он не 200 (G10)."""

    def __init__(self, status: int, name: str, message: str, reason: str, place_status: int) -> None:
        super().__init__(status, name, message, reason)
        self.place_status = place_status


def _busy() -> Refused:
    return Refused(503, "WorkplaceBusy", PROXY_MESSAGES["busy"], "busy")


class _PlaceGate:
    """Вызовы прокси к месту (G1): не больше PROXY_PARALLEL разом, ждущих — не больше PROXY_QUEUE; сверх —
    _busy() сразу. Семафор места создаётся при первом вызове к нему."""

    def __init__(self) -> None:
        self._sems: dict[int, asyncio.Semaphore] = {}
        self._waiting: dict[int, int] = {}

    async def acquire(self, n: int) -> Callable[[], None]:
        """Занять слот места n (отмена ожидания слот не занимает); отдаёт release — освобождение, повтор — ничего."""
        sem = self._sems.get(n)
        if sem is None:
            sem = self._sems[n] = asyncio.Semaphore(PROXY_PARALLEL)
        if sem.locked() and self._waiting.get(n, 0) >= PROXY_QUEUE:
            raise _busy()
        self._waiting[n] = self._waiting.get(n, 0) + 1
        try:
            await sem.acquire()
        finally:
            self._waiting[n] -= 1
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                sem.release()
        return release


class _PlaceResponse(Response):
    """Ответ места браузеру, держащий слот места до конца отправки (G1). Тело уходит кусками по SEND_CHUNK: uvicorn
    ждёт опустошения буфера сокета перед следующим куском, и медленный браузер задерживает отправку (и слот), а не
    копит всё тело в буфере соединения мимо слота. Тело — как его прочитал fetch (bytearray), без копии целиком.
    Слот освобождается в finally отправки (и при обрыве), а ответ, который так и не отправлялся, — при сборке
    мусора (weakref.finalize): слот не теряется ни при каком исходе."""

    def __init__(self, content: bytes | bytearray, status_code: int, headers: dict[str, str] | None,
                 release: Callable[[], None]) -> None:
        self.status_code = status_code
        self.media_type = None
        self.background = None
        self.body = content                             # Response.render не принимает bytearray — без него
        self.init_headers(headers)
        self._release = weakref.finalize(self, release)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            view = memoryview(self.body)
            for i in range(0, len(view), SEND_CHUNK):
                await send({"type": "http.response.body", "body": bytes(view[i:i + SEND_CHUNK]), "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            if self.background is not None:
                await self.background()
        finally:
            self._release()


class _Call(NamedTuple):
    """Поля строк журнала вызова: логин, место «NN» (у преподавателя «-»), метод, путь для журнала."""
    login: str
    place: str
    method: str
    path: str


class _NotReadyLog:
    """Отказы not_ready места — одна строка журнала за NOT_READY_LOG_S с числом отказов с прошлой строки (Д5-R37):
    цикл переподключения адаптера даёт до 6 запросов в секунду на вкладку. У строки — since_s: сколько секунд
    копились отказы (от первого не записанного до этого), чтобы старое число не выдавало себя за свежее (G8)."""

    def __init__(self, monotonic: Callable[[], float]):
        self._monotonic = monotonic
        self._last: dict[int, float] = {}
        self._first: dict[int, float] = {}
        self._count: dict[int, int] = {}

    def refused(self, n: int) -> tuple[int, int] | None:
        """Учесть отказ места n; (число отказов, since_s) для строки журнала или None — строку не писать."""
        now = self._monotonic()
        self._count[n] = self._count.get(n, 0) + 1
        self._first.setdefault(n, now)
        last = self._last.get(n)
        if last is not None and now - last < NOT_READY_LOG_S:
            return None
        self._last[n] = now
        count, self._count[n] = self._count[n], 0
        return count, round(now - self._first.pop(n))


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

async def _place_list(role: GatewayRole, n: int, path: str) -> tuple[list, int]:
    """GET списка своего места (/agent, /permission) и код ответа: 401 — _PlaceAuth; не 200, не JSON, не список —
    пустой список."""
    answer = await role.workplaces.fetch(n, "GET", path, max_bytes=UPSTREAM_MAX_BYTES)
    if answer.status == 401:
        raise _PlaceAuth()
    if answer.status != 200:
        return [], answer.status
    try:
        items = json.loads(answer.body)
    except (ValueError, RecursionError):
        return [], answer.status
    return (items if isinstance(items, list) else []), answer.status


async def _check_agent(role: GatewayRole, n: int, name: str) -> None:
    """Д5-R33: агент есть в GET /agent своего места, native строго false, mode — primary или all; иначе 400.
    Имя в текст отказа — только прошедшее AGENT_NAME (Ш12); в журнал оно не идёт."""
    if not isinstance(name, str) or AGENT_NAME.fullmatch(name) is None:      # сборщик уже сверил — на всякий случай
        raise Refused(400, "BadRequest", RULE_MESSAGES["agent_name"], "bad_body")
    agents, status = await _place_list(role, n, "/agent")
    if not any(isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"] == name
               and a.get("native") is False and isinstance(a.get("mode"), str) and a["mode"] in AGENT_MODES
               for a in agents):
        raise _CheckRefused(400, "BadRequest", PROXY_MESSAGES["agent"].format(agent=name), "agent", status)


async def _check_permission(role: GatewayRole, n: int, per: str) -> None:
    """Д5-R34: once — только висящему запросу своего места с permission из APPROVABLE; иначе 403."""
    pending, status = await _place_list(role, n, "/permission")
    if not any(isinstance(p, dict) and p.get("id") == per and isinstance(p.get("permission"), str)
               and p["permission"] in APPROVABLE for p in pending):
        raise _CheckRefused(403, "Forbidden", PROXY_MESSAGES["permission"], "permission", status)


# --- ответы и журнал ---

def _refused(role: GatewayRole, call: _Call, refusal: Refused) -> JSONResponse:
    role.stats.event("refused")
    place_status = getattr(refusal, "place_status", 200)
    tail = f" place_status={place_status}" if place_status != 200 else ""
    log.warning("gw refused login=%s place=%s method=%s path=%s reason=%s status=%d%s", call.login, call.place,
                call.method, call.path, refusal.reason, refusal.status, tail)
    return gw_error(refusal.status, refusal.name, refusal.message)


def _oc_line(call: _Call, rule: Rule, status: int, started: float, tail: str = "", *, warn: bool = False) -> None:
    ms = round((time.monotonic() - started) * 1000)
    (log.warning if warn else log.info)("gw oc login=%s place=%s rule=%s status=%d ms=%d%s", call.login,
                                        call.place, rule.name, status, ms, tail)


def _content_type(ct: str | None, body: bytes | bytearray) -> str | None:
    """Content-Type браузеру (G7): application/json и text/plain (с параметрами, печатный ASCII до
    CONTENT_TYPE_CHARS) — как есть; прочий — application/octet-stream: HTML или SVG места страницей не станет. Нет
    заголовка — у пустого тела нет и у ответа, у непустого — application/octet-stream (H6)."""
    if ct is None:
        return OCTET_STREAM if body else None
    ok = len(ct) <= CONTENT_TYPE_CHARS and ct.isascii() and ct.isprintable()
    return ct if ok and ct.split(";", 1)[0].strip().lower() in PASSED_TYPES else OCTET_STREAM


def _answer(answer: PlaceAnswer, release: Callable[[], None]) -> _PlaceResponse:
    """Ответ места браузеру: тот же код, тело как есть, из заголовков — только Content-Type (_content_type); слот
    места освобождает отправка (_PlaceResponse)."""
    ct = _content_type(answer.content_type, answer.body)
    return _PlaceResponse(answer.body, answer.status, {"content-type": ct} if ct else None, release)


def body_busy(role: GatewayRole) -> Callable[[Scope], Response]:
    """Ответ на тело /pcbk/api/oc/ сверх общего бюджета (H2): вне pcbk-front — 404, как отказ зоны (иначе сеть места
    узнала бы ручку); в pcbk-front — 503 WorkplaceBusy «busy» и счётчик refused."""
    def respond(scope: Scope) -> Response:
        if not zones.is_front(scope):
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        role.stats.event("refused")
        return gw_error(503, "WorkplaceBusy", PROXY_MESSAGES["busy"])
    return respond


# --- маршрут ---

def proxy_router(role: GatewayRole) -> APIRouter:
    """/pcbk/api/oc/{rest:path} и /pcbk/api/oc на методы METHODS: зависимости front_only и configured — у роутера,
    вход — у обработчика; ставится до catch_all_router."""
    router = APIRouter(dependencies=[Depends(front_only), Depends(configured(role))])
    account_of = current_account(role)
    not_ready = _NotReadyLog(role.monotonic)
    gate = _PlaceGate()

    async def oc(request: Request, account: Account = Depends(account_of)) -> Response:
        return await _proxy(role, request, account, not_ready, gate)

    for path in (OC_PREFIX + "/{rest:path}", OC_PREFIX):
        router.add_api_route(path, oc, methods=list(METHODS), include_in_schema=False)
    return router


async def _proxy(role: GatewayRole, request: Request, account: Account, not_ready: _NotReadyLog,
                 gate: _PlaceGate) -> Response:
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
        rolled = not_ready.refused(n)
        if rolled is not None:
            log.info("gw refused login=%s place=%s method=%s path=%s reason=not_ready status=503 n=%d since_s=%d",
                     call.login, call.place, call.method, call.path, *rolled)
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
    release: Callable[[], None] | None = None       # слот места; None до очереди и после передачи ответу
    try:
        try:
            async with asyncio.timeout(PROXY_DEADLINE_S):
                async with asyncio.timeout(PROXY_QUEUE_WAIT_S):     # очередь не съедает срок места (H1)
                    release = await gate.acquire(n)
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
        except httpx.PoolTimeout:                   # пул клиента места занят — место живо (G1)
            return _refused(role, call, _busy())
        except (httpx.HTTPError, OSError) as exc:   # TimeoutError срока хода — подкласс OSError (Ш11)
            if isinstance(exc, TimeoutError) and release is None:
                return _refused(role, call, _busy())    # не дождался очереди места до срока хода (G1)
            role.workplaces.mark_unready(n)
            role.stats.event("upstream_failed")
            _oc_line(call, rule, 503, started, f" reason=no_answer error={type(exc).__name__}", warn=True)
            return gw_error(503, "WorkplaceNotReady", PROXY_MESSAGES["no_answer"])
        if answer.status == 401:                    # без WWW-Authenticate места
            role.stats.event("place_auth_failed")
            _oc_line(call, rule, 502, started, " reason=place_auth", warn=True)
            return gw_error(502, "BadGateway", PROXY_MESSAGES["place_auth"])
        _oc_line(call, rule, answer.status, started)
        response = _answer(answer, release)
        release = None                              # слот — у ответа: освободит конец отправки
        return response
    finally:
        if release is not None:
            release()
