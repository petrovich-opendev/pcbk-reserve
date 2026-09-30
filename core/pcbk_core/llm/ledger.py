"""Журнал вызовов LLM-прокси: учёт денег в памяти, таблица llm_calls в SQLite без содержимого.

Что стоит в сумме (charge): цена, если она пришла (stream, body, generation) или денег нет
наверняка (none — отказ прокси или ошибка OpenRouter до начала ответа). Иначе — верхняя оценка
cost_est, поставленная при открытии вызова (Д4а-R13): вызов идёт (open), ждёт дозапроса
(pending), цена не нашлась (unknown) или вызов потерян при перезапуске (lost). Нуля по
умолчанию нет: «stream» без цены, NaN или минус записываются как unknown и стоят оценкой.
Строка идущего вызова в файле — status = open, cost_source = pending (цена ещё впереди).

Память — синхронно, SQLite — один поток-писатель (П5). Методы записи меняют память в
вызывающем потоке без ожидания и ставят операцию в очередь FIFO; её выполняет один поток
pcbk-ledger-writer, соединение на операцию со сроком WRITE_TIMEOUT_S. Порядок INSERT →
INSERT OR REPLACE одной строки держится, цикл событий SQLite не касается никогда (F9), а
бюджет держится и при сбое диска, пока жив процесс. Сбой записи — счётчик и last_error,
а не ожидание и не исключение вызывающему.

Конструктор без ввода-вывода (F4). open() — в начале lifespan через asyncio.to_thread:
файл, WAL, таблицы, память с нуля по файлу, поток-писатель. Суммы по вызывающим — за всё
время (бюджет без сброса). Для счётчиков за 24 ч загружаются строки unknown и с
provider_ok = 0 за всё время: окно считает totals(now) от своего now, поэтому счётчики не
зависят от часов в момент open(); таких строк мало — это сбои, а не вызовы.
"""
import logging
import math
import queue
import sqlite3
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone
from typing import Literal

log = logging.getLogger("pcbk_core.llm.ledger")

LEDGER_CHECK_S = 30.0          # период фоновой проверки записи
WRITE_TIMEOUT_S = 0.5          # ожидание блокировки у записи строки (поток-писатель)
SQLITE_TIMEOUT_S = 5.0         # ожидание блокировки у open, writable/check и чтения
ERROR_CHARS = 200              # last_error: класс и начало текста
PENDING_STUCK_S = 900          # дозапрос цены идёт около 9 минут; старше 15 минут — не идёт
COUNTERS_WINDOW = timedelta(hours=24)
WRITER_NAME = "pcbk-ledger-writer"
OPS = "ops"

Refusal = Literal["budget_spent", "share_spent"]
STATUSES = ("open", "ok", "upstream_error", "money", "refused", "cancelled", "lost")
COST_SOURCES = ("stream", "body", "generation", "pending", "unknown", "none")
PRICED = ("stream", "body", "generation")   # цена пришла


def _why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:ERROR_CHARS]


def _money(value: object) -> float | None:
    """Конечное неотрицательное число (не bool) → float; иначе None — «цены нет»."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _count(value: object) -> int:
    """Неотрицательное целое (не bool); иначе 0 — поля нет."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _aware(moment: datetime) -> datetime:
    """Время без смещения считается UTC: сравнения окон не падают на смеси."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Usage:
    """usage ответа OpenRouter: цена (None — её нет) и токены (нет поля — 0)."""

    cost: float | None
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int

    @classmethod
    def from_openrouter(cls, usage: dict) -> "Usage":
        """Разбор без исключений (задача 5, П21): значение не того типа — отсутствует."""
        u = usage if isinstance(usage, dict) else {}
        prompt = u.get("prompt_tokens_details")
        prompt = prompt if isinstance(prompt, dict) else {}
        completion = u.get("completion_tokens_details")
        completion = completion if isinstance(completion, dict) else {}
        return cls(cost=_money(u.get("cost")), prompt_tokens=_count(u.get("prompt_tokens")),
                   completion_tokens=_count(u.get("completion_tokens")),
                   cached_tokens=_count(prompt.get("cached_tokens")),
                   cache_write_tokens=_count(prompt.get("cache_write_tokens")),
                   reasoning_tokens=_count(completion.get("reasoning_tokens")))


NO_USAGE = Usage(None, 0, 0, 0, 0, 0)


@dataclass(frozen=True)
class CallRow:
    """Строка llm_calls: только числа, коды, имена моделей и провайдеров, id сессии — без содержимого."""

    id: int
    ts: datetime
    caller: str
    session: str | None
    model: str
    step: int
    status: str
    code: str
    http_status: int
    provider: str | None
    provider_ok: bool | None
    generation_id: str | None
    cost: float | None
    cost_est: float
    cost_source: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    reasoning_tokens: int
    duration_ms: int


def _at_estimate(row: CallRow) -> bool:
    """Стоит ли строка в сумме оценкой: всё, кроме пришедшей цены и none."""
    if row.cost_source == "none":
        return False
    return not (row.cost_source in PRICED and row.cost is not None)


def charge(row: CallRow) -> float:
    """Что вызов стоит в сумме: cost у stream/body/generation/none, иначе cost_est."""
    if _at_estimate(row):
        return row.cost_est
    return row.cost if row.cost is not None else 0.0


_COLUMNS = tuple(f.name for f in fields(CallRow))
_SCHEMA_CALLS = """CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    caller TEXT NOT NULL,
    session TEXT,
    model TEXT NOT NULL,
    step INTEGER NOT NULL,
    status TEXT NOT NULL,
    code TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    provider TEXT,
    provider_ok INTEGER,
    generation_id TEXT,
    cost REAL,
    cost_est REAL NOT NULL,
    cost_source TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    reasoning_tokens INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL
)"""
_SCHEMA_INDEX = "CREATE INDEX IF NOT EXISTS llm_calls_ts ON llm_calls (ts)"
_SCHEMA_META = "CREATE TABLE IF NOT EXISTS llm_meta (key TEXT PRIMARY KEY, value REAL)"
_INSERT = f"INSERT INTO llm_calls ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})"
_REPLACE = f"INSERT OR REPLACE INTO llm_calls ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})"
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM llm_calls"
_META_SET = "INSERT OR REPLACE INTO llm_meta (key, value) VALUES (?, ?)"
# строки прошлого запуска: идущий вызов потерян, pending без generation_id дозапросить нечем
_MARK_LOST = "UPDATE llm_calls SET status = 'lost', cost_source = 'unknown' WHERE status = 'open'"
_MARK_UNKNOWN = "UPDATE llm_calls SET cost_source = 'unknown' WHERE cost_source = 'pending' AND generation_id IS NULL"
# charge() на SQL: первая сумма — пришедшие цены и none, вторая — оценки; pending — поштучно
_PRICED_SQL = "cost_source IN ('stream', 'body', 'generation', 'none') AND cost IS NOT NULL"
_SUMS = (f"SELECT caller,"
         f" SUM(CASE WHEN {_PRICED_SQL} THEN cost ELSE 0 END),"
         f" SUM(CASE WHEN {_PRICED_SQL} OR cost_source = 'none' THEN 0 ELSE cost_est END)"
         f" FROM llm_calls WHERE cost_source != 'pending' GROUP BY caller")
_FLAGS = "SELECT id, ts, cost_source, provider_ok FROM llm_calls WHERE cost_source = 'unknown' OR provider_ok = 0"
# проба записи; откатывается
_PROBE = ("INSERT INTO llm_calls (ts, caller, model, step, status, code, http_status, cost_est, cost_source,"
          " prompt_tokens, completion_tokens, cached_tokens, cache_write_tokens, reasoning_tokens, duration_ms)"
          " VALUES ('', 'probe', 'probe', 0, 'refused', '', 0, 0, 'none', 0, 0, 0, 0, 0, 0)")

_SQL, _MARK, _STOP = "sql", "mark", "stop"


def _values(row: CallRow) -> tuple:
    data = {name: getattr(row, name) for name in _COLUMNS}
    data["ts"] = row.ts.isoformat()
    data["provider_ok"] = None if row.provider_ok is None else int(row.provider_ok)
    return tuple(data[name] for name in _COLUMNS)


def _row(values: Iterable) -> CallRow:
    data = dict(zip(_COLUMNS, values))
    data["ts"] = _aware(datetime.fromisoformat(data["ts"]))
    data["provider_ok"] = None if data["provider_ok"] is None else bool(data["provider_ok"])
    return CallRow(**data)


@dataclass
class _Memory:
    """Учёт в памяти. priced — пришедшие цены по вызывающим, estimated — оценки окончательных
    строк без цены (unknown, lost); идущие и ждущие дозапроса вызовы лежат поштучно."""

    last_id: int = 0
    priced: dict[str, float] = field(default_factory=dict)
    estimated: dict[str, float] = field(default_factory=dict)
    open: dict[int, CallRow] = field(default_factory=dict)
    pending: dict[int, CallRow] = field(default_factory=dict)
    since: dict[int, datetime] = field(default_factory=dict)       # начало ожидания цены у pending
    flags: dict[int, tuple[datetime, bool, bool]] = field(default_factory=dict)   # ts, unknown, provider_ok = 0
    meta: dict[str, float] = field(default_factory=dict)

    def settle(self, row: CallRow) -> None:
        """Строка, у которой вызов закончился: в pending, в оценки или в цены; флаги счётчиков."""
        if row.cost_source == "pending":
            self.pending[row.id] = row
        elif _at_estimate(row):
            self.estimated[row.caller] = self.estimated.get(row.caller, 0.0) + row.cost_est
        else:
            self.priced[row.caller] = self.priced.get(row.caller, 0.0) + charge(row)
        unknown, mismatch = row.cost_source == "unknown", row.provider_ok is False
        if unknown or mismatch:
            self.flags[row.id] = (row.ts, unknown, mismatch)
        else:
            self.flags.pop(row.id, None)

    def live(self, caller: str | None) -> float:
        """Оценки идущих и ждущих дозапроса вызовов."""
        return sum((r.cost_est for rows in (self.open, self.pending) for r in rows.values()
                    if caller is None or r.caller == caller), 0.0)

    def estimated_of(self, caller: str | None) -> float:
        done = sum(self.estimated.values(), 0.0) if caller is None else self.estimated.get(caller, 0.0)
        return done + self.live(caller)

    def spent(self, caller: str | None) -> float:
        priced = sum(self.priced.values(), 0.0) if caller is None else self.priced.get(caller, 0.0)
        return priced + self.estimated_of(caller)

    def callers(self) -> set[str]:
        return {*self.priced, *self.estimated, *(r.caller for r in self.open.values()),
                *(r.caller for r in self.pending.values())}


def _settled_cost(source: str, cost: object, call_id: int) -> tuple[str, float | None]:
    """Источник и цена закрытой строки; источник с ценой, но без цены — unknown (стоит оценкой)."""
    value = _money(cost)
    if source == "none":
        return "none", 0.0 if value is None else value
    if source in PRICED and value is None:
        log.warning("журнал вызовов: id=%d источник %s без цены — записан unknown", call_id, source)
        return "unknown", None
    if source not in COST_SOURCES:
        log.warning("журнал вызовов: id=%d неизвестный источник цены %.40r — записан unknown", call_id, source)
        return "unknown", value
    return source, value


class Ledger:
    """Журнал вызовов: память — синхронно, SQLite — один поток-писатель; конструктор без ввода-вывода.

    opened — open() удался и писатель запущен; writable_ok — итог последней check() (None до
    первой); consecutive_failures — неудачных записей писателя подряд; last_error — «<класс>:
    <текст>» последнего сбоя. health() роли читает только эти поля.
    """

    def __init__(self, path: str, *, wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.path = path
        self.wallclock = wallclock
        self.opened = False
        self.writable_ok: bool | None = None
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self._mem = _Memory()
        self._queue: queue.SimpleQueue | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()          # память и очередь; писатель его не берёт
        self._state_lock = threading.Lock()    # признаки сбоев
        self._open_lock = threading.Lock()

    # --- SQLite: соединение на операцию ---

    def _connect(self, timeout: float = WRITE_TIMEOUT_S) -> sqlite3.Connection:
        """Точка подмены в тестах: писатель зовёт без аргументов, open/writable/recent — с SQLITE_TIMEOUT_S."""
        return sqlite3.connect(self.path, timeout=timeout, isolation_level=None)

    def _load(self, now: datetime) -> _Memory:
        db = self._connect(SQLITE_TIMEOUT_S)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            for statement in (_SCHEMA_CALLS, _SCHEMA_INDEX, _SCHEMA_META):
                db.execute(statement)
            db.execute("BEGIN IMMEDIATE")
            db.execute(_MARK_LOST)
            db.execute(_MARK_UNKNOWN)
            sums = db.execute(_SUMS).fetchall()
            pending = [_row(r) for r in db.execute(f"{_SELECT} WHERE cost_source = 'pending' ORDER BY id")]
            flags = db.execute(_FLAGS).fetchall()
            meta = db.execute("SELECT key, value FROM llm_meta WHERE value IS NOT NULL").fetchall()
            last_id = db.execute("SELECT COALESCE(MAX(id), 0) FROM llm_calls").fetchone()[0]
            db.execute("COMMIT")
        finally:
            db.close()          # незавершённая транзакция откатывается при закрытии
        mem = _Memory(last_id=int(last_id), meta={key: float(value) for key, value in meta})
        for caller, priced, estimated in sums:
            mem.priced[caller] = float(priced or 0.0)
            mem.estimated[caller] = float(estimated or 0.0)
        for row in pending:
            mem.pending[row.id] = row
            mem.since[row.id] = _aware(now)          # дозапрос после перезапуска начинается заново
        for call_id, ts, source, provider_ok in flags:
            mem.flags[call_id] = (_aware(datetime.fromisoformat(ts)), source == "unknown", provider_ok == 0)
        return mem

    def open(self) -> bool:
        """Файл, WAL, таблицы, память с нуля по файлу, поток-писатель. Уже открыт — ничего не делает (П11).

        Сбой не бросает: opened = False, last_error, False. Ввод-вывод до SQLITE_TIMEOUT_S — не в
        цикле событий (роль зовёт через asyncio.to_thread).
        """
        with self._open_lock:
            if self.opened:
                return True
            try:
                mem = self._load(self.wallclock())
                q: queue.SimpleQueue = queue.SimpleQueue()
                thread = threading.Thread(target=self._drain, args=(q,), name=WRITER_NAME, daemon=True)
                thread.start()
            except Exception as exc:
                with self._state_lock:
                    self.writable_ok = False
                    self.last_error = _why(exc)
                return False
            with self._lock:
                self._mem, self._queue, self._thread = mem, q, thread
                self.opened = True
            return True

    def _drain(self, q: queue.SimpleQueue) -> None:
        """Поток-писатель: операции по порядку; сбой считается и не убивает поток."""
        while True:
            item = q.get()
            if item[0] == _STOP:
                return
            if item[0] == _MARK:
                item[1].set()
                continue
            _, sql, params = item
            try:
                db = self._connect()
                try:
                    db.execute(sql, params)
                finally:
                    db.close()
            except Exception as exc:
                self._write_failed(exc)
            else:
                self._write_ok()

    def _write_failed(self, exc: Exception) -> None:
        with self._state_lock:
            first = self.consecutive_failures == 0
            self.consecutive_failures += 1
            self.last_error = _why(exc)
            why = self.last_error
        if first:
            log.warning("журнал вызовов: запись не удалась — %s", why)

    def _write_ok(self) -> None:
        with self._state_lock:
            failed, self.consecutive_failures = self.consecutive_failures, 0
        if failed:
            log.info("журнал вызовов: запись снова идёт, сбоев подряд было %d", failed)

    def _put(self, sql: str, params: tuple) -> None:
        """В очередь писателя — под self._lock, в порядке изменений памяти; не открыт — только память."""
        if self._queue is not None:
            self._queue.put((_SQL, sql, params))

    def flush(self, timeout: float = 5.0) -> None:
        """Ждёт, пока писатель выполнит всё, что поставлено до вызова; не успел — TimeoutError.

        Писатель не запущен — сразу выходит. Для тестов и close(); в цикле событий не зовётся.
        """
        with self._lock:
            if self._queue is None:
                return
            done = threading.Event()
            self._queue.put((_MARK, done))
        if not done.wait(timeout):
            raise TimeoutError(f"журнал вызовов: писатель не успел за {timeout:g} с")

    def close(self) -> None:
        """flush(), остановка писателя, opened = False: дальше — только память, до нового open()."""
        late: TimeoutError | None = None
        try:
            self.flush()
        except TimeoutError as exc:
            late = exc
        with self._lock:
            q, thread = self._queue, self._thread
            self._queue, self._thread = None, None
            self.opened = False
            if q is not None:
                q.put((_STOP,))         # всё, что встало до остановки, писатель ещё допишет
        if thread is not None and late is None:
            thread.join(SQLITE_TIMEOUT_S)
        if late is not None:
            raise late

    def writable(self) -> bool:
        """Проба записи на новом соединении: BEGIN IMMEDIATE, INSERT, ROLLBACK. Ввод-вывод до
        SQLITE_TIMEOUT_S ожидания блокировки — не в цикле событий (F9)."""
        try:
            db = self._connect(SQLITE_TIMEOUT_S)
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(_PROBE)      # одного BEGIN IMMEDIATE мало: на файле только для чтения он проходит
                db.execute("ROLLBACK")
            finally:
                db.close()
        except Exception as exc:
            with self._state_lock:
                self.last_error = _why(exc)
            return False
        return True

    def check(self) -> None:
        """Фоновая проверка записи: роль зовёт её через asyncio.to_thread раз в LEDGER_CHECK_S."""
        ok = self.writable()
        with self._state_lock:
            self.writable_ok = ok

    def recent(self, limit: int = 10) -> list[CallRow]:
        """Последние строки из файла, новые сверху; сначала flush(). Для тестов и llmrows, не для ручек."""
        self.flush()
        db = self._connect(SQLITE_TIMEOUT_S)
        try:
            rows = db.execute(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            db.close()
        return [_row(r) for r in rows]

    # --- записи: память сразу, SQLite — в очередь ---

    def open_call(self, *, ts: datetime, caller: str, session: str | None, model: str, step: int,
                  estimate: float) -> int:
        """Резерв вызова: оценка сразу в доле и в итоге; id — из счётчика в памяти."""
        est = _money(estimate)
        if est is None:
            raise ValueError("оценка вызова — конечное неотрицательное число")
        with self._lock:
            m = self._mem
            m.last_id += 1
            row = CallRow(id=m.last_id, ts=_aware(ts), caller=caller, session=session, model=model, step=step,
                          status="open", code="", http_status=0, provider=None, provider_ok=None,
                          generation_id=None, cost=None, cost_est=est, cost_source="pending", prompt_tokens=0,
                          completion_tokens=0, cached_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
                          duration_ms=0)
            m.open[row.id] = row
            self._put(_INSERT, _values(row))
        return row.id

    def close_call(self, call_id: int, *, status: str, code: str, http_status: int, provider: str | None,
                   provider_ok: bool | None, generation_id: str | None, usage: Usage | None, cost_source: str,
                   duration_ms: int, now: datetime | None = None) -> None:
        """Оценка сменяется charge закрытой строки. pending — ожидание цены с now или ts + duration."""
        u = usage if usage is not None else NO_USAGE
        with self._lock:
            m = self._mem
            row = m.open.pop(call_id, None)
            if row is None:
                log.warning("журнал вызовов: закрытие неизвестного вызова id=%d — пропущено", call_id)
                return
            source, cost = _settled_cost(cost_source, u.cost, call_id)
            closed = replace(row, status=status, code=code, http_status=http_status, provider=provider,
                             provider_ok=provider_ok, generation_id=generation_id, cost=cost, cost_source=source,
                             prompt_tokens=_count(u.prompt_tokens), completion_tokens=_count(u.completion_tokens),
                             cached_tokens=_count(u.cached_tokens), cache_write_tokens=_count(u.cache_write_tokens),
                             reasoning_tokens=_count(u.reasoning_tokens), duration_ms=duration_ms)
            m.settle(closed)
            if source == "pending":
                m.since[call_id] = _aware(now) if now is not None else row.ts + timedelta(milliseconds=duration_ms)
            self._put(_REPLACE, _values(closed))

    def resolve_started(self, call_id: int, now: datetime) -> None:
        """Запуск дозапроса цены (и после перезапуска): отсчёт pending_stuck — заново."""
        with self._lock:
            if call_id in self._mem.pending:
                self._mem.since[call_id] = _aware(now)

    def refused(self, *, ts: datetime, caller: str, session: str | None, model: str, code: str,
                http_status: int) -> None:
        """Отказ прокси: строка с ценой 0, cost_est = 0, cost_source = none, step = 0."""
        with self._lock:
            m = self._mem
            m.last_id += 1
            row = CallRow(id=m.last_id, ts=_aware(ts), caller=caller, session=session, model=model, step=0,
                          status="refused", code=code, http_status=http_status, provider=None, provider_ok=None,
                          generation_id=None, cost=0.0, cost_est=0.0, cost_source="none", prompt_tokens=0,
                          completion_tokens=0, cached_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
                          duration_ms=0)
            m.settle(row)
            self._put(_INSERT, _values(row))

    def set_cost(self, call_id: int, cost: float | None, source: str) -> None:
        """pending → generation (в сумме оценку сменяет цена) или unknown (остаётся оценка).

        Строка не pending (уже решена, неизвестный id) — ничего не меняется.
        """
        with self._lock:
            m = self._mem
            row = m.pending.pop(call_id, None)
            if row is None:
                return
            m.since.pop(call_id, None)
            if source not in ("generation", "unknown"):
                log.warning("журнал вызовов: id=%d дозапрос с источником %.40r — записан unknown", call_id, source)
                source = "unknown"
            source, value = _settled_cost(source, cost, call_id)
            resolved = replace(row, cost=value, cost_source=source)
            m.settle(resolved)
            self._put(_REPLACE, _values(resolved))

    def meta_set(self, key: str, value: float) -> None:
        with self._lock:
            self._mem.meta[key] = float(value)
            self._put(_META_SET, (key, float(value)))

    # --- чтение: только память, без ввода-вывода ---

    def meta_get(self, key: str) -> float | None:
        with self._lock:
            return self._mem.meta.get(key)

    def pending_to_resolve(self) -> list[tuple[int, str]]:
        """Строки pending с generation_id — для дозапроса после перезапуска."""
        with self._lock:
            return [(r.id, r.generation_id) for _, r in sorted(self._mem.pending.items()) if r.generation_id]

    def spent(self, caller: str | None = None) -> float:
        """Сумма charge: цены и оценки идущих и безценовых вызовов."""
        with self._lock:
            return self._mem.spent(caller)

    def estimated(self, caller: str | None = None) -> float:
        """Часть spent, которая стоит оценкой."""
        with self._lock:
            return self._mem.estimated_of(caller)

    def reserved_open(self) -> float:
        """Оценки идущих (open) вызовов."""
        with self._lock:
            return sum((r.cost_est for r in self._mem.open.values()), 0.0)

    def has_open_or_pending(self) -> bool:
        with self._lock:
            return bool(self._mem.open or self._mem.pending)

    def totals(self, now: datetime) -> dict:
        """Итоги для health_json — только из памяти: зовётся в цикле событий."""
        now = _aware(now)
        since = now - COUNTERS_WINDOW
        with self._lock:
            m = self._mem
            return {
                "spent_usd": m.spent(None),
                "estimated_usd": m.estimated_of(None),
                "reserved_usd": sum((r.cost_est for r in m.open.values()), 0.0),
                "by_caller": {caller: m.spent(caller) for caller in sorted(m.callers())},
                "pending": len(m.pending),
                "pending_stuck": sum(1 for s in m.since.values() if (now - s).total_seconds() > PENDING_STUCK_S),
                "cost_unknown_24h": sum(1 for ts, unknown, _ in m.flags.values() if unknown and ts >= since),
                "provider_mismatch_24h": sum(1 for ts, _, mismatch in m.flags.values() if mismatch and ts >= since),
            }


class Budget:
    """Доли вызывающих и общий бюджет. Оценку нового вызова проверка не прибавляет (Д4а-R13)."""

    def __init__(self, ledger: Ledger, *, total_usd: float, share_usd: float, ops_share_usd: float,
                 key_spent: Callable[[], float | None] = lambda: None):
        self.ledger = ledger
        self.total_usd = total_usd
        self.share_usd = share_usd
        self.ops_share_usd = ops_share_usd
        self.key_spent = key_spent

    def share_of(self, caller: str) -> float:
        """ops_share_usd для ops, иначе share_usd. Перераспределение долей — Д10."""
        return self.ops_share_usd if caller == OPS else self.share_usd

    def total_spent(self) -> float:
        """Итог для отказов: при расходе по ключу выше журнала резервы идущих вызовов тоже в итоге."""
        return max(self.ledger.spent(), (self.key_spent() or 0.0) + self.ledger.reserved_open())

    def page_spent(self) -> float:
        """Число строки страницы — без резервов идущих вызовов (Д4а-R11)."""
        return max(self.ledger.spent() - self.ledger.reserved_open(), self.key_spent() or 0.0)

    def refusal(self, caller: str) -> Refusal | None:
        """Сначала общий бюджет, потом доля вызывающего."""
        if self.total_spent() >= self.total_usd:
            return "budget_spent"
        if self.ledger.spent(caller) >= self.share_of(caller):
            return "share_spent"
        return None
