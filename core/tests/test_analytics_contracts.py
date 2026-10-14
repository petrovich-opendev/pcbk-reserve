"""Модели ⇄ схемы аналитики КДМ-1 (план КДМ1, Task 0): `analytics/models.py` принимает те же фикстуры, что
были подготовлены для JSON Schema в пакете заказчика (`request-valid.json`, `request-invalid-owner.json`,
`selection-valid.json`, `selection-invalid-prose.json` — скопированы ниже как инлайн-данные теста, без зависимости
от каталога вне репозитория: тексты в них — демонстрационные, без тегов ПЦБК), и отвергает любое лишнее поле
(`extra="forbid"`, мастер-промпт §10) — не только у `AnalysisRequest`/`LlmSelection`, но и у `Preanalysis`/
`JobResponse`, для которых готовых фикстур в пакете не было.

Копии самих схем лежат в `analytics/contracts/*.schema.json` (сверены на отсутствие имён тегов при копировании,
`CONTRACT_SCHEMAS` в `models.py` — какая модель какой файл зеркалит); jsonschema не используется (новых
зависимостей нет — `jsonschema` в `requirements.lock` только транзитивная зависимость `mcp`, не прямая, Rules
плана), проверка схем — через сами закрытые модели pydantic."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pcbk_core.analytics.models import (CONTRACT_SCHEMAS, AnalysisRequest, JobResponse, LlmSelection,
                                        Preanalysis)

CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "pcbk_core" / "analytics" / "contracts"

# --- копии фикстур пакета заказчика (contracts/fixtures/*.json) — тексты демонстрационные, без тегов ПЦБК ---

REQUEST_VALID = {"client_request_id": "demo-001", "question": "Что означает масса квадратного метра?",
                 "mode": "reference", "context": {"process_id": None}}
REQUEST_INVALID_OWNER = {"client_request_id": "demo-002", "question": "Покажи историю", "mode": "reference",
                         "owner": "another-user"}
SELECTION_VALID = {"reference_claim_ids": ["DEF-basis-weight"], "mechanism_ids": [], "clarification_keys": [],
                   "calculation_ids": []}
SELECTION_INVALID_PROSE = {**SELECTION_VALID, "recommendation": "Измените давление"}

PREANALYSIS_VALID = {
    "scope": "KDM1_PCBK", "intent": "definition", "decision": "ready", "resolved_terms": [],
    "process_ids": [], "conflict_ids": [], "missing_fields": [], "risk": "reference",
    "corpus_version": "test-v1", "evidence_pack_id": None, "allowed_claim_ids": [], "allowed_mechanism_ids": [],
    "data_access_ready": False, "llm_export_policy": "public_only",
}

JOB_RESPONSE_VALID = {
    "request_id": "r1", "conversation_id": "c1", "state": "completed", "corpus_version": "test-v1",
    "stage_label": "Готово",
    "sections": [{
        "kind": "document_observation", "text": "В предоставленной модели указано: …",
        "evidence_refs": [{
            "source_id": "S1", "fragment_id": "F1", "corpus_version": "test-v1", "limitation": "",
            "locator": {"type": "pdf_page", "page": 3},
        }],
        "rendered_by": "server", "claim_id": "C1",
    }],
    "clarification_keys": [], "error_code": None,
}


def test_contract_schema_files_exist_and_tag_free():
    """Копии схем на месте, по одной на модель, и в них нет ни одного имени тега вида ПЦБК (ворота плана:
    `\\b[0-9]{2}[A-Z]{2,4}[0-9]{2}`)."""
    import re

    tag_re = re.compile(r"\b[0-9]{2}[A-Z]{2,4}[0-9]{2}\b")
    for model, filename in CONTRACT_SCHEMAS.items():
        path = CONTRACTS_DIR / filename
        text = path.read_text(encoding="utf-8")
        assert json.loads(text)["$id"]                 # валидный JSON Schema файл
        assert tag_re.search(text) is None, (model, filename)


@pytest.mark.parametrize("data", [REQUEST_VALID])
def test_analysis_request_valid(data):
    req = AnalysisRequest.model_validate(data)
    assert req.client_request_id == "demo-001"
    assert req.mode == "reference"
    assert req.context is not None and req.context.process_id is None


@pytest.mark.parametrize("data", [
    REQUEST_INVALID_OWNER,                                          # лишнее поле "owner" — identity не от клиента
    {**REQUEST_VALID, "mode": "control"},                           # mode вне enum схемы
    {**REQUEST_VALID, "client_request_id": ""},                     # minLength 1
    {k: v for k, v in REQUEST_VALID.items() if k != "question"},    # обязательное поле пропущено
    {**REQUEST_VALID, "context": {"process_id": None, "place": 3}},  # лишнее поле во вложенном объекте
])
def test_analysis_request_invalid(data):
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate(data)


def test_llm_selection_valid():
    sel = LlmSelection.model_validate(SELECTION_VALID)
    assert sel.reference_claim_ids == ["DEF-basis-weight"]


@pytest.mark.parametrize("data", [
    SELECTION_INVALID_PROSE,                                        # лишнее поле "recommendation" — свободная проза
    {**SELECTION_VALID, "clarification_keys": ["not_a_real_key"]},  # вне enum ClarificationKey
    {**SELECTION_VALID, "calculation_ids": ["dry_solid_flow", "dry_solid_flow"]},  # uniqueItems
    {k: v for k, v in SELECTION_VALID.items() if k != "mechanism_ids"},            # обязательное поле пропущено
])
def test_llm_selection_invalid(data):
    with pytest.raises(ValidationError):
        LlmSelection.model_validate(data)


def test_preanalysis_valid():
    p = Preanalysis.model_validate(PREANALYSIS_VALID)
    assert p.decision == "ready"
    assert p.llm_export_policy == "public_only"


@pytest.mark.parametrize("data", [
    {**PREANALYSIS_VALID, "scope": "BDM2_PCBK"},                      # const "KDM1_PCBK" — чужая машина
    {**PREANALYSIS_VALID, "llm_export_policy": "everything"},         # вне enum
    {**PREANALYSIS_VALID, "process_ids": ["P1", "P1"]},                # uniqueItems
    {**PREANALYSIS_VALID, "extra_field": 1},                          # закрытость
    {k: v for k, v in PREANALYSIS_VALID.items() if k != "evidence_pack_id"},  # обязательное (хоть и nullable)
])
def test_preanalysis_invalid(data):
    with pytest.raises(ValidationError):
        Preanalysis.model_validate(data)


def test_job_response_valid():
    job = JobResponse.model_validate(JOB_RESPONSE_VALID)
    assert job.state == "completed"
    assert job.sections[0].evidence_refs[0].locator.page == 3


@pytest.mark.parametrize("data", [
    {**JOB_RESPONSE_VALID, "state": "running"},                       # вне enum JobState
    {**JOB_RESPONSE_VALID, "raw_model_output": "…"},                  # закрытость: сырой ответ модели не пройдёт
])
def test_job_response_invalid_top_level(data):
    with pytest.raises(ValidationError):
        JobResponse.model_validate(data)


def test_job_response_invalid_evidence_locator_extra_field():
    bad = json.loads(json.dumps(JOB_RESPONSE_VALID))
    bad["sections"][0]["evidence_refs"][0]["locator"] = {"type": "pdf_page", "page": 3, "note": "left in by LLM"}
    with pytest.raises(ValidationError):
        JobResponse.model_validate(bad)


def test_job_response_invalid_locator_wrong_variant_fields():
    bad = json.loads(json.dumps(JOB_RESPONSE_VALID))
    bad["sections"][0]["evidence_refs"][0]["locator"] = {"type": "xlsx_cell", "page": 3}  # нет sheet/cell
    with pytest.raises(ValidationError):
        JobResponse.model_validate(bad)


def test_job_response_invalid_duplicate_clarification_keys():
    bad = json.loads(json.dumps(JOB_RESPONSE_VALID))
    bad["clarification_keys"] = ["unit", "unit"]
    with pytest.raises(ValidationError):
        JobResponse.model_validate(bad)


# --- план Д17, Task 1: telemetry_observation — SectionKind и enum схемы не расходятся ---

def test_job_response_accepts_telemetry_observation_section_kind():
    """`kind="telemetry_observation"` (план Д17, Task 1) принимается моделью и зеркалит `evidence_refs=[]`,
    `claim_id=None`, `rendered_by="server"` — как у прочих серверных ограничений/наблюдений, не контракт
    заказчика (мастер-промпт §7 п.7: источник телеметрии — служба данных, не корпус)."""
    body = json.loads(json.dumps(JOB_RESPONSE_VALID))
    body["sections"] = [{
        "kind": "telemetry_observation",
        "text": "Данные историана по подписи 20FAKE_001_PV (тег 20FAKE_001_PV; связь согласовал tech1, "
               "01.01.2026): 12,5 л/час на 2026-10-14T08:00:00+00:00 — время историана (Europe/Moscow); "
               "качество: good. Это измеренное значение на момент ответа, не норматив и не уставка.",
        "evidence_refs": [], "rendered_by": "server", "claim_id": None,
    }]
    job = JobResponse.model_validate(body)
    assert job.sections[0].kind == "telemetry_observation"


def test_job_response_schema_enum_includes_telemetry_observation():
    """Копия схемы (`contracts/job-response.schema.json`) и модель (`models.SectionKind`) несут один и тот же
    набор значений — синхронность, а не только то, что модель принимает значение."""
    schema = json.loads((CONTRACTS_DIR / CONTRACT_SCHEMAS[JobResponse]).read_text(encoding="utf-8"))
    schema_kinds = set(schema["properties"]["sections"]["items"]["properties"]["kind"]["enum"])
    from typing import get_args
    from pcbk_core.analytics.models import SectionKind
    assert schema_kinds == set(get_args(SectionKind)) == {
        "document_observation", "public_definition", "calculation", "qualified_hypothesis", "limitation",
        "clarification", "telemetry_observation",
    }
