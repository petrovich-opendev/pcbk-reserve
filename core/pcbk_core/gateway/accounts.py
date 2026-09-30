"""Учётные записи шлюза (Д5-R24, Д5-R28): учётки, закрепление мест, надгробия, сессии, замок входа.

Таблицы в core.db — файл общий с журналом событий Д3б и журналом вызовов Д4а:
- gw_accounts — учётка: логин, роль, место, хеш пароля scrypt, признак отключения. Место уникально
  среди действующих учёток (частичный индекс);
- gw_places — за кем закреплено место. Строка пишется при первой выдаче места и не удаляется ни
  disable, ни remove: она же надгробие логина — логин с закреплением без учётки занят. Снимает её
  только release_place: у пробных учёток dN-probe-* и без действующей учётки на месте;
- gw_sessions — сессии: sha256 значения cookie, сам токен в базе не лежит. Срок — от открытия;
- gw_login_failures — неудачные входы существующих логинов в окне LOCK_WINDOW_S. Неизвестные
  логины — только в памяти (OrderedDict на UNKNOWN_KEYS ключей под threading.Lock, ключ — sha256
  логина): замок для всех одинаков, перебор чужих логинов не растит базу и память.

Договор Role: конструктор только запоминает; файл, WAL и таблицы — open() (роль зовёт её через
asyncio.to_thread в начале lifespan). Соединение — на каждую операцию, операции идут из потоков.
Операции открывают файл с mode=rw: пропавший файл не превращается в пустую «исправную» базу, создаёт
её только open(). Сбой SQLite в операции — StoreError без пути и значений, op_failures растёт,
удачная операция его обнуляет. ping() — проба записи со своим счётчиком probe_failures; op_failures
она не трогает (Д5-R28). scrypt — вне открытой транзакции записи: core.db делят журналы, их запись
ждёт блокировку не дольше 0,5 с (Г14).

Пароль не попадает ни в базу (только хеш), ни в тексты исключений, ни в журнал. Команда
администратора — main(): python -m pcbk_core.gateway.accounts.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import logging
import re
import secrets
import sqlite3
import sys
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pcbk_core.settings import Settings

log = logging.getLogger("pcbk_core.gateway.accounts")

LOGIN_RE = re.compile(r"[a-z][a-z0-9._-]{2,31}")
PROBE_LOGIN = re.compile(r"d[0-9]+[a-z]?-probe-[a-z0-9-]+")
PASSWORD_MIN = 8
PASSWORD_MAX = 128
PLACES = range(1, 11)
LOCK_FAILURES = 5
LOCK_WINDOW_S = 900.0
UNKNOWN_KEYS = 10000
SQLITE_TIMEOUT_S = 2.0

ROLES = ("student", "teacher")
SESSION_TOKEN_BYTES = 32           # secrets.token_urlsafe(32) — 43 знака
GENERATED_PW_BYTES = 9             # --generate: secrets.token_urlsafe(9) — 12 знаков
# scrypt по Д5-R24: n = 2**14, r = 8, p = 1, соль 16 байт, хеш 32 байта
SCRYPT_N, SCRYPT_R, SCRYPT_P = 16384, 8, 1
SALT_BYTES = 16
HASH_BYTES = 32
_HASH_PREFIX = f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
_HASH_TAIL = re.compile(r"([0-9a-f]{32})\$([0-9a-f]{64})")
# ключ пробной строки ping(): не проходит LOGIN_RE, со строками логинов не совпадёт; строка откатывается
_PROBE_KEY = "~ping"
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]{1,40}")

_LOGIN_RULE = "логин: 3…32 знака из a-z, 0-9, «.», «_», «-», первый — буква a-z"
_RELEASE_REFUSED = "освобождать можно только места пробных учёток после disable"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS gw_accounts (
        login TEXT PRIMARY KEY,
        role TEXT NOT NULL CHECK (role IN ('student', 'teacher')),
        place INTEGER,
        pw_hash TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    # место уникально среди действующих учёток: отключённая учётка место держит закреплением, а не индексом
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
)
_ACCOUNT = "SELECT login, role, place, disabled, pw_hash FROM gw_accounts WHERE login = ?"


# --- пароли ---

def _pw_bytes(password: str) -> bytes:
    # одиночный суррогат (JSON «\udc80», stdin с surrogateescape) кодируется одинаково при заведении и сверке
    return password.encode("utf-8", "surrogatepass")


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(_pw_bytes(password), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=HASH_BYTES)


def hash_password(password: str) -> str:
    """Хеш по Д5-R24: scrypt$16384$8$1$<соль 16 байт hex>$<32 байта hex>; соль — своя у каждого вызова."""
    salt = secrets.token_bytes(SALT_BYTES)
    return f"{_HASH_PREFIX}{salt.hex()}${_scrypt(password, salt).hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Сверка пароля с хешем hash_password — hmac.compare_digest. Не строка или хеш не того вида — False.

    Модульная функция: AccountStore.verify зовёт её по имени модуля (её подменяет тест задачи А4).
    """
    if not isinstance(password, str) or not isinstance(stored, str) or not stored.startswith(_HASH_PREFIX):
        return False
    m = _HASH_TAIL.fullmatch(stored, len(_HASH_PREFIX))
    if m is None:
        return False
    return hmac.compare_digest(_scrypt(password, bytes.fromhex(m[1])), bytes.fromhex(m[2]))


_dummy: str | None = None
_dummy_lock = threading.Lock()


def _dummy_hash() -> str:
    """Хеш-пустышка: неизвестный логин сверяется по нему, и ответ идёт столько же времени. Считается один раз
    на процесс (open() считает его заранее, не первый вход)."""
    global _dummy
    if _dummy is None:
        with _dummy_lock:
            if _dummy is None:
                _dummy = hash_password(secrets.token_urlsafe(16))
    return _dummy


def _check_login(login: object) -> None:
    # в текст отказа логин не идёт: по ошибке в поле логина мог попасть пароль
    if not isinstance(login, str) or LOGIN_RE.fullmatch(login) is None:
        raise ValueError(_LOGIN_RULE)


def _check_password(password: object) -> None:
    if not isinstance(password, str):
        raise ValueError("пароль: нужна строка")
    if len(password) < PASSWORD_MIN:
        raise ValueError(f"пароль короче {PASSWORD_MIN} знаков")
    if len(password) > PASSWORD_MAX:
        raise ValueError(f"пароль длиннее {PASSWORD_MAX} знаков")


def _nn(place: int | None) -> str:
    """Место двумя цифрами («03»), у преподавателя — «-»."""
    return "-" if place is None else f"{place:02d}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


def _key(login: object) -> bytes:
    """Ключ счётчика неизвестных логинов: sha256 — память не держит ни длинных строк, ни пароля, набранного
    в поле логина."""
    text = login if isinstance(login, str) else repr(login)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest()


# --- хранилище ---

@dataclass(frozen=True)
class Account:
    login: str
    role: Literal["student", "teacher"]
    place: int | None
    disabled: bool

    def to_json(self) -> dict:
        """{"login", "role", "place"}: место — строка из двух цифр («03») или None."""
        return {"login": self.login, "role": self.role, "place": None if self.place is None else _nn(self.place)}


class StoreError(Exception):
    """Сбой SQLite в операции хранилища. Текст — класс ошибки и код SQLite, без пути и значений."""


def _store_error(exc: sqlite3.Error) -> StoreError:
    code = getattr(exc, "sqlite_errorname", None)
    code = code if isinstance(code, str) and _SQLITE_CODE.fullmatch(code) else None
    return StoreError(f"учётные записи: {type(exc).__name__}" + (f" {code}" if code else ""))


class AccountStore:
    """Учётки, закрепления мест, сессии и замок входа в core.db. Конструктор только запоминает.

    Поля для health() роли — из памяти: op_failures — сбоев настоящих операций подряд (снимает удачная
    операция), probe_failures — сбоев ping() подряд (снимает удачная проба), last_error — класс последней
    ошибки. unknown_failures — счётчик неизвестных логинов: sha256 логина → метки неудачных входов.
    """

    def __init__(self, db_path: str, *, ttl_s: float = 43200.0, clock: Callable[[], float] = time.time):
        self.path = db_path
        self.ttl_s = ttl_s
        self.clock = clock
        self.op_failures = 0
        self.probe_failures = 0
        self.last_error: str | None = None
        self.unknown_failures: OrderedDict[bytes, deque[float]] = OrderedDict()
        self._unknown_lock = threading.Lock()
        self._state_lock = threading.Lock()

    # --- SQLite: соединение на операцию ---

    def _connect(self, create: bool = False) -> sqlite3.Connection:
        """Создаёт файл только create (open(), mode=rwc); операции и проба — mode=rw. Срок блокировки —
        SQLITE_TIMEOUT_S модуля в момент вызова; режим автофиксации, транзакции — явные."""
        uri = f"{Path(self.path).absolute().as_uri()}?mode={'rwc' if create else 'rw'}"
        return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S, isolation_level=None)

    @contextmanager
    def _op(self) -> Iterator[sqlite3.Connection]:
        """Соединение настоящей операции. Сбой SQLite — op_failures, last_error и StoreError без текста
        SQLite; удача — op_failures = 0. Отказ по правилам (ValueError) счётчик не трогает."""
        try:
            db = self._connect()
            try:
                yield db
            finally:
                db.close()          # незавершённая транзакция откатывается при закрытии
        except sqlite3.Error as exc:
            with self._state_lock:
                self.op_failures += 1
                self.last_error = type(exc).__name__
            raise _store_error(exc) from None
        with self._state_lock:
            self.op_failures = 0

    def open(self) -> bool:
        """Файл, WAL, таблицы и индексы; идемпотентна. Сбой не бросает: last_error и False.

        Ввод-вывод до SQLITE_TIMEOUT_S и один scrypt (хеш-пустышка) — не в цикле событий: роль зовёт
        через asyncio.to_thread.
        """
        try:
            db = self._connect(create=True)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA:
                    db.execute(statement)
                db.execute("COMMIT")
            finally:
                db.close()
            _dummy_hash()
        except Exception as exc:
            with self._state_lock:
                self.last_error = type(exc).__name__
            return False
        return True

    def ping(self) -> bool:
        """Проба записи (Д5-R28): mode=rw, SELECT count(*), BEGIN IMMEDIATE, INSERT пробной строки, ROLLBACK.

        Не бросает. Итог — probe_failures (удача обнуляет) и last_error; op_failures не трогает: сбой
        настоящей операции снимает только удачная операция.
        """
        try:
            db = self._connect()
            try:
                db.execute("SELECT count(*) FROM gw_accounts").fetchone()
                db.execute("BEGIN IMMEDIATE")
                # одного BEGIN IMMEDIATE мало: на файле только для чтения он проходит
                db.execute("INSERT INTO gw_login_failures (login, at) VALUES (?, 0)", (_PROBE_KEY,))
                db.execute("ROLLBACK")
            finally:
                db.close()
        except Exception as exc:
            with self._state_lock:
                self.probe_failures += 1
                self.last_error = type(exc).__name__
            return False
        with self._state_lock:
            self.probe_failures = 0
        return True

    # --- учётки и места ---

    def add(self, login: str, role: str, place: int | None, password: str, *,
            same_person: bool = False) -> Account:
        """Новая учётка. Отказ — ValueError с русским текстом без пароля.

        Место, закреплённое за другим логином, не выдаётся; логин удалённой учётки (надгробие) — только
        с same_person, со строкой журнала. Первая выдача места пишет gw_places.
        """
        _check_login(login)
        if role not in ROLES:
            raise ValueError("роль: student или teacher")
        if role == "student":
            if type(place) is not int or place not in PLACES:
                raise ValueError("у студента должно быть место 1…10")
        elif place is not None:
            raise ValueError("у преподавателя места нет")
        _check_password(password)
        pw_hash = hash_password(password)           # scrypt — до транзакции записи (Г14)
        now = _iso(self.clock())
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM gw_accounts WHERE login = ?", (login,)).fetchone() is not None:
                raise ValueError(f"логин: {login} уже есть")
            held = db.execute("SELECT min(place) FROM gw_places WHERE login = ?", (login,)).fetchone()[0]
            if held is not None and not same_person:
                raise ValueError(f"логин {login} был у студента места {_nn(held)} — новому человеку нужен новый логин")
            if place is not None:
                if db.execute("SELECT 1 FROM gw_accounts WHERE place = ? AND disabled = 0",
                              (place,)).fetchone() is not None:
                    raise ValueError(f"место {_nn(place)} занято")
                bound = db.execute("SELECT login FROM gw_places WHERE place = ?", (place,)).fetchone()
                if bound is not None and bound[0] != login:
                    raise ValueError(f"место {_nn(place)} закреплено за учёткой {bound[0]} — смена студента в месте"
                                     " только по процедуре владельца")
                if bound is None:
                    db.execute("INSERT INTO gw_places (place, login, since) VALUES (?, ?, ?)", (place, login, now))
            db.execute("INSERT INTO gw_accounts (login, role, place, pw_hash, disabled, created_at)"
                       " VALUES (?, ?, ?, ?, 0, ?)", (login, role, place, pw_hash, now))
            db.execute("COMMIT")
        if held is not None:
            log.info("gw account readd login=%s place=%s", login, _nn(place))
        return Account(login, role, place, False)

    def set_password(self, login: str, password: str) -> None:
        """Новый пароль: сессии учётки закрываются, замок снимается."""
        _check_login(login)
        _check_password(password)
        pw_hash = hash_password(password)           # scrypt — до транзакции записи (Г14)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE gw_accounts SET pw_hash = ? WHERE login = ?", (pw_hash, login)).rowcount != 1:
                raise ValueError(f"учётки {login} нет")
            db.execute("DELETE FROM gw_sessions WHERE login = ?", (login,))
            db.execute("DELETE FROM gw_login_failures WHERE login = ?", (login,))
            db.execute("COMMIT")
        self._forget_unknown(login)

    def disable(self, login: str) -> None:
        """Учётка отключается, её сессии закрываются; закрепление места остаётся."""
        _check_login(login)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE gw_accounts SET disabled = 1 WHERE login = ?", (login,)).rowcount != 1:
                raise ValueError(f"учётки {login} нет")
            db.execute("DELETE FROM gw_sessions WHERE login = ?", (login,))
            db.execute("COMMIT")

    def remove(self, login: str) -> None:
        """Удаляет только отключённую учётку; gw_places не трогает — логин остаётся за местом (надгробие)."""
        _check_login(login)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT disabled FROM gw_accounts WHERE login = ?", (login,)).fetchone()
            if row is None:
                raise ValueError(f"учётки {login} нет")
            if not row[0]:
                raise ValueError("учётка активна — сначала disable")
            db.execute("DELETE FROM gw_accounts WHERE login = ?", (login,))
            db.execute("DELETE FROM gw_sessions WHERE login = ?", (login,))
            db.execute("DELETE FROM gw_login_failures WHERE login = ?", (login,))
            db.execute("COMMIT")

    def release_place(self, place: int) -> None:
        """Снимает закрепление места — только за пробной учёткой (PROBE_LOGIN) и без действующей учётки на месте."""
        if type(place) is not int or place not in PLACES:
            raise ValueError("место: 1…10")
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            bound = db.execute("SELECT login FROM gw_places WHERE place = ?", (place,)).fetchone()
            active = db.execute("SELECT 1 FROM gw_accounts WHERE place = ? AND disabled = 0", (place,)).fetchone()
            if bound is None or PROBE_LOGIN.fullmatch(bound[0]) is None or active is not None:
                raise ValueError(_RELEASE_REFUSED)
            db.execute("DELETE FROM gw_places WHERE place = ?", (place,))
            db.execute("COMMIT")

    def list(self) -> list[Account]:
        """Все учётки, в том числе отключённые: студенты по местам, затем преподаватели."""
        with self._op() as db:
            rows = db.execute("SELECT login, role, place, disabled FROM gw_accounts"
                              " ORDER BY place IS NULL, place, login").fetchall()
        return [Account(login, role, place, bool(disabled)) for login, role, place, disabled in rows]

    def bindings(self) -> list[tuple[int, str, bool]]:
        """Закрепления мест: (место, логин, есть ли учётка), в том числе за удалёнными логинами."""
        with self._op() as db:
            rows = db.execute("SELECT p.place, p.login, EXISTS (SELECT 1 FROM gw_accounts a WHERE a.login = p.login)"
                              " FROM gw_places p ORDER BY p.place").fetchall()
        return [(place, login, bool(has)) for place, login, has in rows]

    def students(self) -> int:
        """Действующих учёток студентов."""
        with self._op() as db:
            return db.execute("SELECT count(*) FROM gw_accounts WHERE role = 'student' AND disabled = 0").fetchone()[0]

    # --- вход и сессии ---

    def verify(self, login: str, password: str) -> Account | None:
        """Учётка при верном пароле; неверный пароль, отключённая или неизвестная учётка — None.

        Неизвестный логин сверяется по хешу-пустышке: ответ идёт столько же времени. scrypt — после
        закрытия соединения.
        """
        row = None
        if isinstance(login, str) and LOGIN_RE.fullmatch(login) is not None:
            with self._op() as db:
                row = db.execute(_ACCOUNT, (login,)).fetchone()
        ok = verify_password(password, row[4] if row is not None else _dummy_hash())
        if row is None or row[3] or not ok:
            return None
        return Account(row[0], row[1], row[2], False)

    def exists(self, login: str) -> bool:
        """Есть ли учётка с таким логином (отключённая — тоже)."""
        if not isinstance(login, str) or LOGIN_RE.fullmatch(login) is None:
            return False
        with self._op() as db:
            return db.execute("SELECT 1 FROM gw_accounts WHERE login = ?", (login,)).fetchone() is not None

    def open_session(self, login: str) -> str:
        """Новая сессия действующей учётки: токен (secrets.token_urlsafe(32)) — вызывающему, в базу — его sha256.
        Заодно удаляются истёкшие сессии. Нет учётки или она отключена — ValueError."""
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        now = self.clock()
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM gw_sessions WHERE expires_at <= ?", (now,))
            added = db.execute("INSERT INTO gw_sessions (token_sha, login, created_at, expires_at)"
                               " SELECT ?, login, ?, ? FROM gw_accounts WHERE login = ? AND disabled = 0",
                               (_sha(token), now, now + self.ttl_s, login)).rowcount
            if added != 1:
                raise ValueError("учётки нет или она отключена")
            db.execute("COMMIT")
        return token

    def session(self, token: str | None) -> Account | None:
        """Учётка сессии; нет токена, нет сессии, отключённая учётка — None. Истёкшая сессия удаляется."""
        if not isinstance(token, str) or not token:
            return None
        sha, now = _sha(token), self.clock()
        with self._op() as db:
            row = db.execute("SELECT s.expires_at, a.login, a.role, a.place, a.disabled FROM gw_sessions s"
                             " LEFT JOIN gw_accounts a ON a.login = s.login WHERE s.token_sha = ?", (sha,)).fetchone()
            if row is None:
                return None
            if row[0] <= now:
                db.execute("DELETE FROM gw_sessions WHERE token_sha = ?", (sha,))
                return None
        _, login, role, place, disabled = row
        if login is None or disabled:
            return None
        return Account(login, role, place, False)

    def close_session(self, token: str | None) -> None:
        """Закрывает сессию по токену; неизвестный токен — ничего."""
        if not isinstance(token, str) or not token:
            return
        with self._op() as db:
            db.execute("DELETE FROM gw_sessions WHERE token_sha = ?", (_sha(token),))

    # --- замок входа ---

    def locked(self, login: str) -> bool:
        """LOCK_FAILURES неудачных входов за LOCK_WINDOW_S — замок. Существующий логин — по таблице (переживает
        перезапуск), прочие — по памяти; ответ для обоих одинаков (нет оракула)."""
        now = self.clock()
        if isinstance(login, str) and LOGIN_RE.fullmatch(login) is not None:
            with self._op() as db:
                if db.execute("SELECT 1 FROM gw_accounts WHERE login = ?", (login,)).fetchone() is not None:
                    n = db.execute("SELECT count(*) FROM gw_login_failures WHERE login = ? AND at > ?",
                                   (login, now - LOCK_WINDOW_S)).fetchone()[0]
                    return n >= LOCK_FAILURES
        with self._unknown_lock:
            stamps = self.unknown_failures.get(_key(login))
            return (stamps is not None and len(stamps) >= LOCK_FAILURES
                    and stamps[-LOCK_FAILURES] > now - LOCK_WINDOW_S)

    def failure(self, login: str) -> None:
        """Неудачный вход. Существующий логин — строка таблицы (в ней только окно и не больше LOCK_FAILURES
        строк логина), прочие — память: UNKNOWN_KEYS ключей, вытесняется тот, чей последний неудачный вход
        старше всех."""
        now = self.clock()
        if isinstance(login, str) and LOGIN_RE.fullmatch(login) is not None:
            with self._op() as db:
                if db.execute("SELECT 1 FROM gw_accounts WHERE login = ?", (login,)).fetchone() is not None:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("INSERT INTO gw_login_failures (login, at) VALUES (?, ?)", (login, now))
                    db.execute("DELETE FROM gw_login_failures WHERE at <= ?", (now - LOCK_WINDOW_S,))
                    db.execute("DELETE FROM gw_login_failures WHERE login = ? AND rowid NOT IN (SELECT rowid"
                               " FROM gw_login_failures WHERE login = ? ORDER BY at DESC, rowid DESC LIMIT ?)",
                               (login, login, LOCK_FAILURES))
                    db.execute("COMMIT")
                    return
        key = _key(login)
        with self._unknown_lock:
            stamps = self.unknown_failures.get(key)
            if stamps is None:
                stamps = self.unknown_failures[key] = deque(maxlen=LOCK_FAILURES)
                while len(self.unknown_failures) > UNKNOWN_KEYS:
                    self.unknown_failures.popitem(last=False)
            else:
                self.unknown_failures.move_to_end(key)
            stamps.append(now)

    def unlock(self, login: str) -> None:
        """Снимает замок логина: строки таблицы и счётчик в памяти."""
        if isinstance(login, str) and LOGIN_RE.fullmatch(login) is not None:
            with self._op() as db:
                db.execute("DELETE FROM gw_login_failures WHERE login = ?", (login,))
        self._forget_unknown(login)

    def _forget_unknown(self, login: object) -> None:
        with self._unknown_lock:
            self.unknown_failures.pop(_key(login), None)


# --- команда администратора: python -m pcbk_core.gateway.accounts ---

class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Ошибка разбора — код 1 и одна строка в stderr (как у прочих ошибок команды), а не usage и код 2."""

    def error(self, message: str):
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m pcbk_core.gateway.accounts",
                description="Учётные записи шлюза. Пароль — одна строка stdin или --generate (печатается один раз).")
    sub = p.add_subparsers(dest="command", required=True, metavar="команда")
    add = sub.add_parser("add", help="завести учётку")
    add.add_argument("--login", required=True)
    add.add_argument("--role", required=True, choices=ROLES)
    add.add_argument("--place", type=int, help="место студента, 1…10")
    add.add_argument("--generate", action="store_true", help="породить пароль и напечатать его один раз")
    add.add_argument("--same-person", action="store_true",
                     help="логин удалённой учётки вернулся к тому же человеку")
    passwd = sub.add_parser("passwd", help="сменить пароль: сессии закрываются, замок снимается")
    passwd.add_argument("--login", required=True)
    passwd.add_argument("--generate", action="store_true", help="породить пароль и напечатать его один раз")
    disable = sub.add_parser("disable", help="отключить учётку: сессии закрываются, место остаётся за ней")
    disable.add_argument("--login", required=True)
    remove = sub.add_parser("remove", help="удалить отключённую учётку; логин остаётся за местом")
    remove.add_argument("--login", required=True)
    remove.add_argument("--yes", action="store_true")
    unlock = sub.add_parser("unlock", help="снять замок входа")
    unlock.add_argument("--login", required=True)
    release = sub.add_parser("release-place", help="освободить место пробной учётки после disable")
    release.add_argument("--place", required=True, type=int)
    release.add_argument("--yes", action="store_true")
    sub.add_parser("list", help="учётки и закрепления мест, без хешей")
    return p


def _read_password() -> str:
    """Одна строка stdin без перевода строки; stdin — терминал: getpass без эха."""
    stdin = sys.stdin
    if stdin is None:
        return ""
    try:
        if stdin.isatty():
            return getpass.getpass("пароль: ")
        line = stdin.readline()
    except EOFError:
        return ""
    except UnicodeError:
        # текст UnicodeDecodeError несёт байты пароля
        raise ValueError("пароль: stdin не читается как UTF-8") from None
    return line.removesuffix("\n").removesuffix("\r")


def _fail(message: str) -> int:
    print(message.replace("\r", " ").replace("\n", " "), file=sys.stderr)
    return 1


def _run(store: AccountStore, args: argparse.Namespace) -> int:
    command = args.command
    if command in ("add", "passwd"):
        password = secrets.token_urlsafe(GENERATED_PW_BYTES) if args.generate else _read_password()
        if command == "add":
            store.add(args.login, args.role, args.place, password, same_person=args.same_person)
        else:
            store.set_password(args.login, password)
        if args.generate:                           # только после удачи и больше ничего в stdout
            print(password, flush=True)
    elif command == "disable":
        store.disable(args.login)
    elif command == "remove":
        if not args.yes:
            raise ValueError("remove удаляет учётку — повторите с --yes")
        store.remove(args.login)
    elif command == "unlock":
        store.unlock(args.login)
    elif command == "release-place":
        if not args.yes:
            raise ValueError("release-place снимает закрепление места — повторите с --yes")
        store.release_place(args.place)
    elif command == "list":
        for a in store.list():
            print(f"{a.login}\t{a.role}\t{_nn(a.place)}\t{'отключена' if a.disabled else 'активна'}")
        for place, login, has in store.bindings():
            print(f"место {_nn(place)}\t{login}\t{'учётка есть' if has else 'удалена'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Команда администратора. База — Settings.from_env().DB_PATH, перед операцией open().

    Удача — код 0; ошибка — код 1 и одна строка в stderr без пароля. Пароль порождённый (--generate)
    печатается в stdout один раз, после удачи; больше ничего в stdout не идёт (кроме вывода list).
    """
    try:
        args = _parser().parse_args(argv)
    except _UsageError as exc:
        return _fail(f"команда: {exc}")
    try:
        path = Settings.from_env().DB_PATH
    except ValueError as exc:
        return _fail(f"настройки: {exc}")
    store = AccountStore(path)
    if not store.open():
        return _fail(f"база учётных записей не открывается: {store.last_error}")
    try:
        return _run(store, args)
    except (ValueError, StoreError) as exc:
        return _fail(str(exc))
    except Exception as exc:                        # текст непредвиденной ошибки может нести ввод — только класс
        return _fail(f"сбой команды: {type(exc).__name__}")


if __name__ == "__main__":
    from pcbk_core.logs import setup_logging

    setup_logging()                                 # строка «gw account readd …» — в stderr
    sys.exit(main())
