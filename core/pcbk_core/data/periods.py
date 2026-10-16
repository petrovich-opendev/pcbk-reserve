"""Периоды сводки: готовые периоды и смены, свой период в ISO 8601.

Всё время — местное время историана без пояса. «Сейчас» округляется вниз до
30 с (round_now): так период один и тот же у повторных вызовов, и кэш
задачи 3 их склеивает. is_open — одно правило и для заметки «период ещё
идёт», и для срока кэша.

Смены КДМ-1 — дневная 08:00–20:00 и ночная 20:00–08:00 следующего дня, пермское время (местное время
историана, UTC+05); подтверждено владельцем 16.10.2026 (Д27; до того — допущение 06:00, 14:00, 22:00, решение №6).
Ночная смена переходит через полночь.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

ROUND_S = 30
CLOSED_AFTER_S = 300
SHIFT_STARTS = (8, 20)          # Д27: дневная 08:00–20:00, ночная 20:00–08:00
PRESETS = ("last_hour", "last_8h", "last_24h", "current_shift", "prev_shift", "today", "yesterday", "last_7d",
           "last_30d")
MIN_PERIOD = timedelta(seconds=60)
EARLIEST = datetime(2000, 1, 1)

# готовые периоды «последние N» — от округлённого «сейчас» назад
_SPANS = {"last_hour": timedelta(hours=1), "last_8h": timedelta(hours=8), "last_24h": timedelta(hours=24),
          "last_7d": timedelta(days=7), "last_30d": timedelta(days=30)}
_SHIFT_PRESETS = frozenset({"current_shift", "prev_shift"})


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    preset: str | None
    shift_assumed: bool         # период — смена: в ответе заметка с границами смен (SHIFT_NOTE)
    clamped: bool               # конец ограничен «сейчас»
    open: bool                  # is_open(end, «сейчас»): период ещё идёт


class PeriodError(ValueError):
    """Период задан неверно; текст — для людей, без введённых строк."""


def round_now(now: datetime) -> datetime:
    """«Сейчас» вниз до ROUND_S секунд."""
    return now.replace(second=now.second - now.second % ROUND_S, microsecond=0)


def is_open(end: datetime, now: datetime) -> bool:
    """Период ещё идёт: конец не старше CLOSED_AFTER_S — по этому же правилу кэш держит его недолго."""
    return end > now - timedelta(seconds=CLOSED_AFTER_S)


def _shift_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Начало текущей смены и начало предыдущей; сетка SHIFT_STARTS, через полночь."""
    day = now.date()
    starts = sorted(datetime.combine(day - timedelta(days=back), time(hour))
                    for back in (2, 1, 0) for hour in SHIFT_STARTS)
    past = [s for s in starts if s <= now]
    return past[-1], past[-2]


def _preset_bounds(preset: str, now: datetime) -> tuple[datetime, datetime]:
    if preset in _SPANS:
        return now - _SPANS[preset], now
    midnight = datetime.combine(now.date(), time())
    if preset == "today":
        return midnight, now
    if preset == "yesterday":
        return midnight - timedelta(days=1), midnight
    current, previous = _shift_bounds(now)
    if preset == "current_shift":
        return current, now
    return previous, current                                    # prev_shift


def _parse(text: object, offset: timedelta, error: str) -> datetime:
    """ISO 8601: с поясом — в местное время историана через offset, без пояса — уже местное; доли секунды — вон."""
    if not isinstance(text, str):
        raise PeriodError(error)
    try:
        dt = datetime.fromisoformat(text.strip())
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone(offset)).replace(tzinfo=None)
    except (ValueError, OverflowError):
        dt = None
    if dt is None:                                  # ошибка — после блока: без текста ввода в __context__
        raise PeriodError(error)
    return dt.replace(microsecond=0)


def resolve_period(now: datetime, offset: timedelta, preset: str | None = None, start: str | None = None,
                   end: str | None = None) -> Period:
    """Период по готовому имени (preset) или по началу и концу (start, end) — ровно одним способом.

    now — «сейчас историана», местное без пояса; offset — сдвиг историана от UTC.
    """
    if now.tzinfo is not None:
        raise ValueError("now — местное время историана без пояса")
    now_r = round_now(now)
    custom = start is not None or end is not None
    if (preset is not None) == custom:
        raise PeriodError("задайте период одним способом: готовый (period) или начало и конец (start, end)")
    if preset is not None:
        if preset not in PRESETS:
            raise PeriodError("неизвестный готовый период")
        first, last = _preset_bounds(preset, now_r)
    else:
        if start is None or end is None:
            raise PeriodError("свой период: нужны начало и конец (start, end)")
        first = _parse(start, offset, "начало периода не распознано — нужен ISO 8601, например 2026-10-01T06:00")
        last = _parse(end, offset, "конец периода не распознан — нужен ISO 8601, например 2026-10-01T14:00")
    clamped = last > now_r
    if clamped:
        last = now_r
    if first < EARLIEST:
        raise PeriodError("начало периода раньше 2000-01-01")
    if first > now_r:
        raise PeriodError("начало периода позже текущего времени историана")
    if last < first:
        raise PeriodError("конец периода раньше начала")
    if last - first < MIN_PERIOD:
        raise PeriodError("период короче минуты")
    return Period(start=first, end=last, preset=preset, shift_assumed=preset in _SHIFT_PRESETS, clamped=clamped,
                  open=is_open(last, now_r))
