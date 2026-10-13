"""Доли мест в бюджете на модель (Д10а, Д10-R97): таблица llm_shares в core.db, целые центы.

Строка есть только у изменённых мест; прочие — по умолчанию default_cents = round(LLM_SHARE_USD × 100). Удалений строк
нет. Доля ops («отбор и проверки») — вне таблицы и суммы (решает Budget).

Память _cents — центы мест, по которым отказывает LLM-прокси (Budget.share_of → usd). Она заменяется целиком
новым словарём (атомарная замена ссылки): usd(), cents(), sum_cents() и floor_of() зовутся из цикла событий без замка и
без SQLite. Конструктор сразу ставит умолчания (m-5 р3): при loaded == False такт шлюза считает доли без KeyError, а
денег эти умолчания не открывают — роль llm при незагруженных долях закрыта (config_error, R98).

Изменения — apply() (перевод «всё или ничего» со сверкой «было») и apply_floor() («Остановить место» — одна серверная
операция, I-1 р2): под _apply_lock (его же берёт verify), соединение mode=rw, BEGIN IMMEDIATE, строка журнала действий
(teacher_log.insert_action) — в той же транзакции. Пол доли — floor_cents(settled_of(место)): оплаченное
(Ledger.settled: цены и окончательные оценки, без идущих и ждущих цены вызовов), вниз до цента в той же арифметике, что
отказ прокси (cents / 100, spent >= share), с запасом 1e-9 против шума float (B-1 р2). Так floor / 100 ≤ settled ≤ spent
при любом исходе идущих вызовов: доля, сведённая к полу, даёт отказ следующему же вызову. Цена: на ровной сумме пол на
цент ниже ($0,30 → 29 центов).

verify() — сверка диска с центами, по которым отказывает прокси (I-7, такт уборщика шлюза): drift — число расхождений.
Память сверка не чинит — молча вылеченное расхождение спрятало бы ошибку; лечит перезапуск core.

Учение shares_stale (DRILL_TEACHER) — apply и apply_floor пишут диск и журнал, а память прокси не обновляют. Учение
journal_down — строка журнала в транзакции не пишется, изменение откатывается (SharesStoreError(journal=True)).

Только stdlib и sqlite3; из пакета — только teacher_log (ничего из gateway).
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from ..places import CALLER_RE, PLACE_MAX, PLACES
from ..teacher_log import LOGIN_RE, TEACHER_SCHEMA, TeacherLogError, insert_action

log = logging.getLogger("pcbk_core.llm.shares")

STALE_DRILL = "shares_stale"
PLACE_CALLER = CALLER_RE
SQLITE_TIMEOUT_S = 2.0                  # запись ждёт блокировку недолго, как AccountStore (Г14)
FLOOR_SLACK_USD = 1e-9                  # запас пола против шума float (B-1 р2)
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]{1,40}")

BAD_TEXT = "Неверный запрос"
CONFLICT_TEXT = "Доли изменились, пока страница была открыта, — обновите её"

SHARES_SCHEMA = f"""CREATE TABLE IF NOT EXISTS llm_shares (
    place INTEGER PRIMARY KEY CHECK (place BETWEEN 1 AND {PLACE_MAX}),
    cents INTEGER NOT NULL CHECK (cents >= 0),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL
)"""
# Д15б: старая проверка (мест было 10) — перенос схемы в open() копией строк, без ALTER TABLE (SQLite его не умеет)
_OLD_CHECK = "CHECK (place BETWEEN 1 AND 10)"
_UPSERT = ("INSERT INTO llm_shares (place, cents, updated_at, updated_by) VALUES (?, ?, ?, ?)"
           " ON CONFLICT (place) DO UPDATE SET cents = excluded.cents, updated_at = excluded.updated_at,"
           " updated_by = excluded.updated_by")


def floor_cents(usd: float) -> int:
    """Пол доли, центы: usd вниз до цента в той же арифметике, что отказ прокси (cents / 100), с запасом 1e-9 —
    c / 100 ≤ usd − 1e-9 (шум float: += 25 × 0,0012 даёт 0.029999999999999995; пересуммирование SUM() SQLite).
    На ровной сумме — цент ниже. Формулу не ослаблять (I-1 р3). Минус и не число — 0 (вне формулы: доли ниже нуля
    нет)."""
    if not isinstance(usd, (int, float)) or isinstance(usd, bool) or not math.isfinite(usd):
        return 0
    c = math.floor(round(usd * 100, 6))
    while c > 0 and c / 100 > usd - FLOOR_SLACK_USD:     # шум float и пересуммирование SUM() SQLite
        c -= 1
    return max(c, 0)


def money_text(cents: int) -> str:
    """Центы — «1234,56 $» (тексты отказов)."""
    return f"{cents // 100},{cents % 100:02d} $"


class ShareRefused(Exception):
    """Отказ по правилам (R97): kind — bad, conflict, below_spent, over_total, at_floor; text — для преподавателя
    дословно."""

    def __init__(self, kind: str, text: str):
        super().__init__(text)
        self.kind = kind
        self.text = text


class SharesStoreError(Exception):
    """Изменение не записано (m-17): journal — сбой строки журнала действий в транзакции (и учение journal_down), иначе
    сбой SQLite самих долей. Текст — класс ошибки и код SQLite, без путей и значений."""

    def __init__(self, text: str, journal: bool = False):
        super().__init__(text)
        self.journal = journal


class _BadRow(Exception):
    """Строка llm_shares не по правилам (правка мимо кабинета дробным или чужим местом)."""


def _why(exc: BaseException) -> str:
    """Класс ошибки и код SQLite (если он есть) — без путей и значений."""
    code = getattr(exc, "sqlite_errorname", None)
    code = code if isinstance(code, str) and _SQLITE_CODE.fullmatch(code) else None
    return type(exc).__name__ + (f" {code}" if code else "")


def _store_error(exc: BaseException) -> SharesStoreError:
    return SharesStoreError(f"доли мест: {_why(exc)}")


def _iso(now: datetime) -> str:
    return (now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)).isoformat()


def _int(value: object) -> bool:
    return type(value) is int


def _nn(place: int) -> str:
    return f"{place:02d}"


def _needs_migration(db: sqlite3.Connection) -> bool:
    """llm_shares уже на диске со старой проверкой (Д15б: мест было 10) — перенести схему."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'llm_shares'").fetchone()
    return row is not None and row[0] is not None and _OLD_CHECK in row[0]


def _migrate_schema(db: sqlite3.Connection) -> None:
    """Новая таблица (SHARES_SCHEMA с текущим PLACE_MAX) под именем на обмен, копия строк старой, удаление старой,
    переименование — одной транзакцией с остальным открытием (ALTER TABLE CHECK не меняет, п. 3.3 Review Focus)."""
    db.execute(SHARES_SCHEMA.replace("llm_shares", "llm_shares_new", 1))
    db.execute("INSERT INTO llm_shares_new (place, cents, updated_at, updated_by)"
              " SELECT place, cents, updated_at, updated_by FROM llm_shares")
    db.execute("DROP TABLE llm_shares")
    db.execute("ALTER TABLE llm_shares_new RENAME TO llm_shares")


class Shares:
    """Доли мест (PLACE_MAX): память — для прокси, SQLite — под _apply_lock. Конструктор только запоминает.

    settled_of — оплаченное вызывающим, $ (ставит LlmRole: ledger.settled, B-1; без роли — ноль). Поля для здоровья —
    из памяти: loaded (open() удался), ok (последняя сверка прочла таблицу), drift (мест с расхождением), checked_at
    (конец последней сверки, и при сбое), last_error (класс и код последней ошибки)."""

    def __init__(self, db_path: str, *, total_usd: float, default_usd: float, drill: str = ""):
        self.path = db_path
        self.drill = drill
        self.total_cents = round(total_usd * 100)
        self.default_cents = round(default_usd * 100)
        self.settled_of: Callable[[str], float] = lambda caller: 0.0
        self.loaded = False
        self.ok = False
        self.drift = 0
        self.checked_at: datetime | None = None
        self.last_error: str | None = None
        self._cents: dict[int, int] = {n: self.default_cents for n in PLACES}
        self._apply_lock = threading.Lock()

    # --- память: без замка и без SQLite ---

    def cents(self, place: int) -> int:
        return self._cents[place]

    def sum_cents(self) -> int:
        return sum(self._cents.values())

    def usd(self, caller: str) -> float | None:
        """Доля вызывающего student-NN, $ (центы / 100 — та же арифметика, что отказ прокси); иначе None (решает
        Budget)."""
        m = PLACE_CALLER.fullmatch(caller) if isinstance(caller, str) else None
        return None if m is None else self._cents[int(m[1])] / 100

    def floor_of(self, place: int) -> int:
        """Пол доли места сейчас — подсказка обзору (R101); перевод берёт пол сам, под замком."""
        return floor_cents(self.settled_of(f"student-{place:02d}"))

    # --- SQLite ---

    def _connect(self, create: bool = False) -> sqlite3.Connection:
        """Точка подмены в тестах (как у Ledger и хранилищ): файл создаёт только create (open(), mode=rwc), иначе
        mode=rw. Режим автофиксации, транзакции — явные."""
        uri = f"{Path(self.path).absolute().as_uri()}?mode={'rwc' if create else 'rw'}"
        return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S, isolation_level=None)

    def _read(self, db: sqlite3.Connection) -> dict[int, int]:
        """Центы мест по диску: строка или умолчание. Строка не по правилам — _BadRow."""
        cents = {n: self.default_cents for n in PLACES}
        for place, value in db.execute("SELECT place, cents FROM llm_shares").fetchall():
            if not _int(place) or place not in PLACES or not _int(value) or value < 0:
                raise _BadRow("llm_shares: строка не по правилам")
            cents[place] = value
        return cents

    def open(self) -> bool:
        """Файл (mode=rwc), WAL, таблицы llm_shares и журнала действий (TEACHER_SCHEMA), память по диску. Старая
        проверка llm_shares (мест было 10, Д15б) — перенос схемы под тем же BEGIN IMMEDIATE: новая таблица, копия
        строк, удаление старой, переименование; идемпотентно (второй open() схему не трогает). Сбой не бросает:
        last_error, loaded = False, False. Ввод-вывод — не в цикле событий (роль зовёт через asyncio.to_thread)."""
        with self._apply_lock:
            try:
                db = self._connect(create=True)
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("BEGIN IMMEDIATE")
                    if _needs_migration(db):
                        _migrate_schema(db)
                    else:
                        db.execute(SHARES_SCHEMA)
                    for statement in TEACHER_SCHEMA:
                        db.execute(statement)
                    cents = self._read(db)
                    db.execute("COMMIT")
                finally:
                    db.close()
            except Exception as exc:
                self.loaded = False
                self.last_error = _why(exc)
                return False
            self._cents = cents
            self.loaded = True
            return True

    def _form(self, changes: object) -> list[tuple[int, int, int]]:
        """Форма изменения (R97, п. 1): 1…len(PLACES) изменений, места различны, int 1…PLACE_MAX (не bool); «было» и
        «станет» — int (не bool) от 0 до total_cents. Иначе ShareRefused bad."""
        if (isinstance(changes, (str, bytes)) or not isinstance(changes, Sequence)
                or not 1 <= len(changes) <= len(PLACES)):
            raise ShareRefused("bad", BAD_TEXT)
        plan: list[tuple[int, int, int]] = []
        for item in changes:
            if isinstance(item, (str, bytes)) or not isinstance(item, Sequence) or len(item) != 3:
                raise ShareRefused("bad", BAD_TEXT)
            place, was, to = item
            if not all(_int(v) for v in (place, was, to)) or place not in PLACES:
                raise ShareRefused("bad", BAD_TEXT)
            if not (0 <= was <= self.total_cents and 0 <= to <= self.total_cents):
                raise ShareRefused("bad", BAD_TEXT)
            plan.append((place, was, to))
        if len({place for place, _, _ in plan}) != len(plan):
            raise ShareRefused("bad", BAD_TEXT)
        return plan

    def _transaction(self, by: str, now: datetime,
                     decide: Callable[[dict[int, int]], tuple[dict[int, int], dict]]) -> dict[int, int]:
        """Под _apply_lock: BEGIN IMMEDIATE, доли с диска, decide(диск) → (новые центы изменённых мест, detail строки
        журнала) или ShareRefused; запись строк, строка журнала shares в той же транзакции, COMMIT; память — новым
        словарём (под shares_stale — нет). Возврат — центы мест по диску после изменения."""
        if not isinstance(by, str) or LOGIN_RE.fullmatch(by) is None:
            raise ValueError("by: логин преподавателя")
        if not isinstance(now, datetime):
            raise ValueError("now: нужно время")
        at = _iso(now)
        with self._apply_lock:
            try:
                db = self._connect()
                try:
                    db.execute("BEGIN IMMEDIATE")
                    disk = self._read(db)
                    new, detail = decide(disk)
                    for place, value in sorted(new.items()):
                        db.execute(_UPSERT, (place, value, at, by))
                    insert_action(db, at=now, login=by, action="shares", place=None, target=None, detail=detail,
                                  outcome="ok", drill=self.drill)
                    db.execute("COMMIT")
                finally:
                    db.close()              # незавершённая транзакция откатывается при закрытии
            except TeacherLogError as exc:
                self.last_error = str(exc)
                raise SharesStoreError(str(exc), journal=True) from None
            except (sqlite3.Error, _BadRow) as exc:
                self.last_error = _why(exc)
                raise _store_error(exc) from None
            after = {**disk, **new}
            if self.drill == STALE_DRILL:
                log.warning("УЧЕНИЯ: shares_stale — доли записаны, память прокси не обновлена")
            else:
                self._cents = {**self._cents, **new}
            return after

    def apply(self, changes: Sequence[tuple[int, int, int]], *, by: str, now: datetime) -> dict[int, int]:
        """Перевод «всё или ничего» (R97): changes — (место, было центов, станет центов). Порядок: форма (bad) → под
        замком сверка «было» по диску (conflict) → пол (below_spent) → сумма (over_total: больше total_cents и больше
        прежней) → строки и строка журнала shares в одной транзакции. Ошибка SQLite — SharesStoreError(journal=False),
        сбой строки журнала — SharesStoreError(journal=True); транзакция откатывается, память не трогается."""
        plan = self._form(changes)

        def decide(disk: dict[int, int]) -> tuple[dict[int, int], dict]:
            if any(disk[place] != was for place, was, _ in plan):
                raise ShareRefused("conflict", CONFLICT_TEXT)
            for place, _, to in plan:
                floor = floor_cents(self.settled_of(f"student-{place:02d}"))
                if to < floor:
                    raise ShareRefused("below_spent", f"Доля места {_nn(place)} не может быть меньше уже оплаченного: "
                                                      f"{money_text(floor)}")
            before = sum(disk.values())
            after = before + sum(to - disk[place] for place, _, to in plan)
            if after > self.total_cents and after > before:
                raise ShareRefused("over_total", f"Сумма долей мест больше общего бюджета: {money_text(after)} из "
                                                 f"{money_text(self.total_cents)}")
            return ({place: to for place, _, to in plan},
                    {_nn(place): [disk[place], to] for place, _, to in sorted(plan)})

        return self._transaction(by, now, decide)

    def apply_floor(self, place: int, to: int, *, by: str, now: datetime) -> dict[int, int]:
        """«Остановить место» (I-1 р2): одна серверная операция — доля места place сводится к полу, разница уходит месту
        to. Пол берётся под _apply_lock в той же транзакции, что перевод: клиент числа пола не передаёт, а вызов,
        кончившийся между обзором и кнопкой, в пол уже вошёл (settled после него только растёт). Отказы: форма — bad;
        доля не выше пола — at_floor (в тексте — пол, то есть оплаченное, m-7 р3); доля to стала бы больше total_cents —
        bad (m-3 р3). Сумма не меняется — «не хуже» всегда."""
        if not (_int(place) and _int(to) and place in PLACES and to in PLACES and place != to):
            raise ShareRefused("bad", BAD_TEXT)

        def decide(disk: dict[int, int]) -> tuple[dict[int, int], dict]:
            floor = floor_cents(self.settled_of(f"student-{place:02d}"))
            amount = disk[place] - floor
            if amount <= 0:
                raise ShareRefused("at_floor", f"Доля места {_nn(place)} уже не выше оплаченного: {money_text(floor)}")
            if disk[to] + amount > self.total_cents:
                raise ShareRefused("bad", f"Доля места {_nn(to)} стала бы больше общего бюджета — "
                                          "выберите другое место")
            new = {place: floor, to: disk[to] + amount}
            detail: dict = {_nn(n): [disk[n], new[n]] for n in sorted(new)}
            detail["kind"] = "floor"
            return new, detail

        return self._transaction(by, now, decide)

    def verify(self, now: datetime, effective: Mapping[int, int] | Callable[[], Mapping[int, int]]) -> None:
        """Сверка (I-7, такт уборщика шлюза): под _apply_lock таблица целиком (mode=rw), ожидаемые центы мест (строка
        или умолчание) сравниваются с effective — центами, по которым отказывает прокси ({n: round(budget.share_of(…)
        × 100)}). effective — словарь или функция без аргументов, которая его вернёт: функцию сверка зовёт под тем же
        замком, что перевод (ревью А2, m-1) — перевод между чтением памяти прокси и диска не даёт ложного расхождения на
        такт. Итог: drift — число расхождений, ok — таблица прочлась, checked_at = now (и при сбое), сбой —
        last_error. Память не чинит."""
        with self._apply_lock:
            try:
                db = self._connect()
                try:
                    disk = self._read(db)
                finally:
                    db.close()
            except (sqlite3.Error, _BadRow) as exc:
                self.ok = False
                self.last_error = _why(exc)
                self.checked_at = now
                return
            expected = effective() if callable(effective) else effective
            self.drift = sum(1 for n in PLACES if expected.get(n) != disk[n])
            self.ok = True
            self.checked_at = now
