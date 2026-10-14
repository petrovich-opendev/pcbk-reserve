"""Поиск по корпусу КДМ-1 (план КДМ1, Task 1; `docs/kdm1/INTERFACES.md`, `analytics/retrieval.py`).

Фикстура — синтетический корпус (`fixtures/kdm1_corpus.py`, теги только `20FAKE_*`). Настоящий корпус
заказчика — только через `KDM1_PACKAGE_DIR` (`pytest.mark.skipif` без неё). Литералы реальных тестов — только
абстрактные id корпуса (`screen-lower`, `machine-chest-upper`…) и общие обозначения («№7», «№8», «№9») — не
подписи оборудования предприятия."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from fixtures.kdm1_corpus import build_corpus
from pcbk_core.analytics.knowledge import CorpusError, close, open_corpus
from pcbk_core.analytics.retrieval import SearchHit, exact_id_lookup, related_fragments, search

KDM1_PACKAGE_DIR = os.environ.get("KDM1_PACKAGE_DIR", "")
real_corpus = pytest.mark.skipif(not KDM1_PACKAGE_DIR, reason="нужен KDM1_PACKAGE_DIR — пакет заказчика вне репозитория")
SEARCH_KINDS = ("claim", "mechanism", "processe", "term")


@pytest.fixture
def handle(tmp_path):
    h = open_corpus(str(build_corpus(tmp_path / "fake.seed.sqlite3")))
    yield h
    close(h)


def _insert_claim(path: Path, claim_id: str, text: str, process_ids: list[str] | None = None,
                  machine_scope: str | None = None) -> None:
    conn = sqlite3.connect(path)
    try:
        payload = {
            "id": claim_id, "kind": "definition", "text": text, "term_ids": [], "evidence_refs": ["FRG-FAKE-001"],
            "applicability": "KDM1_PCBK, тест", "publication_state": "desk_verified_draft",
            "allowed_render_mode": "attributed_reference", "risk_level": "non_operational_reference",
            "permits_local_setpoint": False, "critical_local_fact": False, "reviewer_login": None,
        }
        if process_ids is not None:
            payload["process_ids"] = process_ids
        if machine_scope is not None:
            payload["machine_scope"] = machine_scope
        conn.execute("INSERT INTO claims (id, payload) VALUES (?, ?)", (claim_id, json.dumps(payload)))
        conn.execute("INSERT INTO kb_search (kind, id, text) VALUES ('claim', ?, ?)", (claim_id, text))
        conn.commit()
    finally:
        conn.close()


# --- search: базовое поведение ---

def test_search_empty_query_returns_empty_list(handle):
    assert search(handle, "") == []
    assert search(handle, "   ") == []


def test_search_punctuation_only_query_returns_empty_list(handle):
    assert search(handle, "???...") == []


def test_search_zero_limit_returns_empty_list(handle):
    assert search(handle, "выдуманный", limit=0) == []


def test_search_unknown_kind_raises_value_error(handle):
    with pytest.raises(ValueError):
        search(handle, "выдуманный", kinds=("fragment",))  # type: ignore[arg-type]


def test_search_default_kinds_only_returns_known_kinds(handle):
    hits = search(handle, "выдуманный", limit=50)
    assert hits
    assert all(hit.kind in SEARCH_KINDS for hit in hits)


def test_search_restricts_to_requested_kind(handle):
    hits = search(handle, "выдуманный", kinds=("term",), limit=50)
    assert hits
    assert all(hit.kind == "term" for hit in hits)


def test_search_respects_limit(handle):
    hits = search(handle, "выдуманный", limit=3)
    assert len(hits) <= 3


def test_search_returns_search_hit_instances(handle):
    hits = search(handle, "выдуманный", limit=1)
    assert hits and isinstance(hits[0], SearchHit)


def test_search_never_surfaces_fragments_kind_structurally(handle):
    """`SEARCH_KINDS` не включает `fragment` — карантин/необработанные формулы живут только там и поиском не
    индексируются вовсе (`tools/import_seed.py`), а не отфильтровываются постфактум."""
    hits = search(handle, "выдуманный фрагмент", limit=50)
    assert all(hit.kind != "fragment" for hit in hits)


def test_search_single_digit_process_number_is_not_dropped(handle):
    """№7/№8/№9 — Особый фокус проверки плана: однозначный цифровой номер узла не теряется как «слишком
    короткое слово» (ровно та же защита, что в `terms._keep_token`)."""
    hits = search(handle, "7", kinds=("processe",))
    assert any(hit.id == "PROC-FAKE-007" for hit in hits)


def test_search_corpus_error_on_broken_corpus_is_not_swallowed_as_no_results(tmp_path):
    """Сбой самого корпуса — не «нет результатов»: `service.py` должен отличить «ничего не нашли» от «корпус
    недоступен», иначе он покажет уверенный пустой ответ вместо 503/`limited_answer`."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    handle = open_corpus(str(path))
    path.unlink()  # корпус исчез между open_corpus и поиском (аналог сбоя файловой системы)
    with pytest.raises(CorpusError):
        search(handle, "выдуманный")


# --- search: process_ids и область (machine_scope) ---

def test_search_filters_claims_by_process_ids(tmp_path):
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_claim(path, "CLAIM-PROC-A", "demo-claim про узел альфа", process_ids=["PROC-FAKE-001"])
    _insert_claim(path, "CLAIM-PROC-B", "demo-claim про узел альфа", process_ids=["PROC-FAKE-002"])
    handle = open_corpus(str(path))
    try:
        narrowed = search(handle, "альфа", kinds=("claim",), process_ids=("PROC-FAKE-001",))
        ids = {hit.id for hit in narrowed}
        assert "CLAIM-PROC-A" in ids
        assert "CLAIM-PROC-B" not in ids
        broad = search(handle, "альфа", kinds=("claim",))
        assert {"CLAIM-PROC-A", "CLAIM-PROC-B"} <= {hit.id for hit in broad}
    finally:
        close(handle)


def test_search_process_ids_filter_matches_processe_kind_by_its_own_id(handle):
    hits_in = search(handle, "выдуманный узел", kinds=("processe",), process_ids=("PROC-FAKE-001",))
    assert {hit.id for hit in hits_in} == {"PROC-FAKE-001"}


def test_search_process_ids_filter_does_not_apply_to_terms(handle):
    """Термин — словарь, не привязан к узлу: фильтр процесса не исключает `kind="term"`."""
    hits = search(handle, "выдуманный термин", kinds=("term",), process_ids=("PROC-FAKE-001",), limit=50)
    assert any(hit.kind == "term" for hit in hits)


def test_search_excludes_foreign_machine_scope_claim(tmp_path):
    """Защита от будущей ошибки корпуса (не текущих данных заказчика): запись «чужой машины» не выдаётся поиском
    даже если по ошибке попала в `kb_search` (§5: «листы с чужими машинами не допускаются целиком в рабочий
    поиск», а это — не задача вызывающего отфильтровать постфактум)."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_claim(path, "CLAIM-FOREIGN", "demo-claim уникальное-слово-бета", machine_scope="BDM2_OTHER_MACHINE")
    _insert_claim(path, "CLAIM-LOCAL", "demo-claim уникальное-слово-бета", machine_scope="KDM1_PCBK")
    handle = open_corpus(str(path))
    try:
        hits = search(handle, "уникальное-слово-бета", kinds=("claim",))
        ids = {hit.id for hit in hits}
        assert "CLAIM-LOCAL" in ids
        assert "CLAIM-FOREIGN" not in ids
    finally:
        close(handle)


def test_search_includes_any_kdm1_subscope(tmp_path):
    """Правка, раунд 2 (найдено Task 8 на настоящем корпусе): `machine_scope` «своей» записи — не один литерал
    (`KDM1_PCBK`), у настоящего корпуса таких значений несколько (`KDM1_PCBK_related`,
    `KDM1_related_general_mathematics` — общая статистика, нужна отдельно от COV Valmet). Любое значение с
    токеном `KDM1` — своя машина, не только то, что совпадает с одним конкретным суффиксом."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_claim(path, "CLAIM-MATH", "demo-claim уникальное-слово-гамма",
                  machine_scope="KDM1_related_general_mathematics")
    handle = open_corpus(str(path))
    try:
        hits = search(handle, "уникальное-слово-гамма", kinds=("claim",))
        assert {hit.id for hit in hits} == {"CLAIM-MATH"}
    finally:
        close(handle)


def test_search_excludes_scope_with_digit_after_kdm1(tmp_path):
    """Граница токена, не префикс строки: `KDM10_…`/`KDM1x…` — не КДМ-1 (другая машина или опечатка с тем же
    началом), совпадать должен токен `KDM1` целиком (граница — `_` или конец строки)."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_claim(path, "CLAIM-KDM10", "demo-claim уникальное-слово-дельта", machine_scope="KDM10_other")
    _insert_claim(path, "CLAIM-KDM1X", "demo-claim уникальное-слово-дельта", machine_scope="KDM1x_other")
    _insert_claim(path, "CLAIM-KDM2", "demo-claim уникальное-слово-дельта", machine_scope="KDM2_PCBK")
    handle = open_corpus(str(path))
    try:
        hits = search(handle, "уникальное-слово-дельта", kinds=("claim",))
        assert hits == []
    finally:
        close(handle)


# --- exact_id_lookup ---

def test_exact_id_lookup_hit(handle):
    assert exact_id_lookup(handle, "term", "TERM-FAKE-001") == "TERM-FAKE-001"


def test_exact_id_lookup_miss(handle):
    assert exact_id_lookup(handle, "term", "NOPE") is None


def test_exact_id_lookup_unknown_kind_raises_value_error(handle):
    with pytest.raises(ValueError):
        exact_id_lookup(handle, "fragment", "x")  # type: ignore[arg-type]


def test_exact_id_lookup_excludes_foreign_scope(tmp_path):
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    _insert_claim(path, "CLAIM-FOREIGN", "demo", machine_scope="BDM2_OTHER_MACHINE")
    handle = open_corpus(str(path))
    try:
        assert exact_id_lookup(handle, "claim", "CLAIM-FOREIGN") is None
    finally:
        close(handle)


# --- related_fragments ---

def test_related_fragments_for_known_claim(handle):
    frags = related_fragments(handle, "CLAIM-FAKE-001")
    assert frags == ["FRG-FAKE-001"]


def test_related_fragments_for_known_mechanism(handle):
    frags = related_fragments(handle, "MECH-FAKE-001")
    assert frags == ["FRG-FAKE-001"]


def test_related_fragments_unknown_id_returns_empty(handle):
    assert related_fragments(handle, "NOPE") == []


def test_related_fragments_drops_dangling_and_source_only_refs(tmp_path):
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    conn = sqlite3.connect(path)
    try:
        payload = {
            "id": "CLAIM-MIXED-REFS", "kind": "definition", "text": "demo",
            "evidence_refs": ["FRG-FAKE-001", "SRC-FAKE-001", "NO-SUCH-ID"], "term_ids": [],
            "applicability": "demo", "publication_state": "desk_verified_draft",
            "allowed_render_mode": "attributed_reference", "risk_level": "non_operational_reference",
            "permits_local_setpoint": False, "critical_local_fact": False, "reviewer_login": None,
        }
        conn.execute("INSERT INTO claims (id, payload) VALUES (?, ?)", ("CLAIM-MIXED-REFS", json.dumps(payload)))
        conn.commit()
    finally:
        conn.close()
    handle = open_corpus(str(path))
    try:
        frags = related_fragments(handle, "CLAIM-MIXED-REFS")
        assert frags == ["FRG-FAKE-001"]  # ни SRC- (источник, не фрагмент), ни несуществующий id
    finally:
        close(handle)


# --- настоящий корпус заказчика ---

@real_corpus
def test_real_corpus_process_number_8_is_genuinely_ambiguous():
    """№8 — чек-лист Этапа 1: бассейн №8 и 2-я ступень сортировки №8 — два разных узла, поиск должен показать
    ОБА, не выбрать один произвольно."""
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        hits = search(handle, "№8", kinds=("processe",), limit=50)
        ids = {hit.id for hit in hits}
        assert {"machine-chest-upper", "screen-secondary"} <= ids
    finally:
        close(handle)


@real_corpus
def test_real_corpus_process_number_7_and_9_are_not_confused():
    """№7/№9 — та же мутация, в обратную сторону: однозначный №7 не возвращает узел №9 и наоборот (у №9 есть
    собственная, отдельная неоднозначность — два других узла, не №7)."""
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        hits7 = {hit.id for hit in search(handle, "№7", kinds=("processe",), limit=50)}
        assert "screen-lower" in hits7
        assert "screen-upper" not in hits7
        hits9 = {hit.id for hit in search(handle, "№9", kinds=("processe",), limit=50)}
        assert "screen-upper" in hits9
        assert "screen-lower" not in hits9
    finally:
        close(handle)


@real_corpus
def test_real_corpus_exact_id_lookup_known_process():
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        assert exact_id_lookup(handle, "processe", "screen-lower") == "screen-lower"
        assert exact_id_lookup(handle, "processe", "no-such-process") is None
    finally:
        close(handle)


@real_corpus
def test_real_corpus_search_finds_statistical_coefficient_of_variation():
    """Правка, раунд 2: до фикса `coefficient-variation-statistical` (`machine_scope=
    "KDM1_related_general_mathematics"`) молча терялся — поиск находил только `cov` (COV Valmet)."""
    handle = open_corpus(str(Path(KDM1_PACKAGE_DIR) / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        hits = {hit.id for hit in search(handle, "коэффициент вариации", kinds=("term",), limit=20)}
        assert "coefficient-variation-statistical" in hits
    finally:
        close(handle)
