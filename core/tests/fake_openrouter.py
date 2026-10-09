"""Двойник OpenRouter для тестов и тестового стенда: только stdlib, ThreadingHTTPServer, HTTP/1.0.

Запуск как __main__ — 0.0.0.0:8080 (контейнер fake-openrouter стенда, только для чтения). Верные ключи —
test-openrouter-key («a») и test-openrouter-key-b («b», ключ с прошлым расходом $50), ключ управления —
test-mgmt-key.

Чат (POST /api/v1/chat/completions) записывает {"auth_ok", "body"}; поведение выбирает слово FAKE… в
тексте последнего сообщения user (таблица — в брифе задачи 5 Д4а; по ревью добавлены FAKEMIDSTR — событие
ошибки с error.code строкой "server_error", и FAKEDRIP — 502 с телом ошибки по байту раз в 0,2 с).
Обычный ответ — поток SSE с комментарием «: OPENROUTER PROCESSING», последним чанком с usage
(cost 0.0012) и data: [DONE]; заголовок X-Generation-Id: gen-<N>.

Запись агента (Д7а): FAKESAVE — если у запроса есть инструмент pcbk_save_agent, а в ходе инструментов ещё не было,
вызов pcbk_save_agent с save_args(имя); имя — из «agent=<имя>» последнего сообщения user, по умолчанию proverka-d7a.
После ответа инструмента — текст «Готово: …». FAKESAVESLOW — то же, но этот текст идёт кусками раз в 0,5 с 10 с подряд,
как у FAKESLOW; запрос без инструментов (фоновый заголовок разговора) получает обычный быстрый ответ. FAKESAVEBAD —
то же, что FAKESAVE, с tools: ["bash"] (отказ службы tool_not_allowed). Ветка — раньше ветки pcbk_tag_now.

Дорогой ответ (Д10а, Д10-R106): FAKEDEAR — обычный ответ, но цена $0,5037 (не кратна центу, B-1) — в usage потока, в
теле без потока и в /generation этого id; прочее поведение — как без слова.

/generation по id: первые два запроса — 404, потом total_cost 0.002 (у FAKEDEAR — 0.5037; id ответа FAKEGENNEVER —
всегда 404). /key: usage ключа — сумма отданных цен (usage потока и найденные дозапросом, каждая один раз).
/credits: ключом управления — {"total_credits": 50.0, "total_usage": 20.0}, иначе 403.

Ручки только для тестов: GET/DELETE /_requests, GET /_stats ({"aborted": n} — обрывы клиентом посреди
потока), POST /_usage ({"add": x, "key": "a"|"b"}), POST /_price ({"model", "prompt", "completion"} —
нынешняя цена точки, $ за 1 млн; выше provider.max_price запроса — 404 «No endpoints found matching
your max_price», max_price в теле нет — обычный ответ).

Стенд отбора модели (Д8-R75): POST /_bench {"personas": {модель: персона}} — 204, включает персоны fake_bench
(пусто или персона вне PERSONA_NAMES — 400); DELETE /_bench — 204, выключает. Чат с включёнными
персонами сначала спрашивает bench_answer; None — дальше прежняя логика слов. Провайдер ответа — по закреплённой
точке запроса (provider.only, PROVIDER_NAMES: «google-vertex/global» → «Google Vertex»); без точки или с чужой —
«Fireworks», как раньше; слово FAKEPROVIDER сильнее точки — «Together».

Нагрузка (Д11б, Б2): POST /_delay {"s": x} — 204, каждый следующий вызов чата (верный ключ и тело) идёт не меньше x
секунд (0 ≤ x ≤ DELAY_MAX_S, иначе 400): у потока — после заголовков и комментария «: OPENROUTER PROCESSING», как
долгая модель, у ответа без потока — до него; DELETE /_delay — 204, задержки нет. Ход двойника длится, и тормоз
прогонщика останавливает его посреди хода; ответы-ошибки до потока (ERRORS, FAKEDRIP, FAKEFLAKY) задержку не ждут.
"""
import json
import re
import socketserver
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fake_bench import PERSONA_NAMES, bench_answer

KEYS = {"test-openrouter-key": "a", "test-openrouter-key-b": "b"}
MGMT_KEY = "test-mgmt-key"
START_USAGE = {"a": 0.0, "b": 50.0}
USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.0012,
         "prompt_tokens_details": {"cached_tokens": 4}, "completion_tokens_details": {"reasoning_tokens": 2}}
GENERATION_COST = 0.002
DEAR_COST = 0.5037                      # FAKEDEAR: цена вызова не кратна центу (Д10а, пол доли B-1)
DEAR_USAGE = {**USAGE, "cost": DEAR_COST}
GENERATION_MISSES = 2                   # столько первых запросов /generation по id — 404
FLAKY_FAILURES = 2
SLOW_EVERY_S, SLOW_CHUNKS = 0.5, 20     # FAKESLOW: чанк каждые 0,5 с 10 с подряд
SLOW_START_S = 1.0                      # FAKESLOWSTART: молчание до строки статуса
BREAK_LENGTH = 1024 * 1024              # FAKEBREAK обещает столько байт и рвёт соединение раньше
ASK_ARGS = {"questions": [{"question": "Какой из тегов?", "header": "Тег",
                           "options": [{"label": "20FAKE_001_PV", "description": "первый"}]}]}
WORDS = ("FAKE402", "FAKE402INFLIGHT", "FAKE403KEY", "FAKE403REGION", "FAKE401", "FAKE500", "FAKE404",
         "FAKEMIDERR", "FAKEMID402", "FAKENOUSAGE", "FAKEGENNEVER", "FAKEFLAKY", "FAKEASK", "FAKEBREAK",
         "FAKESLOW", "FAKESLOWSTART", "FAKEPROVIDER", "FAKEMIDSTR", "FAKEDRIP",
         "FAKESAVE", "FAKESAVESLOW", "FAKESAVEBAD", "FAKEDEAR")
_WORD = re.compile(r"\bFAKE[A-Z0-9]+\b")
# закреплённая точка запроса (provider.only прокси) → имя провайдера в ответе, как его пишет OpenRouter
PROVIDER_NAMES = {"fireworks": "Fireworks", "google-vertex/global": "Google Vertex", "azure": "Azure"}
DEFAULT_PROVIDER = "Fireworks"
SAVE_WORDS = ("FAKESAVE", "FAKESAVESLOW", "FAKESAVEBAD")
SAVE_NAME = "proverka-d7a"              # имя агента, если в вопросе нет «agent=<имя>»
_AGENT = re.compile(r"\bagent=([a-z][a-z0-9-]*)")


def save_args(name: str) -> dict:
    """Аргументы pcbk_save_agent двойника (Д7а): годный черновик без тегов — служба его сохраняет."""
    return {"name": name, "title": "Проверка Д7", "node": "Тестовый узел", "purpose": "Проверка записи агента службой",
            "tools": ["catalog_search", "tag_now"], "tags": [],
            "prompt": "Ты — помощник технолога по тестовому узлу. " * 6, "student_reply": "да"}
MID_ERRORS = {"FAKEMIDERR": (502, "Provider returned error"), "FAKEMID402": (402, "Insufficient credits"),
              "FAKEMIDSTR": ("server_error", "Provider returned error")}      # error.code строкой
DRIP_EVERY_S = 0.2                      # FAKEDRIP: 502, тело ошибки по байту — около 14 с
DELAY_MAX_S = 60.0                      # POST /_delay: задержка вызова чата не больше (Д11б)
ERRORS = {   # слово → (код, текст, metadata, Retry-After)
    "FAKE402": (402, "Insufficient credits. Add more using https://openrouter.ai/settings/credits", None, None),
    "FAKE402INFLIGHT": (402, "Too many requests in flight for this key's budget, retry shortly",
                        {"limit_source": "in_flight_budget"}, "1"),
    "FAKE403KEY": (403, "Key limit exceeded", None, None),
    "FAKE403REGION": (403, "This model is not available in your region.", None, None),
    "FAKE401": (401, "User not found.", None, None),
    "FAKE500": (500, "Internal Server Error", None, None),
    "FAKE404": (404, "No endpoints found matching your data policy", None, None),
}


class State:
    """Всё, что двойник помнит; под одним замком — обработчики идут в своих потоках."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict] = []
        self.aborted = 0
        self.usage = dict(START_USAGE)
        self.prices: dict[str, tuple[float, float]] = {}
        self.generations = 0
        self.gen_owner: dict[str, str] = {}      # id → ключ («a»/«b»), которым сделан вызов
        self.gen_hits: dict[str, int] = {}
        self.gen_never: set[str] = set()
        self.gen_found: set[str] = set()         # цена по id уже отдана — в usage ключа второй раз не идёт
        self.gen_cost: dict[str, float] = {}     # id → своя цена /generation (FAKEDEAR), иначе GENERATION_COST
        self.flaky = 0
        self.personas: dict[str, str] = {}      # стенд отбора: модель → персона fake_bench (POST /_bench)
        self.delay_s = 0.0                      # нагрузка: задержка каждого вызова чата (POST /_delay, Д11б)


def _text(content: object) -> str:
    """Текст сообщения: строка или части [{"type": "text", "text": …}]."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def _messages(body: dict) -> list[dict]:
    msgs = body.get("messages")
    return [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []


def _tool_names(body: dict) -> set[str]:
    tools = body.get("tools")
    names = set()
    for t in tools if isinstance(tools, list) else []:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            names.add(fn["name"])
    return names


def word_of(body: dict) -> str | None:
    """Первое известное слово FAKE… в тексте последнего сообщения user."""
    users = [m for m in _messages(body) if m.get("role") == "user"]
    if not users:
        return None
    return next((w for w in _WORD.findall(_text(users[-1].get("content"))) if w in WORDS), None)


def answer(body: dict, word: str | None, gen: str = "gen-0") -> tuple[dict, str]:
    """(delta/message ответа, finish_reason): вызов инструмента (id вызова — от gen) или текст «Готово: …»."""
    msgs = _messages(body)
    tools = _tool_names(body)
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    turn_tools = [m for m in msgs[last_user + 1:] if m.get("role") == "tool"]
    loop = any("FAKELOOP" in _text(m.get("content")) for m in msgs if m.get("role") == "user")
    if word == "FAKEASK" and "question" in tools and not turn_tools:
        return _tool_call(gen, "question", ASK_ARGS), "tool_calls"
    if word in SAVE_WORDS and "pcbk_save_agent" in tools and not turn_tools:      # раньше ветки pcbk_tag_now
        found = _AGENT.search(_text(msgs[last_user].get("content")))
        args = save_args(found[1] if found else SAVE_NAME)
        if word == "FAKESAVEBAD":
            args["tools"] = ["bash"]
        return _tool_call(gen, "pcbk_save_agent", args), "tool_calls"
    if "pcbk_tag_now" in tools and not turn_tools:
        return _tool_call(gen, "pcbk_tag_now", {"tags": ["20FAKE_001_PV"]}), "tool_calls"
    if "pcbk_tag_now" in tools and loop:        # аргумент меняется с числом сообщений tool: не doom_loop
        return _tool_call(gen, "pcbk_tag_now", {"tags": [f"20FAKE_{len(tool_msgs) + 1:03d}_PV"]}), "tool_calls"
    tail = _text(tool_msgs[-1].get("content"))[:60] if tool_msgs else "нет инструмента"
    return {"role": "assistant", "content": "Готово: " + tail}, "stop"


def provider_of(body: dict, word: str | None) -> str:
    """Провайдер ответа: FAKEPROVIDER — «Together»; иначе по закреплённой точке (PROVIDER_NAMES), без неё —
    DEFAULT_PROVIDER."""
    if word == "FAKEPROVIDER":
        return "Together"
    provider = body.get("provider")
    only = provider.get("only") if isinstance(provider, dict) else None
    pinned = only[0] if isinstance(only, list) and only and isinstance(only[0], str) else None
    return PROVIDER_NAMES.get(pinned, DEFAULT_PROVIDER)


def _tool_call(gen: str, name: str, args: dict) -> dict:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"index": 0, "id": f"call-{gen}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"               # соединение на запрос; конец потока — закрытие
    server_version = "fake-openrouter"

    def log_message(self, fmt, *args):          # в тестах — тихо, на стенде — строка запроса
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass                                # клиент ушёл посреди ответа не из потока — не ошибка двойника

    @property
    def state(self) -> State:
        return self.server.state

    # --- разбор и ответы ---

    def _path(self) -> tuple[str, dict[str, list[str]]]:
        url = urllib.parse.urlsplit(self.path)
        return url.path, urllib.parse.parse_qs(url.query)

    def _bearer(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return auth[7:].strip() if auth[:7].lower() == "bearer " else None

    def _read_json(self) -> object:
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size) if size > 0 else b""
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def _json(self, status: int, obj: object, headers: tuple[tuple[str, str], ...] = ()) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _no_content(self) -> None:
        """204 без тела (ручки /_bench)."""
        self.send_response(204)
        self.end_headers()

    def _error(self, status: int, message: str, metadata: dict | None = None,
               retry_after: str | None = None) -> None:
        error = {"code": status, "message": message, **({"metadata": metadata} if metadata else {})}
        self._json(status, {"error": error}, (("Retry-After", retry_after),) if retry_after else ())

    # --- маршруты ---

    def do_GET(self):
        path, query = self._path()
        if path == "/api/v1/generation":
            return self._generation(query.get("id", [""])[0])
        if path == "/api/v1/key":
            key = KEYS.get(self._bearer() or "")
            if key is None:
                return self._error(401, "User not found.")
            with self.state.lock:
                usage = self.state.usage[key]
            return self._json(200, {"data": {"label": "test", "usage": usage, "limit": None,
                                             "limit_remaining": None, "limit_reset": None}})
        if path == "/api/v1/credits":
            if self._bearer() != MGMT_KEY:
                return self._error(403, "Only management keys can fetch credits")
            return self._json(200, {"data": {"total_credits": 50.0, "total_usage": 20.0}})
        if path == "/_requests":
            with self.state.lock:
                items = list(self.state.requests)
            return self._json(200, items)
        if path == "/_stats":
            with self.state.lock:
                aborted = self.state.aborted
            return self._json(200, {"aborted": aborted})
        return self._error(404, "Not Found")

    def do_DELETE(self):
        path = self._path()[0]
        if path == "/_requests":
            with self.state.lock:
                self.state.requests.clear()
            return self._json(200, {"ok": True})
        if path == "/_bench":
            with self.state.lock:
                self.state.personas = {}
            return self._no_content()
        if path == "/_delay":
            with self.state.lock:
                self.state.delay_s = 0.0
            return self._no_content()
        return self._error(404, "Not Found")

    def do_POST(self):
        path = self._path()[0]
        if path == "/api/v1/chat/completions":
            return self._chat()
        payload = self._read_json()
        payload = payload if isinstance(payload, dict) else {}
        if path == "/_usage":
            key = payload.get("key", "a")
            if key not in START_USAGE or not isinstance(payload.get("add"), (int, float)):
                return self._error(400, "need {add: number, key: a|b}")
            with self.state.lock:
                self.state.usage[key] += payload["add"]
            return self._json(200, {"ok": True})
        if path == "/_price":
            model, prompt, completion = payload.get("model"), payload.get("prompt"), payload.get("completion")
            if not isinstance(model, str) or not all(isinstance(x, (int, float)) for x in (prompt, completion)):
                return self._error(400, "need {model, prompt, completion}")
            with self.state.lock:
                self.state.prices[model] = (float(prompt), float(completion))
            return self._json(200, {"ok": True})
        if path == "/_bench":
            personas = payload.get("personas")
            if not isinstance(personas, dict) or not personas or not all(
                    isinstance(k, str) and v in PERSONA_NAMES for k, v in personas.items()):
                return self._error(400, "need {personas: {model: " + "|".join(sorted(PERSONA_NAMES)) + "}}")
            with self.state.lock:
                self.state.personas = dict(personas)
            return self._no_content()
        if path == "/_delay":
            s = payload.get("s")
            if isinstance(s, bool) or not isinstance(s, (int, float)) or not 0 <= s <= DELAY_MAX_S:
                return self._error(400, f"need {{s: 0..{DELAY_MAX_S:g}}}")
            with self.state.lock:
                self.state.delay_s = float(s)
            return self._no_content()
        return self._error(404, "Not Found")

    def _generation(self, gen_id: str) -> None:
        key = KEYS.get(self._bearer() or "")
        if key is None:
            return self._error(401, "User not found.")
        if not gen_id:
            return self._error(400, "id is required")
        st = self.state
        with st.lock:
            st.gen_hits[gen_id] = st.gen_hits.get(gen_id, 0) + 1
            found = gen_id not in st.gen_never and st.gen_hits[gen_id] > GENERATION_MISSES
            cost = st.gen_cost.get(gen_id, GENERATION_COST)
            if found and gen_id not in st.gen_found:
                st.gen_found.add(gen_id)
                st.usage[st.gen_owner.get(gen_id, key)] += cost
        if not found:
            return self._error(404, f"Generation {gen_id} not found")
        return self._json(200, {"data": {"id": gen_id, "total_cost": cost}})

    def _over_max_price(self, body: dict) -> bool:
        """Нынешняя цена точки модели выше provider.max_price (любая из двух); цены или предела нет — нет."""
        with self.state.lock:
            price = self.state.prices.get(body.get("model"))
        provider = body.get("provider")
        limits = provider.get("max_price") if isinstance(provider, dict) else None
        if price is None or not isinstance(limits, dict):
            return False
        for name, now in zip(("prompt", "completion"), price):
            limit = limits.get(name)
            if isinstance(limit, (int, float)) and not isinstance(limit, bool) and now > limit:
                return True
        return False

    def _chat(self) -> None:
        body = self._read_json()
        key = KEYS.get(self._bearer() or "")
        st = self.state
        with st.lock:
            st.requests.append({"auth_ok": key is not None, "body": body if isinstance(body, dict) else {}})
        if not isinstance(body, dict):
            return self._error(400, "Invalid JSON body")
        if key is None:
            return self._error(401, "User not found.")
        if self._over_max_price(body):
            return self._error(404, "No endpoints found matching your max_price")
        with st.lock:
            personas = dict(st.personas)
        try:                                        # персоны стенда отбора — первыми (Д8-R75)
            bench = bench_answer(body, personas) if personas else None
        except Exception as exc:                    # сбой персоны — явный 500, а не оборванное соединение
            if getattr(self.server, "verbose", False):
                traceback.print_exc()
            return self._error(500, f"fake bench persona failed: {type(exc).__name__}")
        word = None if bench is not None else word_of(body)
        if word in ERRORS:
            return self._error(*ERRORS[word])
        if word == "FAKEDRIP":
            return self._drip()
        if word == "FAKEFLAKY":
            with st.lock:
                st.flaky += 1
                flaky = st.flaky <= FLAKY_FAILURES
            if flaky:
                return self._error(502, "Provider returned error")
        with st.lock:
            st.generations += 1
            gen = f"gen-{st.generations}"
            st.gen_owner[gen] = key
            if word == "FAKEGENNEVER":
                st.gen_never.add(gen)
            if word == "FAKEDEAR":
                st.gen_cost[gen] = DEAR_COST
        provider = provider_of(body, word)
        with_usage = word not in ("FAKENOUSAGE", "FAKEGENNEVER")
        usage = DEAR_USAGE if word == "FAKEDEAR" else USAGE
        message, finish = bench if bench is not None else answer(body, word, gen)
        # FAKESAVESLOW медлит только текстом хода после инструмента: запрос заголовка (без инструментов) — быстрый
        slow = word in ("FAKESLOW", "FAKESLOWSTART") or (word == "FAKESAVESLOW" and finish == "stop"
                                                          and bool(_tool_names(body)))
        if body.get("stream") is not True:
            self._delay()
            reply = {"id": gen, "object": "chat.completion", "created": int(time.time()), "model": body.get("model"),
                     "provider": provider, "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
            if with_usage:
                reply["usage"] = usage
                self._charge(key, usage["cost"])
            return self._json(200, reply, (("X-Generation-Id", gen),))
        self._stream(key, gen, provider, word, message, finish, with_usage, slow, usage)

    def _drip(self) -> None:
        """502 с телом ошибки по байту раз в DRIP_EVERY_S: каждый кусок успевает до срока чтения."""
        data = json.dumps({"error": {"code": 502, "message": "Provider returned error"}}).encode()
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            for i in range(len(data)):
                self.wfile.write(data[i:i + 1])
                time.sleep(DRIP_EVERY_S)
        except OSError:
            pass                                # клиент бросил чтение по своему сроку

    def _delay(self) -> None:
        """Задержка вызова чата (POST /_delay, Д11б): спит, сколько велено сейчас."""
        with self.state.lock:
            delay = self.state.delay_s
        if delay > 0:
            time.sleep(delay)

    def _charge(self, key: str, cost: float) -> None:
        with self.state.lock:
            self.state.usage[key] += cost

    def _stream(self, key: str, gen: str, provider: str, word: str | None, message: dict, finish: str,
                with_usage: bool, slow: bool = False, usage: dict = USAGE) -> None:
        if word == "FAKESLOWSTART":
            time.sleep(SLOW_START_S)                # молчание до строки статуса и заголовков (П7)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Generation-Id", gen)
        if word == "FAKEBREAK":                     # обещано больше, чем придёт: обрыв, а не конец потока
            self.send_header("Content-Length", str(BREAK_LENGTH))

        def chunk(delta: dict, finish_reason: str | None = None, **extra) -> bytes:
            event = {"id": gen, "provider": provider,
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}], **extra}
            return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()

        def send(data: bytes) -> None:
            self.wfile.write(data)
            self.wfile.flush()

        try:
            self.end_headers()
            send(b": OPENROUTER PROCESSING\n\n")
            self._delay()                           # долгая модель (Д11б): заголовки уже ушли, ответа ещё нет
            if word in ("FAKEBREAK", *MID_ERRORS):
                send(chunk({"role": "assistant", "content": "Начало ответа"}))
                if word != "FAKEBREAK":
                    error = MID_ERRORS[word]
                    send(f"data: {json.dumps({'error': {'code': error[0], 'message': error[1]}})}\n\n".encode())
                return                              # без [DONE]; FAKEBREAK — меньше обещанной длины
            if slow:                                # FAKESLOW, FAKESLOWSTART, текст хода FAKESAVESLOW
                for i in range(SLOW_CHUNKS):
                    send(chunk({"role": "assistant", "content": f"{i} "}))
                    time.sleep(SLOW_EVERY_S)
            send(chunk(message))
            if with_usage:
                send(chunk({}, finish, usage=usage))
                self._charge(key, usage["cost"])
            else:
                send(chunk({}, finish))
            send(b"data: [DONE]\n\n")
        except OSError:                             # клиент оборвал поток
            with self.state.lock:
                self.state.aborted += 1


class FakeServer(ThreadingHTTPServer):
    daemon_threads = True                       # FAKESLOW не держит остановку сервера
    request_queue_size = 64

    def server_bind(self):                      # без socket.getfqdn: обратного DNS на старте нет
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    """Сервер двойника на (host, port); port 0 — свободный. Запуск — serve_forever() в своём потоке."""
    server = FakeServer((host, port), Handler)
    server.state = State()
    server.verbose = False
    return server


if __name__ == "__main__":
    srv = make_server("0.0.0.0", 8080)
    srv.verbose = True
    srv.serve_forever()
