"""Режим нагрузки прогонщика (Д11-R119…R122, R131): сторож памяти и вердикт по числам строки «Память сервера», ворота
лестницы (load-gate), числа load-check, цена реплики, «только своё».

Сторож памяти MemoryGuard (R119) живёт с prepare() до close(): раз в LOAD_MEM_POLL_S — stand.status() и
stand.health_llm(), каждый DIFY_EVERY_POLLS-й опрос (раз в 15 с) — stand.dify_swap_mib(); точка — строкой в
OUT/memory.jsonl (ровно MEM_KEYS). Опрос и вердикт — check(): его зовут поток сторожа и prepare() перед каждым
пробуждением места. Сбой кода сторожа — blind: мёртвый сторож не прячется. Метка последнего опроса (at_mono, at_wall) —
живость: главный поток прогонщика сверяет её на каждом шаге барьера.

Вердикт guard_verdict — по числам ответа сторожа (detail и note), а не по тому, какой detail показала строка (I-1 р3):
в полосе «свободно меньше 2048 МиБ» давление, убийства и swap всё равно останавливают жёстко.
- memory (жёстко): строка fail или unknown (кроме «датчик памяти замолчал…» — это blind); warn с detail swap, убийств
  вне мест или давления (сторож уже выдержал своё удержание) и warn с незнакомым detail; свободно < mem_fail_mib;
  убийств по памяти вне мест и core за час > 0; swap за 10 мин ≥ swap_grow_mib; swap от базы окна ≥ swap_grow_mib
  (B-1 р2); давление full > psi_full_max в PSI_HOLD_POLLS разных снимках сторожа подряд (снимок — checked_at: один
  снимок, прочитанный трижды, — не три); swap Dify от базы ≥ dify_swap_max_mib; тормоз core защёлкнут
  (run_brake.state == "halted");
- blind (жёстко): нового свежего снимка нет дольше LOAD_BLIND_S; строка unknown «датчик памяти замолчал…» (доводка
  А1) или пометка «нет данных: swap|vmstat|давление» в note — датчик жёсткого условия без данных: это слепота, а не
  «0 убийств» и не «давление в норме» (решение контроллера 03.10, итоговое ревью Д11а I-2); swap Dify не читается
  DIFY_FAIL_LIMIT раз подряд; сторож умер;
- data_drill (жёстко, только «без данных» — load_data none): в строке историана страницы нет пометки «учения:
  tool_error» (core пересоздан без DRILL_DATA посреди ступени; доводка р1, m-4);
- brake (жёстко): в /health/llm нет run_brake или его age_s > BRAKE_SILENT_S (/health/llm не прочитан — возраст
  считается от последнего прочитанного);
- soft (мягко — memory_warn): warn по порогу и ни одного жёсткого числа.
Части убийств в note нет при K = 0 (договор R114) — это 0, пока нет пометки «нет данных: vmstat». Базы окна —
swap_base_mib и dify_swap_base_mib конфига; null (конфиг стенда) — первое прочитанное значение. Тексты detail и пометок
— значением, тест сверяет их с watchdog/pcbk_watchdog/memory.py.

load-gate (R122): база B — первая точка memory.jsonl, минимум M, мест k (OUT/load.json); прирост на место P = (B − M) /
k, прогноз на K мест B − K × P; «можно» — прогноз не ниже warn_mib + GATE_MARGIN_MIB. На старте ступени (первая точка)
нет данных датчика жёсткого условия (поле точки — null: свободная память, swap, давление, vmstat, swap Dify) — «нельзя»
без прогноза (start_gaps; решение контроллера 03.10). Ступень кончилась не «закончен» (причина в OUT/load.json —
остановка тормозом, мягкая, окно, недоделанная) — «нельзя» без прогноза: прогноз по оборванной ступени занижает прирост,
а давление после сна мест спадает (доводка р1, I-4; причины нет — load.json не прогонщика, судят числа).

load-check (R120): LOAD_CHECK_PY изнутри pcbk-core (sqlite mode=ro, белый список /app/data/whitelist.txt) — только
числа; check_verdict судит их, снимки /health/llm начала и конца и OUT/isolation.json; цена реплики price_per_reply —
по OUT/runs.jsonl (ходы — реплики студентов, строки журнала прокси — вызовы и расход), write_price пишет OUT/price.json.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import WINDOW_BASE_KEYS

log = logging.getLogger("pcbk_bench.load")

LOAD_MEM_POLL_S = 5.0
LOAD_BLIND_S = 30.0
PSI_HOLD_POLLS = 3
DIFY_POLL_S = 15.0
DIFY_EVERY_POLLS = round(DIFY_POLL_S / LOAD_MEM_POLL_S)        # swap Dify — каждый третий опрос (раз в 15 с)
DIFY_FAIL_LIMIT = 3
BRAKE_SILENT_S = 30.0                                           # как RUN_BRAKE_SILENT строки LLM сторожа (R118)
PAUSE_STEP_S = 0.25                                             # пауза сторожа шагами: close() не ждёт весь опрос
POINTS_KEPT = 64
GUARD_JOIN_S = 5.0
# пороги по умолчанию (R119; в конфигах стенда поля базы окна — по желанию)
SWAP_GROW_MIB = 512
DIFY_SWAP_MAX_MIB = 256
MEM_FAIL_MIB = 1024
PSI_FULL_MAX = 10.0
MEM_WARN_MIB = 2048
MAX_PLACES = 10
# ворота службы данных: core/pcbk_core/data/gate.py GLOBAL_LIMIT за RATE_WINDOW_S — значением, тест сверяет
GATE_LIMIT_5M = 300
GATE_WINDOW_S = 300.0
GATE_MARGIN_MIB = 512

# тексты строки «Память сервера» (watchdog/pcbk_watchdog/memory.py) — значением: стенд только на stdlib
MEM_NO_DATA_DETAIL = "нет данных о свободной памяти"
MEM_OOM_OUTSIDE_DETAIL = "сервер убивал процессы по памяти вне мест и core"
MEM_PSI_DETAIL = "сервер ждёт освобождения памяти — давление выше порога"
MEM_SWAP_DETAIL = "сервер вытесняет память в swap"
MEM_SENSOR_SILENT_DETAIL = "датчик памяти замолчал — убийства, давление или swap не читаются"
SENSOR_NOTES = {"swap": "нет данных: swap", "vmstat": "нет данных: vmstat", "pressure": "нет данных: давление"}
# пометка учения службы данных в строке историана (watchdog/pcbk_watchdog/checks.py DRILL_NOTE) — значением
DRILL_DATA_NOTE = "учения: tool_error"
CONSENT_FILE = "real-data-consent.txt"
ISO_SKIPPED = "не проверено: места усыплены тормозом"

# части note (R114): «свободно N МиБ», «swap занят M МиБ (+G МиБ за 10 мин)», «давление памяти some S %, full F %»,
# «убийств по памяти вне мест и core за час: K»
MEM_NOTE_RE = re.compile(r"свободно (\d+) МиБ")
SWAP_NOTE_RE = re.compile(r"swap занят (\d+) МиБ \(\+(\d+) МиБ за \d+ мин\)")
_PCT = r"(\d+(?:\.\d+)?(?:e[+-]?\d+)?)"
PSI_NOTE_RE = re.compile(r"давление памяти (?:some \S+ %, )?full " + _PCT + " %")
OOM_NOTE_RE = re.compile(r"убийств по памяти вне мест и core за час: (\d+)")
THRESHOLD_RE = re.compile(r"свободно меньше \d+ МиБ")

MEM_KEYS = ("t", "checked_at", "state", "kind", "free_mib", "swap_used_mib", "swap_grow10_mib", "psi_full",
            "oom_outside", "dify_swap_mib", "brake", "brake_age_s")
HARD_VERDICTS = ("memory", "data_drill", "blind", "brake")
VERDICTS = ("ok", "soft", *HARD_VERDICTS)
BRAKE_STATES = ("watching", "halted")
_KINDS = {MEM_SWAP_DETAIL: "swap", MEM_OOM_OUTSIDE_DETAIL: "oom_outside", MEM_PSI_DETAIL: "psi",
          MEM_NO_DATA_DETAIL: "no_data", MEM_SENSOR_SILENT_DETAIL: "silent"}

# тексты load-check: какая проверка не прошла (без значений)
CHECK_SENT_UNLISTED = "имя вне белого списка дошло до SQL"
CHECK_GATE = "запросов к историану за 5 минут больше предела ворот"
CHECK_NUMBERS = "нет чисел журнала службы"
CHECK_RUN_CAP = "расход прогона выше потолка и перерасхода"
CHECK_NO_RUN = "в снимке /health/llm конца нет блока прогона"
CHECK_SHARE = "нагрузка исчерпала долю места"
CHECK_TOTAL = "общий расход дошёл до бюджета"
CHECK_HEALTH = "в снимках /health/llm нет чисел расхода"
CHECK_FOREIGN = "на месте чужие разговоры или агенты"
CHECK_ISO = "«только своё» не проверено"
NO_DATA_LINE = "успех 4: не проверяется (без данных)"


# --- точка сторожа ---

@dataclass(frozen=True)
class MemPoint:
    """Один опрос сторожа памяти: t — часы прогонщика; checked_at — снимок сторожа (None — страницы нет или в ней нет
    времени); state, kind — строка «Память сервера» (None — страницы или строки нет); числа note; swap Dify, МиБ;
    run_brake core. Служебные: fresh — страница прочитана, не stale и строка памяти в ней есть; no_data — датчики с
    пометкой «нет данных: …»; health_ok — /health/llm прочитан; dify_fails — неудачных чтений swap Dify подряд;
    drill — в строке историана пометка «учения: tool_error» (None — страницы нет)."""
    t: float
    checked_at: str | None
    state: str | None
    kind: str | None
    free_mib: int | None
    swap_used_mib: int | None
    psi_full: float | None
    dify_swap_mib: int | None
    brake: str | None
    brake_age_s: float | None
    swap_grow10_mib: int | None = None
    oom_outside: int | None = None
    fresh: bool = False
    no_data: tuple[str, ...] = ()
    health_ok: bool = True
    dify_fails: int = 0
    drill: bool | None = None


def point_json(p: MemPoint) -> dict:
    """Строка OUT/memory.jsonl: ровно MEM_KEYS (R119)."""
    d = asdict(p)
    return {k: d[k] for k in MEM_KEYS}


def point_from_json(d: Mapping[str, Any]) -> MemPoint:
    """Точка из строки memory.jsonl: поля MEM_KEYS, недостающие — None (t — 0)."""
    kw = {k: d.get(k) for k in MEM_KEYS}
    t = _number(kw.pop("t"))
    return MemPoint(t=0.0 if t is None else t, **kw)


def read_points(path: Path) -> list[MemPoint]:
    """Точки OUT/memory.jsonl по порядку; оборванная строка — пропуск."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if isinstance(raw, dict):
            out.append(point_from_json(raw))
    return out


def _row(status: Mapping | None, component: str) -> dict | None:
    if not isinstance(status, Mapping):
        return None
    for c in status.get("checks") or []:
        if isinstance(c, dict) and c.get("component") == component:
            return c
    return None


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def memory_kind(detail: str) -> str:
    """Вид detail строки памяти: swap, oom_outside, psi, no_data, silent, threshold (мягкий или жёсткий порог Д1),
    other («свободно N МиБ» нормы и всё незнакомое)."""
    if detail in _KINDS:
        return _KINDS[detail]
    return "threshold" if THRESHOLD_RE.fullmatch(detail) else "other"


def mem_point(status: Mapping | None, health: Mapping | None, dify_swap: int | None, t: float) -> MemPoint:
    """Точка по ответу /status.json сторожа, /health/llm core и swap Dify (МиБ). Числа — из note (свободно — и из detail
    нормы); нет части — None, кроме убийств: их части нет при K = 0 (R114), при пометке «нет данных: vmstat» — None."""
    st = status if isinstance(status, Mapping) else None
    row = _row(st, "memory")
    checked = _text(st.get("checked_at")) or None if st is not None else None
    state = kind = None
    free = used = grow = kills = None
    psi = None
    no_data: tuple[str, ...] = ()
    if row is not None:
        state = _text(row.get("state")) or "unknown"
        detail, note = _text(row.get("detail")), _text(row.get("note"))
        kind = memory_kind(detail)
        m = MEM_NOTE_RE.search(note) or MEM_NOTE_RE.search(detail)
        free = int(m.group(1)) if m else None
        m = SWAP_NOTE_RE.search(note)
        used, grow = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        m = PSI_NOTE_RE.search(note)
        psi = float(m.group(1)) if m else None
        no_data = tuple(name for name, text in SENSOR_NOTES.items() if text in note)
        m = OOM_NOTE_RE.search(note)
        kills = int(m.group(1)) if m else (None if "vmstat" in no_data else 0)
    rb = health.get("run_brake") if isinstance(health, Mapping) else None
    brake = _text(rb.get("state")) or None if isinstance(rb, Mapping) else None
    age = _number(rb.get("age_s")) if isinstance(rb, Mapping) else None
    dify = None if isinstance(dify_swap, bool) or not isinstance(dify_swap, (int, float)) else int(dify_swap)
    hist = _row(st, "historian")
    drill = None if st is None else (hist is not None
                                     and DRILL_DATA_NOTE in f"{_text(hist.get('detail'))} {_text(hist.get('note'))}")
    return MemPoint(t=float(t), checked_at=checked, state=state, kind=kind, free_mib=free, swap_used_mib=used,
                    psi_full=psi, dify_swap_mib=dify, brake=brake, brake_age_s=age, swap_grow10_mib=grow,
                    oom_outside=kills, fresh=row is not None and not st.get("stale"), no_data=no_data,
                    health_ok=isinstance(health, Mapping), drill=drill)


# --- вердикт ---

@dataclass(frozen=True)
class Limits:
    """Пороги и базы окна сторожа памяти; база None — условие «от базы» не проверяется; need_drill — «без данных»:
    пометка учения tool_error обязательна на каждой странице."""
    swap_base_mib: int | None
    swap_grow_mib: int
    dify_swap_base_mib: int | None
    dify_swap_max_mib: int
    mem_fail_mib: int
    psi_full_max: float
    need_drill: bool = False


def limits(cfg: Any, swap_base: int | None = None, dify_base: int | None = None) -> Limits:
    """Пороги из конфига (поля базы окна); нет поля или null — умолчание R119, базы — первое прочитанное значение."""
    def get(name: str, default: Any) -> Any:
        value = getattr(cfg, name, None)
        return default if value is None else value
    return Limits(swap_base_mib=get("swap_base_mib", swap_base), swap_grow_mib=get("swap_grow_mib", SWAP_GROW_MIB),
                  dify_swap_base_mib=get("dify_swap_base_mib", dify_base),
                  dify_swap_max_mib=get("dify_swap_max_mib", DIFY_SWAP_MAX_MIB),
                  mem_fail_mib=get("mem_fail_mib", MEM_FAIL_MIB), psi_full_max=float(get("psi_full_max", PSI_FULL_MAX)),
                  need_drill=getattr(cfg, "load_data", None) == "none")


def _psi_held(points: Sequence[MemPoint], psi_max: float) -> bool:
    """Давление full выше порога в PSI_HOLD_POLLS разных снимках сторожа подряд (по checked_at; точки без страницы —
    не снимки, без checked_at — каждая свой снимок)."""
    snaps: list[tuple[object, float | None]] = []
    for p in points:
        if p.state is None:
            continue
        if snaps and p.checked_at is not None and snaps[-1][0] == p.checked_at:
            snaps[-1] = (p.checked_at, p.psi_full)
            continue
        snaps.append((p.checked_at if p.checked_at is not None else object(), p.psi_full))
    last = snaps[-PSI_HOLD_POLLS:]
    return len(last) == PSI_HOLD_POLLS and all(v is not None and v > psi_max for _, v in last)


def _memory_hard(points: Sequence[MemPoint], cur: MemPoint, lim: Limits) -> bool:
    if cur.state is not None:
        if cur.state not in ("ok", "warn") and cur.kind != "silent":    # fail, unknown и незнакомое
            return True
        if cur.state == "warn" and cur.kind != "threshold":   # swap, убийства, давление и незнакомый detail
            return True
        if cur.free_mib is not None and cur.free_mib < lim.mem_fail_mib:
            return True
        if (cur.oom_outside or 0) > 0:
            return True
        if cur.swap_grow10_mib is not None and cur.swap_grow10_mib >= lim.swap_grow_mib:
            return True
        if (cur.swap_used_mib is not None and lim.swap_base_mib is not None
                and cur.swap_used_mib - lim.swap_base_mib >= lim.swap_grow_mib):
            return True
    if _psi_held(points, lim.psi_full_max):
        return True
    if (cur.dify_swap_mib is not None and lim.dify_swap_base_mib is not None
            and cur.dify_swap_mib - lim.dify_swap_base_mib >= lim.dify_swap_max_mib):
        return True
    return cur.brake == "halted"


def _brake_silent(points: Sequence[MemPoint], cur: MemPoint, now_t: float) -> bool:
    if cur.health_ok:
        return cur.brake not in BRAKE_STATES or cur.brake_age_s is None or cur.brake_age_s > BRAKE_SILENT_S
    prev = next((p for p in reversed(points) if p.health_ok), None)
    if prev is None or prev.brake not in BRAKE_STATES or prev.brake_age_s is None:
        return True
    return prev.brake_age_s + (now_t - prev.t) > BRAKE_SILENT_S


def guard_verdict(points: Sequence[MemPoint], cfg: Any, last_fresh_t: float | None, now_t: float) -> str:
    """Вердикт сторожа памяти по точкам (последняя — текущая): memory, data_drill, blind, brake (жёстко), soft или ok
    (см. шапку модуля). cfg — конфиг прогона или Limits; last_fresh_t — часы последнего нового свежего снимка."""
    if not points:
        return "blind"
    lim = cfg if isinstance(cfg, Limits) else limits(cfg)
    cur = points[-1]
    if _memory_hard(points, cur, lim):
        return "memory"
    if lim.need_drill and cur.drill is False:                # «без данных» без пометки учения — данные заказчика
        return "data_drill"
    if (cur.kind == "silent" or cur.no_data or cur.dify_fails >= DIFY_FAIL_LIMIT or last_fresh_t is None
            or now_t - last_fresh_t > LOAD_BLIND_S):
        return "blind"
    if _brake_silent(points, cur, now_t):
        return "brake"
    return "soft" if cur.state == "warn" else "ok"


# --- сторож памяти ---

class MemoryGuard:
    """Сторож памяти прогонщика (см. шапку модуля). on_verdict(вердикт) — после каждого check(act=True)."""

    def __init__(self, stand: Any, cfg: Any, out: Path, *, clock: Callable[[], float],
                 now: Callable[[], datetime], sleep: Callable[[float], None], on_verdict: Callable[[str], None],
                 poll_s: float = LOAD_MEM_POLL_S):
        self.stand = stand
        self.cfg = cfg
        self.path = Path(out) / "memory.jsonl"
        self.clock = clock
        self.now = now
        self.sleep = sleep
        self.on_verdict = on_verdict
        self.poll_s = poll_s
        self.points: deque[MemPoint] = deque(maxlen=POINTS_KEPT)
        self.last_status: dict | None = None
        self.last_health: dict | None = None
        self.first_health: dict | None = None
        self.last_fresh_t: float | None = None
        self.at_mono: float | None = None
        self.at_wall: datetime | None = None
        self.dead = False
        self.verdict: str | None = None
        self.swap_base: int | None = None
        self.dify_base: int | None = None
        self.thread: threading.Thread | None = None
        self._polls = 0
        self._dify: int | None = None
        self._dify_fails = 0
        self._checked: str | None = None
        self._failing: set[str] = set()
        self._write_failed = False
        self._peak: dict[str, float | None] = {"free_base": None, "free_min": None, "swap_max": None, "dify_max": None,
                                               "psi_max": None}
        self._poll_lock = threading.Lock()
        self._stop = threading.Event()

    # --- опрос ---

    def check(self, act: bool = True) -> str:
        """Опрос и вердикт; сбой кода сторожа (не чтения стенда) — сторож мёртв, вердикт blind."""
        with self._poll_lock:
            if self.dead:
                verdict = "blind"
            else:
                try:
                    verdict = self._poll()
                except Exception as exc:
                    self.dead = True
                    log.error("сторож памяти: сбой опроса (%s) — прогонщик слеп", type(exc).__name__)
                    verdict = "blind"
            self.verdict = verdict
        if act:
            self.on_verdict(verdict)
        return verdict

    def _read(self, what: str, fn: Callable[[], Any]) -> Any:
        try:
            value = fn()
        except Exception as exc:
            if what not in self._failing:
                log.warning("сторож памяти: %s не прочитан (%s)", what, type(exc).__name__)
            self._failing.add(what)
            return None
        if what in self._failing:
            log.info("сторож памяти: %s снова читается", what)
        self._failing.discard(what)
        return value

    def _poll(self) -> str:
        t = self.clock()
        status = self._read("/status.json", self.stand.status)
        health = self._read("/health/llm", self.stand.health_llm)
        if self._polls % DIFY_EVERY_POLLS == 0:
            value = self._read("swap Dify", self.stand.dify_swap_mib)
            if isinstance(value, int) and not isinstance(value, bool):
                self._dify, self._dify_fails = value, 0
            else:
                self._dify_fails += 1
        self._polls += 1
        status = status if isinstance(status, dict) else None
        health = health if isinstance(health, dict) else None
        p = replace(mem_point(status, health, self._dify, t), dify_fails=self._dify_fails)
        if status is not None:
            self.last_status = status
        if health is not None:
            self.last_health = health
            if self.first_health is None:
                self.first_health = health
        if p.fresh and (p.checked_at is None or p.checked_at != self._checked):
            self.last_fresh_t, self._checked = t, p.checked_at
        elif self.last_fresh_t is None:
            self.last_fresh_t = t                       # отсчёт слепоты — с первого опроса
        if self.swap_base is None and p.swap_used_mib is not None:
            self.swap_base = p.swap_used_mib
        if self.dify_base is None and p.dify_swap_mib is not None:
            self.dify_base = p.dify_swap_mib
        self.points.append(p)
        self._peaks(p)
        self._record(p)
        verdict = guard_verdict(list(self.points), self.limits(), self.last_fresh_t, t)
        self.at_mono, self.at_wall = self.clock(), self.now()
        return verdict

    def limits(self) -> Limits:
        return limits(self.cfg, self.swap_base, self.dify_base)

    def _peaks(self, p: MemPoint) -> None:
        if self._peak["free_base"] is None:                 # база — первая точка со свободной памятью (как load-gate)
            self._peak["free_base"] = p.free_mib
        for key, value, pick in (("free_min", p.free_mib, min), ("swap_max", p.swap_used_mib, max),
                                 ("dify_max", p.dify_swap_mib, max), ("psi_max", p.psi_full, max)):
            if value is not None:
                old = self._peak[key]
                self._peak[key] = value if old is None else pick(old, value)

    def _record(self, p: MemPoint) -> None:
        """Строка memory.jsonl; сбой записи — строка журнала, не смерть сторожа: решения от файла не зависят."""
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(point_json(p), ensure_ascii=False) + "\n")
        except OSError as exc:
            if not self._write_failed:
                log.warning("сторож памяти: memory.jsonl не записан (%s)", type(exc).__name__)
            self._write_failed = True

    def summary(self) -> dict:
        """База и минимум свободной памяти, swap и swap Dify от базы (наибольшие), наибольшее давление full."""
        lim = self.limits()

        def above(peak: float | None, base: int | None) -> float | None:
            return None if peak is None or base is None else peak - base
        return {"free_base_mib": self._peak["free_base"], "free_min_mib": self._peak["free_min"],
                "swap_from_base_mib": above(self._peak["swap_max"], lim.swap_base_mib),
                "dify_swap_from_base_mib": above(self._peak["dify_max"], lim.dify_swap_base_mib),
                "psi_full_peak": self._peak["psi_max"]}

    # --- поток ---

    def start(self) -> None:
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="сторож-памяти", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._pause()
            if self._stop.is_set():
                return
            self.check()
            if self.dead:
                return

    def _pause(self) -> None:
        """poll_s по часам прогонщика шагами PAUSE_STEP_S (на подставных часах — без настоящей паузы)."""
        end = self.clock() + self.poll_s
        while not self._stop.is_set():
            left = end - self.clock()
            if left <= 0:
                return
            self.sleep(min(left, PAUSE_STEP_S))

    def stop(self) -> None:
        self._stop.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=GUARD_JOIN_S)

    def alive(self, now_mono: float) -> bool:
        """Сторож жив: не умер, поток (если запущен) идёт, последний опрос не старше LOAD_BLIND_S."""
        if self.dead:
            return False
        if self.thread is not None and not self.thread.is_alive() and not self._stop.is_set():
            return False
        return self.at_mono is not None and now_mono - self.at_mono <= LOAD_BLIND_S


# --- ворота лестницы ---

@dataclass(frozen=True)
class GateVerdict:
    base: int
    low: int
    places: int
    per_place: float
    next_k: int
    projected: float
    limit: int
    ok: bool
    max_k: int


def gate_verdict(points: Sequence[MemPoint], places: int, next_k: int, warn_mib: int = MEM_WARN_MIB) -> GateVerdict:
    """Прогноз памяти на next_k мест по ступени: база — первая точка со свободной памятью, минимум — наименьшая, прирост
    на место — (база − минимум) / places; «можно» — прогноз не ниже warn_mib + GATE_MARGIN_MIB; max_k — наибольшее
    число мест (до MAX_PLACES), для которого это так."""
    free = [p.free_mib for p in points if p.free_mib is not None]
    if not free:
        raise ValueError("load-gate: в memory.jsonl нет точек свободной памяти")
    if isinstance(places, bool) or not isinstance(places, int) or places <= 0:
        raise ValueError("load-gate: мест ступени — целое больше нуля")
    base, low = free[0], min(free)
    per = max(0, base - low) / places
    limit = warn_mib + GATE_MARGIN_MIB
    projected = base - next_k * per
    if per <= 0:
        max_k = MAX_PLACES if base >= limit else 0
    else:
        max_k = max(0, min(MAX_PLACES, math.floor((base - limit) / per)))
    return GateVerdict(base=base, low=low, places=places, per_place=per, next_k=next_k, projected=projected,
                       limit=limit, ok=projected >= limit, max_k=max_k)


def gate_line(v: GateVerdict) -> str:
    """Одна строка чисел load-gate."""
    tail = "можно" if v.ok else f"нельзя, наибольшее {v.max_k}"
    return (f"база {v.base} МиБ, минимум {v.low} МиБ, мест {v.places}, прирост на место {v.per_place:.0f} МиБ; "
            f"прогноз на {v.next_k} мест: {v.projected:.0f} МиБ — {tail}")


# датчики жёстких условий в точке memory.jsonl: поле null — у датчика на старте не было данных
START_SENSORS = (("свободная память", "free_mib"), ("swap", "swap_used_mib"), ("давление", "psi_full"),
                 ("vmstat", "oom_outside"), ("swap Dify", "dify_swap_mib"))


def start_gaps(first: Mapping[str, Any]) -> list[str]:
    """Датчики жёстких условий без данных на старте ступени: поле первой строки memory.jsonl есть и равно null (решение
    контроллера 03.10: лестницу и ступень без них не начинать). Поля нет — строка не формата R119, не судится."""
    return [name for name, key in START_SENSORS if key in first and first[key] is None]


def gate_from_out(out: Path, next_k: int, warn_mib: int = MEM_WARN_MIB) -> tuple[str, int]:
    """load-gate по OUT ступени: (одна строка, код выхода — 0 «можно», 1 «нельзя»)."""
    lines = [json.loads(x) for x in (Path(out) / "memory.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    points = [point_from_json(x) for x in lines if isinstance(x, dict)]
    if not points:
        raise ValueError("load-gate: memory.jsonl пуст")
    gaps = start_gaps(lines[0])
    if gaps:
        return f"нельзя: на старте ступени нет данных датчиков: {', '.join(gaps)}", 1
    summary = json.loads((Path(out) / "load.json").read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise ValueError("load-gate: load.json — не объект")
    reason = summary.get("reason")
    if reason is not None and reason != "finished":
        return f"нельзя: ступень не закончена ({reason})", 1
    places = summary.get("places")
    if not isinstance(places, list):
        raise ValueError("load-gate: в load.json нет мест ступени")
    v = gate_verdict(points, len(places), next_k, warn_mib)
    return gate_line(v), 0 if v.ok else 1


# --- load-check ---

# числа нагрузки изнутри pcbk-core: argv — JSON-список вызывающих, начало и конец (ISO 8601), необязательно — путь базы
# и белого списка (тест); только чтение (mode=ro), в stdout — только числа и коды исходов (R120)
LOAD_CHECK_PY = """\
import json, sqlite3, sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
def when(text):
    try:
        t = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
def names(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []
callers = [c for c in json.loads(sys.argv[1]) if isinstance(c, str)]
since, until = when(sys.argv[2]), when(sys.argv[3])
if since is None or until is None:
    sys.exit('load_check: начало и конец - ISO 8601')
db = sys.argv[4] if len(sys.argv) > 4 else '/var/lib/pcbk-core/core.db'
wl = sys.argv[5] if len(sys.argv) > 5 else '/app/data/whitelist.txt'
with open(wl, encoding='utf-8-sig') as f:
    white = {x.strip() for x in f.read().splitlines() if x.strip() and not x.strip().startswith('#')}
c = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
low = (since - timedelta(days=1)).astimezone(timezone.utc).isoformat()
own = set(callers)
out = {'events': 0, 'sql_events': 0, 'sent_unlisted': 0, 'requested_unlisted': 0, 'max_sql_5m': 0}
outcomes = Counter()
sql_times = []
for caller, channel, requested, sent, outcome, ts in c.execute(
        'SELECT caller, channel, requested, sent, outcome, ts FROM agent_events WHERE ts >= ?', (low,)):
    t = when(ts)
    if t is None or not since <= t <= until:
        continue
    sent_names = names(sent)
    if channel != 'system' and sent_names:
        sql_times.append(t)
    if caller not in own:
        continue
    out['events'] += 1
    outcomes[str(outcome)] += 1
    if sent_names:
        out['sql_events'] += 1
    out['sent_unlisted'] += sum(1 for n in sent_names if n not in white)
    out['requested_unlisted'] += sum(1 for n in names(requested) if n.strip() not in white)
sql_times.sort()
j = 0
for i, t in enumerate(sql_times):
    while sql_times[j] < t - timedelta(seconds=300):
        j += 1
    out['max_sql_5m'] = max(out['max_sql_5m'], i - j + 1)
out['outcomes'] = dict(outcomes)
calls, usd, refused, minutes = 0, 0.0, Counter(), defaultdict(set)
if callers:
    q = ('SELECT caller, ts, status, code, cost, cost_est FROM llm_calls WHERE ts >= ? AND caller IN (%s)'
         % ','.join('?' * len(callers)))
    for caller, ts, status, code, cost, cost_est in c.execute(q, (low, *callers)):
        t = when(ts)
        if t is None or not since <= t <= until:
            continue
        if status == 'refused':
            refused[str(code)] += 1
            continue
        calls += 1
        usd += cost if isinstance(cost, (int, float)) else (cost_est or 0.0)
        minutes[t.astimezone(timezone.utc).replace(second=0, microsecond=0)].add(caller)
out['llm_calls'] = calls
out['llm_usd'] = round(usd, 6)
out['llm_refused'] = dict(refused)
out['concurrency_minutes'] = sum(1 for s in minutes.values() if own <= s) if own else 0
print(json.dumps(out))
"""


def _when(text: Any) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def row_cost(row: Mapping[str, Any]) -> float:
    """Что стоит строка журнала прокси: cost, а без него — cost_est (как сумма прогона)."""
    cost = _number(row.get("cost"))
    if cost is None:
        cost = _number(row.get("cost_est"))
    return cost or 0.0


def _model_rows(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Вызовы модели: строки журнала прокси без отказов прокси (status refused — до модели не дошли)."""
    return [r for r in rows if isinstance(r, Mapping) and r.get("status") != "refused"]


def concurrency_minutes(rows: Iterable[Mapping[str, Any]], callers: Iterable[str]) -> int:
    """Минуты одновременной работы (R120): минуты UTC, в которые у каждого вызывающего был хотя бы один вызов модели;
    то же, что считает LOAD_CHECK_PY."""
    need = set(callers)
    if not need:
        return 0
    by_minute: dict[datetime, set[str]] = defaultdict(set)
    for r in _model_rows(rows):
        t = _when(r.get("ts"))
        if t is not None and isinstance(r.get("caller"), str):
            by_minute[t.astimezone(timezone.utc).replace(second=0, microsecond=0)].add(r["caller"])
    return sum(1 for s in by_minute.values() if need <= s)


def price_per_reply(records: Iterable[Mapping[str, Any]], rows: Iterable[Mapping[str, Any]]) -> dict:
    """Цена реплики (R120, I-2 р2): реплик студентов — ходов записей runs.jsonl (и вопрос моста), вызовов модели и
    расход — по строкам журнала прокси; на реплику — расход и вызовы (без реплик — None)."""
    replies = sum(len(r.get("turns") or []) + (1 if r.get("bridge") else 0) for r in records if isinstance(r, Mapping))
    calls = _model_rows(rows)
    usd = sum(row_cost(r) for r in calls)
    return {"replies": replies, "calls": len(calls), "usd": round(usd, 6),
            "usd_per_reply": round(usd / replies, 6) if replies else None,
            "calls_per_reply": round(len(calls) / replies, 2) if replies else None}


def runs_json(out: Path) -> list[dict]:
    """Записи OUT/runs.jsonl как есть (объекты JSON); оборванная строка — пропуск."""
    path = Path(out) / "runs.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if isinstance(raw, dict):
            records.append(raw)
    return records


def run_rows(records: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Строки журнала прокси всех записей прогона (llm_rows)."""
    return [r for rec in records for r in rec.get("llm_rows") or [] if isinstance(r, dict)]


def write_json(path: Path, value: Any) -> None:
    """JSON атомарно (временный файл рядом и os.replace)."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_price(out: Path) -> dict:
    """Цена реплики по OUT/runs.jsonl — в OUT/price.json; возвращает её."""
    records = runs_json(out)
    price = price_per_reply(records, run_rows(records))
    write_json(Path(out) / "price.json", price)
    return price


def overrun_usd(rows: Iterable[Mapping[str, Any]], places: int) -> float:
    """Допустимый перерасход прогона (Д8-R69): 3 × мест × наибольшая цена строки журнала прокси."""
    costs = [row_cost(r) for r in rows if isinstance(r, Mapping)]
    return 3 * places * max(costs) if costs else 0.0


def check_verdict(numbers: Mapping[str, Any], before: Mapping[str, Any], end: Mapping[str, Any],
                  iso: Mapping[str, Any], *, overrun_usd: float, data: bool = True) -> list[str]:
    """Что не прошло в load-check (тексты без значений; пусто — норма): SQL (только с данными) — имён вне списка в SQL
    0, событий с SQL за любые 5 минут не больше GATE_LIMIT_5M; деньги — расход прогона не выше потолка и перерасхода,
    доли мест не исчерпаны нагрузкой, общий расход ниже бюджета; «только своё» — чужих 0 на каждом месте."""
    problems: list[str] = []
    if data:
        unlisted, top = _number(numbers.get("sent_unlisted")), _number(numbers.get("max_sql_5m"))
        if unlisted is None or top is None:
            problems.append(CHECK_NUMBERS)
        else:
            if unlisted > 0:
                problems.append(CHECK_SENT_UNLISTED)
            if top > GATE_LIMIT_5M:
                problems.append(CHECK_GATE)
    run = end.get("run")
    if not isinstance(run, Mapping):
        problems.append(CHECK_NO_RUN)
    else:
        spent, cap = _number(run.get("spent_usd")), _number(run.get("cap_usd"))
        if spent is None or cap is None:
            problems.append(CHECK_HEALTH)
        elif spent > cap + overrun_usd:
            problems.append(CHECK_RUN_CAP)
    shares_end = end.get("shares") if isinstance(end.get("shares"), Mapping) else {}
    shares_before = before.get("shares") if isinstance(before.get("shares"), Mapping) else {}
    spent_end, spent_before = _number(shares_end.get("spent")), _number(shares_before.get("spent"))
    total_spent, total = _number(end.get("spent_usd")), _number(end.get("total_usd"))
    if None in (spent_end, spent_before, total_spent, total):
        problems.append(CHECK_HEALTH)
    else:
        if spent_end > spent_before:
            problems.append(CHECK_SHARE)
        if total_spent >= total:
            problems.append(CHECK_TOTAL)
    places = [v for v in iso.values() if isinstance(v, Mapping)]
    counts = [(v.get("foreign_sessions"), v.get("foreign_agents")) for v in places]
    if not places or any(not isinstance(a, int) or not isinstance(b, int) for a, b in counts):
        problems.append(CHECK_ISO)
    elif any(a or b for a, b in counts):
        problems.append(CHECK_FOREIGN)
    return problems


def check_lines(numbers: Mapping[str, Any], price: Mapping[str, Any], before: Mapping[str, Any],
                end: Mapping[str, Any], iso: Mapping[str, Any], *, overrun_usd: float, data: bool) -> list[str]:
    """Строки load-check — только числа и коды исходов (без тегов, имён и текстов разговоров)."""
    def usd(x: Any) -> str:
        v = _number(x)
        return "-" if v is None else f"${v:.4f}"

    def kv(d: Any) -> str:
        return ", ".join(f"{k} {v}" for k, v in sorted(d.items())) if isinstance(d, Mapping) and d else "нет"
    lines = [f"событий {numbers.get('events')}, с SQL {numbers.get('sql_events')}, имён вне списка в SQL "
             f"{numbers.get('sent_unlisted')}, в запросе вне списка {numbers.get('requested_unlisted')}, наибольшее "
             f"с SQL за 5 мин {numbers.get('max_sql_5m')} (предел {GATE_LIMIT_5M})",
             f"исходы: {kv(numbers.get('outcomes'))}"]
    if not data:
        lines.append(NO_DATA_LINE)
    lines.append(f"вызовов модели {numbers.get('llm_calls')}, расход {usd(numbers.get('llm_usd'))}, отказы прокси: "
                 f"{kv(numbers.get('llm_refused'))}")
    lines.append(f"минут одновременной работы {numbers.get('concurrency_minutes')}")
    lines.append(f"реплик {price.get('replies')}, вызовов {price.get('calls')}, {usd(price.get('usd'))}, на реплику "
                 f"{usd(price.get('usd_per_reply'))} (вызовов {price.get('calls_per_reply')})")
    run = end.get("run") if isinstance(end.get("run"), Mapping) else {}
    shares_b = before.get("shares") if isinstance(before.get("shares"), Mapping) else {}
    shares_e = end.get("shares") if isinstance(end.get("shares"), Mapping) else {}
    lines.append(f"прогон: расход {usd(run.get('spent_usd'))} при потолке {usd(run.get('cap_usd'))} и перерасходе "
                 f"{usd(overrun_usd)}; исчерпанных долей {shares_b.get('spent')} → {shares_e.get('spent')}; общий "
                 f"расход {usd(end.get('spent_usd'))} из {usd(end.get('total_usd'))}")
    places = {k: v for k, v in iso.items() if isinstance(v, Mapping)}
    if places:
        lines.append(f"только своё: мест {len(places)}, чужих разговоров "
                     f"{sum(v.get('foreign_sessions') or 0 for v in places.values())}, чужих агентов "
                     f"{sum(v.get('foreign_agents') or 0 for v in places.values())}")
    else:
        lines.append(f"только своё: {ISO_SKIPPED if iso.get('skipped') else 'нет OUT/isolation.json'}")
    return lines


# --- «только своё» ---

def own_only(listed: Iterable[str], own: Iterable[str], pre: Iterable[str]) -> list[str]:
    """Чужие разговоры места (R121): из списка места — не свои (sessions.log) и не бывшие на месте до прогона."""
    mine, before = set(own), set(pre)
    return [s for s in listed if s not in mine and s not in before]


__all__ = ["CHECK_FOREIGN", "CHECK_GATE", "CHECK_ISO", "CHECK_RUN_CAP", "CHECK_SENT_UNLISTED", "CHECK_SHARE",
           "CHECK_TOTAL", "CONSENT_FILE", "DIFY_EVERY_POLLS", "DIFY_FAIL_LIMIT", "DRILL_DATA_NOTE", "GATE_LIMIT_5M",
           "GATE_MARGIN_MIB", "GateVerdict", "HARD_VERDICTS", "ISO_SKIPPED", "LOAD_BLIND_S", "LOAD_CHECK_PY",
           "LOAD_MEM_POLL_S", "Limits", "MEM_KEYS", "MEM_NOTE_RE", "MEM_NO_DATA_DETAIL", "MEM_OOM_OUTSIDE_DETAIL",
           "MEM_PSI_DETAIL", "MEM_SENSOR_SILENT_DETAIL", "MEM_SWAP_DETAIL", "MemPoint", "MemoryGuard", "OOM_NOTE_RE",
           "PSI_HOLD_POLLS", "PSI_NOTE_RE", "SENSOR_NOTES", "START_SENSORS", "SWAP_NOTE_RE", "VERDICTS",
           "WINDOW_BASE_KEYS", "check_lines", "check_verdict", "concurrency_minutes", "gate_from_out", "gate_line",
           "gate_verdict", "guard_verdict", "limits", "mem_point", "memory_kind", "overrun_usd", "own_only",
           "point_from_json", "point_json", "price_per_reply", "read_points", "row_cost", "run_rows", "runs_json",
           "start_gaps", "write_json", "write_price"]
