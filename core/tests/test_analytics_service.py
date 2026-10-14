"""Координатор аналитики КДМ-1 (Task 4; план §Task 4/Этап 4) — `analytics/service.py`.

Task 1 (`knowledge.py`) и Task 2 (`store.py`) уже реализованы — используются как есть (синтетический корпус
`core/tests/fixtures/kdm1_corpus.py`, `20FAKE_*`). Task 3/5 (`preanalysis.py`, `calculations.py`, `validator.py`,
`rendering.py`, `llm_adapter.py`) — ещё заглушки (`NotImplementedError`): здесь они подменяются фейками через
`monkeypatch.setattr(service.<module>, "<func>", ...)`, как велит план («в тестах — подмены/фейки»); проверяется
только координатор (`AnalyticsService`), а не домен Task 3/5.

Пять фокусов проверки плана (`IMPLEMENTATION_PLAN.md`, «Особый фокус проверки») на уровне координатора:
1. Чужая идентичность/история — `test_cross_user_*`.
2. Отзыв права/карточки между поиском и ответом — `test_owner_revoked_between_search_and_publish`,
   `test_validator_rejection_excluded_from_response`.
3. Гонка отправка/отмена/перезапуск, без повторной оплаты — `test_idempotent_submit_same_client_request_id`,
   `test_cancel_before_generating_stops_background_task`, `test_timeout_watchdog_marks_failed_without_crash`,
   `test_cancelled_job_publish_is_noop`.
4. Ложный смысл не проходит — `test_validator_rejection_excluded_from_response`.
5. Неисправный корпус/аналитическая SQLite — `test_open_with_broken_corpus_keeps_state_working`,
   `test_open_with_broken_state_sets_config_error`.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from fixtures.kdm1_corpus import build_corpus

from pcbk_core.analytics import service as service_mod
from pcbk_core.analytics.models import (LlmSelection, EvidenceCard, JobResponse, Preanalysis, RequestIdentity, Section,
                                        ValidationOutcome)
from pcbk_core.analytics.service import AnalyticsService, AnalyticsUnavailable, encode_evidence_id
from pcbk_core.analytics.settings import AnalyticsSettings
from pcbk_core.analytics.store import AnalyticsStore

pytestmark = pytest.mark.anyio

STUDENT_A = RequestIdentity(login="stu-03", role="student", place=3, caller="student-03")
STUDENT_B = RequestIdentity(login="stu-05", role="student", place=5, caller="student-05")
TEACHER = RequestIdentity(login="teach", role="teacher", place=None, caller="teacher")


def _settings(tmp_path, *, corpus_dir=None, timeout_s: float = 30.0,
              reviewers: str = "grants") -> AnalyticsSettings:
    corpus_dir = str(tmp_path / "corpus") if corpus_dir is None else corpus_dir
    return AnalyticsSettings(ANALYTICS_ENABLED=True, ANALYTICS_CORPUS_DIR=corpus_dir,
                             ANALYTICS_STATE_PATH=str(tmp_path / "analytics.db"), ANALYTICS_JOB_TIMEOUT_S=timeout_s,
                             ANALYTICS_REVIEWERS=reviewers)


def _insert_row(corpus_path: Path, table: str, payload: dict) -> None:
    """Вставить строку в синтетический корпус до открытия (правка F4a, раунд 1, контроллер): «плохое» поле
    карточки должно лежать в самом корпусе — `_refresh_pack` перечитывает карточки по id перед проверкой."""
    conn = sqlite3.connect(corpus_path)
    try:
        conn.execute(f"INSERT INTO {table} (id, payload) VALUES (?, ?)", (payload["id"], json.dumps(payload)))
        conn.commit()
    finally:
        conn.close()


def _claim_payload(claim_id: str, *, critical: bool = False, refs: tuple[str, ...] = ()) -> dict:
    return {"id": claim_id, "kind": "definition", "text": f"Текст {claim_id} для теста.", "term_ids": [],
            "evidence_refs": list(refs), "applicability": "KDM1_PCBK, тест",
            "publication_state": "desk_verified_draft", "allowed_render_mode": "attributed_reference",
            "risk_level": "non_operational_reference", "permits_local_setpoint": False,
            "critical_local_fact": critical, "reviewer_login": None}


async def _service(tmp_path, *, timeout_s: float = 30.0, build: "callable | None" = None,
                   reviewers: str = "grants") -> AnalyticsService:
    """`build` (правка F4a) — опциональный хук для вставки/правки строк файла корпуса ДО открытия (как у
    `_service_no_processes` ниже) — нужен тестам, где `_refresh_pack` (`_build_outcome`) теперь перечитывает
    карточки пакета из НАСТОЯЩЕГО корпуса по id: фейковый `EvidenceCard` с намеренно «плохим» полем (например,
    `publication_state`) проходит проверку validator'а только если это поле «плохое» и в самом файле корпуса,
    не только в объекте теста (правка F4a, п.1 — повторная проверка «здесь и сейчас», не по снимку преданализа).
    `reviewers` (F5) — `ANALYTICS_REVIEWERS`, умолчание `"grants"` — прежнее поведение существующих тестов."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    corpus_path = corpus_dir / "fake.seed.sqlite3"
    build_corpus(corpus_path)
    if build is not None:
        build(corpus_path)
    svc = AnalyticsService(_settings(tmp_path, corpus_dir=str(corpus_dir), timeout_s=timeout_s,
                                     reviewers=reviewers))
    await svc.open()
    assert svc.corpus_error is None and svc.config_error is None
    return svc


async def _service_no_processes(tmp_path, *, build: "callable | None" = None) -> AnalyticsService:
    """Правка F1: корпус БЕЗ таблицы `processes` заполненной (`counts={"processes": 0}`) — числа расчётных
    вопросов (200, 4%, 1 т/м³...) иначе совпадают с фикстурными узлами `PROC-FAKE-001..012` через
    `preanalysis._digit_node_process_ids` (полнотекстовый поиск цифрового токена 1-2 знака по виду `processe`,
    см. докстринг `preanalysis.py`) — несколько найденных узлов дают ложную `needs_clarification` по `process`,
    не имеющую отношения к расчёту (артефакт синтетической фикстуры: её узлы названы простыми числами 1..12,
    настоящий корпус заказчика не называет узлы так). `build` — опциональный хук для вставки своих строк в
    файл корпуса ДО открытия (например, `_insert_cov_claim`)."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    corpus_path = corpus_dir / "fake.seed.sqlite3"
    build_corpus(corpus_path, counts={"processes": 0})
    if build is not None:
        build(corpus_path)
    svc = AnalyticsService(_settings(tmp_path, corpus_dir=str(corpus_dir)))
    await svc.open()
    assert svc.corpus_error is None and svc.config_error is None
    return svc


def _preanalysis(decision: str, *, allowed_claim_ids=(), allowed_mechanism_ids=(), missing_fields=(),
                 corpus_version: str = "v1", resolved_terms=(), intent: str = "definition",
                 llm_export_policy: str = "public_only") -> Preanalysis:
    """`corpus_version` (правка F4a) — умолчание `"v1"` остаётся для тестов с пустым пакетом (отсутствие
    карточек само по себе ничего не сверяет с версией); тесты, где пакет непустой и карточки несут настоящие
    `evidence_refs` (после `_refresh_pack` — см. `_service`), передают сюда РЕАЛЬНУЮ версию хендла, иначе
    `validator._card_block_reason` отклонит каждую карточку по несовпадению версии корпуса раньше, чем по тому,
    что тест на самом деле проверяет. `resolved_terms` (дополнение контроллера к правке F4a, H-26) — для тестов
    подписи кандидатов неоднозначного термина (`preanalysis.clarification_candidates`, см. `_build_outcome`).
    `intent`/`llm_export_policy` (итоговая правка F6) — умолчания прежние (`"definition"`/`"public_only"`),
    тесты control-ветки (F6, п.2 брифа) и подстановки политики экспорта (F6, п.1) передают их явно."""
    return Preanalysis(scope="KDM1_PCBK", intent=intent, decision=decision,
                       resolved_terms=list(resolved_terms), process_ids=[], conflict_ids=[],
                       missing_fields=list(missing_fields), risk="reference", corpus_version=corpus_version,
                       evidence_pack_id=None, allowed_claim_ids=list(allowed_claim_ids),
                       allowed_mechanism_ids=list(allowed_mechanism_ids), data_access_ready=False,
                       llm_export_policy=llm_export_policy)


def _card(card_id: str, *, kind: str = "claim", publication_state: str = "desk_verified_draft",
         critical_local_fact: bool = False, evidence_refs: tuple = (), applicability: str = "KDM1_PCBK") -> EvidenceCard:
    return EvidenceCard(id=card_id, kind=kind, text=f"текст {card_id}", risk_level="reference",
                        publication_state=publication_state, allowed_render_mode="attributed_reference",
                       applicability=applicability, critical_local_fact=critical_local_fact,
                       evidence_refs=evidence_refs)


def _section(text: str, kind: str = "public_definition", claim_id: str | None = None,
            evidence_refs: tuple = ()) -> Section:
    return Section(kind=kind, text=text, evidence_refs=list(evidence_refs), rendered_by="server", claim_id=claim_id)


def _fake_render_response(request_id, conversation_id, state, corpus_version, stage_label, outcome, sections):
    return JobResponse(request_id=request_id, conversation_id=conversation_id, state=state,
                       corpus_version=corpus_version, stage_label=stage_label, sections=list(sections),
                       clarification_keys=list(outcome.clarification_keys), error_code=outcome.error_code)


async def _ask(svc: AnalyticsService, identity: RequestIdentity, conversation_id: str, question: str = "вопрос",
              client_request_id: str = "cr-1"):
    from pcbk_core.analytics.models import AnalysisRequest
    request = AnalysisRequest(client_request_id=client_request_id, question=question, mode="reference")
    return await svc.submit_question(identity, conversation_id, request)


async def _wait_terminal(svc: AnalyticsService, identity: RequestIdentity, request_id: str,
                         timeout: float = 2.0) -> JobResponse:
    from pcbk_core.analytics.models import JOB_TERMINAL_STATES
    end = asyncio.get_event_loop().time() + timeout
    while True:
        resp = await svc.get_request(identity, request_id)
        assert resp is not None
        if resp.state in JOB_TERMINAL_STATES:
            return resp
        if asyncio.get_event_loop().time() > end:
            raise AssertionError(f"задание не завершилось за {timeout} с: {resp.state}")
        await asyncio.sleep(0.01)


# --- телеметрия БДРВ в ответе (план Д17, Task 1): двойник DataService свой (фиксирует вызовы, отдаёт заданные
# ответы) — не настоящий DataService/FakeHistorian (helpers.make_service), которые проверяет отдельно
# test_analytics_data_bridge.py; здесь проверяется только то, как service.py использует результат ---

class _FakeCalcRole:
    def __init__(self, config_error: str | None = None):
        self.config_error = config_error


class _FakeCalc:
    """Двойник `DataService` для тестов телеметрии: фиксирует вызовы `self.calls`, отдаёт заранее заданные
    ответы `tag_now`/`tag_period`/`catalog_search` (не считает ничего сам)."""

    def __init__(self, *, now_reply: dict | None = None, period_reply: dict | None = None,
                config_error: str | None = None):
        self.role = _FakeCalcRole(config_error)
        self.calls: list[tuple] = []
        self._now_reply = now_reply
        self._period_reply = period_reply

    async def tag_now(self, caller, channel, tags):
        self.calls.append(("tag_now", caller, channel, tuple(tags)))
        return self._now_reply

    async def tag_period(self, caller, channel, tags, period, start, end):
        self.calls.append(("tag_period", caller, channel, tuple(tags), period, start, end))
        return self._period_reply

    async def catalog_search(self, caller, channel, query, limit=10):
        self.calls.append(("catalog_search", caller, channel, query, limit))
        return {"status": "ok", "matches": []}


def _now_reply(*, value: float = 12.5, unit: str = "л/час", tag: str = "20FAKE_001_PV", status: str = "ok",
              quality: str = "good", time_iso: str = "2026-10-14T08:00:00+00:00", tz: str = "Europe/Moscow",
              item_notes: tuple = (), text: str = "нет значения", state_label: str | None = None) -> dict:
    """Ответ `DataService.tag_now` ровно формы `core/pcbk_core/data/service.py::_tag_now`/`_now_item`."""
    item: dict = {"tag": tag, "status": status, "description": "расход", "unit": unit, "kind": "analog",
                 "value": value if status == "ok" else None, "time": time_iso, "age_s": 5,
                 "quality": quality if status == "ok" else None, "item_notes": list(item_notes)}
    if status != "ok":
        item["text"] = text
    if state_label is not None:
        item["state_label"] = state_label
    return {"tool": "tag_now", "status": "ok" if status == "ok" else "partial", "notes": [], "cache": "none",
           "asof": time_iso, "tz": tz, "source": "live", "items": [item]}


def _period_reply(*, avg: float = 12.4, low: float = 10.1, high: float = 15.0, percent_good: float = 99.5,
                  unit: str = "л/час", tag: str = "20FAKE_001_PV", preset: str | None = "prev_shift",
                  start: str = "2026-10-14T00:00:00+00:00", end: str = "2026-10-14T08:00:00+00:00",
                  tz: str = "Europe/Moscow", notes: tuple = ()) -> dict:
    """Ответ `DataService.tag_period` ровно формы `_tag_period`/`_period_item`."""
    item = {"tag": tag, "status": "ok", "description": "расход", "unit": unit, "avg": avg, "min": low,
           "max": high, "range": high - low, "std": 0.3, "percent_good": percent_good}
    return {"tool": "tag_period", "status": "ok", "notes": list(notes), "cache": "none",
           "period": {"start": start, "end": end, "tz": tz, "preset": preset, "shift_grid_assumed": False,
                      "clamped": False, "open": False},
           "source": "summary", "avg_kind": {"code": "time_weighted", "text": "по времени"},
           "std_kind": {"code": "time_weighted", "text": "по времени"}, "cost_tag_days": 0.1, "items": [item]}


def _approve_equipment_link(svc: AnalyticsService, *, link_id: str, tag: str, scope: str = "PROC-FAKE-001",
                            author: RequestIdentity = STUDENT_A, decider: RequestIdentity | None = None,
                            reviewers: str = "all_students"):
    """Заявка `kind="equipment_link"` на `link_id` → сразу согласована: в режиме `all_students` любой студент
    (по умолчанию `STUDENT_B`) решает без назначения полномочия; в `"grants"` вызывающий передаёт `decider` с
    уже назначенным полномочием (`svc.reviews.bootstrap_authority`/`grant_authority` до вызова этой функции).
    Простой синхронный помощник теста — те же методы `reviews.ReviewStore`, что использует `service.py`."""
    pending = svc.reviews.create_request("equipment_link", link_id, tag, scope, "видел подпись на снимке",
                                         created_by=author)
    decider = decider or (STUDENT_B if author is not STUDENT_B else STUDENT_A)
    return svc.reviews.decide(pending.id, "accepted", "", decided_by=decider, reviewers_mode=reviewers)


# --- базовые open()/capabilities() ---

async def test_open_opens_corpus_and_state(tmp_path):
    svc = await _service(tmp_path)
    caps = svc.capabilities(STUDENT_A)
    assert caps.reference_available is True
    assert caps.corpus_version is not None
    await svc.aclose()


async def test_capabilities_llm_none_is_unavailable(tmp_path):
    svc = await _service(tmp_path)
    assert svc.capabilities(STUDENT_A).llm_available is False
    assert svc.capabilities(TEACHER).llm_available is False
    await svc.aclose()


async def test_capabilities_teacher_without_explicit_payer_stays_false_even_with_llm(tmp_path):
    """Мастер-промпт §6: без явно настроенного плательщика преподавателя платный разбор недоступен — честное
    умолчание, даже если role.llm формально «здоров» (нет механизма явного плательщика, см. отчёт)."""
    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()
    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    assert svc.capabilities(STUDENT_A).llm_available is True
    assert svc.capabilities(TEACHER).llm_available is False
    await svc.aclose()


async def test_teacher_payer_configured_enables_llm_and_reaches_adapter_with_teacher_caller(tmp_path, monkeypatch):
    """Хвост Task 5 (ANALYTICS_TEACHER_PAYER) + Task 7, шаг 2 (уточнение ревью Task 5): capabilities() и
    настоящий вызов `llm_adapter.request_selection` смотрят на тот же флаг, не расходятся — без плательщика
    `llm_available=False` И вызов до двойника не доходит; с настроенным — `True` И вызов доходит ровно с
    `identity.caller == "teacher"`."""
    from dataclasses import replace
    calls: list[tuple[str, str]] = []
    card = _card("CLAIM-FAKE-001")

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,)), (card,)

    def fake_select_model(role, models):
        return object()

    def fake_build_raw_body(analysis, pack, *, schema, question=""):
        return {}

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        # повторяет ровно тот же первый gate, что настоящий llm_adapter.request_selection (докстринг модуля):
        # преподаватель без настроенного плательщика — отказ раньше журнала/бюджета/сети, до двойника
        if identity.role == "teacher" and not teacher_payer.strip():
            raise service_mod.llm_adapter.LlmAdapterRefusal("teacher_payer")
        calls.append((identity.caller, teacher_payer))
        return object()

    def fake_validate_selection(selection, analysis, pack, approved_card_ids=frozenset()):
        return ValidationOutcome(accepted_claim_ids=(card.id,), accepted_mechanism_ids=(), rejected=(),
                                 clarification_keys=(), refused=False)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", fake_select_model)
    monkeypatch.setattr(service_mod.llm_adapter, "build_raw_body", fake_build_raw_body)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.validator, "validate_selection", fake_validate_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    assert svc.capabilities(TEACHER).llm_available is False
    conv = await svc.create_conversation(TEACHER, "reference")
    request_id = await _ask(svc, TEACHER, conv)
    response = await _wait_terminal(svc, TEACHER, request_id)
    assert calls == []                               # без плательщика двойника не вызывали вовсе
    assert response.state == "completed"             # откат на детерминированный путь, не падение задания
    assert {s.claim_id for s in response.sections} == {card.id}

    svc.settings = replace(svc.settings, ANALYTICS_TEACHER_PAYER="ops")
    assert svc.capabilities(TEACHER).llm_available is True
    conv2 = await svc.create_conversation(TEACHER, "reference")
    request_id2 = await _ask(svc, TEACHER, conv2, client_request_id="cr-2")
    response2 = await _wait_terminal(svc, TEACHER, request_id2)
    assert calls == [("teacher", "ops")]              # с плательщиком — дошло, caller=teacher, payer передан как есть
    assert response2.state == "completed"
    await svc.aclose()


# --- Особый фокус №5: неисправный корпус/состояние ---

async def test_open_with_broken_corpus_keeps_state_working(tmp_path):
    svc = AnalyticsService(_settings(tmp_path, corpus_dir=str(tmp_path / "nope")))
    await svc.open()
    assert svc.corpus_error is not None
    assert svc.config_error is None
    caps = svc.capabilities(STUDENT_A)
    assert caps.reference_available is False
    assert caps.corpus_version is None
    with pytest.raises(AnalyticsUnavailable):
        await svc.create_conversation(STUDENT_A, "reference")
    await svc.aclose()


async def test_open_with_broken_state_sets_config_error(tmp_path):
    bad_state_dir = tmp_path / "no_such_dir" / "analytics.db"   # каталог не существует — SQLite не создаст файл
    svc = AnalyticsService(AnalyticsSettings(ANALYTICS_ENABLED=True, ANALYTICS_CORPUS_DIR=str(tmp_path / "c"),
                                             ANALYTICS_STATE_PATH=str(bad_state_dir)))
    (tmp_path / "c").mkdir()
    build_corpus(tmp_path / "c" / "fake.seed.sqlite3")
    await svc.open()
    assert svc.config_error is not None
    with pytest.raises(AnalyticsUnavailable):
        await svc.create_conversation(STUDENT_A, "reference")
    await svc.aclose()


# --- Особый фокус №1: чужая идентичность/история ---

async def test_cross_user_conversation_not_visible(tmp_path):
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    assert await svc.get_conversation(STUDENT_B, conv) is None
    assert conv not in [m.id for m in await svc.list_conversations(STUDENT_B)]
    assert any(m.id == conv for m in await svc.list_conversations(STUDENT_A))
    await svc.aclose()


async def test_cross_user_request_and_evidence_not_visible(tmp_path, monkeypatch):
    svc = await _service(tmp_path)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", lambda *a, **k: (_preanalysis("limited_answer"), ()))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    await _wait_terminal(svc, STUDENT_A, request_id)
    assert await svc.get_request(STUDENT_B, request_id) is None
    evidence_id = encode_evidence_id(request_id, "anypack", "anyitem")
    assert await svc.get_evidence(STUDENT_B, evidence_id) is None
    await svc.aclose()


# --- Особый фокус №3: гонка отправка/отмена/перезапуск ---

async def test_idempotent_submit_same_client_request_id(tmp_path, monkeypatch):
    calls = []

    def fake_analyze(identity, request, handle, corpus_version):
        calls.append(1)
        return _preanalysis("limited_answer"), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    r1 = await _ask(svc, STUDENT_A, conv, client_request_id="cr-dup")
    r2 = await _ask(svc, STUDENT_A, conv, client_request_id="cr-dup")
    assert r1 == r2
    await _wait_terminal(svc, STUDENT_A, r1)
    assert len(calls) == 1          # второй POST не запустил второй фоновый проход — не вызвал preanalysis снова
    await svc.aclose()


async def test_cancel_before_generating_stops_background_task(tmp_path, monkeypatch):
    import threading
    started = threading.Event()
    blocked = threading.Event()

    def sync_slow(identity, request, handle, corpus_version):
        # analyze() вызывается через asyncio.to_thread — настоящий поток ОС, блокировать его событием threading,
        # не asyncio (тот ждал бы в чужом цикле и завис бы)
        started.set()
        if not blocked.wait(timeout=5):
            raise AssertionError("test did not release blocked in time")
        return _preanalysis("limited_answer"), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", sync_slow)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    await asyncio.to_thread(started.wait, 2.0)
    cancelled = await svc.cancel_request(STUDENT_A, request_id)
    assert cancelled is True
    blocked.set()                   # отпустить фейковый preanalysis — задание уже отменено на уровне store
    await asyncio.sleep(0.2)
    response = await svc.get_request(STUDENT_A, request_id)
    assert response.state == "cancelled"
    await svc.aclose()


async def test_timeout_watchdog_marks_failed_without_crash(tmp_path, monkeypatch):
    import threading

    def sync_never(identity, request, handle, corpus_version):
        threading.Event().wait(timeout=2.0)       # дольше ANALYTICS_JOB_TIMEOUT_S ниже, без вечного сна потока
        return _preanalysis("limited_answer"), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", sync_never)
    svc = await _service(tmp_path, timeout_s=0.2)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id, timeout=3.0)
    assert response.state == "failed"
    assert response.error_code == "unavailable"
    await svc.aclose()


async def test_cancelled_job_publish_is_noop(tmp_path):
    """`store.publish_response` после отмены не перезаписывает терминальную стадию (ОСОБЫЙ ФОКУС ПРОВЕРКИ №3) —
    прямая проверка контракта store.py, которым опирается координатор."""
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, client_request_id="cr-cancel")
    assert await svc.cancel_request(STUDENT_A, request_id) is True
    late = _fake_render_response(request_id, conv, "completed", "v1", "поздно", ValidationOutcome((), (), (), (),
                                 False), ())
    published = await asyncio.to_thread(svc.store.publish_response, request_id, late)
    assert published is False
    response = await svc.get_request(STUDENT_A, request_id)
    assert response.state == "cancelled"
    await svc.aclose()


# --- Особый фокус №2/№4: отзыв права/карточки между поиском и ответом, ложный смысл ---

async def test_owner_revoked_between_search_and_publish(tmp_path, monkeypatch):
    """Диалог «исчезает» (чужой владелец) между преданализом и публикацией — ответ не публикуется."""
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")

    def fake_analyze(identity, request, handle, corpus_version):
        # между поиском и ответом владение диалогом сменилось (имитация отзыва права на диалог) —
        # прямая правка строки SQLite мимо store.py, как было бы при параллельной операции другого пути
        import sqlite3
        with sqlite3.connect(svc.settings.ANALYTICS_STATE_PATH) as db:
            db.execute("UPDATE conversations SET owner_login = 'someone-else' WHERE id = ?", (conv,))
            db.commit()
        return _preanalysis("limited_answer"), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    request_id = await _ask(svc, STUDENT_A, conv)
    await asyncio.sleep(0.3)
    record = svc.store.get_request(request_id)
    assert record.response is None          # не опубликовано — владение диалогом изменилось между поиском и ответом
    await svc.aclose()


async def test_validator_rejection_excluded_from_response(tmp_path, monkeypatch):
    """Ложный смысл блокируется независимо от присутствия ссылки в пакете: фейковый validator отклоняет
    карточку, выбранную «моделью» — итоговый ответ её не показывает (ОСОБЫЙ ФОКУС ПРОВЕРКИ №4)."""
    card_ok, card_bad = _card("CLAIM-FAKE-001"), _card("CLAIM-FAKE-002")

    def fake_analyze(identity, request, handle, corpus_version):
        pre = _preanalysis("ready", allowed_claim_ids=(card_ok.id, card_bad.id))
        return pre, (card_ok, card_bad)

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def fake_select_model(role, models):
        return object()

    def fake_build_raw_body(analysis, pack, *, schema, question=""):
        return {}

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        return object()          # содержимое неважно — validate_selection ниже целиком фейковый

    def fake_validate_selection(selection, analysis, pack, approved_card_ids=frozenset()):
        # «модель» предложила обе карточки, сервер отклонил card_bad по смысловой проверке
        return ValidationOutcome(accepted_claim_ids=(card_ok.id,), accepted_mechanism_ids=(),
                                 rejected=((card_bad.id, "ложный смысл"),), clarification_keys=(), refused=False)

    def fake_render_claim_section(card):
        return _section(card.text, claim_id=card.id)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", fake_select_model)
    monkeypatch.setattr(service_mod.llm_adapter, "build_raw_body", fake_build_raw_body)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.validator, "validate_selection", fake_validate_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", fake_render_claim_section)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "completed"
    shown_claims = {s.claim_id for s in response.sections}
    assert card_ok.id in shown_claims
    assert card_bad.id not in shown_claims
    await svc.aclose()


# --- Правка, раунд 1, Critical: путь без LLM и путь «LLM упал» — тоже через validator.validate_selection,
# не raw allowed_claim_ids/allowed_mechanism_ids напрямую ---

async def test_deterministic_path_rechecks_publication_state_via_real_validator(tmp_path, monkeypatch):
    """Путь без LLM (self.llm is None, Task 4 по умолчанию) не публикует карточку с неразрешённым
    publication_state («draft_untested» не входит в validator.RENDERABLE_PUBLICATION_STATES) — проверяет
    НАСТОЯЩИЙ `validator.validate_selection`, не фейк.

    Правка F4a: `_build_outcome` теперь перечитывает пакет из НАСТОЯЩЕГО корпуса по id (`_refresh_pack`) перед
    этой проверкой — «плохое» поле нужно поставить в САМ файл корпуса (`_mark_claim_002_untested`), фейковый
    `EvidenceCard.publication_state` ниже используется только как исходный id/kind, остальные его поля
    `_refresh_pack` перезатирает настоящими."""
    def _mark_claim_002_untested(corpus_path: Path) -> None:
        conn = sqlite3.connect(corpus_path)
        try:
            conn.execute("UPDATE claims SET payload = json_set(payload, '$.publication_state', "
                         "'draft_untested') WHERE id = 'CLAIM-FAKE-002'")
            conn.commit()
        finally:
            conn.close()
    card_ok = _card("CLAIM-FAKE-001")
    card_bad = _card("CLAIM-FAKE-002", publication_state="draft_untested")

    def fake_analyze(identity, request, handle, corpus_version):
        pre = _preanalysis("ready", allowed_claim_ids=(card_ok.id, card_bad.id), corpus_version=corpus_version)
        return pre, (card_ok, card_bad)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path, build=_mark_claim_002_untested)
    assert svc.llm is None
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "completed"
    shown = {s.claim_id for s in response.sections}
    assert card_ok.id in shown
    assert card_bad.id not in shown
    await svc.aclose()


async def test_deterministic_path_blocks_critical_local_fact_without_approval(tmp_path, monkeypatch):
    """Местный критический факт без согласования не проходит даже с разрешённым publication_state — и, раз
    ничего не осталось, ответ `limited_answer`, не пустой `completed` (Правка, раунд 1, Critical). Правка F4a,
    раунд 1 (контроллер): карточка лежит в самом корпусе, версия преанализа — действующая, поэтому блокирует
    именно отсутствие согласования, а не несовпадение версии."""
    card = _card("CLAIM-FAKE-701", critical_local_fact=True)

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path, build=lambda p: _insert_row(p, "claims", _claim_payload(card.id, critical=True)))
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "limited_answer"
    assert response.sections == []
    await svc.aclose()


async def test_real_run_job_shows_critical_local_fact_after_reviews_approval_and_hides_after_revoke(
        tmp_path, monkeypatch):
    """Task 7, шаг 2 (стык с validator.py): критический местный факт без `reviewer_approved` в самой карточке
    корпуса, но с ДЕЙСТВУЮЩИМ согласованием через настоящий `reviews.ReviewStore.approved_claim` — проходит
    настоящий `validator.validate_selection` (не фейк) на пути без LLM. Отзыв полномочия решившего сразу
    убирает карточку из СЛЕДУЮЩЕГО ответа (пройдена заново «здесь и сейчас», не по факту прошлого решения).

    Правка F4a: `_build_outcome` перечитывает пакет из НАСТОЯЩЕГО корпуса по id (`_refresh_pack`) — id
    «CLAIM-FAKE-777» в фикстуре по умолчанию не существует, поэтому своя карточка вставляется В ФАЙЛ корпуса
    (`_insert_critical_claim`, приём как `_insert_cov_claim` ниже в файле), не только в объект теста."""
    def _insert_critical_claim(corpus_path: Path) -> None:
        conn = sqlite3.connect(corpus_path)
        try:
            payload = {
                "id": "CLAIM-FAKE-777", "kind": "definition", "text": "Критический местный факт для теста.",
                "term_ids": [], "evidence_refs": [], "applicability": "KDM1_PCBK, тест",
                "publication_state": "desk_verified_draft", "allowed_render_mode": "attributed_reference",
                "risk_level": "non_operational_reference", "permits_local_setpoint": False,
                "critical_local_fact": True, "reviewer_login": None,
            }
            conn.execute("INSERT INTO claims (id, payload) VALUES (?, ?)", ("CLAIM-FAKE-777", json.dumps(payload)))
            conn.commit()
        finally:
            conn.close()
    card = _card("CLAIM-FAKE-777", critical_local_fact=True, publication_state="desk_verified_draft")

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path, build=_insert_critical_claim)
    assert svc.llm is None                  # путь без LLM — ровно тот, что проверяется здесь
    tech1 = RequestIdentity(login="tech1", role="teacher", place=None, caller="teacher")
    svc.reviews.bootstrap_authority("tech1", "KDM1_PCBK", ("local_claim",), operator="operator")
    pending = svc.reviews.create_request("local_claim", card.id, "критический факт подтверждён", "KDM1_PCBK",
                                         "подтверждено по паспорту узла", created_by=STUDENT_B)
    svc.reviews.decide(pending.id, "accepted", "", decided_by=tech1)

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "completed"
    assert {s.claim_id for s in response.sections} == {card.id}

    svc.reviews.revoke_authority("tech1", "KDM1_PCBK", revoked_by=tech1)
    conv2 = await svc.create_conversation(STUDENT_A, "reference")
    request_id2 = await _ask(svc, STUDENT_A, conv2, client_request_id="cr-2")
    response2 = await _wait_terminal(svc, STUDENT_A, request_id2)
    assert response2.state == "limited_answer"
    assert response2.sections == []
    await svc.aclose()


# --- F5 (итоговое ревью, 14.10.2026, решение владельца): ANALYTICS_REVIEWERS=all_students ---

async def test_capabilities_review_authority_true_for_student_in_all_students_mode_not_for_teacher(tmp_path):
    """`capabilities().review_authority` — настроечный факт в режиме `all_students` (п.4 F5-brief): любой
    `student` держит право без назначения; `teacher` остаётся исключением — только по действующему
    полномочию, как в `grants` (мастер-промпт §6 это решение владельца не отменяет)."""
    svc = await _service(tmp_path, reviewers="all_students")
    assert svc.capabilities(STUDENT_A).review_authority is True
    assert svc.capabilities(TEACHER).review_authority is False
    await svc.aclose()


async def test_capabilities_review_authority_false_for_student_without_grant_in_grants_mode(tmp_path):
    svc = await _service(tmp_path)                                    # ANALYTICS_REVIEWERS=grants (умолчание)
    assert svc.capabilities(STUDENT_A).review_authority is False
    await svc.aclose()


async def test_real_run_job_shows_critical_local_fact_approved_by_student_without_grant_then_hides_after_mode_switch(
        tmp_path, monkeypatch):
    """F5, стык с `validator.py` (как `test_real_run_job_shows_critical_local_fact_after_reviews_approval_and_
    hides_after_revoke` выше, но решающий — `student` БЕЗ какого-либо назначенного полномочия, доступно только
    через `ANALYTICS_REVIEWERS=all_students`): проходит настоящий `validator.validate_selection` через
    `approved_claim(reviewers_mode="all_students")`. Переключение режима на `grants` (решение владельца,
    п.3 F5-brief: «отзыв режима... решения студентов без назначения перестают действовать для нового
    применения») убирает карточку из СЛЕДУЮЩЕГО ответа — журнал заявки цел (`status` остаётся `accepted`),
    тот же приём, что у отзыва полномочия/истечения `valid_until`."""
    def _insert_critical_claim(corpus_path: Path) -> None:
        conn = sqlite3.connect(corpus_path)
        try:
            payload = {
                "id": "CLAIM-FAKE-778", "kind": "definition", "text": "Критический местный факт для теста F5.",
                "term_ids": [], "evidence_refs": [], "applicability": "KDM1_PCBK, тест",
                "publication_state": "desk_verified_draft", "allowed_render_mode": "attributed_reference",
                "risk_level": "non_operational_reference", "permits_local_setpoint": False,
                "critical_local_fact": True, "reviewer_login": None,
            }
            conn.execute("INSERT INTO claims (id, payload) VALUES (?, ?)", ("CLAIM-FAKE-778", json.dumps(payload)))
            conn.commit()
        finally:
            conn.close()
    card = _card("CLAIM-FAKE-778", critical_local_fact=True, publication_state="desk_verified_draft")

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path, build=_insert_critical_claim, reviewers="all_students")
    assert svc.llm is None                  # путь без LLM — ровно тот, что проверяется здесь
    # STUDENT_B решает заявку STUDENT_A БЕЗ какого-либо назначенного полномочия — доступно только из-за
    # ANALYTICS_REVIEWERS=all_students (reviews.ReviewStore.bootstrap_authority/grant_authority не вызваны)
    pending = svc.reviews.create_request("local_claim", card.id, "критический факт подтверждён", "KDM1_PCBK",
                                         "подтверждено технологом", created_by=STUDENT_A)
    decided = await svc.decide_review_request(STUDENT_B, pending.id, "accepted", "подтверждено")
    assert decided.status == "accepted"

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "completed"
    assert {s.claim_id for s in response.sections} == {card.id}

    from dataclasses import replace
    svc.settings = replace(svc.settings, ANALYTICS_REVIEWERS="grants")   # отзыв режима — переключение на grants
    conv2 = await svc.create_conversation(STUDENT_A, "reference")
    request_id2 = await _ask(svc, STUDENT_A, conv2, client_request_id="cr-2")
    response2 = await _wait_terminal(svc, STUDENT_A, request_id2)
    assert response2.state == "limited_answer"           # решение студента без назначения больше не применяется
    assert response2.sections == []
    await svc.aclose()


async def test_deterministic_path_blocks_restricted_source(tmp_path, monkeypatch):
    """PUB-10/11 (validator.RESTRICTED_SOURCE_IDS) блокируется путём без LLM. Правка F4a, раунд 1 (контроллер):
    ссылка на ограниченный источник — в самом корпусе (фрагмент источника PUB-10), версия преанализа —
    действующая. Сценарий «ссылка устаревшей версии» после правки F4a закрывается сверкой версии корпуса перед
    публикацией — см. тесты смены версии ниже в файле."""
    def build(corpus_path: Path) -> None:
        _insert_row(corpus_path, "fragments", {
            "id": "FRG-PUB10", "source_id": "PUB-10", "locator": {"type": "pdf_page", "page": 1},
            "text": "Фрагмент источника с ограничением использования.", "text_sha256": "pub10",
            "scope": "KDM1_document_model", "publication_state": "extracted", "contains_instructions": False,
            "current_telemetry": False, "interpretation": "demo"})
        _insert_row(corpus_path, "claims", _claim_payload("CLAIM-FAKE-704", refs=("FRG-PUB10",)))
    card = _card("CLAIM-FAKE-704")

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path, build=build)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "limited_answer"
    assert response.sections == []
    await svc.aclose()


async def test_llm_path_revoked_approval_during_paid_call_is_not_published(tmp_path, monkeypatch):
    """Правка F4a, раунд 1 (контроллер; особый фокус №2): критический местный факт согласован до вызова модели,
    полномочие решившего отзывают, ПОКА модель отвечает. Ответ модели выбирает эту карточку — но перед
    публикацией пакет и согласования перечитываются заново, и отозванный факт в ответ не попадает."""
    card = _card("CLAIM-FAKE-777", critical_local_fact=True)
    tech1 = RequestIdentity(login="tech1", role="teacher", place=None, caller="teacher")

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        svc.reviews.revoke_authority("tech1", "KDM1_PCBK", revoked_by=tech1)       # отзыв во время вызова
        return LlmSelection(reference_claim_ids=[card.id], mechanism_ids=[], clarification_keys=[],
                            calculation_ids=[])
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", lambda role, models: object())
    monkeypatch.setattr(service_mod.llm_adapter, "build_raw_body",
                       lambda analysis, pack, *, schema, question="": {})
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path, build=lambda p: _insert_row(p, "claims", _claim_payload(card.id, critical=True)))
    svc.llm = FakeLlm()
    svc.reviews.bootstrap_authority("tech1", "KDM1_PCBK", ("local_claim",), operator="operator")
    pending = svc.reviews.create_request("local_claim", card.id, "факт подтверждён", "KDM1_PCBK",
                                         "подтверждено по паспорту узла", created_by=STUDENT_B)
    svc.reviews.decide(pending.id, "accepted", "", decided_by=tech1)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert card.id not in {s.claim_id for s in response.sections}
    assert response.state == "limited_answer"
    await svc.aclose()


async def test_llm_failure_path_also_rechecks_via_validator(tmp_path, monkeypatch):
    """«LLM недоступен сейчас» (_select_with_llm → None, здесь — отказ select_model) откатывается на тот же
    детерминированный путь через настоящий validator, не на сырой allowed_claim_ids (Правка, раунд 1,
    Critical) — llm сконфигурирован, запрос к нему падает.

    Правка F4a: «плохое» поле — в самом файле корпуса (`_mark_claim_002_untested`, см. тот же приём у
    `test_deterministic_path_rechecks_publication_state_via_real_validator` выше) — `_refresh_pack` перечитывает
    карточки по id перед проверкой, фейковые поля `_card()` на этом пути сами по себе больше не достаточны."""
    def _mark_claim_002_untested(corpus_path: Path) -> None:
        conn = sqlite3.connect(corpus_path)
        try:
            conn.execute("UPDATE claims SET payload = json_set(payload, '$.publication_state', "
                         "'draft_untested') WHERE id = 'CLAIM-FAKE-002'")
            conn.commit()
        finally:
            conn.close()
    card_ok = _card("CLAIM-FAKE-001")
    card_bad = _card("CLAIM-FAKE-002", publication_state="draft_untested")

    def fake_analyze(identity, request, handle, corpus_version):
        pre = _preanalysis("ready", allowed_claim_ids=(card_ok.id, card_bad.id), corpus_version=corpus_version)
        return pre, (card_ok, card_bad)

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def boom_select_model(role, models):
        raise service_mod.llm_adapter.LlmAdapterRefusal("model")
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", boom_select_model)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path, build=_mark_claim_002_untested)
    svc.llm = FakeLlm()
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "completed"
    shown = {s.claim_id for s in response.sections}
    assert card_ok.id in shown
    assert card_bad.id not in shown
    await svc.aclose()


# --- решения преданализа без LLM: needs_clarification/refused/ready-без-LLM ---

async def test_needs_clarification_renders_clarification_section(tmp_path, monkeypatch):
    """Правка F4a (хвост F4b, H-26): `_build_outcome` всегда передаёт `candidate_labels=` (пусто здесь — фейк
    `analyze` не заполняет `resolved_terms`) — фейк рендеринга принимает этот необязательный параметр, как
    настоящий `rendering.render_clarification_section`."""
    monkeypatch.setattr(service_mod.preanalysis, "analyze",
                        lambda *a, **k: (_preanalysis("needs_clarification", missing_fields=("unit",)), ()))
    monkeypatch.setattr(service_mod.rendering, "render_clarification_section",
                        lambda keys, candidate_labels=(): _section("уточните", kind="clarification"))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "needs_clarification"
    assert response.sections[0].kind == "clarification"
    await svc.aclose()


async def test_refused_decision_terminal_failed_with_policy_code(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod.preanalysis, "analyze", lambda *a, **k: (_preanalysis("refused"), ()))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "failed"
    assert response.error_code == "policy_refused"
    await svc.aclose()


async def test_ready_without_llm_renders_full_allowed_pack(tmp_path, monkeypatch):
    """Правка F4a: `corpus_version=corpus_version` — `_refresh_pack` перечитывает CLAIM-FAKE-001 из настоящего
    корпуса, чьи настоящие `evidence_refs` несут настоящую версию, не фейковое умолчание `_preanalysis`."""
    card = _card("CLAIM-FAKE-001")

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section",
                        lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    assert svc.llm is None                  # Task 4: по умолчанию выключена
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "completed"
    assert response.sections[0].claim_id == card.id
    await svc.aclose()


async def test_run_job_failure_becomes_failed_not_crash(tmp_path, monkeypatch):
    def boom(identity, request, handle, corpus_version):
        raise NotImplementedError("Task 3")  # как настоящий текущий стаб preanalysis.analyze
    monkeypatch.setattr(service_mod.preanalysis, "analyze", boom)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "failed"
    await svc.aclose()


# --- Task 7, шаг 2: list_review_requests/create_review_request/decide_review_request — тонкий проброс ---

async def test_review_request_service_methods_wrap_reviews_store(tmp_path):
    svc = await _service(tmp_path)
    created = await svc.create_review_request(STUDENT_A, "conflict", "CONF-FAKE-030", "значение", "KDM1_PCBK",
                                               "основание")
    assert created.status == "pending" and created.created_by == STUDENT_A.login
    assert [r.id for r in await svc.list_review_requests(STUDENT_A)] == [created.id]
    assert await svc.list_review_requests(STUDENT_B) == []       # посторонний без права не видит чужую заявку

    svc.reviews.bootstrap_authority(STUDENT_B.login, "KDM1_PCBK", ("conflict",), operator="operator")
    decided = await svc.decide_review_request(STUDENT_B, created.id, "accepted", "подтверждено")
    assert decided.status == "accepted" and decided.decided_by == STUDENT_B.login
    await svc.aclose()


# --- история диалога (GET conversations/{id}) и evidence ---

async def test_list_requests_returns_history_in_order(tmp_path, monkeypatch):
    """GET conversations/{id} — `ConversationTurn(question, response)` на каждое задание (Правка, раунд 1,
    Minor «д»): владелец видит и исходный текст своего вопроса, не только ответ."""
    monkeypatch.setattr(service_mod.preanalysis, "analyze", lambda *a, **k: (_preanalysis("limited_answer"), ()))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    r1 = await _ask(svc, STUDENT_A, conv, client_request_id="cr-1", question="первый вопрос")
    await _wait_terminal(svc, STUDENT_A, r1)
    r2 = await _ask(svc, STUDENT_A, conv, client_request_id="cr-2", question="второй вопрос")
    await _wait_terminal(svc, STUDENT_A, r2)
    history = await svc.list_requests(STUDENT_A, conv)
    assert [t.response.request_id for t in history] == [r1, r2]
    assert [t.question for t in history] == ["первый вопрос", "второй вопрос"]
    assert await svc.list_requests(STUDENT_B, conv) is None
    await svc.aclose()


async def test_evidence_roundtrip_and_malformed_id(tmp_path):
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    pack_id = await asyncio.to_thread(svc.store.save_evidence_pack, request_id,
                                      (("item-1", {"id": "item-1", "source_id": "SRC-FAKE-001", "text": "т",
                                                   "locator": {"type": "pdf_page", "page": 1},
                                                   "corpus_version": "v1", "limitation": ""}),))
    evidence_id = encode_evidence_id(request_id, pack_id, "item-1")
    item = await svc.get_evidence(STUDENT_A, evidence_id)
    assert item is not None and item["id"] == "item-1"
    assert await svc.get_evidence(STUDENT_A, "not-a-valid-id") is None
    assert await svc.get_evidence(STUDENT_A, "a:b") is None       # ровно 3 части, не 2
    await svc.aclose()


async def test_evidence_pack_owner_rejects_pack_from_another_request(tmp_path):
    """Правка, раунд 1, Important 3: свой `request_id`, но ЧУЖОЙ `pack_id` (пакет другого задания того же
    владельца) — 404, не 200 (store.evidence_pack_owner сверяет пакет→задание, не только задание→владелец)."""
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_a = await _ask(svc, STUDENT_A, conv, client_request_id="cr-a")
    request_b = await _ask(svc, STUDENT_A, conv, client_request_id="cr-b")
    pack_a = await asyncio.to_thread(svc.store.save_evidence_pack, request_a,
                                     (("item-1", {"id": "item-1", "source_id": "SRC-FAKE-001", "text": "a",
                                                  "locator": {"type": "pdf_page", "page": 1},
                                                  "corpus_version": "v1", "limitation": ""}),))
    # свой request_b + чужой (request_a) pack_id
    assert await svc.get_evidence(STUDENT_A, encode_evidence_id(request_b, pack_a, "item-1")) is None
    # свой request_a + свой pack_a — работает как обычно
    assert (await svc.get_evidence(STUDENT_A, encode_evidence_id(request_a, pack_a, "item-1")))["id"] == "item-1"
    await svc.aclose()


async def test_real_run_job_persists_evidence_pack_and_rewrites_fragment_id(tmp_path, monkeypatch):
    """Правка, раунд 1, Important 2: настоящий `_run_job` (не прямой `save_evidence_pack`) сохраняет пакет
    доказательств и переписывает `fragment_id` секций в составной `evidence_id` — GET evidence/{id} из ОТВЕТА
    находит сохранённый фрагмент; чужой пользователь — None.

    Правка F4a: `evidence_refs=(ref,)` ниже теперь не доходит до ответа как есть — `_refresh_pack` перечитывает
    CLAIM-FAKE-001 из настоящего корпуса и строит НАСТОЯЩИЕ `evidence_refs` (которые по той же фикстуре и так
    указывают на FRG-FAKE-001/SRC-FAKE-001 — проверка `fragment_id`/`source_id` ниже не меняется); оставлен как
    напоминание, что `_card()` сам по себе больше не источник правды для этого поля на пути без LLM."""
    from pcbk_core.analytics.models import EvidenceRef
    ref = EvidenceRef(source_id="SRC-FAKE-001", fragment_id="FRG-FAKE-001",
                      locator={"type": "pdf_page", "page": 1}, corpus_version="v1", limitation="")
    card = _card("CLAIM-FAKE-001", evidence_refs=(ref,))

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section",
                        lambda c: _section(c.text, claim_id=c.id, evidence_refs=c.evidence_refs))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "completed"
    evidence_ref = response.sections[0].evidence_refs[0]
    assert evidence_ref.fragment_id is not None and evidence_ref.fragment_id.count(service_mod.EVIDENCE_SEP) == 2
    assert evidence_ref.fragment_id.startswith(request_id + service_mod.EVIDENCE_SEP)

    item = await svc.get_evidence(STUDENT_A, evidence_ref.fragment_id)
    assert item is not None
    assert item["source_id"] == "SRC-FAKE-001"
    assert item["text"]              # текст фрагмента настоящего синтетического корпуса, не пусто

    assert await svc.get_evidence(STUDENT_B, evidence_ref.fragment_id) is None     # чужой пользователь — 404
    await svc.aclose()


async def test_cancel_during_generating_does_not_force_cancel_background_task(tmp_path, monkeypatch):
    """Правка, раунд 1, Important 4: отмена при стадии "generating" (снятой из той же транзакции, что сама
    отмена — store.cancel_request, не отдельное раннее чтение) НЕ прерывает фоновую asyncio.Task насильно —
    платный вызов доводится до конца, его результат просто не публикуется (store уже терминален)."""
    import threading
    started, blocked = threading.Event(), threading.Event()

    def sync_slow(identity, request, handle, corpus_version):
        started.set()
        if not blocked.wait(timeout=5):
            raise AssertionError("test did not release blocked in time")
        return _preanalysis("limited_answer"), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", sync_slow)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    await asyncio.to_thread(started.wait, 2.0)
    # имитация гонки: к моменту отмены задание (по данным store) уже продвинулось до "generating", хотя
    # настоящий фоновый проход всё ещё блокирован внутри preanalysis.analyze (как был бы заблокирован внутри
    # настоящего вызова LLM) — проверяем решение service.cancel_request именно по этому снимку
    await asyncio.to_thread(svc.store.advance_stage, request_id, "generating", "модель думает")
    task = svc._jobs.get(request_id)
    assert task is not None and not task.done()

    cancelled = await svc.cancel_request(STUDENT_A, request_id)
    assert cancelled is True
    assert not task.cancelled() and not task.done()         # фоновая задача НЕ прервана насильно

    blocked.set()
    await asyncio.sleep(0.2)
    response = await svc.get_request(STUDENT_A, request_id)
    assert response.state == "cancelled"                     # store уже терминален — поздняя публикация отклонена
    await svc.aclose()


async def test_control_question_refuses_command_keeps_reference_and_never_calls_llm(tmp_path, monkeypatch):
    """Правка, раунд 4 (контроллер, T-10 «отказ от команды с сохранением допустимой справки»): настоящий
    преанализ и настоящая проверка на синтетическом корпусе. Команда → `limited_answer` с кодом `policy_refused`,
    первая секция — серверный отказ от управляющей части, дальше — справка по карточкам; модель не вызывается
    даже при настроенной роли LLM."""
    calls: list[str] = []

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        calls.append(identity.caller)
        raise service_mod.llm_adapter.LlmAdapterRefusal("stopped")
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)

    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Заглуши насос номер два, срочно")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "limited_answer"
    assert response.error_code == "policy_refused"
    assert response.sections[0].kind == "limitation"
    assert response.sections[0].text == service_mod.rendering.CONTROL_REFUSAL_TEXT
    assert all(section.rendered_by == "server" for section in response.sections)
    assert calls == []                                                    # команда — модель не вызывалась
    await svc.aclose()


# ================================================================================================================
# Правка итогового ревью F1 — расчёты подключены к живому пути вопроса (п.1), капабилити/сроки хранения/evidence
# (пп.2/3/5). Настоящий преданализ (НЕ подмена) на синтетическом корпусе — тот же приём, что
# `test_control_question_refuses_command_keeps_reference_and_never_calls_llm` выше: только так вопрос реально
# доходит до `calculations.plan_from_question` через `request.question`.
# ================================================================================================================

def _insert_cov_claim(corpus_path: Path) -> None:
    """Дополнение контроллера (в): в синтетическом корпусе фикстуры нет своей карточки-определения COV — заводим
    свою (не трогая `fixtures/kdm1_corpus.py`, приём — как `test_analytics_preanalysis.py::_insert_claim`)."""
    conn = sqlite3.connect(corpus_path)
    try:
        payload = {
            "id": "CLAIM-FAKE-COV", "kind": "definition",
            "text": "В руководстве Valmet IQ показатель COV относится к вкладке анализа и предельным линиям.",
            "term_ids": [], "evidence_refs": ["FRG-FAKE-001"], "applicability": "KDM1_PCBK, тест",
            "publication_state": "desk_verified_draft", "allowed_render_mode": "attributed_reference",
            "risk_level": "non_operational_reference", "permits_local_setpoint": False,
            "critical_local_fact": False, "reviewer_login": None,
        }
        conn.execute("INSERT INTO claims (id, payload) VALUES (?, ?)", ("CLAIM-FAKE-COV", json.dumps(payload)))
        conn.execute("INSERT INTO kb_search (kind, id, text) VALUES ('claim', ?, ?)",
                     ("CLAIM-FAKE-COV", payload["text"]))
        conn.commit()
    finally:
        conn.close()


async def test_calculation_dry_solid_flow_t04_real_run_job(tmp_path):
    """T-04: «200 м³/ч, ρ=1 т/м³, 4% по массе» → секция calculation с «8» и допущением, настоящий `_run_job`."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    question = "Явный учебный набор: 200 м³/ч, ρ=1 т/м³, 4% по массе. Сколько сухого вещества?"
    request_id = await _ask(svc, STUDENT_A, conv, question=question)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "calculation"
    assert "8" in response.sections[0].text
    assert "т/ч" in response.sections[0].text
    assert response.sections[0].evidence_refs == []                        # число — не карточка корпуса
    assert response.state == "completed"
    await svc.aclose()


async def test_calculation_t03_missing_density_no_silent_8(tmp_path):
    """T-03: «Расход 200, концентрация 4 — сколько сухого волокна?» — без числа 8, есть уточнение/ограничение
    (мастер-промпт: «без чисел ответа (уточнение/ограничение)» — ОБА исхода допустимы, здесь преданализ сам
    запрашивает единицу/базу раньше расчётной ветки — тот же факт «нет единицы/базы», просто другим путём)."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    question = "Расход 200, концентрация 4 — сколько сухого волокна?"
    request_id = await _ask(svc, STUDENT_A, conv, question=question)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind in ("limitation", "clarification")
    assert "8" not in response.sections[0].text
    assert response.state in ("limited_answer", "needs_clarification")
    await svc.aclose()


async def test_calculation_linear_load_to_pressure_refused_t05(tmp_path):
    """T-05: «Давление пресса 80 кН/м, переведи в бары» — различить линейную нагрузку и давление; limitation."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Давление пресса 80 кН/м, переведи в бары.")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "limitation"
    assert "не переводится в давление" in response.sections[0].text
    await svc.aclose()


async def test_calculation_geometric_output_18_716334(tmp_path):
    """Мастер-промпт §11: 4,15 м / 590 м/мин / 127,4 г/м² → 18,716334 т/ч (десятичная запятая, Правка раунд 1
    Important 5 rendering._fmt)."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    question = ("Ширина полотна 4,15 м, скорость машины 590 м/мин, масса 1 м² 127,4 г/м². "
               "Посчитай производительность.")
    request_id = await _ask(svc, STUDENT_A, conv, question=question)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "calculation"
    assert "18,716334" in response.sections[0].text
    await svc.aclose()


async def test_calculation_interval_union_overlapping_merges_not_sums(tmp_path):
    """Два пересекающихся интервала → объединение (2 ч), не сумма отдельных длительностей (1,5 + 1 = 2,5 ч)."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    question = "Обрыв был 10:00–11:30, простой 11:00–12:00 — сложить суммарное время простоя."
    request_id = await _ask(svc, STUDENT_A, conv, question=question)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "calculation"
    assert "2" in response.sections[0].text
    assert "2,5" not in response.sections[0].text                          # не сумма исходных длительностей
    await svc.aclose()


async def test_calculation_interval_union_t32_no_times_asks_for_periods(tmp_path):
    """T-32: «Сложить времена обрыва, простоя...» — требуется учёт перекрытия, без literal HH:MM — ограничение
    «укажите начало и конец», не выдуманные числа."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    question = "Сложить времена обрыва, простоя, некондиционного выпуска и смены сорта"
    request_id = await _ask(svc, STUDENT_A, conv, question=question)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "limitation"
    assert "укажите начало и конец" in response.sections[0].text
    await svc.aclose()


async def test_calculation_zero_concentration_is_explicit_zero_result(tmp_path):
    """Дополнение контроллера (а): «0 % по массе» — явный результат 0 т/ч, не пропуск базы (ноль ≠ пропуск,
    мастер-промпт §8)."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    question = "200 м³/ч, ρ=1 т/м³, 0 % по массе. Сколько сухого вещества?"
    request_id = await _ask(svc, STUDENT_A, conv, question=question)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "calculation"
    assert "Результат — 0 т/ч" in response.sections[0].text
    await svc.aclose()


async def test_calculation_gauge_to_absolute_pressure_refused(tmp_path):
    """Дополнение контроллера (б): перевод избыточного давления в абсолютное — limitation, нужна база."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv,
                            question="Переведи манометрическое давление 3,5 бар в абсолютное.")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "limitation"
    assert "абсолютное" in response.sections[0].text
    assert "атмосферное давление" in response.sections[0].text
    await svc.aclose()


async def test_calculation_cov_valmet_screen_refused_with_definition_card(tmp_path):
    """Дополнение контроллера (в): COV экрана Valmet — не считать, limitation с объяснением, плюс карточка-
    определение COV (найденная обычным поиском, корпус дополнен своей `CLAIM-FAKE-COV` без правки фикстуры)."""
    svc = await _service_no_processes(tmp_path, build=_insert_cov_claim)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Рассчитай COV по данным экрана Valmet за смену.")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "limitation"
    assert "COV Valmet IQ" in response.sections[0].text
    assert "коэффициент вариации" in response.sections[0].text
    assert any(s.claim_id == "CLAIM-FAKE-COV" for s in response.sections[1:])
    await svc.aclose()


async def test_calculation_external_benchmark_comparison_refused(tmp_path):
    """Дополнение контроллера (г): сравнение с другими заводами/странами — не расчёт, limitation без выдуманной
    статистики."""
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv,
                            question="Сравни производительность КДМ-1 с аналогичными машинами в Китае.")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "limitation"
    assert "межзаводского" in response.sections[0].text
    await svc.aclose()


# --- план Д17, Task 1: capabilities.data_bridge_available следует за calc, не ВСЕГДА False ---

async def test_capabilities_data_bridge_available_follows_calc(tmp_path):
    """`data_bridge_available = self.calc is not None` И у его роли нет `config_error` (план Д17, Task 1) —
    снимает прежнее решение контроллера Д16 «ВСЕГДА False» (тогда контракт ответа ещё не нёс
    `telemetry_observation`)."""
    svc = await _service(tmp_path)
    assert svc.capabilities(STUDENT_A).data_bridge_available is False     # calc не подключён вовсе
    svc.calc = _FakeCalc()
    assert svc.capabilities(STUDENT_A).data_bridge_available is True      # подключён, у роли нет config_error
    svc.calc.role.config_error = "нет связи с историаном"
    assert svc.capabilities(STUDENT_A).data_bridge_available is False     # подключён, но роль неисправна
    await svc.aclose()


# --- п.3: сроки хранения применяются — один раз в open() и фоновой задачей раз в RETENTION_SWEEP_INTERVAL_S ---

async def test_retention_sweep_runs_once_on_open_and_periodically(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "RETENTION_SWEEP_INTERVAL_S", 0.03)
    calls: list[tuple] = []
    original_sweep = AnalyticsStore.sweep_retention

    def spy(self, now, *, history_days, evidence_days):
        calls.append((history_days, evidence_days))
        return original_sweep(self, now, history_days=history_days, evidence_days=evidence_days)
    monkeypatch.setattr(AnalyticsStore, "sweep_retention", spy)

    svc = await _service(tmp_path)
    assert len(calls) == 1                                       # вызван сразу в open()
    await asyncio.sleep(0.12)
    assert len(calls) >= 2                                        # и фоновой задачей дальше
    await svc.aclose()
    count_after_close = len(calls)
    await asyncio.sleep(0.12)
    assert len(calls) == count_after_close                        # отменена в aclose() — больше не растёт


async def test_retention_sweep_failure_is_logged_not_raised(tmp_path, monkeypatch):
    """Сбой одного прохода очистки — строка журнала, не падение open()/службы."""
    def boom(self, now, *, history_days, evidence_days):
        raise RuntimeError("диск недоступен")
    monkeypatch.setattr(AnalyticsStore, "sweep_retention", boom)
    svc = await _service(tmp_path)                                  # open() не бросает несмотря на сбой очистки
    assert svc.config_error is None
    await svc.aclose()


# --- п.5: название/тип/дата источника в раскрытом основании (мастер-промпт §6) ---

async def test_evidence_includes_source_title_type_date(tmp_path):
    svc = await _service_no_processes(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    # "Выдуманная карточка N: 20FAKE_NNN_PV." — вопрос по тегу достаточной карточки фикстуры
    request_id = await _ask(svc, STUDENT_A, conv, question="Покажи подробно 20FAKE_001_PV.")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    refs = [ref for section in response.sections for ref in section.evidence_refs]
    assert refs, "вопрос должен найти хотя бы одну карточку с основанием в синтетическом корпусе"
    evidence_id = refs[0].fragment_id
    item = await svc.get_evidence(STUDENT_A, evidence_id)
    assert item is not None
    assert item["source_title"] == "Выдуманный источник 1"
    assert item["source_type"] in ("public_primary", "supplied_oem_manual")
    assert item["source_date"] == "2026-01-01"
    await svc.aclose()


# ================================================================================================================
# Правка итогового ревью F4a (14.10) — свежая проверка в момент выдачи (п.1), смена версии корпуса без
# перезапуска (п.2), исторический ответ при чтении (п.3), занятость диалога (п.4), смешанный вопрос (п.5).
# ================================================================================================================

# --- п.1: ОСОБЫЙ ФОКУС №2 плана, усиление — _refresh_pack перед validate_selection ---

async def test_refresh_pack_drops_card_withdrawn_between_search_and_validation(tmp_path, monkeypatch):
    """Карточка найдена НАСТОЯЩИМ преданализом, но её `publication_state` отозван (`UPDATE ... 'withdrawn'`) В
    ФАЙЛЕ корпуса между поиском и проверкой — прямая имитация брифа (п.1): `_build_outcome` перечитывает пакет
    по id (`_refresh_pack`) НЕПОСРЕДСТВЕННО перед `validator.validate_selection`, снимок `EvidenceCard`
    преданализа (который «ещё не знает» об отзыве) не используется напрямую — отозванная карточка не выходит."""
    svc = await _service_no_processes(tmp_path)
    corpus_path = Path(svc._handle.path)
    real_analyze = service_mod.preanalysis.analyze
    withdrawn: dict[str, str] = {}

    def fake_analyze(identity, request, handle, corpus_version):
        pre, pack = real_analyze(identity, request, handle, corpus_version)
        assert pre.allowed_claim_ids, "вопрос должен найти хотя бы одну карточку в синтетическом корпусе"
        target = pre.allowed_claim_ids[0]
        withdrawn["id"] = target
        conn = sqlite3.connect(corpus_path)
        try:
            conn.execute("UPDATE claims SET payload = json_set(payload, '$.publication_state', 'withdrawn') "
                         "WHERE id = ?", (target,))
            conn.commit()
        finally:
            conn.close()
        return pre, pack
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Покажи подробно 20FAKE_001_PV.")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    shown = {s.claim_id for s in response.sections if s.claim_id}
    assert withdrawn["id"] not in shown
    await svc.aclose()


# --- п.2: смена версии корпуса без перезапуска процесса ---

async def test_corpus_reopens_on_version_change_between_jobs(tmp_path, monkeypatch):
    """Публикация новой версии (переключение указателя `CURRENT`) МЕЖДУ заданиями — следующее задание отвечает
    по НОВОЙ версии без пересоздания процесса (`_maybe_reopen_corpus`, в начале КАЖДОГО задания)."""
    from pcbk_core.analytics import knowledge as knowledge_mod
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    v1_dir, v2_dir = corpus_dir / "v1", corpus_dir / "v2"
    v1_dir.mkdir()
    v2_dir.mkdir()
    build_corpus(v1_dir / "a.seed.sqlite3", counts={"terms": 3})
    build_corpus(v2_dir / "b.seed.sqlite3", counts={"terms": 5})
    (corpus_dir / knowledge_mod.CURRENT_POINTER_NAME).write_text("v1", encoding="utf-8")

    monkeypatch.setattr(service_mod.preanalysis, "analyze",
                        lambda *a, **k: (_preanalysis("limited_answer"), ()))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = AnalyticsService(_settings(tmp_path, corpus_dir=str(corpus_dir)))
    await svc.open()
    v1_version = svc._handle.version
    conv = await svc.create_conversation(STUDENT_A, "reference")
    r1 = await _ask(svc, STUDENT_A, conv, client_request_id="cr-1")
    resp1 = await _wait_terminal(svc, STUDENT_A, r1)
    assert resp1.corpus_version == v1_version

    (corpus_dir / knowledge_mod.CURRENT_POINTER_NAME).write_text("v2", encoding="utf-8")
    conv2 = await svc.create_conversation(STUDENT_A, "reference")
    r2 = await _ask(svc, STUDENT_A, conv2, client_request_id="cr-2")
    resp2 = await _wait_terminal(svc, STUDENT_A, r2)
    assert resp2.corpus_version != v1_version
    assert svc._handle.version == resp2.corpus_version          # переоткрыт без перезапуска процесса
    await svc.aclose()


async def test_job_does_not_publish_stale_version_after_switch_mid_flight(tmp_path, monkeypatch):
    """Версия корпуса сменилась МЕЖДУ поиском и выдачей ОДНОГО задания (поиск/платный вызов заняли время) — не
    публикует карточки «как ни в чём не бывало»: `limited_answer` с единственной секцией `limitation`, без
    карточек (брифа п.2, 3-й подпункт)."""
    from pcbk_core.analytics import knowledge as knowledge_mod
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    v1_dir, v2_dir = corpus_dir / "v1", corpus_dir / "v2"
    v1_dir.mkdir()
    v2_dir.mkdir()
    build_corpus(v1_dir / "a.seed.sqlite3", counts={"terms": 3})
    build_corpus(v2_dir / "b.seed.sqlite3", counts={"terms": 5})
    (corpus_dir / knowledge_mod.CURRENT_POINTER_NAME).write_text("v1", encoding="utf-8")

    card = _card("CLAIM-FAKE-001")

    def fake_analyze(identity, request, handle, corpus_version):
        # имитация публикации новой версии ПОСЛЕ поиска (преданализ), ДО выдачи этого же задания
        (corpus_dir / knowledge_mod.CURRENT_POINTER_NAME).write_text("v2", encoding="utf-8")
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = AnalyticsService(_settings(tmp_path, corpus_dir=str(corpus_dir)))
    await svc.open()
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "limited_answer"
    assert len(response.sections) == 1
    assert response.sections[0].kind == "limitation"
    assert response.sections[0].text == service_mod.CORPUS_VERSION_CHANGED_TEXT
    await svc.aclose()


# --- п.3: исторический ответ при ЧТЕНИИ (не при сохранении) ---

async def test_get_request_adds_historical_notice_when_corpus_version_changed(tmp_path, monkeypatch):
    """`response.corpus_version` сохранённого ответа отличается от действующей версии корпуса — `get_request`/
    `list_requests` добавляют (не сохраняя) последней секцией `limitation` «ответ исторический»."""
    monkeypatch.setattr(service_mod.preanalysis, "analyze", lambda *a, **k: (_preanalysis("limited_answer"), ()))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    old_version = svc._handle.version
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.corpus_version == old_version

    # имитация смены действующей версии между публикацией ответа и чтением — подмена self._handle, сохранённый
    # ответ в store.py не трогается
    from pcbk_core.analytics.knowledge import CorpusHandle
    svc._handle = CorpusHandle(path=svc._handle.path, version="другая-версия")

    read_back = await svc.get_request(STUDENT_A, request_id)
    assert read_back.corpus_version == old_version                      # сам сохранённый ответ не переписан
    assert read_back.sections[-1].kind == "limitation"
    assert old_version in read_back.sections[-1].text
    assert "другая-версия" in read_back.sections[-1].text
    assert "исторический" in read_back.sections[-1].text

    history = await svc.list_requests(STUDENT_A, conv)
    assert history[-1].response.sections[-1].kind == "limitation"
    assert "исторический" in history[-1].response.sections[-1].text
    await svc.aclose()


async def test_get_request_historical_notice_skipped_when_sections_at_limit(tmp_path):
    """Краевой случай брифа (п.3): секций уже предел контракта (`job-response.schema.json`, `maxItems=24`) —
    заменить последнюю для предупреждения нельзя, оно просто не добавляется (сохранённый ответ как есть)."""
    from pcbk_core.analytics.knowledge import CorpusHandle
    from pcbk_core.analytics.models import AnalysisRequest
    svc = await _service(tmp_path)
    old_version = svc._handle.version
    conv = await svc.create_conversation(STUDENT_A, "reference")
    req = AnalysisRequest(client_request_id="cr-full", question="вопрос", mode="reference")
    request_id, created = await asyncio.to_thread(svc.store.create_request, STUDENT_A, conv, req)
    assert created
    full_sections = [_section(f"текст {i}") for i in range(service_mod._JOB_RESPONSE_MAX_SECTIONS)]
    response = _fake_render_response(request_id, conv, "completed", old_version, "готово",
                                     ValidationOutcome((), (), (), (), False), full_sections)
    assert await asyncio.to_thread(svc.store.publish_response, request_id, response)

    svc._handle = CorpusHandle(path=svc._handle.path, version="другая-версия")
    read_back = await svc.get_request(STUDENT_A, request_id)
    assert len(read_back.sections) == service_mod._JOB_RESPONSE_MAX_SECTIONS   # не добавлена сверх предела
    assert read_back.corpus_version == old_version
    await svc.aclose()


# --- п.4: занятость — не больше одного платного вызова модели одновременно на диалог ---

async def test_busy_second_question_in_same_conversation_skips_model_call(tmp_path, monkeypatch):
    """Два вопроса ОДНОГО диалога, пока первый ждёт задержанный двойник модели: второй не вызывает модель вовсе
    (ровно 1 вызов модели за тест), его ответ — `error_code="busy"` с отличимой секцией `limitation`."""
    import threading
    card = _card("CLAIM-FAKE-001")
    calls: list[str] = []
    release = threading.Event()

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def fake_select_model(role, models):
        return object()

    def fake_build_raw_body(analysis, pack, *, schema, question=""):
        return {}

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        calls.append(session)
        await asyncio.to_thread(release.wait, 5)          # задержанный двойник — "платный вызов в процессе"
        return object()

    def fake_validate_selection(selection, analysis, pack, approved_card_ids=frozenset()):
        return ValidationOutcome(accepted_claim_ids=(card.id,), accepted_mechanism_ids=(), rejected=(),
                                 clarification_keys=(), refused=False)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", fake_select_model)
    monkeypatch.setattr(service_mod.llm_adapter, "build_raw_body", fake_build_raw_body)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.validator, "validate_selection", fake_validate_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    conv = await svc.create_conversation(STUDENT_A, "reference")
    r1 = await _ask(svc, STUDENT_A, conv, client_request_id="cr-1")
    for _ in range(300):
        if conv in svc._llm_inflight_conversations:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("первый вопрос не дошёл до вызова модели за отведённое время")

    r2 = await _ask(svc, STUDENT_A, conv, client_request_id="cr-2")
    resp2 = await _wait_terminal(svc, STUDENT_A, r2)
    assert resp2.error_code == "busy"
    assert any(s.kind == "limitation" for s in resp2.sections)

    release.set()
    resp1 = await _wait_terminal(svc, STUDENT_A, r1)
    assert resp1.error_code != "busy"
    assert len(calls) == 1          # второй вопрос модель вообще не вызывал
    await svc.aclose()


async def test_busy_llm_adapter_refusal_marks_busy_error_code(tmp_path, monkeypatch):
    """`LlmAdapterRefusal("busy")` (предел параллельности самого ВЫЗЫВАЮЩЕГО, `role.inflight`, не этого диалога)
    — так же: справка без модели, `error_code="busy"`, отличимая секция `limitation`."""
    card = _card("CLAIM-FAKE-001")

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def fake_select_model(role, models):
        return object()

    def fake_build_raw_body(analysis, pack, *, schema, question=""):
        return {}

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        raise service_mod.llm_adapter.LlmAdapterRefusal("busy")
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", fake_select_model)
    monkeypatch.setattr(service_mod.llm_adapter, "build_raw_body", fake_build_raw_body)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.error_code == "busy"
    assert any(s.kind == "limitation" for s in response.sections)
    assert card.id in {s.claim_id for s in response.sections}
    await svc.aclose()


# --- п.5: смешанный вопрос (вместе с F4b) — первой секцией limitation про чужую машину ---

async def test_mixed_question_other_machine_gets_limitation_section_first(tmp_path, monkeypatch):
    """Вопрос упоминает другую машину (БДМ-2) вместе с КДМ-1, решение преданализа не `refused` (здесь — прямая
    подмена `analyze`, не завязана на то, как именно F4b решает не отклонять смешанный вопрос целиком) —
    первой секцией `limitation` с названием чужой машины."""
    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("limited_answer", corpus_version=corpus_version), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="КДМ-1 и БДМ-2 — в чём разница по приводу?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections[0].kind == "limitation"
    assert "БДМ-2" in response.sections[0].text
    assert "КДМ-1" in response.sections[0].text
    await svc.aclose()


async def test_mixed_question_without_other_machine_has_no_extra_limitation(tmp_path, monkeypatch):
    """Контрольный тест: вопрос без чужой машины не получает лишней секции (решение контроллера — не гасить
    обычный путь)."""
    monkeypatch.setattr(service_mod.preanalysis, "analyze", lambda *a, **k: (_preanalysis("limited_answer"), ()))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Что такое базисный вес на КДМ-1?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.sections == []
    await svc.aclose()


# ================================================================================================================
# Дополнение контроллера к правке F4a (14.10, хвосты F4b, коммит 74172d1): подписи кандидатов термина в
# уточнении (H-26), честное отключение LLM по ANALYTICS_LLM_EXPORT_POLICY == "none".
# ================================================================================================================

async def test_needs_clarification_shows_candidate_labels_for_ambiguous_term(tmp_path, monkeypatch):
    """`_build_outcome` в ветке `needs_clarification` зовёт НАСТОЯЩИЙ `preanalysis.clarification_candidates` и
    передаёт результат в НАСТОЯЩИЙ `rendering.render_clarification_section(keys, candidate_labels=...)` — подписи
    кандидатов неоднозначного термина (`terms.jsonl.preferred_ru`) попадают в текст уточнения технологу, не
    только список ключей. `preanalysis.analyze` подменён готовым `ResolvedTerm` с двумя кандидатами (не завязан
    на то, какое именно слово настоящего вопроса настоящий `terms.resolve_term` разрешает в несколько
    кандидатов одновременно — это ответственность Task 1/3, не этой правки)."""
    from pcbk_core.analytics.models import ResolvedTerm
    ambiguous_term = ResolvedTerm(original="термин", candidate_ids=["TERM-FAKE-004", "TERM-FAKE-008"],
                                  selected_id=None, requires_confirmation=True)

    def fake_analyze(identity, request, handle, corpus_version):
        pre = _preanalysis("needs_clarification", missing_fields=("quantity",),
                           resolved_terms=(ambiguous_term,))
        return pre, ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Сколько сейчас воды в обороте?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert response.state == "needs_clarification"
    text = response.sections[0].text
    assert "выдуманный термин 4" in text
    assert "выдуманный термин 8" in text
    await svc.aclose()


async def test_capabilities_and_llm_call_disabled_when_export_policy_none(tmp_path, monkeypatch):
    """`ANALYTICS_LLM_EXPORT_POLICY == "none"` — сервер-уровневый выключатель (не per-запросный `Preanalysis.
    llm_export_policy`, который `preanalysis.py` выставляет за каждое решение отдельно): `capabilities().
    llm_available=False` И модель не вызывается вовсе — откат на детерминированный путь, не падение."""
    from dataclasses import replace
    card = _card("CLAIM-FAKE-001")
    calls: list[str] = []

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        calls.append(session)
        return object()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    assert svc.capabilities(STUDENT_A).llm_available is True          # до выключателя — доступно как обычно

    svc.settings = replace(svc.settings, ANALYTICS_LLM_EXPORT_POLICY="none")
    assert svc.capabilities(STUDENT_A).llm_available is False

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert calls == []                      # модель не вызывалась вовсе
    assert response.state == "completed"    # откат на детерминированный путь, не падение
    assert card.id in {s.claim_id for s in response.sections}
    await svc.aclose()


# ================================================================================================================
# Итоговая правка F6 (14.10.2026, решение владельца, дословно): «4. Разрешен полный контент в тч внутренний и
# данные с БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в модель!». `_select_with_llm`
# подставляет `settings.ANALYTICS_LLM_EXPORT_POLICY` в `Preanalysis.llm_export_policy` перед вызовом
# `llm_adapter.build_raw_body`, и передаёт туда исходный вопрос технолога; `intent == "control"` — по-прежнему
# без модели вовсе, независимо от значения настройки.
# ================================================================================================================

async def test_approved_subset_setting_is_substituted_into_preanalysis_before_adapter_call(tmp_path, monkeypatch):
    """F6, п.1 брифа: `AnalyticsSettings.ANALYTICS_LLM_EXPORT_POLICY == "approved_subset"` подставляется в
    `Preanalysis.llm_export_policy`, который видит `llm_adapter.build_raw_body`, НЕПОСРЕДСТВЕННО перед вызовом
    адаптера — `preanalysis.py` (здесь подменён фейком) по-прежнему всегда отдаёт `public_only`, политика,
    дошедшая до адаптера, — значение настройки, не снимка преданализа. Вопрос технолога (`AnalysisRequest.
    question`) доходит до `build_raw_body` как переданный клиентом текст."""
    from dataclasses import replace
    card = _card("CLAIM-FAKE-001")
    seen: dict = {}

    def fake_analyze(identity, request, handle, corpus_version):
        pre = _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version,
                           llm_export_policy="public_only")
        return pre, (card,)

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def fake_select_model(role, models):
        return object()

    def fake_build_raw_body(analysis, pack, *, schema, question=""):
        seen["policy"] = analysis.llm_export_policy
        seen["question"] = question
        return {}

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        return object()

    def fake_validate_selection(selection, analysis, pack, approved_card_ids=frozenset()):
        return ValidationOutcome(accepted_claim_ids=(card.id,), accepted_mechanism_ids=(), rejected=(),
                                 clarification_keys=(), refused=False)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", fake_select_model)
    monkeypatch.setattr(service_mod.llm_adapter, "build_raw_body", fake_build_raw_body)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.validator, "validate_selection", fake_validate_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    svc.settings = replace(svc.settings, ANALYTICS_LLM_EXPORT_POLICY="approved_subset")
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="сколько сухого остатка на 2БДМ?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert seen["policy"] == "approved_subset"
    assert seen["question"] == "сколько сухого остатка на 2БДМ?"
    assert response.state == "completed"
    await svc.aclose()


async def test_public_only_setting_keeps_preanalysis_policy_unchanged(tmp_path, monkeypatch):
    """Контрольный тест: умолчание настройки (`public_only`) не меняет то, что и так стоит у преданализа —
    подстановка F6 не создаёт расхождения для прежнего поведения."""
    card = _card("CLAIM-FAKE-001")
    seen: dict = {}

    def fake_analyze(identity, request, handle, corpus_version):
        pre = _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version,
                           llm_export_policy="public_only")
        return pre, (card,)

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def fake_select_model(role, models):
        return object()

    def fake_build_raw_body(analysis, pack, *, schema, question=""):
        seen["policy"] = analysis.llm_export_policy
        return {}

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        return object()

    def fake_validate_selection(selection, analysis, pack, approved_card_ids=frozenset()):
        return ValidationOutcome(accepted_claim_ids=(card.id,), accepted_mechanism_ids=(), rejected=(),
                                 clarification_keys=(), refused=False)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", fake_select_model)
    monkeypatch.setattr(service_mod.llm_adapter, "build_raw_body", fake_build_raw_body)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.validator, "validate_selection", fake_validate_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv)
    await _wait_terminal(svc, STUDENT_A, request_id)
    assert seen["policy"] == "public_only"
    await svc.aclose()


async def test_control_intent_never_reaches_llm_regardless_of_export_policy(tmp_path, monkeypatch):
    """F6, п.2 брифа: `intent == "control"` — по-прежнему без модели, даже когда `ANALYTICS_LLM_EXPORT_POLICY
    == "approved_subset"` — ветка control в `_build_outcome` возвращает ответ раньше, чем доходит до
    `_select_with_llm`."""
    from dataclasses import replace
    calls: list[str] = []

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("limited_answer", intent="control", llm_export_policy="none"), ()

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        calls.append(session)
        return object()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.llm = FakeLlm()
    svc.settings = replace(svc.settings, ANALYTICS_LLM_EXPORT_POLICY="approved_subset")
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="выполни остановку КДМ-1")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert calls == []                                # модель не вызывалась вовсе
    assert response.state == "limited_answer"
    assert response.error_code == "policy_refused"
    await svc.aclose()


# --- план Д17, Task 1: телеметрия БДРВ в ответе (мастер-промпт §7 п.7, §8) ---

async def test_telemetry_unresolved_label_reads_nothing_and_explains(tmp_path, monkeypatch):
    """Review Focus №1: подпись без согласованной связи — ни одного вызова `tag_now`, ограничение с подписью и
    словами «связь»/«не согласована»."""
    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", corpus_version=corpus_version), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.calc = _FakeCalc(now_reply=_now_reply())              # служба данных подключена — связи всё равно нет
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Что покажет 20FAKE_001_PV в машинном зале?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)

    assert svc.calc.calls == []
    limitation = [s for s in response.sections if s.kind == "limitation"]
    assert len(limitation) == 1
    assert "20FAKE_001_PV" in limitation[0].text
    assert "связь" in limitation[0].text and "не согласована" in limitation[0].text
    await svc.aclose()


async def test_telemetry_resolved_label_shows_value_unit_time_quality(tmp_path, monkeypatch):
    """Принятая студентом заявка `equipment_link` (режим `all_students`) → `tag_now` вызван ровно с
    `[historian_tag]`, секция `telemetry_observation` содержит подпись, тег, значение, единицу и время из
    ответа двойника, с подписью «время историана»; в запрос к двойнику LLM ни тег, ни значение не попадают
    (телеметрия строится ПОСЛЕ выбора модели, вне пакета доказательств `llm_adapter.build_raw_body` видит)."""
    card = _card("CLAIM-FAKE-001")
    captured: list[dict] = []

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)

    class FakeLlm:
        config_error = None
        stopped = False
        upstream = object()

    def fake_build_raw_body(analysis, pack, *, schema, question=""):
        body = {"question": question, "cards": [c.id for c in pack]}
        captured.append(body)
        return body

    async def fake_request_selection(role, identity, session, raw_body, *, analytics_models, teacher_payer=""):
        return LlmSelection(reference_claim_ids=[card.id], mechanism_ids=[], clarification_keys=[],
                           calculation_ids=[])

    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.llm_adapter, "select_model", lambda role, models: object())
    monkeypatch.setattr(service_mod.llm_adapter, "build_raw_body", fake_build_raw_body)
    monkeypatch.setattr(service_mod.llm_adapter, "request_selection", fake_request_selection)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    # связь ведёт на ДРУГОЙ тег, чем подпись в вопросе (как в жизни: display_label — подпись документа/PNG,
    # historian_tag — отдельное решение согласования, мастер-промпт §7, этап 7) — иначе проверить «тег/значение
    # не попали в LLM» нечем: подпись студент и так печатает в своём же вопросе, который видит модель
    svc = await _service(tmp_path, reviewers="all_students")
    svc.llm = FakeLlm()
    svc.calc = _FakeCalc(now_reply=_now_reply(tag="20FAKE_777_HIST", value=12.5, unit="л/час"))
    _approve_equipment_link(svc, link_id="EQ-FAKE-001", tag="20FAKE_777_HIST")

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Что покажет 20FAKE_001_PV в машинном зале?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)

    assert svc.calc.calls == [("tag_now", "student-03", "web", ("20FAKE_777_HIST",))]
    telemetry = [s for s in response.sections if s.kind == "telemetry_observation"]
    assert len(telemetry) == 1
    text = telemetry[0].text
    assert "20FAKE_001_PV" in text and "20FAKE_777_HIST" in text
    assert "12,5" in text and "л/час" in text and "время историана" in text
    assert response.state == "completed"
    assert captured and "20FAKE_777_HIST" not in str(captured[0]) and "12,5" not in str(captured[0])
    await svc.aclose()


async def test_telemetry_with_context_period_uses_tag_period(tmp_path, monkeypatch):
    """`request.context.period` задан → `tag_period` с этими границами, в секции среднее/мин/макс и доля
    хороших точек из ответа двойника, без пересчёта."""
    from datetime import datetime, timezone

    from pcbk_core.analytics.models import AnalysisRequest, PeriodWindow, RequestContext

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", corpus_version=corpus_version), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path, reviewers="all_students")
    svc.calc = _FakeCalc(period_reply=_period_reply())
    _approve_equipment_link(svc, link_id="EQ-FAKE-001", tag="20FAKE_001_PV")

    period = PeriodWindow(start=datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc),
                          end=datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc))
    request = AnalysisRequest(client_request_id="cr-1", question="Какой средний расход по 20FAKE_001_PV?",
                              mode="reference", context=RequestContext(period=period))
    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await svc.submit_question(STUDENT_A, conv, request)
    response = await _wait_terminal(svc, STUDENT_A, request_id)

    assert len(svc.calc.calls) == 1 and svc.calc.calls[0][0] == "tag_period"
    assert svc.calc.calls[0][4:] == (None, period.start.isoformat(), period.end.isoformat())
    telemetry = [s for s in response.sections if s.kind == "telemetry_observation"]
    assert len(telemetry) == 1
    text = telemetry[0].text
    assert "12,4" in text and "10,1" in text and "15" in text and "99,5" in text
    await svc.aclose()


async def test_telemetry_mode_switch_to_grants_stops_reading(tmp_path, monkeypatch):
    """Review Focus №2: та же заявка, затем `ANALYTICS_REVIEWERS=grants` → чтения нет, ограничение."""
    from dataclasses import replace

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", corpus_version=corpus_version), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path, reviewers="all_students")
    svc.calc = _FakeCalc(now_reply=_now_reply())
    _approve_equipment_link(svc, link_id="EQ-FAKE-001", tag="20FAKE_001_PV")

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Что показывает 20FAKE_001_PV?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)
    assert any(s.kind == "telemetry_observation" for s in response.sections)
    assert len(svc.calc.calls) == 1

    svc.settings = replace(svc.settings, ANALYTICS_REVIEWERS="grants")
    conv2 = await svc.create_conversation(STUDENT_A, "reference")
    request_id2 = await _ask(svc, STUDENT_A, conv2, question="Что показывает 20FAKE_001_PV?",
                             client_request_id="cr-2")
    response2 = await _wait_terminal(svc, STUDENT_A, request_id2)
    assert len(svc.calc.calls) == 1                           # второй вопрос не прочитал значение заново
    limitation = [s for s in response2.sections if s.kind == "limitation"]
    assert any("не согласована" in s.text for s in limitation)
    await svc.aclose()


async def test_telemetry_not_read_on_clarification_control_refused(tmp_path, monkeypatch):
    """Review Focus №3 (мастер-промпт §7 п.4: «уточнение до оплачиваемого вызова и чтения данных») —
    уточняющий вопрос, команда и отказ преданализа не читают телеметрию вовсе, даже когда в вопросе есть
    подпись с согласованной связью."""
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)
    svc = await _service(tmp_path, reviewers="all_students")
    svc.calc = _FakeCalc(now_reply=_now_reply())
    _approve_equipment_link(svc, link_id="EQ-FAKE-001", tag="20FAKE_001_PV")
    question = "Что покажет 20FAKE_001_PV?"

    scenarios = (
        ("needs_clarification", "lookup", ("quantity",)),
        ("limited_answer", "control", ()),
        ("refused", "out_of_scope", ()),
    )
    for decision, intent, missing in scenarios:
        def fake_analyze(identity, request, handle, corpus_version, decision=decision, intent=intent,
                        missing=missing):
            return (_preanalysis(decision, corpus_version=corpus_version, intent=intent, missing_fields=missing),
                   ())
        monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
        conv = await svc.create_conversation(STUDENT_A, "reference")
        request_id = await _ask(svc, STUDENT_A, conv, question=question, client_request_id=f"cr-{decision}")
        await _wait_terminal(svc, STUDENT_A, request_id)
    assert svc.calc.calls == []
    await svc.aclose()


async def test_telemetry_refusal_text_passed_through(tmp_path, monkeypatch):
    """Review Focus №4: `DataService` отказал — ограничение содержит текст отказа как есть, карточки справки
    остаются."""
    card = _card("CLAIM-FAKE-001")

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path, reviewers="all_students")
    svc.calc = _FakeCalc(now_reply={"tool": "tag_now", "status": "refused",
                                    "message": "ворота закрыты (учения)", "notes": [], "cache": "none"})
    _approve_equipment_link(svc, link_id="EQ-FAKE-001", tag="20FAKE_001_PV")

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Что показывает 20FAKE_001_PV?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)

    limitation = [s for s in response.sections if s.kind == "limitation" and "20FAKE_001_PV" in s.text]
    assert len(limitation) == 1 and "ворота закрыты (учения)" in limitation[0].text
    assert any(s.claim_id == card.id for s in response.sections)      # карточки справки остаются
    await svc.aclose()


async def test_telemetry_review_store_failure_gives_unavailable_limitation_not_crash(tmp_path, monkeypatch):
    """Ревью, раунд 1, Important 2: `reviews.resolved_link` бросает `ReviewStoreError` (сбой самого хранилища
    согласований, не правило) — задание завершается, не падает `failed`; по этой подписи — ограничение
    «служба данных недоступна», двойник `DataService` не вызван вовсе (решение о чтении не принято — читать
    нечего), карточки справки остаются."""
    from pcbk_core.analytics.reviews import ReviewStoreError

    card = _card("CLAIM-FAKE-001")

    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", allowed_claim_ids=(card.id,), corpus_version=corpus_version), (card,)
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_claim_section", lambda c: _section(c.text, claim_id=c.id))
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.calc = _FakeCalc(now_reply=_now_reply())

    def boom(link_id, *, reviewers_mode="grants"):
        raise ReviewStoreError("аналитика КДМ-1 (согласование): сбой хранилища (двойник)")
    monkeypatch.setattr(svc.reviews, "resolved_link", boom)

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Что показывает 20FAKE_001_PV?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)

    assert response.state != "failed"
    assert svc.calc.calls == []                                      # решение о чтении не принято — не читал
    limitation = [s for s in response.sections if s.kind == "limitation" and "20FAKE_001_PV" in s.text]
    assert len(limitation) == 1 and "недоступна" in limitation[0].text
    assert any(s.claim_id == card.id for s in response.sections)      # карточки справки остаются
    await svc.aclose()


async def test_telemetry_at_most_three_labels(tmp_path, monkeypatch):
    """4 подписи со связями → 3 чтения + ограничение «показаны первые 3 … из 4»."""
    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("ready", corpus_version=corpus_version), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path, reviewers="all_students")
    svc.calc = _FakeCalc(now_reply=_now_reply())
    tags = ["20FAKE_001_PV", "20FAKE_002_PV", "20FAKE_003_PV", "20FAKE_004_PV"]
    for i, tag in enumerate(tags, start=1):
        _approve_equipment_link(svc, link_id=f"EQ-FAKE-{i:03d}", tag=tag)

    conv = await svc.create_conversation(STUDENT_A, "reference")
    question = "Сравни показания " + ", ".join(tags)
    request_id = await _ask(svc, STUDENT_A, conv, question=question)
    response = await _wait_terminal(svc, STUDENT_A, request_id)

    assert len(svc.calc.calls) == 3
    telemetry = [s for s in response.sections if s.kind == "telemetry_observation"]
    assert len(telemetry) == 3
    truncated = [s for s in response.sections if s.kind == "limitation" and "Показаны первые" in s.text]
    assert len(truncated) == 1 and "4" in truncated[0].text
    await svc.aclose()


async def test_telemetry_only_answer_with_empty_pack_and_limited_answer_decision_is_completed(tmp_path, monkeypatch):
    """Раунд правки 3 (стык с Task 5): вопрос только о подписи оборудования без содержательных слов — пакет
    пуст, преданализ отдаёт `decision="limited_answer"` (Task 5: модель не вызывается), но значение историана
    — полноценный ответ на такой вопрос. Хотя бы одна `telemetry_observation` → `completed`, независимо от
    `ready`/`limited_answer`."""
    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("limited_answer", corpus_version=corpus_version), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path, reviewers="all_students")
    svc.calc = _FakeCalc(now_reply=_now_reply())
    _approve_equipment_link(svc, link_id="EQ-FAKE-001", tag="20FAKE_001_PV")

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Что сейчас на 20FAKE_001_PV?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)

    assert response.state == "completed"
    assert any(s.kind == "telemetry_observation" for s in response.sections)
    assert svc.llm is None                           # LLM не подключён вовсе — путь без модели не затронут
    await svc.aclose()


async def test_telemetry_absent_with_empty_pack_and_limited_answer_decision_stays_limited(tmp_path, monkeypatch):
    """То же, но подпись без связи — пустой пакет + ограничение (не `telemetry_observation`) — состояние
    остаётся `limited_answer`, как раньше (правило раунда 3 не применяется без хотя бы одной секции
    телеметрии)."""
    def fake_analyze(identity, request, handle, corpus_version):
        return _preanalysis("limited_answer", corpus_version=corpus_version), ()
    monkeypatch.setattr(service_mod.preanalysis, "analyze", fake_analyze)
    monkeypatch.setattr(service_mod.rendering, "render_response", _fake_render_response)

    svc = await _service(tmp_path)
    svc.calc = _FakeCalc(now_reply=_now_reply())          # служба подключена — связи всё равно нет

    conv = await svc.create_conversation(STUDENT_A, "reference")
    request_id = await _ask(svc, STUDENT_A, conv, question="Что сейчас на 20FAKE_001_PV?")
    response = await _wait_terminal(svc, STUDENT_A, request_id)

    assert response.state == "limited_answer"
    assert not any(s.kind == "telemetry_observation" for s in response.sections)
    limitation = [s for s in response.sections if s.kind == "limitation"]
    assert len(limitation) == 1 and "не согласована" in limitation[0].text
    await svc.aclose()
