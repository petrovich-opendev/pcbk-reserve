"""Роль llm (Д4а): LLM-прокси перед OpenRouter — отказы словами, резерв и предел одновременных вызовов, учёт
денег через журнал вызовов, опрос /key и /credits, выключатель и учение 402; прогон отбора модели под своим
потолком (Д8-R69).

Конструктор без ввода-вывода (П22, договор Role Д3а): файлов не читает, core.db не открывает, сети не трогает,
клиента httpx не создаёт. Журнал открывается в начале lifespan через asyncio.to_thread; health() и
health_json() читают только запомненное. Состояние роли меняется только в цикле событий; SQLite — поток-писатель
журнала, flush/check/open/close — только через asyncio.to_thread.

Доли мест (Д10а, Д10-R98): shares — Shares на том же core.db (build_llm_role); роль ставит ему settled_of =
ledger.settled (пол доли, B-1) и отдаёт его Budget — отказ «доля израсходована» идёт по доле своего места. Доли
открываются в lifespan сразу после журнала (asyncio.to_thread); не открылись — config_error «доли бюджета не загружены»:
все вызовы /llm/ — 403 pcbk_config (закрыто при сбое: иначе студент с урезанной долей снова получил бы долю по
умолчанию).

Тормоз прогона (Д11, Д11-R117): brake — RunBrake при прогоне (LLM_RUN_*), иначе None (тормоза нет вовсе). lifespan после
открытия журнала читает его защёлку и базы из llm_meta (brake.load), а без config_error ставит задачу llm-run-brake:
проверка сразу и раз в RUN_BRAKE_POLL_S, в потоке (чтение /proc и flush журнала при защёлке); исключение — строка журнала
и проверка без данных (blind). Под защёлкой Budget отказывает вызывающим прогона 402 pcbk_run_halted (budget.halted —
чтение флага), шлюз усыпляет места прогона и не будит их (gateway: Workplaces.halted, _halt_watch). /health/llm — блок
run_brake (32-й ключ): null без прогона. Модуль brake импортируется только внутри методов: python -m
pcbk_core.llm.brake иначе давал бы RuntimeWarning runpy."""
import asyncio
import logging
import math
import re
import time
from collections import deque
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI

from ..app import RoleBase
from ..egress import refresh_started_age_s, refresh_status
from ..hooks import hooks_of
from ..secrets import TokenTable
from ..settings import LlmSettings, Settings
from .api import CHAT_DEADLINE_S, LEDGER_DURABLE_S, LlmCall, llm_router, too_large_body
from .ledger import LEDGER_CHECK_S, OPS, Budget, Ledger, LedgerWriteLost, RunSpec, run_spec
from .models import select_models
from .shares import PLACES, Shares
from .state import FAIL_STATES, UpstreamState
from .turns import TurnLimiter
from .upstream import Upstream, UpstreamError, UpstreamKind

if TYPE_CHECKING:
    from .brake import RunBrake

# llm-tokens не прочитан: это не config_error — всем 401, но опрос /key, дозапрос цен и проверка журнала идут
LLM_TOKENS_ERROR = "токены LLM не читаются"
# дозапрос цены после 404 — около 9 минут (Д4а-R13)
COST_RETRY_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0, 120.0, 300.0)
# новая core.db при расходе ключа больше этого — прежний расход пропал из бюджета (больше трат сырой проверки)
DB_RESET_WARN_USD = 0.05
CONFIG_ERROR_CHARS = 60                 # 18 + 60 ≤ 80
LEDGER_ERROR = "журнал вызовов не пишется"
LEDGER_ERROR_CHARS = 53                 # 27 + 53 ≤ 80
LEDGER_DROPPED = "строка журнала вызовов потеряна — перезапустите core"
SHARE_WARN = 0.8                        # «доля больше 80 %»
STUDENT = re.compile(r"student-[0-9]+")
# прогон отбора модели (Д8-R69): точка отсчёта вызывающего в llm_meta — расход до прогона; окно счёта вызовов, с
RUN_BASE_KEY = "run_base_usd:{id}:{caller}"
RUN_WINDOW_S = 600
RUN_BASE_ERROR = "прогон: точка отсчёта не записана"
SHARES_ERROR = "доли бюджета не загружены"           # Д10-R98: закрыто при сбое

log = logging.getLogger("pcbk_core.llm")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


class LlmRole(RoleBase):
    """upstream None — ключа OpenRouter нет (или настройка с ошибкой); key_fingerprint — первые 12 знаков sha256
    ключа, сам ключ живёт только внутри Upstream. config_error — настройка с ошибкой: /llm/ — 403 pcbk_config,
    фоновых циклов, кроме проверки журнала, нет. tokens_error — llm-tokens не прочитан (П23).

    Прогон отбора (Д8-R69): run — RunSpec или None; run_calls — монотонные метки begin_call вызывающих прогона за
    RUN_WINDOW_S, last_run_call_mono — последняя из них, started_mono — запуск роли (тишина без вызовов — от него).
    LLM_RUN_* с ошибкой (настройки без from_env) — config_error: закрыто при сомнении.

    shares (Д10-R98) — доли мест или None (у всех мест одна LLM_SHARE_USD, как до Д10)."""

    name = "llm"

    def __init__(self, settings: Settings, llm: LlmSettings, *, tokens: TokenTable, upstream: Upstream | None,
                 ledger: Ledger, key_fingerprint: str | None = None, config_error: str | None = None,
                 tokens_error: str | None = None, monotonic: Callable[[], float] = time.monotonic,
                 wallclock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 cost_retry_delays: Sequence[float] = COST_RETRY_DELAYS_S,
                 chat_deadline_s: float = CHAT_DEADLINE_S, shares: Shares | None = None):
        self.settings = settings
        self.llm = llm
        self.tokens = tokens
        self.upstream = upstream
        self.ledger = ledger
        self.key_fingerprint = key_fingerprint
        self.config_error = config_error
        self.tokens_error = tokens_error
        self.monotonic = monotonic
        self.wallclock = wallclock
        self.cost_retry_delays = tuple(cost_retry_delays)
        self.chat_deadline_s = chat_deadline_s
        self.models = select_models(llm.LLM_MODELS)
        run: RunSpec | None = None
        try:
            run = run_spec(llm)
        except ValueError as exc:           # from_env это уже отсёк; здесь — настройки, построенные мимо него
            why = f"настройки: {exc}"
            self.config_error = f"{self.config_error}; {why}" if self.config_error else why
        self.run = run
        self.run_calls: deque[float] = deque()
        self.last_run_call_mono: float | None = None
        self.started_mono = monotonic()
        # хост OpenRouter, как его разрешает охрана выхода (egress_targets): живость адресов — только по нему
        self.egress_name = urlsplit(llm.OPENROUTER_BASE_URL).hostname
        # доли мест (Д10-R98): пол доли — от оплаченного по журналу вызовов (B-1), отказ — по доле своего места
        self.shares = shares
        if shares is not None:
            shares.settled_of = ledger.settled
        # тормоз прогона (Д11-R117): только при прогоне; конструктор без ввода-вывода — мету читает load() в lifespan
        self.brake: "RunBrake | None" = None
        if run is not None:
            from .brake import RunBrake
            self.brake = RunBrake(
                (llm.LLM_RUN_MEMINFO, llm.LLM_RUN_VMSTAT, llm.LLM_RUN_PRESSURE),
                (llm.LLM_RUN_MEM_FAIL_MIB, llm.LLM_RUN_SWAP_GROW_MIB, llm.LLM_RUN_SWAP_BASE_MIB,
                 llm.LLM_RUN_PSI_FULL_MAX),
                meta=ledger, run_id=run.id, monotonic=monotonic, wallclock=wallclock, started_mono=self.started_mono)
        self.budget = Budget(ledger, total_usd=llm.LLM_TOTAL_USD, share_usd=llm.LLM_SHARE_USD,
                             ops_share_usd=llm.LLM_OPS_SHARE_USD, key_spent=self.key_spent_usd, run=run,
                             shares=shares, halted=self.run_halted)
        self.turns = TurnLimiter(limit=llm.LLM_TURN_CALLS, monotonic=monotonic)
        self.state = UpstreamState(drill=self.drill)
        self.inflight: dict[str, int] = {}
        self.checked_at: datetime | None = None           # конец последней попытки опроса
        self.key_usage_usd: float | None = None
        self.first_key_usage_usd: float | None = None     # usage первого удачного /key этого процесса
        self.account_remaining_usd: float | None = None
        self.mgmt_key: Literal["ok", "missing", "rejected", "error"] = (
            "ok" if upstream is not None and upstream.has_mgmt_key else "missing")
        self._cost_tasks: set[asyncio.Task] = set()

    # --- признаки ---

    @property
    def drill(self) -> bool:
        return self.llm.DRILL_LLM == "402"

    @property
    def stopped(self) -> bool:
        return self.llm.LLM_STATE == "stopped"

    def run_halted(self) -> bool:
        """Защёлка тормоза прогона (Д11-R117): чтение флага, без ввода-вывода; без прогона — ложь."""
        brake = self.brake
        return brake is not None and brake.halted

    def key_spent_usd(self) -> float | None:
        """Расход по ключу с точки отсчёта этого ключа (Д4а-R13); без точки, usage или при off — None."""
        if self.llm.LLM_KEY_DEDICATED != "on" or self.key_usage_usd is None or self.key_fingerprint is None:
            return None
        base = self.ledger.meta_get(f"key_base_usd:{self.key_fingerprint}")
        return None if base is None else self.key_usage_usd - base

    def ledger_ok(self) -> bool:
        """Только запомненные итоги журнала, без ввода-вывода (ревью задачи 4): открыт, писатель жив, проверка
        записи не провалилась, сбоев подряд нет и ни одна операция не брошена."""
        led = self.ledger
        return (led.opened and led.writer_alive and led.writable_ok is not False and led.consecutive_failures == 0
                and led.writes_dropped_total == 0)

    def ledger_error(self) -> Literal["dropped", "write"] | None:
        """dropped — операция журнала брошена (держится до перезапуска: каждый flush — LedgerWriteLost), write —
        запись не идёт сейчас (сбои подряд, писатель мёртв, проверка провалилась, журнал закрыт)."""
        if self.ledger.writes_dropped_total > 0:
            return "dropped"
        return None if self.ledger_ok() else "write"

    def ledger_reset(self) -> bool:
        """Журнал создан заново, а ключ уже тратил: прежний расход пропал из бюджета вместе с core.db."""
        return (self.ledger.fresh is True and self.llm.LLM_KEY_DEDICATED == "on"
                and self.first_key_usage_usd is not None and self.first_key_usage_usd > DB_RESET_WARN_USD)

    # --- протокол роли ---

    def health(self) -> tuple[bool, str]:
        """Без ввода-вывода (договор Role Д3а), первое подходящее; тексты — не длиннее 80 знаков."""
        if self.config_error:
            return False, "ошибка настройки: " + self.config_error[:CONFIG_ERROR_CHARS]
        if self.stopped:
            return False, "LLM-прокси остановлен администратором"
        if self.tokens_error:
            return False, self.tokens_error
        if self.upstream is None:
            return False, "нет ключа OpenRouter"
        error = self.ledger_error()
        if error == "dropped":
            return False, LEDGER_DROPPED
        if error is not None:
            why = self.ledger.last_error
            return False, LEDGER_ERROR + (f": {why[:LEDGER_ERROR_CHARS]}" if why else "")
        if self.budget.total_spent() >= self.budget.total_usd:
            return False, "общий бюджет исчерпан"
        return self.state.state not in FAIL_STATES, self.state.text

    def health_json(self, now: datetime) -> dict:
        """/health/llm: числа, коды и флаги — без токенов, id вызывающих и текстов. now — только для окон
        «за 24 ч» и pending_stuck; checked_at — время опроса."""
        t = self.ledger.totals(now)
        return {
            "checked_at": None if self.checked_at is None else self.checked_at.isoformat(),
            "config_error": self.config_error,
            "tokens_error": self.tokens_error,
            "stopped": self.stopped,
            "key": "ok" if self.upstream is not None or self.key_fingerprint is not None else "missing",
            "mgmt_key": self.mgmt_key,
            "drill": self.llm.DRILL_LLM,
            "ledger_ok": self.ledger_ok(),
            "ledger_error": self.ledger_error(),
            "upstream": self.state.to_json(),
            "spent_usd": self.budget.page_spent(),
            "reserved_usd": t["reserved_usd"],
            "ledger_usd": t["spent_usd"],
            "estimated_usd": t["estimated_usd"],
            "key_spent_usd": self.key_spent_usd(),
            "key_usage_usd": self.key_usage_usd,
            "total_usd": self.llm.LLM_TOTAL_USD,
            "ops_usd": self.ledger.spent(OPS),
            "shares": self._shares(t["by_caller"]),
            "pending": t["pending"],
            "pending_stuck": t["pending_stuck"],
            "cost_unknown_24h": t["cost_unknown_24h"],
            "provider_mismatch_24h": t["provider_mismatch_24h"],
            "account_remaining_usd": self.account_remaining_usd,
            "models": list(self.models),
            "ledger_queue": self.ledger.queue_size,
            "ledger_fresh": self.ledger.fresh,
            "ledger_reset": self.ledger_reset(),
            "egress": refresh_status(self.egress_name),
            "refresh_started_age_s": refresh_started_age_s(),
            "run": self.run_json(self.monotonic()),
            "run_brake": None if self.brake is None else self.brake.json(self.monotonic()),
        }

    def run_json(self, now_mono: float) -> dict | None:
        """Блок run /health/llm (Д8-R69): None без прогона, иначе ровно шесть ключей — id, потолок, расход прогона
        (с резервами идущих вызовов, как у отказа), число вызывающих, вызовов за RUN_WINDOW_S и тишина, с: от
        последнего вызова прогона, а без вызовов — от запуска роли; один знак. Без ввода-вывода."""
        run = self.run
        if run is None:
            return None
        last = self.last_run_call_mono
        since = self.started_mono if last is None else last
        return {"id": run.id, "cap_usd": run.cap_usd, "spent_usd": self.budget.run_spent(),
                "callers": len(run.callers),
                "calls_10m": sum(1 for mark in self.run_calls if now_mono - mark < RUN_WINDOW_S),
                "idle_s": round(max(0.0, now_mono - since), 1)}

    def _shares(self, by_caller: dict[str, float]) -> dict[str, int]:
        """Места student-NN: доля больше 80 % (ещё не исчерпана) и исчерпана — только счётчики. Доля — своя у каждого
        места (budget.share_of, Д10-R98): после перевода счётчики не врут."""
        over = spent = 0
        for caller, amount in by_caller.items():
            if not STUDENT.fullmatch(caller):
                continue
            share = self.budget.share_of(caller)
            if amount >= share:
                spent += 1
            elif amount > SHARE_WARN * share:
                over += 1
        return {"over_80": over, "spent": spent}

    def router(self) -> APIRouter:
        return llm_router(self)

    def install(self, app: FastAPI) -> None:
        """Предел тела /llm/ — LLM_MAX_BODY_BYTES, 413 в форме OpenAI (П9, П19)."""
        hooks_of(app).body_limit("/llm/", self.llm.LLM_MAX_BODY_BYTES, refusal=too_large_body)

    # --- вызов ---

    def begin_call(self, *, caller: str, session: str | None, parent: str | None, model: str, step: int,
                   estimate: float) -> LlmCall:
        """Синхронно: резерв в журнале (open_call) и место одновременности; LlmCall.finish() делает обратное.
        Вызывающему прогона — метка активности прогона (Д8-R69): и под учением 402, раньше open_chat."""
        call_id = self.ledger.open_call(ts=self.wallclock(), caller=caller, session=session, model=model, step=step,
                                        estimate=estimate)
        self.inflight[caller] = self.inflight.get(caller, 0) + 1
        if self.run is not None and caller in self.run.callers:
            mark = self.monotonic()
            self.run_calls.append(mark)
            self.last_run_call_mono = mark
            while self.run_calls and mark - self.run_calls[0] >= RUN_WINDOW_S:
                self.run_calls.popleft()
        return LlmCall(self, call_id, caller=caller, session=session, parent=parent, model=model, step=step,
                       estimate=estimate)

    def note_upstream(self, kind: UpstreamKind | None, now: datetime, *, source: Literal["chat", "poll"]) -> None:
        was = self.state.state
        self.state.record(kind, now, source=source)
        self._note_state(was)

    def _clear_money(self, now: datetime, kind: Literal["money_out", "key_limit"]) -> None:
        was = self.state.state
        self.state.clear_money(now, kind)
        self._note_state(was)

    def _note_state(self, was: str) -> None:
        """В журнал — только смена состояния OpenRouter."""
        now = self.state.state
        if now == was:
            return
        level = logging.WARNING if now in FAIL_STATES or now == "errors" else logging.INFO
        log.log(level, "LLM-прокси: %s", self.state.text)

    def spawn_resolve(self, call_id: int, gen_id: str) -> None:
        """Фоновый дозапрос цены; задачи снимает выход из lifespan (строки остаются pending до следующего старта)."""
        task = asyncio.get_running_loop().create_task(self.resolve_cost(call_id, gen_id), name=f"llm-cost-{call_id}")
        self._cost_tasks.add(task)
        task.add_done_callback(self._cost_done)

    def _cost_done(self, task: asyncio.Task) -> None:
        self._cost_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("LLM-прокси: дозапрос цены упал (%s)", type(task.exception()).__name__)

    async def resolve_cost(self, call_id: int, gen_id: str) -> None:
        """Цена по /generation после пауз cost_retry_delays; нашлась — generation, нет — unknown (стоит оценкой)."""
        self.ledger.resolve_started(call_id, self.wallclock())
        upstream = self.upstream
        if upstream is not None:
            for delay in self.cost_retry_delays:
                await asyncio.sleep(delay)
                cost = await upstream.generation_cost(gen_id)
                if cost is not None:
                    self.ledger.set_cost(call_id, cost, "generation")
                    return
        self.ledger.set_cost(call_id, None, "unknown")
        log.warning("LLM-прокси: цена вызова id=%d не нашлась — в расходе по верхней оценке", call_id)

    # --- прогон отбора (Д8-R69) ---

    def ensure_run_base(self) -> tuple[dict[str, float], str | None]:
        """Точки отсчёта прогона в llm_meta — в потоке, сразу после открытия журнала, до первого вызова; роль не
        меняет: точки и ошибку присваивает lifespan в цикле событий (доводка р1, m4).

        У вызывающего прогона без ключа RUN_BASE_KEY точка — оплаченное им сейчас (Ledger.settled: цены и
        окончательные оценки, без идущих и ждущих цены вызовов). Цена идущих и ждущих вызовов войдёт в прогон —
        завышение в безопасную сторону, а отказа нет: вызов без цены у места на старте не закрывает прокси (доводка
        р1, I1 — отступление от Д8-R69). Записанная точка не меняется: перезапуск core продолжает тот же прогон, новый
        id — новая точка. Новые точки — на диск через _flush_run_base. Возврат — (точки по вызывающим, ошибка или
        None)."""
        run = self.run
        if run is None:
            return {}, None
        led = self.ledger
        keys = {caller: RUN_BASE_KEY.format(id=run.id, caller=caller) for caller in sorted(run.callers)}
        missing = [caller for caller, key in keys.items() if led.meta_get(key) is None]
        unpriced = sum(1 for caller in missing if led.has_open_or_pending_of(caller))
        if unpriced:
            log.warning("LLM-прокси: прогон %s: вызовы без цены у мест прогона без точки отсчёта, мест: %d — их цена "
                        "войдёт в прогон", run.id, unpriced)
        for caller in missing:
            led.meta_set(keys[caller], led.settled(caller))
        error = self._flush_run_base(run) if missing else None
        bases = {caller: base for caller, key in keys.items() if (base := led.meta_get(key)) is not None}
        if error is None:
            log.info("LLM-прокси: прогон %s: потолок $%g, вызывающих %d, новых точек отсчёта %d", run.id,
                     run.cap_usd, len(run.callers), len(missing))
        return bases, error

    def _flush_run_base(self, run: RunSpec) -> str | None:
        """Новые точки — на диск: flush до LEDGER_DURABLE_S, без повторов (доводка р1б).

        Не дождались диска (TimeoutError) — WARNING, а не ошибка: точку допишет писатель, а строка каждого вызова
        ложится на диск до OpenRouter (Д4а-R19) — очередь FIFO не выпустит вызов раньше точки, и денег мимо прогона
        нет. Брошенная операция (LedgerWriteLost) или закрытый журнал — RUN_BASE_ERROR: записи точки не будет."""
        led = self.ledger
        try:
            if not led.opened:                  # журнал закрыт: flush() без очереди вернулся бы сразу
                raise LedgerWriteLost("журнал вызовов закрыт")
            led.flush(LEDGER_DURABLE_S)
        except LedgerWriteLost as exc:          # дыра в файле держится до нового open()
            log.error("LLM-прокси: прогон %s: точка отсчёта не записана (%s)", run.id, type(exc).__name__)
            return RUN_BASE_ERROR
        except TimeoutError as exc:
            log.warning("LLM-прокси: прогон %s: точка отсчёта ещё не на диске (%s) — вызовы ждут записи журнала",
                        run.id, type(exc).__name__)
        return None

    # --- опрос OpenRouter ---

    async def poll(self) -> None:
        """/key, затем, если есть ключ управления, /credits; при выключателе или без ключа — ничего. checked_at —
        конец попытки, удачной или нет."""
        if self.stopped or self.upstream is None:
            return
        try:
            await self._poll_key()
            if self.upstream.has_mgmt_key:
                await self._poll_credits()
        finally:
            self.checked_at = self.wallclock()

    async def _poll_key(self) -> None:
        before = (self.ledger.has_open_or_pending(), self.ledger.spent())
        try:
            info = await self.upstream.key_info()
        except UpstreamError as exc:
            self.note_upstream(exc.kind, self.wallclock(), source="poll")
            return
        now = self.wallclock()
        self.note_upstream(None, now, source="poll")
        usage = _number(info.get("usage"))
        if usage is not None:
            self.key_usage_usd = usage
            if self.first_key_usage_usd is None:
                self.first_key_usage_usd = usage
            await self._set_key_base(usage, before)
        raw = info.get("limit_remaining")
        remaining = _number(raw)
        if remaining is not None and remaining <= 0:
            self.note_upstream("key_limit", now, source="poll")
        elif raw is None or remaining is not None:          # больше 0 или лимита нет
            self._clear_money(now, "key_limit")

    async def _set_key_base(self, usage: float, before: tuple[bool, float]) -> None:
        """Точка отсчёта ключа (Д4а-R13): при LLM_KEY_DEDICATED=on, если её нет, а журнал не держал идущих и
        ждущих дозапроса вызовов ни до запроса /key, ни после, и учтённое за это время не менялось. Точки
        других отпечатков не трогаются."""
        if self.llm.LLM_KEY_DEDICATED != "on" or self.key_fingerprint is None:
            return
        key = f"key_base_usd:{self.key_fingerprint}"
        busy, spent = before
        if (self.ledger.meta_get(key) is not None or busy or self.ledger.has_open_or_pending()
                or self.ledger.spent() != spent):
            return
        self.ledger.meta_set(key, usage - spent)
        try:                                # точка отсчёта — на диске, как строка вызова
            await asyncio.to_thread(self.ledger.flush, LEDGER_DURABLE_S)
        except TimeoutError as exc:
            log.warning("LLM-прокси: точка отсчёта ключа не записана (%s)", type(exc).__name__)

    async def _poll_credits(self) -> None:
        """Остаток счёта ключом управления; его отказ — свой признак mgmt_key, состояние основного ключа не
        меняется (Д4а-R16)."""
        try:
            remaining = await self.upstream.credits()
        except UpstreamError as exc:
            mgmt = "rejected" if exc.status in (401, 403) else "error"
            if mgmt != self.mgmt_key:
                log.warning("LLM-прокси: ключ управления OpenRouter: %s (%s)",
                            "отклонён — остаток счёта не виден" if mgmt == "rejected" else "остаток не получен", exc)
            self.mgmt_key, self.account_remaining_usd = mgmt, None
            return
        if remaining is None:
            return
        now = self.wallclock()
        self.mgmt_key, self.account_remaining_usd = "ok", remaining
        if remaining <= 0:
            self.note_upstream("money_out", now, source="poll")
        else:
            self._clear_money(now, "money_out")

    # --- жизненный цикл ---

    def lifespan(self):
        return self._lifespan()

    @asynccontextmanager
    async def _lifespan(self):
        if not await asyncio.to_thread(self.ledger.open):
            why = f"журнал вызовов не открылся: {self.ledger.last_error}"
            self.config_error = f"{self.config_error}; {why}" if self.config_error else why
        shares = self.shares
        if shares is not None and not await asyncio.to_thread(shares.open):   # закрыто при сбое (Д10-R98)
            why = SHARES_ERROR + (f": {shares.last_error}" if shares.last_error else "")
            self.config_error = f"{self.config_error}; {why}" if self.config_error else why
        try:                                # точка отсчёта прогона — до первого вызова и до дозапроса цен
            bases, error = await asyncio.to_thread(self.ensure_run_base)
        except Exception as exc:            # закрыто при сомнении; в журнал — только класс
            log.error("LLM-прокси: точка отсчёта прогона не поставлена (%s)", type(exc).__name__)
            bases, error = {}, RUN_BASE_ERROR
        self.budget.run_base = bases        # состояние роли — в цикле событий (m4)
        if error is not None:
            self.config_error = f"{self.config_error}; {error}" if self.config_error else error
        if self.brake is not None:          # защёлка и базы тормоза прогона — из llm_meta (память журнала)
            self.brake.load()
        llm = self.llm
        allocated = (len(PLACES) * llm.LLM_SHARE_USD if shares is None else shares.sum_cents() / 100)
        log.info("LLM-прокси: моделей %d, доли мест: распределено $%g из $%g, по умолчанию $%g", len(self.models),
                 allocated, llm.LLM_TOTAL_USD, llm.LLM_SHARE_USD)
        if self.drill:
            log.warning("УЧЕНИЯ: DRILL_LLM=%s", llm.DRILL_LLM)
        if self.stopped:
            log.warning("LLM-прокси остановлен администратором (LLM_STATE=stopped)")
        if self.tokens_error:
            log.error("LLM-прокси: токены LLM не читаются — все вызовы получают 401")
        tasks = [asyncio.create_task(self._ledger_loop(), name="llm-ledger-check")]
        if self.config_error:
            log.error("LLM-прокси: ошибка настройки: %s", self.config_error)
        else:
            if self.upstream is None:
                log.warning("LLM-прокси: нет ключа OpenRouter — вызовы получают отказ")
            else:
                for call_id, gen_id in self.ledger.pending_to_resolve():     # цена, которую не дождался прошлый запуск
                    self.spawn_resolve(call_id, gen_id)
            tasks.append(asyncio.create_task(self._poll_loop(), name="llm-poll"))
            if self.brake is not None:      # тормоз прогона (Д11-R117) — только при прогоне и без config_error
                tasks.append(asyncio.create_task(self._brake_loop(), name="llm-run-brake"))
        try:
            yield
        finally:
            pending = [*tasks, *self._cost_tasks]           # строки дозапроса остаются pending до следующего старта
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if self.upstream is not None:
                try:
                    await self.upstream.aclose()
                except Exception as exc:
                    log.warning("LLM-прокси: клиент OpenRouter не закрыт (%s)", type(exc).__name__)
            await asyncio.to_thread(self.ledger.close)      # дописать очередь писателя

    async def _ledger_loop(self) -> None:
        """Проверка записи журнала сразу и раз в LEDGER_CHECK_S — в потоке (F9); в журнал — только смена."""
        while True:
            was = self.ledger.writable_ok
            try:
                await asyncio.to_thread(self.ledger.check)
            except Exception as exc:
                log.error("LLM-прокси: проверка журнала вызовов упала (%s)", type(exc).__name__)
            now = self.ledger.writable_ok
            if now is False and was is not False:
                log.error("LLM-прокси: журнал вызовов: запись не проходит (%s)", self.ledger.last_error)
            elif now and was is False:
                log.info("LLM-прокси: журнал вызовов: запись снова проходит")
            await asyncio.sleep(LEDGER_CHECK_S)

    async def _brake_loop(self) -> None:
        """Тормоз прогона: проверка сразу и раз в RUN_BRAKE_POLL_S (читается каждый раз), в потоке. Исключение — строка
        журнала с классом и проверка без данных (blind): цикл не умирает, а молчание видно по age_s."""
        from . import brake as brake_module

        brake = self.brake
        log.info("LLM-прокси: тормоз прогона %s: свободно меньше %d МиБ, swap +%d МиБ (база %s), давление full > %g %%",
                 brake.run_id, brake.fail_mib, brake.swap_grow_mib,
                 "первая проверка" if brake.swap_base_cfg < 0 else f"{brake.swap_base_cfg} МиБ", brake.psi_full_max)
        while True:
            try:
                await asyncio.to_thread(brake.check)
            except Exception as exc:
                log.error("LLM-прокси: тормоз прогона: проверка упала (%s)", type(exc).__name__)
                try:
                    await asyncio.to_thread(brake.failed)
                except Exception as again:
                    log.error("LLM-прокси: тормоз прогона: учёт сбоя упал (%s)", type(again).__name__)
            await asyncio.sleep(brake_module.RUN_BRAKE_POLL_S)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception as exc:        # цикл не умирает; в журнал — только класс
                log.error("LLM-прокси: опрос OpenRouter упал (%s)", type(exc).__name__)
            await asyncio.sleep(self.llm.LLM_POLL_S)
