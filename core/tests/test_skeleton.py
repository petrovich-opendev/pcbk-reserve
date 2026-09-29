"""Каркас серверного слоя: настройки, учётные данные файлом, роли, здоровье, журнал, зависимости."""
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helpers import SETTINGS, DummyRole, write
from pcbk_core.app import create_app
from pcbk_core.logs import setup_logging
from pcbk_core.main import build_roles
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


def test_build_roles_empty_until_data_role():
    assert build_roles(SETTINGS) == []


def test_logging_info_reaches_stderr_once(capfd):
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


def test_mcp_1x_api_importable():                 # несовпадение API всплывает в первый час
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.client.streamable_http import streamablehttp_client
    assert FastMCP and TransportSecuritySettings and streamablehttp_client
