"""Роль «kb» (Д25): поправки технологов к базе знаний V1/V2 — MCP /mcp-kb (mcp_server.py), хранилище kb_corrections в
core.db (store.py), команда оператора `python -m pcbk_core.kb` (__main__.py).

Отдельная роль, а не инструменты /mcp службы данных: файлы службы данных защищены пакетом КДМ-1. Токены мест — та
же таблица core-tokens, что у службы данных (build_roles передаёт её объект). Здоровье без ввода-вывода: хранилище
не открылось — красная строка с классом ошибки; инструменты при этом отвечают «недоступно», процесс жив.
"""
import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import APIRouter
from starlette.types import ASGIApp

from ..app import RoleBase
from ..secrets import TokenTable
from .mcp_server import KB_MCP_PATH, build_kb_mcp, kb_auth, now_utc
from .store import CorrectionStore

log = logging.getLogger("pcbk_core.kb")
KB_ERROR = "поправки базы знаний: хранилище не открыто — "


class KbRole(RoleBase):
    name = "kb"

    def __init__(self, db_path: str, tokens: TokenTable, *, wallclock: Callable[[], datetime] = now_utc):
        self.tokens = tokens
        self.wallclock = wallclock
        self.store = CorrectionStore(db_path)
        self.opened: bool | None = None
        self.mcp = build_kb_mcp(self)
        self.mcp_app = kb_auth(self.mcp.streamable_http_app(), self)

    def router(self) -> APIRouter:
        return APIRouter()

    def mounts(self) -> list[tuple[str, ASGIApp]]:
        """Точный маршрут /mcp-kb; /mcp-kb/ — 404, как у /mcp."""
        return [(KB_MCP_PATH, self.mcp_app)]

    def health(self) -> tuple[bool, str]:
        if self.opened is False:
            return False, KB_ERROR + (self.store.last_error or "Error")
        return True, "поправки базы знаний: хранилище открыто" if self.opened else "поправки базы знаний: открывается"

    def lifespan(self):
        return self._lifespan()

    @asynccontextmanager
    async def _lifespan(self):
        # run() SDK — один раз за жизнь роли; без него /mcp-kb отвечает 500 «Task group is not initialized»
        async with self.mcp.session_manager.run():
            self.opened = await asyncio.to_thread(self.store.open)
            if not self.opened:
                log.error("поправки базы знаний: не открыты (%s)", self.store.last_error)
            yield
