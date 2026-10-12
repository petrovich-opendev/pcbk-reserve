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

from helpers import LLM_ENV, SETTINGS, DummyRole, dummy, sha, write
from pcbk_core.app import create_app
from pcbk_core.llm.ledger import RunSpec, run_spec
from pcbk_core.logs import setup_logging
from pcbk_core.main import build_roles, healthcheck, main
from pcbk_core.secrets import BdrvConfig, KeyFileError, TokenTable, read_env_file, read_key_file
from pcbk_core.settings import LlmSettings, Settings


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


def test_token_table(tmp_path):                      # F15: рядом с BdrvConfig, write — оттуда же
    t = TokenTable.from_file(write(tmp_path, f"ops {sha('tok-b')}\nstudent-01 {sha('tok-a')}\n"))
    assert (t.caller("tok-a"), t.caller("tok-b"), t.caller("x"), t.caller(None)) == ("student-01", "ops", None, None)
    assert TokenTable.empty().ids == frozenset() and TokenTable.empty().caller("tok-a") is None
    with pytest.raises(ValueError):
        TokenTable.from_file(write(tmp_path, "Student_01 abc\n"))


def test_token_table_comments_and_duplicate_id(tmp_path):
    t = TokenTable.from_file(write(tmp_path, f"# ops — дежурный\n\n  ops {sha('tok')}  \n"))
    assert t.ids == frozenset({"ops"}) and t.caller("tok") == "ops"
    with pytest.raises(ValueError, match="строка 2 — id повторяется") as e:
        TokenTable.from_file(write(tmp_path, f"ops {sha('a')}\nops {sha('b')}\n"))
    assert sha("a") not in str(e.value) and sha("b") not in str(e.value)       # строк файла в ошибке нет


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


def test_drill_data_setting():                                                  # Д9-R83
    assert Settings.from_env({"DRILL_DATA": "tool_error"}).DRILL_DATA == "tool_error"
    assert Settings.from_env({}).DRILL_DATA == ""
    with pytest.raises(ValueError, match="DRILL_DATA"):
        Settings.from_env({"DRILL_DATA": "calc"})


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
            "print(sorted(m for m in ('fastapi', 'starlette', 'uvicorn', 'pydantic', 'httpx') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).parent.parent,
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_llm_settings_validation():
    assert LlmSettings.from_env({"LLM_MODELS": "z-ai/glm-5.3", "DRILL_LLM": "402"}).DRILL_LLM == "402"
    assert LlmSettings.from_env({}).LLM_MAX_BODY_BYTES == 1_048_576
    assert Settings.from_env({"LLM_MODELS": "openrouter/auto"})                # Settings Д3а поля LLM не читает
    for bad in ({"LLM_STATE": "off"}, {"DRILL_LLM": "403"}, {"LLM_MODELS": "openrouter/auto"},
                {"LLM_MAX_TOKENS": "32000"}, {"LLM_TOTAL_USD": "0"}, {"OPENROUTER_BASE_URL": "ftp://x"},
                {"LLM_MAX_BODY_BYTES": "8388608"}, {"LLM_CALLER_CONCURRENCY": "0"}, {"LLM_KEY_DEDICATED": "yes"}):
        with pytest.raises(ValueError):
            LlmSettings.from_env(bad)


def test_llm_settings_errors_name_the_field():                                   # сверх брифа: имя поля в ошибке
    assert LlmSettings.from_env({}) == LlmSettings()
    for name, raw in (("LLM_TOTAL_USD", "inf"), ("LLM_SHARE_USD", "-1"), ("LLM_OPS_SHARE_USD", "x"),
                      ("LLM_MAX_TOKENS", "0"), ("LLM_TURN_CALLS", "0"), ("LLM_POLL_S", "nan"),
                      ("LLM_MAX_BODY_BYTES", "1000"), ("LLM_CALLER_CONCURRENCY", "17"),
                      ("OPENROUTER_BASE_URL", "https://"), ("OPENROUTER_BASE_URL", "http://h:99999/api"),
                      ("LLM_MAX_TOKENS", "1.5"), ("LLM_TURN_CALLS", "101"), ("LLM_POLL_S", "4.9")):
        with pytest.raises(ValueError, match=name):
            LlmSettings.from_env({name: raw})
    assert LlmSettings.from_env({"LLM_SHARE_USD": "2000"}).LLM_SHARE_USD == 2000.0   # больше общего — можно
    assert (LlmSettings.from_env({"LLM_TURN_CALLS": "100"}).LLM_TURN_CALLS,              # предел шагов — в тексте
            LlmSettings.from_env({"LLM_POLL_S": "5"}).LLM_POLL_S) == (100, 5.0)          # отказа: не больше 100


@pytest.mark.parametrize("name,raw", [
    ("LLM_STATE", "zq-raw-off"), ("DRILL_LLM", "zq-raw-403"), ("LLM_KEY_DEDICATED", "zq-raw-yes"),
    ("LLM_MODELS", "zq-raw/model"), ("LLM_MODELS", "z-ai/glm-5.3,zq-raw/model"), ("LLM_TOTAL_USD", "zq-raw-usd"),
    ("LLM_MAX_TOKENS", "zq-raw-tok"), ("OPENROUTER_BASE_URL", "zq-raw://host"), ("LLM_POLL_S", "0.25")])
def test_llm_settings_error_names_variable_without_value(name, raw):             # ревью B: текст идёт на страницу
    with pytest.raises(ValueError) as e:
        LlmSettings.from_env({name: raw})
    assert str(e.value).startswith(name) and "zq-raw" not in str(e.value) and "0.25" not in str(e.value)


@pytest.mark.parametrize("env", [
    {"LLM_RUN_ID": "d8"},
    {"LLM_RUN_ID": "D8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "student-07"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "2000", "LLM_RUN_CALLERS": "student-07"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "student-7"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "student-07,student-07"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "student-11"},
])
def test_llm_run_settings_refused(env):                                           # Review Focus 1
    with pytest.raises(ValueError, match=r"^LLM_RUN_") as e:
        LlmSettings.from_env(env)
    assert "student-7" not in str(e.value) and "D8" not in str(e.value)


def test_llm_run_settings_ok():
    s = LlmSettings.from_env({"LLM_RUN_ID": "d8-1008", "LLM_RUN_USD": "80",
                              "LLM_RUN_CALLERS": "student-07, student-10"})
    assert run_spec(s) == RunSpec("d8-1008", 80.0, frozenset({"student-07", "student-10"}))
    assert run_spec(LlmSettings.from_env({})) is None


# сверх брифа: прочие опечатки и края LLM_RUN_* — текст с именем, без значения
@pytest.mark.parametrize("env", [
    {"LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "ops"},                               # без id
    {"LLM_RUN_ID": "d8", "LLM_RUN_CALLERS": "ops"},                                # без потолка
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "0", "LLM_RUN_CALLERS": "ops"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "nan", "LLM_RUN_CALLERS": "ops"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "zq-raw", "LLM_RUN_CALLERS": "ops"},
    {"LLM_RUN_ID": "zq_raw", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "ops"},
    {"LLM_RUN_ID": "a" * 33, "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "ops"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "ops,"},          # пустой элемент
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "student-00"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "Student-01"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "zq-raw"},
    {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10", "LLM_RUN_CALLERS": " , "},
])
def test_llm_run_settings_refused_more(env):
    with pytest.raises(ValueError, match=r"^LLM_RUN_") as e:
        LlmSettings.from_env(env)
    assert "zq" not in str(e.value) and "Student" not in str(e.value) and "student-00" not in str(e.value)


@pytest.mark.parametrize("env", [{"LLM_RUN_ID": "d8"}, {"LLM_RUN_USD": "10", "LLM_RUN_CALLERS": "ops"},
                                 {"LLM_RUN_ID": "d8", "LLM_RUN_USD": "10"}, {"LLM_RUN_CALLERS": "ops"}])
def test_llm_run_settings_all_or_none(env):                                        # своя причина, а не соседняя
    with pytest.raises(ValueError, match=r"^LLM_RUN_\*: нужны все три переменные или ни одной$"):
        LlmSettings.from_env(env)


def test_llm_run_settings_edges():
    full = {"LLM_RUN_ID": "a" * 32, "LLM_RUN_USD": "1000", "LLM_RUN_CALLERS": " ops ,student-01,student-10 "}
    assert run_spec(LlmSettings.from_env(full)) == RunSpec("a" * 32, 1000.0,
                                                            frozenset({"ops", "student-01", "student-10"}))
    with pytest.raises(ValueError, match=r"^LLM_RUN_USD"):                          # потолок — не выше бюджета
        LlmSettings.from_env({**full, "LLM_TOTAL_USD": "500"})
    assert LlmSettings.from_env({**full, "LLM_TOTAL_USD": "500", "LLM_RUN_USD": "500"}).LLM_RUN_USD == 500.0


def test_build_roles_llm_run_typo_is_config_error(tmp_path):                       # опечатка — 403 всем
    from pcbk_core.main import build_llm_role
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"))
    role = build_llm_role(settings, {**LLM_ENV, "LLM_RUN_ID": "d8", "LLM_RUN_USD": "10",
                                     "LLM_RUN_CALLERS": "student-07,student-7"})
    assert role.config_error.startswith("настройки: LLM_RUN_CALLERS") and role.run is None


# Д14: запасная модель — разбор LLM_FALLBACK (settings.py), имена — кандидаты models.py (их тестирует test_llm_fallback.py)
@pytest.mark.parametrize("env", [
    {"LLM_FALLBACK": "zq-raw/model=z-ai/glm-5.3-flash"},                  # основная неизвестна
    {"LLM_FALLBACK": "z-ai/glm-5.3=zq-raw/model"},                        # запасная неизвестна
    {"LLM_FALLBACK": "z-ai/glm-5.3=z-ai/glm-5.3"},                        # совпадают
    {"LLM_FALLBACK": "z-ai/glm-5.3=z-ai/glm-5.3-flash,z-ai/glm-5.3=openai/gpt-6-sol"},   # основная повторена
    {"LLM_FALLBACK": "z-ai/glm-5.3"},                                     # без «=»
    {"LLM_MODELS": "z-ai/glm-5.3,z-ai/glm-5.3-flash", "LLM_FALLBACK": "z-ai/glm-5.3=openai/gpt-6-luna"},
])
def test_llm_fallback_settings_refused(env):
    with pytest.raises(ValueError, match=r"^LLM_FALLBACK:") as e:
        LlmSettings.from_env(env)
    assert "zq-raw" not in str(e.value)


def test_llm_fallback_settings_ok():
    assert LlmSettings.from_env({}).LLM_FALLBACK == ""
    s = LlmSettings.from_env({"LLM_FALLBACK": "z-ai/glm-5.3=z-ai/glm-5.3-flash"})
    assert s.LLM_FALLBACK == "z-ai/glm-5.3=z-ai/glm-5.3-flash"


def test_build_roles_llm_fallback_typo_is_config_error(tmp_path):                 # опечатка — 403 всем, как LLM_RUN_*
    from pcbk_core.main import build_llm_role
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"))
    role = build_llm_role(settings, {**LLM_ENV, "LLM_FALLBACK": "z-ai/glm-5.3=z-ai/glm-5.3"})
    assert role.config_error.startswith("настройки: LLM_FALLBACK") and role.fallback == {}
    assert "student-7" not in role.config_error


def test_read_key_file(tmp_path):
    assert read_key_file(write(tmp_path, "  sk-test-key\n")) == "sk-test-key"
    assert read_key_file(write(tmp_path, "\n  \n")) is None and read_key_file(str(tmp_path / "нет")) is None
    with pytest.raises(KeyFileError) as e:
        read_key_file(str(tmp_path))                                            # каталог вместо файла
    assert str(tmp_path) in str(e.value)
    p = tmp_path / "bad.key"
    p.write_bytes(b"sk-\xffsecret-tail\n")
    with pytest.raises(KeyFileError) as e:
        read_key_file(str(p))
    assert "secret" not in str(e.value) and "xff" not in str(e.value) and e.value.__cause__ is None


@pytest.mark.parametrize("env,broken_tokens,health", [
    ({"LLM_MODELS": "openrouter/auto"}, False, "ошибка настройки: настройки: LLM_MODELS"),
    ({}, True, "токены LLM не читаются")])                                        # П23: не config_error
def test_build_roles_llm_config_error_keeps_process(tmp_path, env, broken_tokens, health):   # раунд 2, П6, П25
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"), BDRV_ENV_FILE=str(tmp_path / "нет.env"),
                       WHITELIST_PATH=write(tmp_path, "20FAKE_001_PV\n"))
    tokens = tmp_path / "llm-tokens"
    tokens.write_text("student-01 не-хеш\n" if broken_tokens else "")
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "LLM_TOKENS_FILE": str(tokens), **env})}   # не бросает
    assert set(roles) == {"data", "llm", "gateway"} and roles["llm"].health()[1].startswith(health)
    assert (roles["llm"].config_error is None) is broken_tokens
    data = roles["data"].health()[1]                                             # П25: настоящая причина роли data
    assert data == "bdrv.env не читается (нет файла или прав)" and "LLM" not in data and "OpenRouter" not in data


def test_build_roles_llm_key_problems_are_config_errors(tmp_path, capfd, restore_logging):   # сверх брифа
    setup_logging()
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"))
    key = tmp_path / "openrouter.key"
    key.write_text("sk-ключ-не-ascii\n", encoding="utf-8")
    roles = {r.name: r for r in build_roles(settings, {**LLM_ENV, "OPENROUTER_KEY_FILE": str(key)})}
    assert roles["llm"].upstream is None and "не печатные знаки ASCII" in roles["llm"].config_error
    unreadable = {r.name: r for r in build_roles(settings, {**LLM_ENV, "OPENROUTER_KEY_FILE": str(tmp_path)})}
    assert "ключ OpenRouter не читается" in unreadable["llm"].config_error
    key.write_text("test-openrouter-key\n", encoding="utf-8")
    good = {r.name: r for r in build_roles(settings, {**LLM_ENV, "OPENROUTER_KEY_FILE": str(key)})}["llm"]
    assert good.config_error is None and good.upstream is not None and len(good.key_fingerprint) == 12
    assert (good.mgmt_key, good.upstream.has_mgmt_key) == ("missing", False)
    bad_env = {r.name: r for r in build_roles(settings, {**LLM_ENV, "LLM_MODELS": "x/y"})}["llm"]
    assert bad_env.tokens_error is None and bad_env.llm.LLM_TOKENS_FILE == LLM_ENV["LLM_TOKENS_FILE"]   # не /run/secrets
    err = capfd.readouterr().err
    assert "ключ-не-ascii" not in err and "test-openrouter-key" not in err


def test_bad_management_key_disables_only_credits(tmp_path, capfd, restore_logging):   # ревью B, Д4а-R16
    setup_logging()
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"))
    key, mgmt = tmp_path / "openrouter.key", tmp_path / "openrouter-mgmt.key"
    key.write_text("test-openrouter-key\n", encoding="utf-8")
    mgmt.write_text("sk-управление-не-ascii\n", encoding="utf-8")
    env = {**LLM_ENV, "OPENROUTER_KEY_FILE": str(key)}
    for mgmt_file in (str(tmp_path), str(mgmt)):                                   # каталог вместо файла; не ASCII
        llm = {r.name: r for r in build_roles(settings, {**env, "OPENROUTER_MGMT_KEY_FILE": mgmt_file})}["llm"]
        assert llm.config_error is None and llm.upstream is not None and not llm.upstream.has_mgmt_key
        assert llm.mgmt_key == "error" and llm.health_json(llm.wallclock())["mgmt_key"] == "error"
    err = capfd.readouterr().err
    assert err.count("WARNING pcbk_core.main: LLM-прокси: ключ управления OpenRouter") == 2
    assert "управление-не-ascii" not in err


def test_build_roles_share_agent_stats(monkeypatch, tmp_path):                  # Д7-R53, задача А1 Д7а
    data, _, gw = build_roles(SETTINGS, env={})
    assert data.agent_stats is gw.agent_stats


# --- Д10а, задача А2: доли мест — одни у LLM-прокси и шлюза (Д10-R98) ---

def test_build_roles_share_shares_with_gateway(tmp_path):
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    roles = {r.name: r for r in build_roles(settings, LLM_ENV)}
    assert roles["gateway"].llm is roles["llm"] and roles["llm"].budget.shares is roles["llm"].shares
    assert roles["llm"].shares.settled_of == roles["llm"].ledger.settled


def test_build_llm_role_shares_from_settings(tmp_path):                   # сверх брифа: бюджет, доля, учение
    from pcbk_core.main import build_llm_role
    settings = Settings.from_env({"DB_PATH": str(tmp_path / "core.db")})
    role = build_llm_role(settings, {**LLM_ENV, "LLM_TOTAL_USD": "300", "LLM_SHARE_USD": "25.5"},
                          shares_drill="shares_stale")
    s = role.shares
    assert (s.path, s.total_cents, s.default_cents, s.drill, s.cents(7)) == \
           (str(tmp_path / "core.db"), 30000, 2550, "shares_stale", 2550)
    assert s.loaded is False and not (tmp_path / "core.db").exists()           # конструкторы ничего не открывают
    assert role.config_error is None
    closed = build_llm_role(settings, LLM_ENV, drill_error=True)
    assert closed.config_error == "учения DRILL_TEACHER с ошибкой — доли закрыты" and closed.shares.drill == ""
