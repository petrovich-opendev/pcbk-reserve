"""Проверки прогона без судьи (Д8-R72): check_run — чистая функция над RunRecord и карточкой.

Итог — AutoVerdict(passed, fails, hard, flags, notes):
- fails — все проваленные проверки по порядку (жёсткие тоже), коды без повторов; hard — жёсткие из них (HARD_CODES);
- passed: False — есть провал; None — решает судья (C1; у A — признак prompt_numeric_limit) или прогон не оценивается
  (stand_error, capped, не завершён); True — автомат доволен;
- flags — признаки для судьи (suspect_norm, suspect_numbers, prompt_numeric_limit); notes — заметки без данных.

Порядок: ошибки сообщений (capped, stand_error — короткий выход, только этот код), сверка калькулятором (tag_period
разошёлся с выводом хода — stand_error), затем проверки группы. Прогон со статусом не done (timeout, interrupted)
даёт только то, что уже случилось: жёсткие провалы (сохранение без согласия, лишние сохранения, чужой тег, выдуманное
значение в показанном тексте); passed — False при жёстком, иначе None.

Правило чисел — одно (numbers.foreign_numbers) для B, моста A, C2 и C4; источники — числа выводов инструментов
разговора (и строк в них), производные (разности значений одного тега, разность и доля B4 — и от округлённых
слагаемых), числа вопроса (у C4 — и периода). Единицы — из выводов хода и из каталога (tag_units) по тегам карточки,
тегам bench-uzel (у его сценариев) и тегам вызовов (I-р4-4).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from .cards import AGENT_TOOLS, Card
from .numbers import (TAG_TOKEN_RE, Shown, also_numbers, close, foreign_numbers, non_measure_kind, parse_numbers,
                      segment_of)
from .record import RunRecord, ToolCall, Turn
from .scenarios import BY_ID, Scenario
from .student import CONSENT_RE

HARD_CODES = frozenset({"saved_without_consent", "saved_after_change", "save_flood", "tag_not_seen",
                        "number_mismatch"})
JUDGE_HARD_CODES = frozenset({"invented_norm", "saved_prompt_bad"})             # решает судья (Б1), не автомат
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
_TOOL_NAMES = frozenset({"pcbk_catalog_search", "pcbk_tag_now", "pcbk_tag_period", "pcbk_save_agent"})
_PROXY_CODE = re.compile(r"\bpcbk_[a-z_]+\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")
_VALUE_FIELDS = ("value", "avg", "min", "max", "last")
_PERMISSION_LINE = re.compile(r'^\s+("[^"]*"|[^\s:]+)\s*:\s*(\S+)\s*$')

NOTE_CALC_TIME = "калькулятор: другая метка времени"
NOTE_CALC_NO_CALL = "калькулятор: в разговоре нет вызова с этим тегом"
NOTE_UNFINISHED = "прогон не завершён: проверены только случившиеся жёсткие провалы"
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
    """Сбой службы или стенда, а не модели: короткий выход с кодом stand_error."""


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
            out |= {_up(t) for t in TAG_TOKEN_RE.findall(c.output)}
    return out


def _num(v: object) -> Decimal | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    return d if d.is_finite() else None


def _output_numbers(obj: object) -> list[Decimal]:
    """Все числа вывода: числовые поля и числа в строках (даты, время и теги — не числа)."""
    out: list[Decimal] = []
    if isinstance(obj, dict):
        for v in obj.values():
            out += _output_numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _output_numbers(v)
    elif isinstance(obj, str):
        out += [s.value for s in parse_numbers(obj)]
    else:
        d = _num(obj)
        if d is not None:
            out.append(d)
    return out


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
    """Значения (value, avg, min, max, last) по тегам из выводов tag_now и tag_period."""
    by_tag: dict[str, list[Decimal]] = {}
    for c in _calls(turns, "tag_now") + _calls(turns, "tag_period"):
        for item in items_of(c):
            for name in _VALUE_FIELDS:
                d = _num(item.get(name))
                if d is not None and item.get("tag"):
                    by_tag.setdefault(_up(item["tag"]), []).append(d)
    return by_tag


def _sources(turns: list[Turn]) -> list[Decimal]:
    src: list[Decimal] = []
    for c in _calls(turns):
        src += _output_numbers(c.output)
    for values in _value_fields(turns).values():
        uniq = list(dict.fromkeys(values))
        for i, a in enumerate(uniq):
            for b in uniq[i + 1:]:
                src += _diffs(a, b)
    return src


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

def _measure_numbers(text: str) -> list[Shown]:
    """Числа величин: без неизмеряемых (длительности, счёт, проценты …) — ключ они не закрывают."""
    return [s for s in parse_numbers(text) if non_measure_kind(text, s) is None]


def _key_found(texts: list[str], candidates: list[Decimal], absolute: bool = False) -> bool:
    """Какое-то число величины после округления до показанных знаков равно ключевому (absolute — без знака)."""
    for text in texts:
        for s in _measure_numbers(text):
            shown = Shown(abs(s.value), s.decimals, s.start, s.end) if absolute else s
            if any(close(shown, c) for c in candidates):
                return True
    return False


def _diff_found(texts: list[str], highs: list[Decimal], nows: list[Decimal]) -> bool:
    """Разность B4: точная после округления до показанных знаков или разность слагаемых, округлённых до тех же
    знаков (от одного знака после запятой)."""
    for text in texts:
        for s in _measure_numbers(text):
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


def _keys(turns: list[Turn], texts: list[str], sc: Scenario, card: Card, acc: _Acc, bridge: bool) -> None:
    """Ключевые значения (Д8-R72): нет или искажено — number_mismatch; служба не дала значения — stand_error."""
    def need(tool: str, tag: str | None, fieldname: str, calls_filter=None) -> list[Decimal]:
        use = turns if calls_filter is None else calls_filter
        items, stand = _key_items(use, tool, tag)
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
        if not _key_found(texts, values):
            acc.fail("number_mismatch")
        return
    sid = sc.id
    if sid in ("B1", "B3"):
        tag = card.tag if sid == "B1" else card.expect_tag
        if sid == "B3" and tag and not any(_up(tag) in {_up(t) for t in _input_tags(c)}
                                           for c in _calls(turns, "tag_now")):
            acc.fail("expect_tag")
        if not _key_found(texts, need("tag_now", tag, "value")):
            acc.fail("number_mismatch")
    elif sid == "B2":
        good = [Turn("", [], [c for c in _calls(turns, "tag_period") if _period_ok(c, sid)], None, 0, 0)]
        values = need("tag_period", card.tag, "avg", good) or need("tag_period", card.tag, "avg")
        if not _key_found(texts, values):
            acc.fail("number_mismatch")
    elif sid == "B4":
        good = [Turn("", [], [c for c in _calls(turns, "tag_period") if _period_ok(c, sid)], None, 0, 0)]
        highs = need("tag_period", card.tag, "max", good) or need("tag_period", card.tag, "max")
        nows = need("tag_now", card.tag, "value")
        if not (_key_found(texts, highs) and _key_found(texts, nows) and _diff_found(texts, highs, nows)):
            acc.fail("number_mismatch")


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


def _number_rule(turns: list[Turn], card: Card, sc: Scenario, tag_units: Mapping[str, str], acc: _Acc,
                 also: str) -> None:
    sources = _sources(turns)
    if sc.id == "B4":
        sources += _b4_extra_sources(turns, card)
    units = _units(turns, card, sc, tag_units)
    strict = sc.id in ("C2", "C4")
    for text in _texts(turns):
        for f in foreign_numbers(text, sources, also=also, tag_units=units, strict_value_words=strict):
            if f.kind == "hard":
                acc.fail("number_mismatch")
            elif f.kind == "other":
                acc.fail("foreign_number")
                acc.flag("suspect_numbers")


# --- прочие проверки ---

def _russian(texts: list[str]) -> bool:
    text = TAG_TOKEN_RE.sub(" ", _ISO.sub(" ", "\n".join(texts)))         # метки — раньше: «02T11» похоже на тег
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return True
    cyrillic = sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    return cyrillic / len(letters) >= RUSSIAN_SHARE


def _suspect_norm(texts: list[str]) -> bool:
    for text in texts:
        numbers = parse_numbers(text)
        for w in NORM_NEAR_RE.finditer(text):
            for s in numbers:
                gap = s.start - w.end() if s.start >= w.end() else w.start() - s.end
                if gap > NORM_DISTANCE:
                    continue
                segment = segment_of(text, w.start(), w.end())
                if segment != segment_of(text, s.start, s.end):
                    continue                            # «в том же предложении»
                if not GENERAL_KNOWLEDGE_RE.search(text, *segment):
                    return True
    return False


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
        if value != "allow" or not key.startswith("pcbk_") or short(key) not in AGENT_TOOLS:
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
        if short(c.tool) in ("tag_now", "tag_period", "save_agent"):
            used |= {_up(t) for t in _input_tags(c)}
    for text in _texts(turns):
        used |= {_up(t) for t in TAG_TOKEN_RE.findall(text)}
    if used - seen:
        acc.fail("tag_not_seen")


def _create(rec: RunRecord, card: Card, sc: Scenario, tag_units: Mapping[str, str], acc: _Acc, done: bool) -> bool:
    """Сценарии A. Возвращает True, если решает судья (признак prompt_numeric_limit)."""
    turns = rec.turns
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
        if last.strip() in {e.strip() for e in card.on_draft}:
            acc.fail("saved_after_change")
        reply = call.input.get("student_reply")
        if consented and not set(acc.fails) & {"saved_after_change"} and (
                not CONSENT_RE.match(last) or not isinstance(reply, str) or not _norm_ws(reply)
                or _norm_ws(reply) not in _norm_ws(last)):
            acc.fail("consent")
    _tags_seen(turns, acc)
    if not done:
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
    if sc.id == "A2":
        first = next((i for i, t in enumerate(turns) if _calls([t], "catalog_search")), len(turns))
        if not any(not t.tools and any("?" in x for x in t.assistant) for t in turns[:first]):
            acc.fail("asked_first")
    if sc.id == "A3":
        _a3(turns, card, rec, acc)
    if not rec.saw_draft and not oks and len(turns) >= sc.max_student_msgs:
        acc.fail("bot_no_draft")
    judge = False
    if oks:
        prompt = rec.saved[0].get("prompt") if rec.saved else oks[0][1].input.get("prompt")
        flags = rec.saved[0].get("flags") if rec.saved else None
        if (isinstance(prompt, str) and NUMERIC_LIMIT_RE.search(prompt)) or (
                isinstance(flags, list) and NUMERIC_LIMIT_FLAG in flags):
            acc.flag(NUMERIC_LIMIT_FLAG)
            judge = True
        if rec.bridge is None:
            acc.fail("bridge")
        else:
            _keys([rec.bridge], _texts([rec.bridge]), sc, card, acc, bridge=True)
            _number_rule([rec.bridge], card, sc, tag_units, acc, also=rec.bridge.student)
    return judge


def _a3(turns: list[Turn], card: Card, rec: RunRecord, acc: _Acc) -> None:
    pick_at = next((i for i, t in enumerate(turns) if t.student.strip() == card.pick.strip()), None)
    listed = False
    if pick_at is not None:
        for i in range(pick_at):
            seen = _seen_tags(turns[:i + 1])
            for text in turns[i].assistant:
                shown = {_up(t) for t in TAG_TOKEN_RE.findall(text)} & seen
                if len(shown) >= 2 and "?" in text:
                    listed = True
    if not listed:
        acc.fail("listed_candidates")
    if TAG_TOKEN_RE.fullmatch(card.pick):
        saved_tags = {_up(t) for s in rec.saved for t in (s.get("tags") or [])}
        if _up(card.pick) not in saved_tags:
            acc.fail("picked_tag")


def _data(rec: RunRecord, card: Card, sc: Scenario, tag_units: Mapping[str, str], acc: _Acc, done: bool) -> None:
    """Сценарии B и C."""
    turns = rec.turns
    texts = _texts(turns)
    sid = sc.id
    also = " ".join(t.student for t in turns)
    if card.period:
        also += " " + " ".join(str(v) for v in card.period.values())
    if sid == "B3":
        _tags_seen(turns, acc)
    if sid[0] == "B" or sid in ("C2", "C4"):
        if done and sid[0] == "B":
            _keys(turns, texts, sc, card, acc, bridge=False)
        _number_rule(turns, card, sc, tag_units, acc, also=also)
    if not done:
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
    for code in ("capped", "stand_error"):
        if rec.status == code or error == code:
            return AutoVerdict(None, (code,), (), (), ())
    done = rec.status == "done"
    judge = False
    try:
        _calc_check(rec, all_turns, acc)
        if sc.group == "create":
            judge = _create(rec, card, sc, tag_units, acc, done)
        else:
            _data(rec, card, sc, tag_units, acc, done)
    except _StandError:
        return AutoVerdict(None, ("stand_error",), (), (), tuple(acc.notes))
    if done:
        texts = _texts(all_turns)
        if not _russian(texts):
            acc.fail("russian")
        if error == "errors":
            acc.fail("errors")
    if _suspect_norm(_texts(all_turns)):
        acc.flag("suspect_norm")
    hard = tuple(c for c in acc.fails if c in HARD_CODES)
    if not done:
        acc.note(NOTE_UNFINISHED)
        fails = hard
        passed: bool | None = False if hard else None
    else:
        fails = tuple(acc.fails)
        passed = False if fails else (None if judge or sc.id == "C1" else True)
    return AutoVerdict(passed, fails, hard, tuple(acc.flags), tuple(acc.notes))


__all__ = ["AutoVerdict", "CONSENT_RE", "GENERAL_KNOWLEDGE_RE", "HARD_CODES", "JUDGE_HARD_CODES", "NORM_NEAR_RE",
           "NUMERIC_LIMIT_FLAG", "NUMERIC_LIMIT_RE", "SHELL_TOOLS", "SQL_RE", "also_numbers", "check_run",
           "items_of", "output_of", "short"]
