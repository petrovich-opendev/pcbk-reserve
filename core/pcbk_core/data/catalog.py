"""Каталог тегов историана в памяти: свойства тегов, счётчики белого списка, набор свежести.

Каталог строится из двух ответов: catalog_sql() (Tag + AnalogTag +
EngineeringUnit) и live_all_sql() (Live). В нём все теги историана, не только
белого списка: разбор имён отличает «вне списка» от «нет такого тега». Свойства
тега, поиск и похожие имена отдаются только по белому списку.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .historian import Row
from .sql import SAFE_NAME

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


LookupStatus = Literal["ok", "unlisted", "unknown"]
SIMILAR_LOOKUP = 5              # похожих имён у «нет такого тега»


@dataclass(frozen=True)
class Lookup:
    """Разбор одного имени: info — только у ok; similar — только у unknown (до 5 имён белого списка)."""

    status: LookupStatus
    info: TagInfo | None
    similar: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    """matches — не больше limit; similar — точных совпадений нет, это похожие; total — найдено всего."""

    matches: tuple[TagInfo, ...]
    similar: bool
    total: int


def norm(text: str) -> str:
    """Для сравнения: нижний регистр, «ё» → «е», пробелы схлопнуты."""
    return " ".join(text.lower().replace("ё", "е").split())


def _stem(key: str) -> str | None:
    """Основа имени — без последнего _ХВОСТА; у имени без «_» основы нет."""
    head, sep, _ = key.rpartition("_")
    return head if sep and head else None


# Д19: хвосты привода/состояния белого списка (docs/research/05 §3.5) — основа с хотя бы одним из них — агрегат
AGGREGATE_TAILS = frozenset({"RUN", "LOD", "SPD", "CUR", "OP", "OPN", "CLS", "ON", "OFF", "STOP", "FLT", "ALM"})
_LEADING_DIGITS = re.compile(r"^[0-9]+")


def _split_tail(name: str) -> tuple[str, str] | None:
    """(основа, ХВОСТ верхним регистром) имени; у имени без «_» основы нет (как _stem)."""
    head, sep, tail = name.rpartition("_")
    return (head, tail.upper()) if sep and head else None


def _group_of(code: str) -> str:
    """Группа агрегата (Д19, план): ведущие цифры кода («21FAKE30» → «21»), иначе префикс до первого «_»
    («TVF_030» → «TVF»), иначе код целиком."""
    digits = _LEADING_DIGITS.match(code)
    if digits:
        return digits.group(0)
    head, sep, _ = code.partition("_")
    return head if sep else code


@dataclass(frozen=True, slots=True)
class Unit:
    """Агрегат (Д19) — основа тега, у которой в белом списке есть хотя бы один тег привода или состояния
    (AGGREGATE_TAILS). signals — хвосты всех её тегов белого списка, отсортированные; description — описание
    тега «_RUN», иначе первого тега по имени; run_tag — имя тега «_RUN», если есть (для «сколько в работе»)."""

    code: str
    description: str
    signals: tuple[str, ...]
    group: str
    run_tag: str | None


@dataclass(frozen=True, slots=True)
class EquipmentMatch:
    """Ответ Catalog.equipment (Д19): units — ВСЕ агрегаты белого списка, подходящие запросу, без обрезки на
    вывод (её делает служба); measure_points_total — число подходящих запросу точек измерения (основ без тега
    привода/состояния) — отдельно, это не агрегаты."""

    units: tuple[Unit, ...]
    measure_points_total: int


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
        # разбор имён: регистр не важен, каноническое имя — из каталога (из совпавших без регистра — первое)
        self._by_key: dict[str, str] = {}
        for name in self._tags:
            self._by_key.setdefault(name.upper(), name)
        # поиск и похожие — только по белому списку
        self._listed: tuple[TagInfo, ...] = tuple(sorted(listed, key=lambda t: t.name))
        self._hay: dict[str, str] = {t.name: norm(f"{t.name} {t.description}") for t in self._listed}

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

    def lookup(self, raw: str) -> Lookup:
        """Имя от вызывающего: пробелы по краям снимаются, регистр не важен.

        ok — тег белого списка есть в каталоге; unlisted — тег есть, но вне списка (о нём ни слова);
        unknown — в каталоге нет (и имя списка, которого нет в историане), similar — до 5 имён
        белого списка с той же основой или содержащих введённое.
        """
        key = raw.strip().upper()
        name = self._by_key.get(key)
        if name is None:
            return Lookup("unknown", None, self._similar_names(key))
        if name not in self.whitelist:
            return Lookup("unlisted", None, ())
        return Lookup("ok", self._tags[name], ())

    def _similar_names(self, key: str) -> tuple[str, ...]:
        if not key:
            return ()
        stem = _stem(key)
        same = [t.name for t in self._listed if stem is not None and _stem(t.name.upper()) == stem]
        inside = [t.name for t in self._listed if key in t.name.upper() and t.name not in same]
        return tuple((same + inside)[:SIMILAR_LOOKUP])

    def search(self, query: str, limit: int = 10) -> SearchResult:
        """Теги белого списка, у которых все слова запроса есть в norm(имя + описание).

        Порядок: точное имя, имя с начала, затем по имени. Ничего не нашлось — похожие
        (similar=True): теги хотя бы с одним словом по убыванию числа слов, а у запроса,
        похожего на имя, ещё теги с той же основой. Пустой запрос ничего не находит.
        """
        q = norm(query)
        words = q.split()
        limit = max(limit, 0)
        found = [t for t in self._listed if words and all(w in self._hay[t.name] for w in words)]
        if found:
            found.sort(key=lambda t: (0 if norm(t.name) == q else 1 if norm(t.name).startswith(q) else 2, t.name))
            return SearchResult(tuple(found[:limit]), False, len(found))
        scored = [(sum(w in self._hay[t.name] for w in words), t) for t in self._listed]
        similar = [t for n, t in sorted(scored, key=lambda p: (-p[0], p[1].name)) if n > 0]
        raw = query.strip()
        stem = _stem(raw.upper()) if SAFE_NAME.fullmatch(raw) and "_" in raw else None
        if stem is not None:
            seen = {t.name for t in similar}
            similar += [t for t in self._listed if _stem(t.name.upper()) == stem and t.name not in seen]
        return SearchResult(tuple(similar[:limit]), True, len(similar))

    def equipment(self, query: str) -> EquipmentMatch:
        """Агрегаты и точки измерения белого списка по словам запроса (Д19): основы (имя без хвоста),
        сгруппированные по тегам белого списка. Основа подходит запросу, если все слова запроса есть в
        norm(имя + описание) хотя бы одного её тега — тот же разбор, что у search(). Пустой запрос ничего не
        находит, как у search(). Основа с хотя бы одним тегом AGGREGATE_TAILS — агрегат (Unit); иначе —
        точка измерения (считается, в units не входит)."""
        words = norm(query).split()
        if not words:
            return EquipmentMatch((), 0)
        by_stem: dict[str, list[tuple[TagInfo, str]]] = {}
        for t in self._listed:
            split = _split_tail(t.name)
            if split is None:
                continue
            stem, tail = split
            by_stem.setdefault(stem, []).append((t, tail))
        units: list[Unit] = []
        measure_points = 0
        for stem, pairs in by_stem.items():
            pairs.sort(key=lambda p: p[0].name)
            if not any(all(w in self._hay[t.name] for w in words) for t, _ in pairs):
                continue
            if not any(tail in AGGREGATE_TAILS for _, tail in pairs):
                measure_points += 1
                continue
            tails = tuple(sorted(tail for _, tail in pairs))
            run = next((t.name for t, tail in pairs if tail == "RUN"), None)
            description = next((t.description for t, tail in pairs if tail == "RUN"), pairs[0][0].description)
            units.append(Unit(code=stem, description=description, signals=tails, group=_group_of(stem),
                              run_tag=run))
        units.sort(key=lambda u: u.code)
        return EquipmentMatch(tuple(units), measure_points)
