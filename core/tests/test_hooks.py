"""Крючки уровня приложения: один обработчик 422 и один предел тела по префиксу пути; живость /healthz/live."""
import anyio
import pytest
from fastapi import HTTPException as FastApiHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from helpers import SETTINGS, TOKEN, DummyRole, make_app, serve_app, wait_until, wait_until_async
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


# --- общий бюджет тел префикса (Д5б, доводка Б3, H2) ---

def _post(path: str, size: int | None) -> dict:
    headers = [] if size is None else [(b"content-length", str(size).encode())]
    return {"type": "http", "method": "POST", "path": path, "headers": headers}


@pytest.mark.anyio
async def test_body_budget_refuses_without_reading_and_frees():
    h = AppHooks()
    h.body_limit("/b/", 100, budget=250, over_budget=lambda scope: JSONResponse({"busy": True}, status_code=503))
    h.body_limit("/n/", 100)                                                     # без бюджета — как прежде
    budget, go, reads, answers = h.budget_for("/b/x"), anyio.Event(), [], []
    assert (budget.total, h.budget_for("/n/x"), h.budget_for("/zzz")) == (250, None, None)

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def call(path, size, body=b"x" * 100):
        async def receive():
            reads.append(path)
            await go.wait()
            return {"type": "http.request", "body": body, "more_body": False}
        sent = []

        async def send(message):
            sent.append(message)
        await BodyLimit(app, hooks=h)(_post(path, size), receive, send)
        answers.append(sent[0]["status"])

    async with anyio.create_task_group() as tg:
        for _ in range(3):
            tg.start_soon(call, "/b/x", 100)
        tg.start_soon(call, "/n/x", 100)
        await wait_until_async(lambda: len(answers) == 1 and budget.used == 200)
        assert answers == [503] and reads.count("/b/x") == 2                    # третий — отказ без чтения
        go.set()
    assert sorted(answers) == [200, 200, 200, 503] and budget.used == 0


@pytest.mark.anyio
async def test_body_budget_counts_chunked_and_frees_on_abort():
    h = AppHooks()
    h.body_limit("/b/", 100, budget=150, over_budget=lambda scope: JSONResponse({}, status_code=503))
    budget = h.budget_for("/b/")
    chunks = [{"type": "http.request", "body": b"x" * 60, "more_body": True},
              {"type": "http.request", "body": b"x" * 30, "more_body": False}]

    async def receive():
        return chunks.pop(0)

    async def boom(scope, receive, send):
        assert budget.used == 90                                                 # тело без длины — по мере чтения
        raise RuntimeError("обработчик упал")

    sent = []

    async def send(message):
        sent.append(message)
    with pytest.raises(RuntimeError):
        await BodyLimit(boom, hooks=h)(_post("/b/x", None), receive, send)
    assert budget.used == 0                                                      # finally: и при исключении
    budget.used = 100
    chunks[:] = [{"type": "http.request", "body": b"x" * 60, "more_body": True}]
    await BodyLimit(boom, hooks=h)(_post("/b/x", None), receive, send)
    assert sent[0]["status"] == 503 and budget.used == 100                       # кусок не поместился — отказ


@pytest.mark.anyio
async def test_body_budget_freed_on_refusal_mid_body():                        # Б3-m1: отказ на втором куске
    h = AppHooks()
    h.body_limit("/b/", 100, budget=150, over_budget=lambda scope: JSONResponse({}, status_code=503),
                 respond=lambda scope, limit: JSONResponse({"too": "large"}, status_code=413))
    budget = h.budget_for("/b/")
    reached = []

    async def app(scope, receive, send):
        reached.append(1)
        await JSONResponse({})(scope, receive, send)

    async def run(parts, used_before):
        chunks = [{"type": "http.request", "body": b"x" * n, "more_body": i < len(parts) - 1}
                  for i, n in enumerate(parts)]
        sent = []

        async def receive():
            return chunks.pop(0)

        async def send(message):
            sent.append(message)
        budget.used = used_before
        await BodyLimit(app, hooks=h)(_post("/b/x", None), receive, send)
        return sent[0]["status"], budget.used

    assert await run([60, 35], 60) == (503, 60)                 # второй кусок не влез в бюджет — первый возвращён
    assert await run([60, 50], 0) == (413, 0)                   # предел превышен посреди чтения — тоже
    assert await run([60, 30], 0) == (200, 0) and reached == [1]   # положительный контроль: влезло — дошло


def test_body_budget_over_return_is_logged(gwlog):                              # Б3-m2: двойной возврат виден
    h = AppHooks()
    h.body_limit("/b/", 100, budget=150, over_budget=lambda scope: JSONResponse({}, status_code=503))
    budget = h.budget_for("/b/")
    assert budget.take(40)
    budget.give(40)
    assert "body_budget_over_return" not in gwlog.text                         # честный возврат — молча
    budget.give(40)
    assert budget.used == 0 and "outcome=body_budget_over_return used=0 returned=40 total=150" in gwlog.text


@pytest.mark.anyio
async def test_body_budget_not_for_other_zones():
    h = AppHooks()
    h.body_limit("/b/", 100, budget=1000, over_budget=lambda scope: JSONResponse({}, status_code=404),
                 budget_admits=lambda scope: scope.get("server") == ("front", 1))
    reads, sent = [], []

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"x" * 10, "more_body": False}

    async def send(message):
        sent.append(message)
    await BodyLimit(_echo, hooks=h)({**_post("/b/x", 10), "server": ("place", 1)}, receive, send)
    assert (sent[0]["status"], reads, h.budget_for("/b/").used) == (404, [], 0)       # тело не читается
    sent.clear()
    await BodyLimit(_echo, hooks=h)({**_post("/b/x", 10), "server": ("front", 1)}, receive, send)
    assert sent[0]["status"] == 200 and reads == [1]


def test_body_budget_arguments_checked():
    h = AppHooks()
    with pytest.raises(ValueError):
        h.body_limit("/d/", 100, budget_admits=lambda scope: True)              # без budget
    with pytest.raises(ValueError):
        h.body_limit("/a/", 100, budget=1000)                                    # без over_budget
    with pytest.raises(ValueError):
        h.body_limit("/b/", 100, over_budget=lambda scope: JSONResponse({}))
    with pytest.raises(ValueError):
        h.body_limit("/c/", 100, budget=99, over_budget=lambda scope: JSONResponse({}))   # тело предела не влезет
