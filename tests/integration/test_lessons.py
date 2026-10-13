"""Пакет уроков вкладки «Уроки» через edge (Д15а): GET /pcbk/api/lessons, GET /pcbk/api/lessons/{id},
GET /pcbk/api/lessons/img/{name}. Образец пакета — deploy/lessons-sample (в git, только выдуманные теги
20FAKE_*): core монтирует его в compose по LESSONS_DIR (Ruling 3 — обязателен, как AGENTS_DIR, но только на
чтение); тестовый test.env ставит LESSONS_DIR на путь образца в этой же копии репозитория (conftest.stack,
REPO/deploy/lessons-sample) — без своего временного каталога.

Ручка пускает любую вошедшую учётку (студента и преподавателя), место ей не нужно — фикстура lesson_student
заводит студента на свободном месте только для входа, место не пробуждается и не трогается."""
import http.client
import json
import ssl

import pytest

from conftest import HOST, PORT, REPO, STAND_HOST
from gwclient import COOKIE
from secret import Secret

LESSONS_PATH = "/pcbk/api/lessons"
SAMPLE = REPO / "deploy" / "lessons-sample"
LOGIN = "lesson-s"
PLACE = 1


def binary_get(gw, path: str, headers: dict | None = None, timeout: float = 20.0) -> tuple[int, bytes, dict[str, str]]:
    """GET path через edge с cookie сессии gw и заголовками браузера — тело как есть, без decode (картинка —
    байты, а не текст; GwClient.call пропустил бы их через UTF-8 с заменой)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(HOST, PORT, context=ctx, timeout=timeout)
    try:
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", STAND_HOST)
        if gw.cookie is not None:
            conn.putheader("Cookie", Secret(f"{COOKIE}={gw.cookie}"))
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        conn.endheaders()
        resp = conn.getresponse()
        raw = resp.read()
        out: dict[str, str] = {}
        for name, value in resp.msg.items():
            key = name.lower()
            out[key] = f"{out[key]}, {value}" if key in out else value
        return resp.status, raw, out
    finally:
        conn.close()


@pytest.fixture(scope="module")
def lesson_student(stack):
    """Учётка студента на месте PLACE — только для входа (как lessons_api: учётка нужна лишь как проверка
    входа, место дальше не используется и не пробуждается); в конце — disable и remove (место стенда
    одноразовое, down -v его всё равно уберёт)."""
    password = stack.new_password()
    stack.add_account(LOGIN, "student", PLACE, password)
    try:
        gw = stack.gw_client()
        gw.login(LOGIN, password)
        yield gw
    finally:
        stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "disable", "--login", LOGIN)
        stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "remove", "--login", LOGIN, "--yes")


def test_core_binds_lessons_dir_read_only(stack):                      # Ruling 3: обязателен, как AGENTS_DIR, но только на чтение
    core = stack.prod_config()["services"]["core"]                     # compose.yaml сервера, без тестовых правок
    lessons = next(v for v in core["volumes"] if v["target"] == "/srv/lessons")
    assert (lessons["type"], lessons.get("read_only", False), lessons["bind"]["create_host_path"]) == \
        ("bind", True, False)


def test_lessons_via_edge_requires_login(stack):
    """Без cookie — 401 JSON шлюза («Нужно войти») на все три ручки, как у прочих ручек места (Д5а)."""
    gw = stack.gw_client()
    for path in (LESSONS_PATH, f"{LESSONS_PATH}/01-primer", f"{LESSONS_PATH}/img/primer.png"):
        code, body, h = gw.call("GET", path)
        assert (code, body, h["content-type"]) == \
            (401, {"name": "Unauthorized", "data": {"message": "Нужно войти"}}, "application/json"), path


def test_lessons_via_edge_student(stack, lesson_student):
    """Студент видит образец пакета: индекс — как в lessons.json образца, каждый урок — markdown с вымышленным
    тегом 20FAKE_; неизвестный id — 404, тем же not_found, что у catch_all_router."""
    gw = lesson_student
    index = json.loads((SAMPLE / "lessons.json").read_text(encoding="utf-8"))
    code, body, h = gw.call("GET", LESSONS_PATH)
    assert (code, h["content-type"]) == (200, "application/json")
    assert body == index

    for lesson in index["lessons"]:
        code, body, h = gw.call("GET", f"{LESSONS_PATH}/{lesson['id']}")
        assert (code, h["content-type"]) == (200, "application/json"), lesson["id"]
        assert (body["id"], body["title"]) == (lesson["id"], lesson["title"]), lesson["id"]
        assert "20FAKE_" in body["markdown"], lesson["id"]

    code, body, _ = gw.call("GET", f"{LESSONS_PATH}/missing-lesson")
    assert (code, body["name"]) == (404, "NotFound")


def test_lessons_image_via_edge(stack, lesson_student):
    """Картинка урока через edge — только заголовки браузера для <img>: Sec-Fetch-Dest: image,
    Sec-Fetch-Site: same-origin; 200 image/png, байты — как в образце deploy/lessons-sample/img/primer.png."""
    gw = lesson_student
    code, raw, h = binary_get(gw, f"{LESSONS_PATH}/img/primer.png",
                              headers={"Sec-Fetch-Dest": "image", "Sec-Fetch-Site": "same-origin"})
    assert (code, h["content-type"]) == (200, "image/png")
    assert raw == (SAMPLE / "img" / "primer.png").read_bytes()
