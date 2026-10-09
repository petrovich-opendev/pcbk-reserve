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
(Д5-R33); permission_reply с once — сначала GET /permission (Д5-R34); event — поток stream_events (ниже). Не-GET —
активность места (touch) до пересылки. Проверка агента или разрешения и пересылка — под одним сроком
PROXY_DEADLINE_S.

Ворота перезагрузки места (Д7а, Д7-R58): prompt_async — единственное правило, начинающее ход, — после разбора тела
проходит ворота места role.reloads.gates.turn(n) (читатель): ждёт только идущую перезагрузку агентов, не дольше
reload.RELOAD_WAIT_S. Ожидание стоит снаружи срока хода: после ворот ход места получает весь PROXY_DEADLINE_S (H1;
худший случай 10 + 25 = 35 с < 45 с у edge). Внутри ворот — снова готовность места (оно могло уйти в перезапуск, m-н4,
ответ — как у спящего), затем прежний ход; при выходе из ворот — note_turn_start(n): отсрочка перезагрузки считается от
конца пересылки (предпроверка П11). Не дождался ворот — 503 WorkplaceBusy с PROXY_MESSAGES["reloading"], без счётчика
refused (место живо, отказа по правилам нет), строка «gw oc … reason=reloading». Прочие правила ворот не берут.

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

Поток /event (Д5-R35, задача Б4) — stream_events: слотов учётки уже MAX_STREAMS_PER_LOGIN — 429 streams (как
not_ready: без счётчика, строка журнала — одна на место за NOT_READY_LOG_S с n= и since_s=: адаптер лишней вкладки
переподключается так же часто); место не готово — 503 not_ready (не будит); у GET есть тело (Content-Length > 0 или
Transfer-Encoding) — 400 bad_body до потока места: иначе тело держало бы общий бюджет тел всю жизнь потока. Затем
поток места (Workplaces.stream): срок заголовков STREAM_OPEN_S и ошибка соединения — 503 no_answer, место неготово,
upstream_failed; занятый пул клиента места — 503 busy, как у вызовов; 401 — 502 place_auth; иначе не 200 — 502
BadGateway с текстом no_answer и upstream_failed без снятия «готово» (не 503 WorkplaceNotReady: место готово, чат не
должен звать «Разбудить»), строка — одна на место за NOT_READY_LOG_S (итоговая доводка Д5б, N4: адаптер
переподключается до 6 раз в секунду); сжатый поток — как нет ответа. Слот учётки занимается только после 200 места, прямо перед
StreamingResponse (гонка вкладок — тот же 429); владение слотом и потоком места ответ принимает в момент return, до
него их возвращает finally обработчика (сначала слот, затем поток места).

Дальше — _EventStream: задача-читатель гонит filter_sse по сырым кускам места в очередь на STREAM_QUEUE_MAX событий
(одно: замер памяти Б4); браузер не читает — читатель ждёт put, давление уходит к месту, а не в память. Генератор
ответа ждёт очередь не дольше срока перепроверки и перепроверяет сессию не чаще и не реже раза в stream_recheck_s
(не на каждом событии — дельты идут десятками в секунду): сессии нет, учётка отключена или место не то — конец
closed_by=session; STREAM_STORE_FAILURES сбоев базы подряд — closed_by=store. Конец места — конец браузеру
(closed_by=place; обрыв httpx или OSError — с классом в журнале); событие больше SSE_MAX_EVENT_BYTES (oversize) и
тишина места дольше places.SSE_READ_TIMEOUT_S (timeout) — конец и stream_broken; обрыв браузера — closed_by=client;
поток живёт не дольше STREAM_MAX_S (closed_by=lifetime, без stream_broken; доводка Б5, L2): зависшая вкладка, которая
поглощает пульс места, не держит слот учётки часами — send_timeout edge её не видит, адаптер переподключается сам.
Срок жизни сверяется на каждом событии и на каждом сроке ожидания очереди.
release() идемпотентна своим флагом: слот, отмена читателя, строка «gw event place=NN closed_by=… s=N» (WARNING — у
timeout, oversize, store и у обрыва места с классом исключения, N5), ожидание
читателя и закрытие потока места под щитом от отмены; её зовут finally генератора и BackgroundTask ответа (Starlette
зовёт его и тогда, когда браузер ушёл до первого шага генератора), а остановка роли (lifespan) закрывает ещё открытые
потоки и дожидается закрытия (_EventStream.shutdown) — всех вместе не дольше STREAM_SHUTDOWN_S (Б4-m2). В реестр
потоков роли ответ попадает, только когда принял владение (Б4-m3). Поток место не будит и простой не продлевает (touch не
зовётся, Д5-R35).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, NamedTuple

import anyio
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.types import Receive, Scope, Send

from .accounts import Account, StoreError
from .. import zones
from . import places
from .api import (ERROR_NAMES, GW_MESSAGES, PREFIX, configured, current_account, front_only, gw_error, same_origin,
                  session_tokens)
from .places import PLACE_READ_S, PROXY_PARALLEL, PlaceAnswer, PlaceAnswerTooLarge
from .reload import GateTimeout
from .rules import (AGENT_NAME, ALLOWED_EVENTS, APPROVABLE, METHODS, RULE_MESSAGES, RULES, Refused, Rule, filter_query,
                    match)
from .stats import MAX_STREAMS_PER_LOGIN

if TYPE_CHECKING:
    from . import GatewayRole

log = logging.getLogger("pcbk_core.gateway.proxy")

OC_PREFIX = PREFIX + "/oc"
_RAW_PREFIX = OC_PREFIX.encode("ascii")
UPSTREAM_MAX_BYTES = 4 * 1024 * 1024        # ответ места в памяти шлюза (Д5-R36)
PLACE_LIST_MAX_BYTES = 1024 * 1024          # списки сверки /agent и /permission (итоговая доводка Д5б, N3): не история
SEND_CHUNK = 64 * 1024                      # кусок отправки ответа места браузеру (G1)
PROXY_DEADLINE_S = 25.0                     # весь ход вызова; edge на /pcbk/api/oc/ ждёт 45 с (Д5-R30)
# ожидание слота места внутри срока хода: месту после слота — не меньше PLACE_READ_S (H1)
PROXY_QUEUE_WAIT_S = PROXY_DEADLINE_S - PLACE_READ_S
# тел /pcbk/api/oc/ в памяти до обработчика разом (H2): 32 тела по 512 КиБ
GW_BODY_BUDGET_BYTES = 16 * 1024 * 1024
NOT_READY_LOG_S = 60.0
PROXY_QUEUE = 8                             # ждущих вызовов на место сверх PROXY_PARALLEL (G1)
LOG_PATH_CHARS = 120
# Д5-R33 с поправкой итоговой доводки Д7б (I-1, второй слой): только primary. mode all — агент без своих правил
# (файл без frontmatter или с битым YAML); служба пишет агентов только mode: primary (шаблон А2), агенты образа —
# тоже primary
AGENT_MODES = ("primary",)
GET_SITES = ("same-origin", "none")         # Sec-Fetch-Site, с которым GET пропускается (Д5-R26, р4)
CONTENT_TYPE_CHARS = 200
# Content-Type ответа места как есть — только эти виды (с параметрами); прочий — OCTET_STREAM (G7)
PASSED_TYPES = ("application/json", "text/plain")
OCTET_STREAM = "application/octet-stream"
# поток /event (Д5-R35, Б4): событие места в памяти шлюза и очередь читателя одного потока
SSE_MAX_EVENT_BYTES = 512 * 1024
# 1, не 4 (решение контроллера Б4): замер 30 потоков по 512 КиБ с медленным читателем — 123 МиБ RSS при 4 и 72 МиБ
# при 1 (порог ~80 МиБ); поток держит ещё кусок в send Starlette и копию в буфере uvicorn — их в формуле R36 не было
STREAM_QUEUE_MAX = 1
STREAM_CLOSE_S = 5.0                        # ожидание читателя и закрытие потока места в release — каждое не дольше
STREAM_SHUTDOWN_S = 3.0                     # остановка роли ждёт закрытия всех потоков вместе не дольше (Б4-m2)
STREAM_STORE_FAILURES = 3                   # сбоев базы подряд при перепроверке сессии — конец потока (closed_by=store)
STREAM_MAX_S = 1800.0                       # предел жизни потока (closed_by=lifetime); читается при открытии потока
_BROKEN = ("timeout", "oversize")           # концы потока, которые считаются stream_broken
_WARN_ENDS = (*_BROKEN, "store")            # концы потока со строкой WARNING

PROXY_MESSAGES = {
    "not_whitelisted": RULE_MESSAGES["not_whitelisted"],        # тот же объект, что у правил Б2
    # Д10-R100: преподаватель читает разговоры в кабинете (/pcbk/api/teacher/), а в прокси по-прежнему 403
    "teacher": "Преподаватель читает разговоры студентов в кабинете — писать в них нельзя",
    "not_ready": "Рабочее место спит — разбудите его на странице входа",
    "no_answer": "Рабочее место не отвечает — разбудите его на странице входа",
    "place_auth": "Рабочее место не приняло пароль шлюза — сообщите администратору",
    "agent": "Агент «{agent}» недоступен",
    "permission": "Это разрешение даёт только преподаватель",
    "too_big": "Ответ рабочего места слишком большой",
    "streams": "Открыто слишком много вкладок чата — закройте лишние",
    "busy": "Слишком много одновременных запросов — повторите позже",
    "reloading": "Обновляю список агентов рабочего места — повторите через несколько секунд",     # Д7-R58
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


class _RolledRefusals:
    """Отказы одного вида по местам (not_ready — Д5-R37; streams — решение контроллера Б4) — одна строка журнала
    за NOT_READY_LOG_S с числом отказов с прошлой строки: цикл переподключения адаптера даёт до 6 запросов в секунду
    на вкладку. У строки — since_s: сколько секунд копились отказы (от первого не записанного до этого), чтобы старое
    число не выдавало себя за свежее (G8)."""

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
    пустой список. Больше PLACE_LIST_MAX_BYTES — PlaceAnswerTooLarge (502 too_big у вызова)."""
    answer = await role.workplaces.fetch(n, "GET", path, max_bytes=PLACE_LIST_MAX_BYTES)
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
    """Д5-R33: агент есть в GET /agent своего места, native строго false, mode — из AGENT_MODES; иначе 400.
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


def _rolled(call: _Call, n: int, rolls: _RolledRefusals | None, reason: str, status: int, name: str) -> JSONResponse:
    """Отказ без счётчика: строка журнала — одна на место за NOT_READY_LOG_S с n= и since_s= (rolls None — на каждый
    отказ, n=1)."""
    rolled = rolls.refused(n) if rolls is not None else (1, 0)
    if rolled is not None:
        log.info("gw refused login=%s place=%s method=%s path=%s reason=%s status=%d n=%d since_s=%d",
                 call.login, call.place, call.method, call.path, reason, status, *rolled)
    return gw_error(status, name, PROXY_MESSAGES[reason])


def _not_ready(call: _Call, n: int, rolls: _RolledRefusals | None) -> JSONResponse:
    """503 «место спит» (Д5-R36, Д5-R37)."""
    return _rolled(call, n, rolls, "not_ready", 503, "WorkplaceNotReady")


def _too_many_streams(call: _Call, n: int, rolls: _RolledRefusals | None) -> JSONResponse:
    """429 «слишком много вкладок» потока /event — как not_ready (решение контроллера Б4)."""
    return _rolled(call, n, rolls, "streams", 429, "TooManyRequests")


def body_busy(role: GatewayRole) -> Callable[[Scope], Response]:
    """Ответ на тело /pcbk/api/oc/ сверх общего бюджета (H2): вне pcbk-front — 404, как отказ зоны (иначе сеть места
    узнала бы ручку); в pcbk-front — 503 WorkplaceBusy «busy» и счётчик refused."""
    def respond(scope: Scope) -> Response:
        if not zones.is_front(scope):
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        role.stats.event("refused")
        return gw_error(503, "WorkplaceBusy", PROXY_MESSAGES["busy"])
    return respond


# --- поток /event (Д5-R35, задача Б4) ---

class SseEventTooLarge(ValueError):
    """Событие потока места больше SSE_MAX_EVENT_BYTES (без пустой строки конца)."""


class _PlaceSilent(Exception):
    """Место молчит дольше places.SSE_READ_TIMEOUT_S."""


class _End(NamedTuple):
    """Метка конца в очереди читателя: вид конца (place, timeout, oversize) и класс исключения места, если было."""
    closed_by: str
    error: str | None = None


def _event_type(lines: list[bytes]) -> str | None:
    """Поле type JSON-объекта события из его строк (без пустой строки конца). Каждая строка обязана начинаться с
    data: (формат OpenCode, доводка Б4 K3): id:, retry:, event:, комментарий «:» и строка data без двоеточия —
    событие не отдаётся (иначе браузер и шлюз прочли бы кадр по-разному). Значения data: склеиваются через \\n, как
    у SDK и EventSource. Не JSON (в том числе целое длиннее предела и вложенность глубже предела разбора), не
    объект, type не строка — None."""
    data = []
    for line in lines:
        if not line.startswith(b"data:"):
            return None
        value = line[5:]
        data.append(value[1:] if value.startswith(b" ") else value)
    if not data:
        return None
    try:
        parsed = json.loads(b"\n".join(data))
    except (ValueError, RecursionError):            # UnicodeDecodeError — подкласс ValueError
        return None
    kind = parsed.get("type") if isinstance(parsed, dict) else None
    return kind if isinstance(kind, str) else None


async def filter_sse(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """События потока места, чей type — из ALLOWED_EVENTS (Д5-R35), кадром из одних строк data: и пустой строки \\n\\n.

    Концы строк — как у SDK OpenCode (serverSentEvents.gen.ts v2, :155, доводка Б4 K2): \\r\\n и одиночный \\r
    приводятся к \\n до поиска конца события — \\r в конце куска ждёт следующего (половина \\r\\n не считается
    лишней строкой); конец события — \\n\\n. Отдаётся событие после приведения: \\r в кадре нет, и браузер (SDK v1
    и v2, EventSource) делит поток на события так же, как шлюз, при любой нарезке на куски — чужой тип через
    \\n\\r\\n или \\r\\r\\n не провезти. У OpenCode (data: …\\n\\n) кадр — байты места как есть; JSON не
    пересобирается. Пустые строки перед событием — пустые отправки, пропускаются. Неразборчивое событие
    отбрасывается, неоконченное к концу потока — тоже (как у EventSource).

    Событие больше SSE_MAX_EVENT_BYTES (после приведения, без пустой строки конца) — SseEventTooLarge (ValueError), и
    неоконченное тоже: копится не больше предела и одного куска. События из того же куска до большого отдаются.
    Буфер сжимается до yield: читатель, ждущий места в очереди, держит отдаваемое событие и остаток куска."""
    buf = bytearray()
    ready: deque[bytes] = deque()
    cr = False                                      # прошлый кусок кончился на \r — возможно, половина \r\n
    async for chunk in chunks:
        if not chunk:
            continue
        if cr:
            chunk = b"\r" + chunk
        cr = chunk.endswith(b"\r")
        if cr:
            chunk = chunk[:-1]
        seek = max(0, len(buf) - 1)                 # раньше конца события в buf нет: его уже искали
        buf += chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        start, too_large = 0, False
        while True:
            while start < len(buf) and buf[start] == 0x0A:      # пустые строки до события
                start += 1
            end = buf.find(b"\n\n", max(start, seek))
            if end < 0:
                break
            if end - start > SSE_MAX_EVENT_BYTES:
                too_large = True
                break
            event = bytes(buf[start:end + 2])
            start = end + 2
            if _event_type(event.split(b"\n")[:-2]) in ALLOWED_EVENTS:
                ready.append(event)
            event = b""                             # отброшенное событие не держится до yield
        if too_large or start == len(buf):
            buf = bytearray()                       # буфер разобран (или брошен): копившееся место не держится
        elif start:
            del buf[:start]
        too_large = too_large or len(buf) - buf.endswith(b"\n") > SSE_MAX_EVENT_BYTES
        while ready:                                # события до большого — браузеру, затем обрыв
            yield ready.popleft()
        if too_large:
            raise SseEventTooLarge()


async def _timed(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Куски места; ожидание одного куска дольше places.SSE_READ_TIMEOUT_S (читается при каждом ожидании) —
    _PlaceSilent. Пока читатель ждёт место в очереди (браузер не читает), срок не идёт: это не тишина места."""
    it = aiter(chunks)
    while True:
        try:
            async with asyncio.timeout(places.SSE_READ_TIMEOUT_S) as limit:
                chunk = await anext(it)
        except StopAsyncIteration:
            return
        except TimeoutError:
            if limit.expired():
                raise _PlaceSilent() from None
            raise
        yield chunk


class _EventStream:
    """Поток /event, принятый ответом (после 200 места и занятого слота): читатель, генератор ответа, release."""

    def __init__(self, role: GatewayRole, login: str, token: str, n: int,
                 opened: AbstractAsyncContextManager[httpx.Response], response: httpx.Response) -> None:
        self.role, self.login, self.token, self.n = role, login, token, n
        self._opened = opened                       # выход из него закрывает поток места
        self._response = response
        self._queue: asyncio.Queue[bytes | _End] = asyncio.Queue(maxsize=STREAM_QUEUE_MAX)
        self._reader: asyncio.Task | None = None
        self._released = False
        self._done = asyncio.Event()                # release() довёл закрытие до конца (или сдался по сроку)
        self._store_failures = 0
        self._started = time.monotonic()
        self._ends_at = self._started + STREAM_MAX_S    # предел жизни потока (L2)

    async def _read(self) -> None:
        """Читатель: события filter_sse — в очередь, в конце — метка _End. Исключения места — только классом (Ш11)."""
        end = _End("place")
        raw = self._response.aiter_raw()
        timed = _timed(raw)
        events = filter_sse(timed)
        try:
            async for event in events:
                await self._queue.put(event)
        except SseEventTooLarge:
            end = _End("oversize")
        except _PlaceSilent:
            end = _End("timeout")
        except Exception as exc:                    # httpx.HTTPError, OSError и непредвиденное — конец места
            end = _End("place", type(exc).__name__)
        finally:
            for gen in (events, timed, raw):        # снятые на yield — сразу, не сборщиком
                await gen.aclose()
        await self._queue.put(end)

    async def _session_end(self) -> str | None:
        """None — сессия действует и ведёт к той же учётке и месту; иначе вид конца: session (сессии нет, учётка
        отключена, место другое) или store — STREAM_STORE_FAILURES сбоев базы подряд (доводка Б4 K4). Одиночный сбой
        поток не рвёт: проверка — через срок; удачная проверка обнуляет счёт."""
        try:
            account = await asyncio.to_thread(self.role.store.session, self.token)
        except StoreError:
            self._store_failures += 1
            return "store" if self._store_failures >= STREAM_STORE_FAILURES else None
        self._store_failures = 0
        if account is None or account.login != self.login or account.place != self.n:
            return "session"
        return None

    async def events(self) -> AsyncIterator[bytes]:
        """Генератор ответа: ждёт очередь не дольше срока до перепроверки сессии или конца жизни потока; перепроверка
        — раз в stream_recheck_s, ни чаще (на событиях), ни реже (на сроках); жизнь потока истекла (STREAM_MAX_S) —
        конец closed_by=lifetime, сверка на каждом событии и каждом сроке. Конец — release() в finally."""
        closed_by, error = "client", None
        try:
            if self._released:
                return
            self._reader = asyncio.create_task(self._read(), name=f"gw-event-{self.n:02d}")
            recheck = self.role.stream_recheck_s
            next_check = time.monotonic() + recheck
            while True:
                item: bytes | _End | None = None
                wait = min(next_check, self._ends_at) - time.monotonic()
                if wait > 0:
                    try:
                        item = await asyncio.wait_for(self._queue.get(), wait)   # отмена get() событие не теряет
                    except TimeoutError:
                        pass
                if time.monotonic() >= self._ends_at:                           # L2: адаптер переподключится сам
                    closed_by = "lifetime"
                    break
                if time.monotonic() >= next_check:
                    ended = await self._session_end()
                    if ended is not None:
                        closed_by = ended
                        break
                    next_check = time.monotonic() + recheck
                if item is None:
                    continue
                if isinstance(item, _End):
                    closed_by, error = item
                    break
                yield item
        finally:
            await self.release(closed_by, error)

    async def release(self, closed_by: str = "client", error: str | None = None) -> None:
        """Освобождение ровно одно (свой флаг: stream_closed без открытых потоков молчит, повтор отнял бы слот
        соседней вкладки). Слот — сразу, до первого await; затем отмена читателя, stream_broken для timeout и
        oversize, строка журнала; затем ожидание читателя и закрытие потока места — каждое под своим щитом от
        отмены (обрыв браузера снимает генератор отменой anyio, повторной на каждом await) и своим сроком
        STREAM_CLOSE_S: без щита поток места не закрылся бы, и пул места кончился бы за несколько обрывов (K1, K6)."""
        if self._released:
            return
        self._released = True
        try:
            self.role.stats.stream_closed(self.login)
            reader = self._reader
            if reader is not None:
                reader.cancel()
            if closed_by in _BROKEN:
                self.role.stats.event("stream_broken")
            tail = f" error={error}" if error else ""
            (log.warning if closed_by in _WARN_ENDS or error else log.info)(      # N5: обрыв места с классом
                "gw event place=%02d closed_by=%s s=%d%s", self.n, closed_by,
                round(time.monotonic() - self._started), tail)
            if reader is not None:
                with anyio.move_on_after(STREAM_CLOSE_S, shield=True):
                    await asyncio.gather(reader, return_exceptions=True)
            with anyio.move_on_after(STREAM_CLOSE_S, shield=True):     # своим сроком: читатель закрытие не съест
                try:
                    await self._opened.__aexit__(None, None, None)
                except Exception as exc:
                    log.warning("gw event place=%02d close_error=%s", self.n, type(exc).__name__)
        finally:
            self._done.set()
            self.role.event_streams.discard(self)

    async def shutdown(self) -> None:
        """Остановка роли: release() (если его ещё никто не звал — closed_by=shutdown) и ожидание, пока закрытие,
        начатое кем угодно, дойдёт до конца. uvicorn по сроку мягкой остановки снимает задачи запросов и сразу идёт в
        lifespan, не дожидаясь их; роль ждёт закрытия здесь, иначе asyncio.run снял бы его родной отменой, от которой
        щит anyio не спасает. Роль ждёт все потоки вместе не дольше STREAM_SHUTDOWN_S (Б4-m2): застрявший поток
        места закрывает конец процесса; свой предел здесь (2 × STREAM_CLOSE_S + 1) — только запас сверху."""
        await self.release("shutdown")
        with anyio.move_on_after(2 * STREAM_CLOSE_S + 1):
            await self._done.wait()


def _has_body(request: Request) -> bool:
    """У запроса есть тело: Transfer-Encoding (любой) или Content-Length не из одних нулей (решение контроллера Б4)."""
    if request.headers.get("transfer-encoding") is not None:
        return True
    length = request.headers.get("content-length")
    return length is not None and length.strip().strip("0") != ""


_EVENT_RULE = next(rule for rule in RULES if rule.kind == "event")


async def stream_events(role: GatewayRole, account: Account, token: str, n: int, *, call: _Call | None = None,
                        not_ready: _RolledRefusals | None = None, streams: _RolledRefusals | None = None,
                        not_stream: _RolledRefusals | None = None,
                        has_body: bool = False) -> Response:
    """Поток /event места n учётки account (сессия token): порядок проверок и владение слотом — в описании модуля.
    call — поля строк журнала; not_ready и streams — свёртки строк этих отказов прокси; has_body — у запроса есть
    тело."""
    login = account.login
    call = call or _Call(login, f"{n:02d}", "GET", "/event")
    if role.stats.streams_of(login) >= MAX_STREAMS_PER_LOGIN:
        return _too_many_streams(call, n, streams)
    if not role.workplaces.is_ready(n):             # не будит: будит только страница входа
        return _not_ready(call, n, not_ready)
    if has_body:
        return _refused(role, call, Refused(400, "BadRequest", RULE_MESSAGES["bad_body"], "bad_body"))
    started = time.monotonic()
    opened = role.workplaces.stream(n, "/event")
    response: httpx.Response | None = None
    slot = owned = False
    try:
        try:
            response = await opened.__aenter__()
        except httpx.PoolTimeout:                   # пул клиента места занят — место живо (как у вызовов, G1)
            return _refused(role, call, _busy())
        except (httpx.HTTPError, OSError) as exc:   # срок STREAM_OPEN_S — TimeoutError, подкласс OSError (Ш11)
            role.workplaces.mark_unready(n)
            role.stats.event("upstream_failed")
            _oc_line(call, _EVENT_RULE, 503, started, f" reason=no_answer error={type(exc).__name__}", warn=True)
            return gw_error(503, "WorkplaceNotReady", PROXY_MESSAGES["no_answer"])
        status = response.status_code
        if status == 401:                           # без WWW-Authenticate места
            role.stats.event("place_auth_failed")
            _oc_line(call, _EVENT_RULE, 502, started, " reason=place_auth", warn=True)
            return gw_error(502, "BadGateway", PROXY_MESSAGES["place_auth"])
        if status != 200:                           # N4: место живо, но не отдаёт поток — счётчик, строка свёрнута
            role.stats.event("upstream_failed")
            rolled = not_stream.refused(n) if not_stream is not None else (1, 0)
            if rolled is not None:
                _oc_line(call, _EVENT_RULE, 502, started, " reason=no_answer place_status=%d n=%d since_s=%d"
                         % (status, *rolled), warn=True)
            # 502 BadGateway, не 503 WorkplaceNotReady: место готово, и «Разбудить» в чате Д6 ходило бы по кругу
            return gw_error(502, "BadGateway", PROXY_MESSAGES["no_answer"])
        if response.headers.get("content-encoding", "").strip().lower() not in ("", "identity"):
            role.workplaces.mark_unready(n)         # как сжатый ответ вызова (PlaceAnswerEncoded): не разобрать
            role.stats.event("upstream_failed")
            _oc_line(call, _EVENT_RULE, 503, started, " reason=no_answer error=PlaceAnswerEncoded", warn=True)
            return gw_error(503, "WorkplaceNotReady", PROXY_MESSAGES["no_answer"])
        if not role.stats.stream_opened(login):     # гонка вкладок: слоты заняли, пока место отвечало
            return _too_many_streams(call, n, streams)
        slot = True
        stream = _EventStream(role, login, token, n, opened, response)
        _oc_line(call, _EVENT_RULE, 200, started)
        owned = True                                # слот и поток места — у ответа: освободит release()
        role.event_streams.add(stream)              # остановка роли дождётся закрытия (shutdown); после owned (Б4-m3)
        return StreamingResponse(stream.events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                                 background=BackgroundTask(stream.release))
    finally:
        if not owned:
            if slot:
                role.stats.stream_closed(login)     # слот — до закрытия потока места (доводка Б4 K7)
            if response is not None:
                with anyio.move_on_after(STREAM_CLOSE_S, shield=True):
                    await opened.__aexit__(None, None, None)


# --- маршрут ---

def proxy_router(role: GatewayRole) -> APIRouter:
    """/pcbk/api/oc/{rest:path} и /pcbk/api/oc на методы METHODS: зависимости front_only и configured — у роутера,
    вход — у обработчика; ставится до catch_all_router."""
    router = APIRouter(dependencies=[Depends(front_only), Depends(configured(role))])
    account_of = current_account(role)
    not_ready, streams, not_stream = (_RolledRefusals(role.monotonic) for _ in range(3))
    gate = _PlaceGate()

    async def oc(request: Request, account: Account = Depends(account_of)) -> Response:
        return await _proxy(role, request, account, not_ready, streams, gate, not_stream)

    for path in (OC_PREFIX + "/{rest:path}", OC_PREFIX):
        router.add_api_route(path, oc, methods=list(METHODS), include_in_schema=False)
    return router


async def _proxy(role: GatewayRole, request: Request, account: Account, not_ready: _RolledRefusals,
                 streams: _RolledRefusals, gate: _PlaceGate, not_stream: _RolledRefusals | None = None) -> Response:
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

    if rule.kind == "event":                        # поток /event: свой порядок проверок (stream_events)
        return await stream_events(role, account, session_tokens(request)[0], n, call=call, not_ready=not_ready,
                                   streams=streams, not_stream=not_stream, has_body=_has_body(request))
    if not role.workplaces.is_ready(n):             # не будит: будит только страница входа
        return _not_ready(call, n, not_ready)
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
    if rule.name != "prompt_async":
        return await _forward(role, call, rule, n, rest, upstream, params, body, gate, started)
    # prompt_async — читатель ворот перезагрузки места (Д7-R58): ожидание — снаружи срока хода (H1)
    reloads = role.reloads
    try:
        async with reloads.gates.turn(n):
            try:
                if not role.workplaces.is_ready(n):     # место ушло в перезапуск, пока ждали ворота (m-н4)
                    return _not_ready(call, n, not_ready)
                return await _forward(role, call, rule, n, rest, upstream, params, body, gate, started)
            finally:
                reloads.note_turn_start(n)              # отсрочка перезагрузки — от конца пересылки (П11)
    except GateTimeout:                             # перезагрузка дольше RELOAD_WAIT_S: место живо, без refused
        _oc_line(call, rule, 503, started, " reason=reloading", warn=True)
        return gw_error(503, "WorkplaceBusy", PROXY_MESSAGES["reloading"])


async def _forward(role: GatewayRole, call: _Call, rule: Rule, n: int, rest: str, upstream: str, params, body,
                   gate: _PlaceGate, started: float) -> Response:
    """Слот места, сверка агента или разрешения и пересылка — под одним сроком PROXY_DEADLINE_S (срок читается при
    вызове); ответ места браузеру держит слот до конца отправки."""
    method = call.method
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
