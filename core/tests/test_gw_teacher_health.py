"""Наблюдение раньше функции (задача А1 Д10а, Д10-R96): счётчики кабинета TeacherStats за час, GET /health/teacher — ровно
13 ключей, только числа и флаги, только зона front, отказы — обработчиком шлюза из реестра; DRILL_TEACHER — в drill. До А2
долей и журнала действий нет: checked_at — null, shares_loaded и shares_ok — false, сумма — 0, store_ok — true, и строка
сторожа честно «неизвестно».

Задача А2: поля долей и журнала — из Shares роли llm и TeacherLog шлюза; teacher_tick — сверка долей с теми, по которым
отказывает прокси (budget.share_of, I-7), и пульс журнала действий — в такте уборщика."""
import sqlite3
import threading
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from helpers import (FRONT, PLACE_NET, FakeMono, Gw, gateway_role, make_gateway, make_llm_role, now_utc,
                     teacher_gateway, wait_until)
from pcbk_core.gateway.stats import TEACHER_KINDS, TeacherStats
from pcbk_core.gateway.teacher_api import HEALTH_TEACHER_PATH, TEACHER_PREFIX
from pcbk_core.hourly import WINDOW_S
from pcbk_core.llm.shares import Shares
from pcbk_core.settings import LlmSettings

KEYS = {"checked_at", "shares_loaded", "shares_ok", "shares_drift", "shares_sum_usd", "total_usd", "store_ok",
        "reads_1h", "read_failed_1h", "changes_1h", "change_failed_1h", "role_refused_1h", "drill"}


def test_teacher_health_contract_before_shares(tmp_path):
    app, role = make_gateway(tmp_path)                                    # без LLM-прокси: долей ещё нет
    with TestClient(app, client=FRONT) as c:
        role.teacher_stats.event("read")
        role.teacher_stats.event("role_refused")
        body = c.get("/health/teacher").json()
    assert set(body) == KEYS
    assert (body["checked_at"], body["shares_loaded"], body["shares_ok"], body["shares_drift"], body["store_ok"],
            body["reads_1h"], body["role_refused_1h"], body["drill"]) == (None, False, False, 0, True, 1, 1, "")
    assert body["total_usd"] == 1000.0                                    # LlmSettings().LLM_TOTAL_USD


def test_teacher_health_front_only(tmp_path):
    app, _ = make_gateway(tmp_path)
    with TestClient(app, client=PLACE_NET) as c:                           # сеть места
        r = c.get("/health/teacher")
    assert (r.status_code, r.json()["name"]) == (404, "NotFound")


def test_teacher_health_drill(tmp_path):
    app, _ = make_gateway(tmp_path, DRILL_TEACHER="journal_down")          # поле GatewaySettings (m-7)
    with TestClient(app, client=FRONT) as c:
        assert c.get("/health/teacher").json()["drill"] == "journal_down"


# --- сверх брифа ---

def test_teacher_kinds_and_path():
    assert TEACHER_KINDS == {"read", "read_failed", "change", "change_failed", "role_refused"}
    assert HEALTH_TEACHER_PATH == "/health/teacher"
    assert len(KEYS) == 13
    counted = {"reads_1h": "read", "read_failed_1h": "read_failed", "changes_1h": "change",
               "change_failed_1h": "change_failed", "role_refused_1h": "role_refused"}
    assert set(counted) == {k for k in KEYS if k.endswith("_1h")} and set(counted.values()) == TEACHER_KINDS


def test_teacher_health_counts_each_kind_to_its_key(tmp_path):
    app, role = make_gateway(tmp_path)
    keys = {"read": "reads_1h", "read_failed": "read_failed_1h", "change": "changes_1h",
            "change_failed": "change_failed_1h", "role_refused": "role_refused_1h"}
    with TestClient(app, client=FRONT) as c:
        for i, kind in enumerate(sorted(TEACHER_KINDS), start=1):        # у каждого вида своё число
            for _ in range(i):
                role.teacher_stats.event(kind)
        body = c.get("/health/teacher").json()
    assert {kind: body[keys[kind]] for kind in TEACHER_KINDS} == \
           {kind: i for i, kind in enumerate(sorted(TEACHER_KINDS), start=1)}


def test_teacher_health_without_shares_is_honest_unknown(tmp_path):       # R96: без долей — «неизвестно», не «норма»
    app, role = make_gateway(tmp_path)
    assert role.llm is None                                               # шлюз без LLM-прокси (до А2 — всегда)
    with TestClient(app, client=FRONT) as c:
        body = c.get("/health/teacher").json()
    assert body == {"checked_at": None, "shares_loaded": False, "shares_ok": False, "shares_drift": 0,
                    "shares_sum_usd": 0, "total_usd": LlmSettings().LLM_TOTAL_USD, "store_ok": True, "reads_1h": 0,
                    "read_failed_1h": 0, "changes_1h": 0, "change_failed_1h": 0, "role_refused_1h": 0, "drill": ""}


def test_teacher_health_total_from_llm_role(tmp_path):                    # R96: LLM_TOTAL_USD роли llm, если она есть
    app, role = make_gateway(tmp_path / "gw")
    role.llm = make_llm_role(tmp_path / "llm", LLM_TOTAL_USD=250.0)
    with TestClient(app, client=FRONT) as c:
        assert c.get("/health/teacher").json()["total_usd"] == 250.0


def test_teacher_health_readable_with_config_error(tmp_path):             # как /health/agents: без configured
    app, role = make_gateway(tmp_path, config_error="GW_ORIGIN не задан")
    with TestClient(app, client=FRONT) as c:
        role.teacher_stats.event("role_refused")
        r = c.get("/health/teacher")
    assert (r.status_code, r.json()["role_refused_1h"], r.json()["checked_at"]) == (200, 1, None)


def test_teacher_health_other_methods_404_without_allow(tmp_path):        # отказ — через реестр, вид OpenCode
    app, _ = make_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        assert c.get("/health/teacher").status_code == 200                   # положительный контроль
        for method in ("POST", "PUT", "DELETE", "PROPFIND"):
            r = c.request(method, "/health/teacher")
            assert (r.status_code, r.json()) == (404, {"name": "NotFound", "data": {"message": "Не найдено"}}), method
            assert "allow" not in r.headers, method
    with TestClient(app, base_url="http://172.31.250.82:8000", client=("172.31.250.81", 40000)) as host:
        r = host.get("/health/teacher")                                      # pcbk-egress — не front
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")


def test_teacher_health_has_no_logins_or_places(tmp_path):                # только числа и флаги
    app, role = make_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        for kind in TEACHER_KINDS:
            role.teacher_stats.event(kind)
        body = c.get("/health/teacher").json()
    assert all(v is None or isinstance(v, (bool, int, float, str)) for v in body.values())
    assert not [k for k in body if "place" in k or "login" in k or "name" in k]
    assert [k for k, v in body.items() if isinstance(v, str)] == ["drill"]


def test_teacher_stats_hour_window_and_unknown_kind():
    mono = FakeMono(100.0)
    s = TeacherStats(monotonic=mono)
    s.event("read"); s.event("read"); mono.advance(10); s.event("change")
    assert (s.count_1h("read"), s.count_1h("change"), s.count_1h("role_refused")) == (2, 1, 0)
    mono.advance(WINDOW_S - 10)                                              # первые вышли из окна
    assert (s.count_1h("read"), s.count_1h("change")) == (0, 1)
    for bad in ("wake_ok", "saved", "", "READ", "reads"):                    # опечатка — не молчаливый ноль
        with pytest.raises(ValueError):
            s.event(bad)
        with pytest.raises(ValueError):
            s.count_1h(bad)


def test_role_teacher_stats_own_and_on_role_clock(tmp_path):
    a, b = gateway_role(tmp_path / "a"), gateway_role(tmp_path / "b")
    assert isinstance(a.teacher_stats, TeacherStats) and a.teacher_stats is not b.teacher_stats
    a.teacher_stats.event("read")
    a.mono.advance(WINDOW_S)                                                 # окно — по монотонным часам роли
    assert a.teacher_stats.count_1h("read") == 0


def test_drill_teacher_warning_in_gateway_lifespan(tmp_path, gwlog):      # R96: строка учений — в lifespan шлюза
    app, _ = make_gateway(tmp_path / "off")                                  # отрицательный контроль: без учения
    with TestClient(app, client=FRONT):
        pass
    assert "DRILL_TEACHER" not in gwlog.text
    app, _ = make_gateway(tmp_path / "on", DRILL_TEACHER="shares_stale")
    with TestClient(app, client=FRONT):
        pass
    assert "УЧЕНИЯ: DRILL_TEACHER=shares_stale" in gwlog.text


# --- задача А2: сверка в такте уборщика, учение shares_stale в /health/teacher (m-1) ---

def test_teacher_tick_verifies_shares_and_pings_journal(tmp_path):
    app, role = teacher_gateway(tmp_path, DRILL_TEACHER="shares_stale")
    assert role.llm.shares.drill == "shares_stale"                          # m-1: учение дошло до долей помощника
    with TestClient(app, client=FRONT) as c:
        role.teacher_tick()
        assert c.get("/health/teacher").json()["shares_drift"] == 0
        role.llm.shares.apply([(3, 10000, 9000), (5, 10000, 11000)], by="teach", now=now_utc())
        role.teacher_tick()
        body = c.get("/health/teacher").json()
    assert (body["shares_loaded"], body["shares_ok"], body["shares_drift"], body["store_ok"],
            body["shares_sum_usd"]) == (True, True, 2, True, 1100.0)       # Д15б: 11 × $100 умолчания
    assert body["checked_at"] is not None


def test_teacher_tick_sees_budget_without_shares(tmp_path):              # I-7: сверка сквозная
    app, role = teacher_gateway(tmp_path)
    role.llm.shares.apply([(3, 10000, 9000), (5, 10000, 11000)], by="teach", now=now_utc())
    role.llm.budget.shares = None                                            # Budget, собранный мимо долей
    with TestClient(app, client=FRONT) as c:
        role.teacher_tick()
        assert c.get("/health/teacher").json()["shares_drift"] == 2


def test_teacher_tick_when_shares_not_loaded(tmp_path, monkeypatch):     # m-5 р3: fail, а не unknown
    monkeypatch.setattr(Shares, "open", lambda self: False)
    app, role = teacher_gateway(tmp_path)
    assert role.llm.shares.loaded is False
    assert role.llm.shares.cents(3) == 10000                                 # умолчания из конструктора, не KeyError
    with TestClient(app, client=FRONT) as c:
        role.teacher_tick()
        body = c.get("/health/teacher").json()
    assert body["shares_loaded"] is False and body["checked_at"] is not None  # строка сторожа — fail по п. 3


# --- сверх брифа ---

def pulses(tmp_path) -> int:
    """Строк пульса журнала действий в core.db (0 или 1: UPSERT одной строки); таблицы ещё нет — 0."""
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        try:
            return db.execute("SELECT count(*) FROM teacher_heartbeat").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def test_reaper_runs_teacher_tick(tmp_path):                              # такт уборщика сам, без ручного вызова
    app, role = teacher_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        wait_until(lambda: c.get("/health/teacher").json()["checked_at"] is not None)
        body = c.get("/health/teacher").json()
    assert (body["shares_loaded"], body["shares_ok"], body["shares_drift"], body["store_ok"], body["shares_sum_usd"],
            body["total_usd"], body["drill"]) == (True, True, 0, True, 1100.0, 1000.0, "")   # Д15б: 11 × $100
    assert set(body) == KEYS


def test_journal_down_turns_store_ok_false_at_first_tick(tmp_path):      # учение journal_down: пульс не пишется
    app, role = teacher_gateway(tmp_path, DRILL_TEACHER="journal_down")
    assert role.teacher_log.drill == "journal_down" and role.llm.shares.drill == "journal_down"
    with TestClient(app, client=FRONT) as c:
        wait_until(lambda: c.get("/health/teacher").json()["store_ok"] is False)
        body = c.get("/health/teacher").json()
    assert (body["drill"], body["shares_ok"], body["shares_drift"]) == ("journal_down", True, 0)   # доли сходятся


def test_store_ok_comes_back_with_the_next_pulse(tmp_path, monkeypatch):  # сбой записи — false, пульс — снова true
    app, role = teacher_gateway(tmp_path)
    with TestClient(app, client=FRONT) as c:
        wait_until(lambda: pulses(tmp_path) == 1)                            # первый такт уборщика прошёл целиком
        real = role.teacher_log._connect
        monkeypatch.setattr(role.teacher_log, "_connect", lambda create=False, readonly=False: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")))
        role.teacher_tick()
        assert c.get("/health/teacher").json()["store_ok"] is False
        monkeypatch.setattr(role.teacher_log, "_connect", real)
        role.teacher_tick()
        assert c.get("/health/teacher").json()["store_ok"] is True


def test_teacher_tick_failure_still_pulses_and_reaper_lives(tmp_path, gwlog, monkeypatch):
    app, role = teacher_gateway(tmp_path)

    def broken(now, effective):
        raise RuntimeError("не должно уйти в журнал")
    monkeypatch.setattr(role.llm.shares, "verify", broken)
    with TestClient(app, client=FRONT):
        wait_until(lambda: "такт кабинета преподавателя: RuntimeError" in gwlog.text)
    assert "не должно уйти" not in gwlog.text                                # только класс
    assert pulses(tmp_path) == 1                                             # пульс прошёл и так


def test_drift_is_logged_once_per_change(tmp_path, gwlog):
    app, role = teacher_gateway(tmp_path, DRILL_TEACHER="shares_stale")
    with TestClient(app, client=FRONT):
        role.teacher_tick()
        role.llm.shares.apply([(3, 10000, 9000), (5, 10000, 11000)], by="teach", now=now_utc())
        role.teacher_tick()
        role.teacher_tick()
    assert gwlog.text.count("кабинет преподавателя: LLM-прокси считает доли не так, как сохранено — мест: 2") == 1


def test_shares_sum_is_memory_and_total_from_llm(tmp_path):              # сумма — память прокси, бюджет — LLM_TOTAL_USD
    llm = make_llm_role(tmp_path, key=None, shares=True, LLM_TOTAL_USD=500.0, LLM_SHARE_USD=40.0)
    assert llm.shares.open()
    app, role = make_gateway(tmp_path, llm=llm)
    with TestClient(app, client=FRONT) as c:
        body = c.get("/health/teacher").json()
    assert (body["shares_sum_usd"], body["total_usd"]) == (440.0, 500.0)  # Д15б: 11 × $40 умолчания


# --- итоговая доводка Д10а: ревью А2, m-1 и m-6 ---

def test_transfer_mid_tick_gives_no_false_drift(tmp_path, monkeypatch):  # m-1: центы прокси — под замком перевода
    app, role = teacher_gateway(tmp_path)
    shares, budget = role.llm.shares, role.llm.budget
    real = budget.share_of
    mover: list[threading.Thread] = []

    def share_of(caller: str) -> float:
        value = real(caller)
        if caller == "student-03" and not mover:                            # такт прочёл долю места 03 — и тут перевод
            t = threading.Thread(target=shares.apply, args=([(3, 10000, 9000), (5, 10000, 11000)],),
                                 kwargs={"by": "teach", "now": now_utc()})
            mover.append(t)
            t.start()
            t.join(0.3)                                                      # вне замка перевод прошёл бы целиком
        return value
    monkeypatch.setattr(budget, "share_of", share_of)
    role.teacher_tick()
    mover[0].join(5)
    assert (shares.ok, shares.drift) == (True, 0)                            # ложного fail на такт нет
    assert (shares.cents(3), shares.cents(5)) == (9000, 11000)               # перевод прошёл — после сверки
    role.teacher_tick()
    assert shares.drift == 0


def test_journal_not_opened_at_start_reopens_by_tick(tmp_path, monkeypatch, gwlog):   # m-6: без перезапуска core
    app, role = teacher_gateway(tmp_path)
    journal = role.teacher_log
    real_open, opens = journal.open, []

    def flaky_open() -> bool:
        opens.append(1)
        if len(opens) == 1:                                                  # при запуске — блокировка дольше срока
            journal.last_error = "OperationalError"
            return False
        return real_open()
    monkeypatch.setattr(journal, "open", flaky_open)
    with TestClient(app, client=FRONT) as c:
        wait_until(lambda: c.get("/health/teacher").json()["store_ok"] is True)   # такт уборщика открыл журнал сам
        r = Gw(app, role).as_("teach").get(TEACHER_PREFIX + "places/03/sessions")
        assert r.status_code == 200                                          # кабинет открыт, 503 journal нет
    assert len(opens) >= 2 and "журнал действий преподавателя не открыт (OperationalError)" in gwlog.text
