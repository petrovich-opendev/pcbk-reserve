"""Каркас серверного слоя: настройки, учётные данные файлом, роли, здоровье, журнал, зависимости."""
import logging
import re
import socket
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from helpers import SETTINGS, DummyRole, dummy, write
from pcbk_core.app import create_app
from pcbk_core.logs import setup_logging
from pcbk_core.main import healthcheck, main
from pcbk_core.secrets import BdrvConfig, read_env_file
from pcbk_core.settings import Settings


def test_read_env_file_strips_quotes_and_export(tmp_path):
    p = write(tmp_path, "BDRV_HOST=h\nBDRV_PW=\"<p w>\"\n# к\n\nexport BDRV_USER='u'\n")
    assert read_env_file(p) == {"BDRV_HOST": "h", "BDRV_PW": "<p w>", "BDRV_USER": "u"}


def test_read_env_file_error_hides_line(tmp_path):
    with pytest.raises(ValueError) as e:
        read_env_file(write(tmp_path, "BDRV_HOST=h\nсекретная-строка\n"))
    assert "строка 2" in str(e.value) and "секрет" not in str(e.value)


def test_bdrv_config_defaults_and_hidden_password(tmp_path):
    cfg = BdrvConfig.from_env_file(write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<s3cr3t>\n"))
    assert (cfg.port, cfg.database) == (1433, "Runtime") and "s3cr3t" not in repr(cfg)
    inst = BdrvConfig.from_env_file(write(tmp_path, "BDRV_HOST=h\\INST\nBDRV_USER=u\nBDRV_PW=<p>\n"))
    assert inst.port is None                       # экземпляр: порт находит pytds
    with pytest.raises(ValueError, match="BDRV_PW"):
        BdrvConfig.from_env_file(write(tmp_path, "BDRV_HOST=h\nBDRV_USER=u\n"))


def test_bdrv_config_explicit_port_and_database(tmp_path):
    cfg = BdrvConfig.from_env_file(write(
        tmp_path, "BDRV_HOST=h\\INST\nBDRV_PORT=1500\nBDRV_DB=Hist\nBDRV_USER=u\nBDRV_PW=<p>\n"))
    assert (cfg.host, cfg.port, cfg.database, cfg.user) == ("h\\INST", 1500, "Hist", "u")
    with pytest.raises(ValueError, match="BDRV_PORT"):
        BdrvConfig.from_env_file(write(tmp_path, "BDRV_HOST=h\nBDRV_PORT=x\nBDRV_USER=u\nBDRV_PW=<p>\n"))


def test_settings_from_env():
    assert Settings.from_env({"FRESH_POLL_S": "5", "DRILL_FRESHNESS": ""}).FRESH_POLL_S == 5.0
    for bad in ({"FRESH_POLL_S": "0"}, {"DRILL_FRESHNESS": "freeze"}, {"CATALOG_DEADLINE_S": "5"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)


def test_settings_errors_name_the_field():
    assert Settings.from_env({}) == Settings()
    for name, raw in (("CORE_PORT", "x"), ("CATALOG_REFRESH_S", "-1"), ("FRESH_POLL_S", "inf"),
                      ("CATALOG_DEADLINE_S", "9.9"), ("DRILL_FRESHNESS", "freeze")):
        with pytest.raises(ValueError, match=name):
            Settings.from_env({name: raw})


def test_install_hook_exact_mounts_and_no_docs():                     # Review Focus 4
    with TestClient(create_app(SETTINGS, [DummyRole("data", (True, "ок"), installs=True)])) as c:
        assert c.get("/healthz").headers["X-Role-Installed"] == "1"
        assert c.post("/dummy").status_code == 200 and c.get("/dummy").status_code == 200   # функция, не только GET
        assert c.post("/dummy/").status_code == 404 and c.get("/dummy/x").status_code == 404
        assert c.get("/openapi.json").status_code == 404 and c.get("/docs").status_code == 404


class CatchAllRole(DummyRole):
    """Роль, которая ставит ловящий поддерево маршрут в install или в router()."""

    def __init__(self, name, where, directory=None):
        super().__init__(name, (True, "ок"))
        self.where, self.directory = where, directory

    def router(self):
        r = APIRouter()
        if self.where == "router":
            r.mount("/sub", dummy)                 # Mount("/") в роутере FastAPI не пускает сам
        return r

    def install(self, app):
        if self.where == "install":
            app.mount("/", dummy)
        elif self.where == "frontend":
            app.frontend("/", directory=self.directory)


@pytest.mark.parametrize("where, kind", [("install", "Mount"), ("router", "Mount"), ("frontend", "frontend")])
def test_catch_all_from_role_is_rejected(where, kind, tmp_path):   # Mount("/") забрал бы чужие пути
    with pytest.raises(ValueError, match=kind):
        create_app(SETTINGS, [CatchAllRole("data", where, tmp_path)])


class PathsRole(DummyRole):
    def __init__(self, name, paths):
        super().__init__(name, (True, "ок"))
        self.paths = paths

    def mounts(self):
        return [(p, dummy) for p in self.paths]


class RoutedRole(DummyRole):
    """Роль с роутером под префиксом: его пути видны только через iter_route_contexts."""

    def router(self):
        r = APIRouter(prefix="/api/data")
        r.add_api_route("/tag_now", lambda: {}, methods=["POST"])
        return r


@pytest.mark.parametrize("roles", [
    [PathsRole("data", ["/mcp"]), PathsRole("llm", ["/mcp"])],     # дубль между ролями
    [RoutedRole("data", (True, "")), PathsRole("llm", ["/api/data/tag_now"])],   # путь роутера роли
    [PathsRole("data", ["/x", "/x"])],                             # дубль в одной роли
    [PathsRole("data", ["/healthz"])],                             # занят ручкой здоровья
    [PathsRole("data", ["/healthz/llm"])],                         # ловит /healthz/{role}
])
def test_taken_or_duplicate_mount_path_is_rejected(roles):
    with pytest.raises(ValueError, match="занят"):
        create_app(SETTINGS, roles)


def test_mount_path_with_parameters_is_rejected():
    with pytest.raises(ValueError, match="без параметров"):
        create_app(SETTINGS, [PathsRole("data", ["/mcp/{x}"])])


def test_healthz_and_role_health():
    ok, bad = DummyRole("data", (True, "ок")), DummyRole("llm", (False, "нет ключа"))
    with TestClient(create_app(SETTINGS, [ok, bad])) as c:
        assert c.get("/healthz").status_code == 503
        assert c.get("/healthz/data").json() == {"ok": True, "detail": "ок"}
        r = c.get("/healthz/llm")
        assert (r.status_code, r.json()["detail"]) == (503, "нет ключа")
        assert c.get("/healthz/gateway").status_code == 404


def test_healthz_all_ok_lists_roles():
    with TestClient(create_app(SETTINGS, [DummyRole("data", (True, "ок"))])) as c:
        r = c.get("/healthz")
        assert (r.status_code, r.json()) == (200, {"ok": True, "roles": {"data": "ок"}})


def test_lifespan_enters_roles_in_order_and_exits_in_reverse():
    events = []

    class Tracked(DummyRole):
        def lifespan(self):
            @asynccontextmanager
            async def cycle():
                events.append(f"+{self.name}")
                yield
                events.append(f"-{self.name}")
            return cycle()

    with TestClient(create_app(SETTINGS, [Tracked("data", (True, "")), Tracked("llm", (True, ""))])):
        assert events == ["+data", "+llm"]
    assert events == ["+data", "+llm", "-llm", "-data"]


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


def test_logging_info_reaches_stderr_once(capfd, restore_logging):
    logging.getLogger().setLevel(logging.WARNING)
    setup_logging()
    setup_logging().info("pcbk-marker")
    assert capfd.readouterr().err.count("pcbk-marker") == 1
    assert not logging.getLogger("pytds").isEnabledFor(logging.INFO)   # адрес и SQL — не в журнал


def test_lock_pins_python_tds_and_mcp_1x():
    text = Path("requirements.lock").read_text()
    assert re.search(r"^python-tds==1\.17\.1\b", text, re.M) and re.search(r"^mcp==1\.30\.0\b", text, re.M)
    reqs = [l for l in text.splitlines() if re.match(r"^[A-Za-z0-9]", l)]
    assert reqs and all("==" in l for l in reqs) and text.count("--hash=sha256:") >= len(reqs)
    # у каждого пакета свой хеш: блок пакета — от строки name== до следующей
    blocks = [b for b in re.split(r"(?m)^(?=[A-Za-z0-9])", text) if re.match(r"[A-Za-z0-9]", b)]
    assert len(blocks) == len(reqs)
    assert [b.split()[0] for b in blocks if "--hash=sha256:" not in b] == []


def test_mcp_1x_api_importable():                 # несовпадение API всплывает в первый час
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.client.streamable_http import streamablehttp_client
    assert FastMCP and TransportSecuritySettings and streamablehttp_client


def _serve_status(code: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("code, rc", [(200, 0), (503, 1), (404, 1)])
def test_healthcheck_non_200_is_1(code, rc):
    server = _serve_status(code)
    try:
        assert healthcheck(replace(SETTINGS, CORE_PORT=server.server_address[1])) == rc
    finally:
        server.shutdown()
        server.server_close()


def test_healthcheck_no_connection_is_1():
    with socket.socket() as s:                       # порт свободен: слушателя на нём нет
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert healthcheck(replace(SETTINGS, CORE_PORT=port)) == 1


def test_main_bad_args_or_settings_exit_1(monkeypatch, capsys):   # код 2 Docker резервирует
    monkeypatch.delenv("CORE_PORT", raising=False)
    assert main(["--bogus"]) == 1 and main(["--healthcheck", "x"]) == 1
    monkeypatch.setenv("CORE_PORT", "x")
    assert main(["--healthcheck"]) == 1 and main([]) == 1
    assert "CORE_PORT" in capsys.readouterr().err


def test_healthcheck_probe_does_not_load_server_stack():
    code = ("import sys, pcbk_core.main; "
            "print(sorted(m for m in ('fastapi', 'starlette', 'uvicorn', 'pydantic') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent.parent,
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
