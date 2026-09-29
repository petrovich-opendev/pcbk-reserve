"""Фикстуры сторожа: двойник прокси сокета sp-ro и локальный TLS-сервер."""
import json
import socket
import ssl
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from helpers import insp

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
