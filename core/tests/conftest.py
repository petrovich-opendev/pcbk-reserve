"""Фикстуры серверного слоя: асинхронные тесты — через плагин anyio на asyncio."""
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
