"""Журнал переходов состояний в SQLite: пишутся только смены, не каждый такт."""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from .checks import Check, State


@dataclass(frozen=True)
class Event:
    ts: datetime
    component: str
    state: State
    detail: str


class Journal:
    def __init__(self, path: str):
        self._path = path
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events"
                       " (ts TEXT, component TEXT, state TEXT, detail TEXT)")
            rows = db.execute("SELECT component, state, detail FROM events WHERE rowid IN"
                              " (SELECT MAX(rowid) FROM events GROUP BY component)").fetchall()
        # последнее записанное состояние каждого компонента — переживает перезапуск
        self._last = {component: (state, detail) for component, state, detail in rows}

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        # соединение на операцию: журнал читают потоки HTTP-сервера
        db = sqlite3.connect(self._path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _append(self, events: list[Event]) -> None:
        with self._db() as db:
            db.executemany("INSERT INTO events (ts, component, state, detail) VALUES (?, ?, ?, ?)",
                           [(e.ts.isoformat(), e.component, e.state, e.detail) for e in events])
        # память — только после удачной записи, иначе следующий такт повторит
        for e in events:
            self._last[e.component] = (e.state, e.detail)

    def _is_transition(self, check: Check) -> bool:
        last = self._last.get(check.component)
        if last is None or last[0] != check.state:
            return True
        return check.state in ("warn", "fail") and last[1] != check.detail

    def probe(self) -> None:
        """Пробная запись с откатом; бросает, если журнал нельзя писать."""
        # одного BEGIN IMMEDIATE мало: на файле только для чтения он проходит
        db = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO events (ts, component, state, detail)"
                       " VALUES ('', 'probe', 'ok', '')")
            db.execute("ROLLBACK")
        finally:
            db.close()   # при ошибке незавершённая проба откатывается при закрытии

    def started(self, now: datetime) -> Event:
        event = Event(now, "watchdog", "ok", "сторож запущен")
        self._append([event])
        return event

    def record(self, checks: list[Check], now: datetime) -> list[Event]:
        events = [Event(now, c.component, c.state, c.detail)
                  for c in checks if self._is_transition(c)]
        if events:
            self._append(events)
        return events

    def recent(self, limit: int = 50) -> list[Event]:
        """Последние события, новые сверху."""
        with self._db() as db:
            rows = db.execute("SELECT ts, component, state, detail FROM events"
                              " ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [Event(datetime.fromisoformat(ts), component, state, detail)
                for ts, component, state, detail in rows]
