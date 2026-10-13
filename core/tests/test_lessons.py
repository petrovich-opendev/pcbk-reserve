"""Чтение пакета уроков (Д15а, без FastAPI): read_index, read_lesson, image_file — pcbk_core.gateway.lessons.
Пакет во tmp_path: lessons.json, <id>.md, img/<имя>; тест пишет файлы напрямую, без макета сервера."""
import json
from pathlib import Path

import pytest

from pcbk_core.gateway.lessons import MAX_IMAGE_BYTES, MAX_LESSON_BYTES, LessonMeta, LessonsError, image_file, \
    read_index, read_lesson


def write_index(root: Path, lessons: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "lessons.json").write_text(json.dumps({"lessons": lessons}, ensure_ascii=False), encoding="utf-8")


def test_index_order_from_json(tmp_path):
    write_index(tmp_path, [{"id": "02-b", "title": "Б", "summary": "сводка Б"},
                           {"id": "01-a", "title": "А", "summary": "сводка А"}])
    metas = read_index(str(tmp_path))
    assert [m.id for m in metas] == ["02-b", "01-a"]            # порядок — как в файле, не по имени
    assert metas[0] == LessonMeta(id="02-b", title="Б", summary="сводка Б")


def test_missing_dir_or_index_is_empty(tmp_path):
    assert read_index(str(tmp_path / "missing")) == []          # каталога нет
    assert read_index(str(tmp_path)) == []                      # каталог есть, lessons.json нет


def test_broken_index_raises(tmp_path):
    (tmp_path / "lessons.json").write_text("не json{", encoding="utf-8")
    with pytest.raises(LessonsError):
        read_index(str(tmp_path))

    (tmp_path / "lessons.json").write_text(json.dumps({"other": []}), encoding="utf-8")
    with pytest.raises(LessonsError):                           # нет списка lessons
        read_index(str(tmp_path))

    bad_id = [{"id": "Урок 1!", "title": "т", "summary": "с"}]
    (tmp_path / "lessons.json").write_text(json.dumps({"lessons": bad_id}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LessonsError):                           # id не по шаблону
        read_index(str(tmp_path))

    dup = [{"id": "a", "title": "т1", "summary": "с1"}, {"id": "a", "title": "т2", "summary": "с2"}]
    (tmp_path / "lessons.json").write_text(json.dumps({"lessons": dup}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LessonsError):                           # повтор id
        read_index(str(tmp_path))


def test_read_lesson_ok_and_unknown_none(tmp_path):
    write_index(tmp_path, [{"id": "01-a", "title": "А", "summary": "сводка"}])
    (tmp_path / "01-a.md").write_text("# Урок\nтекст урока", encoding="utf-8")
    meta, text = read_lesson(str(tmp_path), "01-a")
    assert meta == LessonMeta(id="01-a", title="А", summary="сводка")
    assert text == "# Урок\nтекст урока"

    assert read_lesson(str(tmp_path), "02-net-v-indekse") is None       # id нет в lessons.json

    write_index(tmp_path, [{"id": "03-net-faila", "title": "т", "summary": "с"}])
    assert read_lesson(str(tmp_path), "03-net-faila") is None            # в индексе есть, файла нет

    write_index(tmp_path, [{"id": "04-ne-utf8", "title": "т", "summary": "с"}])
    (tmp_path / "04-ne-utf8.md").write_bytes(b"\xff\xfe\x00\x01")
    assert read_lesson(str(tmp_path), "04-ne-utf8") is None              # не UTF-8


def test_lesson_too_big_none(tmp_path):
    write_index(tmp_path, [{"id": "01-a", "title": "т", "summary": "с"}])
    (tmp_path / "01-a.md").write_bytes(b"x" * MAX_LESSON_BYTES)
    assert read_lesson(str(tmp_path), "01-a") is not None                # ровно предел — ещё можно

    (tmp_path / "01-a.md").write_bytes(b"x" * (MAX_LESSON_BYTES + 1))
    assert read_lesson(str(tmp_path), "01-a") is None                    # предел + 1 — нет


def test_image_types_and_size(tmp_path):
    img = tmp_path / "img"
    img.mkdir(parents=True)
    for name, kind in (("a.png", "image/png"), ("b.jpg", "image/jpeg"), ("c.jpeg", "image/jpeg"),
                       ("d.webp", "image/webp")):
        (img / name).write_bytes(b"\x00")
        found = image_file(str(tmp_path), name)
        assert found is not None, name
        path, mime = found
        assert mime == kind
        assert path == (img / name).resolve()

    (img / "e.gif").write_bytes(b"\x00")
    assert image_file(str(tmp_path), "e.gif") is None                    # не из IMAGE_TYPES
    (img / "f.svg").write_bytes(b"\x00")
    assert image_file(str(tmp_path), "f.svg") is None

    (img / "big.png").write_bytes(b"\x00" * MAX_IMAGE_BYTES)
    assert image_file(str(tmp_path), "big.png") is not None              # ровно предел — ещё можно
    (img / "big.png").write_bytes(b"\x00" * (MAX_IMAGE_BYTES + 1))
    assert image_file(str(tmp_path), "big.png") is None                  # предел + 1 — нет


def test_lessons_traversal_and_symlink_404(tmp_path):
    img = tmp_path / "img"
    img.mkdir(parents=True)
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"\x00")

    for bad in ("../x.png", "a/b.png", "%2e%2e.png"):
        assert image_file(str(tmp_path), bad) is None                    # шаблону не подходят

    (img / "s.png").symlink_to(outside)
    assert image_file(str(tmp_path), "s.png") is None                    # ссылка на файл вне пакета

    write_index(tmp_path, [{"id": "01-a", "title": "т", "summary": "с"}])
    outside_md = tmp_path.parent / "outside.md"
    outside_md.write_text("вне пакета", encoding="utf-8")
    (tmp_path / "01-a.md").symlink_to(outside_md)
    assert read_lesson(str(tmp_path), "01-a") is None                    # <id>.md — ссылка


def test_lessons_edit_visible_without_restart(tmp_path):
    write_index(tmp_path, [{"id": "01-a", "title": "старое", "summary": "старое"}])
    (tmp_path / "01-a.md").write_text("старый текст", encoding="utf-8")
    meta, text = read_lesson(str(tmp_path), "01-a")
    assert (meta.title, text) == ("старое", "старый текст")

    write_index(tmp_path, [{"id": "01-a", "title": "новое", "summary": "новое"}])
    (tmp_path / "01-a.md").write_text("новый текст", encoding="utf-8")
    meta, text = read_lesson(str(tmp_path), "01-a")
    assert (meta.title, text) == ("новое", "новый текст")                # следующий вызов видит новое, без перезапуска
