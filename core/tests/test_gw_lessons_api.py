"""Ручки пакета уроков у шлюза (Д15а, вкладка «Уроки»): GET /pcbk/api/lessons, GET /pcbk/api/lessons/{id},
GET /pcbk/api/lessons/img/{name}. Пускает любую вошедшую учётку — и студента, и преподавателя (в отличие от
/pcbk/api/agents и /pcbk/api/events, преподавателю тут не 403: учётка нужна только как проверка входа, место ей
не нужно). Фикстура gw — как в test_gw_agents_api.py; корень пакета — tmp_path/lessons (gateway_role без
lessons_root)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helpers import BASE, FRONT, PLACE_NET, Gw, make_gateway
from pcbk_core.gateway.api import GW_MESSAGES
from pcbk_core.gateway.lessons_api import LESSON_IMG_PATH, LESSONS_PATH


def write_index(root: Path, lessons: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "lessons.json").write_text(json.dumps({"lessons": lessons}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def gw(tmp_path):
    app, role = make_gateway(tmp_path)
    with TestClient(app, base_url=BASE, client=FRONT):               # lifespan роли — на весь тест
        yield Gw(app, role)


def lessons_root(gw: Gw) -> Path:
    return Path(gw.role.lessons_root)


def test_lessons_refusals(gw):
    r = gw.anon().get(LESSONS_PATH)                                   # тело 401 — JSON шлюза
    assert (r.status_code, r.headers["content-type"], r.json()) == \
        (401, "application/json", {"name": "Unauthorized", "data": {"message": GW_MESSAGES["need_login"]}})
    assert gw.as_("stu-03", client=PLACE_NET).get(LESSONS_PATH).status_code == 404   # вне pcbk-front


def test_lessons_student_and_teacher_200(gw):
    write_index(lessons_root(gw), [{"id": "01-a", "title": "А", "summary": "сводка"}])
    assert gw.as_("stu-03").get(LESSONS_PATH).status_code == 200
    assert gw.as_("teach").get(LESSONS_PATH).status_code == 200       # преподавателю тоже 200 — не 403


def test_lessons_index_and_lesson(gw):
    root = lessons_root(gw)
    write_index(root, [{"id": "01-a", "title": "А", "summary": "сводка"}])
    (root / "01-a.md").write_text("# Урок\nтекст урока", encoding="utf-8")

    body = gw.as_("stu-03").get(LESSONS_PATH).json()
    assert body == {"lessons": [{"id": "01-a", "title": "А", "summary": "сводка"}]}

    r = gw.as_("stu-03").get(LESSONS_PATH + "/01-a")
    assert (r.status_code, r.json()) == (200, {"id": "01-a", "title": "А", "markdown": "# Урок\nтекст урока"})

    assert gw.as_("stu-03").get(LESSONS_PATH + "/02-chuzhoj").status_code == 404     # нет в lessons.json


def test_lessons_broken_index_503(gw):
    root = lessons_root(gw)
    root.mkdir(parents=True, exist_ok=True)
    (root / "lessons.json").write_text("не json{", encoding="utf-8")
    r = gw.as_("stu-03").get(LESSONS_PATH)
    assert r.status_code == 503
    message = r.json()["data"]["message"]
    assert message.startswith("Пакет уроков не читается: ")
    assert str(root) not in message                                  # без путей хоста


def test_lessons_empty_without_pack(gw):
    assert gw.as_("stu-03").get(LESSONS_PATH).json() == {"lessons": []}
    assert gw.as_("teach").get(LESSONS_PATH).json() == {"lessons": []}


def test_lessons_image_headers(gw):
    root = lessons_root(gw)
    img = root / "img"
    img.mkdir(parents=True)
    data = b"\x89PNG\r\n\x1a\n" + "фальшивые байты картинки".encode("utf-8")
    (img / "a.png").write_bytes(data)

    r = gw.as_("stu-03").get(LESSONS_PATH + "/img/a.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content == data

    assert gw.as_("stu-03").get(LESSONS_PATH + "/img/missing.png").status_code == 404
    assert gw.as_("stu-03").get(LESSONS_PATH + "/img/..%2fetc").status_code == 404


def test_lessons_config_error_503(gw):                                 # m24: при config_error ручка не отвечает
    write_index(lessons_root(gw), [{"id": "01-a", "title": "А", "summary": "сводка"}])
    s3, t = gw.as_("stu-03"), gw.as_("teach")                          # сессии — до ошибки настройки
    assert s3.get(LESSONS_PATH).status_code == 200                     # положительный контроль
    gw.role.config_error = "GW_ORIGIN не задан"
    for who, c in (("student", s3), ("teacher", t), ("anon", gw.anon())):
        r = c.get(LESSONS_PATH)
        assert (r.status_code, r.json()) == (503, {"name": "Unavailable",
                                                   "data": {"message": "Шлюз не настроен: GW_ORIGIN не задан"}}), who


def test_lessons_other_methods_404(gw):
    s3 = gw.as_("stu-03")
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        r = s3.request(method, LESSONS_PATH, headers={"Origin": BASE})
        assert (r.status_code, r.json()) == (404, {"name": "NotFound", "data": {"message": "Не найдено"}}), method
        assert "allow" not in r.headers, method


def test_lessons_path():
    assert LESSONS_PATH == "/pcbk/api/lessons"
    assert LESSON_IMG_PATH == "/pcbk/api/lessons/img/{name}"
