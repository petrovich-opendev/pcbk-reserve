"""Фикстуры сторожа: двойник прокси сокета sp-ro и локальный TLS-сервер."""
import json
import socket
import ssl
import subprocess
import threading
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


def self_signed(directory, name):
    """Самоподписанный сертификат на 90 дней; возвращает (cert, key)."""
    cert, key = directory / f"{name}-cert.pem", directory / f"{name}-key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
         "-nodes", "-days", "90", "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True)
    return str(cert), str(key)


@pytest.fixture
def tls_server(tmp_path):
    cert, key = self_signed(tmp_path, "edge")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.05)
    stop = threading.Event()

    def serve():
        # только рукопожатие: принять, отдать сертификат, закрыть
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
    yield SimpleNamespace(port=listener.getsockname()[1], cafile=cert)
    stop.set()
    thread.join()
    listener.close()


@pytest.fixture
def other_cert(tmp_path):
    cert, _ = self_signed(tmp_path, "other")
    return cert
