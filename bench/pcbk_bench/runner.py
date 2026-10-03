"""Прогонщик отбора (Д8-R74): план по повторам, места параллельно, барьер, пробуждение, сроки, конец окна, пульс,
замок, остановки, журнал сессий, сверка модели и калькулятором, возобновление.

Порядок и барьер. plan_runs — по повторам (REPS по группам; повтор 3 — только B и C). Места идут параллельно, в
месте — один разговор за раз. Повтор k+1 не стартует, пока все работающие места не закончили повтор k: главный
поток запускает потоки мест одного повтора и ждёт их всех (барьер). Место, закончившее повтор, ждёт и раз в
KEEPALIVE_S по clock получает POST …/workplace/wake (продлевает простой места; GET-опрос его не продлевает).
Остановленное место (PLACE_STRIKES stand_error подряд) барьер не ждёт и в следующих повторах не идёт.

Подготовка — prepare(), порядок строгий (I-р4-7): window_start не позже сейчас + 5 мин (иначе RunRefused
("window_start"), р1 m7) → замок OUT/.lock (fcntl, pid внутри; close() его стирает) → пульс (поток-демон, раз в
heartbeat_s атомарно переписывает OUT/progress.json; П7) → блок run в /health/llm и LLM_RUN_CALLERS → вход учёткой
и ensure_ready каждого места → abort сессий из OUT/sessions.log без строки конца через своё место (П5; 404 — «уже
нет») → пустой /session/status у каждого места (≤ IDLE_WAIT_S, иначе place_busy: чужой прогонщик с другим OUT, р1
m4) → модель места (GET /config) → единицы тегов карточек (OUT/tag_units.json) → архив чужих агентов места →
bench-uzel и его ready (только если в плане есть его сценарии: фильтр scenarios конфига, П14). Любое расхождение —
RunRefused с кодом; замок и пульс снимаются (close()). run() зовёт prepare() сам, если его не звали; close() — в
finally у run().

Прогон (run_one): пробуждение места (ensure_ready: GET /workplace, не ready — wake и ожидание ready, затем
mcp=connected), разговор со сценарным ботом или скриптом (ScriptedStudent), у A — после удачного save_agent ожидание
ready нового агента, мост к нему в новой сессии и архив всех агентов места, кроме bench-uzel, при любом статусе (и
сохранённых уже после abort, m-р4-3), с ожиданием их ухода из «Мои агенты» — внутри срока прогона. Затем калькулятор
по аргументам последних вызовов tag_now и tag_period хода (статус rate — пауза и повтор на всё окно частоты службы,
calc_patient; после жёсткой остановки или сигнала — без пауз; rate и после него — заметка NOTE_CALC_RATE, сбой —
NOTE_CALC_FAIL: сверка не молчит, Б3) и строки журнала прокси: у каждой сессии с законченным ходом (и у моста) есть
строки, их model — модель места, иначе stand_error (Д8-R71, р1 m6).

Сроки (m-р4-5): ожидание хода — min(turn_timeout_s, срок прогона − сейчас), у A срок разговора — без
archive_reserve_s. Срок — abort сессии через шлюз, статус timeout; срок, вышедший между ходами, — timeout без новой
реплики (р1, m1). 503 от шлюза посреди прогона (WorkplaceNotReady
или WorkplaceBusy после повторов клиента) — ensure_ready и один повтор вызова, а не stand_error.

Конец окна: window_end() = min(начало прогона + 130 мин, window_start + 165 мин). Новый прогон группы g стартует,
только если now() + run_timeout_s[g] ≤ window_end() — время берётся после проверки потолка (р1, m3); не успевает —
ключ остаётся для возобновления, причина window.

Остановки (STOP_REASONS): cap — run.spent_usd + stop_margin_usd ≥ cap (новые прогоны не начинаются) или 402
pcbk_run_spent в ходе; money — pcbk_money_out, pcbk_key_limit, pcbk_budget_spent, pcbk_share_spent; stand — строка
«LLM-прокси и бюджет» fail или «Память сервера» не ok STAND_STRIKES проверок подряд через STAND_CHECK_S (одна
проверка на прогонщик: перед повтором и в главном потоке, m13), или в /health/llm нет блока run этого прогона (перед
каждым прогоном и каждой репликой, р1 I2); window; interrupt — SIGINT/SIGTERM: идущие сессии получают abort, пока
они идут, не чаще ABORT_REPEAT_S (р1 m2, р1б), прогоны — interrupted; projection — пауза на барьере 1: прогноз
Σ_g(расход повтора 1 группы g × reps[g]) больше cap − stop_margin, а projection_ack нет; place — у места,
остановленного PLACE_STRIKES stand_error подряд, остались прогоны плана (р1, I1; state stopped, status — код 2).
После cap по 402, money, interrupt и пропавшего блока run места новых реплик не шлют («жёсткая» остановка).

Журнал сессий OUT/sessions.log — строки «<id>\\t<NN>\\tstart» при создании сессии и «<id>\\t<NN>\\tend» в конце
прогона, при abort и при уборке (П5). Записи прогонов — OUT/runs.jsonl (RunRecord.to_json, по строке); возобновление
пропускает ключи с последней записью done. Строки журнала прогонщика (logging) — без текста разговоров и тегов.

Режим нагрузки (cfg.load; Д11-R119…R122, R131; сторож памяти и вердикт — load.py). prepare(): окно → замок → пульс →
сторож памяти (первая точка) → блок run (места ⊆ вызывающих) и run_brake (watching и свежий, иначе RunRefused("brake"))
→ потолок и пороги LLM_RUN_* core — как в конфиге ступени (иначе RunRefused("run_limits", поле); доводка р1, m-3) →
строка памяти на старте ok, без пометок «нет данных» (иначе RunRefused("memory")) → режим данных (none — пометка
«учения: tool_error» в строке историана, иначе RunRefused("data_drill"); live — OUT/real-data-consent.txt, иначе
RunRefused("consent"), и без пометки учения; twin — только стенд) → OUT/health_start.json → поток сторожа → для каждого
места: остановка — выход; вход; опрос сторожа (тормоз перед пробуждением, I-6 р2); остановка — выход; пробуждение и
ensure_ready → живые агенты, кроме bench-uzel, — RunRefused("place_agents") без архива → OUT/pre_sessions.json (id
разговоров, бывших на местах) → прежние сессии → пустой /session/status → модель → единицы → bench-uzel (если нужен).
Остановка во время подготовки — выход без исключения: run() вернёт её причину. В «без данных» пометку учения сторож
проверяет и на ходу, каждым опросом: пропала — жёстко data_drill (доводка р1, m-4).
Жёсткая остановка сторожа (memory, data_drill, blind, brake) и pcbk_run_halted в ходе — _halt_stop: причина (поверх
мягкой), сначала stand.sleep_places всех мест конфига (медленный шлюз не держит сон, доводка р1, I-2), затем abort
идущих сессий (и на каждом шаге барьера); пробуждений после неё нет, а
пробуждение, гонкой прошедшее после неё, сразу снова усыпляет место; close() усыпляет все места ещё раз. Мягкая
(memory_warn) — новые прогоны не начинаются, идущие доходят. Решения о памяти — только у сторожа (_stand_ok смотрит
строку LLM из его последнего опроса). Живость сторожа — на каждом шаге барьера (поток жив, опрос не старше
LOAD_BLIND_S), иначе blind; метка guard_at — в progress.json (status OUT: старая — код 1). «Закончен» — только после
чистого опроса сторожа в конце плана. В конце плана (и при мягкой остановке) — «только своё» в OUT/isolation.json,
после жёсткой — пропуск с заметкой; в конце прогона — OUT/load.json (места, начало, конец, причина, числа сторожа).
"""
from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import signal
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .cards import ALLOWED_TOOLS, CardSet, bench_agent_args
from .config import BenchConfig
from .gateway import GatewayChat, GatewayError
from .load import (BRAKE_SILENT_S, CONSENT_FILE, DRILL_DATA_NOTE, HARD_VERDICTS, ISO_SKIPPED, LOAD_MEM_POLL_S,
                   MemoryGuard, own_only, write_json)
from .numbers import TAG_TOKEN_RE
from .record import RunKey, RunRecord, ToolCall, Turn, parse_turn
from .scenarios import BENCH_AGENT, BY_ID, SCENARIO_IDS
from .stand import CALC_RATE_PAUSE_S, CALC_TRIES, Stand, calc_patient  # noqa: F401 — постоянные: имена прогонщика
from .student import ScriptedStudent

log = logging.getLogger("pcbk_bench.runner")

STOP_REASONS = ("cap", "money", "stand", "window", "interrupt", "projection", "place", "memory", "blind", "brake",
                "memory_warn", "data_drill")
MONEY_CODES = frozenset({"pcbk_money_out", "pcbk_key_limit", "pcbk_budget_spent", "pcbk_share_spent"})
CAP_CODE = "pcbk_run_spent"
RUN_SPENT_TEXT = "Потолок прогона отбора модели исчерпан"           # LLM_MESSAGES["run_spent"]
HALT_CODE = "pcbk_run_halted"                                        # тормоз прогона core (Д11-R117)
LIMIT_EPS = 1e-6                                                     # сверка LLM_RUN_* core с конфигом ступени
RUN_HALTED_TEXT = "Прогон остановлен: сервер на пределе памяти"     # LLM_MESSAGES["run_halted"]
MODEL_CODES = frozenset({"pcbk_turn_limit"})                         # отказ прокси по вине модели — не стенд
# имена инструментов службы — не коды отказа прокси: из ALLOWED_TOOLS (тот же источник, что у check_run; с Д9а — и ряд
# с наработкой, Д9-R89), без своей копии списка
TOOL_NAMES = frozenset({f"pcbk_{tool}" for tool in ALLOWED_TOOLS} | {"pcbk_save_agent"})
RUN_WINDOW_S = 130 * 60
OWNER_WINDOW_S = 165 * 60
KEEPALIVE_S = 600.0
WAKE_TIMEOUT_S = 120.0
MCP_TIMEOUT_S = 90.0
AGENT_WAIT_S = 60.0
IDLE_WAIT_S = 60.0
FUTURE_WINDOW_S = 300.0                                    # window_start позже сейчас + 5 мин — отказ (р1, m7)
ABORT_WAIT_S = 10.0
ABORT_REPEAT_S = 1.0                                        # повторный abort одной сессии — не чаще (р1б, п. 4)
STAND_CHECK_S = 60.0
STAND_STRIKES = 3
PLACE_STRIKES = 3
HEALTH_TRIES = 3
HEALTH_PAUSE_S = 5.0
ROWS_RETRY_S = 2.0
POLL_S = 1.0
BARRIER_TICK_S = 0.05
NOTE_CALC_RATE = "калькулятор: rate — сверка не сделана"     # заметка записи; таблица считает такие прогоны
NOTE_CALC_FAIL = "калькулятор: сбой"     # начало заметки о сбое калькулятора (+ класс); таблица считает и такие
AGENTS = {"bench": BENCH_AGENT, "konstruktor": "konstruktor", "pomoshnik": "pomoshnik"}
_PCBK_CODE = re.compile(r"\bpcbk_[a-z_]+\b")


def card_tags(cards: CardSet) -> list[str]:
    """Теги карточек, единицы которых прогонщик читает из каталога (_read_units): теги заготовленного агента, затем
    tag, expect_tag и pick карточек — только вида тега, без повторов."""
    tags: list[str] = list(cards.bench_tags)
    for c in cards.cards.values():
        tags += [t for t in (c.tag, c.expect_tag, c.pick) if t and TAG_TOKEN_RE.fullmatch(t)]
    return list(dict.fromkeys(tags))


class RunRefused(Exception):
    """Отказ запуска прогона: код (locked, run_id, run_callers, place_model, bench_agent, not_ready, place_busy,
    window_start; нагрузка — brake, run_limits, memory, data_drill, consent, load_data, place_agents, window_base) и
    уточнение без значений (у run_limits — какое поле LLM_RUN_* расходится с конфигом)."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class PlaceNotReady(Exception):
    """Место не стало готовым: wake не дал ready или mcp=connected не пришёл в срок."""


class _RunStatus(Exception):
    """Конец прогона с этим статусом (timeout, interrupted, capped, stand_error)."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


# --- план, журналы и состояние на диске ---

def plan_runs(cfg: BenchConfig) -> list[RunKey]:
    """Ключи плана по повторам: повтор, затем место, затем сценарий; группа идёт, пока reps[группа] ≥ повтора;
    cfg.scenarios (П14) оставляет только эти сценарии."""
    top = max(cfg.reps.values())
    wanted = set(cfg.scenarios) if cfg.scenarios is not None else set(SCENARIO_IDS)
    return [RunKey(n, sid, rep) for rep in range(1, top + 1) for n in sorted(cfg.places) for sid in SCENARIO_IDS
            if sid in wanted and cfg.reps[BY_ID[sid].group] >= rep]


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_progress(path: Path, state: dict) -> None:
    """progress.json атомарно (временный файл рядом и os.replace): читатель не видит половины файла."""
    _atomic_write(Path(path), json.dumps(state, ensure_ascii=False, indent=1) + "\n")


def load_tag_units(out: Path) -> dict[str, str]:
    """Единицы тегов карточек из OUT/tag_units.json; нет файла — {}."""
    try:
        raw = json.loads((Path(out) / "tag_units.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("tag_units.json: нужен объект")
    return {str(k): v for k, v in raw.items() if isinstance(v, str)}


def load_records(out: Path) -> list[RunRecord]:
    """Все записи OUT/runs.jsonl по порядку; оборванная последняя строка (смерть процесса посреди записи) —
    пропускается."""
    path = Path(out) / "runs.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(RunRecord.from_json(json.loads(line)))
        except (ValueError, KeyError, TypeError):
            log.warning("runs.jsonl: строка не разобрана — пропущена")
    return records


def status_main(out: Path, stale_s: float) -> int:
    """Код состояния прогона по OUT/progress.json: 0 — идёт и свеж или закончен; 1 — running, а updated_at старше
    stale_s (умер прогонщик) или last_turn_at старше turn_timeout_s + 300 (завис главный поток, m-р4-6), или в режиме
    нагрузки метка сторожа памяти guard_at старше stale_s (сторож молчит, R119), или файла нет; 2 — stopped или paused.
    Печатает одну строку без данных."""
    try:
        st = json.loads((Path(out) / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("прогон: progress.json нет или не читается")
        return 1
    if not isinstance(st, dict):
        print("прогон: progress.json не объект")
        return 1
    state = st.get("state")
    by_place = st.get("by_place") if isinstance(st.get("by_place"), dict) else {}
    stopped = sorted(f"{int(n):02d}" for n, v in by_place.items()
                     if isinstance(v, dict) and v.get("status") == "stopped" and str(n).isdecimal())
    tail = f", места остановлены: {', '.join(stopped)}" if stopped else ""
    print(f"прогон {st.get('run_id')}: {state}, сделано {st.get('done')} из {st.get('total')}, повтор {st.get('rep')},"
          f" причина {st.get('stop_reason') or '-'}{tail}")
    if state == "finished":
        return 0
    if state in ("stopped", "paused"):
        return 2
    if state != "running":
        return 1
    now = datetime.now(timezone.utc)
    updated = _parse_ts(st.get("updated_at"))
    if updated is None or (now - updated).total_seconds() > stale_s:
        return 1
    if st.get("load"):
        guard = _parse_ts(st.get("guard_at")) or _parse_ts(st.get("started_at"))
        if guard is None or (now - guard).total_seconds() > stale_s:
            return 1
    turn = _parse_ts(st.get("last_turn_at")) or _parse_ts(st.get("started_at"))
    limit = float(st.get("turn_timeout_s") or 900.0) + 300.0
    if turn is not None and (now - turn).total_seconds() > limit:
        return 1
    return 0


# --- разбор хода ---

def turn_code(error: str | None) -> str | None:
    """Класс ошибки хода для прогонщика: capped (402 pcbk_run_spent), halted (402 pcbk_run_halted — тормоз прогона core,
    Д11-R119), money, stand_error (прочий отказ прокси) или None — ошибки нет или она модели (MessageAbortedError,
    pcbk_turn_limit …): та идёт в провал errors check_run."""
    if not error:
        return None
    codes = set(_PCBK_CODE.findall(error)) - TOOL_NAMES
    if CAP_CODE in codes or RUN_SPENT_TEXT in error:
        return "capped"
    if HALT_CODE in codes or RUN_HALTED_TEXT in error:
        return "halted"
    if codes & MONEY_CODES:
        return "money"
    if codes - MODEL_CODES:
        return "stand_error"
    return None


def _short(tool: str) -> str:
    return tool.removeprefix("pcbk_")


def _output(call: ToolCall) -> dict:
    out = call.output
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except ValueError:
            return {}
    return out if isinstance(out, dict) else {}


def _save_ok(call: ToolCall) -> bool:
    out = _output(call)
    return _short(call.tool) == "save_agent" and out.get("status") == "ok" and not out.get("already")


def _flags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return [value]
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    return []


def _row_cost(row: Mapping[str, Any]) -> float:
    cost = row.get("cost")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        cost = row.get("cost_est")
    return float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0


class Runner:
    """Прогонщик отбора (см. шапку модуля). poll_s — шаг опроса ожиданий (место, mcp, агенты, статус сессии);
    mem_poll_s — такт сторожа памяти в режиме нагрузки (по часам clock)."""

    poll_s = POLL_S
    mem_poll_s = LOAD_MEM_POLL_S

    def __init__(self, cfg: BenchConfig, stand: Stand, chats: Mapping[int, GatewayChat], cards: CardSet,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 only: Iterable[RunKey] | None = None):
        if cfg.out_dir is None:
            raise ValueError("out_dir: не задан")
        if set(cfg.places) - set(chats):
            raise ValueError("chats: нет клиента шлюза у места конфига")
        self.cfg = cfg
        self.stand = stand
        self.chats = dict(chats)
        self.cards = cards
        self.sleep = sleep
        self.clock = clock
        self.now = now
        self.out = Path(cfg.out_dir)
        self.out.mkdir(mode=0o700, parents=True, exist_ok=True)
        plan = plan_runs(cfg)
        if only is not None:
            wanted = set(only)
            plan = [k for k in plan if k in wanted]
        self.plan = plan
        missing = {k.scenario for k in plan} - set(cards.cards)
        if missing:
            raise ValueError(f"карточки: нет сценариев плана ({len(missing)})")
        self.tag_units = load_tag_units(self.out)
        self.started: list[RunKey] = []
        self._started_mono: list[float] = []
        self._started_wall: dict[RunKey, datetime] = {}
        self._finished_mono: dict[RunKey, float] = {}
        self._lock = threading.RLock()
        self._io = threading.Lock()
        self._hard = threading.Event()
        self._interrupt = threading.Event()
        self._pulse_stop = threading.Event()
        self._pulse: threading.Thread | None = None
        self._lock_fd: int | None = None
        self._prepared = False
        self._state = "idle"
        self._stop_reason: str | None = None
        self._window_hit = False
        self._run_started_wall: datetime | None = None
        self._last_turn_at: datetime | None = None
        self._run_spent: float | None = None
        self._projected: float | None = None
        self._rep = 0
        self._live: dict[int, str] = {}
        self._waiting: dict[int, float] = {}
        self._ended: set[str] = set()
        self._turned: set[str] = set()                  # сессии с законченным ходом (строки журнала — у каждой)
        self._aborted_at: dict[str, float] = {}         # сессия → clock последнего abort главного потока
        self._thread_errors: list[BaseException] = []
        self._halt = threading.Event()                  # жёсткая остановка тормозом (сторож памяти, pcbk_run_halted)
        self._guard: MemoryGuard | None = None
        self._pre: dict[int, set[str]] = {}             # разговоры, бывшие на местах до прогона (R121)
        latest: dict[RunKey, RunRecord] = {}
        for rec in load_records(self.out):
            latest[rec.key] = rec
        self._done_keys = {k for k, r in latest.items() if r.status == "done"}
        self._plan_set = set(plan)
        self._place = {n: {"status": "idle", "done": sum(1 for k in plan if k.place == n and k in self._done_keys),
                           "total": sum(1 for k in plan if k.place == n), "current": None, "stand_errors": 0}
                       for n in cfg.places}

    # --- для проверки ---

    def started_at(self, i: int) -> float:
        return self._started_mono[i]

    def finished_at(self, key: RunKey) -> float:
        return self._finished_mono[key]

    def started_at_wall(self, key: RunKey) -> datetime:
        return self._started_wall[key]

    # --- окно, прогноз, пульс ---

    def window_end(self) -> datetime:
        """min(начало прогона + 130 мин, window_start + 165 мин); до prepare() начало прогона — сейчас."""
        start = self._run_started_wall or self.now()
        end = start + timedelta(seconds=RUN_WINDOW_S)
        if self.cfg.window_start is not None:
            end = min(end, self.cfg.window_start + timedelta(seconds=OWNER_WINDOW_S))
        return end

    def projection(self) -> float:
        """Прогноз расхода по журналу прокси: Σ по группам (расход всех прогонов повтора 1 группы × reps[группа])."""
        spent: dict[str, float] = defaultdict(float)
        for rec in load_records(self.out):
            if rec.key.rep == 1 and rec.key.place in self.cfg.places and rec.key.scenario in BY_ID:
                spent[BY_ID[rec.key.scenario].group] += sum(_row_cost(r) for r in rec.llm_rows)
        return round(sum(v * self.cfg.reps[g] for g, v in spent.items()), 6)

    def progress(self) -> dict:
        """Состояние для progress.json; в режиме нагрузки — и load, guard_at (последний опрос сторожа памяти)."""
        with self._lock:
            state = {"run_id": self.cfg.run_id, "state": self._state, "started_at": _iso(self._run_started_wall),
                     "updated_at": _iso(self.now()), "last_turn_at": _iso(self._last_turn_at),
                     "turn_timeout_s": self.cfg.turn_timeout_s, "done": len(self._done_keys & self._plan_set),
                     "total": len(self.plan), "rep": self._rep,
                     "by_place": {str(n): dict(v) for n, v in self._place.items()},
                     "run_spent_usd": self._run_spent, "projected_usd": self._projected,
                     "stop_reason": self._stop_reason}
        if self.cfg.load:
            guard = self._guard
            state["load"] = True
            state["guard_at"] = _iso(guard.at_wall) if guard is not None else None
        return state

    def _write_progress(self) -> None:
        write_progress(self.out / "progress.json", self.progress())

    def _pulse_loop(self) -> None:
        while not self._pulse_stop.wait(self.cfg.heartbeat_s):
            try:
                self._write_progress()
            except OSError as exc:
                log.warning("пульс: progress.json не записан (%s)", type(exc).__name__)

    # --- замок, подготовка, закрытие ---

    def _acquire_lock(self) -> None:
        fd = os.open(self.out / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RunRefused("locked") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
        self._lock_fd = fd

    def prepare(self) -> None:
        """Порядок Д8-R74 (см. шапку), в режиме нагрузки — Д11-R119 (_prepare_load); расхождение — RunRefused с кодом,
        замок, пульс и сторож памяти снимаются; остановка во время подготовки нагрузки — выход без исключения."""
        if self._prepared:
            return
        ws = self.cfg.window_start
        if ws is not None and ws > self.now() + timedelta(seconds=FUTURE_WINDOW_S):
            raise RunRefused("window_start")             # начало окна — первая строка Б5, не будущее (р1, m7)
        self._acquire_lock()
        try:
            with self._lock:
                self._run_started_wall = self.now()
                self._state = "running"
            self._pulse_stop.clear()
            self._write_progress()
            self._pulse = threading.Thread(target=self._pulse_loop, name="пульс", daemon=True)
            self._pulse.start()
            log.info("подготовка: прогон %s, мест %d", self.cfg.run_id, len(self.cfg.places))
            if self.cfg.load:
                self._prepare_load()
            else:
                self._prepare_selection()
            self._prepared = True
            log.info("подготовка: %s", "остановлена" if self._stop_reason is not None else "готово")
        except BaseException:
            self.close()
            raise

    def _prepare_selection(self) -> None:
        """Подготовка отбора (Д8-R74): после окна, замка и пульса."""
        self._check_run_block()
        for n in self.cfg.places:
            try:
                self.chats[n].login()
                self.ensure_ready(n)
            except Exception as exc:
                log.warning("подготовка: место %02d не готово (%s)", n, type(exc).__name__)
                raise RunRefused("not_ready") from None
        self._resume_sessions()
        self._idle_places()
        self._place_models()
        self._read_units()
        for n in self.cfg.places:
            for row in self.stand.agent_rows(n):
                name = row.get("name")
                if isinstance(name, str) and name != BENCH_AGENT:
                    self.stand.archive_agent(n, name)
        if any(BY_ID[k.scenario].agent == "bench" for k in self.plan):
            self._bench_agents()                  # без сценариев bench-uzel — не сохраняется (П14)

    def _idle_places(self) -> None:
        for n in self.cfg.places:                     # чужой прогонщик (другой OUT) на тех же местах (р1, m4)
            if not self._until(lambda n=n: not self.chats[n].session_status(), IDLE_WAIT_S):
                log.warning("подготовка: на месте %02d идёт чужой разговор", n)
                raise RunRefused("place_busy")

    def _place_models(self) -> None:
        for n, ref in self.cfg.places.items():
            if self.stand.place_model(n) != ref:
                log.warning("подготовка: модель места %02d — не из конфига", n)
                raise RunRefused("place_model")

    def _stopping(self) -> bool:
        return self._stop_reason is not None or self._hard.is_set()

    def _prepare_load(self) -> None:
        """Подготовка нагрузки (Д11-R119, см. шапку): остановка во время неё — выход, дальше места не будятся."""
        guard = MemoryGuard(self.stand, self.cfg, self.out, clock=self.clock, now=self.now, sleep=self.sleep,
                            on_verdict=self._on_guard, poll_s=self.mem_poll_s)
        self._guard = guard
        first = guard.check(act=False)
        if guard.dead:                                    # сторож умер на первом опросе — слепой прогон не идёт
            self._halt_stop("blind")
            return
        self._check_run_block(guard.last_health)
        p = guard.points[-1]
        if not p.health_ok or p.brake != "watching" or p.brake_age_s is None or p.brake_age_s > BRAKE_SILENT_S:
            log.warning("подготовка: тормоз прогона core не следит (%s)", p.brake)
            raise RunRefused("brake")
        self._check_run_limits(guard.last_health)
        if p.state != "ok" or first not in ("ok", "data_drill"):          # пометку учения называет режим данных
            log.warning("подготовка: строка памяти на старте — %s, сторож — %s", p.state, first)
            raise RunRefused("memory")
        self._check_data_mode(guard.last_status)
        if not (self.out / "health_start.json").exists():
            write_json(self.out / "health_start.json", guard.first_health)
        guard.start()
        try:
            self._prepare_load_places()
        except Exception:
            if not self._halt.is_set():
                raise
            log.warning("подготовка: прервана тормозом")      # места усыплены — их ответы уже не отказ подготовки

    def _prepare_load_places(self) -> None:
        """Места нагрузки: перед каждым входом и пробуждением — остановка? (I-6 р2), затем проверки R121 и Д8-R74."""
        guard = self._guard
        for n in self.cfg.places:
            if self._stopping():
                return
            try:
                self.chats[n].login()
            except Exception as exc:
                log.warning("подготовка: вход на место %02d не удался (%s)", n, type(exc).__name__)
                raise RunRefused("not_ready") from None
            guard.check()                                 # тормоз перед пробуждением — свежий опрос (I-6 р2)
            if self._stopping():
                return
            try:
                self._wake(n)
                self.ensure_ready(n)
            except Exception as exc:
                if self._halt.is_set():
                    return
                log.warning("подготовка: место %02d не готово (%s)", n, type(exc).__name__)
                raise RunRefused("not_ready") from None
        for n in self.cfg.places:                         # чужие агенты: отказ без архива (R121)
            if any(isinstance(r.get("name"), str) and r["name"] != BENCH_AGENT for r in self.stand.agent_rows(n)):
                log.warning("подготовка: на месте %02d живые агенты — место не для нагрузки", n)
                raise RunRefused("place_agents")
        self._record_pre_sessions()
        if self._stopping():
            return
        self._resume_sessions()
        self._idle_places()
        if self._stopping():
            return
        self._place_models()
        self._read_units()
        if self._stopping():
            return
        if any(BY_ID[k.scenario].agent == "bench" for k in self.plan):
            self._bench_agents()

    def _check_run_limits(self, health: dict | None) -> None:
        """Потолок прогона и пороги тормоза core (LLM_RUN_*, как их отдаёт /health/llm) — как в конфиге ступени (доводка
        р1, m-3): забытый LLM_RUN_SWAP_BASE_MIB или другой потолок — отказ с полем; поле конфига null — не сверяется."""
        health = health if isinstance(health, dict) else {}
        run = health.get("run") if isinstance(health.get("run"), dict) else {}
        brake = health.get("run_brake") if isinstance(health.get("run_brake"), dict) else {}
        pairs = (("cap_usd", run.get("cap_usd"), self.cfg.cap_usd),
                 ("mem_fail_mib", brake.get("fail_mib"), self.cfg.mem_fail_mib),
                 ("swap_base_mib", brake.get("swap_base_mib"), self.cfg.swap_base_mib),
                 ("swap_grow_mib", brake.get("swap_grow_mib"), self.cfg.swap_grow_mib),
                 ("psi_full_max", brake.get("psi_full_max"), self.cfg.psi_full_max))
        for name, have, want in pairs:
            if want is None:
                continue
            if isinstance(have, bool) or not isinstance(have, (int, float)) or abs(have - want) > LIMIT_EPS:
                log.warning("подготовка: %s в core — не как в конфиге ступени", name)
                raise RunRefused("run_limits", name)

    def _check_data_mode(self, status: dict | None) -> None:
        """Режим данных (R131) по строке историана страницы: none — пометка учения tool_error обязательна; live —
        OUT/real-data-consent.txt и без пометки; twin — только стенд (CLI run его не пускает)."""
        mode = self.cfg.load_data
        row = next((c for c in (status or {}).get("checks") or []
                    if isinstance(c, dict) and c.get("component") == "historian"), None)
        text = " ".join(str(row.get(k) or "") for k in ("detail", "note")) if row is not None else ""
        drill = DRILL_DATA_NOTE in text
        if mode == "none":
            if not drill:
                log.warning("подготовка: «без данных», а пометки учения tool_error в строке историана нет")
                raise RunRefused("data_drill")
        elif mode == "live":
            if not (self.out / CONSENT_FILE).is_file():
                raise RunRefused("consent")
            if drill:
                log.warning("подготовка: «с данными», а в строке историана пометка учения")
                raise RunRefused("data_drill")
        elif mode != "twin":
            raise RunRefused("load_data")

    def _record_pre_sessions(self) -> None:
        """Разговоры, бывшие на местах до прогона (R121), — OUT/pre_sessions.json (только id; при возобновлении —
        объединение)."""
        path = self.out / "pre_sessions.json"
        pre: dict[str, list[str]] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                pre = {str(k): [str(x) for x in v] for k, v in raw.items() if isinstance(v, list)}
        for n in self.cfg.places:
            try:
                listed = self._gw(n, self.chats[n].sessions)
            except Exception as exc:
                log.warning("подготовка: разговоры места %02d не прочитаны (%s)", n, type(exc).__name__)
                raise RunRefused("not_ready") from None
            ids = {s["id"] for s in listed if isinstance(s.get("id"), str)}
            pre[str(n)] = sorted(set(pre.get(str(n), [])) | ids)
        write_json(path, pre)
        self._pre = {int(k): set(v) for k, v in pre.items() if k.isdecimal()}

    def _check_run_block(self, health: dict | None = None) -> None:
        """Блок run этого прогона в /health/llm; вызывающие — ровно места конфига, в режиме нагрузки — места ⊆
        вызывающих (R119)."""
        if health is None:
            health = self.stand.health_llm()
        run = health.get("run")
        if not isinstance(run, dict) or run.get("id") != self.cfg.run_id:
            raise RunRefused("run_id")
        places = frozenset(f"student-{n:02d}" for n in self.cfg.places)
        if self.cfg.load:
            callers = run.get("callers")
            if (isinstance(callers, bool) or not isinstance(callers, int) or callers < len(places)
                    or not places <= self.stand.run_callers()):
                raise RunRefused("run_callers")
        else:
            if run.get("callers") != len(self.cfg.places):
                raise RunRefused("run_callers")
            if self.stand.run_callers() != places:
                raise RunRefused("run_callers")
        with self._lock:
            self._run_spent = run.get("spent_usd")

    def _open_sessions(self) -> dict[str, int | None]:
        path = self.out / "sessions.log"
        if not path.exists():
            return {}
        opened: dict[str, int | None] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                sid, nn, kind = parts
                place = int(nn) if nn.isdecimal() else None
            elif len(parts) == 2:                       # строка без места — abort через все места конфига
                (sid, kind), place = parts, None
            else:
                continue
            if kind == "start":
                opened[sid] = place
            elif kind == "end":
                opened.pop(sid, None)
        return opened

    def _resume_sessions(self) -> None:
        """Сессии прошлого запуска без строки конца — abort через своё место (П5); 404 — разговора уже нет."""
        for sid, place in self._open_sessions().items():
            targets = [place] if place in self.chats else ([] if place is not None else sorted(self.chats))
            if not targets:
                log.warning("возобновление: сессия места вне конфига — не прервана")
                continue
            for p in targets:
                try:
                    self.chats[p].abort(sid)
                except GatewayError as exc:
                    if exc.status != 404:
                        raise RunRefused("not_ready") from None
            self._journal(sid, targets[0], "end")
            log.info("возобновление: сессия прошлого запуска прервана (место %02d)", targets[0])

    def _read_units(self) -> None:
        """Единицы тегов карточек из каталога (catalog_search токеном ops) → tag_units и OUT/tag_units.json; при
        возобновлении — только недостающие (m10)."""
        units = dict(self.tag_units)
        for tag in card_tags(self.cards):
            if tag in units:
                continue
            answer = self._calc("catalog_search", {"query": tag, "limit": 10})
            if answer.get("status") == "rate":
                log.warning("подготовка: единица тега не прочитана — rate")
            for match in answer.get("matches") or []:
                if (isinstance(match, dict) and str(match.get("tag", "")).upper() == tag.upper()
                        and isinstance(match.get("unit"), str) and match["unit"]):
                    units[tag] = match["unit"]
                    break
        self.tag_units = units
        _atomic_write(self.out / "tag_units.json", json.dumps(units, ensure_ascii=False, indent=1) + "\n")
        log.info("подготовка: единиц тегов %d", len(units))

    def _bench_agents(self) -> None:
        args = bench_agent_args(self.cards)
        for n in self.cfg.places:
            answer = self.stand.save_agent(n, args)
            problems = answer.get("problems") if isinstance(answer.get("problems"), list) else []
            if answer.get("status") != "ok" or any(isinstance(p, dict) and p.get("code") == "name_taken"
                                                   for p in problems):
                log.warning("подготовка: %s места %02d не сохранён", BENCH_AGENT, n)
                raise RunRefused("bench_agent")
            if not self._wait_agent(n, BENCH_AGENT, present=True, deadline=self.clock() + AGENT_WAIT_S):
                log.warning("подготовка: %s места %02d не стал готов", BENCH_AGENT, n)
                raise RunRefused("bench_agent")

    def close(self) -> None:
        """Снять сторож памяти, пульс и замок (идемпотентно); после жёсткой остановки тормозом — ещё раз усыпить все
        места конфига (I-6 р2); последний progress.json — у владельца замка. pid в OUT/.lock стирается до снятия замка:
        устаревший pid после конца прогона не назовёт чужой процесс (README, «Аварийная уборка», шаг 2); после смерти
        процесса pid остаётся, но замок свободен."""
        guard = self._guard
        if guard is not None:
            guard.stop()
        if self._halt.is_set():
            self._sleep_places()
        self._pulse_stop.set()
        pulse = self._pulse
        if pulse is not None and pulse is not threading.current_thread():
            pulse.join(timeout=5.0)
        self._pulse = None
        if self._lock_fd is not None:
            with self._lock:
                if self._state == "running":
                    self._state = "stopped"
            try:
                self._write_progress()
            except OSError as exc:
                log.warning("закрытие: progress.json не записан (%s)", type(exc).__name__)
            try:
                os.ftruncate(self._lock_fd, 0)
            except OSError as exc:
                log.warning("закрытие: pid в .lock не стёрт (%s)", type(exc).__name__)
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None
        self._prepared = False

    # --- места ---

    def _until(self, ready: Callable[[], bool], limit_s: float, deadline: float | None = None) -> bool:
        """Опрос ready() раз в poll_s до limit_s (и не дольше deadline по clock); хотя бы одна проверка."""
        end = self.clock() + limit_s if deadline is None else min(self.clock() + limit_s, deadline)
        while True:
            if ready():
                return True
            if self.clock() >= end:
                return False
            self.sleep(self.poll_s)

    def ensure_ready(self, place: int) -> None:
        """GET /workplace; не ready — wake и ожидание ready (≤ WAKE_TIMEOUT_S), затем mcp=connected
        (≤ MCP_TIMEOUT_S). После жёсткой остановки тормозом место не будится (PlaceNotReady)."""
        chat = self.chats[place]
        if chat.workplace().get("state") != "ready":
            log.info("место %02d не готово — пробуждение", place)
            self._wake(place)
        if not self._until(lambda: self.stand.place_mcp_ok(place), MCP_TIMEOUT_S):
            raise PlaceNotReady("mcp")

    def _wake(self, place: int) -> None:
        """POST wake места: после жёсткой остановки — нет; гонкой прошло после неё — место сразу снова усыпляется."""
        if self._halt.is_set():
            raise PlaceNotReady("halted")
        self.chats[place].wake(timeout=WAKE_TIMEOUT_S)
        self._resleep(place)

    def _resleep(self, place: int) -> None:
        if not self._halt.is_set():
            return
        try:
            self.stand.sleep_places([place])
        except Exception as exc:
            log.error("тормоз: место %02d после пробуждения не усыплено (%s)", place, type(exc).__name__)
        raise PlaceNotReady("halted")

    def _gw(self, place: int, fn: Callable[..., Any], *args: Any) -> Any:
        """Вызов шлюза: 503 (место спит или ворота перезагрузки) — ensure_ready и один повтор (Д8-R74, П6)."""
        try:
            return fn(*args)
        except GatewayError as exc:
            if exc.status != 503:
                raise
            log.info("место %02d: 503 %s — пробуждение и повтор", place, exc.name)
        self.ensure_ready(place)
        return fn(*args)

    def _wait_agent(self, place: int, name: str, *, present: bool, deadline: float) -> bool:
        """«Мои агенты» места: present — агент name в состоянии ready; иначе — его нет в списке."""
        chat = self.chats[place]

        def seen() -> bool:
            found = [a for a in self._gw(place, chat.agents) if a.get("name") == name and a.get("kind") != "builtin"]
            return any(a.get("state") == "ready" for a in found) if present else not found
        return self._until(seen, AGENT_WAIT_S, deadline)

    def _keepalive(self) -> None:
        """Места, ждущие на барьере, — POST …/wake раз в KEEPALIVE_S по clock (продлевает простой места); после жёсткой
        остановки тормозом — нет."""
        if self._halt.is_set():
            return
        due = []
        with self._lock:
            now = self.clock()
            for place, last in self._waiting.items():
                if now - last >= KEEPALIVE_S:
                    self._waiting[place] = now
                    due.append(place)
        for place in due:
            try:
                self.chats[place].wake(timeout=WAKE_TIMEOUT_S)
                self._resleep(place)
                log.info("место %02d ждёт на барьере — пробуждение", place)
            except Exception as exc:
                log.warning("место %02d: пробуждение на барьере не удалось (%s)", place, type(exc).__name__)

    def _abort_live(self) -> None:
        """abort идущих сессий; одной сессии — не чаще ABORT_REPEAT_S (р1б, п. 4): иначе неснятый ход дал бы ~20
        запросов в секунду на cookie, а limit_req edge (pcbk_oc_rate, 20 r/s) ответил бы 503 busy."""
        now = self.clock()
        with self._lock:
            live = {p: s for p, s in self._live.items()
                    if now - self._aborted_at.get(s, -math.inf) >= ABORT_REPEAT_S}
            for session in live.values():
                self._aborted_at[session] = now
        for place, session in live.items():
            try:
                self.chats[place].abort(session)
            except Exception as exc:
                log.warning("место %02d: abort не прошёл (%s)", place, type(exc).__name__)

    # --- журналы ---

    def _journal(self, session: str, place: int, kind: str) -> None:
        with self._io:
            with open(self.out / "sessions.log", "a", encoding="utf-8") as f:
                f.write(f"{session}\t{place:02d}\t{kind}\n")
                f.flush()
                os.fsync(f.fileno())
            if kind == "end":
                self._ended.add(session)

    def _end(self, session: str, place: int) -> None:
        if session not in self._ended:
            self._journal(session, place, "end")

    def _append_record(self, rec: RunRecord) -> None:
        line = json.dumps(rec.to_json(), ensure_ascii=False)
        with self._io:
            with open(self.out / "runs.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())

    # --- остановки ---

    def _set_stop(self, reason: str, hard: bool = False) -> None:
        with self._lock:
            if self._stop_reason is None:
                self._stop_reason = reason
                log.warning("остановка: %s", reason)
        if hard:
            self._hard.set()

    def _on_signal(self, signum: int, frame: Any) -> None:
        self._interrupt.set()
        self._set_stop("interrupt", hard=True)

    def _aborting(self) -> bool:
        """Идущие сессии прерываются: сигнал или жёсткая остановка тормозом."""
        return self._interrupt.is_set() or self._halt.is_set()

    def _on_guard(self, verdict: str) -> None:
        """Вердикт сторожа памяти: memory, data_drill, blind, brake — жёстко; soft — мягко memory_warn."""
        if verdict in HARD_VERDICTS:
            self._halt_stop(verdict)
        elif verdict == "soft":
            self._set_stop("memory_warn")

    def _halt_stop(self, reason: str) -> None:
        """Жёсткая остановка тормозом (R119): причина (поверх мягкой; первая жёсткая остаётся), сон всех мест конфига —
        один раз и первым (close() повторит): abort идёт через шлюз и при больном core тянется минутами (доводка р1,
        I-2), а docker stop от шлюза не зависит; затем abort идущих сессий (его повторяет и барьер)."""
        with self._lock:
            first = not self._halt.is_set()
            if self._stop_reason is None or not self._hard.is_set():
                if self._stop_reason != reason:
                    log.warning("остановка: %s (тормоз, жёстко)", reason)
                self._stop_reason = reason
            self._halt.set()
            self._hard.set()
        if first:
            self._sleep_places()
        self._abort_live()

    def _sleep_places(self) -> None:
        places = sorted(self.cfg.places)
        try:
            self.stand.sleep_places(places)
            log.warning("тормоз: места прогона усыплены (%d)", len(places))
        except Exception as exc:
            log.error("тормоз: места не усыплены (%s) — их усыпит core", type(exc).__name__)

    def _stand_ok(self) -> bool:
        """Строки стенда: «LLM-прокси и бюджет» не fail и «Память сервера» ok; в режиме нагрузки — только строка LLM из
        последнего опроса сторожа памяти (решения о памяти — у него, R119)."""
        if self._guard is not None:
            st = self._guard.last_status
            if st is None:
                log.warning("строки стенда: сторож памяти ещё не прочитал страницу")
                return False
        else:
            try:
                st = self.stand.status()
            except Exception as exc:
                log.warning("строки стенда не прочитаны (%s)", type(exc).__name__)
                return False
        rows = {c.get("component"): c.get("state") for c in st.get("checks") or [] if isinstance(c, dict)}
        ok = rows.get("llm") != "fail" and (self.cfg.load or rows.get("memory") == "ok")
        if not ok:
            log.warning("строки стенда: llm=%s, memory=%s", rows.get("llm"), rows.get("memory"))
        return ok

    def _stand_gate(self) -> bool:
        """Строки стенда перед повтором: STAND_STRIKES проверок подряд с паузой STAND_CHECK_S — иначе stand."""
        for i in range(STAND_STRIKES):
            if self._stand_ok():
                return True
            if i < STAND_STRIKES - 1:
                self.sleep(STAND_CHECK_S)
        self._set_stop("stand")
        return False

    def _cap_reached(self) -> bool:
        """run.spent_usd + stop_margin_usd ≥ потолка (меньший из конфига и прокси) — cap; блока run нет или он чужой —
        stand: потолок прокси не держит этот прогон. /health/llm не читается HEALTH_TRIES раз — stand."""
        health = None
        for attempt in range(HEALTH_TRIES):
            try:
                health = self.stand.health_llm()
                break
            except Exception as exc:
                log.warning("/health/llm не прочитан (%s)", type(exc).__name__)
                if attempt < HEALTH_TRIES - 1:
                    self.sleep(HEALTH_PAUSE_S)
        run = health.get("run") if isinstance(health, dict) else None
        if health is None:
            self._set_stop("stand")
            return True
        if not isinstance(run, dict) or run.get("id") != self.cfg.run_id:
            log.warning("в /health/llm нет блока этого прогона — потолок прокси его не держит")
            self._set_stop("stand", hard=True)           # ни новых прогонов, ни реплик (р1, I2)
            return True
        spent = float(run.get("spent_usd") or 0.0)
        cap = min(self.cfg.cap_usd, float(run.get("cap_usd") or self.cfg.cap_usd))
        with self._lock:
            self._run_spent = spent
        if spent + self.cfg.stop_margin_usd >= cap:
            self._set_stop("cap")
            return True
        return False

    def _run_guard(self) -> None:
        """Перед каждой репликой: блок run этого прогона есть в /health/llm, иначе жёсткая остановка stand (р1, I2).
        /health/llm не прочитан — реплика идёт: потолок держит прокси, а молчание стенда ловят проверки повтора."""
        try:
            health = self.stand.health_llm()
        except Exception as exc:
            log.warning("/health/llm не прочитан перед репликой (%s)", type(exc).__name__)
            return
        run = health.get("run") if isinstance(health, dict) else None
        if not isinstance(run, dict) or run.get("id") != self.cfg.run_id:
            log.warning("в /health/llm нет блока этого прогона — реплики остановлены")
            self._set_stop("stand", hard=True)
            raise _RunStatus("interrupted")

    # --- прогон ---

    def _blank(self, key: RunKey) -> RunRecord:
        return RunRecord(key=key, model=self.cfg.places[key.place], status="done", turns=[], sessions=[], saved=[],
                         agent_file=None, visible_delay_s=None, bridge=None, calc={}, llm_rows=[], notes=[],
                         saw_draft=False)

    def run_one(self, key: RunKey) -> RunRecord:
        """Один прогон с пробуждением места; запись — в OUT/runs.jsonl."""
        try:
            self.ensure_ready(key.place)
        except Exception as exc:
            return self._not_ready(key, exc)
        return self._run(key, self.now())

    def _not_ready(self, key: RunKey, exc: Exception) -> RunRecord:
        log.warning("место %02d не готово к прогону (%s)", key.place, type(exc).__name__)
        rec = self._blank(key)
        rec.status = "stand_error"
        rec.notes.append(f"место не готово: {type(exc).__name__}")
        self._finish(rec)
        return rec

    def _run(self, key: RunKey, wall: datetime) -> RunRecord:
        sc = BY_ID[key.scenario]
        card = self.cards.cards[sc.id]
        place = key.place
        start = self.clock()
        deadline = start + self.cfg.run_timeout_s[sc.group]
        talk_deadline = deadline - (self.cfg.archive_reserve_s if sc.group == "create" else 0.0)
        with self._lock:
            self.started.append(key)
            self._started_mono.append(start)
            self._started_wall[key] = wall
            self._place[place]["current"] = f"{key.scenario}/{key.rep}"
        log.info("прогон начат: место %02d, %s, повтор %d", place, key.scenario, key.rep)
        rec = self._blank(key)
        student = ScriptedStudent(card, sc)
        try:
            self._talk(rec, place, AGENTS[sc.agent], student.opening(),
                       lambda turn: student.reply("\n".join(turn.assistant)), talk_deadline, bridge=False)
            if sc.group == "create":
                self._after_save(rec, place, card.bridge, talk_deadline)
        except _RunStatus as st:
            rec.status = st.status
        except Exception as exc:
            rec.status = "interrupted" if self._halt.is_set() else "stand_error"     # после тормоза — не сбой стенда
            rec.notes.append(f"сбой: {type(exc).__name__}")
            log.warning("место %02d, %s: сбой (%s)", place, key.scenario, type(exc).__name__)
        rec.saw_draft = student.saw_draft
        if sc.group == "create":
            if not rec.saved:                        # разговор кончился статусом, а сохранение в нём было
                rec.saved = [dict(c.input) for t in rec.turns for c in t.tools if _save_ok(c)]
            self._archive_place(rec, place, deadline)
        if rec.status == "done":
            self._calc_all(rec, deadline)
        self._llm_rows(rec)
        self._finish(rec)
        return rec

    def _talk(self, rec: RunRecord, place: int, agent: str, first: str,
              reply: Callable[[Turn], str | None], deadline: float, *, bridge: bool) -> None:
        """Разговор в новой сессии: реплика → ход → следующая реплика (reply), пока она есть. У A разговор кончается
        удачным save_agent. Срок — abort и _RunStatus("timeout")."""
        chat = self.chats[place]
        if self._hard.is_set():                      # после cap по 402, money и interrupt — ни одного нового разговора
            raise _RunStatus("interrupted")
        session = self._gw(place, chat.new_session)
        rec.sessions.append(session)
        self._journal(session, place, "start")
        with self._lock:
            self._live[place] = session
        try:
            msg: str | None = first
            since = 0
            while msg is not None:
                self._run_guard()                         # сначала /health/llm: пока он читается, могли прийти
                if self._hard.is_set():                   # чужая жёсткая остановка или срок (р1б, п. 1)
                    raise _RunStatus("interrupted")
                if self.clock() >= deadline:              # срок вышел между ходами — реплики нет (р1, m1)
                    raise _RunStatus("timeout")
                self._gw(place, chat.send, session, msg, agent)
                sent = self.now()
                if self._aborting():                      # сигнал или тормоз пришли, пока реплика уходила (р1, m2)
                    self._abort(place, session)
                    raise _RunStatus("interrupted")
                messages = self._wait_turn(place, session, deadline)
                with self._lock:
                    self._turned.add(session)
                turn = parse_turn(messages, since, msg)
                since = len(messages)
                if not turn.started:
                    turn.started = sent.timestamp()
                if not turn.ended:
                    turn.ended = self.now().timestamp()
                if bridge:
                    rec.bridge = turn
                else:
                    rec.turns.append(turn)
                if self._aborting():
                    raise _RunStatus("interrupted")
                code = turn_code(turn.error)
                if code == "capped":
                    self._set_stop("cap", hard=True)
                    raise _RunStatus("capped")
                if code == "halted":                      # тормоз core защёлкнут — жёстко memory (R119)
                    self._halt_stop("memory")
                    raise _RunStatus("interrupted")
                if code == "money":
                    self._set_stop("money", hard=True)
                    raise _RunStatus("stand_error")
                if code == "stand_error":
                    raise _RunStatus("stand_error")
                if bridge or any(_save_ok(c) for c in turn.tools):
                    break
                msg = reply(turn)
        except _RunStatus:
            raise          # срок — abort уже был; отказ прокси — ход кончен; interrupt — abort дал главный поток
        except Exception:
            self._abort(place, session)              # сбой посреди хода: место — один разговор за раз
            raise
        finally:
            with self._lock:
                self._live.pop(place, None)

    def _wait_turn(self, place: int, session: str, deadline: float) -> list[dict]:
        """wait_idle на min(turn_timeout_s, срок − сейчас); 503 — ensure_ready и один повтор; срок — abort."""
        chat = self.chats[place]
        for attempt in (1, 2):
            timeout = min(self.cfg.turn_timeout_s, deadline - self.clock())
            if timeout <= 0:
                break
            try:
                messages = chat.wait_idle(session, timeout)
            except TimeoutError:
                break
            except GatewayError as exc:
                if exc.status != 503 or attempt == 2:
                    raise
                log.info("место %02d: 503 %s при ожидании хода — пробуждение", place, exc.name)
                self.ensure_ready(place)
                continue
            self._turn_done()
            return messages
        log.info("место %02d: срок хода или прогона — abort", place)
        self._abort(place, session)
        self._turn_done()
        raise _RunStatus("timeout")

    def _abort(self, place: int, session: str) -> None:
        """abort через своё место; 404 — разговора уже нет; затем ожидание ухода сессии из /session/status."""
        chat = self.chats[place]
        try:
            chat.abort(session)
        except GatewayError as exc:
            if exc.status != 404:
                log.warning("место %02d: abort — %d %s", place, exc.status, exc.name)
        except Exception as exc:
            log.warning("место %02d: abort не прошёл (%s)", place, type(exc).__name__)

        def gone() -> bool:
            try:
                return session not in chat.session_status()
            except Exception:
                return True
        self._until(gone, ABORT_WAIT_S)
        self._end(session, place)

    def _turn_done(self) -> None:
        with self._lock:
            self._last_turn_at = self.now()
        self._keepalive()

    def _after_save(self, rec: RunRecord, place: int, bridge_text: str, deadline: float) -> None:
        """A: удачные save_agent → rec.saved; ожидание ready нового агента, строка учёта (flags, visible_at), файл
        агента и мост к нему в новой сессии."""
        oks = [(i, c) for i, t in enumerate(rec.turns) for c in t.tools if _save_ok(c)]
        rec.saved = [dict(c.input) for _, c in oks]
        if not oks:
            return
        name = rec.saved[0].get("name")
        if not isinstance(name, str) or not name:
            rec.notes.append("сохранённый агент без имени")
            return
        if not self._wait_agent(place, name, present=True, deadline=deadline):
            rec.notes.append("агент не стал готов в «Мои агенты» за срок")
            return
        rows = {r.get("name"): r for r in self.stand.agent_rows(place)}
        for saved in rec.saved:
            row = rows.get(saved.get("name"))
            if row is not None:
                saved["flags"] = _flags(row.get("flags"))
                saved["visible_at"] = row.get("visible_at")
        visible = _parse_ts(rows.get(name, {}).get("visible_at"))
        ended = rec.turns[oks[0][0]].ended
        if visible is not None and ended:
            rec.visible_delay_s = round(visible.timestamp() - ended, 1)
        try:
            rec.agent_file = self.stand.agent_file(place, name)
        except Exception as exc:
            rec.notes.append(f"файл агента не прочитан: {type(exc).__name__}")
        if not bridge_text:
            rec.notes.append("у карточки нет вопроса моста")
            return
        self._talk(rec, place, name, bridge_text, lambda turn: None, deadline, bridge=True)

    def _archive_place(self, rec: RunRecord, place: int, deadline: float) -> None:
        """После A — все агенты места, кроме bench-uzel, в архив и ожидание их ухода (внутри срока прогона). В режиме
        нагрузки — только сохранённые этим прогоном (rec.saved): чужой агент остаётся уликой для isolation.json (R121,
        доводка р1, I-1); сохранённый уже после abort тоже остаётся и считается чужим — ошибка в безопасную сторону."""
        try:
            own = {s.get("name") for s in rec.saved} if self.cfg.load else None
            names = [r["name"] for r in self.stand.agent_rows(place)
                     if isinstance(r.get("name"), str) and r["name"] != BENCH_AGENT
                     and (own is None or r["name"] in own)]
            for name in names:
                self.stand.archive_agent(place, name)
            for name in names:
                if not self._wait_agent(place, name, present=False, deadline=deadline):
                    rec.notes.append("архив: агент ещё виден в «Мои агенты»")
                    break
        except Exception as exc:
            rec.notes.append(f"архив: сбой {type(exc).__name__}")
            log.warning("место %02d: архив агентов не прошёл (%s)", place, type(exc).__name__)

    def _calc(self, tool: str, args: dict, deadline: float | None = None) -> dict:
        """Калькулятор: rate — пауза и повтор на всё окно частоты службы (calc_patient), не позже deadline; после
        жёсткой остановки или сигнала пауз больше нет (итоговая доводка Д8б)."""
        return calc_patient(self.stand, tool, args, sleep=self.sleep, clock=self.clock, deadline=deadline,
                            stop=lambda: self._hard.is_set() or self._interrupt.is_set())

    def _calc_all(self, rec: RunRecord, deadline: float) -> None:
        """Сверка калькулятором по аргументам последнего вызова tag_now и tag_period разговора (и моста)."""
        turns = rec.turns + ([rec.bridge] if rec.bridge else [])
        for tool in ("tag_now", "tag_period"):
            calls = [c for t in turns for c in t.tools
                     if _short(c.tool) == tool and c.status == "completed" and c.input]
            if not calls:
                continue
            try:
                rec.calc[tool] = self._calc(tool, dict(calls[-1].input), deadline)
            except Exception as exc:
                rec.notes.append(f"{NOTE_CALC_FAIL} {type(exc).__name__}")
                continue
            if rec.calc[tool].get("status") == "rate":       # окно частоты не освободилось до срока: не молча (Б3)
                rec.notes.append(NOTE_CALC_RATE)
                log.warning("место %02d: сверка калькулятором не сделана — rate", rec.key.place)

    def _llm_rows(self, rec: RunRecord) -> None:
        """Строки журнала прокси сессий прогона (Д8-R71): у КАЖДОЙ сессии с законченным ходом (и у моста) есть строки
        (р1, m6), model строк — модель места; иначе — stand_error у прогона done или timeout."""
        if not rec.sessions:
            return
        with self._lock:
            need = [s for s in rec.sessions if s in self._turned]
        checked = rec.status in ("done", "timeout")

        def missing(rows: list[dict]) -> list[str]:
            have = {r.get("session") for r in rows}
            return [s for s in need if s not in have]
        try:
            rows = self.stand.llm_rows(rec.sessions)
            if checked and missing(rows):
                self.sleep(ROWS_RETRY_S)                    # журнал пишет строки с задержкой (flush)
                rows = self.stand.llm_rows(rec.sessions)
        except Exception as exc:
            rec.notes.append(f"журнал прокси не прочитан: {type(exc).__name__}")
            if checked:
                rec.status = "stand_error"
            return
        rec.llm_rows = rows
        expected = rec.model.split("/", 1)[1]
        if any(r.get("model") != expected for r in rows):
            rec.notes.append("модель в журнале прокси — не модель места")
            if checked:
                rec.status = "stand_error"
        elif missing(rows):
            rec.notes.append("у сессии с законченным ходом нет строк журнала прокси")
            if checked:
                rec.status = "stand_error"

    def _finish(self, rec: RunRecord) -> None:
        for session in rec.sessions:
            self._end(session, rec.key.place)
        self._append_record(rec)
        with self._lock:
            self._finished_mono[rec.key] = self.clock()
            p = self._place[rec.key.place]
            p["current"] = None
            if rec.status == "done" and rec.key not in self._done_keys:
                self._done_keys.add(rec.key)
                if rec.key in self._plan_set:
                    p["done"] += 1
            p["stand_errors"] = p["stand_errors"] + 1 if rec.status == "stand_error" else 0
            if p["stand_errors"] >= PLACE_STRIKES and p["status"] != "stopped":
                p["status"] = "stopped"
                log.warning("место %02d остановлено: %d stand_error подряд", rec.key.place, PLACE_STRIKES)
        log.info("прогон кончен: место %02d, %s, повтор %d — %s, ходов %d", rec.key.place, rec.key.scenario,
                 rec.key.rep, rec.status, len(rec.turns))

    # --- места, повторы, план ---

    def _place_loop(self, place: int, keys: list[RunKey]) -> None:
        for key in keys:
            if self._stop_reason is not None or self._hard.is_set():
                break
            with self._lock:
                if self._place[place]["status"] == "stopped":
                    break
            try:
                self.ensure_ready(place)
            except Exception as exc:
                if self._halt.is_set():                   # тормоз: место не будится, прогона нет — не сбой места
                    break
                self._not_ready(key, exc)
                continue
            if self._cap_reached():
                break
            wall = self.now()                             # после /health/llm: он мог идти долго (р1, m3)
            span = timedelta(seconds=self.cfg.run_timeout_s[BY_ID[key.scenario].group])
            if wall + span > self.window_end():
                with self._lock:
                    self._window_hit = True
                continue
            self._run(key, wall)
        with self._lock:
            if self._place[place]["status"] != "stopped":
                self._place[place]["status"] = "waiting"
                self._waiting[place] = self.clock()

    def _place_thread(self, place: int, keys: list[RunKey]) -> None:
        try:
            self._place_loop(place, keys)
        except BaseException as exc:                 # главному потоку: он поднимет его после барьера
            with self._lock:
                self._thread_errors.append(exc)
            if isinstance(exc, Exception):           # сбой кода — прочие места новых реплик не шлют
                self._hard.set()

    def _run_rep(self, rep: int, keys: list[RunKey]) -> None:
        by_place: dict[int, list[RunKey]] = defaultdict(list)
        for key in keys:
            by_place[key.place].append(key)
        threads = []
        with self._lock:
            self._rep = rep
            self._waiting.clear()
            for n in self.cfg.places:
                if self._place[n]["status"] == "stopped":
                    continue
                if n in by_place:
                    self._place[n]["status"] = "running"
                else:
                    self._place[n]["status"] = "waiting"
                    self._waiting[n] = self.clock()
        log.info("повтор %d: прогонов %d", rep, len(keys))
        for n, pkeys in sorted(by_place.items()):
            if self._place[n]["status"] == "stopped":
                continue
            t = threading.Thread(target=self._place_thread, args=(n, pkeys), name=f"место-{n:02d}", daemon=True)
            threads.append(t)
            t.start()
        strikes = 0
        next_check = self.clock() + STAND_CHECK_S
        while True:
            alive = [t for t in threads if t.is_alive()]
            if not alive:
                break
            alive[0].join(BARRIER_TICK_S)
            guard = self._guard
            if guard is not None and not self._halt.is_set() and not guard.alive(self.clock()):
                log.error("сторож памяти молчит или умер — прогонщик слеп")
                self._halt_stop("blind")
            if self._aborting():                          # каждый такт: реплика могла уйти после прошлого abort
                self._abort_live()
            self._keepalive()
            if self.clock() >= next_check:
                next_check = self.clock() + STAND_CHECK_S
                strikes = 0 if self._stand_ok() else strikes + 1
                if strikes >= STAND_STRIKES:
                    self._set_stop("stand")
        with self._lock:
            errors = list(self._thread_errors)
        if errors:
            raise errors[0]

    def _run_plan(self) -> str:
        todo: dict[int, list[RunKey]] = defaultdict(list)
        for key in self.plan:
            if key not in self._done_keys:
                todo[key.rep].append(key)
        projected = False
        for rep in sorted({k.rep for k in self.plan}):
            if self._stop_reason is not None:
                break
            keys = todo.get(rep, [])
            if not keys:
                continue
            if rep >= 2 and not projected:             # барьер 1: прогноз расхода
                projected = True
                value = self.projection()
                with self._lock:
                    self._projected = value
                limit = self.cfg.cap_usd - self.cfg.stop_margin_usd
                log.info("прогноз расхода: $%.2f при пределе $%.2f", value, limit)
                if value > limit and not self.cfg.projection_ack:
                    self._set_stop("projection")
                    break
            if not self._stand_gate() or self._cap_reached():
                break
            self._run_rep(rep, keys)
            if self._stop_reason is not None or self._window_hit:
                break
        if self.cfg.load:
            if self._guard is not None and not self._halt.is_set():
                self._guard.check()                       # «закончен» — только при чистом последнем опросе сторожа
            try:
                self._isolation()
            except Exception as exc:                      # нет isolation.json — load-check скажет «не проверено»
                log.warning("только своё: не записано (%s)", type(exc).__name__)
        reason = self._stop_reason
        if reason is None:
            undone = {k.place for k in self.plan if k not in self._done_keys}
            stopped = [n for n in self.cfg.places if self._place[n]["status"] == "stopped" and n in undone]
            if stopped:                                   # у остановленного места остались прогоны (р1, I1)
                log.warning("места остановлены: %s", ", ".join(f"{n:02d}" for n in stopped))
                reason = "place"
            else:
                reason = "window" if self._window_hit else "finished"
        with self._lock:
            if self._stop_reason is None and reason != "finished":
                self._stop_reason = reason
            self._state = {"finished": "finished", "projection": "paused"}.get(reason, "stopped")
        log.info("прогон окончен: %s", reason)
        return reason

    def _isolation(self) -> None:
        """«Только своё» в конце плана (R121): у каждого подготовленного места чужие разговоры (не из sessions.log и не
        бывшие до прогона) и чужие агенты (не встроенные, не bench-uzel, не сохранённые этим местом) —
        OUT/isolation.json, только числа; после жёсткой остановки тормозом — пропуск с заметкой."""
        path = self.out / "isolation.json"
        if self._halt.is_set():
            write_json(path, {"skipped": ISO_SKIPPED})
            log.warning("только своё: %s", ISO_SKIPPED)
            return
        own: dict[int, set[str]] = defaultdict(set)
        log_path = self.out / "sessions.log"
        for line in log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []:
            parts = line.split("\t")
            if len(parts) == 3 and parts[1].isdecimal() and parts[2] == "start":
                own[int(parts[1])].add(parts[0])
        saved: dict[int, set[str]] = defaultdict(set)
        for rec in load_records(self.out):
            saved[rec.key.place].update(s["name"] for s in rec.saved if isinstance(s.get("name"), str))
        result: dict[str, dict] = {}
        for n in self.cfg.places:
            if n not in self._pre:                        # место не подготовлено — до него прогон не дошёл
                continue
            try:
                listed = [s["id"] for s in self.chats[n].sessions() if isinstance(s.get("id"), str)]
                agents = self.chats[n].agents()
            except Exception as exc:
                log.warning("только своё: место %02d не прочитано (%s)", n, type(exc).__name__)
                result[str(n)] = {"listed": None, "foreign_sessions": None, "foreign_agents": None}
                continue
            foreign = own_only(listed, own[n], self._pre[n])
            strangers = [a for a in agents if a.get("kind") != "builtin" and a.get("name") != BENCH_AGENT
                         and a.get("name") not in saved[n]]
            result[str(n)] = {"listed": len(listed), "foreign_sessions": len(foreign),
                              "foreign_agents": len(strangers)}
            if foreign or strangers:
                log.warning("только своё: на месте %02d чужих разговоров %d, агентов %d", n, len(foreign),
                            len(strangers))
        write_json(path, result)

    def _load_summary(self) -> dict:
        """OUT/load.json (R119): места, начало, конец, причина, база и минимум свободной памяти, swap и swap Dify от
        базы, наибольшее давление (время остановки и сна — не здесь, R133)."""
        with self._lock:
            reason = self._stop_reason or ("finished" if self._state == "finished" else self._state)
        guard = self._guard
        return {"places": sorted(self.cfg.places), "started_at": _iso(self._run_started_wall),
                "ended_at": _iso(self.now()), "reason": reason, **(guard.summary() if guard is not None else {})}

    def _install_signals(self) -> dict:
        if threading.current_thread() is not threading.main_thread():
            return {}
        return {sig: signal.signal(sig, self._on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}

    def run(self) -> str:
        """prepare() (если не звали), повторы с барьером; причина из STOP_REASONS или "finished". В конце — снимок
        /health/llm в OUT/health_end.json, его run.spent_usd (блок этого прогона) — в run_spent_usd последнего
        progress.json: после жёсткой остановки cap последнее чтение до прогонов устарело; пульс и замок снимаются."""
        previous = self._install_signals()
        try:
            self.prepare()
            return self._run_plan()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            if self._lock_fd is not None:
                try:
                    health = self.stand.health_llm()
                    _atomic_write(self.out / "health_end.json", json.dumps(health, ensure_ascii=False, indent=1))
                    run = health.get("run") if isinstance(health, dict) else None
                    spent = run.get("spent_usd") if isinstance(run, dict) and run.get("id") == self.cfg.run_id else None
                    if isinstance(spent, (int, float)) and not isinstance(spent, bool):
                        with self._lock:
                            self._run_spent = spent
                except Exception as exc:
                    log.warning("снимок /health/llm не записан (%s)", type(exc).__name__)
                if self.cfg.load:
                    try:
                        write_json(self.out / "load.json", self._load_summary())
                    except Exception as exc:
                        log.warning("load.json не записан (%s)", type(exc).__name__)
            self.close()


__all__ = ["NOTE_CALC_FAIL", "NOTE_CALC_RATE", "PlaceNotReady", "RunRefused", "Runner", "STOP_REASONS", "card_tags",
           "load_records", "load_tag_units", "plan_runs", "status_main", "turn_code", "write_progress"]
