"""Синтетический корпус-фикстура КДМ-1 (план КДМ1, Task 0; `docs/kdm1/INTERFACES.md`).

Строит во временном каталоге SQLite той же СХЕМЫ, что `knowledge/kdm1.seed.sqlite3` пакета заказчика (таблицы
`sources/fragments/processes/terms/claims/conflicts/equipment_links/mechanisms` — `(id TEXT PRIMARY KEY, payload
TEXT NOT NULL CHECK(json_valid(payload)))`, `seed_meta(key, value)` с единственной записью `key="manifest"`,
виртуальная FTS5 `kb_search(kind UNINDEXED, id UNINDEXED, text, tokenize='unicode61')`), но с полностью
выдуманными данными: тегов реального предприятия здесь нет, только `20FAKE_*` (ворота git плана —
`grep -nE '\\b[0-9]{2}[A-Z]{2,4}[0-9]{2}'` — `20FAKE_001_PV` не ловит: после двух цифр и 2–4 букв регексу нужны
сразу две цифры, а у `20FAKE_...` дальше идёт `_`, не цифра). Поля каждой таблицы — те же имена и типы, что у
настоящего пакета (`knowledge/README.md`), значения — `demo`-тексты; обозначения `kind`/`publication_state`/
`risk_level`/`allowed_render_mode`/`machine_scope`/`knowledge_state`/`review_state`/`access_policy` — из
словаря настоящего корпуса (это общие метки состояния схемы, не секрет предприятия), не выдуманы заново, чтобы
Task 1/3/7 не угадывали вид данных.

Намеренно воспроизведена особенность настоящего сидера: `kb_search.kind` для процессов — строка `"processe"`
(опечатка множественного числа в заказанном импортере, не «process»/«processes»). `retrieval.py` (Task 1) должен
искать по этому же виду, не «исправленному» — тест на этой фикстуре и есть защита от такой тихой правки.

Подключение настоящего корпуса заказчика локально — переменная `KDM1_PACKAGE_DIR` (см. `README.md` рядом);
без неё тесты, которым нужен настоящий корпус, помечаются `pytest.mark.skipif`, не падают.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

# полная схема — как `sqlite_master` настоящего kdm1.seed.sqlite3 (core/tests/fixtures/README.md, раздел «схема»)
_PAYLOAD_TABLES = ("sources", "fragments", "processes", "terms", "claims", "conflicts", "equipment_links",
                   "mechanisms")
_SCHEMA_SQL = tuple(
    f"CREATE TABLE {name} (id TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)))"
    for name in _PAYLOAD_TABLES
) + (
    "CREATE TABLE seed_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE VIRTUAL TABLE kb_search USING fts5(kind UNINDEXED, id UNINDEXED, text, tokenize='unicode61')",
)

FAKE_SCOPE = "KDM1_PCBK"          # сама константа схемы не секрет — её несут contracts/*.schema.json
FAKE_TAG_PREFIX = "20FAKE_"
DEFAULT_COUNT = 12                # 10–20 записей на таблицу (план, Task 0, п.4)


def _tag(i: int) -> str:
    return f"{FAKE_TAG_PREFIX}{i:03d}_PV"


def _source_rows(n: int) -> list[tuple[str, dict]]:
    rows = []
    for i in range(1, n + 1):
        kind = "public_primary" if i % 3 else "supplied_oem_manual"
        rows.append((f"SRC-FAKE-{i:03d}", {
            "id": f"SRC-FAKE-{i:03d}", "title": f"Выдуманный источник {i}", "url": f"https://example.invalid/{i}",
            "reviewed_on": "2026-01-01", "supported_context": "demo", "limits": "только для тестов",
            "scope": "public_context_not_local_operational_norm", "type": kind, "sha256": None,
            "publication_state": "desk_verified_reference", "content_capture_state": "selected_editorial_summary_with_locator",
            "local_operating_authority": False, "llm_context_allowed": True,
            "selected_fragment_ids": [f"FRG-FAKE-{i:03d}"],
        }))
    return rows


def _fragment_rows(n: int) -> list[tuple[str, dict]]:
    rows = []
    for i in range(1, n + 1):
        source_id = f"SRC-FAKE-{((i - 1) % DEFAULT_COUNT) + 1:03d}"
        rows.append((f"FRG-FAKE-{i:03d}", {
            "id": f"FRG-FAKE-{i:03d}", "source_id": source_id,
            "locator": {"type": "pdf_page", "page": i},
            "text": f"Выдуманный фрагмент {i} про тег {_tag(i)}.", "text_sha256": f"fake{i:03d}",
            "scope": "KDM1_document_model", "publication_state": "extracted",
            "contains_instructions": False, "current_telemetry": False, "interpretation": "demo",
        }))
    return rows


def _process_rows(n: int) -> list[tuple[str, dict]]:
    rows = []
    for i in range(1, n + 1):
        rows.append((f"PROC-FAKE-{i:03d}", {
            "id": f"PROC-FAKE-{i:03d}", "title": f"Выдуманный узел {i}", "source_label": f"demo-node-{i}",
            "machine_scope": FAKE_SCOPE, "source_refs": [f"SRC-FAKE-{i:03d}"],
            "knowledge_state": "document_label", "actual_name_requires_review": True,
            "inferred_connectivity": None, "operating_limits": None, "conflict_ids": [],
        }))
    return rows


def _term_rows(n: int) -> list[tuple[str, dict]]:
    rows = []
    for i in range(1, n + 1):
        rows.append((f"TERM-FAKE-{i:03d}", {
            "id": f"TERM-FAKE-{i:03d}", "preferred_ru": f"выдуманный термин {i}",
            "aliases": [f"вт{i}"], "definition": f"Определение выдуманного термина {i} — только для тестов.",
            "source_refs": [f"SRC-FAKE-{i:03d}"], "ambiguous": i % 4 == 0,
            "machine_scope": "KDM1_PCBK_related", "knowledge_state": "editorial_definition_or_unresolved_candidate",
            "local_operating_authority": False, "auto_merge_aliases": False,
        }))
    return rows


def _claim_rows(n: int) -> list[tuple[str, dict]]:
    rows = []
    for i in range(1, n + 1):
        kind = "definition" if i % 2 else "document_label"
        rows.append((f"CLAIM-FAKE-{i:03d}", {
            "id": f"CLAIM-FAKE-{i:03d}", "kind": kind, "text": f"Выдуманная карточка {i}: {_tag(i)}.",
            "term_ids": [f"TERM-FAKE-{i:03d}"], "evidence_refs": [f"FRG-FAKE-{i:03d}"],
            "applicability": "KDM1_PCBK, тест", "publication_state": "desk_verified_draft",
            "allowed_render_mode": "attributed_document_literal" if kind == "document_label" else "attributed_reference",
            "risk_level": "historical_document" if kind == "document_label" else "non_operational_reference",
            "permits_local_setpoint": False, "critical_local_fact": False, "reviewer_login": None,
        }))
    return rows


def _conflict_rows(n: int) -> list[tuple[str, dict]]:
    rows = []
    for i in range(1, n + 1):
        rows.append((f"CONF-FAKE-{i:03d}", {
            "id": f"CONF-FAKE-{i:03d}", "description": f"Выдуманное открытое расхождение {i}.", "state": "open",
            "resolution": None, "reviewer_login": None, "resolved_at": None,
            "blocking_scope": "related_local_claims_only",
        }))
    return rows


def _equipment_link_rows(n: int) -> list[tuple[str, dict]]:
    rows = []
    for i in range(1, n + 1):
        rows.append((f"EQ-FAKE-{i:03d}", {
            "id": f"EQ-FAKE-{i:03d}", "display_label": _tag(i), "source_ref": f"SRC-FAKE-{i:03d}",
            "document_context": f"demo-context-{i}", "historian_tag": None, "process_id": None,
            "review_state": "unbound_document_label", "reviewer_login": None, "runtime_read_enabled": False,
            "access_policy": "existing_DataService_only", "changes_global_tag_access": False,
        }))
    return rows


def _mechanism_rows(n: int) -> list[tuple[str, dict]]:
    rows = []
    for i in range(1, n + 1):
        rows.append((f"MECH-FAKE-{i:03d}", {
            "id": f"MECH-FAKE-{i:03d}", "title": f"Выдуманный общий механизм {i}",
            "evidence_refs": [f"FRG-FAKE-{i:03d}"], "template": "qualified_general_hypothesis",
            "required_observations": [f"TERM-FAKE-{i:03d}"], "asserts_actual_cause": False,
            "operating_limits": None, "allows_control_action": False, "publication_state": "desk_verified_draft",
            "applicability": "KDM1_PCBK, тест", "reviewer_login": None,
            "allowed_render_mode": "qualified_general_hypothesis",
        }))
    return rows


_ROW_BUILDERS = {
    "sources": _source_rows, "fragments": _fragment_rows, "processes": _process_rows, "terms": _term_rows,
    "claims": _claim_rows, "conflicts": _conflict_rows, "equipment_links": _equipment_link_rows,
    "mechanisms": _mechanism_rows,
}
# текст для kb_search по виду — то же поле, что заполняет настоящий импортер (knowledge/README.md); "processe" —
# не опечатка этого файла, а воспроизведение вида в настоящем kb_search.kind (см. докстринг модуля)
_SEARCH_TEXT_FIELD = {"claim": ("claims", "text"), "mechanism": ("mechanisms", "title"),
                      "processe": ("processes", "title"), "term": ("terms", "definition")}


def build_corpus(path: str | Path, *, counts: dict[str, int] | None = None) -> Path:
    """Новый файл SQLite по пути `path` (как `tools/import_seed.py` — путь должен быть новым, существующий
    файл не переписывается) со схемой `kdm1.seed.sqlite3` и выдуманными данными. `counts` — число записей на
    таблицу (по умолчанию `DEFAULT_COUNT` у каждой из восьми таблиц)."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path}: файл уже существует — build_corpus не переписывает")
    counts = {**{name: DEFAULT_COUNT for name in _PAYLOAD_TABLES}, **(counts or {})}
    conn = sqlite3.connect(path)
    try:
        for stmt in _SCHEMA_SQL:
            conn.execute(stmt)
        manifest = {
            "schema_version": "2.0", "scope": FAKE_SCOPE, "created_on": "2026-01-01",
            "operating_approval": False, "global_tag_access_modified": False,
            "raw_data_contains_untrusted_instructions": True, "human_review_claimed": False,
            "independent_holdout_included": False,
            "counts": {name: counts[name] for name in _PAYLOAD_TABLES},
        }
        conn.execute("INSERT INTO seed_meta (key, value) VALUES ('manifest', ?)", (json.dumps(manifest),))
        for table, builder in _ROW_BUILDERS.items():
            for row_id, payload in builder(counts[table]):
                conn.execute(f"INSERT INTO {table} (id, payload) VALUES (?, ?)", (row_id, json.dumps(payload)))
        for kind, (table, field) in _SEARCH_TEXT_FIELD.items():
            for row in conn.execute(f"SELECT id, payload FROM {table}").fetchall():
                payload = json.loads(row[1])
                conn.execute("INSERT INTO kb_search (kind, id, text) VALUES (?, ?, ?)",
                            (kind, row[0], payload[field]))
        conn.commit()
    finally:
        conn.close()
    return path
