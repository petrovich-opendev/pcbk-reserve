"""Нагрузка на двойниках (Д11б, Б2; Д11-R119…R123, R131, R133): прогонщик pcbk_bench в режиме нагрузки на всех десяти
местах через настоящие edge, шлюз и OpenCode мест к двойнику OpenRouter (персона honest) и двойнику историана в core;
тормоз прогонщика по строке «Память сервера» — сбой памяти, swap от базы окна, давление full; счёт цены реплики под
учением tool_error. Между учениями тормоза — BenchHarness.settle(): агент, сохранённый посреди оборванного тормозом
хода, в записи прогона не остаётся и держал бы место (place_agents R121) — в архив до следующего учения.

Модуль идёт ПЕРВЫМ (имя — раньше test_accounts.py, R123): все десять мест учётками d11-probe-load-NN (пробные — место
освобождается). В finally фикстуры — двойники памяти в норме и уборка BenchHarness.cleanup (порядок П2): задержка и
персоны двойника OpenRouter сняты, агенты мест в архиве, места без переопределения и остановлены, учётки удалены и места
освобождены, core и сторож — значения test.env. Последний тест сверяет это состояние: следующим модулям места свободны.

Тормоз прогонщика, а не core: двойники /proc у сторожа и core — разные файлы (R123а); память, swap и давление меняются
только у сторожа, тормоз core следит и не защёлкивается. Сроки — по независимым источникам (R133): t0 — checked_at
снимка сторожа, где строка памяти впервые не «норма» (тест видит /status.json опросом раз в секунду); вызовы модели мест
— журнал прокси (ts — начало вызова; ход двойника — вызовы по 2 с), сон мест — State.FinishedAt (docker inspect).

Снимки d11b-01…03 — PCBK_SHOTS; строки «Б2 журнал: …» (pytest -rP) — числа нагрузки, сроки тормоза, вызовов модели на
реплику под tool_error.
"""
import json
import re
import shutil
import signal
import threading
import time

import pytest

from benchstand import BenchHarness, agents_left
from conftest import MEM_AVAIL_MIB, MEM_READERS, REPO, iso_s, row_of, wait_for

TEN = {n: "pcbk/z-ai/glm-5.3" for n in range(1, 11)}
LOGIN = "d11-probe-load-{n:02d}"                 # LOAD_LOGIN прогонщика (bench/pcbk_bench/__main__.py)
FAKE_CALL_USD = 0.0012                          # цена вызова двойника OpenRouter (USAGE fake_openrouter.py)
TURN_DELAY_S = 2.0                              # ход двойника — вызовы по 2 с (R133)
CALL_LATE_S, ASLEEP_S = 30, 60                  # R133, стенд: вызовов после t0 + 30 с нет, места спят ≤ t0 + 60 с
# подготовка будит десять спящих мест по одному и ждёт пустой /session/status каждого (до 60 с на место); в первом
# прогоне подготовка второго прогонщика шла 6,3 мин — шаг не установлен (отчёт Б2), срок — с запасом
FIRST_CALL_S = 900
MEM_ROW_S = 60                                  # давление — «внимание» и снятие за три такта сторожа по 10 с
CALLS_10M = re.compile(r"прогон d11-twin: [^·]*вызовов за 10 мин: [1-9]")   # строка LLM: вызовы прогона уже идут


@pytest.fixture(scope="module")
def load(stack, tmp_path_factory):
    h = BenchHarness(stack, tmp_path_factory.mktemp("load"), TEN, config=REPO / "bench/config.load.twin.json",
                     login=LOGIN)
    try:
        h.start()
        yield h
    finally:
        for who in MEM_READERS:                 # у двойников памяти — читатели core и watchdog по отдельности
            stack.set_memory(who, MEM_AVAIL_MIB)
            stack.set_psi(who, 0.0)
        h.cleanup()                             # и задержка двойника OpenRouter (DELETE /_delay)


def run_bg(runner):
    result = {}
    t = threading.Thread(target=lambda: result.update(reason=runner.run()), daemon=True)
    t.start()
    return t, result


def json_times(out):
    p = json.loads((out / "progress.json").read_text())
    return p["started_at"], p["updated_at"]


def timing(load, places, t0):                                               # R133: журнал прокси и docker inspect
    callers = [f"student-{n:02d}" for n in places]
    last_call = max(iso_s(r["ts"]) for r in load.stack.llm_calls(last=500) if r["caller"] in callers)
    finished = [iso_s(load.stack.inspect(f"pcbk-student-{n:02d}")["State"]["FinishedAt"]) for n in places]
    return last_call - t0, max(finished) - t0, min(finished) - t0


def memory_ok(stack, timeout=MEM_ROW_S * 1.5):
    """Строка памяти снова «норма»: следующему прогонщику старт не откажет RunRefused("memory")."""
    stack.wait_status(lambda d: row_of(d, "memory")["state"] == "ok", timeout)


def test_load_ten_places_twin(load):                                        # Review Focus 4
    out = load.tmp / "ten"
    begun = time.monotonic()
    t, result = run_bg(load.runner_for(out))
    s = load.stack.wait_status(lambda d: "работает мест: 10" in row_of(d, "gateway")["note"], 300)
    assert "прогон d11-twin" in row_of(s, "llm")["note"]
    load.stack.wait_status(lambda d: CALLS_10M.search(row_of(d, "llm")["note"]) is not None, 300)   # уже под нагрузкой
    load.shot("d11b-01-load-ten.png", row="gateway")
    t.join(900)
    assert result["reason"] == "finished"
    recs = load.records(out)
    assert len(recs) == 30 and {r.status for r in recs} == {"done"}
    numbers = load.stand.load_check([f"student-{n:02d}" for n in range(1, 11)], *json_times(out))
    assert (numbers["sent_unlisted"], numbers["sql_events"] > 0, numbers["max_sql_5m"] <= 300) == (0, True, True)
    assert numbers["concurrency_minutes"] >= 1
    iso = json.loads((out / "isolation.json").read_text())
    assert len(iso) == 10 and sum(v["foreign_sessions"] + v["foreign_agents"] for v in iso.values()) == 0
    print(f"Б2 журнал: десять мест — прогонов {len(recs)} done за {time.monotonic() - begun:.0f} с; событий службы "
          f"{numbers['events']}, с SQL {numbers['sql_events']}, вне списка в SQL {numbers['sent_unlisted']}, "
          f"наибольшее с SQL за 5 мин {numbers['max_sql_5m']}; вызовов модели {numbers['llm_calls']}, минут "
          f"одновременной работы {numbers['concurrency_minutes']}")


def test_note_numbers_parse_from_real_watchdog(load):                       # I-1 р3: формат note не разошёлся
    from pcbk_bench.load import mem_point
    env = load.stack.test_env()
    band = (int(env["MEM_WARN_MIB"]) + int(env["MEM_FAIL_MIB"])) // 2       # полоса порога стенда (32 ≤ N < 64 МиБ)
    load.stack.set_memory("watchdog", band)                                 # полоса порога: в note есть «свободно N»
    s = load.stack.wait_status(lambda d: row_of(d, "memory")["state"] == "warn", 60)
    p = mem_point(s, {"run_brake": None}, 0, 0.0)
    assert None not in (p.free_mib, p.swap_used_mib, p.swap_grow10_mib, p.psi_full) and p.oom_outside == 0
    assert (p.kind, p.free_mib, p.swap_used_mib, p.swap_grow10_mib, p.psi_full, p.no_data) == \
        ("threshold", band, 0, 0, 0.0, ())
    load.stack.set_memory("watchdog", MEM_AVAIL_MIB)
    memory_ok(load.stack, 60)


@pytest.mark.parametrize("name,setter,shot_name", [
    ("mem", lambda s: s.set_memory("watchdog", 16), "d11b-02-runner-memory-stop.png"),
    ("swap", lambda s: s.set_memory("watchdog", 8000, swap_used_mib=900), "d11b-03-runner-swap-stop.png"),
    ("psi", lambda s: s.set_psi("watchdog", 50.0), None),                  # B-1 р2: давление full выше порога
])
def test_runner_hard_stop(load, name, setter, shot_name):                  # тормоз прогонщика, не core
    memory_ok(load.stack)                       # строка памяти «норма» — иначе старт откажет memory (сбой прошлого)
    load.delay(TURN_DELAY_S)                                                # ход идёт долго — стоп посреди него
    out = load.tmp / name
    out.mkdir(mode=0o700)                       # единицы тегов карточек — из прогона десяти мест: подготовка
    shutil.copyfile(load.tmp / "ten" / "tag_units.json", out / "tag_units.json")   # не зовёт калькулятор ops
    runner = load.runner_for(out, scenarios=("A1",))
    t = None
    try:
        begun = time.time()
        t, result = run_bg(runner)
        wait_for(lambda: any(r["caller"].startswith("student-") and iso_s(r["ts"]) > begun          # m-3 р2
                             for r in load.stack.llm_calls(last=20)), FIRST_CALL_S)
        setter(load.stack)
        first = load.stack.wait_status(lambda d: row_of(d, "memory")["state"] != "ok", MEM_ROW_S)
        t0 = iso_s(first["checked_at"])                                     # время сторожа
        t.join(120)
        assert result["reason"] == "memory"
        wait_for(lambda: all(not load.stack.inspect(f"pcbk-student-{n:02d}")["State"]["Running"] for n in TEN), 60)
        calls_s, asleep_s, first_asleep_s = timing(load, TEN, t0)
        assert calls_s <= CALL_LATE_S and asleep_s <= ASLEEP_S              # успех 2: журнал прокси и docker
        assert first_asleep_s >= 0                                          # места уснули от этого тормоза, не раньше
        assert load.stack.llm_health()["run_brake"]["state"] == "watching"
        print(f"Б2 журнал: тормоз прогонщика ({name}) — строка памяти «{row_of(first, 'memory')['state']}», последний "
              f"вызов модели мест через {calls_s:.1f} с после t0, все десять мест остановлены через {asleep_s:.1f} с "
              f"(State.FinishedAt), тормоз core — watching")
        if shot_name:
            load.shot(shot_name, row="memory")
    finally:
        if t is not None and t.is_alive():      # тест упал раньше тормоза: прогонщик не идёт в следующее учение
            runner._on_signal(signal.SIGINT, None)                          # как Ctrl+C оператора
            t.join(180)
        load.stack.set_memory("watchdog", MEM_AVAIL_MIB)
        load.stack.set_psi("watchdog", 0.0)
        load.delay(0)
    memory_ok(load.stack)
    strays = load.settle()                      # агенты оборванных ходов — в архив: следующее учение с чистых мест
    print(f"Б2 журнал: после тормоза ({name}) агенты оборванных ходов — мест {len(strays)}")


def test_price_per_reply_under_drill_twin(load):                           # I-2 р2: счёт цены под учением — на двойнике
    load.recreate_core(load.cfg.run_id, load.cfg.cap_usd, extra={"DRILL_DATA": "tool_error"})
    out = load.tmp / "price"
    r = load.runner_for(out, load_data="none", scenarios=("A1", "B3", "C2"), places={1: TEN[1], 2: TEN[2]})
    assert r.run() == "finished"
    load.stand.load_check(["student-01", "student-02"], *json_times(out), out=out)          # пишет OUT/price.json
    price = json.loads((out / "price.json").read_text())
    assert price["usd"] == pytest.approx(price["calls"] * FAKE_CALL_USD) and price["replies"] > 0
    print(f"Б2 журнал: под tool_error на двойнике — вызовов на реплику {price['calls_per_reply']:.2f} (реплик "
          f"{price['replies']}, вызовов {price['calls']}, ${price['usd']:.4f})")
    load.recreate_core(load.cfg.run_id, load.cfg.cap_usd)


def test_load_cleanup_restores(load):                                       # последним
    load.cleanup()
    rows = load.stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "list")
    assert "d11-probe-load-" not in rows and "место " not in rows
    assert agents_left(load.stack, range(1, 11)) == []
    assert all(not load.stack.inspect(f"pcbk-student-{n:02d}")["State"]["Running"] for n in range(1, 11))
