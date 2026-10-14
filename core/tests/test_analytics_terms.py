"""Разрешение терминов КДМ-1 (план КДМ1, Task 1; `docs/kdm1/INTERFACES.md`, `analytics/terms.py`).

Фикстура — синтетический корпус (`fixtures/kdm1_corpus.py`, теги только `20FAKE_*`). Настоящий корпус
заказчика — только через `KDM1_PACKAGE_DIR` (`pytest.mark.skipif` без неё): реальные термины-примеры здесь не
теги предприятия (`20{буквы}{цифры}`), а словарные русские слова и абстрактные id корпуса («масса», «COV»,
«cov», «pc-cvv», «coefficient-variation-statistical», «screen-lower»…) — ворота `\\b[0-9]{2}[A-Z]{2,4}[0-9]{2}`
их не ловит, они не подпадают под запрет имён тегов."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from fixtures.kdm1_corpus import build_corpus
from pcbk_core.analytics.knowledge import close, open_corpus
from pcbk_core.analytics.terms import (exact_tag_candidates, known_ambiguous_words, normalize_ru, resolve_term,
                                       resolve_term_exact_phrase)

KDM1_PACKAGE_DIR = os.environ.get("KDM1_PACKAGE_DIR", "")
real_corpus = pytest.mark.skipif(not KDM1_PACKAGE_DIR, reason="нужен KDM1_PACKAGE_DIR — пакет заказчика вне репозитория")


@pytest.fixture
def handle(tmp_path):
    h = open_corpus(str(build_corpus(tmp_path / "fake.seed.sqlite3")))
    yield h
    close(h)


def _insert_term(path: Path, term_id: str, preferred_ru: str, aliases: list[str], ambiguous: bool) -> None:
    """Добавляет к уже собранному фикстурой корпусу ровно один именованный термин — для сценариев, которым
    нужен конкретный, не пронумерованный шаблонно текст (общие `TERM-FAKE-NNN` различаются только числом в
    конце, что не годится для проверки непересекающихся/неоднозначных формулировок)."""
    conn = sqlite3.connect(path)
    try:
        payload = {
            "id": term_id, "preferred_ru": preferred_ru, "aliases": aliases,
            "definition": f"Тестовое определение «{preferred_ru}».", "source_refs": ["SRC-FAKE-001"],
            "ambiguous": ambiguous, "machine_scope": "KDM1_PCBK_related",
            "knowledge_state": "editorial_definition_or_unresolved_candidate",
            "local_operating_authority": False, "auto_merge_aliases": False,
        }
        conn.execute("INSERT INTO terms (id, payload) VALUES (?, ?)", (term_id, json.dumps(payload)))
        text = " ".join([preferred_ru, payload["definition"], *aliases])
        conn.execute("INSERT INTO kb_search (kind, id, text) VALUES ('term', ?, ?)", (term_id, text))
        conn.commit()
    finally:
        conn.close()


# --- normalize_ru ---

@pytest.mark.parametrize("a,b", [
    ("Массы", "масса"),      # падежные формы одного слова — общий нормализованный стем
    ("верхней", "верхняя"),
    ("нижней", "нижний"),
])
def test_normalize_ru_unifies_inflected_forms(a, b):
    assert normalize_ru(a) == normalize_ru(b)


def test_normalize_ru_lowercases_and_folds_yo():
    assert normalize_ru("ЁЖИК") == normalize_ru("ежик")


def test_normalize_ru_empty_and_punctuation_only_is_empty_string():
    assert normalize_ru("") == ""
    assert normalize_ru("  ---,,, ???  ") == ""


def test_normalize_ru_keeps_short_words_untouched():
    """Минимальная длина остатка (`_MIN_STEM`) не даёт стеммеру стирать короткие слова целиком."""
    assert normalize_ru("бак") == "бак"
    assert normalize_ru("пц") == "пц"


def test_normalize_ru_keeps_lone_digit_token():
    """№7/№8/№9 (Особый фокус проверки плана) — однозначный номер узла, не отбрасывается как «слишком
    короткое слово» (тот же стемм попадает в normalize_ru без дробления цифр)."""
    assert normalize_ru("№7") == "7"


# --- resolve_term: однозначный случай ---

def test_resolve_term_single_unambiguous_candidate_by_alias(handle):
    """Фикстурный псевдоним `втN` — единственный для своего термина (не делит общий текст «выдуманный термин»
    с соседями по номеру, в отличие от `preferred_ru`)."""
    result = resolve_term(handle, "вт1")
    assert result.candidate_ids == ["TERM-FAKE-001"]
    assert result.selected_id == "TERM-FAKE-001"
    assert result.requires_confirmation is False
    assert result.original == "вт1"


def test_resolve_term_no_match_requires_confirmation_not_silent_skip(handle):
    result = resolve_term(handle, "совершенно неизвестное слово")
    assert result.candidate_ids == []
    assert result.selected_id is None
    assert result.requires_confirmation is True


def test_resolve_term_empty_original_raises_value_error_not_pydantic_crash(handle):
    """Правка, раунд 1 (проверяющий): пустая строка нарушает контракт `ResolvedTerm.original` (`min_length=1`)
    — раньше это падало голым `pydantic.ValidationError` из глубины функции; теперь — понятный `ValueError`
    до попытки построить модель."""
    with pytest.raises(ValueError):
        resolve_term(handle, "")


def test_resolve_term_whitespace_only_original_does_not_raise(handle):
    """Строка из одних пробелов — не та же ошибка, что пустая строка (сама модель её принимает, `len>=1`):
    это осмысленный, хоть и ничего не находящий ввод — результат с `requires_confirmation=True`, не
    исключение. Пустая и «пробельная» строки должны вести себя по-разному согласованно, не произвольно."""
    result = resolve_term(handle, "   ")
    assert result.candidate_ids == []
    assert result.selected_id is None
    assert result.requires_confirmation is True
    assert result.original == "   "


def test_resolve_term_truncates_overlong_original(handle):
    long_text = "а" * 200
    result = resolve_term(handle, long_text)
    assert len(result.original) == 120


def test_resolve_term_ambiguous_flag_requires_confirmation_even_for_single_candidate(tmp_path):
    """Термин может быть единственным кандидатом и всё равно спорным (корпус сам знает об этом, `ambiguous`
    флаг) — ровно случай «масса» в настоящем корпусе (отдельный тест ниже, на реальных данных)."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_term(path, "AMBIG-ONLY", "Единственное неоднозначное слово", ["единственноеслово"], ambiguous=True)
    handle = open_corpus(str(path))
    try:
        result = resolve_term(handle, "единственноеслово")
        assert result.candidate_ids == ["AMBIG-ONLY"]
        assert result.selected_id is None
        assert result.requires_confirmation is True
    finally:
        close(handle)


def test_resolve_term_multiple_candidates_require_confirmation(tmp_path):
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_term(path, "TWO-A", "Общее слово варианта А", ["дваварианта"], ambiguous=False)
    _insert_term(path, "TWO-B", "Общее слово варианта Б", ["дваварианта"], ambiguous=False)
    handle = open_corpus(str(path))
    try:
        result = resolve_term(handle, "дваварианта")
        assert set(result.candidate_ids) == {"TWO-A", "TWO-B"}
        assert result.selected_id is None
        assert result.requires_confirmation is True
    finally:
        close(handle)


def test_resolve_term_cov_vs_statistical_cv_stay_distinct(tmp_path):
    """COV Valmet ≠ статистический коэффициент вариации (Scope задачи): две записи словаря с разными ключевыми
    словами никогда не дают один кандидат для другого, даже если оба содержат слово «коэффициент»."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_term(path, "COV-SCREEN", "Показатель COV на экранах", ["COV", "коэффициент отклонения"], True)
    _insert_term(path, "CV-STAT", "Коэффициент вариации в статистике",
                ["выборочный коэффициент вариации", "coefficient of variation"], False)
    handle = open_corpus(str(path))
    try:
        cov_only = resolve_term(handle, "COV")
        assert cov_only.candidate_ids == ["COV-SCREEN"]
        cv_only = resolve_term(handle, "коэффициент вариации")
        assert cv_only.candidate_ids == ["CV-STAT"]
        # общее слово без уточнения — спорно между двумя, не угадывается
        shared = resolve_term(handle, "коэффициент")
        assert set(shared.candidate_ids) == {"COV-SCREEN", "CV-STAT"}
        assert shared.requires_confirmation is True
    finally:
        close(handle)


# --- resolve_term_exact_phrase: фраза термина (Task 3, раунд 1, Д17) — тот же класс дефекта, что фраза узла,
# но равенство множества основ, не подмножество (отличает термин от более длинного термина, содержащего ту же
# пару слов как часть своего названия — на настоящем корпусе: «Машинное направление» / «Кратковременная
# изменчивость в машинном направлении»; здесь — синтетические имена по тому же образцу) ---

def test_resolve_term_exact_phrase_single_word_returns_none(handle):
    """Меньше двух слов — не фраза; функция отдаёт `None`, слово разрешает обычный пословный `resolve_term`."""
    assert resolve_term_exact_phrase(handle, ["термин"]) is None
    assert resolve_term_exact_phrase(handle, []) is None


def test_resolve_term_exact_phrase_no_match_returns_none(handle):
    assert resolve_term_exact_phrase(handle, ["совершенно", "неизвестная", "фраза"]) is None


def test_resolve_term_exact_phrase_short_vs_long_term_disambiguates(tmp_path):
    """Синтетика по образцу md/st-md настоящего корпуса: короткий термин «Опорная линия» и более длинный
    «Опорная линия базового контура выравнивания», содержащий ТУ ЖЕ пару слов как часть названия. Фраза,
    РАВНАЯ короткому названию (в другом падеже — как «машинном бассейне» у фразы узла), выбирает ТОЛЬКО
    короткий; фраза, равная полному длинному названию, выбирает ТОЛЬКО длинный — равенство множества основ, не
    подмножество (которое совпало бы с обоими, воспроизводя дефект)."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_term(path, "SHORT", "Опорная линия", [], ambiguous=False)
    _insert_term(path, "LONG", "Опорная линия базового контура выравнивания", [], ambiguous=False)
    handle = open_corpus(str(path))
    try:
        short = resolve_term_exact_phrase(handle, ["опорную", "линию"])
        assert short is not None
        assert short.candidate_ids == ["SHORT"]
        assert short.selected_id == "SHORT"
        assert short.requires_confirmation is False

        long_ = resolve_term_exact_phrase(handle, ["опорная", "линия", "базового", "контура", "выравнивания"])
        assert long_ is not None
        assert long_.candidate_ids == ["LONG"]
        assert long_.selected_id == "LONG"
        assert long_.requires_confirmation is False

        # подмножество названия длинного термина (не целиком) — равенством не совпадает ни с одним
        assert resolve_term_exact_phrase(handle, ["базового", "контура"]) is None
    finally:
        close(handle)


def test_resolve_term_exact_phrase_ambiguous_term_still_requires_confirmation(tmp_path):
    """Термин, точно совпавший ФРАЗОЙ, но помеченный `ambiguous` в корпусе, — уточнение сохраняется, как у
    пословного `resolve_term` (равенство множества основ не отменяет флаг корпуса)."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_term(path, "AMBIG-PHRASE", "Теневая зона", [], ambiguous=True)
    handle = open_corpus(str(path))
    try:
        result = resolve_term_exact_phrase(handle, ["теневую", "зону"])
        assert result is not None
        assert result.candidate_ids == ["AMBIG-PHRASE"]
        assert result.selected_id is None
        assert result.requires_confirmation is True
    finally:
        close(handle)


def test_resolve_term_exact_phrase_two_terms_same_name_both_candidates(tmp_path):
    """Два термина с одинаковым (по основам) названием — оба кандидата, уточнение, как и у `resolve_term`."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_term(path, "DUP-A", "Общая фраза варианта", [], ambiguous=False)
    _insert_term(path, "DUP-B", "Общая фраза варианта", [], ambiguous=False)
    handle = open_corpus(str(path))
    try:
        result = resolve_term_exact_phrase(handle, ["общая", "фраза", "варианта"])
        assert result is not None
        assert set(result.candidate_ids) == {"DUP-A", "DUP-B"}
        assert result.selected_id is None
        assert result.requires_confirmation is True
    finally:
        close(handle)


def test_resolve_term_exact_phrase_matches_via_alias_too(tmp_path):
    """Равенство проверяется по ЛЮБОЙ форме термина (`preferred_ru` ИЛИ alias), как у `_candidate_forms`."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_term(path, "ALIAS-PHRASE", "Основное название термина", ["короткий псевдоним фразы"],
                ambiguous=False)
    handle = open_corpus(str(path))
    try:
        result = resolve_term_exact_phrase(handle, ["короткий", "псевдоним", "фразы"])
        assert result is not None
        assert result.candidate_ids == ["ALIAS-PHRASE"]
        assert result.selected_id == "ALIAS-PHRASE"
    finally:
        close(handle)


@real_corpus
def test_real_corpus_term_phrase_disambiguates_machine_direction():
    """Буквальный пример корня дефекта раунда 1 на настоящем корпусе: «машинное направление» (`md`) и
    «кратковременная изменчивость в машинном направлении» (`st-md`) — равенство выбирает каждый по своей
    фразе."""
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        short = resolve_term_exact_phrase(handle, ["машинное", "направление"])
        assert short is not None
        assert short.selected_id == "md"
        long_ = resolve_term_exact_phrase(
            handle, ["кратковременная", "изменчивость", "в", "машинном", "направлении"])
        assert long_ is not None
        assert long_.selected_id == "st-md"
    finally:
        close(handle)


# --- known_ambiguous_words: из корпуса, не хардкод ---

def test_known_ambiguous_words_reflects_corpus_not_hardcoded(tmp_path):
    path_a = tmp_path / "a" / "fake.seed.sqlite3"
    path_a.parent.mkdir()
    build_corpus(path_a, counts={name: 0 for name in
                                 ("sources", "fragments", "processes", "terms", "claims", "conflicts",
                                  "equipment_links", "mechanisms")})
    _insert_term(path_a, "ONLY-A", "Слово один", ["слово1"], ambiguous=True)
    handle_a = open_corpus(str(path_a))

    path_b = tmp_path / "b" / "fake.seed.sqlite3"
    path_b.parent.mkdir()
    build_corpus(path_b, counts={name: 0 for name in
                                 ("sources", "fragments", "processes", "terms", "claims", "conflicts",
                                  "equipment_links", "mechanisms")})
    _insert_term(path_b, "ONLY-B", "Слово два", ["слово2"], ambiguous=True)
    handle_b = open_corpus(str(path_b))
    try:
        words_a = known_ambiguous_words(handle_a)
        words_b = known_ambiguous_words(handle_b)
        assert "Слово один" in words_a and "слово1" in words_a
        assert "Слово один" not in words_b
        assert "Слово два" in words_b
        assert "Слово два" not in words_a
    finally:
        close(handle_a)
        close(handle_b)


def test_known_ambiguous_words_excludes_non_ambiguous(tmp_path):
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_term(path, "CALM", "Спокойное слово", ["спокойный"], ambiguous=False)
    handle = open_corpus(str(path))
    try:
        assert "Спокойное слово" not in known_ambiguous_words(handle)
    finally:
        close(handle)


# --- exact_tag_candidates: точная подпись, без морфологии, без нечёткого сходства ---

def test_exact_tag_candidates_finds_known_fake_tags(handle):
    tags = exact_tag_candidates(handle, "Вопрос про 20FAKE_001_PV и соседний 20FAKE_002_PV, остальное мимо.")
    assert tags == ["20FAKE_001_PV", "20FAKE_002_PV"]


def test_exact_tag_candidates_case_sensitive_no_fuzzy_match(handle):
    """Регистр — часть подписи: нижний регистр того же текста — другая строка, не вариант той же подписи."""
    tags = exact_tag_candidates(handle, "мимо: 20fake_001_pv (не то же самое, что 20FAKE_001_PV)")
    assert tags == ["20FAKE_001_PV"]


def test_exact_tag_candidates_does_not_match_substring_of_longer_tag(handle):
    """«20FAKE_0010_PV» не существует в корпусе — не подтягивать «20FAKE_001_PV» как «похожую» подпись."""
    tags = exact_tag_candidates(handle, "упоминание 20FAKE_0010_PV без границы слова")
    assert tags == []


def test_exact_tag_candidates_empty_text_returns_empty(handle):
    assert exact_tag_candidates(handle, "") == []


def test_exact_tag_candidates_distinguishes_similar_numbered_labels(tmp_path):
    """№7/№9 (Особый фокус проверки плана): две разные подписи, отличающиеся только числом, не путаются —
    упоминание одной не возвращает другую."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    conn = sqlite3.connect(path)
    try:
        for suffix, label in (("7", "Сортировка нижней сетки №7-demo"), ("9", "Сортировка верхней сетки №9-demo")):
            payload = {
                "id": f"PROC-DEMO-{suffix}", "title": label, "source_label": label, "machine_scope": "KDM1_PCBK",
                "source_refs": ["SRC-FAKE-001"], "knowledge_state": "document_label",
                "actual_name_requires_review": True, "inferred_connectivity": None, "operating_limits": None,
                "conflict_ids": [],
            }
            conn.execute("INSERT INTO processes (id, payload) VALUES (?, ?)", (f"PROC-DEMO-{suffix}",
                                                                                json.dumps(payload)))
        conn.commit()
    finally:
        conn.close()
    handle = open_corpus(str(path))
    try:
        tags7 = exact_tag_candidates(handle, "Вопрос про Сортировка нижней сетки №7-demo")
        assert tags7 == ["Сортировка нижней сетки №7-demo"]
        tags9 = exact_tag_candidates(handle, "Вопрос про Сортировка верхней сетки №9-demo")
        assert tags9 == ["Сортировка верхней сетки №9-demo"]
    finally:
        close(handle)


# --- настоящий корпус заказчика ---

@real_corpus
def test_real_corpus_mass_is_ambiguous_single_candidate():
    """«Масса» — чек-лист Этапа 1 («неоднозначная «масса»»): единственный прямой кандидат словаря, но корпус
    сам помечает его спорным — подтверждение нужно всегда, даже без конкурирующих id."""
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        result = resolve_term(handle, "масса")
        assert "stock" in result.candidate_ids
        assert result.requires_confirmation is True
    finally:
        close(handle)


@real_corpus
def test_real_corpus_cov_and_statistical_coefficient_stay_distinct():
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        cov_only = resolve_term(handle, "COV")
        assert cov_only.candidate_ids == ["cov"]
        stat_only = resolve_term(handle, "выборочный коэффициент вариации")
        assert "coefficient-variation-statistical" in stat_only.candidate_ids
        assert "cov" not in stat_only.candidate_ids
    finally:
        close(handle)


@real_corpus
def test_real_corpus_pc_cvv_abbreviation_resolves_and_is_ambiguous():
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        result = resolve_term(handle, "ПЦ")
        assert result.candidate_ids == ["pc-cvv"]
        assert result.requires_confirmation is True  # ambiguous=True у pc-cvv в корпусе
    finally:
        close(handle)


@real_corpus
def test_real_corpus_known_ambiguous_words_contains_mass():
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        assert "Масса" in known_ambiguous_words(handle)
    finally:
        close(handle)
