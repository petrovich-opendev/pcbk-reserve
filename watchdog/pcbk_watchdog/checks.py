"""Чистые функции проверок сторожа: память, контейнеры, HTTP, сертификат, свежесть историана."""
import http.client
import json
import re
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
# текст постоянный: с числом перезапусков журнал писал бы событие на каждый
CRASH_LOOP_DETAIL = "падает в цикле (перезапуски подряд)"
# проверка здоровья места: интервал 30 с + срок 5 с с запасом
HEALTH_SILENCE = timedelta(minutes=2)
HEALTH_SILENT_DETAIL = "проверка здоровья молчит"
UNHEALTHY_DETAIL = "OpenCode не отвечает"
# пути каталога выкладки в State.Error (секреты мест) на страницу не выводим
HOST_PATH = re.compile(r"/opt/pcbk-reserve[^\s\"':]*")
# рабочее место спит: вышло само, SIGKILL или SIGTERM
SLEEP_EXIT_CODES = frozenset({0, 137, 143})
# коды OpenSSL X509_V_ERR_CERT_NOT_YET_VALID и X509_V_ERR_CERT_HAS_EXPIRED
CERT_NOT_YET_VALID = 9
CERT_HAS_EXPIRED = 10
# срок ответа для сетевых проверок, с (на операцию сокета)
NET_CHECK_TIMEOUT_S = 3.0
# тело ответа ручки здоровья читаем не больше этого
MAX_BODY_BYTES = 64 * 1024
# причину из ответа на страницу — не длиннее
REASON_CHARS = 80
HTTP_OK_DETAIL = "отвечает"
# каталог тегов старше двух суток — предупреждение
CATALOG_MAX_AGE_S = 172800
CORE_DOWN_DETAIL = "служба данных не отвечает — свежесть неизвестна"


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


def check_container(component: str, title: str, inspect: dict | None, *,
                    sleeping_ok: bool, now: datetime) -> Check:
    """Вердикт по inspect Docker; порядок правил — таблица задачи 1 Д1 и задачи 2 Д2."""
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
            return result("fail", CRASH_LOOP_DETAIL)
        if st["Restarting"]:
            return result("warn", "перезапускается")
        health = st.get("Health")
        if health:   # у остановленного Health прошлого запуска не смотрим
            # монитор Docker ждёт exec без срока: заклинившая проверка молчит
            ends = [datetime.fromisoformat(e["End"]) for e in health.get("Log") or []]
            if now - max([started, *ends]) > HEALTH_SILENCE:
                return result("fail", HEALTH_SILENT_DETAIL)
            if health.get("Status") == "unhealthy":
                return result("fail", UNHEALTHY_DETAIL)
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
        # сперва маска, потом обрезка: иначе уцелело бы начало пути
        return result("fail", f"не запускается: {HOST_PATH.sub('…', error)[:80]}")
    code = st["ExitCode"]
    if sleeping_ok and code in SLEEP_EXIT_CODES:
        return result("ok", "спит")
    return result("fail", f"остановлен, код {code}")


def _reason(text: str) -> str:
    """Причина из ответа для страницы: без путей каталога выкладки, первые 80 знаков."""
    return HOST_PATH.sub("…", text.strip())[:REASON_CHARS]


def _http_get(url: str, timeout: float, read_body) -> tuple[int, bytes]:
    """GET без перенаправлений и без прокси из окружения.

    Тело читается (не больше MAX_BODY_BYTES), только если read_body(код) истинно;
    сбой чтения тела при известном коде — пустое тело. Нет связи — OSError или
    HTTPException наружу.
    """
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        raise ValueError(f"нужен адрес http://, получен {url!r}")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = b""
        if read_body(resp.status):
            try:
                body = resp.read(MAX_BODY_BYTES)
            except (OSError, http.client.HTTPException):
                pass
        return resp.status, body
    finally:
        conn.close()


def _json_object(body: bytes) -> dict | None:
    try:
        obj = json.loads(body)
    except ValueError:   # в том числе UnicodeDecodeError
        return None
    return obj if isinstance(obj, dict) else None


def check_http(component: str, title: str, url: str, timeout: float = 3.0,
               ok_detail: str = HTTP_OK_DETAIL) -> Check:
    """2xx — ok с ok_detail; иначе fail с причиной из JSON-поля detail, если она есть."""
    try:
        status, body = _http_get(url, timeout, lambda code: not 200 <= code < 300)
    except (OSError, http.client.HTTPException):
        return Check(component, title, "fail", "не отвечает")
    if 200 <= status < 300:
        return Check(component, title, "ok", ok_detail)
    detail = (_json_object(body) or {}).get("detail")
    if isinstance(detail, str) and detail.strip():
        return Check(component, title, "fail", _reason(detail))
    return Check(component, title, "fail", f"отвечает ошибкой HTTP {status}")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def historian_verdict(reply: dict, now: datetime, warn_s: int, fail_s: int,
                      stale_s: int) -> tuple[State, str]:
    """Вердикт по ответу /health/historian; первая подходящая строка таблицы задачи 6 Д3а.

    Ответ не по договору — ValueError, TypeError или KeyError.
    """
    checked_raw = reply["checked_at"]
    if checked_raw is None:
        return "unknown", "служба ещё не опрашивала историан"
    if not isinstance(checked_raw, str):
        raise TypeError("checked_at — не строка")
    checked_at = datetime.fromisoformat(checked_raw)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)   # договор — ISO UTC
    if (now - checked_at).total_seconds() > stale_s:
        return "unknown", "служба давно не опрашивала историан"
    if reply.get("error"):
        text = reply.get("error_text")
        return "fail", _reason(text if isinstance(text, str) and text.strip() else str(reply["error"]))
    age = reply["age_s"]
    if not _is_number(age):
        raise TypeError("age_s — не число")
    # в warn/fail текст постоянный: иначе журнал пишет событие каждый такт
    if age >= fail_s:
        return "fail", f"последняя метка старше {fail_s} с"
    if age >= warn_s:
        return "warn", f"последняя метка старше {warn_s} с"
    catalog_age = reply.get("catalog_age_s")
    if catalog_age is not None and not _is_number(catalog_age):
        raise TypeError("catalog_age_s — не число")
    if catalog_age is not None and catalog_age > CATALOG_MAX_AGE_S:
        return "warn", "каталог тегов старше двух суток"
    return "ok", f"последняя метка {round(age)} с назад"


def check_historian(component: str, title: str, url: str, now: datetime, warn_s: int, fail_s: int,
                    stale_s: int, timeout: float = NET_CHECK_TIMEOUT_S) -> Check:
    """Свежесть историана по ручке службы данных; сама служба историан не опрашивает заново."""
    try:
        status, body = _http_get(url, timeout, lambda code: code == 200)
    except (OSError, http.client.HTTPException):
        return Check(component, title, "unknown", CORE_DOWN_DETAIL)
    reply = _json_object(body) if status == 200 else None
    if reply is None:
        return Check(component, title, "unknown", CORE_DOWN_DETAIL)
    try:
        state, detail = historian_verdict(reply, now, warn_s, fail_s, stale_s)
    except (KeyError, TypeError, ValueError):
        return Check(component, title, "unknown", CORE_DOWN_DETAIL)   # ответ не по договору
    return Check(component, title, state, detail)


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
