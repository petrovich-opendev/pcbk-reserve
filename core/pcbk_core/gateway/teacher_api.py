"""Кабинет преподавателя у шлюза (Д10а): наблюдение GET /health/teacher (задача А1, Д10-R96) и ручки кабинета
/pcbk/api/teacher/ (задача А3, Д10-R100…R104).

GET /health/teacher — только зона front (front_only), как /health/gateway и /health/agents (Д5-R31): сторож ходит из
pcbk-front. Отказы (зона, неизвестный метод) — обработчиком HTTPException шлюза из реестра hooks_of(app): роль ставит его
на этот путь в install. Тело — GatewayRole.teacher_health_json: ровно 13 ключей, только числа и флаги, без логинов и
номеров мест. Ручка читается и при config_error шлюза (без configured): тогда сверки долей нет, и строка сторожа —
«неизвестно», причина — в строке «Шлюз и вход» (m-13). На core старше Д10а ручки нет — 404, и сторож пишет «кабинет
преподавателя ещё не выложен».

Ручки кабинета (TEACHER_PREFIX):
- GET overview — обзор мест и бюджета (R101), GET journal — последние JOURNAL_ROWS строк журнала действий (I-1);
- GET places/NN — состояние места (опрос, пока оно просыпается), POST places/NN/wake — пробуждение для просмотра (R102);
- GET places/NN/sessions и places/NN/sessions/SES — разговоры места только через само место, тремя GET
  (TEACHER_PLACE_PATHS) паролем шлюза (R102); GET places/NN/agents и places/NN/agents/NAME — агенты места из учёта и
  каталога службы, место не нужно (R103);
- POST shares — перевод доли (R104), POST shares/floor — «Остановить место» (I-1 р2): пол берёт сервер.

Порядок проверок каждой ручки (R100) — строго: зона (front_only, 404) → настройка (configured, 503) → сессия
(current_account, 401) → роль: не преподаватель — 403 teacher_only, событие role_refused и строка «gw teacher refused
login=… reason=role» (одна на логин за ORIGIN_LOG_S), ни места, ни учёта, ни журнала → у POST — источник
(origin_checked, 403) → части пути по шаблонам (NN — 01…10, SES — ID_RE["ses"], имя агента — NAME_RE черновика; не
совпало — 404 not_found, без запроса к месту) → у ручек места — no_student (404: на месте нет учётки студента,
действующей или отключённой) → строка журнала действий requested (до доступа; не записана — 503 journal без доступа и без проверки
готовности, I-6) → у чтения разговоров — готовность (503 WorkplaceNotReady asleep, место не будится) → доступ.

Чтение разговоров (R102): путь к месту строит только _teacher_fetch из TEACHER_PLACE_PATHS и проверенного id — ввод
клиента в путь как есть не идёт; метод — только GET, простой места он не продлевает (touch не зовётся). Ворота кабинета
(TeacherGate, у роли — teacher_gate): к месту не больше TEACHER_PARALLEL чтений разом, ждущих — не больше TEACHER_QUEUE,
ожидание — не дольше TEACHER_WAIT_S внутри срока PROXY_DEADLINE_S; сверх — 503 busy. Соединение кабинета — своё
(places.PLACE_CONNECTIONS + TEACHER_PARALLEL): кабинет не берёт соединения студента. Ответ места: 401 — 502 place_auth
(place_auth_failed); нет ответа — 503 no_answer, место неготово, upstream_failed; больше предела — 502 too_big; 404 на
/session/SES — 404 no_session; прочее не 200, не JSON, не по договору (transcript.TranscriptError) — 502 bad_answer.

Пробуждение (R102, I-2) — обычный запуск места: та же задача Workplaces.wake, что у студента (touch, sp-ctl start,
_await_ready, крючок готовности и карантин), своих запросов к месту у кабинета нет.

Доли (R104): роли llm нет — 503 no_llm; доли не загружены или журнал вызовов не открыт — 503 shares_down без записи и
без строки журнала (иначе пол был бы 0, ревью А2 m-2); тело не по форме (повтор или лишний ключ) — 400 bad_body; затем
Shares.apply / apply_floor в потоке — строка журнала shares пишется в той же транзакции. ShareRefused — 400 (conflict —
409 Conflict) с текстом дословно и строкой refused без гарантии; SharesStoreError — 503 journal или shares_down,
change_failed.

Журнал службы — строка «gw teacher login=… action=… place=… outcome=… status=… ms=…» на каждый вызов преподавателя: ни
тел, ни текстов, ни названий разговоров. В detail строк журнала действий — только целые и коды (TeacherLog).

Модуль импортируется лениво, как api, proxy и agents_api (команда администратора accounts).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..agents.draft import NAME_MAX, NAME_MIN, NAME_RE
from ..agents.store import AgentMissing, DirUnavailable
from ..agents.store import StoreError as AgentsStoreError
from ..llm.ledger import OPS
from ..llm.shares import ShareRefused, SharesStoreError
from ..teacher_log import TeacherLogError
from .accounts import NOTICE, Account, StoreError, login_key
from .api import (ERROR_NAMES, GW_MESSAGES, PREFIX, GatewayRefusal, _RolledLines, _unique_keys, configured,
                  current_account, front_only, gw_error, origin_checked)
from .places import PLACES, TEACHER_PARALLEL, PlaceAnswer, PlaceAnswerTooLarge
from .proxy import PLACE_LIST_MAX_BYTES, PROXY_DEADLINE_S, PROXY_MESSAGES, UPSTREAM_MAX_BYTES
from .rules import ID_RE
from .transcript import TranscriptError, sessions_view, transcript

if TYPE_CHECKING:
    from ..llm import LlmRole
    from . import GatewayRole

log = logging.getLogger("pcbk_core.gateway.teacher")

HEALTH_TEACHER_PATH = "/health/teacher"
TEACHER_PREFIX = PREFIX + "/teacher/"
JOURNAL_ROWS = 50                       # строк журнала действий в кабинете (I-1)
TEACHER_QUEUE = 2                       # ждущих чтений кабинета к одному месту сверх TEACHER_PARALLEL (Д10-R102)
TEACHER_WAIT_S = 5.0                    # ожидание ворот кабинета — внутри срока PROXY_DEADLINE_S

# Д10-R100: тексты постоянные, без ввода клиента
TEACHER_TEXTS = {
    "teacher_only": "Только для преподавателя",
    "no_student": "На этом месте нет студента",
    "asleep": "Рабочее место студента спит — разбудите его для просмотра",
    "no_answer": "Рабочее место студента не отвечает — попробуйте позже",
    "too_big": "Разговор слишком большой для просмотра",
    "bad_answer": "Рабочее место ответило не по договору",
    "no_session": "Разговор не найден",
    "no_agent": "Агент не найден",
    "agents_down": "Учёт агентов недоступен — повторите позже",
    "journal": "Журнал действий преподавателя не пишется — просмотр и изменения закрыты",
    "no_llm": "Доли бюджета недоступны — LLM-прокси не подключён",
    "shares_down": "Доли бюджета недоступны — повторите позже",
    "busy": PROXY_MESSAGES["busy"],
}
# код и имя ответа каждого отказа кабинета; 503 asleep и no_answer — WorkplaceNotReady: по нему веб рисует «Разбудить
# для просмотра» (R106), прочие 503 — Unavailable, занятость — WorkplaceBusy, как у прокси
_REFUSALS = {
    "teacher_only": (403, ERROR_NAMES[403]),
    "no_student": (404, ERROR_NAMES[404]),
    "asleep": (503, "WorkplaceNotReady"),
    "no_answer": (503, "WorkplaceNotReady"),
    "too_big": (502, ERROR_NAMES[502]),
    "bad_answer": (502, ERROR_NAMES[502]),
    "no_session": (404, ERROR_NAMES[404]),
    "no_agent": (404, ERROR_NAMES[404]),
    "agents_down": (503, ERROR_NAMES[503]),
    "journal": (503, ERROR_NAMES[503]),
    "no_llm": (503, ERROR_NAMES[503]),
    "shares_down": (503, ERROR_NAMES[503]),
    "busy": (503, "WorkplaceBusy"),
}

# Д10-R102: к месту — ровно эти три вида, только GET; путь собирается из шаблона и проверенного id разговора
TEACHER_PLACE_PATHS: dict[str, tuple[str, str, tuple[tuple[str, str], ...]]] = {
    "sessions": ("GET", "/experimental/session", (("roots", "true"), ("archived", "true"))),
    "session": ("GET", "/session/{ses}", ()),
    "messages": ("GET", "/session/{ses}/message", ()),
}
# предел ответа места: список разговоров и сам разговор — как списки сверки прокси, сообщения — как ответ вызова
_PLACE_LIMITS = {"sessions": "list", "session": "list", "messages": "upstream"}

_NN = re.compile(r"0[1-9]|10")
_SES = ID_RE["ses"]
_NO_ANSWER = (httpx.HTTPError, OSError)         # TimeoutError срока — подкласс OSError; PlaceAnswerEncoded — тоже


class _Refused(Exception):
    """Отказ шага чтения места: key — ключ TEACHER_TEXTS (или place_auth); failed — по вине стенда (счётчик
    read_failed); error — класс исключения места для строки журнала службы."""

    def __init__(self, key: str, *, failed: bool = True, error: str | None = None):
        super().__init__(key)
        self.key = key
        self.failed = failed            # read_failed: по вине стенда
        self.error = error


class TeacherGate:
    """Ворота кабинета (Д10-R102): к одному месту не больше TEACHER_PARALLEL чтений разом, ждущих — не больше
    TEACHER_QUEUE, ожидание — не дольше TEACHER_WAIT_S; сверх — _Refused("busy"). Семафор места — при первом чтении.
    Константы читаются при вызове (тесты их меняют). Живёт у роли (GatewayRole.teacher_gate)."""

    def __init__(self) -> None:
        self._sems: dict[int, asyncio.Semaphore] = {}
        self._waiting: dict[int, int] = {}

    @asynccontextmanager
    async def slot(self, n: int) -> AsyncIterator[None]:
        sem = self._sems.get(n)
        if sem is None:
            sem = self._sems[n] = asyncio.Semaphore(TEACHER_PARALLEL)
        if sem.locked() and self._waiting.get(n, 0) >= TEACHER_QUEUE:
            raise _Refused("busy", failed=False)
        self._waiting[n] = self._waiting.get(n, 0) + 1
        try:
            async with asyncio.timeout(TEACHER_WAIT_S):
                await sem.acquire()
        except TimeoutError:
            raise _Refused("busy", failed=False) from None
        finally:
            self._waiting[n] -= 1
        try:
            yield
        finally:
            sem.release()


@dataclass
class _Call:
    """Поля строки журнала службы вызова кабинета: логин, действие, место, начало (monotonic)."""
    login: str
    action: str
    place: int | None
    started: float = field(default_factory=time.monotonic)


def _nn(place: int | None) -> str:
    return "-" if place is None else f"{place:02d}"


def _line(call: _Call, outcome: str, status: int, tail: str = "") -> None:
    ms = round((time.monotonic() - call.started) * 1000)
    (log.warning if status >= 500 else log.info)(
        "gw teacher login=%s action=%s place=%s outcome=%s status=%d ms=%d%s", call.login, call.action,
        _nn(call.place), outcome, status, ms, tail)


def _ok(call: _Call, body: dict, status: int = 200, *, count: Callable[[], None] | None = None) -> JSONResponse:
    """Удача: ответ собирается (тело кодируется) до счётчика удачи count и строки «ok» журнала службы — сбой сборки не
    оставляет ни «ok», ни удачного чтения (доводка р1, I-1)."""
    response = JSONResponse(body, status_code=status)
    if count is not None:
        count()
    _line(call, "ok", status)
    return response


def _fail(call: _Call, key: str, tail: str = "") -> JSONResponse:
    status, name = _REFUSALS[key]
    _line(call, key, status, tail)
    return gw_error(status, name, TEACHER_TEXTS[key])


def _fail_text(call: _Call, status: int, name: str, message: str, outcome: str) -> JSONResponse:
    _line(call, outcome, status)
    return gw_error(status, name, message)


def place_of(nn: str) -> int | None:
    """Номер места из части пути: только «01»…«10»."""
    return int(nn) if _NN.fullmatch(nn) else None


def _agent_name_ok(name: str) -> bool:
    return NAME_MIN <= len(name) <= NAME_MAX and NAME_RE.fullmatch(name) is not None


# --- место: только три GET ---

def teacher_path(kind: str, ses: str | None = None) -> tuple[str, str, list[tuple[str, str]]]:
    """(метод, путь, строка запроса) вида kind из TEACHER_PLACE_PATHS; id разговора — только по ID_RE["ses"]. Иное —
    ValueError: путь к месту из чего-то, кроме шаблона и проверенного id, не строится."""
    if kind not in TEACHER_PLACE_PATHS:
        raise ValueError("вид запроса к месту вне TEACHER_PLACE_PATHS")
    method, template, params = TEACHER_PLACE_PATHS[kind]
    if "{ses}" in template:
        if not isinstance(ses, str) or _SES.fullmatch(ses) is None:
            raise ValueError("id разговора вне ID_RE")
        path = template.format(ses=ses)
    elif ses is not None:
        raise ValueError("у этого вида id разговора нет")
    else:
        path = template
    if method != "GET":
        raise ValueError("кабинет ходит к месту только GET")
    return method, path, list(params)


async def _teacher_fetch(role: "GatewayRole", n: int, kind: str, ses: str | None = None) -> PlaceAnswer:
    """Запрос кабинета к месту n — только вид kind из TEACHER_PLACE_PATHS (не путь); предел ответа — по виду."""
    method, path, params = teacher_path(kind, ses)
    limit = UPSTREAM_MAX_BYTES if _PLACE_LIMITS[kind] == "upstream" else PLACE_LIST_MAX_BYTES
    return await role.workplaces.fetch(n, method, path, params=params or None, max_bytes=limit)


def _place_body(answer: PlaceAnswer, *, not_found: bool = False) -> bytes | bytearray:
    """Тело ответа места 200; 401 — place_auth, 404 (там, где он значит «разговора нет») — no_session, прочее —
    bad_answer."""
    if answer.status == 401:
        raise _Refused("place_auth")
    if answer.status == 404 and not_found:
        raise _Refused("no_session", failed=False)
    if answer.status != 200:
        raise _Refused("bad_answer")
    return answer.body


async def _read_once(role: "GatewayRole", n: int, ses: str | None) -> dict:
    """Список разговоров места (ses None) или стенограмма разговора ses: два или три GET вида TEACHER_PLACE_PATHS."""
    try:
        if ses is None:
            return {"sessions": sessions_view(_place_body(await _teacher_fetch(role, n, "sessions")))}
        session = _place_body(await _teacher_fetch(role, n, "session", ses), not_found=True)
        messages = _place_body(await _teacher_fetch(role, n, "messages", ses), not_found=True)
        body = transcript(session, messages)
    except PlaceAnswerTooLarge:
        raise _Refused("too_big") from None
    except TranscriptError:
        raise _Refused("bad_answer") from None
    except httpx.PoolTimeout:                       # пул клиента места занят — место живо, как у прокси (G1)
        raise _Refused("busy", failed=False) from None
    except _NO_ANSWER as exc:                       # в том числе нет пароля места и сжатый ответ (OSError)
        raise _Refused("no_answer", error=type(exc).__name__) from None
    if body["session"]["id"] != ses:                # место отдало чужой разговор — не по договору
        raise _Refused("bad_answer")
    return body


async def _read_place(role: "GatewayRole", n: int, ses: str | None) -> dict:
    """Чтение места под воротами кабинета и сроком хода PROXY_DEADLINE_S (срок вышел — no_answer)."""
    try:
        async with asyncio.timeout(PROXY_DEADLINE_S):
            async with role.teacher_gate.slot(n):
                return await _read_once(role, n, ses)
    except TimeoutError:
        raise _Refused("no_answer", error="TimeoutError") from None


# --- тела записей ---

def _json_body(raw: bytes) -> object:
    try:
        return json.loads(raw, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):            # в том числе повтор ключа и UnicodeDecodeError
        return None


def changes_of(raw: bytes) -> list[tuple[int, int, int]] | None:
    """Тело ровно {"changes": [{"place": "NN", "from_cents": int, "to_cents": int}, …]} (1–10 элементов, без повтора и
    лишних ключей, целые — не bool) → [(место, было, станет)]; иначе None. Прочие правила (диапазон, повтор места) —
    Shares.apply."""
    body = _json_body(raw)
    if not isinstance(body, dict) or set(body) != {"changes"}:
        return None
    items = body["changes"]
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        return None
    plan = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"place", "from_cents", "to_cents"}:
            return None
        place = place_of(item["place"]) if isinstance(item["place"], str) else None
        was, to = item["from_cents"], item["to_cents"]
        if place is None or type(was) is not int or type(to) is not int:
            return None
        plan.append((place, was, to))
    return plan


def floor_body_of(raw: bytes) -> tuple[int, int] | None:
    """Тело ровно {"place": "NN", "to": "MM"} → (место, на место); иначе None. Чисел пола клиент не передаёт."""
    body = _json_body(raw)
    if not isinstance(body, dict) or set(body) != {"place", "to"}:
        return None
    if not isinstance(body["place"], str) or not isinstance(body["to"], str):
        return None
    place, to = place_of(body["place"]), place_of(body["to"])
    return None if place is None or to is None else (place, to)


# --- обзор и деньги ---

def _llm_ready(role: "GatewayRole") -> "LlmRole | None":
    """Роль llm, если её доли можно показывать и менять: доли загружены и журнал вызовов открыт (иначе пол — 0)."""
    llm = role.llm
    if llm is None or llm.shares is None or not llm.shares.loaded or not llm.ledger.opened:
        return None
    return llm


def _spent(llm: "LlmRole", n: int) -> float:
    return max(0.0, llm.ledger.spent(f"student-{n:02d}"))


def _budget_json(llm: "LlmRole", allocated: int) -> dict:
    total = llm.shares.total_cents
    return {"total_cents": total, "allocated_cents": allocated, "free_cents": max(0, total - allocated),
            "spent_usd": max(0.0, llm.budget.page_spent()), "ops_usd": max(0.0, llm.ledger.spent(OPS))}


def _accounts(store) -> tuple[dict[int, Account], set[int]]:
    """Учётки студентов по местам (действующие и отключённые) и закреплённые места (в том числе за удалёнными)."""
    students = {a.place: a for a in store.list() if a.role == "student" and a.place in PLACES}
    return students, {place for place, _, _ in store.bindings()}


def _marks(role: "GatewayRole", students: dict[int, Account],
           pending: dict[str, tuple[float, ...]]) -> tuple[dict[int, tuple[bool, bool]], dict[int, int] | None]:
    """Пометки учёток (подтвердил ли предупреждение, заперт ли вход) и живые агенты по местам (сбой учёта — None)."""
    store = role.store
    marks = {n: (store.notice_acked(a.login, NOTICE), store.locked(a.login, pending[a.login]))
             for n, a in students.items()}
    try:
        agents = role.agents.live_counts()
    except AgentsStoreError:
        agents = None
    return marks, agents


async def overview_json(role: "GatewayRole") -> dict:
    """Обзор мест (Д10-R101): память мест и долей, учётки и учёт агентов — в потоке. Сбой учёток — StoreError."""
    students, bound = await asyncio.to_thread(_accounts, role.store)
    gate = role.login_gate                          # незаписанные неудачи входа — в цикле событий
    pending = {a.login: gate.pending_of(login_key(a.login)) for a in students.values()}
    marks, agents = await asyncio.to_thread(_marks, role, students, pending)
    llm = _llm_ready(role)
    places = []
    for n in PLACES:
        account = students.get(n)
        noticed, locked = marks.get(n, (None, None))
        places.append({
            "place": f"{n:02d}",
            "login": None if account is None else account.login,
            "free": n not in bound,
            "disabled": None if account is None else account.disabled,
            "noticed": None if account is None else noticed,
            "locked": None if account is None else locked,
            "state": role.workplaces.view(n).state,
            "share_cents": None if llm is None else llm.shares.cents(n),
            "floor_cents": None if llm is None else llm.shares.floor_of(n),
            "spent_usd": None if llm is None else _spent(llm, n),
            "agents": None if agents is None else agents.get(n, 0),
        })
    return {"places": places, "budget": None if llm is None else _budget_json(llm, llm.shares.sum_cents()),
            "shares_ok": llm is not None}


def _shares_json(llm: "LlmRole", after: dict[int, int]) -> dict:
    places = [{"place": f"{n:02d}", "share_cents": after[n], "floor_cents": llm.shares.floor_of(n),
               "spent_usd": _spent(llm, n)} for n in PLACES]
    return {"places": places, "budget": _budget_json(llm, sum(after.values()))}


def _journal_row(row) -> dict:
    shown = {"id": row.id, "at": row.at, "login": row.login, "action": row.action, "place": None if row.place is None
             else f"{row.place:02d}", "target": row.target, "outcome": row.outcome}
    if row.action == "shares":                      # detail — только у долей: центы и номера мест
        shown["detail"] = row.detail
    return shown


# --- маршруты ---

class _Cabinet:
    """Общие шаги ручек кабинета: роль, источник, место со студентом, строка журнала действий."""

    def __init__(self, role: "GatewayRole"):
        self.role = role
        self.check_origin = origin_checked(role)
        self.refusals = _RolledLines(role.monotonic)

    def not_teacher(self, account: Account) -> JSONResponse | None:
        """Шаг 4 R100: не преподаватель — 403 teacher_only, событие role_refused, строка журнала службы — одна на логин
        за ORIGIN_LOG_S; ни места, ни учёта, ни журнала действий."""
        if account.role == "teacher":
            return None
        self.role.teacher_stats.event("role_refused")
        rolled = self.refusals.hit(account.login)
        if rolled is not None:
            log.warning("gw teacher refused login=%s reason=role n=%d since_s=%d", account.login, *rolled)
        status, name = _REFUSALS["teacher_only"]
        return gw_error(status, name, TEACHER_TEXTS["teacher_only"])

    async def origin(self, request: Request, call: _Call) -> None:
        """Шаг 5 R100 (у POST): чужой источник — 403 (GatewayRefusal origin_checked) и строка вызова."""
        try:
            await self.check_origin(request)
        except GatewayRefusal:
            _line(call, "origin", 403)
            raise

    async def student_place(self, call: _Call) -> JSONResponse | None:
        """Шаг 7 R100: на месте call.place есть учётка студента (действующая или отключённая), иначе 404 no_student;
        сбой учёток — 503 store."""
        n = call.place
        try:
            accounts = await asyncio.to_thread(self.role.store.list)
        except StoreError:
            return _fail_text(call, 503, ERROR_NAMES[503], GW_MESSAGES["store"], "store")
        if not any(a.role == "student" and a.place == n for a in accounts):
            return _fail(call, "no_student")
        return None

    async def journal(self, call: _Call, target: str | None = None) -> JSONResponse | None:
        """Шаг 8 R100: строка requested до доступа; журнал не открыт или запись не прошла — 503 journal, read_failed."""
        log_ = self.role.teacher_log
        try:
            if not log_.opened:
                raise TeacherLogError("журнал действий не открыт")
            await asyncio.to_thread(log_.requested, call.login, call.action, call.place, target)
        except TeacherLogError:
            self.role.teacher_stats.event("read_failed")
            return _fail(call, "journal")
        return None

    def refused_place(self, call: _Call, exc: _Refused) -> JSONResponse:
        """Отказ чтения места: счётчики (read_failed, upstream_failed, place_auth_failed); нет ответа — место
        неготово."""
        role, n = self.role, call.place
        if exc.failed:
            role.teacher_stats.event("read_failed")
        if exc.key == "place_auth":
            role.stats.event("place_auth_failed")
            return _fail_text(call, 502, ERROR_NAMES[502], PROXY_MESSAGES["place_auth"], "place_auth")
        if exc.key == "no_answer":
            role.workplaces.mark_unready(n)
            role.stats.event("upstream_failed")
            return _fail(call, "no_answer", f" error={exc.error}" if exc.error else "")
        return _fail(call, exc.key)


def teacher_router(role: "GatewayRole") -> APIRouter:
    """GET /health/teacher (зона front) и ручки кабинета (зона, настройка, сессия — у маршрутов, как agents_router,
    m24); ставится в GatewayRole.router до catch_all_router."""
    router = APIRouter()
    account_of = current_account(role)
    guarded = [Depends(front_only), Depends(configured(role))]
    cab = _Cabinet(role)
    P = TEACHER_PREFIX

    def read_ok() -> None:                          # удачное чтение — после сборки ответа (_ok)
        role.teacher_stats.event("read")

    @router.get(HEALTH_TEACHER_PATH, dependencies=[Depends(front_only)])
    async def health_teacher() -> JSONResponse:
        # только числа и флаги: ни логинов, ни номеров мест
        return JSONResponse(role.teacher_health_json(role.wallclock()))

    @router.get(P + "overview", dependencies=guarded)
    async def overview(account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        call = _Call(account.login, "overview", None)
        try:
            body = await overview_json(role)
        except StoreError:
            return _fail_text(call, 503, ERROR_NAMES[503], GW_MESSAGES["store"], "store")
        return _ok(call, body)

    @router.get(P + "journal", dependencies=guarded)
    async def journal(account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        call = _Call(account.login, "journal", None)
        try:                                        # чтение журнала само в журнал не пишется
            rows = await asyncio.to_thread(role.teacher_log.recent, JOURNAL_ROWS)
        except TeacherLogError:
            return _fail(call, "journal")
        return _ok(call, {"rows": [_journal_row(row) for row in rows]})

    @router.get(P + "places/{nn}", dependencies=guarded)
    async def place(nn: str, account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        n = place_of(nn)
        if n is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        call = _Call(account.login, "place", n)
        if (refused := await cab.student_place(call)) is not None:
            return refused
        return _ok(call, role.workplaces.view(n).to_json())     # память, не будит

    @router.post(P + "places/{nn}/wake", dependencies=guarded)
    async def wake(nn: str, request: Request, account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        call = _Call(account.login, "wake", place_of(nn))
        await cab.origin(request, call)
        if call.place is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        if (refused := await cab.student_place(call)) is not None:
            return refused
        if (refused := await cab.journal(call)) is not None:
            return refused
        # обычный запуск места — та же задача, что у студента (I-2): атрибут читается при вызове
        view = await role.workplaces.wake(call.place)
        return _ok(call, view.to_json(), status=202)

    async def read(account: Account, nn: str, ses: str | None) -> Response:
        """Список разговоров места (ses None) или стенограмма разговора ses: R100, шаги 6–10."""
        call = _Call(account.login, "sessions" if ses is None else "read", place_of(nn))
        if call.place is None or (ses is not None and _SES.fullmatch(ses) is None):
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        if (refused := await cab.student_place(call)) is not None:
            return refused
        if (refused := await cab.journal(call, ses)) is not None:
            return refused
        if not role.workplaces.is_ready(call.place):    # место не будится: будит только кнопка кабинета
            return _fail(call, "asleep")
        try:
            body = await _read_place(role, call.place, ses)
        except _Refused as exc:
            return cab.refused_place(call, exc)
        return _ok(call, body, count=read_ok)

    @router.get(P + "places/{nn}/sessions", dependencies=guarded)
    async def sessions(nn: str, account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        return await read(account, nn, None)

    @router.get(P + "places/{nn}/sessions/{ses}", dependencies=guarded)
    async def session(nn: str, ses: str, account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        return await read(account, nn, ses)

    @router.get(P + "places/{nn}/agents", dependencies=guarded)
    async def agents(nn: str, account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        call = _Call(account.login, "agents", place_of(nn))
        if call.place is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        if (refused := await cab.student_place(call)) is not None:
            return refused
        if (refused := await cab.journal(call)) is not None:
            return refused
        try:
            cards = await asyncio.to_thread(role.agents.cards, call.place)
        except (AgentsStoreError, DirUnavailable):
            role.teacher_stats.event("read_failed")
            return _fail(call, "agents_down")
        return _ok(call, {"agents": [{"name": c.name, "title": c.title, "node": c.node, "purpose": c.purpose,
                                      "tools": c.tools, "tags": c.tags, "state": c.state, "created_at": c.created_at,
                                      "visible_at": c.visible_at} for c in cards]}, count=read_ok)

    @router.get(P + "places/{nn}/agents/{name}", dependencies=guarded)
    async def agent(nn: str, name: str, account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        call = _Call(account.login, "agent", place_of(nn))
        if call.place is None or not _agent_name_ok(name):
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        if (refused := await cab.student_place(call)) is not None:
            return refused
        if (refused := await cab.journal(call, name)) is not None:
            return refused
        try:
            text = await asyncio.to_thread(role.agents.read_file, call.place, name)
        except AgentMissing as exc:
            if exc.odd:                             # ссылка, не файл, больше предела, не UTF-8 — без имени и пути
                log.warning("gw teacher agent_odd place=%02d", call.place)
            return _fail(call, "no_agent")
        except (AgentsStoreError, DirUnavailable):
            role.teacher_stats.event("read_failed")
            return _fail(call, "agents_down")
        return _ok(call, {"name": name, "text": text}, count=read_ok)

    async def change(call: _Call, request: Request, parse: Callable[[bytes], object],
                     run: Callable[["LlmRole", object], dict[int, int]],
                     refused_detail: Callable[[object, str], dict]) -> Response:
        """Изменение доли (R104): источник → роль llm (no_llm) → доли и журнал вызовов (shares_down) → тело (bad_body) →
        Shares в потоке; строка журнала действий — в его транзакции."""
        await cab.origin(request, call)
        if role.llm is None:
            return _fail(call, "no_llm")
        llm = _llm_ready(role)
        if llm is None:                             # пол без журнала вызовов был бы 0 (ревью А2, m-2): без записи
            return _fail(call, "shares_down")
        body = parse(await request.body())
        if body is None:
            return _fail_text(call, 400, ERROR_NAMES[400], GW_MESSAGES["bad_body"], "bad_body")
        try:
            after = await asyncio.to_thread(run, llm, body)
        except ShareRefused as exc:
            await asyncio.to_thread(role.teacher_log.refused, call.login, "shares", None,
                                    refused_detail(body, exc.kind))     # без гарантии: отказ уже есть
            status = 409 if exc.kind == "conflict" else 400
            return _fail_text(call, status, ERROR_NAMES[status], exc.text, exc.kind)
        except SharesStoreError as exc:             # текст — класс и код SQLite; в журнал службы его пишет сам Shares
            role.teacher_stats.event("change_failed")
            return _fail(call, "journal" if exc.journal else "shares_down")
        role.teacher_stats.event("change")
        return _ok(call, _shares_json(llm, after))

    @router.post(P + "shares", dependencies=guarded)
    async def shares(request: Request, account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        login = account.login
        return await change(
            _Call(login, "shares", None), request, changes_of,
            lambda llm, plan: llm.shares.apply(plan, by=login, now=role.wallclock()),
            lambda plan, kind: {**{f"{n:02d}": [was, to] for n, was, to in plan}, "reason": kind})

    @router.post(P + "shares/floor", dependencies=guarded)
    async def shares_floor(request: Request, account: Account = Depends(account_of)) -> Response:
        if (refused := cab.not_teacher(account)) is not None:
            return refused
        login = account.login
        return await change(
            _Call(login, "shares_floor", None), request, floor_body_of,
            lambda llm, pair: llm.shares.apply_floor(*pair, by=login, now=role.wallclock()),
            lambda pair, kind: {"kind": "floor", "reason": kind, "from": f"{pair[0]:02d}", "to": f"{pair[1]:02d}"})

    return router
