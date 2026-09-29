"""Общие константы и помощники тестов сторожа (импорт явный: from helpers import …)."""
import atexit
import http.client
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from pcbk_watchdog.main import Settings, make_server

# фиксированное время тестов, UTC
T0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def insp(running=False, oom=False, code=0, restarts=0, restarting=False, paused=False,
         error="", started=T0 - timedelta(hours=1), health: str | None = None,
         last_end: datetime | None = T0 - timedelta(seconds=10)):
    """Ответ Docker inspect в объёме, который читает сторож.

    health — State.Health.Status; last_end — конец последней проверки здоровья,
    None — журнал проверок пуст.
    """
    state = {"Running": running, "Paused": paused, "OOMKilled": oom, "ExitCode": code,
             "Restarting": restarting, "Error": error, "StartedAt": started.isoformat()}
    if health is not None:
        log = [] if last_end is None else [
            {"Start": (last_end - timedelta(seconds=1)).isoformat(), "End": last_end.isoformat(),
             "ExitCode": 0 if health == "healthy" else 1, "Output": "200\n"}]
        state["Health"] = {"Status": health, "FailingStreak": 3 if health == "unhealthy" else 0,
                           "Log": log}
    return {"State": state, "RestartCount": restarts}


# /proc/meminfo для тестов: памяти с запасом; файл удаляется при выходе
_fd, MEMINFO_FILE = tempfile.mkstemp(prefix="pcbk-meminfo-")
with os.fdopen(_fd, "w") as _f:
    _f.write("MemTotal: 16384000 kB\nMemAvailable: 12000000 kB\n")
atexit.register(os.remove, MEMINFO_FILE)

SETTINGS = Settings(MEMINFO_PATH=MEMINFO_FILE)

CHROME = shutil.which("google-chrome")


class NoJournal:
    """Журнал без событий — для сервера в тестах, где события не важны."""

    def recent(self, limit: int = 50) -> list:
        return []


def start_test_server(state, journal=None, settings=SETTINGS):
    """Сервер сторожа на 127.0.0.1 и свободном порту; гасится при выходе из pytest."""
    srv = make_server(state, journal or NoJournal(), replace(settings, LISTEN_PORT=0),
                      host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
    atexit.register(srv.server_close)
    atexit.register(srv.shutdown)
    return srv


def http_get(srv, path: str) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def http_status(srv, path: str) -> int:
    return http_get(srv, path)[0]


def chrome_dom(url: str, virtual_time_ms: int) -> str:
    """DOM страницы после работы её скриптов в безголовом Chrome."""
    with tempfile.TemporaryDirectory(prefix="pcbk-chrome-") as profile:
        out = subprocess.run(
            [CHROME, "--headless=new", "--dump-dom", f"--virtual-time-budget={virtual_time_ms}",
             "--no-sandbox", "--disable-gpu", "--no-proxy-server", "--no-first-run",
             f"--user-data-dir={profile}", url],
            capture_output=True, text=True, timeout=90, check=True)
    return out.stdout
