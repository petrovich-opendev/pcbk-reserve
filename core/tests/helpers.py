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
from contextlib import asynccontextmanager, closing, contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import anyio
import httpx
import pytest
import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from fake_places import PLACE_PW, FakeDocker, FakePlaces
from fakes import WHITELIST, FakeHistorian
from pcbk_core.agents.draft import Draft, render_agent_file
from pcbk_core.app import Role, RoleBase, create_app
from pcbk_core.data import DataRole
from pcbk_core.data.catalog import Catalog, TagInfo
from pcbk_core.data.events import CallEvent
from pcbk_core.data.historian import parse_clock
from pcbk_core.data.service import DataService
from pcbk_core.data.sql import catalog_sql, clock_sql, live_all_sql, names_in
from pcbk_core.gateway import GatewayRole
from pcbk_core.gateway.accounts import AccountStore
from pcbk_core.gateway.api import COOKIE
from pcbk_core.gateway.places import SpCtl, Workplaces
from pcbk_core.gateway.stats import GatewayStats
from pcbk_core.hooks import hooks_of
from pcbk_core.llm import LlmRole
from pcbk_core.llm.ledger import Ledger
from pcbk_core.llm.shares import Shares
from pcbk_core.llm.upstream import Upstream
from pcbk_core.secrets import BdrvConfig, TokenTable, key_fingerprint
from pcbk_core.settings import GatewaySettings, LlmSettings, Settings

# каталог core/: из него подпроцессы импортируют pcbk_core
CORE_DIR = Path(__file__).resolve().parent.parent
# корень репозитория: compose.yaml, bench/ (Д8а)
REPO = CORE_DIR.parent

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

# тормоз прогона (Д11-R117): двойники /proc для ролей llm тестов — свободно 8000 из 16000 МиБ, swap свободен, убийств и
# давления нет. Настоящий /proc машины тестов в тормоз не идёт: её память и убийства — не повод для 402 в тестах Д8
BRAKE_PROC: dict[str, str] = {}
for _name, _file, _text in (
        ("LLM_RUN_MEMINFO", "meminfo",
         "MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\nSwapTotal: 4194304 kB\nSwapFree: 4194304 kB\n"),
        ("LLM_RUN_VMSTAT", "vmstat", "oom_kill 0\n"),
        ("LLM_RUN_PRESSURE", "pressure", "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                                         "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")):
    (SESSION_DIR / f"brake-{_file}").write_text(_text, encoding="utf-8")
    BRAKE_PROC[_name] = str(SESSION_DIR / f"brake-{_file}")

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

    kwargs — в make_role. Пометка zone_names_ok: имя «testserver» клиента тестов — своя зона (Д5-R31).
    """
    role, _ = make_role(db_dir=tmp_path, **kwargs)
    app = create_app(SETTINGS, [role, *extra_roles])
    app.state.zone_names_ok = True          # клиент тестов в процессе — свой: имя вместо адреса (Д5-R31)
    return app, role


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
                  wallclock: Callable[[], datetime] | None = None, shares: bool = False, shares_drill: str = "",
                  **llm_overrides) -> LlmRole:
    """Роль llm на двойнике OpenRouter (fake — фикстура fake_or): журнал — tmp_path/core.db, открыт, как в
    lifespan; LlmSettings — replace без проверок from_env; таблица из двух токенов; дозапрос цены — 3 × 0,01 с.

    shares (Д10а, задача А2) — доли мест Shares на том же core.db (бюджет и доля по умолчанию — из LlmSettings, учение —
    shares_drill), как в build_llm_role. Доли не открыты: их открывает lifespan роли — так тест «доли не загрузились»
    проверяет настоящий путь.

    Тормоз прогона (Д11а): пути /proc — двойники BRAKE_PROC, если тест не задал свои LLM_RUN_MEMINFO/VMSTAT/PRESSURE."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    llm_overrides = {**BRAKE_PROC, **llm_overrides}
    if fake is not None:
        llm_overrides = {"OPENROUTER_BASE_URL": fake.url, **llm_overrides}
    llm = replace(LLM_SETTINGS, **llm_overrides)
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"))
    clock = {} if wallclock is None else {"wallclock": wallclock}
    upstream = Upstream(llm.OPENROUTER_BASE_URL, key, mgmt, drill=llm.DRILL_LLM) if key else None
    places_shares = (Shares(settings.DB_PATH, total_usd=llm.LLM_TOTAL_USD, default_usd=llm.LLM_SHARE_USD,
                            drill=shares_drill) if shares else None)
    role = LlmRole(settings, llm, tokens=LLM_TOKENS, upstream=upstream, ledger=Ledger(settings.DB_PATH, **clock),
                   key_fingerprint=key_fingerprint(key) if key else None, cost_retry_delays=(0.01, 0.01, 0.01),
                   shares=places_shares, **clock)
    assert role.ledger.open(), role.ledger.last_error
    return role


def make_llm_app(tmp_path, fake=None, **kw) -> tuple[FastAPI, LlmRole]:
    """create_app с одной ролью llm (make_llm_role); у приложения тот же core.db."""
    role = make_llm_role(tmp_path, fake, **kw)
    app = create_app(replace(SETTINGS, DB_PATH=role.settings.DB_PATH), [role])
    app.state.zone_names_ok = True          # /health/llm полностью — как из pcbk-front (Д5-R31)
    return app, role


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


class FullDisk:
    """Соединение SQLite как на переполненном диске: COMMIT и запись в режиме автофиксации (сама себе COMMIT)
    падают SQLITE_FULL; чтения и запись внутри транзакции до COMMIT идут (в WAL вставка без COMMIT на диск не
    пишет). Подмена AccountStore._connect: full_disk(store)."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db

    def execute(self, sql: str, *args):
        head = sql.strip().upper()
        if head == "COMMIT" or (not self._db.in_transaction and head.startswith(("INSERT", "UPDATE", "DELETE"))):
            raise sqlite3.OperationalError("database or disk is full")
        return self._db.execute(sql, *args)

    def __getattr__(self, name: str):
        return getattr(self._db, name)


@contextmanager
def full_disk(s: AccountStore) -> Iterator[None]:
    """На время блока соединения хранилища s — FullDisk; после — прежние."""
    real = s._connect
    s._connect = lambda create=False: FullDisk(real(create))
    try:
        yield
    finally:
        del s._connect


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


# --- Шлюз: роль (Д5а, задача А4) ---

# учётные данные историана для egress_targets: несекретная заглушка
BDRV_CFG = BdrvConfig("h", 1433, "Runtime", "u", "p")
# публичный origin страницы входа в тестах (GW_ORIGIN)
ORIGIN = "https://stand.test:8443"
# заголовки источника записи со своей страницы — у POST ручек шлюза (Д11а, тормоз прогона: вход студента и кабинет)
ORIGIN_HEADERS = {"Origin": ORIGIN}


def gateway_role(tmp_path, docker: FakeDocker | None = None, oc: FakePlaces | None = None, ready: Sequence[int] = (),
                 stream_recheck_s: float = 10.0, config_error: str | None = None, db_path: Path | None = None,
                 accounts: bool = True, agents_root: Path | None = None, calc: DataService | None = None,
                 llm: LlmRole | None = None, **gw_overrides) -> GatewayRole:
    """Роль gateway на двойниках задачи А3, FakeMono(1000.0), GW_ORIGIN = ORIGIN; gw_overrides — поля
    GatewaySettings (без проверок from_env), в том числе учения DRILL_GATEWAY, DRILL_AGENTS и DRILL_TEACHER (Д10а, m-6).
    stream_recheck_s — срок перепроверки сессии потоком /event (Д5б, Б4).

    tmp_path создаётся; база — db_path или tmp_path/core.db. accounts — завести отдельным AccountStore с open()
    учётки stu-03 (место 3), stu-05 (место 5) и teach (преподаватель), у всех пароль PW; иначе база заранее не
    открывается. Места из ready работают в двойнике Docker и готовы без пробуждения: первый такт их не трогает.
    Корень агентов (Settings.AGENTS_ROOT, Д7а, предпроверка П3) — agents_root как есть, по умолчанию
    tmp_path/agents, подготовленный make_agents_root: воркер перезагрузки проверяет каталог первым тактом.
    calc — служба данных калькуляторов страницы (Д9-R87, обычно make_service), None — маршрут отвечает 503.
    llm — роль LLM-прокси с долями мест (Д10а, задача А2: make_llm_role(…, shares=True)), None — шлюз без неё.
    Роли для тестов добавлены fake_docker, fake_places, mono.
    """
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = make_agents_root(tmp_path / "agents") if agents_root is None else Path(agents_root)
    db = Path(db_path) if db_path is not None else tmp_path / "core.db"
    docker = FakeDocker() if docker is None else docker
    oc = FakePlaces() if oc is None else oc
    gw = replace(GW_SETTINGS, **{"GW_PLACE_PW_DIR": str(pw_dir(tmp_path)), "GW_ORIGIN": ORIGIN, **gw_overrides})
    mono = FakeMono(1000.0)
    stats = GatewayStats(monotonic=mono)
    oc.stu_net = gw.STU_NET
    workplaces = Workplaces(gw, SpCtl(gw.SP_CTL_URL, transport=httpx.MockTransport(docker)), stats=stats,
                            transport=httpx.MockTransport(oc), monotonic=mono, sleep=fast_sleep(mono))
    if accounts:
        s = AccountStore(str(db))
        assert s.open(), s.last_error
        s.add("stu-03", "student", 3, PW)
        s.add("stu-05", "student", 5, PW)
        s.add("teach", "teacher", None, PW)
    for n in ready:
        ready_place(workplaces, docker, n)
    role = GatewayRole(replace(SETTINGS, DB_PATH=str(db), AGENTS_ROOT=str(root)), gw,
                       store=AccountStore(str(db), ttl_s=gw.GW_SESSION_TTL_S), workplaces=workplaces, stats=stats,
                       config_error=config_error, monotonic=mono, stream_recheck_s=stream_recheck_s, calc=calc,
                       llm=llm)
    role.fake_docker, role.fake_places, role.mono = docker, oc, mono
    return role


def park_reload_worker(role: GatewayRole) -> None:
    """Фоновый воркер перезагрузки агентов (Д7-R58) под lifespan не идёт, а ждёт остановки роли: такты ведёт тест сам
    (предпроверка m9) — ручной такт и фоновый не спорят о пульсе и флагах учёта."""
    async def parked() -> None:
        await asyncio.Event().wait()
    role.reloads.run = parked


def make_gateway(tmp_path, **kw) -> tuple[FastAPI, GatewayRole]:
    """create_app(SETTINGS, [gateway_role(tmp_path, **kw)]) и пометка zone_names_ok: клиент тестов — свой."""
    role = gateway_role(tmp_path, **kw)
    app = create_app(SETTINGS, [role])
    app.state.zone_names_ok = True
    return app, role


# разговор места 03 для кабинета преподавателя (Д10а, задача А3): один вопрос и один ответ, вид — как у
# web/tests/chatGateway.ts (OpenCode 1.18.33); теги — только 20FAKE_*
TEACHER_SES = "ses_" + "teacher0123456789abcdefgh".ljust(26, "Z")
TEACHER_SESSION = {"id": TEACHER_SES, "slug": "s", "projectID": "p", "directory": "/work",
                   "title": "Значение 20FAKE_001_PV", "version": "1.18.33", "time": {"created": 1, "updated": 3}}
TEACHER_HISTORY = [
    {"info": {"id": "msg_t1", "sessionID": TEACHER_SES, "role": "user", "time": {"created": 1}, "agent": "pomoshnik",
              "model": {"providerID": "openrouter", "modelID": "m"}},
     "parts": [{"id": "prt_msg_t1", "sessionID": TEACHER_SES, "messageID": "msg_t1", "type": "text",
                "text": "что сейчас на 20FAKE_001_PV"}]},
    {"info": {"id": "msg_t2", "sessionID": TEACHER_SES, "role": "assistant", "time": {"created": 2, "completed": 3},
              "parentID": "msg_t1", "modelID": "m", "providerID": "openrouter", "mode": "pomoshnik",
              "agent": "pomoshnik", "path": {"cwd": "/work", "root": "/work"}, "cost": 0,
              "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}}, "finish": "stop"},
     "parts": [{"id": "prt_msg_t2", "sessionID": TEACHER_SES, "messageID": "msg_t2", "type": "text",
                "text": "Готово: 12,5"}]},
]


def teacher_gateway(tmp_path, *, ready: Sequence[int] = (3,), oc: FakePlaces | None = None,
                    **gw_overrides) -> tuple[FastAPI, GatewayRole]:
    """Шлюз кабинета преподавателя (Д10а, задача А2; m-1): роль llm с долями мест на том же core.db (без ключа
    OpenRouter, учение долей — DRILL_TEACHER из gw_overrides, как в build_roles), доли открыты здесь (роль llm в
    приложение шлюза не входит, её lifespan не идёт; итог open() не проверяется — тест «доли не загрузились» его
    подменяет), затем make_gateway(…, llm=…, ready=ready, **gw_overrides). Место 03 по умолчанию готово.

    Задача А3: у двойника мест (oc или новый FakePlaces) место 03 держит разговор TEACHER_SES с сообщениями
    TEACHER_HISTORY — его читает кабинет."""
    llm = make_llm_role(tmp_path, key=None, shares=True, shares_drill=gw_overrides.get("DRILL_TEACHER", ""))
    llm.shares.open()
    oc = FakePlaces() if oc is None else oc
    oc.sessions.setdefault(3, [TEACHER_SESSION])
    oc.messages.setdefault((3, TEACHER_SES), TEACHER_HISTORY)
    return make_gateway(tmp_path, llm=llm, ready=ready, oc=oc, **gw_overrides)


def login(c, name: str, pw: str = PW, **headers) -> httpx.Response:
    """POST /pcbk/api/auth/login; Origin: ORIGIN, если не передан другой; заголовок с пустым значением не шлётся."""
    sent = {"Origin": ORIGIN, **headers}
    return c.post("/pcbk/api/auth/login", json={"login": name, "password": pw},
                  headers={k: v for k, v in sent.items() if v != ""})


# --- Шлюз: клиенты по зонам и учёткам (Д7б «Мои агенты»; общие с калькуляторами Д9а) ---

BASE = "https://stand.test:8443"                # cookie сессии — Secure: клиент тестов шлёт её только по https
FRONT = ("172.31.250.35", 40000)                # edge в pcbk-front
PLACE_NET = ("172.31.3.3", 5000)                # сеть места 03


class Gw:
    """Шлюз на двойниках под lifespan: as_(login, client) — клиент с cookie сессии этой учётки (вход — из front),
    anon(client) — без cookie, store_save — строка учёта агента напрямую в core.db."""

    def __init__(self, app, role):
        self.app, self.role = app, role

    def as_(self, name: str, client=FRONT) -> TestClient:
        r = login(TestClient(self.app, base_url=BASE, client=FRONT), name)
        assert r.status_code == 200, (name, r.status_code)
        return TestClient(self.app, base_url=BASE, client=client, headers={"Cookie": f"{COOKIE}={r.cookies[COOKIE]}"})

    def anon(self, client=FRONT) -> TestClient:
        return TestClient(self.app, base_url=BASE, client=client)

    def store_save(self, place: int, name: str, state: str, title: str = "", purpose: str = "") -> None:
        at = AGENT_NOW.isoformat()
        with closing(sqlite3.connect(self.role.agents.db_path)) as db, db:
            db.execute("INSERT INTO agents (place, name, title, node, purpose, tools, tags, digest, sha256,"
                       " student_reply, flags, state, created_at, state_at, visible_at)"
                       " VALUES (?, ?, ?, 'узел', ?, '[]', '[]', 'd', 's', 'да', '[]', ?, ?, ?, NULL)",
                       (place, name, title or f"Агент {name}", purpose or f"назначение {name}", state, at, at))


# --- Шлюз: прокси /pcbk/api/oc (Д5б, задача Б3) ---

def oc_headers(**extra) -> dict:
    """Заголовки вызова адаптера со своей страницы: Origin: ORIGIN и extra (места готовит make_gateway(…, ready=…))."""
    return {"Origin": ORIGIN, **extra}


# --- Шлюз: поток /event (Д5б, задача Б4) ---

def login_cookie(url: str, name: str, pw: str = PW) -> str:
    """Вход по HTTP (serve_app) — значение cookie pcbk_session из Set-Cookie: по http httpx cookie Secure не шлёт,
    тест ставит её заголовком Cookie сам."""
    r = httpx.post(url + "/pcbk/api/auth/login", json={"login": name, "password": pw}, headers={"Origin": ORIGIN},
                   trust_env=False)
    assert r.status_code == 200, r.status_code
    line = next(h for h in r.headers.get_list("set-cookie") if h.startswith("pcbk_session="))
    return line.split(";", 1)[0].split("=", 1)[1]


# --- Агенты студентов: каталог черновика (Д7а, задача А2) ---

# 16 тегов белого списка с именами ровно по 128 знаков (вид SAFE_NAME) — худший черновик по размеру файла (m10)
BIG_TAGS = tuple(f"20FAKE_{100 + i:03d}_PV".ljust(128, "X") for i in range(16))


def _agent_tag(name: str, description: str, unit: str | None) -> TagInfo:
    return TagInfo(name=name, description=description, kind="analog", unit=unit, min_eu=0.0, max_eu=100.0,
                   live=False, live_time=None)


def agent_catalog(big: bool = False) -> Catalog:
    """Каталог черновика агента: 20FAKE_001_PV (в списке; описание с кавычками, переводом строки, «##» и обратными
    кавычками; °C), 20FAKE_002_PV (вне списка), 20FAKE_003_PV (в списке, без единицы).

    big — ещё BIG_TAGS в белом списке: описания по 50 знаков и единицы по 10 знаков, кириллицей (два байта на знак).
    """
    tags = [_agent_tag("20FAKE_001_PV", 'Температура "сукна"\n## x `y`', "°C"),
            _agent_tag("20FAKE_002_PV", "Скорость сеточного стола", "м/мин"),
            _agent_tag("20FAKE_003_PV", "Давление пара в сушильной части", None)]
    listed = {"20FAKE_001_PV", "20FAKE_003_PV"}
    if big:
        tags += [_agent_tag(name, f"Описание длинного тега номер {i:02d} ".ljust(50, "ж"), "единицаизм")
                 for i, name in enumerate(BIG_TAGS)]
        listed |= set(BIG_TAGS)
    return Catalog({t.name: t for t in tags}, frozenset(listed))


# --- Агенты студентов: учёт и файлы (Д7а, задача А3) ---

AGENT_NOW = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)
# черновик агента тестов: синтетический текст, без тегов (данных заказчика в файле нет)
AGENT_DRAFT = Draft(name="sushka-bdm2", title="Сушка БДМ-2", node="Сушильная часть",
                    purpose="Текущие значения и сводка за смену", tools=("catalog_search", "tag_now"), tags=(),
                    prompt="Ты — помощник технолога по узлу «Сушка». Отвечай про температуру и давление коротко. " * 4,
                    student_reply="да, сохраняй")


def agent_text(draft: Draft = AGENT_DRAFT, caller: str = "student-03") -> str:
    """Файл агента по шаблону v1 (Д7-R55) для черновика тестов."""
    return render_agent_file(draft, {}, caller=caller, created_at=AGENT_NOW)


def make_agents_root(path) -> Path:
    """Корень агентов, как на сервере после смены владельца (Д7-R55): student-01…10 — 0755, quarantine и
    archive — 0700. Сам корень — 0700 (у владельца тестов)."""
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    for n in range(1, 11):
        (root / f"student-{n:02d}").mkdir(exist_ok=True)
        (root / f"student-{n:02d}").chmod(0o755)
    for side in ("quarantine", "archive"):
        (root / side).mkdir(exist_ok=True)
        (root / side).chmod(0o700)
    return root
