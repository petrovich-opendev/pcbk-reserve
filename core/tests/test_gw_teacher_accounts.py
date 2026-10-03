# core/tests/test_gw_teacher_accounts.py — Д10-R108: учётки студентов в кабинете преподавателя. Фикстура tg — как в
# test_gw_teacher.py; пароли — Secret (m-14): в сообщении упавшего assert пароль печатается как «<скрыт>»
import json
import logging
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from helpers import BASE, FRONT, ORIGIN, PW, Gw, Secret, park_reload_worker, teacher_gateway
from pcbk_core.gateway.accounts import LOCK_FAILURES
from pcbk_core.gateway.api import GW_MESSAGES
from pcbk_core.gateway.teacher_api import TEACHER_PREFIX as P, TEACHER_TEXTS


@pytest.fixture
def tg(tmp_path):
    app, role = teacher_gateway(tmp_path, ready=(3,))                   # место 3 готово, место 5 спит
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT):
        yield Gw(app, role)


def post(c, path, body, origin=ORIGIN):
    return c.post(P + path, json=body, headers={"Origin": origin})


def test_accounts_only_students(tg):                                     # Review Focus 1
    t = tg.as_("teach")
    r = post(t, "accounts", {"login": "d10-probe-n", "place": "04"})
    pw = Secret(r.json()["password"])
    assert r.status_code == 201 and (r.json()["login"], r.json()["place"]) == ("d10-probe-n", "04")
    assert r.headers["cache-control"] == "no-store" and len(pw) >= 12
    assert tg.role.store.verify("d10-probe-n", pw).place == 4
    for path in ("accounts/teach/disable", "accounts/teach/password", "accounts/nobody/enable"):
        assert post(t, path, {}).status_code == 404, path
    assert post(t, "accounts", {"login": "t2", "place": "04", "role": "teacher"}).status_code == 400   # лишний ключ


def test_accounts_store_rules_are_kept(tg):
    t = tg.as_("teach")
    r = post(t, "accounts", {"login": "other", "place": "03"})                # место 3 у stu-03
    assert r.status_code == 400 and "место 03" in r.json()["data"]["message"]
    post(tg.as_("teach"), "accounts/stu-05/disable", {})
    r = post(t, "accounts", {"login": "stu-05b", "place": "05"})            # закреплено за stu-05
    assert r.status_code == 400 and "закреплено" in r.json()["data"]["message"]


def test_disable_closes_sessions_and_enable_rules(tg):                    # Review Focus 2
    s5 = tg.as_("stu-05")
    t = tg.as_("teach")
    assert post(t, "accounts/stu-05/disable", {}).status_code == 204
    assert s5.get("/pcbk/api/auth/me").status_code == 401
    assert post(t, "accounts/stu-05/enable", {}).status_code == 204
    assert post(t, "accounts/stu-05/enable", {}).status_code == 400         # уже включена


def test_password_once_no_store_no_log(tg, caplog):
    r = post(tg.as_("teach"), "accounts/stu-03/password", {})
    pw = Secret(r.json()["password"])
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert pw not in caplog.text
    assert all(pw not in json.dumps(row.detail or {}) for row in tg.role.teacher_log.recent(10))
    assert tg.role.store.verify("stu-03", PW) is None and tg.role.store.verify("stu-03", pw).place == 3


def test_accounts_journal_started_then_finish(tg):
    post(tg.as_("teach"), "accounts", {"login": "d10-probe-n", "place": "04"})
    row = tg.role.teacher_log.recent(1)[0]
    assert (row.action, row.place, row.target, row.outcome) == ("account_add", 4, "d10-probe-n", "ok")


def test_student_refused_on_accounts(tg):
    assert post(tg.as_("stu-03"), "accounts", {"login": "x-new", "place": "04"}).status_code == 403
    assert not tg.role.store.exists("x-new")


# --- сверх брифа ---

def test_not_teacher_refused_on_every_account_route(tg):                  # доводка р1, ревью I-1; Review Focus 1
    """Роль — на сервере у каждой из пяти ручек учёток: студент и учётка чужой роли (правка базы мимо CHECK, как в
    test_gw_calc) — 403 teacher_only, без сессии — 401; учётки не меняются, строк журнала действий нет. Без проверки роли
    в account_op студент получил бы 200 и пароль чужой учётки."""
    store = tg.role.store
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("PRAGMA ignore_check_constraints = ON")
        db.execute("INSERT INTO gw_accounts (login, role, place, pw_hash, disabled, created_at) SELECT 'oper',"
                   " 'operator', 8, pw_hash, 0, created_at FROM gw_accounts WHERE login = 'stu-05'")
    store.add("stu-06", "student", 6, PW)
    store.disable("stu-06")                                                   # цель enable — отключённая учётка
    for _ in range(LOCK_FAILURES):
        store.failure("stu-05")                                               # цель unlock — запертая учётка
    calls = (("accounts", {"login": "x-new", "place": "04"}), ("accounts/stu-05/password", {}),
             ("accounts/stu-05/disable", {}), ("accounts/stu-06/enable", {}), ("accounts/stu-05/unlock", {}),
             ("accounts/teach/password", {}))
    rows = len(tg.role.teacher_log.recent(100))
    for login in ("stu-03", "oper"):
        c = tg.as_(login)
        for path, body in calls:
            r = post(c, path, body)
            assert (r.status_code, r.json()["data"]["message"]) == (403, TEACHER_TEXTS["teacher_only"]), (login, path)
            assert "password" not in r.json(), (login, path)
    for path, body in calls:
        assert post(tg.anon(), path, body).status_code == 401, path
    assert len(tg.role.teacher_log.recent(100)) == rows
    assert tg.role.teacher_stats.count_1h("role_refused") == 2 * len(calls)
    assert store.verify("stu-05", PW) is not None and store.status("stu-05") == "active" and store.locked("stu-05")
    assert store.status("stu-06") == "disabled" and store.verify("teach", PW) is not None
    assert not store.exists("x-new")
    assert tg.role.teacher_stats.count_1h("change") == 0


def test_password_answers_say_no_cache_and_carry_only_password(tg):     # R108: заголовки и форма ответов
    t = tg.as_("teach")
    r = post(t, "accounts", {"login": "d10-probe-n", "place": "04"})
    assert (r.headers["cache-control"], r.headers["pragma"]) == ("no-store", "no-cache")
    assert set(r.json()) == {"login", "place", "password"}
    r = post(t, "accounts/stu-03/password", {})
    assert (r.headers["cache-control"], r.headers["pragma"]) == ("no-store", "no-cache")
    assert set(r.json()) == {"password"} and len(Secret(r.json()["password"])) == 12
    for kind in ("disable", "enable", "unlock"):                             # 204 — без тела
        if kind == "enable":
            assert tg.role.store.status("stu-05") == "disabled"
        r = post(t, f"accounts/stu-05/{kind}", {})
        assert (r.status_code, r.content) == (204, b""), kind
    assert tg.role.store.status("stu-05") == "active"


def test_new_password_closes_sessions(tg):                               # R108: passwd — set_password
    s3 = tg.as_("stu-03")
    assert s3.get("/pcbk/api/auth/me").status_code == 200
    assert post(tg.as_("teach"), "accounts/stu-03/password", {}).status_code == 200
    assert s3.get("/pcbk/api/auth/me").status_code == 401


def test_unlock_lifts_lock_of_student(tg):                               # R108: unlock — AccountStore.unlock
    store = tg.role.store
    for _ in range(LOCK_FAILURES):
        store.failure("stu-05")
    assert store.locked("stu-05")
    assert post(tg.as_("teach"), "accounts/stu-05/unlock", {}).status_code == 204
    assert not store.locked("stu-05")
    row = tg.role.teacher_log.recent(1)[0]
    assert (row.action, row.place, row.target, row.outcome) == ("account_unlock", 5, "stu-05", "ok")


def test_teacher_accounts_untouched_and_not_named(tg):                  # R108: преподаватель — как нет учётки
    t = tg.as_("teach")
    tg.role.store.add("teach2", "teacher", None, PW)
    tg.role.store.disable("teach2")
    before = len(tg.role.teacher_log.recent(100))
    for kind in ("password", "disable", "enable", "unlock"):
        for login in ("teach", "teach2", "nobody"):
            r = post(t, f"accounts/{login}/{kind}", {})
            assert (r.status_code, r.json()["data"]["message"]) == (404, TEACHER_TEXTS["no_account"]), (kind, login)
    assert len(tg.role.teacher_log.recent(100)) == before                     # отказ до журнала: строк нет
    assert tg.role.store.status("teach2") == "disabled" and tg.role.store.verify("teach", PW) is not None
    assert TEACHER_TEXTS["no_account"] == "Учётки студента с таким логином нет"


def test_same_person_never_from_cabinet(tg):                              # R108: возврат логина — только администратор
    store = tg.role.store
    store.disable("stu-05")
    store.remove("stu-05")                                                    # надгробие: логин stu-05 за местом 05
    r = post(tg.as_("teach"), "accounts", {"login": "stu-05", "place": "05"})
    assert r.status_code == 400 and "новому человеку нужен новый логин" in r.json()["data"]["message"]
    assert not store.exists("stu-05")
    row = tg.role.teacher_log.recent(1)[0]
    assert (row.action, row.place, row.target, row.outcome) == ("account_add", 5, "stu-05", "refused")


def test_account_bodies_and_paths(tg):                                    # тело — вручную, путь — по LOGIN_RE
    t = tg.as_("teach")
    before = len(tg.role.teacher_log.recent(100))
    bad_add = ({"login": "x-new"}, {"place": "04"}, {"login": "x-new", "place": 4}, {"login": "x-new", "place": "4"},
               {"login": "x-new", "place": "11"}, {"login": 7, "place": "04"}, [], "x", None,
               {"login": "x-new", "place": "04", "same_person": True})
    for body in bad_add:
        r = post(t, "accounts", body)
        assert (r.status_code, r.json()["data"]["message"]) == (400, GW_MESSAGES["bad_body"]), body
    raw = t.post(P + "accounts", content=b'{"login": "x-new", "login": "y-new", "place": "04"}',
                 headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    assert raw.status_code == 400                                             # повтор ключа
    r = post(t, "accounts", {"login": "Bad Login", "place": "04"})          # вид логина — текст правила, без логина
    assert r.status_code == 400 and "логин: 3…32 знака" in r.json()["data"]["message"]
    assert "Bad Login" not in r.text
    for body in ({"x": 1}, [], "x"):
        r = post(t, "accounts/stu-05/disable", body)
        assert (r.status_code, r.json()["data"]["message"]) == (400, GW_MESSAGES["bad_body"]), body
    assert t.post(P + "accounts/stu-05/disable", headers={"Origin": ORIGIN}).status_code == 204   # пустое тело — как {}
    for path in ("accounts/Stu-05/disable", "accounts/s/disable", "accounts/stu-05/remove", "accounts/stu-05",
                 "accounts/stu-05/disable/x"):
        assert post(t, path, {}).status_code == 404, path
    assert len(tg.role.teacher_log.recent(100)) == before + 1                 # строка — только у удачного disable
    assert not tg.role.store.exists("x-new")


def test_origin_before_everything(tg):                                    # R100, шаг 5: чужой источник — 403
    t = tg.as_("teach")
    for path, body in (("accounts", {"login": "x-new", "place": "04"}), ("accounts/stu-05/disable", {}),
                       ("accounts/stu-05/password", {}), ("accounts/teach/enable", {}), ("accounts/Bad/unlock", {})):
        assert post(t, path, body, origin="https://evil.test").status_code == 403, path
    assert not tg.role.store.exists("x-new") and tg.role.store.status("stu-05") == "active"
    assert tg.role.teacher_log.recent(100) == []


def test_journal_down_no_action(tmp_path):                                # R108: нет started — 503 journal, действия нет
    app, role = teacher_gateway(tmp_path, ready=(3,), DRILL_TEACHER="journal_down")
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT):
        t = Gw(app, role).as_("teach")
        r = post(t, "accounts", {"login": "x-new", "place": "04"})
        assert (r.status_code, r.json()["data"]["message"]) == (503, TEACHER_TEXTS["journal"])
        assert "password" not in r.json()
        r = post(t, "accounts/stu-03/password", {})
        assert (r.status_code, r.json()["data"]["message"]) == (503, TEACHER_TEXTS["journal"])
        assert post(t, "accounts/stu-05/disable", {}).status_code == 503
        assert not role.store.exists("x-new") and role.store.status("stu-05") == "active"
        assert role.store.verify("stu-03", PW) is not None                   # пароль прежний
        assert role.teacher_stats.count_1h("change_failed") == 3 and role.teacher_stats.count_1h("change") == 0


def test_finish_failure_keeps_done_change(tg, monkeypatch, caplog):       # действие прошло, итог не записан
    from pcbk_core.teacher_log import TeacherLogError

    caplog.set_level(logging.DEBUG)

    def broken(*a, **k):
        raise TeacherLogError("журнал действий: OperationalError")
    monkeypatch.setattr(tg.role.teacher_log, "finish", broken)
    r = post(tg.as_("teach"), "accounts", {"login": "d10-probe-n", "place": "04"})
    pw = Secret(r.json()["password"])
    assert r.status_code == 201 and tg.role.store.verify("d10-probe-n", pw).place == 4   # пароль не потерян
    assert tg.role.teacher_log.recent(1)[0].outcome == "started"                 # след попытки остался
    assert (tg.role.teacher_stats.count_1h("change"), tg.role.teacher_stats.count_1h("change_failed")) == (1, 1)
    assert "finish=failed" in caplog.text and pw not in caplog.text


def test_store_error_is_503_and_error_row(tg, monkeypatch):               # сбой SQLite — change_failed, итог error
    from pcbk_core.gateway.accounts import StoreError

    def broken(*a, **k):
        raise StoreError("учётные записи: OperationalError")
    monkeypatch.setattr(tg.role.store, "disable", broken)
    r = post(tg.as_("teach"), "accounts/stu-05/disable", {})
    assert (r.status_code, r.json()["data"]["message"]) == (503, GW_MESSAGES["store"])
    row = tg.role.teacher_log.recent(1)[0]
    assert (row.action, row.target, row.outcome) == ("account_disable", "stu-05", "error")
    assert tg.role.teacher_stats.count_1h("change_failed") == 1


def test_counters_and_service_lines(tg, caplog):                          # R108: удача — change, отказ — без счётчика
    caplog.set_level(logging.DEBUG)                                           # все строки, и httpx
    t = tg.as_("teach")
    r = post(t, "accounts", {"login": "d10-probe-n", "place": "04"})
    pw = Secret(r.json()["password"])
    assert post(t, "accounts", {"login": "other", "place": "03"}).status_code == 400
    assert post(t, "accounts/stu-05/disable", {}).status_code == 204
    stats = tg.role.teacher_stats
    assert (stats.count_1h("change"), stats.count_1h("change_failed")) == (2, 0)
    lines = [m for m in caplog.messages if m.startswith("gw teacher login=teach action=account_")]
    assert len(lines) == 3 and pw not in caplog.text
    assert "action=account_add place=04 outcome=ok status=201" in lines[0] and lines[0].endswith("target=d10-probe-n")
    assert "outcome=refused status=400" in lines[1] and lines[1].endswith("target=other")
    assert "action=account_disable place=05 outcome=ok status=204" in lines[2]
    rows = tg.role.teacher_log.recent(3)
    assert [(r.action, r.place, r.target, r.outcome) for r in rows] == [
        ("account_disable", 5, "stu-05", "ok"), ("account_add", 3, "other", "refused"),
        ("account_add", 4, "d10-probe-n", "ok")]
