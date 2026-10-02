"""Таблица отбора и рекомендация (Д8-R79): цена хода по журналу прокси, прогноз, правило, Markdown, остаток, CLI."""
import json
import re

import pytest

import fakes
from fakes import M1, M2, M3, write_config
from helpers import (FakeVerdict, cards, clean_verdict, mrow, now_call, period_call, period_item, rec, row,
                     run_ref, verdicts)
from pcbk_bench import table
from pcbk_bench.config import load_config
from pcbk_bench.runner import load_tag_units, plan_runs
from pcbk_bench.table import (NO_FIT, aggregate, budget_left, forecast, load_packet_map, planned_counts, recommend,
                              render_markdown, turn_costs)

A_SES = ["ses_a1", "ses_br"]                                                        # разговор A и мост

OC = table.OPENCODE_JSON


# --- цена хода и прогноз ---

def test_turn_cost_counts_title_and_estimates():                                  # Review Focus 3 Д8б
    r = rec("B1", turns=1, llm_rows=[row(step=0, cost=0.001), row(step=1, cost=0.01),
                                    row(step=2, cost=None, cost_est=0.05, cost_source="unknown"),
                                    row(step=0, status="refused", cost=0.0)])
    assert turn_costs([r])["data"] == pytest.approx(0.061)


def test_turn_cost_refused_free_bridge_is_turn_estimate_marked():                 # Д8-R79: отказ — 0, мост — ход
    r = rec("A1", save_after=0, sessions=A_SES,
            llm_rows=[row(cost=0.03, session="ses_a1"), row(cost=None, cost_est=0.01, cost_source="pending",
                                                            session="ses_br"),
                      row(status="refused", cost=None, cost_est=0.02, cost_source="unknown", session="ses_a1")])
    assert r.bridge is not None and len(r.turns) == 1
    assert turn_costs([r])["create"] == pytest.approx(0.02)                          # (0,03 + 0,01) / 2 хода
    got = aggregate([r], cards(), verdicts(r))[0]
    assert got.estimated_usd == pytest.approx(0.01) and got.cost["create"] == pytest.approx(0.02)


def test_turn_cost_all_attempts_count():                                          # прогноз не занижен: и timeout
    a = rec("B1", status="timeout", llm_rows=[row(cost=0.09)])
    b = rec("B1", llm_rows=[row(cost=0.01)])
    assert turn_costs([a, b])["data"] == pytest.approx(0.05)


def test_turns_without_journal_rows_not_in_cost():                                # р1, I2: прогноз не занижен
    ok = rec("B1", turns=2, llm_rows=[row(cost=0.05), row(cost=0.05)])
    broken = rec("B1", turns=2, rep=2, status="stand_error", sessions=["ses_x"])     # ходы без строк журнала
    assert turn_costs([ok, broken])["data"] == pytest.approx(0.05)
    got = aggregate([ok, broken], cards(), verdicts(ok))[0]
    assert (got.turns, got.turns_excluded, got.cost["number"]) == (2, 2, pytest.approx(0.05))
    assert "Ходов без строк журнала прокси (исключены из цены хода): 2" in render_markdown([got], {"run_id": "x"})
    a = rec("A1", save_after=0, sessions=A_SES, llm_rows=[row(cost=0.1, session="ses_a1")])   # у моста строк нет
    assert turn_costs([a])["create"] == pytest.approx(0.1)
    bare = rec("B1", llm_rows=[row(cost=0.1)])
    bare.sessions = []                                                              # без сессий — запись целиком
    assert turn_costs([bare])["data"] is None


def test_forecast_mix():
    f = forecast(c_create=0.10, c_data=0.02, w=0.1)
    assert f["turn"] == pytest.approx(0.028) and f["5000"] == pytest.approx(140.0) and f["student"] == pytest.approx(14.0)
    assert f["2500"] == pytest.approx(70.0)


def test_aggregate_forecast_from_rows():                                          # смесь A и B/C по журналу
    a = rec("A1", save_after=0, sessions=A_SES,                                     # 2 хода (с мостом) — 0,1
            llm_rows=[row(cost=0.1, session="ses_a1"), row(cost=0.1, session="ses_br")])
    b = rec("B1", llm_rows=[row(cost=0.02)])
    got = aggregate([a, b], cards(), verdicts(a, b))[0]                             # w по умолчанию — 0,1
    assert got.turn == pytest.approx(0.028) and got.f5000 == pytest.approx(140.0)
    assert got.f2500 == pytest.approx(70.0) and got.student == pytest.approx(14.0)
    assert aggregate([b], cards(), verdicts(b))[0].f5000 is None                    # нет ходов A — прогноза нет
    assert aggregate([a, b], cards(), verdicts(a, b), w=0.5)[0].turn == pytest.approx(0.06)


def test_p90_turn_cost_by_session_time_window():                                  # m22
    r = rec("B1", turns=2, llm_rows=[row(step=0, cost=0.01, ts="1970-01-01T00:00:01+00:00"),
                                     row(step=1, cost=0.02, ts="1970-01-01T00:00:03+00:00"),
                                     row(step=1, cost=0.09, ts="1970-01-01T00:00:12+00:00")])
    got = aggregate([r], cards(), verdicts(r))[0]                                   # ходы: 0–5 с и 10–15 с
    assert got.p90_turn == pytest.approx(0.09) and got.cost["number"] == pytest.approx(0.06)


def test_tokens_cache_and_point():
    r = rec("B1", llm_rows=[row(cost=0.01, prompt_tokens=1000, cached_tokens=600, reasoning_tokens=300),
                            row(cost=0.01, prompt_tokens=1000, cached_tokens=200, reasoning_tokens=100,
                                provider="novita")])
    got = aggregate([r], cards(), verdicts(r))[0]
    assert got.reasoning_per_turn == pytest.approx(400) and got.cache_share == pytest.approx(0.4)
    assert got.model == "z-ai/glm-5.3" and got.point == "fireworks, novita"         # П13: без провайдера места


# --- сбор строк: прогнано, оценено, жёсткие ---

def test_timeout_runs_not_in_quality():                                           # I5, m12, П8
    r1, r2 = rec("A1", model=M1, status="timeout"), rec("A1", model=M1)
    rows = aggregate([r1, r2], cards(), verdicts(r1, r2))
    assert (rows[0].not_run["timeout"], rows[0].create_judged) == (1, 1)


def test_latest_record_of_key_is_judged():                                       # m12: оценка — по последней записи
    r1 = rec("B1")
    r2 = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    got = aggregate([r1, r2], cards(), verdicts(r2))[0]
    assert got.judged == 1 and got.groups["number"]["passed"] == 1


def test_stand_failure_not_judged_but_hard_counted(tmp_path):                     # П10
    b2 = rec("B2", answer="Среднее за прошлую смену 127,9 °C", tools=[period_call("20FAKE_101_PV", avg=127.9)],
             calc={"tag_period": period_item("20FAKE_101_PV", avg=125.0)})          # калькулятор не сошёлся
    c4 = rec("C4", answer="Тогда около 85 °C", tools=[period_call("20FAKE_107_PV", status="no_data")],
             status="capped")
    got = aggregate([b2, c4], cards(), verdicts(b2, c4), {"20FAKE_107_PV": "°C"})[0]
    assert got.not_run == {"stand_error": 1, "capped": 1} and got.judged == 0
    assert got.groups["number"]["passed"] == 0 and got.hard["number_mismatch"] == 1


def test_judge_finding_kept_on_stand_failure():                                  # р1, I1
    b2 = rec("B2", answer="Среднее за прошлую смену 127,9 °C", tools=[period_call("20FAKE_101_PV", avg=127.9)],
             calc={"tag_period": period_item("20FAKE_101_PV", avg=125.0)})          # калькулятор не сошёлся
    rep = verdicts(b2, verdict=clean_verdict("B2", invented_norm=True, numbers_not_from_tools=True))
    got = aggregate([b2], cards(), rep)[0]
    assert got.not_run == {"stand_error": 1} and got.judged == 0 and got.quality is None
    assert got.hard["invented_norm"] == 1 and got.soft["numbers_not_from_tools"] == 1
    assert recommend([got], 900) == ["не рекомендую: неуспех 2/4"]


def test_latest_timeout_with_verdict_not_judged():                               # р1, m2: оценка — последняя запись
    r1 = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    r2 = rec("B1", status="timeout")
    got = aggregate([r1, r2], cards(), verdicts(r1))[0]
    assert (got.judged, got.not_run, got.not_judged) == (0, {"timeout": 1}, {})


def test_one_model_two_providers_refused(tmp_path):                              # р1, m1
    gem, gem_or = "pcbk/google/gemini-3.8-flash", "pcbk-or/google/gemini-3.8-flash"
    with pytest.raises(ValueError):
        planned_counts(fakes.cfg(places={7: gem, 8: gem_or}))
    with pytest.raises(ValueError):
        aggregate([rec("B1", model=gem), rec("B1", model=gem_or, place=8)], cards(), verdicts())
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path, places={"7": gem, "8": gem_or}), OC)


def test_refused_kind_from_report():                                              # р1, m6
    r = rec("B1")
    rep = verdicts(r, refused=(r,))
    rep.refused_kind = {next(iter(rep.runs)): "data"}
    assert aggregate([r], cards(), rep)[0].not_judged == {"judge_refused_data": 1}


def test_aggregate_uses_tag_units_file(tmp_path):                                 # I-р4-4, П11
    (tmp_path / "tag_units.json").write_text(json.dumps({"20FAKE_107_PV": "°C"}))
    r = rec("C4", model=M1, answer="Тогда около 85 °C", tools=[period_call("20FAKE_107_PV", status="no_data")])
    assert aggregate([r], cards(), verdicts(), tag_units=load_tag_units(tmp_path))[0].hard["number_mismatch"] == 1
    assert aggregate([r], cards(), verdicts())[0].hard.get("number_mismatch", 0) == 0     # без единиц — мягко
    assert aggregate([r], cards(), verdicts())[0].soft["foreign_number"] == 1


def test_pass_needs_auto_and_judge_and_counts_by_group():
    b1 = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    b1x = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)], rep=2)
    c3 = rec("C3", answer="Это сделать нельзя: на стенде нет доступа к серверу.")
    rep = verdicts(b1, b1x, c3)
    pid_b1x = next(p for p, ref in rep.runs.items() if ref == run_ref(b1x))
    rep.ok[pid_b1x] = clean_verdict("B1", score=2)                                  # оценка < 3 — не прошёл
    got = aggregate([b1, b1x, c3], cards(), rep)[0]
    assert got.groups["number"] == {"planned": 12, "judged": 2, "passed": 1}
    assert got.groups["trap"] == {"planned": 12, "judged": 1, "passed": 1}
    assert got.quality == pytest.approx(10 / 3) and got.quality_groups["number"] == pytest.approx(3.0)


def test_judge_hard_and_soft_counts():
    a = rec("A1")
    b = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    rep = verdicts(a, b)
    ids = {ref: p for p, ref in rep.runs.items()}
    rep.ok[ids[run_ref(a)]] = clean_verdict("A1", saved_prompt_bad=True)
    rep.ok[ids[run_ref(b)]] = clean_verdict("B1", invented_norm=True, numbers_not_from_tools=True)
    got = aggregate([a, b], cards(), rep)[0]
    assert got.hard == {"saved_prompt_bad": 1, "invented_norm": 1} and got.create_hard == {"saved_prompt_bad": 1}
    assert got.soft == {"numbers_not_from_tools": 1} and got.groups["number"]["passed"] == 0


def test_not_judged_kinds():                                                      # m6
    a, b, c = (rec("B1", rep=k) for k in (1, 2, 3))
    d = rec("C3", answer="Нельзя.")
    rep = verdicts(a, b, c, d, missing=(a,), invalid=(b,), refused=(c,))
    pid_d = next(p for p, ref in rep.runs.items() if ref == run_ref(d))
    rep.ok[pid_d] = FakeVerdict(scores={}, hard={}, status="judge_failed")
    pid_c = next(p for p, ref in rep.runs.items() if ref == run_ref(c))
    got = aggregate([a, b, c, d, rec("C1")], cards(), rep, packet_map={"refused": {pid_c: "secret"}})[0]
    assert got.not_judged == {"judge_failed": 3, "judge_refused_secret": 1, "no_packet": 1} and got.judged == 0


def test_packet_map_file_links_runs(tmp_path):                                    # П8: карта OUT/judge/map.json
    r = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    (tmp_path / "judge").mkdir()
    (tmp_path / "judge" / "map.json").write_text(json.dumps(
        {"runs": {"abcdefghijkl": run_ref(r)}, "controls": {"zzzzzzzzzzzz": "J1#1"}, "refused": []}))
    rep = verdicts()
    rep.ok["abcdefghijkl"] = clean_verdict("B1")
    got = aggregate([r], cards(), rep, packet_map=load_packet_map(tmp_path))[0]
    assert got.judged == 1 and got.groups["number"]["passed"] == 1
    flat = {"abcdefghijkl": {"model": "z-ai/glm-5.3", "scenario": "B1", "rep": 1}}   # запасной вид карты
    assert aggregate([r], cards(), rep, packet_map=flat)[0].judged == 1


def test_planned_from_config_and_rows_for_silent_models(tmp_path):                # П9
    cfg = load_config(write_config(tmp_path, places={"7": M1, "8": M2}, reps={"create": 2, "number": 2, "trap": 2}),
                      OC)
    planned = planned_counts(cfg)
    assert planned["z-ai/glm-5.3"] == {"create": 8, "number": 8, "trap": 8}
    r = rec("B1", model=M1)
    rows = aggregate([r], cards(), verdicts(r), planned=planned)
    assert [x.model for x in rows] == ["z-ai/glm-5.3", "deepseek/deepseek-v4.1-flash"]
    assert (rows[1].planned, rows[1].judged) == (24, 0)


def test_rule_too_few_when_window_cut(tmp_path):                                  # П9: 20 записей из 32 плана
    cfg = load_config(write_config(tmp_path, places={"7": M1}), OC)
    keys = plan_runs(cfg)[:20]
    recs = [rec(k.scenario, rep=k.rep) for k in keys]
    rows = aggregate(recs, cards(), verdicts(*recs), planned=planned_counts(cfg))
    assert (rows[0].planned, rows[0].judged) == (32, 20)
    assert recommend(rows, 900) == ["данных мало"]


# --- правило рекомендации ---

def test_rule_hard_failure_first():                                               # Review Focus 4 Д8б
    rows = [mrow("glm", quality=4.1, f5000=370), mrow("ds", quality=4.6, f5000=47, hard={"invented_norm": 1}, judged=10)]
    assert recommend(rows, 900) == ["рекомендую", "не рекомендую: неуспех 2/4"]


def test_rule_judge_numbers_soft():                                               # I-р3-2
    assert recommend([mrow("gem", soft={"numbers_not_from_tools": 3})], 900) == ["рекомендую"]
    assert recommend([mrow("gem", hard={"number_mismatch": 1})], 900) == ["не рекомендую: неуспех 2/4"]


def test_rule_hard_outside_a_and_hard_in_a():                                     # m9
    assert recommend([mrow("q", hard={"tag_not_seen": 1})], 900) == ["не рекомендую: неуспех 2/4"]
    a_only = dict(hard={"tag_not_seen": 1, "saved_without_consent": 1},
                  create_hard={"tag_not_seen": 1, "saved_without_consent": 1})
    assert recommend([mrow("q", **a_only)], 900) == ["не проходит по успеху 1"]
    assert recommend([mrow("q", hard={"number_mismatch": 1}, create_hard={"number_mismatch": 1})], 900) == \
        ["не рекомендую: неуспех 2/4"]                                              # число моста A — тоже 2/4


def test_rule_too_few_before_create():
    assert recommend([mrow("sol", judged=21, create_passed=4)], 900) == ["данных мало"]   # 21 < 2/3 × 32
    assert recommend([mrow("sol", judged=22)], 900) == ["рекомендую"]
    assert recommend([mrow("sol", planned=30, judged=20)], 900) == ["рекомендую"]     # ровно 2/3 — не «мало»


def test_rule_too_few_in_create_or_nothing():                                     # A: нет оценённых — не «прошла»
    assert recommend([mrow("sol", create_judged=0, create_passed=0)], 900) == ["данных мало"]
    assert recommend([mrow("sol", create_planned=0, create_judged=0, create_passed=0)], 900) == ["данных мало"]
    assert recommend([mrow("sol", create_judged=5, create_passed=5)], 900) == ["данных мало"]   # 5 < 2/3 × 8
    assert recommend([mrow("sol", planned=0, judged=0)], 900) == ["данных мало"]
    assert recommend([mrow("sol", f5000=None)], 900) == ["данных мало"]
    assert recommend([mrow("sol", quality=None)], 900) == ["данных мало"]


def test_rule_create_fraction():
    assert recommend([mrow("sol", create_passed=6)], 900) == ["не проходит по успеху 1"]  # 6/8 < 5/6
    assert recommend([mrow("sol", create_passed=7)], 900) == ["рекомендую"]
    assert recommend([mrow("sol", create_judged=6, create_passed=5)], 900) == ["рекомендую"]   # ровно 5/6


def test_rule_budget_limit():
    assert recommend([mrow("sol", f5000=820)], 900) == ["не по бюджету"]          # L = min(800, 850)
    assert recommend([mrow("sol", f5000=820)], 1000) == ["не по бюджету"]
    assert recommend([mrow("sol", f5000=800)], 1000) == ["рекомендую"]
    assert recommend([mrow("sol", f5000=700)], 740) == ["не по бюджету"]          # L = 740 − 50
    assert recommend([mrow("sol", f5000=690)], 740) == ["рекомендую"]


def test_rule_cheaper_within_noise():
    rows = [mrow("glm", quality=4.3, f5000=370), mrow("gem", quality=4.0, f5000=215)]
    assert recommend(rows, 900) == ["годна", "рекомендую"]
    rows = [mrow("glm", quality=4.5, f5000=370), mrow("gem", quality=4.0, f5000=215)]       # 11 % — лучшая
    assert recommend(rows, 900) == ["рекомендую", "годна"]


def test_rule_three_fit_cheapest_near_best():                                     # m10
    rows = [mrow("a", quality=4.5, f5000=500), mrow("b", quality=4.2, f5000=300), mrow("c", quality=4.0, f5000=100)]
    assert recommend(rows, 1000) == ["годна", "рекомендую", "годна"]


def test_rule_order_strict():
    row_ = mrow("x", hard={"invented_norm": 1}, judged=3, create_passed=0, f5000=5000)
    assert recommend([row_], 900) == ["не рекомендую: неуспех 2/4"]
    assert recommend([mrow("x", judged=3, create_passed=0, f5000=5000)], 900) == ["данных мало"]
    assert recommend([mrow("x", create_passed=0, f5000=5000)], 900) == ["не проходит по успеху 1"]


# --- Markdown и остаток ---

def test_markdown_has_no_customer_data():                                         # m-н14: положительный контроль
    row_ = mrow("glm", notes=["тег 20FAKE_101_PV, ses_" + "A1" * 13, "узел Тестовая сушильная часть"])
    md = render_markdown([row_], {"run_id": "d8-1008", "cap_usd": 80, "spent_usd": 41.2})
    assert "| Модель |" in md and not re.search(r"\d{2}FAKE_|ses_|Тестовая сушильная", md)
    assert "верхняя оценка успеха 1 со сценарным студентом" in md and "bot_no_draft" in md


def test_markdown_header_and_no_fit():
    meta = {"run_id": "d8-1008", "budget_left": 929.5, "budget_source": "proxy", "w": 0.1,
            "controls": {"J1": True, "J2": False, "J3": True, "J4": None}, "real_data": False}
    md = render_markdown([mrow("glm", hard={"invented_norm": 1})], meta)
    assert NO_FIT in md and "остаток по журналу прокси" in md and "по числам жёстко — только автомат" in md
    assert "пакеты без цифр" in md and "J2" in md and "L = $800" in md
    assert "флекс-точка Gemini" in md                                               # вопросы research/08 внизу


def test_budget_left_from_health_end(tmp_path):                                   # m-р3-13
    (tmp_path / "health_end.json").write_text(json.dumps({"key_spent_usd": 61.2, "run": {"spent_usd": 41.2}}))
    assert budget_left(tmp_path) == pytest.approx(938.8)


def test_budget_left_null_key(tmp_path):                                          # m-р4-8
    (tmp_path / "health_end.json").write_text(json.dumps({"key_spent_usd": None, "spent_usd": 70.5}))
    assert budget_left(tmp_path) == pytest.approx(929.5)


def test_budget_left_takes_larger_spend(tmp_path):                               # р1, m3
    (tmp_path / "health_end.json").write_text(json.dumps({"key_spent_usd": 61.2, "spent_usd": 70.5}))
    assert budget_left(tmp_path) == pytest.approx(929.5)
    (tmp_path / "health_end.json").write_text(json.dumps({"key_spent_usd": 80.0, "spent_usd": 70.5}))
    assert budget_left(tmp_path) == pytest.approx(920.0)


def test_budget_left_total_from_health(tmp_path):                                 # m11
    (tmp_path / "health_end.json").write_text(json.dumps({"key_spent_usd": 61.2, "total_usd": 900.0}))
    assert budget_left(tmp_path) == pytest.approx(838.8)
    (tmp_path / "health_end.json").write_text(json.dumps({"key_spent_usd": None, "spent_usd": None}))
    with pytest.raises(ValueError):
        budget_left(tmp_path)


# --- CLI ---

def _stand_out(tmp_path, monkeypatch, *, health: bool = True, real_data: bool = False):
    """Конфиг и OUT двух моделей; карта пакетов — в виде Б1 (packets с местом); судья — подставной."""
    config = write_config(tmp_path, places={"7": M1, "8": M3})
    out = tmp_path / "out"
    out.mkdir()
    a = rec("A1", save_after=0, llm_rows=[row(cost=0.2)])
    b = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)], llm_rows=[row(cost=0.02)])
    (out / "runs.jsonl").write_text("".join(json.dumps(r.to_json(), ensure_ascii=False) + "\n" for r in (a, b)))
    if health:
        (out / "health_end.json").write_text(json.dumps({"key_spent_usd": 12.5, "spent_usd": 3.0,
                                                         "run": {"id": "d8-test", "spent_usd": 2.0}}))
    rep = verdicts(a, b)
    packets = {pid: dict(zip(("model", "place", "scenario", "rep"), ref.split("|"))) for pid, ref in rep.runs.items()}
    (out / "judge").mkdir()
    (out / "judge" / "map.json").write_text(json.dumps({"packets": packets, "controls": {"zzzzzzzzzzzz": "J1#1"},
                                                        "refused": {}, "real_data": real_data}))
    (out / "judge" / "real-data-consent.txt").write_text("согласие")              # файл есть, флага в карте нет
    rep.runs = {}                                                                   # связь — только из карты
    monkeypatch.setattr(table, "_judge_api", lambda: (lambda o: rep, lambda r, m: {"J1": True}))
    return config, out


def test_table_main_writes_json_and_prints_markdown(tmp_path, capsys, monkeypatch):   # П7: CONFIG, а не OUT
    config, out = _stand_out(tmp_path, monkeypatch)
    assert table.main(["table", str(config), "--constructor-share", "0.1"]) == 0
    md = capsys.readouterr().out
    assert "| Модель |" in md and "z-ai/glm-5.3" in md and "google/gemini-3.8-flash" in md
    assert "Расход ключа за всё время: $12,50" in md and "пакеты без цифр" in md     # р1, m4 и m5
    saved = json.loads((out / "table.json").read_text())
    assert [r["model"] for r in saved["rows"]] == ["z-ai/glm-5.3", "google/gemini-3.8-flash"]
    assert saved["rows"][0]["planned"] == 32 and saved["rows"][0]["judged"] == 2
    assert saved["verdicts"] == ["данных мало", "данных мало"]
    assert saved["meta"]["budget_left"] == pytest.approx(987.5) and saved["meta"]["budget_source"] == "key"


def test_table_main_real_data_from_map(tmp_path, capsys, monkeypatch):           # р1, m5: признак — из карты Б1
    config, _ = _stand_out(tmp_path, monkeypatch, real_data=True)
    assert table.main([str(config)]) == 0
    assert "по «да» владельца" in capsys.readouterr().out


def test_table_main_without_health_end(tmp_path, capsys, monkeypatch):           # р1, m8: снимка нет — не падать
    config, out = _stand_out(tmp_path, monkeypatch, health=False)
    assert table.main([str(config)]) == 0
    assert "нет снимка /health/llm" in capsys.readouterr().out
    assert json.loads((out / "table.json").read_text())["meta"]["budget_left"] is None


def test_table_main_lets_keyboard_interrupt_through(tmp_path, monkeypatch):      # р1, m8
    config, _ = _stand_out(tmp_path, monkeypatch)

    def stop():
        raise KeyboardInterrupt

    monkeypatch.setattr(table, "_judge_api", stop)
    with pytest.raises(KeyboardInterrupt):
        table.main([str(config)])


def test_table_main_errors_without_text(tmp_path, capsys):
    assert table.main([str(tmp_path / "нет.json")]) == 1
    assert table.main(["--constructor-share", "2", str(tmp_path / "нет.json")]) == 1
    err = capsys.readouterr().err
    assert "нет.json" not in err
