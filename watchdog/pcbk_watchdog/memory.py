"""«Память сервера» v2 (Д11-R114) и причины перезапуска контейнеров (Д11-R115).

Замер — /proc/meminfo (MemAvailable, MemTotal, SwapTotal, SwapFree), /proc/vmstat (oom_kill) и
/proc/pressure/memory (some и full avg10); vmstat и давление необязательны, но без данных датчика note несёт
пометку «нет данных: …», а датчик, замолчавший на ходу, даёт «неизвестно». История тактов за час: рост swap
за 10 мин, давление с удержанием, убийства по памяти, которые не объяснил перезапуск строки контейнера
(места, core, sp-ctl), — «вне мест и core» (Dify, система, edge, прокси сокета, сторож). Модуль без
зависимостей от остального сторожа: строку собирает checks.check_memory или такт main.MemState.
"""
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

# давление full выше порога столько тактов подряд — «внимание», столько же подряд не выше — снять (m-3 р3)
PSI_HOLD_TICKS = 3
# окна истории, с: убийства по памяти — за час, рост swap — за 10 мин
MEM_WINDOW_S = 3600.0
SWAP_WINDOW_S = 600.0
# замеров давления, ждущих psi_high, — не больше (час тактов по 10 с): без строки памяти очередь не растёт
PSI_BACKLOG = 360

# тексты detail постоянные — журнал пишет переход, а не каждый такт; числа — только в note
MEM_NO_DATA_DETAIL = "нет данных о свободной памяти"
MEM_OOM_OUTSIDE_DETAIL = "сервер убивал процессы по памяти вне мест и core"
MEM_PSI_DETAIL = "сервер ждёт освобождения памяти — давление выше порога"
MEM_SWAP_DETAIL = "сервер вытесняет память в swap"
# порог памяти выше всей памяти сервера — так ставят учения memory-warn и memory-fail (R116: пометка «учени»)
MEM_DRILL_NOTE = "учения: порог выше всей памяти"
# датчик, который в этой истории уже читался, замолчал или отвечает не по формату (доводка р1 А1, решение контроллера
# к R114): молчание — «неизвестно», не «норма»; тормоза видят его как «память не читается»
MEM_SENSOR_SILENT_DETAIL = "датчик памяти замолчал — убийства, давление или swap не читаются"
# датчики замера и пометки note без их данных: часть note не пропадает молча — по договору R114 нет части убийств
# значит «0 убийств», а нет части давления — «давление не проверено»
SENSORS = ("swap", "vmstat", "pressure")
SENSOR_NOTES = {"swap": "нет данных: swap", "vmstat": "нет данных: vmstat", "pressure": "нет данных: давление"}
NOTE_SEP = " · "

Reason = Literal["oom", "crash"]
_PSI_AVG10 = re.compile(r"\bavg10=([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True)
class MemSample:
    """Один замер такта; None — поле не прочитано (vmstat и давление необязательны)."""
    avail_mib: int
    total_mib: int | None
    swap_used_mib: int | None
    oom_kill: int | None
    psi_some10: float | None
    psi_full10: float | None

    def missing(self) -> tuple[str, ...]:
        """Датчики без данных в этом замере: swap — нет SwapTotal/SwapFree, vmstat — нет oom_kill, давление — нет
        full avg10 (файл не читается или не по формату)."""
        values = {"swap": self.swap_used_mib, "vmstat": self.oom_kill, "pressure": self.psi_full10}
        return tuple(name for name in SENSORS if values[name] is None)


def _fields(text: str, sep: str | None) -> dict[str, int]:
    """«имя<sep> число …» по строкам; нечисловое значение пропускается."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        if sep is None:
            parts = line.split()
            key, value = (parts[0], parts[1:]) if parts else ("", [])
        else:
            key, found, rest = line.partition(sep)
            if not found:
                continue
            value = rest.split()
        try:
            out[key.strip()] = int(value[0])
        except (IndexError, ValueError):
            pass
    return out


def parse_oom_kill(vmstat: str) -> int | None:
    """Счётчик ядра oom_kill из /proc/vmstat: все убийства по памяти — глобальные и по лимиту cgroup."""
    return _fields(vmstat, None).get("oom_kill")


def _parse_psi(pressure: str) -> tuple[float | None, float | None]:
    found: dict[str, float] = {}
    for line in pressure.splitlines():
        kind, _, rest = line.strip().partition(" ")
        m = _PSI_AVG10.search(rest)
        if kind in ("some", "full") and m:
            found[kind] = float(m.group(1))
    return found.get("some"), found.get("full")


def parse_sample(meminfo: str, vmstat: str, pressure: str) -> MemSample | None:
    """Замер из текстов файлов; без MemAvailable — None («нет данных о свободной памяти»)."""
    kb = _fields(meminfo, ":")
    if "MemAvailable" not in kb:
        return None
    total = kb.get("MemTotal")
    swap = (kb["SwapTotal"] - kb["SwapFree"]) // 1024 if {"SwapTotal", "SwapFree"} <= kb.keys() else None
    some, full = _parse_psi(pressure)
    return MemSample(avail_mib=kb["MemAvailable"] // 1024, total_mib=None if total is None else total // 1024,
                     swap_used_mib=None if swap is None else max(0, swap), oom_kill=parse_oom_kill(vmstat),
                     psi_some10=some, psi_full10=full)


class MemHistory:
    """История тактов строки «Память сервера».

    Первый замер счётчика oom_kill — база, не событие. Прирост счётчика — событие с временем замера; его объясняет
    attribute(n, now) из перезапуска своей строки контейнера (R115): необъяснённые приросты не старше match_s, старые
    первыми; не хватило — остаток ждёт приростов следующие match_s секунд (перезапуск увиден раньше замера).
    Необъяснённый прирост старше grace_s и не старше window_s — «вне мест и core».
    """

    def __init__(self, window_s: float = MEM_WINDOW_S, swap_window_s: float = SWAP_WINDOW_S, grace_s: float = 20.0,
                 match_s: float = 30.0, psi_hold: int = PSI_HOLD_TICKS):
        self.window_s, self.swap_window_s, self.grace_s, self.match_s = window_s, swap_window_s, grace_s, match_s
        self.psi_hold = psi_hold
        self._swap: deque[tuple[datetime, int]] = deque()
        self._oom_last: int | None = None
        self._kills: list[list] = []        # [время замера, необъяснённых убийств]
        self._credits: list[list] = []      # [срок, объяснённых, но ещё не увиденных замером]
        # давление full по тактам, ещё не учтённое удержанием: порог знает только psi_high
        self._psi_new: deque[float | None] = deque(maxlen=max(psi_hold, PSI_BACKLOG))
        self._psi_on = False
        self._psi_run = 0
        self._sensors_seen: set[str] = set()   # датчики, давшие данные хоть раз в этой истории

    def observe(self, now: datetime, sample: MemSample | None) -> None:
        if sample is None:
            return
        self._sensors_seen.update(set(SENSORS) - set(sample.missing()))
        if sample.swap_used_mib is not None:
            self._swap.append((now, sample.swap_used_mib))
        while self._swap and self._age(self._swap[0][0], now) > self.swap_window_s:
            self._swap.popleft()
        self._credits = [c for c in self._credits if c[0] >= now and c[1] > 0]
        if sample.oom_kill is not None:
            if self._oom_last is not None and sample.oom_kill > self._oom_last:
                n = sample.oom_kill - self._oom_last
                for c in self._credits:     # старые объяснения первыми
                    take = min(n, c[1])
                    c[1] -= take
                    n -= take
                if n:
                    self._kills.append([now, n])
            self._oom_last = sample.oom_kill   # меньше прежнего (счётчик другого ядра) — новая база
        self._kills = [k for k in self._kills if k[1] > 0 and self._age(k[0], now) <= self.window_s]
        self._psi_new.append(sample.psi_full10)

    def attribute(self, n: int, now: datetime, v_after: int | None = None) -> None:
        """n убийств по памяти объяснены перезапуском своей строки контейнера в now; v_after — счётчик oom_kill после
        осмотра этого контейнера. Остаток, не найденный в истории, ждёт приростов как кредит — но не больше приростов,
        которых история ещё не видела и которые не ждут другие кредиты (v_after минус последний замер и кредиты, m-5
        ревью А1): иначе кредит за убийство, замеренное раньше match_s назад или уже объяснённое перезапуском другой
        строки, поглотил бы следующее настоящее убийство вне мест."""
        for k in self._kills:
            if n <= 0:
                return
            if 0 <= self._age(k[0], now) <= self.match_s:
                take = min(n, k[1])
                k[1] -= take
                n -= take
        if n > 0 and v_after is not None and self._oom_last is not None:
            n = min(n, v_after - self._oom_last - sum(c[1] for c in self._credits))
        if n > 0:
            self._credits.append([now + timedelta(seconds=self.match_s), n])

    def swap_growth(self, now: datetime) -> int:
        """Рост занятого swap за swap_window_s: последний замер минус наименьший в окне; убыль — 0."""
        used = [v for t, v in self._swap if self._age(t, now) <= self.swap_window_s]
        return max(0, used[-1] - min(used)) if used else 0

    def outside(self, now: datetime) -> int:
        """Необъяснённые убийства по памяти старше grace_s и не старше window_s."""
        return sum(k[1] for k in self._kills if self.grace_s < self._age(k[0], now) <= self.window_s)

    def silent(self, sample: MemSample) -> tuple[str, ...]:
        """Датчики, которые в этой истории уже читались, а в этом замере данных не дали, — отказ на ходу."""
        return tuple(name for name in sample.missing() if name in self._sensors_seen)

    def psi_high(self, limit: float) -> bool:
        """Давление full выше limit: psi_hold тактов подряд выше — включить, столько же подряд не выше — снять.
        Такт без данных давления удержание не двигает: «внимание» само не снимается, пока файл молчит."""
        while self._psi_new:
            value = self._psi_new.popleft()
            if value is None:
                continue
            above = value > limit
            if above == self._psi_on:
                self._psi_run = 0
                continue
            self._psi_run += 1
            if self._psi_run >= self.psi_hold:
                self._psi_on, self._psi_run = above, 0
        return self._psi_on

    @staticmethod
    def _age(t: datetime, now: datetime) -> float:
        return (now - t).total_seconds()


def _pct(x: float) -> str:
    return f"{round(x, 2):g}"


def memory_verdict(sample: MemSample | None, history: MemHistory, now: datetime, warn_mib: int, fail_mib: int,
                   swap_grow_warn_mib: int, psi_full_max: float) -> tuple[str, str, str]:
    """(состояние, detail, note) строки по таблице R114: жёсткие для нагрузки условия — выше мягкого порога.

    Порядок: нет MemAvailable — unknown; свободно < fail — fail; датчик, который в этой истории читался, замолчал —
    unknown (доводка р1); убийства вне мест и core, давление, swap — warn; мягкий порог — warn; иначе ok.
    note — числа по порядку: «свободно N МиБ» (кроме ok), swap и его рост, давление, убийства вне мест и core
    (только больше нуля), пометка учений при пороге выше всей памяти. Датчик без данных — своя пометка
    «нет данных: …» на месте своей части. Прогонщик решает по числам note (R119).
    """
    if sample is None:
        return "unknown", MEM_NO_DATA_DETAIL, ""
    avail = sample.avail_mib
    growth = history.swap_growth(now)
    kills = history.outside(now)
    psi = history.psi_high(psi_full_max)
    missing = sample.missing()
    if avail < fail_mib:
        state, detail = "fail", f"свободно меньше {fail_mib} МиБ"
    elif history.silent(sample):
        state, detail = "unknown", MEM_SENSOR_SILENT_DETAIL
    elif kills > 0:
        state, detail = "warn", MEM_OOM_OUTSIDE_DETAIL
    elif psi:
        state, detail = "warn", MEM_PSI_DETAIL
    elif growth >= swap_grow_warn_mib:
        state, detail = "warn", MEM_SWAP_DETAIL
    elif avail < warn_mib:
        state, detail = "warn", f"свободно меньше {warn_mib} МиБ"
    else:
        state, detail = "ok", f"свободно {avail} МиБ"
    parts = [] if state == "ok" else [f"свободно {avail} МиБ"]
    if sample.swap_used_mib is not None:
        minutes = int(history.swap_window_s // 60)
        parts.append(f"swap занят {sample.swap_used_mib} МиБ (+{growth} МиБ за {minutes} мин)")
    else:
        parts.append(SENSOR_NOTES["swap"])
    if sample.psi_full10 is not None:
        psi_parts = [f"{kind} {_pct(v)} %" for kind, v in (("some", sample.psi_some10), ("full", sample.psi_full10))
                     if v is not None]
        parts.append("давление памяти " + ", ".join(psi_parts))
    else:
        parts.append(SENSOR_NOTES["pressure"])
    if kills:
        parts.append(f"убийств по памяти вне мест и core за час: {kills}")
    if "vmstat" in missing:
        parts.append(SENSOR_NOTES["vmstat"])
    if sample.total_mib is not None and max(warn_mib, fail_mib) > sample.total_mib:
        parts.append(MEM_DRILL_NOTE)
    return state, detail, NOTE_SEP.join(parts)


@dataclass
class _Seen:
    base: int | None = None              # v_before последнего такта не в окне Restarting
    started: datetime | None = None      # StartedAt, когда контейнер последний раз виден работающим
    restarts: int | None = None          # RestartCount тогда же
    pending: Reason | None = None        # причина из окна Restarting (State.OOMKilled)
    reason: Reason | None = None         # причина перезапуска с StartedAt reason_started
    reason_started: datetime | None = None
    last: Reason | None = None           # последняя причина — для «падает в цикле»


class RestartReasons:
    """Причины перезапуска строк вида container (R115): OOMKilled в окне Restarting и счётчик oom_kill ядра.

    Docker сбрасывает OOMKilled при новом старте, поэтому вне окна Restarting причину даёт прирост oom_kill от
    базы (v_before последнего такта не в окне) до v_after этого осмотра.
    """

    def __init__(self):
        self._seen: dict[str, _Seen] = {}

    def observe(self, component: str, inspect: dict | None, v_before: int | None,
                v_after: int | None) -> tuple[Reason | None, int]:
        """Осмотр такта → (причина нового перезапуска или None, сколько убийств по памяти он объясняет)."""
        if inspect is None:                  # контейнера нет — история заново
            self._seen.pop(component, None)
            return None, 0
        st = inspect.get("State") or {}
        rec = self._seen.setdefault(component, _Seen())
        if st.get("Restarting"):
            # окно Restarting (Running и Restarting): база и «виден работающим» не меняются
            rec.pending = "oom" if st.get("OOMKilled") or rec.pending == "oom" else "crash"
            return None, 0
        result: tuple[Reason | None, int] = (None, 0)
        if st.get("Running"):
            started = datetime.fromisoformat(st["StartedAt"])
            restarts = int(inspect.get("RestartCount") or 0)
            if rec.started is not None and started != rec.started:
                if restarts > rec.restarts:          # перезапуск политикой
                    result = self._classify(rec, restarts - rec.restarts, v_after)
                    rec.reason, rec.reason_started = result[0], started
                    if result[0] is not None:
                        rec.last = result[0]
                elif restarts < rec.restarts:        # docker start обнуляет счётчик: пробуждение, не сбой
                    rec.reason = rec.reason_started = rec.last = None
            rec.started, rec.restarts, rec.pending = started, restarts, None
        else:
            # остановлен (не работает и не в окне Restarting): следующий старт — docker start, он обнуляет
            # RestartCount, а прошлые причины к новой жизни не относятся (m-1 ревью А1: пробуждение и убийство места
            # за один такт дают RestartCount, равный прежнему, — перезапуск всё равно виден)
            rec.restarts, rec.pending = 0, None
            rec.reason = rec.reason_started = rec.last = None
        rec.base = v_before
        return result

    @staticmethod
    def _classify(rec: _Seen, growth: int, v_after: int | None) -> tuple[Reason | None, int]:
        kills = v_after - rec.base if v_after is not None and rec.base is not None else None
        if rec.pending == "oom":
            return "oom", max(1, min(kills or 0, growth))
        if rec.pending == "crash":
            return "crash", 0
        if kills is None:                    # счётчика нет — причину не знаем
            return None, 0
        if kills > 0:
            return "oom", min(kills, growth)
        return "crash", 0

    def reason(self, component: str, started_at: datetime | None) -> Reason | None:
        """Причина перезапуска, после которого контейнер работает с этим StartedAt."""
        rec = self._seen.get(component)
        return rec.reason if rec is not None and started_at is not None and rec.reason_started == started_at else None

    def last(self, component: str) -> Reason | None:
        """Последняя известная причина (в окне Restarting — ожидающая) — для «падает в цикле»."""
        rec = self._seen.get(component)
        return None if rec is None else rec.pending or rec.last
