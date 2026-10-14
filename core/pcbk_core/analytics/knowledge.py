"""Корпус КДМ-1 только для чтения (Task 1; план §Task 1/Этап 1; мастер-промпт §4, §5).

Открывает `kdm1.seed.sqlite3` (или следующую опубликованную версию из `ANALYTICS_CORPUS_DIR`, Task 8) в режиме
`mode=ro` — как `lessons.py` читает `LESSONS_ROOT`, но корпус не пишется никогда и никем из core (мастер-промпт
§2: «корпус только для чтения; не писать туда диалоги»). Схема — `knowledge/README.md` и
`knowledge/seed_manifest.json` пакета заказчика: таблицы `sources/fragments/processes/terms/claims/conflicts/
equipment_links/mechanisms` (`id TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload))`),
`seed_meta(key, value)` с единственной записью `key="manifest"`, и FTS5 `kb_search(kind, id, text,
tokenize='unicode61')` — виды `kind` в реальном корпусе: `claim`, `mechanism`, `processe` (так в сидере
заказчика — опечатка множественного числа `process`, не исправлять молча: `retrieval.py` должен искать именно
её, иначе поиск по процессам тихо не находит ничего), `term`. Синтетический корпус теста — `core/tests/fixtures/
kdm1_corpus.py`, той же схемы.

Решение Task 1 по соединению: ни одна функция модуля не хранит постоянное соединение SQLite в `CorpusHandle`
(он остаётся `path, version` — ровно как в `docs/kdm1/INTERFACES.md`). Каждый вызов (`get_row`, `get_rows`,
`corpus_report`, `all_ids`, поиск в `retrieval.py`) открывает своё соединение `mode=ro` и закрывает его до
возврата — тот же приём, что `teacher_log.TeacherLog.recent`/`agents.store` («соединение — на каждую
операцию»). Причина не стилистическая: вызывающие зовут эти функции через `asyncio.to_thread` (docstring
`open_corpus`), то есть разные вызовы одного запроса могут исполниться в разных потоках пула — один объект
`sqlite3.Connection`, созданный в одном потоке и используемый в другом, кидает `ProgrammingError` без
`check_same_thread=False`. Открывать соединение на операцию решает это без общего флага отключения защиты.

Версия корпуса (`corpus_version` контрактов) — Task 1 фиксирует решение здесь: `schema_version` манифеста +
первые 16 знаков sha256 файла корпуса. Это не номер строки файла (запрет мастер-промпта §4) и не имя
подкаталога `ANALYTICS_CORPUS_DIR/<version>/` — такого подката у заказчика пока нет (сейчас один файл
`knowledge/kdm1.seed.sqlite3`), а хеш-версия переживает и такой случай, и будущую публикацию версий Task 8
(тогда он застынет на хеше опубликованного файла, что и нужно: «повторная проверка версии между поиском и
выдачей», §10, не даёт старому хендлу тихо увидеть новые байты). `corpus_dirs()` — отдельная, независимая от
этого решения точка для Task 8 (список подкаталогов версий на будущее).

Сбой открытия (битый файл, нет файла, SQLite не читается, манифест битый или чужая область) не бросает из
общего жизненного цикла шлюза (мастер-промпт §4: «ошибка корпуса или аналитическая база не ломают вход, чат,
калькуляторы, уроки») — по аналогии с `agents.store.DirUnavailable`/`LessonsError`: исключение модуля, его ловит
`service.py` и превращает в 503 «аналитика ещё не готова» или в `limited_answer`, никогда — в падение lifespan
роли gateway. Текст `CorpusError` — без путей хоста (его показывает ручка студенту/преподавателю в 503, как
`LessonsError`).

Дополнение к `docs/kdm1/INTERFACES.md` (не меняет ни одной уже описанной сигнатуры — только добавляет):
`all_ids(handle, kind) -> tuple[str, ...]`. `terms.py` (Task 1) должен перечислить все термины корпуса, чтобы
сравнить нормализованное слово вопроса с `preferred_ru`/`aliases` каждого — а словарь термина мал (41 запись в
настоящем корпусе), в отличие от `fragments`/`claims`. Без этой функции `terms.py` либо открывал бы SQLite
напрямую мимо `knowledge.py` (дублирование решения о соединении и ошибках выше), либо гадал бы идентификаторы.
Это осознанное добавление Task 1, не изменение выданного интерфейса; в отчёте задачи — отдельным пунктом.

Дополнение Task 8 (план, Этап 8; мастер-промпт §4, §10 «атомарная замена и откат не меняют исторические
пакеты»): `_find_seed_file` теперь понимает необязательный файл-указатель `CURRENT_POINTER_NAME` прямо в
`root` — одна строка с именем подкаталога версии. Если указатель есть, он приоритетнее сортировки подкаталогов
по имени (иначе откат на старую версию не мог бы открыть именно её: самым новым ПО ИМЕНИ подкаталогом остаётся
только что опубликованный/отклонённый, Task 8 его никогда не удаляет). Нет указателя — поведение Task 1
не меняется ни на байт (обратная совместимость `test_open_corpus_picks_latest_versioned_subdir` и соседних
тестов): сортировка подкаталогов по убыванию имени, как раньше. Указатель пишет и меняет только
`analytics/ingestion/versions.py` (Task 8) — атомарно, временный файл + `os.replace`; этот модуль остаётся
только читающим (докстринг выше, «Решение Task 1 по соединению» — та же дисциплина применена и к указателю:
`knowledge.py` корпус не пишет никогда и никем из core, в т.ч. и указатель версии).

Дополнение правки итогового ревью F4a (14.10, «смена версии корпуса без перезапуска процесса»):
`version_signature(root) -> str` — дешёвый признак текущей версии на диске для `AnalyticsService.
_maybe_reopen_corpus`: путь выбранного файла (`_find_seed_file`, учитывает указатель так же, как `open_corpus`)
плюс его размер и время изменения, БЕЗ хеширования содержимого (в отличие от `open_corpus`/`corpus_version`,
которые читают файл целиком — дорогая операция, не на каждый вопрос). Признак меняется и когда публикация/откат
Task 8 переключает указатель на другой подкаталог (другой путь), и когда тот же файл перезаписан на месте
(другой размер/mtime) — ровно то, что должно вызвать переоткрытие корпуса. `CorpusError`, если файл не
находится — так же строго, как `open_corpus`: «признак не посчитался» не должно молча означать «признак не
изменился», вызывающий должен увидеть сбой отдельно от «изменений нет» (`AnalyticsService.
_current_corpus_signature` их и различает). Дополнение к `docs/kdm1/INTERFACES.md` — не меняет уже описанных
сигнатур, см. обновление файла рядом."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

RowKind = Literal["sources", "fragments", "processes", "terms", "claims", "conflicts", "equipment_links",
                  "mechanisms"]
# виды FTS5 kb_search — как в реальном сидере (kb_search.kind), не множественное число своими силами (m-? Task 1)
SEARCH_KINDS: tuple[str, ...] = ("claim", "mechanism", "processe", "term")

_ROW_KINDS: frozenset[str] = frozenset(get_args(RowKind))
_REQUIRED_TABLES: frozenset[str] = _ROW_KINDS | {"seed_meta", "kb_search"}
_SEED_SUFFIX = ".seed.sqlite3"
_CONNECT_TIMEOUT_S = 5.0
_IN_CHUNK = 400  # параметров в одном IN(...) — запас от предела SQLite (обычно 999)
_SCOPE_PREFIX = "KDM1_PCBK"  # манифест и machine_scope реальных записей — этот корень или "<корень>_related"

# Task 8: имя файла-указателя текущей версии внутри ANALYTICS_CORPUS_DIR (см. докстринг модуля, «Дополнение
# Task 8»). Пишет и меняет только analytics/ingestion/versions.py; здесь — только чтение.
CURRENT_POINTER_NAME = "CURRENT"


class CorpusError(Exception):
    """Корпус не открывается или не читается: битый файл, нет файла, SQLite вернул ошибку, манифест битый или
    область не КДМ-1. Текст — без путей хоста (его показывает ручка студенту/преподавателю в 503, как
    `LessonsError`)."""


@dataclass(frozen=True)
class CorpusHandle:
    """Открытый корпус: путь к конкретному файлу `*.seed.sqlite3` (только для внутреннего журнала, не в ответ
    клиенту) и версия (см. докстринг модуля). Строит её `open_corpus`; закрывает — `close`. Соединения SQLite
    этот объект не хранит (см. докстринг модуля, «Решение Task 1 по соединению»)."""

    path: str
    version: str


def _ro_connection(path: Path) -> sqlite3.Connection:
    """Соединение `mode=ro` к конкретному файлу. Нет файла/каталога, нет прав — `CorpusError`, без пути."""
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
    except (OSError, ValueError) as exc:
        raise CorpusError(type(exc).__name__) from None
    try:
        return sqlite3.connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_S)
    except sqlite3.Error as exc:
        raise CorpusError(type(exc).__name__) from None


def _manifest(conn: sqlite3.Connection) -> dict:
    try:
        row = conn.execute("SELECT value FROM seed_meta WHERE key = 'manifest'").fetchone()
    except sqlite3.Error as exc:
        raise CorpusError(type(exc).__name__) from None
    if row is None:
        raise CorpusError("нет записи manifest в seed_meta")
    try:
        manifest = json.loads(row[0])
    except ValueError:
        raise CorpusError("манифест не читается как JSON") from None
    if not isinstance(manifest, dict) or manifest.get("scope") != _SCOPE_PREFIX:
        raise CorpusError("манифест битый или область не КДМ-1")
    return manifest


def _check_schema(conn: sqlite3.Connection) -> None:
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    except sqlite3.Error as exc:
        raise CorpusError(type(exc).__name__) from None
    missing = _REQUIRED_TABLES - names
    if missing:
        raise CorpusError("в корпусе нет таблиц: " + ",".join(sorted(missing)))


def _single_seed_file(directory: Path) -> Path | None:
    try:
        candidates = sorted(p for p in directory.iterdir() if p.is_file() and p.name.endswith(_SEED_SUFFIX))
    except OSError:
        return None
    return candidates[0] if len(candidates) == 1 else None


def _seed_file_from_pointer(root: Path, pointer: Path) -> Path:
    """Версия, на которую явно указывает `CURRENT_POINTER_NAME` (Task 8) — приоритетнее сортировки подкаталогов
    по имени (см. докстринг модуля, «Дополнение Task 8»). Указатель повреждён/ссылается не на прямой подкаталог
    `root` или в нём не ровно один файл корпуса — `CorpusError`, без угадывания и без падения назад на
    сортировку по имени (иначе откат на старую версию иногда «срабатывал» бы, а иногда тихо открывал не ту)."""
    try:
        name = pointer.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CorpusError(type(exc).__name__) from None
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise CorpusError("указатель текущей версии повреждён")
    version_dir = root / name
    if not version_dir.is_dir():
        raise CorpusError("указатель текущей версии ссылается на несуществующий каталог")
    found = _single_seed_file(version_dir)
    if found is None:
        raise CorpusError("версия из указателя не содержит ровно один файл корпуса")
    return found


def _find_seed_file(root: Path) -> Path:
    """Файл корпуса из `root`: сам файл `*.seed.sqlite3`; если в `root` есть файл-указатель
    `CURRENT_POINTER_NAME` (Task 8) — версия, на которую он указывает; иначе самый новый подкаталог версий
    (`corpus_dirs`) с ровно одним таким файлом, либо (нет подкаталогов версий — заказчик пока публикует один
    файл) сам `root`, если в нём ровно один такой файл. Иначе — `CorpusError` без пути."""
    if root.is_file():
        if not root.name.endswith(_SEED_SUFFIX):
            raise CorpusError("путь не файл корпуса *.seed.sqlite3")
        return root
    if not root.is_dir():
        raise CorpusError("нет каталога или файла корпуса")
    pointer = root / CURRENT_POINTER_NAME
    if pointer.is_file():
        return _seed_file_from_pointer(root, pointer)
    versions = corpus_dirs(str(root))
    if versions:
        found = _single_seed_file(versions[0])
        if found is not None:
            return found
        raise CorpusError("в текущей версии корпуса не один файл *.seed.sqlite3")
    found = _single_seed_file(root)
    if found is None:
        raise CorpusError("файл корпуса *.seed.sqlite3 не найден")
    return found


def open_corpus(root: str) -> CorpusHandle:
    """Открыть текущую версию корпуса из `ANALYTICS_CORPUS_DIR` (или прямой путь к файлу `*.seed.sqlite3`) в
    режиме `mode=ro`. Нет каталога/файла, битая схема, `seed_meta` без `manifest`, манифест не JSON или область
    не КДМ-1 — `CorpusError`. Вызывающий — в потоке (`asyncio.to_thread`), как `AccountStore.open`: конструктор
    этого модуля (функции без состояния) не делает ввод-вывода вне самого вызова `open_corpus`."""
    path = _find_seed_file(Path(root))
    conn = _ro_connection(path)
    try:
        _check_schema(conn)
        manifest = _manifest(conn)
    finally:
        conn.close()
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError as exc:
        raise CorpusError(type(exc).__name__) from None
    version = f"{manifest.get('schema_version', '0')}:{digest}"
    return CorpusHandle(path=str(path), version=version)


def close(handle: CorpusHandle) -> None:
    """Нет постоянного соединения в `CorpusHandle` (см. докстринг модуля) — ничего закрывать. Операция оставлена
    для симметрии интерфейса и будущего кэширующего слоя; повторный вызов и уже «закрытый» хендл — не ошибка."""
    return None


def corpus_version(handle: CorpusHandle) -> str:
    """Версия для `Preanalysis.corpus_version`/`JobResponse.corpus_version` — одна и та же строка у всех
    ответов, построенных на этом соединении (повторная проверка версии между поиском и выдачей, §10, зовёт
    именно это значение, не читает файл заново)."""
    return handle.version


def version_signature(root: str) -> str:
    """Дешёвый признак текущей версии на диске (правка F4a, см. докстринг модуля, «Дополнение правки итогового
    ревью F4a») — путь выбранного файла (`_find_seed_file`) плюс его размер и время изменения, БЕЗ хеширования
    содержимого. `CorpusError`, если файл не находится — те же случаи, что `_find_seed_file`/`open_corpus`."""
    path = _find_seed_file(Path(root))
    try:
        stat = path.stat()
    except OSError as exc:
        raise CorpusError(type(exc).__name__) from None
    return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"


def get_row(handle: CorpusHandle, kind: RowKind, row_id: str) -> dict | None:
    """Одна запись `payload` (уже разобранный JSON) таблицы `kind` по `id`; нет записи — `None`. Основа
    `EvidenceCard`/`ValidationOutcome` (`models.py`): каждый `claim_id`/`mechanism_id`, который предлагает LLM
    (`LlmSelection`), проверяется здесь, а не доверяется форме идентификатора."""
    if kind not in _ROW_KINDS:
        raise ValueError(f"неизвестный вид корпуса: {kind!r}")
    conn = _ro_connection(Path(handle.path))
    try:
        row = conn.execute(f"SELECT payload FROM {kind} WHERE id = ?", (row_id,)).fetchone()  # noqa: S608
    except sqlite3.Error as exc:
        raise CorpusError(type(exc).__name__) from None
    finally:
        conn.close()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        raise CorpusError("payload записи не читается как JSON") from None


def get_rows(handle: CorpusHandle, kind: RowKind, row_ids: Sequence[str]) -> dict[str, dict]:
    """`get_row` пакетом: словарь только найденных id → payload; пропавшие id — не ошибка здесь (её поднимает
    вызывающий, у которого есть контекст — преданализ или проверка выбора LLM)."""
    if kind not in _ROW_KINDS:
        raise ValueError(f"неизвестный вид корпуса: {kind!r}")
    ids = list(dict.fromkeys(row_ids))  # дедуп, порядок не важен для словаря результата
    if not ids:
        return {}
    result: dict[str, dict] = {}
    conn = _ro_connection(Path(handle.path))
    try:
        for start in range(0, len(ids), _IN_CHUNK):
            chunk = ids[start:start + _IN_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            try:
                rows = conn.execute(  # noqa: S608 — kind сверен со словарём таблиц выше, не из пользовательского ввода
                    f"SELECT id, payload FROM {kind} WHERE id IN ({placeholders})", chunk).fetchall()
            except sqlite3.Error as exc:
                raise CorpusError(type(exc).__name__) from None
            for row_id, payload in rows:
                try:
                    result[row_id] = json.loads(payload)
                except ValueError:
                    raise CorpusError("payload записи не читается как JSON") from None
    finally:
        conn.close()
    return result


def all_ids(handle: CorpusHandle, kind: RowKind) -> tuple[str, ...]:
    """Все `id` таблицы `kind` по возрастанию (детерминированный порядок). Добавлено Task 1 сверх перечня
    `docs/kdm1/INTERFACES.md` — см. докстринг модуля, «Дополнение к INTERFACES.md»: `terms.py` перечисляет
    словарь терминов целиком, чтобы сравнить его с нормализованным словом вопроса."""
    if kind not in _ROW_KINDS:
        raise ValueError(f"неизвестный вид корпуса: {kind!r}")
    conn = _ro_connection(Path(handle.path))
    try:
        rows = conn.execute(f"SELECT id FROM {kind} ORDER BY id")  # noqa: S608
        return tuple(r[0] for r in rows.fetchall())
    except sqlite3.Error as exc:
        raise CorpusError(type(exc).__name__) from None
    finally:
        conn.close()


def corpus_report(handle: CorpusHandle) -> dict:
    """Отчёт покрытия корпуса (план, Этап 1: «рабочий сервис справки … с отчётом покрытия корпуса») — число
    записей по каждой таблице и `kb_search`, без текста фрагментов: безопасен для журнала и `/health/*`."""
    conn = _ro_connection(Path(handle.path))
    try:
        counts = {}
        for table in sorted(_ROW_KINDS):
            counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
        kb_rows = conn.execute("SELECT count(*) FROM kb_search").fetchone()[0]
    except sqlite3.Error as exc:
        raise CorpusError(type(exc).__name__) from None
    finally:
        conn.close()
    return {"version": handle.version, "counts": counts, "kb_search_rows": kb_rows}


def corpus_dirs(root: str) -> list[Path]:
    """Подкаталоги версий `ANALYTICS_CORPUS_DIR` по убыванию имени (для Task 8: список/откат версий) — только
    те, где есть хотя бы один файл `*.seed.sqlite3` (иначе подкаталог явно не версия корпуса, его пропустить, не
    упасть). «Хотя бы один», не «ровно один»: список самих версий — это не точка, которая решает, можно ли из
    подкаталога открыть корпус (этим занимается `_find_seed_file`/`open_corpus` — он требует от уже выбранного,
    самого нового подкаталога ровно один файл и иначе поднимает `CorpusError`); `corpus_dirs` — отдельная,
    более терпимая точка для Task 8 (например, показать и недособранную версию в списке, а не скрыть её молча).
    Каталога нет, не читается, версий нет — пустой список, не ошибка (как `lessons.read_index` на пустом
    пакете)."""
    base = Path(root)
    if not base.is_dir():
        return []
    try:
        entries = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    except OSError:
        return []
    result = []
    for entry in entries:
        try:
            has_seed = any(p.is_file() and p.name.endswith(_SEED_SUFFIX) for p in entry.iterdir())
        except OSError:
            continue
        if has_seed:
            result.append(entry)
    return result
