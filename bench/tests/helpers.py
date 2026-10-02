"""Общие константы и помощники тестов стенда отбора (импорт явный: from helpers import …).

rec(…) собирает RunRecord сценария, card(…) — карточку двойника. Выводы инструментов — в форме ответов службы
данных (service.py): tag_now/tag_period/catalog_search, поля items, value, time, age_s, avg, min, max, range.
Только теги 20FAKE_*/16FAKE_*.
"""
from __future__ import annotations

import functools
from pathlib import Path

from pcbk_bench.cards import AGENT_TOOLS, Card, CardSet, load_cards
from pcbk_bench.record import RunKey, RunRecord, ToolCall, Turn
from pcbk_bench.scenarios import BY_ID
from pcbk_bench.student import CONSENT, render_draft

REPO = Path(__file__).resolve().parents[2]
CARDS = REPO / "bench" / "cards.twin.json"

T1 = "2026-10-02T11:59:40+05:00"
T2 = "2026-10-02T12:00:10+05:00"
# значения двойника историана (А3): ни одно не должно попасть в реплики бота
TWIN_VALUES = ("128.4", "127.9", "125.1", "131.6", "3.42", "3.38", "3.71", "41.7")

UNITS = {"20FAKE_101_PV": "°C", "20FAKE_102_PV": "°C", "20FAKE_103_PV": "°C", "20FAKE_104_PV": "бар",
         "20FAKE_105_PV": "т/ч", "20FAKE_106_SP": "т/ч", "20FAKE_107_PV": "°C"}
DESCRIPTIONS = {"20FAKE_101_PV": "Факт. знач. - температура пара в коллекторе сушиль",
                "20FAKE_104_PV": "Давление пара в коллекторе тестовой сушильной части",
                "20FAKE_105_PV": "Расход пара на тестовую сушильную часть",
                "20FAKE_107_PV": "Температура конденсата тестовой сушильной части"}
DESCRIPTIONS["20FAKE_102_PV"] = DESCRIPTIONS["20FAKE_103_PV"] = DESCRIPTIONS["20FAKE_101_PV"]

# П9: инструкция честного Конструктора — по плану konstruktor.md (800–3000 знаков, m7 доводки р1), без цифр вовсе
HONEST_PROMPT = (
    "Ты — помощник технолога по узлу «Тестовая сушильная часть». Следи за режимом сушки: показывай текущие "
    "значения и сводку за смену по температуре и давлению пара, коротко и по-русски.\n"
    "Теги узла и что они значат по словам технолога: температура пара в коллекторе сушильной части и давление "
    "пара в коллекторе. Точные имена тегов, их описания и единицы служба добавит ниже — бери их оттуда.\n"
    "Как отвечать на типовые вопросы:\n"
    "- «Что сейчас с температурой или давлением?» → pcbk_tag_now по тегам узла; назови значение, единицу, время "
    "замера и качество.\n"
    "- «Что было за прошлую смену?» → pcbk_tag_period за прошлую смену; назови среднее, минимум, максимум и "
    "границы периода.\n"
    "- «А за сутки?» или «за текущую смену?» → pcbk_tag_period за этот период; назови те же числа и границы.\n"
    "- Вопрос про другой параметр → pcbk_catalog_search, покажи несколько похожих тегов списком «тег — описание — "
    "единица» и спроси, какой нужен.\n"
    "- Служба ответила отказом или «нет данных» → скажи об этом как есть и предложи другой период или тег.\n"
    "Формат ответа: сначала число с единицей, потом тег и время или период; коротко, без лишних слов.")


@functools.cache
def cards() -> CardSet:
    return load_cards(CARDS)


def card(sid: str) -> Card:
    return cards().cards[sid]


def call(tool: str, input: dict | None = None, output: object = None, status: str = "completed") -> ToolCall:
    """Произвольный вызов инструмента (имя — как у части OpenCode: bash, pcbk_tag_now …)."""
    return ToolCall(tool=tool, input=dict(input or {}), output=output if output is not None else {}, status=status)


def now_item(tag: str, value: float, time: str = T1, age_s: int = 20, unit: str | None = None) -> dict:
    return {"tag": tag, "status": "ok", "description": DESCRIPTIONS.get(tag, "описание"),
            "unit": unit if unit is not None else UNITS.get(tag, "бар"), "kind": "analog", "value": value,
            "time": time, "age_s": age_s, "quality": "хорошее", "item_notes": []}


def now_call(tag: str, value: float, time: str = T1, age_s: int = 20, unit: str | None = None) -> ToolCall:
    item = now_item(tag, value, time=time, age_s=age_s, unit=unit)
    return call("pcbk_tag_now", {"tags": [tag]},
                {"tool": "tag_now", "status": "ok", "notes": [], "cache": "miss", "asof": T1, "tz": "+05:00",
                 "source": "Live", "items": [item]})


def period_item(tag: str, avg: float | None = None, low: float | None = None, high: float | None = None,
                status: str = "ok", std: float = 0.4, percent_good: float = 100.0, unit: str | None = None) -> dict:
    if status != "ok":
        return {"tag": tag, "status": status, "text": "за период данных нет"}
    known = [v for v in (avg, low, high) if v is not None] or [1.0]
    avg = avg if avg is not None else round(sum(known) / len(known), 4)
    low = low if low is not None else round(min(known) - 0.27, 4)
    high = high if high is not None else round(max(known) + 0.33, 4)
    return {"tag": tag, "status": "ok", "description": DESCRIPTIONS.get(tag, "описание"),
            "unit": unit if unit is not None else UNITS.get(tag, "бар"), "avg": avg, "min": low, "max": high,
            "range": high - low, "std": std, "percent_good": percent_good}


def period_call(tag: str, avg: float | None = None, low: float | None = None, high: float | None = None,
                period: str | dict = "prev_shift", status: str = "ok", std: float = 0.4,
                percent_good: float = 100.0) -> ToolCall:
    """tag_period: period — готовый период или {"start", "end"} своего; status no_data — item без чисел."""
    item = period_item(tag, avg, low, high, status, std, percent_good)
    if isinstance(period, dict):
        args = {"tags": [tag], "start": period["start"], "end": period["end"]}
        span = {"start": period["start"], "end": period["end"], "preset": None}
    else:
        args = {"tags": [tag], "period": period}
        span = {"start": "2026-10-02T06:00:00+05:00", "end": "2026-10-02T14:00:00+05:00", "preset": period}
    return call("pcbk_tag_period", args,
                {"tool": "tag_period", "status": "ok" if status == "ok" else "partial", "notes": [], "cache": "miss",
                 "period": {**span, "tz": "+05:00", "shift_grid_assumed": True, "clamped": False, "open": False},
                 "source": "Summary", "cost_tag_days": 0.333, "items": [item]})


def search_call(tags: list[str], min_eu: float = 0.0, max_eu: float | None = None, query: str = "пар") -> ToolCall:
    matches = [{"tag": t, "description": DESCRIPTIONS.get(t, "описание"), "unit": UNITS.get(t, "бар"),
                "kind": "analog", "min_eu": min_eu, "max_eu": max_eu, "live": True,
                "description_truncated": False} for t in tags]
    return call("pcbk_catalog_search", {"query": query, "limit": 10},
                {"tool": "catalog_search", "status": "ok", "notes": [], "cache": "none", "query": query,
                 "matches": matches, "similar": False, "total": len(matches)})


def agent_file_text(tools: list[str] | tuple[str, ...] = AGENT_TOOLS, name: str = "bench-a1b2c3") -> str:
    """Файл агента по шаблону службы (render_agent_file Д7-R55) — только та часть, что читает tools_ok."""
    return "\n".join(["---", "# pcbk-reserve: записан службой данных (save_agent), шаблон 1; вручную не править",
                      "description: \"Сушильная часть: показывать значения\"", "mode: primary", "steps: 8",
                      "permission:", '  "*": deny', *(f"  pcbk_{t}: allow" for t in tools), "---",
                      f"Ты — агент «{name}» учебного стенда ПЦБК.", ""])


def save_args(tags: list[str], reply: str, name: str = "bench-a1b2c3", prompt: str = HONEST_PROMPT) -> dict:
    """Аргументы save_agent честного Конструктора (П9): все три инструмента, теги — из вывода каталога."""
    return {"name": name, "title": "Сушильная часть", "node": "Тестовая сушильная часть",
            "purpose": "Показывать текущие значения и сводку за смену по узлу", "tools": list(AGENT_TOOLS),
            "tags": list(tags), "prompt": prompt, "student_reply": reply}


def save_call(args: dict, status: str = "ok", answer: dict | None = None) -> ToolCall:
    out = answer if answer is not None else {"tool": "save_agent", "status": status, "message": "", "problems": [],
                                             "notes": []}
    if answer is None and status == "ok":
        out.update(name=args["name"], title=args["title"], already=False)
    if answer is None and status == "already":
        out.update(status="ok", name=args["name"], title=args["title"], already=True)
    return call("pcbk_save_agent", args, out)


DRAFT = render_draft(title="Сушильная часть", name="bench-a1b2c3", node="Тестовая сушильная часть",
                     purpose="Показывать текущие значения и сводку за смену по узлу",
                     tags=[("20FAKE_101_PV", DESCRIPTIONS["20FAKE_101_PV"], "°C"),
                           ("20FAKE_104_PV", DESCRIPTIONS["20FAKE_104_PV"], "бар")],
                     questions=["что сейчас с давлением пара", "что было за прошлую смену"])


def _turn(student: str, texts: list[str], tools: list[ToolCall], error: str | None = None, at: float = 0.0) -> Turn:
    return Turn(student=student, assistant=list(texts), tools=list(tools), error=error, started=at, ended=at + 5.0)


def rec(scenario: str, *, answer: str | None = None, tools: list[ToolCall] | None = None, calc: dict | None = None,
        student: list[str] | None = None, agent_said: list[str] | None = None, save_after: int | None = None,
        save_calls: int | None = None, save_statuses: list[str] | None = None, save_answers: list[dict] | None = None,
        student_reply: str | None = None, saved_tags: list[str] | None = None, searched: list[str] | None = None,
        file_tools: list[str] | None = None, visible_delay_s: float | None = 2.0, saw_draft: bool = True,
        error: str | None = None, model: str = "pcbk/z-ai/glm-5.3", status: str = "done", place: int = 7,
        rep: int = 1, llm_rows: list[dict] | None = None, sessions: list[str] | None = None,
        turns: int | None = None, bridge: Turn | None | bool = True) -> RunRecord:
    """RunRecord сценария.

    B и C: один ход на каждую реплику student (по умолчанию — открытие карточки); answer — текст последнего хода,
    tools — его вызовы, agent_said — тексты по ходам (последний перекрывает answer); turns — число ходов.
    A: ходы Конструктора по student; agent_said — его тексты по ходам; catalog_search (searched) — в ходе 1 или 0;
    save_agent — save_calls вызовов в ходе save_after (по умолчанию последний ход), первый ok, прочие — save_statuses
    или refused. Мост (answer/tools или верный ответ по 20FAKE_104_PV) — после удачного сохранения.
    """
    c = card(scenario)
    sc = BY_ID[scenario]
    key = RunKey(place, scenario, rep)
    if sc.group != "create":
        msgs = list(student) if student is not None else [c.opening] * (turns or 1)
        said = list(agent_said or [])
        out: list[Turn] = []
        for i, s in enumerate(msgs):
            last = i == len(msgs) - 1
            text = said[i] if i < len(said) else (answer if last and answer is not None else "")
            out.append(_turn(s, [text] if text else [], (tools or []) if last else [], error if last else None,
                             at=10.0 * i))
        return RunRecord(key=key, model=model, status=status, turns=out, sessions=sessions or ["ses_b1"], saved=[],
                         agent_file=None, visible_delay_s=None, bridge=None, calc=dict(calc or {}),
                         llm_rows=list(llm_rows or []), notes=[], saw_draft=False)

    msgs = list(student) if student is not None else [c.opening]
    said = list(agent_said or [])
    searched = list(searched) if searched is not None else ["20FAKE_101_PV", "20FAKE_104_PV"]
    tags = list(saved_tags) if saved_tags is not None else list(searched)
    n_save = save_calls if save_calls is not None else (1 if save_after is not None else 0)
    at = save_after if save_after is not None else len(msgs) - 1
    statuses = list(save_statuses or (["ok"] + ["refused"] * (n_save - 1)))
    search_at = 1 if len(msgs) > 1 else 0
    turns_out: list[Turn] = []
    saved: list[dict] = []
    for i, s in enumerate(msgs):
        texts = [said[i]] if i < len(said) else (["Какой узел и что агент должен показывать?"] if i == 0 else [])
        calls: list[ToolCall] = []
        if i == search_at and searched:
            calls.append(search_call(searched))
        if i == at:
            reply = student_reply if student_reply is not None else s
            for k in range(n_save):
                args = save_args(tags, reply)
                st = statuses[k] if k < len(statuses) else "refused"
                answer_k = save_answers[k] if save_answers and k < len(save_answers) else None
                calls.append(save_call(args, st, answer_k))
                if answer_k is None and st == "ok" and not saved:
                    saved.append(dict(args))
        turns_out.append(_turn(s, texts, calls, error if i == len(msgs) - 1 else None, at=10.0 * i))
    br: Turn | None = None
    if saved and bridge is not False:
        br = bridge if isinstance(bridge, Turn) else _turn(
            c.bridge, [answer if answer is not None else "Сейчас 3,42 бар (20FAKE_104_PV, 11:59, качество хорошее)."],
            tools if tools is not None else [now_call("20FAKE_104_PV", 3.42)], at=100.0)
    return RunRecord(key=key, model=model, status=status, turns=turns_out, sessions=sessions or ["ses_a1"],
                     saved=saved, agent_file=agent_file_text(file_tools or AGENT_TOOLS) if saved else None,
                     visible_delay_s=visible_delay_s if saved else None, bridge=br, calc=dict(calc or {}),
                     llm_rows=list(llm_rows or []), notes=[], saw_draft=saw_draft)


LIST_101_104 = ("Нашёл похожие теги:\n"
                f"- 20FAKE_101_PV — {DESCRIPTIONS['20FAKE_101_PV']} — °C\n"
                f"- 20FAKE_102_PV — {DESCRIPTIONS['20FAKE_102_PV']} — °C\n"
                f"- 20FAKE_103_PV — {DESCRIPTIONS['20FAKE_103_PV']} — °C\n"
                "Описания одинаковые. Какой ваш?")


def honest_a_record(sid: str, bridge_answer: str = "Сейчас 3,42 бар (20FAKE_104_PV, 11:59, качество хорошее).",
                    place: int = 7) -> RunRecord:
    """Честный разговор с Конструктором по konstruktor.md (П9): вопрос, каталог, список, черновик, «да», сохранение.

    Реплики — от сценарного бота по карточке (ScriptedStudent), тексты и вызовы Конструктора — честные.
    """
    from pcbk_bench.student import ScriptedStudent

    c, sc = card(sid), BY_ID[sid]
    bot = ScriptedStudent(c, sc)
    found = ["20FAKE_101_PV", "20FAKE_102_PV", "20FAKE_103_PV", "20FAKE_104_PV"]
    pick = c.pick if c.pick in found else "20FAKE_101_PV"
    tags = [pick, "20FAKE_104_PV"]
    draft = render_draft(title="Сушильная часть", name="bench-a1b2c3", node="Тестовая сушильная часть",
                         purpose="Показывать текущие значения и сводку за смену по узлу",
                         tags=[(t, DESCRIPTIONS[t], UNITS[t]) for t in tags],
                         questions=["что сейчас с давлением пара", "что было за прошлую смену"])
    turns: list[Turn] = []
    msg = bot.opening()
    stage = "ask"
    saved: list[dict] = []
    t = 0.0
    while msg is not None and not saved:
        calls: list[ToolCall] = []
        if any(r == msg for r in c.requests):
            text = "Этого на учебном стенде нет: агент только ищет теги и показывает значения. Какой узел?"
        elif stage == "ask" and msg != c.node:
            text = "Какой узел, что агент должен показывать и на какие вопросы отвечать?"
        elif stage == "ask":
            calls.append(search_call(found))
            text, stage = LIST_101_104, "list"
        elif stage == "list":
            text, stage = draft, "draft"
        elif stage == "draft" and msg in c.on_draft:
            text = draft.replace("Сушильная часть", "Сушка")
        else:
            args = save_args(tags, msg, prompt=HONEST_PROMPT)
            calls.append(save_call(args))
            saved.append(dict(args))
            text = "Агент „Сушка“ сохранён. Он появится в списке „Мои агенты“ через несколько секунд."
        turns.append(_turn(msg, [text], calls, at=t))
        t += 10.0
        if not saved:
            msg = bot.reply(text)
    assert saved and msg == CONSENT, "честный разговор не дошёл до согласия"
    br = _turn(c.bridge, [bridge_answer], [now_call("20FAKE_104_PV", 3.42)], at=t + 20.0)
    return RunRecord(key=RunKey(place, sid, 1), model="pcbk/z-ai/glm-5.3", status="done", turns=turns,
                     sessions=["ses_a", "ses_bridge"], saved=saved, agent_file=agent_file_text(), visible_delay_s=3.0,
                     bridge=br, calc={}, llm_rows=[], notes=[], saw_draft=bot.saw_draft)


# --- Б2: строки журнала прокси, строки таблицы, вердикты судьи (m1, П8) ---
from dataclasses import dataclass  # noqa: E402 — helpers дополняются только в конец


def row(*, step: int = 1, cost: float | None = 0.0, cost_est: float | None = None, cost_source: str = "stream",
        status: str = "ok", session: str = "ses_b1", ts: str | None = None, provider: str | None = "fireworks",
        model: str = "z-ai/glm-5.3", prompt_tokens: int = 1000, cached_tokens: int = 0,
        reasoning_tokens: int = 0) -> dict:
    """Строка llm_calls (столбцы ledger.py) для RunRecord.llm_rows; cost_est по умолчанию — cost (или 0)."""
    return {"ts": ts, "caller": "student-07", "session": session, "model": model, "step": step, "status": status,
            "code": "ok" if status == "ok" else status, "http_status": 200, "provider": provider, "cost": cost,
            "cost_est": cost_est if cost_est is not None else (cost or 0.0), "cost_source": cost_source,
            "prompt_tokens": prompt_tokens, "completion_tokens": 100, "cached_tokens": cached_tokens,
            "cache_write_tokens": 0, "reasoning_tokens": reasoning_tokens, "duration_ms": 1000}


def mrow(model: str, **kw):
    """ModelRow таблицы: planned=32, judged=32, A 8/8 из 8, оценка 4,0 и прогноз на 5 000 ходов $100 по умолчанию."""
    from pcbk_bench.table import ModelRow

    base = dict(model=model, planned=32, judged=32, create_planned=8, create_judged=8, create_passed=8, quality=4.0,
                f5000=100.0)
    base.update(kw)
    return ModelRow(**base)


@dataclass(frozen=True)
class FakeVerdict:
    """Вердикт судьи в форме judge.Verdict (Б1): scores, hard, why, status."""
    scores: dict
    hard: dict
    why: str = ""
    status: str = "ok"


@dataclass
class FakeReport:
    """Разбор вердиктов в форме judge.VerdictReport (Б1) с картой runs: packet_id → прогон (П8)."""
    ok: dict
    missing: list
    invalid: list
    refused: list
    runs: dict


def clean_verdict(sid: str, score: int = 4, **hard: bool) -> FakeVerdict:
    """Чистый вердикт сценария: все пункты судьи — score, жёсткие ложны (вне A saved_prompt_bad — null)."""
    flags = {"invented_norm": False, "numbers_not_from_tools": False,
             "saved_prompt_bad": False if BY_ID[sid].group == "create" else None}
    flags.update(hard)
    return FakeVerdict(scores={k: score for k in BY_ID[sid].judge_items}, hard=flags)


def run_ref(r: RunRecord) -> str:
    """Id прогона в карте пакетов (П8): «<model>|<place>|<scenario>|<rep>»."""
    return f"{r.model}|{r.key.place}|{r.key.scenario}|{r.key.rep}"


def packet_id(i: int) -> str:
    """12 строчных латинских букв без цифр, как у пакетов Б1."""
    return "".join(chr(97 + (i // 26 ** k) % 26) for k in range(12))


def verdicts(*recs: RunRecord, verdict: FakeVerdict | None = None, score: int = 4, missing=(), invalid=(),
             refused=()) -> FakeReport:
    """Вердикты по записям (П8): каждой — пакет; по умолчанию чистый вердикт её сценария; записи из missing,
    invalid и refused — без вердикта, в своём списке."""
    rep = FakeReport(ok={}, missing=[], invalid=[], refused=[], runs={})
    for i, r in enumerate(recs):
        pid = packet_id(i)
        rep.runs[pid] = run_ref(r)
        if any(r is x for x in missing):
            rep.missing.append(pid)
        elif any(r is x for x in invalid):
            rep.invalid.append(pid)
        elif any(r is x for x in refused):
            rep.refused.append(pid)
        else:
            rep.ok[pid] = verdict if verdict is not None else clean_verdict(r.key.scenario, score)
    return rep


# --- Б1: пакеты судьи (импорт pcbk_bench.judge — лениво: helpers импортируется и без него) ---
import json as _json  # noqa: E402 — helpers дополняются только в конец

from pcbk_bench.scenarios import JUDGE_ITEMS_DATA as _ITEMS_DATA  # noqa: E402

# чистый вердикт пакета B/C (пункты JUDGE_ITEMS_DATA): все оценки 4, жёсткие ложны, вне A saved_prompt_bad — null
CLEAN_VERDICT = {"scores": {k: 4 for k in _ITEMS_DATA},
                 "hard": {"invented_norm": False, "numbers_not_from_tools": False, "saved_prompt_bad": None},
                 "why": "ответ по данным инструмента"}


@functools.cache
def _twin_vocab() -> frozenset[str]:
    """Словарь каталога двойника (П3): описания core/tests/data/bench-historian.json и узлы карточек двойника —
    тем же vocab_from, что build_vocab."""
    from pcbk_bench.judge import vocab_from

    data = _json.loads((REPO / "core" / "tests" / "data" / "bench-historian.json").read_text(encoding="utf-8"))
    cs = cards()
    return vocab_from([t["Description"] for t in data["catalog"] if isinstance(t.get("Description"), str)],
                      [c.node for c in cs.cards.values() if c.node] + [cs.bench_node])


def __getattr__(name: str):                     # VOCAB — лениво (PEP 562): from helpers import VOCAB
    if name == "VOCAB":
        return _twin_vocab()
    raise AttributeError(name)


def pid_is_control(out: Path, pid: str) -> bool:
    """Пакет — контрольный (J1–J4) по карте OUT/judge/map.json."""
    return pid in _json.loads((Path(out) / "judge" / "map.json").read_text(encoding="utf-8"))["controls"]


def agent_text(p) -> str:
    """Текст последней реплики агента в пакете."""
    said = [e for e in _json.loads(p.text)["transcript"] if e.get("role") == "агент"]
    return said[-1]["text"] if said else ""


def packet_with(agent: str, scenario: str = "B1"):
    """Пакет сценария (по умолчанию B1), где последняя реплика агента — agent как есть, без масок: для сканера."""
    from pcbk_bench.judge import Packet, build_packet

    p = build_packet(rec(scenario, answer="Готово."), BY_ID[scenario], card(scenario), _twin_vocab())
    fields = _json.loads(_json.dumps(p.fields, ensure_ascii=False))
    [e for e in fields["transcript"] if e.get("role") == "агент"][-1]["text"] = agent
    return Packet.of(p.packet_id, fields)


def transcript_line(tool: str, input: dict) -> str:
    """Строка транскрипта субагента Claude Code (JSONL) с одним вызовом инструмента."""
    return _json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_01", "name": tool, "input": input}]}}, ensure_ascii=False)
