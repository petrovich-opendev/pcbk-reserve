"""Назначенное согласование КДМ-1 (план КДМ1, Task 7; `docs/kdm1/INTERFACES.md`, `analytics/reviews.py`):
отсутствие полномочия у обычного технолога, запрет самоназначения, срок/область, отзыв права и решения,
журнал заявок/решений, видимость заявок только имеющему право, и `resolved_link`/`approved_claim` —
действующее согласование проверяется заново при каждом обращении, не по факту решения в прошлом.

Правка раунд 1 (2026-10-14, ревью контроллера): первое назначение полномочия (бутстрап пустого хранилища)
теперь отдельный метод `bootstrap_authority` (не `grant_authority` с исключением) — закрыто кодом, не только
отсутствием веб-маршрута; `test_grant_authority_never_bootstraps_*` ниже это проверяет напрямую.

Правка F3 (итоговое ревью, 2026-10-14) — секция `=== F3 ===` в конце файла: срок полномочия (`valid_until`),
`info_requested` не терминален (журнал `review_decisions`), второй независимый согласующий для
`local_claim`/`local_norm`.

Правка F5 (итоговое ревью, 2026-10-14, решение владельца) — секция `=== F5 ===` в конце файла:
`reviewers_mode="all_students"` — любой student решает любую заявку области без назначения, в одно действие
(без второго согласующего, без запрета автору); teacher — исключение, только по назначению, как в `grants`;
переключение режима обесценивает `approved_claim`/`resolved_link` студенческих решений для нового применения;
`ReviewStoreUnavailable` — отдельный подкласс для настоящего сбоя SQLite (не для «заявка не найдена»).

Теги/подписи — только выдуманные `20FAKE_*`/`EQ-FAKE-*` (ворота плана: `git diff --cached | grep -nE
'\\b[0-9]{2}[A-Z]{2,4}[0-9]{2}'` не должен ничего находить)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from helpers import fail_connect
from pcbk_core.analytics.models import RequestIdentity
from pcbk_core.analytics.reviews import (AuthorityGrant, ReviewRequest, ReviewStore, ReviewStoreError,
                                         ReviewStoreUnavailable, _OperatorIdentity, main as reviews_main)


def ident(login: str, role: str = "teacher", place: int | None = None) -> RequestIdentity:
    caller = "teacher" if role == "teacher" else f"student-{place:02d}"
    return RequestIdentity(login=login, role=role, place=place, caller=caller)


def bootstrap(store: ReviewStore, login: str, scope: str, decision_types: tuple, operator: str = "operator"
             ) -> AuthorityGrant:
    """Помощник тестов: первое назначение в заведомо пустом хранилище — всегда `bootstrap_authority`, не
    `grant_authority` (она больше не бутстрапит, правка раунд 1)."""
    return store.bootstrap_authority(login, scope, decision_types, operator=operator)


OPERATOR = ident("operator")
TECH1 = ident("tech1")
TECH2 = ident("tech2")
STUDENT = ident("stu-05", role="student", place=5)
SCOPE = "PROC-FAKE-001"


@pytest.fixture
def store(tmp_path) -> ReviewStore:
    s = ReviewStore(str(tmp_path / "analytics.db"))
    assert s.open()
    return s


# --- открытие ---

def test_open_is_idempotent(tmp_path):
    path = str(tmp_path / "analytics.db")
    assert ReviewStore(path).open()
    assert ReviewStore(path).open()           # повтор на уже заполненной базе — не падает


def test_open_false_on_missing_dir():
    assert ReviewStore("/no/such/dir/analytics.db").open() is False


# --- полномочия: отсутствие по умолчанию, самоназначение, бутстрап, отзыв ---

def test_ordinary_teacher_has_no_authority_by_default(store):
    """Роль teacher сама по себе не даёт права (мастер-промпт §6) — без явного назначения активного
    полномочия нет ни у кого, в т.ч. у «оператора» до первого вызова."""
    assert store.active_grant("tech1", SCOPE, "local_claim") is None
    assert store.active_grant(STUDENT.login, SCOPE, "local_claim") is None


def test_self_grant_is_forbidden(store):                                            # T-44 ACCEPTANCE_CASES
    with pytest.raises(ValueError):
        store.grant_authority("operator", SCOPE, ("local_claim",), granted_by=OPERATOR)


def test_bootstrap_authority_forbids_self_assignment(store):                        # T-44, тот же запрет и тут
    with pytest.raises(ValueError):
        store.bootstrap_authority("operator", SCOPE, ("local_claim",), operator="operator")


def test_bootstrap_authority_only_works_once_on_empty_store(store):
    """Первое назначение — bootstrap_authority на полностью пустом хранилище; второй вызов (хранилище уже не
    пусто) — отказ, дальше только grant_authority держателем полномочия."""
    grant = bootstrap(store, TECH1.login, SCOPE, ("local_claim", "conflict"))
    assert isinstance(grant, AuthorityGrant)
    assert grant.decision_types == ("conflict", "local_claim")       # сортировка хранения, не порядок ввода
    assert grant.granted_by == "operator"
    with pytest.raises(ReviewStoreError):
        store.bootstrap_authority(TECH2.login, SCOPE, ("local_claim",), operator="operator")
    # TECH1 уже держит действующее полномочие в SCOPE — может назначить TECH2 в той же области через grant_authority
    store.grant_authority(TECH2.login, SCOPE, ("equipment_link",), granted_by=TECH1)
    assert store.active_grant(TECH2.login, SCOPE, "equipment_link") is not None


def test_grant_authority_never_bootstraps_even_for_operator_identity(store):          # Important (а), раунд 1
    """Пустое хранилище закрыто для grant_authority кодом, не только отсутствием веб-маршрута: ни обычная
    RequestIdentity, ни операторская личность CLI (_OperatorIdentity — тот же объект, что main() передаёт в
    grant_authority после bootstrap) не заводят первый ряд полномочий этим методом — только
    bootstrap_authority умеет работать с пустым хранилищем."""
    with pytest.raises(PermissionError):
        store.grant_authority(TECH1.login, SCOPE, ("local_claim",), granted_by=OPERATOR)
    with pytest.raises(PermissionError):
        store.grant_authority(TECH1.login, SCOPE, ("local_claim",), granted_by=_OperatorIdentity("operator"))
    assert store.active_grant(TECH1.login, SCOPE, "local_claim") is None   # ни один вызов не создал запись


def test_grant_authority_rejects_unknown_decision_type(store):
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    with pytest.raises(ValueError):
        store.grant_authority(TECH2.login, SCOPE, ("no_such_kind",), granted_by=TECH1)


def test_bootstrap_authority_rejects_unknown_decision_type(store):
    with pytest.raises(ValueError):
        store.bootstrap_authority(TECH1.login, SCOPE, ("no_such_kind",), operator="operator")


def test_revoke_authority_disables_active_grant_and_is_idempotent(store):
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    assert store.active_grant(TECH1.login, SCOPE, "local_claim") is not None
    assert store.revoke_authority(TECH1.login, SCOPE, revoked_by=OPERATOR) is True
    assert store.active_grant(TECH1.login, SCOPE, "local_claim") is None
    assert store.revoke_authority(TECH1.login, SCOPE, revoked_by=OPERATOR) is False      # уже отозвано


def test_revoke_authority_false_when_never_granted(store):
    assert store.revoke_authority("nobody", SCOPE, revoked_by=OPERATOR) is False


def test_expired_grant_via_scope_mismatch_is_not_active(store):
    """Полномочие в одной области не действует в другой (область — часть ключа проверки, не только логин)."""
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    assert store.active_grant(TECH1.login, "PROC-FAKE-002", "local_claim") is None


def test_has_any_active_grant_true_in_any_scope_false_after_revoke(store):             # Task 7, шаг 2
    """`Capabilities.review_authority`: держит хоть какое-то право где-то внутри КДМ-1, не обязательно на
    весь верхний scope сразу."""
    assert store.has_any_active_grant(TECH1.login) is False
    bootstrap(store, TECH1.login, "PROC-FAKE-002", ("local_claim",))     # произвольная узкая область
    assert store.has_any_active_grant(TECH1.login) is True
    store.revoke_authority(TECH1.login, "PROC-FAKE-002", revoked_by=OPERATOR)
    assert store.has_any_active_grant(TECH1.login) is False


# --- заявки: создание, решение, повтор, видимость ---

def test_create_request_defaults_pending_and_anyone_can_file(store):
    """Создание заявки не требует полномочия — её может отправить любой (AnalyticsPage.tsx, Task 6)."""
    r = store.create_request("conflict", "CONF-FAKE-001", "рассмотреть конфликт источников", SCOPE,
                             "расхождение источников в ответе", created_by=STUDENT)
    assert isinstance(r, ReviewRequest)
    assert (r.status, r.created_by, r.decided_by) == ("pending", STUDENT.login, None)


def test_create_request_rejects_period_end_before_start(store):
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        store.create_request("local_norm", "CLAIM-FAKE-001", "новое значение", SCOPE, "основание",
                             created_by=STUDENT, period_start=start, period_end=end)


def test_decide_requires_active_grant_matching_scope_and_kind(store):
    """Без видимости на эту заявку (не автор, нет полномочия на её kind/scope) — ReviewStoreError, тот же
    отказ, что у несуществующего request_id (правка контроллера, 2026-10-14: не раскрывать посторонним факт
    существования заявки)."""
    r = store.create_request("local_claim", "CLAIM-FAKE-001", "новая формулировка", SCOPE, "основание",
                             created_by=STUDENT)
    with pytest.raises(ReviewStoreError):
        store.decide(r.id, "accepted", "без права", decided_by=TECH1)
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    decided = store.decide(r.id, "accepted", "подтверждено", decided_by=TECH1)
    assert (decided.status, decided.decided_by, decided.comment) == ("accepted", TECH1.login, "подтверждено")


def test_decide_wrong_kind_grant_is_not_enough(store):
    """Полномочие на equipment_link не даёт решать local_claim в той же области (вид решения — часть ключа
    видимости, не только права): для держателя не того вида заявка НЕВИДИМА — ReviewStoreError, как у
    несуществующей, не PermissionError (правка контроллера, 2026-10-14)."""
    r = store.create_request("local_claim", "CLAIM-FAKE-002", "новая формулировка", SCOPE, "основание",
                             created_by=STUDENT)
    bootstrap(store, TECH1.login, SCOPE, ("equipment_link",))
    with pytest.raises(ReviewStoreError):
        store.decide(r.id, "accepted", "", decided_by=TECH1)


def test_decide_stranger_without_visibility_gets_same_not_found_for_any_status(store):   # Important, 2026-10-14
    """Посторонний без права (не автор заявки, нет действующего полномочия на её kind/scope) получает РОВНО
    ТОТ ЖЕ отказ (класс и текст исключения), что для несуществующего request_id, — независимо от истинного
    статуса заявки (pending/решена): по ответу нельзя различить существование или статус чужой заявки."""
    stranger = ident("tech9")
    pending = store.create_request("conflict", "CONF-FAKE-900", "значение", SCOPE, "основание",
                                   created_by=STUDENT)
    bootstrap(store, TECH1.login, SCOPE, ("conflict",))
    decided_request = store.create_request("conflict", "CONF-FAKE-901", "значение", SCOPE, "основание",
                                           created_by=STUDENT)
    store.decide(decided_request.id, "accepted", "", decided_by=TECH1)

    texts: set[str] = set()
    for rid in (pending.id, decided_request.id, "no-such-review-id"):
        with pytest.raises(ReviewStoreError) as exc_info:
            store.decide(rid, "accepted", "", decided_by=stranger)
        texts.add(str(exc_info.value))
    assert len(texts) == 1               # один и тот же класс/текст на все три случая


def test_decide_rejects_second_decision_on_same_request(store):
    r = store.create_request("local_norm", "CLAIM-FAKE-003", "новый норматив", SCOPE, "основание",
                             created_by=STUDENT)
    bootstrap(store, TECH1.login, SCOPE, ("local_norm",))
    store.decide(r.id, "rejected", "недостаточно оснований", decided_by=TECH1)
    with pytest.raises(ValueError):
        store.decide(r.id, "accepted", "передумал", decided_by=TECH1)


def test_decide_forbids_author_deciding_own_request_even_with_authority(store):       # Task 7, шаг 2
    """Автор заявки не решает её сам, даже держа нужное действующее полномочие — симметрично запрету
    самоназначения (T-44)."""
    bootstrap(store, STUDENT.login, SCOPE, ("conflict",))
    r = store.create_request("conflict", "CONF-FAKE-777", "значение", SCOPE, "основание", created_by=STUDENT)
    with pytest.raises(PermissionError):
        store.decide(r.id, "accepted", "", decided_by=STUDENT)
    assert store.list_requests(STUDENT, status="pending") == [r]     # заявка осталась нерешённой


def test_decide_normalizes_blank_comment_to_none(store):
    """Пустой/пробельный комментарий хранится как None, не как "" (веб isNullableText не принимает "")."""
    bootstrap(store, TECH1.login, SCOPE, ("conflict",))
    r = store.create_request("conflict", "CONF-FAKE-778", "значение", SCOPE, "основание", created_by=STUDENT)
    decided = store.decide(r.id, "accepted", "   ", decided_by=TECH1)
    assert decided.comment is None


def test_decide_unknown_request_raises_review_store_error(store):
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    with pytest.raises(ReviewStoreError):
        store.decide("no-such-id", "accepted", "", decided_by=TECH1)


def test_decide_rejects_unknown_decision_value(store):
    r = store.create_request("conflict", "CONF-FAKE-002", "решить конфликт", SCOPE, "основание",
                             created_by=STUDENT)
    bootstrap(store, TECH1.login, SCOPE, ("conflict",))
    with pytest.raises(ValueError):
        store.decide(r.id, "maybe", "", decided_by=TECH1)


def test_list_requests_visible_to_author_and_to_authority_holder_only(store):
    """Чужие заявки видит только имеющий право (мастер-промпт §6, план «обязательные проверки» п.2)."""
    r1 = store.create_request("local_claim", "CLAIM-FAKE-010", "значение", SCOPE, "основание", created_by=STUDENT)
    other_author = ident("stu-06", role="student", place=6)
    r2 = store.create_request("local_claim", "CLAIM-FAKE-011", "значение", "PROC-FAKE-009", "основание",
                              created_by=other_author)
    # сам автор видит свою заявку
    assert {r.id for r in store.list_requests(STUDENT)} == {r1.id}
    # посторонний технолог без полномочия не видит ничьи заявки
    stranger = ident("tech9")
    assert store.list_requests(stranger) == []
    # обладатель полномочия в SCOPE видит заявку в этой области (не в PROC-FAKE-009, там у него права нет)
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    visible_ids = {r.id for r in store.list_requests(TECH1)}
    assert visible_ids == {r1.id}
    assert r2.id not in visible_ids


def test_list_requests_filters_by_status(store):
    r = store.create_request("conflict", "CONF-FAKE-003", "значение", SCOPE, "основание", created_by=STUDENT)
    bootstrap(store, TECH1.login, SCOPE, ("conflict",))
    store.decide(r.id, "accepted", "", decided_by=TECH1)
    pending = store.create_request("conflict", "CONF-FAKE-004", "значение", SCOPE, "основание", created_by=STUDENT)
    assert {x.id for x in store.list_requests(TECH1, status="accepted")} == {r.id}
    assert {x.id for x in store.list_requests(TECH1, status="pending")} == {pending.id}


# --- resolved_link: 67 исходных подписей без связи, отзыв сразу обесценивает, срок заявки не проверяется ---

def test_resolved_link_none_without_any_request(store):                              # I-25 INTEGRATION_CASES
    """Точное совпадение подписи/display_label с именем тега сама по себе не включает чтение: без заявки вовсе
    (т.е. без какого-либо решения) resolved_link — None для любого link_id."""
    assert store.resolved_link("EQ-FAKE-001") is None


def test_resolved_link_none_while_pending_or_rejected(store):
    bootstrap(store, TECH1.login, SCOPE, ("equipment_link",))
    pending = store.create_request("equipment_link", "EQ-FAKE-002", "20FAKE_002_PV", SCOPE,
                                   "подпись совпадает с руководством", created_by=STUDENT)
    assert store.resolved_link(pending.subject_id) is None
    rejected = store.create_request("equipment_link", "EQ-FAKE-003", "20FAKE_003_PV", SCOPE,
                                    "подпись совпадает с руководством", created_by=STUDENT)
    store.decide(rejected.id, "rejected", "не доказано", decided_by=TECH1)
    assert store.resolved_link(rejected.subject_id) is None


def test_resolved_link_present_after_accept_and_none_after_authority_revoked(store):
    """Принятая заявка даёт ResolvedLink с ровно теми же tag/scope; отзыв полномочия решившего сразу
    обесценивает применение (resolved_link → None), но историческая запись заявки остаётся accepted (план,
    обязательные проверки п.3)."""
    bootstrap(store, TECH1.login, SCOPE, ("equipment_link",))
    r = store.create_request("equipment_link", "EQ-FAKE-001", "20FAKE_001_PV", SCOPE,
                             "подпись совпадает с руководством Valmet", created_by=STUDENT)
    store.decide(r.id, "accepted", "сверено по паспорту узла", decided_by=TECH1)
    link = store.resolved_link("EQ-FAKE-001")
    assert link is not None
    assert (link.link_id, link.historian_tag, link.process_id, link.scope, link.approved_by) == (
        "EQ-FAKE-001", "20FAKE_001_PV", SCOPE, SCOPE, TECH1.login)
    store.revoke_authority(TECH1.login, SCOPE, revoked_by=OPERATOR)
    assert store.resolved_link("EQ-FAKE-001") is None
    # историческая запись решения цела — не переписана отзывом полномочия
    still = [x for x in store.list_requests(TECH1, status="accepted")]
    assert still == []                           # TECH1 больше не видит её — полномочия нет; запись не удалена
    raw = sqlite3.connect(store.path)
    try:
        row = raw.execute("SELECT status FROM review_requests WHERE id = ?", (r.id,)).fetchone()
    finally:
        raw.close()
    assert row == ("accepted",)


def test_resolved_link_picks_most_recent_accepted_decision(store):
    """Два решения по одному subject_id (вторая заявка после отмены первой учёткой-согласующим) —
    resolved_link берёт более новое принятое решение, не первое попавшееся."""
    bootstrap(store, TECH1.login, SCOPE, ("equipment_link",))
    first = store.create_request("equipment_link", "EQ-FAKE-005", "20FAKE_001_PV", SCOPE, "первая заявка",
                                 created_by=STUDENT)
    store.decide(first.id, "accepted", "", decided_by=TECH1)
    second = store.create_request("equipment_link", "EQ-FAKE-005", "20FAKE_004_PV", SCOPE, "уточнённая заявка",
                                  created_by=STUDENT)
    store.decide(second.id, "accepted", "уточнение тега", decided_by=TECH1)
    link = store.resolved_link("EQ-FAKE-005")
    assert link is not None and link.historian_tag == "20FAKE_004_PV"


def test_has_any_resolved_link_true_after_accept_false_after_revoke(store):           # Task 7, шаг 2
    """`Capabilities.data_bridge_available`: хоть одна действующая связь где угодно, не конкретный link_id."""
    assert store.has_any_resolved_link() is False
    bootstrap(store, TECH1.login, SCOPE, ("equipment_link",))
    r = store.create_request("equipment_link", "EQ-FAKE-800", "20FAKE_001_PV", SCOPE, "основание",
                             created_by=STUDENT)
    assert store.has_any_resolved_link() is False               # pending — ещё не accepted
    store.decide(r.id, "accepted", "", decided_by=TECH1)
    assert store.has_any_resolved_link() is True
    store.revoke_authority(TECH1.login, SCOPE, revoked_by=OPERATOR)
    assert store.has_any_resolved_link() is False


def test_resolved_link_ignores_request_period_even_when_already_expired(store):       # Minor, раунд 1
    """Связь оборудования — не временное утверждение (докстринг resolved_link): даже явно истёкший
    period_start/period_end заявки equipment_link не мешает resolved_link — в отличие от approved_claim, срок
    здесь не проверяется вовсе."""
    bootstrap(store, TECH1.login, SCOPE, ("equipment_link",))
    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    r = store.create_request("equipment_link", "EQ-FAKE-777", "20FAKE_001_PV", SCOPE,
                             "подпись совпадает с руководством", created_by=STUDENT,
                             period_start=long_ago - timedelta(days=10), period_end=long_ago)
    store.decide(r.id, "accepted", "", decided_by=TECH1)
    link = store.resolved_link("EQ-FAKE-777")
    assert link is not None and link.historian_tag == "20FAKE_001_PV"


# --- approved_claim: действующее согласование карточки (дополнение контроллера к Task 7) ---

def test_approved_claim_none_without_decision(store):
    assert store.approved_claim("CLAIM-FAKE-100", SCOPE) is None


def test_approved_claim_present_after_accept_and_respects_period(store):
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    now = datetime.now(timezone.utc)
    r = store.create_request("local_claim", "CLAIM-FAKE-100", "критический местный факт", SCOPE, "основание",
                             created_by=STUDENT, period_start=now - timedelta(days=1),
                             period_end=now + timedelta(days=1))
    store.decide(r.id, "accepted", "подтверждено", decided_by=TECH1)
    approved = store.approved_claim("CLAIM-FAKE-100", SCOPE, at=now)
    assert approved is not None and approved.id == r.id
    assert store.approved_claim("CLAIM-FAKE-100", SCOPE, at=now + timedelta(days=5)) is None     # вне срока
    assert store.approved_claim("CLAIM-FAKE-100", "PROC-FAKE-009") is None                       # вне области


def test_approved_claim_none_after_authority_revoked(store):
    bootstrap(store, TECH1.login, SCOPE, ("local_norm",))
    r = store.create_request("local_norm", "CLAIM-FAKE-101", "новый норматив", SCOPE, "основание",
                             created_by=STUDENT)
    store.decide(r.id, "accepted", "", decided_by=TECH1)
    assert store.approved_claim("CLAIM-FAKE-101", SCOPE) is not None
    store.revoke_authority(TECH1.login, SCOPE, revoked_by=OPERATOR)
    assert store.approved_claim("CLAIM-FAKE-101", SCOPE) is None


def test_approved_claim_ignores_equipment_link_kind(store):
    """approved_claim — только карточки/конфликты/нормативы, не связи оборудования (та заявка решается
    тем же decide, но подключение к читающим тег функциям — resolved_link, другой путь данных)."""
    bootstrap(store, TECH1.login, SCOPE, ("equipment_link",))
    r = store.create_request("equipment_link", "EQ-FAKE-900", "20FAKE_001_PV", SCOPE, "основание",
                             created_by=STUDENT)
    store.decide(r.id, "accepted", "", decided_by=TECH1)
    assert store.approved_claim("EQ-FAKE-900", SCOPE) is None


# --- сбой SQLite ---

def test_sqlite_failure_raises_review_store_error_not_sqlite_error(store, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    with pytest.raises(ReviewStoreError):
        store.active_grant(TECH1.login, SCOPE, "local_claim")


def test_sqlite_failure_is_review_store_unavailable_not_plain_review_store_error(store, monkeypatch):      # F5
    """Правка F5, п.6: сбой самого SQLite — отдельный подкласс `ReviewStoreUnavailable`, отличимый от плоского
    `ReviewStoreError`, который `decide()` бросает НАПРЯМУЮ по правилам (заявка не найдена/нет видимости) —
    `gateway/analytics_api.py` отображает их в разные коды (503 против 404)."""
    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    with pytest.raises(ReviewStoreUnavailable):
        store.active_grant(TECH1.login, SCOPE, "local_claim")


def test_decide_unknown_request_is_plain_review_store_error_not_unavailable(store):                        # F5
    """«Заявка не найдена» — решение по правилам, НЕ сбой инфраструктуры: не поднимается до
    `ReviewStoreUnavailable` (иначе gateway отдал бы 503 вместо 404 для обычного чужого/несуществующего id)."""
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    with pytest.raises(ReviewStoreError) as exc_info:
        store.decide("no-such-id", "accepted", "", decided_by=TECH1)
    assert not isinstance(exc_info.value, ReviewStoreUnavailable)


# --- команда оператора: python -m pcbk_core.analytics.reviews bootstrap|grant|revoke|list ---

def test_cli_bootstrap_then_grant_then_list_then_revoke(tmp_path, capsys):
    db = str(tmp_path / "analytics.db")
    assert reviews_main(["bootstrap", "--db", db, "--login", "tech1", "--scope", SCOPE, "--types",
                         "local_claim,conflict", "--by", "operator"]) == 0
    capsys.readouterr()
    assert reviews_main(["list", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "tech1" in out and SCOPE in out and "действует" in out
    assert reviews_main(["revoke", "--db", db, "--login", "tech1", "--scope", SCOPE, "--by", "operator"]) == 0
    capsys.readouterr()
    assert reviews_main(["list", "--db", db, "--login", "tech1"]) == 0
    assert "отозвано" in capsys.readouterr().out


def test_cli_bootstrap_twice_fails(tmp_path, capsys):
    db = str(tmp_path / "analytics.db")
    assert reviews_main(["bootstrap", "--db", db, "--login", "tech1", "--scope", SCOPE, "--types", "local_claim",
                         "--by", "operator"]) == 0
    capsys.readouterr()
    rc = reviews_main(["bootstrap", "--db", db, "--login", "tech2", "--scope", SCOPE, "--types", "local_claim",
                      "--by", "operator"])
    assert rc == 1
    assert capsys.readouterr().err.strip() != ""


def test_cli_grant_without_prior_bootstrap_fails(tmp_path, capsys):               # Important (а), раунд 1
    """grant на совсем пустом хранилище не бутстрапит сам — для первого раза нужна отдельная команда
    bootstrap: закрыто и на уровне CLI, не только стора."""
    db = str(tmp_path / "analytics.db")
    rc = reviews_main(["grant", "--db", db, "--login", "tech1", "--scope", SCOPE, "--types", "local_claim",
                      "--by", "operator"])
    assert rc == 1
    assert capsys.readouterr().err.strip() != ""


def test_cli_grant_after_bootstrap_requires_grantor_authority(tmp_path, capsys):
    db = str(tmp_path / "analytics.db")
    assert reviews_main(["bootstrap", "--db", db, "--login", "tech1", "--scope", SCOPE, "--types", "local_claim",
                         "--by", "operator"]) == 0
    capsys.readouterr()
    # operator сам полномочия не держит — назначать дальше через grant не может
    rc = reviews_main(["grant", "--db", db, "--login", "tech2", "--scope", SCOPE, "--types", "local_claim",
                      "--by", "operator"])
    assert rc == 1
    assert capsys.readouterr().err.strip() != ""
    capsys.readouterr()
    # tech1 уже держит полномочие в этой области — может назначить tech2
    assert reviews_main(["grant", "--db", db, "--login", "tech2", "--scope", SCOPE, "--types", "local_claim",
                         "--by", "tech1"]) == 0


def test_cli_missing_required_argument_is_usage_error(tmp_path, capsys):
    db = str(tmp_path / "analytics.db")
    rc = reviews_main(["grant", "--db", db, "--login", "tech1"])
    assert rc == 1
    assert "нужны аргументы" in capsys.readouterr().err


def test_cli_unknown_command_is_usage_error(capsys):
    rc = reviews_main(["bogus"])
    assert rc == 1
    assert capsys.readouterr().err.strip() != ""


def test_cli_revoke_without_prior_grant_fails(tmp_path, capsys):
    db = str(tmp_path / "analytics.db")
    rc = reviews_main(["revoke", "--db", db, "--login", "nobody", "--scope", SCOPE, "--by", "operator"])
    assert rc == 1


# === F3 (правка итогового ревью, 2026-10-14): срок полномочия, info_requested не терминален, второй =====
# === независимый согласующий ============================================================================

# --- срок полномочия (valid_until) ---

def test_bootstrap_and_grant_accept_valid_until_and_store_it(store):
    until = datetime.now(timezone.utc) + timedelta(days=1)
    granted = bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    assert granted.valid_until is None                    # без --until — без ограничения, как раньше
    grant = store.grant_authority(TECH2.login, SCOPE, ("conflict",), granted_by=TECH1, valid_until=until)
    assert grant.valid_until == until
    assert store.active_grant(TECH2.login, SCOPE, "conflict").valid_until == until


def test_expired_valid_until_without_revoke_hides_grant_like_a_stranger(store):
    """Истёкшее вчера полномочие, НЕ отозванное, — active_grant невидим, has_any_active_grant ложно, и
    decide() для такого держателя идёт по тому же пути, что для постороннего без права (ReviewStoreError, та
    же невидимость, что и у несуществующей заявки) — не PermissionError."""
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    bootstrap(store, TECH1.login, SCOPE, ("local_claim",))
    store.grant_authority(TECH2.login, SCOPE, ("local_claim",), granted_by=TECH1, valid_until=yesterday)
    assert store.active_grant(TECH2.login, SCOPE, "local_claim") is None
    assert store.has_any_active_grant(TECH2.login) is False

    r = store.create_request("local_claim", "CLAIM-FAKE-200", "новая формулировка", SCOPE, "основание",
                             created_by=STUDENT)
    with pytest.raises(ReviewStoreError):
        store.decide(r.id, "accepted", "", decided_by=TECH2)
    assert r.status == "pending"                           # заявка осталась нерешённой


def test_approved_claim_and_resolved_link_none_after_valid_until_expires_without_revoke(store):
    """Решение, принятое ДО истечения срока, остаётся historical accepted в журнале, но «новое применение»
    (approved_claim/resolved_link «здесь и сейчас») после истечения запрещено — так же, как после отзыва
    (test_approved_claim_none_after_authority_revoked/test_resolved_link_present_after_accept_and_none_after_
    authority_revoked — тот же эффект, другая причина)."""
    soon = datetime.now(timezone.utc) + timedelta(seconds=0.4)
    bootstrap(store, TECH1.login, SCOPE, ("local_norm", "equipment_link"))
    store.grant_authority(TECH2.login, SCOPE, ("local_norm", "equipment_link"), granted_by=TECH1,
                          valid_until=soon)
    claim = store.create_request("local_norm", "CLAIM-FAKE-201", "новый норматив", SCOPE, "основание",
                                 created_by=STUDENT)
    store.decide(claim.id, "accepted", "", decided_by=TECH2)
    link = store.create_request("equipment_link", "EQ-FAKE-201", "20FAKE_201_PV", SCOPE, "основание",
                                created_by=STUDENT)
    store.decide(link.id, "accepted", "", decided_by=TECH2)
    assert store.approved_claim("CLAIM-FAKE-201", SCOPE) is not None
    assert store.resolved_link("EQ-FAKE-201") is not None

    import time
    time.sleep(0.6)                                         # пересечь valid_until — срок истёк, не отозван
    assert store.approved_claim("CLAIM-FAKE-201", SCOPE) is None
    assert store.resolved_link("EQ-FAKE-201") is None
    # историческая запись решения цела — не переписана истечением срока
    raw = sqlite3.connect(store.path)
    try:
        row = raw.execute("SELECT status FROM review_requests WHERE id = ?", (claim.id,)).fetchone()
    finally:
        raw.close()
    assert row == ("accepted",)


def test_valid_until_migration_adds_column_to_legacy_db_without_data_loss(tmp_path):
    """База, созданная ДО F3 (review_authority_grants без valid_until) — open() добавляет столбец
    (ALTER TABLE ... ADD COLUMN), существующая строка полномочия остаётся читаемой и действующей (NULL —
    «без ограничения», то же поведение, что было до правки)."""
    path = str(tmp_path / "analytics.db")
    raw = sqlite3.connect(path)
    try:
        raw.execute("""CREATE TABLE review_authority_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT NOT NULL, scope TEXT NOT NULL,
            decision_types TEXT NOT NULL, granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT
        )""")
        raw.execute("INSERT INTO review_authority_grants (login, scope, decision_types, granted_by,"
                   " granted_at, revoked_at) VALUES ('tech1', ?, 'local_claim', 'operator', ?, NULL)",
                   (SCOPE, datetime.now(timezone.utc).isoformat()))
        raw.commit()
    finally:
        raw.close()
    store = ReviewStore(path)
    assert store.open() is True
    grant = store.active_grant("tech1", SCOPE, "local_claim")
    assert grant is not None and grant.valid_until is None       # старая строка цела, без ограничения срока
    # повторный open() на уже мигрированной базе — идемпотентен, не падает
    assert ReviewStore(path).open() is True


def test_decided_by_role_migration_adds_column_to_legacy_db_without_data_loss(tmp_path):                   # F5
    """База, заведённая ДО F5 (`review_requests` без `decided_by_role`, схема F3 целиком) — `open()` добавляет
    столбец (`ALTER TABLE ... ADD COLUMN`), существующая решённая строка остаётся читаемой; `NULL` трактуется
    как «не студент по обходному пути all_students» — `approved_claim(reviewers_mode="all_students")` для
    такой старой строки не считает её действующей без реального полномочия (тот же путь, что для teacher)."""
    path = str(tmp_path / "analytics.db")
    raw = sqlite3.connect(path)
    try:
        raw.execute("""CREATE TABLE review_authority_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT NOT NULL, scope TEXT NOT NULL,
            decision_types TEXT NOT NULL, granted_by TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT,
            valid_until TEXT
        )""")
        raw.execute("""CREATE TABLE review_requests (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject_id TEXT NOT NULL, old_value TEXT,
            new_value TEXT NOT NULL, scope TEXT NOT NULL, period_start TEXT, period_end TEXT,
            rationale TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            decided_by TEXT, decided_at TEXT, comment TEXT
        )""")
        now = datetime.now(timezone.utc).isoformat()
        raw.execute(
            "INSERT INTO review_requests (id, kind, subject_id, old_value, new_value, scope, period_start,"
            " period_end, rationale, status, created_by, created_at, decided_by, decided_at, comment) VALUES"
            " ('legacy-1', 'local_claim', 'CLAIM-FAKE-900', NULL, 'значение', ?, NULL, NULL, 'основание',"
            " 'accepted', 'stu-01', ?, 'stu-01', ?, NULL)", (SCOPE, now, now))
        raw.commit()
    finally:
        raw.close()
    store = ReviewStore(path)
    assert store.open() is True
    # «старая» строка без decided_by_role не считается студенческим обходным путём — approved_claim требует
    # настоящего полномочия, как для teacher, даже в режиме all_students (решение не было по этому пути)
    assert store.approved_claim("CLAIM-FAKE-900", SCOPE, reviewers_mode="all_students") is None
    # повторный open() на уже мигрированной базе — идемпотентен, не падает
    assert ReviewStore(path).open() is True


def test_cli_bootstrap_and_grant_accept_until_and_list_shows_expired(tmp_path, capsys):
    db = str(tmp_path / "analytics.db")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    assert reviews_main(["bootstrap", "--db", db, "--login", "tech1", "--scope", SCOPE, "--types",
                         "local_claim", "--by", "operator", "--until", "2099-01-01"]) == 0
    capsys.readouterr()
    assert reviews_main(["grant", "--db", db, "--login", "tech2", "--scope", SCOPE, "--types", "local_claim",
                        "--by", "tech1", "--until", yesterday]) == 0
    capsys.readouterr()
    out = reviews_main(["list", "--db", db]) == 0
    assert out
    printed = capsys.readouterr().out
    assert "tech1" in printed and "действует" in printed
    assert "tech2" in printed and "истекло" in printed


def test_cli_until_rejects_bad_format(tmp_path, capsys):
    db = str(tmp_path / "analytics.db")
    rc = reviews_main(["bootstrap", "--db", db, "--login", "tech1", "--scope", SCOPE, "--types", "local_claim",
                      "--by", "operator", "--until", "15/10/2026"])
    assert rc == 1
    assert capsys.readouterr().err.strip() != ""


# --- info_requested не терминален (журнал решений) ---

def test_decide_info_requested_is_not_terminal_and_repeatable(store):
    """info_requested не закрывает заявку: держатель права может позже вынести окончательное решение, и может
    повторить info_requested — каждое решение попадает в журнал review_decisions (мастер-промпт §6: «статусы и
    версии сохраняются»)."""
    bootstrap(store, TECH1.login, SCOPE, ("conflict",))
    r = store.create_request("conflict", "CONF-FAKE-210", "значение", SCOPE, "основание", created_by=STUDENT)
    once = store.decide(r.id, "info_requested", "нужны данные", decided_by=TECH1)
    assert once.status == "info_requested"
    twice = store.decide(r.id, "info_requested", "ещё не хватает", decided_by=TECH1)
    assert twice.status == "info_requested"
    final = store.decide(r.id, "accepted", "теперь достаточно", decided_by=TECH1)
    assert final.status == "accepted"
    # терминальное решение закрывает заявку — третья попытка решить снова — ValueError, как и раньше
    with pytest.raises(ValueError):
        store.decide(r.id, "rejected", "", decided_by=TECH1)
    raw = sqlite3.connect(store.path)
    try:
        rows = raw.execute(
            "SELECT decision, decided_by, comment FROM review_decisions WHERE request_id = ? ORDER BY id",
            (r.id,)).fetchall()
    finally:
        raw.close()
    assert rows == [("info_requested", "tech1", "нужны данные"), ("info_requested", "tech1", "ещё не хватает"),
                    ("accepted", "tech1", "теперь достаточно")]


# --- второй независимый согласующий (local_claim/local_norm) ---

@pytest.mark.parametrize("kind", ["local_claim", "local_norm"])
def test_decide_accepted_after_own_info_requested_needs_second_approver(store, kind):
    """X выносит info_requested, затем сам пытается accepted по той же заявке local_claim/local_norm — 403
    «нужен второй независимый согласующий»; другой держатель права (Y) — 200; автор — 403 как раньше."""
    bootstrap(store, TECH1.login, SCOPE, (kind,))
    store.grant_authority(TECH2.login, SCOPE, (kind,), granted_by=TECH1)
    r = store.create_request(kind, "CLAIM-FAKE-220", "новое значение", SCOPE, "основание", created_by=STUDENT)

    store.decide(r.id, "info_requested", "нужны данные", decided_by=TECH1)
    with pytest.raises(PermissionError, match="второй независимый согласующий"):
        store.decide(r.id, "accepted", "теперь ладно", decided_by=TECH1)

    decided = store.decide(r.id, "accepted", "подтверждаю", decided_by=TECH2)
    assert (decided.status, decided.decided_by) == ("accepted", TECH2.login)


@pytest.mark.parametrize("kind", ["local_claim", "local_norm"])
def test_decide_second_approver_check_does_not_block_author_check_or_strangers(store, kind):
    """Автор заявки получает прежний отказ (самозапрет), посторонний — прежнюю невидимость; проверка второго
    согласующего не меняет эти два пути."""
    bootstrap(store, TECH1.login, SCOPE, (kind,))
    r = store.create_request(kind, "CLAIM-FAKE-221", "новое значение", SCOPE, "основание", created_by=STUDENT)
    store.grant_authority(STUDENT.login, SCOPE, (kind,), granted_by=TECH1)
    with pytest.raises(PermissionError, match="автор заявки"):
        store.decide(r.id, "accepted", "", decided_by=STUDENT)
    stranger = ident("tech9")
    with pytest.raises(ReviewStoreError):
        store.decide(r.id, "accepted", "", decided_by=stranger)


@pytest.mark.parametrize("kind", ["conflict", "equipment_link"])
def test_decide_second_approver_not_required_for_conflict_and_equipment_link(store, kind):
    """Для conflict/equipment_link одного правообладателя достаточно — тот же логин, что оставил
    info_requested, может сам вынести accepted по этой же заявке."""
    bootstrap(store, TECH1.login, SCOPE, (kind,))
    new_value = "20FAKE_230_PV" if kind == "equipment_link" else "значение"
    r = store.create_request(kind, "SUBJ-FAKE-230", new_value, SCOPE, "основание", created_by=STUDENT)
    store.decide(r.id, "info_requested", "уточняю", decided_by=TECH1)
    decided = store.decide(r.id, "accepted", "теперь принято", decided_by=TECH1)
    assert (decided.status, decided.decided_by) == ("accepted", TECH1.login)


# === F5 (итоговое ревью, 2026-10-14, решение владельца 14.10.2026): ANALYTICS_REVIEWERS=all_students ========
# === — «любому студенту без утверждения со стороны преподавателя» / «любой утверждает без дополнительных ===
# === утверждений» ==============================================================================================

OTHER_STUDENT = ident("stu-09", role="student", place=9)


@pytest.mark.parametrize("kind", ["local_claim", "equipment_link", "local_norm", "conflict"])
def test_decide_all_students_mode_student_without_any_grant_decides_any_kind_and_scope(store, kind):
    """Любой вошедший student БЕЗ какого-либо назначенного полномочия решает заявку ЛЮБОГО вида в ЛЮБОЙ
    области — всех четырёх видов решения (F5-brief, п.1), не только там, где у кого-то есть право."""
    new_value = "20FAKE_300_PV" if kind == "equipment_link" else "новое значение"
    r = store.create_request(kind, "SUBJ-FAKE-300", new_value, SCOPE, "основание", created_by=OTHER_STUDENT)
    decided = store.decide(r.id, "accepted", "подтверждаю", decided_by=STUDENT, reviewers_mode="all_students")
    assert (decided.status, decided.decided_by) == ("accepted", STUDENT.login)


def test_decide_all_students_mode_author_student_decides_own_request(store):
    """«Любой утверждает без дополнительных утверждений, включая свою» — самозапрет автора НЕ действует в
    all_students для student (отличие от grants, где это PermissionError — см.
    test_decide_forbids_author_deciding_own_request_even_with_authority)."""
    r = store.create_request("conflict", "CONF-FAKE-300", "значение", SCOPE, "основание", created_by=STUDENT)
    decided = store.decide(r.id, "accepted", "", decided_by=STUDENT, reviewers_mode="all_students")
    assert (decided.status, decided.decided_by) == ("accepted", STUDENT.login)


@pytest.mark.parametrize("kind", ["local_claim", "local_norm"])
def test_decide_all_students_mode_second_approver_not_required(store, kind):
    """Второй независимый согласующий (F3) НЕ требуется в all_students: тот же student, что оставил
    info_requested, сам потом выносит accepted по local_claim/local_norm — F3 запрещал это в grants."""
    r = store.create_request(kind, "CLAIM-FAKE-300", "новое значение", SCOPE, "основание",
                             created_by=OTHER_STUDENT)
    store.decide(r.id, "info_requested", "уточняю", decided_by=STUDENT, reviewers_mode="all_students")
    decided = store.decide(r.id, "accepted", "теперь принято", decided_by=STUDENT, reviewers_mode="all_students")
    assert (decided.status, decided.decided_by) == ("accepted", STUDENT.login)


def test_decide_all_students_mode_still_rejects_second_decision_on_terminal_request(store):
    """«Нет переголосования» НЕ входит в список снятых правил — терминальный статус остаётся ValueError даже
    в all_students."""
    r = store.create_request("conflict", "CONF-FAKE-301", "значение", SCOPE, "основание", created_by=STUDENT)
    store.decide(r.id, "accepted", "", decided_by=OTHER_STUDENT, reviewers_mode="all_students")
    with pytest.raises(ValueError):
        store.decide(r.id, "rejected", "", decided_by=STUDENT, reviewers_mode="all_students")


def test_decide_all_students_mode_teacher_without_designation_still_has_no_authority(store):
    """F5-brief, п.1: teacher — исключение из all_students, не «любой вошедший» — без действующего
    полномочия получает ту же невидимость, что в grants (ReviewStoreError, не PermissionError)."""
    r = store.create_request("conflict", "CONF-FAKE-302", "значение", SCOPE, "основание", created_by=STUDENT)
    with pytest.raises(ReviewStoreError):
        store.decide(r.id, "accepted", "", decided_by=TECH1, reviewers_mode="all_students")
    # тот же teacher ПОСЛЕ назначения решает как обычно, даже в режиме all_students
    bootstrap(store, TECH1.login, SCOPE, ("conflict",))
    decided = store.decide(r.id, "accepted", "", decided_by=TECH1, reviewers_mode="all_students")
    assert decided.status == "accepted"


def test_list_requests_all_students_mode_student_sees_everything_grants_mode_sees_nothing(store):
    """Видимость (F5-brief, п.2): в all_students любой student видит ВСЕ заявки области КДМ-1, включая не
    свои и там, где ни у кого нет назначенного права; в grants — прежнее поведение (store.list_requests без
    reviewers_mode == умолчание "grants" — ничего не видит, кроме своих)."""
    r1 = store.create_request("local_claim", "CLAIM-FAKE-310", "значение", SCOPE, "основание",
                              created_by=OTHER_STUDENT)
    r2 = store.create_request("conflict", "CONF-FAKE-310", "значение", "PROC-FAKE-777", "основание",
                              created_by=OTHER_STUDENT)
    assert store.list_requests(STUDENT) == []                                         # grants (умолчание)
    visible_ids = {r.id for r in store.list_requests(STUDENT, reviewers_mode="all_students")}
    assert visible_ids == {r1.id, r2.id}


def test_list_requests_all_students_mode_does_not_change_teacher_visibility(store):
    """Видимость teacher в all_students — не шире, чем в grants (только свои заявки и где есть полномочие);
    режим `all_students` расширяет видимость только для `student`."""
    store.create_request("conflict", "CONF-FAKE-311", "значение", SCOPE, "основание", created_by=OTHER_STUDENT)
    assert store.list_requests(TECH1, reviewers_mode="all_students") == []


def test_approved_claim_all_students_mode_student_without_grant_stops_applying_after_mode_switch_to_grants(store):
    """F5-brief, п.3: решение студента без назначения (all_students) действует для approved_claim ТОЛЬКО пока
    режим включён — переключение на grants обесценивает его для НОВОГО применения, журнал (review_requests.
    status) цел."""
    r = store.create_request("local_claim", "CLAIM-FAKE-320", "критический местный факт", SCOPE, "основание",
                             created_by=OTHER_STUDENT)
    store.decide(r.id, "accepted", "подтверждено", decided_by=STUDENT, reviewers_mode="all_students")

    approved = store.approved_claim("CLAIM-FAKE-320", SCOPE, reviewers_mode="all_students")
    assert approved is not None and approved.id == r.id

    # переключение режима — тот же вызов store.approved_claim, но с reviewers_mode="grants": у STUDENT нет
    # реального полномочия, решение перестаёт действовать для нового применения
    assert store.approved_claim("CLAIM-FAKE-320", SCOPE, reviewers_mode="grants") is None
    # история цела — заявка осталась accepted, не переписана переключением режима
    raw = sqlite3.connect(store.path)
    try:
        row = raw.execute("SELECT status FROM review_requests WHERE id = ?", (r.id,)).fetchone()
    finally:
        raw.close()
    assert row == ("accepted",)


def test_approved_claim_all_students_mode_decision_by_teacher_still_requires_real_grant(store):
    """Решения teacher не затрагиваются обходным путём all_students для approved_claim — для них
    перепроверяется реальный active_grant, как в grants, даже когда reviewers_mode="all_students"."""
    bootstrap(store, TECH1.login, SCOPE, ("local_norm",))
    r = store.create_request("local_norm", "CLAIM-FAKE-321", "новый норматив", SCOPE, "основание",
                             created_by=STUDENT)
    store.decide(r.id, "accepted", "", decided_by=TECH1, reviewers_mode="all_students")
    assert store.approved_claim("CLAIM-FAKE-321", SCOPE, reviewers_mode="all_students") is not None
    store.revoke_authority(TECH1.login, SCOPE, revoked_by=OPERATOR)
    assert store.approved_claim("CLAIM-FAKE-321", SCOPE, reviewers_mode="all_students") is None


def test_resolved_link_all_students_mode_student_without_grant_stops_applying_after_mode_switch_to_grants(store):
    """Симметрично approved_claim (F5-brief, п.3) — resolved_link студенческого решения без назначения."""
    r = store.create_request("equipment_link", "EQ-FAKE-320", "20FAKE_320_PV", SCOPE,
                             "подпись совпадает с руководством", created_by=OTHER_STUDENT)
    store.decide(r.id, "accepted", "", decided_by=STUDENT, reviewers_mode="all_students")

    link = store.resolved_link("EQ-FAKE-320", reviewers_mode="all_students")
    assert link is not None and link.historian_tag == "20FAKE_320_PV"
    assert store.resolved_link("EQ-FAKE-320", reviewers_mode="grants") is None
