"""Чистые функции проверок сторожа: память, контейнеры, HTTP, сертификат, свежесть историана,
LLM-прокси и бюджет, шлюз и вход, веб-чат, агенты студентов."""
import http.client
import io
import json
import math
import re
import socket
import ssl
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import Literal
from urllib.parse import urljoin, urlsplit

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
# MCP места (Д4б): проверка здоровья образа :d4 печатает «200 mcp=<слово>», образ :d2 — только код
MCP_WORD = re.compile(r"\bmcp=([a-z_]+)")
# слова, при которых связь не потеряна: unknown — ответ /mcp не прочитан, disabled — MCP выключен
MCP_NOT_DOWN = frozenset({"connected", "unknown", "disabled"})
MCP_DOWN_DETAIL = "нет связи со службой данных (MCP)"
# столько записей Health.Log хранит Docker; все с mcp=unknown и старейшая кончилась раньше
# MCP_UNKNOWN_S назад — «внимание». На старте (start-interval 2 с, ленивый подъём MCP под gVisor)
# пять unknown набираются за 10 с — это норма (К30). Записи прошлого запуска (End раньше StartedAt)
# строки MCP не считают: Docker хранит журнал и через сон места
HEALTH_LOG_KEEP = 5
MCP_UNKNOWN_S = 120
MCP_UNKNOWN_DETAIL = "состояние MCP не читается"
# пути каталога выкладки в State.Error (секреты мест) на страницу не выводим
HOST_PATH = re.compile(r"/opt/pcbk-reserve[^\s\"':]*")
# рабочее место спит: вышло само, SIGKILL или SIGTERM
SLEEP_EXIT_CODES = frozenset({0, 137, 143})
# коды OpenSSL X509_V_ERR_CERT_NOT_YET_VALID и X509_V_ERR_CERT_HAS_EXPIRED
CERT_NOT_YET_VALID = 9
CERT_HAS_EXPIRED = 10
# срок ответа для сетевых проверок, с (на операцию сокета)
NET_CHECK_TIMEOUT_S = 3.0
# HTTP-проверка целиком (соединение, запрос, заголовки, тело) — не дольше стольких
# сроков сокета: столько на начатую проверку закладывает правило срока такта
HTTP_CHECK_SPANS = 2
# тело ответа ручки здоровья читаем не больше этого
MAX_BODY_BYTES = 64 * 1024
# причину из ответа на страницу — не длиннее
REASON_CHARS = 80
HTTP_OK_DETAIL = "отвечает"
# каталог тегов старше двух суток — предупреждение
CATALOG_MAX_AGE_S = 172800
CORE_DOWN_DETAIL = "служба данных не отвечает — свежесть неизвестна"
OFF_CONTRACT_DETAIL = "служба данных ответила не по договору — свежесть неизвестна"
# нагрузка историана и ошибки инструментов (Д4б-R22): поля gate и tools ответа службы Д4б
SLOW_P95_MS = 5000
TOOL_ERRORS_SHARE = 0.2
TOOL_ERRORS_MIN_CALLS = 10
USER_PAUSED_DETAIL = "запросы людей к историану на паузе после сроков подряд"
# порог — из SLOW_P95_MS: при его смене текст не соврёт (5000 → «5 с», 5500 → «5,5 с»)
SLOW_P95_DETAIL = f"историан отвечает медленно: p95 больше {format(SLOW_P95_MS / 1000, 'g').replace('.', ',')} с"
TOOL_ERRORS_DETAIL = "инструменты отвечают ошибкой"

# строка «LLM-прокси и бюджет» (задача 7 Д4а)
LLM_DOWN_DETAIL = "LLM-прокси не отвечает"
LLM_OFF_CONTRACT_DETAIL = "LLM-прокси ответил не по договору"
# опрос OpenRouter старше этого, с — «неизвестно». Опрос раз в LLM_POLL_S (60 с), в нём
# до двух запросов по POLL_TIMEOUT_S (15 с), такт сторожа 10 с и запас 60 с на медленный
# старт: 60 + 2 × 15 + 10 + 60 = 160 ≤ 180.
# Долг (ревью задачи 7, M6): LLM_POLL_S — настройка core (LlmSettings), а здесь число; при
# смене опроса core поправить и эту константу, иначе строка уйдёт в «неизвестно» или опоздает.
LLM_STALE_S = 180
# десять кругов обновления адресов охраны выхода по 30 с (Д4а-R18): дольше без удачного
# круга — предупреждение
EGRESS_STALE_S = 300
# остаток на счёте OpenRouter меньше этого — предупреждение
ACCOUNT_WARN_USD = 20.0
# OpenRouter насчитал по ключу больше, чем учёл прокси, на столько — предупреждение
DRIFT_WARN_USD = 1.0
# полосы бюджета, %: «израсходовано больше P %», первая подходящая
BUDGET_STEPS = (95, 80, 50)
# состояния OpenRouter по договору /health/llm (UPSTREAM_TEXTS задачи 6 Д4а)
UPSTREAM_FAIL_STATES = frozenset({"unreachable", "key_rejected", "money_out", "key_limit", "region"})
UPSTREAM_STATES = UPSTREAM_FAIL_STATES | {"unknown", "ok", "errors"}
# разность сумм меньше этого — шум сложения чисел с плавающей точкой, не деньги
USD_NOISE = 1e-9
# меньше этого сумма пишется «<$0.0001»: четыре знака дали бы «$0.0000» при ненулевом расходе
USD_TINY = Decimal("0.0001")
# что пишет core в ledger_error (задача 6 Д4а): dropped — операция журнала брошена, write — запись не идёт
LEDGER_ERRORS = frozenset({"dropped", "write"})
# прогон отбора модели (Д8-R69): блок run ответа /health/llm — null или ровно эти ключи; id — как LLM_RUN_ID core
RUN_KEYS = frozenset({"id", "cap_usd", "spent_usd", "callers", "calls_10m", "idle_s"})
RUN_ID_RE = re.compile(r"[a-z0-9-]{1,32}")
# к модели прогона не обращались дольше этого, с — «прогон отбора молчит» (умолчание Settings.LLM_RUN_SILENT_S)
LLM_RUN_SILENT_S = 300
RUN_CAP_DETAIL = "потолок прогона отбора исчерпан — вызовы прогона получают отказ"
RUN_SILENT_DETAIL = "прогон отбора молчит — к модели давно никто не обращался"
# потолок прогона меньше этого, $ — в note с центами ($5.00), иначе целым, как бюджет ($80)
RUN_CAP_CENTS_BELOW_USD = 10.0

# строка «Шлюз и вход» (задача А1 Д5а, Д5-R29); счётчики /health/gateway — за час в памяти core
GW_DOWN_DETAIL = "шлюз не отвечает"
GW_OFF_CONTRACT_DETAIL = "шлюз ответил не по договору"
# столько неудачных пробуждений за час без единой удачи — места не поднимаются вовсе
WAKE_FAILS_FAIL = 3
# столько неверных паролей за час — предупреждение
LOGIN_FAILS_WARN = 30
# договор /health/gateway (задача А3 Д5а): sp_ctl — Literal["ok", "down", "unknown"];
# drill — "" или режим DRILL_GATEWAY. Прочее — ответ не по договору
GW_SP_CTL_STATES = frozenset({"ok", "down", "unknown"})
GW_DRILL_MODES = frozenset({"freeze_reaper", "bad_place_pw"})
# ключи ответа: 15 Д5а обязательны; четыре ключа прокси Д5б (Б1) — все вместе или ни одного
# (core :d5a во время выкладки); любой другой ключ — ответ не по договору
GW_KEYS_D5A = frozenset({"checked_at", "config_error", "store_ok", "sp_ctl", "running_n", "ready_n", "wake_ok_1h",
                         "wake_failed_1h", "stop_failed_1h", "place_auth_failed_1h", "logins_failed_1h",
                         "origin_refused_1h", "login_busy_1h", "cookies_duplicate_1h", "drill"})
GW_KEYS_D5B = frozenset({"refused_1h", "streams", "stream_broken_1h", "upstream_failed_1h"})

# строка «Веб-чат» (задача А1 Д6а, Д6-R46): страница чата и опись сборки build.json через внутренний 8080 edge.
# Договор описи с задачей А2 (плагин pcbkBuildInfo и check-dist): {"build": str, "files": {путь: sha256 hex}};
# метка страницы — ровно в форме WEB_META_RE; путь — WEB_FILE_RE целиком; записей — от 1 до WEB_MAX_FILES
WEB_MAX_FILES = 8
WEB_META_RE = re.compile(r'<meta name="pcbk-build" content="([A-Za-z0-9._-]{1,64})">')
# assets/* сборки и загрузчик Д6б boot.js из public/ (сторож Д6а его уже понимает)
WEB_FILE_RE = re.compile(r"assets/[A-Za-z0-9._-]{1,100}|boot\.js")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
WEB_DOWN_DETAIL = "не отвечает"
WEB_PAGE_DETAIL = "страница чата не отдаётся"
WEB_PARTIAL_DETAIL = "сборка чата неполная"
WEB_OK_DETAIL = "страница чата отдаётся"
WEB_BAD_MANIFEST = "опись неверна"
# отчёты браузеров (задача Б1 Д6б, Д6-R50): ответ /health/web core — ровно эти четыре счётчика за час; строки
# warn — в порядке проверки, первая с числом больше нуля; нет ответа, срок или не по договору — ok с пометкой
WEB_REPORT_ROWS = (("boot_failed_1h", "чат не запускается в браузере"),
                   ("ui_crash_1h", "чат падает в браузере"),
                   ("turn_silent_1h", "ответ модели не пришёл вовремя"),
                   ("stream_down_1h", "браузеры теряют связь с сервером"))
WEB_NO_REPORTS = "отчёты браузеров недоступны"

# строка «Агенты студентов» (задача А1 Д7а, Д7-R60): ответ /health/agents core — ровно эти 15 ключей; drill — "" или
# режим DRILL_AGENTS. Отдельная ручка, а не ключи /health/gateway: его договор строгий (лишний ключ — не по договору)
AGENTS_KEYS = frozenset({"checked_at", "store_ok", "dir_error", "pending", "pending_oldest_s", "saved_1h", "refused_1h",
                         "unavailable_1h", "visible_1h", "not_visible_1h", "quarantined_1h", "reload_failed_1h",
                         "reload_delayed_1h", "image_config_1h", "drill"})
AGENTS_DRILL_MODES = frozenset({"freeze_reload"})
AGENTS_DOWN_DETAIL = "служба агентов не отвечает"
AGENTS_NOT_DEPLOYED_DETAIL = "служба агентов ещё не выложена"       # 404: core старше сторожа (выкладка)
AGENTS_OFF_CONTRACT_DETAIL = "служба агентов ответила не по договору"
# агент ждёт перезагрузки места дольше стольких секунд — «внимание» (RELOAD_DELAYED_S core, Д7-R58)
AGENTS_DELAYED_S = 900
# столько отказов save_agent за час без единого сохранения — «внимание»
AGENTS_REFUSED_WARN = 6


@dataclass(frozen=True)
class Check:
    component: str
    title: str
    state: State
    detail: str
    # меняющиеся числа строки (расход, остаток): в журнал не идут и перехода не делают,
    # поэтому предел 80 знаков на них не действует
    note: str = ""


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
                    sleeping_ok: bool, now: datetime, unhealthy_detail: str = UNHEALTHY_DETAIL) -> Check:
    """Вердикт по inspect Docker; порядок правил — таблица задачи 1 Д1, задачи 2 Д2 и задачи 3 Д4б.

    unhealthy_detail — текст строки при Health.Status unhealthy: по умолчанию — про OpenCode места,
    у контейнеров с другой проверкой здоровья (core) — свой из components.json.
    """
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
                return result("fail", unhealthy_detail)
            if health.get("Status") == "healthy":
                mcp = _mcp_detail(health.get("Log") or [], started, now)
                if mcp is not None:
                    return result("warn", mcp)
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


def _mcp_word(entry: dict) -> str | None:
    found = MCP_WORD.search(entry.get("Output") or "")
    return found.group(1) if found else None


def _mcp_detail(log: list[dict], started: datetime, now: datetime) -> str | None:
    """Строки MCP места (Д4б) по Health.Log, где Docker пишет новые записи последними.

    Считаются только записи этого запуска (End не раньше StartedAt): журнал прошлого запуска не даёт
    «внимания» сразу после пробуждения. Решает слово mcp= последней из них; вывода без слова
    (образ :d2) строки не касаются.
    """
    run = [e for e in log if datetime.fromisoformat(e["End"]) >= started][-HEALTH_LOG_KEEP:]
    words = [_mcp_word(e) for e in run]
    if not words or words[-1] is None:
        return None
    if words[-1] not in MCP_NOT_DOWN:
        return MCP_DOWN_DETAIL
    if (len(words) == HEALTH_LOG_KEEP and set(words) == {"unknown"}
            and now - datetime.fromisoformat(run[0]["End"]) > timedelta(seconds=MCP_UNKNOWN_S)):
        return MCP_UNKNOWN_DETAIL
    return None


def _reason(text: str) -> str:
    """Причина из ответа для страницы: без путей каталога выкладки, первые 80 знаков."""
    return HOST_PATH.sub("…", text.strip())[:REASON_CHARS]


class _DeadlineReader(io.RawIOBase):
    """Чтение сокета под общий срок: каждое чтение ждёт не дольше остатка срока."""

    def __init__(self, sock: socket.socket, deadline: float):
        self._sock, self._deadline = sock, deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("срок проверки истёк")
        self._sock.settimeout(left)
        return self._sock.recv_into(buf)


class _DeadlineSocket:
    """Сокет для HTTPResponse: ему нужен только makefile("rb")."""

    def __init__(self, sock: socket.socket, deadline: float):
        self._sock, self._deadline = sock, deadline

    def makefile(self, mode: str):
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline))


def _http_get(url: str, timeout: float, read_body, deadline: float | None = None) -> tuple[int, bytes]:
    """GET без перенаправлений и без прокси из окружения, под общим сроком.

    Соединение — не дольше timeout на адрес; вся проверка — не дольше HTTP_CHECK_SPANS × timeout:
    срок на каждую операцию сокета не ловит ответ, который капает по байту.
    deadline — абсолютный срок по time.monotonic() (один на несколько запросов, Д6-R46): соединение —
    не дольше min(timeout, остаток), весь запрос — не дальше min(свой срок, deadline); остатка нет —
    TimeoutError без соединения.
    Тело читается (не больше MAX_BODY_BYTES), только если read_body(код) истинно;
    сбой чтения тела при известном коде — пустое тело. Нет связи или срок вышел до
    кода — OSError (TimeoutError) или HTTPException наружу.
    """
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        raise ValueError(f"нужен адрес http://, получен {url!r}")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    now = time.monotonic()
    connect_s, own_deadline = timeout, now + HTTP_CHECK_SPANS * timeout
    if deadline is not None:
        connect_s, own_deadline = min(timeout, deadline - now), min(own_deadline, deadline)
        if connect_s <= 0:
            raise TimeoutError("срок проверки истёк")
    deadline = own_deadline
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=connect_s)
    try:
        conn.connect()
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("срок проверки истёк")
        conn.sock.settimeout(left)   # запрос мал, но и его отправка — под сроком
        conn.request("GET", path)
        # ответ читаем сами, не через getresponse(): тот при Connection: close
        # закрыл бы сокет, а наш читатель не держит ссылку, как makefile сокета
        resp = http.client.HTTPResponse(_DeadlineSocket(conn.sock, deadline), method="GET")
        resp.begin()
        body = b""
        if read_body(resp.status):
            try:
                body = resp.read(MAX_BODY_BYTES)
            except (OSError, http.client.HTTPException):
                pass
        return resp.status, body
    finally:
        conn.close()


def check_http(component: str, title: str, url: str, timeout: float = 3.0,
               ok_detail: str = HTTP_OK_DETAIL) -> Check:
    """2xx — ok с ok_detail; 503 — fail с причиной из JSON-поля detail, если она есть (Д3а-R5);
    иначе — fail с кодом."""
    try:
        status, body = _http_get(url, timeout, lambda code: code == 503)
    except (OSError, http.client.HTTPException):
        return Check(component, title, "fail", "не отвечает")
    if 200 <= status < 300:
        return Check(component, title, "ok", ok_detail)
    if status == 503:
        try:
            reply = json.loads(body)
        except (ValueError, RecursionError):   # в том числе UnicodeDecodeError
            reply = None
        detail = reply.get("detail") if isinstance(reply, dict) else None
        if isinstance(detail, str) and detail.strip():
            return Check(component, title, "fail", _reason(detail))
    return Check(component, title, "fail", f"отвечает ошибкой HTTP {status}")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _load_verdict(reply: dict) -> tuple[State, str, str] | None:
    """Строки нагрузки историана (задача 1 Д4б): пауза людей, медленный p95, ошибки инструментов.

    Нет ключа tools или новых полей gate (служба :d4a) — строка не срабатывает; поле не того вида —
    TypeError или ValueError (ответ не по договору). «k из N» — в note: detail постоянный.
    """
    gate = reply.get("gate")
    if gate is not None:
        if not isinstance(gate, dict):
            raise TypeError("gate — не объект JSON")
        paused = gate.get("user_paused")
        if paused is not None and not isinstance(paused, bool):
            raise TypeError("gate.user_paused — не логическое")
        if paused:
            return "warn", USER_PAUSED_DETAIL, ""
        p95 = gate.get("p95_ms")
        if p95 is not None and not (_is_number(p95) and math.isfinite(p95)):
            raise TypeError("gate.p95_ms — не конечное число")
        if p95 is not None and p95 > SLOW_P95_MS:
            return "warn", SLOW_P95_DETAIL, ""
    tools = reply.get("tools")
    if tools is not None:
        if not isinstance(tools, dict):
            raise TypeError("tools — не объект JSON")
        recent, errors = _count(tools, "recent"), _count(tools, "errors")
        if not 0 <= errors <= recent:
            raise ValueError("tools: число ошибок вне 0…recent")
        if recent >= TOOL_ERRORS_MIN_CALLS and errors / recent >= TOOL_ERRORS_SHARE:
            return "warn", TOOL_ERRORS_DETAIL, f"{errors} из {recent}"
    return None


def historian_verdict(reply, now: datetime, warn_s: int, fail_s: int,
                      stale_s: int) -> tuple[State, str, str]:
    """Вердикт по ответу /health/historian: (состояние, detail, note); первая подходящая строка таблицы
    задачи 6 Д3а со строками нагрузки задачи 1 Д4б перед каталогом.

    Ответ не по договору — ValueError, TypeError, KeyError или OverflowError.
    """
    if not isinstance(reply, dict):
        raise TypeError("ответ — не объект JSON")
    checked_raw = reply["checked_at"]
    if checked_raw is None:
        return "unknown", "служба ещё не опрашивала историан", ""
    if not isinstance(checked_raw, str):
        raise TypeError("checked_at — не строка")
    checked_at = datetime.fromisoformat(checked_raw)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)   # договор — ISO UTC
    if (now - checked_at).total_seconds() > stale_s:
        return "unknown", "служба давно не опрашивала историан", ""
    if reply.get("error"):
        text = reply.get("error_text")
        return "fail", _reason(text if isinstance(text, str) and text.strip() else str(reply["error"])), ""
    age = reply["age_s"]
    if not _is_number(age):
        raise TypeError("age_s — не число")
    # в warn/fail текст постоянный: иначе журнал пишет событие каждый такт
    if age >= fail_s:
        return "fail", f"последняя метка старше {fail_s} с", ""
    if age >= warn_s:
        return "warn", f"последняя метка старше {warn_s} с", ""
    load = _load_verdict(reply)
    if load is not None:
        return load
    catalog_age = reply.get("catalog_age_s")
    if catalog_age is not None and not _is_number(catalog_age):
        raise TypeError("catalog_age_s — не число")
    if catalog_age is not None and catalog_age > CATALOG_MAX_AGE_S:
        return "warn", "каталог тегов старше двух суток", ""
    return "ok", f"последняя метка {round(age)} с назад", ""


def check_historian(component: str, title: str, url: str, now: datetime, warn_s: int, fail_s: int,
                    stale_s: int, timeout: float = NET_CHECK_TIMEOUT_S) -> Check:
    """Свежесть историана по ручке службы данных; сама служба историан не опрашивает заново."""
    try:
        status, body = _http_get(url, timeout, lambda code: code == 200)
    except (OSError, http.client.HTTPException):
        return Check(component, title, "unknown", CORE_DOWN_DETAIL)
    if status != 200:
        return Check(component, title, "unknown", CORE_DOWN_DETAIL)
    try:
        reply = json.loads(body)
    except (ValueError, RecursionError):   # не JSON, в том числе тело, не дочитанное к сроку
        return Check(component, title, "unknown", CORE_DOWN_DETAIL)
    try:
        state, detail, note = historian_verdict(reply, now, warn_s, fail_s, stale_s)
    except (KeyError, TypeError, ValueError, OverflowError):   # OverflowError — age_s = -Infinity
        return Check(component, title, "unknown", OFF_CONTRACT_DETAIL)
    return Check(component, title, state, detail, note)


def fmt_usd(x: float, keep: Iterable[float] = ()) -> str:
    """Сумма для строки: $0.00; меньше $0.0001 — «<$0.0001»; меньше доллара — четыре знака ($0.0012);
    иначе два ($12.35, -$0.50).

    Округление — как у f"{x:.2f}", но не переводит сумму на другую сторону порога из keep (и доллара
    у четырёх знаков): при бюджете $1000 расход 999.996 — «$999.99», а не «$1000.00», 500.004 при
    полосе 50 % — «$500.01».
    """
    if x == 0:
        return "$0.00"
    exact = Decimal(x)   # точное значение float: округление то же, что у форматирования float
    if 0 < exact < USD_TINY:
        return f"<${USD_TINY}"
    small = 0 < exact < 1
    step = USD_TINY if small else Decimal("0.01")
    shown = exact.quantize(step, ROUND_HALF_EVEN)
    for edge in (*map(Decimal, keep), *((Decimal(1),) if small else ())):
        if (shown > edge, shown < edge) != (exact > edge, exact < edge):
            shown = exact.quantize(step, ROUND_FLOOR if exact < edge else ROUND_CEILING)
    return f"{'-' if shown < 0 else ''}${abs(shown):f}"   # -0.00 — это ноль, без минуса


def fmt_total(x: float) -> str:
    """Бюджет — целым, без дробной части ($1000)."""
    return f"${x:.0f}"


def _time(obj: dict, key: str) -> datetime | None:
    raw = obj[key]
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TypeError(f"{key} — не строка")
    value = datetime.fromisoformat(raw)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)   # договор — ISO UTC


def _usd(obj: dict, key: str, *, nullable: bool = False) -> float | None:
    value = obj[key]
    if value is None and nullable:
        return None
    if not _is_number(value) or not math.isfinite(value):   # OverflowError — целое больше float
        raise TypeError(f"{key} — не конечное число")
    return value


def _count(obj: dict, key: str) -> int:
    value = obj[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} — не целое")
    return value


def _flag(obj: dict, key: str, *, nullable: bool = False) -> bool | None:
    value = obj[key]
    if not isinstance(value, bool) and not (value is None and nullable):
        raise TypeError(f"{key} — не логическое")
    return value


def _text(obj: dict, key: str, *, nullable: bool = False) -> str | None:
    value = obj[key]
    if not isinstance(value, str) and not (value is None and nullable):
        raise TypeError(f"{key} — не строка")
    return value


def _part(obj, key: str) -> dict:
    value = obj[key]
    if not isinstance(value, dict):
        raise TypeError(f"{key} — не объект JSON")
    return value


@dataclass(frozen=True)
class RunInfo:
    """Блок run ответа /health/llm (Д8-R69): прогон отбора модели — потолок, расход, вызовы за 10 мин, тишина."""
    id: str
    cap_usd: float
    spent_usd: float
    callers: int
    calls_10m: int
    idle_s: float

    @classmethod
    def parse(cls, obj) -> "RunInfo | None":
        """null — прогона нет; объект — ровно RUN_KEYS, числа не меньше нуля, потолок больше нуля; иначе
        ValueError, TypeError или KeyError."""
        if obj is None:
            return None
        if not isinstance(obj, dict):
            raise TypeError("run — не объект JSON")
        if set(obj) != RUN_KEYS:
            raise ValueError("run: ключи не по договору")
        run = cls(id=_text(obj, "id"), cap_usd=_usd(obj, "cap_usd"), spent_usd=_usd(obj, "spent_usd"),
                  callers=_count(obj, "callers"), calls_10m=_count(obj, "calls_10m"), idle_s=_usd(obj, "idle_s"))
        if not RUN_ID_RE.fullmatch(run.id):
            raise ValueError("run: id не по договору")
        if min(run.spent_usd, run.callers, run.calls_10m, run.idle_s) < 0:
            raise ValueError("run: число меньше нуля")
        if run.cap_usd <= 0:
            raise ValueError("run: потолок не больше нуля")
        return run


@dataclass(frozen=True)
class LlmReply:
    """Поля ответа /health/llm, которые читает сторож; типы проверены при разборе. run — прогон отбора (Д8а):
    ключа нет (core старше сторожа во время выкладки) или null — None."""
    checked_at: datetime | None
    stopped: bool
    config_error: str | None
    tokens_error: str | None
    key: str
    mgmt_key: str
    ledger_ok: bool
    ledger_error: str | None
    ledger_reset: bool
    drill: str
    upstream_state: str
    upstream_text: str
    spent_usd: float
    reserved_usd: float
    ledger_usd: float
    estimated_usd: float
    key_spent_usd: float | None
    total_usd: float
    ops_usd: float
    over_80: int
    shares_spent: int
    pending_stuck: int
    cost_unknown_24h: int
    provider_mismatch_24h: int
    account_remaining_usd: float | None
    egress_alive: bool | None
    egress_last_ok_age_s: float | None
    refresh_started_age_s: float | None
    run: RunInfo | None = None

    @classmethod
    def parse(cls, reply) -> "LlmReply":
        """Ответ не по договору — ValueError, TypeError, KeyError или ArithmeticError."""
        if not isinstance(reply, dict):
            raise TypeError("ответ — не объект JSON")
        upstream, shares, egress = _part(reply, "upstream"), _part(reply, "shares"), _part(reply, "egress")
        r = cls(
            checked_at=_time(reply, "checked_at"), stopped=_flag(reply, "stopped"),
            config_error=_text(reply, "config_error", nullable=True),
            tokens_error=_text(reply, "tokens_error", nullable=True),
            key=_text(reply, "key"), mgmt_key=_text(reply, "mgmt_key"),
            ledger_ok=_flag(reply, "ledger_ok"), ledger_error=_text(reply, "ledger_error", nullable=True),
            ledger_reset=_flag(reply, "ledger_reset"), drill=_text(reply, "drill"),
            upstream_state=_text(upstream, "state"), upstream_text=_text(upstream, "text"),
            spent_usd=_usd(reply, "spent_usd"), reserved_usd=_usd(reply, "reserved_usd"),
            ledger_usd=_usd(reply, "ledger_usd"), estimated_usd=_usd(reply, "estimated_usd"),
            key_spent_usd=_usd(reply, "key_spent_usd", nullable=True), total_usd=_usd(reply, "total_usd"),
            ops_usd=_usd(reply, "ops_usd"),
            over_80=_count(shares, "over_80"), shares_spent=_count(shares, "spent"),
            pending_stuck=_count(reply, "pending_stuck"), cost_unknown_24h=_count(reply, "cost_unknown_24h"),
            provider_mismatch_24h=_count(reply, "provider_mismatch_24h"),
            account_remaining_usd=_usd(reply, "account_remaining_usd", nullable=True),
            egress_alive=_flag(egress, "alive", nullable=True),
            egress_last_ok_age_s=_usd(egress, "last_ok_age_s", nullable=True),
            refresh_started_age_s=_usd(reply, "refresh_started_age_s", nullable=True),
            run=RunInfo.parse(reply.get("run")))
        if r.upstream_state not in UPSTREAM_STATES:
            raise ValueError("неизвестное состояние OpenRouter")
        if not r.upstream_text.strip():
            raise ValueError("пустой текст состояния OpenRouter")
        if r.ledger_error is not None and r.ledger_error not in LEDGER_ERRORS:
            raise ValueError("неизвестная причина сбоя журнала")
        if r.egress_alive and r.egress_last_ok_age_s is None and r.refresh_started_age_s is None:
            raise ValueError("поток адресов жив, а время его запуска неизвестно")
        if r.total_usd <= 0:
            raise ValueError("бюджет не больше нуля")
        return r


def llm_note(r: LlmReply) -> str:
    """Числа строки: расход из бюджета, оценки, сверка с ключом, доли, остаток счёта."""
    # округление расхода не переходит полос и бюджета: число согласно со словами строки
    edges = (*(step * r.total_usd / 100 for step in BUDGET_STEPS), r.total_usd)
    parts = [f"расход {fmt_usd(r.spent_usd, keep=edges)} из {fmt_total(r.total_usd)}"]
    if r.reserved_usd > 0:
        parts.append(f"идёт на оценку {fmt_usd(r.reserved_usd)}")
    unpriced = r.estimated_usd - r.reserved_usd   # вызовы без цены, кроме идущих
    if unpriced > USD_NOISE:
        parts.append(f"по оценке {fmt_usd(unpriced)}")
    if r.key_spent_usd is not None:
        parts.append(f"по ключу OpenRouter {fmt_usd(r.key_spent_usd)}")
    if r.ops_usd > 0:
        parts.append(f"отбор и проверки {fmt_usd(r.ops_usd)}")
    if r.run is not None:
        run = r.run
        cap = fmt_usd(run.cap_usd) if run.cap_usd < RUN_CAP_CENTS_BELOW_USD else fmt_total(run.cap_usd)
        # округление расхода не переходит потолка: «$80.00 из $80» — только при исчерпанном потолке
        parts.append(f"прогон {run.id}: {fmt_usd(run.spent_usd, keep=(run.cap_usd,))} из {cap}, "
                     f"вызовов за 10 мин: {run.calls_10m}")
    if r.over_80 > 0:
        parts.append(f"доля больше 80 %: {r.over_80}")
    if r.shares_spent > 0:
        parts.append(f"доля исчерпана: {r.shares_spent}")
    if r.account_remaining_usd is not None:
        parts.append(f"на счёте {fmt_usd(r.account_remaining_usd, keep=(ACCOUNT_WARN_USD,))}")
    elif r.mgmt_key == "error":
        parts.append("остаток счёта не виден")   # /credits не ответил; rejected — своя строка detail
    if r.drill:
        parts.append(f"учения DRILL_LLM={r.drill}")
    return " · ".join(parts)


def llm_verdict(r: LlmReply, now: datetime, run_silent_s: float = LLM_RUN_SILENT_S) -> tuple[State, str]:
    """Первая подходящая строка таблицы задачи 7 Д4а: сбои, «неизвестно», прогон отбора (Д8-R69), деньги мимо
    учёта и провайдер, полосы бюджета — последними, чтобы полоса после $500 не прятала остальное.

    Строки прогона — после fail и unknown (учения 402 и сбой OpenRouter их перекрывают), до errors: исчерпанный
    потолок, затем тишина дольше run_silent_s. В warn/fail текст постоянный — числа идут в note (правило журнала Д1).
    """
    upstream_text = _reason(r.upstream_text)
    if r.stopped:
        return "fail", "LLM-прокси остановлен администратором"
    if r.config_error is not None:
        return "fail", _reason("ошибка настройки: " + r.config_error)
    if r.tokens_error is not None:
        return "fail", "токены LLM не читаются"
    if r.key == "missing":
        return "fail", "нет ключа OpenRouter"
    if not r.ledger_ok or r.ledger_error is not None:
        if r.ledger_error == "dropped":   # брошенная строка держится до перезапуска core
            return "fail", "строка журнала вызовов потеряна — перезапустите core"
        return "fail", "журнал вызовов не пишется"
    if r.egress_alive is False:
        return "fail", "охрана выхода: поток обновления адресов остановлен"
    if (r.egress_alive and r.egress_last_ok_age_s is None
            and r.refresh_started_age_s is not None and r.refresh_started_age_s > EGRESS_STALE_S):
        return "fail", "охрана выхода: адреса OpenRouter ни разу не получены"
    # spent_usd — без резервов идущих вызовов (Д4а-R11): строка не мигает на каждом вызове
    if r.spent_usd >= r.total_usd:
        return "fail", f"бюджет {fmt_total(r.total_usd)} исчерпан — модель не отвечает"
    if r.upstream_state in UPSTREAM_FAIL_STATES:
        return "fail", upstream_text
    if r.checked_at is None:
        return "unknown", "OpenRouter ещё не опрашивался"
    if r.upstream_state == "unknown":   # опрос был, но удачи не было ни разу — «норма» была бы ложью
        return "unknown", "OpenRouter ещё ни разу не ответил на опрос"
    if (now - r.checked_at).total_seconds() > LLM_STALE_S:
        return "unknown", "LLM-прокси давно не опрашивал OpenRouter"
    if r.run is not None:
        if r.run.spent_usd >= r.run.cap_usd:
            return "warn", RUN_CAP_DETAIL
        if r.run.idle_s > run_silent_s:
            return "warn", RUN_SILENT_DETAIL
    if r.upstream_state == "errors":
        return "warn", upstream_text
    if r.account_remaining_usd is not None and r.account_remaining_usd < ACCOUNT_WARN_USD:
        return "warn", f"на счёте OpenRouter меньше {fmt_total(ACCOUNT_WARN_USD)}"
    if r.provider_mismatch_24h > 0:
        return "warn", "за сутки были ответы не от закреплённого провайдера"
    if r.cost_unknown_24h > 0:
        return "warn", "цена неизвестна у части вызовов — в расходе по верхней оценке"
    if r.pending_stuck > 0:
        return "warn", "цена ждёт дозапроса дольше 15 минут"
    if r.ledger_reset:
        return "warn", "журнал вызовов начат заново при ненулевом расходе ключа"
    if r.egress_last_ok_age_s is not None and r.egress_last_ok_age_s > EGRESS_STALE_S:
        return "warn", "охрана выхода: адреса OpenRouter не обновлялись больше 5 минут"
    if r.key_spent_usd is not None and r.key_spent_usd - r.ledger_usd > DRIFT_WARN_USD:
        return "warn", "OpenRouter насчитал больше, чем учёл прокси"
    if r.mgmt_key == "rejected":
        return "warn", "ключ управления OpenRouter отклонён — остаток счёта не виден"
    for step in BUDGET_STEPS:
        if r.spent_usd * 100 > step * r.total_usd:
            return "warn", f"израсходовано больше {step} % бюджета"
    return "ok", upstream_text


def check_llm(component: str, title: str, url: str, now: datetime,
              timeout: float = NET_CHECK_TIMEOUT_S, run_silent_s: float = LLM_RUN_SILENT_S) -> Check:
    """LLM-прокси и бюджет по ручке /health/llm службы; сама служба OpenRouter заново не опрашивает. run_silent_s —
    порог «прогон отбора молчит» (Settings.LLM_RUN_SILENT_S)."""
    try:
        status, body = _http_get(url, timeout, lambda code: code == 200)
    except (OSError, http.client.HTTPException):
        return Check(component, title, "fail", LLM_DOWN_DETAIL)
    if status != 200:
        return Check(component, title, "fail", LLM_DOWN_DETAIL)
    try:
        reply = json.loads(body)
    except (ValueError, RecursionError):   # не JSON, в том числе тело, не дочитанное к сроку
        return Check(component, title, "fail", LLM_DOWN_DETAIL)
    try:
        parsed = LlmReply.parse(reply)
        state, detail = llm_verdict(parsed, now, run_silent_s)
        note = llm_note(parsed)
    except (KeyError, TypeError, ValueError, ArithmeticError):   # ArithmeticError — Overflow, Decimal
        return Check(component, title, "fail", LLM_OFF_CONTRACT_DETAIL)
    return Check(component, title, state, detail, note)


def _gw_count(obj: dict, key: str, *, optional: bool = False) -> int | None:
    """Счётчик шлюза: целое не меньше нуля; optional — ключа нет, тогда None (ключи Д5б).

    Свой, а не правка общего _count: договор LlmReply (Д4а) этим не меняется.
    """
    if optional and key not in obj:
        return None
    value = _count(obj, key)
    if value < 0:
        raise ValueError(f"{key} — меньше нуля")
    return value


@dataclass(frozen=True)
class GatewayReply:
    """Поля ответа /health/gateway, которые читает сторож; типы проверены при разборе.

    Ключи Д5а обязательны. Ключи Д5б (refused_1h, streams, stream_broken_1h, upstream_failed_1h) — все
    четыре или ни одного: во время выкладки сторож может читать core :d5a; тогда все четыре None.
    """
    checked_at: datetime | None
    config_error: str | None
    store_ok: bool
    sp_ctl: str
    running_n: int
    ready_n: int
    wake_ok_1h: int
    wake_failed_1h: int
    stop_failed_1h: int
    place_auth_failed_1h: int
    logins_failed_1h: int
    origin_refused_1h: int
    login_busy_1h: int
    cookies_duplicate_1h: int
    drill: str
    refused_1h: int | None
    streams: int | None
    stream_broken_1h: int | None
    upstream_failed_1h: int | None

    @classmethod
    def parse(cls, reply) -> "GatewayReply":
        """Ответ не по договору — только ValueError (Ш8): не объект JSON, нет ключа Д5а, лишний ключ, часть
        ключей Д5б, тип не тот, счётчик меньше нуля, sp_ctl не из GW_SP_CTL_STATES, пустая причина
        config_error, drill не "" и не из GW_DRILL_MODES."""
        if not isinstance(reply, dict):
            raise ValueError("ответ — не объект JSON")
        if not set(reply) <= GW_KEYS_D5A | GW_KEYS_D5B:
            raise ValueError("в ответе шлюза лишний ключ")
        if len(GW_KEYS_D5B & set(reply)) not in (0, len(GW_KEYS_D5B)):
            raise ValueError("в ответе шлюза часть ключей прокси")
        try:
            r = cls(
                checked_at=_time(reply, "checked_at"),   # не ISO — ValueError из fromisoformat
                config_error=_text(reply, "config_error", nullable=True),
                store_ok=_flag(reply, "store_ok"), sp_ctl=_text(reply, "sp_ctl"),
                running_n=_gw_count(reply, "running_n"), ready_n=_gw_count(reply, "ready_n"),
                wake_ok_1h=_gw_count(reply, "wake_ok_1h"), wake_failed_1h=_gw_count(reply, "wake_failed_1h"),
                stop_failed_1h=_gw_count(reply, "stop_failed_1h"),
                place_auth_failed_1h=_gw_count(reply, "place_auth_failed_1h"),
                logins_failed_1h=_gw_count(reply, "logins_failed_1h"),
                origin_refused_1h=_gw_count(reply, "origin_refused_1h"),
                login_busy_1h=_gw_count(reply, "login_busy_1h"),
                cookies_duplicate_1h=_gw_count(reply, "cookies_duplicate_1h"),
                drill=_text(reply, "drill"),
                refused_1h=_gw_count(reply, "refused_1h", optional=True),
                streams=_gw_count(reply, "streams", optional=True),
                stream_broken_1h=_gw_count(reply, "stream_broken_1h", optional=True),
                upstream_failed_1h=_gw_count(reply, "upstream_failed_1h", optional=True))
        except (KeyError, TypeError) as e:
            raise ValueError(f"ответ шлюза не по договору: {type(e).__name__} {e}") from e
        if r.sp_ctl not in GW_SP_CTL_STATES:
            raise ValueError("неизвестное состояние связи с sp-ctl")
        if r.config_error is not None and not r.config_error.strip():
            raise ValueError("ошибка настройки без причины")   # иначе строка «ошибка настройки:» без слов
        if r.drill and r.drill not in GW_DRILL_MODES:
            raise ValueError("неизвестный режим учений")
        return r


def gateway_verdict(r: GatewayReply, now: datetime, stale_s: int) -> tuple[State, str, str]:
    """Первая подходящая строка таблицы задачи А1 Д5а с двумя строками прокси Б1 Д5б после «не приняло пароль
    шлюза»: (состояние, detail, note без учений).

    В warn/fail текст постоянный — числа идут в note (правило журнала Д1).
    """
    if r.config_error is not None:
        return "fail", _reason("ошибка настройки: " + r.config_error), ""
    if not r.store_ok:
        return "fail", "учётные записи недоступны", ""
    if r.checked_at is None:
        return "unknown", "уборщик простоя ещё не проходил", ""
    if (now - r.checked_at).total_seconds() > stale_s:
        return "fail", "уборщик простоя молчит — места не засыпают", ""
    if r.sp_ctl == "down":
        return "fail", "нет связи с прокси сокета — места не будятся", ""
    if r.wake_failed_1h >= WAKE_FAILS_FAIL and r.wake_ok_1h == 0:
        return "fail", "рабочие места не поднимаются", f"{r.wake_failed_1h} за час"
    for count, detail in ((r.place_auth_failed_1h, "рабочее место не приняло пароль шлюза"),
                          (r.upstream_failed_1h, "рабочее место не отвечает шлюзу"),     # Д5б; core :d5a — None
                          (r.stream_broken_1h, "поток событий рвётся"),
                          (r.cookies_duplicate_1h, "подброшена cookie сессии"),
                          (r.origin_refused_1h, "вход отклоняется проверкой источника — проверьте GW_ORIGIN"),
                          (r.login_busy_1h, "вход перегружен"),
                          (r.wake_failed_1h, "рабочее место не поднялось по входу"),
                          (r.stop_failed_1h, "уборщик не смог усыпить место")):
        if count is not None and count > 0:
            return "warn", detail, f"{count} за час"
    if r.logins_failed_1h >= LOGIN_FAILS_WARN:
        return "warn", "много неверных паролей", f"{r.logins_failed_1h} за час"
    note = f"работает мест: {r.running_n}"
    if r.streams is not None:           # ответ core :d5b — все четыре ключа прокси (parse)
        note += f"; потоков: {r.streams}; отказов за час: {r.refused_1h}"
    return "ok", "принимает вход", note


def check_gateway(component: str, title: str, url: str, now: datetime, stale_s: int,
                  timeout: float = NET_CHECK_TIMEOUT_S) -> Check:
    """Шлюз и вход по ручке /health/gateway службы (только числа и тексты, без логинов и номеров мест).

    Нет ответа, HTTP ≠ 200 или не JSON — «шлюз не отвечает»; JSON не по договору — свой текст (Ш8).
    При учениях к note любой строки таблицы первым добавляется «учения: <режим>».
    """
    try:
        status, body = _http_get(url, timeout, lambda code: code == 200)
    except (OSError, http.client.HTTPException):
        return Check(component, title, "fail", GW_DOWN_DETAIL)
    if status != 200:
        return Check(component, title, "fail", GW_DOWN_DETAIL)
    try:
        reply = json.loads(body)
    except (ValueError, RecursionError):   # не JSON, в том числе тело, не дочитанное к сроку
        return Check(component, title, "fail", GW_DOWN_DETAIL)
    try:
        parsed = GatewayReply.parse(reply)
    except ValueError:
        return Check(component, title, "fail", GW_OFF_CONTRACT_DETAIL)
    state, detail, note = gateway_verdict(parsed, now, stale_s)
    if parsed.drill:
        note = "; ".join(part for part in (f"учения: {parsed.drill}", note) if part)
    return Check(component, title, state, detail, note)


@dataclass(frozen=True)
class AgentsReply:
    """Ответ /health/agents (Д7-R60): все 15 ключей, типы проверены при разборе."""
    checked_at: datetime | None
    store_ok: bool
    dir_error: str | None
    pending: int
    pending_oldest_s: float | None
    saved_1h: int
    refused_1h: int
    unavailable_1h: int
    visible_1h: int
    not_visible_1h: int
    quarantined_1h: int
    reload_failed_1h: int
    reload_delayed_1h: int
    image_config_1h: int
    drill: str

    @classmethod
    def parse(cls, reply) -> "AgentsReply":
        """Ответ не по договору — только ValueError (Ш8), как у GatewayReply: не объект JSON, лишний или недостающий
        ключ, тип не тот, счётчик или pending_oldest_s меньше нуля (или не конечное число), пустая причина dir_error,
        drill не "" и не из AGENTS_DRILL_MODES."""
        if not isinstance(reply, dict):
            raise ValueError("ответ — не объект JSON")
        if set(reply) != AGENTS_KEYS:
            raise ValueError("в ответе службы агентов лишний или недостающий ключ")
        try:
            oldest = _usd(reply, "pending_oldest_s", nullable=True)    # число или null; bool и не конечное — TypeError
            r = cls(checked_at=_time(reply, "checked_at"),           # не ISO — ValueError из fromisoformat
                    store_ok=_flag(reply, "store_ok"), dir_error=_text(reply, "dir_error", nullable=True),
                    pending=_gw_count(reply, "pending"), pending_oldest_s=oldest,
                    **{key: _gw_count(reply, key) for key in sorted(AGENTS_KEYS) if key.endswith("_1h")},
                    drill=_text(reply, "drill"))
        except (KeyError, TypeError, ArithmeticError) as e:     # ArithmeticError — целое больше float в isfinite
            raise ValueError(f"ответ службы агентов не по договору: {type(e).__name__} {e}") from e
        if r.pending_oldest_s is not None and r.pending_oldest_s < 0:
            raise ValueError("pending_oldest_s — меньше нуля")
        if r.dir_error is not None and not r.dir_error.strip():
            raise ValueError("сбой каталога без причины")       # иначе строка «каталог недоступен» без слов
        if r.drill and r.drill not in AGENTS_DRILL_MODES:
            raise ValueError("неизвестный режим учений")
        return r


def agents_verdict(r: AgentsReply, now: datetime, stale_s: int) -> tuple[State, str, str]:
    """Первая подходящая строка таблицы Д7-R60: (состояние, detail, note без учений).

    В warn/fail текст постоянный — числа идут в note (правило журнала Д1); dir_error — тоже в note (без путей, до 80).
    """
    if r.image_config_1h > 0:
        return "fail", "настройка рабочего места сломана — место не поднимается", f"{r.image_config_1h} за час"
    if r.dir_error is not None:
        return "fail", "каталог агентов недоступен — агенты не сохраняются", _reason(r.dir_error)
    if not r.store_ok:
        return "fail", "учёт агентов недоступен", ""
    if r.checked_at is None:
        return "unknown", "перезагрузка агентов ещё не проходила", ""
    if (now - r.checked_at).total_seconds() > stale_s:
        return "fail", "перезагрузка агентов молчит", ""
    for count, detail in ((r.quarantined_1h, "файл агента сломал рабочее место — в карантине, место перезапущено"),
                          (r.not_visible_1h, "сохранённый агент не появился в рабочем месте"),
                          (r.reload_failed_1h, "перезагрузка рабочего места не удалась")):
        if count > 0:
            return "warn", detail, f"{count} за час"
    if r.pending_oldest_s is not None and r.pending_oldest_s >= AGENTS_DELAYED_S:
        return "warn", "агент ждёт перезагрузки места дольше 15 минут", f"мест: {r.pending}"
    if r.unavailable_1h > 0:
        return "warn", "save_agent отвечает «недоступно» — агенты не сохраняются", f"{r.unavailable_1h} за час"
    if r.refused_1h >= AGENTS_REFUSED_WARN and r.saved_1h == 0:
        return "warn", "save_agent отказывает — агенты не сохраняются", f"{r.refused_1h} за час"
    note = f"сохранено за час: {r.saved_1h}; ждут перезагрузки: {r.pending}"
    if r.reload_delayed_1h > 0:
        note += f"; ждали дольше 15 мин: {r.reload_delayed_1h}"
    return "ok", "агенты сохраняются", note


def check_agents(component: str, title: str, url: str, now: datetime, stale_s: int,
                 timeout: float = NET_CHECK_TIMEOUT_S) -> Check:
    """Агенты студентов по ручке /health/agents службы (только числа и тексты, без имён агентов и номеров мест).

    Нет ответа, срок, не JSON, код не 200 и не 404 — «служба агентов не отвечает»; 404 (core старше сторожа во время
    выкладки) — «неизвестно»; JSON не по договору — свой текст (Ш8). При учениях к note любой строки таблицы первым
    добавляется «учения: <режим>».
    """
    try:
        status, body = _http_get(url, timeout, lambda code: code == 200)
    except (OSError, http.client.HTTPException):
        return Check(component, title, "fail", AGENTS_DOWN_DETAIL)
    if status == 404:
        return Check(component, title, "unknown", AGENTS_NOT_DEPLOYED_DETAIL)
    if status != 200:
        return Check(component, title, "fail", AGENTS_DOWN_DETAIL)
    try:
        reply = json.loads(body)
    except (ValueError, RecursionError):   # не JSON, в том числе тело, не дочитанное к сроку
        return Check(component, title, "fail", AGENTS_DOWN_DETAIL)
    try:
        parsed = AgentsReply.parse(reply)
    except ValueError:
        return Check(component, title, "fail", AGENTS_OFF_CONTRACT_DETAIL)
    state, detail, note = agents_verdict(parsed, now, stale_s)
    if parsed.drill:
        note = "; ".join(part for part in (f"учения: {parsed.drill}", note) if part)
    return Check(component, title, state, detail, note)


def _web_manifest(body: bytes) -> tuple[str, list[str]] | None:
    """Опись build.json → (сборка, пути по порядку описи); не JSON или не той формы — None."""
    try:
        reply = json.loads(body)
    except (ValueError, RecursionError):   # в том числе UnicodeDecodeError и тело, не дочитанное к сроку
        return None
    if not isinstance(reply, dict):
        return None
    build, files = reply.get("build"), reply.get("files")
    # пустая опись ничего не проверила бы: у сборки Vite всегда есть хотя бы один файл assets/
    if not isinstance(build, str) or not isinstance(files, dict) or not 0 < len(files) <= WEB_MAX_FILES:
        return None
    for path, digest in files.items():
        # «assets/..» и «assets/.» шаблон пропускает, а edge свёл бы их к самому /chat/
        if (not WEB_FILE_RE.fullmatch(path) or path.rsplit("/", 1)[-1] in (".", "..")
                or not isinstance(digest, str) or not SHA256_HEX.fullmatch(digest)):
            return None
    return build, list(files)


def _web_reports(body: bytes) -> dict[str, int] | None:
    """Ответ /health/web → счётчики; не JSON, не объект, ключи не ровно четыре счётчика, значение не целое
    не меньше нуля (bool — не целое) — None."""
    try:
        reply = json.loads(body)
    except (ValueError, RecursionError):   # в том числе UnicodeDecodeError и тело, не дочитанное к сроку
        return None
    keys = {key for key, _ in WEB_REPORT_ROWS}
    if not isinstance(reply, dict) or set(reply) != keys:
        return None
    if not all(type(reply[key]) is int and reply[key] >= 0 for key in keys):
        return None
    return reply


def check_web(component: str, title: str, url: str, timeout: float = NET_CHECK_TIMEOUT_S,
              reports_url: str | None = None) -> Check:
    """Страница чата и опись сборки (таблица Д6-R46): GET url (тело — метка сборки) → GET build.json →
    GET каждого файла описи (тело не читается); если статика в порядке и задан reports_url — последним GET
    отчётов браузеров (таблица Д6-R50).

    Один срок HTTP_CHECK_SPANS × timeout от начала на все запросы, отчёты тоже — так проверка, начатая до конца
    такта, кончается не позже запаса tick_budget_s. Нет соединения или срок — «не отвечает», в том числе когда
    срок оборвал тело страницы или описи. Отчёты строку не роняют: нет ответа, срок (и вышедший до их запроса),
    не 200 или не по договору — ok с пометкой «отчёты браузеров недоступны» (core без ручки во время выкладки).
    """
    deadline = time.monotonic() + HTTP_CHECK_SPANS * timeout

    def get(target: str, read_body) -> tuple[int, bytes]:
        status, body = _http_get(target, timeout, read_body, deadline=deadline)
        if time.monotonic() >= deadline:   # тело могло оборваться сроком — это срок, а не «нет метки»
            raise TimeoutError("срок проверки истёк")
        return status, body

    def fail(detail: str, note: str = "") -> Check:
        return Check(component, title, "fail", detail, note)

    try:
        status, body = get(url, lambda code: code == 200)
        if status != 200:
            return fail(WEB_PAGE_DETAIL, f"HTTP {status}")
        meta = WEB_META_RE.search(body.decode("utf-8", "replace"))
        if meta is None:
            return fail(WEB_PAGE_DETAIL, "нет метки сборки")
        status, body = get(urljoin(url, "build.json"), lambda code: code == 200)
        manifest = _web_manifest(body) if status == 200 else None
        if manifest is None:
            return fail(WEB_PARTIAL_DETAIL, WEB_BAD_MANIFEST)
        build, files = manifest
        if build != meta.group(1):
            return fail(WEB_PARTIAL_DETAIL, "страница и опись разных сборок")
        for path in files:
            status, _ = get(urljoin(url, path), lambda code: False)
            if status != 200:
                return fail(WEB_PARTIAL_DETAIL, f"нет {path}")
    except (OSError, http.client.HTTPException):
        return fail(WEB_DOWN_DETAIL)
    note = f"сборка {build}"
    if reports_url is None:
        return Check(component, title, "ok", WEB_OK_DETAIL, note)
    try:
        status, body = get(reports_url, lambda code: code == 200)
    except (OSError, http.client.HTTPException):
        status, body = None, b""
    counts = _web_reports(body) if status == 200 else None
    if counts is None:
        return Check(component, title, "ok", WEB_OK_DETAIL, f"{note}; {WEB_NO_REPORTS}")
    for key, detail in WEB_REPORT_ROWS:
        if counts[key] > 0:
            return Check(component, title, "warn", detail, f"{counts[key]} за час")
    return Check(component, title, "ok", WEB_OK_DETAIL, note)


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
