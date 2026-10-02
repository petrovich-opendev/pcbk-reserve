"""Клиент шлюза стенда (Д5а/Д5б, Д7б) для прогонщика отбора: одна учётка d8-probe-bench-NN — одно место.

Ходит через edge, как браузер студента: cookie pcbk_session, Host — имя сертификата стенда, на записи —
Sec-Fetch-Site: same-origin (и Origin, если задан). Сертификат не проверяется: на сервере — туннель, а сертификат
edge истёк (напоминание 01.10). Схема http:// — только для подставного сервера в тестах.

Ответы шлюза об ошибках — вида OpenCode {"name", "data": {"message"}}; исключение несёт код и имя, но не текст.
- 401 — один повторный вход с запомненной учёткой и повтор вызова;
- 503 с именем WorkplaceNotReady — исключение WorkplaceNotReady: место спит, будит прогонщик (Д8-R74);
- прочие 503 (WorkplaceBusy — ворота перезагрузки места, П6) и 429 — до RETRIES повторов через retry_delay_s;
  кончились — GatewayError(503 | 429, имя): прогонщик будит место и повторяет вызов один раз.

wait_idle(session, timeout_s) опрашивает GET /session/status раз в status_poll_s и GET /session/{id}/message —
после idle или раз в messages_poll_s (m-р4-15). Ход кончен, когда сессии нет в статусе, а последнее сообщение —
законченный (time.completed или error) ответ ассистента после последней реплики, отправленной этим клиентом:
сразу после prompt_async старый ответ прошлого хода за конец не принимается. Вопросы и запросы разрешений своей
сессии снимаются отказом, как converse.js. Срок — TimeoutError.
"""
from __future__ import annotations

import http.client
import json
import ssl
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .secret import Secret, SecretBytes

API = "/pcbk/api"
OC = API + "/oc"
COOKIE = "pcbk_session"
RETRIES = 5
RETRY_DELAY_S = 2.0
STATUS_POLL_S = 1.0
MESSAGES_POLL_S = 5.0
WAKE_POLL_S = 2.0


class GatewayError(Exception):
    """Отказ шлюза или места: код HTTP и имя ошибки вида OpenCode (без текста — в нём бывает ввод)."""

    def __init__(self, status: int, name: str):
        super().__init__(f"{status} {name}")
        self.status = status
        self.name = name


class WorkplaceNotReady(GatewayError):
    """503 WorkplaceNotReady: место спит или не отвечает — будить (Д8-R74)."""


def _error_name(data: Any) -> str:
    if isinstance(data, dict) and isinstance(data.get("name"), str):
        return data["name"]
    return "Error"


class GatewayChat:
    """Сессия одной учётки шлюза. login/password можно задать сразу (П4) — тогда login() без аргументов."""

    def __init__(self, base: str, host: str, origin: str | None = None, timeout: float = 30.0, *,
                 login: str | None = None, password: str | None = None):
        parts = urlsplit(base)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError("base — https://адрес[:порт]")
        self._https = parts.scheme == "https"
        self._addr = parts.hostname
        self._port = parts.port or (443 if self._https else 80)
        self.host = host
        self.origin = origin
        self.timeout = timeout
        self._login = login
        self._password = None if password is None else Secret(password)
        self._cookie: Secret | None = None
        self._lock = threading.Lock()
        self._sent: dict[str, int] = {}
        self.place: str | None = None
        self.rejected = 0
        self.retry_delay_s = RETRY_DELAY_S
        self.status_poll_s = STATUS_POLL_S
        self.messages_poll_s = MESSAGES_POLL_S
        self.wake_poll_s = WAKE_POLL_S
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    # --- запросы ---

    def _request(self, method: str, path: str, body: Any = None) -> tuple[int, Any, http.client.HTTPMessage]:
        data = SecretBytes(b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"))
        if self._https:
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(self._addr, self._port, context=self._ctx,
                                                                         timeout=self.timeout)
        else:
            conn = http.client.HTTPConnection(self._addr, self._port, timeout=self.timeout)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", self.host)
            with self._lock:
                cookie = self._cookie
            if cookie is not None:
                conn.putheader("Cookie", Secret(f"{COOKIE}={cookie}"))
            if method != "GET":
                conn.putheader("Sec-Fetch-Site", "same-origin")
                if self.origin:
                    conn.putheader("Origin", self.origin)
            if body is not None:
                conn.putheader("Content-Type", "application/json")
            if method != "GET":
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data or None)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = None
            return resp.status, parsed, resp.msg
        finally:
            conn.close()

    def _call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200,)) -> Any:
        relogin = self._login is not None
        attempts = 0
        while True:
            status, data, _ = self._request(method, path, body)
            if status in ok:
                return data
            name = _error_name(data)
            if status == 401 and relogin:
                relogin = False
                self.login()
                continue
            if status == 503 and name == "WorkplaceNotReady":
                raise WorkplaceNotReady(status, name)
            if status in (429, 503) and attempts < RETRIES:
                attempts += 1
                time.sleep(self.retry_delay_s)
                continue
            raise GatewayError(status, name)

    # --- учётка и место ---

    def login(self, login: str | None = None, password: str | None = None) -> None:
        """Вход учёткой (запоминается для повторного входа на 401); cookie сессии — в памяти, не на диске."""
        if login is not None:
            self._login = login
        if password is not None:
            self._password = Secret(password)
        if self._login is None or self._password is None:
            raise ValueError("учётка шлюза не задана")
        status, data, headers = self._request("POST", API + "/auth/login",
                                              {"login": self._login, "password": self._password})
        if status != 200:
            raise GatewayError(status, _error_name(data))
        cookie = None
        for line in headers.get_all("Set-Cookie") or []:
            name, _, rest = line.partition("=")
            value = rest.split(";", 1)[0]
            if name.strip() == COOKIE and value:
                cookie = Secret(value)
        if cookie is None:
            raise GatewayError(status, "NoSessionCookie")
        with self._lock:
            self._cookie = cookie
        self.place = data.get("place") if isinstance(data, dict) else None

    def workplace(self) -> dict:
        """GET /pcbk/api/workplace: {"place", "state", "detail"} — состояние места, не активность."""
        data = self._call("GET", API + "/workplace")
        return data if isinstance(data, dict) else {}

    def wake(self, timeout: float = 120.0) -> None:
        """POST /pcbk/api/workplace/wake (продлевает простой места) и ожидание ready; failed или срок —
        WorkplaceNotReady."""
        view = self._call("POST", API + "/workplace/wake", ok=(200, 202))
        deadline = time.monotonic() + timeout
        while True:
            state = view.get("state") if isinstance(view, dict) else None
            if state == "ready":
                return
            if state == "failed" or time.monotonic() >= deadline:
                raise WorkplaceNotReady(503, "WorkplaceNotReady")
            time.sleep(self.wake_poll_s)
            view = self.workplace()

    def agents(self) -> list[dict]:
        """GET /pcbk/api/agents — «Мои агенты»: встроенные агенты образа и агенты места с состоянием панели."""
        data = self._call("GET", API + "/agents")
        agents = data.get("agents") if isinstance(data, dict) else None
        return [a for a in agents if isinstance(a, dict)] if isinstance(agents, list) else []

    # --- разговор (белый список шлюза, rules.py) ---

    def new_session(self) -> str:
        data = self._call("POST", OC + "/session", {})
        sid = data.get("id") if isinstance(data, dict) else None
        if not isinstance(sid, str) or not sid:
            raise GatewayError(200, "NoSessionId")
        with self._lock:
            self._sent[sid] = 0
        return sid

    def send(self, session: str, text: str, agent: str) -> None:
        """prompt_async: одна текстовая часть и агент хода; ответ — 204 без тела."""
        self._call("POST", f"{OC}/session/{session}/prompt_async",
                   {"parts": [{"type": "text", "text": text}], "agent": agent}, ok=(200, 204))
        with self._lock:
            self._sent[session] = self._sent.get(session, 0) + 1

    def messages(self, session: str) -> list[dict]:
        data = self._call("GET", f"{OC}/session/{session}/message")
        return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []

    def session_status(self) -> dict:
        data = self._call("GET", OC + "/session/status")
        return data if isinstance(data, dict) else {}

    def abort(self, session: str) -> None:
        """POST /session/{id}/abort; 404 — GatewayError(404, …): разговора уже нет."""
        self._call("POST", f"{OC}/session/{session}/abort")

    def _finished(self, session: str, messages: list[dict]) -> bool:
        with self._lock:
            sent = self._sent.get(session, 0)
        roles = [(m.get("info") or {}).get("role") for m in messages]
        if roles.count("user") < sent or not roles or roles[-1] != "assistant":
            return False
        info = messages[-1].get("info") or {}
        return bool((info.get("time") or {}).get("completed")) or bool(info.get("error"))

    def _reject_asks(self, session: str) -> None:
        """Вопросы модели и запросы разрешений своей сессии — отказом (как converse.js): иначе ход висит до срока."""
        for q in self._call("GET", OC + "/question") or []:
            if isinstance(q, dict) and q.get("sessionID") == session and isinstance(q.get("id"), str):
                self._call("POST", f"{OC}/question/{q['id']}/reject")
                self.rejected += 1
        for p in self._call("GET", OC + "/permission") or []:
            if isinstance(p, dict) and p.get("sessionID") == session and isinstance(p.get("id"), str):
                self._call("POST", f"{OC}/permission/{p['id']}/reply", {"reply": "reject"})
                self.rejected += 1

    def wait_idle(self, session: str, timeout_s: float) -> list[dict]:
        """Ожидание конца хода; сообщения сессии целиком. Срок — TimeoutError."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        next_messages = 0.0
        while True:
            busy = session in self.session_status()
            now = time.monotonic()
            if not busy or now >= next_messages:
                messages = self.messages(session)
                next_messages = now + self.messages_poll_s
                if not busy and self._finished(session, messages):
                    return messages
                if busy:
                    self._reject_asks(session)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("ход не кончился в срок")
            time.sleep(min(self.status_poll_s, left))


__all__ = ["API", "GatewayChat", "GatewayError", "OC", "WorkplaceNotReady"]
