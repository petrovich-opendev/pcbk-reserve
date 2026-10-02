"""Двойники мест для шлюза (Д5а, задача А3): обработчики httpx.MockTransport, без Docker и сборок (Ш2).

FakeDocker — Docker API за прокси сокета sp-ctl (правила Д2: ping, inspect, start и stop только
pcbk-student-01…10; путь вне правил — 403, метод без правил — 405). FakePlaces — серверы OpenCode мест по
адресу {STU_NET}.N.3:4096 с Basic «opencode:<пароль места>».

Пароли мест тестов — PLACE_PW; их пишет helpers.pw_dir. Ожидаемый Authorization двойник считает сам, а не
берёт у Workplaces.basic: неверная сборка заголовка (и учения bad_place_pw) получает 401, как от места.
"""
import asyncio
import base64
import itertools
import re
from collections import Counter

import httpx

PLACES = range(1, 11)
# тестовые пароли мест — не секреты: не 48 шестнадцатеричных знаков
PLACE_PW = {n: f"test-place-pw-{n:02d}" for n in PLACES}
NEVER_STARTED = "0001-01-01T00:00:00Z"          # StartedAt контейнера, который ни разу не запускали

_DOCKER_GET = re.compile(r"/v1\.44/(_ping|containers/pcbk-student-(0[1-9]|10)/json)")
_DOCKER_POST = re.compile(r"/v1\.44/containers/pcbk-student-(0[1-9]|10)/(start|stop)")
_PLACE_TAIL = re.compile(r"([1-9]|10)\.3")         # после префикса STU_NET: N.3, номер без нуля впереди
_stamps = itertools.count(1)


def expected_basic(n: int) -> str:
    """Authorization, который примет место n: Basic base64("opencode:" + пароль)."""
    return "Basic " + base64.b64encode(f"opencode:{PLACE_PW[n]}".encode()).decode()


def _stamp() -> str:
    """Новый StartedAt в виде RFC 3339 с наносекундами, как у Docker; каждый вызов — другой."""
    return f"2026-10-05T08:00:00.{next(_stamps):09d}Z"


def _json(status: int, body: object) -> httpx.Response:
    return httpx.Response(status, json=body)


class FakeDocker:
    """Docker за sp-ctl. containers[n] — exists, running, restarting, restart_count, started_at.

    start ставит новый started_at и restart_count = 0, как Docker; место из exit_on_start после start видно
    как падающий контейнер при restart: unless-stopped — Running и Restarting. down — ConnectError (вызов не
    записывается: до прокси он не дошёл), hang — ReadTimeout (записывается). stop_delay — пауза внутри stop,
    контейнер на это время ещё Running. calls — (действие, место) по порядку прихода, ping — ("ping", 0);
    stopped — места, остановленные удачно (204), по порядку.
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
            return httpx.Response(204)
        if self.stop_status != 204:
            return _json(self.stop_status, {"message": "отказ"})
        if not c["running"]:
            return httpx.Response(304)
        c.update(running=False, restarting=False)
        self._stopped.append(n)
        return httpx.Response(204)


class FakePlaces:
    """Серверы OpenCode мест по хосту {stu_net}.N.3:4096 (stu_net — как STU_NET шлюза; чужой хост —
    ConnectError).

    - Authorization не тот (expected_basic) или wrong_pw — 401 на любой запрос;
    - /agent — 200 [] начиная с вызова номер ready_after этого места (после настоящей паузы agent_delay),
      раньше и при never_ready — 503;
    - /session/status — {} (после status_delay), у мест из busy — одна занятая сессия, у мест из status_raw —
      эти байты как тело 200 (неверный ответ места);
    - прочее — 404.
    agent_calls[n] — сколько раз звали /agent места n; requests — все запросы по порядку.
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
        self.agent_calls: Counter[int] = Counter()
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        host, prefix = request.url.host, self.stu_net + "."
        m = _PLACE_TAIL.fullmatch(host[len(prefix):]) if host.startswith(prefix) else None
        if m is None or request.url.port != 4096:
            raise httpx.ConnectError("нет такого места", request=request)
        n = int(m[1])
        self.requests.append(request)
        if self.wrong_pw or request.headers.get("authorization") != expected_basic(n):
            return _json(401, {"name": "Unauthorized"})
        path = request.url.path
        if request.method == "GET" and path == "/agent":
            self.agent_calls[n] += 1
            if self.agent_delay:
                await asyncio.sleep(self.agent_delay)
            if self.never_ready or self.agent_calls[n] < self.ready_after:
                return _json(503, {"name": "NotReady"})
            return _json(200, [])
        if request.method == "GET" and path == "/session/status":
            if self.status_delay:
                await asyncio.sleep(self.status_delay)
            if n in self.status_raw:
                return httpx.Response(200, content=self.status_raw[n], headers={"content-type": "application/json"})
            return _json(200, {"ses_" + "B" * 26: {"type": "busy"}} if n in self.busy else {})
        return _json(404, {"name": "NotFound"})
