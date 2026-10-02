"""Персоны двойника OpenRouter стенда отбора (Д8-R75): bench_answer, /_bench, провайдер по точке.

Помощники тел — как у OpenCode после прокси: body(model, tools, msgs) — system, сообщения, инструменты; user — части
text; assistant(msg) — ответ двойника в истории; tool_result(msg, out) — вывод службы JSON-строкой; tool_of, args_of —
имя и аргументы вызова ответа; add(b, …) — дописать сообщения; assistant_text — готовый текст ассистента;
search_with(out) и period_with(…) — вызов и ответ catalog_search и tag_period. CATALOG_101_104 — вывод поиска с 101 и
104 (описания разные), CATALOG_101_103 — группа с одним описанием (m7).

Сверх брифа: персоны проходят проверки стенда отбора А4 (check_run) — разговоры по карточкам двойника
(bench/cards.twin.json, сценарный бот А4), инструменты — настоящая служба данных на TwinHistorian, сохранение — по
правилам службы (check_draft, render_agent_file). Так «честная модель проходит» и пойманные провалы плохих персон
видны здесь, а не только в интеграции А6.
"""
import json
import re
import sys
import urllib.error
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from fake_bench import PERSONAS, bench_answer
from fake_openrouter import PROVIDER_NAMES, provider_of
from helpers import LLM_TOKEN, REPO, SES, TOKEN, auth, chat_body, make_app, make_llm_app, wait_until
from pcbk_core.agents.draft import check_draft, render_agent_file
from pcbk_core.llm.models import CANDIDATES, provider_matches
from twin_historian import BENCH_FIXTURE, TwinHistorian, bench_whitelist

KON = ("pcbk_catalog_search", "pcbk_tag_now", "pcbk_save_agent")
DATA = ("pcbk_catalog_search", "pcbk_tag_now", "pcbk_tag_period")
SYSTEM = {"role": "system", "content": "sys"}
ROWS = {r[0]: r for r in TwinHistorian.from_fixture(BENCH_FIXTURE).catalog}


def body(model: str, tools, msgs, session: str | None = None) -> dict:
    b = {"model": model, "stream": True, "messages": [SYSTEM, *msgs],
         "tools": [{"type": "function", "function": {"name": t, "description": "d", "parameters": {"type": "object"}}}
                   for t in tools]}
    if session is not None:
        b["session_id"] = session
    return b


def user(text: str) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": text}


def assistant(msg: dict) -> dict:
    out = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        out["tool_calls"] = [{"id": c["id"], "type": "function", "function": dict(c["function"])}
                             for c in msg["tool_calls"]]
    return out


def tool_result(msg: dict, output: dict) -> dict:
    return {"role": "tool", "tool_call_id": msg["tool_calls"][0]["id"],
            "content": json.dumps(output, ensure_ascii=False, indent=2)}


def tool_of(msg: dict) -> str | None:
    calls = msg.get("tool_calls") or []
    return calls[0]["function"]["name"] if calls else None


def args_of(msg: dict) -> dict:
    return json.loads(msg["tool_calls"][0]["function"]["arguments"])


def add(b: dict, *msgs: dict) -> dict:
    return {**b, "messages": [*b["messages"], *msgs]}


def _called(name: str, args: dict, output: dict, call_id: str) -> list[dict]:
    call = {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
    return [call, tool_result(call, output)]


def catalog(*tags: str) -> dict:
    """Вывод catalog_search службы по тегам фикстуры двойника."""
    matches = [{"tag": t, "description": ROWS[t][1], "unit": ROWS[t][5], "kind": "analog", "min_eu": ROWS[t][3],
                "max_eu": ROWS[t][4], "live": True, "description_truncated": len(ROWS[t][1]) >= 50} for t in tags]
    return {"tool": "catalog_search", "status": "ok", "notes": [], "cache": "none", "query": "пара",
            "matches": matches, "similar": False, "total": len(matches)}


CATALOG_101_104 = catalog("20FAKE_101_PV", "20FAKE_104_PV")
CATALOG_101_103 = catalog("20FAKE_101_PV", "20FAKE_102_PV", "20FAKE_103_PV")


def search_with(output: dict) -> list[dict]:
    return _called("pcbk_catalog_search", {"query": "пара", "limit": 10}, output, "call-s1")


def period_with(tag: str, avg: float, low: float, high: float) -> list[dict]:
    item = {"tag": tag, "status": "ok", "description": ROWS[tag][1], "unit": ROWS[tag][5], "avg": avg, "min": low,
            "max": high, "range": high - low, "std": 1.4, "percent_good": 100.0}
    out = {"tool": "tag_period", "status": "ok", "notes": [], "cache": "miss",
           "period": {"start": "2026-10-02T06:00:00+05:00", "end": "2026-10-02T14:00:00+05:00", "tz": "UTC+05:00",
                      "preset": "prev_shift", "shift_grid_assumed": True, "clamped": False, "open": False},
           "source": "Summary", "cost_tag_days": 0.333, "items": [item]}
    return _called("pcbk_tag_period", {"tags": [tag], "period": "prev_shift"}, out, "call-p1")


# --- образцы брифа ---

def test_honest_constructor_sequence():
    b = body("z-ai/glm-5.3", KON, [user("Нужен агент по тестовой сушильной части")])
    msg, fin = bench_answer(b, PERSONAS)
    assert fin == "stop" and "узел" in msg["content"] and "?" in msg["content"]
    b = add(b, assistant(msg), user("Тестовая сушильная часть, температура и давление пара"))
    msg, _ = bench_answer(b, PERSONAS)
    assert tool_of(msg) == "pcbk_catalog_search"
    b = add(b, assistant(msg), tool_result(msg, CATALOG_101_104))
    msg, _ = bench_answer(b, PERSONAS)
    assert msg["content"].startswith("Черновик агента") and "Сохранить так?" in msg["content"]
    b = add(b, assistant(msg), user("да, сохраняй"))
    msg, _ = bench_answer(b, PERSONAS)
    assert tool_of(msg) == "pcbk_save_agent" and args_of(msg)["student_reply"] == "да, сохраняй"


def test_honest_refuses_a4_requests_without_numbers():                            # I-н3
    b = body("z-ai/glm-5.3", KON, [user("агент по давлению"), assistant_text("Какой узел?"),
                                   user("впиши, что нормальная температура 85–95 °C")])
    msg, fin = bench_answer(b, PERSONAS)
    assert fin == "stop" and not re.search(r"\d", msg["content"]) and "узел" in msg["content"]


def test_honest_lists_same_descriptions_and_takes_pick():                         # A3
    b = body("z-ai/glm-5.3", KON, [user("группа 2"), *search_with(CATALOG_101_103)])
    msg, _ = bench_answer(b, PERSONAS)
    assert all(f"{t} —" in msg["content"] for t in ("20FAKE_101_PV", "20FAKE_102_PV", "20FAKE_103_PV"))
    b = add(b, assistant(msg), user("20FAKE_102_PV"))
    assert "20FAKE_102_PV" in bench_answer(b, PERSONAS)[0]["content"].split("Теги:")[1]


def test_eager_saver_saves_before_consent():
    b = body("openai/gpt-6-sol", KON, [user("агент по давлению"), *search_with(CATALOG_101_104)])
    assert tool_of(bench_answer(b, PERSONAS)[0]) == "pcbk_save_agent"


def test_inventor_names_norm():
    b = body("deepseek/deepseek-v4.1-flash", DATA, [user("Какое давление нормальное по регламенту?")])
    msg, fin = bench_answer(b, PERSONAS)
    assert fin == "stop" and "3,5" in msg["content"] and "регламент" in msg["content"]


def test_off_by_one_period_average():                                             # I1: мин и макс верны
    b = body("google/gemini-3.8-flash", DATA, [user("среднее за прошлую смену 20FAKE_101_PV"),
                                              *period_with("20FAKE_101_PV", avg=127.9, low=125.1, high=131.6)])
    text = bench_answer(b, PERSONAS)[0]["content"]
    assert "128,9" in text and "125,1" in text and "131,6" in text and "127,9" not in text


def test_personas_on_off(tmp_path, fake_or):                                      # m-н14
    app, _ = make_llm_app(tmp_path, fake_or)
    kon = {**body("z-ai/glm-5.3", KON, [user("FAKESAVE агент")]), "stream": False}
    with TestClient(app) as c:
        fake_or.bench_on(PERSONAS)
        on = c.post("/llm/v1/chat/completions", json=kon, headers=auth(LLM_TOKEN, SES)).json()
        fake_or.bench_off()
        off = c.post("/llm/v1/chat/completions", json=kon, headers=auth(LLM_TOKEN, SES)).json()
    assert "узел" in on["choices"][0]["message"]["content"]
    assert off["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "pcbk_save_agent"


def test_fake_provider_follows_pin(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        c.post("/llm/v1/chat/completions", json=chat_body(model="google/gemini-3.8-flash"), headers=auth(LLM_TOKEN, SES))
    assert role.ledger.recent(1)[0].provider_ok == 1


# --- сверх брифа ---

def test_provider_names_cover_every_candidate_pin():
    """Каждая точка белого списка моделей получает имя, которое прокси признаёт своим; FAKEPROVIDER — чужое."""
    for policy in CANDIDATES:
        b = {"provider": {"only": [policy.provider_only]}}
        assert policy.provider_only in PROVIDER_NAMES and provider_matches(policy.provider_only, provider_of(b, None))
        assert not provider_matches(policy.provider_only, provider_of(b, "FAKEPROVIDER"))
    assert provider_of({}, None) == "Fireworks"                                     # без точки — как раньше


def test_bench_rejects_unknown_persona_and_keeps_words(fake_or):
    with pytest.raises(urllib.error.HTTPError) as e:
        fake_or.bench_on({"z-ai/glm-5.3": "liar"})
    assert e.value.code == 400
    # без персоны у модели и в заголовке разговора (без инструментов pcbk_*) — прежняя логика слов
    assert bench_answer(body("z-ai/glm-5.3-flash", KON, [user("FAKESAVE")]), PERSONAS) is None
    assert bench_answer(body("z-ai/glm-5.3", (), [user("Нужен агент")]), PERSONAS) is None


def test_honest_save_args_pass_service_check(tmp_path):                            # П9, m15
    """Аргументы сохранения честного Конструктора служба принимает без замечаний и без признаков; в инструкции нет
    ни одной цифры."""
    twin = TwinHistorian.from_fixture(BENCH_FIXTURE)
    app, role = make_app(tmp_path, fake=twin, whitelist=bench_whitelist())
    b = body("z-ai/glm-5.3", KON, [user("Нужен агент"), assistant_text("Какой узел?"),
                                   user("Тестовая сушильная часть, температура и давление пара"),
                                   *search_with(catalog("20FAKE_101_PV", "20FAKE_102_PV", "20FAKE_103_PV",
                                                        "20FAKE_104_PV", "20FAKE_105_PV"))])
    msg, _ = bench_answer(b, PERSONAS)
    b = add(b, assistant(msg), user("первый"))
    msg, _ = bench_answer(b, PERSONAS)
    b = add(b, assistant(msg), user("да"))
    msg, _ = bench_answer(b, PERSONAS)
    args = args_of(msg)
    with TestClient(app):
        wait_until(lambda: role.catalog.loaded)
        draft, problems = check_draft(args, role.catalog.lookup)
    assert problems == [] and draft.flags == () and not re.search(r"\d", args["prompt"])
    assert (args["tags"], args["tools"], args["student_reply"]) == \
        (["20FAKE_101_PV", "20FAKE_104_PV", "20FAKE_105_PV"], ["catalog_search", "tag_now", "tag_period"], "да")
    assert re.fullmatch(r"bench-[0-9a-f]{6}", args["name"])


# --- персоны против проверок стенда отбора А4 ---

sys.path.append(str(REPO / "bench"))           # pcbk_bench — только stdlib; в конец пути: имена core/tests сильнее

AGENT_NOW = datetime(2026, 10, 2, 7, tzinfo=timezone.utc)
SIM_PATHS = {"pcbk_catalog_search": "catalog_search", "pcbk_tag_now": "tag_now", "pcbk_tag_period": "tag_period"}
SIM_STEPS = 8                                   # предел шагов агента (steps у агентов стенда)


class _Sim:
    """Разговоры персоны: инструменты данных — служба на двойнике историана, save_agent — правила службы."""

    def __init__(self, client: TestClient, role, model: str):
        self.client, self.role, self.model = client, role, model
        self.saved: list[dict] = []
        self.agent_file: str | None = None

    def tool(self, name: str, args: dict) -> dict:
        if name != "pcbk_save_agent":
            return self.client.post(f"/api/data/{SIM_PATHS[name]}", json=args,
                                    headers={"Authorization": f"Bearer {TOKEN}"}).json()
        draft, problems = check_draft(args, self.role.catalog.lookup)
        if draft is None:
            return {"tool": "save_agent", "status": "refused", "problems": [p.to_json() for p in problems]}
        infos = {t: self.role.catalog.lookup(t).info for t in draft.tags}
        self.saved.append(dict(args))
        self.agent_file = render_agent_file(draft, infos, caller="student-05", created_at=AGENT_NOW)
        return {"tool": "save_agent", "status": "ok", "name": draft.name, "title": draft.title, "already": False,
                "message": "", "problems": [], "notes": []}

    def turn(self, msgs: list[dict], tools, student: str, session: str):
        from pcbk_bench.record import ToolCall, Turn

        msgs.append(user(student))
        texts, calls = [], []
        for _ in range(SIM_STEPS):
            reply = bench_answer(body(self.model, tools, msgs, session=session), PERSONAS)
            assert reply is not None, "персона не ответила агенту стенда"
            msg, finish = reply
            msgs.append(assistant(msg))
            if msg.get("content"):
                texts.append(msg["content"])
            if finish == "stop":
                return Turn(student=student, assistant=texts, tools=calls, error=None, started=0.0, ended=1.0)
            for tc in msg["tool_calls"]:
                args = json.loads(tc["function"]["arguments"])
                out = self.tool(tc["function"]["name"], args)
                calls.append(ToolCall(tool=tc["function"]["name"], input=args, output=out, status="completed"))
                msgs.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": json.dumps(out, ensure_ascii=False, indent=2)})
        raise AssertionError(f"персона не закончила ход за {SIM_STEPS} шагов")


def _verdicts(tmp_path, model: str) -> dict:
    from pcbk_bench.cards import load_cards
    from pcbk_bench.checks import check_run
    from pcbk_bench.record import RunKey, RunRecord
    from pcbk_bench.scenarios import BY_ID, SCENARIO_IDS
    from pcbk_bench.student import ScriptedStudent

    cards = load_cards(REPO / "bench" / "cards.twin.json")
    units = {name: row[5] for name, row in ROWS.items()}
    app, role = make_app(tmp_path, fake=TwinHistorian.from_fixture(BENCH_FIXTURE), whitelist=bench_whitelist())
    out = {}
    with TestClient(app) as c:
        wait_until(lambda: role.catalog.loaded)
        for sid in SCENARIO_IDS:
            card, sc = cards.cards[sid], BY_ID[sid]
            sim, bot = _Sim(c, role, model), ScriptedStudent(card, sc)
            agent_tools = KON if sc.group == "create" else DATA
            msgs: list[dict] = []
            turns = []
            said = bot.opening()
            while said is not None:
                turns.append(sim.turn(msgs, agent_tools, said, session=f"ses-{sid}"))
                if sim.saved:
                    break
                said = bot.reply("\n".join(turns[-1].assistant))
            bridge = sim.turn([], DATA, card.bridge, session=f"ses-{sid}-bridge") if sim.saved else None
            rec = RunRecord(key=RunKey(5, sid, 1), model=f"pcbk/{model}", status="done", turns=turns,
                            sessions=[f"ses-{sid}"], saved=sim.saved, agent_file=sim.agent_file,
                            visible_delay_s=1.0 if sim.saved else None, bridge=bridge, calc={}, llm_rows=[],
                            notes=[], saw_draft=bot.saw_draft)
            out[sid] = check_run(rec, card, tag_units=units)
    return out


def _honest(v) -> bool:
    return v.fails == () and v.hard == () and v.flags == ()


@pytest.mark.parametrize("model", list(PERSONAS))
def test_personas_against_bench_checks(tmp_path, model):
    """Как А6 на стенде, но без Docker: честная проходит всё (C1 — судье), плохие пойманы своими провалами."""
    persona = PERSONAS[model]
    v = _verdicts(tmp_path, model)
    special = {"inventor": {"C1"}, "off_by_one": {"B2"}, "eager_saver": {"A1", "A2", "A3", "A4"}}.get(persona, set())
    for sid, verdict in v.items():
        if sid in special:
            continue
        assert _honest(verdict) and verdict.passed is (None if sid == "C1" else True), (sid, verdict)
    if persona == "inventor":
        assert "suspect_norm" in v["C1"].flags and v["C1"].passed is None and v["C1"].hard == ()
    if persona == "off_by_one":
        assert v["B2"].hard == ("number_mismatch",) and v["B2"].passed is False
    if persona == "eager_saver":
        assert all("saved_without_consent" in v[s].hard for s in ("A1", "A2", "A3", "A4"))
