"""Фикстуры серверного слоя: асинхронные тесты — через плагин anyio на asyncio."""
import logging
import socket
from types import SimpleNamespace

import pytest


@pytest.fixture
def listeners():
    """Два слушающих сокета на 127.0.0.1 — их порты (a, b); соединения принимает ядро, accept не нужен."""
    socks = [socket.socket(), socket.socket()]
    try:
        for s in socks:
            s.bind(("127.0.0.1", 0))
            s.listen(8)
        yield tuple(s.getsockname()[1] for s in socks)
    finally:
        for s in socks:
            s.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def restore_logging():
    """setup_logging меняет глобальные логгеры — после теста всё как было (caplog других тестов).

    С корня снимается только наш обработчик: свои обработчики pytest ставит и снимает на каждую фазу теста.
    """
    from pcbk_core.logs import _StderrHandler

    root, core = logging.getLogger(), logging.getLogger("pcbk_core")
    levels = {name: logging.getLogger(name).level for name in ("", "pcbk_core", "pytds", "mcp")}
    propagate, handlers, root_handlers = core.propagate, list(core.handlers), list(root.handlers)
    yield
    for h in [h for h in core.handlers if h not in handlers]:
        core.removeHandler(h)
    for h in [h for h in root.handlers if isinstance(h, _StderrHandler) and h not in root_handlers]:
        root.removeHandler(h)
    core.propagate = propagate
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)
    assert root.level == levels[""]


@pytest.fixture
def core_server(tmp_path):
    """Роль «данные» на двойнике историана (FakeHistorian, FakeMono) под uvicorn: url и role; каталог загружен."""
    from helpers import make_role, serve_role      # helpers тянет fastapi и uvicorn — только тем, кому нужно

    role, _ = make_role(db_dir=tmp_path)
    with serve_role(role) as url:
        yield SimpleNamespace(url=url, role=role)
