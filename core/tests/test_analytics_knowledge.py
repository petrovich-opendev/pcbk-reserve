"""Корпус КДМ-1 только для чтения (план КДМ1, Task 1; `docs/kdm1/INTERFACES.md`, `analytics/knowledge.py`).

Фикстура — синтетический SQLite той же схемы (`fixtures/kdm1_corpus.py`, теги только `20FAKE_*`). Настоящий
корпус заказчика (`/home/dev/incoming/kdm1-package/`) подключается только через `KDM1_PACKAGE_DIR` и пропускается
без неё (`pytest.mark.skipif`) — ни путь, ни содержимое пакета в репозиторий не попадают."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from fixtures.kdm1_corpus import DEFAULT_COUNT, build_corpus
from pcbk_core.analytics.knowledge import (CURRENT_POINTER_NAME, CorpusError, CorpusHandle, all_ids, close,
                                           corpus_dirs, corpus_report, corpus_version, get_row, get_rows,
                                           open_corpus, version_signature)

KDM1_PACKAGE_DIR = os.environ.get("KDM1_PACKAGE_DIR", "")
real_corpus = pytest.mark.skipif(not KDM1_PACKAGE_DIR, reason="нужен KDM1_PACKAGE_DIR — пакет заказчика вне репозитория")


@pytest.fixture
def corpus_path(tmp_path) -> Path:
    return build_corpus(tmp_path / "fake.seed.sqlite3")


@pytest.fixture
def handle(corpus_path) -> CorpusHandle:
    h = open_corpus(str(corpus_path))
    yield h
    close(h)


# --- open_corpus: успешные пути ---

def test_open_corpus_from_file_path(corpus_path):
    h = open_corpus(str(corpus_path))
    assert h.path == str(corpus_path)
    assert h.version  # не пустая строка
    close(h)


def test_open_corpus_from_directory_with_single_file(corpus_path):
    """`root` — каталог с ровно одним файлом `*.seed.sqlite3` прямо внутри (как у заказчика — `knowledge/`)."""
    h = open_corpus(str(corpus_path.parent))
    assert h.path == str(corpus_path)
    close(h)


def _build_in(path: Path, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return build_corpus(path, **kw)


def test_open_corpus_picks_latest_versioned_subdir(tmp_path):
    """`root` с подкаталогами версий: open_corpus берёт самый новый по имени (`corpus_dirs` — по убыванию)."""
    older = _build_in(tmp_path / "v1" / "a.seed.sqlite3")
    newer = _build_in(tmp_path / "v2" / "b.seed.sqlite3")
    h = open_corpus(str(tmp_path))
    assert h.path == str(newer)
    assert h.path != str(older)
    close(h)


def test_corpus_dirs_skips_subdir_with_no_seed_file(tmp_path):
    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / "readme.txt").write_text("не корпус")
    _build_in(tmp_path / "v2" / "b.seed.sqlite3")
    dirs = corpus_dirs(str(tmp_path))
    assert [d.name for d in dirs] == ["v2"]


def test_corpus_dirs_lists_dir_with_two_seed_files_too(tmp_path):
    """`corpus_dirs` — «хотя бы один файл», не «ровно один» (список для Task 8, не точка открытия)."""
    _build_in(tmp_path / "v1" / "a.seed.sqlite3")
    _build_in(tmp_path / "v1" / "b.seed.sqlite3")
    _build_in(tmp_path / "v2" / "c.seed.sqlite3")
    dirs = corpus_dirs(str(tmp_path))
    assert [d.name for d in dirs] == ["v2", "v1"]


def test_open_corpus_latest_subdir_with_two_seed_files_raises_corpus_error(tmp_path):
    """Но открыть корпус из такого (самого нового, неоднозначного) подкаталога — нельзя: `CorpusError`, не
    угадывание, какой из двух файлов актуальный."""
    _build_in(tmp_path / "v2" / "a.seed.sqlite3")
    _build_in(tmp_path / "v2" / "b.seed.sqlite3")
    with pytest.raises(CorpusError):
        open_corpus(str(tmp_path))


def test_corpus_dirs_empty_without_root(tmp_path):
    assert corpus_dirs(str(tmp_path / "nope")) == []


# --- указатель текущей версии (Task 8, план Этап 8) — приоритетнее сортировки подкаталогов по имени ---

def test_open_corpus_honors_current_pointer_over_name_sort(tmp_path):
    """Без указателя «самый новый по имени» — v9; указатель на v1 должен открыть именно v1 (откат на старую
    версию не может зависеть от того, как Task 8 назвала подкаталоги)."""
    rolled_back_to = _build_in(tmp_path / "v1" / "a.seed.sqlite3")
    _build_in(tmp_path / "v9" / "b.seed.sqlite3")
    (tmp_path / CURRENT_POINTER_NAME).write_text("v1", encoding="utf-8")
    h = open_corpus(str(tmp_path))
    assert h.path == str(rolled_back_to)
    close(h)


def test_open_corpus_pointer_to_missing_dir_raises_corpus_error(tmp_path):
    _build_in(tmp_path / "v1" / "a.seed.sqlite3")
    (tmp_path / CURRENT_POINTER_NAME).write_text("v404", encoding="utf-8")
    with pytest.raises(CorpusError):
        open_corpus(str(tmp_path))


def test_open_corpus_pointer_with_path_traversal_raises_corpus_error(tmp_path):
    _build_in(tmp_path / "v1" / "a.seed.sqlite3")
    (tmp_path / CURRENT_POINTER_NAME).write_text("../v1", encoding="utf-8")
    with pytest.raises(CorpusError):
        open_corpus(str(tmp_path))


def test_open_corpus_pointer_to_ambiguous_dir_raises_corpus_error(tmp_path):
    """Указатель ссылается на подкаталог с двумя файлами корпуса — отказ, не угадывание."""
    _build_in(tmp_path / "v1" / "a.seed.sqlite3")
    _build_in(tmp_path / "v1" / "b.seed.sqlite3")
    (tmp_path / CURRENT_POINTER_NAME).write_text("v1", encoding="utf-8")
    with pytest.raises(CorpusError):
        open_corpus(str(tmp_path))


# --- open_corpus: неисправный/отсутствующий корпус → CorpusError, без исключений другого вида ---

def test_open_corpus_missing_path_raises_corpus_error(tmp_path):
    with pytest.raises(CorpusError):
        open_corpus(str(tmp_path / "nope"))


def test_open_corpus_empty_directory_raises_corpus_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(CorpusError):
        open_corpus(str(tmp_path / "empty"))


def test_open_corpus_not_a_sqlite_file_raises_corpus_error(tmp_path):
    bad = tmp_path / "broken.seed.sqlite3"
    bad.write_bytes(b"not a database at all")
    with pytest.raises(CorpusError):
        open_corpus(str(bad))


def test_open_corpus_missing_table_raises_corpus_error(tmp_path):
    """Битая схема (нет одной из обязательных таблиц, например `claims`) — `CorpusError`, не падение глубже."""
    bad = tmp_path / "broken.seed.sqlite3"
    conn = sqlite3.connect(bad)
    try:
        for table in ("sources", "fragments", "processes", "terms", "conflicts", "equipment_links", "mechanisms"):
            conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("CREATE TABLE seed_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE VIRTUAL TABLE kb_search USING fts5(kind UNINDEXED, id UNINDEXED, text)")
        conn.execute("INSERT INTO seed_meta VALUES ('manifest', ?)", (json.dumps({"scope": "KDM1_PCBK"}),))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CorpusError):
        open_corpus(str(bad))


def test_open_corpus_broken_manifest_raises_corpus_error(tmp_path):
    """Чек-лист Этапа 1: «битый манифест» — проверяется первым, до любого поиска."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    conn = sqlite3.connect(path)
    try:
        conn.execute("UPDATE seed_meta SET value = ? WHERE key = 'manifest'", ("не json {{{",))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CorpusError):
        open_corpus(str(path))


def test_open_corpus_missing_manifest_row_raises_corpus_error(tmp_path):
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM seed_meta WHERE key = 'manifest'")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CorpusError):
        open_corpus(str(path))


def test_open_corpus_foreign_scope_raises_corpus_error(tmp_path):
    """Чек-лист Этапа 1: «чужая область» — манифест синтаксически валиден, но scope не КДМ-1."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    conn = sqlite3.connect(path)
    try:
        manifest = json.loads(conn.execute("SELECT value FROM seed_meta WHERE key = 'manifest'").fetchone()[0])
        manifest["scope"] = "BDM2_OTHER_MACHINE"
        conn.execute("UPDATE seed_meta SET value = ? WHERE key = 'manifest'", (json.dumps(manifest),))
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CorpusError):
        open_corpus(str(path))


def test_close_is_idempotent_noop(handle):
    close(handle)
    close(handle)  # повторный вызов — не ошибка


def test_write_to_corpus_is_rejected_by_mode_ro(handle):
    """Правка, раунд 1 (опционально, дёшево): корпус открывается `mode=ro` (докстринг модуля) — запись в него
    должна быть физически невозможна, не только «мы просто не пишем». Открываем тем же URI, что `knowledge.py`,
    и пробуем `INSERT` — SQLite обязан отказать (`OperationalError: attempt to write a readonly database`),
    не тихо проглотить."""
    uri = f"{Path(handle.path).resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO terms (id, payload) VALUES ('HACK', '{}')")
            conn.commit()
    finally:
        conn.close()


# --- версия: не номер строки, детерминирована по содержимому ---

def test_corpus_version_deterministic_for_same_content(tmp_path):
    a = open_corpus(str(_build_in(tmp_path / "a" / "x.seed.sqlite3")))
    b = open_corpus(str(_build_in(tmp_path / "b" / "x.seed.sqlite3")))
    assert corpus_version(a) == corpus_version(b)  # тот же набор данных → та же версия


def test_corpus_version_differs_for_different_content(tmp_path):
    a = open_corpus(str(_build_in(tmp_path / "a" / "x.seed.sqlite3", counts={"terms": 3})))
    b = open_corpus(str(_build_in(tmp_path / "b" / "x.seed.sqlite3", counts={"terms": 5})))
    assert corpus_version(a) != corpus_version(b)


def test_corpus_version_matches_handle_version(handle):
    assert corpus_version(handle) == handle.version


# --- version_signature: признак версии без хеширования (правка F4a — смена версии без перезапуска) ---

def test_version_signature_stable_for_unchanged_file(corpus_path):
    assert version_signature(str(corpus_path)) == version_signature(str(corpus_path))


def test_version_signature_changes_when_current_pointer_switches(tmp_path):
    v1 = _build_in(tmp_path / "v1" / "a.seed.sqlite3")
    v2 = _build_in(tmp_path / "v2" / "b.seed.sqlite3", counts={"terms": 3})
    (tmp_path / CURRENT_POINTER_NAME).write_text("v1", encoding="utf-8")
    sig_v1 = version_signature(str(tmp_path))
    (tmp_path / CURRENT_POINTER_NAME).write_text("v2", encoding="utf-8")
    sig_v2 = version_signature(str(tmp_path))
    assert sig_v1 != sig_v2
    assert str(v1) in sig_v1
    assert str(v2) in sig_v2


def test_version_signature_changes_when_same_file_rewritten(tmp_path):
    """Тот же путь, но файл перезаписан на месте (другой размер) — признак меняется без смены пути."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    sig_before = version_signature(str(path))
    with open(path, "ab") as f:
        f.write(b"\x00")                       # размер меняется — достаточно для другого признака
    sig_after = version_signature(str(path))
    assert sig_after != sig_before


def test_version_signature_does_not_hash_file_content(tmp_path, monkeypatch):
    """Дёшево — без чтения байтов файла (в отличие от `open_corpus`/`corpus_version`, которые хешируют целиком).
    `Path.read_bytes` не должен вызываться вовсе."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")

    def boom(self, *a, **k):
        raise AssertionError("version_signature не должен читать содержимое файла")
    monkeypatch.setattr(Path, "read_bytes", boom)
    version_signature(str(path))               # не бросает — read_bytes не вызван


def test_version_signature_missing_path_raises_corpus_error(tmp_path):
    with pytest.raises(CorpusError):
        version_signature(str(tmp_path / "nope"))


# --- get_row / get_rows ---

def test_get_row_found(handle):
    row = get_row(handle, "terms", "TERM-FAKE-001")
    assert row is not None
    assert row["id"] == "TERM-FAKE-001"


def test_get_row_missing_returns_none(handle):
    assert get_row(handle, "terms", "NOPE-NOT-THERE") is None


def test_get_row_unknown_kind_raises_value_error(handle):
    with pytest.raises(ValueError):
        get_row(handle, "not_a_table", "x")  # type: ignore[arg-type]


def test_get_rows_batch_dedup_and_missing(handle):
    result = get_rows(handle, "terms", ["TERM-FAKE-001", "TERM-FAKE-001", "TERM-FAKE-002", "NOPE"])
    assert set(result) == {"TERM-FAKE-001", "TERM-FAKE-002"}
    assert result["TERM-FAKE-001"]["id"] == "TERM-FAKE-001"


def test_get_rows_empty_ids_returns_empty_dict(handle):
    assert get_rows(handle, "terms", []) == {}


def test_get_rows_unknown_kind_raises_value_error(handle):
    with pytest.raises(ValueError):
        get_rows(handle, "not_a_table", ["x"])  # type: ignore[arg-type]


def test_get_row_preserves_quarantine_and_unprocessed_formula_verbatim(tmp_path):
    """Карантин/необработанные формулы не выдаются как норма — но здесь, в `knowledge.py`, они и не прячутся:
    `get_row` возвращает `publication_state`/`is_formula`/`recalculated` в точности как в корпусе, решение
    «не показывать как норму» принимает слой выше (`validator.py`/`rendering.py`, Task 3). Если бы `knowledge.py`
    тихо поправил это поле, вышестоящий слой не смог бы отличить карантин от обычной записи."""
    path = build_corpus(tmp_path / "fake.seed.sqlite3")
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO fragments (id, payload) VALUES (?, ?)",
            ("FRG-FAKE-QUARANTINE", json.dumps({
                "id": "FRG-FAKE-QUARANTINE", "source_id": "SRC-FAKE-001",
                "locator": {"type": "xlsx_cell", "sheet": "demo-hidden", "cell": "A1"},
                "text": "=A2+A3", "text_sha256": "fakehash", "is_formula": True, "formula_text": "=A2+A3",
                "recalculated": False, "scope": "KDM1_document_model",
                "publication_state": "quarantined_alternative_model", "current_telemetry": False,
            })),
        )
        conn.commit()
    finally:
        conn.close()
    handle = open_corpus(str(path))
    row = get_row(handle, "fragments", "FRG-FAKE-QUARANTINE")
    assert row["publication_state"] == "quarantined_alternative_model"
    assert row["is_formula"] is True
    assert row["recalculated"] is False
    close(handle)


# --- all_ids (дополнение Task 1 к INTERFACES.md — см. докстринг knowledge.py) ---

def test_all_ids_sorted_and_complete(handle):
    ids = all_ids(handle, "terms")
    assert len(ids) == DEFAULT_COUNT
    assert list(ids) == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_all_ids_unknown_kind_raises_value_error(handle):
    with pytest.raises(ValueError):
        all_ids(handle, "not_a_table")  # type: ignore[arg-type]


# --- corpus_report ---

def test_corpus_report_counts_match_fixture(handle):
    report = corpus_report(handle)
    assert report["version"] == handle.version
    for table in ("sources", "fragments", "processes", "terms", "claims", "conflicts", "equipment_links",
                  "mechanisms"):
        assert report["counts"][table] == DEFAULT_COUNT
    # kb_search индексирует только claims/mechanisms/processes/terms (не fragments/sources/…)
    assert report["kb_search_rows"] == DEFAULT_COUNT * 4


def test_corpus_report_no_fragment_text_leak(handle):
    """Отчёт — числа, не текст фрагментов (безопасен для журнала/`/health/*`)."""
    report = corpus_report(handle)
    dumped = json.dumps(report)
    assert "Выдуманный фрагмент" not in dumped


# --- настоящий корпус заказчика (KDM1_PACKAGE_DIR) ---

@real_corpus
def test_real_corpus_opens_and_matches_seed_manifest():
    package = Path(KDM1_PACKAGE_DIR)
    manifest = json.loads((package / "knowledge" / "seed_manifest.json").read_text(encoding="utf-8"))
    handle = open_corpus(str(package / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        report = corpus_report(handle)
        for table, expected in manifest["counts"].items():
            assert report["counts"][table] == expected
        # kb_search — claims+mechanisms+processes+terms (не fragments: карантин/формулы там не индексируются)
        expected_kb = (manifest["counts"]["claims"] + manifest["counts"]["mechanisms"]
                      + manifest["counts"]["processes"] + manifest["counts"]["terms"])
        assert report["kb_search_rows"] == expected_kb
    finally:
        close(handle)


@real_corpus
def test_real_corpus_referential_integrity_no_dangling_refs():
    """«Проверить исходную базу импортера и ее манифест» (Этап 1, п.2) — независимо от `tools/import_seed.py`
    (не переписывается): каждая ссылка claims/processes/terms/mechanisms на источник/фрагмент/процесс/термин
    существует в соответствующей таблице. Неизвестный источник — не задача этого теста угадать, а повод
    сообщить, если бы он нашёлся; при настоящем корпусе их нет."""
    package = Path(KDM1_PACKAGE_DIR)
    handle = open_corpus(str(package / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        sources = set(all_ids(handle, "sources"))
        fragments = set(all_ids(handle, "fragments"))
        processes = set(all_ids(handle, "processes"))
        terms = set(all_ids(handle, "terms"))
        for table in ("claims", "processes", "terms", "mechanisms"):
            ids = all_ids(handle, table)
            for row in get_rows(handle, table, ids).values():
                refs = row.get("source_refs", row.get("evidence_refs", []))
                assert refs, f"{table}:{row['id']} без ссылки на доказательство"
                for ref in refs:
                    assert ref in sources or ref in fragments, f"{table}:{row['id']} ссылается на {ref!r} — нет"
                for pid in row.get("process_ids", []):
                    assert pid in processes
                for tid in row.get("term_ids", []):
                    assert tid in terms
    finally:
        close(handle)


@real_corpus
def test_real_corpus_pub10_pub11_marked_llm_context_not_allowed():
    """§5 мастер-промпта: PUB-10/PUB-11 (ISO) — ограничения правообладателя, `llm_context_allowed=false`,
    «не передавать в LLM» решается полем источника, не угадыванием по ссылке. `knowledge.py` отдаёт поле как
    есть — вышестоящий слой (llm_adapter.py, Task 5) обязан его прочитать, а не домыслить."""
    package = Path(KDM1_PACKAGE_DIR)
    handle = open_corpus(str(package / "knowledge" / "kdm1.seed.sqlite3"))
    try:
        for source_id in ("PUB-10", "PUB-11"):
            row = get_row(handle, "sources", source_id)
            assert row is not None
            assert row["llm_context_allowed"] is False
    finally:
        close(handle)
