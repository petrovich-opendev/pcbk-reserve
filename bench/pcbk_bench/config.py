"""Конфиг прогона отбора (Д8-R71, Д8-R74, П4): места и их модели, повторы, потолок и запас, начало окна, пути.

Файл — JSON. Обязательные ключи: run_id, places ({"7": "pcbk/z-ai/glm-5.3", …}), reps ({"create", "number",
"trap"}), cap_usd, stop_margin_usd, window_start (ISO с поясом или null), out_dir, cards. Необязательные: stand (путь к
stand.json — адрес сервера и туннелей, П4), turn_timeout_s, heartbeat_s, run_timeout_s (по группам, недостающие — из
RUN_TIMEOUT_S), projection_ack, archive_reserve_s, scenarios. Пути — относительно каталога файла конфига.

- scenarios (П14) — фильтр плана: список разных id сценариев (сухой прогон Б4 — ["B3"], слой 0 Б5 — ["B1"]); нет
  ключа или null — все двенадцать. Без сценариев агента bench-uzel прогонщик его не сохраняет (bench_tags может быть
  пуст).
- Режим нагрузки (Д11-R119, R131): "load": true, "load_data" — none (без данных: только сценарии без агента bench —
  A1–A4, B3, C2), live (с данными по «да») или twin (только стенд); поля базы окна WINDOW_BASE_KEYS — swap_base_mib и
  dify_swap_base_mib (МиБ, от 0), swap_grow_mib, dify_swap_max_mib, mem_fail_mib (МиБ, от 1), psi_full_max (%, больше
  0, не больше 100) — по желанию (null — умолчание сторожа памяти, базы — первое прочитанное), но CLI run в режиме
  нагрузки без любого из них отказывает RunRefused("window_base"). load_data и поля базы окна без load: true —
  ValueError. В режиме нагрузки одна модель может стоять на многих местах.

- Ссылка модели места — только из model_refs(student/config/opencode.json): «pcbk/<модель>» или «pcbk-or/<модель>»;
  одна модель — одно место (m5): иначе в таблице не различить моделей (кроме режима нагрузки).
- window_start — только с поясом (m-р4-2): без пояса — ValueError. null допустим в конфиге (двойник А6, тесты), но CLI
  run без него отказывает RunRefused("window_start").
- out_dir null — «задаёт тот, кто строит прогонщик» (config.twin.json: А6 подставляет свой каталог); Runner без
  out_dir не строится. Так забытое переопределение не пишет прогон в дерево репозитория.

Тексты ValueError называют поле, но не значение.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .scenarios import BY_ID, RUN_TIMEOUT_S, SCENARIO_IDS

RUN_ID_RE = re.compile(r"[a-z0-9-]{1,32}")          # RUN_ID_RE LlmSettings (Д8-R69) — значением
PLACE_MAX = 11                                       # Д15б: мест 11, не 10 — единственный источник границы
PLACES = range(1, PLACE_MAX + 1)
PROVIDERS = ("pcbk", "pcbk-or")
GROUPS = ("create", "number", "trap")
MAX_REPS = 10
LOAD_DATA_MODES = ("none", "live", "twin")
WINDOW_BASE_KEYS = ("swap_base_mib", "swap_grow_mib", "dify_swap_base_mib", "dify_swap_max_mib", "mem_fail_mib",
                    "psi_full_max")
MAX_MIB = 1_048_576                                  # предел LLM_RUN_* МиБ тормоза core (R117) — значением

_REQUIRED = frozenset({"run_id", "places", "reps", "cap_usd", "stop_margin_usd", "window_start", "out_dir", "cards"})
_OPTIONAL = frozenset({"stand", "turn_timeout_s", "heartbeat_s", "run_timeout_s", "projection_ack",
                       "archive_reserve_s", "scenarios", "load", "load_data", *WINDOW_BASE_KEYS})


@dataclass(frozen=True)
class BenchConfig:
    run_id: str
    places: dict[int, str]
    reps: dict[str, int]
    cap_usd: float
    stop_margin_usd: float
    window_start: datetime | None
    out_dir: Path | None
    cards: Path
    turn_timeout_s: float = 900.0
    heartbeat_s: float = 30.0
    run_timeout_s: dict[str, float] = field(default_factory=lambda: dict(RUN_TIMEOUT_S))
    projection_ack: bool = False
    archive_reserve_s: float = 120.0
    stand: Path | None = None
    scenarios: tuple[str, ...] | None = None        # фильтр плана (П14); None — все сценарии
    load: bool = False                              # режим нагрузки (Д11-R119)
    load_data: str | None = None                    # none | live | twin (R131); только с load
    swap_base_mib: int | None = None                # поля базы окна (R119); None — умолчание сторожа памяти
    swap_grow_mib: int | None = None
    dify_swap_base_mib: int | None = None
    dify_swap_max_mib: int | None = None
    mem_fail_mib: int | None = None
    psi_full_max: float | None = None


def model_refs(opencode: Mapping[str, Any]) -> frozenset[str]:
    """Ссылки «провайдер/модель» мест из opencode.json образа: провайдеры pcbk и pcbk-or из enabled_providers."""
    enabled = opencode.get("enabled_providers")
    providers = opencode.get("provider")
    refs: set[str] = set()
    if not isinstance(providers, Mapping):
        return frozenset()
    for pid, prov in providers.items():
        if pid not in PROVIDERS or (isinstance(enabled, list) and pid not in enabled):
            continue
        models = prov.get("models") if isinstance(prov, Mapping) else None
        if isinstance(models, Mapping):
            refs.update(f"{pid}/{mid}" for mid in models)
    return frozenset(refs)


def _number(raw: dict, key: str) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"конфиг: {key} — число")
    return float(value)


def _positive(raw: dict, key: str, default: float) -> float:
    if key not in raw:
        return default
    value = _number(raw, key)
    if value <= 0:
        raise ValueError(f"конфиг: {key} — больше нуля")
    return value


def parse_window_start(value: object) -> datetime | None:
    """ISO 8601 с поясом или None; без пояса — ValueError (m-р4-2)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("конфиг: window_start — строка ISO 8601 с поясом или null")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("конфиг: window_start — ISO 8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("конфиг: window_start — только с поясом")
    return parsed


def _places(raw: object, refs: frozenset[str], load: bool = False) -> dict[int, str]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("конфиг: places — непустой объект «место: модель»")
    out: dict[int, str] = {}
    for key, ref in raw.items():
        if not isinstance(key, str) or not key.isdecimal() or int(key) not in PLACES:
            raise ValueError("конфиг: places — номера мест от 1 до 11")
        n = int(key)
        if n in out:
            raise ValueError("конфиг: places — место названо дважды")
        if not isinstance(ref, str) or ref not in refs:
            raise ValueError("конфиг: places — модель не из opencode.json места (pcbk/… или pcbk-or/…)")
        out[n] = ref
    if not load and len({ref.split("/", 1)[1] for ref in out.values()}) != len(out):    # и pcbk, и pcbk-or (р1 Б2)
        raise ValueError("конфиг: places — одна модель на двух местах")
    return dict(sorted(out.items()))


def _reps(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict) or set(raw) != set(GROUPS):
        raise ValueError("конфиг: reps — ровно create, number, trap")
    out = {}
    for g in GROUPS:
        v = raw[g]
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= MAX_REPS:
            raise ValueError(f"конфиг: reps.{g} — целое от 1 до {MAX_REPS}")
        out[g] = v
    return out


def _run_timeouts(raw: object) -> dict[str, float]:
    out = dict(RUN_TIMEOUT_S)
    if raw is None:
        return out
    if not isinstance(raw, dict) or not set(raw) <= set(GROUPS):
        raise ValueError("конфиг: run_timeout_s — только create, number, trap")
    for g, v in raw.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"конфиг: run_timeout_s.{g} — больше нуля")
        out[g] = float(v)
    return out


def _scenarios(raw: object) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if (not isinstance(raw, list) or not raw or not all(isinstance(s, str) and s in SCENARIO_IDS for s in raw)
            or len(set(raw)) != len(raw)):
        raise ValueError("конфиг: scenarios — непустой список разных id сценариев (A1–C4) или null")
    return tuple(raw)


def _load_fields(raw: dict, scenarios: tuple[str, ...] | None) -> dict:
    """Ключи режима нагрузки (R119, R131): load, load_data и поля базы окна; без load: true — ни одного из них."""
    load = raw.get("load", False)
    if not isinstance(load, bool):
        raise ValueError("конфиг: load — true или false")
    data = raw.get("load_data")
    given = [k for k in WINDOW_BASE_KEYS if raw.get(k) is not None]
    if not load:
        if data is not None or given:
            raise ValueError("конфиг: load_data и поля базы окна — только с load: true")
        return {}
    if data not in LOAD_DATA_MODES:
        raise ValueError("конфиг: load_data — none, live или twin (режим нагрузки)")
    if data == "none" and (scenarios is None or any(BY_ID[s].agent == "bench" for s in scenarios)):
        raise ValueError("конфиг: load_data none — сценарий использует агент bench (без данных — только A1–A4, B3, C2)")
    out: dict = {"load": True, "load_data": data}
    for key in given:
        value = raw[key]
        if key == "psi_full_max":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 100:
                raise ValueError("конфиг: psi_full_max — больше 0, не больше 100")
            out[key] = float(value)
            continue
        low = 0 if key in ("swap_base_mib", "dify_swap_base_mib") else 1
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= MAX_MIB:
            raise ValueError(f"конфиг: {key} — целое МиБ от {low} до {MAX_MIB}")
        out[key] = value
    return out


def _path(base: Path, raw: dict, key: str, required: bool) -> Path | None:
    value = raw.get(key)
    if value is None:
        if required:
            raise ValueError(f"конфиг: {key} — путь")
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"конфиг: {key} — путь")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: Path, opencode_json: Path) -> BenchConfig:
    """Конфиг прогона из JSON; ссылки моделей сверяются с opencode.json образа места. Ошибка — ValueError."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"конфиг: не JSON (строка {exc.lineno})") from None
    if not isinstance(raw, dict):
        raise ValueError("конфиг: нужен объект")
    unknown = set(raw) - _REQUIRED - _OPTIONAL
    if unknown:
        raise ValueError(f"конфиг: неизвестные ключи ({len(unknown)})")
    missing = _REQUIRED - set(raw)
    if missing:
        raise ValueError(f"конфиг: нет ключей ({', '.join(sorted(missing))})")
    if not isinstance(raw["run_id"], str) or not RUN_ID_RE.fullmatch(raw["run_id"]):
        raise ValueError("конфиг: run_id — [a-z0-9-], от 1 до 32 знаков")
    refs = model_refs(json.loads(Path(opencode_json).read_text(encoding="utf-8")))
    cap = _number(raw, "cap_usd")
    margin = _number(raw, "stop_margin_usd")
    if cap <= 0:
        raise ValueError("конфиг: cap_usd — больше нуля")
    if not 0 <= margin < cap:
        raise ValueError("конфиг: stop_margin_usd — от нуля и меньше cap_usd")
    timeouts = _run_timeouts(raw.get("run_timeout_s"))
    reserve = _number(raw, "archive_reserve_s") if "archive_reserve_s" in raw else 120.0
    if not 0 <= reserve < timeouts["create"]:
        raise ValueError("конфиг: archive_reserve_s — от нуля и меньше срока прогона create")
    ack = raw.get("projection_ack", False)
    if not isinstance(ack, bool):
        raise ValueError("конфиг: projection_ack — true или false")
    base = path.resolve().parent
    scenarios = _scenarios(raw.get("scenarios"))
    load = _load_fields(raw, scenarios)
    return BenchConfig(
        run_id=raw["run_id"], places=_places(raw["places"], refs, load=bool(load)), reps=_reps(raw["reps"]),
        cap_usd=cap, stop_margin_usd=margin, window_start=parse_window_start(raw["window_start"]),
        out_dir=_path(base, raw, "out_dir", required=False), cards=_path(base, raw, "cards", required=True),
        turn_timeout_s=_positive(raw, "turn_timeout_s", 900.0), heartbeat_s=_positive(raw, "heartbeat_s", 30.0),
        run_timeout_s=timeouts, projection_ack=ack, archive_reserve_s=reserve,
        stand=_path(base, raw, "stand", required=False), scenarios=scenarios, **load)


__all__ = ["BenchConfig", "GROUPS", "LOAD_DATA_MODES", "MAX_REPS", "PLACE_MAX", "PLACES", "PROVIDERS", "RUN_ID_RE",
           "WINDOW_BASE_KEYS", "load_config", "model_refs", "parse_window_start"]
