"""Протокол роли и сборка приложения: роутеры ролей, здоровье по ролям, точные маршруты."""
import logging
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Protocol

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .settings import Settings

log = logging.getLogger("pcbk_core.app")


class Role(Protocol):
    """Роль серверного слоя: данные (Д3а), LLM-прокси (Д4), шлюз (Д5)."""

    name: str

    def router(self) -> APIRouter: ...

    def health(self) -> tuple[bool, str]:
        """Быстрая проверка без ввода-вывода: вызывается в цикле событий."""
        ...

    def lifespan(self) -> AbstractAsyncContextManager[None]: ...

    def install(self, app: FastAPI) -> None:
        """Обработчики исключений и промежуточные звенья уровня приложения — только здесь."""
        ...

    def mounts(self) -> list[tuple[str, ASGIApp]]:
        """Точные маршруты: путь → ASGI-приложение на любой метод."""
        ...


class RoleBase:
    """Пустые install и mounts — роли наследуют и заполняют нужное."""

    def install(self, app: FastAPI) -> None:
        pass

    def mounts(self) -> list[tuple[str, ASGIApp]]:
        return []


class _Asgi:
    """Переходник: Route пускает на любой метод только экземпляр класса.

    Обычную функцию Starlette считает обработчиком запроса и пускает лишь на GET.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)


def _health(role: Role) -> tuple[bool, str]:
    try:
        ok, detail = role.health()
        return bool(ok), str(detail)
    except Exception:
        log.exception("роль %s: проверка здоровья упала", role.name)
        return False, "сбой проверки здоровья"


def create_app(settings: Settings, roles: Sequence[Role]) -> FastAPI:
    roles = list(roles)
    by_name = {r.name: r for r in roles}
    if len(by_name) != len(roles):
        raise ValueError(f"роли с одинаковыми именами: {[r.name for r in roles]}")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            for role in roles:
                await stack.enter_async_context(role.lifespan())
            yield

    # с Д4 порт 8000 виден из сетей мест: описание API им не нужно;
    # без перенаправлений /mcp/ даёт 404, а не 307
    app = FastAPI(title="pcbk-core", docs_url=None, redoc_url=None, openapi_url=None,
                  redirect_slashes=False, lifespan=lifespan)
    app.state.settings = settings

    for role in roles:
        app.include_router(role.router())

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        results = {r.name: _health(r) for r in roles}
        ok = all(good for good, _ in results.values())
        return JSONResponse({"ok": ok, "roles": {name: detail for name, (_, detail) in results.items()}},
                            status_code=200 if ok else 503)

    @app.get("/healthz/{role}")
    async def role_healthz(role: str) -> JSONResponse:
        if role not in by_name:
            return JSONResponse({"ok": False, "detail": "нет такой роли"}, status_code=404)
        ok, detail = _health(by_name[role])
        return JSONResponse({"ok": ok, "detail": detail}, status_code=200 if ok else 503)

    for role in roles:
        role.install(app)

    # точные маршруты — последними: чужие пути они не перехватывают
    for role in roles:
        for path, asgi in role.mounts():
            app.router.routes.append(Route(path, _Asgi(asgi)))
    return app
