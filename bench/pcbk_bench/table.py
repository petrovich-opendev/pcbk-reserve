"""Таблица отбора и рекомендация (Д8-R79): записи прогонов, автомат с единицами каталога и вердикты судьи → строки
моделей; цена хода по журналу прокси, прогноз на обучение, правило рекомендации, Markdown без данных заказчика.

Источники:
- записи OUT/runs.jsonl (load_records) — все попытки по порядку; после возобновления у ключа бывает несколько записей
  (timeout, interrupted, затем done; m12);
- автомат — check_run каждой записи с единицами каталога OUT/tag_units.json (I-р4-4), пересчёт в этом процессе;
- вердикты судьи — VerdictReport Б1 (ok, missing, invalid, refused) и связь «пакет → прогон» (П8): поле runs отчёта
  и/или карта OUT/judge/map.json (load_packet_map): раздел runs (или packets), иначе записи верхнего уровня, кроме
  controls и refused. Прогон пакета — «<model>|<place>|<scenario>|<rep>» или {"model", "scenario", "rep"[, "place"]};
  вид отказа сканера — map.json["refused"] ({packet_id: "secret"|"data"} или [{"packet_id", "kind"}]);
- план (П9) — planned_counts(cfg): модель → число ключей плана по группам; без плана — полный план REPS (8/12/12);
- остаток бюджета — OUT/health_end.json (снимок /health/llm в конце прогона, А5): total_usd минус больший из
  key_spent_usd и spent_usd; снимка нет — таблица строится, L берётся верхней границей $800 с пометкой в шапке.

Что считается:
- не прогнано — каждая попытка со статусом не done, и запись done, у которой автомат первым дал код сбоя стенда
  (fails[0] ∈ STAND_CODES, П10); в качество и доли такие не идут;
- оценено — последняя запись ключа (m12): done, без сбоя стенда, вердикт судьи со статусом ok. Не оценено (not_judged):
  вердикт пропал или кривой (judge_failed), отказ сканера (judge_refused_secret/_data; вид неизвестен —
  judge_refused), иной статус вердикта, нет пакета в карте (no_packet), нет карточки сценария (no_card);
- прошёл — оценён, автомат не провалил (passed не False) и судья доволен (judge.passed_by_judge Б1, Д8-R78: все
  оценки ≥ 3, invented_norm и saved_prompt_bad ложны);
- жёсткие (hard) — автомат (AutoVerdict.hard) у каждой попытки, в том числе не прогнанной (П10), и судья
  (invented_norm, saved_prompt_bad) у последней done-записи ключа с вердиктом ok — и при сбое стенда (доводка р1,
  I1: в качество и доли такая запись не идёт); create_hard — то же у группы A (m9);
- мягкие (soft) — foreign_number автомата и numbers_not_from_tools судьи (I-р3-2); number_missing — провал без жёсткого;
- калькулятор недоступен (calc_skipped, Б3) — последняя done-запись ключа, у которой калькулятор ответил rate и после
  пауз на всё окно частоты (заметка прогонщика NOTE_CALC_RATE) или упал (заметка NOTE_CALC_FAIL, итоговая доводка
  Д8б): вторая сверка не сделана — строкой в шапке; там же — число тегов карточек (runner.card_tags), чьих единиц нет
  в OUT/tag_units.json (подготовка не прочитала их из каталога или у тега нет единицы: правило чисел берёт их единицы
  только из выводов хода);
- цена хода — Σ цены строк llm_calls всех попыток группы / Σ ходов (реплик студента; мост — тоже ход) тех сессий,
  у которых есть строки журнала (priced_turns, доводка р1 I2; исключённые ходы — в шапке); заголовок
  разговора (step 0) входит; строка без пришедшей цены — по cost_est (сумма — estimated_usd, «по оценке»); отказ
  прокси (status refused) стоит 0, но ход считается. Цена строки — как ledger.charge (row_charge);
- p90 цены хода (m22) — по ценам отдельных ходов: строка llm_calls относится к ходу своей записи по времени (сессии
  записи идут одна за другой, мост — последней): к последнему ходу, начатому не позже ts строки + TS_SLACK_S; строка
  раньше всех ходов — к первому; без ts или без меток ходов — к последнему ходу. Сумма по ходам равна цене записи;
- смесь и прогноз — forecast(c_A, c_BC, w): c = w·c_A + (1 − w)·c_BC; c × 2 500, c × 5 000, доля студента c × 500.

Правило (recommend, порядок строгий): 1) invented_norm или number_mismatch где угодно, иной жёсткий провал автомата
вне A (m9) → «не рекомендую: неуспех 2/4»; 2) оценённых меньше 2/3 плана, в A оценённых нет или меньше 2/3 плана A,
нет прогноза или оценки → «данных мало»; 3) в A прошедших меньше 5/6 оценённых или жёсткий провал в A → «не проходит
по успеху 1»; 4) прогноз на 5 000 ходов > L = min($800, остаток − $50) → «не по бюджету»; 5) из оставшихся — самая
дешёвая по прогнозу на 5 000 ходов среди тех, чья оценка ниже лучшей меньше чем на 10 % (m10): «рекомендую», прочим —
«годна».
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .cards import CardSet, load_cards
from .checks import HARD_CODES, STAND_CODES, AutoVerdict, check_run
from .config import PROVIDERS, BenchConfig, load_config
from .judge import check_verdicts, controls_ok, passed_by_judge
from .record import RunRecord, Turn
from .runner import NOTE_CALC_FAIL, card_tags, load_records, load_tag_units, plan_runs
from .scenarios import BY_ID, REPS, SCENARIO_IDS

log = logging.getLogger("pcbk_bench.table")

REPO = Path(__file__).resolve().parents[2]
OPENCODE_JSON = REPO / "student" / "config" / "opencode.json"
GROUPS = ("create", "number", "trap")
LETTER = {"create": "A", "number": "B", "trap": "C"}
CONSTRUCTOR_SHARE = 0.1                     # w — доля ходов Конструктора в обучении
TOTAL_USD = 1000.0                          # бюджет, если в снимке нет total_usd (m11)
LIMIT_USD = 800.0
RESERVE_USD = 50.0
TURNS = {"2500": 2500, "5000": 5000, "student": 500}
QUALITY_NOISE = 0.10
PRICED = ("stream", "body", "generation")   # ledger.PRICED: цена пришла
TS_SLACK_S = 1.0
JUDGE_HARD = ("invented_norm", "saved_prompt_bad")
JUDGE_SOFT = "numbers_not_from_tools"
AUTO_SOFT = ("foreign_number",)
RULE1_ANYWHERE = ("invented_norm", "number_mismatch")
NAMED_HARD = ("invented_norm", "number_mismatch", "saved_without_consent", "saved_after_change", "saved_prompt_bad")
NOT_RUN = ("timeout", "stand_error", "capped", "interrupted")
NOT_JUDGED = ("judge_failed", "judge_refused_secret", "judge_refused_data")

VERDICT_HARD = "не рекомендую: неуспех 2/4"
VERDICT_FEW = "данных мало"
VERDICT_CREATE = "не проходит по успеху 1"
VERDICT_BUDGET = "не по бюджету"
VERDICT_BEST = "рекомендую"
VERDICT_FIT = "годна"
NO_FIT = "годной модели нет: варианты — запасные или премиум с ограничением рассуждения"
OWNER_QUESTIONS = (                         # research/08, «Вопросы владельцу вместе с таблицей»
    "флекс-точка Gemini — вдвое дешевле, но медленнее;",
    "страна хостера для GLM — США (Fireworks) или Сингапур (Z.AI), цена одинаковая;",
    "премиум — только при месячном обучении или с ограниченным рассуждением.",
)


@dataclass
class ModelRow:
    """Строка таблицы на модель (Д8-R79, m21).

    - model — имя без провайдера места («z-ai/glm-5.3», П13); point — провайдеры строк журнала (частые — первыми);
    - planned — ключей плана модели; judged — оценённых; create_planned, create_judged, create_passed — то же у A;
      groups — по группам create/number/trap: {"planned", "judged", "passed"};
    - not_run — не прогнано по причинам (timeout, stand_error, capped, interrupted); not_judged — не оценено
      (judge_failed, judge_refused_secret, judge_refused_data, judge_refused, no_packet, no_card);
    - bot_no_draft — прогонов A, где бот так и не увидел черновика;
    - calc_skipped — прогонов (последняя done-запись ключа), где сверка калькулятором не сделана: ответ rate и после
      пауз на всё окно частоты или сбой калькулятора (калькулятор недоступен, Б3); в шапке Markdown — строкой;
    - hard — жёсткие провалы поштучно (автомат и судья), create_hard — они же у A; soft — мягкие сигналы
      (foreign_number, numbers_not_from_tools);
    - quality — средняя оценка судьи по всем пунктам оценённых; quality_groups — по группам;
    - cost — цена хода по группам: create, number, trap и data (B и C вместе), $; turn — смесь; p90_turn — p90 цены
      хода; estimated_usd — сколько из цены строк взято по оценке (cost_est); turns — ходов в цене хода (сессии со
      строками журнала, priced_turns); turns_excluded — ходов без строк журнала, исключённых из цены (I2);
    - reasoning_per_turn — токенов рассуждения на ход; cache_share — доля входа из кэша (cached / prompt);
    - f2500, f5000 — прогноз на 2 500 и 5 000 ходов, student — доля студента за 2 месяца (500 ходов), $;
    - time_median_s, time_p90_s — время хода, справочно;
    - notes — заметки автомата и прогонщика (без данных; в Markdown не идут).
    """
    model: str
    point: str = ""
    planned: int = 0
    judged: int = 0
    create_planned: int = 0
    create_judged: int = 0
    create_passed: int = 0
    groups: dict[str, dict[str, int]] = field(default_factory=dict)
    not_run: dict[str, int] = field(default_factory=dict)
    not_judged: dict[str, int] = field(default_factory=dict)
    bot_no_draft: int = 0
    calc_skipped: int = 0
    hard: dict[str, int] = field(default_factory=dict)
    create_hard: dict[str, int] = field(default_factory=dict)
    soft: dict[str, int] = field(default_factory=dict)
    quality: float | None = None
    quality_groups: dict[str, float] = field(default_factory=dict)
    cost: dict[str, float | None] = field(default_factory=dict)
    turn: float | None = None
    p90_turn: float | None = None
    estimated_usd: float = 0.0
    turns: int = 0
    turns_excluded: int = 0
    reasoning_per_turn: float | None = None
    cache_share: float | None = None
    f2500: float | None = None
    f5000: float | None = None
    student: float | None = None
    time_median_s: float | None = None
    time_p90_s: float | None = None
    notes: list[str] = field(default_factory=list)


# --- имена, числа, цена строки ---

def model_name(ref: str) -> str:
    """«pcbk/z-ai/glm-5.3» → «z-ai/glm-5.3» (П13): снимается только провайдер места (pcbk, pcbk-or)."""
    head, sep, tail = ref.partition("/")
    return tail if sep and head in PROVIDERS else ref


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def row_charge(row: Mapping[str, Any]) -> tuple[float, bool]:
    """Цена строки llm_calls и признак «по оценке»: отказ прокси — 0; дальше как ledger.charge — cost_source none —
    cost или 0, пришедшая цена (stream, body, generation; строка без cost_source — её cost) — cost, прочее (pending,
    unknown, цены нет) — cost_est."""
    if row.get("status") == "refused":
        return 0.0, False
    source = row.get("cost_source")
    cost = _num(row.get("cost"))
    if source == "none":
        return (cost or 0.0), False
    if cost is not None and (source is None or source in PRICED):
        return cost, False
    return (_num(row.get("cost_est")) or 0.0), True


def _turns(rec: RunRecord) -> list[Turn]:
    """Ходы записи: реплики студента и мост."""
    return list(rec.turns) + ([rec.bridge] if rec.bridge is not None else [])


def priced_turns(rec: RunRecord) -> list[Turn]:
    """Ходы, которые идут в цену хода (доводка р1, I2): только ходы сессий, у которых есть строки журнала прокси.
    Сессия хода — sessions[0] у разговора (B/C и Конструктор A), sessions[-1] у моста; без sessions — ни одного хода
    (запись исключается из цены целиком). Иначе ход без строк (сбой стенда до вызова LLM) занижал бы цену хода."""
    if not rec.sessions:
        return []
    seen = {r.get("session") for r in rec.llm_rows}
    out = list(rec.turns) if rec.sessions[0] in seen else []
    if rec.bridge is not None and rec.sessions[-1] in seen:
        out.append(rec.bridge)
    return out


def _ts(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def _per_turn(rec: RunRecord) -> list[float]:
    """Цена каждого хода записи (m22, см. шапку модуля) — по ходам priced_turns."""
    turns = sorted(priced_turns(rec), key=lambda t: t.started)
    if not turns:
        return []
    out = [0.0] * len(turns)
    timed = any(t.started > 0 for t in turns)
    for r in rec.llm_rows:
        amount, _ = row_charge(r)
        at = _ts(r.get("ts"))
        idx = len(turns) - 1
        if at is not None and timed:
            idx = 0
            for i, t in enumerate(turns):
                if t.started <= at + TS_SLACK_S:
                    idx = i
        out[idx] += amount
    return out


def _p90(values: list[float]) -> float | None:
    """p90 по ближайшему рангу."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]


# --- цена хода и прогноз ---

def turn_costs(recs: list[RunRecord]) -> dict[str, float | None]:
    """Цена хода: create (A), number (B), trap (C), data (B и C) — Σ цены строк / Σ ходов сессий со строками журнала
    (priced_turns); группа без таких ходов — None."""
    cost: dict[str, float] = defaultdict(float)
    turns: Counter = Counter()
    for r in recs:
        sc = BY_ID.get(r.key.scenario)
        if sc is None:
            continue
        cost[sc.group] += sum(row_charge(x)[0] for x in r.llm_rows)
        turns[sc.group] += len(priced_turns(r))

    def per(groups: tuple[str, ...]) -> float | None:
        n = sum(turns[g] for g in groups)
        return sum(cost[g] for g in groups) / n if n else None

    return {"create": per(("create",)), "number": per(("number",)), "trap": per(("trap",)),
            "data": per(("number", "trap"))}


def forecast(c_create: float, c_data: float, w: float = CONSTRUCTOR_SHARE) -> dict[str, float]:
    """Смесь c = w·c_A + (1 − w)·c_BC и прогноз: turn, "2500", "5000" ходов и student (500 ходов за 2 месяца)."""
    c = w * c_create + (1 - w) * c_data
    return {"turn": c, **{k: c * n for k, n in TURNS.items()}}


# --- остаток бюджета ---

def read_health_end(out: Path) -> dict:
    raw = json.loads((Path(out) / "health_end.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("health_end.json: нужен объект")
    return raw


def spent_of(health: Mapping[str, Any]) -> tuple[float, str]:
    """Расход для остатка — больший из key_spent_usd (по ключу, "key") и spent_usd журнала прокси ("proxy"); ключа нет
    (null, m-р4-8) — журнал прокси. Нет обоих — ValueError."""
    key = _num(health.get("key_spent_usd"))
    proxy = _num(health.get("spent_usd"))
    if key is None and proxy is None:
        raise ValueError("health_end.json: нет ни key_spent_usd, ни spent_usd")
    if proxy is None or (key is not None and key >= proxy):
        return float(key), "key"
    return proxy, "proxy"


def budget_left(out: Path) -> float:
    """Остаток бюджета по OUT/health_end.json: total_usd снимка (нет — $1 000, m11) минус расход (spent_of: больший
    из расхода по ключу и по журналу прокси)."""
    health = read_health_end(out)
    spent, _ = spent_of(health)
    total = _num(health.get("total_usd"))
    return (total if total is not None and total > 0 else TOTAL_USD) - spent


def limit_usd(left: float) -> float:
    """Предел прогноза на 5 000 ходов: L = min($800, остаток − $50)."""
    return min(LIMIT_USD, left - RESERVE_USD)


# --- план и карта пакетов ---

def _full_plan() -> dict[str, int]:
    per_group = Counter(BY_ID[sid].group for sid in SCENARIO_IDS)
    return {g: per_group[g] * REPS[g] for g in GROUPS}


def planned_counts(cfg: BenchConfig) -> dict[str, dict[str, int]]:
    """Модель (без провайдера) → число ключей plan_runs(cfg) её места по группам (П9; фильтр scenarios — тоже)."""
    names = [model_name(ref) for _, ref in sorted(cfg.places.items())]
    if len(set(names)) != len(names):
        raise ValueError("план: одна модель на двух местах (pcbk и pcbk-or) — в таблице не различить")
    out = {name: {g: 0 for g in GROUPS} for name in names}
    for k in plan_runs(cfg):
        out[model_name(cfg.places[k.place])][BY_ID[k.scenario].group] += 1
    return out


def load_packet_map(out: Path) -> dict:
    """OUT/judge/map.json (пишет judge-pack Б1); нет файла — {}."""
    try:
        raw = json.loads((Path(out) / "judge" / "map.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("judge/map.json: нужен объект")
    return raw


_Ref = tuple[str | None, int | None, str, int]


def _ref(value: Any) -> _Ref | None:
    """Прогон пакета → (модель без провайдера, место, сценарий, повтор); не разобралось — None."""
    try:
        if isinstance(value, str):
            model, place, sid, rep = value.rsplit("|", 3)
            return (model_name(model) or None), int(place), sid, int(rep)
        if isinstance(value, Mapping) and "scenario" in value and "rep" in value:
            model, place = value.get("model"), value.get("place")
            ref = (model_name(model) if isinstance(model, str) and model else None,
                   None if place is None else int(place), str(value["scenario"]), int(value["rep"]))
            return ref if ref[0] is not None or ref[1] is not None else None
    except (TypeError, ValueError):
        return None
    return None


def _packet_runs(verdicts: Any, packet_map: Mapping | None) -> dict[str, _Ref]:
    pm = packet_map or {}
    section = next((pm[k] for k in ("runs", "packets") if isinstance(pm.get(k), Mapping)), None)
    raw: dict = dict(section) if section is not None else {k: v for k, v in pm.items()
                                                           if k not in ("controls", "refused")}
    raw.update(getattr(verdicts, "runs", None) or {})
    out = {}
    for pid, value in raw.items():
        ref = _ref(value)
        if ref is not None:
            out[str(pid)] = ref
    return out


def _refused_kinds(verdicts: Any, packet_map: Mapping | None) -> dict[str, str]:
    """Вид отказа сканера: карта пакетов, а чего в ней нет — VerdictReport.refused_kind (m6)."""
    raw = (packet_map or {}).get("refused")
    pairs: list = list((getattr(verdicts, "refused_kind", None) or {}).items())
    if isinstance(raw, Mapping):
        pairs += list(raw.items())
    elif isinstance(raw, list):
        pairs += [(x.get("packet_id"), x.get("kind")) for x in raw if isinstance(x, Mapping)]
    out = {}
    for pid, kind in pairs:
        if isinstance(pid, str) and isinstance(kind, str) and kind:
            out[pid] = kind if kind.startswith("judge_refused") else f"judge_refused_{kind}"
    return out


_Judge = tuple[str | None, str, Any]


def _judge_index(verdicts: Any, packet_map: Mapping | None) -> tuple[dict, dict]:
    """(место, сценарий, повтор) → (модель, статус, вердикт); пакеты без места — (модель, сценарий, повтор) → …"""
    ok = getattr(verdicts, "ok", None) or {}
    refused = set(getattr(verdicts, "refused", None) or ())
    kinds = _refused_kinds(verdicts, packet_map)
    by_place: dict[tuple, _Judge] = {}
    by_model: dict[tuple, _Judge] = {}
    for pid, (model, place, sid, rep) in _packet_runs(verdicts, packet_map).items():
        v = ok.get(pid)
        if v is not None:
            status = getattr(v, "status", "ok") or "ok"
        elif pid in refused:
            status = kinds.get(pid, "judge_refused")
        else:
            status = "judge_failed"                      # вердикт пропал или кривой (missing, invalid)
        if place is not None:
            by_place[(place, sid, rep)] = (model, status, v)
        else:
            by_model[(model, sid, rep)] = (model, status, v)
    return by_place, by_model


def _judge_of(rec: RunRecord, by_place: dict, by_model: dict) -> tuple[str, Any]:
    name = model_name(rec.model)
    entry = by_place.get((rec.key.place, rec.key.scenario, rec.key.rep))
    if entry is not None and entry[0] in (None, name):
        return entry[1], entry[2]
    entry = by_model.get((name, rec.key.scenario, rec.key.rep))
    if entry is not None:
        return entry[1], entry[2]
    return "no_packet", None


# --- строки моделей ---

def _plan_of(value: Any) -> tuple[dict[str, int], int]:
    if isinstance(value, Mapping):
        groups = {g: int(value.get(g, 0)) for g in GROUPS}
        return groups, sum(groups.values())
    return {}, int(value)                               # только число ключей: по группам неизвестно


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _row(name: str, plan: tuple[dict[str, int], int], recs: list[RunRecord], cards: CardSet,
         tag_units: Mapping[str, str], index: tuple[dict, dict], w: float) -> ModelRow:
    groups_plan, total = plan
    autos: dict[int, AutoVerdict | None] = {}
    for r in recs:
        card = cards.cards.get(r.key.scenario)
        autos[id(r)] = check_run(r, card, tag_units) if card is not None else None
    not_run: Counter = Counter()
    not_judged: Counter = Counter()
    hard: Counter = Counter()
    create_hard: Counter = Counter()
    soft: Counter = Counter()
    notes: list[str] = []
    latest: dict = {}
    for r in recs:
        latest[r.key] = r
        av = autos[id(r)]
        group = BY_ID[r.key.scenario].group
        notes += [n for n in r.notes if n not in notes]
        if av is not None:
            notes += [n for n in av.notes if n not in notes]
            for code in av.hard:                         # жёсткие — у каждой попытки, и у не прогнанной (П10)
                hard[code] += 1
                if group == "create":
                    create_hard[code] += 1
            soft.update(c for c in AUTO_SOFT if c in av.fails)
        if r.status != "done":
            not_run[r.status] += 1
        elif av is not None and av.fails and av.fails[0] in STAND_CODES:
            not_run[av.fails[0]] += 1

    judged: Counter = Counter()
    passed: Counter = Counter()
    scores: dict[str, list[float]] = defaultdict(list)
    bot_no_draft = 0
    calc_skipped = 0
    for r in latest.values():                            # оценка — по последней записи ключа (m12)
        av = autos[id(r)]
        group = BY_ID[r.key.scenario].group
        if r.status != "done":
            continue
        if any(isinstance(e, Mapping) and e.get("status") == "rate" for e in r.calc.values()) or any(
                isinstance(n, str) and n.startswith(NOTE_CALC_FAIL) for n in r.notes):
            calc_skipped += 1                            # калькулятор недоступен (rate или сбой): сверка не сделана
        status, v = _judge_of(r, *index)
        if status == "ok" and v is not None:             # находки судьи — и у записи со сбоем стенда (р1, I1)
            vh = getattr(v, "hard", None) or {}
            for code in JUDGE_HARD:
                if vh.get(code):
                    hard[code] += 1
                    if group == "create":
                        create_hard[code] += 1
            if vh.get(JUDGE_SOFT):
                soft[JUDGE_SOFT] += 1
        if av is not None and av.fails and av.fails[0] in STAND_CODES:
            continue                                     # сбой стенда: не в качество, доли и judged (П10)
        if av is None:
            not_judged["no_card"] += 1
            continue
        if "bot_no_draft" in av.fails:
            bot_no_draft += 1
        if status != "ok" or v is None:
            not_judged[status] += 1
            continue
        judged[group] += 1
        scores[group] += [float(s) for s in (getattr(v, "scores", None) or {}).values() if _num(s) is not None]
        if av.passed is not False and passed_by_judge(v):
            passed[group] += 1

    costs = turn_costs(recs)
    rows = [x for r in recs for x in r.llm_rows]
    n_turns = sum(len(priced_turns(r)) for r in recs)
    excluded = sum(len(_turns(r)) for r in recs) - n_turns
    prompt = sum(_num(x.get("prompt_tokens")) or 0.0 for x in rows)
    cached = sum(_num(x.get("cached_tokens")) or 0.0 for x in rows)
    reasoning = sum(_num(x.get("reasoning_tokens")) or 0.0 for x in rows)
    durations = [t.ended - t.started for r in recs for t in _turns(r) if t.started > 0 and t.ended >= t.started]
    providers = Counter(x["provider"] for x in rows if isinstance(x.get("provider"), str) and x["provider"])
    f = forecast(costs["create"], costs["data"], w) if costs["create"] is not None and costs["data"] is not None \
        else None
    return ModelRow(
        model=name, point=", ".join(p for p, _ in providers.most_common()), planned=total,
        judged=sum(judged.values()), create_planned=groups_plan.get("create", 0), create_judged=judged["create"],
        create_passed=passed["create"],
        groups={g: {"planned": groups_plan.get(g, 0), "judged": judged[g], "passed": passed[g]} for g in GROUPS},
        not_run=dict(not_run), not_judged=dict(not_judged), bot_no_draft=bot_no_draft, calc_skipped=calc_skipped,
        hard=dict(hard),
        create_hard=dict(create_hard), soft=dict(soft), quality=_mean([s for g in GROUPS for s in scores[g]]),
        quality_groups={g: m for g in GROUPS if (m := _mean(scores[g])) is not None}, cost=costs,
        turn=f["turn"] if f else None, p90_turn=_p90([c for r in recs for c in _per_turn(r)]),
        estimated_usd=sum(a for a, est in map(row_charge, rows) if est), turns=n_turns, turns_excluded=excluded,
        reasoning_per_turn=reasoning / n_turns if n_turns else None, cache_share=cached / prompt if prompt else None,
        f2500=f["2500"] if f else None, f5000=f["5000"] if f else None, student=f["student"] if f else None,
        time_median_s=statistics.median(durations) if durations else None, time_p90_s=_p90(durations), notes=notes)


def aggregate(recs: list[RunRecord], cards: CardSet, verdicts: Any, tag_units: Mapping[str, str] = {}, *,
              planned: Mapping[str, Any] | None = None, packet_map: Mapping | None = None,
              w: float = CONSTRUCTOR_SHARE) -> list[ModelRow]:
    """Строки моделей (см. шапку модуля). planned — модель (с провайдером или без) → {группа: ключей} или число
    ключей (П9); модели плана без записей — тоже строки. Порядок — план, затем модели записей вне плана."""
    plans: dict[str, tuple[dict[str, int], int]] = {}
    for ref, value in (planned or {}).items():
        plans[model_name(ref)] = _plan_of(value)
    by_model: dict[str, list[RunRecord]] = defaultdict(list)
    refs: dict[str, str] = {}
    for r in recs:
        if r.key.scenario not in BY_ID:
            log.warning("таблица: запись неизвестного сценария — пропущена")
            continue
        name = model_name(r.model)
        if refs.setdefault(name, r.model) != r.model:
            raise ValueError("таблица: одна модель через два провайдера мест — в таблице не различить")
        if name not in plans:
            plans[name] = _plan_of(_full_plan())
        by_model[name].append(r)
    index = _judge_index(verdicts, packet_map)
    return [_row(name, plan, by_model[name], cards, tag_units, index, w) for name, plan in plans.items()]


# --- правило ---

def _hard_failure(row: ModelRow) -> bool:
    if any(row.hard.get(c, 0) > 0 for c in RULE1_ANYWHERE):
        return True
    return any(row.hard.get(c, 0) > row.create_hard.get(c, 0) for c in HARD_CODES)      # вне A (m9)


def _too_few(row: ModelRow) -> bool:
    return (row.planned <= 0 or 3 * row.judged < 2 * row.planned or row.create_judged <= 0
            or 3 * row.create_judged < 2 * row.create_planned or row.f5000 is None or row.quality is None)


def _create_fails(row: ModelRow) -> bool:
    return 6 * row.create_passed < 5 * row.create_judged or any(n > 0 for n in row.create_hard.values())


def recommend(rows: list[ModelRow], budget_left: float) -> list[str]:
    """Вердикт каждой строки по правилу Д8-R79 (см. шапку модуля), в порядке строк."""
    cap = limit_usd(budget_left)
    labels: list[str | None] = []
    for r in rows:
        if _hard_failure(r):
            labels.append(VERDICT_HARD)
        elif _too_few(r):
            labels.append(VERDICT_FEW)
        elif _create_fails(r):
            labels.append(VERDICT_CREATE)
        elif r.f5000 > cap:
            labels.append(VERDICT_BUDGET)
        else:
            labels.append(None)
    fit = [i for i, label in enumerate(labels) if label is None]
    if fit:
        best = max(rows[i].quality for i in fit)
        near = [i for i in fit if best - rows[i].quality < QUALITY_NOISE * best]
        pick = min(near, key=lambda i: (rows[i].f5000, -rows[i].quality, i))
        for i in fit:
            labels[i] = VERDICT_BEST if i == pick else VERDICT_FIT
    return [str(label) for label in labels]


# --- Markdown ---

def _dec(x: float, digits: int) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def _usd(x: Any, digits: int = 2) -> str:
    x = _num(x)
    if x is None:
        return "—"
    if digits == 2 and abs(x - round(x)) < 0.005:
        return f"${round(x)}"
    return "$" + _dec(x, digits)


def _counts(d: Mapping[str, int]) -> str:
    items = [f"{k} {v}" for k, v in d.items() if v]
    return "; ".join(items) if items else "—"


def _frac(row: ModelRow, group: str) -> str:
    g = row.groups.get(group)
    if g is None:
        return f"{row.create_passed}/{row.create_planned}" if group == "create" else "—"
    return f"{g['passed']}/{g['planned'] or '—'}"


def _total(row: ModelRow) -> str:
    if not row.groups:
        return f"—/{row.planned}"
    return f"{sum(g['passed'] for g in row.groups.values())}/{row.planned}"


def _score(x: float | None) -> str:
    return "—" if x is None else _dec(x, 2)


def _header(meta: Mapping[str, Any], rows: list[ModelRow]) -> list[str]:
    reps = meta.get("reps") if isinstance(meta.get("reps"), Mapping) else None
    reps_text = ", ".join(f"{LETTER[g]}×{reps[g]}" for g in GROUPS if g in reps) if reps else "—"
    if meta.get("scenarios"):
        reps_text += f"; фильтр сценариев: {', '.join(meta['scenarios'])}"
    retries = meta.get("judge_retries")
    data = ("пакеты с настоящими числами — по «да» владельца (файл согласия)" if meta.get("real_data")
            else "пакеты без цифр")
    left = _num(meta.get("budget_left"))
    source = "по ключу" if meta.get("budget_source") == "key" else "остаток по журналу прокси"
    budget = (f"остаток бюджета {_usd(left)} ({source}); предел прогноза L = {_usd(limit_usd(left))} "
              f"(min($800, остаток − $50))" if left is not None else
              f"остаток бюджета — нет снимка /health/llm (OUT/health_end.json): предел прогноза взят верхней "
              f"границей L = {_usd(LIMIT_USD)}, остаток не проверен — решает владелец")

    def total(key: str) -> str:
        return _usd(meta.get(key)) if _num(meta.get(key)) is not None else "нет данных"
    w = _num(meta.get("w"))
    lines = [
        f"- Дата: {meta.get('date', '—')}; прогон `{meta.get('run_id', '—')}`; "
        f"снимок цен Б0: {meta.get('prices', '—')}.",
        f"- Судья: субагенты Opus 5.5 по подписке; пачек — {meta.get('judge_batches', '—')}, повторных заходов — "
        f"{'—' if retries is None else retries}; {data}; по числам жёстко — только автомат, судья — мягкий сигнал.",
        f"- Повторы: {reps_text}.",
        f"- Расход прогона по журналу прокси: {total('spent_usd')} (потолок {_usd(meta.get('cap_usd'))}). Расход ключа "
        f"за всё время: {total('key_spent_usd')}; журнала прокси за всё время: {total('proxy_spent_usd')}.",
        f"- {budget[0].upper()}{budget[1:]}.",
        f"- Вес Конструктора w = {'—' if w is None else _dec(w, 2)}: цена хода c = w × c_A + (1 − w) × c_BC; прогноз — "
        f"c × 2 500 и c × 5 000 ходов, доля студента за 2 месяца — c × 500.",
    ]
    controls = meta.get("controls") if isinstance(meta.get("controls"), Mapping) else {}
    if controls:
        marks = ", ".join(f"{j} {'✓' if ok is True else ('✗' if ok is False else '—')}"
                          for j, ok in sorted(controls.items()))
        miss = any(ok is not True for ok in controls.values())
        lines.append(f"- Контроль судьи: {marks}" + (" — промах контроля, решает владелец." if miss else "."))
    else:
        lines.append("- Контроль судьи J1–J4: нет данных — решает владелец.")
    lines.append("- Доля A — верхняя оценка успеха 1 со сценарным студентом.")
    excluded = [(r.model, r.turns_excluded) for r in rows if r.turns_excluded]
    if excluded:
        per = "; ".join(f"`{m}` — {n}" for m, n in excluded)
        lines.append(f"- Ходов без строк журнала прокси (исключены из цены хода): {sum(n for _, n in excluded)} "
                     f"({per}).")
    skipped = [(r.model, r.calc_skipped) for r in rows if r.calc_skipped]
    if skipped:
        per = "; ".join(f"`{m}` — {n}" for m, n in skipped)
        lines.append(f"- Сверка калькулятором не сделана (калькулятор недоступен: rate после пауз на всё окно "
                     f"частоты или сбой): {sum(n for _, n in skipped)} прогонов ({per}).")
    units = meta.get("units_missing") if isinstance(meta.get("units_missing"), Mapping) else None
    if units and units.get("missing"):
        lines.append(f"- Единиц тегов карточек нет в OUT/tag_units.json у {units['missing']} из "
                     f"{units.get('total', '—')} (подготовка не прочитала их из каталога — калькулятор недоступен — "
                     f"или у тега нет единицы): правило чисел автомата берёт единицы этих тегов только из выводов "
                     f"хода.")
    if _num(meta.get("pending")):
        lines.append(f"- Строк журнала прокси с ценой в ожидании (pending): {int(meta['pending'])} — "
                     "их цена по оценке.")
    if meta.get("judge_family"):
        lines.append("- Судья той же семьи, что и модель-финалист (Claude): оценку судьи читать с поправкой.")
    return lines


RULE_TEXT = (
    "## Правило выбора (предложение, решение — владелец), порядок строгий",
    "",
    "1. Жёсткие провалы — первыми: хоть один `invented_norm` (судья) или `number_mismatch` (автомат), или иной "
    "жёсткий провал автомата вне A → «не рекомендую: неуспех 2/4». `numbers_not_from_tools` судьи — мягкий сигнал.",
    "2. Данных мало: оценённых меньше 2/3 запланированных (или в A меньше 2/3 плана A) → «данных мало».",
    "3. Успех 1: в A прошедших меньше 5/6 оценённых или есть жёсткий провал группы A → «не проходит по успеху 1».",
    "4. Бюджет: прогноз на 5 000 ходов больше L → «не по бюджету».",
    "5. Качество: из оставшихся — лучшая по средней оценке судьи; если более дешёвая (по прогнозу на 5 000 ходов) "
    "ниже лучшей меньше чем на 10 % — самая дешёвая из таких. Ей — «рекомендую», прочим проходящим — «годна».",
)


def render_markdown(rows: list[ModelRow], meta: Mapping[str, Any]) -> str:
    """Таблица отбора в Markdown: шапка, правило, качество, цена и прогноз, итог, вопросы research/08. Только модели,
    числа и коды: заметок строк, тегов, узлов и текстов разговоров нет."""
    if meta.get("verdicts") is not None:
        verdicts = [str(v) for v in meta["verdicts"]]
    elif _num(meta.get("budget_left")) is not None:
        verdicts = recommend(rows, float(meta["budget_left"]))
    else:
        verdicts = ["—"] * len(rows)
    out = [f"# Таблица отбора модели — прогон {meta.get('run_id', '—')}", "", *_header(meta, rows), "", *RULE_TEXT,
           ""]
    out += ["## Качество", "",
            "| Модель | Точка | Прошло A | Прошло B | Прошло C | Всего | Не прогнано | Не оценено | bot_no_draft "
            "| invented_norm | number_mismatch | saved_without_consent | saved_after_change | saved_prompt_bad "
            "| Прочие жёсткие | foreign_number | numbers_not_from_tools | Оценка судьи: всего / A / B / C | Вердикт |",
            "|" + "---|" * 19]
    for r, v in zip(rows, verdicts):
        other = {k: n for k, n in r.hard.items() if k not in NAMED_HARD}
        q = " / ".join(_score(x) for x in (r.quality, *(r.quality_groups.get(g) for g in GROUPS)))
        cells = [f"`{r.model}`", r.point or "—", _frac(r, "create"), _frac(r, "number"), _frac(r, "trap"), _total(r),
                 _counts(r.not_run), _counts(r.not_judged), str(r.bot_no_draft),
                 *(str(r.hard.get(c, 0)) for c in NAMED_HARD), _counts(other),
                 str(r.soft.get("foreign_number", 0)), str(r.soft.get(JUDGE_SOFT, 0)), q, v]
        out.append("| " + " | ".join(cells) + " |")
    out += ["", "## Цена хода и прогноз", "",
            "| Модель | Ход A | Ход B | Ход C | Смесь | p90 хода | По оценке | Рассуждение, токенов на ход | Доля кэша "
            "| 2 500 ходов | 5 000 ходов | Доля студента за 2 месяца | Время хода: медиана / p90, с |",
            "|" + "---|" * 13]
    for r in rows:
        cache = "—" if r.cache_share is None else f"{round(r.cache_share * 100)} %"
        reasoning = "—" if r.reasoning_per_turn is None else str(round(r.reasoning_per_turn))
        times = " / ".join("—" if x is None else str(round(x)) for x in (r.time_median_s, r.time_p90_s))
        est = f"по оценке {_usd(r.estimated_usd)}" if r.estimated_usd else "—"
        cells = [f"`{r.model}`", *(_usd(r.cost.get(g), 4) for g in GROUPS), _usd(r.turn, 4), _usd(r.p90_turn, 4),
                 est, reasoning, cache, _usd(r.f2500), _usd(r.f5000), _usd(r.student), times]
        out.append("| " + " | ".join(cells) + " |")
    best = [r.model for r, v in zip(rows, verdicts) if v == VERDICT_BEST]
    out += ["", "## Итог", ""]
    out.append(f"Рекомендую (предложение, решение — владелец): `{best[0]}`." if best else f"«{NO_FIT}».")
    out += ["", "## Вопросы владельцу (research/08)", "", *(f"- {q}" for q in OWNER_QUESTIONS), ""]
    return "\n".join(out)


# --- CLI: python -m pcbk_bench table CONFIG [--constructor-share 0.1] ---

class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise _UsageError(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m pcbk_bench table", description="Таблица отбора и рекомендация (Д8-R79)")
    p.add_argument("config", type=Path)
    p.add_argument("--constructor-share", type=float, default=CONSTRUCTOR_SHARE)
    p.add_argument("--judge-retries", type=int, default=None)
    return p


def _judge_api():
    """check_verdicts и controls_ok судьи (Б1); отдельной функцией — для подмены в тестах CLI."""
    return check_verdicts, controls_ok


def _run_date(out: Path) -> str:
    try:
        started = json.loads((out / "progress.json").read_text(encoding="utf-8")).get("started_at")
        return datetime.fromisoformat(started).date().isoformat()
    except (OSError, ValueError, TypeError, AttributeError):
        return date.today().isoformat()


def _prices(cfg: BenchConfig) -> str:
    snap = cfg.stand.parent / "snapshot-b.json" if cfg.stand is not None else None
    if snap is None or not snap.exists():
        return "в журнале D8b.md (Б0)"
    stamp = datetime.fromtimestamp(snap.stat().st_mtime, timezone.utc).date().isoformat()
    return f"{snap.name} от {stamp}"


def build(cfg: BenchConfig, w: float = CONSTRUCTOR_SHARE, judge_retries: int | None = None
          ) -> tuple[list[ModelRow], dict]:
    """Строки и шапка по конфигу прогона (П7): OUT, карточки и план — из конфига."""
    if cfg.out_dir is None:
        raise ValueError("конфиг: out_dir — путь")
    out = Path(cfg.out_dir)
    check_verdicts, controls_ok = _judge_api()
    report = check_verdicts(out)
    pmap = load_packet_map(out)
    cards = load_cards(cfg.cards)
    units = load_tag_units(out)
    rows = aggregate(load_records(out), cards, report, units, planned=planned_counts(cfg), packet_map=pmap, w=w)
    read = {str(k).upper() for k in units}
    tags = card_tags(cards)
    units_missing = {"missing": sum(1 for t in tags if t.upper() not in read), "total": len(tags)}
    try:
        health: dict = read_health_end(out)
    except FileNotFoundError:
        health = {}                                      # снимка нет — L верхней границей, пометка в шапке
    if health:
        _, source = spent_of(health)
        left: float | None = budget_left(out)
    else:
        source, left = None, None
    verdicts = recommend(rows, TOTAL_USD if left is None else left)
    cmap = pmap.get("controls")
    controls = {str(k): v for k, v in controls_ok(report, cmap).items()} if isinstance(cmap, Mapping) and cmap else {}
    run = health.get("run") if isinstance(health.get("run"), Mapping) else {}
    family = any(("claude" in r.model or r.model.startswith("anthropic/")) and v in (VERDICT_BEST, VERDICT_FIT)
                 for r, v in zip(rows, verdicts))
    meta = {"run_id": cfg.run_id, "date": _run_date(out), "prices": _prices(cfg),
            "judge_batches": sum(1 for p in (out / "judge").glob("batch-*") if p.is_dir()),
            "judge_retries": judge_retries, "real_data": pmap.get("real_data") is True,
            "reps": dict(cfg.reps), "scenarios": list(cfg.scenarios) if cfg.scenarios else None,
            "cap_usd": _num(run.get("cap_usd")) or cfg.cap_usd, "spent_usd": _num(run.get("spent_usd")),
            "key_spent_usd": _num(health.get("key_spent_usd")), "proxy_spent_usd": _num(health.get("spent_usd")),
            "budget_left": left, "budget_source": source, "limit_usd": LIMIT_USD if left is None else limit_usd(left),
            "w": w, "controls": controls, "pending": health.get("pending"), "units_missing": units_missing,
            "judge_family": family, "verdicts": verdicts}
    return rows, meta


def main(argv: list[str] | None = None) -> int:
    """table CONFIG [--constructor-share W] [--judge-retries N]: Markdown — в stdout, всё — в OUT/table.json. Первое
    слово «table» (если его передал верх CLI) пропускается. Коды: 0 — успех, 1 — ошибка (только класс, без текста);
    KeyboardInterrupt не глотается."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["table"]:
        args_list = args_list[1:]
    parser = _parser()
    try:
        args = parser.parse_args(args_list)
        if not 0.0 <= args.constructor_share <= 1.0:
            raise _UsageError("constructor-share")
        cfg = load_config(args.config, OPENCODE_JSON)
        rows, meta = build(cfg, args.constructor_share, args.judge_retries)
        text = render_markdown(rows, meta)
        doc = {"meta": meta, "rows": [asdict(r) for r in rows], "verdicts": meta["verdicts"]}
        (Path(cfg.out_dir) / "table.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                                                      encoding="utf-8")
        sys.stdout.write(text)
        return 0
    except _UsageError:
        parser.print_usage(sys.stderr)
        return 1
    except SystemExit as exc:                  # --help
        return exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:                   # noqa: BLE001 — верх CLI: только класс, без текста (m-р4-16)
        print(type(exc).__name__, file=sys.stderr)
        return 1


__all__ = ["CONSTRUCTOR_SHARE", "ModelRow", "NO_FIT", "OPENCODE_JSON", "aggregate", "budget_left", "build", "forecast",
           "limit_usd", "load_packet_map", "main", "model_name", "planned_counts", "read_health_end",
           "recommend", "render_markdown", "row_charge", "spent_of", "turn_costs"]
