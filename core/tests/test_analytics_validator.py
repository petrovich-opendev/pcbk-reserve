"""Проверка выбора LLM КДМ-1 (план КДМ1, Task 3b; `docs/kdm1/INTERFACES.md`, `analytics/validator.py`).

Без зависимости от корпуса/SQLite — `validate_selection`/`check_claim_mutation`/`check_calculation_consistency`
работают над уже построенными `EvidenceCard`/`Preanalysis`/`LlmSelection`/`CalculationResult` (`models.py`),
которые здесь собираются вручную фабриками ниже (не через `preanalysis.build_evidence_pack`/`knowledge.py` —
эти модули, Task 3a/Task 1, не входят в область этого файла). Теги — только абстрактные id (`CLAIM-FAKE-*`,
`MECH-FAKE-*`), без подписей предприятия."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pcbk_core.analytics.models import (
    EvidenceCard,
    EvidenceRef,
    LlmSelection,
    PdfPageLocator,
    Preanalysis,
)
from pcbk_core.analytics.validator import (
    APPROVED_CRITICAL_FACT_STATES,
    MAX_SELECTION_RETRIES,
    RENDERABLE_PUBLICATION_STATES,
    RESTRICTED_SOURCE_IDS,
    check_calculation_consistency,
    check_claim_mutation,
    validate_selection,
)

try:
    from pcbk_core.analytics.models import CalculationResult
except ImportError:  # pragma: no cover — на случай, если CalculationResult ещё TYPE_CHECKING-only у Task 0
    CalculationResult = None


CORPUS_V1 = "2.0:aaaa111122223333"
CORPUS_V2 = "2.0:bbbb444455556666"

KDM1_PACKAGE_DIR = os.environ.get("KDM1_PACKAGE_DIR", "")
real_corpus = pytest.mark.skipif(not KDM1_PACKAGE_DIR, reason="нужен KDM1_PACKAGE_DIR — пакет заказчика вне репозитория")


def _ref(**over) -> EvidenceRef:
    base = dict(source_id="SRC-FAKE-001", fragment_id="FRG-FAKE-001",
               locator=PdfPageLocator(type="pdf_page", page=1), corpus_version=CORPUS_V1, limitation="")
    base.update(over)
    return EvidenceRef(**base)


def _claim(**over) -> EvidenceCard:
    base = dict(id="CLAIM-FAKE-001", kind="claim",
               text="Выдуманная карточка про узел №7, давление 80 кН/м.",
               risk_level="non_operational_reference", publication_state="desk_verified_draft",
               allowed_render_mode="attributed_reference", applicability="KDM1_PCBK, тест",
               critical_local_fact=False, evidence_refs=())
    base.update(over)
    return EvidenceCard(**base)


def _mechanism(**over) -> EvidenceCard:
    base = dict(id="MECH-FAKE-001", kind="mechanism", text="Выдуманный общий механизм про теплообмен.",
               risk_level="non_operational_reference", publication_state="desk_verified_draft",
               allowed_render_mode="qualified_general_hypothesis", applicability="generic_mechanism_only",
               critical_local_fact=False, evidence_refs=())
    base.update(over)
    return EvidenceCard(**base)


def _preanalysis(**over) -> Preanalysis:
    base = dict(scope="KDM1_PCBK", intent="definition", decision="ready", resolved_terms=[], process_ids=[],
               conflict_ids=[], missing_fields=[], risk="reference", corpus_version=CORPUS_V1,
               evidence_pack_id="PACK-1", allowed_claim_ids=["CLAIM-FAKE-001"], allowed_mechanism_ids=[],
               data_access_ready=False, llm_export_policy="public_only")
    base.update(over)
    return Preanalysis(**base)


def _selection(**over) -> LlmSelection:
    base = dict(reference_claim_ids=[], mechanism_ids=[], clarification_keys=[], calculation_ids=[])
    base.update(over)
    return LlmSelection(**base)


# --- validate_selection: базовый приём/отказ ---

def test_validate_selection_accepts_claim_in_pack_and_allowed():
    card = _claim()
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ("CLAIM-FAKE-001",)
    assert outcome.rejected == ()
    assert outcome.refused is False
    assert outcome.error_code is None


def test_validate_selection_accepts_mechanism_in_pack_and_allowed():
    card = _mechanism()
    pre = _preanalysis(allowed_claim_ids=[], allowed_mechanism_ids=["MECH-FAKE-001"])
    sel = _selection(mechanism_ids=["MECH-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_mechanism_ids == ("MECH-FAKE-001",)
    assert outcome.refused is False


# --- отсутствующая карточка (критичный сценарий задачи) ---

def test_validate_selection_rejects_id_not_in_allowed_ids_even_if_in_pack():
    """Карточка физически есть в `pack`, но преданализ её не разрешал (`allowed_claim_ids` пуст) — отказ, не
    молчаливый пропуск: модель не может «протащить» карточку обходом списка разрешённых id."""
    card = _claim()
    pre = _preanalysis(allowed_claim_ids=[])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ()
    assert outcome.rejected == (("CLAIM-FAKE-001", "id не входит в разрешённый пакет преданализа"),)
    assert outcome.refused is False  # не нарушение политики — модель просто предложила недопустимый id


def test_validate_selection_rejects_id_allowed_but_absent_from_pack():
    """Двойная сверка (докстринг `validate_selection`): id в `allowed_claim_ids`, но пакет доказательств,
    переданный именно этому вызову, карточки не содержит — отказ, схема `llm-selection.schema.json` сама по
    себе не единственная защита."""
    pre = _preanalysis(allowed_claim_ids=["CLAIM-GHOST"])
    sel = _selection(reference_claim_ids=["CLAIM-GHOST"])
    outcome = validate_selection(sel, pre, ())
    assert outcome.accepted_claim_ids == ()
    assert outcome.rejected[0][0] == "CLAIM-GHOST"


def test_validate_selection_rejects_claim_id_proposed_as_mechanism():
    """id карточки вида `claim` предложен в `mechanism_ids` (даже если формально числится в
    `allowed_mechanism_ids` по ошибке преданализа) — отказ по несовпадению `kind`, не тихий показ."""
    card = _claim(id="CLAIM-FAKE-001")
    pre = _preanalysis(allowed_claim_ids=[], allowed_mechanism_ids=["CLAIM-FAKE-001"])
    sel = _selection(mechanism_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_mechanism_ids == ()
    assert outcome.rejected[0][0] == "CLAIM-FAKE-001"
    assert "другого вида" in outcome.rejected[0][1]  # Правка, раунд 1, Minor 1 — не общее «отсутствует в пакете»


# --- отзыв прав/карточки между поиском и ответом (ОСОБЫЙ ФОКУС ПРОВЕРКИ №2) ---

def test_validate_selection_blocks_revoked_publication_state_at_issuance_time():
    """Повторная проверка «в момент выдачи»: карточка, которую отдаёт `pack` этому конкретному вызову, уже не в
    допустимом публикационном состоянии (имитирует отзыв между поиском и ответом) — блокируется, хотя она была
    разрешена преданализом."""
    card = _claim(publication_state="revoked_by_reviewer")
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ()
    assert outcome.refused is True
    assert outcome.error_code == "policy_refused"
    assert "revoked_by_reviewer" in outcome.rejected[0][1] or "публикационный статус" in outcome.rejected[0][1]


def test_validate_selection_old_evidence_pack_does_not_grant_access_after_corpus_republish():
    """«Старый пакет доказательств не даёт нового разрешения»: ссылка карточки несёт версию корпуса,
    зафиксированную на момент поиска; преданализ в этом вызове сообщает уже другую (новую) версию — значит,
    корпус был переопубликован между поиском и ответом, карточка не проходит повторную проверку."""
    card = _claim(evidence_refs=(_ref(corpus_version=CORPUS_V1),))
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"], corpus_version=CORPUS_V2)
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ()
    assert outcome.refused is True
    assert "верси" in outcome.rejected[0][1]


def test_validate_selection_matching_corpus_version_passes():
    card = _claim(evidence_refs=(_ref(corpus_version=CORPUS_V1),))
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"], corpus_version=CORPUS_V1)
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ("CLAIM-FAKE-001",)


def test_validate_selection_rejects_card_without_applicability():
    card = _claim(applicability="")
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ()
    assert outcome.refused is True


# --- запрет местных нормативов без согласования ---

def test_validate_selection_blocks_critical_local_fact_without_approval():
    card = _claim(critical_local_fact=True, publication_state="desk_verified_draft")
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ()
    assert outcome.refused is True
    assert outcome.error_code == "policy_refused"


def test_validate_selection_allows_critical_local_fact_with_explicit_review_approval():
    """`reviewer_approved` — состояние, зарезервированное для будущего `reviews.py` (Task 7, ещё не
    реализован): без него никакая карточка в реальном корпусе так не помечена, здесь — ручная проверка контракта
    «согласование снимает запрет»."""
    assert "reviewer_approved" in APPROVED_CRITICAL_FACT_STATES
    card = _claim(critical_local_fact=True, publication_state="reviewer_approved")
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ("CLAIM-FAKE-001",)
    assert outcome.refused is False


def test_validate_selection_allows_critical_local_fact_via_approved_card_ids():            # Task 7, шаг 2
    """Дополнение контроллера: действующее согласование (`reviews.approved_claim`, собранное `service.py` ДО
    вызова) снимает запрет и без `publication_state == "reviewer_approved"` — через новый параметр
    `approved_card_ids`, не через саму карточку корпуса."""
    card = _claim(critical_local_fact=True, publication_state="desk_verified_draft")
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,), approved_card_ids=frozenset({"CLAIM-FAKE-001"}))
    assert outcome.accepted_claim_ids == ("CLAIM-FAKE-001",)
    assert outcome.refused is False


def test_validate_selection_approved_card_ids_default_empty_keeps_old_behaviour():
    card = _claim(critical_local_fact=True, publication_state="desk_verified_draft")
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))             # approved_card_ids не передан — прежнее поведение
    assert outcome.accepted_claim_ids == ()
    assert outcome.refused is True


def test_validate_selection_approved_card_ids_does_not_cover_a_different_card():
    card = _claim(critical_local_fact=True, publication_state="desk_verified_draft")
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    # согласование есть, но для ДРУГОГО id — эту карточку не покрывает
    outcome = validate_selection(sel, pre, (card,), approved_card_ids=frozenset({"CLAIM-FAKE-999"}))
    assert outcome.accepted_claim_ids == ()
    assert outcome.refused is True


def test_validate_selection_approved_card_ids_does_not_bypass_restricted_source():
    """Согласование критического факта не обходит PUB-10/11 — `_card_block_reason` проверяет источники
    раньше `critical_local_fact`, порядок проверок не меняется этим параметром."""
    source_id = sorted(RESTRICTED_SOURCE_IDS)[0]
    card = _claim(critical_local_fact=True, publication_state="desk_verified_draft",
                 evidence_refs=(_ref(source_id=source_id),))
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,), approved_card_ids=frozenset({"CLAIM-FAKE-001"}))
    assert outcome.accepted_claim_ids == ()
    assert outcome.refused is True


# --- PUB-10/PUB-11 не через LLM ---

@pytest.mark.parametrize("source_id", sorted(RESTRICTED_SOURCE_IDS))
def test_validate_selection_blocks_claims_citing_restricted_iso_sources(source_id):
    card = _claim(evidence_refs=(_ref(source_id=source_id),))
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ()
    assert outcome.refused is True
    assert source_id in outcome.rejected[0][1]


def test_validate_selection_allows_non_restricted_sources():
    card = _claim(evidence_refs=(_ref(source_id="PUB-02"),))
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001"])
    outcome = validate_selection(sel, pre, (card,))
    assert outcome.accepted_claim_ids == ("CLAIM-FAKE-001",)


# --- calculation_ids: допустимость только для интента calculation ---

def test_validate_selection_rejects_calculation_id_when_intent_is_not_calculation():
    pre = _preanalysis(intent="definition")
    sel = _selection(calculation_ids=["dry_solid_flow"])
    outcome = validate_selection(sel, pre, ())
    assert outcome.rejected == (("dry_solid_flow", "расчёт недопустим для интента 'definition'"),)


def test_validate_selection_allows_calculation_id_when_intent_is_calculation():
    pre = _preanalysis(intent="calculation")
    sel = _selection(calculation_ids=["dry_solid_flow"])
    outcome = validate_selection(sel, pre, ())
    assert outcome.rejected == ()


# --- clarification_keys: проходят как есть (формат уже сузила схема LlmSelection) ---

def test_validate_selection_passes_through_clarification_keys():
    sel = _selection(clarification_keys=["unit", "period"])
    outcome = validate_selection(sel, _preanalysis(), ())
    assert set(outcome.clarification_keys) == {"unit", "period"}


def test_validate_selection_empty_selection_is_not_refused():
    """Пустой выбор (модель ничего не предложила) — не нарушение политики само по себе; решение «повтор или
    ограниченный ответ» — за `service.py` (докстринг модуля), не за этой функцией."""
    outcome = validate_selection(_selection(), _preanalysis(), ())
    assert outcome.refused is False
    assert outcome.accepted_claim_ids == ()
    assert outcome.accepted_mechanism_ids == ()


# --- смешанный сценарий ---

def test_validate_selection_mixed_accept_and_reject_in_one_call():
    good = _claim(id="CLAIM-FAKE-001")
    bad = _claim(id="CLAIM-FAKE-002", publication_state="withdrawn")
    pre = _preanalysis(allowed_claim_ids=["CLAIM-FAKE-001", "CLAIM-FAKE-002"])
    sel = _selection(reference_claim_ids=["CLAIM-FAKE-001", "CLAIM-FAKE-002"])
    outcome = validate_selection(sel, pre, (good, bad))
    assert outcome.accepted_claim_ids == ("CLAIM-FAKE-001",)
    assert outcome.rejected == (("CLAIM-FAKE-002", "публикационный статус карточки не допускает показа сейчас: "
                                 "'withdrawn'"),)
    assert outcome.refused is True  # один опасный отказ — весь исход помечен политикой, не только эта карточка


# --- MAX_SELECTION_RETRIES / публичные константы контракта ---

def test_max_selection_retries_constant_is_two():
    assert MAX_SELECTION_RETRIES == 2


def test_renderable_publication_states_include_current_real_corpus_value():
    """Единственное состояние, в котором сейчас опубликованы карточки настоящего корпуса заказчика
    (claims.jsonl/mechanisms.jsonl: `publication_state == "desk_verified_draft"` у всех записей) — не должно
    случайно оказаться вне белого списка."""
    assert "desk_verified_draft" in RENDERABLE_PUBLICATION_STATES


# --- check_claim_mutation: подмена номера/единицы/отрицания (ОСОБЫЙ ФОКУС ПРОВЕРКИ №4) ---

def test_check_claim_mutation_identical_text_passes():
    card = _claim(text="Карточка про узел №7, давление 80 кН/м, нельзя переводить в бары.")
    assert check_claim_mutation(card, card.text) is None


def test_check_claim_mutation_allows_paraphrase_preserving_facts():
    """Перефразировка, сохраняющая и состав, и ПОРЯДОК чисел карточки (7, затем 80) — не мутация."""
    card = _claim(text="Карточка про узел №7, давление 80 кН/м, нельзя переводить в бары.")
    proposed = "Для узла №7 указано давление 80 кН/м; переводить его в бары нельзя."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_detects_number_swap():
    card = _claim(text="Карточка про узел №7.")
    assert check_claim_mutation(card, "Карточка про узел №9.") is not None


# --- подмена тега (Правка, раунд 1, Important 1) — буквенно-цифровой токен, не только число ---

def test_check_claim_mutation_detects_foreign_tag_substitution():
    """`20FAKE_QT31` → `20FAKE_QP31` — те же цифры-префикс, другая буква: старая проверка (только числа) этого
    не ловила вовсе."""
    card = _claim(text="Карточка про подпись 20FAKE_QT31 на модели.")
    proposed = "Карточка про подпись 20FAKE_QP31 на модели."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_allows_identical_tag_reference():
    card = _claim(text="Карточка про подпись 20FAKE_QT31 на модели.")
    proposed = "На модели упомянута подпись 20FAKE_QT31."
    assert check_claim_mutation(card, proposed) is None


# --- номер: порядок, не только множество (Правка, раунд 1, Important 2) ---

def test_check_claim_mutation_detects_number_permutation_swap():
    """Множество чисел `{7, 9}` совпадает в обе стороны — только порядок ловит подмену объекта, к которому
    привязан каждый номер."""
    card = _claim(text="Узел №7 и датчик №9 относятся к разным линиям.")
    proposed = "Узел №9 и датчик №7 относятся к разным линиям."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_allows_same_order_paraphrase_with_two_numbers():
    card = _claim(text="Узел №7 и датчик №9 относятся к разным линиям.")
    proposed = "И узел №7, и датчик №9 — на разных линиях."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_applicability_numbers_do_not_legitimize_text_number_swap():
    """Число/год из `applicability` (диапазон «2024-2025») не попадает в сверку вовсе — подмена номера в
    `card.text` не прощается только потому, что похожая цифра встречается в applicability."""
    card = _claim(text="Карточка про узел №7.", applicability="подтверждено на период 2024-2025")
    assert check_claim_mutation(card, "Карточка про узел №9.") is not None


# --- номер: повтор того же числа не ломает проверку (Правка, раунд 2, Important) ---
# Найдено повторным ревью 0d36825: общий итератор _is_order_preserving_subsequence продвигался после первого
# совпадения и не находил ВТОРОЕ упоминание того же числа — корректный пересказ, повторяющий число карточки,
# ложно блокировался как подмена номера.

def test_check_claim_mutation_allows_correct_paraphrase_repeating_same_number():
    """Пример из ревью: «Узел №7 — главный датчик линии.» → «Узел №7 (ещё раз: узел 7) — главный датчик
    линии.» — повтор того же числа, не подмена."""
    card = _claim(text="Узел №7 — главный датчик линии.")
    proposed = "Узел №7 (ещё раз: узел 7) — главный датчик линии."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_allows_correct_paraphrase_repeating_same_number_with_unit():
    """Второй пример из ревью: «Давление 80 кН/м.» → «Давление 80 кН/м составляет 80 кН/м согласно
    карточке.» — число и единица повторены, не подменены."""
    card = _claim(text="Давление 80 кН/м.")
    proposed = "Давление 80 кН/м составляет 80 кН/м согласно карточке."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_detects_permutation_even_with_a_repeated_number():
    """Перестановка двух РАЗНЫХ чисел не перестаёт быть мутацией только потому, что одно из них к тому же
    повторяется в тексте («№7 … №9 … №7» → «№9 … №7 … №9» — после схлопывания повторов первые появления
    остаются [7, 9] и [9, 7] соответственно, разный порядок)."""
    card = _claim(text="Узел №7, датчик №9, снова узел №7 для проверки.")
    proposed = "Узел №9, датчик №7, снова узел №9 для проверки."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_still_detects_plain_number_swap_after_dedup_fix():
    """Контроль регрессии: обычная (без повторов) подмена номера всё ещё ловится после схлопывания повторов."""
    card = _claim(text="Карточка про узел №7.")
    assert check_claim_mutation(card, "Карточка про узел №9.") is not None


# --- номер: пропажа числа + избыточный повтор другого — тоже подмена (Правка, раунд 3) ---
# Найдено повторным ревью 591e9b4: дедупликация раунда 2 открыла свой пробел — «…№7…№9…» → «…№7…№7…» (9
# пропало, его место занял лишний повтор 7) схлопывается в proposed=[7], card=[7,9], и [7] тривиально
# подпоследовательность [7, 9] — мутация проходила мимо проверки. Пять обязательных случаев ревью:

def test_check_claim_mutation_pure_repeat_without_omission_is_not_a_mutation():
    """«7» → «7 … 7» (повтор без пропажи какого-либо числа карточки) — не мутация."""
    card = _claim(text="Узел №7 — главный датчик линии.")
    proposed = "Узел №7, и ещё раз №7 для ясности, — главный датчик линии."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_pure_omission_without_over_repeat_is_not_a_mutation():
    """«7, 9» → «7» (чистый пропуск одного числа, без избыточного повтора другого) — не мутация."""
    card = _claim(text="Узел №7 и датчик №9 относятся к разным линиям.")
    proposed = "Узел №7 относится к своей линии."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_plain_permutation_without_repeat_is_still_a_mutation():
    """«7 … 9» → «9 … 7» — обычная перестановка без повторов всё ещё ловится (контроль регрессии к правилу
    этого раунда — не должно случайно перестать работать)."""
    card = _claim(text="Узел №7 и датчик №9 относятся к разным линиям.")
    proposed = "Узел №9 и датчик №7 относятся к разным линиям."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_permutation_with_repeat_is_still_a_mutation():
    """«7 … 9 … 7» → «9 … 7 … 9» — перестановка с повтором всё ещё ловится (контроль регрессии к правилу
    раунда 2, не должно случайно перестать работать)."""
    card = _claim(text="Узел №7, датчик №9, снова узел №7 для проверки.")
    proposed = "Узел №9, датчик №7, снова узел №9 для проверки."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_vanished_number_masked_by_extra_repeat_is_a_mutation():
    """Сам пробел, который закрывает этот раунд: «…№7…№9…» → «…№7…№7…» — число 9 пропало целиком, а 7
    встречается в предложенном тексте чаще (дважды), чем в карточке (один раз) — подмена."""
    card = _claim(text="Узел №7 работает совместно с датчиком №9 на одной линии.")
    proposed = "Узел №7 работает совместно с датчиком №7 на одной линии."
    assert check_claim_mutation(card, proposed) is not None


# --- единица: привязана к числу, не просто множество (Правка, раунд 1, Important 3) ---

def test_check_claim_mutation_detects_unit_swap():
    card = _claim(text="Линейное давление 80 кН/м.")
    assert check_claim_mutation(card, "Линейное давление 80 бар.") is not None


def test_check_claim_mutation_detects_bar_to_kpa_replacement_for_same_number():
    """`бар` → `кПа` — конкретная подмена, названная в ревью: `кПа` отсутствовала в списке единиц вовсе."""
    card = _claim(text="Давление в линии — 80 бар.")
    proposed = "Давление в линии — 80 кПа."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_detects_unit_disappearance_for_same_number():
    card = _claim(text="Давление в линии — 80 бар.")
    proposed = "Давление в линии составляет 80."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_detects_unit_swap_between_two_numbers_both_known_to_card():
    """Обе единицы уже встречаются в карточке (для разных чисел) — простая проверка «появилась ли новая
    единица» не поймает подмену; нужна привязка конкретной единицы к конкретному числу."""
    card = _claim(text="Давление до пресса — 80 бар, давление после пресса — 2 кПа.")
    proposed = "Давление до пресса — 80 кПа, давление после пресса — 2 бар."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_allows_same_unit_paraphrase():
    card = _claim(text="Давление в линии — 80 бар.")
    proposed = "В линии наблюдается давление 80 бар."
    assert check_claim_mutation(card, proposed) is None


# --- отрицание: несколько форм, появление и исчезновение (Правка, раунд 1, Important 4) ---

def test_check_claim_mutation_detects_negation_swap():
    card = _claim(text="Показание нельзя переводить в бары напрямую.")
    assert check_claim_mutation(card, "Показание можно переводить в бары напрямую.") is not None


def test_check_claim_mutation_detects_negation_via_bez_disappearing():
    card = _claim(text="Вывод сделан без подтверждения технологом.")
    proposed = "Вывод подтверждён технологом."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_detects_negation_via_net_disappearing():
    card = _claim(text="Данных за этот период нет.")
    proposed = "Данные за этот период актуальны."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_detects_negation_via_otsutstvie_disappearing():
    card = _claim(text="Отсутствует подтверждение проверяющего.")
    proposed = "Подтверждение проверяющего получено."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_bez_word_boundary_does_not_false_positive_on_bezopasnost():
    """«Безопасности» не содержит отрицания «без» как отдельного слова — граница слова обязательна, иначе
    обычное существительное ложно считалось бы отрицанием (здесь ни в карточке, ни в предложенном тексте нет
    других слов-отрицаний, так что ложное срабатывание на «без…» было бы единственной причиной отказа)."""
    card = _claim(text="Требования безопасности для персонала.")
    proposed = "Требования надёжности для персонала."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_allows_paraphrase_keeping_negation():
    card = _claim(text="Вывод сделан без подтверждения технологом.")
    proposed = "Технолог вывод не подтвердил."
    assert check_claim_mutation(card, proposed) is None


# --- отрицание по количеству маркеров, не только по наличию (Правка, раунд 2, Out-of-scope) ---

def test_check_claim_mutation_detects_disappearance_of_one_of_two_negations():
    """Было: `bool` до/после — оба `True` (хоть один маркер есть), исчезновение ОДНОГО из ДВУХ отрицаний
    оставалось незамеченным. Теперь — по количеству маркеров (2 → 1)."""
    card = _claim(text="Без подтверждения показание нельзя использовать.")
    proposed = "Без подтверждения показание можно использовать."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_allows_same_count_negation_word_substitution():
    """Замена слова-отрицания на другое (не о количестве) — не мутация этой проверки: счёт маркеров (1 и 1)
    совпадает, хотя сами слова разные."""
    card = _claim(text="Показание нельзя переводить в бары напрямую.")
    proposed = "Показание не переводится в бары напрямую."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_negation_count_ignores_common_words_with_substring():
    """«необходимо», «нередко», «безопасности» не считаются маркерами отрицания границами слова — проверка не
    ломается при переходе с `bool` на подсчёт количества маркеров."""
    card = _claim(text="Необходимо соблюдать требования безопасности, это делают нередко.")
    proposed = "Требования безопасности соблюдаются нередко — это необходимо."
    assert check_claim_mutation(card, proposed) is None


def test_check_claim_mutation_detects_up_down_swap():
    card = _claim(text="Сортировка нижней сетки.")
    assert check_claim_mutation(card, "Сортировка верхней сетки.") is not None


def test_check_claim_mutation_detects_dangerous_control_action():
    card = _claim(text="Справочная карточка про давление пара.")
    assert check_claim_mutation(card, "Увеличьте уставку давления пара немедленно.") is not None


def test_check_claim_mutation_detects_hypothesis_turned_into_established_cause():
    card = _mechanism(text="Общий возможный механизм с неустановленной причиной отклонения на КДМ-1.")
    proposed = "Это точно является причиной отклонения на КДМ-1."
    assert check_claim_mutation(card, proposed) is not None


def test_check_claim_mutation_plain_reference_card_is_not_subject_to_cause_check():
    """Переход «гипотеза → причина» проверяется только для `kind='mechanism'` (докстринг функции) — обычная
    справочная карточка такой фразой не блокируется этим конкретным правилом (другие правила могут сработать
    независимо, здесь текст подобран, чтобы не задеть их)."""
    card = _claim(text="Справочное определение массы 1 м².", applicability="generic_or_document_literal")
    proposed = "Справочное определение массы 1 м² является причиной путаницы в терминах."
    assert check_claim_mutation(card, proposed) is None


# --- check_calculation_consistency ---

@pytest.mark.skipif(CalculationResult is None, reason="models.CalculationResult недоступен")
def test_check_calculation_consistency_passes_when_nothing_missing():
    result = CalculationResult(calculation_id="dry_solid_flow", inputs=(("расход", 200.0, "м3/ч"),),
                               value=8.0, unit="т/ч", assumptions=())
    pre = _preanalysis(decision="ready", missing_fields=[])
    assert check_calculation_consistency(result, pre) is None


@pytest.mark.skipif(CalculationResult is None, reason="models.CalculationResult недоступен")
def test_check_calculation_consistency_blocks_when_relevant_field_missing():
    result = CalculationResult(calculation_id="dry_solid_flow", inputs=(("расход", 200.0, "м3/ч"),),
                               value=8.0, unit="т/ч", assumptions=())
    pre = _preanalysis(decision="ready", missing_fields=["period"])
    reason = check_calculation_consistency(result, pre)
    assert reason is not None and "period" in reason


@pytest.mark.skipif(CalculationResult is None, reason="models.CalculationResult недоступен")
def test_check_calculation_consistency_none_value_is_not_a_conflict():
    """Отсутствие значения (не хватает базы) — не противоречие само по себе: это `limitation`
    (`rendering.render_calculation_section`), не дело этой функции."""
    result = CalculationResult(calculation_id="dry_grammage", inputs=(), value=None, unit=None, assumptions=(),
                               missing_basis=("база сухости не указана",))
    pre = _preanalysis(decision="needs_clarification", missing_fields=["basis"])
    assert check_calculation_consistency(result, pre) is None


@pytest.mark.skipif(CalculationResult is None, reason="models.CalculationResult недоступен")
def test_check_calculation_consistency_blocks_when_preanalysis_not_ready():
    result = CalculationResult(calculation_id="dry_solid_flow", inputs=(), value=8.0, unit="т/ч", assumptions=())
    pre = _preanalysis(decision="refused", missing_fields=[])
    assert check_calculation_consistency(result, pre) is not None


# --- попытка протащить текст модели в ответ: структурная проверка контракта LlmSelection ---

def test_llm_selection_has_no_free_text_field_for_model_output():
    """`LlmSelection` несёт только списки id — у модели структурно нет канала для свободного текста (мастер-
    промпт §8: «модель не создает местные нормы, ссылки, уставки»); это сам контракт, не поведение валидатора."""
    assert set(LlmSelection.model_fields) == {
        "reference_claim_ids", "mechanism_ids", "clarification_keys", "calculation_ids",
    }


def test_llm_selection_rejects_extra_free_text_field():
    """Попытка протащить произвольное поле (например, якобы «текст модели») в `LlmSelection` — отказ схемы
    (`extra='forbid'`), не тихий отброс."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        LlmSelection(reference_claim_ids=[], mechanism_ids=[], clarification_keys=[], calculation_ids=[],
                    raw_model_text="системный промпт или рассуждения модели")


# --- RESTRICTED_SOURCE_IDS не расходится с настоящим sources.jsonl (Правка, раунд 1, Minor 3) ---
# Только локально под KDM1_PACKAGE_DIR (пропуск без переменной) — без настоящих данных в git.

@real_corpus
def test_real_corpus_restricted_source_ids_cover_all_llm_context_disallowed_sources():
    """Если в `sources.jsonl` пакета заказчика появится (или уже есть) запись с `llm_context_allowed=false`,
    не отражённая в `RESTRICTED_SOURCE_IDS`, этот тест падает — список констант модуля не должен тихо
    разойтись с настоящим корпусом."""
    path = Path(KDM1_PACKAGE_DIR) / "knowledge" / "sources.jsonl"
    disallowed_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("llm_context_allowed") is False:
            disallowed_ids.add(row["id"])
    missing = disallowed_ids - RESTRICTED_SOURCE_IDS
    assert not missing, (
        f"sources.jsonl: llm_context_allowed=false не отражено в RESTRICTED_SOURCE_IDS: {sorted(missing)}"
    )
