"""Учёт агентов студентов и их файлы (Д7-R56): строки agents и очередь перезагрузок agent_reloads в core.db, файлы —
в каталогах мест <root>/student-NN/ (= ${AGENTS_DIR}/student-NN, точки монтирования мест), карантин и архив — в корне,
вне каталогов мест.

Договор как у AccountStore: конструктор только запоминает, файл базы и таблицы — open() (роль зовёт её через
asyncio.to_thread); методы синхронные, роли зовут их через asyncio.to_thread. Соединение — на каждую операцию, mode=rw:
пропавший файл не превращается в пустую базу, создаёт её только open(). Запись ждёт блокировку не дольше
OP_TIMEOUT_S (Г14). Сбой SQLite — StoreError без пути и значений; каталог места отсутствует, не читается или не
пишется — DirUnavailable (текст тоже без пути).

Файлы:
- save пишет файл вне транзакции: .<имя>.md.tmp-<hex> (OpenCode его не сканирует) → fsync → os.link в <имя>.md
  (уже есть — NameTaken, без перезаписи) → unlink временного → fsync каталога. Строка учёта и очередь — короткой
  транзакцией после; не прошла — свой файл удаляется;
- обход места — как загрузчик OpenCode (O:config/agent.ts:12–18): рекурсивно **/*.md с точечными каталогами и файлами;
  по символьным ссылкам обход не ходит, а любая ссылка — расхождение (раунд 3, m-р3-2): OpenCode их разыменовал бы.
  Ошибка чтения любого подкаталога — DirUnavailable (отказ), а не «агентов нет» (раунд 2, m-н3);
- карантин и архив переносят сам файл или саму ссылку (os.replace без следования) и никогда не перезаписывают:
  имя назначения сначала занимается пустым файлом (O_EXCL), затем os.replace поверх него.

Кабинет преподавателя (Д10а, Д10-R103) — только чтение: cards(place) — карточки живых строк места (узел, инструменты,
теги), read_file(place, name) — текст файла живого агента места (каталог места — настоящий каталог, файл —
O_NOFOLLOW, только обычный файл до MAX_FILE_BYTES в UTF-8; иначе AgentMissing), live_counts() — живых строк по местам
одним запросом.

Только stdlib (Д7-R53, П1). Имён агентов, путей и значений полей в текстах исключений нет: тексты идут в журнал службы
и в вывод команды администратора.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, get_args

from .draft import MAX_FILE_BYTES, NAME_MAX, NAME_MIN, NAME_RE, RESERVED_NAMES, RESERVED_PREFIX, Draft, draft_digest

PLACE_RE = re.compile(r"student-(0[1-9]|10)")
PLACES = range(1, 11)
MAX_AGENTS = 20
QUARANTINE_DIR = "quarantine"
ARCHIVE_DIR = "archive"

AgentState = Literal["saved", "visible", "not_visible", "quarantined", "archived"]
STATES: tuple[str, ...] = get_args(AgentState)
# ожидаемые после перезагрузки места (воркер Д7-R58, шаг г) и строки, у которых в каталоге места должен быть файл
_EXPECTED_STATES = ("saved", "visible")
_FILE_STATES = ("saved", "visible", "not_visible")

FILE_MODE = 0o644                  # место читает файлы своего каталога (Global Constraints Д7)
PRIVATE_DIR_MODE = 0o700           # подкаталоги карантина и архива
SQLITE_TIMEOUT_S = 5.0             # open(): схема и WAL
OP_TIMEOUT_S = 0.5                 # операции: запись ждёт блокировку не дольше 0,5 с (Г14)
_STAMP = "%Y%m%dT%H%M%SZ"
_MOVE_TRIES = 8
# путь файла агента внутри каталога места (как agents_rel_path Д7-R59): части без пустых, «.» и «..»
_REL_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")
_SQLITE_CODE = re.compile(r"SQLITE_[A-Z_]{1,40}")

# dir_problem (Д7-R60): не длиннее 80 знаков, без номеров мест
DIR_NO_ROOT = "нет каталога агентов"
DIR_ROOT_READONLY = "каталог агентов не пишется"
DIR_PLACE = "каталог места отсутствует или не пишется"
DIR_SIDE = "нет каталога карантина или архива"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY, place INTEGER NOT NULL, name TEXT NOT NULL,"
    " title TEXT NOT NULL, node TEXT NOT NULL, purpose TEXT NOT NULL, tools TEXT NOT NULL, tags TEXT NOT NULL,"
    " digest TEXT NOT NULL, sha256 TEXT NOT NULL, student_reply TEXT NOT NULL, flags TEXT NOT NULL,"
    " state TEXT NOT NULL, created_at TEXT NOT NULL, state_at TEXT NOT NULL, visible_at TEXT)",
    "CREATE UNIQUE INDEX IF NOT EXISTS agents_live ON agents(place, name) WHERE state != 'archived'",
    # seq — поколение метки (раунд 2, I-н2б): reload_done снимает только ту метку, по которой шла перезагрузка
    "CREATE TABLE IF NOT EXISTS agent_reloads (place INTEGER PRIMARY KEY, since TEXT NOT NULL, seq INTEGER NOT NULL)",
)
# mark_reload и save (раунд 3, m-р3-7): seq + 1 всегда, since — только при reset_since (крючок готовности, m-н6)
_UPSERT_RELOAD = ("INSERT INTO agent_reloads (place, since, seq) VALUES (?, ?, 1) ON CONFLICT (place) DO UPDATE SET"
                  " seq = agent_reloads.seq + 1,"
                  " since = CASE WHEN ? THEN excluded.since ELSE agent_reloads.since END")
_LIVE = "place = ? AND state != 'archived'"


class StoreError(Exception):
    """Сбой SQLite учёта агентов. Текст — класс ошибки и код SQLite, без пути и значений."""


class DirUnavailable(Exception):
    """Каталог места (карантина, архива) отсутствует, не читается или не пишется. Текст — без пути."""


class NameTaken(Exception):
    """Имя занято живым агентом места или файлом без строки учёта. existing — занятые имена места по алфавиту, только
    годные имена агента (NAME_RE, 3–40 знаков): А4 кладёт их в ответ модели, а файл без учёта с переводом строки или
    не в UTF-8 в имени туда попасть не должен (доводка р1, m7)."""

    def __init__(self, existing: Iterable[str]):
        super().__init__("имя агента на месте уже занято")
        self.existing = tuple(sorted({name for name in existing if _valid_name(name)}))


class TooManyAgents(Exception):
    """У места уже MAX_AGENTS живых агентов (строки и файлы **/*.md вместе)."""

    def __init__(self) -> None:
        super().__init__(f"у места уже {MAX_AGENTS} агентов")


class AgentMissing(Exception):
    """read_file: агента у места нет (нет живой строки, архивный, файла нет) или файл не годится — odd: ссылка, не
    обычный файл, больше MAX_FILE_BYTES, не UTF-8 (шлюз пишет строку WARNING). Текст — без имени и пути."""

    def __init__(self, odd: bool = False) -> None:
        super().__init__("файл агента не годится" if odd else "агента нет")
        self.odd = odd


@dataclass(frozen=True)
class AgentCard:
    """Карточка живой строки учёта для кабинета преподавателя (Д10-R103): tools и tags — списки из JSON столбцов."""
    place: int
    name: str
    title: str
    node: str
    purpose: str
    tools: list[str]
    tags: list[str]
    state: AgentState
    created_at: str
    visible_at: str | None


@dataclass(frozen=True)
class AgentRow:
    place: int
    name: str
    title: str
    purpose: str
    state: AgentState
    created_at: str
    visible_at: str | None


@dataclass(frozen=True)
class PendingReload:
    """Метка очереди перезагрузки места: since — с какого времени ждёт, seq — поколение метки."""

    since: datetime
    seq: int


@dataclass(frozen=True)
class _Scan:
    """Обход каталога места (пути от каталога места, «/»): files — обычные файлы *.md, links — все символьные ссылки,
    others — прочие не-каталоги (временные файлы оборванной записи, любые другие файлы): они тоже держат место и
    уходят в архив — новому студенту не остаётся данных прежнего (доводка р1, m3)."""

    files: tuple[str, ...]
    links: tuple[str, ...]
    others: tuple[str, ...] = ()

    @property
    def agent_names(self) -> set[str]:
        """Имена агентов, которые увидел бы OpenCode: файлы *.md и ссылки *.md, без «.md»."""
        return {rel[:-3] for rel in self.files} | {rel[:-3] for rel in self.links if rel.endswith(".md")}


def place_of_caller(caller: object) -> int | None:
    """Номер места по id вызывающего (student-01…student-10), иначе None."""
    if not isinstance(caller, str):
        return None
    m = PLACE_RE.fullmatch(caller)
    return int(m[1]) if m else None


def _valid_name(name: object) -> bool:
    return isinstance(name, str) and NAME_MIN <= len(name) <= NAME_MAX and NAME_RE.fullmatch(name) is not None


def _check_place(place: object) -> int:
    if type(place) is not int or place not in PLACES:
        raise ValueError("место: 1…10")
    return place


def _check_name(name: object) -> str:
    """Имя файла агента ещё раз по правилам check_draft (заметка А2: Draft собирается и в обход проверки) — до того,
    как из имени строится путь."""
    if not _valid_name(name):
        raise ValueError("имя агента вне правил")
    if name in RESERVED_NAMES or name.startswith(RESERVED_PREFIX):
        raise ValueError("имя агента занято стендом")
    return name


def _check_rel(rel: object) -> list[str]:
    """Путь файла агента от каталога места: [A-Za-z0-9._/-]{1,200}, без пустых частей, «.» и «..», на «.md»."""
    if not isinstance(rel, str) or not _REL_RE.fullmatch(rel) or not rel.endswith(".md"):
        raise ValueError("путь файла агента вне правил")
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("путь файла агента вне правил")
    return parts


def _utc(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("время без пояса")
    return now.astimezone(timezone.utc)


def _iso(now: datetime) -> str:
    """Время строк учёта — ISO 8601 UTC до секунд."""
    return _utc(now).isoformat(timespec="seconds")


def _agent_name(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def _str_list(text: object) -> list[str]:
    """Столбец tools или tags — JSON-список строк; иначе (правка мимо службы) — пустой список."""
    try:
        value = json.loads(text) if isinstance(text, str) else None
    except (ValueError, RecursionError):
        return []
    return list(value) if isinstance(value, list) and all(isinstance(v, str) for v in value) else []


def _store_error(exc: sqlite3.Error) -> StoreError:
    code = getattr(exc, "sqlite_errorname", None)
    code = code if isinstance(code, str) and _SQLITE_CODE.fullmatch(code) else None
    return StoreError(f"учёт агентов: {type(exc).__name__}" + (f" {code}" if code else ""))


def _fsync_dir(path: Path) -> None:
    """fsync каталога после link/unlink/replace — без ошибок: запись уже состоялась."""
    with suppress(OSError):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _usable_dir(path: Path, mode: int, *, follow: bool = False) -> bool:
    """Каталог (не ссылка, кроме follow — для корня) с правами mode у процесса."""
    try:
        st = os.stat(path) if follow else os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and os.access(path, mode)


def _move_no_clobber(src: Path, dest_dir: Path, base: str, *, always_suffix: bool) -> Path:
    """Переносит файл или ссылку src (os.replace без следования) в dest_dir под именем base или base-<hex6>, не
    перезаписывая: имя сначала занимается пустым файлом с O_EXCL. Повтор с новым хвостом — до _MOVE_TRIES раз."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for attempt in range(_MOVE_TRIES):
        name = f"{base}-{secrets.token_hex(3)}" if always_suffix or attempt else base
        dst = dest_dir / name
        try:
            os.close(os.open(dst, flags, 0o600))
        except FileExistsError:
            continue
        try:
            os.replace(src, dst)
        except BaseException:
            with suppress(OSError):
                os.unlink(dst)
            raise
        _fsync_dir(dest_dir)
        _fsync_dir(src.parent)
        return dst
    raise FileExistsError("имя назначения занято")


class AgentStore:
    """Учёт агентов (agents, agent_reloads в core.db) и файлы мест под root. Конструктор только запоминает.

    last_error — класс последней ошибки open() (для текста роли и команды администратора).
    """

    def __init__(self, db_path: str | os.PathLike[str], root: str | os.PathLike[str]):
        self.db_path = str(db_path)
        self.root = Path(root)
        self.last_error: str | None = None

    # --- SQLite: соединение на операцию ---

    def _connect(self, create: bool = False) -> sqlite3.Connection:
        uri = f"{Path(self.db_path).absolute().as_uri()}?mode={'rwc' if create else 'rw'}"
        return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S if create else OP_TIMEOUT_S,
                               isolation_level=None)

    @contextmanager
    def _op(self) -> Iterator[sqlite3.Connection]:
        """Соединение операции; сбой SQLite — StoreError без текста SQLite. Незавершённая транзакция откатывается
        при закрытии. Прочие исключения (NameTaken) проходят как есть."""
        try:
            db = self._connect()
            try:
                yield db
            finally:
                db.close()
        except sqlite3.Error as exc:
            raise _store_error(exc) from None

    def open(self) -> bool:
        """Файл, WAL, таблицы и индекс; идемпотентна. Сбой не бросает: last_error и False."""
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

    def probe_write(self) -> None:
        """Проба записи учёта: BEGIN IMMEDIATE, вставка в agent_reloads (место 0 — вне 1…10) и ROLLBACK. Одного open()
        мало: на файле только для чтения BEGIN IMMEDIATE и CREATE … IF NOT EXISTS проходят (итоговая доводка Д7а,
        I-1). Сбой — StoreError без пути и значений."""
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO agent_reloads (place, since, seq) VALUES (0, '', 0)")
            db.execute("ROLLBACK")

    def _live_rows(self, place: int) -> list[tuple[str, str, str]]:
        """(имя, digest, состояние) неархивных строк места."""
        with self._op() as db:
            return db.execute(f"SELECT name, digest, state FROM agents WHERE {_LIVE} ORDER BY id", (place,)).fetchall()

    # --- каталоги ---

    def place_dir(self, place: int) -> Path:
        return self.root / f"student-{_check_place(place):02d}"

    def _place_base(self, place: int) -> Path:
        """Каталог места — настоящий каталог (lstat: не ссылка), иначе DirUnavailable. Его проверяют и обход, и
        перенос (доводка р1, m4): по каталогу-ссылке служба не читает и не переносит."""
        base = self.place_dir(place)
        try:
            if stat.S_ISDIR(os.lstat(base).st_mode):
                return base
            name = "NotADirectoryError"
        except OSError as exc:
            name = type(exc).__name__
        raise DirUnavailable(f"{DIR_PLACE} ({name})")

    def _scan(self, place: int) -> _Scan:
        """Обход каталога места без следования по ссылкам; любая ошибка чтения — DirUnavailable."""
        base = self._place_base(place)
        files: list[str] = []
        links: list[str] = []
        others: list[str] = []
        try:
            stack = [("", base)]
            while stack:
                prefix, path = stack.pop()
                with os.scandir(path) as entries:
                    for entry in entries:
                        rel = prefix + entry.name
                        if entry.is_symlink():
                            links.append(rel)
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append((rel + "/", Path(entry.path)))
                        elif entry.name.endswith(".md") and entry.is_file(follow_symlinks=False):
                            files.append(rel)
                        else:
                            others.append(rel)
        except OSError as exc:
            raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
        return _Scan(tuple(sorted(files)), tuple(sorted(links)), tuple(sorted(others)))

    def _locate(self, place: int, parts: Sequence[str]) -> tuple[Path, str] | None:
        """Что переносить по пути parts внутри места: сам файл (обычный или ссылку) или первую ссылку на пути — по
        ссылке перенос не идёт (раунд 3, m-р3-2). Нет файла, часть пути не каталог, сам путь — каталог — None.
        Каталог места — ссылка или не читается — DirUnavailable (m4). Между проверкой промежуточных каталогов и
        переносом есть окно гонки: подменить каталог на ссылку в нём могут только root и uid 10003 (core), а
        dir_problem такое место ловит — известно и принято (доводка р1)."""
        path = self._place_base(place)
        for i, part in enumerate(parts):
            path = path / part
            try:
                mode = os.lstat(path).st_mode
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
            if stat.S_ISLNK(mode):
                return path, "/".join(parts[:i + 1])
            last = i == len(parts) - 1
            if stat.S_ISDIR(mode) == last:              # часть пути не каталог или файл агента — каталог
                return None
        return path, "/".join(parts)

    def dir_problem(self) -> str | None:
        """Тексты Д7-R60 (для stats.dir_error) или None: корень, каталоги мест, карантин и архив."""
        if not _usable_dir(self.root, os.X_OK, follow=True):
            return DIR_NO_ROOT
        if not os.access(self.root, os.W_OK | os.X_OK):
            return DIR_ROOT_READONLY
        if not all(_usable_dir(self.place_dir(n), os.R_OK | os.W_OK | os.X_OK) for n in PLACES):
            return DIR_PLACE
        if not all(_usable_dir(self.root / side, os.W_OK | os.X_OK) for side in (QUARANTINE_DIR, ARCHIVE_DIR)):
            return DIR_SIDE
        return None

    def _side_dir(self, side: str, name: str) -> Path:
        """Подкаталог карантина или архива (0700); сам quarantine/archive создаёт выкладка — иначе DirUnavailable."""
        parent = self.root / side
        if not _usable_dir(parent, os.W_OK | os.X_OK):
            raise DirUnavailable(DIR_SIDE)
        path = parent / name
        try:
            path.mkdir(mode=PRIVATE_DIR_MODE, exist_ok=True)
        except OSError as exc:
            raise DirUnavailable(f"{DIR_SIDE} ({type(exc).__name__})") from None
        if not _usable_dir(path, os.W_OK | os.X_OK):
            raise DirUnavailable(DIR_SIDE)
        return path

    # --- запись ---

    def _insert(self, db: sqlite3.Connection, place: int, draft: Draft, digest: str, sha256: str, at: str) -> None:
        db.execute("INSERT INTO agents (place, name, title, node, purpose, tools, tags, digest, sha256, student_reply,"
                   " flags, state, created_at, state_at, visible_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'saved',"
                   " ?, ?, NULL)",
                   (place, draft.name, draft.title, draft.node, draft.purpose,
                    json.dumps(list(draft.tools), ensure_ascii=False), json.dumps(list(draft.tags), ensure_ascii=False),
                    digest, sha256, draft.student_reply, json.dumps(list(draft.flags), ensure_ascii=False), at, at))

    def save(self, place: int, draft: Draft, text: str, now: datetime) -> Literal["saved", "already"]:
        """Запись агента места по шагам Д7-R56.

        1. Короткое чтение: живая строка с тем же именем и digest — "already" (ничего не пишется), с другим или в
           карантине (quarantined, Д7б П5 а) — NameTaken. Живых строк и файлов **/*.md вместе ≥ MAX_AGENTS —
           TooManyAgents.
        2. Файл вне транзакции (см. модуль); <имя>.md уже есть — NameTaken.
        3. Строка saved и метка очереди (seq + 1, since не сдвигается) — одной короткой транзакцией. Не прошла —
           свой файл удаляется, NameTaken (уникальность) или StoreError.

        Имя черновика сверяется по NAME_RE до построения пути (ValueError); каталог места отсутствует или не
        пишется — DirUnavailable.
        """
        _check_place(place)
        name = _check_name(draft.name)
        if not isinstance(text, str):
            raise ValueError("текст файла агента — не строка")
        data = text.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("файл агента больше MAX_FILE_BYTES")
        at, since = _iso(now), _utc(now).isoformat()
        digest = draft_digest(draft)

        rows = self._live_rows(place)
        live = {row_name: row_digest for row_name, row_digest, _ in rows}
        if name in live:
            # тот же черновик в карантине — не already (предпроверка Д7б, П5 а): место его не видит, и «сохранено»
            # было бы ложью; имя занято живой строкой, пока администратор не унесёт её в архив
            state = next(row_state for row_name, _, row_state in rows if row_name == name)
            if live[name] == digest and state != "quarantined":
                return "already"
            raise NameTaken(live)
        scan = self._scan(place)
        taken = set(live) | scan.agent_names
        if len(taken) >= MAX_AGENTS:
            raise TooManyAgents()
        if name in taken:                               # файл без строки учёта (или ссылка) с этим именем
            raise NameTaken(taken)

        base = self.place_dir(place)
        final = base / f"{name}.md"
        self._write_new(base, final, f".{name}.md.tmp-{secrets.token_hex(4)}", data, taken)
        try:
            with self._op() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    self._insert(db, place, draft, digest, hashlib.sha256(data).hexdigest(), at)
                except sqlite3.IntegrityError:
                    names = {r[0] for r in db.execute(f"SELECT name FROM agents WHERE {_LIVE}", (place,))}
                    raise NameTaken(taken | names | {name}) from None
                db.execute(_UPSERT_RELOAD, (place, since, 0))
                db.execute("COMMIT")
        except BaseException:
            with suppress(OSError):
                os.unlink(final)
            _fsync_dir(base)
            raise
        return "saved"

    @staticmethod
    def _write_new(base: Path, final: Path, tmp_name: str, data: bytes, taken: set[str]) -> None:
        """Шаг 2 save: временный файл → fsync → os.link (без перезаписи) → unlink временного → fsync каталога."""
        tmp = base / tmp_name
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, FILE_MODE)
        except OSError as exc:
            raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fchmod(fd, FILE_MODE)                # umask процесса права файла не сужает
                os.fsync(fd)
            finally:
                os.close(fd)
            os.link(tmp, final)
        except FileExistsError:
            raise NameTaken(taken | {final.name[:-3]}) from None
        except OSError as exc:
            raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
        finally:
            with suppress(OSError):
                os.unlink(tmp)
        _fsync_dir(base)

    def mark_reload(self, place: int, now: datetime, *, reset_since: bool = False) -> None:
        """Место — в очередь перезагрузки: upsert, seq + 1; since сдвигается только при reset_since (Д7-R56)."""
        _check_place(place)
        since = _utc(now).isoformat()
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(_UPSERT_RELOAD, (place, since, 1 if reset_since else 0))
            db.execute("COMMIT")

    def reload_done(self, place: int, seq: int) -> bool:
        """Снимает метку места только того же поколения seq; True — снята. Метка, пришедшая во время перезагрузки,
        остаётся (раунд 2, I-н2б)."""
        _check_place(place)
        with self._op() as db:
            return db.execute("DELETE FROM agent_reloads WHERE place = ? AND seq = ?", (place, seq)).rowcount == 1

    def mark_visible(self, place: int, names: Sequence[str], now: datetime) -> None:
        """Место видит эти имена: строки saved/visible/not_visible → visible; visible_at — время первой видимости."""
        _check_place(place)
        at = _iso(now)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            for name in names:
                db.execute("UPDATE agents SET state = 'visible', visible_at = COALESCE(visible_at, ?),"
                           " state_at = CASE WHEN state = 'visible' THEN state_at ELSE ? END"
                           " WHERE place = ? AND name = ? AND state IN ('saved', 'visible', 'not_visible')",
                           (at, at, place, name))
            db.execute("COMMIT")

    def set_state(self, place: int, name: str, state: AgentState, now: datetime) -> None:
        """Состояние живой строки места; архивные не меняются (возврат из архива — только новым save)."""
        _check_place(place)
        if state not in STATES:
            raise ValueError("состояние агента вне AgentState")
        at = _iso(now)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE agents SET state = ?, state_at = ?,"
                       " visible_at = CASE WHEN ? = 'visible' THEN COALESCE(visible_at, ?) ELSE visible_at END"
                       f" WHERE name = ? AND {_LIVE}", (state, at, state, at, name, place))
            db.execute("COMMIT")

    def quarantine(self, place: int, rel: str, now: datetime) -> str | None:
        """Файл места по пути rel (как agents_rel_path Д7-R59) — в <root>/quarantine/student-NN/<rel с «/» → «__»>.
        <YYYYmmddTHHMMSSZ>-<hex6>, без перезаписи; строка агента с этим именем (если есть) — quarantined.

        Переносится сам файл или первая символьная ссылка на пути (по ссылке перенос не идёт). Ответ — имя агента
        (rel без «.md») или None, если файла не было. Путь вне правил — ValueError.
        """
        _check_place(place)
        parts = _check_rel(rel)
        at = _iso(now)
        found = self._locate(place, parts)
        if found is None:
            return None
        src, moved_rel = found
        qdir = self._side_dir(QUARANTINE_DIR, f"student-{place:02d}")
        try:
            _move_no_clobber(src, qdir, f"{moved_rel.replace('/', '__')}.{_utc(now):{_STAMP}}", always_suffix=True)
        except OSError as exc:
            raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
        name = rel[:-3]
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(f"UPDATE agents SET state = 'quarantined', state_at = ? WHERE name = ? AND {_LIVE}",
                       (at, name, place))
            db.execute("COMMIT")
        return name

    def archive(self, place: int, name: str | None, now: datetime) -> tuple[str, ...]:
        """В архив: без name — все неархивные строки места (и quarantined) и все не-каталоги каталога места, рекурсивно:
        **/*.md, символьные ссылки и прочие файлы — временные файлы оборванной записи тоже (доводка р1, m3); с name —
        строка и файл <name>.md. Файлы — в один каталог <root>/archive/student-NN-<YYYYmmddTHHMMSSZ>/ на вызов (вызовы
        в одну секунду делят его, m19) под именем «путь с «/» → «__»», совпадение — хвост -<hex6> (раунд 3, m-р3-3).
        Пустые подкаталоги остаются в месте. Строки — archived, место — в очередь перезагрузки (seq + 1).

        Ответ — имена по алфавиту (у файлов не *.md — путь как есть). С name, когда ни строки, ни файла нет, — () и
        ничего не меняется. Файл, исчезнувший посреди переноса, пропускается, остальные переносятся (m2). Файлы
        переносятся до транзакции: при её сбое (StoreError) повтор archive доводит дело до конца.
        """
        _check_place(place)
        parts = _check_rel(f"{name}.md") if name is not None else None
        at, since = _iso(now), _utc(now).isoformat()
        with self._op() as db:
            sql = f"SELECT name FROM agents WHERE {_LIVE}" + (" AND name = ?" if name is not None else "")
            rows = [r[0] for r in db.execute(sql, (place,) if name is None else (place, name))]
        if parts is None:
            scan = self._scan(place)
            every = set(scan.files) | set(scan.links) | set(scan.others)
            targets = [(rel, self.place_dir(place) / rel) for rel in sorted(every)]
        else:
            found = self._locate(place, parts)
            targets = [] if found is None else [(found[1], found[0])]
        if parts is not None and not rows and not targets:
            return ()
        moved: list[str] = []
        if targets:
            adir = self._side_dir(ARCHIVE_DIR, f"student-{place:02d}-{_utc(now):{_STAMP}}")
            for rel, src in targets:
                try:
                    _move_no_clobber(src, adir, rel.replace("/", "__"), always_suffix=False)
                except FileNotFoundError:
                    if os.path.lexists(src):                # пропал каталог архива, а не файл места
                        raise DirUnavailable(f"{DIR_PLACE} (FileNotFoundError)") from None
                    continue                                # файл исчез посреди переноса (доводка р1, m2)
                except OSError as exc:
                    raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
                moved.append(rel)
        with self._op() as db:
            db.execute("BEGIN IMMEDIATE")
            for row_name in rows:
                db.execute(f"UPDATE agents SET state = 'archived', state_at = ? WHERE name = ? AND {_LIVE}",
                           (at, row_name, place))
            db.execute(_UPSERT_RELOAD, (place, since, 0))
            db.execute("COMMIT")
        return tuple(sorted(set(rows) | {_agent_name(rel) for rel in moved}))

    # --- чтение ---

    def expected(self, place: int) -> tuple[str, ...]:
        """Имена, которые место должно видеть после перезагрузки: строки saved и visible, по алфавиту."""
        _check_place(place)
        with self._op() as db:
            rows = db.execute("SELECT name FROM agents WHERE place = ? AND state IN (?, ?) ORDER BY name",
                              (place, *_EXPECTED_STATES)).fetchall()
        return tuple(r[0] for r in rows)

    def pending(self) -> dict[int, PendingReload]:
        """Очередь перезагрузок: место → PendingReload(since, seq)."""
        with self._op() as db:
            rows = db.execute("SELECT place, since, seq FROM agent_reloads ORDER BY place").fetchall()
        try:
            return {place: PendingReload(datetime.fromisoformat(since), seq) for place, since, seq in rows}
        except (TypeError, ValueError):
            raise StoreError("учёт агентов: метка очереди не того вида") from None

    def live_count(self, place: int) -> int:
        """Неархивных строк места (quarantined — тоже живой агент)."""
        _check_place(place)
        with self._op() as db:
            return db.execute(f"SELECT count(*) FROM agents WHERE {_LIVE}", (place,)).fetchone()[0]

    def place_has_agents(self, place: int) -> bool:
        """Живые строки (и quarantined, not_visible — без файла тоже) или любой не-каталог в каталоге места: **/*.md,
        символьная ссылка, прочий файл (раунд 1, I2; раунд 2, m-н3; доводка р1, m3) — ровно то, что уносит archive
        без name. Ошибка чтения каталога — DirUnavailable: не «агентов нет»."""
        if self.live_count(place):
            return True
        scan = self._scan(place)
        return bool(scan.files or scan.links or scan.others)

    def has_saved(self, place: int) -> bool:
        """Есть строки saved — крючок готовности ставит место в очередь (Д7-R58)."""
        _check_place(place)
        with self._op() as db:
            return db.execute("SELECT 1 FROM agents WHERE place = ? AND state = 'saved' LIMIT 1",
                              (place,)).fetchone() is not None

    def flags_of(self, place: int, name: str) -> tuple[str, ...]:
        """Признаки черновика живой строки (столбец flags); строки нет — ()."""
        _check_place(place)
        with self._op() as db:
            row = db.execute(f"SELECT flags FROM agents WHERE name = ? AND {_LIVE}", (name, place)).fetchone()
        return () if row is None else tuple(json.loads(row[0]))

    def list_place(self, place: int) -> list[AgentRow]:
        """Неархивные строки места в порядке записи."""
        _check_place(place)
        with self._op() as db:
            rows = db.execute("SELECT place, name, title, purpose, state, created_at, visible_at FROM agents"
                              f" WHERE {_LIVE} ORDER BY id", (place,)).fetchall()
        return [AgentRow(*row) for row in rows]

    # --- кабинет преподавателя (Д10-R103): только чтение ---

    def cards(self, place: int) -> list[AgentCard]:
        """Живые (неархивные) строки места в порядке записи — карточки с узлом, инструментами и тегами."""
        _check_place(place)
        with self._op() as db:
            rows = db.execute("SELECT place, name, title, node, purpose, tools, tags, state, created_at, visible_at"
                              f" FROM agents WHERE {_LIVE} ORDER BY id", (place,)).fetchall()
        return [AgentCard(p, name, title, node, purpose, _str_list(tools), _str_list(tags), state, created, visible)
                for p, name, title, node, purpose, tools, tags, state, created, visible in rows]

    def live_counts(self) -> dict[int, int]:
        """Живых (неархивных) строк по местам одним запросом; мест без строк в ответе нет."""
        with self._op() as db:
            rows = db.execute("SELECT place, count(*) FROM agents WHERE state != 'archived' GROUP BY place").fetchall()
        return {place: count for place, count in rows}

    def read_file(self, place: int, name: str) -> str:
        """Текст файла живого агента места (Д10-R103), только чтение.

        Нет живой строки места с этим именем (архивный, неизвестный, имя не по правилам) или файла нет — AgentMissing.
        Каталог места — настоящий каталог (_place_base, m-12), открывается O_DIRECTORY | O_NOFOLLOW, файл — относительно
        него с O_NOFOLLOW | O_NONBLOCK (FIFO не держит поток): каталог места — ссылка или не открывается —
        DirUnavailable; файл — ссылка, не обычный файл, больше MAX_FILE_BYTES или не UTF-8 — AgentMissing(odd=True).
        Прочие ошибки чтения файла — DirUnavailable. Сбой SQLite — StoreError."""
        _check_place(place)
        if not _valid_name(name):
            raise AgentMissing()
        with self._op() as db:
            row = db.execute(f"SELECT 1 FROM agents WHERE name = ? AND {_LIVE}", (name, place)).fetchone()
        if row is None:
            raise AgentMissing()
        base = self._place_base(place)
        try:
            dfd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
        try:
            try:
                fd = os.open(f"{name}.md", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
            except FileNotFoundError:
                raise AgentMissing() from None
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENXIO, errno.EISDIR, errno.ENOTDIR):     # ссылка, сокет, …
                    raise AgentMissing(odd=True) from None
                raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
                    raise AgentMissing(odd=True)
                data = bytearray()
                while len(data) <= MAX_FILE_BYTES:      # файл мог вырасти после fstat: дальше предела + 1 не читаем
                    chunk = os.read(fd, MAX_FILE_BYTES + 1 - len(data))
                    if not chunk:
                        break
                    data += chunk
            except OSError as exc:
                raise DirUnavailable(f"{DIR_PLACE} ({type(exc).__name__})") from None
            finally:
                os.close(fd)
        finally:
            os.close(dfd)
        if len(data) > MAX_FILE_BYTES:
            raise AgentMissing(odd=True)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise AgentMissing(odd=True) from None

    def stray_files(self, place: int) -> tuple[str, ...]:
        """Прочие не-каталоги места — не *.md и не ссылки (временные файлы оборванной записи, любые другие файлы), пути
        от каталога места по алфавиту. Агентами они не считаются (drift их не показывает), но место держат
        (place_has_agents) и уходят в archive без name: их показывает admin list (доводка Д7а, А3 п. 2)."""
        return self._scan(place).others

    def drift(self, place: int) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Расхождения файлов и учёта (Д7-R61): (файлы *.md без живой строки, строки saved/visible/not_visible без
        своего файла, символьные ссылки) — пути от каталога места и имена, по алфавиту."""
        rows = self._live_rows(place)
        scan = self._scan(place)
        live = {name for name, _, _ in rows}
        own = {name for name, _, state in rows if state in _FILE_STATES}
        files = set(scan.files)
        return (tuple(rel for rel in scan.files if rel[:-3] not in live),
                tuple(sorted(name for name in own if f"{name}.md" not in files)),
                scan.links)
