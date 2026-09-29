"""Свежесть историана по набору тегов (Д3а-R3).

Возраст — сколько прошло с последнего роста наибольшей метки набора по
монотонным часам службы, но не меньше возраста самой метки по часам историана.
Метки впереди GETDATE() поэтому не дают вечного «0 с», а подача, замершая до
запуска службы, видна сразу. Пороги держит сторож; здесь — только числа и код
ошибки, без имён тегов: ручку с Д4 видят сети мест.
"""
from datetime import datetime, timezone

from .gate import AUTH_LATCH_TEXT
from .historian import HistClock, Row

ERROR_TEXTS = {
    "connect": "нет связи с историаном",
    "timeout": "историан не ответил вовремя",
    "query": "историан вернул ошибку",
    # тот же текст, что у защёлки ворот: 4060 («база недоступна») приходит вместе с 18456
    "auth": AUTH_LATCH_TEXT,
    "catalog": "каталог тегов не загружен",
    "no_tags": "не выбраны теги свежести",
    "no_rows": "нет меток по тегам свежести",
}


def _tenth(value: float) -> float:
    """До 0,1 с; −0.0 → 0.0."""
    return round(value, 1) + 0.0


class FreshnessTracker:
    """Состояние опроса свежести; все часы — аргументами, сам трекер времени не берёт.

    freeze_stamps — учение «метка устарела»: держится первая наблюдённая метка,
    она никогда не «растёт», а историан читается как обычно.
    """

    def __init__(self, freeze_stamps: bool = False):
        self.freeze_stamps = freeze_stamps
        self.error: str | None = None
        self.tags = 0
        self._checked_at: datetime | None = None     # стена последней попытки — удачной или нет
        self._stamp: datetime | None = None          # наибольшая метка последнего удачного опроса
        self._raw_s: float | None = None             # часы историана − метка в том опросе
        self._poll_mono: float | None = None         # когда был тот опрос
        self._advance_mono: float | None = None      # когда метка последний раз выросла

    def observe(self, clock: HistClock, rows: list[Row], tags: int, mono: float, wall: datetime) -> None:
        """Ответ Live по набору: берётся наибольшая метка; строк с меткой нет — ошибка no_rows."""
        stamps = [clock.aware(r[1]) for r in rows if len(r) > 1 and isinstance(r[1], datetime)]
        if not stamps:
            self.fail("no_rows", tags, mono, wall)
            return
        stamp = max(stamps)
        if self.freeze_stamps and self._stamp is not None:
            stamp = self._stamp
        # рост — против прошлого опроса: сменённый набор с меткой пониже ростом не считается,
        # а следующий рост от неё — считается
        if self._stamp is None or stamp > self._stamp:
            self._advance_mono = mono
        self._stamp = stamp
        self._raw_s = (clock.aware(clock.local) - stamp).total_seconds()
        self._poll_mono = mono
        self._checked_at = wall
        self.tags = tags
        self.error = None

    def fail(self, code: str, tags: int, mono: float, wall: datetime) -> None:
        """Попытка без меток: прошлые метки сохраняются, возраст по ним растёт дальше."""
        if code not in ERROR_TEXTS:
            raise ValueError(f"нет текста для кода свежести {code!r}")
        self._checked_at = wall
        self.tags = tags
        self.error = code

    def to_json(self, mono: float) -> dict:
        age = skew = None
        if self._raw_s is not None:
            age = _tenth(max(0.0, max(0.0, self._raw_s) + (mono - self._poll_mono), mono - self._advance_mono))
            skew = _tenth(min(0.0, self._raw_s))
        checked = None if self._checked_at is None else self._checked_at.astimezone(timezone.utc).isoformat()
        return {"checked_at": checked, "age_s": age, "skew_s": skew, "error": self.error,
                "error_text": None if self.error is None else ERROR_TEXTS[self.error], "tags": self.tags}
