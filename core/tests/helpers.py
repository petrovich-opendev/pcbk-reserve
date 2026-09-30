"""Общие константы и помощники тестов серверного слоя (импорт явный: from helpers import …)."""
import asyncio
import atexit
import hashlib
import itertools
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import anyio
import httpx
import pytest
import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from fake_places import PLACE_PW, FakeDocker, FakePlaces
from fakes import WHITELIST, FakeHistorian
from pcbk_core.app import Role, RoleBase, create_app
from pcbk_core.data import DataRole
from pcbk_core.data.catalog import Catalog
from pcbk_core.data.events import CallEvent
from pcbk_core.data.historian import parse_clock
from pcbk_core.data.service import DataService
from pcbk_core.data.sql import catalog_sql, clock_sql, live_all_sql, names_in
from pcbk_core.gateway.accounts import AccountStore
from pcbk_core.gateway.places import SpCtl, Workplaces
from pcbk_core.gateway.stats import GatewayStats
from pcbk_core.hooks import hooks_of
from pcbk_core.llm import LlmRole
from pcbk_core.llm.ledger import Ledger
from pcbk_core.llm.upstream import Upstream
from pcbk_core.secrets import TokenTable, key_fingerprint
from pcbk_core.settings import GatewaySettings, LlmSettings, Settings

# каталог core/: из него подпроцессы импортируют pcbk_core
CORE_DIR = Path(__file__).resolve().parent.parent

_files = itertools.count(1)


def sha(text: str) -> str:
    """sha256 строки в шестнадцатеричном виде — как в файле core-tokens."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# тестовый токен — не 48 шестнадцатеричных знаков: шаблон проверки на секреты Д2 его не ловит (F17)
TOKEN = "test-token-student-01"
TEST_TOKENS = TokenTable({"student-01": sha(TOKEN)})

# каталог сессии тестов: журнал событий и core-tokens — здесь, не в /var/lib/pcbk-core и /run/secrets (F4)
SESSION_DIR = Path(tempfile.mkdtemp(prefix="pcbk-core-tests-"))
atexit.register(shutil.rmtree, SESSION_DIR, ignore_errors=True)
(SESSION_DIR / "core-tokens").write_text(f"student-01 {sha(TOKEN)}\n", encoding="utf-8")
# настройки тестов строятся напрямую: проверки from_env на них не действуют
SETTINGS = Settings(DB_PATH=str(SESSION_DIR / "core.db"), TOKENS_FILE=str(SESSION_DIR / "core-tokens"))

# роль llm в build_roles тестов Д3а/Д3б: пустые llm-tokens (пустая таблица без ошибки) и пустые ключи («нет
# ключа») — в каталоге сессии, не в /run/secrets (F4, П6)
LLM_ENV: dict[str, str] = {}
for _name, _file in (("LLM_TOKENS_FILE", "llm-tokens"), ("OPENROUTER_KEY_FILE", "openrouter.key"),
                     ("OPENROUTER_MGMT_KEY_FILE", "openrouter-mgmt.key")):
    (SESSION_DIR / _file).write_text("", encoding="utf-8")
    LLM_ENV[_name] = str(SESSION_DIR / _file)

# тестовые токены LLM — не 48 шестнадцатеричных знаков (шаблон проверки на секреты)
LLM_TOKEN = "test-llm-token-student-01"
OPS_LLM_TOKEN = "test-llm-token-ops"
LLM_TOKENS = TokenTable({"student-01": sha(LLM_TOKEN), "ops": sha(OPS_LLM_TOKEN)})
SES = "ses_" + "A" * 26
LLM_SETTINGS = LlmSettings()
# один инструмент, как в OC_BODY задачи 3
TAG_NOW_TOOL = {"type": "function", "function": {"name": "pcbk_tag_now", "description": "d",
                                                  "parameters": {"type": "object"}}}

# тесты, где права отнимает chmod: от root права каталога и файла не действуют
non_root = pytest.mark.skipif(os.geteuid() == 0, reason="от root chmod не отнимает права")

# пример события вызова: берут test_events.py и test_role.py (F16)
EVENT = CallEvent(ts=datetime(2026, 10, 1, 7, tzinfo=timezone.utc), caller="student-01", agent=None,
                  channel="http", tool="tag_now", requested=("20FAKE_001_PV", "X' OR 1=1--"),
                  tags=("20FAKE_001_PV",), sent=("20FAKE_001_PV",), start=None, end=None, outcome="partial",
                  summary="", cost=0.0, cache="miss", rows=1, duration_ms=12)


def write(tmp_path, text: str) -> str:
    """Новый файл с текстом в tmp_path; путь строкой."""
    path = tmp_path / f"file-{next(_files)}.env"
    path.write_text(text, encoding="utf-8")
    return str(path)


def run_python(code: str) -> str:
    """Код в свежем интерпретаторе из каталога core/; ненулевой код — CalledProcessError.

    stdout — результат; stderr не перехватывается: pytest покажет его у упавшего теста.
    """
    return subprocess.run([sys.executable, "-c", code], cwd=CORE_DIR, check=True,
                          stdout=subprocess.PIPE, text=True).stdout


def free_port() -> int:
    """Свободный сейчас TCP-порт на 127.0.0.1."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 10) -> tuple[int, str]:
    """GET, пока слушатель не ответит: (код, тело), в том числе 503; не дождался — AssertionError."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # мимо прокси окружения
    end = time.monotonic() + timeout
    while True:
        try:
            with opener.open(url, timeout=2) as r:
                return r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode("utf-8")
        except (urllib.error.URLError, OSError):
            if time.monotonic() > end:
                raise AssertionError(f"{url}: слушатель не ответил за {timeout} с") from None
            time.sleep(0.1)


class FakeMono:
    """Подменные монотонные часы: вызываемые, идут только по advance(s)."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


def make_role(settings: Settings = SETTINGS, fake: FakeHistorian | None = None,
              whitelist: frozenset[str] = WHITELIST, mono: Callable[[], float] | None = None, *,
              db_dir: str | Path | None = None, tokens: TokenTable = TEST_TOKENS,
              config_error: str | None = None) -> tuple[DataRole, FakeHistorian]:
    """Роль «данные» на двойнике историана; часы по умолчанию — FakeMono(1000.0).

    Журнал событий — core.db в db_dir, по умолчанию в новом каталоге внутри каталога сессии: у каждой
    роли свой журнал. Каталог только запоминается — роль его не трогает до lifespan или записи.
    """
    fake = FakeHistorian() if fake is None else fake
    mono = FakeMono(1000.0) if mono is None else mono
    db_dir = tempfile.mkdtemp(dir=SESSION_DIR) if db_dir is None else db_dir
    settings = replace(settings, DB_PATH=str(Path(db_dir) / "core.db"))
    return DataRole(settings, fake, whitelist, monotonic=mono, tokens=tokens, config_error=config_error), fake


def make_service(tmp_path, **kwargs) -> tuple[DataService, FakeHistorian]:
    """Служба роли с загруженным каталогом; fake.calls очищены.

    Каталог грузится теми же запросами, что refresh_catalog, но прямо через двойник, без ворот и цикла
    событий: помощник синхронный. Первый вызов инструмента поэтому — первый вход ворот.
    """
    role, fake = make_role(db_dir=tmp_path, **kwargs)
    clock_rows, tag_rows = fake([clock_sql(), catalog_sql()], 10.0)
    [live_rows] = fake([live_all_sql()], 10.0)
    role._catalog_loaded(Catalog.from_rows(tag_rows, live_rows, role.whitelist), parse_clock(clock_rows),
                         role.monotonic())
    fake.calls.clear()
    fake.timeouts.clear()
    return role.service, fake


def make_app(tmp_path, *, extra_roles: Sequence[Role] = (), **kwargs) -> tuple[FastAPI, DataRole]:
    """Приложение с ролью «данные» на двойнике и extra_roles за ней; каталог грузит lifespan (TestClient).

    kwargs — в make_role.
    """
    role, _ = make_role(db_dir=tmp_path, **kwargs)
    return create_app(SETTINGS, [role, *extra_roles]), role


def sent_of(fake: FakeHistorian) -> list[str]:
    """Имена из names_in всех SQL, переданных двойнику, по порядку."""
    return [name for call in fake.calls for sql in call for name in names_in(sql)]


def wait_until(pred: Callable[[], bool], timeout: float = 5) -> None:
    """Ждёт условия в потоке теста (цикл TestClient — в своём потоке); не дождался — AssertionError."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError(f"условие не выполнилось за {timeout} с")
        time.sleep(0.01)


async def wait_until_async(pred: Callable[[], bool], timeout: float = 5) -> None:
    """Ждёт условия в цикле событий теста, отдавая его фоновым задачам; не дождался — AssertionError."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError(f"условие не выполнилось за {timeout} с")
        await anyio.sleep(0.01)


@contextmanager
def serve_app(app: FastAPI) -> Iterator[str]:
    """Приложение под uvicorn в потоке на 127.0.0.1 (порт выбирает ядро); отдаёт базовый url.

    lifespan включён. Журнал uvicorn не настраивается, доступ не пишется — как в serve(). Не поднялся или не
    остановился за 10 с — AssertionError.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="on", log_config=None, access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="serve-app", daemon=True)
    thread.start()
    try:
        wait_until(lambda: server.started or not thread.is_alive(), timeout=10)
        assert server.started, "uvicorn не поднялся"
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive(), "uvicorn не остановился за 10 с"


@contextmanager
def serve_role(role: DataRole, *, wait_catalog: bool = True) -> Iterator[str]:
    """create_app с одной ролью под uvicorn (serve_app); отдаёт url.

    wait_catalog — ждать загрузки каталога (роль с ошибкой настройки его не грузит).
    """
    with serve_app(create_app(SETTINGS, [role])) as url:
        if wait_catalog:
            wait_until(lambda: role.catalog.loaded)
        yield url


@asynccontextmanager
async def mcp_session(url: str, token: str) -> AsyncIterator[ClientSession]:
    """Клиент MCP SDK с Bearer-токеном: Streamable HTTP → ClientSession → initialize().

    streamable_http_client с готовым httpx-клиентом — то же, что устаревший streamablehttp_client(url,
    headers=…), без предупреждения; trust_env=False — мимо прокси окружения.
    """
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(10, read=30), trust_env=False) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def http_post(url: str, body: object, token: str | None = None) -> tuple[int, str]:
    """POST JSON мимо прокси окружения: (код, тело), в том числе 4xx и 5xx."""
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read().decode("utf-8")


async def dummy(scope, receive, send):
    """Точный маршрут роли — обычная ASGI-функция: 200 на любой метод."""
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
    await send({"type": "http.response.body", "body": b"dummy"})


async def _mark_installed(request, call_next):
    response = await call_next(request)
    response.headers["X-Role-Installed"] = "1"
    return response


class _Typed(BaseModel):
    n: int


class DummyRole(RoleBase):
    """Роль-двойник: здоровье задано; installs=True — промежуточное звено и точный маршрут /dummy.

    validates — префикс: обработчик 422 на него в реестре крючков, ответ 422 {"handled_by": имя роли}, и
    маршрут POST {префикс}typed с телом {"n": int}. body_limit — (префикс, предел) в реестр крючков.
    """

    def __init__(self, name: str, health: tuple[bool, str], installs: bool = False,
                 validates: str | None = None, body_limit: tuple[str, int] | None = None):
        self.name = name
        self._health = health
        self.installs = installs
        self.validates = validates
        self.body_limit = body_limit

    def router(self) -> APIRouter:
        router = APIRouter()
        if self.validates is not None:
            @router.post(f"{self.validates}typed")
            async def typed(body: _Typed) -> dict:
                return {"n": body.n}
        return router

    def health(self) -> tuple[bool, str]:
        return self._health

    def lifespan(self):
        return nullcontext()

    def install(self, app: FastAPI) -> None:
        if self.installs:
            app.middleware("http")(_mark_installed)
        if self.validates is not None:
            async def handled(request, exc) -> JSONResponse:
                return JSONResponse({"handled_by": self.name}, status_code=422)
            hooks_of(app).validation(self.validates, handled)
        if self.body_limit is not None:
            hooks_of(app).body_limit(*self.body_limit)

    def mounts(self):
        return [("/dummy", dummy)] if self.installs else []


# --- LLM-прокси (Д4а, задача 6) ---

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def chat_body(text: str = "вопрос", model: str = "z-ai/glm-5.3", stream: bool = True, steps: int = 0,
              tools: bool = True, tail_user: str | None = None) -> dict:
    """Тело запроса OpenCode: system, вопрос user, steps пар «assistant с вызовом — tool», при tail_user — ещё
    одно сообщение user в конце (продолжение после сжатия); tools — один инструмент pcbk_tag_now."""
    messages: list[dict] = [{"role": "system", "content": "sys"},
                            {"role": "user", "content": [{"type": "text", "text": text}]}]
    for i in range(steps):
        messages.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": f"call-{i}", "type": "function",
                                         "function": {"name": "pcbk_tag_now",
                                                      "arguments": '{"tags": ["20FAKE_001_PV"]}'}}]})
        messages.append({"role": "tool", "tool_call_id": f"call-{i}", "content": "{\"status\": \"ok\"}"})
    if tail_user is not None:
        messages.append({"role": "user", "content": [{"type": "text", "text": tail_user}]})
    body = {"model": model, "stream": stream, "messages": messages}
    if tools:
        body["tools"] = [TAG_NOW_TOOL]
    return body


def auth(token: str | None, session: str | None = None, parent: str | None = None) -> dict:
    """Заголовки вызова: Authorization (без токена — нет), X-Session-Id, при parent — x-parent-session-id."""
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if session is not None:
        headers["X-Session-Id"] = session
    if parent is not None:
        headers["x-parent-session-id"] = parent
    return headers


def make_llm_role(tmp_path, fake=None, *, key: str | None = "test-openrouter-key", mgmt: str | None = None,
                  wallclock: Callable[[], datetime] | None = None, **llm_overrides) -> LlmRole:
    """Роль llm на двойнике OpenRouter (fake — фикстура fake_or): журнал — tmp_path/core.db, открыт, как в
    lifespan; LlmSettings — replace без проверок from_env; таблица из двух токенов; дозапрос цены — 3 × 0,01 с."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    if fake is not None:
        llm_overrides = {"OPENROUTER_BASE_URL": fake.url, **llm_overrides}
    llm = replace(LLM_SETTINGS, **llm_overrides)
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"))
    clock = {} if wallclock is None else {"wallclock": wallclock}
    upstream = Upstream(llm.OPENROUTER_BASE_URL, key, mgmt, drill=llm.DRILL_LLM) if key else None
    role = LlmRole(settings, llm, tokens=LLM_TOKENS, upstream=upstream, ledger=Ledger(settings.DB_PATH, **clock),
                   key_fingerprint=key_fingerprint(key) if key else None, cost_retry_delays=(0.01, 0.01, 0.01),
                   **clock)
    assert role.ledger.open(), role.ledger.last_error
    return role


def make_llm_app(tmp_path, fake=None, **kw) -> tuple[FastAPI, LlmRole]:
    """create_app с одной ролью llm (make_llm_role); у приложения тот же core.db."""
    role = make_llm_role(tmp_path, fake, **kw)
    return create_app(replace(SETTINGS, DB_PATH=role.settings.DB_PATH), [role]), role


# --- Шлюз: учётные записи (Д5а, задача А2) ---

# тестовый пароль учёток: не секрет, 10 знаков — проходит PASSWORD_MIN
PW = "пароль-123"


def store(tmp_path, **kw) -> AccountStore:
    """AccountStore на tmp_path/core.db с вызванным open(); kw — в конструктор (clock, ttl_s)."""
    s = AccountStore(str(Path(tmp_path) / "core.db"), **kw)
    assert s.open(), s.last_error
    return s


def fail_connect(*a, **k):
    """Подмена sqlite3.connect: база заперта."""
    raise sqlite3.OperationalError("database is locked")


# --- Шлюз: места (Д5а, задача А3) ---

# настройки шлюза тестов строятся напрямую: проверки from_env (задача А4) на них не действуют
GW_SETTINGS = GatewaySettings()


def pw_dir(tmp_path) -> Path:
    """Каталог паролей мест student-01…10.pw (пароли — PLACE_PW, без перевода строки, как на сервере)."""
    d = Path(tmp_path) / "place-secrets"
    d.mkdir(parents=True, exist_ok=True)
    for n, pw in PLACE_PW.items():
        (d / f"student-{n:02d}.pw").write_text(pw, encoding="utf-8")
    return d


def fast_sleep(mono: FakeMono) -> Callable:
    """Пауза для Workplaces: двигает подменные часы на s и только отдаёт управление циклу."""
    async def sleep(s: float) -> None:
        mono.advance(s)
        await asyncio.sleep(0)
    return sleep


def make_places(tmp_path, docker: FakeDocker | None = None, oc: FakePlaces | None = None,
                **overrides) -> Workplaces:
    """Workplaces на двойниках sp-ctl и мест, FakeMono(1000.0) и fast_sleep; overrides — поля GatewaySettings.

    Тестам доступны .docker, .oc, .mono, .stats. Клиенты открываются при первом запросе (как Upstream, П22):
    помощник синхронный, а open() — корутина.
    """
    docker = FakeDocker() if docker is None else docker
    oc = FakePlaces() if oc is None else oc
    gw = replace(GW_SETTINGS, **{"GW_PLACE_PW_DIR": str(pw_dir(tmp_path)), **overrides})
    mono = FakeMono(1000.0)
    stats = GatewayStats(monotonic=mono)
    oc.stu_net = gw.STU_NET
    w = Workplaces(gw, SpCtl(gw.SP_CTL_URL, transport=httpx.MockTransport(docker)), stats=stats,
                   transport=httpx.MockTransport(oc), monotonic=mono, sleep=fast_sleep(mono))
    w.docker, w.oc, w.mono = docker, oc, mono
    return w


def ready_place(workplaces: Workplaces, docker: FakeDocker, n: int) -> None:
    """Место n работает в двойнике Docker и готово у шлюза без пробуждения — как после такта (А4
    gateway_role(ready=…), Д5б): первый такт его не трогает."""
    workplaces.set_ready_for_tests(n, docker.run(n))
