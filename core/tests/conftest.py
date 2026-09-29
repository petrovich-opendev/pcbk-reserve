"""Фикстуры серверного слоя: асинхронные тесты — через плагин anyio на asyncio."""
import logging

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def restore_logging():
    """setup_logging меняет глобальные логгеры — после теста всё как было (caplog других тестов)."""
    root, core = logging.getLogger(), logging.getLogger("pcbk_core")
    levels = {name: logging.getLogger(name).level for name in ("", "pcbk_core", "pytds", "mcp")}
    propagate, handlers = core.propagate, list(core.handlers)
    yield
    for h in [h for h in core.handlers if h not in handlers]:
        core.removeHandler(h)
    core.propagate = propagate
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)
    assert root.level == levels[""]
