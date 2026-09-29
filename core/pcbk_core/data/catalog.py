"""Каталог тегов историана в памяти: свойства тегов, счётчики белого списка, набор свежести.

Каталог строится из двух ответов: catalog_sql() (Tag + AnalogTag +
EngineeringUnit) и live_all_sql() (Live). В нём все теги историана, не только
белого списка: Д3б по нему отличает «вне списка» от «нет такого тега». Поиск и
разбор имён — Д3б.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .historian import Row

Kind = Literal["analog", "discrete"]
# Tag.TagType; прочие типы (строковые, событийные) каталог не держит
KIND_BY_TYPE: dict[Any, Kind] = {1: "analog", 2: "discrete"}
# «единица не задана»: в историане это литерал None (у 780 тегов), бывают и пустые строки
_NO_UNIT = frozenset({"", "none", "null", "-"})


@dataclass(frozen=True, slots=True)
class TagInfo:
    name: str
    description: str
    kind: Kind
    unit: str | None
    min_eu: float | None
    max_eu: float | None
    live: bool                          # в Live есть строка с Value IS NOT NULL
    live_time: datetime | None          # метка этой строки, время историана без пояса


def _unit(value: Any) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return None if text.lower() in _NO_UNIT else text


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Catalog:
    """Снимок каталога. Счётчики — числа, без имён: они идут в журнал и на ручки здоровья.

    total — тегов в историане (аналоговых и дискретных); whitelisted — имён белого
    списка, найденных в каталоге; missing — имён списка, которых в каталоге нет;
    live — живых среди whitelisted.
    """

    def __init__(self, tags: Mapping[str, TagInfo], whitelist: frozenset[str], *, loaded: bool = True):
        self._tags: dict[str, TagInfo] = dict(tags)
        self.whitelist = whitelist
        self.loaded = loaded
        listed = [self._tags[n] for n in whitelist if n in self._tags]
        self.total = len(self._tags)
        self.whitelisted = len(listed)
        self.missing = len(whitelist) - len(listed)
        self.live = sum(1 for t in listed if t.live)
        fresh = sorted((t for t in listed if t.kind == "analog" and t.live and t.live_time is not None),
                       key=lambda t: t.name)
        # сортировка устойчива: при равной метке остаётся порядок по имени
        self._freshest = tuple(t.name for t in sorted(fresh, key=lambda t: t.live_time, reverse=True))

    @classmethod
    def from_rows(cls, tag_rows: list[Row], live_rows: list[Row], whitelist: frozenset[str]) -> "Catalog":
        """tag_rows: (TagName, Description, TagType, MinEU, MaxEU, Unit); live_rows: (TagName, DateTime, Value, Quality).

        Тег с чужим TagType пропускается; при повторе имени берётся первая строка, в Live — самая новая метка.
        """
        stamps: dict[str, datetime | None] = {}
        for name, stamp, value, *_ in live_rows:
            if value is None:
                continue
            stamp = stamp if isinstance(stamp, datetime) else None
            prev = stamps.get(name)
            if name not in stamps or (stamp is not None and (prev is None or stamp > prev)):
                stamps[name] = stamp
        tags: dict[str, TagInfo] = {}
        for name, description, tag_type, min_eu, max_eu, unit, *_ in tag_rows:
            kind = KIND_BY_TYPE.get(tag_type)
            if kind is None or not isinstance(name, str) or name in tags:
                continue
            tags[name] = TagInfo(
                name=name, description=description.strip() if isinstance(description, str) else "",
                kind=kind, unit=_unit(unit),
                min_eu=_float(min_eu), max_eu=_float(max_eu), live=name in stamps, live_time=stamps.get(name))
        return cls(tags, whitelist)

    @classmethod
    def empty(cls) -> "Catalog":
        return cls({}, frozenset(), loaded=False)

    def freshest(self, k: int = 8) -> tuple[str, ...]:
        """Аналоговые живые теги белого списка по убыванию метки Live, не больше k."""
        return self._freshest[:max(k, 0)]
