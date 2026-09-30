"""Клиент OpenRouter: поток чата, дозапрос цены, опрос /key и /credits, разбор «кончились деньги».

Конструктор без ввода-вывода (П22): httpx.AsyncClient читает файл CA certifi и привязывается к циклу
событий, поэтому он создаётся при первом запросе, в том цикле, где работает; aclose() закрывает его.
Прокси окружения не берутся (trust_env=False, П14): путь наружу — только мимо охраны выхода.

Тексты исключений (П21): str(UpstreamError) — «<вид> <код>»; текст OpenRouter — только атрибутом
message (первые 300 знаков: его видит клиент в отказе rejected, в журнал он не идёт); тела запроса,
ключа и чанков в исключениях нет, исключения httpx к ним не цепляются (как HistorianError Д3а).
"""
import asyncio
import json
import math
import re
from typing import Literal

import anyio
import httpx

from pcbk_core.llm.ledger import Usage

UpstreamKind = Literal["money_out", "key_limit", "in_flight", "key_rejected", "region", "rate", "rejected", "server",
                       "unreachable"]

KEY_LIMIT_RE = re.compile(r"key limit exceeded|budget limit exceeded", re.I)
CREDITS_RE = re.compile(r"insufficient credits", re.I)
REGION_RE = re.compile(r"not available in your region", re.I)

CHAT_TIMEOUT = httpx.Timeout(connect=10, read=120, write=30, pool=10)
MAX_CONNECTIONS = 40            # 10 мест × 3 одновременных вызова, ops и опрос
POLL_TIMEOUT_S = 15.0           # срок одного запроса /key, /credits, /generation — целиком, не read=120 чата
ERROR_BODY_BYTES = 64 * 1024    # тело ответа об ошибке и ответов опроса читается не дальше
MESSAGE_CHARS = 300
RETRY_AFTER_CHARS = 32
DRILL_MESSAGE = "Insufficient credits (учения)"

# соединение не открылось или тело не ушло целиком — модель запроса не видела, денег нет (sent = False)
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol,
             httpx.LocalProtocolError, httpx.WriteError, httpx.WriteTimeout, httpx.ProxyError)


class UpstreamError(Exception):
    """Отказ или недоступность OpenRouter. str(e) — «<kind> <status>», без текстов (П21).

    message — первые 300 знаков текста OpenRouter, только атрибутом; retry_after — заголовок
    Retry-After; sent — тело могло дойти до модели (ответа нет после отправки: ReadTimeout, обрыв):
    по нему роль ставит cost_source = "unknown", а не "none".
    """

    def __init__(self, kind: UpstreamKind, status: int, message: str = "", *, retry_after: str | None = None,
                 sent: bool = False):
        super().__init__(f"{kind} {status}")
        self.kind: UpstreamKind = kind
        self.status = status
        self.message = message[:MESSAGE_CHARS]
        self.retry_after = retry_after
        self.sent = sent

    def __str__(self) -> str:
        return f"{self.kind} {self.status}"


def _status_code(status: object) -> int:
    """Код ответа или error.code события посреди потока: целое или строка цифр; иначе 0."""
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    if isinstance(status, str) and status.strip().isascii() and status.strip().isdigit():
        return int(status.strip())
    return 0


def classify(status: int, error: dict | None) -> UpstreamKind:
    """Вид отказа по коду и объекту error OpenRouter — первая подходящая строка таблицы.

    Код не 4xx и не из таблицы (3xx без перехода, 2xx вместо 200, код события не числом) — server:
    клиенту не отдаются ни чужой код, ни «отказ по запросу».
    """
    code = _status_code(status)
    error = error if isinstance(error, dict) else {}
    message = error.get("message")
    message = message if isinstance(message, str) else ""
    meta = error.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    if code == 401:
        return "key_rejected"
    if code == 402 and meta.get("limit_source") == "in_flight_budget":
        return "in_flight"
    if code in (402, 403) and KEY_LIMIT_RE.search(message):
        return "key_limit"
    if code == 402 or (code == 403 and CREDITS_RE.search(message)):
        return "money_out"
    if code == 403 and REGION_RE.search(message):
        return "region"
    if code == 429:
        return "rate"
    if code == 408 or code >= 500:
        return "server"
    if 400 <= code < 500:
        return "rejected"
    return "server"


def _json_object(raw: bytes) -> dict | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception:           # не UTF-8, не JSON, слишком глубокая вложенность
        return None
    return value if isinstance(value, dict) else None


def _error_of(raw: bytes) -> tuple[dict | None, str]:
    """Объект error из тела ответа и текст для message: error.message, иначе само тело (обрезанное)."""
    body = _json_object(raw)
    error = body.get("error") if body is not None else None
    error = error if isinstance(error, dict) else None
    message = error.get("message") if error is not None else None
    if not isinstance(message, str):
        message = raw[:MESSAGE_CHARS * 4].decode("utf-8", "replace")
    return error, message[:MESSAGE_CHARS]


def _number(value: object) -> float | None:
    """Конечное число (не bool) → float; иначе None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


async def _read_capped(response: httpx.Response, limit: int = ERROR_BODY_BYTES) -> bytes:
    """Тело ответа не дальше limit байт; остальное не читается."""
    parts, size = [], 0
    async for part in response.aiter_bytes():
        parts.append(part)
        size += len(part)
        if size >= limit:
            break
    return b"".join(parts)[:limit]


def _clean_key(key: str, name: str) -> str:
    """Ключ из файла без пробелов, переводов строк и BOM по краям; только печатные знаки ASCII — иначе
    ValueError без самого ключа (ошибка настройки роли, а не UnicodeEncodeError в заголовке)."""
    key = key.strip().strip("\ufeff").strip()
    if not (key.isascii() and key.isprintable()):
        raise ValueError(f"{name} — не печатные знаки ASCII")
    return key


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


class Upstream:
    """Запросы к OpenRouter с ключом прокси (чат, /generation, /key) и ключом управления (/credits).

    client — готовый httpx.AsyncClient (тесты): берётся как есть, Upstream его не закрывает и не меняет.
    drill="402" — учение: open_chat сразу бросает money_out 402, в сеть запрос не уходит.
    """

    def __init__(self, base_url: str, key: str, mgmt_key: str | None, *, drill: str = "",
                 client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self._key = _clean_key(key, "ключ OpenRouter")
        self._mgmt_key = _clean_key(mgmt_key or "", "ключ управления OpenRouter") or None   # пустой файл — ключа нет
        self.drill = drill
        self._client = client
        self._own = client is None
        self._loop: asyncio.AbstractEventLoop | None = None

    def __repr__(self) -> str:                  # ключи — никогда
        return f"Upstream({self.base_url!r}, mgmt_key={'yes' if self._mgmt_key else 'no'}, drill={self.drill!r})"

    def _http(self) -> httpx.AsyncClient:
        """Свой клиент — в цикле событий, где идёт запрос. Клиент другого цикла (прошлый anyio.run) не
        годится: его соединения привязаны к тому циклу — он оставляется, берётся новый."""
        if not self._own:
            return self._client
        loop = _running_loop()
        if self._client is not None and self._loop is not loop:
            self._client = None
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=CHAT_TIMEOUT,
                                             limits=httpx.Limits(max_connections=MAX_CONNECTIONS),
                                             trust_env=False, follow_redirects=False)
            self._loop = loop
        return self._client

    def _headers(self, key: str, accept: str) -> dict[str, str]:
        # identity: байты aiter_raw() — сам поток SSE, не сжатый gzip (их отдают клиенту и наблюдателю)
        return {"Authorization": f"Bearer {key}", "Accept": accept, "Accept-Encoding": "identity"}

    async def open_chat(self, body: dict) -> httpx.Response:
        """POST /chat/completions: ответ 200 — открытым потоком (закрывает вызывающий). Иначе тело ошибки
        (до 64 КиБ) читается, ответ закрывается и бросается UpstreamError по classify; нет связи или
        срок — UpstreamError("unreachable", 0, sent=…)."""
        if self.drill == "402":
            raise UpstreamError("money_out", 402, DRILL_MESSAGE)
        stream = body.get("stream") is True
        failure: UpstreamError | None = None
        response: httpx.Response | None = None
        try:
            client = self._http()
            headers = {**self._headers(self._key, "text/event-stream" if stream else "application/json"),
                       "Content-Type": "application/json"}
            request = client.build_request("POST", self.base_url + "/chat/completions", json=body, headers=headers)
            response = await client.send(request, stream=True)
        except UnicodeError:                        # заголовок не кодируется: ключ, минуя конструктор
            failure = UpstreamError("key_rejected", 0)
        except (httpx.HTTPError, httpx.InvalidURL) as err:
            failure = UpstreamError("unreachable", 0, sent=not isinstance(err, (*_NOT_SENT, httpx.InvalidURL)))
        if failure is not None:
            raise failure                           # вне except: без __context__ с запросом httpx
        if response.status_code == 200:
            return response
        try:
            with anyio.fail_after(POLL_TIMEOUT_S):  # тело ошибки по байту не держит вызов read=120 на кусок
                raw = await _read_capped(response)
        except (httpx.HTTPError, TimeoutError):
            raw = b""
        finally:
            await response.aclose()
        error, message = _error_of(raw)
        retry_after = response.headers.get("retry-after")
        raise UpstreamError(classify(response.status_code, error), response.status_code, message,
                            retry_after=retry_after[:RETRY_AFTER_CHARS] if retry_after is not None else None)

    async def _get(self, path: str, key: str, params: dict[str, str] | None = None) -> tuple[int, bytes]:
        """GET с ключом key: (код, тело до 64 КиБ). Срок — POLL_TIMEOUT_S на весь запрос; нет связи или
        срок — UpstreamError("unreachable", 0)."""
        failure: UpstreamError | None = None
        try:
            with anyio.fail_after(POLL_TIMEOUT_S):
                client = self._http()
                request = client.build_request("GET", self.base_url + path, params=params,
                                               headers=self._headers(key, "application/json"),
                                               timeout=httpx.Timeout(POLL_TIMEOUT_S))
                response = await client.send(request, stream=True)
                try:
                    raw = await _read_capped(response)
                finally:
                    await response.aclose()
        except UnicodeError:
            failure = UpstreamError("key_rejected", 0)
        except (httpx.HTTPError, httpx.InvalidURL, TimeoutError):
            failure = UpstreamError("unreachable", 0)
        if failure is not None:
            raise failure
        return response.status_code, raw

    async def _get_data(self, path: str, key: str) -> dict:
        """Объект data ответа 200; ошибка OpenRouter — UpstreamError по classify с настоящим кодом."""
        status, raw = await self._get(path, key)
        if status != 200:
            error, message = _error_of(raw)
            raise UpstreamError(classify(status, error), status, message)
        body = _json_object(raw)
        data = body.get("data") if body is not None else None
        if not isinstance(data, dict):
            raise UpstreamError("server", status)
        return data

    async def generation_cost(self, gen_id: str) -> float | None:
        """total_cost вызова по id из /generation — один запрос; 404 и любая ошибка — None."""
        try:
            status, raw = await self._get("/generation", self._key, {"id": gen_id})
        except Exception:                           # фоновый дозапрос не падает: цена просто не нашлась
            return None
        body = _json_object(raw) if status == 200 else None
        data = body.get("data") if body is not None else None
        if not isinstance(data, dict):
            return None
        return Usage.from_openrouter({"cost": data.get("total_cost")}).cost     # то же правило денег, что у потока

    async def key_info(self) -> dict:
        """data из /key (usage, limit, limit_remaining…); ошибки — UpstreamError по classify."""
        return await self._get_data("/key", self._key)

    async def credits(self) -> float | None:
        """Остаток счёта total_credits − total_usage ключом управления; без ключа — None. Ошибки —
        UpstreamError с настоящим status: 401/403 — ключ управления отклонён."""
        if self._mgmt_key is None:
            return None
        data = await self._get_data("/credits", self._mgmt_key)
        total, used = _number(data.get("total_credits")), _number(data.get("total_usage"))
        if total is None or used is None:
            raise UpstreamError("server", 200)
        return total - used

    async def aclose(self) -> None:
        """Закрывает свой клиент и обнуляет его; следующий запрос создаст новый. Чужой клиент не трогается."""
        if not self._own:
            return
        client, self._client = self._client, None
        if client is not None and self._loop is _running_loop():
            await client.aclose()
