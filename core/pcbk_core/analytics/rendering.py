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

Телеметрия БДРВ в ответе (план Д17, Task 1; мастер-промпт §7 п.7, §8): `render_telemetry_section`/
`render_telemetry_period_section` строят секцию `telemetry_observation` буквально из полей ответа `DataService`
(значение/единица/время/качество или среднее/минимум/максимум/доля хороших точек) — без пересчёта, с явной
подписью «время историана» (§8: «время — как отдаёт `DataService`, браузер не подмешивается»). `ResolvedLink`
(кто и когда согласовал связь) печатается в тексте, а не в `evidence_refs` — источник значения телеметрии не
корпус (у секции `evidence_refs=[]`, `claim_id=None`, как и у прочих серверных ограничений). Элемент ответа со
`status != "ok"` (дискретный тег без недавнего значения, «нет данных» и т.п.) — в текст идёт его `text`, не
придуманное число; дискретный тег при `status == "ok"` — `state_label` вместо значения/единицы. Вызывающий
(`service.py`) передаёт сюда только ответ `DataService`, у которого уже есть хотя бы один элемент `items` —
полный отказ службы без `items` (ворота/бюджет/сбой настройки) рендерится отдельно, `render_telemetry_refusal_
section` ниже, а не этими двумя функциями.

Четыре серверных ограничения телеметрии — тоже фиксированные шаблоны этого модуля, не текст вопроса/ответа
модели: `render_telemetry_unresolved_section` (подпись есть в документах, но связь не согласована — читать
нечего), `render_telemetry_unavailable_section` (служба данных не подключена к этому процессу), `render_
telemetry_refusal_section` (служба данных подключена, но отказала — текст отказа передаётся как есть, мастер-
промпт: «отказ `DataService` показывается как есть, не прячется и не переформулируется в «ошибку»»), `render_
telemetry_truncated_section` (подписей в вопросе больше, чем сервер читает за один ответ).

Правка плана Д17, Task 1, раунд правки 2 (живая проверка на сервере, 14.10.2026) — три уточнения оформления
телеметрии (значения не менялись, только показ):

1. Число — `_fmt_measured`: не больше трёх знаков после запятой, без хвостовых нулей, запятая не точка; это
   округление ПОКАЗА, не пересчёт (сырое значение историана, например `-75.99102783203125`, без округления
   уходило в текст целиком). Целое/дискретное (`state_label`) — как есть, эта функция их не трогает.
2. Единица `None`/пустая строка (у реального тега в историане бывает не задана) — place-holder `_UNIT_NOT_SET`
   («единица в историане не задана») вместо того, чтобы молча пропасть из текста (было: значение без единицы
   встык со временем, неотличимо от опечатки).
3. Время — `_telemetry_time_text`/`_telemetry_bound_text`: строка `time`/`start`/`end` ответа `DataService`
   (ISO со своим смещением) разбирается и показывается В ЕЁ ЖЕ поясе («ДД.ММ.ГГГГ ЧЧ:ММ:СС (UTC±ЧЧ:ММ)»), не
   переводится в другой — отдельное поле `tz` ответа (`reply["tz"]`/`period["tz"]`, текстовая метка пояса
   каталога) для этого больше не читается, чтобы не показывать смещение одного пояса рядом с меткой другого.
   Строка не разбирается (нет значения, не ISO, нет смещения) — показывается как есть, без форматирования.

Открытые расхождения корпуса (план Д17, Task 1, раунд правки 4 — пробел Д16, мастер-промпт §6: «конфликт виден
и может быть отправлен на согласование»): `render_conflict_section`/`render_conflict_truncated_section` — тот
же приём, что у ограничений телеметрии (фиксированный шаблон, `evidence_refs=[]`, `claim_id=None`). Какое
расхождение показывать и какой `error_code` ставить — решает `service._conflict_sections`/`_build_outcome`
(только статус `"open"`, не перетирая уже выставленный код `busy`/`policy_refused`), этот модуль только строит
секцию из уже выбранного `conflict_id`/`description`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .models import JobResponse, Section
from .validator import APPROVED_CRITICAL_FACT_STATES

if TYPE_CHECKING:
    from .models import CalculationResult, EvidenceCard, ValidationOutcome
    from .reviews import ResolvedLink

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


# --- телеметрия БДРВ в ответе (план Д17, Task 1; мастер-промпт §7 п.7, §8) ---

TELEMETRY_LINKS_SECTION_NAME = "Связи с историаном"  # заголовок раздела вкладки — тот же текст, что в подсказке


_UNIT_NOT_SET = "(единица в историане не задана)"  # план Д17, Task 1, раунд правки 2 — unit=None/"" у тега


def _fmt_measured(value: float) -> str:
    """Округление ПОКАЗА сырого измеренного значения историана (не `_fmt` — та хранит полную точность для
    расчётов, план Д17, Task 1, раунд правки 2): не больше трёх знаков после запятой, без хвостовых нулей,
    запятая не точка; целое — без разделителя вовсе (как `_fmt`). Это только отображение, значение из ответа
    `DataService` не пересчитывается и никуда дальше в этом виде не передаётся."""
    rounded = round(float(value), 3)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def _unit_text(unit: str | None) -> str:
    """Единица для текста секции — `_UNIT_NOT_SET`, если у тега в историане она не задана (`unit=None`/пустая
    строка), не молчаливое исчезновение единицы из текста (план Д17, Task 1, раунд правки 2)."""
    text = str(unit or "").strip()
    return text if text else _UNIT_NOT_SET


def _utc_offset_text(offset: timedelta) -> str:
    total_minutes = round(offset.total_seconds() / 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _parse_iso_datetime(time_iso) -> "datetime | None":
    """`time_iso` → `datetime` СО СМЕЩЕНИЕМ (`utcoffset()` не `None`) или `None`, если строка не ISO, пустая
    или наивная (без смещения — показывать как «её же пояс» тогда нечего, план Д17, Task 1, раунд правки 2)."""
    if not isinstance(time_iso, str) or not time_iso:
        return None
    try:
        parsed = datetime.fromisoformat(time_iso)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _telemetry_time_text(time_iso) -> str:
    """«ДД.ММ.ГГГГ ЧЧ:ММ:СС (UTC±ЧЧ:ММ, время историана)» — из строки `time` ответа `DataService` (ISO со
    своим смещением), в ЕЁ ЖЕ поясе, без перевода в другой (план Д17, Task 1, раунд правки 2: `reply["tz"]`,
    отдельная текстовая метка пояса каталога, здесь больше не используется — смещение берётся из самой строки
    времени значения, не рискует разойтись с ней). Строка не разобралась — показывается как есть."""
    parsed = _parse_iso_datetime(time_iso)
    if parsed is None:
        return f"{time_iso} — время историана"
    return f"{parsed:%d.%m.%Y %H:%M:%S} ({_utc_offset_text(parsed.utcoffset())}, время историана)"


def _telemetry_bound_text(time_iso) -> str:
    """Граница периода тем же приёмом, что `_telemetry_time_text`, без повторяющейся на каждой границе подписи
    «время историана» (она не о конкретном значении, а об окне целиком — секция и так про историан)."""
    parsed = _parse_iso_datetime(time_iso)
    if parsed is None:
        return str(time_iso)
    return f"{parsed:%d.%m.%Y %H:%M:%S} ({_utc_offset_text(parsed.utcoffset())})"


def _telemetry_header(label: str, tag: str, link: "ResolvedLink") -> str:
    """Общий префикс обеих секций значения: подпись из вопроса, тег и кто/когда согласовал связь — из
    `ResolvedLink` (мастер-промпт §7, этап 7: связь оборудования согласуется отдельно, подпись её не
    устанавливает), не из текста вопроса."""
    return (f"Данные историана по подписи {label} (тег {tag}; связь согласовал {link.approved_by}, "
           f"{link.approved_at:%d.%m.%Y})")


def _telemetry_value_text(item: dict) -> str:
    """Значение элемента ответа `DataService` для текста секции: дискретный тег — `state_label` вместо
    значения/единицы (план, Task 1: «дискретный тег — state_label вместо значения/единицы»); аналоговое
    значение — округление показа (`_fmt_measured`) и честная единица (`_unit_text`, план Д17, Task 1, раунд
    правки 2)."""
    if "state_label" in item:
        return str(item["state_label"])
    return f"{_fmt_measured(item['value'])} {_unit_text(item.get('unit'))}"


def render_telemetry_section(label: str, link: "ResolvedLink", reply: dict) -> "Section":
    """Секция `telemetry_observation` — значение «сейчас» согласованного тега (`data_bridge.tag_now`). `reply`
    — ответ `DataService`, в котором уже есть хотя бы один элемент `items` (вызывающий, `service.py`, отличает
    полный отказ службы без `items` раньше и строит для него `render_telemetry_refusal_section`, не эту
    функцию). Текст — только из полей ответа, без пересчёта значения; время — `item["time"]`, разобранное в
    ЕЁ ЖЕ поясе (`_telemetry_time_text`, план Д17, Task 1, раунд правки 2), с явной подписью «время историана»
    (мастер-промпт §8). `item["status"] != "ok"` (например, тег без недавнего значения) — в текст идёт его
    `item["text"]`, не придуманное число."""
    item = reply["items"][0]
    tag = item.get("tag") or link.historian_tag
    header = _telemetry_header(label, tag, link)
    if item.get("status") != "ok":
        text = f"{header}: {item.get('text', '')}"
    else:
        text = (f"{header}: {_telemetry_value_text(item)} на {_telemetry_time_text(item.get('time'))}; "
               f"качество: {item.get('quality')}. Это измеренное значение на момент ответа, не норматив и не "
               "уставка.")
        notes = item.get("item_notes") or []
        if notes:
            text += " " + "; ".join(notes) + "."
    return Section(kind="telemetry_observation", text=_truncate(text), evidence_refs=[], rendered_by="server",
                   claim_id=None)


def _telemetry_period_text(period: dict) -> str:
    """Период текстом — границы (`start`/`end`) разобраны в ИХ ЖЕ поясе (`_telemetry_bound_text`, план Д17,
    Task 1, раунд правки 2), `preset` ответа — как есть; `period["tz"]` (отдельная метка пояса каталога) здесь
    больше не читается, по той же причине, что в `_telemetry_time_text`."""
    preset, start, end = period.get("preset"), period.get("start"), period.get("end")
    span = f"{_telemetry_bound_text(start)} — {_telemetry_bound_text(end)}"
    return f"{preset} ({span})" if preset else span


def render_telemetry_period_section(label: str, link: "ResolvedLink", reply: dict) -> "Section":
    """Секция `telemetry_observation` за период (`data_bridge.tag_period`) — среднее/минимум/максимум/доля
    хороших точек буквально из ответа `DataService` (округление показа — `_fmt_measured`, честная единица —
    `_unit_text`, план Д17, Task 1, раунд правки 2), тем же приёмом, что `render_telemetry_section`."""
    item = reply["items"][0]
    tag = item.get("tag") or link.historian_tag
    header = _telemetry_header(label, tag, link)
    period_text = _telemetry_period_text(reply.get("period") or {})
    if item.get("status") != "ok":
        text = f"{header} за {period_text}: {item.get('text', '')}"
    else:
        unit = _unit_text(item.get("unit"))
        text = (f"{header} за {period_text}: среднее {_fmt_measured(item['avg'])}, минимум "
               f"{_fmt_measured(item['min'])}, максимум {_fmt_measured(item['max'])} {unit}; доля хороших "
               f"точек {_fmt_measured(item['percent_good'])} %.")
        notes = reply.get("notes") or []
        if notes:
            text += " " + "; ".join(notes) + "."
    return Section(kind="telemetry_observation", text=_truncate(text), evidence_refs=[], rendered_by="server",
                   claim_id=None)


def render_telemetry_unresolved_section(label: str) -> "Section":
    """Подпись найдена в документах КДМ-1, но связи с тегом историана нет — значение не читается вовсе (вызов
    этой функции сам по себе доказывает, что `tag_now`/`tag_period` не вызывались: `service.py` строит её ДО
    любого обращения к `data_bridge.py`)."""
    text = (f"Подпись {label} есть в документах КДМ-1, но её связь с тегом историана не согласована — значение "
           f"не показывается. Предложить связь можно в разделе «{TELEMETRY_LINKS_SECTION_NAME}» этой вкладки.")
    return Section(kind="limitation", text=_truncate(text), evidence_refs=[], rendered_by="server", claim_id=None)


def render_telemetry_unavailable_section(label: str) -> "Section":
    """Связь согласована, но служба данных (`DataService`) не подключена к этому процессу/сейчас неисправна —
    честное ограничение, не падение ответа."""
    text = f"Служба данных недоступна — значение по подписи {label} не показывается."
    return Section(kind="limitation", text=_truncate(text), evidence_refs=[], rendered_by="server", claim_id=None)


def render_telemetry_refusal_section(label: str, message: str) -> "Section":
    """Служба данных подключена, но отказала (тег вне разрешённого списка, частота, учения) — текст отказа
    `DataService` передаётся как есть (мастер-промпт: «отказ показывается как есть, не прячется и не
    переформулируется в «ошибку»»)."""
    text = f"Служба данных отказала по подписи {label}: {message}"
    return Section(kind="limitation", text=_truncate(text), evidence_refs=[], rendered_by="server", claim_id=None)


def render_telemetry_truncated_section(shown: int, total: int) -> "Section":
    """Подписей со связью в вопросе больше, чем сервер читает за один ответ (`service.MAX_TELEMETRY_LABELS`) —
    `shown` — сколько реально прочитано, `total` — сколько подписей со связью нашёл сервер всего."""
    text = f"Показаны первые {shown} подписи из {total}."
    return Section(kind="limitation", text=_truncate(text), evidence_refs=[], rendered_by="server", claim_id=None)


# --- открытые расхождения корпуса в ответе (план Д17, Task 1, раунд правки 4; мастер-промпт §6) ---

def render_conflict_section(conflict_id: str, description: str) -> "Section":
    """Открытое расхождение корпуса (`conflicts`), на которое преданализ сослался (`analysis.conflict_ids`) —
    мастер-промпт §6: «конфликт виден и может быть отправлен на согласование» (пробел Д16: `error_code=
    "source_conflict"` был объявлен в контракте, но никогда не выставлялся). Текст — фиксированный шаблон с
    `description` как есть, не вольный пересказ; вызывающий (`service._conflict_sections`) передаёт сюда
    только расхождение в статусе `"open"` — решённые/отклонённые сюда не доходят вовсе."""
    text = (f"В источниках КДМ-1 есть открытое расхождение {conflict_id}: {description} Ответ ниже его не "
           "разрешает — расхождение можно отправить на согласование.")
    return Section(kind="limitation", text=_truncate(text), evidence_refs=[], rendered_by="server", claim_id=None)


def render_conflict_truncated_section(remaining: int) -> "Section":
    """Открытых расхождений, на которые преданализ сослался, больше, чем секций на ответ (`service.
    MAX_CONFLICT_SECTIONS`) — `remaining` — сколько ещё не показано этим ответом."""
    text = f"Ещё {remaining} открытых расхождений корпуса не показаны."
    return Section(kind="limitation", text=_truncate(text), evidence_refs=[], rendered_by="server", claim_id=None)
