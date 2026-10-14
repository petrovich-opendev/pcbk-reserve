"""Карантин входящих файлов/архивов (план КДМ1, Task 8, Этап 8; мастер-промпт §9 последний абзац, §5).

Офлайн-шаг оператора — `python -m pcbk_core.analytics.ingestion quarantine|report` (`cli.py`). Служба `core` этот
модуль не вызывает во время обычного вопроса (мастер-промпт §9: «не запускать распаковщик/парсер во время
обычного вопроса»). Ничего, кроме отчёта и заведомо безопасных извлечённых элементов, за пределы собственного
каталога элемента карантина (`<quarantine_dir>/<item_id>/`) не попадает: отклонённый целиком элемент получает
только `report.json`, без копии содержимого.

Что проверяется (чек-лист Этапа 8, п.1–3):

1. Архив — путь с обходом каталога (`..`)/абсолютный путь внутри записи (для ЛЮБОЙ записи — и файла, и
   каталога), символическая ссылка внутри записи, избыточная распаковка («zip-бомба» — по числу элементов, по
   размеру одного элемента и по сумме всех), неверное соответствие расширения реальной сигнатуре файла (ZIP или
   не ZIP), конфликт имён между записями (один путь занят файлом, другая запись требует того же пути как
   каталога — правка, раунд 1). Отказ — по каждому элементу отдельно (другие элементы того же архива при этом
   разбираются), либо по всему элементу целиком (повреждён, слишком много записей, превышен суммарный предел,
   расширение не совпадает с сигнатурой, чужая область).
2. XLSX/DOCX (оба — ZIP-контейнеры: сначала та же проверка архива, затем разбор известных частей) — формулы не
   пересчитываются (текст формулы сохраняется как есть, значение не вычисляется), макросы (`vbaProject.bin`)
   только отмечаются флагом и никогда не читаются как текст, внешние связи и командные поля документа (Word
   field codes) отмечаются, не исполняются. Пустой/нечитаемый файл — отчёт с `verdict="empty_or_unreadable"`,
   без выдуманного текста.
3. Разбор XML части — только для частей в UTF-8 БЕЗ DOCTYPE/ENTITY (правка, раунд 1, см. `_xml_rejection_reason`):
   BOM UTF-16/UTF-32, `encoding=` в прологе отличный от UTF-8, невалидный UTF-8 или найденный в декодированном
   тексте `<!DOCTYPE`/`<!ENTITY` — отказ части ДО вызова `ElementTree`, без попытки разбора.

Зависимостей, кроме стандартной библиотеки, не добавлено (в `requirements.lock` нет `openpyxl`/`python-docx`/
`defusedxml` — проверено перед реализацией, см. отчёт задачи).

## Правка, раунд 1 (проверяющий, коммит 8603a13 → см. `task-8-report.md`)

- **Critical**: `_has_doctype` искала `b"<!DOCTYPE"` побайтово в СЫРЫХ байтах части. Для XML в UTF-16 (BOM
  `FF FE`/`FE FF`, или декларация `encoding="UTF-16"` в прологе) каждый ASCII-символ хранится двумя байтами —
  подстрока `b"<!DOCTYPE"` в таком потоке НИ РАЗУ не встречается подряд, проверка молчала (`False`), а
  `ElementTree`/expat сам по BOM/прологу переключался на UTF-16, успешно разбирал DOCTYPE и раскрывал цепочку
  сущностей («миллиард смешков» — пример в `probe2_utf16_doctype_bypass.py` проверяющего). Исправлено:
  `_xml_rejection_reason` принимает часть ТОЛЬКО если это валидный UTF-8 без постороннего BOM и без
  `encoding=…`, отличного от UTF-8, в прологе — отказ до декодирования, без попытки разбора. DOCTYPE/ENTITY
  ищутся в уже декодированном тексте (эквивалентно байтовому поиску для валидного UTF-8, но решение явно не
  зависит от кодировки потока).
- **Important**: zip-энтри «a» (файл) и «a/b.txt» (файл «внутри» файла) — второй `mkdir(parents=True)` на
  `entries_dir/a` падал необработанным `FileExistsError`: `quarantine_item` падала целиком, `report.json` не
  писался, `item_dir` оставался частично записанным, и повторный вызов с тем же `item_id` упирался в «уже
  существует» навсегда. Исправлено: (а) `_resolve_path_collisions` — отдельный проход ПЕРЕД записью на диск,
  помечает конфликтующие записи (повтор пути, путь одновременно файл и каталог для другой записи) отказом с
  причиной; (б) запись элемента карантина — во временный каталог `.tmp-<id>-…` и один `os.replace` в `item_dir`
  в конце: падение на любом шаге (в т.ч. непредвиденное — каждая запись архива и сама постановка элемента
  обёрнуты в `try/except Exception`, конвертируются в отказ с причиной, не в необработанное исключение) не
  оставляет частично записанного `item_dir` — повтор того же `item_id` работает.
- **Important**: записи-каталоги получали `verdict="accepted"` ДО проверки `_safe_member_path`/символической
  ссылки — `../../evil_dir/` помечался `accepted` без извлечения (само извлечение и так было безопасно: явных
  каталогов карантин не создаёт вне `entries_dir` — ни один файл физически никуда не писался), но ОТЧЁТ вводил
  в заблуждение. Исправлено: проверка пути и символической ссылки — для всех видов записи, включая каталоги.
- Minor: проверка «путь вне карантина после нормализации» (`entries_root ∉ parents`) — теперь до
  `dest.parent.mkdir`, не после (не создаём ничего, пока не убедились, что путь внутри границы).

## Правка, раунд 2 (проверяющий, коммит `bdae9dc` → см. `task-8-report.md`): закрыт Critical не полностью

- **Critical (ещё раз)**: `_xml_rejection_reason` раунда 1 проверяла BOM и `encoding=` в прологе — но это
  ЭВРИСТИКИ по сигнатуре, а не структурная защита. UTF-16 БЕЗ BOM (ни малейшей сигнатуры в начале байт) — будь
  то с прологом `encoding="UTF-16"` (сам пролог в байтах UTF-16, наш ASCII-регэксп `_PROLOG_ENCODING_RE` его не
  находит) или вовсе без пролога — `ElementTree`/expat определяет UTF-16 по собственной эвристике (нулевые
  байты через один, приложение F спецификации XML) независимо от BOM/пролога и благополучно разбирает
  DOCTYPE/ENTITY. Старая проверка такие данные декодирует как «валидный UTF-8» (каждый отдельный байт
  ASCII-символа и каждый нулевой байт по отдельности — валидные однобайтовые UTF-8 последовательности), подряд
  `<!DOCTYPE`/`<!ENTITY` не находит (между каждой буквой — байт `\x00`) — отказа нет. Исправлено СТРУКТУРНО, не
  добавлением ещё одной сигнатуры:
  1. Любой байт `\x00` в части — немедленный отказ («в валидном UTF-8 XML его не бывает: `Char` исключает
     `#x0`»; на практике это означает отказ почти ЛЮБОГО UTF-16/UTF-32 потока ASCII-совместимого текста, с BOM
     или без, с прологом или без — структурная разметка XML (`<?xml`, теги, атрибуты) всегда ASCII, при
     кодировании в UTF-16 это всегда нулевые байты через один).
  2. Независимо от (1) — `_structural_doctype_entity_gate`: принудительный разбор `xml.parsers.expat.
     ParserCreate(encoding="UTF-8")` с `StartDoctypeDeclHandler`/`EntityDeclHandler`/`UnparsedEntityDeclHandler`,
     которые поднимают исключение на ПЕРВОМ встреченном DOCTYPE/ENTITY — разбор останавливается до того, как
     раскрытие сущностей могло бы начаться, ЛЮБАЯ кодировка, которую expat сам определит. Это не зависит от
     того, нашли ли мы NUL-байт заранее, не зависит от BOM/пролога/регистра — DOCTYPE запрещён целиком, не
     только «DOCTYPE с опасными ENTITY внутри» (легитимные части OOXML/Word DOCTYPE не содержат вовсе).
  `_parse_xml_safe` теперь — два прохода одних и тех же байт: сначала `_xml_rejection_reason` (barrier выше),
  затем (только если `None`) настоящее дерево — `ET.fromstring(body, parser=ET.XMLParser(encoding="utf-8"))`
  (тот же принудительный `encoding="utf-8"`, что и в барьере: решение «нет DOCTYPE» и дерево строятся
  одинаковой библиотекой/настройками на одних и тех же байтах — второй проход не может «увидеть» DOCTYPE,
  которого не нашёл первый). Проверено пробами `adv_a_encodings.py` проверяющего (UTF-16LE/BE без BOM, с
  прологом и без, UTF-32 без BOM — все отказаны, ничего не раскрыто) — см. отчёт задачи.
- **Important**: `os.replace(tmp_dir, item_dir)` был ВНЕ `try/except` — если `item_dir` появился между ранней
  проверкой `.exists()` и этим `os.replace` (настоящая конкурентная гонка — второй вызов с тем же `item_id`
  успел финишировать первым), `os.replace` падал необработанным `OSError` (`ENOTEMPTY` — цель не пуста), а
  `tmp_dir` оставался осиротевшим на диске. Исправлено: `os.replace` — внутри того же `try`; при `OSError` на
  этом шаге, если `item_dir` теперь существует (гонку проиграли) — удалить свой `tmp_dir`, поднять
  `QuarantineError("уже существует")`, как и при ранней проверке; если `item_dir` не появился (не гонка, а
  другая файловая ошибка) — исходный `OSError` поднимается как есть (не маскируется под «уже существует»).
- **Minor**: широкие `except Exception` (четыре места — две записи ZIP-чтения/записи в `_inspect_zip`, две
  ветки `_assess_item`) теперь явно пробрасывают `MemoryError` дальше (`if isinstance(exc, MemoryError): raise`)
  — нехватка памяти не вердикт содержимого «не читается»/«внутренняя ошибка», а условие, которое должно
  прервать процесс, не быть тихо проглоченным с переходом к следующей записи."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
import xml.parsers.expat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

DEFAULT_SCOPE_PREFIX = "KDM1_PCBK"  # тот же корень, что knowledge._SCOPE_PREFIX/machine_scope (своя константа:
                                     # имя там приватное, дублирование устойчивой строки дешевле импорта приватного)
_ZIP_MAGICS: tuple[bytes, ...] = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_NS_XLSX_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_DOCX_MAIN = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Правка, раунд 1 — безопасная кодировка XML-частей (см. докстринг модуля, «Правка, раунд 1», Critical).
_BOM_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xfe\x00\x00", "UTF-32 LE"),   # длинные сигнатуры — ПЕРЕД короткими (UTF-32 LE начинается как UTF-16 LE)
    (b"\x00\x00\xfe\xff", "UTF-32 BE"),
    (b"\xff\xfe", "UTF-16 LE"),
    (b"\xfe\xff", "UTF-16 BE"),
)
_UTF8_BOM = b"\xef\xbb\xbf"
_PROLOG_ENCODING_RE = re.compile(rb'<\?xml[^>]{0,200}?\bencoding\s*=\s*["\']([A-Za-z0-9_.:-]+)["\']',
                                 re.IGNORECASE)


class _ForbiddenXmlConstruct(Exception):
    """Внутренний сигнал (правка, раунд 2): `_structural_doctype_entity_gate` поднимает его из обработчика
    expat на ПЕРВОМ встреченном DOCTYPE/ENTITY — разбор останавливается немедленно, до какого-либо раскрытия
    сущностей. Наружу модуля не выходит — перехватывается внутри той же функции и превращается в причину
    отказа."""


class QuarantineError(Exception):
    """Элемент не поставлен в карантин вообще (не найден исходный путь, занятый/недопустимый идентификатор) —
    это ошибка вызова команды, не вердикт содержимого; для вердиктов содержимого см. `report["verdict"]`."""


@dataclass(frozen=True)
class Limits:
    """Пределы проверки одного элемента. Промышленные значения — поля по умолчанию; тесты передают меньшие,
    чтобы не гонять мегабайты ради проверки одной и той же логики предела."""

    max_submitted_bytes: int = 100 * 1024 * 1024
    max_entries: int = 2000
    max_entry_bytes: int = 50 * 1024 * 1024
    max_total_bytes: int = 200 * 1024 * 1024
    max_ratio: int = 100  # declared file_size / compress_size — порог «подозрительное сжатие»


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_item_id(item_id: str) -> None:
    if not _ITEM_ID_RE.fullmatch(item_id):
        raise QuarantineError(f"недопустимый идентификатор элемента карантина: {item_id!r}")


def _classify_by_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return "archive"
    if suffix == ".xlsx":
        return "xlsx"
    if suffix == ".docx":
        return "docx"
    return "other"


def _safe_member_path(name: str) -> str | None:
    """Относительный, безопасный POSIX-путь элемента архива, либо `None` — обход каталога (`..`), абсолютный
    путь (POSIX `/...` или Windows `C:...`), NUL-байт или пустое имя. Вызывается для ЛЮБОГО вида записи —
    файла и каталога одинаково (правка, раунд 1, Important: раньше каталоги эту проверку обходили)."""
    if not name or "\x00" in name:
        return None
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return None
    first = normalized.split("/", 1)[0]
    if len(first) == 2 and first[1] == ":":  # "C:/..." — диск Windows
        return None
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def _resolve_path_collisions(plan: list[dict]) -> None:
    """Проход ПЕРЕД записью на диск (правка, раунд 1, Important): помечает отказом конфликтующие пути — точный
    повтор пути (две записи архива с одинаковым путём, независимо от вида — первая побеждает, вторая
    отклоняется) и «путь одновременно файл для одной записи и каталог на пути другой» (`probe1`/`probe_dir_vs_
    file`: запись «a» файлом, запись «a/b.txt» — тоже файлом «внутри» него; без этой проверки второй
    `mkdir(parents=True)` падает `FileExistsError`). Каталог как РОДИТЕЛЬ другого элемента — это нормальная
    вложенность, не конфликт (блокирует только ФАЙЛ на пути другого элемента)."""
    accepted = [e for e in plan if e.get("verdict") == "accepted" and "_safe_path" in e]
    seen: dict[str, dict] = {}
    for entry in accepted:
        path = entry["_safe_path"]
        existing = seen.get(path)
        if existing is not None:
            entry["verdict"] = "rejected"
            entry["reason"] = "повтор пути внутри архива — конфликт с другим элементом"
            continue
        seen[path] = entry
    still_accepted = [e for e in accepted if e.get("verdict") == "accepted"]
    for entry in still_accepted:
        parts = entry["_safe_path"].split("/")
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            blocker = seen.get(ancestor)
            if blocker is not None and blocker.get("verdict") == "accepted" and not blocker.get("_is_dir"):
                blocker["verdict"] = "rejected"
                blocker["reason"] = "конфликт имён: путь используется как каталог для другого элемента архива"


def _inspect_zip(path: Path, entries_dir: Path, limits: Limits) -> tuple[str, list[dict], dict]:
    """Безопасно разобрать ZIP-контейнер (сам `.zip`, либо `.xlsx`/`.docx` — оба тоже ZIP). Возвращает
    `(вердикт_целиком, отчёт_по_элементам, доп_сведения)`. Три прохода: (1) собрать план — какие записи
    безопасны по пути/ссылке/размеру, без записи на диск; (2) `_resolve_path_collisions` — отклонить
    конфликтующие пути; (3) записать на диск только то, что всё ещё `accepted`. Ни один байт содержимого не
    попадает на диск, если решение по архиву в целом — отказ (суммарный предел превышен, слишком много
    элементов, архив повреждён). Любая непредвиденная ошибка на отдельной записи (правка, раунд 1) —
    `verdict="rejected"` этой записи с причиной, не исключение, крадущее всю постановку в карантин."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return "rejected", [], {"reason": f"архив повреждён или не читается: {type(exc).__name__}"}
    try:
        infos = zf.infolist()
        if len(infos) > limits.max_entries:
            return "rejected", [], {
                "reason": f"слишком много элементов архива ({len(infos)} > {limits.max_entries}) — возможна zip-бомба"}
        plan: list[dict] = []
        total = 0
        for info in infos:
            entry: dict = {"name": info.filename, "declared_size": info.file_size,
                           "compressed_size": info.compress_size,
                           "kind": "directory" if info.is_dir() else "file"}
            try:
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    entry["verdict"], entry["reason"] = "rejected", "символическая ссылка внутри архива"
                    plan.append(entry)
                    continue
                safe_rel = _safe_member_path(info.filename)
                if safe_rel is None:
                    entry["verdict"], entry["reason"] = "rejected", "путь с обходом каталога или абсолютный путь"
                    plan.append(entry)
                    continue
                if info.is_dir():
                    entry["verdict"] = "accepted"
                    entry["_safe_path"], entry["_is_dir"] = safe_rel, True
                    plan.append(entry)
                    continue
                if (info.file_size and info.compress_size
                        and info.file_size / info.compress_size > limits.max_ratio):
                    entry["verdict"], entry["reason"] = "rejected", "аномальное соотношение сжатия (возможна zip-бомба)"
                    plan.append(entry)
                    continue
                with zf.open(info) as fh:
                    # Чтение с пределом: ZipExtFile распаковывает ровно столько, сколько запрошено — это и
                    # есть защита от zip-бомбы, а не доверие заявленному в метаданных `file_size` (подделывается).
                    data = fh.read(limits.max_entry_bytes + 1)
            except Exception as exc:  # noqa: BLE001 — одна битая запись не должна ронять постановку в карантин целиком
                if isinstance(exc, MemoryError):  # правка, раунд 2, Minor: нехватка памяти — не вердикт элемента
                    raise
                entry["verdict"], entry["reason"] = "rejected", f"не читается: {type(exc).__name__}"
                plan.append(entry)
                continue
            if len(data) > limits.max_entry_bytes:
                entry["verdict"], entry["reason"] = "rejected", "превышен предел распакованного размера элемента"
                plan.append(entry)
                continue
            total += len(data)
            if total > limits.max_total_bytes:
                return "rejected", [], {
                    "reason": "суммарный распакованный размер архива превышает предел (возможна zip-бомба)"}
            entry["verdict"], entry["actual_size"] = "accepted", len(data)
            entry["_safe_path"], entry["_is_dir"], entry["_data"] = safe_rel, False, data
            plan.append(entry)

        _resolve_path_collisions(plan)

        accepted = 0
        # .resolve() не требует существования пути — можно звать до первого mkdir (минорная правка, р.1:
        # проверка границы карантина — ДО создания каталогов, не после).
        entries_root = entries_dir.resolve()
        for entry in plan:
            if entry.get("verdict") != "accepted" or "_safe_path" not in entry:
                continue
            try:
                safe_rel = entry["_safe_path"]
                dest = entries_dir / safe_rel
                dest_resolved = dest.resolve()
                if entries_root != dest_resolved and entries_root not in dest_resolved.parents:
                    entry["verdict"], entry["reason"] = "rejected", "путь вне карантина после нормализации"
                    continue
                if entry["_is_dir"]:
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(entry["_data"])
                accepted += 1
            except Exception as exc:  # noqa: BLE001 — та же дисциплина, что при чтении: одна запись не рушит всё
                if isinstance(exc, MemoryError):  # правка, раунд 2, Minor
                    raise
                entry["verdict"], entry["reason"] = "rejected", f"не удалось записать: {type(exc).__name__}"
        entries_report = []
        for entry in plan:
            entry.pop("_safe_path", None)
            entry.pop("_is_dir", None)
            entry.pop("_data", None)
            entries_report.append(entry)
        if accepted == 0:
            return "empty_or_unreadable", entries_report, {"reason": "в архиве нет безопасных читаемых элементов"}
        return "accepted", entries_report, {"total_uncompressed_bytes": total, "entry_count": len(infos)}
    finally:
        zf.close()


def _forbid_doctype(*_args: object) -> None:
    raise _ForbiddenXmlConstruct("DOCTYPE запрещён — легитимные части OOXML/Word его не содержат")


def _forbid_entity(*_args: object) -> None:
    raise _ForbiddenXmlConstruct("ENTITY запрещена")


def _structural_doctype_entity_gate(data: bytes) -> str | None:
    """Структурная (не сигнатурная) защита — правка, раунд 2: принудительный разбор `xml.parsers.expat` с
    `encoding="UTF-8"` и обработчиками, которые бросают исключение на ПЕРВОМ встреченном DOCTYPE/ENTITY.
    Останавливает разбор ДО какого-либо раскрытия сущностей независимо от того, в какой кодировке expat сам
    определит поток (по BOM, по эвристике нулевых байт приложения F спецификации XML, как угодно) — поэтому
    не обходится никакой перестановкой BOM/пролога/регистра, в отличие от проверок по сигнатуре. `None` —
    DOCTYPE/ENTITY не встречен и сами байты разбираются как корректный XML; иначе — причина отказа."""
    parser = xml.parsers.expat.ParserCreate(encoding="UTF-8")
    parser.StartDoctypeDeclHandler = _forbid_doctype
    parser.EntityDeclHandler = _forbid_entity
    parser.UnparsedEntityDeclHandler = _forbid_entity
    parser.ExternalEntityRefHandler = lambda *_args: False  # отказ, если бы дело дошло до запроса внешней сущности
    try:
        parser.Parse(data, True)
    except _ForbiddenXmlConstruct as exc:
        return str(exc)
    except xml.parsers.expat.ExpatError as exc:
        return f"XML не разбирается: {exc}"
    return None


def _xml_rejection_reason(data: bytes) -> str | None:
    """`None` — часть безопасна для разбора; иначе — причина отказа (правка, раунд 1 и раунд 2, Critical).

    Части OOXML/Word допустимы ТОЛЬКО в UTF-8 (так их пишет легитимный пакет — ни Excel, ни Word не кладут туда
    UTF-16/UTF-32). Раунд 1 проверял BOM/`encoding=` в прологе — это СИГНАТУРЫ, их можно обойти: UTF-16 БЕЗ BOM
    (с прологом в самих байтах UTF-16, которого наш ASCII-регэксп не находит, или вовсе без пролога) не даёт ни
    одной из этих сигнатур, а `ElementTree`/expat определяет кодировку своей собственной эвристикой (не только
    по BOM) и благополучно разбирает DOCTYPE/ENTITY (`adv_a_encodings.py` проверяющего, раунд 2). Отсюда два
    независимых, СТРУКТУРНЫХ барьера (не «ещё одна сигнатура»):
    1. Любой байт `\\x00` в данных — немедленный отказ. В валидном UTF-8 XML нулевой байт не встречается
       (`Char` XML 1.0 исключает `#x0`); на практике любой реальный UTF-16/UTF-32 поток текста содержит его на
       КАЖДОМ ASCII-символе структурной разметки (`<`, `?`, имена тегов) — с BOM или без, с прологом или без.
    2. `_structural_doctype_entity_gate` — принудительный разбор с запрещёнными DOCTYPE/ENTITY (см. её
       докстринг). Это задний рубеж, не зависящий от того, нашёл ли пункт 1 нулевой байт заранее: даже если
       какая-то ДРУГАЯ, не предусмотренная здесь кодировка проскочит проверку 1, раскрытие сущностей всё равно
       структурно невозможно — разбор прерывается на самом DOCTYPE.
    BOM/`encoding=`-в-прологе проверки раунда 1 оставлены как быстрый, более говорящий отказ для типичных
    случаев (не единственная защита — ею больше не является ни одна отдельная проверка)."""
    if b"\x00" in data:
        return "обнаружен NUL-байт — недопустим в UTF-8 XML (вероятна UTF-16/UTF-32 часть без BOM)"
    for signature, name in _BOM_SIGNATURES:
        if data.startswith(signature):
            return f"недопустимая кодировка XML: BOM {name} (принимается только UTF-8)"
    body = data[len(_UTF8_BOM):] if data.startswith(_UTF8_BOM) else data
    prolog_match = _PROLOG_ENCODING_RE.search(body[:1024])
    if prolog_match:
        declared = prolog_match.group(1).decode("ascii", errors="replace").lower()
        if declared not in ("utf-8", "utf8"):
            return f"недопустимая кодировка XML в прологе: {declared!r} (принимается только UTF-8)"
    return _structural_doctype_entity_gate(body)


def _parse_xml_safe(data: bytes) -> ET.Element | None:
    """Дерево — только ПОСЛЕ того, как `_xml_rejection_reason` (включая структурный запрет DOCTYPE/ENTITY)
    подтвердил безопасность тех же байт; разбор — тем же принудительным `encoding="utf-8"`, что и барьер
    (правка, раунд 2): второй проход не может «увидеть» DOCTYPE, которого не нашёл первый, — те же байты, та
    же библиотека, те же настройки кодировки."""
    if _xml_rejection_reason(data) is not None:
        return None
    body = data[len(_UTF8_BOM):] if data.startswith(_UTF8_BOM) else data
    try:
        return ET.fromstring(body, parser=ET.XMLParser(encoding="utf-8"))
    except ET.ParseError:
        return None


def _xlsx_facts(entries_dir: Path) -> dict:
    """Структурные факты XLSX из уже безопасно извлечённых (пределы размера/пути пройдены) частей: листы,
    скрытые листы, формулы (текст формулы — без пересчёта значения), макросы и внешние связи — только флагом,
    не как текст для модели."""
    facts: dict = {"sheets": [], "hidden_sheets": [], "formula_cells": [], "has_macros": False,
                   "has_external_links": False, "external_link_targets": [], "unsafe_xml_detected": False,
                   "unsafe_xml_reasons": {}}
    if (entries_dir / "xl" / "vbaProject.bin").is_file():
        facts["has_macros"] = True
    workbook_path = entries_dir / "xl" / "workbook.xml"
    if workbook_path.is_file():
        data = workbook_path.read_bytes()
        reason = _xml_rejection_reason(data)
        if reason is not None:
            facts["unsafe_xml_detected"] = True
            facts["unsafe_xml_reasons"]["xl/workbook.xml"] = reason
        else:
            root = _parse_xml_safe(data)
            if root is not None:
                sheets_el = root.find(f"{_NS_XLSX_MAIN}sheets")
                if sheets_el is not None:
                    for sheet in sheets_el.findall(f"{_NS_XLSX_MAIN}sheet"):
                        name = sheet.get("name", "")
                        facts["sheets"].append(name)
                        if sheet.get("state") in ("hidden", "veryHidden"):
                            facts["hidden_sheets"].append(name)
    rels_path = entries_dir / "xl" / "_rels" / "workbook.xml.rels"
    if rels_path.is_file():
        data = rels_path.read_bytes()
        reason = _xml_rejection_reason(data)
        if reason is not None:
            facts["unsafe_xml_detected"] = True
            facts["unsafe_xml_reasons"]["xl/_rels/workbook.xml.rels"] = reason
        else:
            root = _parse_xml_safe(data)
            if root is not None:
                for rel in root:
                    if rel.get("TargetMode") == "External":
                        facts["has_external_links"] = True
                        facts["external_link_targets"].append(rel.get("Target", ""))
    worksheets_dir = entries_dir / "xl" / "worksheets"
    if worksheets_dir.is_dir():
        for sheet_file in sorted(worksheets_dir.glob("sheet*.xml")):
            data = sheet_file.read_bytes()
            reason = _xml_rejection_reason(data)
            if reason is not None:
                facts["unsafe_xml_detected"] = True
                facts["unsafe_xml_reasons"][f"xl/worksheets/{sheet_file.name}"] = reason
                continue
            root = _parse_xml_safe(data)
            if root is None:
                continue
            for cell in root.iter(f"{_NS_XLSX_MAIN}c"):
                f_el = cell.find(f"{_NS_XLSX_MAIN}f")
                if f_el is not None:
                    facts["formula_cells"].append(
                        {"sheet_file": sheet_file.name, "cell": cell.get("r", ""), "formula": f_el.text or ""})
    return facts


def _docx_facts(entries_dir: Path) -> dict:
    """Структурные факты DOCX: макросы и внешние связи — флагом; командные поля документа (Word field codes,
    например `INCLUDETEXT`/`DDE`) — текст инструкции сохраняется как найденный факт, но нигде не исполняется
    (у нас и нет движка Word, который мог бы её исполнить) — мастер-промпт §9: «команды документов не
    выполняются»."""
    facts: dict = {"has_macros": False, "has_external_links": False, "external_link_targets": [],
                   "field_code_instructions": [], "unsafe_xml_detected": False, "unsafe_xml_reasons": {}}
    if (entries_dir / "word" / "vbaProject.bin").is_file():
        facts["has_macros"] = True
    rels_path = entries_dir / "word" / "_rels" / "document.xml.rels"
    if rels_path.is_file():
        data = rels_path.read_bytes()
        reason = _xml_rejection_reason(data)
        if reason is not None:
            facts["unsafe_xml_detected"] = True
            facts["unsafe_xml_reasons"]["word/_rels/document.xml.rels"] = reason
        else:
            root = _parse_xml_safe(data)
            if root is not None:
                for rel in root:
                    if rel.get("TargetMode") == "External":
                        facts["has_external_links"] = True
                        facts["external_link_targets"].append(rel.get("Target", ""))
    document_path = entries_dir / "word" / "document.xml"
    if document_path.is_file():
        data = document_path.read_bytes()
        reason = _xml_rejection_reason(data)
        if reason is not None:
            facts["unsafe_xml_detected"] = True
            facts["unsafe_xml_reasons"]["word/document.xml"] = reason
        else:
            root = _parse_xml_safe(data)
            if root is not None:
                for instr in root.iter(f"{_NS_DOCX_MAIN}instrText"):
                    if instr.text:
                        facts["field_code_instructions"].append(instr.text)
                for simple in root.iter(f"{_NS_DOCX_MAIN}fldSimple"):
                    instr = simple.get(f"{_NS_DOCX_MAIN}instr")
                    if instr:
                        facts["field_code_instructions"].append(instr)
    return facts


def _assess_item(source: Path, scope: str, limits: Limits, item_id: str, work_dir: Path) -> dict:
    """Вся логика вердикта одного элемента — пишет найденные безопасные части ТОЛЬКО внутрь `work_dir`
    (временный каталог постановки, см. `quarantine_item`) и всегда ВОЗВРАЩАЕТ отчёт, никогда не поднимает
    исключение дальше себя (правка, раунд 1, Important: непредвиденная ошибка одного элемента → вердикт
    `rejected` с причиной, а не крах всей постановки в карантин)."""
    report: dict = {"item_id": item_id, "submitted_name": source.name, "declared_scope": scope,
                    "verdict": None, "reason": None, "kind": None, "details": {}}

    if source.is_symlink():
        report["verdict"], report["reason"] = "rejected", "источник — символическая ссылка"
        return report
    if not source.is_file():
        report["verdict"], report["reason"] = "rejected", "источник не обычный файл"
        return report
    st = source.stat()
    if st.st_nlink > 1:
        report["verdict"], report["reason"] = "rejected", "источник имеет более одной жёсткой ссылки"
        return report
    report["submitted_size"] = st.st_size
    if not scope.startswith(DEFAULT_SCOPE_PREFIX):
        report["verdict"], report["reason"] = "rejected", f"чужая область: {scope!r} не относится к КДМ-1"
        return report
    if st.st_size > limits.max_submitted_bytes:
        report["verdict"] = "rejected"
        report["reason"] = f"файл превышает предел размера ({st.st_size} > {limits.max_submitted_bytes} байт)"
        return report
    if st.st_size == 0:
        report["verdict"], report["reason"] = "empty_or_unreadable", "пустой файл — отчёт без выдуманного текста"
        return report

    try:
        with source.open("rb") as fh:
            magic = fh.read(4)
    except OSError as exc:
        report["verdict"], report["reason"] = "rejected", f"источник не читается: {type(exc).__name__}"
        return report
    is_zip_magic = magic in _ZIP_MAGICS
    claimed = _classify_by_extension(source)
    report["kind"] = claimed
    if claimed in ("archive", "xlsx", "docx") and not is_zip_magic:
        report["verdict"] = "rejected"
        report["reason"] = "расширение заявляет ZIP-контейнер (zip/xlsx/docx), но содержимое — не ZIP"
        return report
    if claimed == "other" and is_zip_magic:
        report["verdict"] = "rejected"
        report["reason"] = "расширение не ZIP-контейнер, но содержимое — ZIP (неверный MIME/расширение)"
        return report

    if claimed in ("archive", "xlsx", "docx"):
        entries_dir = work_dir / "entries"
        try:
            verdict, entries, meta = _inspect_zip(source, entries_dir, limits)
            report["verdict"] = verdict
            report["details"]["entries"] = entries
            report["details"].update(meta)
            if verdict != "accepted":
                report["reason"] = meta.get("reason")
            elif claimed == "xlsx":
                report["details"]["xlsx"] = _xlsx_facts(entries_dir)
            elif claimed == "docx":
                report["details"]["docx"] = _docx_facts(entries_dir)
        except Exception as exc:  # noqa: BLE001 — последний рубеж: элемент → отказ, не крах постановки (правка р.1)
            if isinstance(exc, MemoryError):  # правка, раунд 2, Minor
                raise
            report["verdict"] = "rejected"
            report["reason"] = f"внутренняя ошибка разбора архива: {type(exc).__name__}"
            report["details"] = {"error": type(exc).__name__}
    else:
        entries_dir = work_dir / "entries"
        try:
            entries_dir.mkdir(parents=True, exist_ok=True)
            dest = entries_dir / source.name
            dest.write_bytes(source.read_bytes())
            report["verdict"] = "accepted"
            report["details"]["entries"] = [{"name": source.name, "size": st.st_size, "verdict": "accepted"}]
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, MemoryError):  # правка, раунд 2, Minor
                raise
            report["verdict"] = "rejected"
            report["reason"] = f"не удалось скопировать в карантин: {type(exc).__name__}"
    return report


def quarantine_item(source: str | Path, quarantine_dir: str | Path, *, item_id: str | None = None,
                    scope: str = DEFAULT_SCOPE_PREFIX, limits: Limits | None = None) -> dict:
    """Поставить один файл/архив в карантин под `<quarantine_dir>/<item_id>/`; всегда возвращает (и
    сохраняет как `report.json`) отчёт с `verdict` — `accepted`/`rejected`/`empty_or_unreadable`. Исключение
    поднимается только при невозможности завести элемент вовсе (нет исходного пути, занятый идентификатор) —
    не как вердикт содержимого.

    Правка, раунд 1: вся работа (разбор архива, запись безопасных частей) идёт во временный каталог
    `<quarantine_dir>/.tmp-<id>-…`, который атомарно (`os.replace`) становится `<quarantine_dir>/<id>` только
    ПОСЛЕ того, как отчёт полностью готов и записан. Любой сбой по пути (в т.ч. непредвиденный — `_assess_item`
    сама превращает ожидаемые ошибки элемента в вердикт, это второй, внешний рубеж) удаляет временный каталог
    и поднимает исключение дальше, НЕ оставляя `<quarantine_dir>/<id>` в частично записанном виде — повторный
    вызов с тем же `item_id` после сбоя не упирается в «уже существует» (было: `probe5_stuck_after_crash.py`
    проверяющего).

    Правка, раунд 2: сам `os.replace` — тоже внутри этой защиты (раньше был снаружи `try`). Настоящая гонка
    (конкурентный вызов с тем же `item_id` успел финишировать первым между раннем `.exists()` выше и этим
    `os.replace`) теперь даёт тот же `QuarantineError("уже существует")`, а не необработанный `OSError` с
    осиротевшим `tmp_dir` (`adv_c_race.py` проверяющего)."""
    limits = limits or Limits()
    source = Path(source)
    quarantine_dir = Path(quarantine_dir)
    if not source.exists() and not source.is_symlink():
        raise QuarantineError(f"исходный путь не найден: {source}")
    if item_id is None:
        item_id = (_sha256_file(source)[:16] if source.is_file() and not source.is_symlink()
                   else hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16])
    _validate_item_id(item_id)
    item_dir = quarantine_dir / item_id
    if item_dir.exists():
        raise QuarantineError(f"элемент карантина {item_id!r} уже существует")

    quarantine_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = quarantine_dir / f".tmp-{item_id}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir()
    try:
        report = _assess_item(source, scope, limits, item_id, tmp_dir)
        (tmp_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        # Правка, раунд 2, Important: os.replace — ВНУТРИ того же try. Если item_dir появился между раннем
        # .exists() выше и этим моментом (настоящая гонка — конкурентный вызов с тем же item_id финишировал
        # первым), os.replace падает OSError (цель — непустой каталог); отличаем гонку (item_dir теперь
        # существует — проигравший получает тот же QuarantineError, что и при ранней проверке) от прочих
        # файловых ошибок (поднимаем как есть — не маскируем под «уже существует»).
        os.replace(tmp_dir, item_dir)
    except OSError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if item_dir.exists():
            raise QuarantineError(f"элемент карантина {item_id!r} уже существует") from None
        raise exc
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return report


def read_report(quarantine_dir: str | Path, item_id: str) -> dict:
    """Отчёт ранее поставленного в карантин элемента (команда `report <id>`)."""
    path = Path(quarantine_dir) / item_id / "report.json"
    if not path.is_file():
        raise QuarantineError(f"элемент карантина не найден: {item_id!r}")
    return json.loads(path.read_text(encoding="utf-8"))
