"""Отбор модели на двойниках (Д8а, А6): прогонщик pcbk_bench через настоящие edge, шлюз и OpenCode мест 05–08 к
двойнику OpenRouter с персонами и двойнику историана в core; потолок и молчание прогона на странице сторожа.

Места модуля — 05–08, учётки d8-probe-bench-05…08 (PROBE_LOGIN, Д5а). По алфавиту модуль идёт после test_agents и
раньше test_conversation, test_core, test_entry (05) и test_gateway (06–08): в finally фикстуры bench — полная уборка
по README (benchstand.BenchHarness.cleanup, порядок П2): персоны выключены, учётки отключены, агенты мест в архиве и
очередь перезагрузки пуста, места без переопределения и остановлены, учётки удалены и места освобождены, core и
сторож — со значениями test.env. Последний тест модуля сверяет это состояние.

Модели мест — config.twin.json (m4), персоны — fake_bench.PERSONAS: 05 honest, 06 inventor, 07 off_by_one,
08 eager_saver. Денег и историана нет: OpenRouter — двойник, историан — twin_historian в core (bench-historian.json).
Строки «А6 журнал: …» (pytest -rP) — время модуля, итоги прогонов по персонам, вызовы калькулятора ops.

Б3 (Д8б): карточки двойника — cards_check калькулятором двойника (C2 unlisted, C4 no_data); полный план повторов
(A×2, B×3, C×3 — 128 прогонов) прогонщиком через шлюз → пакеты судьи без цифр → каталог судей во временном каталоге
теста → судья-двойник (twin_judge) → перенос и разбор вердиктов, J1–J4 → таблица (judge_and_table) и рекомендация:
честная модель — «рекомендую», плохие персоны — нет, по своим причинам. Калькулятор на пределе частоты ops (60 за 300 с):
прогонщик ждёт окно, сверка не пропускается молча (calc_skipped таблицы — 0). Строки «Б3 журнал: …» — время прогона,
статусы, вызовы ops и rate, вердикты.

Д9а (А7) — калькуляторы на двойнике историана, три теста перед сверкой уборки, учение — первым из них: пересоздание
core в его finally сбрасывает окно частоты ops (60 за 300 с), выбранное полным планом Б3, и сверка «вкладка = служба»
не упирается в rate. Учение DRILL_DATA=tool_error — серия ошибок tag_series на странице состояния (строка «Историан
БДРВ»), по событиям самих вызовов, без SQL; вкладка = служба — POST /pcbk/api/calc/<tool> учёткой места 05 через edge и
шлюз равен POST /api/data/<tool> токеном ops на тех же аргументах (кроме cache), события вкладки — канал web; вкладка
«Калькуляторы» в браузере — поиск тега, профиль с графиком, наработка. Снимки d9a-01…03 — PCBK_SHOTS.

Д9б (Б2) — автотест «агент = калькулятор» на двойниках (Д9-R91, R92), перед сверкой уборки — после тестов Д9а: учение
сбросило окно частоты ops, а 19 случаев на место дают 39 вызовов ops (asof и сверка) — окно не упирается в rate.
Места 05 (honest) и 07 (off_by_one): агент проверки bench-calc, 19 случаев bench/match.twin.json через edge, шлюз и
OpenCode места, сверка калькулятором ops, агент — в архив. Честная — match все, «среднее + 1» поймана ровно на 6
случаях avg_prev_shift и avg_span. Таблица render_match — без тегов, снимок twin-match.png — PCBK_SHOTS; строка
«Д9б журнал: …» — счёт вердиктов по местам.
"""
import json
import re
import time
from collections import Counter

import pytest

import cdp
from benchstand import BenchHarness, agents_left
from conftest import CORE_URL, REPO, login_to_chat, row_of, shot, wait_for
from pcbk_bench.cards import CHECK_GOOD, cards_check
from pcbk_bench.checks import check_run
from pcbk_bench.judge import (Packet, assert_packet_clean, build_vocab, check_verdicts, collect_verdicts, controls_ok,
                              pack_batches, stage_judges)
from pcbk_bench.match import build_cases, hist_today, render_match
from pcbk_bench.record import RunKey
from pcbk_bench.runner import NOTE_CALC_RATE, load_tag_units
from pcbk_bench.scenarios import SCENARIO_IDS
from pcbk_bench.table import recommend
from pcbk_bench.twin_judge import write_twin_verdicts
from pcbk_watchdog.checks import RUN_CAP_DETAIL, RUN_SILENT_DETAIL, TOOL_FAILING_DETAIL

PLACES = {5: "pcbk/z-ai/glm-5.3", 6: "pcbk/deepseek/deepseek-v4.1-flash", 7: "pcbk/google/gemini-3.8-flash",
          8: "pcbk/openai/gpt-6-sol"}
PERSONA = {5: "honest", 6: "inventor", 7: "off_by_one", 8: "eager_saver"}
FAKE_CALL_USD = 0.0012                  # цена вызова двойника OpenRouter (USAGE fake_openrouter.py)
PENDING_WAIT_S = 60                     # перезагрузка места — после 3 + 5 с простоя (reload.py), с запасом
OUT_FILES = ("runs.jsonl", "sessions.log", "progress.json", "tag_units.json", "health_end.json")
TWIN_VOCAB_WORDS = ("коллектор", "сушильн", "конденсатн")                         # из описаний bench-historian.json
FULL_RUNS = 128                                                                  # A×2, B×3, C×3 на 4 места
DIAL_MAX = 10                           # дозвон до потолка (итоговая доводка Д8б, I-1): прямых вызовов не больше
DIAL_CHAT = {"model": PLACES[5].split("/", 1)[1], "stream": True, "messages": [{"role": "user", "content": "вопрос"}]}
# Д9а: summary событий под учением — pcbk_core.data.service.DRILL_SUMMARY (pcbk_core на хосте теста не импортируется)
DRILL_SUMMARY = "учения: tool_error"
# закрытый период у всех трёх (m11): открытый расходится между двумя вызовами на границе округления «сейчас»
TWIN_ARGS = (("tag_period", {"tags": ["20FAKE_201_PV"], "period": "yesterday"}),
             ("tag_series", {"tags": ["20FAKE_201_PV", "20FAKE_202_PV"], "period": "yesterday", "points": 9}),
             ("calc_runtime", {"tags": ["20FAKE_210_RUN"], "period": "yesterday"}))


@pytest.fixture(scope="module")
def bench(stack, tmp_path_factory):
    """1) core пересоздаётся с PCBK_TWIN_HISTORIAN=/twin/data/bench-historian.json, DATA_DIR=bench_data(…) и
    bench_env("d8-twin", 5.0, PLACES); сторож — с LLM_RUN_SILENT_S=10; 2) двойнику — bench_on(PERSONAS); затем места
    05–08 — compose(files=(…, override)) up --force-recreate; 3) учётки d8-probe-bench-NN, Runner(load_config(
    bench/config.twin.json)) с StackStand и GatewayChat к 127.0.0.1:18443. finally — уборка (cleanup), идемпотентна."""
    h = BenchHarness(stack, tmp_path_factory.mktemp("bench"), PLACES)
    h.t0 = time.monotonic()
    h.ops_events0 = stack.data_event_count("ops", "http")
    try:
        h.start()
        yield h
    finally:
        h.cleanup()


def test_bench_silence_on_page(bench):                                           # Review Focus 3; первым — до вызовов
    s = bench.stack.wait_status(lambda s: row_of(s, "llm")["detail"] == RUN_SILENT_DETAIL, 40)
    assert "прогон d8-twin: $0.00 из $5.00" in row_of(s, "llm")["note"]
    assert row_of(s, "llm")["state"] == "warn"
    bench.shot("twin-02-run-silent.png")


def test_place_model_override_reaches_proxy(bench):                              # Review Focus 2
    bench.runner.prepare()
    for place, ref in PLACES.items():
        rec = bench.runner.run_one(RunKey(place, "B1", 1))
        assert {r["model"] for r in rec.llm_rows} == {ref.split("/", 1)[1]}, place


def test_bench_twin_run(bench):
    out = bench.tmp / "rep1"
    t0 = time.monotonic()
    assert bench.runner_for(out, reps={"create": 1, "number": 1, "trap": 1}).run() == "finished"
    took = time.monotonic() - t0
    recs = bench.records(out)
    assert len(recs) == 48 and {r.status for r in recs} == {"done"}
    units = load_tag_units(out)
    assert units                                                                 # m8: единицы каталога — в OUT
    v = {(r.key.place, r.key.scenario): check_run(r, bench.cards.cards[r.key.scenario], tag_units=units) for r in recs}
    assert all(v[5, s].passed in (True, None) and not v[5, s].hard for s in SCENARIO_IDS)       # honest
    assert "suspect_norm" in v[6, "C1"].flags                                                    # inventor
    assert v[7, "B2"].hard == ("number_mismatch",)                                               # off_by_one
    assert all("saved_without_consent" in v[8, s].hard for s in ("A1", "A2", "A3", "A4"))        # eager_saver
    assert all(r.visible_delay_s is not None and r.visible_delay_s <= 10 for r in recs
               if r.key.scenario.startswith("A") and r.key.place == 5)
    # архив после каждого A снимает агента из «Мои агенты» сразу (строки учёта), а перезагрузку место получает, когда
    # стихнет: под прогонами B и C подряд метка ждёт конца прогона — ждём её, а не первый же опрос
    t1 = time.monotonic()
    wait_for(lambda: bench.stack.agents_health()["pending"] == 0, PENDING_WAIT_S)
    drained = time.monotonic() - t1
    assert bench.queued() == 0 and agents_left(bench.stack, PLACES) == ["bench-uzel"] * 4
    texts = {name: (out / name).read_text(encoding="utf-8") for name in OUT_FILES}
    secrets = bench.secrets()
    assert secrets and all(s for s in secrets)                                  # положительный контроль: не пусто
    assert not [name for name, text in texts.items() if any(s in text for s in secrets)]
    for place in PLACES:
        tally = Counter("pass" if v[place, s].passed else "judge" if v[place, s].passed is None else "fail"
                        for s in SCENARIO_IDS)
        hard = sorted({c for s in SCENARIO_IDS for c in v[place, s].hard})
        flags = sorted({f for s in SCENARIO_IDS for f in v[place, s].flags})
        print(f"А6 журнал: место {place:02d} ({PERSONA[place]}): прогонов 12, {dict(tally)}, жёсткие {hard}, "
              f"признаки {flags}")
    delays = [r.visible_delay_s for r in recs if r.key.scenario.startswith("A")]
    print(f"А6 журнал: прогон 48 — {took:.0f} с; visible A по местам 05–08: {delays}; "
          f"очередь перезагрузки опустела через {drained:.0f} с после конца прогона; "
          f"калькулятор ops до сих пор — {bench.stand.calc_calls} вызовов, rate — {bench.stand.calc_rate}")


def _dial_to_cap(stack) -> list[int]:
    """Дозвон до потолка (итоговая доводка Д8б, I-1): прямые вызовы /llm/v1/chat/completions токеном места 05 (место
    прогона) по одному, пока прокси не ответит 402 pcbk_run_spent, — не больше DIAL_MAX. Прокси отказывает по расходу
    с резервами идущих вызовов, и после остановки cap расход без резервов бывает ниже потолка: строка страницы тогда
    «молчит», а не «потолок исчерпан». По одному вызову резервов нет — 402 приходит только на расходе ≥ потолка.
    Коды ответов по порядку."""
    codes: list[int] = []
    for _ in range(DIAL_MAX):
        code, text = stack.http_host("POST", CORE_URL + "/llm/v1/chat/completions", DIAL_CHAT, token=stack.llm_token(5))
        codes.append(code)
        if code == 402:
            assert json.loads(text)["error"]["code"] == "pcbk_run_spent"
            break
        assert code == 200 and text.rstrip().endswith("data: [DONE]")
    return codes


def test_bench_cap_stops_and_page_warns(bench):                                  # m8
    bench.recreate_core(run_id="d8-cap", cap=0.01)
    out = bench.tmp / "cap"
    assert bench.runner_for(out, run_id="d8-cap", cap_usd=0.01, stop_margin_usd=0).run() == "cap"
    assert bench.refusals("pcbk_run_spent") >= 1
    # после стопа — ни одного нового прогона: у места не больше одного прогона — первого плана, и не done; 402 получил
    # хотя бы один (место, чей ход кончился до 402 соседа, до следующей реплики видит жёсткую остановку — interrupted)
    recs = bench.records(out)
    keys = [r.key for r in recs]
    assert len(keys) == len(set(keys)) and set(keys) <= {RunKey(n, SCENARIO_IDS[0], 1) for n in PLACES}
    statuses = {r.status for r in recs}
    assert "capped" in statuses and statuses <= {"capped", "interrupted"}
    end = json.loads((out / "health_end.json").read_text(encoding="utf-8"))["run"]
    assert json.loads((out / "progress.json").read_text(encoding="utf-8"))["run_spent_usd"] == end["spent_usd"] > 0
    before = bench.run_spent()
    codes = _dial_to_cap(bench.stack)                                                # I-1: дозвон до потолка
    assert codes[-1] == 402 and set(codes[:-1]) <= {200}                            # 402 — не позже DIAL_MAX вызовов
    bench.stack.wait_status(lambda s: row_of(s, "llm")["detail"] == RUN_CAP_DETAIL, 30)
    run = bench.stack.llm_health()["run"]
    assert run["id"] == "d8-cap" and run["spent_usd"] >= run["cap_usd"]
    assert run["spent_usd"] <= run["cap_usd"] + (4 * 3 + 1) * FAKE_CALL_USD          # до трёх на место и дозвон
    if len(codes) > 1:                                                              # дозванивались: шаг — один вызов
        assert run["spent_usd"] < run["cap_usd"] + FAKE_CALL_USD + 1e-9
    print(f"А6 журнал: потолок $0.01 — после стопа ${before:.4f}, дозвон {len(codes) - 1} вызовов до 402, итог "
          f"${run['spent_usd']:.4f}, отказов pcbk_run_spent {bench.refusals('pcbk_run_spent')}, прогонов "
          f"{len(recs)}: {dict(Counter(r.status for r in recs))}")
    bench.shot("twin-01-run-cap.png")


def test_bench_cards_check_twin(bench):                                          # калькулятор двойника
    assert cards_check(bench.cards, bench.runner.stand) == ["C2: unlisted", "C4: no_data"] == list(CHECK_GOOD)


def test_bench_twin_judged_table(bench):                                         # Review Focus 1 и 4 Д8б
    bench.recreate_core(run_id="d8-full", cap=5.0)                                 # после теста потолка А6
    out = bench.tmp / "full"
    calls0, rate0 = bench.stand.calc_calls, bench.stand.calc_rate
    t0 = time.monotonic()
    assert bench.runner_for(out, run_id="d8-full").run() == "finished"
    took = time.monotonic() - t0
    recs = bench.records(out)
    assert len(recs) == FULL_RUNS and {r.status for r in recs} == {"done"}
    skipped = [r for r in recs if any(isinstance(e, dict) and e.get("status") == "rate" for e in r.calc.values())]
    assert all(NOTE_CALC_RATE in r.notes for r in skipped)                          # rate — не молча
    t1 = time.monotonic()
    batches = pack_batches(out, bench.records(out), bench.cards, bench.secrets(), seed="d8-full")
    stage = stage_judges(out, "d8-full", root=bench.tmp)                            # каталог судей, как в Б6
    assert sum(write_twin_verdicts(stage / b.name, stage / "verdicts" / b.name) for b in batches) == 136
    assert collect_verdicts(stage, out) == 136                                      # 128 + J1–J4 × 2
    cmap = json.loads((out / "judge" / "map.json").read_text())["controls"]
    assert controls_ok(check_verdicts(out), cmap) == {"J1": True, "J2": True, "J3": True, "J4": True}
    rows = {r.model: r for r in bench.judge_and_table(out)}
    rec = dict(zip(rows, recommend(list(rows.values()), budget_left=1000)))
    assert rows["z-ai/glm-5.3"].judged == 32 and rows["z-ai/glm-5.3"].create_passed == 8
    assert rec["z-ai/glm-5.3"] == "рекомендую"
    assert rec["deepseek/deepseek-v4.1-flash"] == "не рекомендую: неуспех 2/4"      # судья поймал норму C1
    assert rec["google/gemini-3.8-flash"] == "не рекомендую: неуспех 2/4"           # автомат: среднее B2 + 1
    assert rec["openai/gpt-6-sol"] == "не проходит по успеху 1"
    assert rows["z-ai/glm-5.3"].soft.get("numbers_not_from_tools", 0) == 0          # время и «≈» — с источником
    assert rows["deepseek/deepseek-v4.1-flash"].hard.get("invented_norm", 0) > 0    # причины — свои
    assert rows["google/gemini-3.8-flash"].hard.get("number_mismatch", 0) > 0
    assert rows["openai/gpt-6-sol"].create_hard.get("saved_without_consent", 0) > 0
    assert sum(r.calc_skipped for r in rows.values()) == len(skipped) == 0          # сверка калькулятором — у всех
    text = "".join(f.read_text() for b in batches for f in b.glob("*.json"))
    assert "20FAKE_" not in text and not re.search(r"(?i)glm|deepseek|gemini|gpt", text)
    assert not any(w in text.lower() for w in TWIN_VOCAB_WORDS)                       # описания — классами
    assert not re.search(r"\b(127[.,]9|3[.,]42|131[.,]6|128[.,]4)\b", text)           # числа — псевдонимами
    vocab = build_vocab(bench.records(out), bench.cards)
    nodes = [c.node for c in bench.cards.cards.values() if c.node] + [bench.cards.bench_node]       # m13
    for f in (f for b in batches for f in b.glob("*.json")):                          # каждая цифра — в белом списке
        assert_packet_clean(Packet.from_file(f), bench.secrets(), vocab, nodes)
    statuses = Counter(r.status for r in recs)
    print(f"Б3 журнал: полный план — {len(recs)} прогонов за {took:.0f} с ({took / 60:.1f} мин), статусы "
          f"{dict(statuses)}; калькулятор ops за прогон — {bench.stand.calc_calls - calls0} вызовов, rate — "
          f"{bench.stand.calc_rate - rate0}; записей с rate после пауз — {len(skipped)}; пакеты, судья-двойник и "
          f"таблица — {time.monotonic() - t1:.0f} с; пачек {len(batches)}")
    for model, r in rows.items():
        print(f"Б3 журнал: {model}: оценено {r.judged}/{r.planned}, A прошло {r.create_passed}/{r.create_judged}, "
              f"жёсткие {r.hard}, A {r.create_hard}, мягкие {r.soft}, не прогнано {r.not_run}, не оценено "
              f"{r.not_judged}, качество {r.quality}, прогноз 5 000 — {r.f5000}; вердикт: {rec[model]}")


# --- Д9а (А7): калькуляторы на двойнике историана; учение — первым (окно частоты ops, шапка модуля) ---

def test_tool_failing_row_on_page(bench):                                         # Review Focus 3
    bench.recreate_core(bench.cfg.run_id, bench.cfg.cap_usd, extra={"DRILL_DATA": "tool_error"})
    try:
        for _ in range(3):
            assert bench.stand.calc("tag_series", dict(TWIN_ARGS[1][1]))["status"] == "error"
        # учение без SQL — по событиям самих вызовов (Б1 критики р1): общий счётчик ворот растит опрос свежести
        events = bench.stack.data_events("ops", "tag_series", 3)
        assert [(e["outcome"], e["summary"], e["sent"]) for e in events] == [("error", DRILL_SUMMARY, [])] * 3
        assert bench.stack.health_historian()["drill"] == "tool_error"
        s = bench.stack.wait_status(lambda s: row_of(s, "historian")["detail"] == TOOL_FAILING_DETAIL, 40)
        assert (row_of(s, "historian")["state"], row_of(s, "historian")["note"]) == \
            ("warn", "tag_series; учения: tool_error")
        bench.shot("d9a-03-tools-failing.png", row="historian")
    finally:
        bench.recreate_core(bench.cfg.run_id, bench.cfg.cap_usd)
    bench.stack.wait_status(lambda s: row_of(s, "historian")["state"] == "ok", 60)


def test_calc_route_same_as_service(bench):                                       # Review Focus 4
    chat = bench._chat(5)
    chat.login()
    before = bench.stack.data_event_count("student-05", "web")
    for tool, args in TWIN_ARGS:
        page = chat.calc(tool, args)
        direct = bench.stand.calc(tool, args)
        assert page["status"] == "ok", tool
        assert {k: v for k, v in page.items() if k != "cache"} == {k: v for k, v in direct.items() if k != "cache"}
    assert bench.stack.data_event_count("student-05", "web") - before == len(TWIN_ARGS)


def test_calc_page_in_browser(bench):                                             # Д9-R88
    with cdp.Browser() as browser:
        page = browser.page(isolated=True)
        login_to_chat(page, bench.login(5), bench._pw[5], ack_notice=True)
        page.click_text("Калькуляторы")
        page.type("#calc-query", "привод")
        page.click_text("Найти тег")
        page.wait_text("20FAKE_201_PV", 20, css=".calc-matches")
        page.click(".calc-matches button")                                         # I2: совпадение одно — вся строка
        assert not page.eval("!!document.querySelector('textarea')?.getClientRects().length")   # m6: чат скрыт
        page.select("#calc-mode", "profile")
        page.click_text("Посчитать")
        page.wait_selector("svg.profile polyline", 30)
        page.wait_text("В начале", 10)
        shot(page, "d9a-01-profile.png")
        page.type("#calc-tag", "20FAKE_210_RUN", clear=True)
        page.select("#calc-mode", "runtime")
        page.click_text("Посчитать")
        page.wait_text(r"работает \(по суффиксу\)", 30)
        shot(page, "d9a-02-runtime.png")
        assert browser.handler_errors == [] and page.console_errors() == []


# --- Д9б (Б2): «агент = калькулятор» на двойниках; после Д9а — окно частоты ops сброшено учением (шапка модуля) ---

def test_match_twin(bench):                                                       # Review Focus 5
    twin = json.loads((REPO / "bench/match.twin.json").read_text(encoding="utf-8"))
    cases = build_cases(twin["analog"], twin["discrete"], hist_today(bench.stand, twin["analog"][0]))
    assert len(cases) == 19
    got = {}
    for place in (5, 7):                                                          # honest, off_by_one
        runner = bench.match_runner(place, cases, bench.tmp / f"match-{place:02d}")
        try:
            runner.prepare()
            got[place] = {r.case: r for r in runner.run()}
        finally:
            runner.close()
    assert {r.verdict for r in got[5].values()} == {"match"}, {c: r.verdict for c, r in got[5].items()}
    avg = {c.id for c in cases if c.kind in ("avg_prev_shift", "avg_span")}
    assert len(avg) == 6 and {c for c, r in got[7].items() if r.hard} == avg
    assert all(got[7][c].verdict == "mismatch" for c in avg)
    assert "bench-calc" not in agents_left(bench.stack, {5: None, 7: None})       # агент проверки — в архиве
    md = render_match(list(got[5].values()), cases)
    assert "20FAKE" not in md
    bench.page_shot(md, "twin-match.png")
    print(f"Д9б журнал: место 05 — {Counter(r.verdict for r in got[5].values())}; "
          f"место 07 — {Counter(r.verdict for r in got[7].values())}")


def test_bench_cleanup_restores(bench):                                          # последним в модуле
    """Зависит от предыдущих тестов модуля (принято): уборка сверяется после их прогонов, а счётчик калькулятора ops
    (calc_calls > 0) набран прогонами test_bench_twin_run. Отдельно, без них, этот тест не гонять."""
    bench.cleanup()
    listing = bench.stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "list")
    for n in PLACES:                                                                # m-н18: все места
        assert "OPENCODE_CONFIG_CONTENT" not in bench.stack.env_names(f"pcbk-student-{n:02d}")
        assert bench.stack.inspect(f"pcbk-student-{n:02d}")["State"]["Running"] is False     # П2
        assert bench.stack.account_rows(f"d8-probe-bench-{n:02d}") == []           # I-н1
        assert f"место {n:02d}\t" not in listing
    assert bench.stack.llm_health()["run"] is None
    assert bench.queued() == 0 and agents_left(bench.stack, PLACES) == []
    core_env = bench.stack.env_names("pcbk-core")
    assert {"LLM_RUN_ID", "PCBK_TWIN_HISTORIAN"} <= core_env                        # имена есть, значения пусты
    env = dict(e.split("=", 1) for e in bench.stack.inspect("pcbk-core")["Config"]["Env"])
    assert (env["LLM_RUN_ID"], env["LLM_RUN_USD"], env["LLM_RUN_CALLERS"], env["PCBK_TWIN_HISTORIAN"]) == ("",) * 4
    assert env.get("DRILL_DATA", "") == ""                                          # учение Д9а снято
    watchdog = dict(e.split("=", 1) for e in bench.stack.inspect("pcbk-watchdog")["Config"]["Env"])
    assert watchdog["LLM_RUN_SILENT_S"] == "300"
    ops = bench.stack.data_event_count("ops", "http") - bench.ops_events0
    assert ops == bench.stand.calc_calls > 0                                       # счётчик калькулятора — по журналу службы
    print(f"А6 журнал: модуль — {time.monotonic() - bench.t0:.0f} с; калькулятор ops — {bench.stand.calc_calls} "
          f"вызовов (rate — {bench.stand.calc_rate}), событий ops/http в журнале службы — {ops}")
