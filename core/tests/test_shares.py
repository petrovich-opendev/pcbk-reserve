"""Доли мест (Д10-R97): целые центы в llm_shares core.db, перевод «всё или ничего» со сверкой «было», пол — оплаченное
вниз до цента с запасом против шума float, сумма не больше бюджета или не больше прежней, строка журнала действий — в
той же транзакции; «Остановить место» — одна серверная операция; сверка диска с долями, по которым отказывает прокси."""
import math
import random
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone

import pytest

from pcbk_core.llm.shares import (PLACE_CALLER, STALE_DRILL, ShareRefused, Shares, SharesStoreError, floor_cents,
                                  money_text)
from pcbk_core.teacher_log import JOURNAL_DRILL, TeacherLog

NOW = datetime(2026, 10, 11, 9, tzinfo=timezone.utc)


def make(path, settled=None, *, total=1000.0, default=100.0, drill=""):
    path.mkdir(parents=True, exist_ok=True)
    settled = {} if settled is None else settled            # без копии: тест меняет оплаченное на ходу
    s = Shares(str(path / "core.db"), total_usd=total, default_usd=default, drill=drill)
    s.settled_of = lambda caller: settled.get(caller, 0.0)
    assert s.open(), s.last_error
    return s


def memory(s):                                                             # центы, по которым отказал бы прокси
    return {n: s.cents(n) for n in range(1, 11)}


def test_defaults(tmp_path):
    s = make(tmp_path)
    assert [s.cents(n) for n in range(1, 11)] == [10000] * 10 and s.sum_cents() == s.total_cents == 100000
    assert (s.usd("student-03"), s.usd("ops"), s.usd("student-11"), s.usd("bench")) == (100.0, None, None, None)
    assert (money_text(123456), money_text(5)) == ("1234,56 $", "0,05 $")


def test_transfer_and_reload(tmp_path):
    s = make(tmp_path)
    s.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)
    assert (s.cents(3), s.cents(5), s.usd("student-05"), s.sum_cents()) == (4000, 16000, 160.0, 100000)
    again = make(tmp_path)                                                 # новый процесс — память по диску
    assert (again.cents(3), again.cents(5)) == (4000, 16000)


def test_floor_is_settled_rounded_down_and_all_or_nothing(tmp_path):     # Review Focus 3; B-1
    s = make(tmp_path, {"student-03": 12.349})
    assert (floor_cents(12.349), floor_cents(0.0), floor_cents(0.0049), floor_cents(0.5037)) == (1234, 0, 0, 50)
    assert (floor_cents(0.3), floor_cents(1.0)) == (29, 99)                  # цена запаса: ровная сумма — цент ниже
    assert s.floor_of(3) == 1234
    with pytest.raises(ShareRefused) as e:
        s.apply([(3, 10000, 1233), (5, 10000, 18767)], by="teach", now=NOW)
    assert (e.value.kind, e.value.text) == ("below_spent",
                                            "Доля места 03 не может быть меньше уже оплаченного: 12,34 $")
    assert (s.cents(3), s.cents(5)) == (10000, 10000)                      # ни одно место не изменилось
    s.apply([(3, 10000, 1234), (5, 10000, 18766)], by="teach", now=NOW)    # ровно пол — можно
    assert s.usd("student-03") <= 12.349                                    # та же арифметика, что Budget: cents / 100


def acc(price: float, n: int) -> float:
    """Сумма как в журнале вызовов: += по одному (sum() в 3.12 складывает точнее и шум прячет)."""
    x = 0.0
    for _ in range(n):
        x += price
    return x


def test_floor_cents_survives_float_noise():                             # B-1 р2
    assert repr(acc(0.0012, 25)) == "0.029999999999999995"
    cases = {(0.0012, 25): 2, (0.0012, 50): 5, (0.013, 90): 116, (0.1, 8): 79}
    for (price, n), want in cases.items():
        s = acc(price, n)
        assert floor_cents(s) == want, (price, n)
        assert floor_cents(s) / 100 <= s                                     # отказ spent >= share сработает
    rnd = random.Random(10)
    prices = (0.0012, 0.5037, 0.002, 0.0007, 0.013, 0.0123, 0.07)
    for _ in range(300):
        s = acc(rnd.choice(prices), rnd.randint(1, 3000)) + acc(rnd.choice(prices), rnd.randint(0, 50))
        c = floor_cents(s)
        assert c == 0 or c / 100 <= s - 1e-9, s
        assert c >= math.floor(s * 100) - 1, s                               # и не больше цента в запас


def test_apply_floor_is_one_operation(tmp_path):                         # I-1 р2: пол берёт сервер под замком
    s = make(tmp_path, {"student-03": 12.349})
    s.apply_floor(3, 5, by="teach", now=NOW)
    assert (s.cents(3), s.cents(5), s.sum_cents()) == (1234, 18766, 100000)
    with pytest.raises(ShareRefused) as e:
        s.apply_floor(3, 5, by="teach", now=NOW)                             # уже на полу
    assert e.value.kind == "at_floor"
    for bad in ((3, 3), (0, 5), (3, 11), (True, 5)):
        with pytest.raises(ShareRefused) as e:
            s.apply_floor(*bad, by="teach", now=NOW)
        assert e.value.kind == "bad", bad
    log = TeacherLog(str(tmp_path / "core.db"))
    assert log.open()
    assert log.recent(1)[0].detail == {"03": [10000, 1234], "05": [10000, 18766], "kind": "floor"}
    # Пол — на момент перевода, не из обзора: вызов кончился между обзором и кнопкой (I-1 р3).
    # ВНИМАНИЕ исполнителю: 1289 при оплаченном ровно $12,90 — НЕ ошибка формулы, а цена запаса 1e-9 (R97):
    # на ровной сумме пол на цент ниже. Тест красный — ищи ошибку в коде или ожидании, формулу floor_cents
    # НЕ ослабляй (без запаса или с round вверх вернётся B-1 р2 и упадёт test_floor_cents_survives_float_noise).
    grow = {"student-03": 12.349}
    s2 = make(tmp_path / "g", grow)
    grow["student-03"] = 12.9                                                # ровная сумма
    s2.apply_floor(3, 5, by="teach", now=NOW)
    assert s2.cents(3) == 1289                                               # ровная сумма — цент ниже, по формуле
    grow3 = {"student-03": 12.349}
    s3 = make(tmp_path / "h", grow3)
    grow3["student-03"] = 12.903                                             # неровный рост
    s3.apply_floor(3, 5, by="teach", now=NOW)
    assert s3.cents(3) == 1290                                               # вниз до цента, без запасного цента
    # m-3 р3: при сумме долей больше бюджета (п. 16) доля «куда» не может стать больше total_cents
    s4 = make(tmp_path / "t", {}, total=150.0)
    with pytest.raises(ShareRefused) as e:
        s4.apply_floor(3, 5, by="teach", now=NOW)                            # 100 + 100 > 150
    assert e.value.kind == "bad" and "больше общего бюджета" in e.value.text
    assert (s4.cents(3), s4.cents(5)) == (10000, 10000)                      # ничего не записано


def test_conflict_and_bad_input(tmp_path):                               # Review Focus 3
    s = make(tmp_path)
    with pytest.raises(ShareRefused) as e:
        s.apply([(3, 9999, 9000), (5, 10000, 11000)], by="teach", now=NOW)   # вторая вкладка видела другое
    assert e.value.kind == "conflict"
    for bad in ([], [(3, 10000, -1)], [(3, 10000, True)], [(3, 10000, 10.5)], [(0, 10000, 1)], [(11, 10000, 1)],
                [(True, 10000, 1)], [(3, 10000, 9000), (3, 9000, 8000)],
                [(n, 10000, 10000) for n in range(1, 11)] + [(1, 10000, 1)], [(3, 10000, 100001)]):
        with pytest.raises(ShareRefused) as e:
            s.apply(bad, by="teach", now=NOW)
        assert e.value.kind == "bad", bad
    assert s.sum_cents() == 100000


def test_sum_not_over_total_unless_not_worse(tmp_path):                  # сверка с кодом, п. 16
    s = make(tmp_path)
    with pytest.raises(ShareRefused) as e:
        s.apply([(3, 10000, 10001)], by="teach", now=NOW)
    assert e.value.kind == "over_total" and "1000,01 $ из 1000,00 $" in e.value.text
    s.apply([(3, 10000, 9000)], by="teach", now=NOW)                       # в свободный остаток
    s.apply([(3, 9000, 10000)], by="teach", now=NOW)                       # и обратно — ровно бюджет
    # I-5: бюджет $500 меньше суммы по умолчанию ($1000), каждая доля ($100) — меньше бюджета
    low = make(tmp_path / "low", total=500.0)
    assert (low.sum_cents(), low.total_cents) == (100000, 50000)
    low.apply([(3, 10000, 9000), (5, 10000, 11000)], by="teach", now=NOW)  # перевод — сумма та же, не хуже
    with pytest.raises(ShareRefused) as e:
        low.apply([(3, 9000, 9001)], by="teach", now=NOW)                  # хуже прежнего
    assert e.value.kind == "over_total"
    low.apply([(3, 9000, 8000)], by="teach", now=NOW)                      # меньше прежнего — можно
    over = make(tmp_path / "over", total=50.0)                              # доля места ($100) больше бюджета ($50)
    with pytest.raises(ShareRefused) as e:
        over.apply([(3, 10000, 9000), (5, 10000, 11000)], by="teach", now=NOW)
    assert e.value.kind == "bad"                                            # граница значения — total_cents (R97)


def test_apply_writes_journal_in_same_transaction(tmp_path):             # Д10-R99; Review Focus 4
    s = make(tmp_path)
    s.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)
    log = TeacherLog(str(tmp_path / "core.db"))
    assert log.open()
    row = log.recent(1)[0]
    assert (row.login, row.action, row.place, row.target, row.outcome, row.detail) == \
           ("teach", "shares", None, None, "ok", {"03": [10000, 4000], "05": [10000, 16000]})


def test_journal_down_refuses_change(tmp_path):                          # учение journal_down; m-17
    s = make(tmp_path, drill=JOURNAL_DRILL)
    with pytest.raises(SharesStoreError) as e:
        s.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)
    assert e.value.journal is True and s.cents(3) == 10000
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        assert db.execute("SELECT count(*) FROM llm_shares").fetchone()[0] == 0     # и на диске ничего


def test_store_error_is_not_journal(tmp_path, monkeypatch):              # m-17: сбой самих долей — journal=False
    s = make(tmp_path)
    monkeypatch.setattr(s, "_connect", lambda create=False: (_ for _ in ()).throw(sqlite3.OperationalError("disk")))
    with pytest.raises(SharesStoreError) as e:
        s.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)
    assert e.value.journal is False and "OperationalError" in str(e.value) and "disk" not in str(e.value)


def test_verify_compares_disk_with_effective_shares(tmp_path, monkeypatch):  # I-7; учение shares_stale
    s = make(tmp_path, drill=STALE_DRILL)
    s.verify(NOW, memory(s))
    assert (s.ok, s.drift, s.checked_at) == (True, 0, NOW)
    s.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)   # диск новый, память прежняя
    assert s.cents(3) == 10000
    s.verify(NOW, memory(s))
    assert s.drift == 2
    honest = make(tmp_path / "h")
    honest.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)
    honest.verify(NOW, memory(honest))
    assert honest.drift == 0
    honest.verify(NOW, {n: 10000 for n in range(1, 11)})                    # Budget собран без долей: $100 у всех
    assert honest.drift == 2
    with closing(sqlite3.connect(tmp_path / "h" / "core.db")) as db, db:
        db.execute("UPDATE llm_shares SET cents = 3999 WHERE place = 3")   # правка мимо кабинета
    honest.verify(NOW, memory(honest))
    assert (honest.drift, honest.cents(3)) == (1, 4000)                     # сверка не чинит память
    monkeypatch.setattr(honest, "_connect",                                 # таблица не читается — ok ложь (m-3)
                        lambda create=False: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    honest.verify(NOW, memory(honest))
    assert (honest.ok, honest.loaded, honest.checked_at) == (False, True, NOW)


def test_open_failure_is_reported(tmp_path):
    s = Shares(str(tmp_path / "нет" / "core.db"), total_usd=1000.0, default_usd=100.0)
    assert s.open() is False and s.loaded is False and s.last_error


# --- сверх брифа ---

def test_constructor_does_no_io_and_keeps_defaults(tmp_path):            # договор Role; m-5 р3
    s = Shares(str(tmp_path / "нет" / "core.db"), total_usd=250.0, default_usd=30.0, drill=STALE_DRILL)
    assert not (tmp_path / "нет").exists()
    assert (s.loaded, s.ok, s.drift, s.checked_at, s.last_error) == (False, False, 0, None, None)
    assert [s.cents(n) for n in range(1, 11)] == [3000] * 10 and (s.total_cents, s.default_cents) == (25000, 3000)
    assert s.drill == STALE_DRILL and s.settled_of("student-01") == 0.0


def test_place_caller_is_exact():
    assert all(PLACE_CALLER.fullmatch(f"student-{n:02d}") for n in range(1, 11))
    for other in ("student-00", "student-11", "student-1", "student-010", "ops", "teacher", "student-01 ", "bench-x"):
        assert PLACE_CALLER.fullmatch(other) is None, other
    s = Shares(":memory:", total_usd=1000.0, default_usd=100.0)
    assert [s.usd(c) for c in ("student-1", "student-010", "Student-01", None, 3)] == [None] * 5


def test_floor_cents_odd_inputs_never_raise():                           # минус и не число — пол ноль, без исключения
    assert [floor_cents(x) for x in (-0.5, -1e-12, float("nan"), float("inf"), float("-inf"))] == [0] * 5
    assert (floor_cents(0.01), floor_cents(0.0100001), floor_cents(1000.0)) == (0, 1, 99999)


def test_rejected_change_writes_nothing(tmp_path):                       # отказ по правилам — ни долей, ни строк
    s = make(tmp_path, {"student-03": 12.349})
    for changes in ([(3, 10000, 1000), (5, 10000, 19000)], [(3, 10000, 10001)], [(3, 1, 2)]):
        with pytest.raises(ShareRefused):
            s.apply(changes, by="teach", now=NOW)
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        assert db.execute("SELECT count(*) FROM llm_shares").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM teacher_actions").fetchone()[0] == 0


def test_rows_on_disk_carry_who_and_when(tmp_path):
    s = make(tmp_path)
    s.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        rows = db.execute("SELECT place, cents, updated_at, updated_by FROM llm_shares ORDER BY place").fetchall()
    assert rows == [(3, 4000, NOW.isoformat(), "teach"), (5, 16000, NOW.isoformat(), "teach")]


def test_shares_stale_logs_warning_and_keeps_memory(tmp_path, caplog):
    s = make(tmp_path, {"student-03": 1.0}, drill=STALE_DRILL)
    with caplog.at_level("WARNING", logger="pcbk_core.llm.shares"):
        s.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)
        s.apply_floor(5, 3, by="teach", now=NOW)                           # и «Остановить место» — диск, не память
    assert caplog.text.count("УЧЕНИЯ: shares_stale — доли записаны, память прокси не обновлена") == 2
    assert memory(s) == {n: 10000 for n in range(1, 11)}
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        assert dict(db.execute("SELECT place, cents FROM llm_shares")) == {3: 20000, 5: 0}   # пол места 05 — ноль


def test_bad_row_on_disk_closes_loading(tmp_path):                       # правка мимо кабинета дробным — не загрузить
    s = make(tmp_path)
    with closing(sqlite3.connect(tmp_path / "core.db")) as db, db:
        db.execute("INSERT INTO llm_shares (place, cents, updated_at, updated_by) VALUES (4, 39.5, 'x', 'x')")
    again = Shares(str(tmp_path / "core.db"), total_usd=1000.0, default_usd=100.0)
    assert again.open() is False and again.loaded is False and again.last_error
    s.verify(NOW, memory(s))
    assert s.ok is False                                                     # сверка тоже не верит строке


def test_concurrent_transfers_do_not_lose_a_cent(tmp_path):              # CAS под замком: две вкладки в один миг
    s = make(tmp_path)
    results: list[str] = []

    def move(to: int) -> None:
        try:
            s.apply([(3, 10000, 9000), (to, 10000, 11000)], by="teach", now=NOW)
            results.append("ok")
        except ShareRefused as exc:
            results.append(exc.kind)
    threads = [threading.Thread(target=move, args=(to,)) for to in (5, 6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "ok"] and s.sum_cents() == 100000
    again = make(tmp_path)
    assert (again.cents(3), again.sum_cents()) == (9000, 100000)


def test_store_error_text_has_code_not_values(tmp_path, monkeypatch):    # текст — класс и код SQLite
    s = make(tmp_path)

    class Full:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if sql.strip().upper() == "COMMIT":
                err = sqlite3.OperationalError("database or disk is full: /secret/path 4000")
                err.sqlite_errorname = "SQLITE_FULL"
                raise err
            return self.db.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.db, name)
    real = s._connect
    monkeypatch.setattr(s, "_connect", lambda create=False: Full(real(create)))
    with pytest.raises(SharesStoreError) as e:
        s.apply([(3, 10000, 4000), (5, 10000, 16000)], by="teach", now=NOW)
    assert e.value.journal is False and str(e.value) == "доли мест: OperationalError SQLITE_FULL"
    assert s.cents(3) == 10000
