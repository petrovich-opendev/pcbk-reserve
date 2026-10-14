"""Карантин входящих файлов/архивов и версии корпуса (план КДМ1, Task 8, Этап 8;
`pcbk_core.analytics.ingestion.{quarantine,versions,cli}`).

Синтетика: версии корпуса собираются из синтетического знания (`fixtures/kdm1_corpus.py`, теги только
`20FAKE_*`), архивы/XLSX/DOCX для проверки карантина строятся прямо здесь (`zipfile`, `tmp_path`) — без новых
зависимостей (в `requirements.lock` нет `openpyxl`/`python-docx`/`defusedxml`). Настоящий импортер пакета
заказчика (`tools/import_seed.py`) не используется в этом файле вообще (он вне git) — вместо него подставной
скрипт того же интерфейса командной строки (`--knowledge DIR --output FILE`), чтобы проверить именно обёртку
(`versions.build_version` запускает импортер субпроцессом и разбирает его вывод), а не переписанную логику
импортера."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest

from fixtures.kdm1_corpus import DEFAULT_COUNT, FAKE_SCOPE, _PAYLOAD_TABLES, _ROW_BUILDERS, build_corpus
from pcbk_core.analytics import knowledge
from pcbk_core.analytics.ingestion import cli, quarantine, versions

# --- помощники сборки тестовых архивов/XLSX/DOCX ---


def _make_zip(path: Path, entries: list[dict]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for e in entries:
            info = zipfile.ZipInfo(e["name"])
            info.external_attr = (0o120777 << 16) if e.get("is_symlink") else (0o100644 << 16)
            zf.writestr(info, e["data"])
    return path


_XLSX_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_RELS_NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
_DOCX_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _workbook_xml(*, sheets: str = '<sheet name="Лист1" sheetId="1"/>', doctype: bool = False) -> bytes:
    prefix = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    if doctype:
        prefix += '<!DOCTYPE workbook [<!ENTITY x "y">]>'
    return f'{prefix}<workbook {_XLSX_NS}><sheets>{sheets}</sheets></workbook>'.encode("utf-8")


def _worksheet_xml(cells: str) -> bytes:
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<worksheet {_XLSX_NS}><sheetData><row r="1">{cells}</row></sheetData></worksheet>')
    return xml.encode("utf-8")


def _rels_external_xml(target: str) -> bytes:
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<Relationships {_RELS_NS}>'
          f'<Relationship Id="rId1" Type="external" Target="{target}" TargetMode="External"/>'
          f'</Relationships>')
    return xml.encode("utf-8")


def _make_xlsx(path: Path, *, formula: bool = False, macro: bool = False, external_link: bool = False,
              hidden_sheet: bool = False, doctype: bool = False) -> Path:
    entries = [{"name": "[Content_Types].xml", "data": b"<Types/>"}]
    sheets = '<sheet name="Лист1" sheetId="1"/>'
    if hidden_sheet:
        sheets += '<sheet name="Скрытый" sheetId="2" state="hidden"/>'
    entries.append({"name": "xl/workbook.xml", "data": _workbook_xml(sheets=sheets, doctype=doctype)})
    cells = '<c r="A1"><v>1</v></c>'
    if formula:
        cells += '<c r="B1"><f>SUM(A1:A1)</f><v>1</v></c>'
    entries.append({"name": "xl/worksheets/sheet1.xml", "data": _worksheet_xml(cells)})
    if macro:
        entries.append({"name": "xl/vbaProject.bin", "data": b"\x00MACROBYTES\x00secret-vba-blob"})
    if external_link:
        entries.append({"name": "xl/_rels/workbook.xml.rels",
                        "data": _rels_external_xml("http://example.invalid/ext.xlsx")})
    return _make_zip(path, entries)


def _document_xml(*, field_code: bool = False) -> bytes:
    body = '<w:p><w:r><w:t>demo</w:t></w:r></w:p>'
    if field_code:
        body += '<w:p><w:r><w:instrText> INCLUDETEXT "C:\\\\secret.docx" </w:instrText></w:r></w:p>'
    xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_DOCX_NS}><w:body>{body}</w:body></w:document>'
    return xml.encode("utf-8")


def _make_docx(path: Path, *, macro: bool = False, external_link: bool = False, field_code: bool = False) -> Path:
    entries = [{"name": "[Content_Types].xml", "data": b"<Types/>"},
              {"name": "word/document.xml", "data": _document_xml(field_code=field_code)}]
    if macro:
        entries.append({"name": "word/vbaProject.bin", "data": b"\x00MACROBYTES\x00secret-vba-blob"})
    if external_link:
        entries.append({"name": "word/_rels/document.xml.rels",
                        "data": _rels_external_xml("http://example.invalid/ext.docx")})
    return _make_zip(path, entries)


# --- quarantine: архивы — путь/ссылки/zip-бомба/MIME ---


def test_quarantine_rejects_path_traversal_entry_but_keeps_safe_ones(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [
        {"name": "safe.txt", "data": b"ok"},
        {"name": "../../etc/evil.txt", "data": b"evil"},
    ])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    assert report["verdict"] == "accepted"
    entries = {e["name"]: e for e in report["details"]["entries"]}
    assert entries["safe.txt"]["verdict"] == "accepted"
    assert entries["../../etc/evil.txt"]["verdict"] == "rejected"
    assert "обходом каталога" in entries["../../etc/evil.txt"]["reason"]
    extracted = list((tmp_path / "q" / report["item_id"] / "entries").rglob("*"))
    assert all("evil" not in p.name for p in extracted)
    assert not (tmp_path / "etc").exists()


def test_quarantine_rejects_absolute_path_entry(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [{"name": "/etc/passwd", "data": b"x"}])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    assert report["verdict"] == "empty_or_unreadable"  # единственный элемент — отклонён
    assert report["details"]["entries"][0]["verdict"] == "rejected"


def test_quarantine_rejects_symlink_entry_in_zip(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [
        {"name": "safe.txt", "data": b"ok"},
        {"name": "link.txt", "data": b"/etc/passwd", "is_symlink": True},
    ])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    entries = {e["name"]: e for e in report["details"]["entries"]}
    assert entries["link.txt"]["verdict"] == "rejected"
    assert "символическая ссылка" in entries["link.txt"]["reason"]
    assert entries["safe.txt"]["verdict"] == "accepted"


def test_quarantine_rejects_entry_exceeding_per_entry_limit(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [{"name": "bomb.bin", "data": b"A" * 2000}])
    report = quarantine.quarantine_item(archive, tmp_path / "q", limits=quarantine.Limits(max_entry_bytes=1000))
    entry = report["details"]["entries"][0]
    assert entry["verdict"] == "rejected"
    assert "предел распакованного размера" in entry["reason"]
    assert not (tmp_path / "q" / report["item_id"] / "entries" / "bomb.bin").exists()


def test_quarantine_rejects_cumulative_zip_bomb_nothing_extracted(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [
        {"name": f"part{i}.bin", "data": b"B" * 1000} for i in range(5)
    ])
    report = quarantine.quarantine_item(
        archive, tmp_path / "q", limits=quarantine.Limits(max_entry_bytes=1_000_000, max_total_bytes=1500))
    assert report["verdict"] == "rejected"
    assert "zip-бомба" in report["reason"] or "суммарный" in report["reason"]
    assert not (tmp_path / "q" / report["item_id"] / "entries").exists()


def test_quarantine_rejects_too_many_entries(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [{"name": f"f{i}.txt", "data": b"x"} for i in range(10)])
    report = quarantine.quarantine_item(archive, tmp_path / "q", limits=quarantine.Limits(max_entries=5))
    assert report["verdict"] == "rejected"
    assert "элементов архива" in report["reason"]
    assert not (tmp_path / "q" / report["item_id"] / "entries").exists()


def test_quarantine_accepts_benign_zip_nothing_outside_quarantine(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [{"name": "a.txt", "data": b"hello"},
                                                   {"name": "sub/b.txt", "data": b"world"}])
    qdir = tmp_path / "q"
    report = quarantine.quarantine_item(archive, qdir)
    assert report["verdict"] == "accepted"
    assert sorted(p.name for p in qdir.iterdir()) == [report["item_id"]]
    assert (qdir / report["item_id"] / "entries" / "a.txt").read_bytes() == b"hello"
    assert (qdir / report["item_id"] / "entries" / "sub" / "b.txt").read_bytes() == b"world"


def test_quarantine_rejects_extension_claims_zip_but_content_is_not(tmp_path):
    fake = tmp_path / "upload.zip"
    fake.write_bytes(b"this is not a zip file at all")
    report = quarantine.quarantine_item(fake, tmp_path / "q")
    assert report["verdict"] == "rejected"
    assert "не ZIP" in report["reason"] or "расширение" in report["reason"]
    assert not (tmp_path / "q" / report["item_id"] / "entries").exists()


def test_quarantine_rejects_extension_other_but_content_is_zip(tmp_path):
    disguised = tmp_path / "notes.txt"
    _make_zip(disguised, [{"name": "a.txt", "data": b"x"}])
    report = quarantine.quarantine_item(disguised, tmp_path / "q")
    assert report["verdict"] == "rejected"
    assert "MIME" in report["reason"] or "расширение" in report["reason"]


def test_quarantine_rejects_foreign_scope_before_reading_content(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [{"name": "a.txt", "data": b"x"}])
    report = quarantine.quarantine_item(archive, tmp_path / "q", scope="BDM2_OTHER_MACHINE")
    assert report["verdict"] == "rejected"
    assert "чужая область" in report["reason"]
    item_dir = tmp_path / "q" / report["item_id"]
    assert (item_dir / "report.json").is_file()
    assert not (item_dir / "entries").exists()


def test_quarantine_rejects_hardlinked_source(tmp_path):
    original = tmp_path / "orig.txt"
    original.write_bytes(b"content")
    linked = tmp_path / "linked.txt"
    os.link(original, linked)
    report = quarantine.quarantine_item(linked, tmp_path / "q")
    assert report["verdict"] == "rejected"
    assert "жёсткой ссылки" in report["reason"]


def test_quarantine_rejects_symlink_source(tmp_path):
    original = tmp_path / "orig.txt"
    original.write_bytes(b"content")
    link = tmp_path / "link.txt"
    link.symlink_to(original)
    report = quarantine.quarantine_item(link, tmp_path / "q")
    assert report["verdict"] == "rejected"
    assert "символическая ссылка" in report["reason"]


def test_quarantine_empty_file_no_fabricated_text(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    report = quarantine.quarantine_item(empty, tmp_path / "q")
    assert report["verdict"] == "empty_or_unreadable"
    dumped = json.dumps(report)
    assert "текст" not in report.get("details", {})
    assert "20FAKE" not in dumped and "выдум" not in dumped.lower() or "выдуманного" in report["reason"]


def test_quarantine_plain_file_accepted_without_interpretation(tmp_path):
    plain = tmp_path / "note.txt"
    plain.write_text("обычный текст без вложений", encoding="utf-8")
    report = quarantine.quarantine_item(plain, tmp_path / "q")
    assert report["verdict"] == "accepted"
    assert report["details"]["entries"][0]["name"] == "note.txt"


def test_quarantine_duplicate_item_id_raises(tmp_path):
    plain = tmp_path / "note.txt"
    plain.write_bytes(b"x")
    quarantine.quarantine_item(plain, tmp_path / "q", item_id="dup1")
    with pytest.raises(quarantine.QuarantineError):
        quarantine.quarantine_item(plain, tmp_path / "q", item_id="dup1")


def test_quarantine_missing_source_raises(tmp_path):
    with pytest.raises(quarantine.QuarantineError):
        quarantine.quarantine_item(tmp_path / "nope.zip", tmp_path / "q")


# --- quarantine: XLSX/DOCX — формулы/макросы/внешние связи/командные поля ---


def test_quarantine_xlsx_formula_not_recalculated(tmp_path):
    path = _make_xlsx(tmp_path / "model.xlsx", formula=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    assert report["verdict"] == "accepted"
    facts = report["details"]["xlsx"]
    assert facts["formula_cells"] == [{"sheet_file": "sheet1.xml", "cell": "B1", "formula": "SUM(A1:A1)"}]
    assert "value" not in facts["formula_cells"][0]  # значение формулы не вычисляется


def test_quarantine_xlsx_macro_flagged_not_as_text(tmp_path):
    path = _make_xlsx(tmp_path / "model.xlsx", macro=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    assert report["details"]["xlsx"]["has_macros"] is True
    dumped = json.dumps(report, ensure_ascii=False)
    assert "secret-vba-blob" not in dumped  # содержимое макроса никогда не попадает в отчёт как текст


def test_quarantine_xlsx_external_link_flagged(tmp_path):
    path = _make_xlsx(tmp_path / "model.xlsx", external_link=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    facts = report["details"]["xlsx"]
    assert facts["has_external_links"] is True
    assert "http://example.invalid/ext.xlsx" in facts["external_link_targets"]


def test_quarantine_xlsx_hidden_sheet_flagged(tmp_path):
    path = _make_xlsx(tmp_path / "model.xlsx", hidden_sheet=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    assert "Скрытый" in report["details"]["xlsx"]["hidden_sheets"]
    assert "Лист1" in report["details"]["xlsx"]["sheets"]


def test_quarantine_xlsx_doctype_not_parsed(tmp_path):
    path = _make_xlsx(tmp_path / "model.xlsx", doctype=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    assert report["details"]["xlsx"]["unsafe_xml_detected"] is True
    assert report["details"]["xlsx"]["sheets"] == []  # DOCTYPE — не парсим вовсе, не угадываем содержимое


def test_quarantine_docx_macro_flagged_not_as_text(tmp_path):
    path = _make_docx(tmp_path / "doc.docx", macro=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    assert report["details"]["docx"]["has_macros"] is True
    assert "secret-vba-blob" not in json.dumps(report, ensure_ascii=False)


def test_quarantine_docx_external_link_flagged(tmp_path):
    path = _make_docx(tmp_path / "doc.docx", external_link=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    facts = report["details"]["docx"]
    assert facts["has_external_links"] is True
    assert "http://example.invalid/ext.docx" in facts["external_link_targets"]


def test_quarantine_docx_field_code_not_executed_only_recorded(tmp_path):
    path = _make_docx(tmp_path / "doc.docx", field_code=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    instructions = report["details"]["docx"]["field_code_instructions"]
    assert any("INCLUDETEXT" in instr for instr in instructions)


def test_quarantine_report_roundtrip(tmp_path):
    plain = tmp_path / "note.txt"
    plain.write_bytes(b"x")
    written = quarantine.quarantine_item(plain, tmp_path / "q", item_id="rt1")
    read_back = quarantine.read_report(tmp_path / "q", "rt1")
    assert read_back == written


def test_quarantine_report_missing_raises(tmp_path):
    with pytest.raises(quarantine.QuarantineError):
        quarantine.read_report(tmp_path / "q", "nope")


# --- правка, раунд 1 (проверяющий, коммит 8603a13): UTF-16 DOCTYPE-обход, конфликт путей, повтор после сбоя ---


def _utf16_billion_laughs_workbook() -> bytes:
    """Та же форма, что `probe2_utf16_doctype_bypass.py` проверяющего: UTF-16 (с BOM — так всегда кодирует
    `str.encode("utf-16")`) workbook.xml с цепочкой ENTITY, которая раскрывается в ~10^4 символов, если
    `ElementTree` всё-таки разберёт DOCTYPE."""
    xml_text = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE workbook [\n'
        '<!ENTITY a "AAAAAAAAAA">\n'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
        ']>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheets><sheet name="&c;" sheetId="1"/></sheets>'
        '</workbook>'
    )
    return xml_text.encode("utf-16")


def test_quarantine_xlsx_utf16_doctype_entity_bomb_not_expanded(tmp_path):
    """Critical, правка р.1: раньше `_has_doctype` искала `b"<!DOCTYPE"` в сырых байтах — для UTF-16 эта
    подстрока ни разу не встречается подряд (каждый символ — 2 байта), `ElementTree` разбирал DOCTYPE в обход
    проверки и раскрывал цепочку сущностей. Теперь — отказ по BOM/кодировке ДО попытки разбора."""
    archive = _make_zip(tmp_path / "bomb.xlsx", [
        {"name": "[Content_Types].xml", "data": b"<Types/>"},
        {"name": "xl/workbook.xml", "data": _utf16_billion_laughs_workbook()},
    ])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    facts = report["details"]["xlsx"]
    assert facts["sheets"] == []  # ничего не раскрыто
    assert facts["unsafe_xml_detected"] is True
    assert "xl/workbook.xml" in facts["unsafe_xml_reasons"]
    dumped = json.dumps(report, ensure_ascii=False)
    assert "AAAAAAAAAA" not in dumped  # цепочка сущностей не раскрыта и не попала в отчёт


def test_quarantine_xml_utf16_be_bom_rejected():
    data = '<?xml version="1.0"?><workbook/>'.encode("utf-16-be")
    data = b"\xfe\xff" + data  # явный BOM UTF-16 BE без кодека, который сам его не добавляет
    reason = quarantine._xml_rejection_reason(data)
    assert reason is not None and "UTF-16" in reason


def test_quarantine_xml_prolog_declares_non_utf8_without_bom_rejected():
    """Декларация `encoding=` в прологе без BOM — вторая эвристика expat (без BOM он доверяет именно ей)."""
    data = b'<?xml version="1.0" encoding="windows-1251"?><workbook/>'
    reason = quarantine._xml_rejection_reason(data)
    assert reason is not None and "кодировка" in reason


def test_quarantine_xml_plain_utf8_with_declared_utf8_prolog_accepted():
    """Легитимные части реально пишут `encoding="UTF-8"` в прологе (видно во всех XLSX/DOCX-помощниках этого
    файла) — такая декларация не должна отклоняться наравне с настоящими посторонними кодировками."""
    data = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook/>'.encode("utf-8")
    assert quarantine._xml_rejection_reason(data) is None


def test_quarantine_dir_entry_with_path_traversal_name_rejected(tmp_path):
    """Important, правка р.1: запись-каталог «../../evil_dir/» раньше получала `verdict="accepted"` без
    проверки пути — проверка пути/ссылки теперь выполняется для записи ЛЮБОГО вида."""
    info = zipfile.ZipInfo("../../evil_dir/")
    info.external_attr = (0o040755 << 16) | 0x10
    with zipfile.ZipFile(tmp_path / "upload.zip", "w") as zf:
        zf.writestr(info, b"")
        zf.writestr("safe.txt", b"ok")
    report = quarantine.quarantine_item(tmp_path / "upload.zip", tmp_path / "q")
    entries = {e["name"]: e for e in report["details"]["entries"]}
    assert entries["../../evil_dir/"]["verdict"] == "rejected"
    assert "обходом каталога" in entries["../../evil_dir/"]["reason"]
    assert not (tmp_path / "evil_dir").exists()
    assert not (tmp_path / "q" / report["item_id"] / "evil_dir").exists()


def test_quarantine_file_then_nested_file_collision_no_crash(tmp_path):
    """Important, правка р.1 (`probe1_conflict.py` проверяющего): «a» файлом, затем «a/b.txt» файлом «внутри»
    него — второй `mkdir(parents=True)` раньше падал `FileExistsError` необработанным. Теперь — «a» отклонена
    конфликтом имён, «a/b.txt» принята и действительно лежит на диске."""
    archive = _make_zip(tmp_path / "upload.zip", [
        {"name": "a", "data": b"I am a file named a"},
        {"name": "a/b.txt", "data": b"I am supposed to be under a/"},
    ])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    assert report["verdict"] == "accepted"  # архив не отклонён целиком — отклонён только конфликтующий элемент
    entries = {e["name"]: e for e in report["details"]["entries"]}
    assert entries["a"]["verdict"] == "rejected"
    assert "конфликт имён" in entries["a"]["reason"]
    assert entries["a/b.txt"]["verdict"] == "accepted"
    entries_dir = tmp_path / "q" / report["item_id"] / "entries"
    assert (entries_dir / "a" / "b.txt").read_bytes() == b"I am supposed to be under a/"
    assert (entries_dir / "a").is_dir()  # «a» — каталог на диске, не файл: конфликт решён безопасно


def test_quarantine_dir_then_file_same_name_collision_no_crash(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [{"name": "a/", "data": b""}, {"name": "a", "data": b"file"}])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    entries = {e["name"]: e for e in report["details"]["entries"]}
    assert entries["a/"]["verdict"] == "accepted"
    assert entries["a"]["verdict"] == "rejected"
    assert (tmp_path / "q" / report["item_id"] / "entries" / "a").is_dir()


def test_quarantine_exact_duplicate_path_second_rejected(tmp_path):
    archive = _make_zip(tmp_path / "upload.zip", [{"name": "dup.txt", "data": b"first"},
                                                   {"name": "dup.txt", "data": b"second"}])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    verdicts = [e["verdict"] for e in report["details"]["entries"]]
    assert verdicts == ["accepted", "rejected"]
    assert (tmp_path / "q" / report["item_id"] / "entries" / "dup.txt").read_bytes() == b"first"


def test_quarantine_normal_dir_plus_nested_file_not_a_collision(tmp_path):
    """Контрольный пример: каталог-родитель и файл внутри него — это НЕ конфликт, обычная вложенность."""
    archive = _make_zip(tmp_path / "upload.zip", [{"name": "a/", "data": b""},
                                                   {"name": "a/b.txt", "data": b"nested"}])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    entries = {e["name"]: e for e in report["details"]["entries"]}
    assert entries["a/"]["verdict"] == "accepted"
    assert entries["a/b.txt"]["verdict"] == "accepted"


def test_quarantine_retry_with_same_id_works_after_internal_failure(tmp_path, monkeypatch):
    """Important, правка р.1 (`probe5_stuck_after_crash.py`): падение на любом шаге постановки элемента не
    должно оставлять `item_dir` частично записанным — иначе повтор с тем же `item_id` упирается в «уже
    существует» навсегда. Временный каталог + `os.replace` в конце гарантируют это независимо от ПРИЧИНЫ сбоя
    (здесь — намеренно внедрённая через monkeypatch, не обязательно конфликт путей)."""
    plain = tmp_path / "note.txt"
    plain.write_bytes(b"x")

    def _boom(*args, **kwargs):
        raise RuntimeError("внедрённый сбой для теста")

    monkeypatch.setattr(quarantine, "_assess_item", _boom)
    with pytest.raises(RuntimeError):
        quarantine.quarantine_item(plain, tmp_path / "q", item_id="retry1")
    assert not (tmp_path / "q" / "retry1").exists()  # не осталось частично записанного каталога

    monkeypatch.undo()
    report = quarantine.quarantine_item(plain, tmp_path / "q", item_id="retry1")
    assert report["verdict"] == "accepted"


def test_cli_unexpected_error_wrapped_as_json_not_bare_traceback(tmp_path, monkeypatch, capsys):
    """Important, правка р.1: cli.py раньше оборачивал только `QuarantineError`/`VersionError` — любая другая
    ошибка уходила голым traceback'ом. Теперь — тот же документированный JSON-конверт на стдерр."""
    plain = tmp_path / "note.txt"
    plain.write_bytes(b"x")

    def _boom(*args, **kwargs):
        raise RuntimeError("непредвиденная ошибка")

    monkeypatch.setattr(quarantine, "quarantine_item", _boom)
    rc = cli.main(["quarantine", str(plain), "--quarantine-dir", str(tmp_path / "q")])
    assert rc == 1
    err = capsys.readouterr().err
    payload = json.loads(err)  # не падает — значит, это JSON, а не traceback
    assert payload["status"] == "error"
    assert "RuntimeError" in payload["error"]


# --- правка, раунд 2 (проверяющий, коммит bdae9dc): UTF-16 без BOM, гонка os.replace, MemoryError ---


def _billion_laughs_body(*, with_prolog: bool) -> str:
    prolog = '<?xml version="1.0" encoding="UTF-16"?>' if with_prolog else ""
    return (
        prolog
        + '<!DOCTYPE workbook [\n'
          '<!ENTITY a "AAAAAAAAAA">\n'
          '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
          '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
          ']>\n'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<sheets><sheet name="&c;" sheetId="1"/></sheets>'
          '</workbook>'
    )


@pytest.mark.parametrize("codec,with_prolog", [
    ("utf-16-le", True), ("utf-16-le", False), ("utf-16-be", True), ("utf-16-be", False),
])
def test_quarantine_xml_rejects_utf16_without_bom_structurally(codec, with_prolog):
    """Critical, правка р.2 (`adv_a_encodings.py` проверяющего, пп.1/4a): UTF-16 БЕЗ BOM (ни с прологом,
    кодированным в тех же байтах, который наш ASCII-регэксп не находит, ни вовсе без пролога) раньше проходила
    `_xml_rejection_reason` — `ElementTree`/expat сам определял кодировку по нулевым байтам и раскрывал
    DOCTYPE/ENTITY. Теперь отказ структурный: NUL-байт присутствует почти в любой такой части (п.1), и
    независимо — принудительный разбор с запрещёнными DOCTYPE/ENTITY (п.2) ловит остальное."""
    data = _billion_laughs_body(with_prolog=with_prolog).encode(codec)
    assert b"\xff\xfe" not in data[:2] and b"\xfe\xff" not in data[:2]  # действительно без BOM
    reason = quarantine._xml_rejection_reason(data)
    assert reason is not None
    assert quarantine._parse_xml_safe(data) is None


def test_quarantine_xlsx_utf16_no_bom_entity_bomb_not_expanded_end_to_end(tmp_path):
    """Тот же сценарий целиком через `quarantine_item` (а не только `_xml_rejection_reason`) — сущности не
    раскрыты, ничего не попало в отчёт."""
    data = _billion_laughs_body(with_prolog=True).encode("utf-16-le")
    archive = _make_zip(tmp_path / "bomb_nobom.xlsx", [
        {"name": "[Content_Types].xml", "data": b"<Types/>"},
        {"name": "xl/workbook.xml", "data": data},
    ])
    report = quarantine.quarantine_item(archive, tmp_path / "q")
    facts = report["details"]["xlsx"]
    assert facts["sheets"] == []
    assert facts["unsafe_xml_detected"] is True
    dumped = json.dumps(report, ensure_ascii=False)
    assert "AAAAAAAAAA" not in dumped


def test_quarantine_xml_structural_gate_rejects_doctype_even_without_entities():
    """Запрещён DOCTYPE целиком, не только «DOCTYPE с опасными ENTITY внутри» — легитимные части OOXML/Word
    DOCTYPE не содержат вовсе."""
    data = b'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE workbook><workbook/>'
    assert quarantine._xml_rejection_reason(data) is not None


def test_quarantine_xml_legit_utf8_sheet_still_parses_after_structural_gate(tmp_path):
    """Контрольный пример: обычный, безопасный UTF-8 XML (как у остальных помощников этого файла) должен
    продолжать разбираться тем же `_parse_xml_safe` — структурная защита не должна ломать легитимный путь."""
    path = _make_xlsx(tmp_path / "model.xlsx", formula=True)
    report = quarantine.quarantine_item(path, tmp_path / "q")
    assert report["details"]["xlsx"]["formula_cells"][0]["formula"] == "SUM(A1:A1)"
    assert report["details"]["xlsx"]["unsafe_xml_detected"] is False


def test_quarantine_replace_race_loses_gracefully_no_leaked_tmp_dir(tmp_path, monkeypatch):
    """Important, правка р.2 (`adv_c_race.py` проверяющего, п.3): если `item_dir` появился между раннем
    `.exists()` и финальным `os.replace` (настоящая гонка), `os.replace` падает `OSError` (цель — непустой
    каталог) — раньше это было необработанным исключением вне `try`, с осиротевшим `.tmp-*`. Теперь — тот же
    `QuarantineError`, временный каталог убран, выигравшая сторона (её `report.json`) не тронута."""
    plain = tmp_path / "note.txt"
    plain.write_bytes(b"x")
    item_id = "race1"
    item_dir = tmp_path / "q" / item_id
    orig_assess = quarantine._assess_item

    def assess_then_inject_race(source, scope, limits, iid, work_dir):
        result = orig_assess(source, scope, limits, iid, work_dir)
        item_dir.mkdir(parents=True)
        (item_dir / "report.json").write_text('{"item_id": "race1", "verdict": "accepted", "winner": "other"}',
                                              encoding="utf-8")
        return result

    monkeypatch.setattr(quarantine, "_assess_item", assess_then_inject_race)
    with pytest.raises(quarantine.QuarantineError, match="уже существует"):
        quarantine.quarantine_item(plain, tmp_path / "q", item_id=item_id)
    monkeypatch.undo()

    leaked_tmp = [p for p in (tmp_path / "q").iterdir() if p.name.startswith(".tmp-")]
    assert leaked_tmp == []
    assert "winner" in quarantine.read_report(tmp_path / "q", item_id)


def test_quarantine_memory_error_propagates_not_converted_to_verdict(tmp_path, monkeypatch):
    """Minor, правка р.2 (`adv_d_broad_except.py` проверяющего): `MemoryError` — не вердикт элемента
    («не читается: MemoryError»), а условие, которое должно прервать процесс."""
    archive = _make_zip(tmp_path / "upload.zip", [{"name": "a.txt", "data": b"hello"}])

    def boom(*a, **kw):
        raise MemoryError("имитация нехватки памяти")

    monkeypatch.setattr(quarantine, "_safe_member_path", boom)
    with pytest.raises(MemoryError):
        quarantine.quarantine_item(archive, tmp_path / "q")
    monkeypatch.undo()
    # не осталось частично записанного item_dir — MemoryError дошёл до внешнего try/except quarantine_item,
    # который чистит tmp_dir и поднимает исключение дальше (та же дисциплина, что и в раунде 1)
    assert not (tmp_path / "q").exists() or list((tmp_path / "q").iterdir()) == []


# --- versions: build/publish/rollback/list на синтетике 20FAKE_* ---

_STUB_IMPORTER_SRC = '''\
import argparse, json, sqlite3, sys
from pathlib import Path

TABLES = ("sources", "fragments", "processes", "terms", "claims", "conflicts", "equipment_links", "mechanisms")
SEARCH_FIELD = {"claims": ("claim", "text"), "mechanisms": ("mechanism", "title"),
                "processes": ("processe", "title"), "terms": ("term", "definition")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--knowledge", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists():
        print("output exists", file=sys.stderr)
        return 1
    manifest = json.loads((args.knowledge / "seed_manifest.json").read_text(encoding="utf-8"))
    rows = {}
    for table in TABLES:
        text = (args.knowledge / f"{table}.jsonl").read_text(encoding="utf-8")
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
        if len(items) != manifest["counts"].get(table):
            print(f"count mismatch: {table}", file=sys.stderr)
            return 1
        rows[table] = items
    args.output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.output)
    try:
        conn.execute("CREATE TABLE seed_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO seed_meta VALUES (?,?)", ("manifest", json.dumps(manifest, ensure_ascii=False)))
        for table in TABLES:
            conn.execute(
                f"CREATE TABLE {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)))")
            conn.executemany(f"INSERT INTO {table} VALUES (?,?)",
                             [(row["id"], json.dumps(row, ensure_ascii=False)) for row in rows[table]])
        conn.execute(
            "CREATE VIRTUAL TABLE kb_search USING fts5(kind UNINDEXED, id UNINDEXED, text, tokenize='unicode61')")
        for table, (kind, field) in SEARCH_FIELD.items():
            for row in rows[table]:
                conn.execute("INSERT INTO kb_search VALUES (?,?,?)", (kind, row["id"], str(row.get(field, ""))))
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"status": "created_unapproved_seed", "counts": manifest["counts"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_FAILING_IMPORTER_SRC = '''\
import sys
print("импорт отказан намеренно (тест)", file=sys.stderr)
sys.exit(1)
'''


@pytest.fixture
def stub_importer(tmp_path) -> Path:
    path = tmp_path / "stub_import_seed.py"
    path.write_text(_STUB_IMPORTER_SRC, encoding="utf-8")
    return path


@pytest.fixture
def failing_importer(tmp_path) -> Path:
    path = tmp_path / "failing_import_seed.py"
    path.write_text(_FAILING_IMPORTER_SRC, encoding="utf-8")
    return path


def _write_knowledge_dir(path: Path, *, counts: dict[str, int] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    counts = {**{name: DEFAULT_COUNT for name in _PAYLOAD_TABLES}, **(counts or {})}
    for table, builder in _ROW_BUILDERS.items():
        rows = builder(counts[table])
        with (path / f"{table}.jsonl").open("w", encoding="utf-8") as fh:
            for _id, payload in rows:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    manifest = {"schema_version": "2.0", "scope": FAKE_SCOPE, "created_on": "2026-01-01", "counts": counts}
    (path / "seed_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def _manual_version_dir(corpus_dir: Path, version_id: str, *, tamper=None) -> Path:
    """Версия, собранная МИМО `versions.build_version` (как если бы оператор сам положил файл) — проверяет,
    что `publish_version` переверяет целостность независимо, не доверяя происхождению файла."""
    version_dir = corpus_dir / version_id
    version_dir.mkdir(parents=True)
    seed_path = build_corpus(version_dir / "manual.seed.sqlite3")
    if tamper is not None:
        tamper(seed_path)
    return version_dir


def _tamper_dangling_ref(seed_path: Path) -> None:
    conn = sqlite3.connect(seed_path)
    try:
        row = conn.execute("SELECT payload FROM claims WHERE id = 'CLAIM-FAKE-001'").fetchone()
        payload = json.loads(row[0])
        payload["evidence_refs"] = ["FRG-FAKE-NOPE-999"]
        conn.execute("UPDATE claims SET payload = ? WHERE id = 'CLAIM-FAKE-001'", (json.dumps(payload),))
        conn.commit()
    finally:
        conn.close()


def _tamper_foreign_scope(seed_path: Path) -> None:
    conn = sqlite3.connect(seed_path)
    try:
        row = conn.execute("SELECT payload FROM terms WHERE id = 'TERM-FAKE-001'").fetchone()
        payload = json.loads(row[0])
        payload["machine_scope"] = "BDM2_OTHER_MACHINE"
        conn.execute("UPDATE terms SET payload = ? WHERE id = 'TERM-FAKE-001'", (json.dumps(payload),))
        conn.commit()
    finally:
        conn.close()


def test_build_creates_version_not_yet_counted_as_fatal(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    result = versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    assert result.version_id == "v1"
    assert Path(result.path).is_file()
    assert (corpus_dir / "v1" / "build_report.json").is_file()
    assert result.counts["terms"] == DEFAULT_COUNT


def test_build_rejects_existing_version_id(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    with pytest.raises(versions.VersionError):
        versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)


def test_build_rejects_bad_version_id(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    with pytest.raises(versions.VersionError):
        versions.build_version(tmp_path / "corpus", "../escape", knowledge_dir, stub_importer)
    with pytest.raises(versions.VersionError):
        versions.build_version(tmp_path / "corpus", knowledge.CURRENT_POINTER_NAME, knowledge_dir, stub_importer)


def test_build_fails_when_importer_refuses_leaves_no_partial_dir(tmp_path, failing_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    with pytest.raises(versions.VersionError):
        versions.build_version(corpus_dir, "v1", knowledge_dir, failing_importer)
    assert not (corpus_dir / "v1").exists()
    assert not any(p.name.startswith(".build-") for p in corpus_dir.iterdir()) if corpus_dir.exists() else True


def test_build_unknown_importer_path_raises(tmp_path):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    with pytest.raises(versions.VersionError):
        versions.build_version(tmp_path / "corpus", "v1", knowledge_dir, tmp_path / "nope.py")


def test_build_alone_without_publish_is_visible_when_it_is_the_only_version(tmp_path, stub_importer):
    """Обратная совместимость Task 1: без указателя `CURRENT` единственный подкаталог версии открывается как
    раньше (одна версия — одна раскладка). Это не «тихая публикация» — как только появится вторая версия и
    `CURRENT` (следующий тест), поведение меняется."""
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    result = versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    handle = knowledge.open_corpus(str(corpus_dir))
    try:
        assert knowledge.corpus_version(handle) == result.corpus_version
    finally:
        knowledge.close(handle)


def test_build_second_version_not_visible_until_published(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    v1 = versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    versions.publish_version(corpus_dir, "v1", approved_by="tech1")
    versions.build_version(corpus_dir, "v2", knowledge_dir, stub_importer)
    handle = knowledge.open_corpus(str(corpus_dir))
    try:
        assert knowledge.corpus_version(handle) == v1.corpus_version  # v2 собрана, но не опубликована
    finally:
        knowledge.close(handle)


def test_publish_switches_current_version_and_pointer_file(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    result = versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    entry = versions.publish_version(corpus_dir, "v1", approved_by="tech1")
    assert entry["approved_by"] == "tech1"
    assert (corpus_dir / knowledge.CURRENT_POINTER_NAME).read_text(encoding="utf-8").strip() == "v1"
    handle = knowledge.open_corpus(str(corpus_dir))
    try:
        assert knowledge.corpus_version(handle) == result.corpus_version
    finally:
        knowledge.close(handle)


def test_publish_requires_approved_by(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    with pytest.raises(versions.VersionError):
        versions.publish_version(corpus_dir, "v1", approved_by="   ")
    assert not (corpus_dir / knowledge.CURRENT_POINTER_NAME).exists()


def test_publish_unknown_version_refuses_pointer_unchanged(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    versions.publish_version(corpus_dir, "v1", approved_by="tech1")
    pointer_before = (corpus_dir / knowledge.CURRENT_POINTER_NAME).read_text(encoding="utf-8")
    with pytest.raises(versions.VersionError):
        versions.publish_version(corpus_dir, "v404", approved_by="tech1")
    pointer_after = (corpus_dir / knowledge.CURRENT_POINTER_NAME).read_text(encoding="utf-8")
    assert pointer_before == pointer_after


def test_publish_refuses_dangling_reference_independent_of_build(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    versions.publish_version(corpus_dir, "v1", approved_by="tech1")
    pointer_before = (corpus_dir / knowledge.CURRENT_POINTER_NAME).read_text(encoding="utf-8")
    _manual_version_dir(corpus_dir, "bad1", tamper=_tamper_dangling_ref)
    with pytest.raises(versions.VersionError):
        versions.publish_version(corpus_dir, "bad1", approved_by="tech2")
    assert (corpus_dir / knowledge.CURRENT_POINTER_NAME).read_text(encoding="utf-8") == pointer_before


def test_publish_refuses_foreign_scope_independent_of_build(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    versions.publish_version(corpus_dir, "v1", approved_by="tech1")
    pointer_before = (corpus_dir / knowledge.CURRENT_POINTER_NAME).read_text(encoding="utf-8")
    _manual_version_dir(corpus_dir, "bad2", tamper=_tamper_foreign_scope)
    with pytest.raises(versions.VersionError):
        versions.publish_version(corpus_dir, "bad2", approved_by="tech2")
    assert (corpus_dir / knowledge.CURRENT_POINTER_NAME).read_text(encoding="utf-8") == pointer_before


def test_rollback_returns_to_previous_version_without_deleting_newer(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    v1 = versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    versions.publish_version(corpus_dir, "v1", approved_by="tech1")
    v1_bytes_before = Path(v1.path).read_bytes()

    v2 = versions.build_version(corpus_dir, "v2", knowledge_dir, stub_importer)
    versions.publish_version(corpus_dir, "v2", approved_by="tech2")
    handle = knowledge.open_corpus(str(corpus_dir))
    try:
        assert knowledge.corpus_version(handle) == v2.corpus_version
    finally:
        knowledge.close(handle)

    rollback_entry = versions.rollback_version(corpus_dir, "v1", approved_by="tech3")
    assert rollback_entry["action"] == "rollback"
    handle = knowledge.open_corpus(str(corpus_dir))
    try:
        assert knowledge.corpus_version(handle) == v1.corpus_version
    finally:
        knowledge.close(handle)

    # v2 (ставшая «старой» после откатa) не удалена и не изменена — мастер-промпт §10 последний абзац
    assert Path(v2.path).is_file()
    assert Path(v1.path).read_bytes() == v1_bytes_before


def test_list_versions_marks_current_and_broken_without_crashing(tmp_path, stub_importer):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    versions.build_version(corpus_dir, "v1", knowledge_dir, stub_importer)
    versions.publish_version(corpus_dir, "v1", approved_by="tech1")
    broken_dir = corpus_dir / "broken1"
    broken_dir.mkdir()
    (broken_dir / "evil.seed.sqlite3").write_bytes(b"not a real sqlite file")

    listing = versions.list_versions(corpus_dir)
    by_id = {entry["version_id"]: entry for entry in listing}
    assert by_id["v1"]["is_current"] is True
    assert by_id["v1"]["status"] == "ok"
    assert by_id["broken1"]["status"] == "broken"
    assert by_id["broken1"]["is_current"] is False


def test_list_versions_empty_root_returns_empty_list(tmp_path):
    assert versions.list_versions(tmp_path / "nope") == []


# --- cli: разбор аргументов и вывод JSON ---


def test_cli_quarantine_and_report_roundtrip(tmp_path, capsys):
    plain = tmp_path / "note.txt"
    plain.write_bytes(b"x")
    rc = cli.main(["quarantine", str(plain), "--quarantine-dir", str(tmp_path / "q"), "--id", "cli1"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "accepted"

    rc = cli.main(["report", "cli1", "--quarantine-dir", str(tmp_path / "q")])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["item_id"] == "cli1"


def test_cli_build_publish_list_round_trip(tmp_path, stub_importer, capsys):
    knowledge_dir = _write_knowledge_dir(tmp_path / "kn")
    corpus_dir = tmp_path / "corpus"
    rc = cli.main(["build", "v1", "--knowledge-dir", str(knowledge_dir), "--importer", str(stub_importer),
                  "--corpus-dir", str(corpus_dir)])
    assert rc == 0
    capsys.readouterr()

    rc = cli.main(["publish", "v1", "--approved-by", "tech1", "--corpus-dir", str(corpus_dir)])
    assert rc == 0
    capsys.readouterr()

    rc = cli.main(["list", "--corpus-dir", str(corpus_dir)])
    assert rc == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing[0]["version_id"] == "v1"
    assert listing[0]["is_current"] is True


def test_cli_without_corpus_dir_or_env_exits(tmp_path, monkeypatch):
    monkeypatch.delenv("ANALYTICS_CORPUS_DIR", raising=False)
    with pytest.raises(SystemExit):
        cli.main(["list"])


def test_cli_publish_error_exits_nonzero(tmp_path, capsys):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    rc = cli.main(["publish", "v404", "--approved-by", "tech1", "--corpus-dir", str(corpus_dir)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "error" in err


# --- настоящий корпус заказчика и настоящий импортер (KDM1_PACKAGE_DIR) — только локально ---

KDM1_PACKAGE_DIR = os.environ.get("KDM1_PACKAGE_DIR", "")
real_corpus = pytest.mark.skipif(not KDM1_PACKAGE_DIR,
                                 reason="нужен KDM1_PACKAGE_DIR — пакет заказчика вне репозитория")


@real_corpus
def test_real_importer_build_publish_rollback_end_to_end(tmp_path):
    """`tools/import_seed.py` настоящий (не подставной), `knowledge/` настоящий — build_version запускает его
    субпроцессом, не переписывая извлечение вложений (бриф Task 8)."""
    package = Path(KDM1_PACKAGE_DIR)
    importer = package / "tools" / "import_seed.py"
    knowledge_dir = package / "knowledge"
    corpus_dir = tmp_path / "corpus"

    v1 = versions.build_version(corpus_dir, "real1", knowledge_dir, importer, timeout_s=300.0)
    assert v1.counts["fragments"] == 2385
    versions.publish_version(corpus_dir, "real1", approved_by="test-operator")

    handle = knowledge.open_corpus(str(corpus_dir))
    try:
        assert knowledge.corpus_version(handle) == v1.corpus_version
    finally:
        knowledge.close(handle)

    v2 = versions.build_version(corpus_dir, "real2", knowledge_dir, importer, timeout_s=300.0)
    versions.publish_version(corpus_dir, "real2", approved_by="test-operator")
    handle = knowledge.open_corpus(str(corpus_dir))
    try:
        assert knowledge.corpus_version(handle) == v2.corpus_version
    finally:
        knowledge.close(handle)

    versions.rollback_version(corpus_dir, "real1", approved_by="test-operator")
    handle = knowledge.open_corpus(str(corpus_dir))
    try:
        assert knowledge.corpus_version(handle) == v1.corpus_version
    finally:
        knowledge.close(handle)
    # откат не удалил "real2" — мастер-промпт §10 последний абзац
    assert Path(v2.path).is_file()
