"""Журнал действий преподавателя (Д10а, Д10-R99): таблицы teacher_actions и teacher_heartbeat в core.db.

Строка пишется до доступа к данным студента (чтения, пробуждение — requested) или вместе с изменением (доля — в
транзакции Shares.apply, insert_action). Нет строки — нет действия. В строке только коды, номера мест, id разговоров,
имена агентов, логины и числа (центы): ни текстов разговоров и агентов, ни паролей — значения проверяются на входе
(ValueError: это ошибка кода, а не среды). Строки не удаляются никогда; ограничения CHECK на action нет — действия учёток
Д10б (account_*, Д10-R108) добавлены в ACTIONS без переделки таблицы.

Модуль верхнего уровня, только stdlib: его берут роль llm (доли), шлюз (кабинет) и команда администратора
(python -m pcbk_core.gateway.accounts journal) — пакет gateway он не тянет.

Договор Role: конструктор только запоминает; файл, WAL и таблицы — open() (шлюз зовёт её через asyncio.to_thread в
lifespan). Соединение — на каждую операцию (как AccountStore), запись ждёт блокировку не дольше SQLITE_TIMEOUT_S
(Г14); чтение recent() — mode=ro: пустую базу вместо пропавшей оно не заводит. Ошибка SQLite — TeacherLogError с классом
и кодом SQLite, без путей и значений.

store_ok — журнал открыт и подряд нет неудачных записей (failures). Любая удачная запись, в том числе пульс ping() в
такте уборщика, обнуляет счёт — store_ok возвращается сам; журнал, не открывшийся при запуске, пульс открывает заново
(ревью А2, m-6). Учение journal_down (DRILL_TEACHER) — любая запись (и пульс,
и строка в транзакции доли) не идёт до SQL: TeacherLogError «учения: journal_down», и счёт само не обнуляет."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .places import PLACE_MAX, place_text

log = logging.getLogger("pcbk_core.teacher_log")

JOURNAL_DRILL = "journal_down"
SQLITE_TIMEOUT_S = 2.0
RECENT_MAX = 1000                      # строк за одно чтение (команда journal --last — от 1 до 1000)

# действия кабинета: Д10а — чтения, пробуждение, доли; Д10б (R108) — учётки студентов. Словарь — договор с вебом
# (ACTION_LABELS кабинета — ровно эти ключи); при записи он не проверяется (ревью Б1+Б2, M-3)
ACTIONS = frozenset({"sessions", "read", "agents", "agent", "wake", "shares",
                     "account_add", "account_passwd", "account_disable", "account_enable", "account_unlock"})
OUTCOMES = frozenset({"requested", "ok", "refused", "error", "started"})
FINAL_OUTCOMES = frozenset({"ok", "refused", "error"})      # итог finish() строки started

ACTION_RE = re.compile(r"[a-z][a-z_]{0,31}")
LOGIN_RE = re.compile(r"[a-z][a-z0-9._-]{2,31}")            # как LOGIN_RE учёток (gateway.accounts, без импорта)
TARGET_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")            # id разговора ses_…, имя агента, логин
CODE_RE = re.compile(r"[A-Za-z0-9_.:-]{0,64}")              # строки detail — только коды
DETAIL_DEPTH = 3
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]{1,40}")

TEACHER_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS teacher_actions (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        login TEXT NOT NULL,
        action TEXT NOT NULL,
        place INTEGER,
        target TEXT,
        detail TEXT,
        outcome TEXT NOT NULL
    )""",
    # пульс ping(): одна служебная строка, её перезаписывает каждый такт уборщика шлюза
    """CREATE TABLE IF NOT EXISTS teacher_heartbeat (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        at REAL NOT NULL
    )""",
)
_INSERT = ("INSERT INTO teacher_actions (at, login, action, place, target, detail, outcome)"
           " VALUES (?, ?, ?, ?, ?, ?, ?)")
_SELECT = "SELECT id, at, login, action, place, target, detail, outcome FROM teacher_actions"


class TeacherLogError(Exception):
    """Строка журнала действий не записана (или не прочитана): SQLite или учение journal_down. Текст — класс ошибки и
    код SQLite, без путей и значений."""


def _log_error(exc: sqlite3.Error) -> TeacherLogError:
    code = getattr(exc, "sqlite_errorname", None)
    code = code if isinstance(code, str) and _SQLITE_CODE.fullmatch(code) else None
    return TeacherLogError(f"журнал действий: {type(exc).__name__}" + (f" {code}" if code else ""))


def _drill_error() -> TeacherLogError:
    return TeacherLogError(f"учения: {JOURNAL_DRILL}")


@dataclass(frozen=True)
class ActionRow:
    """Строка teacher_actions: at — ISO с поясом UTC, place — 1…11 или None, detail — разобранный JSON или None."""

    id: int
    at: str
    login: str
    action: str
    place: int | None
    target: str | None
    detail: dict | None
    outcome: str


def _at(moment: datetime) -> str:
    """ISO в UTC до секунды; время без пояса считается UTC (как ts журнала вызовов)."""
    moment = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    return moment.isoformat(timespec="seconds")


def _codes(value: object, depth: int = 0) -> None:
    """detail — только целые числа (не bool), коды CODE_RE и списки и словари из них (ключи — коды); иначе ValueError
    без значения: текст разговора, пароль или дробь сюда не попадут."""
    if depth > DETAIL_DEPTH:
        raise ValueError("detail: слишком глубоко")
    if isinstance(value, bool):
        raise ValueError("detail: флаг вместо числа")
    if isinstance(value, int):
        return
    if isinstance(value, str):
        if CODE_RE.fullmatch(value) is None:
            raise ValueError("detail: строка — не код")
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _codes(item, depth + 1)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or CODE_RE.fullmatch(key) is None:
                raise ValueError("detail: ключ — не код")
            _codes(item, depth + 1)
        return
    raise ValueError("detail: только целые числа и коды")


def _detail_json(detail: Mapping | None) -> str | None:
    if detail is None:
        return None
    if not isinstance(detail, Mapping):
        raise ValueError("detail: нужен словарь")
    _codes(detail)
    return json.dumps(dict(detail), ensure_ascii=False, sort_keys=True)


def _values(*, at: datetime, login: object, action: object, place: object, target: object, detail: Mapping | None,
            outcome: object) -> tuple:
    """Строка для INSERT после проверки: ошибка — ValueError с именем поля, без значения."""
    if not isinstance(at, datetime):
        raise ValueError("at: нужно время")
    if not isinstance(login, str) or LOGIN_RE.fullmatch(login) is None:
        raise ValueError("login: не логин")
    if not isinstance(action, str) or ACTION_RE.fullmatch(action) is None:
        raise ValueError("action: не код действия")
    if place is not None and (type(place) is not int or not 1 <= place <= PLACE_MAX):
        raise ValueError(f"place: {place_text()} или None")
    if target is not None and (not isinstance(target, str) or TARGET_RE.fullmatch(target) is None):
        raise ValueError("target: id разговора, имя агента или логин")
    if outcome not in OUTCOMES:
        raise ValueError("outcome: requested, ok, refused, error или started")
    return _at(at), login, action, place, target, _detail_json(detail), outcome


def insert_action(db: sqlite3.Connection, *, at: datetime, login: str, action: str, place: int | None,
                  target: str | None, detail: Mapping | None, outcome: str, drill: str) -> int:
    """Строка журнала в чужой транзакции (её зовёт Shares.apply): id строки. Под учением journal_down —
    TeacherLogError до SQL; sqlite3.Error самой вставки — тоже TeacherLogError (так apply отличает сбой журнала от сбоя
    долей, m-17). Значения не по правилам — ValueError."""
    values = _values(at=at, login=login, action=action, place=place, target=target, detail=detail, outcome=outcome)
    if drill == JOURNAL_DRILL:
        raise _drill_error()
    try:
        return db.execute(_INSERT, values).lastrowid
    except sqlite3.Error as exc:
        raise _log_error(exc) from None


def _row(values: tuple) -> ActionRow:
    rid, at, login, action, place, target, detail, outcome = values
    parsed = None
    if detail is not None:
        try:
            parsed = json.loads(detail)
        except (TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, dict):
            parsed = None
    return ActionRow(rid, at, login, action, place, target, parsed, outcome)


class TeacherLog:
    """Журнал действий преподавателя в core.db. Конструктор только запоминает.

    Поля для health() — из памяти: opened (open() удался), failures — неудачных записей подряд (запись строки или
    пульс), last_error — класс последней ошибки; store_ok — opened и failures == 0."""

    def __init__(self, db_path: str, *, drill: str = "", clock: Callable[[], float] = time.time):
        self.path = db_path
        self.drill = drill
        self.clock = clock
        self.opened = False
        self.failures = 0
        self.last_error: str | None = None
        self._state_lock = threading.Lock()

    @property
    def store_ok(self) -> bool:
        return self.opened and self.failures == 0

    # --- SQLite: соединение на операцию ---

    def _connect(self, create: bool = False, readonly: bool = False) -> sqlite3.Connection:
        """Точка подмены в тестах. Файл создаёт только create (open(), mode=rwc); запись — mode=rw, чтение — mode=ro.
        Режим автофиксации: одна вставка — сама себе COMMIT."""
        mode = "ro" if readonly else "rwc" if create else "rw"
        uri = f"{Path(self.path).absolute().as_uri()}?mode={mode}"
        return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S, isolation_level=None)

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def _failed(self, why: str) -> None:
        with self._state_lock:
            first = self.failures == 0
            self.failures += 1
            self.last_error = why
        if first:
            log.error("журнал действий преподавателя: запись не идёт (%s)", why)

    def _succeeded(self) -> None:
        with self._state_lock:
            was, self.failures = self.failures, 0
        if was:
            log.info("журнал действий преподавателя: запись снова идёт, неудачных подряд было %d", was)

    def _write(self, operation: Callable[[sqlite3.Connection], int]) -> int:
        """Одна запись: учение journal_down — TeacherLogError до SQL; sqlite3.Error — TeacherLogError. Сбой и удача —
        в счёт failures."""
        try:
            if self.drill == JOURNAL_DRILL:
                raise _drill_error()
            db = self._connect()
            try:
                result = operation(db)
            finally:
                db.close()
        except TeacherLogError as exc:
            self._failed(str(exc))
            raise
        except sqlite3.Error as exc:
            error = _log_error(exc)
            self._failed(str(error))
            raise error from None
        self._succeeded()
        return result

    def open(self) -> bool:
        """Файл, WAL, таблицы; идемпотентна. Сбой не бросает: last_error и False. Ввод-вывод до SQLITE_TIMEOUT_S —
        не в цикле событий (шлюз зовёт через asyncio.to_thread). Удача обнуляет счёт сбоев: таблицы записаны."""
        try:
            db = self._connect(create=True)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("BEGIN IMMEDIATE")
                for statement in TEACHER_SCHEMA:
                    db.execute(statement)
                db.execute("COMMIT")
            finally:
                db.close()
        except Exception as exc:
            with self._state_lock:
                self.last_error = type(exc).__name__
            return False
        with self._state_lock:
            self.opened = True
            self.failures = 0
        return True

    # --- записи ---

    def _insert(self, login: str, action: str, place: int | None, target: str | None, detail: Mapping | None,
                outcome: str) -> int:
        at = self._now()
        # значения не по правилам — ValueError до учения и SQL и мимо счёта сбоев: это ошибка кода, а не среды
        _values(at=at, login=login, action=action, place=place, target=target, detail=detail, outcome=outcome)
        return self._write(lambda db: insert_action(db, at=at, login=login, action=action, place=place, target=target,
                                                    detail=detail, outcome=outcome, drill=self.drill))

    def requested(self, login: str, action: str, place: int | None, target: str | None) -> int:
        """Строка requested до доступа к данным (чтения, пробуждение): id строки. Не записана — TeacherLogError, и
        действие не идёт."""
        return self._insert(login, action, place, target, None, "requested")

    def started(self, login: str, action: str, place: int | None, target: str | None) -> int:
        """Строка started до изменения учётки (Д10б); итог — finish(id, …)."""
        return self._insert(login, action, place, target, None, "started")

    def finish(self, row_id: int, outcome: str, detail: Mapping | None = None) -> None:
        """Итог строки started: ok, refused или error; detail None — прежний. Строки нет — ValueError (ошибка кода)."""
        if outcome not in FINAL_OUTCOMES:
            raise ValueError("outcome: ok, refused или error")
        if type(row_id) is not int:
            raise ValueError("id: нужно целое")
        text = _detail_json(detail)

        def update(db: sqlite3.Connection) -> int:
            return db.execute("UPDATE teacher_actions SET outcome = ?, detail = COALESCE(?, detail) WHERE id = ?",
                              (outcome, text, row_id)).rowcount
        if self._write(update) != 1:
            raise ValueError("строки журнала с таким id нет")

    def refused(self, login: str, action: str, place: int | None, detail: Mapping | None = None, *,
                target: str | None = None) -> bool:
        """Отказ по правилам — строка refused без гарантии: сбой её записи действие не меняет (оно и так отклонено) и не
        бросается; False и счёт сбоев. Значения не по правилам — ValueError."""
        try:
            self._insert(login, action, place, target, detail, "refused")
        except TeacherLogError:
            return False
        return True

    def ping(self) -> bool:
        """Пульс (такт уборщика шлюза): mode=rw, чтение teacher_actions, BEGIN IMMEDIATE, UPSERT teacher_heartbeat,
        COMMIT — настоящая запись, как AccountStore.ping. Не бросает; удача обнуляет счёт сбоев. Журнал не открылся при
        запуске — пульс сначала повторяет open() (ревью А2, m-6): удача ставит opened, и store_ok возвращается без
        перезапуска core; неудача идёт в счёт сбоев."""
        if not self.opened and not self.open():
            self._failed(f"журнал действий не открыт: {self.last_error or 'сбой'}")
            return False

        def beat(db: sqlite3.Connection) -> int:
            db.execute("SELECT max(id) FROM teacher_actions").fetchone()
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO teacher_heartbeat (id, at) VALUES (1, ?)"
                       " ON CONFLICT (id) DO UPDATE SET at = excluded.at", (self.clock(),))
            db.execute("COMMIT")
            return 1
        try:
            self._write(beat)
        except TeacherLogError:
            return False
        return True

    # --- чтение ---

    def recent(self, limit: int = 50) -> list[ActionRow]:
        """Последние строки, новые первыми (по id): limit — 1…RECENT_MAX. Только чтение (mode=ro), и под учением тоже;
        сбой — TeacherLogError (в счёт сбоев записи не идёт)."""
        if type(limit) is not int or not 1 <= limit <= RECENT_MAX:
            raise ValueError(f"limit: от 1 до {RECENT_MAX}")
        try:
            db = self._connect(readonly=True)
            try:
                rows = db.execute(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise _log_error(exc) from None
        return [_row(r) for r in rows]
