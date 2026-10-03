# bench/tests/test_load.py — Д11-R119…R122, R131
"""Режим нагрузки прогонщика (Д11б, Б1): сторож памяти и вердикт по числам строки «Память сервера», жёсткие и мягкая
остановки, prepare() под остановкой, режим данных, «только своё», цена реплики, load-check, load-gate.

Тесты брифа Б1 — дословно, кроме test_slow_swap_across_stages_stops: задержка хода FakeChat — настоящие секунды, и
ступени «закончен» шли бы весь план по 60 с на ход (полчаса); там ход без задержки — то же утверждение (три ступени,
одна база окна). Строки test_guard_verdict с пометками «нет данных» и «датчик замолчал», разбор note на
настоящих строках сторожа и отказ load-gate без данных датчика на старте — по решениям контроллера (03.10).
"""
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import fakes
from fakes import FakeChat, FakeClock, FakeStand, M1, health_brake, runner, write_config
from pcbk_bench.__main__ import LOAD_LOGIN, main
from pcbk_bench.config import load_config
from pcbk_bench.load import (GATE_LIMIT_5M, LOAD_CHECK_PY, MEM_NOTE_RE, MEM_OOM_OUTSIDE_DETAIL, MEM_PSI_DETAIL,
                             MEM_SWAP_DETAIL, MemPoint, check_verdict, concurrency_minutes, gate_verdict,
                             guard_verdict, mem_point, own_only, price_per_reply)
from pcbk_bench.runner import STOP_REASONS, RunRefused, status_main, turn_code

REPO = Path(__file__).resolve().parents[2]
OPENCODE = REPO / "student/config/opencode.json"
BASE = {"swap_base_mib": 100, "swap_grow_mib": 512, "dify_swap_base_mib": 50, "dify_swap_max_mib": 256,
        "mem_fail_mib": 1024, "psi_full_max": 10.0}
BAND = "свободно меньше 2048 МиБ"                                           # мягкий порог сторожа
TWIN = {"load": True, "load_data": "twin", **BASE}


@pytest.fixture(autouse=True)
def _close_runners(tmp_path):
    fakes.OUT_BASE[0] = tmp_path / "outs"
    yield
    fakes.close_runners()
    fakes.OUT_BASE[0] = None


def status(state="ok", detail=None, note="", stale=False, historian="", checked_at="2026-10-12T09:00:00+00:00"):
    mem = {"component": "memory", "state": state, "note": note,
           "detail": detail or ("свободно 8000 МиБ" if state == "ok" else "свободно меньше 1024 МиБ")}
    return {"stale": stale, "checked_at": checked_at,
            "checks": [{"component": "llm", "state": "ok"}, mem,
                       {"component": "historian", "state": "ok", "note": historian}]}


def swap_status(used, full=0.5):
    return status(note=f"swap занят {used} МиБ (+0 МиБ за 10 мин) · давление памяти some 1 %, full {full} %")


def note(free=1500, used=100, grow=0, full=0.5, kills=0):                   # формат R114
    tail = f" · убийств по памяти вне мест и core за час: {kills}" if kills else ""
    return (f"свободно {free} МиБ · swap занят {used} МиБ (+{grow} МиБ за 10 мин) · "
            f"давление памяти some 1 %, full {full} %{tail}")


def health(brake=None):
    return {"run": {"id": "d11-test"}, "run_brake": brake}


OK_BRAKE = {"state": "watching", "age_s": 2}
# пометки датчиков без данных и «датчик замолчал» (watchdog memory.py, доводка А1 b279e4d) — по решению контроллера
NO_PSI = "swap занят 100 МиБ (+0 МиБ за 10 мин) · нет данных: давление"
NO_SWAP = "нет данных: swap · давление памяти some 1 %, full 0.5 %"
NO_VMSTAT = "swap занят 100 МиБ (+0 МиБ за 10 мин) · давление памяти some 1 %, full 0.5 % · нет данных: vmstat"
SILENT = "датчик памяти замолчал — убийства, давление или swap не читаются"


def test_texts_match_watchdog():
    src = (REPO / "watchdog/pcbk_watchdog/memory.py").read_text(encoding="utf-8")
    assert all(f'"{t}"' in src for t in (MEM_SWAP_DETAIL, MEM_OOM_OUTSIDE_DETAIL, MEM_PSI_DETAIL))


def test_load_login_is_probe():                                             # B-1 р1
    src = (REPO / "core/pcbk_core/gateway/accounts.py").read_text(encoding="utf-8")
    probe = re.compile(re.search(r'^PROBE_LOGIN = re\.compile\(r"(.+)"\)$', src, re.M).group(1))
    login = re.compile(re.search(r'^LOGIN_RE = re\.compile\(r"(.+)"\)$', src, re.M).group(1))
    for n in (1, 10):
        assert probe.fullmatch(LOAD_LOGIN.format(n=n)) and login.fullmatch(LOAD_LOGIN.format(n=n))


@pytest.mark.parametrize("st,brake,dify,last,now,want", [
    (status(), OK_BRAKE, 50, 0, 5, "ok"),
    (status("warn", "свободно меньше 2048 МиБ", "свободно 1900 МиБ"), OK_BRAKE, 50, 0, 5, "soft"),
    (status("warn", MEM_SWAP_DETAIL), OK_BRAKE, 50, 0, 5, "memory"),
    (status("warn", MEM_OOM_OUTSIDE_DETAIL), OK_BRAKE, 50, 0, 5, "memory"),
    (status("warn", MEM_PSI_DETAIL), OK_BRAKE, 50, 0, 5, "memory"),                   # B-1 р2
    (swap_status(612), OK_BRAKE, 50, 0, 5, "memory"),                                  # от базы окна: 612 − 100 ≥ 512
    (swap_status(611), OK_BRAKE, 50, 0, 5, "ok"),
    (status(), OK_BRAKE, 306, 0, 5, "memory"),                                         # swap Dify: 306 − 50 ≥ 256
    (status("fail", note="свободно 900 МиБ"), OK_BRAKE, 50, 0, 5, "memory"),
    (status("unknown", "нет данных о свободной памяти"), OK_BRAKE, 50, 0, 5, "memory"),
    (status("warn", BAND, note(kills=1)), OK_BRAKE, 50, 0, 5, "memory"),             # I-1 р3: порог + убийство вне мест
    (status("warn", BAND, note(grow=600)), OK_BRAKE, 50, 0, 5, "memory"),             # порог + swap за 10 мин
    (status("warn", BAND, note(used=612)), OK_BRAKE, 50, 0, 5, "memory"),             # порог + swap от базы окна
    (status("warn", BAND, note(free=1000)), OK_BRAKE, 50, 0, 5, "memory"),            # число ниже mem_fail при «внимании»
    (status("warn", BAND, note(full=12.5)), OK_BRAKE, 50, 0, 5, "soft"),              # давление в одном снимке — ещё шум
    (status("warn", BAND, note()), OK_BRAKE, 50, 0, 5, "soft"),
    (status(), {"state": "halted", "age_s": 2}, 50, 0, 5, "memory"),
    (status(), {"state": "watching", "age_s": 31}, 50, 0, 5, "brake"),
    (status(), None, 50, 0, 5, "brake"),
    (None, OK_BRAKE, 50, 0, 29, "ok"),
    (None, OK_BRAKE, 50, 0, 31, "blind"),
    (status(stale=True), OK_BRAKE, 50, 0, 31, "blind"),
    # решение контроллера 03.10 (I-2 итогового ревью Д11а): датчик жёсткого условия без данных посреди ступени —
    # слепота, жёстко; ни «0 убийств», ни «давление в норме»
    (status(note=NO_PSI), OK_BRAKE, 50, 0, 5, "blind"),                                # давление пропало
    (status(note=NO_SWAP), OK_BRAKE, 50, 0, 5, "blind"),
    (status(note=NO_VMSTAT), OK_BRAKE, 50, 0, 5, "blind"),                             # убийства не читаются — не 0
    (status("warn", BAND, "свободно 1500 МиБ · " + NO_PSI), OK_BRAKE, 50, 0, 5, "blind"),   # и в полосе порога
    (status("unknown", SILENT, "свободно 3000 МиБ · " + NO_VMSTAT), OK_BRAKE, 50, 0, 5, "blind"),
    (status("unknown", SILENT, "свободно 900 МиБ · " + NO_VMSTAT), OK_BRAKE, 50, 0, 5, "memory"),   # числа — первыми
])
def test_guard_verdict(st, brake, dify, last, now, want):
    cfg = type("Cfg", (), BASE)
    assert guard_verdict([mem_point(st, health(brake), dify, now)], cfg, last, now) == want


def test_threshold_plus_pressure_is_hard_after_three_snapshots():           # I-1 р3, m-3 р3
    cfg = type("Cfg", (), BASE)
    pts = [mem_point(status("warn", BAND, note(full=12.5), checked_at=f"2026-10-12T09:00:{k:02d}+00:00"),
                     health(OK_BRAKE), 50, float(k)) for k in (0, 10, 20)]
    assert guard_verdict(pts[:2], cfg, 0, 20) == "soft"
    assert guard_verdict(pts[:1] * 3, cfg, 0, 20) == "soft"                # один снимок, прочитанный трижды
    assert guard_verdict(pts, cfg, 0, 20) == "memory"


def test_mem_note_regex():
    assert MEM_NOTE_RE.search("свободно 900 МиБ · swap занят 300 МиБ").group(1) == "900"
    p = mem_point(status("fail", note="свободно 900 МиБ · swap занят 0 МиБ (+0 МиБ за 10 мин)"), health(OK_BRAKE), 50, 10.0)
    assert (p.state, p.free_mib, p.swap_used_mib, p.kind, p.checked_at) == ("fail", 900, 0, "threshold",
                                                                            "2026-10-12T09:00:00+00:00")
    q = mem_point(status("warn", BAND, note(free=1500, used=300, grow=40, full=12.5, kills=2)), health(OK_BRAKE), 50, 0.0)
    assert (q.free_mib, q.swap_used_mib, q.swap_grow10_mib, q.psi_full, q.oom_outside) == (1500, 300, 40, 12.5, 2)
    assert mem_point(status("warn", BAND, note()), health(OK_BRAKE), 50, 0.0).oom_outside == 0


def test_load_config_rules(tmp_path):
    ok = load_config(write_config(tmp_path, **TWIN, places={str(n): M1 for n in range(1, 11)}), OPENCODE)
    assert (ok.load, ok.load_data, ok.swap_base_mib) == (True, "twin", 100)
    with pytest.raises(ValueError, match="одна модель на двух местах"):
        load_config(write_config(tmp_path, places={"7": M1, "8": M1}), OPENCODE)
    with pytest.raises(ValueError, match="load_data"):
        load_config(write_config(tmp_path, load=True), OPENCODE)
    with pytest.raises(ValueError, match="агент bench"):
        load_config(write_config(tmp_path, load=True, load_data="none", scenarios=["A1", "B4"]), OPENCODE)


def test_load_data_none_needs_drill_on_page(tmp_path):                      # B-6 р1
    stand = FakeStand(health=health_brake(), historian_note="")
    with pytest.raises(RunRefused) as e:
        runner(out=tmp_path / "a", places={1: M1}, stand=stand, **{**TWIN, "load_data": "none"},
               scenarios=("A1", "B3", "C2")).prepare()
    assert e.value.code == "data_drill"
    stand = FakeStand(health=health_brake(), historian_note="учения: tool_error")
    runner(out=tmp_path / "b", places={1: M1}, stand=stand, **{**TWIN, "load_data": "none"},
           scenarios=("A1", "B3", "C2")).prepare()
    assert stand.bench_saves == []


def test_load_data_live_needs_consent_and_no_drill(tmp_path):
    stand = FakeStand(health=health_brake(), historian_note="")
    with pytest.raises(RunRefused) as e:
        runner(out=tmp_path / "c", places={1: M1}, stand=stand, **{**TWIN, "load_data": "live"}).prepare()
    assert e.value.code == "consent"
    (tmp_path / "d").mkdir(); (tmp_path / "d" / "real-data-consent.txt").write_text("да")
    stand = FakeStand(health=health_brake(), historian_note="учения: tool_error")
    with pytest.raises(RunRefused) as e:
        runner(out=tmp_path / "d", places={1: M1}, stand=stand, **{**TWIN, "load_data": "live"}).prepare()
    assert e.value.code == "data_drill"


def test_cli_refuses_twin_on_server(tmp_path):
    cfg = write_config(tmp_path, **TWIN, places={"1": M1}, window_start="2026-10-01T10:00:00+05:00")
    assert main(["run", str(cfg)]) == 2


def test_cli_needs_window_base(tmp_path):
    cfg = write_config(tmp_path, load=True, load_data="none", scenarios=["A1", "B3", "C2"], places={"1": M1},
                       window_start="2026-10-01T10:00:00+05:00")
    assert main(["run", str(cfg)]) == 2                                     # RunRefused("window_base") до подключения


def test_stop_reasons_and_halted_code():
    assert {"memory", "blind", "brake", "memory_warn"} <= set(STOP_REASONS)
    assert turn_code("pcbk_run_halted: Прогон остановлен") == "halted"


def test_load_hard_stop_on_memory_fail(tmp_path):
    clock = FakeClock()
    stand = FakeStand(status_seq=[status(), status(), status("fail", note="свободно 900 МиБ")], health=health_brake())
    r = runner(out=tmp_path, places={1: M1, 2: M1}, stand=stand, **TWIN, chat=FakeChat.honest(delay_s={1: 60, 2: 60}),
               clock=clock.mono, now=clock.now, sleep=clock.sleep)
    assert r.run() == "memory" and [1, 2] in stand.slept
    assert json.loads((tmp_path / "progress.json").read_text())["stop_reason"] == "memory"


def test_load_hard_stop_on_swap_warn(tmp_path):
    stand = FakeStand(status_seq=[status(), status("warn", MEM_SWAP_DETAIL)], health=health_brake())
    assert runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN, chat=FakeChat.honest(delay_s={1: 60})).run() == "memory"


def test_slow_swap_across_stages_stops(tmp_path):                           # B-1 р2: за 10 мин — меньше порога
    seq = [[swap_status(100), swap_status(350)], [swap_status(350), swap_status(500)], [swap_status(500), swap_status(620)]]
    reasons = []
    for k, statuses in enumerate(seq, 1):                                   # три ступени, одна база окна
        stand = FakeStand(status_seq=statuses, health=health_brake())
        reasons.append(runner(out=tmp_path / f"s{k}", places={1: M1}, stand=stand, **TWIN,
                              chat=FakeChat.honest()).run())
    assert reasons == ["finished", "finished", "memory"]


def test_threshold_band_with_kills_stops_hard(tmp_path):                    # I-1 р3: прогонщиком целиком
    band = status("warn", BAND, note(kills=1))
    stand = FakeStand(status_seq=[status(), band], health=health_brake())
    assert runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN, chat=FakeChat.honest(delay_s={1: 60})).run() == "memory"


def test_psi_warn_is_hard(tmp_path):
    stand = FakeStand(status_seq=[status(), status("warn", MEM_PSI_DETAIL)], health=health_brake())
    assert runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN, chat=FakeChat.honest(delay_s={1: 60})).run() == "memory"


def test_dify_swap_stops(tmp_path):
    stand = FakeStand(status_seq=[status()], dify_swap_seq=[50, 120, 320], health=health_brake())
    assert runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN, chat=FakeChat.honest(delay_s={1: 120})).run() == "memory"


def test_load_hard_stop_on_core_brake(tmp_path):
    stand = FakeStand(status_seq=[status()], health=health_brake(halt_after_calls=3))
    assert runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN).run() == "memory"


def test_brake_silent_is_hard(tmp_path):
    stand = FakeStand(status_seq=[status()], health=health_brake(age_after_calls=(3, 31.0)))
    assert runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN, chat=FakeChat.honest(delay_s={1: 60})).run() == "brake"


def test_guard_death_is_blind(tmp_path, monkeypatch):
    import pcbk_bench.load as load
    monkeypatch.setattr(load, "mem_point", lambda *a, **k: 1 / 0)
    r = runner(out=tmp_path, places={1: M1}, stand=FakeStand(health=health_brake()), **TWIN,
               chat=FakeChat.honest(delay_s={1: 120}))
    assert r.run() == "blind"


def test_load_blind_stop(tmp_path):
    clock = FakeClock()
    stand = FakeStand(status_seq=[status()], status_fail_after=1, health=health_brake())
    r = runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN, chat=FakeChat.honest(delay_s={1: 120}),
               clock=clock.mono, now=clock.now, sleep=clock.sleep)
    assert r.run() == "blind"


def test_hard_stop_during_prepare_does_not_wake_rest(tmp_path):            # I-6 р2
    stand = FakeStand(status_seq=[status(), status("fail", note="свободно 900 МиБ")], health=health_brake(),
                      fail_after_wakes=1)
    chat = FakeChat.honest()
    r = runner(out=tmp_path, places={1: M1, 2: M1, 3: M1}, stand=stand, chat=chat, **TWIN)
    assert r.run() == "memory"
    assert chat.woke == [1]
    assert sorted({n for batch in stand.slept for n in batch}) == [1, 2, 3]


def test_load_soft_stop_on_threshold_warn_keeps_turns(tmp_path):
    stand = FakeStand(status_seq=[status(), status("warn", "свободно меньше 2048 МиБ", "свободно 1900 МиБ")],
                      health=health_brake())
    assert runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN).run() == "memory_warn" and stand.slept == []


def test_status_main_old_guard_mark_is_silent(tmp_path):
    (tmp_path / "progress.json").write_text(json.dumps({"state": "running", "run_id": "d11", "load": True,
        "updated_at": "2099-01-01T00:00:00+00:00", "guard_at": "2000-01-01T00:00:00+00:00"}))
    assert status_main(tmp_path, 120.0) == 1


def test_load_refuses_without_brake_or_low_memory(tmp_path):
    with pytest.raises(RunRefused) as e:
        runner(out=tmp_path / "a", places={1: M1}, stand=FakeStand(health={"run_brake": None}), **TWIN).prepare()
    assert e.value.code == "brake"
    with pytest.raises(RunRefused) as e:
        runner(out=tmp_path / "b", places={1: M1}, **TWIN,
               stand=FakeStand(status_seq=[status("warn", "свободно меньше 2048 МиБ")], health=health_brake())).prepare()
    assert e.value.code == "memory"


def test_load_places_subset_of_run_callers(tmp_path):
    stand = FakeStand(run={"callers": 10}, run_callers=[f"student-{n:02d}" for n in range(1, 11)], health=health_brake())
    runner(out=tmp_path, places={1: M1, 2: M1}, stand=stand, **TWIN).prepare()


def test_load_refuses_place_with_live_agents_never_archives(tmp_path):
    stand = FakeStand(health=health_brake()); stand.add_agent(1, "chuzhoy-agent")
    with pytest.raises(RunRefused) as e:
        runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN).prepare()
    assert e.value.code == "place_agents" and stand.archived == []


def test_own_only():
    assert own_only(listed=["s1", "s2", "s0"], own={"s1", "s2"}, pre={"s0"}) == []
    assert own_only(listed=["s1", "x9"], own={"s1"}, pre=set()) == ["x9"]


def test_own_only_written_per_place(tmp_path):
    r = runner(out=tmp_path, places={1: M1, 2: M1}, chat=FakeChat.honest(pre_sessions={1: ["old-1"]}),
               stand=FakeStand(health=health_brake()), **TWIN)
    r.run()
    iso = json.loads((tmp_path / "isolation.json").read_text())
    assert {n: (v["foreign_sessions"], v["foreign_agents"]) for n, v in iso.items()} == {"1": (0, 0), "2": (0, 0)}


def test_concurrency_minutes():
    rows = [{"caller": "student-01", "ts": "2026-10-12T09:00:10+00:00"}, {"caller": "student-02", "ts": "2026-10-12T09:00:40+00:00"},
            {"caller": "student-01", "ts": "2026-10-12T09:01:05+00:00"}]
    assert concurrency_minutes(rows, ["student-01", "student-02"]) == 1


def test_price_per_reply():                                                 # I-2 р2
    records = [{"turns": [{}, {}, {}]}, {"turns": [{}]}]
    rows = [{"cost": 0.0012}] * 10
    assert price_per_reply(records, rows) == {"replies": 4, "calls": 10, "usd": pytest.approx(0.012),
                                              "usd_per_reply": pytest.approx(0.003), "calls_per_reply": 2.5}


def test_gate_verdict():
    pts = [MemPoint(0, None, "ok", "other", 8000, 0, 0.5, 50, "watching", 1),
           MemPoint(60, None, "ok", "other", 7000, 0, 0.5, 50, "watching", 1)]
    v = gate_verdict(pts, places=2, next_k=5, warn_mib=2048)
    assert (v.base, v.low, v.per_place, v.projected, v.ok) == (8000, 7000, 500, 5500, True)
    assert (gate_verdict(pts, 2, 12, 2048).ok, gate_verdict(pts, 2, 12, 2048).max_k) == (False, 10)


def test_check_verdict():
    good = {"events": 40, "sql_events": 30, "sent_unlisted": 0, "requested_unlisted": 3, "max_sql_5m": 12,
            "outcomes": {"ok": 35, "refused": 5}}
    before = {"shares": {"spent": 0}, "spent_usd": 10.0, "total_usd": 1000.0}
    end = {"shares": {"spent": 0}, "spent_usd": 12.0, "total_usd": 1000.0, "run": {"id": "d11-test", "cap_usd": 5.0, "spent_usd": 2.0}}
    iso = {"1": {"listed": 3, "foreign_sessions": 0, "foreign_agents": 0}}
    assert check_verdict(good, before, end, iso, overrun_usd=0.5) == []
    assert check_verdict({**good, "sent_unlisted": 1}, before, end, iso, overrun_usd=0.5) == ["имя вне белого списка дошло до SQL"]
    assert check_verdict({**good, "max_sql_5m": 301}, before, end, iso, overrun_usd=0.5) == \
           ["запросов к историану за 5 минут больше предела ворот"]
    assert check_verdict(good, before, {**end, "shares": {"spent": 1}}, iso, overrun_usd=0.5) == ["нагрузка исчерпала долю места"]


def test_gate_limit_matches_core():
    text = (REPO / "core/pcbk_core/data/gate.py").read_text(encoding="utf-8")
    assert re.search(r"^GLOBAL_LIMIT = (\d+)$", text, re.M).group(1) == str(GATE_LIMIT_5M)


def test_cli_prints_no_values(tmp_path, capsys):
    pts = [{"t": 0, "checked_at": None, "state": "ok", "kind": "other", "free_mib": 8000, "swap_used_mib": 0, "psi_full": 0.5,
            "dify_swap_mib": 50, "brake": "watching", "brake_age_s": 1},
           {"t": 60, "checked_at": None, "state": "ok", "kind": "other", "free_mib": 7000, "swap_used_mib": 0, "psi_full": 0.5,
            "dify_swap_mib": 50, "brake": "watching", "brake_age_s": 1}]
    (tmp_path / "memory.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pts), encoding="utf-8")
    (tmp_path / "load.json").write_text(json.dumps({"places": [1, 2]}), encoding="utf-8")
    assert main(["load-gate", str(tmp_path), "--next", "5"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "прогноз на 5 мест: 5500 МиБ — можно" in out
    assert not re.search(r"20FAKE|d11-probe|[0-9a-f]{24}", out)


# --- сверх брифа: решения контроллера 03.10 и пути, которые тесты брифа не различают ---

def _watchdog_memory():
    """watchdog/pcbk_watchdog/memory.py — модуль без зависимостей от остального сторожа: грузится файлом."""
    spec = importlib.util.spec_from_file_location("wd_memory_b1", REPO / "watchdog/pcbk_watchdog/memory.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _wd_status(samples, warn_mib=2048, fail_mib=1024, swap_grow=512, psi_max=10.0):
    """Строка «Память сервера» настоящего memory_verdict сторожа по замерам (avail, swap_used, oom_kill, some, full;
    None — датчик не читается) раз в 10 с; checked_at — время последнего замера."""
    wd = _watchdog_memory()
    history = wd.MemHistory()
    t0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    for i, (avail, swap, oom, some, full) in enumerate(samples):
        now = t0 + timedelta(seconds=10 * i)
        meminfo = (f"MemTotal: {16000 * 1024} kB\nMemAvailable: {avail * 1024} kB\n"
                   + ("" if swap is None else f"SwapTotal: {4096 * 1024} kB\nSwapFree: {(4096 - swap) * 1024} kB\n"))
        vmstat = "" if oom is None else f"nr_free_pages 1\noom_kill {oom}\n"
        pressure = "" if full is None else (
            ("" if some is None else f"some avg10={some:.2f} avg60=0.00 avg300=0.00 total=1\n")
            + f"full avg10={full:.2f} avg60=0.00 avg300=0.00 total=1\n")
        sample = wd.parse_sample(meminfo, vmstat, pressure)
        history.observe(now, sample)
        state, detail, text = wd.memory_verdict(sample, history, now, warn_mib, fail_mib, swap_grow, psi_max)
    return {"stale": False, "checked_at": now.isoformat(),
            "checks": [{"component": "memory", "state": state, "detail": detail, "note": text}]}


@pytest.mark.parametrize("samples,fields,want", [
    ([(8000, 300, 0, 0.5, 0.25)], (8000, 300, 0, 0.25, 0), "ok"),
    ([(1500, 300, 0, 0.5, 0.25)], (1500, 300, 0, 0.25, 0), "soft"),                    # полоса порога
    ([(900, 300, 0, 0.5, 0.25)], (900, 300, 0, 0.25, 0), "memory"),
    ([(8000, 100, 0, 0.5, 0.0), (8000, 700, 0, 0.5, 0.0)], (8000, 700, 600, 0.0, 0), "memory"),   # swap за 10 мин
    ([(8000, 0, 0, 1.0, 50.0)] * 3, (8000, 0, 0, 50.0, 0), "memory"),                    # давление 3 такта
    ([(1500, 0, 0, 1.0, 12.5)], (1500, 0, 0, 12.5, 0), "soft"),                          # давление в одном снимке
    ([(8000, 0, 0, None, 3.5)], (8000, 0, 0, 3.5, 0), "ok"),                             # some без данных — full есть
    ([(8000, 0, 0, 0.0, 0.0), (8000, 0, 1, 0.0, 0.0), (8000, 0, 1, 0.0, 0.0), (8000, 0, 1, 0.0, 0.0),
      (8000, 0, 1, 0.0, 0.0)], (8000, 0, 0, 0.0, 1), "memory"),                         # убийство вне мест
    ([(8000, 300, 0, None, None)], (8000, 300, 0, None, 0), "blind"),                   # давление не читается с начала
    ([(8000, 300, None, 0.5, 0.25)], (8000, 300, 0, 0.25, None), "blind"),              # vmstat — не «0 убийств»
    ([(8000, None, 0, 0.5, 0.25)], (8000, None, None, 0.25, 0), "blind"),
    ([(8000, 300, 0, 0.5, 0.25), (8000, 300, 0, None, None)], (8000, 300, 0, None, 0), "blind"),   # давление пропало
    ([(8000, 300, 0, 0.5, 0.25), (8000, 300, None, 0.5, 0.25)], (8000, 300, 0, 0.25, None), "blind"),
])
def test_note_parse_on_real_watchdog_rows(samples, fields, want):          # решение контроллера: note — настоящий
    st = _wd_status(samples)
    p = mem_point(st, health(OK_BRAKE), 0, 0.0)
    assert (p.free_mib, p.swap_used_mib, p.swap_grow10_mib, p.psi_full, p.oom_outside) == fields
    cfg = type("Cfg", (), {**BASE, "swap_base_mib": 0, "dify_swap_base_mib": 0})
    assert guard_verdict([p] * (3 if want == "memory" and fields[3] == 50.0 else 1), cfg, 0.0, 0.0) == want


def test_real_watchdog_silent_sensor_is_unknown_and_blind():
    st = _wd_status([(8000, 300, 0, 0.5, 0.25), (8000, 300, 0, None, None)])
    row = st["checks"][0]
    assert (row["state"], row["detail"]) == ("unknown", SILENT) and "нет данных: давление" in row["note"]


def test_prepare_refuses_without_hard_sensor_data(tmp_path):                # давление нечитаемо с начала
    for k, text in enumerate((NO_PSI, NO_SWAP, NO_VMSTAT)):
        stand = FakeStand(status_seq=[status(note=text)], health=health_brake())
        with pytest.raises(RunRefused) as e:
            runner(out=tmp_path / f"r{k}", places={1: M1}, stand=stand, **TWIN).prepare()
        assert e.value.code == "memory"


def test_load_gate_refuses_without_start_sensor_data(tmp_path, capsys):     # решение контроллера 03.10
    first = {"t": 0, "checked_at": None, "state": "ok", "kind": "other", "free_mib": 8000, "swap_used_mib": 0,
             "swap_grow10_mib": 0, "psi_full": None, "oom_outside": 0, "dify_swap_mib": 50, "brake": "watching",
             "brake_age_s": 1}
    later = {**first, "t": 60, "free_mib": 7900, "psi_full": 0.5}
    (tmp_path / "memory.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(later) + "\n", encoding="utf-8")
    (tmp_path / "load.json").write_text(json.dumps({"places": [1, 2]}), encoding="utf-8")
    assert main(["load-gate", str(tmp_path), "--next", "5"]) == 1
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("нельзя: на старте ступени нет данных датчиков: давление")
    later["oom_outside"] = None                                             # посреди ступени — дело сторожа, не ворот
    first["psi_full"] = 0.5
    (tmp_path / "memory.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(later) + "\n", encoding="utf-8")
    assert main(["load-gate", str(tmp_path), "--next", "5"]) == 0


def test_load_gate_says_no_with_max(tmp_path, capsys):
    pts = [{"t": 0, "free_mib": 8000}, {"t": 60, "free_mib": 7000}]
    (tmp_path / "memory.jsonl").write_text("".join(json.dumps(p) + "\n" for p in pts), encoding="utf-8")
    (tmp_path / "load.json").write_text(json.dumps({"places": [1, 2]}), encoding="utf-8")
    assert main(["load-gate", str(tmp_path), "--next", "10", "--warn-mib", "3000"]) == 1
    out = capsys.readouterr().out                                           # наибольшее: (8000 − 3512) / 500
    assert "прогноз на 10 мест: 3000 МиБ — нельзя, наибольшее 8" in out


def test_halted_turn_is_hard_memory(tmp_path):                             # pcbk_run_halted в ходе — жёстко memory
    stand = FakeStand(health=health_brake())
    r = runner(out=tmp_path, places={1: M1, 2: M1}, stand=stand, chat=FakeChat.honest(error="pcbk_run_halted"), **TWIN)
    assert r.run() == "memory" and [1, 2] in stand.slept
    statuses = {json.loads(x)["status"] for x in (tmp_path / "runs.jsonl").read_text().splitlines()}
    assert statuses == {"interrupted"}


def test_wake_racing_hard_stop_puts_place_back_to_sleep(tmp_path):         # пробуждение после тормоза — снова сон
    stand = FakeStand(health=health_brake())
    chat = FakeChat.honest()
    r = runner(out=tmp_path, places={1: M1, 2: M1}, stand=stand, chat=chat, **TWIN)
    wake = chat._wake

    def wake_then_halt(place):                                              # тормоз сработал, пока шло пробуждение
        wake(place)
        r._halt_stop("memory")
    chat._wake = wake_then_halt
    assert r.run() == "memory"
    assert chat.woke == [1] and [1] in stand.slept                          # место 1 — отдельной пачкой после wake


def test_close_after_hard_stop_sleeps_all_again(tmp_path):                 # I-6 р2: close() — ещё раз все места
    stand = FakeStand(status_seq=[status(), status("warn", MEM_SWAP_DETAIL)], health=health_brake())
    assert runner(out=tmp_path, places={1: M1, 2: M1}, stand=stand, **TWIN).run() == "memory"
    assert stand.slept.count([1, 2]) == 2


def test_guard_hang_is_blind_by_barrier(tmp_path):                         # поток сторожа завис — главный поток видит
    class HangingStand(FakeStand):
        hang = False

        def __init__(self, **kw):
            super().__init__(**kw)
            self.release = threading.Event()

        def status(self):
            if self.hang and threading.current_thread().name == "сторож-памяти":
                self.release.wait(10)
            return super().status()

        def sleep_places(self, places):
            super().sleep_places(places)
            self.release.set()

    clock = FakeClock()
    stand = HangingStand(health=health_brake())
    chat = FakeChat.honest(delay_s={1: 600}, advance=clock, on_send=lambda place, n: setattr(stand, "hang", True))
    r = runner(out=tmp_path, places={1: M1}, stand=stand, chat=chat, **TWIN, clock=clock.mono, now=clock.now,
               sleep=clock.sleep)
    started = time.monotonic()
    assert r.run() == "blind" and stand.slept
    assert time.monotonic() - started < 8                                   # не ждали release-срок


def test_status_main_fresh_guard_and_progress_keys(tmp_path):
    fresh = datetime.now(timezone.utc).isoformat()
    (tmp_path / "progress.json").write_text(json.dumps({"state": "running", "run_id": "d11", "load": True,
                                                        "updated_at": fresh, "guard_at": fresh}))
    assert status_main(tmp_path, 120.0) == 0
    r = runner(out=tmp_path / "o", places={1: M1}, stand=FakeStand(health=health_brake()), **TWIN)
    assert r.run() == "finished"
    p = json.loads((tmp_path / "o" / "progress.json").read_text())
    assert p["load"] is True and isinstance(p["guard_at"], str)
    load = json.loads((tmp_path / "o" / "load.json").read_text())
    assert (load["places"], load["reason"]) == ([1], "finished")
    assert set(load) == {"places", "started_at", "ended_at", "reason", "free_base_mib", "free_min_mib",
                         "swap_from_base_mib", "dify_swap_from_base_mib", "psi_full_peak"}
    first = json.loads((tmp_path / "o" / "memory.jsonl").read_text().splitlines()[0])
    assert tuple(first) == ("t", "checked_at", "state", "kind", "free_mib", "swap_used_mib", "swap_grow10_mib",
                            "psi_full", "oom_outside", "dify_swap_mib", "brake", "brake_age_s")
    assert json.loads((tmp_path / "o" / "health_start.json").read_text())["run_brake"]["state"] == "watching"


def _schema(path: str, table: str) -> str:
    text = (REPO / path).read_text(encoding="utf-8")
    return re.search(r'"""(CREATE TABLE IF NOT EXISTS ' + table + r' \(.+?\))"""', text, re.S).group(1)


def test_load_check_py_on_core_schema(tmp_path):                           # R120: числа по настоящим схемам core
    db = tmp_path / "core.db"
    c = sqlite3.connect(db)
    c.execute(_schema("core/pcbk_core/data/events.py", "agent_events"))
    c.execute(_schema("core/pcbk_core/llm/ledger.py", "llm_calls"))

    def event(ts, caller, sent, requested, outcome="ok", channel="mcp"):
        c.execute("INSERT INTO agent_events (ts, caller, channel, tool, requested, tags, sent, outcome, summary, cost,"
                  " cache, row_count, duration_ms) VALUES (?, ?, ?, 'tag_now', ?, '[]', ?, ?, '', 0, 'none', 0, 0)",
                  (ts, caller, channel, json.dumps(requested), json.dumps(sent), outcome))
    event("2026-10-12T09:00:10+00:00", "student-01", ["T1"], ["T1"])
    event("2026-10-12T09:00:40+00:00", "student-02", ["BAD"], ["BAD"])
    event("2026-10-12T09:02:00+00:00", "student-01", [], ["X"], outcome="refused")
    event("2026-10-12T09:01:00+00:00", "system", ["T1"], [], channel="system")      # фон — не люди
    event("2026-10-12T09:03:00+00:00", "teacher", ["T2"], ["T2"], channel="web")    # человек вне прогона — в окне ворот
    event("2026-10-12T09:09:00+00:00", "teacher", ["T2"], ["T2"], channel="web")    # через 6 мин: окно 5 мин — 3, не 4
    event("2026-10-12T08:00:00+00:00", "student-01", ["BAD"], ["BAD"])              # до окна

    rows = [("2026-10-12T09:00:10+00:00", "student-01", "ok", "ok", 0.001, 0.001),
            ("2026-10-12T09:00:40+00:00", "student-02", "ok", "ok", None, 0.002),
            ("2026-10-12T09:01:05+00:00", "student-01", "ok", "ok", 0.001, 0.001),
            ("2026-10-12T09:01:30+00:00", "student-02", "refused", "run_halted", None, 0.0),
            ("2026-10-12T09:01:40+00:00", "student-03", "ok", "ok", 0.5, 0.5)]      # не вызывающий ступени
    for ts, caller, st, code, cost, est in rows:
        c.execute("INSERT INTO llm_calls (ts, caller, model, step, status, code, http_status, cost, cost_est,"
                  " cost_source, prompt_tokens, completion_tokens, cached_tokens, cache_write_tokens,"
                  " reasoning_tokens, duration_ms) VALUES (?, ?, 'm', 0, ?, ?, 200, ?, ?, 'body', 0, 0, 0, 0, 0, 0)",
                  (ts, caller, st, code, cost, est))
    c.commit()
    c.close()
    (tmp_path / "whitelist.txt").write_text("# список\nT1\nT2\n", encoding="utf-8")
    callers = ["student-01", "student-02"]
    out = subprocess.run([sys.executable, "-c", LOAD_CHECK_PY, json.dumps(callers), "2026-10-12T08:59:00+00:00",
                          "2026-10-12T09:10:00+00:00", str(db), str(tmp_path / "whitelist.txt")],
                         capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got == {"events": 3, "sql_events": 2, "sent_unlisted": 1, "requested_unlisted": 2, "max_sql_5m": 3,
                   "outcomes": {"ok": 2, "refused": 1}, "llm_calls": 3, "llm_usd": 0.004,
                   "llm_refused": {"run_halted": 1}, "concurrency_minutes": 1}
    as_rows = [{"ts": ts, "caller": caller, "status": st} for ts, caller, st, *_ in rows]
    assert concurrency_minutes(as_rows, callers) == got["concurrency_minutes"]          # одно правило в двух местах
    assert not re.search(r"T1|T2|BAD|student", out)                                    # только числа и коды


class _Ssh:
    def __init__(self, out="", code=0):
        self.out, self.code, self.calls = out, code, []

    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None):
        self.calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, self.code, self.out, "")


def test_server_stand_load_methods(tmp_path):
    from pcbk_bench.stand import ServerStand, StandError
    ssh = _Ssh()
    stand = ServerStand(["ssh", "dev@host"], "http://127.0.0.1:18000", "https://127.0.0.1:28443", "stand:28443",
                        tmp_path, run=ssh)
    stand.sleep_places([1, 10])
    assert ssh.calls[-1][0][2] == "docker stop -t 10 pcbk-student-01 pcbk-student-10"
    ssh.out = f"{1024 * 1024 * 3}\n{1024 * 1024 * 5 + 7}\n"
    assert stand.dify_swap_mib() == 8
    command, timeout = ssh.calls[-1][0][2], ssh.calls[-1][1]
    assert "$2 !~ /^pcbk-/" in command and "memory.swap.current" in command and timeout == 20 and "$(" not in command
    assert command.startswith("bash -o pipefail -c ")                      # сбой docker ps — код не 0 (I-3 р1)
    for out, code in (("max\n", 0), ("", 123), ("", 1), ("", 0)):          # не число; нет файла; docker ps; не видно
        ssh.out, ssh.code = out, code
        with pytest.raises(StandError):
            stand.dify_swap_mib()
    ssh.code = 0
    ssh.out = json.dumps({"events": 1, "sent_unlisted": 0})
    out = tmp_path / "out"
    out.mkdir()
    (out / "runs.jsonl").write_text(json.dumps({"turns": [{}, {}], "llm_rows": [{"cost": 0.002}] * 3}) + "\n")
    got = stand.load_check(["student-01"], "2026-10-12T09:00:00+00:00", "2026-10-12T10:00:00+00:00", out=out)
    command = ssh.calls[-1][0][2]
    assert command.startswith("docker exec -i pcbk-core python -c ") and "mode=ro" in command
    assert '\'["student-01"]\' 2026-10-12T09:00:00+00:00' in command
    assert got["price"]["calls"] == 3 and json.loads((out / "price.json").read_text())["replies"] == 2
    with pytest.raises(StandError):
        stand.load_check(["teacher; rm"], "a", "b")


def test_gateway_sessions_path():
    import http.server
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            raw = json.dumps([{"id": "ses_a", "title": "x"}, "мусор"]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        from pcbk_bench.gateway import GatewayChat
        chat = GatewayChat(f"http://127.0.0.1:{server.server_address[1]}", "stand:18443", timeout=5)
        assert chat.sessions() == [{"id": "ses_a", "title": "x"}]
        assert seen == ["/pcbk/api/oc/experimental/session?roots=true&archived=true"]
    finally:
        server.shutdown()
        server.server_close()


def test_connect_uses_load_logins(tmp_path):
    from pcbk_bench.__main__ import connect
    (tmp_path / "stand.json").write_text(json.dumps({"ssh": ["ssh", "dev@host"], "core_url": "http://127.0.0.1:1",
                                                     "edge_url": "https://127.0.0.1:2", "edge_host": "stand:2"}))
    (tmp_path / "secrets").mkdir()
    for n in (1, 2):
        (tmp_path / "secrets" / f"{LOAD_LOGIN.format(n=n)}.pw").write_text(f"pw-test-{n}\n")
    _, chats = connect(load_config(write_config(tmp_path, **TWIN, places={"1": M1, "2": M1}), OPENCODE))
    assert [chats[n]._login for n in (1, 2)] == ["d11-probe-load-01", "d11-probe-load-02"]


def test_check_verdict_money_iso_and_no_data():
    good = {"sent_unlisted": 3, "max_sql_5m": 999}                          # без данных SQL не судят
    before = {"shares": {"spent": 0}, "spent_usd": 10.0, "total_usd": 1000.0}
    end = {"shares": {"spent": 0}, "spent_usd": 12.0, "total_usd": 1000.0, "run": {"cap_usd": 5.0, "spent_usd": 5.4}}
    iso = {"1": {"listed": 3, "foreign_sessions": 0, "foreign_agents": 0}}
    assert check_verdict(good, before, end, iso, overrun_usd=0.5, data=False) == []
    assert check_verdict(good, before, {**end, "run": {"cap_usd": 5.0, "spent_usd": 5.6}}, iso, overrun_usd=0.5,
                         data=False) == ["расход прогона выше потолка и перерасхода"]
    assert check_verdict(good, before, {**end, "spent_usd": 1000.0}, iso, overrun_usd=0.5, data=False) == \
        ["общий расход дошёл до бюджета"]
    assert check_verdict(good, before, end, {"1": {"listed": 3, "foreign_sessions": 1, "foreign_agents": 0}},
                         overrun_usd=0.5, data=False) == ["на месте чужие разговоры или агенты"]
    assert check_verdict(good, before, end, {"skipped": "x"}, overrun_usd=0.5, data=False) == \
        ["«только своё» не проверено"]


def test_foreign_session_counted(tmp_path):                                 # R121: чужой разговор посреди плана
    chat = FakeChat.honest(pre_sessions={1: ["old-1"]})
    chat.on_send = lambda place, n: chat.pre_sessions[1].append("x9") if n == 1 else None
    r = runner(out=tmp_path, places={1: M1, 2: M1}, chat=chat, stand=FakeStand(health=health_brake()), **TWIN)
    assert r.run() == "finished"
    iso = json.loads((tmp_path / "isolation.json").read_text())
    assert (iso["1"]["foreign_sessions"], iso["2"]["foreign_sessions"]) == (1, 0)
    assert json.loads((tmp_path / "pre_sessions.json").read_text()) == {"1": ["old-1"], "2": []}


class _GuardStand:
    """Стенд сторожа памяти: статус, здоровье и swap Dify — функциями номера опроса."""

    def __init__(self, status_of, health_of=lambda n: health(OK_BRAKE), dify_of=lambda n: 50):
        self.status_of, self.health_of, self.dify_of, self.n = status_of, health_of, dify_of, 0

    def status(self):
        self.n += 1
        return self.status_of(self.n)

    def health_llm(self):
        return self.health_of(self.n)

    def dify_swap_mib(self):
        return self.dify_of(self.n)


def _guard(tmp_path, stand):
    from pcbk_bench.load import MemoryGuard
    clock = FakeClock()
    verdicts = []
    g = MemoryGuard(stand, type("Cfg", (), BASE), tmp_path, clock=clock.mono, now=clock.now, sleep=clock.sleep,
                    on_verdict=verdicts.append)
    return g, clock, verdicts


def test_guard_same_snapshot_goes_blind(tmp_path):                         # свежая — новая страница сторожа
    g, clock, verdicts = _guard(tmp_path, _GuardStand(lambda n: status()))  # checked_at не меняется: сторож встал
    for _ in range(8):                                                      # опросы в t = 0, 5 … 35
        g.check()
        clock.sleep(5.0)
    assert verdicts == ["ok"] * 7 + ["blind"]                              # 35 − 0 > LOAD_BLIND_S
    g2, clock2, verdicts2 = _guard(tmp_path / "x", _GuardStand(
        lambda n: status(checked_at=f"2026-10-12T09:{n:02d}:00+00:00")))
    (tmp_path / "x").mkdir()
    for _ in range(10):
        g2.check()
        clock2.sleep(5.0)
    assert set(verdicts2) == {"ok"}


def test_guard_dify_unreadable_three_times_is_blind(tmp_path):
    def dify(n):
        raise RuntimeError("ssh")
    g, _, verdicts = _guard(tmp_path, _GuardStand(lambda n: status(checked_at=f"t{n}"), dify_of=dify))
    for _ in range(7):                                                      # чтение swap Dify — опросы 1, 4, 7
        g.check()
    assert verdicts == ["ok"] * 6 + ["blind"]


def test_brake_age_counts_on_when_health_unreadable():                     # /health/llm молчит — тормоз стареет
    cfg = type("Cfg", (), BASE)
    seen = mem_point(status(checked_at="a"), health(OK_BRAKE), 50, 0.0)     # возраст 2 с в t = 0
    lost = mem_point(status(checked_at="b"), None, 50, 20.0)
    assert guard_verdict([seen, lost], cfg, 20.0, 20.0) == "ok"
    late = mem_point(status(checked_at="c"), None, 50, 29.0)               # 2 + 29 > 30
    assert guard_verdict([seen, lost, late], cfg, 29.0, 29.0) == "brake"
    assert guard_verdict([lost], cfg, 20.0, 20.0) == "brake"               # не читался ни разу


def test_hard_after_soft_overrides_reason(tmp_path):                        # жёсткая — поверх мягкой
    soft = status("warn", BAND, "свободно 1900 МиБ")
    stand = FakeStand(status_seq=[status(), soft, status("fail", note="свободно 900 МиБ")], health=health_brake())
    r = runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN)
    r.mem_poll_s = 3600.0                                                   # поток сторожа молчит: решают опросы
    assert r.run() == "memory" and stand.slept                              # подготовки и конца плана


def test_end_of_plan_check_catches_brake(tmp_path):                         # «закончен» — после чистого опроса
    stand = FakeStand(status_seq=[status()], health=health_brake(halt_after_calls=40))
    r = runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN)
    r.mem_poll_s = 3600.0
    assert r.run() == "memory" and stand.health_calls > 40
    calm = FakeStand(status_seq=[status()], health=health_brake())
    r = runner(out=tmp_path / "calm", places={1: M1}, stand=calm, **TWIN)
    r.mem_poll_s = 3600.0
    assert r.run() == "finished"


def test_no_keepalive_wake_after_hard_stop(tmp_path):                       # после тормоза ждущие места не будятся
    chat = FakeChat.honest()
    r = runner(out=tmp_path, places={1: M1, 2: M1}, stand=FakeStand(health=health_brake()), chat=chat, **TWIN)
    r._waiting = {2: -1e9}
    r._keepalive()
    assert chat.wakes[2] == 1                                                # контроль: без тормоза — будит
    r._halt.set()
    r._waiting = {2: -1e9}
    r._keepalive()
    assert chat.wakes[2] == 1


def test_load_places_outside_run_callers_refused(tmp_path):
    stand = FakeStand(run={"callers": 10}, run_callers=["student-01"], health=health_brake())
    with pytest.raises(RunRefused) as e:
        runner(out=tmp_path, places={1: M1, 2: M1}, stand=stand, **TWIN).prepare()
    assert e.value.code == "run_callers"


def test_halt_during_late_prepare_is_stop_not_error(tmp_path):              # места усыплены — их отказ не сбой
    class LateStand(FakeStand):
        runner = None

        def place_model(self, place):
            self.runner._halt_stop("memory")                                # тормоз сработал посреди подготовки
            raise RuntimeError("место спит")
    stand = LateStand(health=health_brake())
    r = runner(out=tmp_path, places={1: M1, 2: M1}, stand=stand, **TWIN)
    stand.runner = r
    assert r.run() == "memory" and [1, 2] in stand.slept
    assert not (tmp_path / "runs.jsonl").exists()


def test_cli_load_check(tmp_path, capsys, monkeypatch):                     # R120: только числа, код по нарушениям
    import pcbk_bench.__main__ as cli
    from pcbk_bench.load import write_price
    out = tmp_path / "out"
    out.mkdir()
    cfg = write_config(tmp_path, **TWIN, places={"1": M1, "2": M1}, out_dir=str(out))
    (out / "progress.json").write_text(json.dumps({"started_at": "2026-10-12T09:00:00+00:00",
                                                   "updated_at": "2026-10-12T09:30:00+00:00"}))
    row = {"cost": 0.0012, "session": "ses_" + "a1" * 13, "caller": "student-01", "model": "z-ai/glm-5.3"}
    (out / "runs.jsonl").write_text(json.dumps({"turns": [{"student": "20FAKE_101_PV?"}, {}], "llm_rows": [row] * 4})
                                    + "\n")
    start = {"shares": {"spent": 0}, "spent_usd": 10.0, "total_usd": 1000.0}
    end = {**start, "spent_usd": 10.1, "run": {"id": "d8-test", "cap_usd": 5.0, "spent_usd": 0.1}}
    (out / "health_start.json").write_text(json.dumps(start))
    (out / "health_end.json").write_text(json.dumps(end))
    (out / "isolation.json").write_text(json.dumps({str(n): {"listed": 3, "foreign_sessions": 0, "foreign_agents": 0}
                                                    for n in (1, 2)}))
    seen = []

    class Stand:
        def load_check(self, callers, since, until, out=None):
            seen.append((callers, since, until, out))
            return {"events": 10, "sql_events": 8, "sent_unlisted": 0, "requested_unlisted": 1, "max_sql_5m": 5,
                    "outcomes": {"ok": 9, "refused": 1}, "llm_calls": 4, "llm_usd": 0.0048, "llm_refused": {},
                    "concurrency_minutes": 2, "price": write_price(out)}
    monkeypatch.setattr(cli, "server_stand", lambda c: Stand())
    assert main(["load-check", str(cfg)]) == 0
    text = capsys.readouterr().out
    assert seen == [(["student-01", "student-02"], "2026-10-12T09:00:00+00:00", "2026-10-12T09:30:00+00:00", out)]
    assert "реплик 2, вызовов 4, $0.0048, на реплику $0.0024 (вызовов 2.0)" in text
    assert "минут одновременной работы 2" in text and text.rstrip().endswith("load-check: норма")
    assert not re.search(r"20FAKE|ses_|d11-probe|glm|[0-9a-f]{24}", text)
    (out / "health_end.json").write_text(json.dumps({**end, "run": {**end["run"], "spent_usd": 9.0}}))
    assert main(["load-check", str(cfg)]) == 1
    assert "расход прогона выше потолка и перерасхода" in capsys.readouterr().out



# --- доводка р1 (ревью Б1: I-1…I-4, m-3, m-4; решения контроллера) ---

def test_load_archive_keeps_foreign_agent(tmp_path):                       # I-1: чужой агент — улика, не архив
    stand = FakeStand(health=health_brake())
    chat = FakeChat.honest()
    chat.on_send = lambda place, n: stand.add_agent(place, "chuzhoy") if n == 1 else None
    r = runner(out=tmp_path, places={1: M1}, stand=stand, chat=chat, **TWIN, scenarios=("A1",))
    assert r.run() == "finished"
    assert (1, "chuzhoy") not in stand.archived and any(n.startswith("bench-") for _, n in stand.archived)
    assert json.loads((tmp_path / "isolation.json").read_text())["1"]["foreign_agents"] == 1


def test_selection_archive_unchanged(tmp_path):                             # вне нагрузки — как Д8: весь архив
    stand = FakeStand()
    chat = FakeChat.honest()
    chat.on_send = lambda place, n: stand.add_agent(place, "chuzhoy") if n == 1 else None
    r = runner(out=tmp_path, places={7: M1}, stand=stand, chat=chat, scenarios=("A1",), reps={"create": 1, "number": 1,
                                                                                             "trap": 1})
    r.run()
    assert (7, "chuzhoy") in stand.archived


def test_sleep_before_slow_aborts(tmp_path):                                # I-2: сон мест не ждёт abort через шлюз
    at = {}

    class Stand(FakeStand):
        def sleep_places(self, places):
            at.setdefault("sleep", time.monotonic())
            super().sleep_places(places)

    class SlowAbort:                                                        # шлюз болен: abort по 0,5 с на место
        def __init__(self, inner):
            self.inner = inner

        def abort(self, session):
            at.setdefault("abort", time.monotonic())
            time.sleep(0.5)

        def __getattr__(self, name):
            return getattr(self.inner, name)
    r = runner(out=tmp_path, places={1: M1, 2: M1, 3: M1}, stand=Stand(health=health_brake()), **TWIN)
    r.chats = {n: SlowAbort(c) for n, c in r.chats.items()}
    r._live = {1: "s1", 2: "s2", 3: "s3"}
    t0 = time.monotonic()
    r._halt_stop("memory")
    assert at["sleep"] - t0 < 0.4 and at["sleep"] <= at["abort"]


def _step(tmp_path, psi, reason):
    pts = [{"t": 60 * k, "checked_at": f"s{k}", "state": "ok", "kind": "other", "free_mib": 8000 - k,
            "swap_used_mib": 100, "swap_grow10_mib": 0, "psi_full": v, "oom_outside": 0, "dify_swap_mib": 50,
            "brake": "watching", "brake_age_s": 1} for k, v in enumerate(psi)]
    (tmp_path / "memory.jsonl").write_text("".join(json.dumps(x) + "\n" for x in pts), encoding="utf-8")
    (tmp_path / "load.json").write_text(json.dumps({"places": [1, 2], "reason": reason}), encoding="utf-8")


@pytest.mark.parametrize("reason,code", [("memory", 1), ("blind", 1), ("memory_warn", 1), ("window", 1),
                                         ("finished", 0)])
def test_load_gate_after_stopped_step_says_no(tmp_path, capsys, reason, code):    # I-4: ворота строго
    _step(tmp_path, [0.5, 40.0, 40.0, 40.0, 0.5], reason)                   # давление спало после сна мест
    assert main(["load-gate", str(tmp_path), "--next", "5"]) == code        # память ровная: прогноз сказал бы «можно»
    out = capsys.readouterr().out
    assert out.startswith(f"нельзя: ступень не закончена ({reason})") if code else "— можно" in out


@pytest.mark.parametrize("limits,run,field", [
    ({"swap_base_mib": 0}, {}, "swap_base_mib"),                            # забыт LLM_RUN_SWAP_BASE_MIB
    ({"swap_base_mib": None}, {}, "swap_base_mib"),                         # core ещё не взял базу: не сверить — отказ
    ({"fail_mib": 2048}, {}, "mem_fail_mib"),
    ({"swap_grow_mib": 256}, {}, "swap_grow_mib"),
    ({"psi_full_max": 20.0}, {}, "psi_full_max"),
    ({}, {"cap_usd": 7.5}, "cap_usd"),                                     # потолок core — не потолок ступени
])
def test_run_limits_mismatch_refused(tmp_path, limits, run, field):        # m-3: LLM_RUN_* — как в конфиге ступени
    stand = FakeStand(run=run, health=health_brake(limits=limits))
    r = runner(out=tmp_path, places={1: M1}, stand=stand, **TWIN)
    with pytest.raises(RunRefused) as e:
        r.prepare()
    assert (e.value.code, e.value.detail) == ("run_limits", field) and r.chats[1].fake.woke == []


def test_run_limits_null_config_fields_not_compared(tmp_path):              # конфиг стенда: null — умолчание сторожа
    stand = FakeStand(health=health_brake(limits={"fail_mib": 2048, "psi_full_max": 20.0}))
    runner(out=tmp_path, places={1: M1}, stand=stand, load=True, load_data="twin", swap_base_mib=0).prepare()


def test_cli_prints_refusal_detail(capsys, monkeypatch):
    import pcbk_bench.__main__ as cli

    def refuse(config):
        raise RunRefused("run_limits", "swap_base_mib")
    monkeypatch.setattr(cli, "_run", refuse)
    assert main(["run", "config.json"]) == 2
    assert capsys.readouterr().err.strip() == "RunRefused: run_limits (swap_base_mib)"


DRILL = "учения: tool_error"


@pytest.mark.parametrize("st,data,want", [
    (status(historian=DRILL), "none", "ok"),
    (status(historian=""), "none", "data_drill"),                           # пометка пропала посреди ступени
    (status(historian="последняя метка 20 с назад"), "none", "data_drill"),
    ({**status(), "checks": status()["checks"][:2]}, "none", "data_drill"),   # строки историана нет — пометки нет
    (None, "none", "ok"),                                                   # страницы нет — дело таймера слепоты
    (status("fail", note="свободно 900 МиБ"), "none", "memory"),           # числа памяти — первыми
    (status(historian=""), "twin", "ok"),
    (status(historian=""), "live", "ok"),
])
def test_guard_verdict_drill_note(st, data, want):                         # m-4: пометка учения — каждым опросом
    cfg = type("Cfg", (), {**BASE, "load_data": data})
    assert guard_verdict([mem_point(st, health(OK_BRAKE), 50, 5.0)], cfg, 0, 5) == want


def test_drill_note_lost_mid_step_is_hard(tmp_path):                       # m-4: «без данных» без пометки — жёстко
    drilled, plain = status(historian=DRILL), status(historian="")
    stand = FakeStand(status_seq=[drilled, drilled, plain], health=health_brake())
    chat = FakeChat.honest(delay_s={1: 20})                                 # ход идёт — стоп посреди него
    r = runner(out=tmp_path, places={1: M1}, stand=stand, chat=chat, **{**TWIN, "load_data": "none"},
               scenarios=("B3",), reps={"create": 1, "number": 1, "trap": 1})
    started = time.monotonic()
    assert r.run() == "data_drill" and [1] in stand.slept and time.monotonic() - started < 10
    assert "data_drill" in STOP_REASONS
