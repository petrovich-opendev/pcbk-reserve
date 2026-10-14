"""Версии корпуса: сборка/публикация/откат (план КДМ1, Task 8, Этап 8; мастер-промпт §4, §10 последний абзац).

Офлайн-шаг оператора — `python -m pcbk_core.analytics.ingestion build|publish|rollback|list` (`cli.py`). Служба
`core` эти функции не вызывает: публикация — не маршрут API (решение контроллера, Task 8, бриф).

Раскладка каталога версий (`ANALYTICS_CORPUS_DIR`, читает `analytics/knowledge.py`):

```
ANALYTICS_CORPUS_DIR/
  CURRENT                 # указатель текущей версии — одна строка с именем подкаталога (knowledge.CURRENT_POINTER_NAME)
  ingestion_log.jsonl     # журнал публикаций/откатов этого модуля — построчный JSON, только дозапись
  <version-id>/
    kdm1.seed.sqlite3     # ровно один файл корпуса — тот же инвариант, что knowledge._single_seed_file
    build_report.json     # сведения сборки (build)
    publish.json / rollback.json   # запись утверждения публикации/отката (--approved-by, время, версия)
```

`build` кладёт новую версию в каталог версий, но НЕ делает её текущей — так `knowledge.open_corpus(root)`
по-прежнему открывает прежнюю (тест Task 1 продолжает проходить без указателя; с указателем — именно он решает,
какая версия текущая, см. `knowledge.py`, «Дополнение Task 8»). `publish`/`rollback` — единственные операции,
которые меняют указатель, и делают это одним атомарным `os.replace` временного файла (мастер-промпт §10:
«публикация ответа и пакета атомарна»; тот же приём применён здесь к версии корпуса). Ни `build`, ни `publish`,
ни `rollback` никогда не удаляют и не изменяют уже существующий подкаталог версии — история версий (и любые
исторические пакеты доказательств, которые помнят свою версию по этой строке) не трогается (мастер-промпт §10
последний абзац: «откат корпуса не удаляет историю»).

Импортер (`tools/import_seed.py` пакета заказчика) не переписывается и не дублируется — `build_version`
запускает его СУБПРОЦЕССОМ в точности с тем же интерфейсом командной строки, что в его собственном README
(`--knowledge DIR --output FILE`), и разбирает его стандартный вывод. Путь к импортеру — обязательный аргумент
(`--importer`), не константа: реальный путь (`/home/dev/incoming/kdm1-package/tools/import_seed.py`) лежит вне
`core` и вне git, подставлять его в код нельзя (мог бы случайно утечь путь хоста/пакета заказчика в репозиторий,
чего требование волны явно запрещает). После сборки `build_version` независимо (не доверяя одному импортеру)
повторно проверяет результат тем же способом, что `publish`/`rollback` — референциальные связи и область."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import knowledge

_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_DEFAULT_SEED_NAME = "kdm1.seed.sqlite3"
_DEFAULT_IMPORT_TIMEOUT_S = 180.0
_LOG_FILE_NAME = "ingestion_log.jsonl"
_RESERVED_VERSION_IDS = frozenset({knowledge.CURRENT_POINTER_NAME, _LOG_FILE_NAME})


class VersionError(Exception):
    """Операция с версией корпуса отказана — указатель текущей версии (`CURRENT`) не меняется, ничего не
    публикуется наполовину (мастер-промпт §10: «сбой посреди публикации не оставляет полуопубликованного
    состояния»)."""


@dataclass(frozen=True)
class BuildResult:
    version_id: str
    path: str
    corpus_version: str
    counts: dict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_version_id(version_id: str) -> None:
    if (not version_id or not _VERSION_ID_RE.fullmatch(version_id) or version_id in _RESERVED_VERSION_IDS):
        raise VersionError(f"недопустимый идентификатор версии корпуса: {version_id!r}")


def _check_referential_integrity(handle: knowledge.CorpusHandle) -> None:
    """Та же проверка, что `test_analytics_knowledge.py::test_real_corpus_referential_integrity_no_dangling_refs`
    (Task 1), но здесь — требование публикации (решение контроллера, Task 8), не только тест: каждая ссылка
    claims/processes/terms/mechanisms на источник/фрагмент/процесс/термин должна существовать именно в ЭТОЙ
    версии корпуса, независимо от того, был ли `build` использован для её сборки."""
    sources = set(knowledge.all_ids(handle, "sources"))
    fragments = set(knowledge.all_ids(handle, "fragments"))
    processes = set(knowledge.all_ids(handle, "processes"))
    terms = set(knowledge.all_ids(handle, "terms"))
    for table in ("claims", "processes", "terms", "mechanisms"):
        ids = knowledge.all_ids(handle, table)
        for row in knowledge.get_rows(handle, table, ids).values():
            refs = row.get("source_refs", row.get("evidence_refs", []))
            if not refs:
                raise knowledge.CorpusError(f"{table}:{row.get('id')} без ссылки на доказательство")
            for ref in refs:
                if ref not in sources and ref not in fragments:
                    raise knowledge.CorpusError(f"{table}:{row.get('id')} ссылается на {ref!r} — нет в корпусе")
            for pid in row.get("process_ids", []):
                if pid not in processes:
                    raise knowledge.CorpusError(f"{table}:{row.get('id')} ссылается на процесс {pid!r} — нет")
            for tid in row.get("term_ids", []):
                if tid not in terms:
                    raise knowledge.CorpusError(f"{table}:{row.get('id')} ссылается на термин {tid!r} — нет")


def _check_scope(handle: knowledge.CorpusHandle) -> None:
    """Область КДМ-1 — помимо `manifest.scope` (его уже проверяет `knowledge.open_corpus`), независимо смотрим
    `machine_scope` каждой записи: манифест мог остаться верным, а отдельная строка — указывать на чужую машину
    (мастер-промпт §9: «чужая область — отказ», это требование публикации, не только сборки).

    Префикс — `KDM1`, НЕ `KDM1_PCBK` буквально: в настоящем корпусе заказчика (`knowledge/terms.jsonl`) у общей
    математической теории (определение коэффициента вариации) `machine_scope="KDM1_related_general_mathematics"`
    — допустимое общее знание (мастер-промпт §5: «общая теория не становится местным нормативом», но и не
    исключается из пакета), а не «<корень>_related» в буквальном смысле комментария `knowledge._SCOPE_PREFIX`.
    Буквальная проверка `startswith("KDM1_PCBK")` (как планировал этот модуль и как уже описано в докстринге
    `retrieval.py`, Task 1) отклонила бы эту настоящую, легитимную запись — найдено локальным прогоном
    `KDM1_PACKAGE_DIR`, см. отчёт задачи. Чужая машина (БДМ-2/КДМ-3) по-прежнему отклоняется: ни один её
    префикс не начинается с `KDM1`."""
    for table in ("processes", "terms", "equipment_links"):
        ids = knowledge.all_ids(handle, table)
        for row in knowledge.get_rows(handle, table, ids).values():
            scope = row.get("machine_scope")
            if scope is not None and not str(scope).startswith("KDM1"):
                raise knowledge.CorpusError(f"{table}:{row.get('id')} чужая область: {scope!r}")


def _validate_built_corpus(version_dir: Path) -> tuple[str, dict]:
    """Открыть конкретный каталог версии (не каталог версий целиком) и прогнать независимые проверки
    (referential integrity + область). Возвращает `(corpus_version, corpus_report)`. `CorpusError` —
    оборачивается в `VersionError` вызывающим."""
    handle = knowledge.open_corpus(str(version_dir))
    try:
        _check_referential_integrity(handle)
        _check_scope(handle)
        return knowledge.corpus_version(handle), knowledge.corpus_report(handle)
    finally:
        knowledge.close(handle)


def build_version(corpus_dir: str | Path, version_id: str, knowledge_dir: str | Path, importer_path: str | Path,
                  *, timeout_s: float = _DEFAULT_IMPORT_TIMEOUT_S) -> BuildResult:
    """Собрать новую версию корпуса из каталога `knowledge_dir` (jsonl + `seed_manifest.json`, формат
    `tools/import_seed.py`) в `<corpus_dir>/<version_id>/`. Не публикует её (указатель `CURRENT` не трогает) —
    см. докстринг модуля. `version_id` должен быть новым: существующие версии не переписываются."""
    _validate_version_id(version_id)
    corpus_dir = Path(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    target_dir = corpus_dir / version_id
    if target_dir.exists():
        raise VersionError(f"версия {version_id!r} уже есть в каталоге версий корпуса")
    importer_path = Path(importer_path)
    if not importer_path.is_file():
        raise VersionError(f"импортер не найден: {importer_path}")
    tmp_dir = corpus_dir / f".build-{version_id}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir()
    output = tmp_dir / _DEFAULT_SEED_NAME
    try:
        try:
            proc = subprocess.run(
                [sys.executable, str(importer_path), "--knowledge", str(knowledge_dir), "--output", str(output)],
                capture_output=True, text=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise VersionError(f"импортер не завершился за {timeout_s} с") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise VersionError(f"импортер отказал (код {proc.returncode}): {detail}")
        counts: dict = {}
        for line in reversed((proc.stdout or "").strip().splitlines()):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            counts = payload.get("counts", {})
            break
        try:
            corpus_version, corpus_report = _validate_built_corpus(tmp_dir)
        except knowledge.CorpusError as exc:
            raise VersionError(f"собранная версия не проходит независимую проверку: {exc}") from None
        build_report = {
            "version_id": version_id, "built_at": _now_iso(),
            "knowledge_dir": str(Path(knowledge_dir).resolve()), "importer": str(importer_path.resolve()),
            "counts": counts, "corpus_version": corpus_version, "corpus_report": corpus_report,
        }
        (tmp_dir / "build_report.json").write_text(
            json.dumps(build_report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    os.replace(tmp_dir, target_dir)  # атомарно на одной ФС: каталог появляется в версиях целиком или не появляется
    return BuildResult(version_id=version_id, path=str(target_dir / _DEFAULT_SEED_NAME),
                       corpus_version=corpus_version, counts=counts)


def _append_log(corpus_dir: Path, entry: dict) -> None:
    log_path = corpus_dir / _LOG_FILE_NAME
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _publish_or_rollback(corpus_dir: str | Path, version_id: str, *, approved_by: str, action: str) -> dict:
    _validate_version_id(version_id)
    if not approved_by or not approved_by.strip():
        raise VersionError("--approved-by обязателен для публикации/отката (мастер-промпт §6/§9)")
    corpus_dir = Path(corpus_dir)
    version_dir = corpus_dir / version_id
    if not version_dir.is_dir():
        raise VersionError(f"версия {version_id!r} не найдена в каталоге версий корпуса")
    try:
        corpus_version, corpus_report = _validate_built_corpus(version_dir)
    except knowledge.CorpusError as exc:
        raise VersionError(f"версия {version_id!r} не прошла проверку публикации: {exc}") from None

    journal_entry = {"action": action, "version_id": version_id, "approved_by": approved_by.strip(),
                     "at": _now_iso(), "corpus_version": corpus_version, "corpus_report": corpus_report}
    # Запись журнала версии — ДО смены указателя: если она не удалась, указатель гарантированно не менялся
    # (ниже — единственная операция, которая меняет видимую текущую версию).
    (version_dir / f"{action}.json").write_text(
        json.dumps(journal_entry, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    pointer_path = corpus_dir / knowledge.CURRENT_POINTER_NAME
    tmp_pointer = corpus_dir / f".{knowledge.CURRENT_POINTER_NAME}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    tmp_pointer.write_text(version_id, encoding="utf-8")
    os.replace(tmp_pointer, pointer_path)  # атомарная смена указателя — temp-файл + os.replace (решение контроллера)
    _append_log(corpus_dir, journal_entry)
    return journal_entry


def publish_version(corpus_dir: str | Path, version_id: str, *, approved_by: str) -> dict:
    """Опубликовать уже собранную (`build_version`) версию как текущую. Проверяет целостность/связи/область
    заново (не доверяя тому, что версия была собрана именно этим модулем) и отказывает без изменения указателя
    при любом нарушении."""
    return _publish_or_rollback(corpus_dir, version_id, approved_by=approved_by, action="publish")


def rollback_version(corpus_dir: str | Path, version_id: str, *, approved_by: str) -> dict:
    """Откатить указатель текущей версии на уже существующую (ранее собранную/опубликованную) версию. Не
    удаляет и не изменяет версию, которая была текущей до отката — мастер-промпт §10 последний абзац."""
    return _publish_or_rollback(corpus_dir, version_id, approved_by=approved_by, action="rollback")


def list_versions(corpus_dir: str | Path) -> list[dict]:
    """Список версий в каталоге (имя подкаталога, текущая ли, версия/число записей — либо `status=broken` без
    падения всего списка, если конкретная версия повреждена)."""
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        return []
    pointer_path = corpus_dir / knowledge.CURRENT_POINTER_NAME
    current = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else None
    result = []
    for version_dir in sorted((p for p in corpus_dir.iterdir() if p.is_dir() and not p.name.startswith(".")),
                              key=lambda p: p.name):
        entry: dict = {"version_id": version_dir.name, "is_current": version_dir.name == current}
        try:
            handle = knowledge.open_corpus(str(version_dir))
            try:
                entry["status"] = "ok"
                entry["corpus_version"] = knowledge.corpus_version(handle)
                entry["counts"] = knowledge.corpus_report(handle)["counts"]
            finally:
                knowledge.close(handle)
        except knowledge.CorpusError as exc:
            entry["status"] = "broken"
            entry["error"] = str(exc)
        result.append(entry)
    return result


def build_result_as_dict(result: BuildResult) -> dict:
    return asdict(result)
