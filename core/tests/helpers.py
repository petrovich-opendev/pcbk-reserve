"""Общие константы и помощники тестов серверного слоя (импорт явный: from helpers import …)."""
import atexit
import hashlib
import itertools
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import anyio
import pytest
from fastapi import APIRouter, FastAPI

from fakes import WHITELIST, FakeHistorian
from pcbk_core.app import RoleBase
from pcbk_core.data import DataRole
from pcbk_core.data.events import CallEvent
from pcbk_core.secrets import TokenTable
from pcbk_core.settings import Settings

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
              ) -> tuple[DataRole, FakeHistorian]:
    """Роль «данные» на двойнике историана; часы по умолчанию — FakeMono(1000.0).

    Журнал событий — core.db в db_dir, по умолчанию в новом каталоге внутри каталога сессии: у каждой
    роли свой журнал. Каталог только запоминается — роль его не трогает до lifespan или записи.
    """
    fake = FakeHistorian() if fake is None else fake
    mono = FakeMono(1000.0) if mono is None else mono
    db_dir = tempfile.mkdtemp(dir=SESSION_DIR) if db_dir is None else db_dir
    settings = replace(settings, DB_PATH=str(Path(db_dir) / "core.db"))
    return DataRole(settings, fake, whitelist, monotonic=mono, tokens=tokens), fake


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


async def dummy(scope, receive, send):
    """Точный маршрут роли — обычная ASGI-функция: 200 на любой метод."""
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
    await send({"type": "http.response.body", "body": b"dummy"})


async def _mark_installed(request, call_next):
    response = await call_next(request)
    response.headers["X-Role-Installed"] = "1"
    return response


class DummyRole(RoleBase):
    """Роль-двойник: здоровье задано; installs=True — промежуточное звено и точный маршрут /dummy."""

    def __init__(self, name: str, health: tuple[bool, str], installs: bool = False):
        self.name = name
        self._health = health
        self.installs = installs

    def router(self) -> APIRouter:
        return APIRouter()

    def health(self) -> tuple[bool, str]:
        return self._health

    def lifespan(self):
        return nullcontext()

    def install(self, app: FastAPI) -> None:
        if self.installs:
            app.middleware("http")(_mark_installed)

    def mounts(self):
        return [("/dummy", dummy)] if self.installs else []
