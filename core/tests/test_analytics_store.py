"""Состояние и принадлежность диалогов аналитики КДМ-1 (план КДМ1, Task 2; `docs/kdm1/INTERFACES.md`,
`analytics/store.py`): миграции с версией, владелец — логин (не место), идемпотентность отправки при гонке,
отмена, восстановление после перезапуска, транзакции, порча/занятость базы — без падения процесса.

Теги корпуса в этом файле — только выдуманные `20FAKE_*` (ворота плана: `git diff --cached | grep -nE
'\\b[0-9]{2}[A-Z]{2,4}[0-9]{2}'` не должен ничего находить)."""
import sqlite3
import threading

import pytest

from helpers import fail_connect
from pcbk_core.analytics import store as store_mod
from pcbk_core.analytics.models import AnalysisRequest, JobResponse, Preanalysis, RequestIdentity
from pcbk_core.analytics.store import AnalyticsStore, CURRENT_SCHEMA_VERSION, StoreError, StoreUnavailable

CORPUS_V1 = "20FAKE_corpus_v1"


def ident(login: str, place: int = 1) -> RequestIdentity:
    return RequestIdentity(login=login, role="student", place=place, caller=f"student-{place:02d}")


def req(client_request_id: str, question: str = "Что означает масса квадратного метра?") -> AnalysisRequest:
    return AnalysisRequest(client_request_id=client_request_id, question=question, mode="reference")


def response_for(request_id: str, conversation_id: str, state: str = "completed") -> JobResponse:
    return JobResponse(request_id=request_id, conversation_id=conversation_id, state=state,
                       corpus_version=CORPUS_V1, stage_label="Готово", sections=[], clarification_keys=[],
                       error_code=None)


def preanalysis_for(decision: str, corpus_version: str = CORPUS_V1) -> Preanalysis:
    return Preanalysis(scope="KDM1_PCBK", intent="definition", decision=decision, resolved_terms=[],
                       process_ids=[], conflict_ids=[], missing_fields=[], risk="reference",
                       corpus_version=corpus_version, evidence_pack_id=None, allowed_claim_ids=[],
                       allowed_mechanism_ids=[], data_access_ready=decision == "ready",
                       llm_export_policy="public_only")


def store(tmp_path, **kw) -> AnalyticsStore:
    s = AnalyticsStore(str(tmp_path / "analytics.db"), **kw)
    assert s.open() is True, s.last_error
    return s


# --- Договор Role: конструктор ничего не открывает ---

def test_constructor_does_no_io(tmp_path):
    AnalyticsStore(str(tmp_path / "нет" / "analytics.db"))
    assert not (tmp_path / "нет").exists()


# --- пустая база, повтор миграции ---

def test_open_empty_db_is_usable_and_idempotent(tmp_path):
    s = store(tmp_path)
    assert s.get_request("нет") is None
    assert s.conversation_owner("нет") is None
    assert s.list_conversations(ident("ivanov")) == []
    assert s.open() is True                                         # повтор миграции — не бросает, не меняет
    with sqlite3.connect(tmp_path / "analytics.db") as con:
        assert con.execute("SELECT version FROM analytics_schema").fetchone()[0] == CURRENT_SCHEMA_VERSION


# --- миграция старой версии ---

def _write_legacy_v1(path) -> None:
    """Таблицы версии 1 (план КДМ1 Task 2): requests без `updated_at`, без `analytics_schema`/
    `analytics_heartbeat` — проверяет настоящий путь миграции `AnalyticsStore.open`, не только пустую базу."""
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE conversations (
        id TEXT PRIMARY KEY, owner_login TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('reference', 'deviation_review')),
        created_at TEXT NOT NULL, last_activity_at TEXT NOT NULL, corpus_version TEXT NOT NULL)""")
    con.execute("""CREATE TABLE requests (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, owner_login TEXT NOT NULL,
        client_request_id TEXT NOT NULL, state TEXT NOT NULL, stage_label TEXT NOT NULL,
        corpus_version TEXT NOT NULL, request_json TEXT NOT NULL, preanalysis_json TEXT,
        response_json TEXT, created_at TEXT NOT NULL)""")
    con.execute("CREATE UNIQUE INDEX requests_idempotency ON requests"
               " (owner_login, conversation_id, client_request_id)")
    con.execute("CREATE TABLE evidence_packs (id TEXT PRIMARY KEY, request_id TEXT NOT NULL,"
               " created_at TEXT NOT NULL)")
    con.execute("CREATE TABLE evidence_items (pack_id TEXT NOT NULL, item_id TEXT NOT NULL,"
               " data_json TEXT NOT NULL, PRIMARY KEY (pack_id, item_id))")
    con.execute("INSERT INTO conversations VALUES ('c1', 'ivanov', 'reference',"
               " '2020-01-01T00:00:00.000000+00:00', '2020-01-01T00:00:00.000000+00:00', '20FAKE_v0')")
    con.execute("INSERT INTO requests (id, conversation_id, owner_login, client_request_id, state, stage_label,"
               " corpus_version, request_json, created_at) VALUES ('r1', 'c1', 'ivanov', 'cr-1', 'completed',"
               " 'Готово', '20FAKE_v0', '{}', '2020-01-01T00:00:00.000000+00:00')")
    con.commit()
    con.close()


def test_open_migrates_legacy_v1_schema(tmp_path):
    path = tmp_path / "analytics.db"
    _write_legacy_v1(path)
    s = AnalyticsStore(str(path))
    assert s.open() is True, s.last_error

    with sqlite3.connect(path) as con:
        cols = {row[1] for row in con.execute("PRAGMA table_info(requests)")}
        assert "updated_at" in cols
        backfilled = con.execute("SELECT updated_at FROM requests WHERE id = 'r1'").fetchone()[0]
        assert backfilled == "2020-01-01T00:00:00.000000+00:00"      # станет created_at, точный момент не известен
        assert con.execute("SELECT version FROM analytics_schema").fetchone()[0] == CURRENT_SCHEMA_VERSION

    rec = s.get_request("r1")                                        # старые данные читаются как обычно
    assert rec is not None and rec.state == "completed" and rec.owner_login == "ivanov"

    assert s.open() is True                                          # повтор после миграции — тоже идемпотентен
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT version FROM analytics_schema").fetchone()[0] == CURRENT_SCHEMA_VERSION


# --- недоступный/занятый файл, порча базы, восстановление ---

def test_open_missing_directory_returns_false(tmp_path):
    s = AnalyticsStore(str(tmp_path / "нет" / "analytics.db"))
    assert s.open() is False and s.last_error
    assert not (tmp_path / "нет").exists()


def test_open_corrupted_file_returns_false_without_crash(tmp_path):
    path = tmp_path / "analytics.db"
    path.write_bytes(b"not a sqlite file, just garbage bytes 0123456789")
    s = AnalyticsStore(str(path))
    assert s.open() is False and s.last_error
    assert str(path) not in s.last_error                             # без пути в тексте


def test_operation_on_broken_connection_raises_storeerror_without_path(tmp_path, monkeypatch):
    s = store(tmp_path)
    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    with pytest.raises(StoreError) as e:
        s.list_conversations(ident("ivanov"))
    assert str(tmp_path) not in str(e.value) and s.op_failures == 1


# --- правка F4b, итоговое ревью (H-18): StoreUnavailable (хранилище недоступно) ≠ StoreError («не найдено») ---

def test_broken_connection_raises_store_unavailable_specifically(tmp_path, monkeypatch):
    """Сбой самого SQLite (заблокирована/недоступна) — именно `StoreUnavailable` (подкласс `StoreError`, но
    отличимый типом), не просто базовый `StoreError`: вызывающий (`gateway/analytics_api.py`) различает их —
    503 vs 404."""
    s = store(tmp_path)
    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    with pytest.raises(StoreUnavailable):
        s.list_conversations(ident("ivanov"))
    with pytest.raises(StoreUnavailable):
        s.create_conversation(ident("ivanov"), "reference", CORPUS_V1)
    with pytest.raises(StoreUnavailable):
        s.get_request("нет")


def test_logical_not_found_or_foreign_is_plain_store_error_not_unavailable(tmp_path):
    """«Диалог не найден/чужой» — решение по правилам (обычный `raise` внутри транзакции `create_request`, не
    `sqlite3.Error`) — остаётся плоским `StoreError`, НЕ `StoreUnavailable`: само хранилище работает исправно,
    503 здесь было бы неверным ответом (то же, что H-18 просит не путать с 404)."""
    s = store(tmp_path)
    with pytest.raises(StoreError) as e:
        s.create_request(ident("ivanov"), "совсем-нет-такого-диалога", req("cr-1"))
    assert not isinstance(e.value, StoreUnavailable)


def test_busy_file_fails_then_recovers(tmp_path, monkeypatch):
    s = store(tmp_path)
    monkeypatch.setattr(store_mod, "SQLITE_TIMEOUT_S", 0.1)
    other = sqlite3.connect(tmp_path / "analytics.db", isolation_level=None)
    other.execute("BEGIN EXCLUSIVE")
    try:
        s.ping()
        assert s.ok is False and s.last_error
    finally:
        other.execute("ROLLBACK")
        other.close()
    s.ping()
    assert s.ok is True and s.last_error is None                      # восстановление без пересоздания объекта


# --- транзакции: откат ---

def test_evidence_pack_duplicate_item_rolls_back_whole_pack(tmp_path):
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    rid, _ = s.create_request(i, conv, req("cr-1"))
    with pytest.raises(StoreError):
        s.save_evidence_pack(rid, (("dup", {"a": 1}), ("dup", {"a": 2})))
    with sqlite3.connect(tmp_path / "analytics.db") as con:
        assert con.execute("SELECT COUNT(*) FROM evidence_packs").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 0


# --- владелец — логин, не место; изоляция между личностями ---

def test_conversation_ownership_isolated_between_identities(tmp_path):
    s = store(tmp_path)
    a, b = ident("ivanov", place=3), ident("petrov", place=7)
    conv_a = s.create_conversation(a, "reference", CORPUS_V1)

    assert s.conversation_owner(conv_a) == "ivanov"
    assert s.list_conversations(a) and s.list_conversations(a)[0].id == conv_a
    assert s.list_conversations(b) == []                              # чужой диалог не виден вовсе

    with pytest.raises(StoreError) as e_foreign:
        s.create_request(b, conv_a, req("cr-1"))
    with pytest.raises(StoreError) as e_missing:
        s.create_request(b, "совсем-нет-такого", req("cr-1"))
    assert str(e_foreign.value) == str(e_missing.value)                # один текст — не раскрывает существование


def test_place_reassignment_does_not_transfer_history(tmp_path):
    """Переназначение рабочего места (то же `place`/`caller`, другой `login`) не передаёт историю: владелец —
    `identity.login` (мастер-промпт §6)."""
    s = store(tmp_path)
    old_tenant = ident("ivanov", place=3)
    new_tenant = ident("petrov", place=3)                              # то же место, другой человек
    conv = s.create_conversation(old_tenant, "reference", CORPUS_V1)

    assert s.list_conversations(new_tenant) == []
    assert s.conversation_owner(conv) == "ivanov"
    with pytest.raises(StoreError):
        s.create_request(new_tenant, conv, req("cr-1"))


# --- идемпотентность отправки ---

def test_create_request_idempotent_on_repeat_call(tmp_path):
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    same_request = req("cr-1")

    rid1, created1 = s.create_request(i, conv, same_request)
    rid2, created2 = s.create_request(i, conv, same_request)
    assert rid1 == rid2 and created1 is True and created2 is False

    rid3, created3 = s.create_request(i, conv, req("cr-2"))             # другой client_request_id — новое задание
    assert created3 is True and rid3 != rid1

    with sqlite3.connect(tmp_path / "analytics.db") as con:
        assert con.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2


def test_create_request_idempotent_under_concurrent_threads(tmp_path):
    """ОСОБЫЙ ФОКУС ПРОВЕРКИ №3 плана: конкурентный повтор одного `client_request_id` не создаёт второе
    задание и не платит второй раз."""
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    same_request = req("race-1")

    results: list[tuple[str, bool]] = []
    lock = threading.Lock()

    def worker() -> None:
        r = s.create_request(i, conv, same_request)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({rid for rid, _ in results}) == 1                       # все потоки видят один и тот же id
    assert sum(1 for _, created in results if created) == 1            # ровно один «настоящий» создатель
    with sqlite3.connect(tmp_path / "analytics.db") as con:
        assert con.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


# --- отмена ---

def test_cancel_request_rules(tmp_path):
    """cancel_request — `(cancelled, stage_before)`: Правка, раунд 1, Important 4 — обе части одной транзакции,
    не отдельное более раннее чтение стадии (ниже, `test_cancel_request_reports_stage_from_same_transaction`)."""
    s = store(tmp_path)
    a, b = ident("ivanov"), ident("petrov")
    conv = s.create_conversation(a, "reference", CORPUS_V1)
    rid, _ = s.create_request(a, conv, req("cr-1"))

    assert s.cancel_request(b, rid) == (False, None)                   # чужое задание
    assert s.get_request(rid).state == "queued"                        # без изменений

    assert s.cancel_request(a, rid) == (True, "queued")
    assert s.get_request(rid).state == "cancelled"

    # повтор отмены — уже терминально; stage_before — само терминальное состояние, не None (чтобы не путать
    # «чужое/нет задания» с «уже завершено» по одному и тому же False)
    assert s.cancel_request(a, rid) == (False, "cancelled")

    # ОСОБЫЙ ФОКУС ПРОВЕРКИ №3: поздняя публикация не перетирает отмену (ни оплаты, ни потери резерва)
    assert s.publish_response(rid, response_for(rid, conv)) is False
    assert s.get_request(rid).state == "cancelled" and s.get_request(rid).response is None


def test_cancel_request_reports_stage_from_same_transaction_not_stale_read(tmp_path):
    """Правка, раунд 1, Important 4: `stage_before` берётся в ТОЙ ЖЕ транзакции, что сама отмена — не из
    отдельного более раннего чтения (окно гонки: `_run_job` успевает продвинуть задание до `generating` между
    чужим снимком стадии и вызовом `cancel_request`). Задание, уже дошедшее до `generating`, отменяется
    (`cancel_request` не блокирует отмену по стадии — это решение вызывающего, прерывать ли фоновую задачу, не
    запрет отмены самой записи), и `stage_before` показывает именно `generating`, не более раннюю стадию."""
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    rid, _ = s.create_request(i, conv, req("cr-1"))
    s.advance_stage(rid, "retrieving", "поиск оснований")
    s.advance_stage(rid, "generating", "модель думает")

    assert s.cancel_request(i, rid) == (True, "generating")
    assert s.get_request(rid).state == "cancelled"


# --- преданализ: маршрут решения, терминальная стадия — только через publish_response ---

@pytest.mark.parametrize("decision,expected_state", [
    ("ready", "retrieving"),
    ("needs_clarification", "validating"),
    ("limited_answer", "validating"),
    ("refused", "validating"),
])
def test_set_preanalysis_decision_routing(tmp_path, decision, expected_state):
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    rid, _ = s.create_request(i, conv, req("cr-1"))

    s.set_preanalysis(rid, preanalysis_for(decision))
    rec = s.get_request(rid)
    assert rec.state == expected_state and rec.response is None        # ответа клиенту пока нет

    # все четыре решения в итоге публикуются одной и той же проверенной операцией
    final_state = "needs_clarification" if decision == "needs_clarification" else \
                  "limited_answer" if decision == "limited_answer" else \
                  "failed" if decision == "refused" else "completed"
    assert s.publish_response(rid, response_for(rid, conv, state=final_state)) is True
    assert s.get_request(rid).state == final_state


def test_set_preanalysis_after_cancel_keeps_cancelled_stage(tmp_path):
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    rid, _ = s.create_request(i, conv, req("cr-1"))
    assert s.cancel_request(i, rid) == (True, "queued")

    s.set_preanalysis(rid, preanalysis_for("ready"))                    # гонка: анализ пришёл после отмены
    rec = s.get_request(rid)
    assert rec.state == "cancelled"                                    # стадию анализ не передвинул


# --- публикация ответа: терминальность, неперезаписываемость ---

def test_publish_response_terminal_and_not_overwritable(tmp_path):
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    rid, _ = s.create_request(i, conv, req("cr-1"))

    assert s.publish_response(rid, response_for(rid, conv)) is True
    rec = s.get_request(rid)
    assert rec.state == "completed" and rec.response is not None and rec.response.request_id == rid

    second = response_for(rid, conv, state="failed")
    assert s.publish_response(rid, second) is False                    # не перезатирает чужую публикацию
    assert s.get_request(rid).state == "completed"


def test_advance_stage_progresses_then_blocked_after_terminal(tmp_path):
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    rid, _ = s.create_request(i, conv, req("cr-1"))

    assert s.advance_stage(rid, "retrieving", "поиск оснований") is True
    assert s.advance_stage(rid, "generating", "модель думает") is True
    assert s.publish_response(rid, response_for(rid, conv)) is True
    assert s.advance_stage(rid, "validating", "опоздавшая стадия") is False
    assert s.get_request(rid).state == "completed"                     # стадия после публикации не сдвинулась


def test_advance_stage_unknown_request_returns_false(tmp_path):
    s = store(tmp_path)
    assert s.advance_stage("нет-такого", "retrieving", "x") is False


# --- восстановление после перезапуска ---

def test_reap_unfinished_after_restart(tmp_path):
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)

    r_queued, _ = s.create_request(i, conv, req("a"))
    r_generating, _ = s.create_request(i, conv, req("b"))
    s.advance_stage(r_generating, "generating", "модель думает")
    r_done, _ = s.create_request(i, conv, req("c"))
    s.publish_response(r_done, response_for(r_done, conv))
    r_cancelled, _ = s.create_request(i, conv, req("d"))
    s.cancel_request(i, r_cancelled)

    assert s.reap_unfinished_after_restart() == 2

    assert s.get_request(r_queued).state == "failed"
    assert s.get_request(r_generating).state == "failed"
    assert s.get_request(r_done).state == "completed"                  # опубликованное не трогается
    assert s.get_request(r_cancelled).state == "cancelled"             # отменённое не трогается

    assert s.reap_unfinished_after_restart() == 0                       # повтор — уже всё терминально

    # неизвестно оплаченный вызов не повторяется автоматически: поздняя публикация отклоняется
    assert s.publish_response(r_generating, response_for(r_generating, conv)) is False


# --- пакеты доказательств: нет оракула между «нет пакета» и «нет элемента» ---

def test_evidence_item_lookup_returns_none_without_oracle(tmp_path):
    s = store(tmp_path)
    i = ident("ivanov")
    conv = s.create_conversation(i, "reference", CORPUS_V1)
    rid, _ = s.create_request(i, conv, req("cr-1"))
    pack_id = s.save_evidence_pack(rid, (("e1", {"text": "фрагмент"}),))

    assert s.get_evidence_item(pack_id, "e1") == {"text": "фрагмент"}
    assert s.get_evidence_item(pack_id, "нет-такого") is None
    assert s.get_evidence_item("нет-такого-пакета", "e1") is None

    second_pack_id = s.save_evidence_pack(rid, (("e1", {"text": "новый фрагмент"}),))
    assert second_pack_id != pack_id
    assert s.get_evidence_item(pack_id, "e1") == {"text": "фрагмент"}   # старый пакет не переписан


# --- хранение / сроки ---

def test_sweep_retention_deletes_old_conversations_and_evidence(tmp_path):
    clock = [1_700_000_000.0]
    s = store(tmp_path, clock=lambda: clock[0])
    i = ident("ivanov")

    old_conv = s.create_conversation(i, "reference", CORPUS_V1)
    rid, _ = s.create_request(i, old_conv, req("cr-1"))
    old_pack = s.save_evidence_pack(rid, (("e1", {"a": 1}),))

    clock[0] += 100 * 86400.0                                           # +100 дней
    new_conv = s.create_conversation(i, "reference", CORPUS_V1)

    from datetime import datetime, timezone
    now = datetime.fromtimestamp(clock[0], tz=timezone.utc)
    deleted_conv, deleted_packs = s.sweep_retention(now, history_days=30.0, evidence_days=30.0)

    assert (deleted_conv, deleted_packs) == (1, 1)
    assert s.conversation_owner(old_conv) is None
    assert s.conversation_owner(new_conv) is not None
    assert s.get_request(rid) is None
    assert s.get_evidence_item(old_pack, "e1") is None
