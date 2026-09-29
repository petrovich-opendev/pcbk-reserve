"""Чистые функции проверок сторожа: память, контейнеры, HTTP, сертификат."""
import http.client
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

State = Literal["ok", "warn", "fail", "absent", "unknown"]

# запуск моложе этого считается свежим перезапуском
RECENT_RESTART = timedelta(minutes=15)
# столько перезапусков при свежем запуске — цикл падений
CRASH_LOOP_RESTARTS = 3
# рабочее место спит: вышло само, SIGKILL или SIGTERM
SLEEP_EXIT_CODES = frozenset({0, 137, 143})
# коды OpenSSL X509_V_ERR_CERT_NOT_YET_VALID и X509_V_ERR_CERT_HAS_EXPIRED
CERT_NOT_YET_VALID = 9
CERT_HAS_EXPIRED = 10


@dataclass(frozen=True)
class Check:
    component: str
    title: str
    state: State
    detail: str


def not_installed(component: str, title: str) -> Check:
    return Check(component, title, "absent", "ещё не установлен")


def check_memory(meminfo: str, warn_mib: int = 2048, fail_mib: int = 1024) -> Check:
    """Свободная память по полю MemAvailable из /proc/meminfo."""
    title = "Память сервера"
    for line in meminfo.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "MemAvailable":
            mib = int(value.split()[0]) // 1024
            break
    else:
        return Check("memory", title, "unknown", "нет данных о свободной памяти")
    # в warn/fail текст постоянный: иначе журнал пишет событие каждый такт
    if mib < fail_mib:
        return Check("memory", title, "fail", f"свободно меньше {fail_mib} МиБ")
    if mib < warn_mib:
        return Check("memory", title, "warn", f"свободно меньше {warn_mib} МиБ")
    return Check("memory", title, "ok", f"свободно {mib} МиБ")


def _restarts(n: int) -> str:
    """«N перезапусков» с верным окончанием."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} перезапуск"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} перезапуска"
    return f"{n} перезапусков"


def check_container(component: str, title: str, inspect: dict | None, *,
                    sleeping_ok: bool, now: datetime) -> Check:
    """Вердикт по inspect Docker; порядок правил — таблица задачи 1."""
    def result(state: State, detail: str) -> Check:
        return Check(component, title, state, detail)

    if inspect is None:
        return result("fail", "нет контейнера")
    st = inspect["State"]
    restarts = inspect["RestartCount"]   # поле верхнего уровня, не State
    started = datetime.fromisoformat(st["StartedAt"])
    recent = now - started < RECENT_RESTART

    if st["Paused"]:
        return result("fail", "приостановлен")
    if st["Running"] or st["Restarting"]:
        if restarts >= CRASH_LOOP_RESTARTS and recent:
            return result("fail", f"падает в цикле: {_restarts(restarts)}")
        if st["Restarting"]:
            return result("warn", "перезапускается")
        if restarts > 0 and recent:
            # время — в поясе now и с явным смещением
            at = started.astimezone(now.tzinfo).strftime("%H:%M UTC%:z")
            return result("warn", f"перезапущен после сбоя в {at} ({restarts} с последнего запуска)")
        if restarts > 0:
            return result("ok", f"работает, сбоев с последнего запуска: {restarts}")
        return result("ok", "работает")

    if st["OOMKilled"]:
        return result("fail", "убит по памяти")
    error = st.get("Error") or ""
    if error:
        return result("fail", f"не запускается: {error[:80]}")
    code = st["ExitCode"]
    if sleeping_ok and code in SLEEP_EXIT_CODES:
        return result("ok", "спит")
    return result("fail", f"остановлен, код {code}")


def check_http(component: str, title: str, url: str, timeout: float = 3.0) -> Check:
    """GET без перенаправлений и без прокси из окружения; 2xx — ok."""
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        raise ValueError(f"check_http: нужен адрес http://, получен {url!r}")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.request("GET", path)
        status = conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return Check(component, title, "fail", "не отвечает")
    finally:
        conn.close()
    if 200 <= status < 300:
        return Check(component, title, "ok", "отвечает")
    return Check(component, title, "fail", f"отвечает ошибкой HTTP {status}")


def tls_verdict(not_after: datetime, now: datetime, warn_days: int = 14) -> tuple[State, str]:
    date = not_after.strftime("%d.%m.%Y")
    if not_after <= now:
        return "fail", f"сертификат истёк {date}"
    days = (not_after - now).days
    return ("warn" if days < warn_days else "ok"), f"сертификат до {date} ({days} дн.)"


def check_tls(component: str, title: str, host: str, port: int, cafile: str, now: datetime,
              timeout: float = 3.0) -> Check:
    """Рукопожатие с доверием только к cafile (тот же cert.pem, что у edge), без проверки имени."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.load_verify_locations(cafile)   # нет файла — ошибка проверки, а не «не отвечает»
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                not_after_text = tls.getpeercert()["notAfter"]
    except ssl.SSLCertVerificationError as e:
        if e.verify_code == CERT_HAS_EXPIRED:
            return Check(component, title, "fail", "сертификат истёк")
        if e.verify_code == CERT_NOT_YET_VALID:
            return Check(component, title, "fail", "сертификат ещё не действителен")
        return Check(component, title, "fail", "отдаёт не тот сертификат")
    except OSError:
        return Check(component, title, "fail", "не отвечает")
    not_after = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after_text), timezone.utc)
    state, detail = tls_verdict(not_after, now)
    return Check(component, title, state, detail)
