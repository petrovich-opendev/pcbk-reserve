"""Двойник историана тестового стенда (Д8-R75, Д9-R86): данные — core/tests/data/bench-historian.json.

TwinHistorian — тот же QueryFn, что FakeHistorian (запись вызовов, сроки, задержки и отказы — его), но ответы на
шаблоны службы — по файлу фикстуры:
- часы (_CLOCK) — нынешние (now), историан в UTC+05:00;
- каталог (_CATALOG) — строки фикстуры как есть, поле catalog;
- теги без сигнала (101–107, 16FAKE_109_PV) отвечают как в Д8: живое значение (_LIVE_ALL, _LIVE_IN) — значение
  фикстуры с меткой «сейчас − 20 с» (age_s службы ≈ 20); сводка (_SUMMARY_IN) не зависит от периода, тега без строки
  сводки (20FAKE_107_PV) нет — у службы no_data; ряд (_SERIES_IN) — постоянное живое значение;
- теги с сигналом (поле signals: 20FAKE_201_PV, 20FAKE_202_PV, 20FAKE_210_RUN) — числа по времени t, секундам эпохи
  UTC (analog_value, state_value): живое значение — в момент «сейчас − 20 с»; сводка и наработка (_STATE_IN) — по
  отсчётам через 60 с на [start, end) (у наработки последний отсчёт — до конца периода; Last сводки — последний
  отсчёт периода); ряд — N точек от start до end, концы включены, Quality 0. Числа зависят от периода: автотест
  «агент = калькулятор» отличит неверный период;
- тег состояния без сигнала (20FAKE_211_CLS) строк наработки не даёт — у службы no_data.

Разбор SQL — регулярками fakes, период и число точек — своими, даты литералов — по своему DATE_FMT (не из
pcbk_core.data.sql: иначе двойник проверял бы код службы им же). В образ core не попадает: core/.dockerignore пускает
только pcbk_core и requirements.lock; в тестовом стенде его подключает обёртка twin_core (PCBK_TWIN_HISTORIAN).
"""
import json
import math
import re
import statistics
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fakes import _CATALOG, _CLOCK, _LIT, _LIVE_ALL, _LIVE_IN, _SERIES_IN, _STATE_IN, _SUMMARY_IN, FakeHistorian
from pcbk_core.data.historian import Row

BENCH_FIXTURE = Path(__file__).resolve().parent / "data" / "bench-historian.json"
TZ = timezone(timedelta(hours=5))               # пояс историана двойника
LIVE_AGE = timedelta(seconds=20)                # метка живого значения — «сейчас − 20 с»
CATALOG_COLUMNS = ("TagName", "Description", "TagType", "MinEU", "MaxEU", "Unit")
OUTSIDE_PREFIX = "16FAKE_"                      # вне белого списка стенда отбора (C2)
DATE_FMT = "%Y-%m-%d %H:%M:%S.000"              # литерал даты в SQL службы — свой, без импорта sql.DATE_FMT
SAMPLE_S = 60                                   # шаг отсчётов сигнала у сводки и наработки, с
ANALOG_KEYS = frozenset({"base", "amp", "period_h"})
STATE_KEYS = frozenset({"cycle_h", "duty"})
ANALOG_TYPE, STATE_TYPE = 1, 2                  # TagType каталога: аналоговый и тег состояния
# период и число точек из текста SQL, уже совпавшего с формой fakes: сводка и наработка — StartDateTime/EndDateTime,
# ряд — DateTime дважды
_SPAN = re.compile(r" AND (?:Start)?DateTime >= '(?P<start>[^']*)' AND (?:End)?DateTime <= '(?P<end>[^']*)'")
_CYCLES = re.compile(r" AND wwCycleCount = (?P<n>[0-9]+)\Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def analog_value(sig: Mapping, t: float) -> float:
    """Аналоговый сигнал в момент t (секунды эпохи UTC): base + amp · sin(2π · t / (period_h · 3600))."""
    return sig["base"] + sig["amp"] * math.sin(2 * math.pi * t / (sig["period_h"] * 3600))


def state_value(sig: Mapping, t: float) -> int:
    """Тег состояния в момент t (секунды эпохи UTC): 1 в первой доле duty каждого цикла cycle_h часов, иначе 0."""
    cycle = sig["cycle_h"] * 3600
    return 1 if t % cycle < sig["duty"] * cycle else 0


def _is_state(sig: Mapping) -> bool:
    """Вид сигнала по ключам: состояние (cycle_h, duty) или аналоговый (base, amp, period_h); иное — ValueError.
    Числа — тоже при загрузке (ревью А3, m-3): период и цикл больше нуля, доля 0…1; иначе первый запрос дал бы
    ZeroDivisionError, а доля вне 0…1 — молча постоянное состояние."""
    if set(sig) == STATE_KEYS:
        if not (_finite(sig["cycle_h"]) and sig["cycle_h"] > 0 and _finite(sig["duty"]) and 0 <= sig["duty"] <= 1):
            raise ValueError("двойник историана: цикл сигнала не больше нуля или доля вне 0…1")
        return True
    if set(sig) == ANALOG_KEYS:
        if not (all(_finite(sig[k]) for k in ANALOG_KEYS) and sig["period_h"] > 0):
            raise ValueError("двойник историана: период сигнала не больше нуля")
        return False
    raise ValueError("двойник историана: сигнал не того вида")


def _finite(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _epoch(local: datetime) -> float:
    """Местное время историана без пояса — секунды эпохи UTC."""
    return local.replace(tzinfo=TZ).timestamp()


def _local(text: str) -> datetime:
    try:
        return datetime.strptime(text, DATE_FMT)
    except ValueError:
        raise AssertionError("двойник историана: дата вне DATE_FMT") from None


def _span(sql: str) -> tuple[datetime, datetime]:
    """Начало и конец периода — местное время без пояса — из литералов SQL."""
    m = _SPAN.search(sql)
    if m is None:
        raise AssertionError("двойник историана: период вне формы")
    return _local(m.group("start")), _local(m.group("end"))


def _samples(start: datetime, end: datetime) -> list[float]:
    """Отсчёты сигнала через SAMPLE_S на [start, end) — секунды эпохи UTC; общие у сводки и наработки."""
    t0, t1 = _epoch(start), _epoch(end)
    return [t0 + i * SAMPLE_S for i in range(max(0, math.ceil((t1 - t0) / SAMPLE_S)))]


def _names(names_sql: str) -> list[str]:
    return [lit.replace("''", "'") for lit in _LIT.findall(names_sql)]


class TwinHistorian(FakeHistorian):
    """Историан стенда: catalog — строки каталога (TagName, Description, TagType, MinEU, MaxEU, Unit);
    values — имя → (Value, Quality); summary — имя → столбцы сводки; signals — имя → сигнал (ANALOG_KEYS у
    аналогового тега, STATE_KEYS у тега состояния). now — часы UTC (подмена в тестах)."""

    def __init__(self, catalog: list[Row], values: Mapping[str, tuple[float | None, int]],
                 summary: Mapping[str, Mapping[str, object]], now: Callable[[], datetime] = _utc_now,
                 signals: Mapping[str, Mapping[str, float]] | None = None):
        super().__init__()
        self.catalog: list[Row] = [tuple(r) for r in catalog]
        self.values: dict[str, tuple[float | None, int]] = dict(values)
        self.summary = {name: dict(cols) for name, cols in summary.items()}
        self.signals: dict[str, dict] = {name: dict(sig) for name, sig in (signals or {}).items()}
        self._states = frozenset(name for name, sig in self.signals.items() if _is_state(sig))
        types = {row[0]: row[2] for row in self.catalog}
        if any(types.get(name) != (STATE_TYPE if name in self._states else ANALOG_TYPE) for name in self.signals):
            raise ValueError("двойник историана: сигнал у тега вне каталога или не того вида")
        self.now = now
        # постоянные ответы и данные FakeHistorian (live, clock, ряд и наработка fakes) здесь не действуют (А3, m-4)
        self.live, self.clock, self.series, self.states = {}, [], {}, {}

    @classmethod
    def from_fixture(cls, path: Path | str = BENCH_FIXTURE,
                     now: Callable[[], datetime] = _utc_now) -> "TwinHistorian":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        catalog = [tuple(row[c] for c in CATALOG_COLUMNS) for row in data["catalog"]]
        values = {name: (v["Value"], v.get("Quality", 0)) for name, v in data["live"].items()}
        return cls(catalog, values, data["summary"], now, data.get("signals", {}))

    def _local_now(self) -> datetime:
        """«Сейчас» историана — местное время без пояса, как его отдаёт pytds."""
        return self.now().astimezone(TZ).replace(tzinfo=None)

    def _value_at(self, name: str, t: float) -> float:
        sig = self.signals[name]
        return float(state_value(sig, t)) if name in self._states else analog_value(sig, t)

    def _live_row(self, name: str, stamp: datetime) -> Row | None:
        """Живая строка: по сигналу в момент stamp или значение фикстуры; тега нет — None."""
        if name in self.signals:
            return (name, stamp, self._value_at(name, _epoch(stamp)), 0)
        if name in self.values:
            value, quality = self.values[name]
            return (name, stamp, value, quality)
        return None

    def _summary_of(self, name: str, span: tuple[datetime, datetime] | None) -> dict[str, object] | None:
        """Столбцы сводки: по сигналу — по отсчётам [start, end) (StdDev арифметическое, по всем отсчётам), иначе
        строка фикстуры за любой период; нет ни того, ни другого или нет отсчётов — None."""
        if name not in self.signals:
            return self.summary.get(name)
        times = _samples(*span)
        values = [self._value_at(name, t) for t in times]
        if not values:
            return None
        # Last (HAS_LAST) — последнее значение в периоде, а не живое (ревью А3, m-5): у закрытого периода — его конец
        last = datetime.fromtimestamp(times[-1], TZ).replace(tzinfo=None)
        return {"Minimum": min(values), "Maximum": max(values), "Average": statistics.fmean(values),
                "StdDev": statistics.pstdev(values), "PercentGood": 100.0, "Last": values[-1], "LastDateTime": last}

    def _series_of(self, name: str, times: list[datetime], stamp: datetime) -> list[Row]:
        """Точки ряда: по сигналу в каждый момент сетки, у тега без сигнала — постоянное живое значение."""
        if name in self.signals:
            return [(name, when, self._value_at(name, _epoch(when)), 0) for when in times]
        live = self._live_row(name, stamp)
        if live is None or live[2] is None:
            return []
        return [(name, when, live[2], live[3]) for when in times]

    def _states_of(self, name: str, samples: list[float], t1: float) -> list[Row]:
        """Наработка тега состояния с сигналом по отсчётам — строка на каждое встретившееся состояние, по убыванию
        значения: vValue; StateTimeTotal — время отсчётов в мс: каждый отсчёт — SAMPLE_S, последний — до конца периода
        t1 (ревью А3, m-2: конец не на целой минуте — часы не длиннее периода); StateTimePercent — доля этого времени
        × 100; StateCount — переходы в состояние внутри периода (начальное не считается). Без сигнала — строк нет."""
        if name not in self._states or not samples:
            return []
        seq = [state_value(self.signals[name], t) for t in samples]
        held: Counter[int] = Counter()
        for t, v in zip(samples, seq):
            held[v] += min(SAMPLE_S, t1 - t)
        total = sum(held.values())
        entered = Counter(b for a, b in zip(seq, seq[1:]) if a != b)
        return [(name, float(v), round(held[v] * 1000), held[v] / total * 100, entered[v])
                for v in sorted(held, reverse=True)]

    def _answer(self, sql: str) -> list[Row]:
        if _CLOCK.fullmatch(sql):
            utc = self.now().astimezone(timezone.utc)
            return [(utc.astimezone(TZ).replace(tzinfo=None), utc.replace(tzinfo=None))]
        if _CATALOG.fullmatch(sql):
            return list(self.catalog)
        stamp = self._local_now() - LIVE_AGE
        if _LIVE_ALL.fullmatch(sql):
            rows = [self._live_row(name, stamp) for name in dict.fromkeys([*self.values, *self.signals])]
            return [row for row in rows if row[2] is not None]
        if _LIVE_IN.fullmatch(sql):
            rows = [self._live_row(name, stamp) for name in _names(sql)]
            return [row for row in rows if row is not None]
        if m := _SUMMARY_IN.fullmatch(sql):
            columns = m.group("columns").split(", ")
            names = _names(m.group("names"))
            # даты нужны только сигналам: теги Д8 отвечают за любой период, как раньше
            span = _span(sql) if any(name in self.signals for name in names) else None
            rows = []
            for name in names:
                cols = self._summary_of(name, span)
                if cols is None:
                    continue
                # Last и LastDateTime (HAS_LAST, Д3б) — живое значение и его метка
                live = self._live_row(name, stamp)
                row = {"Last": None if live is None else live[2], "LastDateTime": stamp, **cols}
                if not all(c in row for c in columns):
                    raise AssertionError("двойник историана: столбец сводки вне фикстуры")
                rows.append((name, *(row[c] for c in columns)))
            return rows
        if m := _SERIES_IN.fullmatch(sql):
            start, end = _span(sql)
            n = int(_CYCLES.search(sql).group("n"))
            if n < 2:
                raise AssertionError("двойник историана: точек ряда меньше двух")
            times = [start + (end - start) * i / (n - 1) for i in range(n)]     # концы включены, последняя — end
            return [row for name in _names(m.group("names")) for row in self._series_of(name, times, stamp)]
        if m := _STATE_IN.fullmatch(sql):
            start, end = _span(sql)
            samples = _samples(start, end)
            return [row for name in _names(m.group("names")) for row in self._states_of(name, samples, _epoch(end))]
        raise AssertionError("двойник историана: SQL вне шаблонов службы")


def bench_whitelist() -> frozenset[str]:
    """Белый список стенда: все теги фикстуры, кроме 16FAKE_* (C2) — с тегами Д9-R86."""
    return frozenset(row[0] for row in TwinHistorian.from_fixture(BENCH_FIXTURE).catalog
                     if not row[0].startswith(OUTSIDE_PREFIX))
