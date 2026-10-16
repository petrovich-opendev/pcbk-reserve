"""Поправки технологов к базе знаний V1/V2 (Д25): студент через Помощника сообщает, что в карточке базы ошибка и как
правильно, — поправка сразу видна всем студентам (решение владельца 16.10: «это учебный стенд», без подтверждения).

Карточки базы не меняются (они смонтированы в места только для чтения): поправка хранится рядом — таблица
kb_corrections в core.db, Помощник читает её инструментом corrections службы kb. Отозвать поправку может её автор (то
же место, correction_withdraw) или оператор командой `python -m pcbk_core.kb withdraw <№>` в контейнере core. Строки не
удаляются: отзыв — отметка withdrawn_ts/withdrawn_by.

Только stdlib. Договор Role: конструктор только запоминает путь; файл, WAL и таблица — open() (роль зовёт её через
asyncio.to_thread в lifespan); идемпотентна, сбой не бросает (last_error, False). Соединение — на каждую операцию,
ожидание блокировки — до SQLITE_TIMEOUT_S: это действие студента, а не фоновая запись. Сбой SQLite — CorrectionsError
с классом и кодом SQLite, без значений; отказ по вводу или пределам — CorrectionRefused с русской причиной.

Свободный текст чистится на входе: управляющие знаки — пробел, пробелы подряд — один, одиночный суррогат — \\uXXXX
(SQLite его не закодирует). Текст поправки — сведения технолога, не указания модели: это говорит ответ инструмента и
правило Помощника; служба команд в тексте не ищет.
"""
from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..places import CALLER_RE

RECORD_CHARS = 160               # id записи («v1:claim:DEF-basis-weight») или тема
STATEMENT_MIN = 10               # «как правильно»: не короче — иначе это не утверждение
STATEMENT_CHARS = 1000
WRONG_CHARS = 600                # что неверно в базе (необязательно)
BASIS_CHARS = 600                # основание: документ, замер, опыт (необязательно)
ACTIVE_PER_CALLER = 30           # действующих поправок у одного места
ACTIVE_TOTAL = 500               # действующих поправок всего
LIST_MAX = 50                    # corrections: не больше стольких за ответ, новые первыми
SQLITE_TIMEOUT_S = 5.0
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]{1,40}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")
_SPACES = re.compile(r" {2,}")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS kb_corrections (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, caller TEXT NOT NULL,"
    " record TEXT NOT NULL, wrong TEXT, statement TEXT NOT NULL, basis TEXT, withdrawn_ts TEXT, withdrawn_by TEXT)",
    "CREATE INDEX IF NOT EXISTS kb_corrections_active ON kb_corrections(withdrawn_ts, id)",
)
_COLUMNS = "id, ts, caller, record, wrong, statement, basis, withdrawn_ts, withdrawn_by"
_INSERT = "INSERT INTO kb_corrections (ts, caller, record, wrong, statement, basis) VALUES (?, ?, ?, ?, ?, ?)"


class CorrectionsError(Exception):
    """Сбой SQLite поправок. Текст — класс ошибки и код SQLite, без значений."""


class CorrectionRefused(ValueError):
    """Отказ по вводу, пределам или праву отзыва; текст — русская причина для студента."""


def _error(exc: sqlite3.Error) -> CorrectionsError:
    code = getattr(exc, "sqlite_errorname", None)
    code = code if isinstance(code, str) and _SQLITE_CODE.fullmatch(code) else None
    return CorrectionsError(f"поправки базы знаний: {type(exc).__name__}" + (f" {code}" if code else ""))


@dataclass(frozen=True)
class Correction:
    id: int
    ts: str
    caller: str
    record: str
    wrong: str | None
    statement: str
    basis: str | None
    withdrawn_ts: str | None
    withdrawn_by: str | None

    @property
    def author(self) -> str:
        """Автор для ответа: место студента («место 03») — места закреплены за студентами; иначе id вызывающего."""
        m = CALLER_RE.fullmatch(self.caller)
        return f"место {m.group(1)}" if m else self.caller


def _iso(now: datetime) -> str:
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def clean(value: object, field: str, limit: int, *, required: bool, minimum: int = 1) -> str | None:
    """Свободный текст поля: строка, управляющие знаки — пробел, пробелы подряд — один, края обрезаны, одиночный
    суррогат — \\uXXXX. Пустое необязательное — None; длина вне minimum…limit — CorrectionRefused."""
    if value is None:
        if required:
            raise CorrectionRefused(f"{field}: обязательно")
        return None
    if not isinstance(value, str):
        raise CorrectionRefused(f"{field}: строка")
    text = value.encode("utf-8", "backslashreplace").decode("utf-8")
    text = _SPACES.sub(" ", _CONTROL.sub(" ", text)).strip()
    if not text:
        if required:
            raise CorrectionRefused(f"{field}: обязательно")
        return None
    if len(text) < minimum:
        raise CorrectionRefused(f"{field}: не короче {minimum} знаков")
    if len(text) > limit:
        raise CorrectionRefused(f"{field}: не длиннее {limit} знаков")
    return text


def matches(c: Correction, query: str) -> bool:
    """Совпадение поправки со словами запроса: каждое слово (без регистра) — в записи, «что неверно» или «как
    правильно». SQLite lower() кириллицу не понижает — сверка в Python (действующих не больше ACTIVE_TOTAL)."""
    hay = " ".join(x for x in (c.record, c.wrong, c.statement) if x).casefold()
    return all(word in hay for word in query.casefold().split())


class CorrectionStore:
    """Поправки базы знаний в core.db (kb_corrections). Конструктор только запоминает путь, без ввода-вывода."""

    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)
        self.last_error: str | None = None

    def _connect(self, create: bool = False) -> sqlite3.Connection:
        uri = f"{Path(self.db_path).absolute().as_uri()}?mode={'rwc' if create else 'rw'}"
        return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S, isolation_level=None)

    @contextmanager
    def _op(self) -> Iterator[sqlite3.Connection]:
        """Соединение операции; сбой SQLite — CorrectionsError без текста SQLite."""
        try:
            db = self._connect()
            try:
                yield db
            finally:
                db.close()      # незавершённая транзакция откатывается при закрытии
        except sqlite3.Error as exc:
            raise _error(exc) from None

    def open(self) -> bool:
        """Файл, WAL и таблица; идемпотентна. Сбой не бросает: last_error и False."""
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
        except Exception as exc:
            self.last_error = type(exc).__name__
            return False
        self.last_error = None
        return True

    def add(self, caller: str, record: object, statement: object, wrong: object, basis: object,
            now: datetime) -> Correction:
        """Новая поправка места; проверки ввода и пределов — до записи, пределы — в той же транзакции, что вставка."""
        rec = clean(record, "record", RECORD_CHARS, required=True)
        stmt = clean(statement, "statement", STATEMENT_CHARS, required=True, minimum=STATEMENT_MIN)
        bad = clean(wrong, "wrong", WRONG_CHARS, required=False)
        why = clean(basis, "basis", BASIS_CHARS, required=False)
        ts = _iso(now)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            mine = db.execute("SELECT count(*) FROM kb_corrections WHERE withdrawn_ts IS NULL AND caller = ?",
                              (caller,)).fetchone()[0]
            if mine >= ACTIVE_PER_CALLER:
                raise CorrectionRefused(f"у этого места уже {ACTIVE_PER_CALLER} действующих поправок — отзовите "
                                        "ненужные (correction_withdraw)")
            total = db.execute("SELECT count(*) FROM kb_corrections WHERE withdrawn_ts IS NULL").fetchone()[0]
            if total >= ACTIVE_TOTAL:
                raise CorrectionRefused(f"действующих поправок уже {ACTIVE_TOTAL} — новые не принимаются, "
                                        "обратитесь к преподавателю")
            row_id = db.execute(_INSERT, (ts, caller, rec, bad, stmt, why)).lastrowid
            db.execute("COMMIT")
        return Correction(row_id, ts, caller, rec, bad, stmt, why, None, None)

    def active(self, query: object = None) -> tuple[list[Correction], int]:
        """Действующие поправки, новые первыми: (не больше LIST_MAX подходящих под query, всего подходящих).
        query — слова (matches); пусто или None — все."""
        q = clean(query, "query", RECORD_CHARS, required=False)
        with self._op() as db:
            rows = db.execute(f"SELECT {_COLUMNS} FROM kb_corrections WHERE withdrawn_ts IS NULL ORDER BY id DESC"
                              ).fetchall()
        found = [Correction(*r) for r in rows]
        if q is not None:
            found = [c for c in found if matches(c, q)]
        return found[:LIST_MAX], len(found)

    def get(self, correction_id: int) -> Correction | None:
        with self._op() as db:
            row = db.execute(f"SELECT {_COLUMNS} FROM kb_corrections WHERE id = ?", (correction_id,)).fetchone()
        return Correction(*row) if row else None

    def all(self) -> list[Correction]:
        """Все строки, включая отозванные, по порядку id — для команды оператора."""
        with self._op() as db:
            rows = db.execute(f"SELECT {_COLUMNS} FROM kb_corrections ORDER BY id").fetchall()
        return [Correction(*r) for r in rows]

    def withdraw(self, correction_id: object, by: str, now: datetime, *, operator: bool = False) -> Correction:
        """Отзыв поправки: автор (то же место) или оператор (operator=True). Нет такой, чужая или уже отозвана —
        CorrectionRefused. Строка остаётся, ставится отметка."""
        if type(correction_id) is not int or correction_id < 1:
            raise CorrectionRefused("id: номер поправки — целое число от 1")
        ts = _iso(now)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT {_COLUMNS} FROM kb_corrections WHERE id = ?", (correction_id,)).fetchone()
            if row is None:
                raise CorrectionRefused(f"поправки №{correction_id} нет")
            c = Correction(*row)
            if c.withdrawn_ts is not None:
                raise CorrectionRefused(f"поправка №{correction_id} уже отозвана")
            if not operator and c.caller != by:
                raise CorrectionRefused(f"поправку №{correction_id} внесло другое место — отозвать её может только "
                                        "автор или преподаватель")
            db.execute("UPDATE kb_corrections SET withdrawn_ts = ?, withdrawn_by = ? WHERE id = ?",
                       (ts, by, correction_id))
            db.execute("COMMIT")
        return Correction(c.id, c.ts, c.caller, c.record, c.wrong, c.statement, c.basis, ts, by)
