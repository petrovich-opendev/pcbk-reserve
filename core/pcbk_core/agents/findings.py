"""Находки агентов студентов (Д13): report_event службы данных пишет, GET /pcbk/api/events шлюза читает — обе роли
через свой AgentFindings на тот же core.db (как AgentStore).

Таблица agent_findings: id, ts, place, agent, kind, title, tags (JSON), period_start, period_end, value, setpoint,
text. У места хранятся последние FINDINGS_KEEP строк — старые удаляются той же транзакцией, что и запись новой (не
отдельной уборкой).

Только stdlib. Договор Role: конструктор только запоминает путь; файл, WAL и таблица — open() (роль зовёт её через
asyncio.to_thread в lifespan, как AgentStore.open); идемпотентна, сбой не бросает (last_error, False). Соединение —
на каждую операцию: запись ждёт блокировку не дольше OP_TIMEOUT_S, открытие — не дольше SQLITE_TIMEOUT_S. Сбой
SQLite — FindingsError с классом и кодом SQLite, без значений.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..places import PLACE_MAX, place_text

FINDINGS_KEEP = 1000             # строк на место; старые удаляются при записи (Д13)
RECENT_MAX = 100                  # GET /pcbk/api/events: не больше стольких за раз (Д13)
SQLITE_TIMEOUT_S = 5.0
OP_TIMEOUT_S = 0.5
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]{1,40}")

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS agent_findings (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, place INTEGER NOT NULL,"
    " agent TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, tags TEXT NOT NULL, period_start TEXT,"
    " period_end TEXT, value REAL, setpoint REAL, text TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS agent_findings_place ON agent_findings(place, id)",
)
_INSERT = ("INSERT INTO agent_findings (ts, place, agent, kind, title, tags, period_start, period_end, value,"
          " setpoint, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
# старые сверх FINDINGS_KEEP у этого места — в той же транзакции, что вставка
_TRIM = ("DELETE FROM agent_findings WHERE place = ? AND id NOT IN"
        " (SELECT id FROM agent_findings WHERE place = ? ORDER BY id DESC LIMIT ?)")
_SELECT = ("SELECT id, ts, agent, kind, title, tags, period_start, period_end, value, setpoint, text"
          " FROM agent_findings WHERE place = ? ORDER BY id DESC LIMIT ?")


class FindingsError(Exception):
    """Сбой SQLite находок агентов. Текст — класс ошибки и код SQLite, без значений."""


def _error(exc: sqlite3.Error) -> FindingsError:
    code = getattr(exc, "sqlite_errorname", None)
    code = code if isinstance(code, str) and _SQLITE_CODE.fullmatch(code) else None
    return FindingsError(f"находки агентов: {type(exc).__name__}" + (f" {code}" if code else ""))


@dataclass(frozen=True)
class Finding:
    """Строка находки (GET /pcbk/api/events отдаёт её почти как есть; agent_title добавляет шлюз из учёта агентов)."""

    id: int
    ts: str
    agent: str
    kind: str
    title: str
    tags: tuple[str, ...]
    period_start: str | None
    period_end: str | None
    value: float | None
    setpoint: float | None
    text: str


def _iso(now: datetime) -> str:
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _row(values: tuple) -> Finding:
    rid, ts, agent, kind, title, tags, p_start, p_end, value, setpoint, text = values
    try:
        tag_list = tuple(json.loads(tags))
    except (TypeError, ValueError):
        tag_list = ()
    return Finding(rid, ts, agent, kind, title, tag_list, p_start, p_end, value, setpoint, text)


class AgentFindings:
    """Находки агентов в core.db (agent_findings). Конструктор только запоминает путь, без ввода-вывода."""

    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = str(db_path)
        self.last_error: str | None = None

    def _connect(self, create: bool = False) -> sqlite3.Connection:
        uri = f"{Path(self.db_path).absolute().as_uri()}?mode={'rwc' if create else 'rw'}"
        return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S if create else OP_TIMEOUT_S,
                               isolation_level=None)

    @contextmanager
    def _op(self) -> Iterator[sqlite3.Connection]:
        """Соединение операции; сбой SQLite — FindingsError без текста SQLite."""
        try:
            db = self._connect()
            try:
                yield db
            finally:
                db.close()
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

    def add(self, place: int, agent: str, kind: str, title: str, tags: Sequence[str], period_start: str | None,
            period_end: str | None, value: float | None, setpoint: float | None, text: str, now: datetime) -> int:
        """Строка находки места; строки этого места сверх FINDINGS_KEEP (старые) удаляются той же транзакцией.
        id новой строки. Сбой SQLite — FindingsError, ничего не меняется (BEGIN IMMEDIATE одной транзакцией)."""
        if type(place) is not int or not 1 <= place <= PLACE_MAX:
            raise ValueError(f"место: {place_text()}")
        ts = _iso(now)
        tags_json = json.dumps(list(tags), ensure_ascii=False)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute(_INSERT, (ts, place, agent, kind, title, tags_json, period_start, period_end, value,
                                       setpoint, text))
            row_id = cur.lastrowid
            db.execute(_TRIM, (place, place, FINDINGS_KEEP))
            db.execute("COMMIT")
        return row_id

    def recent(self, place: int, limit: int = RECENT_MAX) -> list[Finding]:
        """Последние находки места, новые первыми (по id); limit — 1…RECENT_MAX."""
        if type(place) is not int or not 1 <= place <= PLACE_MAX:
            raise ValueError(f"место: {place_text()}")
        if type(limit) is not int or not 1 <= limit <= RECENT_MAX:
            raise ValueError(f"limit: от 1 до {RECENT_MAX}")
        with self._op() as db:
            rows = db.execute(_SELECT, (place, limit)).fetchall()
        return [_row(r) for r in rows]
