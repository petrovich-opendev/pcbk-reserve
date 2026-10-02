"""Места шлюза (Д5-R27, Д5-R28): прокси сокета sp-ctl, замок места, пробуждение, готовность, уборщик простоя.

Будит и усыпляет места только шлюз — через sp-ctl (правила Д2: ping, inspect, start и stop контейнеров
pcbk-student-01…10, списка контейнеров нет). Имя контейнера сверяется с PLACE_NAME до подстановки в путь.

Пробуждение и остановка уборщиком одного места идут под одним asyncio.Lock места. Пробуждение — одно на
место (single-flight): touch до ожидания замка, задача в фоне, wake() отвечает сразу. «Упало при старте» —
Restarting, StartedAt, сменившийся после нашего start, или контейнер, вышедший после него; базовая метка
StartedAt — из inspect сразу после start 204, при 304 (место уже шло) — из inspect до start. Флаг «готово»
живёт в памяти и привязан к StartedAt: его снимают остановка (до stop), такт, увидевший Running == false,
Restarting или новый StartedAt, и mark_unready (Д5б: отказ соединения). Пробуждение готового места, которое
не дольше такта уборщика назад подтвердили inspect такта или пробуждения (и флаг с тех пор не менялся), —
без работы: без inspect и строки журнала (итоговое ревью Д5а, M4); событие wake_ok — только счётчик в памяти,
иначе на долгом занятии сторож видел бы одни неудачи сломанного места и красил строку «места не поднимаются».

Простой — по монотонным часам: от последней активности (touch), а без неё — с первого раза, когда шлюз
увидел место работающим (такт или пробуждение). Уборщик под замком места: GET /session/status →
перепроверка простоя → без await снять «готово» → stop. Занятое место гаснет не позже двойного срока.
Неудавшийся stop оставляет место в running; повтор — не раньше паузы (2 такта, дальше вдвое, до
GW_IDLE_STOP_S), паузу снимают удачная остановка, такт, увидевший место стоящим, и пробуждение.

Такт снимает inspect мест параллельно, вне замков; итог места применяется, только если за это время место
не трогали пробуждение и остановка (счётчик _gen) и замок свободен: запоздавший inspect не снимет свежий
флаг. sp-ctl ответил на ping, но ни на один inspect такта — sp_ctl = "down", как без ping (итоговое ревью
Д5а, M5): места не будятся, строка сторожа красная. Первый проход, где sp-ctl ответил хоть об одном месте, возвращает «готово» работающим местам без
флага одной попыткой GET /agent, по местам параллельно: открытые вкладки после перезапуска core работают без
пробуждения. Исключение шага одного места (gather с return_exceptions) — строка журнала с классом, проход
идёт дальше: неожиданный ответ места не останавливает уборщик.

Конструкторы без ввода-вывода. httpx.AsyncClient создаётся в open() или при первом запросе (как Upstream,
П22), прокси окружения не берутся (trust_env=False). Сроки попыток держит asyncio.timeout поверх срока
httpx. Исключения httpx ловятся целиком (httpx.HTTPError, Ш11) и в журнал не пишутся: строки журнала — только
номера мест, коды и виды. Authorization к месту ставит только request(); пароль места и Basic не попадают ни
в журнал, ни в тексты исключений.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from pcbk_core.gateway.stats import GatewayStats
from pcbk_core.settings import GatewaySettings

log = logging.getLogger("pcbk_core.gateway.places")

PLACE_NAME = re.compile(r"pcbk-student-(0[1-9]|10)")
DOCKER_API = "/v1.44"
PING_TIMEOUT_S = 3.0          # ping и inspect
ACTION_TIMEOUT_S = 20.0       # start и stop (stop ждёт до 10 с остановки контейнера)
READY_ATTEMPT_S = 2.0         # попытка GET /agent: первые ~3 с после открытия порта запросы висят (research/04 §3)
READY_PAUSE_S = 0.5
BUSY_CHECK_S = 2.0
PLACE_READ_S = 20.0           # срок чтения запроса к месту (Д5-R30)
PLACE_PORT = 4096
SP_CTL_DOWN_TICK_S = 5.0      # такт, пока sp-ctl не отвечает (Ш10)
PLACES = range(1, 11)
# занятая сессия OpenCode: busy — ход идёт, retry — ход ждёт повтора запроса к модели
BUSY_TYPES = frozenset({"busy", "retry"})

PLACE_TEXTS = {
    "sleeping": "Рабочее место спит",
    "starting": "Готовлю рабочее место…",
    "ready": "Рабочее место готово",
    "down": "Нет связи с прокси сокета — рабочее место не поднять. Сообщите преподавателю.",
    "refused": "Прокси сокета отказал в запуске рабочего места — сообщите администратору.",
    "missing": "Рабочее место не создано — сообщите администратору.",
    "no_password": "У шлюза нет пароля рабочего места — сообщите администратору.",
    "place_auth": "Рабочее место не приняло пароль шлюза — сообщите администратору.",
    "crashed": "Рабочее место падает при запуске — сообщите преподавателю.",
    "timeout": "Рабочее место не ответило за отведённое время — попробуйте ещё раз или сообщите преподавателю.",
}

PLACE_TIMEOUT = httpx.Timeout(connect=2, read=PLACE_READ_S, write=5, pool=5)
_SP_CTL_TIMEOUT = httpx.Timeout(ACTION_TIMEOUT_S, connect=PING_TIMEOUT_S)
# нет ответа: соединение, срок (asyncio.timeout — TimeoutError), обрыв, отказ охраны выхода (OSError)
_NO_ANSWER = (httpx.HTTPError, TimeoutError, OSError)
_ACTIONS = {"inspect": "json", "start": "start", "stop": "stop"}
_FAILED = object()            # inspect такта не удался: место не трогать

SpCtlKind = Literal["down", "missing", "refused"]
PlaceState = Literal["sleeping", "starting", "ready", "failed"]


def _place(n: object) -> int:
    """Номер места — целое 1…10, иначе ValueError (значение в текст не идёт)."""
    if type(n) is not int or n not in PLACES:
        raise ValueError("номер места — целое от 1 до 10")
    return n


class SpCtlError(Exception):
    """Отказ sp-ctl: down — нет связи или срок, missing — контейнера нет (404), refused — прочий код."""

    def __init__(self, kind: SpCtlKind, status: int = 0):
        super().__init__(f"{kind} {status}" if status else kind)
        self.kind: SpCtlKind = kind
        self.status = status


class PlacePasswordError(OSError):
    """У шлюза нет пароля места: файла нет, он не читается или первая строка пуста. OSError — чтобы вызов
    места без пароля шёл тем же путём «место не отвечает», что обрыв соединения (Д5б)."""


class SpCtl:
    """Клиент Docker API за sp-ctl: ping, inspect, start, stop мест. Конструктор без ввода-вывода."""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, transport=self._transport,
                                             timeout=_SP_CTL_TIMEOUT, trust_env=False, follow_redirects=False)
        return self._client

    async def open(self) -> None:
        self._http()

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    def path(self, n: int, action: str) -> str:
        """Путь Docker API действия inspect, start или stop над местом n; имя — по PLACE_NAME (research/04 §1)."""
        name = f"pcbk-student-{n:02d}" if type(n) is int else ""
        if not PLACE_NAME.fullmatch(name) or action not in _ACTIONS:
            raise ValueError("место вне pcbk-student-01…10 или неизвестное действие")
        return f"{DOCKER_API}/containers/{name}/{_ACTIONS[action]}"

    async def _send(self, method: str, path: str, limit: float) -> httpx.Response:
        async with asyncio.timeout(limit):
            return await self._http().request(method, path, timeout=limit)

    async def ping(self) -> bool:
        try:
            r = await self._send("GET", f"{DOCKER_API}/_ping", PING_TIMEOUT_S)
        except _NO_ANSWER:
            return False
        return r.status_code == 200

    async def inspect(self, n: int) -> dict | None:
        """Ответ inspect места; None — контейнера нет (404). Нет связи — down, прочий код — refused."""
        path = self.path(n, "inspect")
        try:
            r = await self._send("GET", path, PING_TIMEOUT_S)
        except _NO_ANSWER:
            raise SpCtlError("down") from None
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise SpCtlError("refused", r.status_code)
        try:
            info = r.json()
        except (ValueError, RecursionError):              # не JSON или вложенность глубже предела разбора
            raise SpCtlError("refused", r.status_code) from None
        if not isinstance(info, dict):
            raise SpCtlError("refused", r.status_code)
        return info

    async def _action(self, n: int, action: str) -> int:
        path = self.path(n, action)
        try:
            r = await self._send("POST", path, ACTION_TIMEOUT_S)
        except _NO_ANSWER:
            raise SpCtlError("down") from None
        if r.status_code in (204, 304):
            return r.status_code
        if r.status_code == 404:
            raise SpCtlError("missing", 404)
        raise SpCtlError("refused", r.status_code)

    async def start(self, n: int) -> int:
        """204 — запущено, 304 — уже шло; иначе SpCtlError."""
        return await self._action(n, "start")

    async def stop(self, n: int) -> int:
        """204 — остановлено, 304 — уже стояло; иначе SpCtlError."""
        return await self._action(n, "stop")


@dataclass(frozen=True)
class PlaceView:
    place: int
    state: PlaceState
    detail: str

    def to_json(self) -> dict[str, str]:
        return {"place": f"{self.place:02d}", "state": self.state, "detail": self.detail}


@dataclass(frozen=True)
class _Container:
    """Нужное из inspect: State.Running, State.Restarting, State.StartedAt."""
    running: bool
    restarting: bool
    started_at: str | None

    @classmethod
    def of(cls, info: dict) -> "_Container":
        state = info.get("State")
        state = state if isinstance(state, dict) else {}
        started = state.get("StartedAt")
        return cls(state.get("Running") is True, state.get("Restarting") is True,
                   started if isinstance(started, str) else None)


class Workplaces:
    """Места для шлюза: пробуждение, готовность, простой и такт уборщика. Конструктор без ввода-вывода."""

    def __init__(self, gw: GatewaySettings, spctl: SpCtl, *, stats: GatewayStats,
                 transport: httpx.AsyncBaseTransport | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep):
        self.gw = gw
        self.spctl = spctl
        self.stats = stats
        self._transport = transport
        self._monotonic = monotonic
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None
        self._locks = {n: asyncio.Lock() for n in PLACES}
        self._tasks: dict[int, asyncio.Task] = {}
        self._ready: dict[int, str | None] = {}          # место → StartedAt, при котором оно готово
        self._failed: dict[int, str] = {}                # место → текст неудачи последнего пробуждения
        self._touched: dict[int, float] = {}             # последняя активность (monotonic)
        self._seen: dict[int, float] = {}                # когда шлюз увидел место работающим (monotonic)
        self._gen = dict.fromkeys(PLACES, 0)             # растёт при каждой смене места пробуждением и остановкой
        self._confirmed: dict[int, tuple[float, int]] = {}   # «готово» подтверждено: (monotonic, _gen) тогда
        self._passwords: dict[int, str] = {}
        self._stop_backoff: dict[int, float] = {}        # пауза до повтора неудавшейся остановки (растёт вдвое)
        self._stop_not_before: dict[int, float] = {}     # monotonic, раньше которого уборщик место не гасит
        self._restored = False
        self.running: set[int] = set()
        self.sp_ctl: Literal["ok", "down", "unknown"] = "unknown"
        self.checked_at: datetime | None = None
        self.last_tick_mono = monotonic()
        self.ticks = 0

    # --- клиент мест ---

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self._transport, timeout=PLACE_TIMEOUT, trust_env=False,
                                             follow_redirects=False)
        return self._client

    async def open(self) -> None:
        self._http()

    async def aclose(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    def lock(self, n: int) -> asyncio.Lock:
        return self._locks[_place(n)]

    def place_url(self, n: int) -> str:
        return f"http://{self.gw.STU_NET}.{_place(n)}.3:{PLACE_PORT}"

    def _password(self, n: int) -> str:
        pw = self._passwords.get(n)
        if pw is None:
            path = Path(self.gw.GW_PLACE_PW_DIR) / f"student-{n:02d}.pw"
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                raise PlacePasswordError(f"нет пароля места {n:02d}") from None
            pw = lines[0].strip() if lines else ""
            if not pw:
                raise PlacePasswordError(f"нет пароля места {n:02d}")
            self._passwords[n] = pw
        return pw

    def basic(self, n: int) -> str:
        """Authorization к месту: Basic base64("opencode:" + пароль). Учения bad_place_pw портят пароль."""
        pw = self._password(_place(n))
        if self.gw.DRILL_GATEWAY == "bad_place_pw":
            pw += "-drill"
        return "Basic " + base64.b64encode(f"opencode:{pw}".encode("utf-8")).decode("ascii")

    async def request(self, n: int, method: str, path: str, *, params=None, json=None,
                      timeout: float | None = None) -> httpx.Response:
        """Запрос к месту n — единственное место, где ставится Authorization. timeout — срок попытки целиком
        (asyncio.timeout: TimeoutError). Ни заголовков, ни запроса в журнал."""
        if not path.startswith("/"):
            raise ValueError("путь к месту начинается с «/»")
        headers = {"Authorization": self.basic(n)}
        url = self.place_url(n) + path
        if timeout is None:
            return await self._http().request(method, url, params=params, json=json, headers=headers)
        async with asyncio.timeout(timeout):
            return await self._http().request(method, url, params=params, json=json, headers=headers,
                                              timeout=timeout)

    # --- состояние места ---

    def touch(self, n: int) -> None:
        """Активность места (Д5-R28): пробуждение и в Д5б вызовы, кроме GET."""
        self._touched[_place(n)] = self._monotonic()

    def idle_s(self, n: int) -> float:
        """Простой: от последней активности, без неё — с первого раза, когда место увидено работающим."""
        n = _place(n)
        marks = [m for m in (self._touched.get(n), self._seen.get(n)) if m is not None]
        return max(0.0, self._monotonic() - max(marks)) if marks else 0.0

    def _waking(self, n: int) -> bool:
        task = self._tasks.get(n)
        return task is not None and not task.done()

    def _busy(self, n: int) -> bool:
        """Место сейчас трогает пробуждение или остановка: такт его итог не применяет."""
        return self._waking(n) or self._locks[n].locked()

    def view(self, n: int) -> PlaceView:
        """Состояние для страницы: без ввода-вывода и не активность."""
        n = _place(n)
        if self._waking(n):
            return PlaceView(n, "starting", PLACE_TEXTS["starting"])
        if n in self._ready:
            return PlaceView(n, "ready", PLACE_TEXTS["ready"])
        if n in self._failed:
            return PlaceView(n, "failed", self._failed[n])
        return PlaceView(n, "sleeping", PLACE_TEXTS["sleeping"])

    def is_ready(self, n: int) -> bool:
        return _place(n) in self._ready

    def mark_unready(self, n: int) -> None:
        self._ready.pop(_place(n), None)

    def set_ready_for_tests(self, n: int, started_at: str | None = None) -> None:
        """Только для тестов (А4 gateway_role(ready=…), Д5б): место готово без пробуждения — как после такта:
        «готово» с этим StartedAt (None — следующий такт возьмёт его из inspect), место в running. Без
        ввода-вывода; контейнер двойника запускает сам тест (helpers.ready_place)."""
        n = _place(n)
        self._ready[n] = started_at
        self._gen[n] += 1
        self._mark_running(n)

    def _confirm(self, n: int) -> None:
        """Место готово — это только что подтвердил inspect (такт, пробуждение) или ответ GET /agent."""
        self._confirmed[n] = (self._monotonic(), self._gen[n])

    def _fresh_ready(self, n: int) -> bool:
        """Готово, и подтверждено не дольше такта уборщика назад при том же флаге (_gen не менялся)."""
        mark = self._confirmed.get(n)
        return (n in self._ready and mark is not None and mark[1] == self._gen[n]
                and self._monotonic() - mark[0] <= self.gw.GW_REAPER_TICK_S)

    def _unready(self, n: int) -> None:
        self._ready.pop(n, None)
        self._gen[n] += 1

    def _mark_running(self, n: int) -> None:
        if n not in self.running:
            self.running.add(n)
            self._seen[n] = self._monotonic()

    def _forget(self, n: int) -> None:
        """Место не работает (или погашено): не в running, отсчёт «увидено» — с нового запуска, пауза повтора
        остановки снята."""
        self.running.discard(n)
        self._seen.pop(n, None)
        self._stop_retry_reset(n)
        self._gen[n] += 1

    def _stop_retry_reset(self, n: int) -> None:
        self._stop_backoff.pop(n, None)
        self._stop_not_before.pop(n, None)

    def _stop_retry_later(self, n: int) -> None:
        """Остановка не удалась: место остаётся в running (оно работает), «увидено» не трогается, повтор — не
        раньше паузы: 2 такта, дальше вдвое, до GW_IDLE_STOP_S. Так сбойный sp-ctl не получает stop каждый такт,
        а stop_failed не растёт на каждом такте."""
        backoff = self._stop_backoff.get(n)
        backoff = 2 * self.gw.GW_REAPER_TICK_S if backoff is None else 2 * backoff
        backoff = min(backoff, self.gw.GW_IDLE_STOP_S)
        self._stop_backoff[n] = backoff
        self._stop_not_before[n] = self._monotonic() + backoff

    def _stop_allowed(self, n: int) -> bool:
        return self._monotonic() >= self._stop_not_before.get(n, float("-inf"))

    # --- пробуждение ---

    async def wake(self, n: int) -> PlaceView:
        """touch, затем задача пробуждения — одна на место; ответ сразу, без ожидания готовности. Место готово,
        и свежее подтверждение есть (_fresh_ready) — ответ «готово» без задачи: без inspect и строки журнала,
        событие wake_ok — только в памяти (счётчик строки сторожа)."""
        n = _place(n)
        self.touch(n)
        if not self._waking(n):
            self._failed.pop(n, None)                    # неудача прошлого пробуждения сбрасывается здесь
            self._stop_retry_reset(n)                    # место снова нужно студенту
            if self._fresh_ready(n):
                self.stats.event("wake_ok")              # только счётчик в памяти: место поднято
            else:
                self._tasks[n] = asyncio.create_task(self._wake(n), name=f"gw-wake-{n:02d}")
        return self.view(n)

    async def wait_ready(self, n: int) -> PlaceView:
        """Ждёт идущее пробуждение (shield: отмена ждущего его не прерывает) и отдаёт состояние."""
        n = _place(n)
        task = self._tasks.get(n)
        if task is not None and not task.done():
            await asyncio.shield(task)
        return self.view(n)

    async def _wake(self, n: int) -> None:
        started = self._monotonic()
        try:
            async with self._locks[n]:
                kind = await self._wake_locked(n)
        except Exception as exc:                         # непредвиденное: страница не ждёт вечно, core жив
            log.warning("gw wake place=%02d error=%s", n, type(exc).__name__)
            kind = "timeout"
        ms = round((self._monotonic() - started) * 1000)
        if kind is None:
            self.stats.event("wake_ok")
            log.info("gw wake place=%02d result=ready kind=- ms=%d", n, ms)
            return
        self._failed[n] = PLACE_TEXTS[kind]
        self.stats.event("wake_failed")
        if kind == "place_auth":
            self.stats.event("place_auth_failed")
        log.warning("gw wake place=%02d result=failed kind=%s ms=%d", n, kind, ms)

    async def _wake_locked(self, n: int) -> str | None:
        """Ход пробуждения под замком места; None — готово, иначе вид неудачи (ключ PLACE_TEXTS)."""
        try:
            self.basic(n)                                # пароль — до start: без него start не зовётся
        except PlacePasswordError:
            return "no_password"
        try:
            info = await self.spctl.inspect(n)
        except SpCtlError as exc:
            return exc.kind
        if info is None:
            return "missing"
        before = _Container.of(info)
        if (before.running and not before.restarting and n in self._ready
                and self._ready[n] == before.started_at):
            self._mark_running(n)
            self._confirm(n)
            return None
        self._unready(n)
        try:
            code = await self.spctl.start(n)
        except SpCtlError as exc:
            return exc.kind
        try:
            kind = await self._await_ready(n, code, before.started_at)
        except Exception as exc:                         # после start место не остаётся полуживым
            log.warning("gw wake place=%02d error=%s", n, type(exc).__name__)
            kind = "timeout"
        if kind is not None:
            await self._stop_failed_wake(n)
        return kind

    async def _await_ready(self, n: int, code: int, base: str | None) -> str | None:
        """После start: GET /agent до 200 — попытка READY_ATTEMPT_S, пауза READY_PAUSE_S, срок GW_WAKE_TIMEOUT_S.

        base — StartedAt, от которого судится «упало»: при 204 — из inspect сразу после start (холодный старт
        «упавшим» не считается), при 304 — из inspect до start. Не удалось узнать — первая удачная сверка.
        """
        if code == 204:
            try:
                info = await self.spctl.inspect(n)
            except SpCtlError:
                base = None
            else:
                if info is None:
                    return "missing"
                base = _Container.of(info).started_at
        deadline = self._monotonic() + self.gw.GW_WAKE_TIMEOUT_S
        while True:
            try:
                status = (await self.request(n, "GET", "/agent", timeout=READY_ATTEMPT_S)).status_code
            except _NO_ANSWER:
                status = 0
            if status == 200:
                break
            if status == 401:
                return "place_auth"
            try:
                info = await self.spctl.inspect(n)
            except SpCtlError:
                info = _FAILED                           # sp-ctl молчит — судит срок
            if info is None:
                return "missing"
            if info is not _FAILED:
                now = _Container.of(info)
                base = now.started_at if base is None else base
                if now.restarting or not now.running or now.started_at != base:
                    return "crashed"
            if self._monotonic() >= deadline:
                return "timeout"
            await self._sleep(READY_PAUSE_S)
        self._ready[n] = base
        self._gen[n] += 1
        self._mark_running(n)
        self._confirm(n)
        return None

    async def _stop_quiet(self, n: int) -> bool:
        """stop без исключения наружу; неудача — счётчик stop_failed."""
        try:
            await self.spctl.stop(n)
        except SpCtlError:
            self.stats.event("stop_failed")
            return False
        return True

    async def _stop_failed_wake(self, n: int) -> None:
        """Неудача после start: полуживое место не держит память — stop; место уходит из running."""
        self._unready(n)
        idle = self.idle_s(n)
        ok = await self._stop_quiet(n)
        (log.info if ok else log.warning)("gw stop place=%02d idle_s=%d busy=no result=%s", n, idle,
                                          "ok" if ok else "failed")
        if ok:
            self._forget(n)
        else:                                            # место запущено нами и работает — уборщик повторит
            self._mark_running(n)
            self._stop_retry_later(n)

    # --- уборщик ---

    def next_tick_s(self) -> float:
        """Пауза до следующего такта: пока sp-ctl молчит — не больше 5 с (Ш10)."""
        if self.sp_ctl == "down":
            return min(SP_CTL_DOWN_TICK_S, self.gw.GW_REAPER_TICK_S)
        return self.gw.GW_REAPER_TICK_S

    def _set_sp_ctl(self, state: Literal["ok", "down"], step: str) -> None:
        """Смена состояния sp-ctl — строка журнала; step — на чём он отказал (ping, inspect)."""
        if state != self.sp_ctl:
            if state == "down":
                log.warning("gw sp-ctl down step=%s", step)
            else:
                log.info("gw sp-ctl ok")
            self.sp_ctl = state

    async def tick(self) -> None:
        """Один проход уборщика (Д5-R28): ping; при живом sp-ctl — inspect мест (ни один не удался — sp-ctl
        «down»), возврат «готово» в первом удачном проходе, остановка простаивающих. В конце — checked_at,
        last_tick_mono, ticks."""
        if await self.spctl.ping():
            await self._pass()
        else:
            self._set_sp_ctl("down", "ping")
        self.checked_at = datetime.now(timezone.utc)
        self.last_tick_mono = self._monotonic()
        self.ticks += 1

    async def _inspect_quiet(self, n: int) -> object:
        try:
            return await self.spctl.inspect(n)
        except SpCtlError:
            return _FAILED

    @staticmethod
    def _log_failures(step: str, places: list[int], results: list[object]) -> None:
        """Исключения шагов прохода (gather с return_exceptions): в журнал только класс, проход идёт дальше."""
        for n, result in zip(places, results):
            if isinstance(result, BaseException):
                log.warning("gw tick place=%02d step=%s error=%s", n, step, type(result).__name__)

    async def _pass(self) -> None:
        gens = dict(self._gen)
        infos = list(await asyncio.gather(*(self._inspect_quiet(n) for n in PLACES), return_exceptions=True))
        self._log_failures("inspect", list(PLACES), infos)
        infos = [_FAILED if isinstance(info, BaseException) else info for info in infos]
        answered = any(info is not _FAILED for info in infos)
        self._set_sp_ctl("ok" if answered else "down", "inspect")   # ping прошёл, а места не видны — как без него
        now = self._monotonic()
        seen: dict[int, _Container] = {}
        for n, info in zip(PLACES, infos):
            if info is _FAILED or self._busy(n) or self._gen[n] != gens[n]:
                continue                                  # пробуждение или остановка знают больше этого inspect
            if info is None or not (container := _Container.of(info)).running:
                self._ready.pop(n, None)                  # остановлено извне или контейнера нет
                if n in self.running:
                    self._forget(n)
                continue
            seen[n] = container
            if n not in self.running:
                self.running.add(n)
                self._seen[n] = now
            if n in self._ready:
                flag = self._ready[n]
                if container.restarting or (flag is not None and flag != container.started_at):
                    self._ready.pop(n)
                else:
                    if flag is None:
                        self._ready[n] = container.started_at
                    self._confirm(n)
        if not self._restored and answered:
            self._restored = True                         # первый проход, где sp-ctl ответил хоть об одном месте
            todo = [n for n, c in seen.items() if not c.restarting and n not in self._ready]
            self._log_failures("restore", todo, await asyncio.gather(
                *(self._restore(n, seen[n]) for n in todo), return_exceptions=True))
        limit = self.gw.GW_IDLE_STOP_S
        idle = [n for n in sorted(self.running)
                if not self._busy(n) and self._stop_allowed(n) and self.idle_s(n) >= limit]
        self._log_failures("reap", idle, await asyncio.gather(*(self._reap(n) for n in idle),
                                                              return_exceptions=True))

    async def _restore(self, n: int, container: _Container) -> None:
        """Первый проход: работающее место без флага — одна попытка GET /agent; 200 — «готово»."""
        if self._busy(n):
            return
        async with self._locks[n]:
            try:
                status = (await self.request(n, "GET", "/agent", timeout=READY_ATTEMPT_S)).status_code
            except _NO_ANSWER:
                return
            if status == 200 and n in self.running and n not in self._ready:
                self._ready[n] = container.started_at
                self._gen[n] += 1
                self._confirm(n)

    async def _session_busy(self, n: int) -> bool:
        """Идёт ли ход на месте: GET /session/status; нет ответа или ответ не тот — не занято."""
        try:
            r = await self.request(n, "GET", "/session/status", timeout=BUSY_CHECK_S)
        except _NO_ANSWER:
            return False
        if r.status_code != 200:
            return False
        try:
            sessions = r.json()
        except (ValueError, RecursionError):              # не JSON или вложенность глубже предела разбора
            return False
        return isinstance(sessions, dict) and any(
            isinstance(s, dict) and isinstance(t := s.get("type"), str) and t in BUSY_TYPES
            for s in sessions.values())

    async def _reap(self, n: int) -> None:
        """Остановка простаивающего места под замком: занятость → перепроверка простоя → снять «готово» → stop."""
        if self._busy(n) or not self._stop_allowed(n):
            return
        async with self._locks[n]:
            if n not in self.running:
                return
            busy = await self._session_busy(n)
            idle = self.idle_s(n)                        # действие студента во время проверки отменяет остановку
            limit = self.gw.GW_IDLE_STOP_S
            if idle < limit or (busy and idle < 2 * limit):
                return
            self._unready(n)                             # без await после перепроверки: запрос не уйдёт в гаснущее
            ok = await self._stop_quiet(n)
            (log.info if ok else log.warning)("gw stop place=%02d idle_s=%d busy=%s result=%s", n, idle,
                                              "yes" if busy else "no", "ok" if ok else "failed")
            if ok:
                self._forget(n)
            else:
                self._stop_retry_later(n)
