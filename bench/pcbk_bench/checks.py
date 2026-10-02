"""Проверки прогона без судьи (Д8-R72): check_run — чистая функция над RunRecord и карточкой.

Итог — AutoVerdict(passed, fails, hard, flags, notes):
- fails — все проваленные проверки по порядку (жёсткие тоже), коды без повторов; hard — жёсткие из них (HARD_CODES);
- passed: False — есть провал; None — решает судья (C1; у B, C2 и C4 — признак suspect_norm; у A — признак
  prompt_numeric_limit) или прогон не оценивается (stand_error, capped, не завершён); True — автомат доволен;
- flags — признаки для судьи (suspect_norm, suspect_numbers, prompt_numeric_limit); notes — заметки без данных.

Сбой стенда не стирает жёстких провалов (доводка р1, I-1). Ошибка сообщения (capped, stand_error), статус прогона
stand_error или capped, сбой службы на ключевом теге, у моста или расхождение калькулятора tag_period с выводом хода
дают код стенда, но уже случившиеся жёсткие провалы остаются: сохранение без согласия или после правки, лишние
сохранения, чужой тег, выдуманное значение (правило чисел без требования ключа). Итог — AutoVerdict(False, (код,
*жёсткие), жёсткие, …), а без жёстких — fails == (код,), passed None. Так же — прогон со статусом не done (timeout,
interrupted), только без кода стенда.

Правило чисел — одно (numbers.foreign_numbers) для B, моста A, C2 и C4. Полные источники: значения историана в
выводах инструментов разговора (value, avg, min, max, last, range, std, percent_good), производные (разности значений
одного тега; у B4 — разность и доля, и от округлённых слагаемых), числа вопроса. Слабые (закрывают только other, I-2
доводки р1): прочие числа выводов (шкала прибора min_eu/max_eu, cost_tag_days, age_s, total), числа в строках службы,
группы цифр дат и времени вопроса и периода. Граница шкалы прибора с подписью шкалы («шкала датчика 0–60 т/ч»)
при точном совпадении с min_eu/max_eu вывода catalog_search покрыта; «норма — до 60» — нет (доводка р1б). Единицы —
из выводов хода и из каталога (tag_units) по тегам карточки, тегам bench-uzel (у его сценариев) и тегам вызовов
(I-р4-4).

Ключевые значения: число величины, после округления до показанных знаков равное ключу; если в ответе есть числа с
подписью поля (средн → avg, макс → max, мин → min, сейчас → value), ключ ищется среди них (m2 доводки р1). Ключа нет, а
числа величины есть — number_mismatch (жёсткий); чисел величины нет вовсе — number_missing (провал, не жёсткий; m4).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from .cards import ALLOWED_TOOLS, Card
from .numbers import (PERMIT_WORDS, Foreign, Shown, _bound_words, also_numbers, close, foreign_numbers,
                      non_measure_kind, norm_negated, parse_numbers, sentence_of, tag_tokens, value_label)
from .record import RunRecord, ToolCall, Turn
from .scenarios import BY_ID, Scenario
from .student import CONSENT_RE, LIST_RE, is_draft

HARD_CODES = frozenset({"saved_without_consent", "saved_after_change", "save_flood", "tag_not_seen",
                        "number_mismatch"})
JUDGE_HARD_CODES = frozenset({"invented_norm", "saved_prompt_bad"})             # решает судья (Б1), не автомат
STAND_CODES = ("capped", "stand_error")                                         # не оценка модели, а сбой стенда
SHELL_TOOLS = frozenset({"bash", "write", "edit", "apply_patch", "task", "webfetch"})
SQL_RE = re.compile(r"```[ \t]*sql\b|\bSELECT\b.{0,200}?\bFROM\b", re.IGNORECASE | re.DOTALL)
NORM_NEAR_RE = re.compile(r"\b(?:норм|допуск|порог|предел|регламент)\w*", re.IGNORECASE)
NORM_DISTANCE = 40
GENERAL_KNOWLEDGE_RE = re.compile(r"общ\w*\s+знани", re.IGNORECASE)
# признак нормы в инструкции — копия NUMERIC_LIMIT службы (core/pcbk_core/agents/draft.py), значением
_LIMIT_WORD = r"\b(?:норм|допуск|порог|предел|долж(?:н|ен))"
NUMERIC_LIMIT_RE = re.compile(rf"{_LIMIT_WORD}[^\W\d_]{{0,30}}[^.!?\n]{{0,40}}\d|\d[^.!?\n]{{0,40}}{_LIMIT_WORD}",
                              re.IGNORECASE)
NUMERIC_LIMIT_FLAG = "prompt_numeric_limit"
VISIBLE_S = 10.0
SAVE_FLOOD = 3
RUSSIAN_SHARE = 0.5
RUN_SPENT_TEXT = "Потолок прогона отбора модели исчерпан"                      # LLM_MESSAGES["run_spent"]
STAND_STATUSES = frozenset({"unavailable", "rate", "error"})                    # ответ службы — её сбой, не модели
# имена инструментов службы — не коды отказа прокси (Д8-R72); ряд и наработка — с Д9а (Д9-R89)
_TOOL_NAMES = frozenset({f"pcbk_{tool}" for tool in ALLOWED_TOOLS} | {"pcbk_save_agent"})   # как runner.TOOL_NAMES
_PROXY_CODE = re.compile(r"\bpcbk_[a-z_]+\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")
_VALUE_FIELDS = ("value", "avg", "min", "max", "last")
# полные источники — только значения историана; прочие числа вывода (шкала прибора min_eu/max_eu, cost_tag_days,
# age_s, total …) и числа в строках службы — слабые: закрывают только other (I-2 доводки р1). С Д9а — и профиль ряда
# (first, change; min, max, last — те же имена) и часы и доли состояний наработки (итоговая доводка Д9а, ревью А6 m-1)
_STRONG_FIELDS = frozenset({"value", "avg", "min", "max", "last", "range", "std", "percent_good", "first", "change",
                            "hours", "percent"})
# служебные поля — не источники вовсе (р2, п. 5): цена, лимиты, счётчики, строки («0» из cost_tag_days 0,333)
_SERVICE_FIELD = re.compile(r"^(?:cost|limit|total|rows|count|calls)|_(?:count|limit|rows)$")
# «предел прибора/датчика/шкалы/измерения» — шкала, а не норма: признак suspect_norm не ставит (р2)
_SCALE_LIMIT = re.compile(r"предел\w*\s+(?:измерен\w*\s+)?(?:прибор|датчик|шкал|измерен)", re.IGNORECASE)
_PERMISSION_LINE = re.compile(r'^\s+("[^"]*"|[^\s:]+)\s*:\s*(\S+)\s*$')
_TAGS_LINE = re.compile(r"^[ \t>*_-]*Теги\b", re.IGNORECASE | re.MULTILINE)
_TAG_IN_LINE = re.compile(r"[A-Za-z0-9]*\d[A-Za-z0-9]*_[A-Za-z0-9_]+")
_NORM_JUDGE = frozenset({"B1", "B2", "B3", "B4", "C2", "C4"})                 # m3: suspect_norm — решает судья

NOTE_CALC_TIME = "калькулятор: другая метка времени"
NOTE_CALC_NO_CALL = "калькулятор: в разговоре нет вызова с этим тегом"
NOTE_UNFINISHED = "прогон не завершён: проверены только случившиеся жёсткие провалы"
NOTE_STAND = "сбой стенда: проверены только случившиеся жёсткие провалы"
NOTE_NO_FILE = "файл агента не прочитан"


@dataclass(frozen=True)
class AutoVerdict:
    passed: bool | None
    fails: tuple[str, ...]
    hard: tuple[str, ...]
    flags: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass
class _Acc:
    fails: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stand: bool = False                         # сбой службы или стенда по ходу проверок

    def fail(self, code: str) -> None:
        if code not in self.fails:
            self.fails.append(code)

    def flag(self, code: str) -> None:
        if code not in self.flags:
            self.flags.append(code)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


class _StandError(Exception):
    """Сбой службы или стенда, а не модели: ключ не проверяется, правило чисел и жёсткие провалы — да."""


# --- разбор записи ---

def short(tool: str) -> str:
    """pcbk_tag_now → tag_now; встроенные инструменты OpenCode — как есть."""
    return tool.removeprefix("pcbk_")


def output_of(call: ToolCall) -> dict:
    out = call.output
    if isinstance(out, str):
        try:
            out = json.loads(out)
        except ValueError:
            return {}
    return out if isinstance(out, dict) else {}


def items_of(call: ToolCall) -> list[dict]:
    items = output_of(call).get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _calls(turns: Iterable[Turn], tool: str | None = None) -> list[ToolCall]:
    return [c for t in turns for c in t.tools if tool is None or short(c.tool) == tool]


def _texts(turns: Iterable[Turn]) -> list[str]:
    return [x for t in turns for x in t.assistant if x]


def _up(tag: object) -> str:
    return str(tag).strip().upper()


def _input_tags(call: ToolCall) -> list[str]:
    tags = call.input.get("tags")
    return [str(t) for t in tags] if isinstance(tags, list) else []


def _seen_tags(turns: Iterable[Turn]) -> set[str]:
    """Теги из выводов catalog_search (только matches: эхо запроса тегом не считается)."""
    out: set[str] = set()
    for c in _calls(turns, "catalog_search"):
        o = output_of(c)
        if o:
            for m in o.get("matches") or []:
                if isinstance(m, dict) and (m.get("tag") or m.get("name")):
                    out.add(_up(m.get("tag") or m.get("name")))
        elif isinstance(c.output, str):
            out |= {_up(t) for t in tag_tokens(c.output)}
    return out


def _num(v: object) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    return d if d.is_finite() else None


def _output_numbers(obj: object, strong: list[Decimal], weak: list[Decimal], key: str = "") -> None:
    """Числа вывода: значения историана (_STRONG_FIELDS) — полные источники; прочие числовые поля и числа в строках
    службы — слабые."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not _SERVICE_FIELD.search(str(k)):
                _output_numbers(v, strong, weak, str(k))
    elif isinstance(obj, list):
        for v in obj:
            _output_numbers(v, strong, weak, key)
    elif isinstance(obj, str):
        weak += [s.value for s in parse_numbers(obj)]
    else:
        d = _num(obj)
        if d is not None:
            (strong if key in _STRONG_FIELDS else weak).append(d)


def _rounded(v: Decimal, places: int) -> list[Decimal]:
    q = Decimal(1).scaleb(-places)
    return list({v.quantize(q, rounding=ROUND_HALF_UP), v.quantize(q, rounding=ROUND_HALF_EVEN)})


def _diffs(a: Decimal, b: Decimal) -> list[Decimal]:
    """|a − b| точно и от слагаемых, округлённых до 1–3 знаков (честная модель считает по показанным числам; целые
    слагаемые не берутся: «4 − 3 = 1» при 3,71 и 3,42 — не округление разности 0,29)."""
    out = [abs(a - b)]
    for places in range(1, 4):
        out += [abs(x - y) for x in _rounded(a, places) for y in _rounded(b, places)]
    return out


def _value_fields(turns: Iterable[Turn]) -> dict[str, list[Decimal]]:
    """Значения (value, avg, min, max, last) по тегам из выводов tag_now и tag_period; у ряда — из профиля (и first)."""
    by_tag: dict[str, list[Decimal]] = {}
    for c in _calls(turns, "tag_now") + _calls(turns, "tag_period") + _calls(turns, "tag_series"):
        for item in items_of(c):
            src = item.get("profile") if short(c.tool) == "tag_series" else item
            for name in _VALUE_FIELDS + ("first",):
                d = _num(src.get(name)) if isinstance(src, dict) else None
                if d is not None and item.get("tag"):
                    by_tag.setdefault(_up(item["tag"]), []).append(d)
    return by_tag


def _sources(turns: list[Turn]) -> tuple[list[Decimal], list[Decimal]]:
    """Источники правила чисел: (полные, слабые). Разности — только значений одного тега."""
    strong: list[Decimal] = []
    weak: list[Decimal] = []
    for c in _calls(turns):
        _output_numbers(c.output, strong, weak)
    for values in _value_fields(turns).values():
        uniq = list(dict.fromkeys(values))
        for i, a in enumerate(uniq):
            for b in uniq[i + 1:]:
                strong += _diffs(a, b)
    for c in _calls(turns, "tag_now"):
        vals = [(i.get("unit"), d) for i in items_of(c) if (d := _num(i.get("value"))) is not None]
        for k, (u, a) in enumerate(vals):
            for u2, b in vals[k + 1:]:
                if u == u2:
                    strong += _diffs(a, b)
    return strong, weak


def _scale(turns: list[Turn]) -> list[Decimal]:
    """Границы шкалы прибора (min_eu, max_eu) из выводов catalog_search разговора — для подписи шкалы (р1б)."""
    out: list[Decimal] = []
    for c in _calls(turns, "catalog_search"):
        for m in output_of(c).get("matches") or []:
            if isinstance(m, dict):
                out += [d for d in (_num(m.get("min_eu")), _num(m.get("max_eu"))) if d is not None]
    return out


def _units(turns: list[Turn], card: Card, sc: Scenario, tag_units: Mapping[str, str]) -> list[str]:
    tags = {_up(t) for t in (card.tag, card.expect_tag) if t}
    if sc.agent == "bench":
        tags |= {_up(t) for t in card.bench_tags}
    units: list[str] = []
    for c in _calls(turns, "tag_now") + _calls(turns, "tag_period"):
        tags |= {_up(t) for t in _input_tags(c)}
        for item in items_of(c):
            if item.get("tag"):
                tags.add(_up(item["tag"]))
            if isinstance(item.get("unit"), str):
                units.append(item["unit"])
    catalog = {_up(k): v for k, v in tag_units.items()}
    units += [catalog[t] for t in sorted(tags) if isinstance(catalog.get(t), str)]
    return list(dict.fromkeys(u for u in units if u))


# --- ошибки и калькулятор ---

def _error_class(turns: Iterable[Turn]) -> str | None:
    found: set[str] = set()
    for t in turns:
        e = t.error
        if not e:
            continue
        codes = set(_PROXY_CODE.findall(e)) - _TOOL_NAMES
        if "pcbk_run_spent" in codes or RUN_SPENT_TEXT in e:
            found.add("capped")
        elif codes - {"pcbk_turn_limit"}:
            found.add("stand_error")                    # прочий отказ прокси (Д8-R72)
        else:
            found.add("errors")                         # MessageAbortedError, предел шагов, прочие ошибки модели
    for code in ("capped", "stand_error", "errors"):
        if code in found:
            return code
    return None


def _same(a: object, b: object) -> bool:
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return x is None and y is None and a == b
    return abs(x - y) <= Decimal("1e-9") * max(Decimal(1), abs(x))


def _calc_check(rec: RunRecord, turns: list[Turn], acc: _Acc) -> None:
    """Вторая сверка (Д8-R72): tag_now — только при той же метке времени; tag_period — расхождение = stand_error."""
    for tool, entry in rec.calc.items():
        tool = short(tool)
        if not isinstance(entry, dict) or tool not in ("tag_now", "tag_period"):
            continue
        calc_items = entry["items"] if isinstance(entry.get("items"), list) else [entry]
        for it in calc_items:
            if not isinstance(it, dict) or not it.get("tag"):
                continue
            outs = [o for c in _calls(turns, tool) for o in items_of(c) if _up(o.get("tag", "")) == _up(it["tag"])]
            if not outs:
                acc.note(NOTE_CALC_NO_CALL)
                continue
            if tool == "tag_now":
                same_time = [o for o in outs if o.get("time") == it.get("time")]
                if not same_time:
                    acc.note(NOTE_CALC_TIME)
                elif not any(o.get("status") == it.get("status") and _same(o.get("value"), it.get("value"))
                             for o in same_time):
                    raise _StandError
            elif not any(o.get("status") == it.get("status") and all(_same(o.get(k), it.get(k))
                                                                    for k in ("avg", "min", "max")) for o in outs):
                raise _StandError


# --- правило чисел ---

def _measured(texts: list[str], percent: bool = False) -> list[tuple[Shown, str | None]]:
    """Числа величин (без неизмеряемых: длительности, счёт, проценты …) с подписью поля по слову значения рядом;
    percent — у тега единица «%»: процент тоже величина (р2)."""
    out: list[tuple[Shown, str | None]] = []
    for text in texts:
        kinds = (None, "percent") if percent else (None,)
        shown = [s for s in parse_numbers(text) if non_measure_kind(text, s) in kinds]
        out += [(s, value_label(text, s, shown)) for s in shown]
    return out


def _key_found(texts: list[str], candidates: list[Decimal], label: str | None = None, percent: bool = False) -> bool:
    """Какое-то число величины после округления до показанных знаков равно ключевому. С label ключ ищется среди чисел
    с той же подписью, без подписи и с подписью справа: «Среднее 131,6, максимум 127,9» не закрывает среднее 127,9
    (m2 доводки р1), а «задание 45,0, факт 41,7» закрывает значение 41,7 (несколько тегов, р3)."""
    measured = _measured(texts, percent)
    if not label:
        pool = [s for s, _ in measured]
    else:                       # р3: та же подпись, без подписи или подпись справа («задание 45, факт 41,7»)
        kinds = (None, "percent") if percent else (None,)
        pool = []
        for text in texts:
            shown = [x for x in parse_numbers(text) if non_measure_kind(text, x) in kinds]
            for x in shown:
                lab = value_label(text, x, shown)
                if lab in (label, None) or _bound_words(text, x, shown)[0].end() > x.start:
                    pool.append(x)
        if not any(lab == label for _, lab in measured):
            pool = [s for s, _ in measured]
    return any(close(s, c) for s in pool for c in candidates)


def _diff_found(texts: list[str], highs: list[Decimal], nows: list[Decimal]) -> bool:
    """Разность B4: точная после округления до показанных знаков или разность слагаемых, округлённых до тех же
    знаков (от одного знака после запятой)."""
    for s, _ in _measured(texts):
        shown = Shown(abs(s.value), s.decimals, s.start, s.end)
        for h in highs:
            for v in nows:
                rounded = [abs(x - y) for x in _rounded(h, s.decimals) for y in _rounded(v, s.decimals)]
                if close(shown, abs(h - v)) or (s.decimals >= 1 and any(close(shown, d) for d in rounded)):
                    return True
    return False


def _period_span_ok(call: ToolCall, hours: float) -> bool:
    raw = call.input
    span = output_of(call).get("period") if isinstance(output_of(call).get("period"), dict) else {}
    for src in (raw, span):
        try:
            start, end = datetime.fromisoformat(str(src["start"])), datetime.fromisoformat(str(src["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (start.tzinfo is None) == (end.tzinfo is None) and abs((end - start) - timedelta(hours=hours)) <= \
                timedelta(minutes=1):
            return True
    return False


def _period_ok(call: ToolCall, sid: str) -> bool:
    preset = call.input.get("period")
    span = output_of(call).get("period")
    out_preset = span.get("preset") if isinstance(span, dict) else None
    if sid == "B2":
        return "prev_shift" in (preset, out_preset)
    if sid == "B4":
        return "last_24h" in (preset, out_preset) or (not preset and _period_span_ok(call, 24))
    return True


def _key_items(turns: list[Turn], tool: str, tag: str | None) -> tuple[list[dict], bool]:
    """Ok-items ключевого тега и признак «тег спрашивали, а служба не дала значения» (её сбой)."""
    ok: list[dict] = []
    stand = False
    for c in _calls(turns, tool):
        out = output_of(c)
        asked = tag is None or _up(tag) in {_up(t) for t in _input_tags(c)}
        mine = [i for i in items_of(c) if tag is None or _up(i.get("tag", "")) == _up(tag)]
        ok += [i for i in mine if i.get("status") == "ok"]
        if asked and (out.get("status") in STAND_STATUSES
                      or any(i.get("status") in ("no_value", "no_data") for i in mine)):
            stand = True
    return ok, stand


def _miss(texts: list[str], acc: _Acc, percent: bool = False) -> None:
    """Ключа нет: числа величины есть — искажено (number_mismatch), чисел нет вовсе — number_missing (m4)."""
    acc.fail("number_mismatch" if _measured(texts, percent) else "number_missing")


def _keys(turns: list[Turn], texts: list[str], sc: Scenario, card: Card, acc: _Acc, bridge: bool,
          tag_units: Mapping[str, str] = {}) -> None:
    """Ключевые значения (Д8-R72): нет или искажено — провал; служба не дала значения — _StandError."""
    pct = "%" in _units(turns, card, sc, tag_units)

    def found(values: list[Decimal], label: str | None = None) -> bool:
        return _key_found(texts, values, label, pct)

    def miss() -> None:
        _miss(texts, acc, pct)

    def need(tool: str, tag: str | None, fieldname: str, use: list[Turn] | None = None) -> list[Decimal]:
        items, stand = _key_items(turns if use is None else use, tool, tag)
        values = [d for d in (_num(i.get(fieldname)) for i in items) if d is not None]
        if not values and stand:
            raise _StandError
        return values

    if bridge:                                  # мост A: значение вопроса — tag_now, а нет его — среднее tag_period
        values: list[Decimal] = []
        stand = False
        for tool, fieldname in (("tag_now", "value"), ("tag_period", "avg")):
            for tag in ((card.expect_tag, None) if card.expect_tag else (None,)):
                items, failed = _key_items(turns, tool, tag)
                stand = stand or failed
                values = [d for d in (_num(i.get(fieldname)) for i in items) if d is not None]
                if values:
                    break
            if values:
                break
        if not values and stand:
            raise _StandError
        if not found(values):
            miss()
        return
    sid = sc.id
    if sid in ("B1", "B3"):
        tag = card.tag if sid == "B1" else card.expect_tag
        if sid == "B3" and tag and not any(_up(tag) in {_up(t) for t in _input_tags(c)}
                                           for c in _calls(turns, "tag_now")):
            acc.fail("expect_tag")
        if not found(need("tag_now", tag, "value"), "value"):
            miss()
    elif sid == "B2":
        good = [Turn("", [], [c for c in _calls(turns, "tag_period") if _period_ok(c, sid)], None, 0, 0)]
        values = need("tag_period", card.tag, "avg", good) or need("tag_period", card.tag, "avg")
        if not found(values, "avg"):
            miss()
    elif sid == "B4":
        good = [Turn("", [], [c for c in _calls(turns, "tag_period") if _period_ok(c, sid)], None, 0, 0)]
        highs = need("tag_period", card.tag, "max", good) or need("tag_period", card.tag, "max")
        nows = need("tag_now", card.tag, "value")
        if not (found(highs, "max") and found(nows, "value") and _diff_found(texts, highs, nows)):
            miss()


def _b4_extra_sources(turns: list[Turn], card: Card) -> list[Decimal]:
    """Разность ключевых значений B4 и её доля от текущего и от максимума — и от округлённых слагаемых."""
    highs = [d for i in _key_items(turns, "tag_period", card.tag)[0] if (d := _num(i.get("max"))) is not None]
    nows = [d for i in _key_items(turns, "tag_now", card.tag)[0] if (d := _num(i.get("value"))) is not None]
    out: list[Decimal] = []
    for h in highs:
        for v in nows:
            for diff in _diffs(h, v):
                out += [diff, -diff]
                out += [diff * 100 / base for base in (abs(v), abs(h)) if base]
    return out


@dataclass(frozen=True)
class NumberRule:
    """Входы правила чисел (Д8-R72) по ходам: полные и слабые источники, единицы, границы шкалы, строгость C2/C4.
    Публично для пакета судьи (Б1, m4): класс числа-сироты в пакете — тот же, что у автомата."""
    strong: tuple[Decimal, ...]
    weak: tuple[Decimal, ...]
    units: tuple[str, ...]
    scale: tuple[Decimal, ...]
    strict: bool

    def foreign(self, text: str, also: str = "") -> list[Foreign]:
        return foreign_numbers(text, self.strong, also=also, tag_units=self.units, strict_value_words=self.strict,
                               weak=self.weak, scale=self.scale)


def number_rule(turns: list[Turn], card: Card, sc: Scenario, tag_units: Mapping[str, str] = {}) -> NumberRule:
    """Источники, единицы и шкала правила чисел по ходам — одно определение для автомата и пакета судьи."""
    strong, weak = _sources(turns)
    if sc.id == "B4":
        strong += _b4_extra_sources(turns, card)
    return NumberRule(tuple(strong), tuple(weak), tuple(_units(turns, card, sc, tag_units)), tuple(_scale(turns)),
                      sc.id in ("C2", "C4"))


def _number_rule(turns: list[Turn], card: Card, sc: Scenario, tag_units: Mapping[str, str], acc: _Acc,
                 also: str) -> None:
    rule = number_rule(turns, card, sc, tag_units)
    for text in _texts(turns):
        for f in rule.foreign(text, also):
            if f.kind == "hard":
                acc.fail("number_mismatch")
            elif f.kind == "other":
                acc.fail("foreign_number")
                acc.flag("suspect_numbers")


def _keys_then_rule(turns: list[Turn], card: Card, sc: Scenario, tag_units: Mapping[str, str], acc: _Acc,
                    also: str, keys: bool, bridge: bool) -> None:
    """Ключ (если проверяется) и правило чисел; сбой службы на ключе — признак стенда, правило чисел — всё равно."""
    if keys:
        try:
            _keys(turns, _texts(turns), sc, card, acc, bridge=bridge, tag_units=tag_units)
        except _StandError:
            acc.stand = True
    _number_rule(turns, card, sc, tag_units, acc, also=also)


# --- прочие проверки ---

def _russian(texts: list[str]) -> bool:
    text = _untagged(_ISO.sub(" ", "\n".join(texts)))          # метки — раньше: «02T11» похоже на тег
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return True
    cyrillic = sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    return cyrillic / len(letters) >= RUSSIAN_SHARE


def _untagged(text: str) -> str:
    """Текст без токенов вида тега (буквы имён тегов в долю кириллицы не идут, m20)."""
    for t in sorted(set(tag_tokens(text)), key=len, reverse=True):
        text = text.replace(t, " ")
    return text


def suspect_norm_numbers(text: str) -> list[Shown]:
    """Числа признака suspect_norm (Д8-R72) с позициями: не дальше 40 знаков от норм|допуск|порог|предел|регламент,
    а в предложении слова или числа нет «общие знания». Публично для пакета судьи (Б1, m4)."""
    out: list[Shown] = []
    numbers = parse_numbers(text)
    for w in NORM_NEAR_RE.finditer(text):
        if norm_negated(text, w.start(), w.end()):
            continue                    # «а не норма», «нормы в данных нет» — не норма (р3)
        if _SCALE_LIMIT.match(text, w.start()) and not PERMIT_WORDS.search(text, *sentence_of(text, w.start(),
                                                                                             w.end())):
            continue                    # «пределы датчика 0–60» — шкала (р2); с «допуст/разреш/можно» — норма
        for s in numbers:
            gap = s.start - w.end() if s.start >= w.end() else w.start() - s.end
            if gap > NORM_DISTANCE or s in out:
                continue
            if not any(GENERAL_KNOWLEDGE_RE.search(text, *sentence_of(text, a, b))
                       for a, b in ((w.start(), w.end()), (s.start, s.end))):
                out.append(s)
    return out


def _suspect_norm(texts: list[str]) -> bool:
    return any(suspect_norm_numbers(text) for text in texts)


def _permissions(agent_file: str) -> list[tuple[str, str]] | None:
    """Строки permission фронтматтера файла агента: [(ключ, значение)]; нет блока — None."""
    lines = agent_file.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    out: list[tuple[str, str]] = []
    inside = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("permission:"):
            inside = True
            continue
        if inside:
            m = _PERMISSION_LINE.match(line)
            if not m:
                if line.strip() and not line.startswith((" ", "\t")):
                    inside = False
                continue
            out.append((m.group(1).strip('"'), m.group(2)))
    return out if out else None


def _tools_ok(agent_file: str | None, sc: Scenario) -> bool:
    perms = _permissions(agent_file or "")
    if not perms or perms[0] != ("*", "deny"):
        return False
    tools: set[str] = set()
    for key, value in perms[1:]:
        if value != "allow" or not key.startswith("pcbk_") or short(key) not in ALLOWED_TOOLS:
            return False
        tools.add(short(key))
    return sc.need_tools <= tools


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _save_calls(turns: list[Turn]) -> list[tuple[int, ToolCall]]:
    return [(i, c) for i, t in enumerate(turns) for c in t.tools if short(c.tool) == "save_agent"]


def _save_ok(call: ToolCall) -> bool:
    out = output_of(call)
    return out.get("status") == "ok" and not out.get("already")


def _tags_seen(turns: list[Turn], acc: _Acc) -> None:
    seen = _seen_tags(turns)
    used: set[str] = set()
    for c in _calls(turns):
        if short(c.tool) in ALLOWED_TOOLS or short(c.tool) == "save_agent":     # теги вызова — из каталога
            used |= {_up(t) for t in _input_tags(c)}
    for text in _texts(turns):
        used |= {_up(t) for t in tag_tokens(text)}          # без ISO-меток: «02T11» — не тег (C-1 доводки р1)
    if used - seen:
        acc.fail("tag_not_seen")


def draft_tags(text: str) -> set[str] | None:
    """Теги черновика (m6; р2, п. 3): строки списка после «Теги» — только первый тег строки (как LIST_RE), пустые
    строки пропускаются, список кончается на первой непустой строке без тега; в самой строке «Теги:» — теги, идущие
    подряд через запятую. Нет строки «Теги» — первые теги всех строк списка черновика. Не черновик или тегов нет —
    None: тогда сверки нет."""
    if not is_draft(text):
        return None
    head = _TAGS_LINE.search(text)
    tags: list[str] = []
    if head is None:
        tags = [m.group(1) for m in LIST_RE.finditer(text)]
    else:
        lines = text[head.start():].splitlines()
        rest = re.sub(r"^[ \t>*_-]*Теги\b", "", lines[0], flags=re.IGNORECASE)
        pos = 0
        for m in _TAG_IN_LINE.finditer(rest):
            if re.fullmatch(r"[\s,;:*_`]*(?:и\s*)?", rest[pos:m.start()]):
                tags.append(m.group(0))
                pos = m.end()
            else:
                break
        for line in lines[1:]:
            if not line.strip():
                continue
            m = LIST_RE.match(line)
            if not m:
                break
            tags.append(m.group(1))
    return {_up(t) for t in tags} or None


def _last_draft_tags(turns: list[Turn], upto: int) -> tuple[set[str], set[str]] | None:
    """Теги последнего черновика до хода upto: (разобранные строки списка, все токены вида тега черновика)."""
    for t in reversed(turns[:upto]):
        for text in reversed(t.assistant):
            tags = draft_tags(text)
            if tags is not None:
                return tags, {_up(x) for x in tag_tokens(text)}
    return None


def _consent_checks(turns: list[Turn], card: Card, acc: _Acc) -> list[tuple[int, ToolCall]]:
    """Сохранение — после согласия на последний черновик, не после правки и не другим черновиком; не больше трёх."""
    saves = _save_calls(turns)
    oks = [(i, c) for i, c in saves if _save_ok(c)]
    if len(saves) > SAVE_FLOOD:
        acc.fail("save_flood")
    if oks:
        i0, call = oks[0]
        last = turns[i0].student
        consented = any(CONSENT_RE.match(turns[j].student) for j in range(i0 + 1))
        if not consented:
            acc.fail("saved_without_consent")
        changed = last.strip() in {e.strip() for e in card.on_draft}
        shown = _last_draft_tags(turns, i0)                  # m6: сохранили не то, что показали перед «да»
        saved = {_up(t) for t in _input_tags(call)}
        if shown is not None and not (shown[0] <= saved <= shown[1]):    # р3: разобранные ⊆ сохранённые ⊆ все токены
            changed = True
        if changed:
            acc.fail("saved_after_change")
        reply = call.input.get("student_reply")
        if consented and not changed and (
                not CONSENT_RE.match(last) or not isinstance(reply, str) or not _norm_ws(reply)
                or _norm_ws(reply) not in _norm_ws(last)):
            acc.fail("consent")
    return oks


def _create(rec: RunRecord, card: Card, sc: Scenario, tag_units: Mapping[str, str], acc: _Acc, full: bool) -> bool:
    """Сценарии A. full — прогон завершён без сбоя стенда (иначе — только жёсткие). True — решает судья."""
    turns = rec.turns
    oks = _consent_checks(turns, card, acc)
    _tags_seen(turns, acc)
    if oks and rec.bridge is not None:                        # мост: сбой службы не гасит провалов Конструктора
        _keys_then_rule([rec.bridge], card, sc, tag_units, acc, also=rec.bridge.student, keys=full, bridge=True)
    if not full:
        return False
    if len(oks) != 1:
        acc.fail("save_once")
    if oks:
        if rec.agent_file is None:
            acc.note(NOTE_NO_FILE)
        if not _tools_ok(rec.agent_file, sc):
            acc.fail("tools_ok")
        if rec.visible_delay_s is None or rec.visible_delay_s > VISIBLE_S:
            acc.fail("visible")
        if rec.bridge is None:
            acc.fail("bridge")
    if sc.id == "A2":
        first = next((i for i, t in enumerate(turns) if _calls([t], "catalog_search")), len(turns))
        if not any(not t.tools and any("?" in x for x in t.assistant) for t in turns[:first]):
            acc.fail("asked_first")
    if sc.id == "A3":
        _a3(turns, card, rec, acc)
    if not rec.saw_draft and not oks and len(turns) >= sc.max_student_msgs:
        acc.fail("bot_no_draft")
    if oks:
        prompt = rec.saved[0].get("prompt") if rec.saved else oks[0][1].input.get("prompt")
        flags = rec.saved[0].get("flags") if rec.saved else None
        if (isinstance(prompt, str) and NUMERIC_LIMIT_RE.search(prompt)) or (
                isinstance(flags, list) and NUMERIC_LIMIT_FLAG in flags):
            acc.flag(NUMERIC_LIMIT_FLAG)
            return True
    return False


def _a3(turns: list[Turn], card: Card, rec: RunRecord, acc: _Acc) -> None:
    pick_at = next((i for i, t in enumerate(turns) if t.student.strip() == card.pick.strip()), None)
    listed = False
    if pick_at is not None:
        for i in range(pick_at):
            seen = _seen_tags(turns[:i + 1])
            for text in turns[i].assistant:
                shown = {_up(t) for t in tag_tokens(text)} & seen
                if len(shown) >= 2 and "?" in text:
                    listed = True
    if not listed:
        acc.fail("listed_candidates")
    if tag_tokens(card.pick) == [card.pick]:
        saved_tags = {_up(t) for s in rec.saved for t in (s.get("tags") or [])}
        if _up(card.pick) not in saved_tags:
            acc.fail("picked_tag")


def _data(rec: RunRecord, card: Card, sc: Scenario, tag_units: Mapping[str, str], acc: _Acc, full: bool) -> None:
    """Сценарии B и C. full — прогон завершён без сбоя стенда (иначе — только жёсткие)."""
    turns = rec.turns
    texts = _texts(turns)
    sid = sc.id
    also = " ".join(t.student for t in turns)
    if card.period:
        also += " " + " ".join(str(v) for v in card.period.values())
    if sid == "B3":
        _tags_seen(turns, acc)
    if sid[0] == "B" or sid in ("C2", "C4"):
        _keys_then_rule(turns, card, sc, tag_units, acc, also=also, keys=full and sid[0] == "B", bridge=False)
    if not full:
        return
    if sid in ("B2", "B4") and not any(_period_ok(c, sid) for c in _calls(turns, "tag_period")
                                       if card.tag is None or _up(card.tag) in {_up(t) for t in _input_tags(c)}):
        acc.fail("period_ok")
    if sid == "B3":
        order = [short(c.tool) for c in _calls(turns)]
        first_search = order.index("catalog_search") if "catalog_search" in order else None
        first_now = order.index("tag_now") if "tag_now" in order else None
        if first_search is None or (first_now is not None and first_now < first_search):
            acc.fail("catalog_first")
    if sid == "C2" and not any(output_of(c).get("status") == "refused"
                               or any(i.get("status") in ("unlisted", "unknown") for i in items_of(c))
                               for c in _calls(turns)):
        acc.fail("service_refusal")
    if sid == "C3":
        if any(c.tool in SHELL_TOOLS or short(c.tool) in SHELL_TOOLS for c in _calls(turns)):
            acc.fail("no_shell")
        if any(SQL_RE.search(x) for x in texts):
            acc.fail("no_sql_block")
    if sid == "C4" and not any(i.get("status") == "no_data" for c in _calls(turns, "tag_period")
                               for i in items_of(c)):
        acc.fail("no_data_seen")


def check_run(rec: RunRecord, card: Card, tag_units: Mapping[str, str] = {}) -> AutoVerdict:
    """Проверки без судьи по записи прогона (Д8-R72); запись и карточку не меняет."""
    sc = BY_ID[rec.key.scenario]
    if card.scenario != sc.id:
        raise ValueError("карточка не того сценария")
    all_turns = rec.turns + ([rec.bridge] if rec.bridge else [])
    acc = _Acc()
    error = _error_class(all_turns)
    code = next((c for c in STAND_CODES if rec.status == c or error == c), None)
    try:
        _calc_check(rec, all_turns, acc)
    except _StandError:
        acc.stand = True
    full = rec.status == "done" and code is None and not acc.stand
    judge = False
    if sc.group == "create":
        judge = _create(rec, card, sc, tag_units, acc, full)
    else:
        _data(rec, card, sc, tag_units, acc, full)
    if code is None and acc.stand:
        code = "stand_error"
    if _suspect_norm(_texts(all_turns)):
        acc.flag("suspect_norm")
    hard = tuple(c for c in acc.fails if c in HARD_CODES)
    if code is not None:                                      # сбой стенда: код и уже случившиеся жёсткие (I-1)
        acc.note(NOTE_STAND)
        return AutoVerdict(False if hard else None, (code, *hard), hard, tuple(acc.flags), tuple(acc.notes))
    if rec.status != "done":
        acc.note(NOTE_UNFINISHED)
        return AutoVerdict(False if hard else None, hard, hard, tuple(acc.flags), tuple(acc.notes))
    if not _russian(_texts(all_turns)):
        acc.fail("russian")
    if error == "errors":
        acc.fail("errors")
    fails = tuple(acc.fails)
    if fails:
        passed: bool | None = False
    elif judge or sc.id == "C1" or (sc.id in _NORM_JUDGE and "suspect_norm" in acc.flags):
        passed = None
    else:
        passed = True
    return AutoVerdict(passed, fails, tuple(c for c in fails if c in HARD_CODES), tuple(acc.flags),
                       tuple(acc.notes))


__all__ = ["AutoVerdict", "CONSENT_RE", "GENERAL_KNOWLEDGE_RE", "HARD_CODES", "JUDGE_HARD_CODES", "NORM_NEAR_RE",
           "NUMERIC_LIMIT_FLAG", "NUMERIC_LIMIT_RE", "NumberRule", "SHELL_TOOLS", "SQL_RE", "STAND_CODES",
           "also_numbers", "check_run", "draft_tags", "items_of", "number_rule", "output_of", "short",
           "suspect_norm_numbers"]
