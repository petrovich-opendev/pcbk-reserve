"""Общие константы и помощники тестов сторожа (импорт явный: from helpers import …)."""
from datetime import datetime, timedelta, timezone

# фиксированное время тестов, UTC
T0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def insp(running=False, oom=False, code=0, restarts=0, restarting=False, paused=False,
         error="", started=T0 - timedelta(hours=1)):
    """Ответ Docker inspect в объёме, который читает сторож."""
    return {"State": {"Running": running, "Paused": paused, "OOMKilled": oom, "ExitCode": code,
                      "Restarting": restarting, "Error": error,
                      "StartedAt": started.isoformat()}, "RestartCount": restarts}
