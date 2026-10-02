"""Отчёты браузеров о молчании веб-чата (Д6-R50): POST /pcbk/api/web/report и GET /health/web.

Отчёт — тело ровно {"kind": K}, K из REPORT_KINDS; тело читается не больше WEB_REPORT_MAX_BYTES + 1 байт. Порядок
проверок: зона (front_only, вне pcbk-front — 404) → ошибка настройки (configured, 503) → сессия (current_account, 401)
→ источник записи (origin_checked, 403) → тело (400): сессия раньше источника, как у пробуждения места (остаток Д5б,
preflight Ф10) — запрос без учётки не растит origin_refused и не пишет строку журнала. Ответ — 204 всегда, когда отчёт
принят, засчитан он или нет: счётчик пары (логин, вид) растёт не чаще раза в WEB_REPORT_EVERY_S, засчитанный отчёт —
одна строка INFO «gw web report login=… kind=…»; отброшенный — без счёта и без строки. Свободного текста клиента в
журнале нет: логин — из учётки сессии, вид — из REPORT_KINDS.

GET /health/web — только зона front, как /health/gateway (Д5-R31): {"<вид>_1h": int} по четырём видам, окно — час.
Отказы (зона, неизвестный метод) — обработчиком HTTPException шлюза из реестра hooks_of(app) (роль ставит его на
префикс /health/web в install). Модуль импортируется лениво, как api и proxy (команда администратора accounts).
"""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .accounts import Account          # аннотация зависимости — FastAPI читает её при сборке маршрута
from .api import (ERROR_NAMES, GW_MESSAGES, PREFIX, configured, current_account, front_only, gw_error,
                  origin_checked)
from .stats import WINDOW_S

if TYPE_CHECKING:
    from . import GatewayRole

log = logging.getLogger("pcbk_core.gateway.web")

REPORT_KINDS = ("boot_failed", "ui_crash", "turn_silent", "stream_down")
WEB_REPORT_EVERY_S = 60                 # засчитанный отчёт пары (логин, вид) — не чаще раза за столько секунд
WEB_REPORT_MAX_BYTES = 256              # тело отчёта; edge — client_max_body_size 1k
HEALTH_WEB_PATH = "/health/web"
REPORT_PATH = PREFIX + "/web/report"


class WebReports:
    """Счётчики отчётов браузеров за час (без ввода-вывода, потокобезопасно); часы — у вызывающего (now).

    report(login, kind, now) — True, если отчёт засчитан: прежний засчитанный отчёт той же пары старше
    WEB_REPORT_EVERY_S. counts_1h(now) — засчитанные за последние WINDOW_S по видам. Память ограничена: засчитанных
    событий на пару за час — не больше WINDOW_S / WEB_REPORT_EVERY_S, пары старше WEB_REPORT_EVERY_S уходят.
    Неизвестный вид — ValueError: опечатка в имени не проходит молча нулём.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {kind: deque() for kind in REPORT_KINDS}
        self._last: dict[tuple[str, str], float] = {}

    def _trim(self, now: float) -> None:
        for queue in self._events.values():
            while queue and now - queue[0] >= WINDOW_S:
                queue.popleft()
        for pair in [p for p, t in self._last.items() if now - t >= WEB_REPORT_EVERY_S]:
            del self._last[pair]

    def report(self, login: str, kind: str, now: float) -> bool:
        if kind not in self._events:
            raise ValueError(f"неизвестный вид отчёта браузера: {kind!r}")
        with self._lock:
            self._trim(now)
            if (login, kind) in self._last:
                return False
            self._last[(login, kind)] = now
            self._events[kind].append(now)
            return True

    def counts_1h(self, now: float) -> dict[str, int]:
        with self._lock:
            self._trim(now)
            return {f"{kind}_1h": len(self._events[kind]) for kind in REPORT_KINDS}


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    """object_pairs_hook: повтор ключа — не тело {"kind": K}."""
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("повтор ключа")
    return obj


def report_kind(raw: bytes) -> str | None:
    """Вид отчёта из тела ровно {"kind": K}, K из REPORT_KINDS; иначе None."""
    try:
        body = json.loads(raw, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):   # в том числе UnicodeDecodeError
        return None
    if not isinstance(body, dict) or set(body) != {"kind"}:
        return None
    kind = body["kind"]
    return kind if isinstance(kind, str) and kind in REPORT_KINDS else None


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """Тело не длиннее limit байт; длиннее — None. Объявленная длина больше limit — отказ без чтения; иначе
    читается до первого байта сверх limit."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk[:limit + 1 - len(body)])
        if len(body) > limit:
            return None
    return bytes(body)


def web_router(role: "GatewayRole") -> APIRouter:
    """POST /pcbk/api/web/report (зона front, настройка, сессия, источник) и GET /health/web (зона front)."""
    router = APIRouter()
    account_of = current_account(role)
    check_origin = origin_checked(role)

    @router.post(REPORT_PATH, dependencies=[Depends(front_only), Depends(configured(role))])
    async def web_report(request: Request, account: Account = Depends(account_of)) -> Response:
        await check_origin(request)     # после сессии (Ф10): без учётки — 401, ни строки, ни счётчика
        raw = await _read_capped(request, WEB_REPORT_MAX_BYTES)
        kind = None if raw is None else report_kind(raw)
        if kind is None:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        if role.web_reports.report(account.login, kind, role.monotonic()):
            log.info("gw web report login=%s kind=%s", account.login, kind)
        return Response(status_code=204)

    @router.get(HEALTH_WEB_PATH, dependencies=[Depends(front_only)])
    async def health_web() -> JSONResponse:
        # только числа: ни логинов, ни номеров мест
        return JSONResponse(role.web_reports.counts_1h(role.monotonic()))

    return router
