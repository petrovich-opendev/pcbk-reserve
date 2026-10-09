"""Фикстуры серверного слоя: асинхронные тесты — через плагин anyio на asyncio."""
import json
import logging
import socket
import threading
import urllib.request
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
    from pcbk_core.logs import QUIET_LOGGERS, _StderrHandler

    root, core = logging.getLogger(), logging.getLogger("pcbk_core")
    levels = {name: logging.getLogger(name).level for name in ("", "pcbk_core", *QUIET_LOGGERS)}
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
def gwlog(capfd, restore_logging):
    """Строки журнала процесса (F13): setup_logging() и накопительный .text из capfd.readouterr().err.

    У корня — WARNING, и строки INFO до caplog не доходят; capfd видит и поток uvicorn и потоки to_thread.
    """
    from pcbk_core.logs import setup_logging

    setup_logging()
    return _Log(capfd)


class _Log:
    def __init__(self, capfd):
        self._capfd = capfd
        self._seen: list[str] = []

    @property
    def text(self) -> str:
        self._seen.append(self._capfd.readouterr().err)
        return "".join(self._seen)


class FakeOpenRouter:
    """Клиент ручек двойника OpenRouter (как у стенда — по HTTP, мимо прокси окружения): url — …/api/v1."""

    def __init__(self, server):
        host, port = server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.url = self.base + "/api/v1"
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _call(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data is not None else {})
        with self._opener.open(req, timeout=5) as r:
            raw = r.read()
        return json.loads(raw) if raw else None         # 204 без тела (/_bench, П10)

    @property
    def requests(self) -> list[dict]:
        """Записанные запросы чата {"auth_ok", "body"} по порядку."""
        return self._call("GET", "/_requests")

    @property
    def aborted(self) -> int:
        """Сколько потоков оборвал клиент."""
        return self._call("GET", "/_stats")["aborted"]

    def add_usage(self, x: float, key: str = "a") -> None:
        """«OpenRouter насчитал больше»: + x к usage ключа a (test-openrouter-key) или b."""
        self._call("POST", "/_usage", {"add": x, "key": key})

    def set_price(self, model: str, prompt: float, completion: float) -> None:
        """Нынешняя цена точки модели, $ за 1 млн: выше provider.max_price запроса — 404 max_price."""
        self._call("POST", "/_price", {"model": model, "prompt": prompt, "completion": completion})

    def bench_on(self, personas: dict) -> None:
        """Персоны стенда отбора (fake_bench.PERSONAS — модель → персона): чат этих моделей отвечает персоной."""
        self._call("POST", "/_bench", {"personas": dict(personas)})

    def bench_off(self) -> None:
        """Персоны выключены: чат — снова по словам FAKE…."""
        self._call("DELETE", "/_bench")


@pytest.fixture
def fake_or():
    """Двойник OpenRouter на 127.0.0.1:0 в своём потоке — на каждый тест свой."""
    from fake_openrouter import make_server

    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              name="fake-openrouter", daemon=True)
    thread.start()
    try:
        yield FakeOpenRouter(server)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def core_server(tmp_path):
    """Роль «данные» на двойнике историана (FakeHistorian, FakeMono) под uvicorn: url и role; каталог загружен."""
    from helpers import make_role, serve_role      # helpers тянет fastapi и uvicorn — только тем, кому нужно

    role, _ = make_role(db_dir=tmp_path)
    with serve_role(role) as url:
        yield SimpleNamespace(url=url, role=role)
