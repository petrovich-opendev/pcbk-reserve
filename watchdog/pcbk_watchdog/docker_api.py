"""Чтение состояния контейнеров через прокси сокета sp-ro (только GET)."""
import http.client
import json
import re
from urllib.parse import urlsplit

# префикс, который принимают все Docker 29.x
API_PREFIX = "/v1.44"
# та же регулярка, что в правилах прокси
CONTAINER_NAME = re.compile(r"pcbk-[a-z0-9-]+")


class DockerUnavailable(Exception):
    """Прокси сокета не ответил или отказал — состояние контейнеров неизвестно."""


class DockerReader:
    def __init__(self, base_url: str, timeout: float = 3.0):
        parts = urlsplit(base_url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError(f"DockerReader: нужен адрес http://, получен {base_url!r}")
        self._host = parts.hostname
        self._port = parts.port or 80
        self.timeout = timeout

    def _get(self, path: str) -> tuple[int, bytes]:
        # http.client не ходит по перенаправлениям и не берёт прокси из окружения
        conn = http.client.HTTPConnection(self._host, self._port, timeout=self.timeout)
        try:
            conn.request("GET", API_PREFIX + path)
            resp = conn.getresponse()
            return resp.status, resp.read()
        except (OSError, http.client.HTTPException) as e:
            raise DockerUnavailable(f"{path}: {type(e).__name__}") from e
        finally:
            conn.close()

    def ping(self) -> None:
        status, body = self._get("/_ping")
        if status != 200 or body.strip() != b"OK":
            raise DockerUnavailable(f"/_ping: HTTP {status}, ответ не OK")

    def inspect(self, name: str) -> dict | None:
        if not CONTAINER_NAME.fullmatch(name):
            raise ValueError(f"чужое имя контейнера: {name!r}")
        status, body = self._get(f"/containers/{name}/json")
        if status == 404:
            return None
        if status != 200:   # 403/405 — отказ прокси, 5xx — сбой Docker
            raise DockerUnavailable(f"inspect {name}: HTTP {status}")
        try:
            return json.loads(body)
        except ValueError as e:
            raise DockerUnavailable(f"inspect {name}: не JSON") from e
