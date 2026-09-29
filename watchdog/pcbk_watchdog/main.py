"""Настройки, цикл проверок и HTTP-сервер сторожа."""
import http.client
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .checks import Check, check_container, check_http, check_memory, check_tls, not_installed
from .docker_api import CONTAINER_NAME, DockerReader, DockerUnavailable
from .journal import Event, Journal
from .page import Snapshot, is_stale, render_html, status_json

log = logging.getLogger("pcbk_watchdog")

# вид проверки → обязательные поля в components.json сверх id и title
KINDS: dict[str, tuple[str, ...]] = {
    "memory": (), "http": ("url",), "tls": ("host", "port"), "docker_ping": (),
    "container": ("container", "sleeping_ok"), "absent": (),
}
# виды без сети — их срок такта не отменяет
LOCAL_KINDS = frozenset({"memory", "absent"})
# имена, которые сторож пишет сам
RESERVED_IDS = frozenset({"journal", "watchdog"})
# срок ответа для проверок tls и http, с (на операцию сокета)
NET_CHECK_TIMEOUT_S = 3.0

STATUS_JS = (Path(__file__).parent / "static" / "status.js").read_bytes()
EVENTS_ON_PAGE = 20

CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@dataclass(frozen=True)
class Settings:
    LISTEN_PORT: int = 8090
    TICK_S: float = 10.0
    STALE_AFTER_S: int = 30
    DISPLAY_TZ: str = "UTC"
    DOCKER_URL: str = "http://pcbk-sp-ro:2375"
    DOCKER_TIMEOUT_S: float = 3.0
    MEM_WARN_MIB: int = 2048
    MEM_FAIL_MIB: int = 1024
    TLS_CAFILE: str = "/app/tls/cert.pem"
    JOURNAL_PATH: str = "/var/lib/pcbk-watchdog/journal.db"
    MEMINFO_PATH: str = "/proc/meminfo"
    COMPONENTS_PATH: str = "/app/components.json"
    # учения «цикл молчит при живом HTTP»: 1 — после первого такта цикл замирает
    DRILL_FREEZE_LOOP: bool = False

    def __post_init__(self):
        if self.TICK_S <= 0 or self.DOCKER_TIMEOUT_S <= 0:
            raise ValueError("TICK_S и DOCKER_TIMEOUT_S должны быть больше нуля")
        if self.tick_budget_s <= 0:
            raise ValueError("срок такта не больше нуля — снимок устареет между тактами: "
                             "увеличьте STALE_AFTER_S или уменьшите TICK_S и DOCKER_TIMEOUT_S")
        try:
            ZoneInfo(self.DISPLAY_TZ)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"DISPLAY_TZ: неизвестный пояс {self.DISPLAY_TZ!r}") from e

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.DISPLAY_TZ)

    @property
    def tick_budget_s(self) -> float:
        """Срок, после которого сетевые проверки такта не начинаются.

        checked_at — начало такта, период цикла — max(TICK_S, d), поэтому перед
        следующим снимком возраст прежнего доходит до max(TICK_S, d) + d. Он не
        больше STALE_AFTER_S, если длина такта d ≤ min(STALE − TICK, STALE / 2).
        Начатая до срока проверка идёт ещё до двух сроков сокета (соединение и
        чтение) — их запас вычитается.
        """
        socket_s = max(self.DOCKER_TIMEOUT_S, NET_CHECK_TIMEOUT_S)
        return min(self.STALE_AFTER_S - self.TICK_S, self.STALE_AFTER_S / 2) - 2 * socket_s

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            raw = env.get(f.name, "")
            if raw == "":
                continue
            if isinstance(f.default, bool):   # bool("0") — истина, поэтому только 0 или 1
                if raw not in ("0", "1"):
                    raise ValueError(f"{f.name}: ожидается 0 или 1, получено {raw!r}")
                values[f.name] = raw == "1"
                continue
            try:
                values[f.name] = type(f.default)(raw)
            except ValueError as e:
                raise ValueError(f"{f.name}: неверное значение {raw!r}") from e
        return cls(**values)


def load_components(path: str) -> list[dict]:
    """Ожидаемые компоненты по порядку страницы; ошибка в файле — отказ при старте."""
    with open(path, encoding="utf-8") as f:
        comps = json.load(f)
    seen = set()
    for c in comps:
        kind = c.get("kind")
        if kind not in KINDS:
            raise ValueError(f"components: неизвестный вид {kind!r}")
        missing = {"id", "title", *KINDS[kind]} - c.keys()
        if missing:
            raise ValueError(f"components: у {c.get('id')!r} нет полей {sorted(missing)}")
        if c["id"] in seen or c["id"] in RESERVED_IDS:
            raise ValueError(f"components: имя {c['id']!r} повторяется или занято")
        seen.add(c["id"])
        if kind == "container" and not CONTAINER_NAME.fullmatch(c["container"]):
            raise ValueError(f"components: чужое имя контейнера {c['container']!r}")
    return comps


# последняя записанная в лог ошибка по компоненту — чтобы не писать её каждый такт
_logged_errors: dict[str, str] = {}


def _log_error_once(component: str, e: BaseException) -> None:
    text = f"{type(e).__name__}: {e}"
    if _logged_errors.get(component) != text:
        _logged_errors[component] = text
        log.warning("%s: %s", component, text)


def _check_one(comp: dict, docker: DockerReader, settings: Settings, now: datetime) -> Check:
    cid, title, kind = comp["id"], comp["title"], comp["kind"]
    if kind == "memory":
        with open(settings.MEMINFO_PATH) as f:
            c = check_memory(f.read(), settings.MEM_WARN_MIB, settings.MEM_FAIL_MIB)
        return Check(cid, title, c.state, c.detail)
    if kind == "http":
        return check_http(cid, title, comp["url"], timeout=NET_CHECK_TIMEOUT_S)
    if kind == "tls":
        return check_tls(cid, title, comp["host"], comp["port"], settings.TLS_CAFILE, now,
                         timeout=NET_CHECK_TIMEOUT_S)
    if kind == "docker_ping":
        docker.ping()
        return Check(cid, title, "ok", "отвечает")
    if kind == "container":
        return check_container(cid, title, docker.inspect(comp["container"]),
                               sleeping_ok=comp["sleeping_ok"], now=now)
    if kind == "absent":
        return not_installed(cid, title)
    raise ValueError(f"неизвестный вид проверки: {kind!r}")


def run_checks(components: list[dict], docker: DockerReader, settings: Settings,
               now: datetime) -> list[Check]:
    """Все проверки такта; сбой одной не останавливает остальные."""
    deadline = time.monotonic() + settings.tick_budget_s
    docker_down = False
    checks = []
    for comp in components:
        cid, title, kind = comp["id"], comp["title"], comp["kind"]
        if kind == "container" and docker_down:
            # прокси уже не ответил в этом такте — не ждать его на каждом контейнере
            checks.append(Check(cid, title, "unknown", "нет связи с прокси сокета"))
            continue
        if kind not in LOCAL_KINDS and time.monotonic() > deadline:
            checks.append(Check(cid, title, "unknown", "проверка пропущена: такт не уложился в срок"))
            continue
        try:
            check = _check_one(comp, docker, settings, now)
            _logged_errors.pop(cid, None)
        except DockerUnavailable as e:
            docker_down = True
            _log_error_once(cid, e)
            check = (Check(cid, title, "fail", "не отвечает") if kind == "docker_ping"
                     else Check(cid, title, "unknown", "нет связи с прокси сокета"))
        except Exception as e:
            _log_error_once(cid, e)
            check = Check(cid, title, "unknown", f"ошибка проверки: {type(e).__name__}")
        checks.append(check)
    return checks


class WatchState:
    """Последний снимок: пишет цикл, читают потоки HTTP-сервера."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None

    @property
    def snapshot(self) -> Snapshot | None:
        with self._lock:
            return self._snapshot

    @snapshot.setter
    def snapshot(self, value: Snapshot | None) -> None:
        with self._lock:
            self._snapshot = value


class LazyJournal:
    """Журнал открывается при первой удачной попытке: сбой открытия не роняет сторожа."""

    def __init__(self, path: str):
        self._path = path
        self._journal: Journal | None = None
        self._lock = threading.Lock()

    def _open(self) -> Journal:
        with self._lock:
            if self._journal is None:
                self._journal = Journal(self._path)
            return self._journal

    def started(self, now: datetime) -> Event:
        return self._open().started(now)

    def probe(self) -> None:
        self._open().probe()

    def record(self, checks: list[Check], now: datetime) -> list[Event]:
        return self._open().record(checks, now)

    def recent(self, limit: int = 50) -> list[Event]:
        journal = self._journal   # страница журнал не открывает — только цикл
        return journal.recent(limit) if journal else []


def _journal_fail(e: BaseException) -> Check:
    return Check("journal", "Журнал сторожа", "fail", type(e).__name__)


def tick(state: WatchState, journal: Journal, components: list[dict], docker: DockerReader,
         settings: Settings, now: datetime) -> None:
    """Одна итерация цикла: проверки → снимок → журнал переходов."""
    checks = run_checks(components, docker, settings, now)
    try:
        journal.probe()
        _logged_errors.pop("journal", None)
    except Exception as e:
        _log_error_once("journal", e)
        checks.append(_journal_fail(e))
    state.snapshot = Snapshot(now, tuple(checks))   # снимок — до записи в журнал
    try:
        # «ещё не установлен» — настройка, не событие; строка журнала — о самом журнале
        events = journal.record([c for c in checks if c.state != "absent"
                                 and c.component != "journal"], now)
    except Exception as e:
        _log_error_once("journal", e)
        if not any(c.component == "journal" for c in checks):
            state.snapshot = Snapshot(now, (*checks, _journal_fail(e)))
        return
    for e in events:
        log.info("%s: %s %s", e.component, e.state, e.detail)


def run_loop(state: WatchState, journal: Journal, components: list[dict], docker: DockerReader,
             settings: Settings, stop: threading.Event) -> None:
    if settings.DRILL_FREEZE_LOOP:
        log.warning("учения DRILL_FREEZE_LOOP=1: после первого такта цикл замрёт, HTTP продолжит отвечать")
    while not stop.is_set():
        started = time.monotonic()
        try:
            tick(state, journal, components, docker, settings, datetime.now(settings.tz))
        except Exception:
            log.exception("такт упал")
        if settings.DRILL_FREEZE_LOOP:
            stop.wait()   # учения: снимок стареет, страница должна показать полосу молчания
            return
        stop.wait(max(0.0, settings.TICK_S - (time.monotonic() - started)))


def make_server(state: WatchState, journal: Journal, settings: Settings,
                host: str = "") -> ThreadingHTTPServer:
    """GET /status, /status.json, /status.js, /healthz; stale — в момент запроса."""

    class Handler(BaseHTTPRequestHandler):
        timeout = 10   # медленный клиент не держит поток дольше

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            now = datetime.now(settings.tz)
            snapshot = state.snapshot
            if path == "/status":
                try:
                    events = journal.recent(EVENTS_ON_PAGE)
                except Exception as e:
                    _log_error_once("journal-read", e)
                    events = []
                html = render_html(snapshot, events, now, settings.STALE_AFTER_S, settings.tz)
                self._send(200, "text/html; charset=utf-8", html.encode(), csp=True)
            elif path == "/status.json":
                data = status_json(snapshot, now, settings.STALE_AFTER_S)
                self._send(503 if snapshot is None else 200, "application/json; charset=utf-8",
                           json.dumps(data, ensure_ascii=False).encode())
            elif path == "/status.js":
                self._send(200, "text/javascript; charset=utf-8", STATUS_JS)
            elif path == "/healthz":
                fresh = not is_stale(snapshot, now, settings.STALE_AFTER_S)
                self._send(200 if fresh else 503, "text/plain; charset=utf-8",
                           b"ok\n" if fresh else b"stale\n")
            else:
                self._send(404, "text/plain; charset=utf-8", "не найдено\n".encode())

        def _send(self, code: int, ctype: str, body: bytes, csp: bool = False) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if csp:
                self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def version_string(self) -> str:
            return "pcbk-watchdog"

        def log_message(self, fmt, *args):
            pass   # страницу опрашивают каждые 5 с — доступ в лог не пишем

    return ThreadingHTTPServer((host, settings.LISTEN_PORT), Handler)


def healthcheck(settings: Settings) -> int:
    """Для HEALTHCHECK образа: 0 — снимок свежий."""
    conn = http.client.HTTPConnection("127.0.0.1", settings.LISTEN_PORT, timeout=5)
    try:
        conn.request("GET", "/healthz")
        return 0 if conn.getresponse().status == 200 else 1
    except (OSError, http.client.HTTPException):
        return 1
    finally:
        conn.close()


def _exit_now(signum, frame):
    raise SystemExit(0)   # SIGTERM — быстрый выход; SQLite откатит незавершённое


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    settings = Settings.from_env()
    if argv == ["--healthcheck"]:
        return healthcheck(settings)
    signal.signal(signal.SIGTERM, _exit_now)   # PID 1 без обработчика SIGTERM не получает
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    components = load_components(settings.COMPONENTS_PATH)
    journal = LazyJournal(settings.JOURNAL_PATH)
    try:
        journal.started(datetime.now(settings.tz))
    except Exception as e:
        _log_error_once("journal", e)   # сбой покажет первый такт
    docker = DockerReader(settings.DOCKER_URL, settings.DOCKER_TIMEOUT_S)
    state = WatchState()
    server = make_server(state, journal, settings)
    stop = threading.Event()
    threading.Thread(target=run_loop, args=(state, journal, components, docker, settings, stop),
                     name="tick", daemon=True).start()
    log.info("сторож слушает порт %s, такт %s с, срок устаревания %s с",
             settings.LISTEN_PORT, settings.TICK_S, settings.STALE_AFTER_S)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
