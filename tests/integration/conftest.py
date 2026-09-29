"""Стенд для проверок компоновки: проект pcbk-test на локальном Docker, порт 127.0.0.1:18443.

Образы — только через deploy/images.lock; тестовый TLS и test.env — во временном
каталоге; в конце — `down -v` даже при сбое. Чужие контейнеры и сети с нашими
именами тест не трогает — отказывается запускаться.
"""
import http.client
import json
import os
import re
import ssl
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROJECT = "pcbk-test"
HOST, PORT = "127.0.0.1", 18443
LOCK = REPO / "deploy" / "images.lock"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
READY_TIMEOUT_S = 60
COMPOSE_TIMEOUT_S = 600
COMPOSE_FILES = ("compose.yaml", "compose.test.yaml")
# переменные компоновки: берутся только из test.env, не из окружения оболочки
STACK_VARS = frozenset({"DOCKER_GID", "STU_NET", "TLS_DIR", "DISPLAY_TZ", "SECRETS_DIR", "AGENTS_DIR",
                        "MEM_WARN_MIB", "MEM_FAIL_MIB", "TICK_S", "STALE_AFTER_S", "DOCKER_TIMEOUT_S",
                        "DRILL_FREEZE_LOOP"})
# метка одноразовых клиентов http_as: уборка снимает только их
ONESHOT_LABEL = "pcbk-test.oneshot"
# запрос изнутри сторожа: argv — метод и адрес; печатает код ответа
WATCHDOG_REQUEST = """\
import http.client, sys
from urllib.parse import urlsplit
u = urlsplit(sys.argv[2])
conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=10)
conn.request(sys.argv[1], u.path + ("?" + u.query if u.query else ""))
print(conn.getresponse().status)
"""


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


def make_test_tls(tls_dir: Path) -> None:
    """Самоподписанный сертификат на 30 дней; каталог 0755, файлы 0644 — только у тестового ключа."""
    tls_dir.mkdir(mode=0o755)
    tls_dir.chmod(0o755)
    _run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
          "-nodes", "-days", "30", "-subj", "/CN=pcbk-edge",
          "-keyout", str(tls_dir / "key.pem"), "-out", str(tls_dir / "cert.pem")])
    for f in ("cert.pem", "key.pem"):
        (tls_dir / f).chmod(0o644)


def locked_image(repo: str) -> str:
    """Имя:тег образа из images.lock — одно место правды для версий."""
    return next(name for name, _, _ in read_lock() if name.rsplit(":", 1)[0] == repo)


def docker_gid() -> str:
    return _run(["getent", "group", "docker"]).stdout.strip().split(":")[2]


class Stack:
    def __init__(self, env_file: Path):
        self.env_file = env_file
        self.env = {k: v for k, v in os.environ.items()
                    if k not in STACK_VARS and not k.startswith("COMPOSE_")}
        # готовность после start/restart/unpause; прочим контейнерам хватает Running
        self.ready: dict[str, Callable[[], bool]] = {
            "pcbk-watchdog": self._watchdog_ready,
            "pcbk-edge": self._edge_ready,
            "pcbk-sp-ro": self._sp_ro_ready,
        }

    # --- docker compose и docker ---

    def compose(self, *args: str, timeout: float = COMPOSE_TIMEOUT_S, check: bool = True,
                files: tuple[str, ...] = COMPOSE_FILES) -> str:
        cmd = ["docker", "compose", "-p", PROJECT, "--env-file", str(self.env_file),
               *(a for f in files for a in ("-f", f)), *args]
        return _run(cmd, timeout=timeout, check=check, cwd=REPO, env=self.env).stdout

    def config(self, files: tuple[str, ...] = COMPOSE_FILES) -> dict:
        """Итоговая компоновка; files=("compose.yaml",) — как на сервере, без тестовых правок."""
        return json.loads(self.compose("config", "--format", "json", files=files))

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

    # --- запросы к стенду ---

    def _https_get(self, path: str, timeout: float) -> tuple[int, http.client.HTTPMessage, str]:
        """GET к https://127.0.0.1:18443 без проверки сертификата, без прокси и перенаправлений."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(HOST, PORT, context=ctx, timeout=timeout)
        try:
            conn.request("GET", path)
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

    def http_from_watchdog(self, method: str, url: str) -> int:
        """Запрос http.client изнутри pcbk-watchdog — от имени сторожа, как его DockerReader."""
        r = self._docker("exec", "pcbk-watchdog", "python", "-c", WATCHDOG_REQUEST, method, url)
        return int(r.stdout.strip())

    def http_as(self, name: str, network: str, method: str, url: str) -> int:
        """Одноразовый curl с этим именем в этой сети; путь — как есть, без нормализации."""
        r = self._docker("run", "--rm", "--pull", "never", "--name", name, "--network", network,
                         "--label", ONESHOT_LABEL, "--read-only", "--cap-drop", "ALL",
                         "--security-opt", "no-new-privileges:true", locked_image("curlimages/curl"),
                         "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--path-as-is",
                         "--max-time", "10", "-X", method, url)
        return int(r.stdout.strip())

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

    def _sp_ro_ready(self) -> bool:
        # ping через прокси тем же путём, что сторож
        r = self._docker("exec", "pcbk-watchdog", "python", "-c", WATCHDOG_REQUEST,
                         "GET", "http://pcbk-sp-ro:2375/v1.44/_ping", check=False)
        return r.returncode == 0 and r.stdout.strip() == "200"

    def _state(self, name: str) -> dict:
        return self.inspect(name)["State"]

    def wait_ready(self, name: str, timeout: float = READY_TIMEOUT_S) -> None:
        """Готов — или явный отказ: контейнер вышел, либо срок истёк."""
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

    def up(self) -> None:
        self.compose("up", "-d", "--build")
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
        self.wait_status(lambda d: bool(d["checked_at"])
                         and datetime.fromisoformat(d["checked_at"]) > ready_at, READY_TIMEOUT_S)

    def down(self) -> None:
        # одноразовые клиенты http_as, оставшиеся после оборванного прогона, — только с нашей меткой
        leftovers = self._docker("ps", "-aq", "--filter", f"label={ONESHOT_LABEL}").stdout.split()
        if leftovers:
            self._docker("rm", "-f", *leftovers)
        self.compose("down", "-v", "--remove-orphans", "--timeout", "10")


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    prepare_images()
    work = tmp_path_factory.mktemp("pcbk-stack")
    make_test_tls(work / "tls")
    env_file = work / "test.env"
    env_file.write_text("\n".join([
        f"DOCKER_GID={docker_gid()}",
        "STU_NET=172.31",
        f"TLS_DIR={work / 'tls'}",
        "DISPLAY_TZ=Europe/Moscow",
        f"SECRETS_DIR={work / 'secrets'}",
        f"AGENTS_DIR={work / 'agents'}",
        "MEM_WARN_MIB=64",
        "MEM_FAIL_MIB=32",
    ]) + "\n", encoding="utf-8")
    s = Stack(env_file)
    s.refuse_foreign()
    s.down()   # остатки прошлого прогона — только своего проекта
    try:
        s.up()
        yield s
    finally:
        s.down()
