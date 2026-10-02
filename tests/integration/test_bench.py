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
"""
import time
from collections import Counter

import pytest

from benchstand import BenchHarness, agents_left
from conftest import row_of, wait_for
from pcbk_bench.checks import check_run
from pcbk_bench.record import RunKey
from pcbk_bench.runner import load_tag_units
from pcbk_bench.scenarios import SCENARIO_IDS
from pcbk_watchdog.checks import RUN_CAP_DETAIL, RUN_SILENT_DETAIL

PLACES = {5: "pcbk/z-ai/glm-5.3", 6: "pcbk/deepseek/deepseek-v4.1-flash", 7: "pcbk/google/gemini-3.8-flash",
          8: "pcbk/openai/gpt-6-sol"}
PERSONA = {5: "honest", 6: "inventor", 7: "off_by_one", 8: "eager_saver"}
FAKE_CALL_USD = 0.0012                  # цена вызова двойника OpenRouter (USAGE fake_openrouter.py)
PENDING_WAIT_S = 60                     # перезагрузка места — после 3 + 5 с простоя (reload.py), с запасом
OUT_FILES = ("runs.jsonl", "sessions.log", "progress.json", "tag_units.json", "health_end.json")


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


def test_bench_cap_stops_and_page_warns(bench):                                  # m8
    bench.recreate_core(run_id="d8-cap", cap=0.01)
    assert bench.runner_for(bench.tmp / "cap", run_id="d8-cap", cap_usd=0.01, stop_margin_usd=0).run() == "cap"
    assert bench.refusals("pcbk_run_spent") >= 1
    bench.stack.wait_status(lambda s: row_of(s, "llm")["detail"] == RUN_CAP_DETAIL, 30)
    run = bench.stack.llm_health()["run"]
    assert run["id"] == "d8-cap" and run["spent_usd"] >= run["cap_usd"]
    assert run["spent_usd"] <= run["cap_usd"] + 4 * 3 * FAKE_CALL_USD                # до трёх на место
    print(f"А6 журнал: потолок $0.01 — потрачено ${run['spent_usd']:.4f}, отказов pcbk_run_spent "
          f"{bench.refusals('pcbk_run_spent')}")
    bench.shot("twin-01-run-cap.png")


def test_bench_cleanup_restores(bench):                                          # последним в модуле
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
    watchdog = dict(e.split("=", 1) for e in bench.stack.inspect("pcbk-watchdog")["Config"]["Env"])
    assert watchdog["LLM_RUN_SILENT_S"] == "300"
    ops = bench.stack.data_event_count("ops", "http") - bench.ops_events0
    assert ops == bench.stand.calc_calls > 0                                       # счётчик калькулятора — по журналу службы
    print(f"А6 журнал: модуль — {time.monotonic() - bench.t0:.0f} с; калькулятор ops — {bench.stand.calc_calls} "
          f"вызовов (rate — {bench.stand.calc_rate}), событий ops/http в журнале службы — {ops}")
