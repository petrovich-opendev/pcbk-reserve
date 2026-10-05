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
десяти мест: фикстура места только создаёт (спят), будит их прогонщик по одному через шлюз (R119), после «закончен» он
сам их усыпляет (R122). Учения тормоза — на трёх местах (DRILL; отступление от брифа, где TEN), цена реплики — на двух;
уборка и settle() будят по одному месту. Сторож настоящей памяти машины (HostMemory): тормоз прогонщика на стенде
смотрит на двойник памяти, а настоящую не видит никто — раз в 2 с MemAvailable хоста; меньше HOST_FLOOR_MIB — места
модуля остановлены, тест остановлен понятной ошибкой с числами (и при сбое подготовки фикстуры). Следующий тест
начинается, только когда свободно снова не меньше HOST_REARM_MIB: срабатывание в прогоне десяти мест не роняет учения
на трёх местах. До старта прогонщика — прогноз (HostMemory.room_for): места в простое по PLACE_IDLE_MIB не помещаются
над HOST_FLOOR_MIB — тест падает сразу с числами, места не будятся.

Доводка по прогону 2 Б2 (05.10, интеграцией не проверено): прогноз отказал десяти местам до пробуждения (свободно
3809 МиБ, нужно не меньше 3896 МиБ в простое) — места не будились, сторож машины не срабатывал, каскада нет; учения
тормоза на трёх местах, цена реплики и уборка прошли, снимки учений — со строками мест «спит». Дефекта кода нет —
ёмкость машины. Под ходами место стоило 390–467 МиБ (не 390): прогноз под ходами — по наибольшему замеру, отказ — как
был.

Доводка по прогону 1 Б2 (05.10, интеграцией не проверено): места будились по одному, свободная память машины падала
ровными ступенями ≈ 236 МиБ на место (3723 МиБ в начале теста — 10 × 236 МиБ ниже порога 1536 МиБ); сторож машины
сработал на десятом месте (1502 МиБ), OOM нет. Дефекта кода нет — ёмкость машины рядом с живым Dify; прогноз теперь
говорит это до пробуждения мест. Снимок d11b-02 показывал места «работает» (снимок сторожа на секунду старше сна) —
теперь снимок ждёт строки мест «спит».

Доводка по прогону 0 Б2 (05.10, интеграцией не проверено): фикстура поднимала все десять мест одной командой up —
свободная память машины за 25 с упала с 3670 до 1453 МиБ, сторож машины остановил места (SIGTERM, код 143), и все семь
тестов дали ERROR в подготовке как «место 01 exited 143», без слова о памяти.

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
import logging
import re
import signal
import threading
import time
from datetime import datetime, timezone
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
# десять мест до «работает мест: 10» и до первых вызовов прогона — от старта прогонщика. Места спят, и прогонщик будит
# их по одному: вход и пробуждение спящего места через шлюз — 6,2–11,5 с (журнал прогонщика «подготовка: место NN —
# вход и пробуждение», прогоны 1–2 Б2, 05.10) — до ≈ 115 с на десять мест и такт сторожа 10 с; срок прежний, запас
# ≈ ×2,4
TEN_AWAKE_S = 300
MEM_ROW_S = 60                                  # давление — «внимание» и снятие за три такта сторожа по 10 с
STOP_JOIN_S = 180                               # прогонщик после SIGINT: abort ходов, сон мест, close()
CALLS_10M = re.compile(r"прогон d11-twin: [^·]*вызовов за 10 мин: [1-9]")   # строка LLM: вызовы прогона уже идут
HOST_MEMINFO = Path("/proc/meminfo")            # настоящая память машины разработчика — не двойник
HOST_FLOOR_MIB = 1536
HOST_POLL_S = 2.0
# снова на страже после срабатывания: та же планка свободной памяти, что перед любым интеграционным прогоном на этой
# машине (правило контроллера); ждём не дольше HOST_REARM_S — после остановки мест память вернулась за ≤ 5 с (прогон 0
# Б2: 1453 → 4038 МиБ между соседними строками журнала памяти с шагом 5 с)
HOST_REARM_MIB = 3500
HOST_REARM_S = 60
# память машины на одно бодрствующее место (рядом живой Dify). В простое — 236 МиБ (прогон 1 Б2, 05.10: MemAvailable
# ровными ступенями 3730 → 1606 МиБ на девяти разбуженных местах; docker stats — 231–242 МиБ на место). Под ходами —
# 390–467 МиБ (прогон 2 Б2, 05.10: от «свободно» строки прогноза до наименьшего MemAvailable за тест — учения mem, swap,
# psi на трёх местах 3889 → 2628, 3765 → 2596, 3755 → 2355 МиБ, цена реплики на двух 3889 → 3078 МиБ; вместе с ростом
# core и сторожа под ходами; docker stats мест под ходами — 386–422 МиБ, прогоны 1–2), прогноз под ходами — по
# наибольшему. Отказ — по прогнозу в простое (HostMemory.room_for), под ходами — строкой журнала и в тексте отказа
PLACE_IDLE_MIB = 236
PLACE_TURN_MIB = 467
ROWS_ASLEEP_S = 30                              # строки мест «спит» — следующий снимок сторожа (такт 10 с), запас ×3

log = logging.getLogger("pcbk.test.load")


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%SZ")


class HostMemory:
    """Сторож настоящей памяти машины (см. шапку). Поток раз в HOST_POLL_S читает MemAvailable хоста; ниже floor_mib —
    метка tripped (МиБ, время UTC), строка журнала WARNING и docker stop мест модуля (один раз на срабатывание).
    check() (в ожиданиях теста и при сбое подготовки фикстуры) и after() (конец теста) превращают метку в pytest.fail с
    числами — один отчёт на срабатывание. begin(тест) перед тестом: отчитанное срабатывание снимается, когда свободно
    снова ≥ HOST_REARM_MIB (≤ HOST_REARM_S, иначе тест не начинается) — строкой «Б2 журнал» и в trips. low_mib —
    наименьшее за модуль; test_base_mib и test_low_mib — в начале теста и наименьшее за тест."""

    def __init__(self, places, floor_mib: int = HOST_FLOOR_MIB):
        self.names = [f"pcbk-student-{n:02d}" for n in sorted(places)]
        self.floor_mib = floor_mib
        self.low_mib: int | None = None
        self.test_base_mib: int | None = None
        self.test_low_mib: int | None = None
        self.tripped: tuple[int, str] | None = None
        self.trips: list[str] = []
        self._reported = False
        self._test = "подготовка фикстуры load"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="память-машины", daemon=True)

    @staticmethod
    def available_mib() -> int:
        for line in HOST_MEMINFO.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
        raise RuntimeError("/proc/meminfo: нет MemAvailable")

    def start(self) -> None:
        self.test_base_mib = self.available_mib()
        self.poll()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def poll(self) -> None:
        mib = self.available_mib()
        with self._lock:
            self.low_mib = mib if self.low_mib is None else min(self.low_mib, mib)
            self.test_low_mib = mib if self.test_low_mib is None else min(self.test_low_mib, mib)
            trip = mib < self.floor_mib and self.tripped is None
            if trip:
                self.tripped = (mib, _utc())
            test = self._test
        if trip:
            log.warning("память машины: свободно %d МиБ < %d МиБ (%s) — места модуля останавливаются", mib,
                        self.floor_mib, test)
            _run(["docker", "stop", "-t", "10", *self.names], check=False)

    def _loop(self) -> None:
        while not self._stop.wait(HOST_POLL_S):
            try:
                self.poll()
            except Exception as exc:                 # noqa: BLE001 — нечитаемо: следующий опрос
                log.warning("память машины: опрос не удался (%s)", type(exc).__name__)

    def room_for(self, places: int) -> int:
        """До пробуждения мест прогонщиком: прогноз свободной памяти машины, когда places мест бодрствуют в простое, —
        свободно сейчас − places × PLACE_IDLE_MIB (ходы и Chrome снимка — сверх него, их держит поток сторожа). Прогноз
        ниже floor_mib — тест падает сразу с числами, места не будятся: сторож всё равно остановил бы их на пороге
        (прогон 1 Б2 — на десятом месте), а машина с живым Dify не идёт к порогу зря. Строка «Б2 журнал» — и при успехе;
        возвращает прогноз."""
        base = self.available_mib()
        idle, busy = base - places * PLACE_IDLE_MIB, base - places * PLACE_TURN_MIB
        print(f"Б2 журнал: память машины до пробуждения мест ({places}) — свободно {base} МиБ, прогноз в простое "
              f"{idle} МиБ, под ходами ≈ {busy} МиБ (порог сторожа {self.floor_mib} МиБ)")
        if idle < self.floor_mib:
            pytest.fail(f"машине не хватает памяти на {places} мест: свободно {base} МиБ, места в простое — ≈ "
                        f"{places * PLACE_IDLE_MIB} МиБ (по {PLACE_IDLE_MIB} МиБ), прогноз {idle} МиБ ниже порога "
                        f"сторожа {self.floor_mib} МиБ — места не будились ({self._test}). До старта нужно "
                        f"свободных не меньше {self.floor_mib + places * PLACE_IDLE_MIB} МиБ, с ходами — ≈ "
                        f"{self.floor_mib + places * PLACE_TURN_MIB} МиБ")
        return idle

    def reason(self) -> str:
        mib, at = self.tripped
        return (f"машине не хватает памяти: в {at} свободно {mib} МиБ при пороге {self.floor_mib} МиБ ({self._test}; в "
                f"начале — {self.test_base_mib} МиБ) — места модуля остановлены, тест остановлен (не тормоз "
                "прогонщика: тот смотрит на двойник памяти)")

    def check(self) -> None:
        if self.tripped is not None:
            self._reported = True
            pytest.fail(self.reason())

    def after(self) -> None:
        """Конец теста: срабатывание, о котором тест не сказал (вне ожиданий со сторожем), — ошибкой; отчитанное —
        нет."""
        if self.tripped is not None and not self._reported:
            self.check()

    def begin(self, test: str) -> None:
        """Перед тестом: неотчитанное срабатывание — ошибкой этого теста; отчитанное снимается, когда свободно снова
        ≥ HOST_REARM_MIB (ожидание ≤ HOST_REARM_S, иначе тест не начинается); затем отсчёт теста."""
        seen = self.tripped
        if seen is not None:
            if not self._reported:
                self.check()
            deadline = time.monotonic() + HOST_REARM_S
            mib = self.available_mib()
            while mib < HOST_REARM_MIB and time.monotonic() < deadline:
                time.sleep(HOST_POLL_S)
                mib = self.available_mib()
            if mib < HOST_REARM_MIB:
                pytest.fail(f"память машины не вернулась после срабатывания сторожа ({self._test}): свободно {mib} МиБ "
                            f"< {HOST_REARM_MIB} МиБ за {HOST_REARM_S} с — тест не начат")
            self.trips.append(f"{self._test} — {seen[0]} МиБ в {seen[1]}")
            print(f"Б2 журнал: сторож памяти машины сработал ({self._test} — {seen[0]} МиБ в {seen[1]}); свободно "
                  f"снова {mib} МиБ — {test} идёт")
        mib = self.available_mib()
        with self._lock:
            if self.tripped is seen:            # снимается только разобранное здесь; новое срабатывание остаётся
                self.tripped, self._reported = None, False
            self._test = test
            self.test_base_mib = self.test_low_mib = mib


@pytest.fixture(scope="module")
def load(stack, tmp_path_factory):
    h = BenchHarness(stack, tmp_path_factory.mktemp("load"), TEN, config=REPO / "bench/config.load.twin.json",
                     login=LOGIN)
    h.host = HostMemory(TEN)
    h.host.start()
    try:
        try:
            h.start()                           # места нагрузки созданы и спят — будит их прогонщик (R119)
        except Exception:
            h.host.check()                      # сработал сторож машины — его причина, а не «место exited 143»
            raise
        yield h
    finally:
        h.host.stop()
        for who in MEM_READERS:                 # у двойников памяти — читатели core и watchdog по отдельности
            stack.set_memory(who, MEM_AVAIL_MIB)
            stack.set_psi(who, 0.0)
        h.cleanup()                             # и задержка двойника OpenRouter (DELETE /_delay)


@pytest.fixture(autouse=True)
def host_memory(load, request):
    """Память машины — до (begin: и снятие отчитанного срабатывания прошлого теста) и после каждого теста модуля."""
    load.host.begin(request.node.name)
    yield
    load.host.after()


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
    тестах. До старта потока — прогноз памяти машины (HostMemory.room_for): места не помещаются и в простое — тест
    падает, прогонщик не начат и мест не будит."""
    load.host.room_for(len(places))
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
    """После теста — и при падении (сторож памяти машины, отказ прогонщика) — агенты оборванных ходов в архиве
    (settle(), как после учения): иначе первое учение откажет place_agents, и одно падение уронит следующие тесты."""
    steps = [("агенты оборванных ходов", lambda: print(f"Б2 журнал: после десяти мест агенты оборванных ходов — мест "
                                                       f"{len(load.settle())}"))]
    try:
        _ten_places(load)
    except BaseException:
        restore(steps, failing=True)
        raise
    restore(steps, failing=False)


def _ten_places(load):
    out = load.tmp / "ten"
    begun = time.monotonic()
    with runner_bg(load, load.runner_for(out), TEN) as (t, result):
        # места спят, прогонщик будит их по одному: его отказ кончает ожидание сразу — причина его, а не срок
        s = load.stack.wait_status(watched(load, lambda d: not t.is_alive()
                                           or "работает мест: 10" in row_of(d, "gateway")["note"]), TEN_AWAKE_S)
        assert t.is_alive(), f"прогонщик кончил до десяти работающих мест: {outcome(result)}"
        assert "прогон d11-twin" in row_of(s, "llm")["note"]
        load.stack.wait_status(watched(load, lambda d: not t.is_alive()
                                       or CALLS_10M.search(row_of(d, "llm")["note"]) is not None), TEN_AWAKE_S)
        assert t.is_alive(), f"прогонщик кончил до вызовов модели: {outcome(result)}"
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
          f"одновременной работы {numbers['concurrency_minutes']}; свободно на машине в начале "
          f"{load.host.test_base_mib} МиБ, не меньше {load.host.test_low_mib} МиБ — на место ≈ "
          f"{(load.host.test_base_mib - load.host.test_low_mib) / len(TEN):.0f} МиБ")


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
            # страница — последний снимок сторожа, а места уснули только что: снимок — когда их строки уже «спит»
            # (прогон 1 Б2: d11b-02 — снимок сторожа 09:47:52, места остановлены ≈ 09:47:53, на снимке «работает»)
            load.stack.wait_status(watched(load, lambda d: all(row_of(d, f"student-{n:02d}")["detail"] == "спит"
                                                               for n in DRILL)), ROWS_ASLEEP_S)
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
          f"{HOST_FLOOR_MIB} МиБ); срабатываний сторожа {len(load.host.trips)}"
          + "".join(f"; {trip}" for trip in load.host.trips))
