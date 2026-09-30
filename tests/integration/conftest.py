"""Стенд для проверок компоновки: проект pcbk-test на локальном Docker, порт 127.0.0.1:18443.

Образы — только через deploy/images.lock; тестовый TLS, секреты и агенты мест, данные
серверного слоя, ключи и токены LLM-прокси (заглушки — настоящего OpenRouter нет, есть
двойник), test.env — во временном каталоге; в конце — `down -v` и тома мест по
метке, даже при сбое. Чужие контейнеры, сети и тома мест с нашими именами тест не
трогает — отказывается запускаться.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cdp import Browser
from gwclient import GwClient
from workplace import IMAGE as STUDENT_IMAGE
from workplace import ONESHOT_LABEL

REPO = Path(__file__).resolve().parents[2]
PROJECT = "pcbk-test"
HOST, PORT = "127.0.0.1", 18443
LOCK = REPO / "deploy" / "images.lock"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
READY_TIMEOUT_S = 60
COMPOSE_TIMEOUT_S = 600
COMPOSE_FILES = ("compose.yaml", "compose.test.yaml")
# профиль мест: без него compose не видит student-NN
PROFILE = "students"
STUDENT_PREFIX = "pcbk-student-"
PLACES = range(1, 11)
# метка томов мест, созданных тестом: уборка удаляет только их
TEST_VOLUME_LABEL = "pcbk-test.volume"
# переменные компоновки: берутся только из test.env, не из окружения оболочки
STACK_VARS = frozenset({"DOCKER_GID", "STU_NET", "TLS_DIR", "DISPLAY_TZ", "SECRETS_DIR", "AGENTS_DIR",
                        "MEM_WARN_MIB", "MEM_FAIL_MIB", "TICK_S", "STALE_AFTER_S", "DOCKER_TIMEOUT_S",
                        "DRILL_FREEZE_LOOP", "DATA_DIR", "FRESH_POLL_S", "CATALOG_DEADLINE_S",
                        "DRILL_FRESHNESS", "HIST_WARN_S", "HIST_FAIL_S", "HIST_STALE_S",
                        "LLM_MODELS", "LLM_STATE", "DRILL_LLM", "LLM_OPS_SHARE_USD", "LLM_KEY_DEDICATED",
                        "LLM_TOTAL_USD",
                        # шлюз (Д5а): core и срок молчания уборщика у сторожа
                        "GW_ORIGIN", "GW_IDLE_STOP_S", "GW_REAPER_TICK_S", "DRILL_GATEWAY", "GW_STALE_S",
                        # статика edge тестового стенда со сборкой чата (Д6а): compose.test.yaml, «:?»
                        "PCBK_TEST_STATIC"})
# учётные данные историана для стенда: TEST-NET-1 (RFC 5737) недостижим, пароль — заглушка
TEST_BDRV_ENV = ("BDRV_HOST=192.0.2.10", "BDRV_PORT=1433", "BDRV_USER=test", "BDRV_PW=<test-pw>")
# белый список стенда — одно синтетическое имя
TEST_WHITELIST = ("20FAKE_001_PV",)
# токен службы данных ops (Д3б): не 48 шестнадцатеричных знаков — шаблон проверки на секреты его не ловит;
# токены мест — student-NN.data-token из make_test_places; в core-tokens — только их sha256
TEST_OPS_DATA_TOKEN = "test-data-token-ops"
# LLM-прокси: ключи — те, что знает двойник OpenRouter (core/tests/fake_openrouter.py), токен ops —
# не 48 шестнадцатеричных знаков; токены мест — student-NN.llm-token из make_test_places
TEST_OPENROUTER_KEY = "test-openrouter-key"
TEST_MGMT_KEY = "test-mgmt-key"
TEST_OPS_LLM_TOKEN = "test-llm-token-ops"
# адреса с хоста теста: серверный слой и двойник OpenRouter в pcbk-egress
CORE_URL = "http://172.31.250.82:8000"
FAKE_OPENROUTER_URL = "http://172.31.250.84:8080"
# строки журнала вызовов изнутри pcbk-core: столбцы llm_calls, содержимого разговоров в таблице нет
LLM_CALLS_PY = ("import json, sqlite3, sys; "
                "c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True); "
                "c.row_factory = sqlite3.Row; "
                "print(json.dumps([dict(r) for r in c.execute("
                "'SELECT * FROM llm_calls ORDER BY id DESC LIMIT ?', (int(sys.argv[1]),))]))")
# отказы прокси одной сессии в журнале вызовов: argv — сессия и code. Повтор отказа OpenCode шлёт в прокси, а
# прокси снова отказывает и в двойник его не пересылает — шторм повторов видно только здесь
LLM_REFUSALS_PY = ("import sqlite3, sys; "
                   "c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True); "
                   "print(c.execute('SELECT COUNT(*) FROM llm_calls WHERE session = ? AND code = ?', "
                   "(sys.argv[1], sys.argv[2])).fetchone()[0])")
# события службы данных изнутри pcbk-core: только четыре столбца agent_events, новые сверху; argv — сколько и
# канал (пусто — любой). Канал отсеивает события system опроса свежести и каталога между вызовами
DATA_EVENTS_PY = ("import json, sqlite3, sys; "
                  "c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True); "
                  "ch = sys.argv[2] or None; "
                  "q = 'SELECT caller, channel, tool, outcome FROM agent_events' "
                  "+ (' WHERE channel = ?' if ch else '') + ' ORDER BY rowid DESC LIMIT ?'; "
                  "print(json.dumps([dict(zip(('caller', 'channel', 'tool', 'outcome'), r)) "
                  "for r in c.execute(q, ((ch,) if ch else ()) + (int(sys.argv[1]),))]))")
# число событий вызывающего в канале: argv — вызывающий и канал
DATA_EVENT_COUNT_PY = ("import sqlite3, sys; "
                       "c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True); "
                       "print(c.execute('SELECT COUNT(*) FROM agent_events WHERE caller = ? AND channel = ?', "
                       "(sys.argv[1], sys.argv[2])).fetchone()[0])")
# разговор через API OpenCode места (Bun из образа места): converse.js рядом с этим файлом
CONVERSE_JS = REPO / "tests" / "integration" / "converse.js"
# худший случай converse.js сверх timeout_s — из его сроков (каждый запрос — до CONVERSE_REQUEST_S = 10 с,
# REQUEST_TIMEOUT_MS там): до цикла POST /session и prompt_async (2 запроса); такт, начатый перед сроком, —
# шесть запросов (status, message, question, reject, permission, reply; по одному снятому вопросу и разрешению
# за такт) и пауза 1 с; abort (1 запрос); ожидание ухода из статуса — до 10 с, плюс запрос, начатый перед его
# концом, и пауза 0,5 с; чтение сообщений (1 запрос). Итого 11 запросов и 11,5 с; ещё 30 с — docker run,
# запуск Bun и --rm
CONVERSE_REQUEST_S = 10
CONVERSE_WORST_EXTRA_S = 11 * CONVERSE_REQUEST_S + 1 + 10 + 0.5 + 30
# запрос изнутри сторожа: argv — метод и адрес; печатает код ответа
WATCHDOG_REQUEST = """\
import http.client, sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[2])
conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=10)
conn.request(sys.argv[1], u.path + ("?" + u.query if u.query else ""))
print(conn.getresponse().status)
"""
# то же, но GET и тело ответа; не 200 — ненулевой код
WATCHDOG_BODY = """\
import http.client, sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[1])
conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=10)
conn.request("GET", u.path + ("?" + u.query if u.query else ""))
r = conn.getresponse()
body = r.read()
if r.status != 200:
    sys.exit(f"HTTP {r.status}")
sys.stdout.write(body.decode("utf-8"))
"""
# запрос изнутри pcbk-core (Д5-R31: клиент sp-ctl — только настоящий core): argv — метод и адрес, путь как есть;
# печатает код ответа. Срок 20 с — stop места ждёт stop_grace_period (10 с)
CORE_REQUEST = """\
import http.client, sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[2])
conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=20)
conn.request(sys.argv[1], u.path + ("?" + u.query if u.query else ""))
print(conn.getresponse().status)
"""
# запрос изнутри pcbk-watchdog к core в pcbk-front мимо edge: argv — метод, путь как есть, заголовки JSON;
# печатает код ответа
FRONT_REQUEST = """\
import http.client, json, sys
conn = http.client.HTTPConnection("pcbk-core", 8000, timeout=20)
conn.putrequest(sys.argv[1], sys.argv[2], skip_accept_encoding=True)
for name, value in json.loads(sys.argv[3]).items():
    conn.putheader(name, value)
conn.endheaders()
r = conn.getresponse()
r.read()
print(r.status)
"""
# публичный origin входа тестового стенда: имя из трёх меток, чтобы удаления cookie с Domain= шли через
# настоящий edge (Д5-R25, Д5-R30); имя нигде не разрешается — клиент ходит на 127.0.0.1:18443 с этим Host
STAND_HOST = "stand.pcbk.test:18443"
STAND_ORIGIN = f"https://{STAND_HOST}"
# D1: соединение есть, если curl -v его сообщил
CONNECTED = re.compile(r"Established connection|Connected to")


def wait_for(pred: Callable[[], bool], timeout: float, step: float = 1.0) -> None:
    """Ждать, пока pred() не станет истинным, опрос раз в step с; по сроку — AssertionError.

    Исключение из pred() не ловится и не повторяется — оно сразу выходит к тесту (сбой сети или
    неверный ответ — это ошибка, а не «ещё не готово»). Ждать того, что может временно не отвечать, —
    ловить в самом pred().
    """
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError(f"за {timeout} с условие не выполнилось")
        time.sleep(step)


def secret_forms(pw: str) -> list[str]:
    """Виды пароля места, которых не должно быть в журналах: сырой, base64("opencode:"+пароль) и Basic."""
    b64 = base64.b64encode(f"opencode:{pw}".encode("utf-8")).decode("ascii")
    return [pw, b64, "Basic " + b64]


def _run(cmd: list[str], *, timeout: float = 120, check: bool = True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}…: код {r.returncode}\n{r.stderr[-3000:]}")
    return r


def read_lock(path: Path = LOCK) -> list[tuple[str, str, bool]]:
    """Строки `имя:тег sha256:<дайджест> [# test]` → (имя:тег, дайджест, тестовый ли)."""
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line, _, comment = raw.partition("#")
        fields = line.split()
        if not fields:
            continue
        tag = fields[0].rsplit("/", 1)[-1].partition(":")[2]
        if len(fields) != 2 or not DIGEST.fullmatch(fields[1]) or tag in ("", "latest"):
            raise ValueError(f"images.lock: неверная строка {raw!r}")
        entries.append((fields[0], fields[1], comment.strip() == "test"))
    return entries


def prepare_images() -> None:
    """Каждый образ — по дайджесту из images.lock; тег ставится на этот дайджест."""
    for name, digest, _ in read_lock():
        pinned = f"{name.rsplit(':', 1)[0]}@{digest}"
        if _run(["docker", "image", "inspect", pinned], check=False).returncode != 0:
            _run(["docker", "pull", pinned], timeout=600)
        _run(["docker", "tag", pinned, name])


def build_test_static(static_dir: Path, src_dir: Path) -> None:
    """Статика edge тестового стенда (Д6а): копия edge/static без сборки чата из дерева и свежая сборка веба в
    static_dir/chat с меткой test-<12 знаков HEAD>. Общее дерево сборка не трогает (Global Constraints Д6): веб
    собирается в копии src_dir/web без node_modules — npm ci по lock там же; рядом копия edge/static, её читает
    tsc (тест места импортирует login.js). Каталоги 0755, файлы 0644: nginx читает их не от владельца."""
    def no_chat(d: str, names: list[str]) -> list[str]:          # сборка чата из дерева — не в копию
        return ["chat"] if Path(d) == REPO / "edge" / "static" else []

    shutil.copytree(REPO / "edge" / "static", static_dir, ignore=no_chat)
    shutil.copytree(REPO / "edge" / "static", src_dir / "edge" / "static", ignore=no_chat)
    shutil.copytree(REPO / "web", src_dir / "web", ignore=shutil.ignore_patterns("node_modules"))
    head = _run(["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO).stdout.strip()
    env = {**os.environ, "PCBK_WEB_OUT": str(static_dir / "chat"), "PCBK_BUILD": f"test-{head}"}
    web = src_dir / "web"
    _run(["npm", "ci", "--ignore-scripts", "--prefer-offline"], cwd=web, timeout=COMPOSE_TIMEOUT_S)
    _run(["npm", "run", "build"], cwd=web, env=env, timeout=COMPOSE_TIMEOUT_S)
    for d, _, files in os.walk(static_dir):
        Path(d).chmod(0o755)
        for f in files:
            (Path(d) / f).chmod(0o644)


def make_test_tls(tls_dir: Path) -> None:
    """Самоподписанный сертификат на 30 дней; каталог 0755, файлы 0644 — только у тестового ключа."""
    tls_dir.mkdir(mode=0o755)
    tls_dir.chmod(0o755)
    _run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
          "-nodes", "-days", "30", "-subj", "/CN=pcbk-edge",
          "-keyout", str(tls_dir / "key.pem"), "-out", str(tls_dir / "cert.pem")])
    for f in ("cert.pem", "key.pem"):
        (tls_dir / f).chmod(0o644)


def make_test_places(secrets_dir: Path, agents_root: Path) -> None:
    """Как на сервере: секреты — пароль, токены LLM и данных, 48 hex без перевода строки, каталог 0700,
    файлы 0444; агенты — корень 0700, каталоги мест 0755."""
    for d in (secrets_dir, agents_root):
        d.mkdir(mode=0o700)
        d.chmod(0o700)
    for n in PLACES:
        for suffix in ("pw", "llm-token", "data-token"):
            f = secrets_dir / f"student-{n:02d}.{suffix}"
            f.write_text(secrets.token_hex(24), encoding="ascii")
            f.chmod(0o444)
        d = agents_root / f"student-{n:02d}"
        d.mkdir(mode=0o755)
        d.chmod(0o755)


def token_line(caller: str, token: str) -> str:
    """Строка таблицы хешей (core-tokens, llm-tokens): «<id> <sha256 токена>»."""
    return f"{caller} {hashlib.sha256(token.encode()).hexdigest()}"


def make_test_core_data(secrets_dir: Path, data_dir: Path) -> None:
    """Как на сервере: bdrv.env, ops.data-token и core-tokens — в каталоге секретов (0700), белый список — в
    DATA_DIR (0700); файлы 0444. core-tokens — хеши токенов данных ops и мест (student-NN.data-token, их пишет
    make_test_places)."""
    data_dir.mkdir(mode=0o700)
    data_dir.chmod(0o700)
    ops = secrets_dir / "ops.data-token"
    ops.write_text(TEST_OPS_DATA_TOKEN, encoding="ascii")
    ops.chmod(0o444)
    core_tokens = [token_line("ops", TEST_OPS_DATA_TOKEN)]
    core_tokens += [token_line(f"student-{n:02d}", (secrets_dir / f"student-{n:02d}.data-token").read_text("ascii"))
                    for n in PLACES]
    for f, lines in ((secrets_dir / "bdrv.env", TEST_BDRV_ENV), (data_dir / "whitelist.txt", TEST_WHITELIST),
                     (secrets_dir / "core-tokens", core_tokens)):
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        f.chmod(0o444)


def make_test_llm(secrets_dir: Path) -> None:
    """Как на сервере, файлы 0444 в каталоге секретов: ключи OpenRouter (заглушки двойника), токен ops и
    llm-tokens — хеши токенов мест (student-NN.llm-token, их пишет make_test_places) и ops."""
    lines = [token_line(f"student-{n:02d}", (secrets_dir / f"student-{n:02d}.llm-token").read_text("ascii"))
             for n in PLACES]
    lines.append(token_line("ops", TEST_OPS_LLM_TOKEN))
    for name, text in (("openrouter.key", TEST_OPENROUTER_KEY), ("openrouter-mgmt.key", TEST_MGMT_KEY),
                       ("ops.llm-token", TEST_OPS_LLM_TOKEN), ("llm-tokens", "\n".join(lines) + "\n")):
        f = secrets_dir / name
        f.write_text(text, encoding="ascii")
        f.chmod(0o444)


def locked_image(repo: str) -> str:
    """Имя:тег образа из images.lock — одно место правды для версий."""
    return next(name for name, _, _ in read_lock() if name.rsplit(":", 1)[0] == repo)


def docker_gid() -> str:
    return _run(["getent", "group", "docker"]).stdout.strip().split(":")[2]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx — ответ как есть: urllib не идёт по Location."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Stack:
    def __init__(self, env_file: Path):
        self.env_file = env_file
        self.env = {k: v for k, v in os.environ.items()
                    if k not in STACK_VARS and not k.startswith("COMPOSE_")}
        values = self.test_env()
        self.stu_net: str = values["STU_NET"]
        self.secrets_dir = Path(values["SECRETS_DIR"])
        self.agents_root = Path(values["AGENTS_DIR"])
        # статика edge со сборкой чата этого прогона (build_test_static)
        self.static_dir = Path(values["PCBK_TEST_STATIC"])
        # токен службы данных места 01 (в core-tokens — его хеш), он же файл student-01.data-token
        self.core_token: str = self.data_token(1)
        # токен LLM администратора, он же файл ops.llm-token (в llm-tokens — его хеш)
        self.ops_llm_token: str = TEST_OPS_LLM_TOKEN
        self._prod_config: dict | None = None
        # готовность после start/restart/unpause; прочим контейнерам хватает Running,
        # местам — healthy (wait_ready); места сюда не входят, up() их не ждёт
        self.ready: dict[str, Callable[[], bool]] = {
            "pcbk-watchdog": self._watchdog_ready,
            "pcbk-edge": self._edge_ready,
            "pcbk-sp-ro": self._sp_ro_ready,
            "pcbk-core": self._core_ready,
        }

    # --- docker compose и docker ---

    def compose(self, *args: str, timeout: float = COMPOSE_TIMEOUT_S, check: bool = True,
                files: tuple[str, ...] = COMPOSE_FILES, env: Mapping[str, str] | None = None) -> str:
        """env — окружение docker compose целиком (None — self.env); оно сильнее --env-file."""
        cmd = ["docker", "compose", "-p", PROJECT, "--env-file", str(self.env_file),
               *(a for f in files for a in ("-f", f)), "--profile", PROFILE, *args]
        return _run(cmd, timeout=timeout, check=check, cwd=REPO, env=self.env if env is None else env).stdout

    def config(self, files: tuple[str, ...] = COMPOSE_FILES) -> dict:
        """Итоговая компоновка; files=("compose.yaml",) — как на сервере, без тестовых правок."""
        return json.loads(self.compose("config", "--format", "json", files=files))

    def prod_config(self) -> dict:
        """compose.yaml как на сервере (без compose.test.yaml), с профилем мест; один раз за прогон."""
        if self._prod_config is None:
            self._prod_config = self.config(files=("compose.yaml",))
        return self._prod_config

    def _docker(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return _run(["docker", *args], check=check)

    def inspect(self, name: str) -> dict:
        return json.loads(self._docker("inspect", "--type", "container", name).stdout)[0]

    def network(self, name: str) -> dict:
        return json.loads(self._docker("network", "inspect", name).stdout)[0]

    def containers(self) -> list[str]:
        return self.compose("ps", "-a", "--format", "{{.Name}}").split()

    def test_env(self) -> dict[str, str]:
        """Значения test.env — те, с которыми поднят стенд."""
        lines = self.env_file.read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def exec(self, name: str, *cmd: str) -> str:
        return self._docker("exec", name, *cmd).stdout.strip()

    def sh(self, name: str, script: str) -> tuple[int, str, str]:
        """bash -c внутри контейнера; ненулевой код — не исключение, а результат."""
        r = self._docker("exec", name, "bash", "-c", script, check=False)
        return r.returncode, r.stdout, r.stderr

    def opencode_pid(self, name: str) -> int:
        """PID OpenCode в месте — по exe, а не по argv."""
        script = ('for p in /proc/[0-9]*; do [ "$(readlink "$p/exe" 2>/dev/null)" = /usr/local/bin/opencode ] '
                  '&& echo "${p#/proc/}"; done; true')
        pids = self.sh(name, script)[1].split()
        if len(pids) != 1:
            raise RuntimeError(f"{name}: процессов OpenCode {len(pids)}, ждали один")
        return int(pids[0])

    # --- секреты и агенты мест (файлы фикстуры) ---

    def password(self, n: int) -> str:
        return (self.secrets_dir / f"student-{n:02d}.pw").read_text(encoding="ascii")

    def llm_token(self, n: int) -> str:
        return (self.secrets_dir / f"student-{n:02d}.llm-token").read_text(encoding="ascii")

    def data_token(self, n: int) -> str:
        return (self.secrets_dir / f"student-{n:02d}.data-token").read_text(encoding="ascii")

    def agents_dir(self, n: int) -> Path:
        return self.agents_root / f"student-{n:02d}"

    def env_names(self, name: str) -> set[str]:
        return {e.split("=", 1)[0] for e in self.inspect(name)["Config"]["Env"] or []}

    def image_env_names(self, image: str) -> set[str]:
        data = json.loads(self._docker("image", "inspect", image).stdout)[0]
        return {e.split("=", 1)[0] for e in data["Config"]["Env"] or []}

    def host_bridge_addresses(self, *names: str) -> list[str]:
        """Адреса IPv4 на мостах этих сетей на хосте; нет моста — ошибка, а не пустой список."""
        addrs = []
        for name in names:
            net = self.network(name)
            bridge = net["Options"].get("com.docker.network.bridge.name") or f"br-{net['Id'][:12]}"
            # без -4: иначе ip прячет мост без адресов IPv4, и «нет моста» не отличить от «нет адреса»
            ifaces = json.loads(_run(["ip", "-j", "addr", "show", "dev", bridge]).stdout)
            if not ifaces:
                raise RuntimeError(f"{name}: моста {bridge} нет на хосте")
            addrs += [a["local"] for i in ifaces for a in i.get("addr_info", []) if a.get("family") == "inet"]
        return addrs

    def host_lan(self) -> str:
        """Первый адрес IPv4 хоста из hostname -I."""
        return next(a for a in _run(["hostname", "-I"]).stdout.split() if ":" not in a)

    # --- запросы к стенду ---

    def _https_get(self, path: str, timeout: float,
                   headers: Mapping[str, str] | None = None) -> tuple[int, http.client.HTTPMessage, str]:
        """GET к https://127.0.0.1:18443 без проверки сертификата, без прокси и перенаправлений. Без своего
        Accept-Encoding http.client шлёт identity — ответ несжатый."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(HOST, PORT, context=ctx, timeout=timeout)
        try:
            conn.request("GET", path, headers=dict(headers or {}))
            resp = conn.getresponse()
            return resp.status, resp.msg, resp.read().decode("utf-8", "replace")
        finally:
            conn.close()

    def https(self, path: str, timeout: float = 20.0) -> tuple[int, str]:
        code, _, body = self._https_get(path, timeout)
        return code, body

    def https_headers(self, path: str, timeout: float = 20.0) -> tuple[int, http.client.HTTPMessage]:
        """Код и заголовки; повторы одного заголовка — через get_all."""
        code, headers, _ = self._https_get(path, timeout)
        return code, headers

    def https_full(self, path: str, headers: Mapping[str, str] | None = None,
                   timeout: float = 20.0) -> tuple[int, str, dict[str, str]]:
        """Код, тело и заголовки ответа; имена заголовков — в нижнем регистре, повторы одного — через «, » (два
        одноимённых заголовка не равны одному). headers — заголовки запроса; сжатое тело не распаковывается."""
        code, msg, body = self._https_get(path, timeout, headers)
        out: dict[str, str] = {}
        for name, value in msg.items():
            key = name.lower()
            out[key] = f"{out[key]}, {value}" if key in out else value
        return code, body, out

    def http_from_watchdog(self, method: str, url: str) -> int:
        """Запрос http.client изнутри pcbk-watchdog — от имени сторожа, как его DockerReader."""
        r = self._docker("exec", "pcbk-watchdog", "python", "-c", WATCHDOG_REQUEST, method, url)
        return int(r.stdout.strip())

    def body_from_watchdog(self, url: str) -> str:
        """GET изнутри pcbk-watchdog, как http_from_watchdog, но тело ответа; не 200 — исключение."""
        return self._docker("exec", "pcbk-watchdog", "python", "-c", WATCHDOG_BODY, url).stdout

    def http_from_core(self, method: str, url: str) -> int:
        """Запрос http.client изнутри pcbk-core — от имени настоящего клиента sp-ctl (Д5-R31); путь как есть."""
        r = self._docker("exec", "pcbk-core", "python", "-c", CORE_REQUEST, method, url)
        return int(r.stdout.strip())

    def http_front(self, method: str, path: str, headers: dict | None = None) -> int:
        """Запрос к core мимо edge: http.client изнутри pcbk-watchdog (он в pcbk-front) к pcbk-core:8000 + путь как
        есть, с заголовками; код ответа."""
        r = self._docker("exec", "pcbk-watchdog", "python", "-c", FRONT_REQUEST, method, path,
                         json.dumps(headers or {}))
        return int(r.stdout.strip())

    def from_place(self, n: int, path: str) -> tuple[int, str]:
        """Одноразовый curl в сети места n к core на {stu_net}.n.2:8000 + путь как есть, без пароля: код и тело."""
        r = self._docker("run", "--rm", "--pull", "never", "--network", f"pcbk-stu-{n:02d}",
                         "--label", ONESHOT_LABEL, "--read-only", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges:true", locked_image("curlimages/curl"),
                         "curl", "-s", "--path-as-is", "--max-time", "10", "-w", "\n%{http_code}",
                         f"http://{self.stu_net}.{n}.2:8000{path}", check=False)
        if r.returncode in (125, 126, 127):      # сбой docker run, а не ответ core
            raise RuntimeError(f"запрос из сети места {n:02d}: docker run, код {r.returncode}\n{r.stderr[-1000:]}")
        body, _, code = r.stdout.rpartition("\n")
        return int(code or 0), body

    def add_account(self, login: str, role: str, place: int | None, password: str) -> None:
        """Учётка шлюза командой администратора в pcbk-core; пароль — в stdin, не в argv."""
        cmd = ["docker", "exec", "-i", "pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "add",
               "--login", login, "--role", role, *(() if place is None else ("--place", str(place)))]
        _run(cmd, input=password + "\n")

    def passwd(self, login: str) -> str:
        """Новый пароль учётки командой администратора (passwd закрывает все её сессии); пароль — в stdin, не в
        argv. Возвращает новый пароль."""
        password = secrets.token_urlsafe(12)
        _run(["docker", "exec", "-i", "pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "passwd",
              "--login", login], input=password + "\n")
        return password

    def gw_health(self) -> dict:
        """/health/gateway серверного слоя изнутри pcbk-watchdog — из pcbk-front, как его читает сторож (preflight
        M14: тело даёт body_from_watchdog, http_front — только код)."""
        return json.loads(self.body_from_watchdog("http://pcbk-core:8000/health/gateway"))

    def gw_client(self) -> GwClient:
        """Клиент шлюза через edge с именем стенда и его Origin."""
        return GwClient(f"https://{HOST}:{PORT}", host=STAND_HOST, origin=STAND_ORIGIN)

    def http_as(self, name: str, network: str, method: str, url: str) -> int:
        """Одноразовый curl, видимый в этой сети под именем name; путь — как есть, без нормализации.

        Имя контейнера своё (pcbk-oneshot-…), name — псевдоним в сети: иначе клиент от имени
        pcbk-core упирается в настоящий pcbk-core («name already in use»). -allowfrom прокси
        разрешает имя через DNS сети — псевдоним проходит так же, как имя контейнера.

        С Д5 настоящий core сам в pcbk-ctl: псевдоним pcbk-core там — второй адрес того же имени, и
        положительный контроль sp-ctl доказывал бы не то (Д5-R31) — только http_from_core.
        """
        if (name, network) == ("pcbk-core", "pcbk-ctl"):
            raise ValueError("положительный контроль sp-ctl — только из настоящего core: http_from_core")
        r = self._docker("run", "--rm", "--pull", "never", "--name", f"pcbk-oneshot-{secrets.token_hex(4)}",
                         "--network", network, "--network-alias", name,
                         "--label", ONESHOT_LABEL, "--read-only", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges:true", locked_image("curlimages/curl"),
                         "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--path-as-is",
                         # stop места ждёт stop_grace_period (10 с)
                         "--max-time", "20", "-X", method, url)
        return int(r.stdout.strip())

    def http_host(self, method: str, url: str, body: dict | None = None,
                  token: str | None = None, headers: dict | None = None) -> tuple[int, str]:
        """Запрос с хоста теста (адрес серверного слоя в pcbk-egress): urllib без прокси и перенаправлений.

        token — заголовок Authorization: Bearer; headers — ещё заголовки (Д5б), поверх Content-Type и
        Authorization. Код ответа и тело; ответ с ошибкой HTTP — тоже результат, а не исключение.
        """
        data = None if body is None else json.dumps(body).encode()
        sent = {} if body is None else {"Content-Type": "application/json"}
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        sent.update(headers or {})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
        try:
            with opener.open(urllib.request.Request(url, data=data, headers=sent, method=method),
                             timeout=20) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode("utf-8", "replace")

    def fake_requests(self) -> list[dict]:
        """Запросы чата, которые получил двойник OpenRouter ({"auth_ok", "body"}), — с хоста теста."""
        code, body = self.http_host("GET", FAKE_OPENROUTER_URL + "/_requests")
        if code != 200:
            raise RuntimeError(f"двойник OpenRouter: /_requests — код {code}")
        return json.loads(body)

    def llm_health(self) -> dict:
        """/health/llm серверного слоя — с хоста теста, без токена."""
        code, body = self.http_host("GET", CORE_URL + "/health/llm")
        if code != 200:
            raise RuntimeError(f"/health/llm: код {code}")
        return json.loads(body)

    def llm_calls(self, last: int = 5) -> list[dict]:
        """Последние строки журнала вызовов LLM (новые — первыми): только столбцы llm_calls."""
        return json.loads(self._docker("exec", "pcbk-core", "python", "-c", LLM_CALLS_PY, str(last)).stdout)

    def llm_refusals(self, session: str, code: str) -> int:
        """Сколько отказов прокси с этим code у сессии OpenCode в журнале вызовов."""
        return int(self._docker("exec", "pcbk-core", "python", "-c", LLM_REFUSALS_PY, session, code).stdout)

    def data_events(self, last: int = 5, channel: str | None = None) -> list[dict]:
        """Последние события службы данных (новые — первыми): caller, channel, tool, outcome; channel — отбор."""
        return json.loads(self._docker("exec", "pcbk-core", "python", "-c", DATA_EVENTS_PY, str(last),
                                       channel or "").stdout)

    def data_event_count(self, caller: str, channel: str) -> int:
        """Сколько событий службы данных у вызывающего в канале (для «данные не читались»)."""
        return int(self._docker("exec", "pcbk-core", "python", "-c", DATA_EVENT_COUNT_PY, caller, channel).stdout)

    def health_log(self, name: str, since: datetime | None = None) -> list[str]:
        """Output записей State.Health.Log этого запуска (End не раньше StartedAt), старые первыми; since —
        ещё и не раньше этого момента (например, старта pcbk-core после пересоздания).

        Docker хранит журнал проверки здоровья (последние пять записей) и через stop/start: запись прошлого
        запуска (например, «200 mcp=connected» до остановки core) не выдаётся за нынешнее состояние.
        """
        st = self._state(name)
        started = datetime.fromisoformat(st["StartedAt"])          # RFC 3339 с наносекундами: Python 3.12 читает
        if since is not None:
            started = max(started, since)
        return [e["Output"] for e in (st.get("Health") or {}).get("Log") or []
                if datetime.fromisoformat(e["End"]) >= started]

    def health_output(self, name: str, since: datetime | None = None) -> str:
        """Output последней записи State.Health.Log этого запуска (и не раньше since); таких нет — пусто."""
        log = self.health_log(name, since)
        return log[-1] if log else ""

    def started_at(self, name: str) -> datetime:
        """State.StartedAt контейнера — момент последнего старта."""
        return datetime.fromisoformat(self._state(name)["StartedAt"])

    def converse(self, n: int, question: str, *, model: str | None = None, tools: dict[str, bool] | None = None,
                 timeout: float = 180.0, host: str | None = None, pw_file: Path | None = None) -> dict:
        """Разговор через API OpenCode места n: converse.js в одноразовом контейнере образа места в сети
        pcbk-stu-NN; пароль (места n или pw_file) и запрос — файлами только для чтения. JSON из stdout.

        model — «провайдер/модель» («pcbk/z-ai/glm-5.3»); tools — карта OpenCode «инструмент → разрешён»
        ({"pcbk_*": False} закрывает инструменты MCP на эту сессию). host — адрес OpenCode вместо STU.N.3.
        """
        ref = None
        if model is not None:
            provider, _, model_id = model.partition("/")
            ref = {"providerID": provider, "modelID": model_id}
        request = {"url": f"http://{host or f'{self.stu_net}.{n}.3'}:4096", "question": question, "model": ref,
                   "tools": tools, "timeout_s": timeout}
        pw = pw_file or self.secrets_dir / f"student-{n:02d}.pw"
        with tempfile.TemporaryDirectory(prefix="pcbk-converse-", dir=self.env_file.parent) as tmp:
            req = Path(tmp) / "req.json"
            req.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            req.chmod(0o444)
            mounts = [a for src, dst in ((CONVERSE_JS, "/app/converse.js"), (pw, "/pw"), (req, "/req.json"))
                      for a in ("--mount", f"type=bind,source={src},target={dst},readonly")]
            r = _run(["docker", "run", "--rm", "--pull", "never", "--network", f"pcbk-stu-{n:02d}",
                      "--label", ONESHOT_LABEL, "--read-only", "--tmpfs", "/tmp:exec", "--no-healthcheck",
                      "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", *mounts,
                      "--entrypoint", "/usr/local/bin/opencode", "-e", "BUN_BE_BUN=1", STUDENT_IMAGE,
                      "/app/converse.js"], timeout=timeout + CONVERSE_WORST_EXTRA_S)
        return json.loads(r.stdout)

    def logs(self, name: str) -> str:
        """Журнал контейнера: stdout и stderr вместе (серверный слой пишет в stderr)."""
        r = self._docker("logs", name)
        return r.stdout + r.stderr

    def oc(self, n: int, method: str, path: str, pw_of: int | None, timeout: float = 10.0) -> tuple[int, str]:
        """Запрос к OpenCode места n из его сети; пароль места pw_of — файлом, не в argv и не в окружении."""
        auth = [] if pw_of is None else ["--variable", "PW@/pw", "--expand-user", "opencode:{{PW}}"]
        mount = [] if pw_of is None else ["-v", f"{self.secrets_dir / f'student-{pw_of:02d}.pw'}:/pw:ro"]
        r = self._docker("run", "--rm", "--pull", "never", "--network", f"pcbk-stu-{n:02d}",
                         "--label", ONESHOT_LABEL, "--read-only", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges:true", *mount, locked_image("curlimages/curl"),
                         "curl", "-s", "--max-time", str(timeout), "-X", method, *auth,
                         "-w", "\n%{http_code}", f"http://{self.stu_net}.{n}.3:4096{path}", check=False)
        body, _, code = r.stdout.rpartition("\n")
        return int(code or 0), body

    def wait_oc(self, n: int, timeout: float = 30.0) -> None:
        """GET /global/health места n со своим паролем, по 2 с на попытку, до 200."""
        deadline = time.monotonic() + timeout
        while True:
            code = self.oc(n, "GET", "/global/health", n, timeout=2)[0]
            if code == 200:
                return
            if time.monotonic() > deadline:
                raise AssertionError(f"место {n:02d}: /global/health не ответил 200 за {timeout} с, код {code}")
            time.sleep(0.5)

    def probe(self, network: str, addr: str, port: int) -> int:
        """Способ Д1 из одноразового curl в сети network: 1 — соединение есть, 0 — нет. Только порт, без пароля."""
        r = self._docker("run", "--rm", "--pull", "never", "--network", network,
                         "--label", ONESHOT_LABEL, "--read-only", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges:true", locked_image("curlimages/curl"),
                         "curl", "-sv", "--connect-timeout", "3", "-m", "4", f"telnet://{addr}:{port}",
                         check=False)
        # 125–127 — сбой docker run, а не отказ соединения: иначе проба дала бы ложный 0
        if r.returncode in (125, 126, 127):
            raise RuntimeError(f"проба в {network}: docker run, код {r.returncode}\n{r.stderr[-1000:]}")
        return 1 if CONNECTED.search(r.stdout + r.stderr) else 0

    def wait_snapshot_after(self, moment: datetime, timeout: float = READY_TIMEOUT_S) -> dict:
        """Снимок сторожа, чей такт начался позже moment (checked_at — начало такта)."""
        return self.wait_status(lambda d: bool(d["checked_at"])
                                and datetime.fromisoformat(d["checked_at"]) > moment, timeout)

    def wait_status(self, pred: Callable[[dict], bool], timeout: float) -> dict:
        """/status.json через edge, пока pred не станет истинным; по сроку — AssertionError."""
        deadline = time.monotonic() + timeout
        last: dict | None = None
        while True:
            try:
                code, body = self.https("/status.json", timeout=10)
                if code == 200:
                    last = json.loads(body)
                    if pred(last):
                        return last
            except (OSError, http.client.HTTPException, ValueError):
                pass
            if time.monotonic() > deadline:
                raise AssertionError(f"за {timeout} с условие не выполнилось; последний ответ: {last}")
            time.sleep(1)

    # --- готовность ---

    def _watchdog_ready(self) -> bool:
        # тот же вызов, что HEALTHCHECK образа: /healthz 200 изнутри
        return self._docker("exec", "pcbk-watchdog", "python", "-m", "pcbk_watchdog.main",
                            "--healthcheck", check=False).returncode == 0

    def _edge_ready(self) -> bool:
        r = self._docker("exec", "pcbk-edge", "wget", "-q", "-T", "2", "-O", "-",
                         "http://127.0.0.1:8080/healthz", check=False)
        return r.returncode == 0 and r.stdout.strip() == "ok"

    def _core_ready(self) -> bool:
        # тот же вызов, что HEALTHCHECK образа: /healthz 200 изнутри
        return self._docker("exec", "pcbk-core", "python", "-m", "pcbk_core.main",
                            "--healthcheck", check=False).returncode == 0

    def _sp_ro_ready(self) -> bool:
        # ping через прокси тем же путём, что сторож
        r = self._docker("exec", "pcbk-watchdog", "python", "-c", WATCHDOG_REQUEST,
                         "GET", "http://pcbk-sp-ro:2375/v1.44/_ping", check=False)
        return r.returncode == 0 and r.stdout.strip() == "200"

    def _state(self, name: str) -> dict:
        return self.inspect(name)["State"]

    def wait_healthy(self, name: str, timeout: float = READY_TIMEOUT_S) -> None:
        """Проверка здоровья образа дала healthy — или отказ: State.Error, выход
        (при restart: unless-stopped он виден как Restarting), срок истёк."""
        deadline = time.monotonic() + timeout
        while True:
            st = self._state(name)
            health = (st.get("Health") or {}).get("Status")
            if st["Running"] and not st["Restarting"] and not st["Paused"] and health == "healthy":
                return
            if st.get("Error") or not st["Running"] or st["Restarting"]:
                raise RuntimeError(f"{name}: не работает — {st['Status']}, код {st['ExitCode']}, "
                                   f"{st.get('Error') or 'без ошибки Docker'}")
            if time.monotonic() > deadline:
                raise RuntimeError(f"{name}: не healthy за {timeout} с, сейчас {health}")
            time.sleep(0.5)

    def wait_ready(self, name: str, timeout: float = READY_TIMEOUT_S) -> None:
        """Готов — или явный отказ: контейнер вышел, либо срок истёк. Место готово, когда healthy."""
        if name.startswith(STUDENT_PREFIX):
            self.wait_healthy(name, timeout)
            return
        probe = self.ready.get(name, lambda: self._state(name)["Running"])
        deadline = time.monotonic() + timeout
        while True:
            st = self._state(name)
            if st["Running"] and not st["Paused"] and probe():
                return
            if not st["Running"] and not st["Restarting"]:
                raise RuntimeError(f"{name}: контейнер не работает — {st['Status']}, код {st['ExitCode']}")
            if time.monotonic() > deadline:
                raise RuntimeError(f"{name}: не готов за {timeout} с")
            time.sleep(0.5)

    def start(self, name: str) -> None:
        self._docker("start", name)
        self.wait_ready(name)

    def restart(self, name: str) -> None:
        self._docker("restart", name)
        self.wait_ready(name)

    def unpause(self, name: str) -> None:
        self._docker("unpause", name)
        self.wait_ready(name)

    def recreate(self, service: str, overrides: Mapping[str, str]) -> None:
        """Пересоздать службу с переменными overrides поверх test.env, дождаться готовности и снимка
        сторожа, снятого уже после неё.

        Окружение оболочки сильнее --env-file; {} — вернуть значения test.env и умолчания компоновки.
        --force-recreate обязателен: при прежнем окружении up -d контейнер не трогает, а ключи и
        переменные серверный слой читает только при старте. Снимок — чтобы wait_status не принял
        строку такта, пришедшегося на само пересоздание («… не отвечает»), за новое состояние.
        """
        self.compose("up", "-d", "--no-build", "--force-recreate", service, env={**self.env, **overrides})
        self.wait_ready(self.config()["services"][service]["container_name"])
        self.wait_snapshot_after(datetime.now(timezone.utc))

    def stop(self, name: str) -> None:
        self._docker("stop", name)
        if self._state(name)["Running"]:
            raise RuntimeError(f"{name}: после stop всё ещё работает")

    def pause(self, name: str) -> None:
        self._docker("pause", name)
        if not self._state(name)["Paused"]:
            raise RuntimeError(f"{name}: после pause не приостановлен")

    # --- подъём и уборка ---

    def refuse_foreign(self) -> None:
        """Контейнеры и сети с нашими именами из другого проекта — не трогаем, тест не идёт."""
        cfg = self.config()
        objects = [("container", s["container_name"]) for s in cfg["services"].values()
                   if s.get("container_name")]
        objects += [("network", n["name"]) for n in cfg.get("networks", {}).values()
                    if n.get("name") and not n.get("external")]
        for kind, name in objects:
            r = self._docker(kind, "inspect", "--format", "{{json .Config.Labels}}" if kind == "container"
                             else "{{json .Labels}}", name, check=False)
            if r.returncode != 0:
                continue
            labels = json.loads(r.stdout) or {}
            if labels.get("com.docker.compose.project") != PROJECT:
                pytest.fail(f"{kind} {name} уже есть и принадлежит не проекту {PROJECT} — тест его не трогает")
        # тома мест внешние: том без метки теста — данные студента, их не трогаем
        for vol in cfg.get("volumes", {}).values():
            name = vol.get("name") or ""
            if not (vol.get("external") and name.startswith(STUDENT_PREFIX)):
                continue
            r = self._docker("volume", "inspect", "--format", "{{json .Labels}}", name, check=False)
            if r.returncode == 0 and TEST_VOLUME_LABEL not in (json.loads(r.stdout) or {}):
                pytest.fail(f"том {name} уже есть и создан не тестом — тест его не трогает")

    def up(self) -> None:
        cfg = self.config()                          # compose() уже передаёт --profile students
        places = sorted(n for n, s in cfg["services"].items() if PROFILE in (s.get("profiles") or []))
        for name in places:                          # тома мест внешние: создаёт фикстура, на сервере — выкладка
            for kind in ("state", "work"):
                self._docker("volume", "create", "--label", TEST_VOLUME_LABEL, f"pcbk-{name}-{kind}")
        self.compose("create", "--no-build", *places)
        self.compose("up", "-d", "--build", *(n for n in cfg["services"] if n not in places))
        for name in self.ready:
            self.wait_ready(name)
        ready_at = datetime.now(timezone.utc)
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            try:
                if self.https("/status", timeout=10)[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"/status через edge не ответил 200 за {READY_TIMEOUT_S} с")
            time.sleep(0.5)
        # первый такт мог пройти до старта edge — ждём снимок, снятый уже при готовом стенде
        self.wait_snapshot_after(ready_at)

    def down(self) -> None:
        # одноразовые клиенты http_as, оставшиеся после оборванного прогона, — только с нашей меткой
        leftovers = self._docker("ps", "-aq", "--filter", f"label={ONESHOT_LABEL}").stdout.split()
        if leftovers:
            self._docker("rm", "-f", *leftovers)
        self.compose("down", "-v", "--remove-orphans", "--timeout", "10")
        # внешние тома мест compose не удаляет; удаляем только созданные тестом — по метке
        volumes = self._docker("volume", "ls", "-q", "--filter", f"label={TEST_VOLUME_LABEL}",
                               "--filter", f"name={STUDENT_PREFIX}").stdout.split()
        if volumes:
            self._docker("volume", "rm", *volumes)


@pytest.fixture(scope="session")
def student_image() -> str:
    """Образ места собирается в каждом прогоне; контекст — student/."""
    _run(["docker", "build", "-t", STUDENT_IMAGE, "student/"], timeout=COMPOSE_TIMEOUT_S, cwd=REPO)
    return STUDENT_IMAGE


@pytest.fixture(scope="session")
def stack(tmp_path_factory, student_image):
    prepare_images()
    work = tmp_path_factory.mktemp("pcbk-stack")
    make_test_tls(work / "tls")
    make_test_places(work / "secrets", work / "agents")
    make_test_core_data(work / "secrets", work / "data")
    make_test_llm(work / "secrets")
    build_test_static(work / "static", work / "web-src")   # до test.env и compose: у PCBK_TEST_STATIC «:?» (M15)
    env_file = work / "test.env"
    env_file.write_text("\n".join([
        f"DOCKER_GID={docker_gid()}",
        "STU_NET=172.31",
        f"TLS_DIR={work / 'tls'}",
        "DISPLAY_TZ=Europe/Moscow",
        f"SECRETS_DIR={work / 'secrets'}",
        f"AGENTS_DIR={work / 'agents'}",
        f"DATA_DIR={work / 'data'}",
        "MEM_WARN_MIB=64",
        "MEM_FAIL_MIB=32",
        f"GW_ORIGIN={STAND_ORIGIN}",
        f"PCBK_TEST_STATIC={work / 'static'}",
    ]) + "\n", encoding="utf-8")
    s = Stack(env_file)
    s.refuse_foreign()
    s.down()   # остатки прошлого прогона — только своего проекта
    try:
        s.up()
        yield s
    finally:
        s.down()


@pytest.fixture(scope="module")
def browser():
    """Headless Chrome (cdp.py) на модуль; вкладки — фикстурой page на тест. Без Chrome — пропуск с причиной
    (закрытие дня требует прогона без пропусков)."""
    if shutil.which("google-chrome") is None:
        pytest.skip("нет google-chrome")
    with Browser() as b:
        yield b


def _tab(browser, isolated: bool):
    p = browser.page(isolated=isolated)
    try:
        yield p
    finally:
        p.close()
    # сбой разбора события — пропавший отчёт CSP или ошибка консоли: зелёный тест без них не считается
    assert browser.handler_errors == [], browser.handler_errors


@pytest.fixture
def page(browser):
    """Вкладка на тест, закрывается в teardown (preflight F3): шлюз держит не больше трёх потоков на логин, а вкладки
    одного профиля делят cookie — незакрытые вкладки прошлых тестов съели бы потоки следующих."""
    yield from _tab(browser, isolated=False)


@pytest.fixture
def isolated_page(browser):
    """Как page, но со своим контекстом браузера (свои cookie): вход другой учёткой (преподаватель) не меняет cookie
    вкладок студента, закрывается вместе с контекстом."""
    yield from _tab(browser, isolated=True)

