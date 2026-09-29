"""Общие константы и помощники тестов серверного слоя (импорт явный: from helpers import …)."""
import itertools
from contextlib import nullcontext

from fastapi import APIRouter, FastAPI

from pcbk_core.app import RoleBase
from pcbk_core.settings import Settings

# настройки тестов строятся напрямую: проверки from_env на них не действуют
SETTINGS = Settings()

_files = itertools.count(1)


def write(tmp_path, text: str) -> str:
    """Новый файл с текстом в tmp_path; путь строкой."""
    path = tmp_path / f"file-{next(_files)}.env"
    path.write_text(text, encoding="utf-8")
    return str(path)


class FakeMono:
    """Подменные монотонные часы: вызываемые, идут только по advance(s)."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


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
