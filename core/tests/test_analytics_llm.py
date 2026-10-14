"""Адаптер LLM аналитики КДМ-1 (Task 5; план §Task 5/Этап 5; мастер-промпт §9) — `analytics/llm_adapter.py`.

Двойник — существующий `fake_openrouter.py`/фикстура `fake_or` (как у `test_llm_*.py`): настоящих ключей,
OpenRouter и подписки Claude в тестах нет. Корпус — синтетические карточки этого файла (`_card`), не настоящий
пакет заказчика; тег `20FAKE_777_PV` — только для проверки, что он НЕ попадает в исходящее тело.

Обязательные проверки задания 5 (`task-5-brief.md`) и где они покрыты:
1. Ноль внешних вызовов для не-`ready` решений — не маршрут этого модуля (решает `service.py`); здесь —
   `test_build_raw_body_and_select_model_make_no_network_calls` (сами функции без сети).
2. Отказы журнала/бюджета/остановки/параллельности/преподавателя без плательщика — `test_request_selection_*`
   (`test_..._config_error_refuses_before_any_call` и далее).
3. Публичная политика — `test_build_raw_body_excludes_card_text_and_tags_only_ids_in_body`,
   `test_request_selection_success_parses_and_sends_only_ids` (тело, фактически ушедшее в двойник).
4. Один резерв/запись журнала до отправки/запасная модель — `test_request_selection_busy_refuses_without_
   extra_reserve`, `test_request_selection_fallback_triggers_and_succeeds_on_retryable_kind`,
   `test_request_selection_cancelled_mid_call_releases_reserve`.
5. Строгий выбор: неверный JSON/лишнее поле — `test_request_selection_invalid_json_content_refused`,
   `test_request_selection_extra_field_in_selection_refused`; неизвестный id — `validator.py`
   (`test_analytics_validator.py`), здесь — один сквозной `test_request_selection_then_validator_rejects_
   unknown_id`, не повтор всей матрицы validator.py.
6. Исходные `test_llm_ledger/request/fallback/models.py` — зелёные без изменений (прогон отдельно, см. отчёт).

Итоговая правка F6 (14.10.2026, решение владельца — см. докстринг `llm_adapter.py`/`settings.py`):
`approved_subset` — полный контент (вопрос и тексты карточек, включая внутренний/БДРВ, если есть в пакете) в
сообщении модели — `test_build_raw_body_approved_subset_*`; `public_only` при этом не меняется —
`test_build_raw_body_public_only_ignores_question_parameter`; `none` — `build_raw_body` всё равно отказывает
явно (в обычной работе сюда не доходит, выключатель — в `service.py`) — `test_build_raw_body_rejects_
unsupported_export_policy`.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from helpers import make_llm_role

from pcbk_core.analytics import llm_adapter, validator
from pcbk_core.analytics.llm_adapter import LlmAdapterRefusal
from pcbk_core.analytics.models import EvidenceCard, EvidenceRef, PdfPageLocator, Preanalysis, RequestIdentity, \
    ResolvedTerm
from pcbk_core.analytics.settings import AnalyticsSettings
from pcbk_core.llm.upstream import UpstreamError

pytestmark = pytest.mark.anyio

_CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "pcbk_core" / "analytics" / "contracts"
SCHEMA = json.loads((_CONTRACTS_DIR / "llm-selection.schema.json").read_text(encoding="utf-8"))

FAKE_TAG = "20FAKE_777_PV"
STUDENT = RequestIdentity(login="stu-01", role="student", place=1, caller="student-01")
TEACHER = RequestIdentity(login="teach", role="teacher", place=None, caller="teacher")


def _card(card_id: str, *, kind: str = "claim", text: str | None = None,
         evidence_refs: tuple[EvidenceRef, ...] = ()) -> EvidenceCard:
    text = text if text is not None else f"Выдуманная карточка {card_id}: {FAKE_TAG}."
    return EvidenceCard(id=card_id, kind=kind, text=text, risk_level="non_operational_reference",
                        publication_state="desk_verified_draft", allowed_render_mode="attributed_reference",
                        applicability="KDM1_PCBK, тест", critical_local_fact=False, evidence_refs=evidence_refs)


def _preanalysis(*, intent: str = "definition", allowed_claim_ids: tuple[str, ...] = (),
                 allowed_mechanism_ids: tuple[str, ...] = (), resolved_terms: tuple[ResolvedTerm, ...] = (),
                 llm_export_policy: str = "public_only") -> Preanalysis:
    return Preanalysis(scope="KDM1_PCBK", intent=intent, decision="ready", resolved_terms=list(resolved_terms),
                       process_ids=[], conflict_ids=[], missing_fields=[], risk="reference", corpus_version="v1",
                       evidence_pack_id=None, allowed_claim_ids=list(allowed_claim_ids),
                       allowed_mechanism_ids=list(allowed_mechanism_ids),
                       data_access_ready=bool(allowed_claim_ids or allowed_mechanism_ids),
                       llm_export_policy=llm_export_policy)


def _raw_body(pre: Preanalysis | None = None, pack: tuple[EvidenceCard, ...] = (), *, question: str = "") -> dict:
    return llm_adapter.build_raw_body(pre or _preanalysis(), pack, schema=SCHEMA, question=question)


def _success_response(selection: dict, *, gen: str = "gen-test") -> httpx.Response:
    content = json.dumps(selection, ensure_ascii=False)
    body = {"id": gen, "object": "chat.completion",
           "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0012}}
    return httpx.Response(200, json=body, headers={"x-generation-id": gen})


# --- build_raw_body: политика public_only собранием, не обещанием модели ---

def test_build_raw_body_excludes_card_text_and_tags_only_ids_in_body():
    pack = (_card("CLAIM-FAKE-001"), _card("MECH-FAKE-001", kind="mechanism", text=f"Общий механизм {FAKE_TAG}"))
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",), allowed_mechanism_ids=("MECH-FAKE-001",))
    raw = llm_adapter.build_raw_body(pre, pack, schema=SCHEMA)
    dumped = json.dumps(raw, ensure_ascii=False)
    assert FAKE_TAG not in dumped
    assert "Выдуманная карточка" not in dumped and "Общий механизм" not in dumped
    assert raw["stream"] is False
    assert raw["response_format"]["type"] == "json_schema"
    system = json.loads(raw["messages"][0]["content"])
    assert system["allowed_reference_claim_ids"] == ["CLAIM-FAKE-001"]
    assert system["allowed_mechanism_ids"] == ["MECH-FAKE-001"]


def test_build_raw_body_drops_allowed_id_missing_from_pack():
    # преданализ разрешил CLAIM-FAKE-002, но в пакете этого запроса его физически нет — двойная сверка отсекает
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001", "CLAIM-FAKE-002"))
    raw = llm_adapter.build_raw_body(pre, (_card("CLAIM-FAKE-001"),), schema=SCHEMA)
    system = json.loads(raw["messages"][0]["content"])
    assert system["allowed_reference_claim_ids"] == ["CLAIM-FAKE-001"]


def test_build_raw_body_narrows_schema_enum_and_zeroes_empty_or_unused():
    pre = _preanalysis(intent="definition", allowed_claim_ids=("CLAIM-FAKE-001",))
    raw = llm_adapter.build_raw_body(pre, (_card("CLAIM-FAKE-001"),), schema=SCHEMA)
    props = raw["response_format"]["json_schema"]["schema"]["properties"]
    assert props["reference_claim_ids"]["items"]["enum"] == ["CLAIM-FAKE-001"]
    assert props["mechanism_ids"].get("maxItems") == 0          # пусто — maxItems 0, не enum: []
    assert props["calculation_ids"].get("maxItems") == 0        # intent != calculation


def test_build_raw_body_response_format_is_wrapped_and_provider_safe():
    """Д17, живая проверка: `json_schema` — обёртка name/strict/schema (без неё DeepInfra — 422, Fireworks молча
    игнорирует схему); служебных ключей схемы и `uniqueItems` (грамматика DeepInfra его не реализует) в теле
    нет — уникальность перепроверяет сервер."""
    raw = llm_adapter.build_raw_body(_preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",)),
                                     (_card("CLAIM-FAKE-001"),), schema=SCHEMA)
    fmt = raw["response_format"]["json_schema"]
    assert set(fmt) == {"name", "strict", "schema"} and fmt["name"] == "llm_selection" and fmt["strict"] is True
    dumped = json.dumps(fmt["schema"])
    assert '"uniqueItems"' not in dumped and '"$schema"' not in dumped and '"$id"' not in dumped
    assert '"uniqueItems"' in json.dumps(SCHEMA)          # исходная схема контракта не тронута
    assert fmt["schema"]["required"] == SCHEMA["required"]
    assert fmt["schema"]["additionalProperties"] is False


def test_build_raw_body_allows_calculation_ids_when_intent_is_calculation():
    raw = llm_adapter.build_raw_body(_preanalysis(intent="calculation"), (), schema=SCHEMA)
    not_calc = SCHEMA["properties"]["calculation_ids"]["maxItems"]               # исходный предел схемы (4)
    assert raw["response_format"]["json_schema"]["schema"]["properties"]["calculation_ids"]["maxItems"] == not_calc


def test_build_raw_body_rejects_unsupported_export_policy():
    """`none` — модель не вызывается вовсе (сервер-уровневый выключатель `service.py`), сюда в обычной работе
    не доходит; `build_raw_body` всё равно отказывает ей явно, а не собирает тело без ограничения."""
    with pytest.raises(ValueError, match="llm_adapter.build_raw_body"):
        llm_adapter.build_raw_body(_preanalysis(llm_export_policy="none"), (), schema=SCHEMA)


# --- build_raw_body: approved_subset (решение владельца F6, 14.10.2026) — полный контент в сообщении ---

def test_build_raw_body_approved_subset_includes_question_and_card_text():
    pack = (_card("CLAIM-FAKE-001"),)
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",), llm_export_policy="approved_subset")
    raw = llm_adapter.build_raw_body(pre, pack, schema=SCHEMA, question="сколько сухого остатка?")
    user = json.loads(raw["messages"][1]["content"])
    assert user["question"] == "сколько сухого остатка?"
    assert user["cards"] == [{"id": "CLAIM-FAKE-001",
                              "text": f"Выдуманная карточка CLAIM-FAKE-001: {FAKE_TAG}.", "fragments": []}]
    # ответ модели по-прежнему ограничен той же схемой — только id, не текст (narrow по тем же claim_ids)
    assert raw["response_format"]["json_schema"]["schema"]["properties"]["reference_claim_ids"]["items"]["enum"] == \
        ["CLAIM-FAKE-001"]


def test_build_raw_body_approved_subset_includes_fragment_and_locator():
    ref = EvidenceRef(source_id="SRC-FAKE-001", fragment_id="FRAG-FAKE-001",
                      locator=PdfPageLocator(type="pdf_page", page=12), corpus_version="v1", limitation="")
    card = _card("CLAIM-FAKE-001", evidence_refs=(ref,))
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",), llm_export_policy="approved_subset")
    raw = llm_adapter.build_raw_body(pre, (card,), schema=SCHEMA)
    user = json.loads(raw["messages"][1]["content"])
    assert user["cards"][0]["fragments"] == [{"fragment_id": "FRAG-FAKE-001",
                                              "locator": {"type": "pdf_page", "page": 12}}]


def test_build_raw_body_approved_subset_excludes_card_not_in_allowed_pack():
    """Двойная сверка не слабее, чем у `public_only` (докстринг `build_raw_body`): карточка, которой нет среди
    `allowed_claim_ids`/`allowed_mechanism_ids` преданализа, не попадает в `cards`, даже если физически есть
    в `pack`."""
    pack = (_card("CLAIM-FAKE-001"), _card("CLAIM-FAKE-002"))
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",), llm_export_policy="approved_subset")
    raw = llm_adapter.build_raw_body(pre, pack, schema=SCHEMA)
    user = json.loads(raw["messages"][1]["content"])
    assert [c["id"] for c in user["cards"]] == ["CLAIM-FAKE-001"]


def test_build_raw_body_public_only_ignores_question_parameter():
    """`public_only` — `question`, даже если передан вызывающим, не попадает в тело (параметр используется
    только при `approved_subset`, докстринг `build_raw_body`)."""
    raw = llm_adapter.build_raw_body(_preanalysis(), (), schema=SCHEMA, question="секретный вопрос технолога")
    assert "секретный вопрос технолога" not in json.dumps(raw, ensure_ascii=False)
    user = json.loads(raw["messages"][1]["content"])
    assert "question" not in user and "cards" not in user


def test_build_raw_body_approved_subset_truncates_oversized_card_text_with_mark():
    """Предел размера тела (брифа F6, п.3) — текст карточки, который вместе с другими превысил бюджет,
    обрезается с явной пометкой, не теряется молча и не раздувает тело без ограничения."""
    huge_text = "А" * 100_000
    pack = (_card("CLAIM-FAKE-001", text=huge_text),)
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",), llm_export_policy="approved_subset")
    raw = llm_adapter.build_raw_body(pre, pack, schema=SCHEMA)
    user = json.loads(raw["messages"][1]["content"])
    card_text = user["cards"][0]["text"]
    assert len(card_text) < len(huge_text)
    assert card_text.endswith("обрезан — предел размера исходящего запроса]")
    assert len(json.dumps(raw, ensure_ascii=False)) < llm_adapter._APPROVED_SUBSET_BODY_CHAR_BUDGET + 5_000


def test_build_raw_body_approved_subset_small_card_text_not_truncated():
    pack = (_card("CLAIM-FAKE-001", text="короткий текст карточки"),)
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",), llm_export_policy="approved_subset")
    raw = llm_adapter.build_raw_body(pre, pack, schema=SCHEMA)
    user = json.loads(raw["messages"][1]["content"])
    assert user["cards"][0]["text"] == "короткий текст карточки"


def test_build_raw_body_sends_term_ids_not_original_question_word():
    term = ResolvedTerm(original="масса", candidate_ids=["TERM-FAKE-001", "TERM-FAKE-002"],
                        selected_id="TERM-FAKE-001", requires_confirmation=False)
    raw = llm_adapter.build_raw_body(_preanalysis(resolved_terms=(term,)), (), schema=SCHEMA)
    assert "масса" not in json.dumps(raw, ensure_ascii=False)
    user = json.loads(raw["messages"][1]["content"])
    assert user["resolved_term_ids"] == ["TERM-FAKE-001"]


def test_build_raw_body_and_select_model_make_no_network_calls(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    llm_adapter.build_raw_body(_preanalysis(), (), schema=SCHEMA)
    llm_adapter.select_model(role, "")
    assert fake_or.requests == []


# --- select_model: пересечение role.models × ANALYTICS_LLM_MODELS, порядок CANDIDATES ---

def test_select_model_picks_first_by_candidates_order_not_llm_models_order(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or, LLM_MODELS="openai/gpt-6-luna,z-ai/glm-5.3")
    assert llm_adapter.select_model(role, "").model == "z-ai/glm-5.3"


def test_select_model_restricted_by_analytics_models(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)       # все 7 кандидатов роли
    assert llm_adapter.select_model(role, "openai/gpt-6-luna").model == "openai/gpt-6-luna"


def test_select_model_empty_intersection_refuses(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or, LLM_MODELS="z-ai/glm-5.3")
    with pytest.raises(LlmAdapterRefusal) as exc:
        llm_adapter.select_model(role, "openai/gpt-6-luna")
    assert exc.value.reason == "model"


def test_select_model_bad_analytics_models_string_refuses(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    with pytest.raises(LlmAdapterRefusal) as exc:
        llm_adapter.select_model(role, "unknown/model")
    assert exc.value.reason == "model"


# --- request_selection: отказы допуска — ничего не отправлено наружу ---

async def test_request_selection_config_error_refuses_before_any_call(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    role.config_error = "сломано"
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, STUDENT, "conv-1", _raw_body(), analytics_models="")
    assert exc.value.reason == "config"
    assert fake_or.requests == []


async def test_request_selection_stopped_refuses(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or, LLM_STATE="stopped")
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, STUDENT, "conv-1", _raw_body(), analytics_models="")
    assert exc.value.reason == "stopped"
    assert fake_or.requests == []


async def test_request_selection_no_upstream_key_refuses(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or, key=None)
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, STUDENT, "conv-1", _raw_body(), analytics_models="")
    assert exc.value.reason == "key_missing"
    assert fake_or.requests == []


async def test_request_selection_ledger_error_refuses_before_reserve(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    role.ledger_error = lambda: "write"
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, STUDENT, "conv-1", _raw_body(), analytics_models="")
    assert exc.value.reason == "ledger"
    assert fake_or.requests == []
    assert role.inflight == {}


async def test_request_selection_busy_refuses_without_extra_reserve(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    role.inflight[STUDENT.caller] = role.llm.LLM_CALLER_CONCURRENCY
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, STUDENT, "conv-1", _raw_body(), analytics_models="")
    assert exc.value.reason == "busy"
    assert fake_or.requests == []
    assert role.inflight[STUDENT.caller] == role.llm.LLM_CALLER_CONCURRENCY      # не выросло вторым резервом


async def test_request_selection_budget_refusal_reason_propagates_and_releases_reserve(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    role.budget.refusal = lambda caller: "share_spent"
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, STUDENT, "conv-1", _raw_body(), analytics_models="")
    assert exc.value.reason == "share_spent"
    assert fake_or.requests == []
    assert role.inflight == {}


async def test_request_selection_teacher_without_payer_refuses_before_config_check(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    role.config_error = "не должно быть видно — отказ должен быть раньше, по преподавателю"
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, TEACHER, "conv-1", _raw_body(), analytics_models="")
    assert exc.value.reason == "teacher_payer"
    assert fake_or.requests == []


async def test_request_selection_teacher_with_payer_configured_proceeds_and_bills_teacher_caller(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",))
    raw_body = _raw_body(pre, (_card("CLAIM-FAKE-001"),))

    async def ok(body):
        return _success_response({"reference_claim_ids": ["CLAIM-FAKE-001"], "mechanism_ids": [],
                                  "clarification_keys": [], "calculation_ids": []})

    role.upstream.open_chat = ok
    selection = await llm_adapter.request_selection(role, TEACHER, "conv-1", raw_body, analytics_models="",
                                                    teacher_payer="ops")
    assert selection.reference_claim_ids == ["CLAIM-FAKE-001"]
    assert role.ledger.recent(1)[0].caller == "teacher"      # свой вызывающий, не student/ops молчком


# --- request_selection: успех — разбор LlmSelection, тело без закрытого содержания ---

async def test_request_selection_success_parses_and_sends_only_ids(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",))
    raw_body = _raw_body(pre, (_card("CLAIM-FAKE-001"),))
    sent = []

    async def ok(body):
        sent.append(body)
        return _success_response({"reference_claim_ids": ["CLAIM-FAKE-001"], "mechanism_ids": [],
                                  "clarification_keys": [], "calculation_ids": []})

    role.upstream.open_chat = ok
    selection = await llm_adapter.request_selection(role, STUDENT, "conv-1", raw_body, analytics_models="")
    assert selection.reference_claim_ids == ["CLAIM-FAKE-001"]
    sent_text = json.dumps(sent[0], ensure_ascii=False)
    assert FAKE_TAG not in sent_text and "Выдуманная карточка" not in sent_text
    assert sent[0]["stream"] is False
    row = role.ledger.recent(1)[0]
    assert (row.status, row.cost_source, row.caller) == ("ok", "body", "student-01")


async def test_request_selection_invalid_json_content_refused(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)

    async def bad_content(body):
        return httpx.Response(200, json={"id": "g", "choices": [{"index": 0,
                              "message": {"role": "assistant", "content": "просто текст, не JSON"},
                              "finish_reason": "stop"}]}, headers={"x-generation-id": "g"})

    role.upstream.open_chat = bad_content
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, STUDENT, "conv-1", _raw_body(), analytics_models="")
    assert exc.value.reason == "invalid_selection"


async def test_request_selection_extra_field_bypass_attempt_refused(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",))
    raw_body = _raw_body(pre, (_card("CLAIM-FAKE-001"),))

    async def extra_field(body):
        return _success_response({"reference_claim_ids": ["CLAIM-FAKE-001"], "mechanism_ids": [],
                                  "clarification_keys": [], "calculation_ids": [], "local_norm": "обход"})

    role.upstream.open_chat = extra_field
    with pytest.raises(LlmAdapterRefusal) as exc:
        await llm_adapter.request_selection(role, STUDENT, "conv-1", raw_body, analytics_models="")
    assert exc.value.reason == "invalid_selection"


async def test_request_selection_then_validator_rejects_unknown_id(tmp_path, fake_or):
    """Схема сужает enum под пакет, но сервер ей не доверяет: id вне пакета, просочившийся мимо провайдера
    (двойник response_format не проверяет), всё равно отклоняет `validator.validate_selection` — не повтор его
    матрицы, один сквозной пример (мастер-промпт §8: «наличие ссылки недостаточно»)."""
    role = make_llm_role(tmp_path, fake_or)
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",))
    pack = (_card("CLAIM-FAKE-001"),)
    raw_body = _raw_body(pre, pack)

    async def unknown_id(body):
        return _success_response({"reference_claim_ids": ["CLAIM-FAKE-999"], "mechanism_ids": [],
                                  "clarification_keys": [], "calculation_ids": []})

    role.upstream.open_chat = unknown_id
    selection = await llm_adapter.request_selection(role, STUDENT, "conv-1", raw_body, analytics_models="")
    outcome = validator.validate_selection(selection, pre, pack)
    assert outcome.accepted_claim_ids == ()
    assert outcome.rejected == (("CLAIM-FAKE-999", "id не входит в разрешённый пакет преданализа"),)


# --- request_selection: один резерв/запасная модель/отмена — по прежней логике forward() ---

async def test_request_selection_fallback_triggers_and_succeeds_on_retryable_kind(tmp_path, fake_or):
    primary, fallback_name = "z-ai/glm-5.3", "z-ai/glm-5.3-flash"
    role = make_llm_role(tmp_path, fake_or, LLM_FALLBACK=f"{primary}={fallback_name}")
    pre = _preanalysis(allowed_claim_ids=("CLAIM-FAKE-001",))
    raw_body = _raw_body(pre, (_card("CLAIM-FAKE-001"),))
    seen = []

    async def once_rate_limited(body):
        seen.append(body["model"])
        if body["model"] == primary:
            raise UpstreamError("rate", 429, "занято", retry_after="1")
        return _success_response({"reference_claim_ids": ["CLAIM-FAKE-001"], "mechanism_ids": [],
                                  "clarification_keys": [], "calculation_ids": []})

    role.upstream.open_chat = once_rate_limited
    selection = await llm_adapter.request_selection(role, STUDENT, "conv-1", raw_body, analytics_models="")
    assert seen == [primary, fallback_name]
    assert selection.reference_claim_ids == ["CLAIM-FAKE-001"]
    rows = role.ledger.recent(2)
    assert (rows[0].model, rows[0].status, rows[0].cost_source) == (fallback_name, "ok", "body")
    assert (rows[1].model, rows[1].status, rows[1].code) == (primary, "upstream_error", "pcbk_upstream_wait")
    assert role.inflight == {}


async def test_request_selection_cancelled_mid_call_releases_reserve(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    started = asyncio.Event()

    async def hangs(body):
        started.set()
        await asyncio.sleep(10)
        raise AssertionError("не должно быть достигнуто — задача отменяется раньше")

    role.upstream.open_chat = hangs
    task = asyncio.ensure_future(
        llm_adapter.request_selection(role, STUDENT, "conv-1", _raw_body(), analytics_models=""))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert role.inflight == {}                      # резерв освобождён, не висит после отмены


# --- ANALYTICS_TEACHER_PAYER (settings.py, Task 5) ---

def test_analytics_teacher_payer_setting_defaults_empty_and_trims():
    assert AnalyticsSettings().ANALYTICS_TEACHER_PAYER == ""
    assert AnalyticsSettings.from_env({}).ANALYTICS_TEACHER_PAYER == ""
    assert AnalyticsSettings.from_env({"ANALYTICS_TEACHER_PAYER": "  ops  "}).ANALYTICS_TEACHER_PAYER == "ops"


# --- дополнение контроллера: main.py подключает llm=... в AnalyticsService (Task 5, build_roles) ---

def test_capabilities_llm_available_true_only_when_role_healthy_not_for_teacher(tmp_path):
    """`main.build_roles` передаёт в `AnalyticsService` ту же роль `llm`, что у шлюза/калькуляторов — не
    заглушку `llm=None`. `capabilities().llm_available` при этом не обещает больше, чем реально есть: true
    только пока роль здорова (ключ есть, не `stopped`, нет `config_error`), и всегда false для `teacher` без
    явно настроенного плательщика (мастер-промпт §6, `llm_adapter.py`), даже если роль формально здорова."""
    from dataclasses import replace

    from fixtures.kdm1_corpus import build_corpus
    from helpers import LLM_ENV, SETTINGS, write
    from pcbk_core.main import build_roles

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    build_corpus(corpus_dir / "fake.seed.sqlite3")
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"))
    base_env = {**LLM_ENV, "ANALYTICS_ENABLED": "1", "ANALYTICS_CORPUS_DIR": str(corpus_dir),
               "ANALYTICS_STATE_PATH": str(tmp_path / "analytics.db")}
    student = RequestIdentity(login="stu-01", role="student", place=1, caller="student-01")
    teacher = RequestIdentity(login="teach", role="teacher", place=None, caller="teacher")

    key = write(tmp_path, "test-openrouter-key\n")
    healthy = {r.name: r for r in build_roles(settings, {**base_env, "OPENROUTER_KEY_FILE": key})}
    svc = healthy["gateway"].analytics
    assert svc is not None and svc.llm is healthy["llm"] and healthy["llm"].upstream is not None
    assert svc.capabilities(student).llm_available is True
    assert svc.capabilities(teacher).llm_available is False      # §6: без плательщика — всегда недоступно

    broken = {r.name: r for r in build_roles(settings, {**base_env, "OPENROUTER_KEY_FILE": str(tmp_path)})}
    broken_svc = broken["gateway"].analytics
    assert broken["llm"].config_error is not None
    assert broken_svc.capabilities(student).llm_available is False
