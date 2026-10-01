"""Headless Chrome по протоколу DevTools (CDP), только stdlib: запуск, вкладки, ввод, ожидания, сеть, CSP, снимки.

Для браузерных тестов веб-чата (Д6а; список разговоров — rename_thread, open_thread, click_text_in — Д6б): фикстуры
browser (на модуль) и page (на тест, вкладка закрывается) — в conftest.py. Все вкладки — сессии одного соединения с
браузером (Target.attachToTarget, flatten). Клиент WebSocket — свой, по RFC 6455 в том объёме, что нужен CDP: только
ws://127.0.0.1, текстовые кадры, длина до 64 бит.

Что видит страница:
- нарушения CSP — слушатель securitypolicyviolation, вставленный в каждый новый документ
  (Page.addScriptToEvaluateOnNewDocument); отчёт идёт в тест через привязку Runtime.addBinding и переживает переходы;
- ошибки — только Runtime.exceptionThrown и console.error страницы (Runtime.consoleAPICalled, type == "error");
  записи домена Log (сетевые 404, favicon.ico, сообщения самого браузера о CSP) сюда не попадают;
- сеть — Network.requestWillBeSent, requestWillBeSentExtraInfo (заголовки, ушедшие на самом деле: cookie, Origin,
  псевдозаголовки HTTP/2), responseReceived (код и протокол), dataReceived (порции тела).
"""
import base64
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

CHROME = "google-chrome"
# имя тестового стенда → 127.0.0.1 (порт в адресе, 18443); живые проверки передают своё имя (preflight M16)
STAND_HOST_RULES = "MAP stand.pcbk.test 127.0.0.1"
START_TIMEOUT_S = 30
CALL_TIMEOUT_S = 30
POLL_S = 0.1
WINDOW = "1280,900"
CSP_BINDING = "__pcbkCspReport"
# до скриптов страницы; привязку берём сразу — страница не подменит её после
CSP_LISTENER = """(() => {
  const report = globalThis.%s;
  if (typeof report !== "function") return;
  document.addEventListener("securitypolicyviolation", (e) => report(JSON.stringify({
    directive: e.violatedDirective, effective: e.effectiveDirective, blocked: e.blockedURI, source: e.sourceFile,
    line: e.lineNumber, sample: e.sample, disposition: e.disposition, page: location.pathname,
  })), true);
})();""" % CSP_BINDING
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# видимая доступная кнопка или ссылка с текстом (точно, без пробелов по краям) внутри корня: %s — выражение корня
# (document или querySelector), %s — текст JSON; ответ — середина элемента или null
_CLICKABLE_JS = ("(() => { const root = %s; if (!root) return null; const want = %s; const el = [...root.querySelectorAll("
                 "'button, a, [role=\"button\"], summary, input[type=\"submit\"], input[type=\"button\"]')].find((e) => "
                 "(e.innerText || e.value || '').trim() === want && e.getClientRects().length && !e.disabled); "
                 "if (!el) return null; el.scrollIntoView({block: 'center'}); const r = el.getBoundingClientRect(); "
                 "return [r.left + r.width / 2, r.top + r.height / 2]; })()")


class _Wire(str):
    """Сообщение CDP на проводе: в трассировке — только длина. В нём бывает пароль поля входа (Input.insertText), а
    pytest печатает аргументы кадров send и _frame, если отправка рвётся (причина CDPError)."""

    def __repr__(self) -> str:
        return f"<сообщение CDP: {len(self)} знаков>"


class _WireBytes(bytes):
    """То же сообщение в байтах кадра WebSocket."""

    def __repr__(self) -> str:
        return f"<кадр WebSocket: {len(self)} байт>"


class _Hidden(str):
    """Значение заголовка с секретом (cookie, authorization) в requests(headers=True): сравнивается как строка, в
    трассировке pytest — «<скрыт>» (итоговое ревью Д6а, P6)."""

    def __repr__(self) -> str:
        return "<скрыт>"


_SECRET_HEADERS = frozenset({"cookie", "authorization"})


class CDPError(RuntimeError):
    """Отказ CDP (ответ с error), срок ответа или обрыв соединения с браузером."""


def _mask(data: bytes, key: bytes) -> bytes:
    """Маска кадра WebSocket (XOR по кругу с 4-байтовым ключом) — одним большим целым, а не циклом по байтам."""
    n = len(data)
    if not n:
        return b""
    return (int.from_bytes(data, "big") ^ int.from_bytes((key * (n // 4 + 1))[:n], "big")).to_bytes(n, "big")


class _WebSocket:
    """Клиент WebSocket: рукопожатие, отправка текстовых кадров с маской, приём кадров (продолжения, ping/pong)."""

    def __init__(self, url: str, timeout: float = 10.0):
        u = urlsplit(url)
        if u.scheme != "ws" or u.hostname != "127.0.0.1" or not u.port:
            raise ValueError(f"только ws://127.0.0.1:<порт>/…, а не {url!r}")
        self._sock = socket.create_connection(("127.0.0.1", u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self._sock.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: 127.0.0.1:{u.port}\r\nUpgrade: websocket\r\n"
                            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                           .encode("ascii"))
        head = bytearray()
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk or len(head) > 65536:
                raise CDPError("браузер не ответил на Upgrade")
            head += chunk
        raw, _, rest = bytes(head).partition(b"\r\n\r\n")
        lines = raw.decode("latin-1").split("\r\n")
        if not re.match(r"HTTP/1\.1 101\b", lines[0]):
            raise CDPError(f"Upgrade отвергнут: {lines[0]}")
        fields = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        if fields.get("sec-websocket-accept") != accept:
            raise CDPError("неверный Sec-WebSocket-Accept")
        self._buf = bytearray(rest)
        self._send_lock = threading.Lock()
        self._sock.settimeout(None)               # чтение — в своём потоке и без срока; сроки — у ожидающих ответа

    def _frame(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 1 << 16:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        key = os.urandom(4)                       # кадры клиента — всегда с маской
        with self._send_lock:
            self._sock.sendall(head + key + _mask(payload, key))

    def send(self, text: str) -> None:
        self._frame(0x1, _WireBytes(text.encode("utf-8")))

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("браузер закрыл соединение")
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def recv(self) -> str:
        """Одно текстовое сообщение (с продолжениями); ping — ответ pong; close — ConnectionError."""
        parts: list[bytes] = []
        while True:
            b0, b1 = self._read(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            key = self._read(4) if b1 & 0x80 else b""
            data = self._read(n)
            if key:
                data = _mask(data, key)
            if opcode == 0x9:
                self._frame(0xA, data)
            elif opcode == 0x8:
                raise ConnectionError("браузер закрыл WebSocket")
            elif opcode in (0x1, 0x0):
                parts.append(data)
                if b0 & 0x80:
                    return b"".join(parts).decode("utf-8")
            elif opcode != 0xA:
                raise CDPError(f"кадр WebSocket с кодом {opcode:#x} — ждали текст")

    def close(self) -> None:
        try:
            self._frame(0x8, b"")
        except OSError:
            pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


class _Connection:
    """Соединение с браузером: вызовы по id с ожиданием ответа, события — обработчику своей сессии (поток чтения)."""

    def __init__(self, ws: _WebSocket):
        self._ws = ws
        self._lock = threading.Lock()
        self._next = 0
        self._waiting: dict[int, list] = {}
        self._handlers: dict[str, Callable[[str, dict], None]] = {}
        self._closed: BaseException | None = None
        self.handler_errors: list[str] = []
        threading.Thread(target=self._reader, name="cdp-reader", daemon=True).start()

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = CALL_TIMEOUT_S) -> dict:
        slot = [threading.Event(), None]
        with self._lock:
            if self._closed is not None:
                raise CDPError(f"{method}: соединение с браузером закрыто ({self._closed})")
            self._next += 1
            mid = self._next
            self._waiting[mid] = slot
        msg = {"id": mid, "method": method, "params": params or {}}
        if session is not None:
            msg["sessionId"] = session
        try:
            self._ws.send(_Wire(json.dumps(msg)))
        except OSError as e:
            with self._lock:
                self._waiting.pop(mid, None)
            raise CDPError(f"{method}: {e}") from e
        if not slot[0].wait(timeout):
            with self._lock:
                self._waiting.pop(mid, None)
            raise CDPError(f"{method}: нет ответа за {timeout} с")
        reply = slot[1]
        if reply is None:
            raise CDPError(f"{method}: соединение с браузером закрыто ({self._closed})")
        if "error" in reply:
            raise CDPError(f"{method}: {reply['error'].get('message')}")
        return reply.get("result", {})

    def on(self, session: str, handler: Callable[[str, dict], None]) -> None:
        self._handlers[session] = handler

    def off(self, session: str) -> None:
        self._handlers.pop(session, None)

    def _reader(self) -> None:
        try:
            while True:
                msg = json.loads(self._ws.recv())
                if "id" in msg:
                    with self._lock:
                        slot = self._waiting.pop(msg["id"], None)
                    if slot is not None:
                        slot[1] = msg
                        slot[0].set()
                    continue
                handler = self._handlers.get(msg.get("sessionId", ""))
                if handler is not None:
                    try:
                        handler(msg.get("method", ""), msg.get("params") or {})
                    except Exception as e:          # сбой обработчика не рвёт чтение: он виден в handler_errors
                        self.handler_errors.append(f"{msg.get('method')}: {e!r}")
        except BaseException as e:                  # обрыв: все ждущие получают отказ сразу, а не по сроку
            with self._lock:
                self._closed = e
                waiting = list(self._waiting.values())
                self._waiting.clear()
            for slot in waiting:
                slot[0].set()

    def close(self) -> None:
        self._ws.close()


def _arg_text(arg: dict) -> str:
    if "value" in arg:
        return str(arg["value"])
    return str(arg.get("description") or arg.get("type", ""))


def _lower(headers: dict | None) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


class Page:
    """Вкладка браузера. Ожидания — опросом раз в POLL_S; по сроку — AssertionError с тем, чего ждали."""

    def __init__(self, conn: _Connection, target_id: str, session_id: str, context_id: str | None = None):
        self._conn = conn
        self.target_id = target_id
        self.session_id = session_id
        self._context_id = context_id
        self._lock = threading.Lock()
        self._csp: list[dict] = []
        self._errors: list[str] = []
        self._requests: dict[str, dict] = {}        # requestId → последняя запись (после перенаправления — новая)
        self._order: list[dict] = []                # все записи в порядке отправки
        self._extra: dict[str, dict[str, str]] = {}  # заголовки ExtraInfo, пришедшие раньше requestWillBeSent
        self._loaded: set[str] = set()              # loaderId документов, дошедших до load
        self._closed = False
        conn.on(session_id, self._on_event)
        for domain in ("Page", "Runtime", "Network"):
            self._call(f"{domain}.enable")
        self._call("Page.setLifecycleEventsEnabled", {"enabled": True})   # load — с loaderId своего документа
        self._call("Runtime.addBinding", {"name": CSP_BINDING})
        self._call("Page.addScriptToEvaluateOnNewDocument", {"source": CSP_LISTENER})

    # --- события (поток чтения) ---

    def _on_event(self, method: str, params: dict) -> None:
        with self._lock:
            if method == "Runtime.bindingCalled" and params.get("name") == CSP_BINDING:
                self._csp.append(json.loads(params["payload"]))
            elif method == "Runtime.exceptionThrown":
                d = params.get("exceptionDetails") or {}
                self._errors.append((d.get("exception") or {}).get("description") or d.get("text") or "исключение")
            elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
                self._errors.append(" ".join(_arg_text(a) for a in params.get("args") or []))
            elif method == "Page.lifecycleEvent" and params.get("name") == "load":
                self._loaded.add(params.get("loaderId", ""))
            elif method.startswith("Network."):
                self._on_network(method, params)

    def _on_network(self, method: str, params: dict) -> None:
        rid = params.get("requestId", "")
        rec = self._requests.get(rid)
        if method == "Network.requestWillBeSent":
            if rec is not None and params.get("redirectResponse"):     # тот же requestId — следующий шаг перенаправления
                rec["status"] = params["redirectResponse"].get("status")
                rec["protocol"] = params["redirectResponse"].get("protocol")
            req = params.get("request") or {}
            rec = {"method": req.get("method", ""), "url": req.get("url", ""), "path": urlsplit(req.get("url", "")).path,
                   "status": None, "protocol": None, "headers": self._extra.pop(rid, None) or _lower(req.get("headers")),
                   "chunks": 0}
            self._requests[rid] = rec
            self._order.append(rec)
        elif method == "Network.requestWillBeSentExtraInfo":
            if rec is None:
                self._extra[rid] = _lower(params.get("headers"))
            else:
                rec["headers"] = _lower(params.get("headers"))
        elif rec is None:
            return
        elif method == "Network.responseReceived":
            resp = params.get("response") or {}
            rec["status"] = resp.get("status")
            rec["protocol"] = resp.get("protocol")
        elif method == "Network.dataReceived":
            rec["chunks"] += 1

    # --- вызовы ---

    def _call(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT_S) -> dict:
        return self._conn.call(method, params, session=self.session_id, timeout=timeout)

    def wait(self, probe: Callable[[], object], timeout: float, what: str) -> object:
        """Опрос probe() до истинного значения, которое и возвращается; отказ CDP во время перехода страницы — «ещё
        нет». По сроку — AssertionError с what."""
        deadline = time.monotonic() + timeout
        last_error = ""
        while True:
            try:
                value = probe()
                if value:
                    return value
            except CDPError as e:
                last_error = f" (последний отказ CDP: {e})"
            if time.monotonic() > deadline:
                raise AssertionError(f"за {timeout} с не дождались: {what}{last_error}")
            time.sleep(POLL_S)

    def eval(self, expr: str, timeout: float = CALL_TIMEOUT_S):
        """Выражение в странице (Promise дожидается); значение — как JSON. Исключение выражения — CDPError."""
        r = self._call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True},
                       timeout=timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise CDPError(f"eval: {(d.get('exception') or {}).get('description') or d.get('text')}")
        return (r.get("result") or {}).get("value")

    # --- навигация и ввод ---

    def goto(self, url: str, timeout: float = 30) -> None:
        """Переход и ожидание load именно этого документа (по loaderId перехода): запоздалый load прошлой загрузки
        не засчитывается. Переход внутри документа (только #…) loaderId не даёт — ждать нечего."""
        r = self._call("Page.navigate", {"url": url})
        if r.get("errorText"):
            raise CDPError(f"переход на {url}: {r['errorText']}")
        loader = r.get("loaderId")
        if loader:
            self.wait(lambda: loader in self._loaded, timeout, f"загрузка {url}")

    def url(self) -> str:
        return self.eval("location.href")

    def _focus(self, selector: str, timeout: float) -> None:
        self.wait_selector(selector, timeout)
        js = (f"(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return false; "
              "e.focus(); return document.activeElement === e; })()")
        if not self.eval(js):
            raise AssertionError(f"{selector}: не принял фокус")

    def type(self, selector: str, text: str, timeout: float = 10) -> None:
        """Фокус в поле и ввод текста, как с клавиатуры (Input.insertText: события input — у React тоже). Пароль —
        строкой с безопасным repr (conftest.Secret): этот кадр и params вызова pytest печатает при сбое как есть, а
        провод (send, _frame) печатается только длиной."""
        self._focus(selector, timeout)
        self._call("Input.insertText", {"text": text})

    def press_enter(self, selector: str, timeout: float = 10) -> None:
        """Enter в поле: у поля формы — отправка формы, у поля чата — отправка сообщения."""
        self._focus(selector, timeout)
        key = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        self._call("Input.dispatchKeyEvent", {"type": "keyDown", "text": "\r", **key})
        self._call("Input.dispatchKeyEvent", {"type": "keyUp", **key})

    def click_text(self, text: str, timeout: float = 10) -> None:
        """Щелчок мышью по видимой доступной кнопке или ссылке с этим текстом (точно, без пробелов по краям)."""
        self._click_found(_CLICKABLE_JS % ("document", json.dumps(text)), timeout, f"кнопка или ссылка «{text}»")

    def click_text_in(self, css: str, text: str, timeout: float = 10) -> None:
        """Как click_text, но кнопка или ссылка — внутри первого элемента css (строка списка, а не первая похожая
        кнопка на странице)."""
        root = f"document.querySelector({json.dumps(css)})"
        self._click_found(_CLICKABLE_JS % (root, json.dumps(text)), timeout, f"кнопка или ссылка «{text}» в {css}")

    def _click_found(self, js: str, timeout: float, what: str) -> None:
        x, y = self.wait(lambda: self.eval(js), timeout, what)
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self._call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left",
                                                     "buttons": 1 if kind == "mousePressed" else 0, "clickCount": 1})

    def click(self, css: str, timeout: float = 10) -> None:
        """Щелчок мышью по середине видимого элемента css."""
        js = (f"(() => {{ const e = document.querySelector({json.dumps(css)}); "
              "if (!e || !e.getClientRects().length) return null; e.scrollIntoView({block: 'center'}); "
              "const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()")
        self._click_found(js, timeout, f"видимый {css}")

    # --- список разговоров чата (Д6-R52; договор строки — задача Б3) ---

    @staticmethod
    def thread_row(sid: str) -> str:
        """CSS строки списка разговоров по id разговора OpenCode (data-session-id), а не «первой» строки."""
        return f'[data-session-id="{sid}"]'

    def rename_thread(self, sid: str, title: str, timeout: float = 10) -> None:
        """«Переименовать» в строке разговора sid: поле input[name=title] открывается в фокусе с выделенным именем —
        ввод (Input.insertText) заменяет имя целиком; Enter сохраняет."""
        row = self.thread_row(sid)
        self.click_text_in(row, "Переименовать", timeout)
        field = f'{row} input[name="title"]'
        self.wait_selector(field, timeout)
        selected = (f"(() => {{ const e = document.querySelector({json.dumps(field)}); return !!e && "
                    "document.activeElement === e && e.selectionStart === 0 && e.selectionEnd === e.value.length; })()")
        self.wait(lambda: self.eval(selected), timeout, f"{field}: в фокусе, имя выделено")
        self.type(field, title, timeout)
        self.press_enter(field, timeout)
        self.wait(lambda: not self.eval(f"!!document.querySelector({json.dumps(field)})"), timeout,
                  f"{field} закрылось после Enter")

    def open_thread(self, sid: str, timeout: float = 10) -> None:
        """Открыть разговор sid щелчком по его имени в списке (button.thread-open строки)."""
        self.click(f"{self.thread_row(sid)} button.thread-open", timeout)

    def screenshot(self, path: str | Path) -> None:
        data = self._call("Page.captureScreenshot", {"format": "png"}, timeout=60)["data"]
        Path(path).write_bytes(base64.b64decode(data))

    # --- ожидания ---

    def wait_url(self, suffix: str, timeout: float = 30) -> str:
        """Адрес страницы кончается на suffix (целиком или без ?… и #…)."""
        def probe():
            url = self.url() or ""
            return url if url.endswith(suffix) or url.split("#")[0].split("?")[0].endswith(suffix) else None
        return self.wait(probe, timeout, f"адрес …{suffix}")

    def wait_selector(self, css: str, timeout: float = 10) -> None:
        """Элемент есть и виден (у скрытого hidden или display: none прямоугольников нет)."""
        js = (f"(() => {{ const e = document.querySelector({json.dumps(css)}); "
              "return !!e && e.getClientRects().length > 0; })()")
        self.wait(lambda: self.eval(js), timeout, f"видимый {css}")

    def _text(self, css: str | None = None) -> str:
        """Видимый текст страницы или первого элемента css (нет элемента — пусто)."""
        root = "document.body" if css is None else f"document.querySelector({json.dumps(css)})"
        return self.eval(f"(() => {{ const e = {root}; return e ? e.innerText : ''; }})()") or ""

    def wait_text(self, pattern: str, timeout: float = 10, css: str | None = None) -> str:
        """Видимый текст страницы (или первого элемента css) совпал с регулярным выражением; возвращает совпавший
        кусок."""
        where = "" if css is None else f" в {css}"
        found = self.wait(lambda: re.search(pattern, self._text(css)), timeout, f"текст /{pattern}/{where}")
        return found.group(0)

    def has_text(self, pattern: str, within: float = 0, css: str | None = None) -> bool:
        """True, если текст страницы (или первого элемента css) совпал с выражением за within с; within=0 — одна
        проверка сейчас. В отличие от wait_text, срок — не сбой: для «этого текста здесь нет». Отказ CDP на последней
        пробе — исключение, а не False: «текста нет» не выводится из того, что текст не прочитан (доводка Б4, m2)."""
        deadline = time.monotonic() + within
        while True:
            failed: CDPError | None = None
            try:
                if re.search(pattern, self._text(css)):
                    return True
            except CDPError as e:                   # переход страницы — ещё нет, пока срок не вышел
                failed = e
            if time.monotonic() >= deadline:
                if failed is not None:
                    raise failed
                return False
            time.sleep(POLL_S)

    def wait_gone(self, pattern: str, timeout: float = 10) -> None:
        self.wait(lambda: re.search(pattern, self._text()) is None, timeout, f"исчезновение /{pattern}/")

    # --- наблюдение ---

    def csp(self) -> list[dict]:
        """Нарушения CSP во всех документах вкладки с её открытия."""
        with self._lock:
            return [dict(v) for v in self._csp]

    def console_errors(self) -> list[str]:
        """Необработанные исключения и console.error страницы; записи Log (сеть, favicon.ico) не считаются."""
        with self._lock:
            return list(self._errors)

    def requests(self, prefix: str, headers: bool = False) -> list[dict]:
        """Запросы вкладки, чей путь (без ?…) начинается с prefix: method, path, status, protocol, url; status None —
        ответа не было. Заголовки (настоящие, имена в нижнем регистре) — только при headers=True, и cookie и
        authorization в них — строкой с безопасным repr: записи попадают в аргументы и переменные кадров, а pytest
        печатает их при сбое (итоговое ревью Д6а, P6)."""
        def view(r: dict) -> dict:
            out = {k: v for k, v in r.items() if k not in ("chunks", "headers")}
            if headers:
                out["headers"] = {k: (_Hidden(v) if k in _SECRET_HEADERS else v) for k, v in r["headers"].items()}
            return out
        with self._lock:
            return [view(r) for r in self._order if r["path"].startswith(prefix)]

    def chunks(self, path: str) -> int:
        """Сколько порций тела (Network.dataReceived) пришло на запросы с ровно этим путём."""
        with self._lock:
            return sum(r["chunks"] for r in self._order if r["path"] == path)

    def close(self) -> None:
        """Закрыть вкладку (и её отдельный контекст cookie, если был)."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.call("Target.closeTarget", {"targetId": self.target_id}, timeout=10)
            # closeTarget отвечает раньше, чем вкладка ушла; ждём ухода — её потоки к шлюзу закрыты до следующего теста
            deadline = time.monotonic() + 10
            while (time.monotonic() < deadline and self.target_id in
                   {t["targetId"] for t in self._conn.call("Target.getTargets")["targetInfos"]}):
                time.sleep(POLL_S)
        except CDPError:
            pass
        self._conn.off(self.session_id)
        if self._context_id is not None:
            try:
                self._conn.call("Target.disposeBrowserContext", {"browserContextId": self._context_id}, timeout=10)
            except CDPError:
                pass

    def __enter__(self) -> "Page":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Browser:
    """Headless Chrome с временным профилем; контекстный менеджер. Вкладки одного контекста делят cookie;
    page(isolated=True) — вкладка со своим контекстом (свои cookie), закрывается вместе с ним."""

    def __init__(self, host_rules: str = STAND_HOST_RULES, chrome: str = CHROME):
        self.host_rules = host_rules
        self._chrome = chrome
        self._proc: subprocess.Popen | None = None
        self._profile: str | None = None
        self._conn: _Connection | None = None
        self._pages: list[Page] = []

    def __enter__(self) -> "Browser":
        try:
            self.start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        self._profile = tempfile.mkdtemp(prefix="pcbk-chrome-")
        log = open(os.path.join(self._profile, "chrome.log"), "wb")
        with log:
            self._proc = subprocess.Popen(
                [self._chrome, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self._profile}",
                 "--ignore-certificate-errors", f"--host-resolver-rules={self.host_rules}",
                 "--no-first-run", "--no-default-browser-check",
                 # тест ходит только на стенд: без фоновых запросов Chrome к своим службам и без прокси окружения
                 "--disable-background-networking", "--disable-component-update", "--no-proxy-server",
                 f"--window-size={WINDOW}", "about:blank"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        port_file = Path(self._profile) / "DevToolsActivePort"
        deadline = time.monotonic() + START_TIMEOUT_S
        while True:
            lines = port_file.read_text().splitlines() if port_file.exists() else []
            if len(lines) >= 2 and lines[0].isdigit() and lines[1].startswith("/devtools/browser/"):
                break
            if self._proc.poll() is not None:
                tail = Path(self._profile, "chrome.log").read_text(errors="replace")[-2000:]
                raise CDPError(f"Chrome вышел с кодом {self._proc.returncode}:\n{tail}")
            if time.monotonic() > deadline:
                raise CDPError(f"Chrome не открыл порт отладки за {START_TIMEOUT_S} с")
            time.sleep(POLL_S)
        self._conn = _Connection(_WebSocket(f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"))

    def page(self, isolated: bool = False) -> Page:
        """Новая вкладка (about:blank) с включёнными Page, Runtime, Network и слушателем CSP."""
        if self._conn is None:
            raise CDPError("браузер не запущен")
        context = None
        params = {"url": "about:blank"}
        if isolated:
            context = self._conn.call("Target.createBrowserContext")["browserContextId"]
            params["browserContextId"] = context
        target = self._conn.call("Target.createTarget", params)["targetId"]
        session = self._conn.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        page = Page(self._conn, target, session, context)
        self._pages.append(page)
        return page

    @property
    def handler_errors(self) -> list[str]:
        """Сбои разбора событий (обработчик страницы бросил): отчёт CSP или ошибка консоли могли пропасть."""
        return list(self._conn.handler_errors) if self._conn is not None else []

    def close(self) -> None:
        for page in self._pages:
            page.close()
        self._pages.clear()
        if self._conn is not None:
            try:
                self._conn.call("Browser.close", timeout=5)
            except CDPError:
                pass
            self._conn.close()
            self._conn = None
        if self._proc is not None:
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            try:                                   # вместе с дочерними процессами (рендеры, zygote)
                os.killpg(self._proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            self._proc = None
        if self._profile is not None:
            shutil.rmtree(self._profile, ignore_errors=True)
            self._profile = None
