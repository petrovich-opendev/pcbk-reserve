"""Стенд для прогонщика отбора (Д8-R74, Д8-R76): чтение журналов core, агенты мест, калькулятор, строки сторожа.

Stand — договор (Protocol): его строят ServerStand (сервер через SSH и туннели, Б4/Б5) и StackStand интеграции
(tests/integration/benchstand.py, А6). Все методы — синхронные; сбой — StandError (у ServerStand) или исключение
стенда как есть: прогонщик пишет такой прогон как stand_error и класс исключения — в заметки.

ServerStand(ssh, core_url, edge_url, edge_host, secrets, remote_dir):
- ssh — argv клиента SSH до адреса сервера включительно (["ssh", "-o", "BatchMode=yes", "user@host"]); удалённая
  команда — одной строкой с экранированием (shlex), у каждого вызова срок (SSH_TIMEOUT_S, у save_agent —
  SAVE_TIMEOUT_S; m-р4-6): истёк — StandError;
- строки llm_calls и agents — только на чтение (sqlite mode=ro) изнутри pcbk-core; архив — agents.admin archive;
- save_agent — клиент MCP изнутри pcbk-core на петле (MCP_SAVE_PY Д7а): токен данных места читается из
  $SECRETS_DIR/student-NN.data-token на сервере и сервер не покидает (Д8-R76);
- run_callers — LLM_RUN_CALLERS из окружения pcbk-core (docker inspect, только эта строка);
- place_model — одноразовый curl в сети места (помощник oc Д2) → GET /config → model;
- place_mcp_ok — запись журнала здоровья места после позднего из StartedAt core и места содержит mcp=connected;
- calc — POST core_url/api/data/<инструмент> токеном данных ops (secrets/ops.data-token) через туннель к core;
- health_llm — GET core_url/health/llm; status — GET edge_url/status.json сторожа с Host = edge_host.

calc_patient(stand, tool, args, sleep=…) — калькулятор с паузой и повтором на rate на всё окно частоты службы (Б3):
его зовут прогонщик (сверка и единицы) и cards_check (Б3).

STU_NET и SECRETS_DIR берутся из .env в remote_dir (/opt/pcbk-reserve) один раз простым grep, сверяются по виду и
идут в команды литералами (SD=…; STU=…;) — подстановки команд «$(…)» в удалённых командах нет; без строк в .env —
умолчания compose.
Тексты StandError — без вывода команд и тел ответов: в них бывают данные.
"""
from __future__ import annotations

import http.client
import json
import math
import re
import shlex
import ssl
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .secret import Secret

SSH_TIMEOUT_S = 60.0
SAVE_TIMEOUT_S = 120.0
HTTP_TIMEOUT_S = 60.0
CORE = "pcbk-core"
CORE_UID = 10003
CURL_IMAGE = "curlimages/curl:8.16.0"
REMOTE_DIR = "/opt/pcbk-reserve"
# имя агента службы (core/pcbk_core/agents/draft.py NAME_RE, 3–40 знаков) — значением: стенд только на stdlib
AGENT_NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", re.ASCII)
# инструменты чтения службы данных (с Д9а — и ряд с наработкой, Д9-R89): калькулятор стенда и субагент карточек
DATA_TOOLS = ("catalog_search", "tag_now", "tag_period", "tag_series", "calc_runtime")
STU_RE = re.compile(r"\d{1,3}\.\d{1,3}")                        # STU_NET compose: первые два октета сетей мест
REMOTE_PATH_RE = re.compile(r"/[A-Za-z0-9._/-]+")

# строки журнала вызовов сессий прогона: stdin — JSON-список id сессий; только столбцы llm_calls
LLM_ROWS_PY = """\
import json, sqlite3, sys
sessions = [s for s in json.load(sys.stdin) if isinstance(s, str)]
c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
rows = []
if sessions:
    q = 'SELECT * FROM llm_calls WHERE session IN (%s) ORDER BY id' % ','.join('?' * len(sessions))
    rows = [dict(r) for r in c.execute(q, sessions)]
print(json.dumps(rows, ensure_ascii=False))
"""
# живые (не archived) агенты места: argv — место
AGENT_ROWS_PY = """\
import json, sqlite3, sys
c = sqlite3.connect('file:/var/lib/pcbk-core/core.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
q = ("SELECT name, title, state, flags, created_at, state_at, visible_at FROM agents"
     " WHERE place = ? AND state != 'archived' ORDER BY id")
print(json.dumps([dict(r) for r in c.execute(q, (int(sys.argv[1]),))], ensure_ascii=False))
"""
# текст файла агента места (AGENT_FILE_PY Д7а, только get): argv — место и имя без .md
AGENT_FILE_PY = """\
import sys
from pathlib import Path
place, name = sys.argv[1:3]
sys.stdout.write((Path("/srv/agents") / f"student-{int(place):02d}" / f"{name}.md").read_text(encoding="utf-8"))
"""
# save_agent клиентом MCP SDK изнутри pcbk-core на петле (MCP_SAVE_PY Д7а): stdin — токен данных места первой
# строкой, дальше JSON аргументов; stdout — structuredContent ответа
MCP_SAVE_PY = """\
import asyncio, json, sys
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
token, _, raw = sys.stdin.read().partition("\\n")
async def main():
    headers = {"Authorization": f"Bearer {token.strip()}"}
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(10, read=30), trust_env=False) as client:
        async with streamable_http_client("http://127.0.0.1:8000/mcp", http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("save_agent", json.loads(raw))
    if result.isError or result.structuredContent is None:
        sys.exit("save_agent: ответ — ошибка MCP, а не результат службы")
    print(json.dumps(result.structuredContent, ensure_ascii=False))
asyncio.run(main())
"""


class StandError(Exception):
    """Сбой стенда (SSH, docker, HTTP): текст — постоянный, без вывода команд и тел ответов."""


# частота вызывающего службы данных (core/pcbk_core/data: PER_CALLER_LIMIT за RATE_WINDOW_S — 60 вызовов за 300 с,
# скользящее окно, отказ не записывается) — значением: стенд только на stdlib
CALC_RATE_WINDOW_S = 300.0
CALC_RATE_PAUSE_S = 15.0
# пауза и повтор на всё окно частоты (Б3): за CALC_RATE_WINDOW_S любой прежний вызов выходит из окна, и повтор
# проходит; трёх попыток (30 с) мало — у двойника прогоны идут быстрее предела, и сверка пропускалась бы
CALC_TRIES = 1 + math.ceil(CALC_RATE_WINDOW_S / CALC_RATE_PAUSE_S)


def calc_patient(stand: Stand, tool: str, args: dict, *, sleep: Callable[[float], None],
                 clock: Callable[[], float] | None = None, deadline: float | None = None,
                 stop: Callable[[], bool] | None = None) -> dict:
    """Калькулятор (stand.calc) с паузой и повтором на ответ rate: до CALC_TRIES попыток через CALC_RATE_PAUSE_S, не
    позже deadline по clock; перед каждой паузой — stop() (остановка прогонщика: жёсткая или сигнал, итоговая доводка
    Д8б): истина — пауз больше нет. Ответ rate после всех попыток возвращается как есть — его учитывает вызывающий."""
    answer = stand.calc(tool, args)
    tries = 1
    while isinstance(answer, dict) and answer.get("status") == "rate" and tries < CALC_TRIES:
        if deadline is not None and clock is not None and clock() + CALC_RATE_PAUSE_S > deadline:
            break
        if stop is not None and stop():
            break
        sleep(CALC_RATE_PAUSE_S)
        answer = stand.calc(tool, args)
        tries += 1
    return answer


class Stand(Protocol):
    def llm_rows(self, sessions: list[str]) -> list[dict]: ...
    def agent_rows(self, place: int) -> list[dict]: ...
    def agent_file(self, place: int, name: str) -> str: ...
    def save_agent(self, place: int, args: dict) -> dict: ...
    def archive_agent(self, place: int, name: str) -> None: ...
    def calc(self, tool: str, args: dict) -> dict: ...
    def status(self) -> dict: ...
    def health_llm(self) -> dict: ...
    def run_callers(self) -> frozenset[str]: ...
    def place_model(self, place: int) -> str: ...
    def place_mcp_ok(self, place: int) -> bool: ...


def _place(place: int) -> int:
    if isinstance(place, bool) or not isinstance(place, int) or not 1 <= place <= 10:
        raise StandError("место: 1…10")
    return place


def _agent_name(name: str) -> str:
    if not isinstance(name, str) or not 3 <= len(name) <= 40 or not AGENT_NAME_RE.fullmatch(name):
        raise StandError("имя агента не по правилам службы")
    return name


def mcp_connected(core_state: dict, place_state: dict) -> bool:
    """mcp=connected в последней записи журнала здоровья места после позднего из StartedAt core и места."""
    try:
        since = max(datetime.fromisoformat(core_state["StartedAt"]), datetime.fromisoformat(place_state["StartedAt"]))
        log = (place_state.get("Health") or {}).get("Log") or []
        fresh = [e.get("Output") or "" for e in log if datetime.fromisoformat(e["End"]) >= since]
    except (KeyError, TypeError, ValueError):
        return False
    return bool(fresh) and "mcp=connected" in fresh[-1]


class ServerStand:
    """Stand сервера: SSH для docker и sqlite в pcbk-core, туннели для core (данные, /health/llm) и edge."""

    def __init__(self, ssh: Sequence[str], core_url: str, edge_url: str, edge_host: str, secrets: Path, *,
                 remote_dir: str = REMOTE_DIR, run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        if not ssh or not all(isinstance(a, str) and a for a in ssh):
            raise ValueError("ssh — непустой список аргументов")
        self.ssh = list(ssh)
        self.core_url = core_url.rstrip("/")
        self.edge_url = edge_url.rstrip("/")
        self.edge_host = edge_host
        self.secrets = Path(secrets)
        if not REMOTE_PATH_RE.fullmatch(remote_dir):
            raise ValueError("remote_dir — абсолютный путь из [A-Za-z0-9._/-]")
        self.remote_dir = remote_dir
        self._env: tuple[str, str] | None = None
        self._run = run
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    # --- SSH ---

    def _ssh(self, command: str, *, stdin: str | None = None, timeout: float = SSH_TIMEOUT_S) -> str:
        try:
            r = self._run([*self.ssh, command], input=stdin, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise StandError(f"ssh: срок {int(timeout)} с истёк") from None
        except OSError as exc:
            raise StandError(f"ssh: {type(exc).__name__}") from None
        if r.returncode != 0:
            raise StandError(f"ssh: код {r.returncode}")
        return r.stdout

    def _core_py(self, script: str, *args: str, stdin: str | None = None, user: bool = False,
                 timeout: float = SSH_TIMEOUT_S) -> str:
        argv = ["docker", "exec", "-i", *(("-u", str(CORE_UID)) if user else ()), CORE, "python", "-c", script, *args]
        return self._ssh(shlex.join(argv), stdin=stdin, timeout=timeout)

    def remote_env(self) -> tuple[str, str]:
        """(STU_NET, SECRETS_DIR) из .env в remote_dir (последняя строка ключа) или умолчания compose; читается один
        раз простым grep и сверяется здесь: в командах — только проверенные значения, без подстановки команд «$(…)»."""
        if self._env is None:
            out = self._ssh(f"cd {shlex.quote(self.remote_dir)} && grep -E '^(STU_NET|SECRETS_DIR)=' .env || true")
            found: dict[str, str] = {}
            for line in out.splitlines():
                key, _, value = line.partition("=")
                found[key.strip()] = value.strip()
            stu = found.get("STU_NET") or "172.31"
            sd = found.get("SECRETS_DIR") or f"{self.remote_dir.rstrip('/')}/secrets"
            if not STU_RE.fullmatch(stu) or not REMOTE_PATH_RE.fullmatch(sd):
                raise StandError(".env сервера: STU_NET или SECRETS_DIR не того вида")
            self._env = (stu, sd)
        return self._env

    def _env_prefix(self) -> str:
        """SD и STU — проверенные значения remote_env() литералами (SD=/opt/…/secrets; STU=172.31; )."""
        stu, sd = self.remote_env()
        return f"SD={shlex.quote(sd)}; STU={shlex.quote(stu)}; "

    @staticmethod
    def _json(text: str) -> Any:
        try:
            return json.loads(text)
        except ValueError:
            raise StandError("ответ стенда — не JSON") from None

    def llm_rows(self, sessions: list[str]) -> list[dict]:
        rows = self._json(self._core_py(LLM_ROWS_PY, stdin=json.dumps(list(sessions))))
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def agent_rows(self, place: int) -> list[dict]:
        rows = self._json(self._core_py(AGENT_ROWS_PY, str(_place(place))))
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def agent_file(self, place: int, name: str) -> str:
        return self._core_py(AGENT_FILE_PY, str(_place(place)), _agent_name(name), user=True)

    def save_agent(self, place: int, args: dict) -> dict:
        nn = f"{_place(place):02d}"
        exec_argv = shlex.join(["docker", "exec", "-i", CORE, "python", "-c", MCP_SAVE_PY])
        command = (self._env_prefix() + f'{{ cat "$SD/student-{nn}.data-token"; echo; cat; }} | {exec_argv}')
        answer = self._json(self._ssh(command, stdin=json.dumps(args, ensure_ascii=False), timeout=SAVE_TIMEOUT_S))
        if not isinstance(answer, dict):
            raise StandError("save_agent: ответ — не объект")
        return answer

    def archive_agent(self, place: int, name: str) -> None:
        self._ssh(shlex.join(["docker", "exec", CORE, "python", "-m", "pcbk_core.agents.admin", "archive",
                              "--place", str(_place(place)), "--name", _agent_name(name), "--yes"]))

    def run_callers(self) -> frozenset[str]:
        out = self._ssh("docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' " + CORE
                        + " | sed -n 's/^LLM_RUN_CALLERS=//p'")
        return frozenset(c.strip() for c in out.strip().split(",") if c.strip())

    def place_model(self, place: int) -> str:
        n = _place(place)
        nn = f"{n:02d}"
        command = (self._env_prefix()
                   + f"docker run --rm --pull never --runtime=runsc --network pcbk-stu-{nn} --read-only --cap-drop ALL"
                   + f' --security-opt no-new-privileges:true -v "$SD/student-{nn}.pw:/pw:ro" {CURL_IMAGE}'
                   + " -sf -m 10 --variable PW@/pw --expand-user 'opencode:{{PW}}'"
                   + f' "http://$STU.{n}.3:4096/config"')
        doc = self._json(self._ssh(command))
        model = doc.get("model") if isinstance(doc, dict) else None
        if not isinstance(model, str):
            raise StandError("GET /config места: нет model")
        return model

    def place_mcp_ok(self, place: int) -> bool:
        nn = f"{_place(place):02d}"
        out = self._ssh(f"docker inspect -f '{{{{json .State}}}}' {CORE} pcbk-student-{nn}")
        states = [self._json(line) for line in out.splitlines() if line.strip()]
        if len(states) != 2 or not all(isinstance(s, dict) for s in states):
            return False
        return mcp_connected(states[0], states[1])

    # --- HTTP через туннели ---

    def _http_json(self, method: str, url: str, body: dict | None = None, token: str | None = None,
                   timeout: float = HTTP_TIMEOUT_S, ok: tuple[int, ...] = (200,)) -> Any:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if token is not None:
            headers["Authorization"] = Secret(f"Bearer {token}")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(urllib.request.Request(url, data=data, headers=headers, method=method),
                             timeout=timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                status, raw = exc.code, exc.read()
        except (OSError, http.client.HTTPException) as exc:
            raise StandError(f"{urlsplit(url).path}: {type(exc).__name__}") from None
        if status not in ok:
            raise StandError(f"{urlsplit(url).path}: код {status}")
        return self._json(raw.decode("utf-8", "replace"))

    def calc(self, tool: str, args: dict) -> dict:
        if tool not in DATA_TOOLS:
            raise StandError("калькулятор: инструмент не из службы данных")
        token = Secret((self.secrets / "ops.data-token").read_text(encoding="ascii").strip())
        # 503 — ответ службы «не настроена» (config_unavailable) в своём виде, его сверяет check_run
        answer = self._http_json("POST", f"{self.core_url}/api/data/{tool}", dict(args), token=token, ok=(200, 503))
        if not isinstance(answer, dict):
            raise StandError("калькулятор: ответ — не объект")
        return answer

    def health_llm(self) -> dict:
        answer = self._http_json("GET", f"{self.core_url}/health/llm", timeout=20.0)
        if not isinstance(answer, dict):
            raise StandError("/health/llm: ответ — не объект")
        return answer

    def status(self) -> dict:
        parts = urlsplit(self.edge_url)
        if parts.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                parts.hostname, parts.port or 443, context=self._ctx, timeout=20)
        else:
            conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=20)
        try:
            conn.putrequest("GET", "/status.json", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", self.edge_host)
            conn.endheaders()
            resp = conn.getresponse()
            status, raw = resp.status, resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise StandError(f"/status.json: {type(exc).__name__}") from None
        finally:
            conn.close()
        if status != 200:
            raise StandError(f"/status.json: код {status}")
        answer = self._json(raw.decode("utf-8", "replace"))
        if not isinstance(answer, dict):
            raise StandError("/status.json: ответ — не объект")
        return answer


__all__ = ["AGENT_FILE_PY", "AGENT_ROWS_PY", "CALC_RATE_PAUSE_S", "CALC_RATE_WINDOW_S", "CALC_TRIES", "LLM_ROWS_PY",
           "MCP_SAVE_PY", "ServerStand", "Stand", "StandError", "calc_patient", "mcp_connected"]
