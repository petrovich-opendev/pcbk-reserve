"""Стенд «агент = калькулятор» (Д9-R91, Д9-R93): матрица случаев, вердикты со сверкой калькулятора, прогонщик,
случаи окна и проба форм. Только stdlib и только машина разработчика (Д9-R82).

Случаи (build_cases): на каждый аналоговый тег — шесть видов по порядку ANALOG_KINDS, на каждый тег состояния — один
(STATE_KINDS); id случая — «{tag}:{kind}». today — местная дата историана (hist_today: asof ответа tag_now); у
avg_span start и end — ISO без пояса (время историана) за вчера 08:00–12:00.

Вердикт (judge_case; calc — ответ калькулятора на аргументы вызова случая):
1. ход с ошибкой или без конца — timeout;
2. вызова случая нет (case_call: последний вызов pcbk_<tool> хода с тегом случая во входе, без регистра): в тексте
   есть числа величин — mismatch с заметкой no_tool, иначе no_number;
3. элемент тега no_data — мягкий no_data (I7); вывод не ok/partial или элемент тега не ok — service;
4. период не тот (period_ok) — wrong_period: у готового периода input.period или output.period.preset равен периоду
   случая; у last_24h и last_8h годится и свой период длиной 24 или 8 ч ± 60 с; у avg_span начало и конец вывода
   отличаются от времени случая (в поясе вывода) не больше чем на 60 с. Период решают поля периода, а не числа:
   у двойника сигналы дают одни и те же числа на разных периодах;
5. каждый ключ (key_values) есть в тексте ответа по правилу numbers.matches — округление до показанных знаков, —
   но только среди чисел величин (parse_numbers без неизмеряемых non_measure_kind; процент — величина у тега с
   единицей «%», часы «ч» — у hours_top). Ключ с подписью поля (value, avg, min, max) ищется среди чисел с той же
   подписью или без подписи, если подпись в ответе есть (как ключи стенда отбора, m2 доводки р1): «Минимум 90,5,
   максимум 70» не сходится. Нет ключа — mismatch, а чисел величин нет вовсе — no_number;
6. сверка с калькулятором на тех же аргументах: ключи калькулятора равны ключам вывода (1e-9 относительной: числа
   службы — без округления, с шумом float); иначе при открытом периоде (output.period.open — решает флаг вывода, а
   не вид случая, m-15) или у tag_now с другой меткой time — заметка calc_moved, вердикт прежний; при закрытом —
   service (заметка calc_differs). Калькулятор не ответил или ответил не ok — service (calc_rate, calc_fail);
7. иначе — match.
HARD — неуспех 4. service — сбой службы, не модели (блокер «того же кода», разбор — до закрытия дня); no_data —
данных за период у тега нет. Заметки — только коды, без данных.

MatchRunner(chat, stand, place, cases, out): prepare() — вход, пробуждение места (≤ 120 с), mcp=connected (≤ 90 с),
агент проверки bench-calc (Stand.save_agent; already — годится; имя занято прежней версией — архив и ещё раз) и его
ready в «Мои агенты» (≤ 60 с); иначе RunRefused (not_ready, calc_agent). run() — по случаю: новая сессия, вопрос
агенту, wait_idle (срок — abort и timeout), parse_turn, калькулятор (calc_patient) по аргументам вызова случая,
вердикт; строка в OUT/match.jsonl (0600: ответы и числа — в $JOB, не в git); в stderr — только «случай k/N: <вердикт>».
503 шлюза (место заснуло, WorkplaceNotReady) — один wake() и повтор случая; прочий отказ шлюза и обрыв связи — service
(gateway, network), прогон идёт дальше. close() — abort открытых сессий, агент проверки — в архив.

render_match — таблица Markdown с псевдонимами ТЕГ-n и итог; имён тегов и значений в ней нет.
cases_from_agent — теги окна (I7): живые аналоговые из раздела «Теги узла» самого нового видимого агента места
(до трёх), добор живыми из catalog_search("_pv", 50); тег состояния — живой дискретный из тегов агента, иначе из
"_run", иначе из "_opn". probe_calc — проба форм окна (Д9-R93): только коды, без тегов и значений.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .cards import ALLOWED_TOOLS
from .checks import TAG_IN_LINE_RE, TAGS_LINE_RE, output_of
from .gateway import GatewayChat, GatewayError, WorkplaceNotReady
from .numbers import Shown, close, non_measure_kind, parse_numbers, value_label
from .record import ToolCall, Turn, parse_turn
from .runner import RunRefused
from .stand import Stand, calc_patient
from .student import CONSENT

CALC_AGENT = "bench-calc"
CALC_TITLE = "Проверка калькуляторов"
CALC_NODE = "Узел проверки калькуляторов"
CALC_PURPOSE = "Отвечать на вопросы автотеста «агент = калькулятор» числами инструментов службы по тегам узла"
# инструкция агента проверки: по строке на вид вопроса, без цифр (в ней нет признака нормы, m15 Д8)
CALC_PROMPT = (
    "Ты — помощник технолога по узлу «{node}». Ты отвечаешь на вопросы о тегах этого узла только числами "
    "инструментов службы. Теги узла служба перечислила ниже, в разделе «Теги узла».\n"
    "Как отвечать:\n"
    "- «Что сейчас по тегу …» → pcbk_tag_now по этому тегу; назови значение, единицу и время замера.\n"
    "- «Какое среднее по тегу … за прошлую смену» → pcbk_tag_period, готовый период «прошлая смена»; назови "
    "среднее.\n"
    "- «Какие минимум и максимум по тегу … за последние сутки» → pcbk_tag_period, готовый период «последние "
    "сутки»; назови минимум и максимум.\n"
    "- «Какой размах по тегу … за вчера» → pcbk_tag_period, готовый период «вчера»; назови размах.\n"
    "- «Какое среднее по тегу … <дата> с <час> до <час>» → pcbk_tag_period с началом и концом периода точно как в "
    "вопросе (время историана); назови среднее.\n"
    "- «Как менялся тег … за последние восемь часов» → pcbk_tag_series, готовый период «последние восемь часов»; "
    "назови значение в начале, в конце и изменение.\n"
    "- «Какая наработка по тегу … за вчера» → pcbk_calc_runtime, готовый период «вчера»; назови часы в каждом "
    "состоянии с подписью.\n"
    "Числа называй, как их вернул инструмент, можно округлить до двух знаков после запятой; сам не пересчитывай.\n"
    "Отказ или нет данных — скажи как есть, число не подставляй.\n"
    "Отвечай коротко, по-русски.")

MATCH_VERDICTS = ("match", "mismatch", "wrong_period", "no_number", "no_data", "service", "timeout")
HARD = frozenset({"mismatch", "wrong_period"})                     # неуспех 4
OK_STATUSES = ("ok", "partial")
WAKE_TIMEOUT_S = 120.0
MCP_TIMEOUT_S = 90.0
AGENT_WAIT_S = 60.0
ABORT_WAIT_S = 10.0
POLL_S = 1.0
SPAN_SLACK_S = 60.0
DEFAULT_TURN_TIMEOUT_S = 300.0
REL_SAME = Decimal("1e-9")
MAX_ANALOG = 3
SEARCH_LIMIT = 50
LOOKUP_LIMIT = 10
PROBE_POINTS = 8
GRID_SLACK_S = 1.0
SUM_HOURS_SLACK = 0.01
SPAN_DAY_HOURS = ("08:00", "12:00")
MAX_CASE_TAGS = 16                                                # MAX_TAGS save_agent
LABELS = {"value": "value", "avg": "avg", "min": "min", "max": "max"}   # подпись числа (numbers.value_label)
PERIOD_TEXT = {"prev_shift": "прошлая смена", "last_24h": "последние сутки", "yesterday": "вчера",
               "last_8h": "последние восемь часов"}
_CODE = re.compile(r"[a-z_]{1,24}")
_TAG = re.compile(r"[A-Za-z0-9_]{1,128}")
_LOGIN = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


@dataclass(frozen=True)
class KindSpec:
    tool: str
    period: str | None                  # готовый период; None — tag_now и свой период avg_span
    keys: tuple[str, ...]
    question: str                       # {tag}; у avg_span ещё {day} — дд.мм.гггг


KINDS: dict[str, KindSpec] = {
    "now": KindSpec("tag_now", None, ("value",), "Что сейчас по {tag}?"),
    "avg_prev_shift": KindSpec("tag_period", "prev_shift", ("avg",), "Какое среднее по {tag} за прошлую смену?"),
    "minmax_last_24h": KindSpec("tag_period", "last_24h", ("min", "max"),
                                "Какие минимум и максимум по {tag} за последние сутки?"),
    "range_yesterday": KindSpec("tag_period", "yesterday", ("range",), "Какой размах по {tag} за вчера?"),
    "avg_span": KindSpec("tag_period", None, ("avg",), "Какое среднее по {tag} {day} с 08:00 до 12:00?"),
    "profile_last_8h": KindSpec("tag_series", "last_8h", ("first", "last"),
                                "Как менялся {tag} за последние 8 часов: какое значение в начале и в конце?"),
    "runtime_yesterday": KindSpec("calc_runtime", "yesterday", ("hours_top",),
                                  "Какая наработка по {tag} за вчера: сколько часов в каждом состоянии?"),
}
ANALOG_KINDS = ("now", "avg_prev_shift", "minmax_last_24h", "range_yesterday", "avg_span", "profile_last_8h")
STATE_KINDS = ("runtime_yesterday",)
BASIC_KINDS = ("now", "avg_prev_shift", "minmax_last_24h", "range_yesterday", "avg_span")   # match --only basic


@dataclass(frozen=True)
class Case:
    id: str
    tag: str
    kind: str
    tool: str
    period: str | None
    keys: tuple[str, ...]
    question: str
    start: str | None = None            # только avg_span: ISO без пояса (время историана)
    end: str | None = None


@dataclass
class MatchResult:
    case: str                           # id случая
    verdict: str
    notes: list[str] = field(default_factory=list)
    session: str | None = None
    turn: Turn | None = None
    calc: dict | None = None

    @property
    def hard(self) -> bool:
        return self.verdict in HARD

    def to_json(self) -> dict[str, Any]:
        return {"case": self.case, "verdict": self.verdict, "hard": self.hard, "notes": list(self.notes),
                "session": self.session, "turn": self.turn.to_json() if self.turn else None, "calc": self.calc}


# --- случаи ---

def build_cases(analog: Sequence[str], discrete: Sequence[str], today: date) -> list[Case]:
    """Матрица Д9-R91: аналоговые теги — по шесть видов, теги состояния — по одному; avg_span — вчера 08:00–12:00."""
    day = today - timedelta(days=1)
    out: list[Case] = []
    for tags, kinds in ((analog, ANALOG_KINDS), (discrete, STATE_KINDS)):
        for tag in tags:
            for kind in kinds:
                spec = KINDS[kind]
                start = end = None
                if kind == "avg_span":
                    start, end = (f"{day.isoformat()}T{hm}:00" for hm in SPAN_DAY_HOURS)
                out.append(Case(f"{tag}:{kind}", tag, kind, spec.tool, spec.period, spec.keys,
                                spec.question.format(tag=tag, day=day.strftime("%d.%m.%Y")), start, end))
    return out


def hist_today(stand: Stand, tag: str, *, sleep: Callable[[float], None] = time.sleep) -> date:
    """Местная дата историана: asof ответа калькулятора tag_now (ISO с поясом историана)."""
    answer = calc_patient(stand, "tag_now", {"tags": [tag]}, sleep=sleep)
    asof = _parse_ts(answer.get("asof")) if isinstance(answer, dict) else None
    if asof is None or asof.tzinfo is None:
        raise ValueError("tag_now: в ответе калькулятора нет asof с поясом")
    return asof.date()


# --- разбор вывода ---

def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if not isinstance(value, float) or math.isfinite(value) else None


def _dec(value: Any) -> Decimal | None:
    v = _number(value)
    if v is None:
        return None
    try:
        return Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
    except InvalidOperation:
        return None


def _item(out: dict, tag: str) -> dict | None:
    want = tag.strip().upper()
    for item in out.get("items") if isinstance(out.get("items"), list) else []:
        if isinstance(item, dict) and str(item.get("tag", "")).strip().upper() == want:
            return item
    return None


def key_values(case: Case, item: dict) -> dict[str, float | int | None]:
    """Ключи случая из элемента вывода: value, avg, min, max, range — поля элемента; first, last — из profile;
    hours_top — hours состояния с наибольшими часами среди состояний со значением («нет данных», value null, не
    берётся, m-14). Нет ключа — None."""
    if "hours_top" in case.keys:
        states = item.get("states") if isinstance(item.get("states"), list) else []
        hours = [h for s in states if isinstance(s, dict) and s.get("value") is not None
                 and (h := _number(s.get("hours"))) is not None]
        return {"hours_top": max(hours) if hours else None}
    src = item.get("profile") if case.tool == "tag_series" else item
    src = src if isinstance(src, dict) else {}
    return {k: _number(src.get(k)) for k in case.keys}


def case_call(case: Case, turn: Turn) -> ToolCall | None:
    """Последний вызов pcbk_<case.tool> хода, у которого во входе есть тег случая (без регистра)."""
    name, want = f"pcbk_{case.tool}", case.tag.strip().upper()
    found = None
    for call in turn.tools:
        tags = call.input.get("tags")
        if call.tool == name and isinstance(tags, list) and any(str(t).strip().upper() == want for t in tags):
            found = call
    return found


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _span(src: Any) -> tuple[datetime, datetime] | None:
    if not isinstance(src, dict):
        return None
    start, end = _parse_ts(src.get("start")), _parse_ts(src.get("end"))
    if start is None or end is None or (start.tzinfo is None) != (end.tzinfo is None):
        return None
    return start, end


def period_ok(case: Case, call: ToolCall) -> bool:
    """Период вызова — период случая (Д9-R91, шаг 4); у tag_now периода нет."""
    if case.tool == "tag_now":
        return True
    period = output_of(call).get("period")
    period = period if isinstance(period, dict) else {}
    asked, preset = call.input.get("period"), period.get("preset")
    if case.kind == "avg_span":
        span = _span(period)
        if span is None:                    # вывода периода нет — только вход без пояса (время историана)
            span = _span(call.input)
            if span is not None and span[0].tzinfo is not None:
                return False
        if span is None or case.start is None or case.end is None:
            return False
        start, end = datetime.fromisoformat(case.start), datetime.fromisoformat(case.end)
        start, end = start.replace(tzinfo=span[0].tzinfo), end.replace(tzinfo=span[1].tzinfo)   # в поясе вывода
        return (abs((span[0] - start).total_seconds()) <= SPAN_SLACK_S
                and abs((span[1] - end).total_seconds()) <= SPAN_SLACK_S)
    if case.period is not None and case.period in (asked, preset):
        return True
    hours = {"last_24h": 24, "last_8h": 8}.get(case.period or "")
    if hours is None or asked or preset:
        return False
    span = _span(period) or _span(call.input)
    return span is not None and abs((span[1] - span[0]).total_seconds() - hours * 3600) <= SPAN_SLACK_S


def _quantities(text: str, case: Case, unit: Any = None) -> list[Shown]:
    """Числа величин ответа: parse_numbers без неизмеряемых; процент — величина у тега с единицей «%», часы «ч» —
    величина hours_top."""
    kinds: set[str | None] = {None}
    if isinstance(unit, str) and unit.strip() == "%":
        kinds.add("percent")
    if "hours_top" in case.keys:
        kinds.add("duration")
    return [s for s in parse_numbers(text) if non_measure_kind(text, s) in kinds]


def _key_shown(text: str, key: str, value: Any, pool: list[Shown]) -> bool:
    """Ключ — среди чисел величин после округления до показанных знаков (правило numbers.matches); у ключа с
    подписью поля — среди чисел с той же подписью и без подписи, если такая подпись в ответе есть."""
    v = _dec(value)
    if v is None:
        return False
    label = LABELS.get(key)
    if label is not None:
        labels = [value_label(text, s, pool) for s in pool]
        if label in labels:
            pool = [s for s, lab in zip(pool, labels, strict=True) if lab in (label, None)]
    return any(close(s, v) for s in pool)


def _same(a: Any, b: Any) -> bool:
    x, y = _dec(a), _dec(b)
    return x is not None and y is not None and abs(x - y) <= REL_SAME * max(Decimal(1), abs(x))


def _status(status: Any) -> str:
    """Статус ответа службы как код (только [a-z_]); иное — failed: в заметки и коды не попадает ничего, кроме кода."""
    return status if isinstance(status, str) and _CODE.fullmatch(status) else "failed"


def _code(prefix: str, status: Any) -> str:
    return f"{prefix}_{_status(status)}"


def judge_case(case: Case, turn: Turn | None, calc: dict | None) -> MatchResult:
    """Вердикт случая по ходу и ответу калькулятора на аргументы вызова случая (см. шапку модуля, шаги 1–7)."""
    if turn is None or turn.error or not turn.ended:
        return MatchResult(case.id, "timeout", ["turn_error" if turn is not None and turn.error else "turn_unfinished"])
    text = "\n".join(turn.assistant)
    call = case_call(case, turn)
    if call is None:
        return MatchResult(case.id, "mismatch" if _quantities(text, case) else "no_number", ["no_tool"])
    out = output_of(call)
    item = _item(out, case.tag)
    if item is not None and item.get("status") == "no_data":
        return MatchResult(case.id, "no_data")
    if out.get("status") not in OK_STATUSES:
        return MatchResult(case.id, "service", [_code("tool", out.get("status"))])
    if item is None or item.get("status") != "ok":
        return MatchResult(case.id, "service", ["no_item" if item is None else _code("item", item.get("status"))])
    if not period_ok(case, call):
        return MatchResult(case.id, "wrong_period")
    keys = key_values(case, item)
    if any(v is None for v in keys.values()):
        return MatchResult(case.id, "service", ["no_key"])
    pool = _quantities(text, case, item.get("unit"))
    if not all(_key_shown(text, k, v, pool) for k, v in keys.items()):
        return MatchResult(case.id, "mismatch" if pool else "no_number")
    return _calc_verdict(case, out, item, keys, calc)


def _calc_verdict(case: Case, out: dict, item: dict, keys: dict, calc: dict | None) -> MatchResult:
    """Шаг 6: ключи калькулятора на тех же аргументах — ключи вывода; открытый период или tag_now с другой меткой —
    заметка calc_moved; закрытый — service."""
    if not isinstance(calc, dict):
        return MatchResult(case.id, "service", ["calc_fail"])
    if calc.get("status") == "rate":
        return MatchResult(case.id, "service", ["calc_rate"])
    calc_item = _item(calc, case.tag)
    if calc.get("status") not in OK_STATUSES or calc_item is None or calc_item.get("status") != "ok":
        return MatchResult(case.id, "service", ["calc_fail"])
    again = key_values(case, calc_item)
    if all(_same(v, again.get(k)) for k, v in keys.items()):
        return MatchResult(case.id, "match")
    if case.tool == "tag_now":
        moved = calc_item.get("time") != item.get("time")
    else:
        period = out.get("period") if isinstance(out.get("period"), dict) else {}
        moved = period.get("open") is True
    if moved:
        return MatchResult(case.id, "match", ["calc_moved"])
    return MatchResult(case.id, "service", ["calc_differs"])


# --- агент проверки и таблица ---

def calc_agent_args(cases: Iterable[Case]) -> dict:
    """Аргументы save_agent агента проверки CALC_AGENT: пять инструментов чтения, теги случаев, инструкция без цифр."""
    return {"name": CALC_AGENT, "title": CALC_TITLE, "node": CALC_NODE, "purpose": CALC_PURPOSE,
            "tools": list(ALLOWED_TOOLS), "tags": list(dict.fromkeys(c.tag for c in cases)),
            "prompt": CALC_PROMPT.format(node=CALC_NODE), "student_reply": CONSENT}


def _period_text(case: Case) -> str:
    if case.kind == "avg_span":
        return "свой период (вчера)"
    return PERIOD_TEXT.get(case.period or "", "—")


def render_match(results: Iterable[MatchResult], cases: Iterable[Case]) -> str:
    """Markdown: «ТЕГ-n · вид · инструмент · период · ключи · вердикт · заметки» и итог; имён тегов и чисел нет."""
    results, cases = list(results), list(cases)
    by_id = {c.id: c for c in cases}
    alias: dict[str, str] = {}
    for c in cases:
        alias.setdefault(c.tag.upper(), f"ТЕГ-{len(alias) + 1}")
    rows = ["| Тег | Вид | Инструмент | Период | Ключи | Вердикт | Заметки |", "|---|---|---|---|---|---|---|"]
    for r in results:
        c = by_id.get(r.case)
        if c is None:
            raise ValueError("результат без случая в списке случаев")
        rows.append(f"| {alias[c.tag.upper()]} | {c.kind} | {c.tool} | {_period_text(c)} | {', '.join(c.keys)} | "
                    f"{r.verdict} | {', '.join(r.notes) or '—'} |")
    count = Counter(r.verdict for r in results)
    hard = sum(1 for r in results if r.hard)
    other = len(results) - count["match"] - hard - count["service"] - count["no_data"]
    total = (f"случаев {len(results)}; match {count['match']}; жёстких (неуспех 4) {hard}; service {count['service']}; "
             f"нет данных {count['no_data']}; прочих {other}")
    return "\n".join(["# Агент = калькулятор", "", *rows, "", total, ""])


# --- прогонщик ---

def _append_line(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def write_private(path: Path, text: str) -> None:
    """Файл с данными окна — 0600 (и прежний файл тоже переводится в 0600)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def _name_taken(answer: dict) -> bool:
    problems = answer.get("problems") if isinstance(answer.get("problems"), list) else []
    return any(isinstance(p, dict) and p.get("code") == "name_taken" for p in problems)


class MatchRunner:
    """Прогонщик «агент = калькулятор» одного места (см. шапку модуля). poll_s — шаг опроса ожиданий."""

    poll_s = POLL_S

    def __init__(self, chat: GatewayChat, stand: Stand, place: int, cases: Sequence[Case], out: Path, *,
                 turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S, sleep: Callable[[float], None] = time.sleep):
        if not cases:
            raise ValueError("случаев нет")
        self.chat = chat
        self.stand = stand
        self.place = place
        self.cases = list(cases)
        self.out = Path(out)
        self.turn_timeout_s = float(turn_timeout_s)
        self.sleep = sleep
        self._prepared = False
        self._saved = False
        self._open: list[str] = []          # сессии с вопросом, чей ход не дождались

    # --- ожидания ---

    def _until(self, ready: Callable[[], bool], limit_s: float) -> bool:
        """Опрос ready() раз в poll_s, не больше limit_s / poll_s пауз; хотя бы одна проверка."""
        tries = max(1, math.ceil(limit_s / self.poll_s))
        for i in range(tries + 1):
            if ready():
                return True
            if i < tries:
                self.sleep(self.poll_s)
        return False

    def _mcp_ready(self) -> None:
        if not self._until(lambda: self.stand.place_mcp_ok(self.place), MCP_TIMEOUT_S):
            raise WorkplaceNotReady(503, "WorkplaceNotReady")

    def _agent_ready(self) -> bool:
        return any(a.get("name") == CALC_AGENT and a.get("kind") != "builtin" and a.get("state") == "ready"
                   for a in self.chat.agents())

    # --- подготовка и закрытие ---

    def prepare(self) -> None:
        """Вход, пробуждение места, mcp=connected, агент проверки и его ready; расхождение — RunRefused."""
        if self._prepared:
            return
        self.out.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.chat.login()
        try:
            if self.chat.workplace().get("state") != "ready":
                self.chat.wake(timeout=WAKE_TIMEOUT_S)
            self._mcp_ready()
        except GatewayError:
            raise RunRefused("not_ready") from None
        args = calc_agent_args(self.cases)
        answer = self.stand.save_agent(self.place, args)
        if _name_taken(answer):              # имя занято прежней версией агента проверки (прошлый прогон не убрал)
            self.stand.archive_agent(self.place, CALC_AGENT)
            answer = self.stand.save_agent(self.place, args)
        if answer.get("status") != "ok" or _name_taken(answer):
            raise RunRefused("calc_agent")
        self._saved = True
        if not self._until(self._agent_ready, AGENT_WAIT_S):
            raise RunRefused("calc_agent")
        self._prepared = True

    def close(self) -> None:
        """abort открытых сессий, агент проверки — в архив (идемпотентно)."""
        for session in list(self._open):
            self._abort(session)
        if self._saved:
            self.stand.archive_agent(self.place, CALC_AGENT)
            self._saved = False
        self._prepared = False

    def _abort(self, session: str) -> None:
        """abort сессии; 404 — её уже нет; иначе — ожидание ухода из /session/status (≤ ABORT_WAIT_S)."""
        try:
            self.chat.abort(session)
        except GatewayError as exc:
            if exc.status != 404:
                return                      # не снята: остаётся в открытых, close() попробует ещё раз
        except Exception:
            return
        else:
            def gone() -> bool:
                try:
                    return session not in self.chat.session_status()
                except Exception:
                    return True
            self._until(gone, ABORT_WAIT_S)
        if session in self._open:
            self._open.remove(session)

    def _abort_open(self) -> None:
        for session in list(self._open):
            self._abort(session)

    # --- прогон ---

    def run(self) -> list[MatchResult]:
        """Случаи по порядку; строка OUT/match.jsonl на случай; в stderr — только «случай k/N: <вердикт>»."""
        self.prepare()
        results = []
        for k, case in enumerate(self.cases, 1):
            result = self._case(case)
            results.append(result)
            _append_line(self.out / "match.jsonl", json.dumps(result.to_json(), ensure_ascii=False))
            print(f"случай {k}/{len(self.cases)}: {result.verdict}", file=sys.stderr, flush=True)
        return results

    def _case(self, case: Case) -> MatchResult:
        """Случай; 503 шлюза (место заснуло) — один wake() и повтор случая в новой сессии. Прочий отказ шлюза и
        обрыв связи — service (не модель), прогон идёт дальше."""
        try:
            return self._ask(case)
        except GatewayError as exc:
            if exc.status != 503:
                self._abort_open()
                return MatchResult(case.id, "service", ["gateway"])
        except (OSError, http.client.HTTPException):
            self._abort_open()
            return MatchResult(case.id, "service", ["network"])
        try:
            self.chat.wake(timeout=WAKE_TIMEOUT_S)
            self._mcp_ready()
            self._abort_open()                  # ход, прерванный сном места
            return self._ask(case)
        except GatewayError as exc:
            self._abort_open()
            return MatchResult(case.id, "service", ["not_ready" if exc.status == 503 else "gateway"])
        except (OSError, http.client.HTTPException):
            self._abort_open()
            return MatchResult(case.id, "service", ["network"])

    def _ask(self, case: Case) -> MatchResult:
        session = self.chat.new_session()
        self.chat.send(session, case.question, CALC_AGENT)
        sent = time.time()
        self._open.append(session)
        try:
            messages = self.chat.wait_idle(session, self.turn_timeout_s)
        except TimeoutError:
            self._abort(session)
            return MatchResult(case.id, "timeout", ["turn_timeout"], session=session)
        self._open.remove(session)
        turn = parse_turn(messages, 0, case.question)
        turn.started = turn.started or sent
        turn.ended = turn.ended or time.time()
        call = case_call(case, turn)
        calc = None
        if call is not None and not turn.error:
            try:
                calc = calc_patient(self.stand, case.tool, dict(call.input), sleep=self.sleep)
            except Exception:
                calc = None                     # сбой калькулятора: шаг 6 даст service с заметкой calc_fail
        result = judge_case(case, turn, calc)
        result.session, result.turn, result.calc = session, turn, calc
        return result


# --- случаи окна и проба ---

def _live(match: Any, kind: str) -> bool:
    return (isinstance(match, dict) and match.get("live") is True and match.get("kind") == kind
            and isinstance(match.get("tag"), str) and bool(_TAG.fullmatch(match["tag"])))


def _search(stand: Stand, query: str, limit: int, sleep: Callable[[float], None]) -> tuple[str, list[dict]]:
    """(статус ответа поиска, совпадения); статус не ok — совпадений нет."""
    answer = calc_patient(stand, "catalog_search", {"query": query, "limit": limit}, sleep=sleep)
    answer = answer if isinstance(answer, dict) else {}
    status = _status(answer.get("status"))
    if status != "ok":
        return status, []
    matches = answer.get("matches") if isinstance(answer.get("matches"), list) else []
    return status, [m for m in matches if isinstance(m, dict)]


def node_tags(agent_file: str) -> list[str]:
    """Теги раздела «Теги узла» файла агента (шаблон v1: «## Теги узла (…)», затем строки «- `тег` — …»): первый тег
    каждой строки списка до пустой строки или строки без тега; заголовок — последний (раздел службы — после
    инструкции)."""
    lines = agent_file.splitlines()
    head = None
    for i, line in enumerate(lines):
        if line.startswith("#") and TAGS_LINE_RE.match(line.lstrip("#")):
            head = i
    if head is None:
        return []
    tags: list[str] = []
    for line in lines[head + 1:]:
        if not line.strip():
            if tags:
                break
            continue
        m = TAG_IN_LINE_RE.search(line)
        if m is None or not line.lstrip().startswith(("-", "*", "•")):
            break
        tags.append(m.group(0))
    return list(dict.fromkeys(tags))


def _agent_tags(stand: Stand, place: int) -> list[str]:
    """Теги раздела «Теги узла» файла самого нового видимого агента места."""
    rows = [r for r in stand.agent_rows(place) if r.get("state") == "visible" and isinstance(r.get("name"), str)]
    if not rows:
        return []
    _, newest = max(enumerate(rows), key=lambda p: (str(p[1].get("created_at") or ""), p[0]))
    return node_tags(stand.agent_file(place, newest["name"]))


def cases_from_agent(stand: Stand, source_place: int, *, sleep: Callable[[float], None] = time.sleep
                     ) -> tuple[list[str], list[str], int]:
    """Теги окна (I7): (аналоговые, состояния, сколько аналоговых — из агента). live и kind — из catalog_search по
    имени тега; аналоговые — до трёх, добор из "_pv"; тег состояния — из агента, иначе "_run", иначе "_opn"."""
    found: dict[str, dict] = {}
    for tag in _agent_tags(stand, source_place):
        _, matches = _search(stand, tag, LOOKUP_LIMIT, sleep)
        hit = next((m for m in matches if str(m.get("tag", "")).upper() == tag.upper()), None)
        if hit is not None:
            found.setdefault(hit["tag"].upper(), hit)
    analog = [m["tag"] for m in found.values() if _live(m, "analog")][:MAX_ANALOG]
    from_agent = len(analog)
    if len(analog) < MAX_ANALOG:
        for m in _search(stand, "_pv", SEARCH_LIMIT, sleep)[1]:
            if len(analog) >= MAX_ANALOG:
                break
            if _live(m, "analog") and m["tag"].upper() not in {t.upper() for t in analog}:
                analog.append(m["tag"])
    discrete = [m["tag"] for m in found.values() if _live(m, "discrete")][:1]
    for query in ("_run", "_opn"):
        if discrete:
            break
        discrete = [m["tag"] for m in _search(stand, query, SEARCH_LIMIT, sleep)[1] if _live(m, "discrete")][:1]
    return analog, discrete, from_agent


def _first_live(stand: Stand, query: str, kind: str, sleep: Callable[[float], None]) -> tuple[str, str | None]:
    """(код, тег): поиск отказал — его статус; живого тега нужного вида нет — no_tag (m-7)."""
    status, matches = _search(stand, query, SEARCH_LIMIT, sleep)
    if status != "ok":
        return status, None
    tag = next((m["tag"] for m in matches if _live(m, kind)), None)
    return ("ok", tag) if tag is not None else ("no_tag", None)


def _probe_item(out: Any, tag: str) -> tuple[str | None, dict]:
    """(код отказа или None, элемент тега ok)."""
    out = out if isinstance(out, dict) else {}
    item = _item(out, tag)
    if out.get("status") not in OK_STATUSES:
        return _status(out.get("status")), {}
    if item is None:
        return "no_item", {}
    if item.get("status") != "ok":
        return _status(item.get("status")), {}
    return None, item


def _series_code(out: dict, tag: str) -> str:
    bad, item = _probe_item(out, tag)
    if bad is not None:
        return f"series: {bad}"
    points = item.get("points") if isinstance(item.get("points"), list) else []
    stamps = [_parse_ts(p[0]) if isinstance(p, list) and p else None for p in points]
    stamps = [s for s in stamps if s is not None and s.tzinfo is not None]
    steps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False)]
    even = len(stamps) == len(points) and bool(steps) and all(abs(s - steps[0]) <= GRID_SLACK_S for s in steps)
    span = _span(out.get("period"))
    ends = (span is not None and span[0].tzinfo is not None and len(stamps) >= 2
            and abs((stamps[0] - span[0]).total_seconds()) <= GRID_SLACK_S
            and abs((stamps[-1] - span[1]).total_seconds()) <= GRID_SLACK_S)
    return (f"series: ok rows={len(points)}/{PROBE_POINTS} grid={'even' if even else 'uneven'} "
            f"ends={'yes' if ends else 'no'}")


def _runtime_code(out: dict, tag: str) -> str:
    bad, item = _probe_item(out, tag)
    if bad is not None:
        return f"runtime: {bad}"
    states = [s for s in item.get("states") or [] if isinstance(s, dict)]
    covered = _number(item.get("covered_percent"))
    total = sum(h for s in states if (h := _number(s.get("hours"))) is not None)
    period = _number(item.get("period_hours"))
    if period is None:
        span = _span(out.get("period"))
        period = (span[1] - span[0]).total_seconds() / 3600 if span is not None else None
    ok = bool(period) and abs(total - period) < SUM_HOURS_SLACK * period
    return (f"runtime: ok states={len(states)} covered={'-' if covered is None else f'{float(covered):.1f}'} "
            f"sum_hours_ok={'да' if ok else 'нет'}")


def probe_calc(stand: Stand, *, sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """Проба форм окна (Д9-R93): ряд last_8h на 8 точек по первому живому аналоговому из "_pv" и наработка за вчера
    по первому живому тегу состояния из "_run". Только коды — тегов и значений нет."""
    codes = []
    tag_status, tag = _first_live(stand, "_pv", "analog", sleep)
    if tag is None:
        codes.append(f"series: {tag_status}")
    else:
        out = calc_patient(stand, "tag_series", {"tags": [tag], "period": "last_8h", "points": PROBE_POINTS},
                           sleep=sleep)
        codes.append(_series_code(out, tag))
    tag_status, tag = _first_live(stand, "_run", "discrete", sleep)
    if tag is None:
        codes.append(f"runtime: {tag_status}")
    else:
        out = calc_patient(stand, "calc_runtime", {"tags": [tag], "period": "yesterday"}, sleep=sleep)
        codes.append(_runtime_code(out, tag))
    return codes


# --- конфиг окна и файл случаев ---

@dataclass(frozen=True)
class MatchConfig:
    """Конфиг «агент = калькулятор» ($JOB/pcbk-d9/live/match.config.json): stand.json (секреты — secrets/ рядом с
    ним: пароль учётки — <login>.pw, токен ops — ops.data-token), место и учётка прогона, файл случаев, OUT, срок
    хода, место-источник случаев окна (source.place)."""
    stand: Path
    place: int
    login: str
    cases: Path
    out_dir: Path
    turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S
    source_place: int | None = None


_M_REQUIRED = frozenset({"stand", "place", "login", "cases", "out_dir"})
_M_OPTIONAL = frozenset({"turn_timeout_s", "source"})


def _place_of(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
        raise ValueError(f"конфиг: {name} — номер места от 1 до 10")
    return value


def load_match_config(path: Path) -> MatchConfig:
    """MatchConfig из JSON; пути — относительно каталога файла. Ошибка — ValueError с полем, без значения."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"конфиг: не JSON (строка {exc.lineno})") from None
    if not isinstance(raw, dict):
        raise ValueError("конфиг: нужен объект")
    if set(raw) - _M_REQUIRED - _M_OPTIONAL:
        raise ValueError(f"конфиг: неизвестные ключи ({len(set(raw) - _M_REQUIRED - _M_OPTIONAL)})")
    missing = _M_REQUIRED - set(raw)
    if missing:
        raise ValueError(f"конфиг: нет ключей ({', '.join(sorted(missing))})")
    base = path.resolve().parent

    def rel(key: str) -> Path:
        value = raw[key]
        if not isinstance(value, str) or not value:
            raise ValueError(f"конфиг: {key} — путь")
        p = Path(value).expanduser()
        return p if p.is_absolute() else (base / p).resolve()

    if not isinstance(raw["login"], str) or not _LOGIN.fullmatch(raw["login"]):
        raise ValueError("конфиг: login — [a-z0-9-], до 40 знаков")
    timeout = raw.get("turn_timeout_s", DEFAULT_TURN_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("конфиг: turn_timeout_s — больше нуля")
    source = raw.get("source")
    if source is not None and (not isinstance(source, dict) or set(source) != {"place"}):
        raise ValueError("конфиг: source — {\"place\": N} или null")
    return MatchConfig(stand=rel("stand"), place=_place_of(raw["place"], "place"), login=raw["login"],
                       cases=rel("cases"), out_dir=rel("out_dir"), turn_timeout_s=float(timeout),
                       source_place=None if source is None else _place_of(source["place"], "source.place"))


def load_case_tags(path: Path) -> tuple[list[str], list[str]]:
    """Файл случаев ({"analog": [...], "discrete": [...]}, как bench/match.twin.json) → (аналоговые, состояния)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"analog", "discrete"}:
        raise ValueError("случаи: ровно analog и discrete")
    out = []
    for key in ("analog", "discrete"):
        tags = raw[key]
        if not isinstance(tags, list) or not all(isinstance(t, str) and _TAG.fullmatch(t) for t in tags):
            raise ValueError(f"случаи: {key} — список имён тегов")
        out.append(list(tags))
    if not 1 <= len(out[0]) + len(out[1]) <= MAX_CASE_TAGS:
        raise ValueError(f"случаи: тегов от 1 до {MAX_CASE_TAGS}")
    return out[0], out[1]


def write_case_tags(path: Path, analog: Sequence[str], discrete: Sequence[str]) -> None:
    """Файл случаев окна — 0600: имена тегов заказчика (в $JOB, не в git)."""
    write_private(Path(path), json.dumps({"analog": list(analog), "discrete": list(discrete)}, ensure_ascii=False,
                                         indent=1) + "\n")


def case_count(analog: Sequence[str], discrete: Sequence[str]) -> int:
    return len(analog) * len(ANALOG_KINDS) + len(discrete) * len(STATE_KINDS)


__all__ = ["ANALOG_KINDS", "BASIC_KINDS", "CALC_AGENT", "CALC_PROMPT", "Case", "HARD", "KINDS", "KindSpec",
           "MATCH_VERDICTS", "MatchConfig", "MatchResult", "MatchRunner", "STATE_KINDS", "build_cases",
           "calc_agent_args", "case_call", "case_count", "cases_from_agent", "hist_today", "judge_case", "key_values",
           "load_case_tags", "load_match_config", "node_tags", "period_ok", "probe_calc", "render_match",
           "write_case_tags", "write_private"]
