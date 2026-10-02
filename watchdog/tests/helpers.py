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
         last_end: datetime | None = T0 - timedelta(seconds=10), output: str = "200\n",
         log: list[str] | None = None, log_step_s: float = 30):
    """Ответ Docker inspect в объёме, который читает сторож.

    health — State.Health.Status; last_end — конец последней проверки здоровья,
    None — журнал проверок пуст. output — вывод единственной записи журнала; log — весь
    Health.Log списком выводов, новые — последними: End последней — last_end, у каждой
    предыдущей — на log_step_s раньше.
    """
    state = {"Running": running, "Paused": paused, "OOMKilled": oom, "ExitCode": code,
             "Restarting": restarting, "Error": error, "StartedAt": started.isoformat()}
    if health is not None:
        outputs = [] if last_end is None else [output] if log is None else log
        entries = []
        for back, out in enumerate(reversed(outputs)):
            end = last_end - timedelta(seconds=back * log_step_s)
            entries.insert(0, {"Start": (end - timedelta(seconds=1)).isoformat(), "End": end.isoformat(),
                               "ExitCode": 0 if health == "healthy" else 1, "Output": out})
        state["Health"] = {"Status": health, "FailingStreak": 3 if health == "unhealthy" else 0,
                           "Log": entries}
    return {"State": state, "RestartCount": restarts}


def llm_obj(**kw):
    """Ответ /health/llm (договор задачи 6 Д4а, 30 ключей — сверяет тест стыка): всё в норме, расход $12.3456."""
    base = {"checked_at": (T0 - timedelta(seconds=10)).isoformat(), "config_error": None, "tokens_error": None,
            "stopped": False, "key": "ok",
            "mgmt_key": "missing", "drill": "", "ledger_ok": True, "ledger_error": None,
            "upstream": {"state": "ok", "text": "OpenRouter отвечает", "since": None},
            "spent_usd": 12.3456, "reserved_usd": 0.0, "ledger_usd": 12.3456, "estimated_usd": 0.0, "key_spent_usd": None,
            "key_usage_usd": 12.3, "total_usd": 1000.0, "ops_usd": 0.0, "shares": {"over_80": 0, "spent": 0},
            "pending": 0, "pending_stuck": 0, "cost_unknown_24h": 0, "provider_mismatch_24h": 0,
            "account_remaining_usd": None, "models": ["z-ai/glm-5.3"],
            "ledger_queue": 0, "ledger_fresh": False, "ledger_reset": False,
            "egress": {"alive": True, "last_ok_age_s": 12.0}, "refresh_started_age_s": 3600.0}
    return {**base, **kw}


def gw(age=5, **kw):
    """Ответ /health/gateway (договор Д5а, 15 ключей): уборщик прошёл age с назад, всё в норме."""
    return {"checked_at": (T0 - timedelta(seconds=age)).isoformat(), "config_error": None, "store_ok": True,
            "sp_ctl": "ok", "running_n": 1, "ready_n": 1, "wake_ok_1h": 1, "wake_failed_1h": 0, "stop_failed_1h": 0,
            "place_auth_failed_1h": 0, "logins_failed_1h": 0, "origin_refused_1h": 0, "login_busy_1h": 0,
            "cookies_duplicate_1h": 0,
            "drill": "", **kw}


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
