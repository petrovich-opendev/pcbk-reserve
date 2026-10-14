"""Проверка выбора LLM (Task 3, дополняется Task 5; план §Task 3/§Task 5, Этап 3 п.«контролируемое отображение»
и Этап 5 п.«строгий выбор карточек»; мастер-промпт §8).

Задача этого модуля — отделить то, что *предложила* модель (`models.LlmSelection`, через `llm_adapter.py`,
Task 5), от того, что сервер действительно разрешает показать (`models.ValidationOutcome`). «Наличие ссылки
недостаточно» (мастер-промпт §8): каждый `id` сверяется с пакетом доказательств преданализа
(`preanalysis.build_evidence_pack`), а не просто с тем, что он синтаксически похож на идентификатор корпуса —
обход через «дополнительное поле» или подмену регистра/номера в `id` не даёт доступа к карточке, которой нет в
разрешённом пакете.

Проверяемые подмены (мастер-промпт §8, ОСОБЫЙ ФОКУС ПРОВЕРКИ №4 плана) — отдельно от присутствия `id` в
разрешённом пакете: верх/низ, №7/№9, отрицание, чужой тег, единица, условие, дата/версия, гипотеза → причина,
общая статья → команда, опасное действие с верной цитатой. Эти проверки сравнивают *смысл*, который несёт
секция ответа, с тем, что на самом деле утверждает исходная карточка (`EvidenceCard.text`/`applicability`) — их
не заменяет обычная проверка сериализации JSON (план, «Особый фокус проверки»: «пункты привязаны к испытаниям
этапов ниже; обычная проверка сериализации их не заменяет»).

Предел исправлений (мастер-промпт §8: «ограниченные повторы проверки вновь учитывают расход; после предела —
уточнение/ограниченный ответ») — считает вызывающий (`service.py`), этот модуль только сообщает, прошла ли
проверка, не хранит счётчик попыток сам.

Реализация (Task 3b). Три решения, зафиксированные здесь (документированы для Task 4/5/7, не меняют
`docs/kdm1/INTERFACES.md`):

1. Повторная проверка «в момент выдачи» (ОСОБЫЙ ФОКУС ПРОВЕРКИ №2 плана) — `validate_selection` не доверяет
   тому, что карточка когда-то прошла преданализ: для каждой выбранной карточки заново проверяются
   `publication_state` (белый список `RENDERABLE_PUBLICATION_STATES`, не чёрный — неизвестное новое состояние
   корпуса по умолчанию запрещено, не разрешено), `critical_local_fact` (требует отдельного, более узкого
   состояния `APPROVED_CRITICAL_FACT_STATES` — запрет местных нормативов без согласования), `applicability`
   (не пусто) и версия корпуса каждой ссылки `evidence_refs[].corpus_version` против `preanalysis.corpus_version`
   («старый пакет доказательств не даёт нового разрешения» — несовпадение версии после публикации новой версии
   корпуса между поиском и ответом блокирует карточку). `reviews.py` (Task 7) пока не существует и не пишет в
   корпус состояние `reviewer_approved` — оно зарезервировано этим модулем на будущее; тесты строят его вручную
   на `EvidenceCard`, не через настоящий корпус.
2. PUB-10/PUB-11 (ISO 536/287 — мастер-промпт §5, §8; план, Scope задачи «PUB-10/PUB-11 — не через LLM»):
   `sources.jsonl` настоящего пакета помечает их `llm_context_allowed=false`. Карточка не несёт этот флаг сама
   (его несёт источник), поэтому проверка — по `EvidenceRef.source_id` каждой ссылки карточки против
   `RESTRICTED_SOURCE_IDS`: прямая ссылка на один из этих источников блокирует карточку независимо от остальных
   полей — в текущем корпусе такой ссылки нет ни у одной карточки (обе ISO-страницы оставлены ссылками для
   человека, не фрагментами), это защита на будущее добавление, не текущий кейс.
3. `check_claim_mutation` — эвристика на регулярных выражениях (без новых зависимостей, план Global Constraints):
   тег (точное совпадение буквенно-цифрового токена, не только числа), номера (включая `№N`, с учётом порядка —
   перестановка двух номеров между разными объектами не маскируется под совпадение множества), единица
   измерения (привязана к конкретному числу — замена/исчезновение единицы у того же числа ловится отдельно от
   появления совсем новой единицы), отрицание (несколько форм: «нельзя», «без», «нет», «отсутств…», «не» как
   отдельное слово), верх/низ, управляющий глагол и переход «гипотеза → причина» (только для `kind="mechanism"`).
   Сверка — только с `card.text`; `card.applicability` — служебная классификация происхождения карточки
   (`"this_attachment_only"`, диапазоны дат и т.п.), не содержание факта, поэтому не подмешивается — число/год
   из `applicability` не легитимизирует подмену числа в тексте (Правка, раунд 1, Important 2). Это не полный
   семантический разбор — функция ловит перечисленные в мастер-промпте §8 мутации на уровне токенов текста,
   которого у LLM в этой архитектуре и так нет (`LlmSelection` несёт только id, без свободного текста модели) —
   функция полезна как самостоятельная проверка соответствия произвольного текста карточке (тесты, будущие
   расширения формата).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from .models import ValidationOutcome

if TYPE_CHECKING:
    from .knowledge import CorpusHandle
    from .models import CalculationResult, EvidenceCard, LlmSelection, Preanalysis

# предел исправлений одного задания (мастер-промпт §8): больше — уточнение/ограниченный ответ, не новая попытка
MAX_SELECTION_RETRIES = 2

# источники, чьё содержимое не передаётся LLM и не отображается без отдельного подтверждённого основания
# (мастер-промпт §5: «официальные страницы ISO … их содержимое не включается в контекст ИИ без отдельного
# допустимого основания»; §8/план Scope: «PUB-10/PUB-11 — не через LLM»). Соответствует настоящему
# `sources.jsonl`: PUB-10 (ISO 536:2019), PUB-11 (ISO 287:2017) — обе `llm_context_allowed=false`.
RESTRICTED_SOURCE_IDS: frozenset[str] = frozenset({"PUB-10", "PUB-11"})

# publication_state, допускающие показ карточки «сейчас» (повторная проверка в момент выдачи, ОСОБЫЙ ФОКУС №2
# плана) — белый список, не чёрный: неизвестное/новое состояние корпуса по умолчанию запрещено показывать, не
# разрешено. "desk_verified_draft" — текущее состояние настоящего корпуса (ни одна карточка ещё не прошла
# назначенное согласование, Task 7 не реализован). "reviewer_approved" зарезервировано для `reviews.py` (Task 7):
# карточка получает это состояние только решением уполномоченного проверяющего; пока таких записей в корпусе
# нет, тесты строят `EvidenceCard` с этим состоянием вручную.
RENDERABLE_PUBLICATION_STATES: frozenset[str] = frozenset({"desk_verified_draft", "reviewer_approved"})

# местный критический факт (`EvidenceCard.critical_local_fact`) показывается только в этом состоянии — подмножество
# RENDERABLE_PUBLICATION_STATES, не отдельный список (мастер-промпт §8: «местный критический факт требует
# согласованной карточки с действующей областью/сроком»; план, Scope: «запрет местных нормативов без согласования»).
APPROVED_CRITICAL_FACT_STATES: frozenset[str] = frozenset({"reviewer_approved"})

# эвристики check_claim_mutation (мастер-промпт §8, перечень мутаций) — регулярные выражения, без зависимостей
_NUMBER_RE = re.compile(r"№?\d+(?:[.,]\d+)?")
# единицы измерения — сверены с реально встречающимися в мастер-промпте §8 примерами расчётов (кН/м, бар, т/ч,
# м³/ч, г/м²) плюс кПа (Правка, раунд 1, Important 3: «бар↔кПа не ловится» — отсутствовала в списке вовсе, из-за
# чего кПа даже не распознавалась как единица). Настоящий корпус (`claims.jsonl`/`mechanisms.jsonl`) в текстах
# карточек единиц измерения в принципе не содержит (проверено по пакету заказчика, read-only) — список собран
# по мастер-промпту и используется прежде всего в тестах/будущих карточках расчётов.
_UNIT_RE = re.compile(
    r"кПа|кН/м|т/ч|м3/ч|м³/ч|г/м2|г/м²|кг/ч|кг/м3|кг/м³|мпа|бар|°c|%", re.IGNORECASE)
# число, сразу за которым (через пробел или без) идёт единица — связка «число+единица» для проверки подмены/
# исчезновения единицы именно У ЭТОГО числа (Правка, раунд 1, Important 3), не только появления новой единицы
# где-то в тексте вообще
_NUMBER_UNIT_RE = re.compile(
    r"(№?\d+(?:[.,]\d+)?)\s*(кПа|кН/м|т/ч|м3/ч|м³/ч|г/м2|г/м²|кг/ч|кг/м3|кг/м³|мпа|бар|°c|%)", re.IGNORECASE)
# тегоподобный токен — по образцу настоящих подписей корпуса (цифровой префикс + буквы/цифры/подчёркивание,
# например тестовый `20FAKE_QT31`): регистр и знаки участвуют в сравнении целиком, это НЕ число (Правка, раунд
# 1, Important 1 — «чужой тег»: `20FAKE_QT31` → `20FAKE_QP31` отличается буквой, не числом, старая проверка
# чисел её не ловила вовсе)
_TAG_RE = re.compile(r"\b\d{1,3}[A-Za-z][A-Za-z0-9_]*\b")
# отрицание — несколько форм и позиций (Правка, раунд 1, Important 4): «нельзя»/«никогда» целым словом, «без»/
# «нет» целым словом (не как часть «безопасности»/«нет» внутри другого слова, «необходимо»/«нередко» —
# границы слова обязательны), «отсутств…» любая форма, «не» как отдельное слово в любой позиции (не только
# перед пробелом, как было раньше — иначе «не?»/«не,»/«не» в конце фразы не ловилось)
_NEGATION_RE = re.compile(r"\bнельзя\b|\bникогда\b|\bбез\b|\bнет\b|отсутств\w*|\bне\b", re.IGNORECASE)
_UPPER_RE = re.compile(r"верхн", re.IGNORECASE)
_LOWER_RE = re.compile(r"нижн", re.IGNORECASE)
_CAUSE_RE = re.compile(
    r"является причиной|точно вызывает|устанавливает причину|подтверждает причину|привел[оа]? к отклонению",
    re.IGNORECASE)
_CONTROL_RE = re.compile(
    r"увелич\w*|сниз\w*|открой\w*|закрой\w*|останов\w*|запуст\w*|установ\w*\s+уставк\w*", re.IGNORECASE)

# ключи missing_fields преданализа, от которых зависит база расчёта (check_calculation_consistency)
_CALCULATION_RELEVANT_MISSING: frozenset[str] = frozenset({"period", "process", "basis", "grade"})


def _number_sequence(text: str) -> list[str]:
    """Числа и `№N` из текста, В ПОРЯДКЕ ПОЯВЛЕНИЯ (не множеством — Правка, раунд 1, Important 2: простое
    множество чисел совпадает и при перестановке, «Узел №7 и датчик №9» → «Узел №9 и датчик №7» осталось бы
    незамеченным). Символ «№» снят — «№7» и «узел 7» сравниваются как один и тот же номер."""
    return [match.lstrip("№") for match in _NUMBER_RE.findall(text)]


def _dedupe_first_occurrence(values: list[str]) -> list[str]:
    """Повторы одного и того же значения схлопнуты до первого появления, порядок первых появлений сохранён
    (Правка, раунд 2, Important: без схлопывания повторный упоминание ОДНОГО И ТОГО ЖЕ числа в корректном
    пересказе — «Узел №7 (ещё раз: узел 7)» — ложно считалось подменой: общий итератор `_is_order_preserving_
    subsequence` после первого совпадения продвигался вперёд и не находил то же число второй раз)."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _is_order_preserving_subsequence(proposed: list[str], reference: list[str]) -> bool:
    """`proposed` — подпоследовательность `reference` в том же порядке (пропуски в `reference` допустимы —
    предложенный текст может упоминать не все числа карточки; числа, которых в `reference` вовсе нет, или
    перестановка относительно друг друга — не подпоследовательность). Вызывающий (`check_claim_mutation`)
    передаёт сюда уже СХЛОПНУТЫЕ последовательности (`_dedupe_first_occurrence`) — эта функция сама повторов не
    ждёт и не обрабатывает (при повторе в `proposed` после схлопывания сверх `reference` его там и не будет —
    поведение намеренно не меняется, дедуп делает вызывающий, чтобы не плодить два похожих приёма сравнения)."""
    it = iter(reference)
    return all(value in it for value in proposed)


def _units(text: str) -> set[str]:
    return {match.lower() for match in _UNIT_RE.findall(text)}


def _number_unit_pairs(text: str) -> dict[str, set[str]]:
    """Число (без «№») → множество единиц, встреченных сразу после него в тексте — основа проверки «та же
    единица у того же числа» (Правка, раунд 1, Important 3)."""
    pairs: dict[str, set[str]] = {}
    for number, unit in _NUMBER_UNIT_RE.findall(text):
        pairs.setdefault(number.lstrip("№"), set()).add(unit.lower())
    return pairs


def _tags(text: str) -> set[str]:
    """Тегоподобные токены текста (см. `_TAG_RE`) — точное буквенно-цифровое совпадение, не нормализация."""
    return set(_TAG_RE.findall(text))


def _negation_marker_total(text: str) -> int:
    """Общее число маркеров отрицания в тексте (Правка, раунд 2, Out-of-scope: было — просто «есть хоть один
    маркер или нет» (`bool`), из-за чего исчезновение ОДНОГО из ДВУХ отрицаний при наличии другого оставалось
    незамеченным — `bool` до и после одинаково `True`). `Counter` — ради раздельного подсчёта по виду маркера
    (на будущее: явная разбивка в журнале при желании), итог сравнения — суммарное количество, не множество
    видов: замена одного слова-отрицания на другое с тем же смыслом (например, «нельзя» → «без») не мутация,
    пропадание одного из нескольких — мутация."""
    return sum(Counter(match.lower() for match in _NEGATION_RE.findall(text)).values())


def _card_block_reason(card: "EvidenceCard", preanalysis: "Preanalysis",
                       approved_card_ids: frozenset[str] = frozenset()) -> str | None:
    """Повторная проверка права показа ОДНОЙ карточки в момент выдачи (ОСОБЫЙ ФОКУС ПРОВЕРКИ №2 плана, см.
    докстринг модуля, «Реализация», п.1–2): пакет `pack`, который получает `validate_selection`, не считается
    доверенным только потому, что карточка туда попала на преданализе — каждое поле состояния сверяется снова,
    здесь и сейчас. Причина отказа (короткая, для журнала/`ValidationOutcome.rejected`, не для пользователя)
    или `None`, если карточка разрешена к показу.

    `approved_card_ids` — дополнение контроллера к Task 7 (2026-10-14, шаг 2): id карточек, для которых
    `service.py` только что получил действующее согласование через `reviews.ReviewStore.approved_claim`
    (перепроверено «здесь и сейчас», не по статичной метке корпуса). Местный критический факт проходит, если
    `publication_state` уже в `APPROVED_CRITICAL_FACT_STATES` (резерв на будущее — ручная правка корпуса) ИЛИ
    его `id` — в этом множестве; пустое множество по умолчанию сохраняет прежнее поведение (все вызывающие,
    кто ещё не передаёт этот параметр, не меняют смысла своих тестов)."""
    for ref in card.evidence_refs:
        if ref.source_id in RESTRICTED_SOURCE_IDS:
            return (f"источник {ref.source_id} требует отдельного подтверждённого основания по условиям "
                    "правообладателя — карточка не передаётся LLM и не отображается без него")
    if (card.critical_local_fact and card.publication_state not in APPROVED_CRITICAL_FACT_STATES
            and card.id not in approved_card_ids):
        return "местный критический факт требует согласованной карточки — согласование не подтверждено"
    if card.publication_state not in RENDERABLE_PUBLICATION_STATES:
        return f"публикационный статус карточки не допускает показа сейчас: {card.publication_state!r}"
    if not card.applicability:
        return "у карточки не указана действующая область/срок применимости"
    for ref in card.evidence_refs:
        if ref.corpus_version != preanalysis.corpus_version:
            return ("версия корпуса основания карточки не совпадает с версией преданализа — старый пакет "
                    "доказательств не даёт нового разрешения")
    return None


def validate_selection(selection: "LlmSelection", preanalysis: "Preanalysis",
                       pack: tuple["EvidenceCard", ...],
                       approved_card_ids: frozenset[str] = frozenset()) -> "ValidationOutcome":
    """`LlmSelection` → `ValidationOutcome`: каждый `reference_claim_ids`/`mechanism_ids` должен входить в
    `preanalysis.allowed_claim_ids`/`allowed_mechanism_ids` **и** существовать в `pack` с тем же `id` (двойная
    сверка — схема `llm-selection.schema.json` сама сужает enum под конкретный пакет, но сервер не доверяет
    только этому: мастер-промпт §8). Не входящие — в `ValidationOutcome.rejected` с причиной, не отбрасываются
    молча. `calculation_ids` здесь не считаются (это `calculations.run_calculation`) — validator только
    проверяет, что выбранный `calculation_id` допустим для `preanalysis.intent`.

    `approved_card_ids` — необязательный параметр (дополнение контроллера к Task 7, 2026-10-14, шаг 2, см.
    `_card_block_reason`): id критических местных карточек с ДЕЙСТВУЮЩИМ согласованием
    (`reviews.ReviewStore.approved_claim`), которое `service.py` собирает ДО вызова этой функции — сама она
    `ReviewStore` не знает и не импортирует (не ломает `validator.py` как модуль без зависимости от Task 7).
    Умолчание — пустое множество, старое поведение (критический факт проходит только по `publication_state`).

    `refused=True` (и `error_code="policy_refused"`) — только когда отказ по карточке вызван нарушением политики
    (`_card_block_reason`: отзыв/устаревшая версия/несогласованный местный факт/PUB-10 или PUB-11), не просто
    «модель предложила id, которого нет в пакете» (это тоже отказ в `rejected`, но не повод отбрасывать весь
    исход — вызывающий `service.py` сам решает про повтор/`limited_answer` по пустоте принятых списков)."""
    pack_by_id = {card.id: card for card in pack}
    rejected: list[tuple[str, str]] = []
    accepted_claim_ids: list[str] = []
    accepted_mechanism_ids: list[str] = []
    hard_violation = False

    def _resolve(selected_id: str, allowed_ids: list[str], expected_kind: str) -> bool:
        nonlocal hard_violation
        if selected_id not in allowed_ids:
            rejected.append((selected_id, "id не входит в разрешённый пакет преданализа"))
            return False
        card = pack_by_id.get(selected_id)
        if card is None:
            rejected.append((selected_id, "id отсутствует в пакете доказательств текущего запроса"))
            return False
        if card.kind != expected_kind:
            rejected.append((selected_id,
                             f"id есть в пакете доказательств, но карточка другого вида ({card.kind!r}), "
                             f"не {expected_kind!r}"))
            return False
        reason = _card_block_reason(card, preanalysis, approved_card_ids)
        if reason is not None:
            rejected.append((selected_id, reason))
            hard_violation = True
            return False
        return True

    for claim_id in selection.reference_claim_ids:
        if _resolve(claim_id, preanalysis.allowed_claim_ids, "claim"):
            accepted_claim_ids.append(claim_id)
    for mechanism_id in selection.mechanism_ids:
        if _resolve(mechanism_id, preanalysis.allowed_mechanism_ids, "mechanism"):
            accepted_mechanism_ids.append(mechanism_id)
    for calculation_id in selection.calculation_ids:
        if preanalysis.intent != "calculation":
            rejected.append((calculation_id, f"расчёт недопустим для интента {preanalysis.intent!r}"))

    return ValidationOutcome(
        accepted_claim_ids=tuple(accepted_claim_ids),
        accepted_mechanism_ids=tuple(accepted_mechanism_ids),
        rejected=tuple(rejected),
        clarification_keys=tuple(selection.clarification_keys),
        refused=hard_violation,
        error_code="policy_refused" if hard_violation else None,
    )


def check_claim_mutation(card: "EvidenceCard", proposed_text: str) -> str | None:
    """Смысловая проверка одной карточки (мастер-промпт §8, перечень мутаций выше): `proposed_text` — то, что
    промпт модели собирался вывести со ссылкой на `card`, сверяется с `card.text`. Совпадает — `None`; не
    совпадает — короткая причина отказа (не текст пользователю, он уходит в `ValidationOutcome.rejected`/
    журнал). Для `kind="mechanism"` отдельно проверяется, что `proposed_text` не превращает «общий возможный
    механизм» в утверждение причины (гипотеза → причина).

    `card.applicability` НЕ подмешивается (Правка, раунд 1, Important 2) — это служебная классификация
    происхождения/области карточки, не содержание факта; число или год из неё не легитимизирует подмену числа
    в самом тексте.

    Эвристика на регулярных выражениях (докстринг модуля, «Реализация», п.3) — не полный семантический разбор:
    ловит подмену тега/номера (с учётом порядка, но без ложных срабатываний на простой повтор одного и того же
    числа — Правка, раунд 2; плюс правило «пропажа + избыточный повтор другого числа» — Правка, раунд 3, ниже)/
    единицы (привязанной к числу)/отрицания (по количеству маркеров, не только по наличию — Правка, раунд 2)/
    верх-низ, опасное управляющее действие в справочном/гипотетическом тексте и превращение гипотезы в причину.
    Чего не ловит — перефразирование без изменения этих токенов (намеренно: пересказ другими словами с тем же
    числом/единицей/отрицанием, в том же порядке и количестве, не мутация).

    Границы проверки номера (Правка, раунд 3): ПРОПУСК числа карточки в `proposed_text` сам по себе допустим —
    предложенный текст может упоминать не все числа, это не мутация (пример: карточка «7, 9» → пересказ только
    про «7» — `None`). Мутацией пропуск становится, только если ОДНОВРЕМЕННО какое-то другое число встречается
    в `proposed_text` ЧАЩЕ, чем в карточке — то есть пропавшее число похоже на замену, замаскированную лишним
    повтором (пример: карточка «7 … 9» → «7 … 7» — число 9 пропало, а 7 повторилось сверх своего счёта в
    карточке — мутация; но «7» → «7 … 7», без пропажи, остаётся `None`).

    Эта функция — ЗАЩИТНАЯ эвристика про сам приём «валидная ссылка с ложным смыслом» (мастер-промпт §8,
    ОСОБЫЙ ФОКУС ПРОВЕРКИ №4 плана), не действующий фильтр живого текста модели: в продукте `LlmSelection`
    несёт только id (`reference_claim_ids`/`mechanism_ids`/…), свободного текста модели в этой архитектуре
    структурно не существует вовсе (см. `models.LlmSelection`, `test_llm_selection_has_no_free_text_field_
    for_model_output`). `check_claim_mutation` остаётся переиспользуемым инструментом проверки произвольного
    текста против карточки — для тестов и на случай будущего расширения формата (докстринг модуля, «Реализация»,
    п.3), а не точкой, через которую сегодня реально проходит текст модели."""
    card_text = card.text

    proposed_tags = _tags(proposed_text)
    if proposed_tags - _tags(card_text):
        return "подмена тега: предложенный текст ссылается на подпись, которой нет в карточке"

    proposed_numbers = _number_sequence(proposed_text)
    # сверка — по схлопнутым до первого появления последовательностям (Правка, раунд 2): повтор уже
    # встреченного числа в корректном пересказе не должен ломать проверку, а перестановка двух РАЗНЫХ чисел
    # (даже если одно из них повторяется в тексте ещё раз) — всё равно подпоследовательность первых появлений,
    # не совпадающая по порядку, и мутация не исчезает
    if proposed_numbers and not _is_order_preserving_subsequence(
            _dedupe_first_occurrence(proposed_numbers), _dedupe_first_occurrence(_number_sequence(card_text))):
        return ("подмена номера: числа предложенного текста не совпадают с карточкой по составу или порядку")
    # дедупликация выше открывает свой пробел (Правка, раунд 3): число карточки может ПРОПАСТЬ целиком, а его
    # место — занять лишний повтор ДРУГОГО числа карточки («…№7…№9…» → «…№7…№7…»), тогда схлопнутая
    # последовательность предложенного текста (`[7]`) тривиально оказывается подпоследовательностью схлопнутой
    # последовательности карточки (`[7, 9]`) и мутация проходит мимо проверки выше. Отдельное правило: ПРОПАЛО
    # хотя бы одно число карточки целиком И какое-то число встречается в предложенном тексте ЧАЩЕ, чем в
    # карточке, — вместе это подмена (просто пропуск без избыточного повтора — не мутация, см. докстринг)
    if proposed_numbers:
        card_counts = Counter(_number_sequence(card_text))
        proposed_counts = Counter(proposed_numbers)
        card_number_vanished = bool(set(card_counts) - set(proposed_counts))
        some_number_over_repeated = any(proposed_counts[n] > card_counts.get(n, 0) for n in proposed_counts)
        if card_number_vanished and some_number_over_repeated:
            return ("подмена номера: число карточки пропало целиком, а другое число предложенного текста "
                    "встречается чаще, чем в карточке — похоже на замену, замаскированную повтором")

    card_units = _units(card_text)
    proposed_units = _units(proposed_text)
    if proposed_units - card_units:
        return "подмена единицы измерения: такой единицы в карточке нет"
    card_pairs = _number_unit_pairs(card_text)
    proposed_pairs = _number_unit_pairs(proposed_text)
    proposed_bare_numbers = set(proposed_numbers)
    for number, units in card_pairs.items():
        if number in proposed_bare_numbers and proposed_pairs.get(number, set()) != units:
            return f"подмена или потеря единицы измерения у числа {number}"

    if _negation_marker_total(card_text) != _negation_marker_total(proposed_text):
        return "подмена отрицания относительно карточки"
    if _UPPER_RE.search(proposed_text) and not _UPPER_RE.search(card_text) and _LOWER_RE.search(card_text):
        return "подмена верх/низ относительно карточки"
    if _LOWER_RE.search(proposed_text) and not _LOWER_RE.search(card_text) and _UPPER_RE.search(card_text):
        return "подмена верх/низ относительно карточки"
    if _CONTROL_RE.search(proposed_text):
        return "опасное управляющее действие недопустимо для справочной/гипотетической карточки"
    if card.kind == "mechanism" and _CAUSE_RE.search(proposed_text) and not _CAUSE_RE.search(card_text):
        return "общий возможный механизм представлен как установленная причина"
    return None


def check_calculation_consistency(result: "CalculationResult", preanalysis: "Preanalysis") -> str | None:
    """Вычисление `calculations.run_calculation` не попадает в ответ, если его база противоречит
    `preanalysis` (другой период/участок/сорт, чем подтвердил технолог) — короткая причина отказа или `None`.
    `result.value is None` (не хватает базы) — не противоречие само по себе, это `limitation`
    (`rendering.render_calculation_section` решает это отдельно, не эта функция)."""
    if result.value is None:
        return None
    conflicting = _CALCULATION_RELEVANT_MISSING & set(preanalysis.missing_fields)
    if conflicting:
        return ("расчёт использует базу, которую преданализ отметил как неподтверждённую: "
                + ", ".join(sorted(conflicting)))
    if preanalysis.decision not in ("ready", "limited_answer"):
        return f"преданализ не подтвердил готовность расчёта: decision={preanalysis.decision!r}"
    return None
