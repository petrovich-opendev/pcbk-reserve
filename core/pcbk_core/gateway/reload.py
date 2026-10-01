"""Перезагрузка рабочего места после конца хода (Д7-R58, задача А5): служба данных записала агента — шлюз сам, когда
ход студента кончился, зовёт у места POST /instance/dispose, и OpenCode перечитывает конфигурацию с новым агентом.

Главный неуспех — оборванный ответ студента: dispose посреди хода кончает его MessageAbortedError. Поэтому dispose
идёт только при пустой карте GET /session/status, которая продержалась IDLE_SETTLE_S, не раньше TURN_START_GRACE_S
после конца последней пересылки prompt_async и под воротами места, мимо которых prompt_async не проходит.

Ворота места (TurnGates, раунд 1, I1 и m2; раунд 2, I-н1) — читатели и писатель на счётчиках и asyncio.Event без
замков: всё меняется в цикле событий без await между проверкой и записью. prompt_async — читатель (turn): ждёт только
идущую перезагрузку, не дольше RELOAD_WAIT_S (иначе GateTimeout), и читатели друг друга не ждут — вторая вкладка без
перезагрузки 503 не получает. Воркер — писатель (try_reload) без ожидания: отказ, если место перезагружается, если идёт
prompt_async или если он ждёт у ворот. Ожидание ворот прокси ставит снаружи срока хода (H1).

Очередь — строки agent_reloads учёта (AgentStore.pending → PendingReload(since, seq)). Неготовое место (спит, будится,
sp-ctl молчит) воркер пропускает и из очереди не снимает никогда (раунд 3, I-р3-2): метка archive/reload
администратора переживает сон места и перезапуск core. В pending_view такие места не считаются. Крючок готовности
(place_ready, его зовёт Workplaces.on_ready) ставит место заново (since — от готовности, seq + 1), если оно в очереди
или у него есть строки saved (m-н6). Снимает метку только reload_done(место, seq) с seq из начала прохода: метка,
пришедшая во время перезагрузки, остаётся, и место перезагружается снова (раунд 2, I-н2б).

Проход (tick) — раз в RELOAD_TICK_S (run):
1. висящие задачи крючка готовности — дождаться (m8); раз в DIR_CHECK_S — store.dir_problem() в stats.dir_error (первая
   проверка — первым проходом);
2. store.pending() в потоке. Удача (и пустая очередь) — пульс checked_at и worker_store_ok(); сбой — worker_store_failed,
   пульса нет, проход кончается (раунд 3, I-р3-1);
3. по местам очереди, перед каждым — снова пульс (m3), шаги — в _reload_place. Исключение прохода места — отметка неудачи
   (повтор не раньше RELOAD_RETRY_S), idle_since снят, reload_failed, у StoreError — ещё worker_store_failed; проход идёт
   к следующему месту.

DRILL_AGENTS=freeze_reload — после первого прохода воркер замирает: checked_at стоит, и строка сторожа «молчит».

Строки журнала — без имён агентов, только номер места, итог и числа:
- gw agents reload place=NN result=visible n=K wait_s=S;
- … result=not_visible n=K;
- … result=failed step=<шаг> error=<Класс или status_NNN>;
- … result=delayed wait_s=S.

Только память и учёт: модуль не импортирует accounts (команда администратора шлюза, runpy).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from ..agents.stats import AgentStats
from ..agents.store import AgentRow, AgentStore, PendingReload, StoreError
from . import places
from .places import Workplaces

log = logging.getLogger("pcbk_core.gateway.reload")

# Д7-R58 — читаются при вызове: тесты их подменяют
RELOAD_TICK_S = 2.0           # такт воркера
TURN_START_GRACE_S = 5.0      # с конца пересылки prompt_async — место ещё могло не стать busy
IDLE_SETTLE_S = 3.0           # пустая карта держится — фоновый заголовок разговора дописался (m15)
RELOAD_WAIT_S = 10.0          # prompt_async ждёт идущую перезагрузку; 10 + 25 (срок хода) < 45 у edge
DISPOSE_TIMEOUT_S = 5.0
VISIBLE_TRIES = 3             # GET /agent после dispose — попыток по places.READY_ATTEMPT_S
VISIBLE_PAUSE_S = 0.5
RELOAD_RETRY_S = 30.0         # после неудачи у места — не раньше
RELOAD_DELAYED_S = 900.0      # ждёт дольше — reload_delayed, один раз на ожидание
DIR_CHECK_S = 10.0            # проверка каталога агентов

RepairResult = Literal["quarantined", "image", "capped", "not_config"]

_EXPECTED = ("saved", "visible")         # место должно видеть после перезагрузки (как AgentStore.expected)
# нет ответа места: соединение, срок (asyncio.timeout — TimeoutError), обрыв, нет пароля места (OSError)
_NO_ANSWER = (httpx.HTTPError, TimeoutError, OSError)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _config_error_body(body: bytes) -> bool:
    """Тело 400 места — ошибка конфигурации OpenCode: JSON-объект, name начинается с Config и кончается на Error.
    Разбор пути и карантин — задача А6 (Д7б, Д7-R59)."""
    try:
        obj = json.loads(body)
    except (ValueError, RecursionError):
        return False
    name = obj.get("name") if isinstance(obj, dict) else None
    return isinstance(name, str) and name.startswith("Config") and name.endswith("Error")


class GateTimeout(Exception):
    """prompt_async не дождался конца перезагрузки места за RELOAD_WAIT_S."""


class _Gate:
    """Ворота одного места: active — идущие prompt_async, waiting — ждущие, reloading — идёт перезагрузка, free —
    «свободно» (установлено, пока reloading нет)."""

    __slots__ = ("active", "waiting", "reloading", "free")

    def __init__(self) -> None:
        self.active = 0
        self.waiting = 0
        self.reloading = False
        self.free = asyncio.Event()
        self.free.set()


class TurnGates:
    """Ворота мест: читатели (turn — prompt_async) и писатель (try_reload / end_reload — воркер). Ворота места
    создаются при первом обращении к нему."""

    def __init__(self) -> None:
        self._gates: dict[int, _Gate] = {}

    def _gate(self, n: int) -> _Gate:
        gate = self._gates.get(n)
        if gate is None:
            gate = self._gates[n] = _Gate()
        return gate

    @asynccontextmanager
    async def turn(self, n: int) -> AsyncIterator[None]:
        """Читатель: пока место перезагружается — ждать free не дольше RELOAD_WAIT_S (иначе GateTimeout); waiting
        уменьшается при любом выходе из ожидания — пробуждение, срок, отмена (m-р3-4). Затем active до выхода."""
        gate = self._gate(n)
        if gate.reloading:
            gate.waiting += 1
            try:
                async with asyncio.timeout(RELOAD_WAIT_S) as limit:
                    while gate.reloading:
                        await gate.free.wait()
            except TimeoutError:
                if limit.expired():
                    raise GateTimeout() from None
                raise
            finally:
                gate.waiting -= 1
        gate.active += 1
        try:
            yield
        finally:
            gate.active -= 1

    def try_reload(self, n: int) -> bool:
        """Писатель без ожидания: отказ, если место уже перезагружается, идёт prompt_async или он ждёт у ворот (m2)."""
        gate = self._gate(n)
        if gate.reloading or gate.active or gate.waiting:
            return False
        gate.reloading = True
        gate.free.clear()
        return True

    def end_reload(self, n: int) -> None:
        """Конец перезагрузки — синхронно, без исключений: ждущие prompt_async проходят."""
        gate = self._gate(n)
        gate.reloading = False
        gate.free.set()

    def active(self, n: int) -> int:
        return self._gate(n).active

    def waiting(self, n: int) -> int:
        return self._gate(n).waiting


class AgentReloads:
    """Воркер перезагрузки агентов мест (Д7-R58). Конструктор без ввода-вывода.

    gates — ворота prompt_async; checked_at — пульс (последний удачный проход по очереди, обновляется и перед каждым
    местом); pending_view(now) — (мест в очереди, возраст старейшей метки в секундах) только по готовым местам.
    monotonic — отсрочка, успокоение, повтор и каталог; wallclock — since меток, пульс и время строк учёта.
    """

    def __init__(self, workplaces: Workplaces, store: AgentStore, stats: AgentStats, *,
                 monotonic: Callable[[], float] = time.monotonic, wallclock: Callable[[], datetime] = _utcnow,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, drill: str = ""):
        self.workplaces = workplaces
        self.store = store
        self.stats = stats
        self._monotonic = monotonic
        self._wallclock = wallclock
        self._sleep = sleep
        self.drill = drill
        self.gates = TurnGates()
        self.checked_at: datetime | None = None
        self._pending: dict[int, PendingReload] = {}     # очередь последнего удачного прохода (для pending_view)
        self._turn_at: dict[int, float] = {}            # конец последней пересылки prompt_async (monotonic)
        self._idle_since: dict[int, float] = {}         # с какого такта карта статусов пуста (monotonic)
        self._failed_at: dict[int, float] = {}          # последняя неудача перезагрузки места (monotonic)
        self._delayed: dict[int, datetime] = {}         # since ожидания, уже посчитанного в reload_delayed
        self._dir_at: float | None = None               # последняя проверка каталога (monotonic)
        self._hooks: set[asyncio.Task] = set()          # задачи крючка готовности
        self._queue_failed = False                      # прошлое чтение очереди не удалось (строка журнала — на смене)

    # --- прокси и места ---

    def note_turn_start(self, n: int) -> None:
        """Пересылка prompt_async места кончилась (прокси зовёт её при выходе из ворот, предпроверка П11): отсрочка
        TURN_START_GRACE_S — от этого момента, а пустая карта, замеченная до хода, не в счёт."""
        self._turn_at[n] = self._monotonic()
        self._idle_since.pop(n, None)

    def place_ready(self, n: int) -> None:
        """Крючок готовности (Workplaces.on_ready): место стало готовым — фоновой задачей поставить его в очередь с
        since от этого момента, если оно уже в очереди или у него есть строки saved. Исключение — строка журнала."""
        self._idle_since.pop(n, None)
        try:
            task = asyncio.get_running_loop().create_task(self._ready_mark(n), name=f"gw-agents-ready-{n:02d}")
        except RuntimeError:                            # вне цикла событий крючку некуда поставить задачу
            log.warning("gw agents ready place=%02d error=NoLoop", n)
            return
        self._hooks.add(task)
        task.add_done_callback(self._hooks.discard)

    async def _ready_mark(self, n: int) -> None:
        now = self._wallclock()
        try:
            await asyncio.to_thread(self._mark_if_needed, n, now)
        except Exception as exc:
            log.warning("gw agents ready place=%02d error=%s", n, type(exc).__name__)

    def _mark_if_needed(self, n: int, now: datetime) -> bool:
        if n in self.store.pending() or self.store.has_saved(n):
            self.store.mark_reload(n, now, reset_since=True)
            return True
        return False

    async def drain(self) -> None:
        """Дождаться идущих задач крючка готовности (начало прохода, остановка роли, тесты)."""
        hooks = [task for task in self._hooks if not task.done()]
        if hooks:
            await asyncio.gather(*hooks, return_exceptions=True)

    def pending_view(self, now: datetime) -> tuple[int, int | None]:
        """Очередь для /health/agents — только готовые места (Д7-R58): (число мест, секунд с since старейшей метки, не
        меньше нуля) или (0, None). Только память: очередь — с последнего удачного прохода."""
        marks = [mark for n, mark in self._pending.items() if n in places.PLACES and self.workplaces.is_ready(n)]
        if not marks:
            return 0, None
        oldest = min(mark.since for mark in marks)
        return len(marks), max(0, int((now - oldest).total_seconds()))

    async def repair(self, n: int, body: bytes) -> RepairResult:
        """Карантин файла агента, сломавшего конфигурацию места (Д7-R59), — задача А6 (Д7б). До неё — заглушка: тело
        «не про конфигурацию», и 400 после dispose — неудача перезагрузки."""
        return "not_config"

    # --- цикл ---

    async def run(self) -> None:
        """Проход сразу, дальше — раз в RELOAD_TICK_S. freeze_reload — только первый проход."""
        if self.drill:
            log.warning("УЧЕНИЯ: DRILL_AGENTS=%s", self.drill)
        while True:
            try:
                await self.tick()
            except Exception as exc:                    # проход не роняет воркер
                log.warning("gw agents tick error=%s", type(exc).__name__)
            if self.drill == "freeze_reload":
                log.warning("УЧЕНИЯ: перезагрузка агентов замерла после первого прохода")
                await asyncio.Event().wait()
            await self._sleep(RELOAD_TICK_S)

    async def tick(self) -> None:
        """Один проход (см. модуль)."""
        await self.drain()                              # m8: метка крючка — до чтения очереди
        await self._check_dir()
        try:
            pending = await asyncio.to_thread(self.store.pending)
        except Exception as exc:                        # StoreError — и непредвиденное: пульса нет
            self.stats.worker_store_failed(type(exc).__name__)
            if not self._queue_failed:
                log.warning("gw agents queue error=%s", type(exc).__name__)
            self._queue_failed = True
            return
        if self._queue_failed:
            log.info("gw agents queue ok")
        self._queue_failed = False
        self._pending = dict(pending)
        self.checked_at = self._wallclock()
        self.stats.worker_store_ok()
        for n, mark in pending.items():
            self.checked_at = self._wallclock()        # m3: долгий проход по местам не даёт ложного «молчит»
            try:
                await self._place_pass(n, mark)
            except Exception as exc:                    # страховка: _place_pass ловит сам
                self._pass_failed(n, "pass", exc)

    async def _check_dir(self) -> None:
        now = self._monotonic()
        if self._dir_at is not None and now - self._dir_at < DIR_CHECK_S:
            return
        self._dir_at = now
        try:
            problem = await asyncio.to_thread(self.store.dir_problem)
        except Exception as exc:
            log.warning("gw agents dir error=%s", type(exc).__name__)
            return
        if problem != self.stats.dir_error:
            if problem is None:
                log.info("gw agents dir ok")
            else:
                log.warning("gw agents dir problem=%s", problem)
        self.stats.dir_error = problem

    # --- место ---

    async def _place_pass(self, n: int, mark: PendingReload) -> None:
        """Проход одного места; любое исключение — отметка неудачи (_pass_failed) с шагом, на котором оно случилось."""
        step = ["status"]
        try:
            await self._reload_place(n, mark, step)
        except Exception as exc:
            self._pass_failed(n, step[0], exc)

    async def _reload_place(self, n: int, mark: PendingReload, step: list[str]) -> None:
        w = self.workplaces
        if not w.is_ready(n) or w.is_waking(n):         # спит, будится, sp-ctl молчит: пропуск, метка остаётся
            self._idle_since.pop(n, None)
            return
        self._note_delayed(n, mark)                     # m6: до выходов по /session/status
        failed = self._failed_at.get(n)
        if failed is not None and self._monotonic() - failed < RELOAD_RETRY_S:
            return
        if not await self._idle(n):
            return
        now = self._monotonic()                          # после await: конец хода мог прийти за время проверки
        since = self._idle_since.setdefault(n, now)
        turn = self._turn_at.get(n)
        if (turn is not None and now - turn < TURN_START_GRACE_S) or now - since < IDLE_SETTLE_S:
            return
        step[0] = "rows"
        rows = await asyncio.to_thread(self.store.list_place, n)   # строки до dispose: их файлы уже на диске
        if not self.gates.try_reload(n):
            return
        lock = w.lock(n)
        held = False
        try:
            # К1 (доводка р1): пока шло чтение строк (await), ход мог целиком пройти ворота — они ещё открыты — и
            # кончиться 204, когда busy у места ещё не стоит. Его конец (note_turn_start) снял idle_since и сдвинул
            # _turn_at, крючок готовности тоже снимает idle_since: проверка — сразу после try_reload, без await между
            # ними. Дальше ворота закрыты: новый ход до dispose не войдёт, повтор проверки перед dispose не нужен.
            if self._idle_since.get(n) != since or self._turn_at.get(n) != turn:
                return
            step[0] = "status"
            if not await self._idle(n):                 # а) ход начался, пока шли проверки
                return
            # замок места занят (пробуждение, остановка, перезапуск) или место не готово — выйти (m-н12). is_waking —
            # не дубль замка: задача пробуждения (и перезапуска А6 — тоже задача в Workplaces._tasks) может быть
            # создана, но замок ещё не взять, или ждать его, когда он только что отпущен
            if lock.locked() or not w.is_ready(n) or w.is_waking(n):
                return
            # m3: замок свободен, и между проверкой и захватом нет await — acquire берёт его без ожидания. Ждать у
            # замка места может только задача пробуждения (А6: и перезапуска): _reap и _restore сначала смотрят _busy
            # (замок или задача) и на занятом замке не ждут. Пока такая задача есть, is_waking истинно — и сюда не
            # дойти; поэтому окна «замок отпущен, но ждущий ещё не проснулся» здесь нет. restart после карантина (А6)
            # зовётся уже после release ниже — воркер своего же замка не ждёт.
            await lock.acquire()
            held = True
            step[0] = "dispose"
            failure = await self._dispose(n)            # б)
            if failure is not None:
                self._failed(n, "dispose", failure)     # m7: idle_since остаётся — повтор после паузы одним тактом
                return
            step[0] = "agent"
            seen, failure = await self._visible(n, {row.name for row in rows if row.state in _EXPECTED})   # в)
            lock.release()                              # замок места — только на шагах б–в (m-р3-5)
            held = False
            if seen is None:
                self._failed(n, "agent", failure)
                return
            step[0] = "mark"                            # г)
            now_wall = self._wallclock()
            newly, visible, invisible = await asyncio.to_thread(self._settle_rows, n, rows, seen, now_wall)
            for _ in range(newly):
                self.stats.event("visible")
            for _ in range(invisible):
                self.stats.event("not_visible")
            done = await asyncio.to_thread(self.store.reload_done, n, mark.seq)
            self._idle_since.pop(n, None)
            self._failed_at.pop(n, None)
            if invisible:
                log.warning("gw agents reload place=%02d result=not_visible n=%d", n, invisible)
            else:
                log.info("gw agents reload place=%02d result=visible n=%d wait_s=%d", n, visible,
                         max(0, int((now_wall - mark.since).total_seconds())))
            if done:                                    # иначе пришла новая метка — следующий проход перезагрузит снова
                self._pending.pop(n, None)
                self._delayed.pop(n, None)
        finally:
            if held:
                lock.release()
            self.gates.end_reload(n)

    def _note_delayed(self, n: int, mark: PendingReload) -> None:
        """Ожидание дольше RELOAD_DELAYED_S от since — reload_delayed и строка журнала, один раз на ожидание (since)."""
        if self._delayed.get(n) == mark.since:
            return
        wait = (self._wallclock() - mark.since).total_seconds()
        if wait > RELOAD_DELAYED_S:
            self._delayed[n] = mark.since
            self.stats.event("reload_delayed")
            log.warning("gw agents reload place=%02d result=delayed wait_s=%d", n, int(wait))

    async def _idle(self, n: int) -> bool:
        """Своя проверка GET /session/status (не _session_busy уборщика, где сбой — «не занято»): True — пустая карта;
        busy/retry, любая непустая карта, не 200, не JSON, не объект, нет ответа — «занято или не знаю»: False, и
        пустая карта считается заново (idle_since снят)."""
        try:
            r = await self.workplaces.request(n, "GET", "/session/status", timeout=places.BUSY_CHECK_S)
            sessions = r.json() if r.status_code == 200 else None
        except _NO_ANSWER:
            sessions = None
        except (ValueError, RecursionError):            # не JSON или вложенность глубже предела разбора
            sessions = None
        if isinstance(sessions, dict) and not sessions:
            return True
        self._idle_since.pop(n, None)
        return False

    async def _dispose(self, n: int) -> str | None:
        """POST /instance/dispose со сроком DISPOSE_TIMEOUT_S: None — 200, иначе причина для журнала."""
        try:
            r = await self.workplaces.request(n, "POST", "/instance/dispose", timeout=DISPOSE_TIMEOUT_S)
        except _NO_ANSWER as exc:
            return type(exc).__name__
        return None if r.status_code == 200 else f"status_{r.status_code}"

    async def _visible(self, n: int, expected: set[str]) -> tuple[set[str] | None, str]:
        """GET /agent до VISIBLE_TRIES раз (попытка — places.READY_ATTEMPT_S, пауза VISIBLE_PAUSE_S), пока место не
        увидит всех expected. (имена последнего ответа 200, "") или (None, причина): ни одного 200, или 400 с
        ошибкой конфигурации — тогда repair (до А6 — заглушка not_config) и без повторов."""
        seen: set[str] | None = None
        why = "no_answer"
        for attempt in range(VISIBLE_TRIES):
            if attempt:
                await self._sleep(VISIBLE_PAUSE_S)
            try:
                r = await self.workplaces.request(n, "GET", "/agent", timeout=places.READY_ATTEMPT_S)
            except _NO_ANSWER as exc:
                why = type(exc).__name__
                continue
            if r.status_code == 400 and _config_error_body(r.content):
                return None, f"config_{await self.repair(n, r.content)}"
            if r.status_code != 200:
                why = f"status_{r.status_code}"
                continue
            try:
                items = r.json()
            except (ValueError, RecursionError):
                why = "not_json"
                continue
            if not isinstance(items, list):
                why = "not_list"
                continue
            seen = {a["name"] for a in items if isinstance(a, dict) and isinstance(a.get("name"), str)}
            if expected <= seen:
                break
        return seen, why

    def _settle_rows(self, n: int, rows: list[AgentRow], seen: set[str], now: datetime) -> tuple[int, int, int]:
        """Шаг г в потоке. Состояния строк — перечитанные сейчас (итоговое ревью Д7а, m1): за время перезагрузки агента
        могли унести в архив или карантин. Ожидаемые (saved, visible) и прежде невидимые (not_visible) строки, которые
        место видит, — visible. Невидимые — только те, что ждали этой перезагрузки (ожидаемые в rows до dispose) и
        ожидаемые до сих пор: ушедший в архив агент — не not_visible, а сохранённый после чтения rows дождётся своей
        перезагрузки (его метка пришла позже). Ответ — (стало видно, видно всего, не видно)."""
        waited = {row.name for row in rows if row.state in _EXPECTED}
        current = self.store.list_place(n)
        visible = [row for row in current if row.state in (*_EXPECTED, "not_visible") and row.name in seen]
        invisible = [row for row in current if row.state in _EXPECTED and row.name in waited and row.name not in seen]
        if visible:
            self.store.mark_visible(n, [row.name for row in visible], now)
        for row in invisible:
            self.store.set_state(n, row.name, "not_visible", now)
        return sum(1 for row in visible if row.state != "visible"), len(visible), len(invisible)

    # --- неудачи ---

    def _failed(self, n: int, step: str, why: str) -> None:
        """Ожидаемая неудача (dispose не 200 или без ответа, GET /agent не ответил): повтор не раньше RELOAD_RETRY_S;
        idle_since остаётся (m7)."""
        self._failed_at[n] = self._monotonic()
        self.stats.event("reload_failed")
        log.warning("gw agents reload place=%02d result=failed step=%s error=%s", n, step, why)

    def _pass_failed(self, n: int, step: str, exc: Exception) -> None:
        """Исключение прохода места (в том числе StoreError учёта): повтор не раньше RELOAD_RETRY_S, idle_since снят —
        dispose не повторяется каждые 2 с (раунд 3, I-р3-1); у StoreError — флаг сбоя учёта воркера."""
        self._failed_at[n] = self._monotonic()
        self._idle_since.pop(n, None)
        self.stats.event("reload_failed")
        if isinstance(exc, StoreError):
            self.stats.worker_store_failed(type(exc).__name__)
        log.warning("gw agents reload place=%02d result=failed step=%s error=%s", n, step, type(exc).__name__)
