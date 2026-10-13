"""Чтение пакета уроков (Д15а): каталог LESSONS_ROOT, смонтированный core только на чтение. Без FastAPI — ручки
читают через asyncio.to_thread (lessons_api.py).

Формат пакета (DESIGN Д15а):
- lessons.json — порядок и заголовки: {"lessons": [{"id", "title", "summary"}, …]};
- <id>.md — markdown текст урока;
- img/<имя> — картинки урока, до MAX_IMAGE_BYTES байт каждая.

Чтение — на каждый вызов, без кеша: правка файла на сервере видна следующему запросу без перезапуска
(DESIGN, критерий 3). Пути не доверенные (id и имя картинки идут от студента через URL) проверяются дважды:
сперва шаблоном (LESSON_ID_RE, IMAGE_NAME_RE) — traversal-строки вида «../x.png», «a/b.png», «%2e%2e.png» ему
не подходят и до диска не доходят; затем на диске — ни один сегмент пути не ссылка (os.lstat, как
AgentStore._place_base) и итоговый путь после resolve() лежит внутри resolve() корня. lessons.json — доверенный
файл (кладёт человек, выкладывающий пакет), его id сверяется тем же шаблоном, чтобы не завести поломанный
<id>.md на диске."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

LESSON_ID_RE = re.compile(r"[a-z0-9-]{1,40}")
IMAGE_NAME_RE = re.compile(r"[a-z0-9-]{1,60}\.(png|jpg|jpeg|webp)")
MAX_LESSON_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}

INDEX_NAME = "lessons.json"
IMG_DIR = "img"


class LessonsError(Exception):
    """lessons.json не читается: не JSON, не объект со списком lessons, элемент не похож на урок, id не по
    шаблону или повторяется. Текст — без путей хоста (его показывает ручка студенту в 503)."""


@dataclass(frozen=True)
class LessonMeta:
    id: str
    title: str
    summary: str


def _lesson_meta(item: object) -> LessonMeta:
    if (not isinstance(item, dict) or set(item) != {"id", "title", "summary"}
            or not all(isinstance(item[key], str) for key in ("id", "title", "summary"))):
        raise LessonsError("элемент lessons не вида {id, title, summary}")
    if LESSON_ID_RE.fullmatch(item["id"]) is None:
        raise LessonsError(f"id не по шаблону: {item['id']!r}")
    return LessonMeta(id=item["id"], title=item["title"], summary=item["summary"])


def read_index(root: str) -> list[LessonMeta]:
    """Уроки в порядке lessons.json. Каталога или файла нет — []. Не JSON, нет списка lessons, элемент не того
    вида, id не по шаблону или повторяется — LessonsError."""
    try:
        raw = (Path(root) / INDEX_NAME).read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise LessonsError(type(exc).__name__) from None
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        raise LessonsError("не читается как JSON") from None
    if not isinstance(body, dict) or not isinstance(body.get("lessons"), list):
        raise LessonsError("нет списка lessons")
    metas = [_lesson_meta(item) for item in body["lessons"]]
    ids = [m.id for m in metas]
    if len(set(ids)) != len(ids):
        raise LessonsError("id повторяется")
    return metas


def _safe_path(root: Path, *parts: str) -> Path | None:
    """Путь parts внутри root: ни один сегмент (файл или промежуточный каталог вроде img/) — не ссылка
    (os.lstat), итоговый путь после resolve() — внутри resolve() корня. Нехватка сегмента, ссылка на любом
    шаге, выход за корень — None."""
    path = root
    for part in parts:
        path = path / part
        try:
            if stat.S_ISLNK(os.lstat(path).st_mode):
                return None
        except OSError:
            return None
    try:
        resolved_root = root.resolve()
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(resolved_root):
        return None
    return resolved


def _read_text(path: Path, max_bytes: int) -> str | None:
    """Текст файла — UTF-8, не больше max_bytes байт; не обычный файл, слишком большой, ошибка чтения или
    декодирования — None."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        if path.stat().st_size > max_bytes:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_lesson(root: str, lesson_id: str) -> tuple[LessonMeta, str] | None:
    """Урок по id — (запись из lessons.json, markdown-текст <id>.md). id нет в индексе, файла нет, файл —
    ссылка, слишком большой или не UTF-8 — None. Битый lessons.json — LessonsError (как read_index); id из
    индекса уже по шаблону — путь к файлу строится только из проверенных id."""
    meta = next((m for m in read_index(root) if m.id == lesson_id), None)
    if meta is None:
        return None
    path = _safe_path(Path(root), f"{lesson_id}.md")
    if path is None:
        return None
    text = _read_text(path, MAX_LESSON_BYTES)
    if text is None:
        return None
    return meta, text


def image_file(root: str, name: str) -> tuple[Path, str] | None:
    """Путь и MIME-тип картинки img/<name>. name не по IMAGE_NAME_RE (в том числе traversal: «../x.png»,
    «a/b.png», «%2e%2e.png»), ссылка (сама img/ или файл), выход за корень, файла нет, не обычный файл, больше
    MAX_IMAGE_BYTES — None."""
    match = IMAGE_NAME_RE.fullmatch(name)
    if match is None:
        return None
    path = _safe_path(Path(root), IMG_DIR, name)
    if path is None:
        return None
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        if path.stat().st_size > MAX_IMAGE_BYTES:
            return None
    except OSError:
        return None
    return path, IMAGE_TYPES[match.group(1)]
