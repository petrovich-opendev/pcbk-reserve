"""Закрытые модели контрактов и внутренние структуры аналитики КДМ-1 (Task 0; план §Task 0; мастер-промпт
§7–§10). Полная реализация (не заглушка): все следующие задачи (1–9) собирают свои объекты только через эти
типы, чтобы интерфейсы между волнами не расходились.

Публичные модели (pydantic, `extra="forbid"`) — зеркало схем `contracts/` (копии лежат рядом, в
`analytics/contracts/*.schema.json`; `CONTRACT_SCHEMAS` ниже — какой файл проверяет какую модель):
- `AnalysisRequest`  ⇄ analysis-request.schema.json — вопрос технолога (клиент → `service.py`);
- `Preanalysis`      ⇄ preanalysis.schema.json       — результат координатора (`preanalysis.py` → `service.py`);
- `LlmSelection`      ⇄ llm-selection.schema.json      — предложение модели (`llm_adapter.py` → `validator.py`);
- `JobResponse`       ⇄ job-response.schema.json       — ответ клиенту (`rendering.py` → `service.py` → API).

В отличие от калькуляторов страницы (`data/http_api.py`, `gateway/calc_api.py`), где лишние поля тела тихо
отбрасываются (модель аргументов их просто не объявляет — подменить `place/caller` всё равно нельзя), контракты
аналитики требуют именно отказа на лишнем поле (мастер-промпт §10: «клиент не задаёт роль, владельца, системный
промпт, полный пакет или статус согласования») — отсюда `extra="forbid"` у каждой модели здесь, а не умолчание
pydantic (`ignore`).

Внутренние структуры (frozen dataclasses — как `Account`, `LessonMeta` в проекте; это не контракт с клиентом,
pydantic им не нужен):
- `RequestIdentity` — личность вызывающего только из серверной сессии (ОСОБЫЙ ФОКУС ПРОВЕРКИ №1 плана:
  `place/caller/login/owner/role` из тела запроса запрещены; собирает `service.py` из `Account`
  `pcbk_core.gateway.accounts`, а не из `AnalysisRequest`);
- `EvidenceCard` — карточка доказательства из корпуса (`knowledge.py`/`retrieval.py`) до отображения;
- `CalculationResult` — вычисление `calculations.py` с явными входами/единицами и допущениями;
- `ValidationOutcome` — результат `validator.py` над `LlmSelection`: что разрешено показать и что отклонено.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# --- словари значений: зеркало enum'ов из contracts/*.schema.json ---

AnalysisMode = Literal["reference", "deviation_review"]
Intent = Literal["definition", "lookup", "calculation", "deviation", "control", "out_of_scope"]
Decision = Literal["ready", "needs_clarification", "limited_answer", "refused"]
Risk = Literal["reference", "calculation", "local_claim", "control"]
LlmExportPolicy = Literal["public_only", "approved_subset", "none"]
ClarificationKey = Literal["quantity", "process", "unit", "basis", "period", "timezone", "grade",
                           "measurement_quality", "source_applicability"]
CalculationId = Literal["dry_solid_flow", "geometric_output", "dry_grammage", "interval_union"]
JobState = Literal["queued", "preanalysis", "retrieving", "generating", "validating", "completed",
                   "needs_clarification", "limited_answer", "cancelled", "failed"]
SectionKind = Literal["document_observation", "public_definition", "calculation", "qualified_hypothesis",
                      "limitation", "clarification"]
ErrorCode = Literal["unavailable", "invalid_input", "policy_refused", "source_conflict", "validation_failed",
                    "interrupted", "busy"]

# задание не меняется дальше (store.py, Task 2: неизменяемый пакет доказательств публикуется атомарно с ответом)
JOB_TERMINAL_STATES = frozenset({"completed", "needs_clarification", "limited_answer", "cancelled", "failed"})

# короткие и длинные идентификаторы корпуса/заданий — одни границы на весь модуль (contracts/*.schema.json)
Id100 = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Id250 = Annotated[str, StringConstraints(min_length=1, max_length=250)]


def _unique(items: list) -> None:
    """uniqueItems схемы: повтор — ValueError (model_validator оборачивает её в ValidationError)."""
    seen = []
    for item in items:
        if item in seen:
            raise ValueError("повтор id в списке")
        seen.append(item)


class _Closed(BaseModel):
    """Общая настройка закрытых контрактов: лишнее поле — отказ (мастер-промпт §10), а не тихий отброс."""

    model_config = ConfigDict(extra="forbid")


# --- analysis-request.schema.json ---

class PeriodWindow(_Closed):
    start: datetime
    end: datetime


class RequestContext(_Closed):
    process_id: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    period: PeriodWindow | None = None
    unit_hint: Annotated[str, StringConstraints(min_length=1, max_length=40)] | None = None


class AnalysisRequest(_Closed):
    """Вопрос технолога с клиента (POST .../conversations/{id}/questions). Идентичность, область КДМ-1, модель,
    пакет и права клиент не передаёт — их добавляет `service.py` из серверной сессии (`RequestIdentity`),
    никогда из этого объекта."""

    client_request_id: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    question: Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    mode: AnalysisMode
    context: RequestContext | None = None


# --- preanalysis.schema.json ---

class ResolvedTerm(_Closed):
    original: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    candidate_ids: Annotated[list[Id100], Field(max_length=10)]
    selected_id: Id100 | None
    requires_confirmation: bool

    @model_validator(mode="after")
    def _check_candidates(self) -> "ResolvedTerm":
        _unique(self.candidate_ids)
        return self


class Preanalysis(_Closed):
    """Единственный достоверный результат координатора (мастер-промпт §7). Схема не доказывает смысл, права,
    размерность или истинность сама по себе — это отдельные проверки `preanalysis.py` (Task 3) и прав владения
    `store.py` (Task 2), не свойство самого объекта."""

    scope: Literal["KDM1_PCBK"]
    intent: Intent
    decision: Decision
    resolved_terms: Annotated[list[ResolvedTerm], Field(max_length=40)]
    process_ids: Annotated[list[Id100], Field(max_length=32)]
    conflict_ids: Annotated[list[Id100], Field(max_length=32)]
    missing_fields: Annotated[list[Id100], Field(max_length=32)]
    risk: Risk
    corpus_version: Id100
    evidence_pack_id: Id100 | None
    allowed_claim_ids: Annotated[list[Id250], Field(max_length=48)]
    allowed_mechanism_ids: Annotated[list[Id100], Field(max_length=12)]
    data_access_ready: bool
    llm_export_policy: LlmExportPolicy

    @model_validator(mode="after")
    def _check_unique(self) -> "Preanalysis":
        for name in ("process_ids", "conflict_ids", "missing_fields", "allowed_claim_ids", "allowed_mechanism_ids"):
            _unique(getattr(self, name))
        _unique([t.model_dump_json() for t in self.resolved_terms])
        return self


# --- llm-selection.schema.json ---

class LlmSelection(_Closed):
    """Предложение модели (мастер-промпт §8): «только предложение выбора» — все id сервер сверяет с
    разрешённым пакетом (`validator.py`, Task 3/5); сама модель не подтверждает права, смысл и размерность."""

    reference_claim_ids: Annotated[list[Id250], Field(max_length=20)]
    mechanism_ids: Annotated[list[Id100], Field(max_length=6)]
    clarification_keys: Annotated[list[ClarificationKey], Field(max_length=9)]
    calculation_ids: Annotated[list[CalculationId], Field(max_length=4)]

    @model_validator(mode="after")
    def _check_unique(self) -> "LlmSelection":
        for name in ("reference_claim_ids", "mechanism_ids", "clarification_keys", "calculation_ids"):
            _unique(getattr(self, name))
        return self


# --- job-response.schema.json ---

class PdfPageLocator(_Closed):
    type: Literal["pdf_page"]
    page: Annotated[int, Field(ge=1, le=10000)]


class XlsxCellLocator(_Closed):
    type: Literal["xlsx_cell"]
    sheet: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    cell: Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")]


class ImageLabelLocator(_Closed):
    type: Literal["image_label"]
    file: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    region_pixels: Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=4, max_length=4)] | None


class PublicSectionLocator(_Closed):
    type: Literal["public_section"]
    url: Annotated[str, StringConstraints(pattern=r"^https://")]
    section: Annotated[str, StringConstraints(min_length=1, max_length=250)]


# oneOf схемы, различается по "type" — discriminator избавляет от перебора всех четырёх веток на ошибке
Locator = Annotated[
    PdfPageLocator | XlsxCellLocator | ImageLabelLocator | PublicSectionLocator,
    Field(discriminator="type"),
]


class EvidenceRef(_Closed):
    source_id: Id100
    fragment_id: Id250 | None
    locator: Locator
    corpus_version: Id100
    limitation: Annotated[str, StringConstraints(min_length=0, max_length=500)]


class Section(_Closed):
    kind: SectionKind
    text: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    evidence_refs: Annotated[list[EvidenceRef], Field(max_length=12)]
    rendered_by: Literal["server"]
    claim_id: Id250 | None

    @model_validator(mode="after")
    def _check_unique(self) -> "Section":
        _unique([r.model_dump_json() for r in self.evidence_refs])
        return self


class JobResponse(_Closed):
    """Единственный публичный результат (мастер-промпт §8, §10): до завершения проверки `sections` пуст; сырые
    ответы модели, системный промпт, внутренние классы и рассуждения сюда не попадают ни при каком состоянии."""

    request_id: Id100
    conversation_id: Id100
    state: JobState
    corpus_version: Id100
    stage_label: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    sections: Annotated[list[Section], Field(max_length=24)]
    clarification_keys: Annotated[list[Id100], Field(max_length=12)]
    error_code: ErrorCode | None

    @model_validator(mode="after")
    def _check_unique(self) -> "JobResponse":
        _unique([s.model_dump_json() for s in self.sections])
        _unique(self.clarification_keys)
        return self


# модель → имя файла схемы в analytics/contracts/ (test_analytics_contracts.py проверяет оба в паре)
CONTRACT_SCHEMAS: dict[type[BaseModel], str] = {
    AnalysisRequest: "analysis-request.schema.json",
    Preanalysis: "preanalysis.schema.json",
    LlmSelection: "llm-selection.schema.json",
    JobResponse: "job-response.schema.json",
}


# --- внутренние структуры: не контракт с клиентом, frozen dataclass (как Account, LessonMeta проекта) ---

@dataclass(frozen=True)
class RequestIdentity:
    """Личность вызывающего — только из серверной сессии (ОСОБЫЙ ФОКУС ПРОВЕРКИ №1 плана; мастер-промпт §6:
    «place/caller/login/owner/role из тела запроса запрещены»). `service.py` строит этот объект из `Account`
    (`pcbk_core.gateway.accounts`), а не из `AnalysisRequest`. `caller` — тот же вид, что у калькуляторов
    страницы (`gateway.calc_api.caller_for`): `student-NN` по месту учётки или `teacher` для преподавателя;
    LLM видит только `caller` (мастер-промпт §6: «для LLM сохраняется student-NN»), не логин."""

    login: str
    role: Literal["student", "teacher"]
    place: int | None
    caller: str


@dataclass(frozen=True)
class EvidenceCard:
    """Карточка доказательства из корпуса (`knowledge.py`/`retrieval.py`, Task 1) до серверного отображения
    (`rendering.py`, Task 3). Поля — зеркало `claims.jsonl`/`mechanisms.jsonl` пакета заказчика (`id, kind, text,
    risk_level, publication_state, allowed_render_mode, applicability, critical_local_fact, evidence_refs`), не
    копия самого корпуса целиком. `applicability` — действующая область/срок (мастер-промпт §8: «местный
    критический факт требует согласованной карточки с действующей областью/сроком»)."""

    id: str
    kind: Literal["claim", "mechanism"]
    text: str
    risk_level: str
    publication_state: str
    allowed_render_mode: str
    applicability: str
    critical_local_fact: bool
    evidence_refs: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class CalculationResult:
    """Вычисление `calculations.py` (Task 3; мастер-промпт §8): явные входы/единицы и допущения — числитель и
    знаменатель независимой проверки (ОСОБЫЙ ФОКУС ПРОВЕРКИ плана: эталон — не вызов той же функции).
    `missing_basis` — какой базы не хватает (например, «неизвестна плотность») — тогда `value` — `None`, и
    ответ уточняет, а не угадывает допущение."""

    calculation_id: CalculationId
    inputs: tuple[tuple[str, float, str], ...]      # (имя, значение, единица) — порядок важен для текста формулы
    value: float | None
    unit: str | None
    assumptions: tuple[str, ...]
    missing_basis: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationOutcome:
    """Результат `validator.py` (Task 3, доп. Task 5) над `LlmSelection`: что сервер разрешает показать клиенту
    и что отклонил и почему (мастер-промпт §8: «каждый идентификатор проверяется»; ОСОБЫЙ ФОКУС ПРОВЕРКИ №4 —
    валидная ссылка с ложным смыслом блокируется независимо от наличия источника)."""

    accepted_claim_ids: tuple[str, ...]
    accepted_mechanism_ids: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]           # (id из LlmSelection, причина отказа)
    clarification_keys: tuple[ClarificationKey, ...]
    refused: bool
    error_code: ErrorCode | None = None
