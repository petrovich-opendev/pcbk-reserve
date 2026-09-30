"""Крючки уровня приложения: один обработчик 422 и один предел тела по префиксу пути; живость /healthz/live."""
import pytest
from fastapi import HTTPException as FastApiHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from helpers import SETTINGS, TOKEN, DummyRole, make_app, serve_app, wait_until
from pcbk_core.app import create_app
from pcbk_core.hooks import DEFAULT_BODY_LIMIT, AppHooks, BodyLimit, hooks_of, size_text
from pcbk_core.logs import setup_logging
from pcbk_core.main import main


async def _echo(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_validation_dispatch_by_prefix_two_roles(tmp_path):                    # Review Focus 1
    app, role = make_app(tmp_path, extra_roles=[DummyRole("llm", (True, "ок"), validates="/dummy-llm/")])
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        r = c.post("/api/data/tag_now", json={"tags": ["x"] * 17}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 422 and r.json()["status"] == "refused"
        assert role.events.recent(channel="http")[0].outcome == "refused"
        assert c.post("/dummy-llm/typed", json={"n": "x"}).json() == {"handled_by": "llm"}


def test_body_limit_per_prefix():                                                # Review Focus 1
    roles = [DummyRole("llm", (True, "ок"), installs=True, body_limit=("/dummy", 200_000))]
    with TestClient(create_app(SETTINGS, roles)) as c:
        assert c.post("/dummy", content=b"x" * 150_000).status_code == 200
        assert c.post("/dummy", content=b"x" * 250_000).json() == {"detail": "тело запроса больше 200000 байт"}
        r = c.post("/healthz", content=b"x" * 70_000)                           # по умолчанию 64 КиБ
        assert (r.status_code, r.json()) == (413, {"detail": "тело запроса больше 64 КиБ"})


def test_body_limit_refusal_by_prefix():                                        # П19: 413 в форме OpenAI у /llm/
    h = AppHooks()
    h.body_limit("/x/", 100, refusal=lambda n: {"error": {"code": "pcbk_body_too_large", "limit": n}})
    c = TestClient(BodyLimit(_echo, hooks=h))
    assert c.post("/x/a", content=b"x" * 100).status_code == 200
    r = c.post("/x/a", content=b"x" * 101)
    assert (r.status_code, r.json()) == (413, {"error": {"code": "pcbk_body_too_large", "limit": 100}})
    assert c.post("/y", content=b"x" * 70_000).json() == {"detail": "тело запроса больше 64 КиБ"}


def test_body_limit_without_hooks_is_d3b():                                      # П4: test_http.py Д3б без правок
    from pcbk_core.data import http_api
    assert http_api.BodyLimit is BodyLimit and http_api.MAX_BODY_BYTES == DEFAULT_BODY_LIMIT
    assert http_api.size_text is size_text and size_text(1_048_576) == "1024 КиБ"
    r = TestClient(BodyLimit(_echo)).post("/x", content=b"x" * 65_537)
    assert (r.status_code, r.json()) == (413, {"detail": "тело запроса больше 64 КиБ"})


def test_hooks_longest_prefix_and_duplicates():
    h = AppHooks()
    h.body_limit("/llm/", 10); h.body_limit("/llm/v1/", 20)
    assert (h.limit_for("/llm/v1/chat/completions"), h.limit_for("/llm/x"), h.limit_for("/mcp")) == \
           (20, 10, DEFAULT_BODY_LIMIT)
    assert h.refusal_for("/llm/x") is None and h.handler_for("/mcp") is None
    with pytest.raises(ValueError):
        h.body_limit("/llm/", 30)


def test_healthz_live_ignores_role_health():
    with TestClient(create_app(SETTINGS, [DummyRole("llm", (False, "нет ключа"))])) as c:
        assert c.get("/healthz").status_code == 503
        assert (c.get("/healthz/live").status_code, c.get("/healthz/live").json()) == (200, {"ok": True})
    with pytest.raises(ValueError):
        create_app(SETTINGS, [DummyRole("live", (True, ""))])


def test_healthcheck_passes_with_unhealthy_role(monkeypatch):
    with serve_app(create_app(SETTINGS, [DummyRole("llm", (False, "нет ключа"))])) as url:
        monkeypatch.setenv("CORE_PORT", url.rsplit(":", 1)[1])
        assert main(["--healthcheck"]) == 0


# --- сверх брифа ---

def test_validation_without_handler_is_fastapi_default():
    """Путь без обработчика по префиксу — ответ FastAPI по умолчанию (список ошибок)."""
    class Unhandled(DummyRole):                    # маршрут /free/typed есть, обработчика на /free/ нет
        def install(self, app):
            pass

    roles = [DummyRole("llm", (True, "ок"), validates="/dummy-llm/"), Unhandled("data", (True, "ок"), validates="/free/")]
    with TestClient(create_app(SETTINGS, roles)) as c:
        r = c.post("/free/typed", json={"n": "x"})
        assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "n"]
        assert c.post("/dummy-llm/typed", json={"n": 1}).json() == {"n": 1}


def test_role_own_422_handler_is_refused():
    """Обработчик 422 мимо реестра молча затёрся бы общим — сборка отказывает."""
    class Bypass(DummyRole):
        def install(self, app):
            app.add_exception_handler(RequestValidationError, lambda request, exc: None)

    with pytest.raises(ValueError, match="реестр"):
        create_app(SETTINGS, [Bypass("llm", (True, "ок"))])


def test_hooks_validation_duplicate_and_bad_prefix():
    h = AppHooks()

    async def handler(request, exc):
        return None

    h.validation("/a/", handler)
    assert h.handler_for("/a/b") is handler and h.handler_for("/b") is None
    with pytest.raises(ValueError):
        h.validation("/a/", handler)
    with pytest.raises(ValueError):
        h.body_limit("llm/", 10)                   # без «/» в начале не совпал бы ни с одним путём
    with pytest.raises(ValueError):
        h.body_limit("/z/", 0)


def test_413_log_line_carries_prefix_limit(capfd, restore_logging):
    setup_logging()
    h = AppHooks()
    h.body_limit("/x/", 100)
    TestClient(BodyLimit(_echo, hooks=h)).post("/x/a", content=b"x" * 101)
    assert "outcome=too_large path=/x/a limit=100" in capfd.readouterr().err


@pytest.mark.anyio
async def test_receive_after_buffer_goes_to_original():
    """После буфера вызовы receive уходят исходному: потоковый ответ узнаёт об обрыве клиента."""
    incoming = [{"type": "http.request", "body": b"ab", "more_body": False}, {"type": "http.disconnect"}]
    seen = []

    async def receive():
        return incoming.pop(0)

    async def app(scope, receive, send):
        seen.append(await receive())
        seen.append(await receive())

    async def send(message):
        pass

    h = AppHooks()
    await BodyLimit(app, hooks=h)({"type": "http", "path": "/x", "headers": []}, receive, send)
    assert [m["type"] for m in seen] == ["http.request", "http.disconnect"] and seen[0]["body"] == b"ab"


# --- реестр HTTPException (Д5, решение контролёра р2, Ш5) ---

async def HANDLER_A(request, exc):
    return JSONResponse({"by": "a"}, status_code=exc.status_code)


async def HANDLER_B(request, exc):
    return JSONResponse({"by": "b"}, status_code=exc.status_code)


def test_http_exception_dispatch_by_prefix():
    h = AppHooks()
    h.http_exception("/pcbk/api/", HANDLER_A); h.http_exception("/pcbk/api/oc/", HANDLER_B)
    assert (h.http_handler_for("/pcbk/api/oc/x"), h.http_handler_for("/pcbk/api/auth/me"),
            h.http_handler_for("/api/data/tag_now")) == (HANDLER_B, HANDLER_A, None)
    with pytest.raises(ValueError):
        h.http_exception("/pcbk/api/", HANDLER_B)


def test_role_own_http_exception_handler_is_refused():                                 # Ш5: как у 422
    class Bypass(DummyRole):
        def install(self, app):
            app.add_exception_handler(StarletteHTTPException, lambda request, exc: None)
    with pytest.raises(ValueError, match="реестр"):
        create_app(SETTINGS, [Bypass("llm", (True, "ок"))])

    class FastApiBypass(DummyRole):                # подкласс FastAPI нашёлся бы раньше общего по MRO
        def install(self, app):
            app.add_exception_handler(FastApiHTTPException, lambda request, exc: None)
    with pytest.raises(ValueError, match="реестр"):
        create_app(SETTINGS, [FastApiBypass("llm", (True, "ок"))])

    class OwnSubclass(StarletteHTTPException):
        pass

    for key in (404, OwnSubclass):                 # F9: по коду ответа Starlette ищет раньше, чем по классу
        class ByKey(DummyRole):
            def install(self, app, key=key):
                app.add_exception_handler(key, lambda request, exc: None)
        with pytest.raises(ValueError, match="реестр"):
            create_app(SETTINGS, [ByKey("llm", (True, "ок"))])


def test_body_limit_respond_by_scope():                                                 # F6: ответ по зоне
    h = AppHooks()
    h.body_limit("/x/", 100, respond=lambda scope, n: JSONResponse({"n": n, "path": scope["path"]}, status_code=404))
    c = TestClient(BodyLimit(_echo, hooks=h))
    r = c.post("/x/a", content=b"x" * 101)
    assert (r.status_code, r.json()) == (404, {"n": 100, "path": "/x/a"})
    with pytest.raises(ValueError):
        h.body_limit("/y/", 10, refusal=lambda n: {}, respond=lambda scope, n: JSONResponse({}))


def test_http_exception_registry_used_by_app():
    """404 и 405 маршрутизации под префиксом роли — её обработчик; прочие пути — ответ FastAPI по умолчанию."""
    class Registered(DummyRole):
        def install(self, app):
            super().install(app)
            hooks_of(app).http_exception("/dummy-llm/", HANDLER_A)

    with TestClient(create_app(SETTINGS, [Registered("llm", (True, "ок"), validates="/dummy-llm/")])) as c:
        assert (c.get("/dummy-llm/nothing").status_code, c.get("/dummy-llm/nothing").json()) == (404, {"by": "a"})
        assert c.get("/dummy-llm/typed").json() == {"by": "a"}                     # 405: маршрут только POST
        assert c.get("/nothing").json() == {"detail": "Not Found"}
        assert (c.put("/healthz").status_code, c.put("/healthz").json()) == (405, {"detail": "Method Not Allowed"})
