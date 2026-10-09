"""Учётные записи шлюза (Д5-R24, Д5-R28): scrypt, место и логин закреплены навсегда, сессии хешем, замок
входа без оракула, хранилище без ввода-вывода в конструкторе, команда администратора."""
import hashlib
import io
import os
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone

import pytest

from helpers import AGENT_DRAFT, AGENT_NOW, PW, FakeMono, agent_text, fail_connect, full_disk, make_agents_root, store
from pcbk_core.agents import admin
from pcbk_core.agents.store import AgentStore
from pcbk_core.gateway import accounts
from pcbk_core.gateway.accounts import (LOCK_FAILURES, LOCK_WINDOW_S, MAX_SESSIONS_PER_LOGIN, NOTICE, UNKNOWN_KEYS,
                                        AccountStore, StoreError, hash_password, verify_password)
from pcbk_core.gateway.accounts import main as accounts_main
from pcbk_core.llm.shares import Shares
from pcbk_core.teacher_log import TeacherLog


def db_bytes(tmp_path) -> bytes:                                                        # WAL: все три файла
    return b"".join(p.read_bytes() for p in tmp_path.glob("core.db*"))


def test_constructor_does_no_io(tmp_path):                                              # договор Role
    AccountStore(str(tmp_path / "нет" / "core.db"))
    assert not (tmp_path / "нет").exists()
    s = AccountStore(str(tmp_path / "нет" / "core.db"))
    assert s.open() is False and s.last_error                                           # каталога нет — не бросает


def test_password_hash_roundtrip_and_format():
    h = hash_password("верный-пароль-1")
    assert re.fullmatch(r"scrypt\$16384\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{64}", h)
    assert verify_password("верный-пароль-1", h) and not verify_password("неверный", h)
    assert hash_password("x" * 8) != hash_password("x" * 8)                          # соль


def test_secrets_at_rest(tmp_path):                                                     # Review Focus 5
    s = store(tmp_path)
    s.add("ivanov", "student", 3, PW)
    tok = s.open_session("ivanov")
    raw = db_bytes(tmp_path)                                                           # соединения закрыты после операций
    assert tok.encode() not in raw and PW.encode("utf-8") not in raw
    # положительный контроль побайтного скана (ревью А2, M4): записанное скан видит
    assert hashlib.sha256(tok.encode()).hexdigest().encode() in raw and b"scrypt$16384$8$1$" in raw
    con = sqlite3.connect(tmp_path / "core.db")                                        # положительный контроль
    assert [r[0] for r in con.execute("SELECT token_sha FROM gw_sessions")] == [hashlib.sha256(tok.encode()).hexdigest()]
    assert con.execute("SELECT pw_hash FROM gw_accounts").fetchone()[0].startswith("scrypt$16384$8$1$")
    con.close()
    raw = db_bytes(tmp_path)                                                           # после чужого чтения — снова
    assert tok.encode() not in raw and PW.encode("utf-8") not in raw


def test_add_rules(tmp_path):
    s = store(tmp_path)
    s.add("ivanov", "student", 3, PW)
    for args in (("ivanov", "student", 4, PW), ("petrov", "student", 3, PW), ("petrov", "student", None, PW),
                 ("teach", "teacher", 5, PW), ("Petrov", "student", 4, PW), ("petrov", "student", 11, PW),
                 ("petrov", "student", 4, "1234567")):
        with pytest.raises(ValueError) as e:
            s.add(*args)
        assert PW not in str(e.value)
    s.disable("ivanov")
    with pytest.raises(ValueError):
        s.add("petrov", "student", 3, PW)                                              # отключение место не освобождает


def test_place_bound_forever(tmp_path):                                                 # Review Focus 1
    s = store(tmp_path)
    s.add("ivanov", "student", 3, PW)
    s.disable("ivanov"); s.remove("ivanov")
    with pytest.raises(ValueError) as e:
        s.add("petrov", "student", 3, PW)
    assert "место 03 закреплено за учёткой ivanov" in str(e.value)
    with pytest.raises(ValueError) as e:
        s.add("ivanov", "student", 3, PW)                                              # надгробие логина
    assert "логин ivanov был у студента места 03" in str(e.value)
    assert s.add("ivanov", "student", 3, PW, same_person=True).place == 3
    assert s.bindings() == [(3, "ivanov", True)]
    with pytest.raises(ValueError):
        s.release_place(3)                                                             # не пробная учётка
    s.add("d5-probe-a", "student", 10, PW)
    with pytest.raises(ValueError):
        s.release_place(10)                                                            # пробная, но действует
    s.disable("d5-probe-a"); s.release_place(10)
    assert s.add("petrov", "student", 10, PW).place == 10


def test_same_person_only_own_place(tmp_path):                                          # ревью А2, I1
    s = store(tmp_path)
    s.add("ivanov", "student", 3, PW)
    s.disable("ivanov"); s.remove("ivanov")
    for args in (("ivanov", "student", 4, PW), ("ivanov", "teacher", None, PW)):      # чужое место, роль teacher
        with pytest.raises(ValueError) as e:
            s.add(*args, same_person=True)
        assert "логин ivanov закреплён за местом 03 — тот же человек возвращается только на своё место" in str(e.value)
    assert s.list() == [] and s.bindings() == [(3, "ivanov", False)]                  # отказ ничего не записал
    assert s.add("ivanov", "student", 3, PW, same_person=True).place == 3


def test_bindings_account_by_login_and_place(tmp_path):                                # ревью А2, M5
    s = store(tmp_path)
    s.add("ivanov", "student", 3, PW)
    con = sqlite3.connect(tmp_path / "core.db")
    with con:                                                                          # учётка того же логина на другом месте
        con.execute("UPDATE gw_accounts SET place = 4 WHERE login = 'ivanov'")
    con.close()
    assert s.bindings() == [(3, "ivanov", False)]


def test_verify_and_sessions(tmp_path):
    clock = FakeMono(1000.0)
    s = store(tmp_path, clock=clock, ttl_s=43200)
    s.add("ivanov", "student", 3, PW)
    assert s.verify("ivanov", PW).place == 3
    assert s.verify("ivanov", "x") is None and s.verify("nobody", "x") is None
    tok = s.open_session("ivanov")
    assert len(tok) >= 43 and s.session(tok).login == "ivanov"
    clock.advance(43201)
    assert s.session(tok) is None
    tok = s.open_session("ivanov")
    s.set_password("ivanov", "новый-пароль")
    assert s.session(tok) is None                                                     # смена пароля закрывает сессии
    tok = s.open_session("ivanov")
    s.disable("ivanov")
    assert s.session(tok) is None and s.verify("ivanov", "новый-пароль") is None


def test_sessions_per_login_capped_oldest_closed(tmp_path, gwlog):                      # итоговая доводка Д5б, N2
    clock = FakeMono(1000.0)
    s = store(tmp_path, clock=clock)
    s.add("ivanov", "student", 3, PW)
    s.add("petrov", "student", 4, PW)
    other = s.open_session("petrov")
    tokens = []
    for _ in range(MAX_SESSIONS_PER_LOGIN + 1):                                       # 4 входа, два устройства — не предел
        tokens.append(s.open_session("ivanov"))
        clock.advance(1)
    assert MAX_SESSIONS_PER_LOGIN == 3
    assert [s.session(t) is not None for t in tokens] == [False, True, True, True]     # закрыта самая старая
    assert s.session(other).login == "petrov"                                          # чужие сессии не тронуты
    assert "gw sessions evicted login=ivanov n=1" in gwlog.text
    same = [s.open_session("ivanov") for _ in range(4)]                                # одно время открытия — по порядку
    assert [s.session(t) is not None for t in tokens + same] == [False] * 5 + [True] * 3


def test_session_eviction_by_insert_order_not_clock(tmp_path):                         # остаток Д5б, 2
    clock = FakeMono(5000.0)
    s = store(tmp_path, clock=clock)
    s.add("ivanov", "student", 3, PW)
    old = [s.open_session("ivanov") for _ in range(MAX_SESSIONS_PER_LOGIN)]
    clock.advance(-600)                                                                # часы шагнули назад (NTP)
    new = s.open_session("ivanov")                                                     # открыта последней — живёт
    assert (s.session(new) is not None, [s.session(t) is not None for t in old]) == (True, [False, True, True])


def test_login_lock_no_oracle_persisted_and_unlocked(tmp_path):
    clock = FakeMono(0.0)
    s = store(tmp_path, clock=clock)
    s.add("ivanov", "student", 3, PW)
    for login in ("ivanov", "nobody"):
        for _ in range(LOCK_FAILURES):
            s.failure(login)
    assert s.locked("ivanov") and s.locked("nobody")                                   # одинаково: нет оракула
    assert store(tmp_path, clock=clock).locked("ivanov")                               # существующий — переживает перезапуск
    s.unlock("ivanov")
    assert not s.locked("ivanov")
    for _ in range(LOCK_FAILURES):
        s.failure("ivanov")
    s.set_password("ivanov", "новый-пароль")
    assert not s.locked("ivanov")
    for i in range(UNKNOWN_KEYS + 50):
        s.failure(f"nobody{i}")                                                        # память ограничена
    assert len(s.unknown_failures) == UNKNOWN_KEYS
    con = sqlite3.connect(tmp_path / "core.db")
    assert con.execute("SELECT count(*) FROM gw_login_failures WHERE login LIKE 'nobody%'").fetchone()[0] == 0
    for _ in range(LOCK_FAILURES):
        s.failure("ivanov")
    clock.advance(LOCK_WINDOW_S)
    assert not s.locked("ivanov")


def test_unknown_lock_survives_junk_flood(tmp_path):                                   # ревью А2, M2
    clock = FakeMono(0.0)
    s = store(tmp_path, clock=clock)
    for _ in range(LOCK_FAILURES):
        s.failure("victim")
    for i in range(UNKNOWN_KEYS + 50):
        s.failure(f"Junk-{i}")                                                         # не вид логина: только память
    assert s.locked("victim") and len(s.unknown_failures) == UNKNOWN_KEYS             # вытеснены незапертые
    clock.advance(LOCK_WINDOW_S)                                                       # замок истёк — вытесняется первым
    s.failure("Junk-new")
    assert not s.locked("victim") and len(s.unknown_failures) == UNKNOWN_KEYS
    assert hashlib.sha256(b"victim").digest() not in s.unknown_failures                # ключ — sha256 логина
    assert hashlib.sha256(b"Junk-51").digest() in s.unknown_failures                  # а не самый старый незапертый


def scrypt_spy(monkeypatch) -> list[int]:
    """Шпион на accounts._scrypt: список, куда каждый вызов кладёт 1 (зовут из потоков — append атомарен)."""
    calls, real = [], accounts._scrypt

    def spy(password, salt):
        calls.append(1)
        return real(password, salt)
    monkeypatch.setattr(accounts, "_scrypt", spy)
    return calls


def test_verify_one_scrypt_in_every_case(tmp_path, monkeypatch):                      # итоговое ревью Д5а, I1 (R1)
    """Выравнивание времени verify: ровно один scrypt на каждый вызов — неверный пароль, отключённая учётка с
    верным, неизвестный логин верного вида и логин неверного вида (свой хеш или хеш-пустышка)."""
    s = store(tmp_path)                                                                # open() посчитал пустышку
    s.add("ivanov", "student", 3, PW)
    s.add("petrov", "student", 4, PW)
    s.disable("petrov")
    calls = scrypt_spy(monkeypatch)
    for login, pw in (("ivanov", "неверный-1"), ("petrov", PW), ("nobody7", PW), ("Не-Логин!", PW)):
        calls.clear()
        assert s.verify(login, pw) is None and len(calls) == 1, login
    calls.clear()
    assert s.verify("ivanov", PW) is not None and len(calls) == 1                     # положительный контроль


def test_lock_counts_pending_stamps_once(tmp_path):                                   # итоговое ревью Д5а, А4-m1 (R7)
    """Метки неудач, которые шлюз учёл, но ещё не записал (pending), считаются вместе с записанными; неудача,
    видная и там и там (записали между чтениями), — один раз; метки вне окна — нет. Таблица и память — одинаково."""
    clock = FakeMono(5000.0)
    s = store(tmp_path, clock=clock)
    s.add("ivanov", "student", 3, PW)
    stamps = [4990.0 + i for i in range(LOCK_FAILURES)]
    old = [5000.0 - LOCK_WINDOW_S - 1, 5000.0 - LOCK_WINDOW_S]                        # окно — строго позже
    for name in ("ivanov", "nobody7", "Не-Логин!"):
        for t in stamps[:3]:
            s.failure(name, t)
        assert not s.locked(name, stamps[:3] + old), name                              # 3, а не 6 и не 8
        assert not s.locked(name, stamps[2:4]), name                                   # 4
        assert s.locked(name, stamps[2:]), name                                        # 5
    assert s.locked("ivanov") is False and s.locked("ivanov", ()) is False


def test_store_errors_counted_without_paths(tmp_path, monkeypatch):
    s = store(tmp_path)
    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    with pytest.raises(StoreError) as e:
        s.students()
    assert str(tmp_path) not in str(e.value) and s.op_failures == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root пишет и в файл 0444")
def test_probe_sees_readonly_and_keeps_operation_failures(tmp_path):                   # Д5-R28, р4; без подмен
    s = store(tmp_path)
    s.add("ivanov", "student", 3, PW)
    db = tmp_path / "core.db"
    assert s.ping() is True and (s.probe_failures, s.op_failures) == (0, 0)
    db.chmod(0o444); tmp_path.chmod(0o555)                                             # том только для чтения
    try:
        with pytest.raises(StoreError):
            s.open_session("ivanov")                                                   # вход пишет — сбой
        assert s.op_failures == 1
        assert s.ping() is False and s.probe_failures == 1                             # SELECT 1 здесь прошёл бы
    finally:
        tmp_path.chmod(0o755); db.chmod(0o644)
    assert s.ping() is True and (s.probe_failures, s.op_failures) == (0, 1)            # проба сбой операций не снимает
    s.open_session("ivanov")
    assert s.op_failures == 0                                                          # снимает удачная операция


def test_full_disk_write_failure_outlives_reads(tmp_path):                           # итоговое ревью Д5а, M2 (R3)
    """Переполненный диск: пульс ping() — настоящая запись с COMMIT и видит его; сбой записи операции не снимают
    ни удачные чтения, ни удачный пульс — только удачная запись."""
    s = store(tmp_path)
    s.add("ivanov", "student", 3, PW)
    with full_disk(s):
        assert s.ping() is False and s.probe_failures == 1                             # INSERT + ROLLBACK прошёл бы
        with pytest.raises(StoreError):
            s.open_session("ivanov")                                                   # вход пишет — сбой
        assert (s.write_failures, s.read_failures) == (1, 0)
        assert s.verify("ivanov", PW) is not None and s.session("чужой") is None       # чтения идут
        assert s.locked("ivanov") is False and s.op_failures == 1                      # и сбой записи не снимают
    assert s.ping() is True and (s.probe_failures, s.op_failures) == (0, 1)            # пульс — тоже (Д5-R28)
    s.close_session("чужой")                                                           # DELETE без строк — не запись
    assert s.op_failures == 1
    s.open_session("ivanov")
    assert s.op_failures == 0                                                          # снимает удачная запись
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        assert db.execute("SELECT count(*) FROM gw_heartbeat").fetchone()[0] == 1      # одна строка пульса


def test_expired_session_delete_is_a_write(tmp_path):                                   # M-4
    """Удаление истёкшей сессии в session() — операция записи: её сбой — сбой записи, который чтение не снимает;
    удачное удаление снимает."""
    clock = FakeMono(5000.0)
    s = store(tmp_path, clock=clock, ttl_s=60.0)
    s.add("ivanov", "student", 3, PW)
    token = s.open_session("ivanov")
    clock.advance(61)
    with full_disk(s):
        with pytest.raises(StoreError):
            s.session(token)                                                           # чтение прошло, DELETE — нет
        assert (s.write_failures, s.read_failures) == (1, 0)
    assert s.session("чужой") is None and s.op_failures == 1                           # чтение сбой записи не снимает
    assert s.session(token) is None and s.op_failures == 0                             # удаление прошло
    with closing(sqlite3.connect(tmp_path / "core.db")) as db:
        assert db.execute("SELECT count(*) FROM gw_sessions").fetchone()[0] == 0


def test_read_failure_cleared_by_read(tmp_path, monkeypatch):                          # R3: сбой чтения — как прежде
    s = store(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(sqlite3, "connect", fail_connect)
        with pytest.raises(StoreError):
            s.students()
    assert (s.read_failures, s.write_failures) == (1, 0)
    s.students()
    assert s.op_failures == 0


def test_probe_sees_lock_and_missing_file(tmp_path, monkeypatch):                      # Д5-R28, р4
    monkeypatch.setattr(accounts, "SQLITE_TIMEOUT_S", 0.1)
    s = store(tmp_path)
    other = sqlite3.connect(tmp_path / "core.db", isolation_level=None)
    other.execute("BEGIN EXCLUSIVE")                                                   # блокировка дольше срока
    assert s.ping() is False
    other.execute("ROLLBACK"); other.close()
    assert s.ping() is True
    for f in tmp_path.glob("core.db*"):
        f.unlink()
    assert s.ping() is False and not (tmp_path / "core.db").exists()                   # mode=rw: пустую базу не создаёт


def test_unknown_failures_thread_safe(tmp_path):                                       # Ш23
    s, errors = store(tmp_path), []
    def hammer(k):
        try:
            for i in range(2000):
                s.failure(f"nobody-{k}-{i}"); s.locked(f"nobody-{k}-{i // 2}")
        except Exception as e:                                                         # исключение потока pytest не видит
            errors.append(type(e).__name__)
    threads = [threading.Thread(target=hammer, args=(k,)) for k in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == [] and len(s.unknown_failures) == UNKNOWN_KEYS


def test_remove_only_disabled(tmp_path):
    s = store(tmp_path)
    s.add("teach", "teacher", None, PW)
    with pytest.raises(ValueError):
        s.remove("teach")
    s.disable("teach"); s.remove("teach")
    assert s.list() == []


# --- Д10б, задача Б1: AccountStore.enable (Д10-R108) ---

def test_enable_rules(tmp_path):
    s = store(tmp_path)
    s.add("stu-a", "student", 3, PW)
    with pytest.raises(ValueError, match="уже включена"):
        s.enable("stu-a")
    s.disable("stu-a")
    assert s.verify("stu-a", PW) is None
    s.enable("stu-a")
    assert s.verify("stu-a", PW).place == 3
    s.add("teach2", "teacher", None, PW)
    s.disable("teach2")
    with pytest.raises(ValueError, match="только учётку студента"):
        s.enable("teach2")
    with pytest.raises(ValueError, match="учётки nobody нет"):
        s.enable("nobody")


def test_enable_only_own_free_place(tmp_path):                                         # сверх брифа: R108, место — своё
    s = store(tmp_path)
    s.add("d10-probe-a", "student", 4, PW)
    s.disable("d10-probe-a"); s.release_place(4)                                       # закрепление снято
    with pytest.raises(ValueError, match="место 04 не закреплено за учёткой d10-probe-a"):
        s.enable("d10-probe-a")
    s.add("stu-b", "student", 4, PW)                                                   # место отдано другому
    s.disable("stu-b")
    with pytest.raises(ValueError, match="место 04 не закреплено за учёткой d10-probe-a"):
        s.enable("d10-probe-a")
    s.add("stu-c", "student", 5, PW)
    s.disable("stu-c")
    with closing(sqlite3.connect(tmp_path / "core.db")) as con, con:                   # мимо add: действующая на 05
        con.execute("INSERT INTO gw_accounts (login, role, place, pw_hash, disabled, created_at) SELECT 'stu-x',"
                    " 'student', 5, pw_hash, 0, created_at FROM gw_accounts WHERE login = 'stu-c'")
    with pytest.raises(ValueError, match="место 05 занято другой учёткой"):
        s.enable("stu-c")
    assert [(a.login, a.disabled) for a in s.list() if a.login in ("d10-probe-a", "stu-b", "stu-c")] == [
        ("d10-probe-a", True), ("stu-b", True), ("stu-c", True)]                       # отказы ничего не записали
    assert s.write_failures == 0 and s.read_failures == 0                              # отказ по правилам — не сбой


def test_student_only_ops_refuse_other_roles(tmp_path):                                  # ревью Б1+Б2, M-1 (TOCTOU роли)
    """set_password, disable и unlock с student_only (так их зовёт кабинет) — роль цели сверяется в транзакции изменения:
    у преподавателя и неизвестного логина ValueError, ничего не меняется (пароль, состояние, замок, сессии), счётчики
    сбоев не растут. Учётке студента — как без флага; команда администратора (без флага) — как прежде."""
    s = store(tmp_path)
    s.add("teach", "teacher", None, PW)
    s.add("stu-a", "student", 3, PW)
    token = s.open_session("teach")
    for login in ("teach", "stu-a"):
        for _ in range(LOCK_FAILURES):
            s.failure(login)
    for login in ("teach", "nobody"):
        for op in (lambda: s.set_password(login, "другой-пароль-1", student_only=True),
                   lambda: s.disable(login, student_only=True), lambda: s.unlock(login, student_only=True)):
            with pytest.raises(ValueError, match=f"^учётки студента {login} нет$"):
                op()
    assert s.status("teach") == "active" and s.locked("teach") and s.session(token) is not None
    assert s.verify("teach", PW) is not None and not s.exists("nobody")
    assert s.write_failures == 0 and s.read_failures == 0                              # отказ по правилам — не сбой
    s.unlock("stu-a", student_only=True)
    assert not s.locked("stu-a")
    s.set_password("stu-a", "другой-пароль-1", student_only=True)
    assert s.verify("stu-a", "другой-пароль-1") is not None
    s.disable("stu-a", student_only=True)
    assert s.status("stu-a") == "disabled"
    s.unlock("teach"); s.set_password("teach", "другой-пароль-2"); s.disable("teach")   # команда — без флага
    assert not s.locked("teach") and s.status("teach") == "disabled" and s.session(token) is None


def test_cli_enable(tmp_path, capsys, monkeypatch):                                    # сверх брифа: команда enable
    monkeypatch.setenv("DB_PATH", str(tmp_path / "core.db"))
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    assert accounts_main(["add", "--login", "ivanov", "--role", "student", "--place", "3"]) == 0
    assert accounts_main(["disable", "--login", "ivanov"]) == 0
    assert accounts_main(["enable", "--login", "ivanov"]) == 0
    assert capsys.readouterr().out == ""
    assert accounts_main(["enable", "--login", "ivanov"]) == 1
    err = capsys.readouterr()
    assert err.err.strip() == "учётка ivanov уже включена" and err.out == ""
    assert store(tmp_path).verify("ivanov", PW).place == 3


def test_cli(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "core.db"))
    assert accounts_main(["add", "--login", "ivanov", "--role", "student", "--place", "3", "--generate"]) == 0
    pw = capsys.readouterr().out.strip()
    assert len(pw) == 12
    monkeypatch.setattr("sys.stdin", io.StringIO("пароль-из-stdin\n"))
    assert accounts_main(["add", "--login", "teach", "--role", "teacher"]) == 0
    assert accounts_main(["list"]) == 0
    out = capsys.readouterr().out
    assert "ivanov\tstudent\t03\tактивна" in out and "teach\tteacher\t-\tактивна" in out
    assert "место 03\tivanov\tучётка есть" in out and pw not in out and "scrypt" not in out
    assert accounts_main(["disable", "--login", "ivanov"]) == 0
    assert accounts_main(["add", "--login", "petrov", "--role", "student", "--place", "3", "--generate"]) == 1
    err = capsys.readouterr()
    assert "место 03 закреплено за учёткой ivanov" in err.err and err.out == ""
    assert accounts_main(["release-place", "--place", "3", "--yes"]) == 1
    assert accounts_main(["unlock", "--login", "ivanov"]) == 0


# --- предупреждение при первом входе (Д6б, Д6-R49) ---

# схема core.db Д5 (Д5а–Д5б) — копия _SCHEMA Д5, без gw_notices: база выкладки Д5 открывается как есть
D5_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS gw_accounts (
        login TEXT PRIMARY KEY,
        role TEXT NOT NULL CHECK (role IN ('student', 'teacher')),
        place INTEGER,
        pw_hash TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    ("CREATE UNIQUE INDEX IF NOT EXISTS gw_accounts_active_place ON gw_accounts (place)"
     " WHERE disabled = 0 AND place IS NOT NULL"),
    """CREATE TABLE IF NOT EXISTS gw_places (
        place INTEGER PRIMARY KEY,
        login TEXT NOT NULL,
        since TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS gw_sessions (
        token_sha TEXT PRIMARY KEY,
        login TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS gw_sessions_login ON gw_sessions (login)",
    """CREATE TABLE IF NOT EXISTS gw_login_failures (
        login TEXT NOT NULL,
        at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS gw_login_failures_login ON gw_login_failures (login, at)",
    """CREATE TABLE IF NOT EXISTS gw_heartbeat (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        at REAL NOT NULL
    )""",
)


def make_d5_db(path) -> None:
    """core.db, как её оставил core Д5: таблицы Д5, учётка stu-03 (место 3, пароль PW) с закреплением места."""
    with closing(sqlite3.connect(path)) as db:
        for statement in D5_SCHEMA:
            db.execute(statement)
        db.execute("INSERT INTO gw_accounts (login, role, place, pw_hash, disabled, created_at)"
                   " VALUES ('stu-03', 'student', 3, ?, 0, '2026-10-05T07:00:00+00:00')", (hash_password(PW),))
        db.execute("INSERT INTO gw_places (place, login, since) VALUES (3, 'stu-03', '2026-10-05T07:00:00+00:00')")
        db.commit()


def tables(path) -> set[str]:
    with closing(sqlite3.connect(path)) as db:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def notice_rows(path) -> list[tuple]:
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT login, notice FROM gw_notices ORDER BY login, notice").fetchall()


def test_store_opens_d5_db(tmp_path):                                                    # Review Focus 5 Д6б
    db = tmp_path / "core.db"; make_d5_db(db)
    assert "gw_notices" not in tables(db) and "gw_accounts" in tables(db)                # положительный контроль: база Д5
    store = AccountStore(str(db)); assert store.open(), store.last_error
    assert "gw_notices" in tables(db)
    assert store.verify("stu-03", PW) is not None                                         # учётка Д5 жива, gw_accounts не тронута
    assert store.notice_acked("stu-03", NOTICE) is False
    assert store.ack_notice("stu-03", NOTICE) is True                                     # первое — записано
    assert store.ack_notice("stu-03", NOTICE) is False                                    # повтор — без ошибки и записи
    assert store.notice_acked("stu-03", NOTICE) and not store.notice_acked("stu-03", "teacher-reads-2")
    assert notice_rows(db) == [("stu-03", NOTICE)]                                        # повтор строку не множит
    assert NOTICE == "teacher-reads-1"


def test_ack_notice_needs_active_account(tmp_path):                                     # подтверждение — только своей учётки
    s = store(tmp_path)
    s.add("stu-03", "student", 3, PW); s.add("stu-05", "student", 5, PW)
    for login in ("nobody7", "stu-05"):
        if login == "stu-05":
            s.disable("stu-05")
        with pytest.raises(ValueError, match="учётки нет или она отключена"):
            s.ack_notice(login, NOTICE)
    for login, notice in (("Не-Логин!", NOTICE), ("stu-03", ""), ("stu-03", 1)):
        with pytest.raises(ValueError):
            s.ack_notice(login, notice)
    assert notice_rows(tmp_path / "core.db") == []
    assert not s.notice_acked("nobody7", NOTICE) and not s.notice_acked("Не-Логин!", NOTICE)
    s.ack_notice("stu-03", NOTICE)                                                        # положительный контроль
    assert notice_rows(tmp_path / "core.db") == [("stu-03", NOTICE)]


def test_notice_store_errors(tmp_path, monkeypatch):                                    # сбой — StoreError, счётчики как у операций
    s = store(tmp_path)
    s.add("stu-03", "student", 3, PW)
    with full_disk(s):
        with pytest.raises(StoreError) as e:
            s.ack_notice("stu-03", NOTICE)
        assert s.write_failures == 1 and "core.db" not in str(e.value)
        assert s.notice_acked("stu-03", NOTICE) is False                                  # чтение на полном диске идёт
    s.ack_notice("stu-03", NOTICE)
    assert s.write_failures == 0 and s.notice_acked("stu-03", NOTICE)
    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    with pytest.raises(StoreError):
        s.notice_acked("stu-03", NOTICE)
    assert s.read_failures == 1


def test_list_shows_notice_column(tmp_path, capsys, monkeypatch):                       # «да»/«нет» у студентов, «-» у преподавателя
    monkeypatch.setenv("DB_PATH", str(tmp_path / "core.db"))
    s = store(tmp_path)
    s.add("stu-03", "student", 3, PW); s.add("stu-05", "student", 5, PW); s.add("teach", "teacher", None, PW)
    s.ack_notice("stu-05", NOTICE)
    s.ack_notice("stu-03", "teacher-reads-0")                                             # прежняя редакция — не в счёт
    s.ack_notice("teach", NOTICE)
    assert accounts_main(["list"]) == 0
    rows = [line.split("\t") for line in capsys.readouterr().out.splitlines() if not line.startswith("место ")]
    assert rows == [["stu-03", "student", "03", "активна", "нет"],
                    ["stu-05", "student", "05", "активна", "да"],
                    ["teach", "teacher", "-", "активна", "-"]]


def test_remove_clears_notices(tmp_path):                                                # раунд 2, Minor 6
    s = store(tmp_path)
    s.add("d6-probe-n", "student", 9, PW); s.add("stu-03", "student", 3, PW)
    s.ack_notice("d6-probe-n", NOTICE); s.ack_notice("stu-03", NOTICE)
    assert s.notice_acked("d6-probe-n", NOTICE)                                           # положительный контроль
    s.disable("d6-probe-n"); s.remove("d6-probe-n")
    assert s.notice_acked("d6-probe-n", NOTICE) is False
    assert notice_rows(tmp_path / "core.db") == [("stu-03", NOTICE)]                      # чужие строки на месте
    s.add("d6-probe-n", "student", 9, PW, same_person=True)                               # новая учётка с тем же логином —
    assert s.notice_acked("d6-probe-n", NOTICE) is False                                  # спросит снова


# --- освобождение места пробной учётки: агенты места (Д7-R61, задача А3) ---

AGENTS_LEFT = "на месте остались агенты — сначала python -m pcbk_core.agents.admin archive --place 10 --yes"


@pytest.fixture
def agents_root(tmp_path, monkeypatch):
    """Корень агентов и core.db в окружении команды: accounts.main берёт Settings.from_env() (раунд 2, m-н2)."""
    root = make_agents_root(tmp_path / "agents")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "core.db"))
    monkeypatch.setenv("AGENTS_ROOT", str(root))
    return root


@pytest.fixture
def probe_off_on_10(tmp_path, agents_root):
    """Пробная учётка d7-probe-a на месте 10, отключена."""
    s = store(tmp_path)
    s.add("d7-probe-a", "student", 10, PW); s.disable("d7-probe-a")
    return s


@pytest.fixture
def agent_on_10(tmp_path, agents_root):
    """У места 10 сохранён агент (строка учёта и файл)."""
    a = AgentStore(str(tmp_path / "core.db"), str(agents_root))
    assert a.open(), a.last_error
    assert a.save(10, AGENT_DRAFT, agent_text(caller="student-10"), AGENT_NOW) == "saved"
    return a


def test_release_place_refused_with_agents(probe_off_on_10, agent_on_10, capsys):
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 1
    assert "на месте остались агенты — сначала python -m pcbk_core.agents.admin archive --place 10 --yes" \
        in capsys.readouterr().err
    admin.main(["archive", "--place", "10", "--yes"])
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 0

def test_release_place_refused_with_stray_file(probe_off_on_10, agents_root, capsys):     # I2: файл без строки учёта
    (agents_root / "student-10" / "manual.md").write_text("x")
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 1
    assert AGENTS_LEFT in capsys.readouterr().err                                       # m21: отказ — по агентам
    (agents_root / "student-10" / "manual.md").unlink()
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 0               # положительный контроль


def test_release_place_refused_when_agents_dir_unreadable(probe_off_on_10, agents_root, capsys):
    (agents_root / "student-10").rmdir()                                                 # каталога места нет
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 1
    assert "каталог агентов места недоступен" in capsys.readouterr().err
    assert probe_off_on_10.bindings() == [(10, "d7-probe-a", True)]                       # закрепление на месте


def test_release_not_probe_place_refused_before_agents(tmp_path, agents_root, capsys):
    """Место не пробной учётки с агентами: отказ по учётке, а не подсказка «сначала archive» — иначе администратор
    унёс бы в архив агентов настоящего студента, а место всё равно не освободилось бы."""
    s = store(tmp_path); s.add("ivanov", "student", 10, PW); s.disable("ivanov")
    (agents_root / "student-10" / "manual.md").write_text("x")
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "освобождать можно только места пробных учёток после disable" in err and "archive" not in err


def test_release_place_refused_with_quarantined_row(probe_off_on_10, agent_on_10, capsys):   # доводка р1, M1
    assert agent_on_10.quarantine(10, "sushka-bdm2.md", AGENT_NOW) == "sushka-bdm2"           # файла в месте нет
    assert list(agent_on_10.place_dir(10).iterdir()) == []
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 1
    assert AGENTS_LEFT in capsys.readouterr().err
    assert admin.main(["archive", "--place", "10", "--yes"]) == 0                              # строку уносит archive
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 0


def test_release_place_refused_when_agent_store_does_not_open(probe_off_on_10, agents_root, capsys,
                                                              monkeypatch):                    # доводка р1, M17
    monkeypatch.setattr(AgentStore, "open", lambda self: False)
    assert accounts.main(["release-place", "--place", "10", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "учёт агентов не открывается — освобождать место нельзя" in err and len(err.splitlines()) == 1
    assert probe_off_on_10.bindings() == [(10, "d7-probe-a", True)]                            # закрепление на месте


# --- Д10а, задача А2: команда journal (I-9); DB_PATH — через monkeypatch.setenv (раунд 2 Д7, m-н2) ---

def test_journal_command_prints_codes_only(tmp_path, monkeypatch, capsys):
    db = tmp_path / "core.db"
    monkeypatch.setenv("DB_PATH", str(db))
    s = Shares(str(db), total_usd=1000.0, default_usd=100.0)
    assert s.open()
    s.apply([(3, 10000, 9000), (5, 10000, 11000)], by="teach", now=datetime(2026, 10, 11, 9, tzinfo=timezone.utc))
    log = TeacherLog(str(db))
    assert log.open()
    log.requested("teach", "read", 3, "ses_" + "A" * 26)
    assert accounts_main(["journal", "--last", "5"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split("\t")[1:] == ["teach", "read", "03", "ses_" + "A" * 26, "requested", "-"]
    assert lines[1].split("\t")[1:] == ["teach", "shares", "-", "-", "ok", '{"03": [10000, 9000], "05": [10000, 11000]}']
    assert len(lines) == 2
    assert accounts_main(["journal", "--last", "0"]) == 1                     # число строк — от 1


def test_journal_command_reads_only_and_limits(tmp_path, monkeypatch, capsys):   # сверх брифа: mode=ro, 1…1000, 50
    db = tmp_path / "core.db"
    monkeypatch.setenv("DB_PATH", str(db))
    assert accounts_main(["journal"]) == 1                                    # базы нет — не заводит пустую
    err = capsys.readouterr().err
    assert not db.exists() and len(err.splitlines()) == 1 and err.startswith("журнал действий: ")
    log = TeacherLog(str(db))
    assert log.open()
    for i in range(60):
        log.requested("teach", "sessions", 1 + i % 10, None)
    assert accounts_main(["journal"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 50 and out[0].split("\t")[0].endswith("+00:00")       # по умолчанию 50, время — UTC
    assert [line.split("\t")[3] for line in out[:2]] == ["10", "09"]          # новые первыми
    for bad in (["--last", "1001"], ["--last", "-1"], ["--last", "x"]):
        assert accounts_main(["journal", *bad]) == 1, bad
    assert len(capsys.readouterr().err.splitlines()) == 3
    assert accounts_main(["journal", "--last", "1000"]) == 0
    with closing(sqlite3.connect(db)) as c:                                    # учётки команда не трогала
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name = 'gw_accounts'").fetchone()[0] == 0
