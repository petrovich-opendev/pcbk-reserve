"""Учётные записи шлюза (Д5-R24, Д5-R28): scrypt, место и логин закреплены навсегда, сессии хешем, замок
входа без оракула, хранилище без ввода-вывода в конструкторе, команда администратора."""
import hashlib
import io
import os
import re
import sqlite3
import threading

import pytest

from helpers import PW, FakeMono, fail_connect, store
from pcbk_core.gateway import accounts
from pcbk_core.gateway.accounts import (LOCK_FAILURES, LOCK_WINDOW_S, UNKNOWN_KEYS, AccountStore, StoreError,
                                        hash_password, verify_password)
from pcbk_core.gateway.accounts import main as accounts_main


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
