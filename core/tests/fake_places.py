"""Двойники мест для шлюза (Д5а, задача А3): обработчики httpx.MockTransport, без Docker и сборок (Ш2).

FakeDocker — Docker API за прокси сокета sp-ctl (правила Д2: ping, inspect, start и stop только
pcbk-student-01…10; путь вне правил — 403, метод без правил — 405). FakePlaces — серверы OpenCode мест по
адресу {STU_NET}.N.3:4096 с Basic «opencode:<пароль места>».

Пароли мест тестов — PLACE_PW; их пишет helpers.pw_dir. Ожидаемый Authorization двойник считает сам, а не
берёт у Workplaces.basic: неверная сборка заголовка (и учения bad_place_pw) получает 401, как от места.

Д5б (задача Б3): FakePlaces записывает запросы видом Recorded, отвечает списками агентов (agents) и висящих
разрешений (pending), по месту умеет ответить заданным кодом и заголовками (force_status), бросить исключение
(raise_for), помолчать (hang_for) и отдать большое тело кусками по 1 КиБ со счётом отданного (big, big_sent).

Д5б (задача Б4): GET /event — поток событий места (events, event_starts, event_open, event_closed; см. FakePlaces).

Д7а (задача А5): POST /instance/dispose — перезагрузка конфигурации места (dispose_calls, dispose_status, on_dispose,
agents_after_dispose; см. FakePlaces).

Д7б (задача А6): кривой файл агента — как OpenCode 1.18.33 (research/09, О3). bad_files[n] = (путь в месте, файл на
диске теста): экземпляр места создаётся при первом запросе после start (FakeDocker.on_start → FakePlaces.started) и после
удачного POST /instance/dispose; если файл в этот момент есть на диске, экземпляр сломан и на все ручки, кроме
/global/health, отвечает 400 ConfigInvalidError с этим путём. Чинит его только следующий start без файла: ни dispose, ни
удаление файла не чинят. Пароль места (401) проверяется раньше экземпляра (предпроверка Д7б, m23).

Д10а (задача А3): разговоры места для кабинета преподавателя — sessions[n] (список разговоров вида OpenCode) и
messages[(n, id)] (сообщения разговора): GET /experimental/session, GET /session/{id} (нет такого у места — 404
{"name": "NotFoundError"}), GET /session/{id}/message (тоже). Тела разговоров — как JSON.stringify OpenCode: одиночный
суррогат уходит экраном \\udXXX (доводка р1, I-1).
"""
from pathlib import Path
import asyncio
import base64
import gzip
import itertools
import json
import re
import threading
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import httpx

PLACES = range(1, 11)
# тестовые пароли мест — не секреты: не 48 шестнадцатеричных знаков
PLACE_PW = {n: f"test-place-pw-{n:02d}" for n in PLACES}
NEVER_STARTED = "0001-01-01T00:00:00Z"          # StartedAt контейнера, который ни разу не запускали

_DOCKER_GET = re.compile(r"/v1\.44/(_ping|containers/pcbk-student-(0[1-9]|10)/json)")
_DOCKER_POST = re.compile(r"/v1\.44/containers/pcbk-student-(0[1-9]|10)/(start|stop)")
_PLACE_TAIL = re.compile(r"([1-9]|10)\.3")         # после префикса STU_NET: N.3, номер без нуля впереди
_PROMPT_ASYNC = re.compile(r"/session/[^/]+/prompt_async")
_SESSION = re.compile(r"/session/(ses_[^/]+)(/message)?")      # разговор и его сообщения (Д10а, кабинет)
BIG_CHUNK = 1024                                   # кусок большого тела big: предел читается с точностью до КиБ
PULSE_S = 0.05                                     # пульс потока "endless"
# "silent" молчит столько, а не вечно: поломка, при которой шлюз тишину не видит, роняет тест, а не вешает прогон
SILENT_S = 20.0
# ошибка сломанного экземпляра места: steps: 0 во фронтматтере агента (OpenCode 1.18.33, PositiveInt; research/09, О3)
BROKEN_ISSUES = [{"message": "Expected a positive integer", "path": ["steps"]}]
_stamps = itertools.count(1)


def sse(event_type: str, **props) -> bytes:
    """Событие потока места, как его пишет OpenCode 1.18.33: одна строка data: с JSON и пустая строка."""
    return b"data: " + json.dumps({"id": "e", "type": event_type, "properties": props}).encode() + b"\n\n"


def expected_basic(n: int) -> str:
    """Authorization, который примет место n: Basic base64("opencode:" + пароль)."""
    return "Basic " + base64.b64encode(f"opencode:{PLACE_PW[n]}".encode()).decode()


def _stamp() -> str:
    """Новый StartedAt в виде RFC 3339 с наносекундами, как у Docker; каждый вызов — другой."""
    return f"2026-10-05T08:00:00.{next(_stamps):09d}Z"


def _json(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


def _json_escaped(status: int, body: object) -> httpx.Response:
    """JSON как у JSON.stringify OpenCode: одиночный суррогат — экраном \\udXXX (json= httpx его не закодировал бы)."""
    return httpx.Response(status, content=json.dumps(body).encode("ascii"),
                          headers={"content-type": "application/json"})


class FakeDocker:
    """Docker за sp-ctl. containers[n] — exists, running, restarting, restart_count, started_at.

    start ставит новый started_at и restart_count = 0, как Docker; место из exit_on_start после start видно
    как падающий контейнер при restart: unless-stopped — Running и Restarting. down — ConnectError (вызов не
    записывается: до прокси он не дошёл), hang — ReadTimeout (записывается). stop_delay — пауза внутри stop,
    контейнер на это время ещё Running. calls — (действие, место) по порядку прихода, ping — ("ping", 0);
    stopped — места, остановленные удачно (204), по порядку. on_start(n) — после удачного start (204): новый процесс
    места (Д7б, А6: FakePlaces.started).
    """

    def __init__(self, *, running=frozenset(), missing=frozenset(), exit_on_start=frozenset(),
                 start_status: int = 204, stop_status: int = 204, stop_delay: float = 0.0, down: bool = False,
                 hang: bool = False):
        self.exit_on_start = set(exit_on_start)
        self.start_status = start_status
        self.stop_status = stop_status
        self.stop_delay = stop_delay
        self.down = down
        self.hang = hang
        self.calls: list[tuple[str, int]] = []
        self._stopped: list[int] = []
        self.on_start: Callable[[int], None] | None = None
        self.containers: dict[int, dict] = {
            n: {"exists": n not in missing, "running": n in running, "restarting": False, "restart_count": 0,
                "started_at": _stamp() if n in running else NEVER_STARTED}
            for n in PLACES}

    @property
    def stopped(self) -> list[int]:
        return list(self._stopped)

    def run(self, n: int) -> str:
        """Контейнер места n работает (как docker start мимо шлюза, в журнал вызовов не идёт); его StartedAt."""
        c = self.containers[n]
        if not c["running"]:
            c.update(running=True, restarting=False, restart_count=0, started_at=_stamp())
        return c["started_at"]

    def _inspect(self, n: int) -> dict:
        c = self.containers[n]
        status = "restarting" if c["restarting"] else ("running" if c["running"] else "exited")
        return {"Name": f"/pcbk-student-{n:02d}", "RestartCount": c["restart_count"],
                "State": {"Status": status, "Running": c["running"], "Restarting": c["restarting"],
                          "Paused": False, "StartedAt": c["started_at"]}}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("sp-ctl недоступен", request=request)
        path, method = request.url.path, request.method
        if method == "GET" and (m := _DOCKER_GET.fullmatch(path)):
            action, n = ("ping", 0) if m[1] == "_ping" else ("inspect", int(m[2]))
        elif method == "POST" and (m := _DOCKER_POST.fullmatch(path)):
            action, n = m[2], int(m[1])
        elif method in ("GET", "POST"):
            return _json(403, {"message": "запрещено прокси"})
        else:
            return _json(405, {"message": "метод не разрешён"})
        self.calls.append((action, n))
        if self.hang:
            raise httpx.ReadTimeout("sp-ctl молчит", request=request)
        if action == "ping":
            return httpx.Response(200, text="OK")
        c = self.containers[n]
        if action == "stop" and self.stop_delay:
            await asyncio.sleep(self.stop_delay)                 # контейнер ещё Running
        if not c["exists"]:
            return _json(404, {"message": f"No such container: pcbk-student-{n:02d}"})
        if action == "inspect":
            return _json(200, self._inspect(n))
        if action == "start":
            if self.start_status != 204:
                return _json(self.start_status, {"message": "отказ"})
            if c["running"]:
                return httpx.Response(304)
            c.update(running=True, restarting=n in self.exit_on_start, restart_count=0, started_at=_stamp())
            if self.on_start is not None:
                self.on_start(n)
            return httpx.Response(204)
        if self.stop_status != 204:
            return _json(self.stop_status, {"message": "отказ"})
        if not c["running"]:
            return httpx.Response(304)
        c.update(running=False, restarting=False)
        self._stopped.append(n)
        return httpx.Response(204)


@dataclass(frozen=True)
class Recorded:
    """Запрос к месту, как его увидел двойник: имена заголовков — в нижнем регистре, json — разобранное тело
    (нет тела — None), query — строка запроса как есть. url — прежним тестам Д5а (r.url.path, r.url.host)."""
    method: str
    path: str
    query: str
    headers: dict[str, str]
    json: object
    url: httpx.URL


class _EventBody(httpx.AsyncByteStream):
    """Тело ответа /event двойника: куски по сценарию; закрытие (aclose или конец сценария) учитывается один раз.

    Сценарий — список, где bytes — кусок, ("pause", s) — пауза, вызываемое — вызов без аргументов (проба теста в
    момент чтения), ("raise", исключение) — бросить его (обрыв посреди потока), ("repeat", кусок, s) — этот кусок раз в
    s без конца, ("linger", s) — ждать без конца, а отмену пережить на s секунд (медленный читатель при закрытии);
    "endless" — пульс server.heartbeat раз в PULSE_S без конца; "silent" — server.connected и тишина SILENT_S (для
    шлюза — без конца), затем конец."""

    def __init__(self, places: "FakePlaces", n: int, script: object):
        self._places, self._n, self._script = places, n, script
        self._closed = False
        places.event_starts[n] += 1
        places.event_open[n] += 1

    async def __aiter__(self) -> AsyncIterator[bytes]:
        script = self._script
        if script == "endless":
            while True:
                yield sse("server.heartbeat")
                await asyncio.sleep(PULSE_S)
        if script == "silent":
            yield sse("server.connected")
            await asyncio.sleep(SILENT_S)
            return
        for item in script:
            if isinstance(item, tuple) and item[0] == "pause":
                await asyncio.sleep(item[1])
            elif isinstance(item, tuple) and item[0] == "raise":
                raise item[1]
            elif isinstance(item, tuple) and item[0] == "repeat":
                while True:
                    yield item[1]
                    await asyncio.sleep(item[2])
            elif isinstance(item, tuple) and item[0] == "linger":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await asyncio.sleep(item[1])
                    raise
            elif isinstance(item, Callable):
                item()
            else:
                yield item

    async def aclose(self) -> None:
        """Закрытие — точка отмены (await): закрывающий без щита в отменённой области его не доведёт. Двойник здесь
        строже настоящего транспорта — httpcore закрывает соединение сам под щитом; строгость нарочная: так тесты
        ловят release() без своего щита (доводка Б4 K1, Б4-m1)."""
        if not self._closed:
            await asyncio.sleep(0)
            self._closed = True
            self._places.event_open[self._n] -= 1
            self._places.event_closed[self._n] = True


def _record(request: httpx.Request) -> Recorded:
    body = request.content
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = body
    return Recorded(request.method, request.url.path, request.url.query.decode("ascii"),
                    {k.lower(): v for k, v in request.headers.items()}, parsed, request.url)


class FakePlaces:
    """Серверы OpenCode мест по хосту {stu_net}.N.3:4096 (stu_net — как STU_NET шлюза; чужой хост —
    ConnectError). Порядок ответа места n:

    - запрос записывается в requests (Recorded);
    - raise_for[n] — это исключение; hang_for[n] — столько секунд молчания перед ответом;
    - force_status[n] = (код, заголовки) — этот код и заголовки (Content-Type по умолчанию — JSON, значение None —
      заголовка нет) на любой запрос, тело {"name": "Forced"}, при content-encoding: gzip — сжатое по-настоящему;
    - Authorization не тот (expected_basic) или wrong_pw — 401;
    - big[n] — 200 с телом из стольких байт кусками по BIG_CHUNK; big_sent[n] — сколько байт отдано;
    - GET /agent — agents[n] (по умолчанию []) начиная с вызова номер ready_after этого места (после настоящей
      паузы agent_delay), раньше и при never_ready — 503;
    - GET /session/status — {} (после status_delay), у мест из busy — одна занятая сессия, у мест из status_raw —
      эти байты как тело 200 (неверный ответ места);
    - GET /permission — pending[n] (по умолчанию []);
    - Д10а: GET /experimental/session — sessions[n] (по умолчанию []); GET /session/{id} — разговор id из sessions[n],
      GET /session/{id}/message — messages[(n, id)] (по умолчанию []); разговора id у места нет — 404 NotFoundError;
    - check_status[n] — этот код и {"name": "Check"} на GET /agent и GET /permission (сверки прокси);
    - GET /event — 200 text/event-stream с потоком по сценарию events[n] (_EventBody; по умолчанию —
      server.connected и конец); event_starts[n] — сколько потоков место отдало, event_open[n] — сколько открыто
      сейчас, event_closed[n] — закрыт ли хоть один (False, пока не закрыт ни один); пауза до заголовков — hang_for;
      ворота до заголовков — event_gate[n] (threading.Event теста): ответ ждёт, пока тест их не откроет, а
      event_waiting[n] — сколько запросов /event места ждёт у ворот сейчас (гонка вкладок — по событию, не по времени);
    - POST /session/{id}/prompt_async — 204;
    - POST /instance/dispose (Д7а) — dispose_calls[n] += 1; при dispose_status[n] — этот код и {"name": "Dispose"},
      иначе 200 true, место «перезагружено» и зовётся on_dispose(n) (если задан); после первого удачного dispose GET
      /agent места из agents_after_dispose отвечает списком agents_after_dispose[n] вместо agents[n];
    - Д7б (А6): после проверки пароля — экземпляр места. Его создаёт первый запрос после started(n) (новый процесс) или
      удачного dispose: если файл bad_files[n][1] есть на диске, экземпляр сломан. Сломанный отвечает на всё, кроме
      GET /global/health, 400 ConfigInvalidError с путём bad_files[n][0] (BROKEN_ISSUES); dispose его не чинит (тоже 400),
      чинит только started(n) без файла. Место, работающее с начала теста (run, ready_place), — с целым экземпляром;
    - прочее — 200 {}.
    agent_calls[n] — сколько раз звали /agent места n; active[n] — запросов к месту n в работе сейчас, max_active[n]
    — наибольшее их число (от записи запроса до ответа; у /event — до заголовков).
    """

    def __init__(self, *, ready_after: int = 1, never_ready: bool = False, wrong_pw: bool = False,
                 busy=frozenset(), status_delay: float = 0.0, agent_delay: float = 0.0):
        self.ready_after = ready_after
        self.never_ready = never_ready
        self.wrong_pw = wrong_pw
        self.busy = set(busy)
        self.status_delay = status_delay
        self.agent_delay = agent_delay
        self.stu_net = "172.31"
        self.status_raw: dict[int, bytes] = {}
        self.agents: dict[int, list[dict]] = {}
        self.pending: dict[int, list[dict]] = {}
        self.force_status: dict[int, tuple[int, dict]] = {}
        self.raise_for: dict[int, Exception] = {}
        self.hang_for: dict[int, float] = {}
        self.big: dict[int, int] = {}
        self.big_sent: Counter[int] = Counter()
        self.check_status: dict[int, int] = {}
        self.agent_calls: Counter[int] = Counter()
        self.active: Counter[int] = Counter()
        self.max_active: Counter[int] = Counter()
        self.requests: list[Recorded] = []
        self.events: dict[int, object] = {}
        self.event_starts: Counter[int] = Counter()
        self.event_open: Counter[int] = Counter()
        self.event_closed: defaultdict[int, bool] = defaultdict(bool)
        self.event_gate: dict[int, threading.Event] = {}
        self.event_waiting: Counter[int] = Counter()
        self.dispose_calls: Counter[int] = Counter()
        self.dispose_status: dict[int, int] = {}
        self.on_dispose: Callable[[int], None] | None = None
        self.agents_after_dispose: dict[int, list[dict]] = {}
        self._disposed: set[int] = set()                # места, где dispose уже удался
        self.bad_files: dict[int, tuple[str, Path]] = {}
        self._instance: dict[int, str | None] = {}      # "ok", "broken"; None — создаст первый запрос; нет — "ok"
        self.sessions: dict[int, list[dict]] = {}       # разговоры места (Д10а): вид OpenCode
        self.messages: dict[tuple[int, str], list] = {}    # сообщения разговора места (Д10а)

    def started(self, n: int) -> None:
        """Новый процесс места (FakeDocker.on_start): экземпляр создаст первый запрос."""
        self._instance[n] = None

    def _instance_of(self, n: int) -> str:
        """Экземпляр места: создаётся при первом запросе после started или dispose — сломан, если кривой файл места есть
        на диске сейчас."""
        state = self._instance.get(n, "ok")
        if state is None:
            bad = self.bad_files.get(n)
            state = self._instance[n] = "broken" if bad is not None and bad[1].exists() else "ok"
        return state

    async def _event_gate(self, n: int) -> None:
        """Ворота /event места n (event_gate): ждать, пока тест их не откроет; ждущие — в event_waiting[n]."""
        gate = self.event_gate.get(n)
        if gate is None:
            return
        self.event_waiting[n] += 1
        try:
            while not gate.is_set():
                await asyncio.sleep(0.01)
        finally:
            self.event_waiting[n] -= 1

    async def _big_body(self, n: int, size: int):
        while size > 0:
            k = min(BIG_CHUNK, size)
            self.big_sent[n] += k
            yield b"x" * k
            size -= k

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        host, prefix = request.url.host, self.stu_net + "."
        m = _PLACE_TAIL.fullmatch(host[len(prefix):]) if host.startswith(prefix) else None
        if m is None or request.url.port != 4096:
            raise httpx.ConnectError("нет такого места", request=request)
        n = int(m[1])
        self.requests.append(_record(request))
        self.active[n] += 1
        self.max_active[n] = max(self.max_active[n], self.active[n])
        try:
            return await self._answer(request, n)
        finally:
            self.active[n] -= 1

    async def _answer(self, request: httpx.Request, n: int) -> httpx.Response:
        if n in self.raise_for:
            raise self.raise_for[n]
        if self.hang_for.get(n):
            await asyncio.sleep(self.hang_for[n])
        if n in self.force_status:
            status, headers = self.force_status[n]
            body = b'{"name":"Forced"}'
            if headers.get("content-encoding") == "gzip":                # сжатие настоящее: httpx его распакует
                body = gzip.compress(body)
            merged = {"content-type": "application/json", **headers}
            return httpx.Response(status, headers={k: v for k, v in merged.items() if v is not None}, content=body)
        if self.wrong_pw or request.headers.get("authorization") != expected_basic(n):
            return _json(401, {"name": "Unauthorized"})
        if request.url.path != "/global/health" and self._instance_of(n) == "broken":
            return _json(400, {"name": "ConfigInvalidError",
                               "data": {"path": self.bad_files[n][0], "issues": BROKEN_ISSUES}})
        if n in self.big:
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=self._big_body(n, self.big[n]))
        path = request.url.path
        if request.method == "GET" and path == "/event":
            await self._event_gate(n)
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=_EventBody(self, n, self.events.get(n, [sse("server.connected")])))
        if request.method == "GET" and path in ("/agent", "/permission") and n in self.check_status:
            return _json(self.check_status[n], {"name": "Check"})
        if request.method == "GET" and path == "/agent":
            self.agent_calls[n] += 1
            if self.agent_delay:
                await asyncio.sleep(self.agent_delay)
            if self.never_ready or self.agent_calls[n] < self.ready_after:
                return _json(503, {"name": "NotReady"})
            if n in self._disposed and n in self.agents_after_dispose:
                return _json(200, self.agents_after_dispose[n])
            return _json(200, self.agents.get(n, []))
        if request.method == "GET" and path == "/session/status":
            if self.status_delay:
                await asyncio.sleep(self.status_delay)
            if n in self.status_raw:
                return httpx.Response(200, content=self.status_raw[n], headers={"content-type": "application/json"})
            return _json(200, {"ses_" + "B" * 26: {"type": "busy"}} if n in self.busy else {})
        if request.method == "GET" and path == "/permission":
            return _json(200, self.pending.get(n, []))
        if request.method == "GET" and path == "/experimental/session":
            return _json_escaped(200, self.sessions.get(n, []))
        if request.method == "GET" and (m := _SESSION.fullmatch(path)):
            found = [s for s in self.sessions.get(n, []) if s.get("id") == m[1]]
            if not found:
                return _json(404, {"name": "NotFoundError", "data": {"message": "Session not found"}})
            return _json_escaped(200, self.messages.get((n, m[1]), []) if m[2] else found[0])
        if request.method == "POST" and _PROMPT_ASYNC.fullmatch(path):
            return httpx.Response(204)
        if request.method == "POST" and path == "/instance/dispose":
            self.dispose_calls[n] += 1
            if n in self.dispose_status:
                return _json(self.dispose_status[n], {"name": "Dispose"})
            self._disposed.add(n)
            self._instance[n] = None                    # новый экземпляр — при следующем запросе
            if self.on_dispose is not None:
                self.on_dispose(n)
            return _json(200, True)
        return _json(200, {})


class LoopPlace:
    """Место на петле (доводка Б3, G1): настоящий сервер asyncio, HTTP/1.1 от руки, пароль не сверяется. На любой
    запрос — 200 {} после hold (если задан). conns/max_conns — открытые соединения, active/max_active — запросы в
    работе. Пулы соединений httpx видны только на настоящих сокетах, MockTransport их не имеет."""

    def __init__(self, hold: asyncio.Event | None = None):
        self.hold = hold
        self.conns = self.max_conns = self.active = self.max_active = self.served = 0
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self, host: str, port: int = 0) -> int:
        """Слушать host:port (0 — выберет ядро); отдаёт порт."""
        self._server = await asyncio.start_server(self._conn, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        for writer in list(self._writers):
            writer.close()
        if self.hold is not None:
            self.hold.set()

    async def _conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        self.conns += 1
        self.max_conns = max(self.max_conns, self.conns)
        try:
            while await reader.readline():               # строка запроса; пусто — соединение закрыто
                length = 0
                while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
                    name, _, value = line.partition(b":")
                    if name.strip().lower() == b"content-length":
                        length = int(value)
                if length:
                    await reader.readexactly(length)
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                try:
                    if self.hold is not None:
                        await self.hold.wait()
                finally:
                    self.active -= 1
                self.served += 1
                writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 2\r\n\r\n{}")
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.conns -= 1
            self._writers.discard(writer)
            writer.close()
