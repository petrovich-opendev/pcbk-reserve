"""Двойник историана тестового стенда отбора модели (Д8-R75): данные — core/tests/data/bench-historian.json.

TwinHistorian — тот же QueryFn, что FakeHistorian (запись вызовов, сроки, задержки и отказы — его), но ответы на
шаблоны службы — по файлу фикстуры:
- часы (_CLOCK) — нынешние, историан в UTC+05:00;
- каталог (_CATALOG) — строки фикстуры как есть, поле catalog;
- живые значения (_LIVE_ALL, _LIVE_IN) — значения фикстуры с меткой «сейчас − 20 с»: age_s службы ≈ 20;
- сводка (_SUMMARY_IN) не зависит от периода; тега без строки сводки (20FAKE_107_PV) нет — у службы no_data.

Разбор SQL — регулярками fakes (свой, не из pcbk_core.data.sql). В образ core не попадает: core/.dockerignore пускает
только pcbk_core и requirements.lock; в тестовом стенде его подключает обёртка twin_core (PCBK_TWIN_HISTORIAN).
"""
import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fakes import _CATALOG, _CLOCK, _LIT, _LIVE_ALL, _LIVE_IN, _SUMMARY_IN, FakeHistorian
from pcbk_core.data.historian import Row

BENCH_FIXTURE = Path(__file__).resolve().parent / "data" / "bench-historian.json"
TZ = timezone(timedelta(hours=5))               # пояс историана двойника
LIVE_AGE = timedelta(seconds=20)                # метка живого значения — «сейчас − 20 с»
CATALOG_COLUMNS = ("TagName", "Description", "TagType", "MinEU", "MaxEU", "Unit")
OUTSIDE_PREFIX = "16FAKE_"                      # вне белого списка стенда отбора (C2)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TwinHistorian(FakeHistorian):
    """Историан стенда отбора: catalog — строки каталога (TagName, Description, TagType, MinEU, MaxEU, Unit);
    values — имя → (Value, Quality); summary — имя → столбцы сводки. now — часы UTC (подмена в тестах)."""

    def __init__(self, catalog: list[Row], values: Mapping[str, tuple[float | None, int]],
                 summary: Mapping[str, Mapping[str, object]], now: Callable[[], datetime] = _utc_now):
        super().__init__()
        self.catalog: list[Row] = [tuple(r) for r in catalog]
        self.values: dict[str, tuple[float | None, int]] = dict(values)
        self.summary = {name: dict(cols) for name, cols in summary.items()}
        self.now = now
        self.live, self.clock = {}, []          # постоянные ответы FakeHistorian здесь не действуют

    @classmethod
    def from_fixture(cls, path: Path | str = BENCH_FIXTURE) -> "TwinHistorian":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        catalog = [tuple(row[c] for c in CATALOG_COLUMNS) for row in data["catalog"]]
        values = {name: (v["Value"], v.get("Quality", 0)) for name, v in data["live"].items()}
        return cls(catalog, values, data["summary"])

    def _local_now(self) -> datetime:
        """«Сейчас» историана — местное время без пояса, как его отдаёт pytds."""
        return self.now().astimezone(TZ).replace(tzinfo=None)

    def _live_row(self, name: str, stamp: datetime) -> Row:
        value, quality = self.values[name]
        return (name, stamp, value, quality)

    def _answer(self, sql: str) -> list[Row]:
        if _CLOCK.fullmatch(sql):
            utc = self.now().astimezone(timezone.utc)
            return [(utc.astimezone(TZ).replace(tzinfo=None), utc.replace(tzinfo=None))]
        if _CATALOG.fullmatch(sql):
            return list(self.catalog)
        stamp = self._local_now() - LIVE_AGE
        if _LIVE_ALL.fullmatch(sql):
            return [self._live_row(name, stamp) for name, (value, _) in self.values.items() if value is not None]
        if _LIVE_IN.fullmatch(sql):
            names = [lit.replace("''", "'") for lit in _LIT.findall(sql)]
            return [self._live_row(name, stamp) for name in names if name in self.values]
        if m := _SUMMARY_IN.fullmatch(sql):
            columns = m.group("columns").split(", ")
            names = [lit.replace("''", "'") for lit in _LIT.findall(m.group("names"))]
            rows = []
            for name in names:
                if name not in self.summary:
                    continue
                # Last и LastDateTime (HAS_LAST, Д3б) — живое значение и его метка
                row = {"Last": self.values.get(name, (None, 0))[0], "LastDateTime": stamp, **self.summary[name]}
                if not all(c in row for c in columns):
                    raise AssertionError("двойник историана: столбец сводки вне фикстуры")
                rows.append((name, *(row[c] for c in columns)))
            return rows
        raise AssertionError("двойник историана: SQL вне шаблонов службы")


def bench_whitelist() -> frozenset[str]:
    """Белый список стенда отбора: все теги фикстуры, кроме 16FAKE_* (C2)."""
    return frozenset(row[0] for row in TwinHistorian.from_fixture(BENCH_FIXTURE).catalog
                     if not row[0].startswith(OUTSIDE_PREFIX))
