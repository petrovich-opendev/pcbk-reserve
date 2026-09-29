"""Общие константы и помощники тестов серверного слоя (импорт явный: from helpers import …)."""
import hashlib
import itertools
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from fastapi import APIRouter, FastAPI

from fakes import WHITELIST, FakeHistorian
from pcbk_core.app import RoleBase
from pcbk_core.data import DataRole
from pcbk_core.settings import Settings

# настройки тестов строятся напрямую: проверки from_env на них не действуют
SETTINGS = Settings()
# каталог core/: из него подпроцессы импортируют pcbk_core
CORE_DIR = Path(__file__).resolve().parent.parent

_files = itertools.count(1)


def write(tmp_path, text: str) -> str:
    """Новый файл с текстом в tmp_path; путь строкой."""
    path = tmp_path / f"file-{next(_files)}.env"
    path.write_text(text, encoding="utf-8")
    return str(path)


def sha(text: str) -> str:
    """sha256 строки в шестнадцатеричном виде — как в файле core-tokens."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
              whitelist: frozenset[str] = WHITELIST, mono: Callable[[], float] | None = None,
              ) -> tuple[DataRole, FakeHistorian]:
    """Роль «данные» на двойнике историана; часы по умолчанию — FakeMono(1000.0)."""
    fake = FakeHistorian() if fake is None else fake
    mono = FakeMono(1000.0) if mono is None else mono
    return DataRole(settings, fake, whitelist, monotonic=mono), fake


def wait_until(pred: Callable[[], bool], timeout: float = 5) -> None:
    """Ждёт условия в потоке теста (цикл TestClient — в своём потоке); не дождался — AssertionError."""
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > end:
            raise AssertionError(f"условие не выполнилось за {timeout} с")
        time.sleep(0.01)


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
