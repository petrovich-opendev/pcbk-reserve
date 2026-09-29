"""Фикстуры сторожа: двойники прокси сокета sp-ro, локальный TLS-сервер, страница для браузера."""
import json
import socket
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from helpers import insp, now_utc
from pcbk_watchdog.checks import Check
from pcbk_watchdog.page import Snapshot, render_html

# ответы двойника sp-ro: путь → (код, тело)
PROXY_ROUTES = {
    "/v1.44/_ping": (200, b"OK"),
    "/v1.44/containers/pcbk-sp-ctl/json": (200, json.dumps(insp(running=True)).encode()),
    "/v1.44/containers/pcbk-student-09/json":
        (404, b'{"message":"No such container: pcbk-student-09"}'),
    "/v1.44/containers/pcbk-forbidden/json": (403, b"Forbidden"),
}


@pytest.fixture
def fake_proxy():
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            code, body = PROXY_ROUTES.get(self.path, (404, b"not found"))
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}", paths=paths)
    server.shutdown()
    server.server_close()
    thread.join()


# срок действия сертификатов для фикстур
VALID_90_DAYS = ("-days", "90")
EXPIRED = ("-not_before", "20250101000000Z", "-not_after", "20250201000000Z")
NOT_YET_VALID = ("-not_before", "20990101000000Z", "-not_after", "20991231000000Z")


def self_signed(directory, name, validity=VALID_90_DAYS):
    """Самоподписанный сертификат с заданным сроком; возвращает (cert, key)."""
    cert, key = directory / f"{name}-cert.pem", directory / f"{name}-key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", *validity, "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True)
    return str(cert), str(key)


@contextmanager
def serve_tls(cert, key):
    """Локальный TLS на свободном порту: только рукопожатие, затем закрыть."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.05)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conn.settimeout(2)
            try:
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except OSError:
                pass
            finally:
                conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(port=listener.getsockname()[1], cafile=cert)
    finally:
        stop.set()
        thread.join()
        listener.close()


@pytest.fixture
def tls_server(tmp_path):
    with serve_tls(*self_signed(tmp_path, "edge")) as server:
        yield server


@pytest.fixture
def expired_tls_server(tmp_path):
    with serve_tls(*self_signed(tmp_path, "expired", EXPIRED)) as server:
        yield server


@pytest.fixture
def future_tls_server(tmp_path):   # сертификат ещё не вступил в силу
    with serve_tls(*self_signed(tmp_path, "future", NOT_YET_VALID)) as server:
        yield server


@pytest.fixture
def other_cert(tmp_path):
    cert, _ = self_signed(tmp_path, "other")
    return cert


@contextmanager
def serve_http(handler):
    """HTTP-двойник на 127.0.0.1 и свободном порту."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def fake_core():
    """Двойник ручек pcbk-core: /health/historian — set(obj) или set_raw(text),
    /healthz/data — set_health(code, obj); прочие пути — 404."""
    replies = {"/health/historian": (200, "application/json", b"{}"),
               "/healthz/data": (200, "application/json", b'{"ok": true, "detail": "ok"}')}

    def as_json(obj) -> bytes:
        return json.dumps(obj, ensure_ascii=False).encode()   # как у FastAPI: UTF-8, не \u

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code, ctype, body = replies.get(self.path, (404, "text/plain", b"not found"))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with serve_http(Handler) as base:
        yield SimpleNamespace(
            base=base, url=base + "/health/historian",
            set=lambda obj: replies.update({"/health/historian": (200, "application/json", as_json(obj))}),
            set_raw=lambda text: replies.update(
                {"/health/historian": (200, "text/plain; charset=utf-8", text.encode())}),
            set_health=lambda code, obj: replies.update(
                {"/healthz/data": (code, "application/json", as_json(obj))}))


@pytest.fixture
def silent_proxy_url():
    """Сокет принимает соединения и молчит — повисший прокси."""
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.05)
    stop, conns = threading.Event(), []

    def accept():
        while not stop.is_set():
            try:
                conns.append(listener.accept()[0])
            except TimeoutError:
                continue
            except OSError:
                return

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    stop.set()
    thread.join()
    for conn in conns:
        conn.close()
    listener.close()


# задержка ответа медленного прокси, с
SLOW_REPLY_S = 0.3


@pytest.fixture
def slow_proxy_url():
    """Прокси отвечает верно, но каждый ответ — через SLOW_REPLY_S."""
    body = json.dumps(insp(running=True)).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(SLOW_REPLY_S)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with serve_http(Handler) as url:
        yield url


STATUS_JS = Path(__file__).parent.parent / "pcbk_watchdog" / "static" / "status.js"


@pytest.fixture
def fake_page_server():
    """Свежая страница (полоса скрыта), а /status.json уже говорит stale: true."""
    now = now_utc()
    html = render_html(Snapshot(now, (Check("edge", "Входной прокси", "ok", "отвечает"),)),
                       [], now, 30, ZoneInfo("UTC")).encode()
    stale = json.dumps({"checked_at": None, "stale": True, "stale_after_s": 30,
                        "overall": "unknown", "checks": []}).encode()
    routes = {"/status": ("text/html; charset=utf-8", html),
              "/status.js": ("text/javascript; charset=utf-8", STATUS_JS.read_bytes()),
              "/status.json": ("application/json", stale)}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in routes:
                self.send_error(404)
                return
            ctype, body = routes[self.path]
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with serve_http(Handler) as url:
        yield SimpleNamespace(url=url)
