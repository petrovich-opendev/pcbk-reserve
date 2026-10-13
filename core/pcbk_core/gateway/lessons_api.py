"""Пакет уроков у шлюза (Д15а, DESIGN): вкладка «Уроки» — GET /pcbk/api/lessons (список), GET /pcbk/api/lessons/{id}
(текст) и GET /pcbk/api/lessons/img/{name} (картинка). Образец — agents_router (GET /pcbk/api/agents): зона
(front_only) и ошибка настройки (configured(role)) — у самого маршрута, до ловящего (m24); сессия —
current_account(role).

В отличие от /pcbk/api/agents и /pcbk/api/events, преподавателю здесь отказа по месту нет: ручки пускают любую
вошедшую учётку (студента и преподавателя) — учётка нужна только как проверка входа (DESIGN: «пускает любую
вошедшую учётку»), account дальше не используется.

Чтение пакета — lessons.py (без FastAPI), в потоке (asyncio.to_thread): каталога или lessons.json нет —
{"lessons": []} (вкладка покажет «Уроки пока не добавлены»), не ошибка; битый lessons.json — 503 «Пакет уроков
не читается: <причина>» (LessonsError — без путей хоста). Неизвестный id или имя картинки (шаблон, ссылка,
выход за корень, размер) — 404, тем же not_found, что у catch_all_router. Картинка — FileResponse с типом по
расширению, Cache-Control: private, max-age=300 (кеш только у браузера этого студента — DESIGN) и
X-Content-Type-Options: nosniff (браузер не угадывает тип по содержимому)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse, Response

from . import clip
from .accounts import Account
from .api import ERROR_NAMES, GW_MESSAGES, PREFIX, configured, current_account, front_only, gw_error
from .lessons import LessonMeta, LessonsError, image_file, read_index, read_lesson

if TYPE_CHECKING:
    from . import GatewayRole

LESSONS_PATH = PREFIX + "/lessons"
LESSON_PATH = LESSONS_PATH + "/{lesson_id}"
LESSON_IMG_PATH = LESSONS_PATH + "/img/{name}"
LESSONS_BROKEN_SHOWN = 60               # причина в 503 «Пакет уроков не читается: …» — короткая, без путей хоста
LESSON_IMG_HEADERS = {"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"}


def lessons_json(metas: list[LessonMeta]) -> dict:
    """Ответ GET /pcbk/api/lessons: порядок — как у read_index (как в lessons.json)."""
    return {"lessons": [{"id": m.id, "title": m.title, "summary": m.summary} for m in metas]}


def _broken_pack(exc: LessonsError) -> JSONResponse:
    return gw_error(503, ERROR_NAMES[503], "Пакет уроков не читается: " + clip(str(exc), LESSONS_BROKEN_SHOWN))


def lessons_router(role: "GatewayRole") -> APIRouter:
    """GET /pcbk/api/lessons* (зона, настройка — у маршрута, как agents_router, m24); сессия — любая учётка."""
    router = APIRouter()
    account_of = current_account(role)
    guarded = [Depends(front_only), Depends(configured(role))]

    @router.get(LESSONS_PATH, dependencies=guarded)
    async def lessons(account: Account = Depends(account_of)) -> JSONResponse:
        try:
            metas = await asyncio.to_thread(read_index, role.lessons_root)
        except LessonsError as exc:
            return _broken_pack(exc)
        return JSONResponse(lessons_json(metas))

    # /img/{name} — раньше {lesson_id}: у lesson_id свой сегмент пути (без «/»), двух сегментов «img/<имя>» он не
    # захватывает, но рядом в коде нагляднее читается как «сперва точный путь»
    @router.get(LESSON_IMG_PATH, dependencies=guarded)
    async def lesson_img(name: str, account: Account = Depends(account_of)) -> Response:
        found = await asyncio.to_thread(image_file, role.lessons_root, name)
        if found is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        path, kind = found
        return FileResponse(path, media_type=kind, headers=LESSON_IMG_HEADERS)

    @router.get(LESSON_PATH, dependencies=guarded)
    async def lesson(lesson_id: str, account: Account = Depends(account_of)) -> JSONResponse:
        try:
            found = await asyncio.to_thread(read_lesson, role.lessons_root, lesson_id)
        except LessonsError as exc:
            return _broken_pack(exc)
        if found is None:
            return gw_error(404, ERROR_NAMES[404], GW_MESSAGES["not_found"])
        meta, markdown = found
        return JSONResponse({"id": meta.id, "title": meta.title, "markdown": markdown})

    return router
