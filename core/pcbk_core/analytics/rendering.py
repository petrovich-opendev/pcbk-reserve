"""Серверное отображение (Task 3; план §Task 3/Этап 3; мастер-промпт §6, §8).

Единственное место, которое строит `models.Section`/`models.JobResponse` — произвольная модельная проза сюда не
попадает мимо проверки (мастер-промпт §8: «свободная проза LLM не должна обходить ограничения через заключение
или рекомендации»; §6: «служебные JSON, внутренние классы и рассуждения модели пользователю не показываются»).
Каждая секция — один из фиксированных серверных шаблонов по `SectionKind`, не передача текста модели как есть:

- `document_observation` — буквальное наблюдение документа/снимка, формулировка начинается с «В предоставленной
  модели указано: …» или «На снимке указано: …» (мастер-промпт §8) — не «сейчас» и не текущее состояние машины;
- `public_definition` — определение из допустимого публичного источника (не PUB-10/PUB-11 без отдельного
  основания, мастер-промпт §5);
- `calculation` — вход, формула и допущения расчёта (`CalculationResult`), не только число;
- `qualified_hypothesis` — «общий возможный механизм; причина на КДМ-1 не установлена» (мастер-промпт §8) —
  шаблон, не свободный текст модели о причине;
- `limitation` — чего не хватает (единица/период/подтверждение) или что отклонено проверкой;
- `clarification` — адресный вопрос технологу (участок/единица/период/сорт), без оплаченного вызова.

Местный критический факт (`EvidenceCard.critical_local_fact`) показывается только если у него есть
согласованная карточка с действующей областью/сроком (мастер-промпт §8) — без неё этот модуль не рендерит
секцию `document_observation`/`public_definition` для такого факта вовсе, а не рендерит её с предупреждением:
вызывающий (`service.py`) должен получить `limited_answer`/`needs_clarification` раньше, чем дойдёт сюда.

Реализация (Task 3b).

- `render_claim_section` различает `document_observation`/`public_definition` по `card.allowed_render_mode`
  ("attributed_document_literal"/"attributed_reference"), не по `card.kind` (у `EvidenceCard.kind` только два
  значения — "claim"/"mechanism" — это таблица корпуса, не подвид карточки `claims.jsonl.kind`
  "definition"/"document_label"; подвид и так однозначно определяет `allowed_render_mode` настоящего корпуса,
  это и есть сигнал, под каким шаблоном карточку разрешено показывать — имя поля в самом корпусе). Неизвестный
  `allowed_render_mode` — `ValueError`, не угадывание шаблона.
- Выбор префикса `document_observation` («В предоставленной модели указано: » / «На снимке указано: ») — по
  типу локатора среди `card.evidence_refs`: `image_label` (снимок SCADA) → «На снимке …», иначе (`pdf_page`/
  `xlsx_cell`/нет ссылок) → «В предоставленной модели …».
- Защита критического местного факта (докстринг модуля выше) повторена и здесь, а не только в `validator.py`:
  если по ошибке вызывающего код дошёл до рендеринга несогласованной `critical_local_fact=True` карточки,
  `render_claim_section` отказывает (`ValueError`), а не рендерит с предупреждением — общее состояние
  `validator.APPROVED_CRITICAL_FACT_STATES` (одно место истины для обоих модулей).
- `render_mechanism_section` добавляет `HYPOTHESIS_TEMPLATE` к тексту карточки (не заменяет его): карточка несёт
  конкретную формулировку общего механизма (`mechanisms.jsonl.template`), шаблон модуля гарантирует обязательную
  оговорку «причина не установлена» независимо от того, что именно написано в тексте карточки.
- `render_calculation_section`/`render_clarification_section` не принимают свободного текста МОДЕЛИ вовсе —
  только `CalculationResult`/ключи уточнения (`models.ClarificationKey`) — у свободного текста модели здесь нет
  входа в принципе (`LlmSelection` и так не содержит текста). `render_clarification_section` дополнительно
  принимает необязательные `candidate_labels` (правка F4b, п.2, H-26) — тоже не текст модели, а готовые строки
  словаря корпуса (`terms.jsonl.preferred_ru`), которые собирает сервер (`preanalysis.clarification_
  candidates`); старый вызов без этого параметра не меняется (умолчание — пустой список, текст как раньше).
- `render_response` — сборка `JobResponse` с явной проверкой пределов (`maxItems` секций/ключей уточнения,
  уникальность секций) до вызова конструктора `models.JobResponse`, чтобы причина отказа была понятной раньше
  `ValidationError`.
- `_fmt` — десятичная запятая, не точка (Правка, раунд 1, Important 5): мастер-промпт §8 и остальной интерфейс
  пишут числа как «18,716334 т/ч»/«0,04», не «18.716334»/«0.04».
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .models import JobResponse, Section
from .validator import APPROVED_CRITICAL_FACT_STATES

if TYPE_CHECKING:
    from .models import CalculationResult, EvidenceCard, ValidationOutcome

# шаблоны секций (мастер-промпт §8) — ключ в journal/тестах, не выводится клиенту как есть
DOCUMENT_OBSERVATION_PREFIXES = ("В предоставленной модели указано: ", "На снимке указано: ")
HYPOTHESIS_TEMPLATE = "Общий возможный механизм; причина на КДМ-1 не установлена."

# пределы контракта job-response.schema.json (models.JobResponse/Section) — проверяются здесь заранее
_MAX_SECTIONS = 24
_MAX_CLARIFICATION_KEYS = 12
_MAX_TEXT_LENGTH = 4000

# правка F4b, п.2 (H-26): до скольких подписей кандидатов неоднозначного термина показывать в тексте секции
# уточнения («какая величина имеется в виду» → + «вода оборотная чистая», «вода оборотная условно-чистая», …)
_MAX_CANDIDATE_LABELS = 6

# адресные формулировки уточнения по ключу (models.ClarificationKey) — участок/единица/период/сорт, не общий
# текст «уточните вопрос» (мастер-промпт §6)
_CLARIFICATION_PHRASES: dict[str, str] = {
    "quantity": "какая величина имеется в виду",
    "process": "какой участок/узел КДМ-1 имеется в виду",
    "unit": "в каких единицах измерения",
    "basis": "по какой базе (сухое/влажное вещество, массовая/объёмная доля)",
    "period": "за какой период",
    "timezone": "в каком часовом поясе (UTC или Europe/Moscow)",
    "grade": "для какого сорта/режима",
    "measurement_quality": "какого качества/свежести измерение",
    "source_applicability": "применим ли источник к этому случаю",
}


def _truncate(text: str) -> str:
    """Текст секции не длиннее `Section.text` схемы (`maxLength=4000`) — обрезка здесь, а не `ValidationError`
    из `models.Section` без понятной причины."""
    return text if len(text) <= _MAX_TEXT_LENGTH else text[:_MAX_TEXT_LENGTH]


def _fmt(value: float) -> str:
    """Короткая форма числа для текста секции расчёта: целое — без `.0`, остальное — с десятичной запятой, не
    точкой (Правка, раунд 1, Important 5: мастер-промпт §8 и весь остальной интерфейс используют «18,716334
    т/ч», «0,04», не «18.716334»/«0.04»)."""
    return str(int(value)) if float(value).is_integer() else str(value).replace(".", ",")


def render_claim_section(card: "EvidenceCard") -> "Section":
    """`EvidenceCard` → `Section`: `kind` — `document_observation` (карточка из вложения/снимка) или
    `public_definition` (карточка публичного источника) по `card.kind`/происхождению источника; текст —
    фиксированный шаблон с `card.text`, не вольный пересказ."""
    if card.kind != "claim":
        raise ValueError(f"render_claim_section: ожидался card.kind == 'claim', получено {card.kind!r}")
    if card.critical_local_fact and card.publication_state not in APPROVED_CRITICAL_FACT_STATES:
        raise ValueError("render_claim_section: местный критический факт без согласования не рендерится вовсе")
    refs = list(card.evidence_refs)
    if card.allowed_render_mode == "attributed_document_literal":
        is_snapshot = any(ref.locator.type == "image_label" for ref in refs)
        prefix = DOCUMENT_OBSERVATION_PREFIXES[1] if is_snapshot else DOCUMENT_OBSERVATION_PREFIXES[0]
        return Section(kind="document_observation", text=_truncate(f"{prefix}{card.text}"),
                       evidence_refs=refs, rendered_by="server", claim_id=card.id)
    if card.allowed_render_mode == "attributed_reference":
        return Section(kind="public_definition", text=_truncate(card.text), evidence_refs=refs,
                       rendered_by="server", claim_id=card.id)
    raise ValueError(f"render_claim_section: неизвестный allowed_render_mode {card.allowed_render_mode!r}")


def render_mechanism_section(card: "EvidenceCard") -> "Section":
    """Механизм (`card.kind == "mechanism"`) → секция `qualified_hypothesis` с `HYPOTHESIS_TEMPLATE`."""
    if card.kind != "mechanism":
        raise ValueError(f"render_mechanism_section: ожидался card.kind == 'mechanism', получено {card.kind!r}")
    text = f"{card.text} {HYPOTHESIS_TEMPLATE}" if card.text else HYPOTHESIS_TEMPLATE
    return Section(kind="qualified_hypothesis", text=_truncate(text), evidence_refs=list(card.evidence_refs),
                   rendered_by="server", claim_id=card.id)


def render_calculation_section(result: "CalculationResult") -> "Section":
    """`CalculationResult` → секция `calculation`: входы, единицы, формула и допущения в тексте; `value is
    None` (не хватает базы) рендерится как `limitation`, не как `calculation` с пропущенным числом."""
    if result.value is None:
        missing = "; ".join(result.missing_basis) if result.missing_basis else "не указана необходимая база расчёта"
        text = f"Расчёт «{result.calculation_id}» невозможен без уточнения: {missing}."
        return Section(kind="limitation", text=_truncate(text), evidence_refs=[], rendered_by="server",
                       claim_id=None)
    inputs_text = "; ".join(f"{name}={_fmt(value)} {unit}".strip() for name, value, unit in result.inputs)
    assumptions_text = "; ".join(result.assumptions) if result.assumptions else "без дополнительных допущений"
    text = (f"Расчёт «{result.calculation_id}»: входы — {inputs_text}. "
            f"Результат — {_fmt(result.value)} {result.unit}. Допущения: {assumptions_text}.")
    return Section(kind="calculation", text=_truncate(text), evidence_refs=[], rendered_by="server", claim_id=None)


def render_clarification_section(clarification_keys: tuple[str, ...],
                                 candidate_labels: tuple[str, ...] = ()) -> "Section":
    """Ключи уточнения (`models.ClarificationKey`) → секция `clarification` с адресной формулировкой (участок,
    единица, период, сорт — не общий текст «уточните вопрос»).

    Правка F4b, п.2 (H-26): `candidate_labels` — необязательный параметр, старый вызов (`render_clarification_
    section(keys)`) работает буквально как раньше (умолчание `()` — ничего не добавляется к тексту). Подписи —
    СЕРВЕРНЫЕ строки словаря корпуса (`terms.jsonl.preferred_ru`, собранные `preanalysis.clarification_
    candidates`), не свободный текст модели — у LLM в этой архитектуре нет канала для текста вообще
    (`LlmSelection` несёт только id, см. докстринг модуля); этот параметр тот же тип данных, что и
    `clarification_keys` — перечень готовых фиксированных строк, выбор которых делает сервер. До
    `_MAX_CANDIDATE_LABELS` (6) — защита здесь же, не только у вызывающего (`preanalysis.clarification_
    candidates` тоже режет до этого предела, но рендеринг не доверяет вызывающему прислать длину в его
    пределах молча)."""
    if not clarification_keys:
        raise ValueError("render_clarification_section: нужен хотя бы один ключ уточнения")
    parts = [_CLARIFICATION_PHRASES.get(key, key) for key in clarification_keys]
    text = "Уточните, пожалуйста: " + "; ".join(parts) + "."
    if candidate_labels:
        text += " Возможные варианты: " + ", ".join(candidate_labels[:_MAX_CANDIDATE_LABELS]) + "."
    return Section(kind="clarification", text=_truncate(text), evidence_refs=[], rendered_by="server",
                   claim_id=None)


CONTROL_REFUSAL_TEXT = (
    "Команда не выполняется: аналитический чат не управляет оборудованием и не выдаёт уставок или порядка "
    "действий. Ниже — только справка по документам; решение о действии принимает технолог по действующим "
    "инструкциям."
)


def render_control_refusal_section() -> "Section":
    """Отказ от управляющей части вопроса (мастер-промпт §7 п.6; открытый сценарий T-10 «отказ от команды с
    сохранением допустимой справки») — серверный шаблон, первая секция ответа; справка по карточкам пакета идёт
    после него обычными секциями."""
    return Section(kind="limitation", text=CONTROL_REFUSAL_TEXT, evidence_refs=[], rendered_by="server",
                   claim_id=None)


def render_response(request_id: str, conversation_id: str, state: str, corpus_version: str, stage_label: str,
                    outcome: "ValidationOutcome", sections: tuple["Section", ...]) -> "JobResponse":
    """Собрать итоговый `JobResponse` (`models.py`) из уже готовых секций и исхода проверки. Эта функция не
    решает, какие секции строить — то делают `render_*_section` выше по `ValidationOutcome`/`service.py`; здесь
    только сборка и проверка общих пределов (`maxItems` схемы — `models.JobResponse` их и так проверит при
    конструировании, но дубль здесь даёт понятную причину отказа раньше `ValidationError`)."""
    if len(sections) > _MAX_SECTIONS:
        raise ValueError(f"render_response: секций больше предела {_MAX_SECTIONS}: {len(sections)}")
    seen: list[str] = []
    for section in sections:
        dump = section.model_dump_json()
        if dump in seen:
            raise ValueError("render_response: повтор секции в ответе")
        seen.append(dump)
    clarification_keys = list(dict.fromkeys(outcome.clarification_keys))
    if len(clarification_keys) > _MAX_CLARIFICATION_KEYS:
        raise ValueError(f"render_response: ключей уточнения больше предела {_MAX_CLARIFICATION_KEYS}")
    return JobResponse(
        request_id=request_id, conversation_id=conversation_id, state=state, corpus_version=corpus_version,
        stage_label=stage_label, sections=list(sections), clarification_keys=clarification_keys,
        error_code=outcome.error_code,
    )
