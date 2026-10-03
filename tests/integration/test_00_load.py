"""Нагрузка на двойниках (Д11б, Б2; Д11-R119…R123, R131, R133): прогонщик pcbk_bench в режиме нагрузки на всех десяти
местах через настоящие edge, шлюз и OpenCode мест к двойнику OpenRouter (персона honest) и двойнику историана в core;
тормоз прогонщика по строке «Память сервера» — сбой памяти, swap от базы окна, давление full; счёт цены реплики под
учением tool_error.

Модуль идёт ПЕРВЫМ (имя — раньше test_accounts.py, R123): все десять мест учётками d11-probe-load-NN (пробные — место
освобождается). В finally фикстуры — двойники памяти в норме и уборка BenchHarness.cleanup (порядок П2, по одному
месту): задержка и персоны двойника OpenRouter сняты, агенты мест в архиве, места без переопределения и остановлены,
учётки удалены и места освобождены, core и сторож — значения test.env. Последний тест сверяет это состояние: следующим
модулям места свободны.

Память машины разработчика (итоговое ревью Д11б, I-4; решение контроллера). Десять мест бодрствуют только в прогоне
десяти мест: после «закончен» прогонщик сам их усыпляет (R122). Учения тормоза — на трёх местах (DRILL; отступление от
брифа, где TEN), цена реплики — на двух; уборка и settle() будят по одному месту. Сторож настоящей памяти машины
(HostMemory): тормоз прогонщика на стенде смотрит на двойник памяти, а настоящую не видит никто — раз в 2 с MemAvailable
хоста; меньше HOST_FLOOR_MIB — места модуля усыплены, тест остановлен понятной ошибкой.

Каскад (итоговое ревью Д11б, I-1): поток прогонщика — в runner_bg (не остановился — SIGINT, как Ctrl+C оператора; и
тогда не остановился — места усыплены, тест падает); RunRefused — кодом в сообщении проверки, а не KeyError; после
учения — двойники в норме, задержки нет, строка памяти «норма», агенты оборванных ходов в архиве (settle()) — и при
падении: каждый шаг отдельно, сбой шага не заслоняет исходную причину.

Тормоз прогонщика, а не core: двойники /proc у сторожа и core — разные файлы (R123а); память, swap и давление меняются
только у сторожа, тормоз core следит и не защёлкивается. Сроки — по независимым источникам (R133): t0 — checked_at
снимка сторожа, где строка памяти впервые не «норма» (тест видит /status.json опросом раз в секунду); вызовы модели мест
— журнал прокси (ts — начало вызова; ход двойника — вызовы по 2 с), сон мест — State.FinishedAt (docker inspect).
Длительности шагов подготовки прогонщика — его журнал (pytest -rP, «подготовка: …»).

Снимки d11b-01…03 — PCBK_SHOTS; строки «Б2 журнал: …» (pytest -rP) — числа нагрузки, сроки тормоза, вызовов модели на
реплику под tool_error, наименьшая свободная память машины.
"""
import contextlib
import json
import re
import signal
import threading
import time
from pathlib import Path

import pytest

from benchstand import BenchHarness, agents_left
from conftest import MEM_AVAIL_MIB, MEM_READERS, REPO, _run, iso_s, row_of, wait_for

TEN = {n: "pcbk/z-ai/glm-5.3" for n in range(1, 11)}
DRILL = {n: TEN[n] for n in (1, 2, 3)}          # учения тормоза — три места (память машины, решение контроллера)
PRICE = {n: TEN[n] for n in (1, 2)}
LOGIN = "d11-probe-load-{n:02d}"                 # LOAD_LOGIN прогонщика (bench/pcbk_bench/__main__.py)
FAKE_CALL_USD = 0.0012                          # цена вызова двойника OpenRouter (USAGE fake_openrouter.py)
TURN_DELAY_S = 2.0                              # ход двойника — вызовы по 2 с (R133)
CALL_LATE_S, ASLEEP_S = 30, 60                  # R133, стенд: вызовов после t0 + 30 с нет, места спят ≤ t0 + 60 с
FIRST_CALL_S = 300                              # подготовка трёх мест; шаги — в журнале прогонщика (I-2)
MEM_ROW_S = 60                                  # давление — «внимание» и снятие за три такта сторожа по 10 с
STOP_JOIN_S = 180                               # прогонщик после SIGINT: abort ходов, сон мест, close()
CALLS_10M = re.compile(r"прогон d11-twin: [^·]*вызовов за 10 мин: [1-9]")   # строка LLM: вызовы прогона уже идут
HOST_MEMINFO = Path("/proc/meminfo")            # настоящая память машины разработчика — не двойник
HOST_FLOOR_MIB = 1536
HOST_POLL_S = 2.0


class HostMemory:
    """Сторож настоящей памяти машины (см. шапку): поток раз в HOST_POLL_S читает MemAvailable хоста; ниже floor_mib —
    docker stop мест модуля (один раз) и метка; check() в главном потоке превращает метку в pytest.fail. low_mib —
    наименьшее увиденное значение (для «Б2 журнал»)."""

    def __init__(self, places, floor_mib: int = HOST_FLOOR_MIB):
        self.names = [f"pcbk-student-{n:02d}" for n in sorted(places)]
        self.floor_mib = floor_mib
        self.low_mib: int | None = None
        self.tripped: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="память-машины", daemon=True)

    @staticmethod
    def available_mib() -> int:
        for line in HOST_MEMINFO.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
        raise RuntimeError("/proc/meminfo: нет MemAvailable")

    def start(self) -> None:
        self.poll()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def poll(self) -> None:
        mib = self.available_mib()
        self.low_mib = mib if self.low_mib is None else min(self.low_mib, mib)
        if mib < self.floor_mib and self.tripped is None:
            self.tripped = mib
            _run(["docker", "stop", "-t", "10", *self.names], check=False)

    def _loop(self) -> None:
        while not self._stop.wait(HOST_POLL_S):
            try:
                self.poll()
            except Exception:                        # noqa: BLE001 — нечитаемо: следующий опрос
                pass

    def check(self) -> None:
        if self.tripped is not None:
            pytest.fail(f"машине не хватает памяти: свободно {self.tripped} МиБ при пороге {self.floor_mib} МиБ — "
                        "места модуля усыплены, тест остановлен (не тормоз прогонщика: тот смотрит на двойник)")


@pytest.fixture(scope="module")
def load(stack, tmp_path_factory):
    h = BenchHarness(stack, tmp_path_factory.mktemp("load"), TEN, config=REPO / "bench/config.load.twin.json",
                     login=LOGIN)
    h.host = HostMemory(TEN)
    h.host.start()
    try:
        h.start()
        yield h
    finally:
        h.host.stop()
        for who in MEM_READERS:                 # у двойников памяти — читатели core и watchdog по отдельности
            stack.set_memory(who, MEM_AVAIL_MIB)
            stack.set_psi(who, 0.0)
        h.cleanup()                             # и задержка двойника OpenRouter (DELETE /_delay)


@pytest.fixture(autouse=True)
def host_memory(load):
    """Память машины — до и после каждого теста модуля."""
    load.host.check()
    yield
    load.host.check()


def watched(load, pred):
    """Условие ожидания, которое сначала смотрит память машины (сработал её сторож — сразу понятная ошибка)."""
    def check(*args):
        load.host.check()
        return pred(*args)
    return check


def run_bg(runner):
    """Прогонщик в потоке: причина — result["reason"], исключение (RunRefused и прочее) — result["error"]."""
    result = {}

    def body():
        try:
            result["reason"] = runner.run()
        except BaseException as exc:            # noqa: BLE001 — в тест кодом, а не KeyError
            result["error"] = exc
    t = threading.Thread(target=body, name="прогонщик", daemon=True)
    t.start()
    return t, result


def outcome(result):
    """Текст для сообщения проверки: причина остановки или код отказа прогонщика."""
    err = result.get("error")
    return f"{type(err).__name__}: {getattr(err, 'code', err)}" if err is not None else result.get("reason")


@contextlib.contextmanager
def runner_bg(load, runner, places):
    """Поток прогонщика с гарантией конца: на выходе живой поток получает SIGINT (как Ctrl+C оператора) и ждётся
    STOP_JOIN_S; не остановился — места прогона усыплены и тест падает: чужой сторож памяти не живёт в следующих
    тестах."""
    t, result = run_bg(runner)
    try:
        yield t, result
    finally:
        if t.is_alive():
            runner._on_signal(signal.SIGINT, None)
            t.join(STOP_JOIN_S)
        if t.is_alive():
            load.stand.sleep_places(sorted(places))
            pytest.fail(f"прогонщик не остановился за {STOP_JOIN_S} с после SIGINT — места усыплены")


def join_watching(load, t, timeout):
    """join потока прогонщика шагами, со сторожем памяти машины."""
    deadline = time.monotonic() + timeout
    while t.is_alive() and time.monotonic() < deadline:
        load.host.check()
        t.join(HOST_POLL_S)
    load.host.check()


def restore(steps, failing):
    """Шаги восстановления — каждый отдельно; сбой шага — строкой журнала, а если тест уже падает — не заслоняет
    исходную причину (иначе поднимается первый)."""
    errors = []
    for what, step in steps:
        try:
            step()
        except Exception as exc:                # noqa: BLE001
            errors.append(exc)
            print(f"Б2 журнал: восстановление — «{what}» не прошло ({type(exc).__name__})")
    if errors and not failing:
        raise errors[0]


def json_times(out):
    p = json.loads((out / "progress.json").read_text())
    return p["started_at"], p["updated_at"]


def callers_of(places):
    return [f"student-{n:02d}" for n in sorted(places)]


def timing(load, places, t0):                                               # R133: журнал прокси и docker inspect
    callers = callers_of(places)
    last_call = max(iso_s(r["ts"]) for r in load.stack.llm_calls(last=500) if r["caller"] in callers)
    finished = [iso_s(load.stack.inspect(f"pcbk-student-{n:02d}")["State"]["FinishedAt"]) for n in places]
    return last_call - t0, max(finished) - t0, min(finished) - t0


def running(load, places):
    return [n for n in sorted(places) if load.stack.inspect(f"pcbk-student-{n:02d}")["State"]["Running"]]


def memory_ok(load, timeout=MEM_ROW_S * 1.5):
    """Строка памяти снова «норма»: следующему прогонщику старт не откажет RunRefused("memory")."""
    load.stack.wait_status(watched(load, lambda d: row_of(d, "memory")["state"] == "ok"), timeout)


def test_load_ten_places_twin(load):                                        # Review Focus 4
    out = load.tmp / "ten"
    begun = time.monotonic()
    with runner_bg(load, load.runner_for(out), TEN) as (t, result):
        s = load.stack.wait_status(watched(load, lambda d: "работает мест: 10" in row_of(d, "gateway")["note"]), 300)
        assert "прогон d11-twin" in row_of(s, "llm")["note"]
        load.stack.wait_status(watched(load, lambda d: CALLS_10M.search(row_of(d, "llm")["note"]) is not None), 300)
        load.shot("d11b-01-load-ten.png", row="gateway")                    # под нагрузкой: вызовы прогона идут
        join_watching(load, t, 900)
    assert result.get("reason") == "finished", outcome(result)
    assert running(load, TEN) == []                                         # R122: после ступени места спят
    recs = load.records(out)
    assert len(recs) == 30 and {r.status for r in recs} == {"done"}
    numbers = load.stand.load_check(callers_of(TEN), *json_times(out))
    assert (numbers["sent_unlisted"], numbers["sql_events"] > 0, numbers["max_sql_5m"] <= 300) == (0, True, True)
    assert numbers["concurrency_minutes"] >= 1
    iso = json.loads((out / "isolation.json").read_text())
    assert len(iso) == 10 and sum(v["foreign_sessions"] + v["foreign_agents"] for v in iso.values()) == 0
    print(f"Б2 журнал: десять мест — прогонов {len(recs)} done за {time.monotonic() - begun:.0f} с; событий службы "
          f"{numbers['events']}, с SQL {numbers['sql_events']}, вне списка в SQL {numbers['sent_unlisted']}, "
          f"наибольшее с SQL за 5 мин {numbers['max_sql_5m']}; вызовов модели {numbers['llm_calls']}, минут "
          f"одновременной работы {numbers['concurrency_minutes']}; свободно на машине не меньше "
          f"{load.host.low_mib} МиБ")


def test_note_numbers_parse_from_real_watchdog(load):                       # I-1 р3: формат note не разошёлся
    from pcbk_bench.load import mem_point
    env = load.stack.test_env()
    band = (int(env["MEM_WARN_MIB"]) + int(env["MEM_FAIL_MIB"])) // 2       # полоса порога стенда (32 ≤ N < 64 МиБ)
    try:
        load.stack.set_memory("watchdog", band)                             # полоса порога: в note есть «свободно N»
        s = load.stack.wait_status(watched(load, lambda d: row_of(d, "memory")["state"] == "warn"), 60)
        p = mem_point(s, {"run_brake": None}, 0, 0.0)
        assert None not in (p.free_mib, p.swap_used_mib, p.swap_grow10_mib, p.psi_full) and p.oom_outside == 0
        assert (p.kind, p.free_mib, p.swap_used_mib, p.swap_grow10_mib, p.psi_full, p.no_data) == \
            ("threshold", band, 0, 0, 0.0, ())
    finally:
        load.stack.set_memory("watchdog", MEM_AVAIL_MIB)
    memory_ok(load, 60)


@pytest.mark.parametrize("name,setter,shot_name", [
    ("mem", lambda s: s.set_memory("watchdog", 16), "d11b-02-runner-memory-stop.png"),
    ("swap", lambda s: s.set_memory("watchdog", 8000, swap_used_mib=900), "d11b-03-runner-swap-stop.png"),
    ("psi", lambda s: s.set_psi("watchdog", 50.0), None),                  # B-1 р2: давление full выше порога
])
def test_runner_hard_stop(load, name, setter, shot_name):                  # тормоз прогонщика, не core
    steps = [("двойник памяти сторожа", lambda: load.stack.set_memory("watchdog", MEM_AVAIL_MIB)),
             ("двойник давления сторожа", lambda: load.stack.set_psi("watchdog", 0.0)),
             ("задержка двойника OpenRouter", lambda: load.delay(0)),
             ("строка памяти «норма»", lambda: memory_ok(load)),
             ("агенты оборванных ходов", lambda: print(f"Б2 журнал: после тормоза ({name}) агенты оборванных ходов — "
                                                       f"мест {len(load.settle())}"))]
    try:
        memory_ok(load)                         # прошлое учение могло оставить «внимание»: старт откажет memory
        load.delay(TURN_DELAY_S)                                            # ход идёт долго — стоп посреди него
        out = load.tmp / name
        callers = callers_of(DRILL)
        with runner_bg(load, load.runner_for(out, scenarios=("A1",), places=DRILL), DRILL) as (t, result):
            begun = time.time()
            wait_for(watched(load, lambda: any(r["caller"] in callers and iso_s(r["ts"]) > begun     # m-3 р2
                                               for r in load.stack.llm_calls(last=20))), FIRST_CALL_S)
            setter(load.stack)
            first = load.stack.wait_status(watched(load, lambda d: row_of(d, "memory")["state"] != "ok"), MEM_ROW_S)
            t0 = iso_s(first["checked_at"])                                 # время сторожа
            join_watching(load, t, 120)
        assert result.get("reason") == "memory", outcome(result)
        wait_for(watched(load, lambda: running(load, DRILL) == []), 60)
        calls_s, asleep_s, first_asleep_s = timing(load, DRILL, t0)
        assert calls_s <= CALL_LATE_S and asleep_s <= ASLEEP_S              # успех 2: журнал прокси и docker
        assert first_asleep_s >= 0                                          # места уснули от этого тормоза, не раньше
        assert load.stack.llm_health()["run_brake"]["state"] == "watching"
        assert set(running(load, TEN)) <= set(DRILL)                       # учение не будило лишних мест
        print(f"Б2 журнал: тормоз прогонщика ({name}) — строка памяти «{row_of(first, 'memory')['state']}», последний "
              f"вызов модели мест через {calls_s:.1f} с после t0, места {', '.join(f'{n:02d}' for n in DRILL)} "
              f"остановлены через {asleep_s:.1f} с (State.FinishedAt), тормоз core — watching")
        if shot_name:
            load.shot(shot_name, row="memory")
    except BaseException:
        restore(steps, failing=True)
        raise
    restore(steps, failing=False)


def test_price_per_reply_under_drill_twin(load):                           # I-2 р2: счёт цены под учением — на двойнике
    steps = [("core без учения", lambda: load.recreate_core(load.cfg.run_id, load.cfg.cap_usd))]
    try:
        load.recreate_core(load.cfg.run_id, load.cfg.cap_usd, extra={"DRILL_DATA": "tool_error"})
        out = load.tmp / "price"
        runner = load.runner_for(out, load_data="none", scenarios=("A1", "B3", "C2"), places=PRICE)
        with runner_bg(load, runner, PRICE) as (t, result):
            join_watching(load, t, 900)
        assert result.get("reason") == "finished", outcome(result)
        load.stand.load_check(callers_of(PRICE), *json_times(out), out=out)    # пишет OUT/price.json
        price = json.loads((out / "price.json").read_text())
        assert price["usd"] == pytest.approx(price["calls"] * FAKE_CALL_USD) and price["replies"] > 0
        print(f"Б2 журнал: под tool_error на двойнике — вызовов на реплику {price['calls_per_reply']:.2f} (реплик "
              f"{price['replies']}, вызовов {price['calls']}, ${price['usd']:.4f})")
    except BaseException:
        restore(steps, failing=True)
        raise
    restore(steps, failing=False)


def test_load_cleanup_restores(load):                                       # последним
    load.cleanup()
    rows = load.stack.exec("pcbk-core", "python", "-m", "pcbk_core.gateway.accounts", "list")
    assert "d11-probe-load-" not in rows and "место " not in rows
    assert agents_left(load.stack, range(1, 11)) == []
    assert running(load, range(1, 11)) == []
    print(f"Б2 журнал: свободно на машине за модуль не меньше {load.host.low_mib} МиБ (порог сторожа "
          f"{HOST_FLOOR_MIB} МиБ)")
