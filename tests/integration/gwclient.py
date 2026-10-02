"""Клиент шлюза для интеграционных тестов (Д5а): вход, выход, рабочее место через edge по HTTPS.

Соединение — по адресу из base (127.0.0.1:18443), заголовок Host — свой (host), так имя стенда нигде не
разрешается. TLS без проверки сертификата: сертификат тестовый, на сервере — туннель. Путь уходит как есть
(putrequest), без нормализации. Каждый вызов — своё соединение HTTP/1.1.
"""
from __future__ import annotations

import http.client
import json
import ssl
import time
from typing import Any
from urllib.parse import urlsplit

COOKIE = "pcbk_session"
WAKE_POLL_S = 1.0


class GwClient:
    """Сессия одного пользователя шлюза: своя cookie pcbk_session, место — из ответа входа."""

    def __init__(self, base: str, *, host: str | None = None, origin: str | None = None,
                 same_origin: bool = False, timeout: float = 30.0):
        parts = urlsplit(base)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("base — https://адрес[:порт]")
        self.addr = parts.hostname
        self.port = parts.port or 443
        self.host = host or parts.netloc
        self.origin = origin
        self.same_origin = same_origin
        self.timeout = timeout
        self.cookie: str | None = None
        self.place: str | None = None
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def call(self, method: str, path: str, body=None, *,
             headers: dict | None = None) -> tuple[int, Any, http.client.HTTPMessage]:
        """Запрос к edge: (код, тело, заголовки). Тело — JSON, если разбирается, иначе текст; пустое — None.

        Cookie — своя; Cookie из headers её заменяет. На не-GET ставятся Origin (если задан) и/или
        Sec-Fetch-Site: same-origin (если same_origin). body — объект JSON.
        """
        extra = dict(headers or {})
        names = {k.lower() for k in extra}
        data = b"" if body is None else json.dumps(body).encode("utf-8")
        conn = http.client.HTTPSConnection(self.addr, self.port, context=self._ctx, timeout=self.timeout)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", self.host)
            if "cookie" not in names and self.cookie is not None:
                conn.putheader("Cookie", f"{COOKIE}={self.cookie}")
            if method.upper() not in ("GET", "HEAD"):
                if self.origin is not None and "origin" not in names:
                    conn.putheader("Origin", self.origin)
                if self.same_origin and "sec-fetch-site" not in names:
                    conn.putheader("Sec-Fetch-Site", "same-origin")
            if body is not None:
                conn.putheader("Content-Type", "application/json")
            if body is not None or method.upper() not in ("GET", "HEAD", "TRACE"):
                conn.putheader("Content-Length", str(len(data)))
            for name, value in extra.items():
                conn.putheader(name, value)
            conn.endheaders(data or None)
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, _parse(raw), resp.msg
        finally:
            conn.close()

    def login(self, login: str, password: str) -> dict:
        """Вход: не 200 — AssertionError с кодом (без пароля); запоминает cookie сессии и место."""
        code, body, headers = self.call("POST", "/pcbk/api/auth/login", {"login": login, "password": password})
        if code != 200:
            raise AssertionError(f"вход {login}: код {code}")
        for line in headers.get_all("Set-Cookie") or []:
            name, _, rest = line.partition("=")
            value = rest.split(";", 1)[0]
            if name.strip() == COOKIE and value:
                self.cookie = value
        if self.cookie is None:
            raise AssertionError(f"вход {login}: нет cookie сессии")
        self.place = body.get("place")
        return body

    def logout(self) -> tuple[int, Any, http.client.HTTPMessage]:
        result = self.call("POST", "/pcbk/api/auth/logout")
        self.cookie = None
        return result

    def wake_and_wait(self, timeout: float = 90.0) -> dict:
        """POST /pcbk/api/workplace/wake (ждём 202), затем GET /pcbk/api/workplace раз в секунду до ready или
        failed; по сроку — AssertionError с последним ответом."""
        code, body, _ = self.call("POST", "/pcbk/api/workplace/wake")
        if code != 202:
            raise AssertionError(f"пробуждение: код {code}, {body}")
        deadline = time.monotonic() + timeout
        last: Any = body
        while True:
            code, last, _ = self.call("GET", "/pcbk/api/workplace")
            if code != 200:
                raise AssertionError(f"состояние места: код {code}, {last}")
            if last.get("state") in ("ready", "failed"):
                return last
            if time.monotonic() > deadline:
                raise AssertionError(f"место не готово за {timeout} с: {last}")
            time.sleep(WAKE_POLL_S)


def _parse(raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return text
