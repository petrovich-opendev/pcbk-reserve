# core/tests/test_llm_brake.py — Д11-R117: тормоз прогона в core
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from helpers import (BRAKE_PROC, CORE_DIR, LLM_SETTINGS, LLM_TOKENS, OPS_LLM_TOKEN, SETTINGS, FakeMono, chat_body,
                     make_llm_app, make_llm_role, wait_until)
from pcbk_core.app import create_app
from pcbk_core.llm import LlmRole
from pcbk_core.llm import brake as brake_module
from pcbk_core.llm.api import LLM_MESSAGES, REFUSALS
from pcbk_core.llm.brake import (BRAKE_HOLD_CHECKS, BRAKE_KEYS, BRAKE_REASONS, RunBrake, read_meminfo, read_oom_kill,
                                 read_psi_full)
from pcbk_core.llm.ledger import Budget, Ledger, RunSpec
from pcbk_core.settings import LlmSettings
from test_llm_run import post, spend                                       # помощники Д8 А1

NOW = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)
MEM = "MemTotal: 16384000 kB\nMemAvailable: {} kB\nSwapTotal: 4194304 kB\nSwapFree: {} kB\n"
PSI = "some avg10=1.00 avg60=0 avg300=0 total=1\nfull avg10={} avg60=0 avg300=0 total=1\n"
RUN = {"LLM_RUN_ID": "d11-test", "LLM_RUN_USD": 5.0, "LLM_RUN_CALLERS": "student-01,student-02"}


def files(tmp_path, avail=5000, swap_used=0, oom=3, full=0.5):
    mi, vm, ps = tmp_path / "meminfo", tmp_path / "vmstat", tmp_path / "pressure"
    mi.write_text(MEM.format(avail * 1024, 4194304 - swap_used * 1024)); vm.write_text(f"oom_kill {oom}\n")
    ps.write_text(PSI.format(full))
    return mi, vm, ps


def make(tmp_path, clock, swap_base=-1, run_id="d11-test", **kw):
    mi, vm, ps = files(tmp_path, **kw)
    led = Ledger(str(tmp_path / "core.db")); assert led.open()
    b = RunBrake((str(mi), str(vm), str(ps)), (1024, 512, swap_base, 10.0), meta=led, run_id=run_id,
                 monotonic=clock, wallclock=lambda: NOW, started_mono=clock())
    return b, mi, vm, ps, led


def test_readers(tmp_path):
    mi, vm, ps = files(tmp_path, avail=5000, swap_used=100, oom=7, full=2.5)
    m = read_meminfo(str(mi))
    assert (m.avail_mib, m.total_mib, m.swap_used_mib, read_oom_kill(str(vm)), read_psi_full(str(ps))) == (5000, 16000, 100, 7, 2.5)
    assert read_meminfo(str(tmp_path / "x")) is None and read_oom_kill(str(tmp_path / "x")) is None
    assert read_psi_full(str(tmp_path / "x")) is None


@pytest.mark.parametrize("change,reason,need", [                           # m-3/m-4 р3: N подряд, oom — сразу
    (lambda mi, vm, ps: mi.write_text(MEM.format(900 * 1024, 4194304)), "memory", 3),
    (lambda mi, vm, ps: mi.write_text(MEM.format(5000 * 1024, 4194304 - 600 * 1024)), "swap", 3),
    (lambda mi, vm, ps: ps.write_text(PSI.format(12.0)), "psi", 3),
    (lambda mi, vm, ps: vm.write_text("oom_kill 4\n"), "oom", 1),
    (lambda mi, vm, ps: mi.unlink(), "blind", 3),
])
def test_brake_latches_by_reason_and_stays(tmp_path, change, reason, need):
    clock = FakeMono(100.0)
    b, mi, vm, ps, _ = make(tmp_path, clock)
    b.check()
    assert (b.halted, b.json(clock())["state"]) == (False, "watching")
    change(mi, vm, ps)
    for i in range(need):
        assert b.halted is False, i                                        # до N-й проверки защёлки нет
        clock.advance(5); b.check()
    body = b.json(clock())
    assert (b.halted, b.event.is_set(), body["reason"], set(body) == BRAKE_KEYS) == (True, True, reason, True)
    files(tmp_path, avail=9000); clock.advance(5); b.check()
    assert b.halted


def test_one_noisy_sample_does_not_latch(tmp_path):                       # m-3/m-4 р3
    clock = FakeMono(100.0)
    b, mi, vm, ps, _ = make(tmp_path, clock)
    b.check()
    ps.write_text(PSI.format(12.0)); clock.advance(5); b.check()
    ps.write_text(PSI.format(1.0)); clock.advance(5); b.check()
    ps.write_text(PSI.format(12.0)); clock.advance(5); b.check()
    assert b.halted is False                                               # счёт подряд сброшен


def test_streak_resets_on_every_clean_sample(tmp_path):                   # мутация: счёт не сбрасывается
    clock = FakeMono(100.0)
    b, mi, vm, ps, _ = make(tmp_path, clock)
    b.check()
    for full in (12.0, 1.0, 12.0, 1.0, 12.0, 1.0, 12.0, 12.0):           # подряд выше порога — только два раза
        ps.write_text(PSI.format(full)); clock.advance(5); b.check()
    assert b.halted is False
    clock.advance(5); b.check()                                            # третий подряд
    assert (b.halted, b.reason) == (True, "psi")


def test_swap_slow_growth_from_base_latches(tmp_path):                      # B-1 р2: за 10 мин — меньше порога
    clock = FakeMono(100.0)
    b, mi, *_ = make(tmp_path, clock, swap_base=0)
    for minutes, used in ((0, 0), (11, 300), (22, 450), (33, 600)):       # каждые 11 мин +150…300 МиБ
        clock.advance(660 if minutes else 0)
        mi.write_text(MEM.format(5000 * 1024, 4194304 - used * 1024)); b.check()
    assert b.halted is False                                               # одна проверка выше порога — мало
    for _ in range(2):
        clock.advance(5); b.check()
    assert (b.halted, b.reason, b.json(clock())["swap_growth_mib"]) == (True, "swap", 600)


def test_latch_and_bases_survive_restart_same_run(tmp_path):               # m-5 р2
    clock = FakeMono(100.0)
    b, mi, vm, ps, led = make(tmp_path, clock)
    b.check(); b.halt("memory", avail_mib=900)
    again = RunBrake((str(mi), str(vm), str(ps)), (1024, 512, -1, 10.0), meta=led, run_id="d11-test",
                     monotonic=clock, wallclock=lambda: NOW, started_mono=clock())
    assert (again.halted, again.reason) == (True, "memory")                 # перезапуск core защёлку не снял
    fresh = RunBrake((str(mi), str(vm), str(ps)), (1024, 512, -1, 10.0), meta=led, run_id="d11-next",
                     monotonic=clock, wallclock=lambda: NOW, started_mono=clock())
    assert fresh.halted is False                                            # новый id — новая защёлка


def test_own_core_oom_after_restart_counts(tmp_path):                       # база oom_kill — из llm_meta
    clock = FakeMono(100.0)
    b, mi, vm, ps, led = make(tmp_path, clock, oom=3)
    b.check()
    vm.write_text("oom_kill 4\n")                                          # core убит по памяти и поднят
    again = RunBrake((str(mi), str(vm), str(ps)), (1024, 512, -1, 10.0), meta=led, run_id="d11-test",
                     monotonic=clock, wallclock=lambda: NOW, started_mono=clock())
    again.check()
    assert (again.halted, again.reason) == (True, "oom")


def test_swap_base_survives_restart(tmp_path):                             # ревью А2, I-1: база swap — из llm_meta
    clock = FakeMono(100.0)
    b, mi, vm, ps, led = make(tmp_path, clock)
    b.check()                                                              # база swap 0
    mi.write_text(MEM.format(5000 * 1024, 4194304 - 300 * 1024)); clock.advance(3600)
    again = RunBrake((str(mi), str(vm), str(ps)), (1024, 512, -1, 10.0), meta=led, run_id="d11-test",
                     monotonic=clock, wallclock=lambda: NOW, started_mono=clock())   # core перезапущен при swap 300
    again.check(); mi.write_text(MEM.format(5000 * 1024, 4194304 - 600 * 1024))
    for _ in range(3):
        clock.advance(700); again.check()                                  # за 10 мин — меньше порога
    assert (again.halted, again.reason, again.json(clock())["swap_base_mib"]) == (True, "swap", 0)


def test_latch_flushed_before_return(tmp_path):                           # m-1 р3: защёлка на диске сразу
    clock = FakeMono(100.0)
    b, *_ = make(tmp_path, clock)
    b.check(); b.halt("memory", avail_mib=900)
    keys = {k for (k,) in sqlite3.connect(tmp_path / "core.db").execute("SELECT key FROM llm_meta")}
    assert any(k.startswith("brake:d11-test:") for k in keys)


def test_latch_and_bases_on_disk_with_slow_writer(tmp_path):              # мутация: halt без flush
    clock = FakeMono(100.0)
    b, mi, vm, ps, led = make(tmp_path, clock)
    real = led._connect

    def slow(*a, **k):                                                      # писатель (без аргументов) медленный
        if not a and not k:
            time.sleep(0.3)
        return real(*a, **k)

    led._connect = slow
    b.check()                                                               # базы swap и oom_kill
    keys = lambda: {k for (k,) in sqlite3.connect(tmp_path / "core.db").execute("SELECT key FROM llm_meta")}  # noqa: E731
    assert {"brake:d11-test:swap_base_mib", "brake:d11-test:oom_base"} <= keys()
    b.halt("memory", avail_mib=900)
    assert {"brake:d11-test:reason", "brake:d11-test:halted_at"} <= keys()


def test_clear_deletes_keys_without_touching_calls(tmp_path):              # m-1 р3: без Ledger.open
    from pcbk_core.llm.brake import clear_brake
    clock = FakeMono(100.0)
    b, *_, led = make(tmp_path, clock)
    b.check(); b.halt("memory", avail_mib=900)
    spend(led, "student-01", 0.0, close=False); led.flush()                 # идущий вызов работающего core (status open)
    assert clear_brake(str(tmp_path / "core.db"), "d11-test") >= 1
    db = sqlite3.connect(tmp_path / "core.db")
    assert not [k for (k,) in db.execute("SELECT key FROM llm_meta") if k.startswith("brake:d11-test:")]
    assert db.execute("SELECT COUNT(*) FROM llm_calls WHERE status = 'open'").fetchone()[0] == 1   # не «lost»


def test_brake_silent_before_first_check(tmp_path):
    clock = FakeMono(100.0)
    b, *_ = make(tmp_path, clock)
    clock.advance(31)
    assert b.json(clock())["age_s"] == 31.0


def test_brake_settings():
    s = LlmSettings()
    assert (s.LLM_RUN_MEM_FAIL_MIB, s.LLM_RUN_SWAP_GROW_MIB, s.LLM_RUN_SWAP_BASE_MIB, s.LLM_RUN_PSI_FULL_MAX) == \
           (1024, 512, -1, 10.0)
    for name, bad in (("LLM_RUN_MEM_FAIL_MIB", "0"), ("LLM_RUN_SWAP_BASE_MIB", "-2"), ("LLM_RUN_PSI_FULL_MAX", "101")):
        with pytest.raises(ValueError, match=name):
            LlmSettings.from_env({name: bad})


def test_halted_refuses_run_callers_only(tmp_path, fake_or):
    mi, vm, ps = files(tmp_path)
    app, role = make_llm_app(tmp_path, fake_or, **RUN, LLM_RUN_MEMINFO=str(mi), LLM_RUN_VMSTAT=str(vm),
                             LLM_RUN_PRESSURE=str(ps))
    with TestClient(app) as c:
        assert post(c, chat_body()).status_code == 200
        role.brake.halt("memory", avail_mib=900)
        r = post(c, chat_body())
    assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_run_halted")
    assert LLM_MESSAGES["run_halted"] in r.text and REFUSALS["run_halted"] == (402, "pcbk_run_halted")
    assert len(fake_or.requests) == 1 and role.budget.refusal("student-03") is None


def test_refusal_order_budget_then_halted_then_run(tmp_path):
    led = Ledger(str(tmp_path / "core.db")); assert led.open()
    spend(led, "student-01", 10.0)
    run = RunSpec("d11", 5.0, frozenset({"student-01", "student-02"}))
    halted = Budget(led, total_usd=1000.0, share_usd=100.0, ops_share_usd=100.0, run=run, halted=lambda: True)
    assert [halted.refusal(c) for c in ("student-01", "student-03")] == ["run_halted", None]
    assert Budget(led, total_usd=5.0, share_usd=100.0, ops_share_usd=100.0, run=run,
                  halted=lambda: True).refusal("student-01") == "budget_spent"
    led.close()


def test_health_has_run_brake_only_with_run(tmp_path, fake_or):
    mi, vm, ps = files(tmp_path)
    app, _ = make_llm_app(tmp_path, fake_or, **RUN, LLM_RUN_MEMINFO=str(mi), LLM_RUN_VMSTAT=str(vm), LLM_RUN_PRESSURE=str(ps))
    with TestClient(app) as c:
        body = c.get("/health/llm").json()
    assert len(body) == 32 and set(body["run_brake"]) == BRAKE_KEYS
    plain, _ = make_llm_app(tmp_path / "p", fake_or)
    with TestClient(plain) as c:
        assert c.get("/health/llm").json()["run_brake"] is None


# --- сверх брифа: молчание датчика (решение контроллера Д11а), цикл роли, команда clear ---

@pytest.mark.parametrize("change", [
    lambda mi, vm, ps: ps.unlink(),                                         # давление читалось и пропало
    lambda mi, vm, ps: ps.write_text("some avg10=1.00 avg60=0 avg300=0 total=1\n"),   # без строки full
    lambda mi, vm, ps: mi.write_text("MemTotal: 16384000 kB\nMemAvailable: 5120000 kB\n"),   # swap пропал
    lambda mi, vm, ps: vm.write_text("oom_kill x\n"),                       # vmstat не по формату
    lambda mi, vm, ps: vm.write_text(""),                                   # vmstat пуст
])
def test_sensor_lost_on_the_fly_is_blind(tmp_path, change):
    clock = FakeMono(100.0)
    b, mi, vm, ps, _ = make(tmp_path, clock)
    b.check()
    change(mi, vm, ps)
    for _ in range(BRAKE_HOLD_CHECKS - 1):
        clock.advance(5); b.check()
    assert b.halted is False                                                # одна-две проверки — мало
    clock.advance(5); b.check()
    assert (b.halted, b.reason) == (True, "blind")


def test_optional_sensor_never_read_is_not_a_reason(tmp_path):
    clock = FakeMono(100.0)
    mi, vm, ps = files(tmp_path)
    ps.unlink()                                                             # давления нет с самого начала
    mi.write_text("MemTotal: 16384000 kB\nMemAvailable: 5120000 kB\n")      # и swap нет
    led = Ledger(str(tmp_path / "core.db")); assert led.open()
    b = RunBrake((str(mi), str(vm), str(ps)), (1024, 512, -1, 10.0), meta=led, run_id="d11-test",
                 monotonic=clock, wallclock=lambda: NOW, started_mono=clock())
    for _ in range(5):
        b.check(); clock.advance(5)
    body = b.json(clock())
    assert b.halted is False and (body["psi_full10"], body["swap_growth_mib"], body["swap_base_mib"]) == (None, None, None)
    ps.write_text(PSI.format(0.5))                                          # появилось — с этого раза датчик «читался»
    b.check(); ps.unlink()
    for _ in range(BRAKE_HOLD_CHECKS):
        clock.advance(5); b.check()
    assert (b.halted, b.reason) == (True, "blind")


def test_required_vmstat_never_read_is_blind(tmp_path):                    # R117: vmstat у core обязателен
    clock = FakeMono(100.0)
    mi, vm, ps = files(tmp_path)
    vm.unlink()
    led = Ledger(str(tmp_path / "core.db")); assert led.open()
    b = RunBrake((str(mi), str(vm), str(ps)), (1024, 512, -1, 10.0), meta=led, run_id="d11-test",
                 monotonic=clock, wallclock=lambda: NOW, started_mono=clock())
    for _ in range(BRAKE_HOLD_CHECKS):
        b.check(); clock.advance(5)
    assert (b.halted, b.reason, b.json(clock())["oom_kills"]) == (True, "blind", None)


def test_oom_counter_reset_is_new_base(tmp_path):
    clock = FakeMono(100.0)
    b, mi, vm, ps, led = make(tmp_path, clock, oom=10)
    b.check()
    vm.write_text("oom_kill 2\n"); clock.advance(5); b.check()               # счётчик другого ядра — новая база
    assert b.halted is False and led.meta_get("brake:d11-test:oom_base") == 2
    vm.write_text("oom_kill 3\n"); clock.advance(5); b.check()
    assert (b.halted, b.reason, b.json(clock())["oom_kills"]) == (True, "oom", 1)


def test_window_growth_latches_swap(tmp_path):                              # рост за 10 мин при высокой базе
    clock = FakeMono(100.0)
    b, mi, *_ = make(tmp_path, clock, swap_base=2000)                       # база выше занятого — от базы роста нет
    for used in (0, 300, 600, 600, 600):
        mi.write_text(MEM.format(5000 * 1024, 4194304 - used * 1024)); b.check(); clock.advance(60)
    assert (b.halted, b.reason) == (True, "swap")


def test_json_contract_and_halted_at(tmp_path):
    clock = FakeMono(100.0)
    b, *_ = make(tmp_path, clock, swap_used=100)
    b.check(); clock.advance(2.04)
    body = b.json(clock())
    assert body == {"state": "watching", "reason": None, "avail_mib": 5000, "total_mib": 16000, "fail_mib": 1024,
                    "swap_base_mib": 100, "swap_growth_mib": 0, "swap_grow_mib": 512, "psi_full10": 0.5,
                    "psi_full_max": 10.0, "oom_kills": 0, "age_s": 2.0, "halted_at": None}
    b.halt("psi", psi_full10=12.0)
    body = b.json(clock())
    assert (body["state"], body["reason"], body["psi_full10"], body["halted_at"]) == \
           ("halted", "psi", 12.0, NOW.isoformat())
    b.halt("memory")                                                        # уже стоит — первая причина остаётся
    assert b.reason == "psi"
    with pytest.raises(ValueError):
        b.halt("dify")
    assert BRAKE_REASONS == ("memory", "swap", "psi", "oom", "blind")


def test_meta_reason_off_contract_latches_blind(tmp_path):                 # при сомнении — закрыто
    clock = FakeMono(100.0)
    led = Ledger(str(tmp_path / "core.db")); assert led.open()
    led.meta_set("brake:d11-test:reason", 42.0)
    mi, vm, ps = files(tmp_path)
    b = RunBrake((str(mi), str(vm), str(ps)), (1024, 512, -1, 10.0), meta=led, run_id="d11-test",
                 monotonic=clock, wallclock=lambda: NOW, started_mono=clock())
    assert (b.halted, b.reason, b.json(clock())["halted_at"]) == (True, "blind", NOW.isoformat())


def test_clear_in_process_and_failed_check(tmp_path):
    clock = FakeMono(100.0)
    b, *_ = make(tmp_path, clock)
    b.halt("oom")
    b.clear()
    assert (b.halted, b.event.is_set()) == (False, False)
    for _ in range(BRAKE_HOLD_CHECKS):                                      # исключение проверки — проверка без данных
        b.failed(); clock.advance(5)
    assert (b.halted, b.reason) == (True, "blind")


def test_no_brake_without_run(tmp_path, fake_or):                           # без LLM_RUN_* тормоза нет вовсе
    role = make_llm_role(tmp_path, fake_or)
    assert role.brake is None and role.run_halted() is False and role.health_json(NOW)["run_brake"] is None


def test_brake_loop_latches_under_lifespan(tmp_path, fake_or, monkeypatch):
    monkeypatch.setattr(brake_module, "RUN_BRAKE_POLL_S", 0.01)
    mi, vm, ps = files(tmp_path, avail=500)                                 # свободно 500 < 1024
    app, role = make_llm_app(tmp_path, fake_or, **RUN, LLM_RUN_MEMINFO=str(mi), LLM_RUN_VMSTAT=str(vm),
                             LLM_RUN_PRESSURE=str(ps))
    with TestClient(app) as c:
        wait_until(lambda: role.brake.halted)
        r = post(c, chat_body())
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 200    # ops вне прогона — без отказа
        body = c.get("/health/llm").json()["run_brake"]
    assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_run_halted")
    assert (body["state"], body["reason"], body["avail_mib"]) == ("halted", "memory", 500)
    row = role.ledger.recent(2)[1]
    assert (row.status, row.code, row.http_status, row.caller) == ("refused", "pcbk_run_halted", 402, "student-01")
    app, role = make_llm_app(tmp_path, fake_or, **RUN, LLM_RUN_MEMINFO=str(mi), LLM_RUN_VMSTAT=str(vm),
                             LLM_RUN_PRESSURE=str(ps))
    files(tmp_path, avail=9000)
    with TestClient(app) as c:                                              # перезапуск core: защёлка из llm_meta
        assert role.brake.halted and post(c, chat_body()).status_code == 402


def test_brake_loop_exception_is_logged_and_blind(tmp_path, fake_or, monkeypatch, gwlog):
    monkeypatch.setattr(brake_module, "RUN_BRAKE_POLL_S", 0.01)
    mi, vm, ps = files(tmp_path)
    app, role = make_llm_app(tmp_path, fake_or, **RUN, LLM_RUN_MEMINFO=str(mi), LLM_RUN_VMSTAT=str(vm),
                             LLM_RUN_PRESSURE=str(ps))
    monkeypatch.setattr(role.brake, "check", lambda: 1 / 0)
    with TestClient(app):
        wait_until(lambda: role.brake.halted)
    assert role.brake.reason == "blind"
    assert "тормоз прогона: проверка упала (ZeroDivisionError)" in gwlog.text
    assert "llm brake halted run=d11-test reason=blind" in gwlog.text


def test_brake_loop_not_started_with_config_error(tmp_path, fake_or, monkeypatch):
    monkeypatch.setattr(brake_module, "RUN_BRAKE_POLL_S", 0.01)
    mi, vm, ps = files(tmp_path, avail=500)
    app, role = make_llm_app(tmp_path, fake_or, **RUN, LLM_RUN_MEMINFO=str(mi), LLM_RUN_VMSTAT=str(vm),
                             LLM_RUN_PRESSURE=str(ps))
    role.config_error = "проба"
    with TestClient(app) as c:
        time.sleep(0.3)                                                     # десятки тактов цикла, будь он запущен
        assert c.get("/health/llm").json()["run_brake"]["age_s"] >= 0.3     # проверок не было: молчит
    assert role.brake.halted is False and role.brake.json(role.monotonic())["avail_mib"] is None


def _clear(db: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DB_PATH": db, "PYTHONWARNINGS": "always"}
    return subprocess.run([sys.executable, "-m", "pcbk_core.llm.brake", "clear", *args], cwd=CORE_DIR, env=env,
                          capture_output=True, text=True, timeout=60)


def test_clear_command(tmp_path):                                           # команда администратора
    clock = FakeMono(100.0)
    b, *_, led = make(tmp_path, clock)
    b.check(); b.halt("memory", avail_mib=900); led.close()
    db = str(tmp_path / "core.db")
    refused = _clear(db, "--run", "d11-test")
    assert refused.returncode == 1 and "--yes" in refused.stderr
    bad = _clear(db, "--run", "D11_%", "--yes")
    assert bad.returncode == 1 and "--run" in bad.stderr
    done = _clear(db, "--run", "d11-test", "--yes")
    assert done.returncode == 0 and int(done.stdout) == 4                   # причина, время, базы swap и oom_kill
    assert "llm brake cleared run=d11-test keys=4" in done.stderr and "RuntimeWarning" not in done.stderr
    keys = [k for (k,) in sqlite3.connect(db).execute("SELECT key FROM llm_meta")]
    assert not [k for k in keys if k.startswith("brake:")]
    missing = _clear(str(tmp_path / "нет" / "core.db"), "--run", "d11-test", "--yes")
    assert missing.returncode == 1 and "core.db не открывается" in missing.stderr


# --- итоговая доводка Д11а (ревью А2, m-3 и m-4) ---

@pytest.mark.parametrize("edge,over", [                                     # B17–B19: граница — не причина / причина
    (lambda mi, vm, ps: mi.write_text(MEM.format(1024 * 1024, 4194304)),
     lambda mi, vm, ps: mi.write_text(MEM.format(1023 * 1024, 4194304))),            # свободно ровно порог — норма
    (lambda mi, vm, ps: ps.write_text(PSI.format("10.00")),
     lambda mi, vm, ps: ps.write_text(PSI.format("10.01"))),                         # давление ровно порог — норма
    (lambda mi, vm, ps: mi.write_text(MEM.format(5000 * 1024, 4194304 - 511 * 1024)),
     lambda mi, vm, ps: mi.write_text(MEM.format(5000 * 1024, 4194304 - 512 * 1024))),   # swap: ровно порог — причина
], ids=["memory", "psi", "swap"])
def test_thresholds_exact_edges(tmp_path, edge, over):
    clock = FakeMono(100.0)
    b, mi, vm, ps, _ = make(tmp_path, clock)
    b.check()                                                               # база swap 0
    edge(mi, vm, ps)
    for _ in range(BRAKE_HOLD_CHECKS + 1):
        clock.advance(5); b.check()
    assert b.halted is False
    over(mi, vm, ps)
    for _ in range(BRAKE_HOLD_CHECKS):
        clock.advance(5); b.check()
    assert b.halted is True


def test_age_counts_from_last_check_not_start(tmp_path):                   # B20: брат раньше первой проверки
    clock = FakeMono(100.0)
    b, *_ = make(tmp_path, clock)
    clock.advance(20); b.check(); clock.advance(3)
    assert b.json(clock())["age_s"] == 3.0


def test_swap_window_forgets_old_samples(tmp_path):                         # B23: окно 10 мин чистится
    clock = FakeMono(100.0)
    b, mi, *_ = make(tmp_path, clock, swap_base=2000)                       # база выше занятого — от базы роста нет
    b.check()                                                               # swap 0
    mi.write_text(MEM.format(5000 * 1024, 4194304 - 600 * 1024)); clock.advance(660)
    for _ in range(BRAKE_HOLD_CHECKS):                                      # +600 МиБ, но за 11 мин
        b.check(); clock.advance(5)
    assert b.halted is False


def test_run_with_ledger_not_opened_latches_blind(tmp_path, fake_or):      # m-3: мету прогона не прочесть
    llm = replace(LLM_SETTINGS, OPENROUTER_BASE_URL=fake_or.url, **BRAKE_PROC, **RUN)
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "нет" / "core.db"))  # каталога нет — журнал не откроется
    role = LlmRole(settings, llm, tokens=LLM_TOKENS, upstream=None, ledger=Ledger(settings.DB_PATH))
    with TestClient(create_app(settings, [role])):
        assert role.config_error.startswith("журнал вызовов не открылся")
        assert (role.brake.halted, role.brake.reason, role.run_halted()) == (True, "blind", True)
