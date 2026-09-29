"""Журнал событий вызовов службы данных: SQLite, таблица agent_events.

Событие доказывает, что ушло в историан: requested — сырой ввод, tags — канонические
имена после разбора, sent — имена, переданные драйверу (их возвращают ворота). Служебные
опросы (каталог, свежесть) пишутся с caller="system", channel="system".

Конструктор без ввода-вывода (F4): файл, WAL и схема — в open() в начале lifespan роли
или при первой операции. Соединение — на каждую операцию, как у журнала сторожа Д1:
журнал пишут и читают потоки uvicorn, TestClient и asyncio.to_thread. writable() — проба
записи с ожиданием блокировки до SQLITE_TIMEOUT_S: в цикле событий её не зовут (F9).
"""
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Outcome = Literal["ok", "partial", "refused", "budget", "rate", "busy", "timeout", "unavailable", "error"]
Channel = Literal["mcp", "http", "system"]
CacheState = Literal["hit", "miss", "shared", "none"]

# одна таблица для инструментов и служебных событий (F11): код ошибки → исход; сырой код — в summary
OUTCOME_BY_CODE: dict[str, Outcome] = {
    "connect": "unavailable", "auth": "unavailable",
    "timeout": "timeout",
    "query": "error", "unlisted": "error",
    "busy": "busy",
    "rate": "rate",
    "no_rows": "unavailable", "no_tags": "unavailable", "catalog": "unavailable",
}
EVENTS_CHECK_S = 30.0          # период фоновой проверки записи
SQLITE_TIMEOUT_S = 5.0         # ожидание блокировки SQLite
REQUESTED_LINES = 16
REQUESTED_CHARS = 128
ERROR_CHARS = 200              # last_error: класс и начало текста

_COLUMNS = ("ts", "caller", "agent", "channel", "tool", "requested", "tags", "sent", "period_start",
            "period_end", "outcome", "summary", "cost", "cache", "row_count", "duration_ms")
_SCHEMA = """CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    caller TEXT NOT NULL,
    agent TEXT,
    channel TEXT NOT NULL,
    tool TEXT NOT NULL,
    requested TEXT NOT NULL,
    tags TEXT NOT NULL,
    sent TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT,
    outcome TEXT NOT NULL,
    summary TEXT NOT NULL,
    cost REAL NOT NULL,
    cache TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL
)"""
_INSERT = f"INSERT INTO agent_events ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})"
# проба записи; откатывается
_PROBE = ("INSERT INTO agent_events (ts, caller, channel, tool, requested, tags, sent, outcome, summary, cost,"
          " cache, row_count, duration_ms) VALUES ('', 'probe', 'system', 'probe', '[]', '[]', '[]', 'ok', '',"
          " 0, 'none', 0, 0)")


def outcome_for(code: str) -> Outcome:
    """Исход по коду ошибки ворот, историана или свежести; неизвестный код — error."""
    return OUTCOME_BY_CODE.get(code, "error")


def _why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:ERROR_CHARS]


@dataclass(frozen=True)
class CallEvent:
    """Один вызов инструмента или служебный опрос.

    requested — сырой ввод, не больше 16 строк по 128 знаков (обрезается здесь); tags — канонические
    имена со статусами ok, no_value, no_data (и у попадания в кэш); sent — имена, переданные драйверу:
    ворота возвращают их и при отказе входа, когда SQL в историан не попал (F12).
    """

    ts: datetime
    caller: str
    agent: str | None
    channel: Channel
    tool: str
    requested: tuple[str, ...]
    tags: tuple[str, ...]
    sent: tuple[str, ...]
    start: datetime | None
    end: datetime | None
    outcome: Outcome
    summary: str
    cost: float
    cache: CacheState
    rows: int
    duration_ms: int

    def __post_init__(self) -> None:
        raw = (self.requested,) if isinstance(self.requested, str) else tuple(self.requested)[:REQUESTED_LINES]
        object.__setattr__(self, "requested", tuple(str(r)[:REQUESTED_CHARS] for r in raw))
        object.__setattr__(self, "tags", tuple(self.tags))
        object.__setattr__(self, "sent", tuple(self.sent))


def _dt(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def _event(row: tuple) -> CallEvent:
    (ts, caller, agent, channel, tool, requested, tags, sent, start, end, outcome, summary, cost, cache,
     rows, duration_ms) = row
    return CallEvent(ts=datetime.fromisoformat(ts), caller=caller, agent=agent, channel=channel, tool=tool,
                     requested=tuple(json.loads(requested)), tags=tuple(json.loads(tags)),
                     sent=tuple(json.loads(sent)), start=_dt(start), end=_dt(end), outcome=outcome,
                     summary=summary, cost=cost, cache=cache, rows=rows, duration_ms=duration_ms)


class EventLog:
    """Журнал событий в SQLite; конструктор только запоминает путь (F4).

    writable_ok — итог последней check(): None до первой; consecutive_failures — неудачных
    write() подряд; last_error — «<класс>: <текст>» последнего сбоя. health() роли читает
    только эти поля, без ввода-вывода.
    """

    def __init__(self, path: str):
        self.path = path
        self.writable_ok: bool | None = None
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self._ready = False
        self._schema_lock = threading.Lock()
        self._state_lock = threading.Lock()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        # соединение на операцию: журнал пишут и читают разные потоки (check_same_thread)
        db = sqlite3.connect(self.path, timeout=SQLITE_TIMEOUT_S)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _ensure(self) -> None:
        """Файл, WAL и таблица — один раз; сбой бросает, следующая операция пробует снова."""
        if self._ready:
            return
        with self._schema_lock:
            if self._ready:
                return
            with self._db() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute(_SCHEMA)
            self._ready = True

    def open(self) -> bool:
        """Файл, WAL и таблица agent_events; идемпотентна. Сбой не бросает: writable_ok = False."""
        try:
            self._ensure()
        except Exception as exc:
            with self._state_lock:
                self.writable_ok = False
                self.last_error = _why(exc)
            return False
        return True

    def _insert(self, e: CallEvent) -> None:
        values = (e.ts.isoformat(), e.caller, e.agent, e.channel, e.tool,
                  json.dumps(list(e.requested), ensure_ascii=False), json.dumps(list(e.tags), ensure_ascii=False),
                  json.dumps(list(e.sent), ensure_ascii=False),
                  None if e.start is None else e.start.isoformat(), None if e.end is None else e.end.isoformat(),
                  e.outcome, e.summary, float(e.cost), e.cache, int(e.rows), int(e.duration_ms))
        with self._db() as db:
            db.execute(_INSERT, values)

    def write(self, e: CallEvent) -> None:
        """Запись события; ошибка считается (consecutive_failures, last_error) и идёт дальше."""
        try:
            self._ensure()
            self._insert(e)
        except Exception as exc:
            with self._state_lock:
                self.consecutive_failures += 1
                self.last_error = _why(exc)
            raise
        with self._state_lock:
            self.consecutive_failures = 0

    def writable(self) -> bool:
        """Проба записи на новом соединении: BEGIN IMMEDIATE, INSERT, ROLLBACK. Ввод-вывод до
        SQLITE_TIMEOUT_S ожидания блокировки — не в цикле событий (F9)."""
        try:
            self._ensure()
            # одного BEGIN IMMEDIATE мало: на файле только для чтения он проходит
            db = sqlite3.connect(self.path, timeout=SQLITE_TIMEOUT_S, isolation_level=None)
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(_PROBE)
                db.execute("ROLLBACK")
            finally:
                db.close()      # при ошибке незавершённая проба откатывается при закрытии
        except Exception as exc:
            with self._state_lock:
                self.last_error = _why(exc)
            return False
        return True

    def check(self) -> None:
        """Фоновая проверка записи: роль зовёт её через asyncio.to_thread раз в EVENTS_CHECK_S."""
        ok = self.writable()
        with self._state_lock:
            self.writable_ok = ok

    def recent(self, limit: int = 50, channel: Channel | None = None) -> list[CallEvent]:
        """Последние события, новые сверху; channel отсеивает, например, события system."""
        self._ensure()
        where, args = (" WHERE channel = ?", (channel,)) if channel else ("", ())
        with self._db() as db:
            rows = db.execute(f"SELECT {', '.join(_COLUMNS)} FROM agent_events{where} ORDER BY id DESC LIMIT ?",
                              (*args, limit)).fetchall()
        return [_event(r) for r in rows]

    def count(self, channel: Channel | None = None) -> int:
        """SELECT COUNT(*) — для тестов на работающем приложении: опрос свежести пишет между замерами."""
        self._ensure()
        where, args = (" WHERE channel = ?", (channel,)) if channel else ("", ())
        with self._db() as db:
            return db.execute(f"SELECT COUNT(*) FROM agent_events{where}", args).fetchone()[0]
