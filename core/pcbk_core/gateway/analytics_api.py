"""Аналитический чат технолога КДМ-1 у шлюза (Task 0 — пустой роутер; Task 4 — реальные ручки): маршруты
`/pcbk/api/analytics/*`, подключённые в `GatewayRole.router` только при `role.analytics_enabled` (Global
Constraints плана: аналитика — за выключателем; выключена — маршрутов нет вовсе, 404 через `catch_all_router`,
не 503 — иначе существование подсистемы было бы видно по коду ответа даже выключенным клиентам). Образец —
`lessons_router`/`agents_router`: зона (`front_only`) и ошибка настройки (`configured(role)`) — у самого
маршрута, до ловящего (m24); сессия — любая вошедшая учётка (`current_account`), как у `/pcbk/api/lessons*`
(задача не сужает круг по месту: справка технолога и преподавателя — разные права внутри `service.py`, не
разные маршруты). Состояние меняющие ручки (`POST`) дополнительно проверяют источник записи после сессии
(`origin_checked`, приём `workplace_router`/`calc_router`): без учётки — 401 без строки журнала и счётчика
`origin_refused`, с учёткой чужого источника — 403.

`role.analytics` — `None` (аналитика включена выключателем роутера, но сервис не подключён — старый текст
Task 0, не изменился ни в одном пути) — все ручки отвечают прежней заглушкой `ANALYTICS_NOT_READY_TEXT`; это
не ломает тесты Task 0 (`test_gw_analytics_api.py`), где `role.analytics` не задан. `role.analytics` подключён
(`main.py`/`gateway/__init__.py`, Task 4) — ручки зовут `AnalyticsService` по-настоящему; его собственный сбой
(`config_error`/`corpus_error` — состояние/корпус не открылись) отвечает понятным 503 (`_state_broken`/
`_reference_broken` ниже), не падением (мастер-промпт §4).

Идентичность — `RequestIdentity`, собранная из `Account` сессии (`_identity_of`, ровно формула
`docs/kdm1/INTERFACES.md`, «Личность вызывающего»), никогда из тела запроса: `AnalysisRequest`/модели этого
файла — `extra="forbid"`, а `place/caller/login/owner/role` они и не объявляют. Студент без рабочего места
(правка базы мимо `AccountStore.add`, тот же редкий случай, что у `gateway.calc_api.caller_for`) — 403, как у
калькуляторов страницы.

Формы JSON ответов — зеркало `web/src/analytics.ts`/`analyticsApi.ts` (Task 6, коммит f6c0c95): контракта
схемой у них нет (только `AnalysisRequest`/`Preanalysis`/`LlmSelection`/`JobResponse` — `CONTRACT_SCHEMAS`
`models.py`), формы `capabilities`/`conversations`/`evidence`/`reviews` собраны по дробным ключам, которые
веб уже разбирает строгим `hasExactKeys`. Отступать от них нельзя без правки веба (не этот файл её делает —
расхождение, если оно найдётся, описывается в отчёте задачи, не правкой `web/`)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from . import clip
from .accounts import Account
from .api import ERROR_NAMES, GW_MESSAGES, PREFIX, configured, current_account, front_only, gw_error, origin_checked
from ..analytics.models import AnalysisMode, AnalysisRequest, RequestIdentity
from ..analytics.reviews import ReviewDecision, ReviewKind, ReviewStoreError, ReviewStoreUnavailable
from ..analytics.service import AnalyticsService, AnalyticsUnavailable
from ..analytics.store import ConversationMeta, StoreError, StoreUnavailable

if TYPE_CHECKING:
    from . import GatewayRole
    from ..analytics.reviews import ReviewRequest
    from ..analytics.service import EquipmentLinkInfo

ANALYTICS_PREFIX = PREFIX + "/analytics"
CAPABILITIES_PATH = ANALYTICS_PREFIX + "/capabilities"
CONVERSATIONS_PATH = ANALYTICS_PREFIX + "/conversations"
CONVERSATION_PATH = CONVERSATIONS_PATH + "/{conversation_id}"
QUESTIONS_PATH = CONVERSATION_PATH + "/questions"
REQUESTS_PATH = ANALYTICS_PREFIX + "/requests/{request_id}"
REQUEST_CANCEL_PATH = REQUESTS_PATH + "/cancel"
EVIDENCE_PATH = ANALYTICS_PREFIX + "/evidence/{evidence_id}"
# согласование (мастер-промпт §6): набор путей Task 7 может расширить, эти три — не переименовывать и не убирать
REVIEW_REQUESTS_PATH = ANALYTICS_PREFIX + "/reviews/requests"
REVIEW_REQUEST_PATH = REVIEW_REQUESTS_PATH + "/{review_id}"
REVIEW_DECISION_PATH = REVIEW_REQUEST_PATH + "/decisions"
# «Связи с историаном» (план Д17, Task 1): состояние строк equipment_links + справочный поиск кандидатов тега
LINKS_PATH = ANALYTICS_PREFIX + "/links"
LINK_CANDIDATES_PATH = LINKS_PATH + "/{link_id}/candidates"

ANALYTICS_NOT_READY_TEXT = "Аналитика КДМ-1 ещё не готова"
ANALYTICS_BROKEN_PREFIX = "Аналитика КДМ-1 недоступна: "
ANALYTICS_BROKEN_SHOWN = 80                  # длина причины в 503 — без путей хоста (её и так укоротил сам сервис)
# правка F4b, итоговое ревью (H-18): отдельный текст от ANALYTICS_BROKEN_PREFIX (config_error/corpus_error —
# подсистема не открылась вовсе) — здесь состояние/корпус открыты, но ОДНА операция хранилища сейчас не
# прошла (store.StoreUnavailable: нет места/только чтение/заблокировано) — временный сбой записи/чтения, не
# диагноз настройки; без причины сбоя SQLite в тексте (класс ошибки уже обрезан самим store.py, см. `_why`).
ANALYTICS_STORE_UNAVAILABLE_TEXT = "Аналитика КДМ-1 временно недоступна"
ROLE_TEXT = "Аналитика КДМ-1 — только для студента с рабочим местом и преподавателя"
TEACHER_CALLER = "teacher"                   # тот же вид, что gateway.calc_api.TEACHER_CALLER


def _not_ready() -> JSONResponse:
    return gw_error(503, ERROR_NAMES[503], ANALYTICS_NOT_READY_TEXT)


def _unavailable(reason: str) -> JSONResponse:
    return gw_error(503, ERROR_NAMES[503], ANALYTICS_BROKEN_PREFIX + clip(reason, ANALYTICS_BROKEN_SHOWN))


def _store_unavailable() -> JSONResponse:
    """H-18: сбой самого хранилища (`store.StoreUnavailable`) — на новый вопрос (запись невозможна) и на
    чтение существующей истории/задания (прочитать нельзя) отвечает 503, а не 404 «не найдено» и не
    необработанным 500 (прежнее поведение — см. докстринги `store.StoreUnavailable`/`store.StoreError`)."""
    return gw_error(503, ERROR_NAMES[503], ANALYTICS_STORE_UNAVAILABLE_TEXT)


def _state_broken(svc: AnalyticsService) -> JSONResponse | None:
    """Состояние (SQLite) не открылось — ничего из аналитики читать/писать нельзя (история, задания, пакеты)."""
    if svc.config_error:
        return _unavailable(svc.config_error)
    return None


def _reference_broken(svc: AnalyticsService) -> JSONResponse | None:
    """Состояние ИЛИ корпус не открылись — для ручек, которым нужен живой корпус (создание диалога/вопроса);
    чтение уже существующей истории (`_state_broken`) корпуса не требует — старые ответы не исчезают из-за
    временной порчи корпуса (мастер-промпт §4)."""
    broken = _state_broken(svc)
    if broken is not None:
        return broken
    if svc.corpus_error:
        return _unavailable(svc.corpus_error)
    return None


def _reviews_broken(svc: AnalyticsService) -> JSONResponse | None:
    """Согласование (`ReviewStore`, Task 7, шаг 2) не открылось — отдельный сбой от `_state_broken`
    (`AnalyticsStore`/`svc.config_error`): тот же файл `ANALYTICS_STATE_PATH`, свои таблицы, деградирует
    отдельно (`AnalyticsService.reviews_error`, докстринг `service.py::open`)."""
    if svc.reviews_error:
        return _unavailable(svc.reviews_error)
    return None


def _identity_of(account: Account) -> RequestIdentity | None:
    """`docs/kdm1/INTERFACES.md`, «Личность вызывающего»: `caller` — `student-NN` по месту учётки или `teacher`;
    студент без места (правка базы мимо `AccountStore.add`) — `None` (вызывающий отвечает 403, как у
    `gateway.calc_api.caller_for`)."""
    if account.role == "student" and account.place is None:
        return None
    caller = TEACHER_CALLER if account.role == "teacher" else f"student-{account.place:02d}"
    return RequestIdentity(login=account.login, role=account.role, place=account.place, caller=caller)


def _json_object(raw: bytes) -> dict | None:
    """Тело — объект JSON; битый JSON, не UTF-8, массив, строка, число и null — `None` (приём `calc_api._body_object`:
    тело читается вручную — разбор FastAPI дал бы 422 мимо отказов вида OpenCode)."""
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):        # в том числе UnicodeDecodeError и пустое тело
        return None
    return body if isinstance(body, dict) else None


class _CreateConversation(BaseModel):
    """Тело POST .../conversations — ровно `{"mode": ...}`; контракта схемой у этой ручки нет (только у
    `AnalysisRequest`/`Preanalysis`/`LlmSelection`/`JobResponse`, `models.CONTRACT_SCHEMAS`), но `extra="forbid"`
    — тот же приём «клиент не задаёт лишнего» (мастер-промпт §10)."""

    model_config = ConfigDict(extra="forbid")
    mode: AnalysisMode


def _conversation_json(meta: ConversationMeta) -> dict:
    return {"id": meta.id, "mode": meta.mode, "created_at": meta.created_at.isoformat(),
           "last_activity_at": meta.last_activity_at.isoformat(), "corpus_version": meta.corpus_version}


def _conversations_json(metas: list[ConversationMeta]) -> dict:
    return {"conversations": [_conversation_json(m) for m in metas]}


# --- согласование (reviews/*, Task 7, шаг 2): формы — зеркало web/src/analytics.ts (parseReviewRequestItem/
# parseReviewList), не контракт схемой (нет файла в analytics/contracts/, как и у capabilities/conversations) ---

_Text1to250 = Annotated[str, StringConstraints(min_length=1, max_length=250)]
_Text1to4000 = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
_Text1to120 = Annotated[str, StringConstraints(min_length=1, max_length=120)]
_Text1to2000 = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class _CreateReview(BaseModel):
    """Тело POST .../reviews/requests — зеркало `web/src/analyticsApi.ts::CreateReviewInput`
    (`kind, subject_id, new_value, scope, rationale, old_value?, period_start?, period_end?`); `extra="forbid"`
    — клиент не задаёт `created_by`/`status`/`id` (мастер-промпт §10)."""

    model_config = ConfigDict(extra="forbid")
    kind: ReviewKind
    subject_id: _Text1to250
    new_value: _Text1to4000
    scope: _Text1to120
    rationale: _Text1to2000
    old_value: _Text1to4000 | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None


class _DecideReview(BaseModel):
    """Тело POST .../reviews/requests/{id}/decisions — зеркало `analyticsApi.ts::decideReview(id, decision,
    comment)`: `extra="forbid"`, клиент не задаёт `decided_by`/`decided_at`."""

    model_config = ConfigDict(extra="forbid")
    decision: ReviewDecision
    comment: Annotated[str, StringConstraints(max_length=2000)] = ""


def _review_json(r: "ReviewRequest") -> dict:
    """Одна заявка → ровно ключи `web/src/analytics.ts::parseReviewRequestItem` (`hasExactKeys`, строгий
    разбор — лишнее/недостающее поле там отказ, не тихий игнор)."""
    return {
        "id": r.id, "kind": r.kind, "subject_id": r.subject_id, "old_value": r.old_value,
        "new_value": r.new_value, "scope": r.scope,
        "period_start": None if r.period_start is None else r.period_start.isoformat(),
        "period_end": None if r.period_end is None else r.period_end.isoformat(),
        "rationale": r.rationale, "status": r.status, "created_by": r.created_by,
        "created_at": r.created_at.isoformat(), "decided_by": r.decided_by,
        "decided_at": None if r.decided_at is None else r.decided_at.isoformat(), "comment": r.comment,
    }


def _reviews_json(items: list["ReviewRequest"]) -> dict:
    return {"requests": [_review_json(r) for r in items]}


# --- «Связи с историаном» (links/*, план Д17, Task 1): формы — зеркало web/src/analytics.ts, не контракт схемой ---

def _link_json(info: "EquipmentLinkInfo") -> dict:
    return {"id": info.id, "display_label": info.display_label, "document_context": info.document_context,
           "state": info.state, "historian_tag": info.historian_tag, "approved_by": info.approved_by,
           "approved_at": None if info.approved_at is None else info.approved_at.isoformat(),
           "pending_request_id": info.pending_request_id}


def analytics_router(role: "GatewayRole") -> APIRouter:
    """`/pcbk/api/analytics/*`; ставится в `GatewayRole.router` до `catch_all_router`, только когда
    `role.analytics_enabled` — иначе эта функция не вызывается вовсе (решение — в `GatewayRole.router`, не
    здесь: сам роутер не умеет самоотключаться)."""
    router = APIRouter()
    account_of = current_account(role)
    check_origin = origin_checked(role)
    guarded = [Depends(front_only), Depends(configured(role))]

    def _svc() -> AnalyticsService | None:
        return role.analytics

    @router.get(CAPABILITIES_PATH, dependencies=guarded)
    async def capabilities(account: Account = Depends(account_of)) -> JSONResponse:
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        caps = await asyncio.to_thread(svc.capabilities, identity)
        body = asdict(caps)
        body["corpus_version"] = caps.corpus_version or "unavailable"   # веб ждёт непустую строку (analytics.ts)
        return JSONResponse(body)

    @router.get(CONVERSATIONS_PATH, dependencies=guarded)
    async def list_conversations(account: Account = Depends(account_of)) -> JSONResponse:
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _state_broken(svc)
        if broken is not None:
            return broken
        try:
            metas = await svc.list_conversations(identity)
        except StoreUnavailable:
            return _store_unavailable()
        return JSONResponse(_conversations_json(metas))

    @router.post(CONVERSATIONS_PATH, dependencies=guarded)
    async def create_conversation(request: Request, account: Account = Depends(account_of)) -> JSONResponse:
        await check_origin(request)         # после сессии: без учётки — 401, ни строки, ни счётчика
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _reference_broken(svc)
        if broken is not None:
            return broken
        body = _json_object(await request.body())
        if body is None:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            payload = _CreateConversation.model_validate(body)
        except ValidationError:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            conversation_id = await svc.create_conversation(identity, payload.mode)
        except AnalyticsUnavailable as exc:
            return _unavailable(str(exc))
        except StoreUnavailable:
            return _store_unavailable()
        return JSONResponse({"id": conversation_id}, status_code=201)

    @router.get(CONVERSATION_PATH, dependencies=guarded)
    async def get_conversation(conversation_id: str, account: Account = Depends(account_of)) -> JSONResponse:
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _state_broken(svc)
        if broken is not None:
            return broken
        try:
            meta = await svc.get_conversation(identity, conversation_id)
            if meta is None:
                return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
            turns = await svc.list_requests(identity, conversation_id)
        except StoreUnavailable:
            return _store_unavailable()
        # {"question", "response"} на каждое задание (Правка, раунд 1, Minor «д»): JobResponse — закрытый
        # контракт (job-response.schema.json), вопрос рядом с ним, не внутри него
        return JSONResponse({"conversation": _conversation_json(meta),
                             "requests": [{"question": t.question, "response": t.response.model_dump()}
                                         for t in (turns or [])]})

    @router.post(QUESTIONS_PATH, dependencies=guarded)
    async def ask_question(conversation_id: str, request: Request,
                           account: Account = Depends(account_of)) -> JSONResponse:
        await check_origin(request)
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _reference_broken(svc)
        if broken is not None:
            return broken
        body = _json_object(await request.body())
        if body is None:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            analysis_request = AnalysisRequest.model_validate(body)
        except ValidationError:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            request_id = await svc.submit_question(identity, conversation_id, analysis_request)
        except AnalyticsUnavailable as exc:
            return _unavailable(str(exc))
        except StoreUnavailable:              # H-18: хранилище недоступно (нет места/только чтение/заблокировано)
            return _store_unavailable()        # — 503, не 404 «не найдено» (StoreUnavailable — подкласс, проверяем
        except StoreError:                     # раньше StoreError ниже); чужой/несуществующий диалог — 404, не
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])  # подтверждаем, какая из двух причин
        return JSONResponse({"request_id": request_id}, status_code=202)

    @router.get(REQUESTS_PATH, dependencies=guarded)
    async def get_request(request_id: str, account: Account = Depends(account_of)) -> JSONResponse:
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _state_broken(svc)
        if broken is not None:
            return broken
        try:
            response = await svc.get_request(identity, request_id)
        except StoreUnavailable:              # H-18: чтение при сбое хранилища — 503, не необработанный 500
            return _store_unavailable()
        if response is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        return JSONResponse(response.model_dump())

    @router.post(REQUEST_CANCEL_PATH, dependencies=guarded)
    async def cancel_request(request_id: str, request: Request,
                             account: Account = Depends(account_of)) -> JSONResponse:
        await check_origin(request)
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _state_broken(svc)
        if broken is not None:
            return broken
        try:
            await svc.cancel_request(identity, request_id)  # отменено сейчас или уже терминально — дальше неважно
            response = await svc.get_request(identity, request_id)
        except StoreUnavailable:              # H-18: хранилище недоступно — 503, не необработанный 500
            return _store_unavailable()
        if response is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        return JSONResponse(response.model_dump())

    @router.get(EVIDENCE_PATH, dependencies=guarded)
    async def get_evidence(evidence_id: str, account: Account = Depends(account_of)) -> JSONResponse:
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _state_broken(svc)
        if broken is not None:
            return broken
        try:
            item = await svc.get_evidence(identity, evidence_id)
        except StoreUnavailable:              # H-18: чтение при сбое хранилища — 503, не необработанный 500
            return _store_unavailable()
        if item is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        return JSONResponse(item)

    # reviews/*: Task 7, шаг 2 (ReviewStore подключён внутри AnalyticsService) — порядок проверок ровно тот же,
    # что у прочих ручек (зона/настройка — guarded, сессия — account_of, POST — origin_checked после сессии);
    # «нет права» (403)/«заявка не найдена» (404)/«уже решена» (409) различает тип исключения `reviews.py`, не
    # текст — см. docs/kdm1/INTERFACES.md, раздел reviews.py. F5, п.6 (14.10.2026): сбой самого SQLite хранилища
    # согласования (`reviews.ReviewStoreUnavailable`, подкласс `ReviewStoreError`) — 503 `_store_unavailable()`
    # на ВСЕХ трёх ручках ниже, не 404/500; проверяется ПЕРВЫМ (except — до более общего `ReviewStoreError`,
    # где он есть), это ОТДЕЛЬНЫЙ сбой от `_reviews_broken` выше (тот — подсистема не открылась вовсе при
    # старте; этот — операция упала сейчас на уже открытом хранилище, как `store.StoreUnavailable` у основной
    # `AnalyticsStore`)
    @router.get(REVIEW_REQUESTS_PATH, dependencies=guarded)
    async def list_reviews(account: Account = Depends(account_of)) -> JSONResponse:
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _state_broken(svc) or _reviews_broken(svc)
        if broken is not None:
            return broken
        try:
            items = await svc.list_review_requests(identity)
        except ReviewStoreUnavailable:        # F5, п.6: сбой самого SQLite хранилища согласования — 503, не 500
            return _store_unavailable()
        return JSONResponse(_reviews_json(items))

    @router.post(REVIEW_REQUESTS_PATH, dependencies=guarded)
    async def create_review(request: Request, account: Account = Depends(account_of)) -> JSONResponse:
        await check_origin(request)
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _state_broken(svc) or _reviews_broken(svc)
        if broken is not None:
            return broken
        body = _json_object(await request.body())
        if body is None:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            payload = _CreateReview.model_validate(body)
        except ValidationError:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        # план Д17, Task 1: kind="equipment_link" — subject_id обязан быть id строки equipment_links ТЕКУЩЕГО
        # корпуса, new_value — именем тега по data.sql.SAFE_NAME (иначе заявка несла бы мусорный тег или
        # ссылку на несуществующую строку — это проверяется здесь, раньше reviews.create_request, который сам
        # ни корпус, ни SAFE_NAME не знает)
        if payload.kind == "equipment_link":
            try:
                valid = await svc.validate_equipment_link(payload.subject_id, payload.new_value)
            except AnalyticsUnavailable as exc:
                return _unavailable(str(exc))
            if not valid:
                return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            created = await svc.create_review_request(
                identity, payload.kind, payload.subject_id, payload.new_value, payload.scope, payload.rationale,
                old_value=payload.old_value, period_start=payload.period_start, period_end=payload.period_end)
        except ReviewStoreUnavailable:        # F5, п.6: сбой самого SQLite хранилища согласования — 503, не 500
            return _store_unavailable()
        except ValueError as exc:
            return gw_error(400, ERROR_NAMES[400], str(exc))
        return JSONResponse(_review_json(created), status_code=201)

    @router.post(REVIEW_DECISION_PATH, dependencies=guarded)
    async def decide_review(review_id: str, request: Request,
                            account: Account = Depends(account_of)) -> JSONResponse:
        await check_origin(request)
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _state_broken(svc) or _reviews_broken(svc)
        if broken is not None:
            return broken
        body = _json_object(await request.body())
        if body is None:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            payload = _DecideReview.model_validate(body)
        except ValidationError:
            return gw_error(400, ERROR_NAMES[400], GW_MESSAGES["bad_body"])
        try:
            decided = await svc.decide_review_request(identity, review_id, payload.decision, payload.comment)
        except ReviewStoreUnavailable:      # F5, п.6: сбой самого SQLite — 503, ПРЕЖДЕ более общего
            return _store_unavailable()     # ReviewStoreError ниже (подкласс — порядок except важен)
        except ReviewStoreError:            # чужой/несуществующий review_id — не подтверждаем, какая из причин
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        except PermissionError as exc:
            return gw_error(403, ERROR_NAMES[403], str(exc))
        except ValueError as exc:           # через эту ручку decision уже проверен _DecideReview — остаётся
            return gw_error(409, ERROR_NAMES[409], str(exc))   # только «заявка уже решена», конфликт состояния
        return JSONResponse(_review_json(decided))

    # «Связи с историаном» (links/*, план Д17, Task 1) — состояние строк equipment_links и справочный поиск
    # кандидатов тега; обе ручки нужен и корпус (_reference_broken), и согласование (_reviews_broken, для
    # resolved_link/list_requests).
    @router.get(LINKS_PATH, dependencies=guarded)
    async def list_links(account: Account = Depends(account_of)) -> JSONResponse:
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _reference_broken(svc) or _reviews_broken(svc)
        if broken is not None:
            return broken
        try:
            links = await svc.list_equipment_links(identity)
        except ReviewStoreUnavailable:        # сбой самого SQLite хранилища согласования — 503, не 500
            return _store_unavailable()
        except AnalyticsUnavailable as exc:
            return _unavailable(str(exc))
        return JSONResponse({"links": [_link_json(info) for info in links]})

    @router.get(LINK_CANDIDATES_PATH, dependencies=guarded)
    async def link_candidates(link_id: str, account: Account = Depends(account_of)) -> JSONResponse:
        svc = _svc()
        if svc is None:
            return _not_ready()
        identity = _identity_of(account)
        if identity is None:
            return gw_error(403, ERROR_NAMES[403], ROLE_TEXT)
        broken = _reference_broken(svc)
        if broken is not None:
            return broken
        try:
            result = await svc.link_candidates(identity, link_id)
        except AnalyticsUnavailable as exc:
            return _unavailable(str(exc))
        if result is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        return JSONResponse(result)

    return router
