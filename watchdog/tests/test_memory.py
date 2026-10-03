# watchdog/tests/test_memory.py — Д11-R114, R115
from datetime import datetime, timedelta, timezone

import pytest

from helpers import insp
from pcbk_watchdog.memory import (MEM_DRILL_NOTE, MEM_OOM_OUTSIDE_DETAIL, MEM_PSI_DETAIL, MEM_SWAP_DETAIL, MemHistory,
                                  RestartReasons, memory_verdict, parse_sample)

T = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
MEMINFO = ("MemTotal:       16384000 kB\nMemAvailable:   {avail} kB\nSwapTotal:       4194304 kB\n"
           "SwapFree:        {swapfree} kB\n")
VMSTAT = "pgfault 1\noom_kill {oom}\npswpout 0\n"
PSI = "some avg10=1.50 avg60=0.80 avg300=0.10 total=123\nfull avg10={full} avg60=0.00 avg300=0.00 total=0\n"


def sample(avail_mib=8000, swap_used_mib=0, oom=0, full=0.4):
    return parse_sample(MEMINFO.format(avail=avail_mib * 1024, swapfree=4194304 - swap_used_mib * 1024),
                        VMSTAT.format(oom=oom), PSI.format(full=full))


def hist():
    return MemHistory(grace_s=20, match_s=30)


def verdict(s, h, at, warn=2048, fail=1024, swap=512, psi=10.0):
    return memory_verdict(s, h, at, warn, fail, swap, psi)


def test_parse_sample_fields():
    s = sample(avail_mib=5123, swap_used_mib=300, oom=7)
    assert (s.avail_mib, s.total_mib, s.swap_used_mib, s.oom_kill, s.psi_some10, s.psi_full10) == \
           (5123, 16000, 300, 7, 1.5, 0.4)


def test_parse_sample_optional_files():
    s = parse_sample(MEMINFO.format(avail=1024 * 1024, swapfree=4194304), "", "")
    assert (s.oom_kill, s.psi_some10, s.psi_full10) == (None, None, None)
    assert parse_sample("MemTotal: 1 kB\n", "", "") is None


@pytest.mark.parametrize("avail,want", [(8000, ("ok", "свободно 8000 МиБ")),
                                        (1500, ("warn", "свободно меньше 2048 МиБ")),
                                        (900, ("fail", "свободно меньше 1024 МиБ"))])
def test_thresholds_keep_d1_texts(avail, want):
    h = hist(); h.observe(T, sample(avail))
    assert verdict(sample(avail), h, T)[:2] == want


def test_note_numbers_in_order():
    h = hist(); h.observe(T, sample(1500, swap_used_mib=300))
    assert verdict(sample(1500, swap_used_mib=300), h, T)[2] == \
           "свободно 1500 МиБ · swap занят 300 МиБ (+0 МиБ за 10 мин) · давление памяти some 1.5 %, full 0.4 %"


def observe_ticks(h, n, **kw):                                             # n тактов по 10 с одним замером
    for i in range(n):
        h.observe(T + timedelta(seconds=10 * i), sample(**kw))
    return T + timedelta(seconds=10 * (n - 1))


def test_psi_full_needs_three_ticks_and_clears_after_three():              # B-1 р2, m-3 р3
    h = hist()
    at = observe_ticks(h, 2, full=12.5)
    assert verdict(sample(full=12.5), h, at)[0] == "ok"                     # два такта — ещё шум
    at = T + timedelta(seconds=20); h.observe(at, sample(full=12.5))
    assert verdict(sample(full=12.5), h, at)[:2] == ("warn", MEM_PSI_DETAIL)
    for i in (3, 4):
        h.observe(T + timedelta(seconds=10 * i), sample(full=1.0))
    assert verdict(sample(full=1.0), h, T + timedelta(seconds=40))[:2] == ("warn", MEM_PSI_DETAIL)   # два ниже — держит
    h.observe(T + timedelta(seconds=50), sample(full=1.0))
    assert verdict(sample(full=1.0), h, T + timedelta(seconds=50))[0] == "ok"


@pytest.mark.parametrize("kw,want", [                                       # I-1 р3: жёсткое — выше мягкого порога
    ({"full": 12.5}, MEM_PSI_DETAIL),
    ({"oom": 1}, MEM_OOM_OUTSIDE_DETAIL),
    ({"swap_used_mib": 700}, MEM_SWAP_DETAIL),
])
def test_hard_conditions_beat_threshold_warn(kw, want):
    h = MemHistory(grace_s=0, match_s=30)
    h.observe(T - timedelta(seconds=30), sample(1500))                      # база: убийств 0, swap 0
    at = observe_ticks(h, 3, avail_mib=1500, **kw)
    assert verdict(sample(1500, **kw), h, at)[:2] == ("warn", want)


def test_swap_growth_in_ten_minutes_warns_then_clears():
    h = hist(); h.observe(T, sample(swap_used_mib=100))
    at = T + timedelta(minutes=5); h.observe(at, sample(swap_used_mib=700))
    state, detail, note = verdict(sample(swap_used_mib=700), h, at)
    assert (state, detail) == ("warn", MEM_SWAP_DETAIL) and "swap занят 700 МиБ (+600 МиБ за 10 мин)" in note
    later = T + timedelta(minutes=16); h.observe(later, sample(swap_used_mib=700))
    assert verdict(sample(swap_used_mib=700), h, later)[0] == "ok"


def test_swap_decrease_is_not_growth():
    h = hist(); h.observe(T, sample(swap_used_mib=900))
    at = T + timedelta(minutes=1); h.observe(at, sample(swap_used_mib=100))
    assert h.swap_growth(at) == 0


def test_first_sample_is_base_not_kills():
    h = hist(); h.observe(T, sample(oom=40))
    assert h.outside(T + timedelta(minutes=5)) == 0


def test_oom_outside_warns_after_grace_for_an_hour():
    h = hist()
    h.observe(T, sample(oom=3)); h.observe(T + timedelta(seconds=10), sample(oom=4))
    assert verdict(sample(oom=4), h, T + timedelta(seconds=10))[0] == "ok"
    at = T + timedelta(seconds=40); h.observe(at, sample(oom=4))
    state, detail, note = verdict(sample(oom=4), h, at)
    assert (state, detail) == ("warn", MEM_OOM_OUTSIDE_DETAIL) and note.endswith("вне мест и core за час: 1")
    gone = T + timedelta(hours=1, seconds=11); h.observe(gone, sample(oom=4))
    assert verdict(sample(oom=4), h, gone)[0] == "ok"


def test_own_container_oom_is_not_outside():
    h = hist()
    h.observe(T, sample(oom=3)); h.observe(T + timedelta(seconds=10), sample(oom=4))
    h.attribute(1, T + timedelta(seconds=20))
    at = T + timedelta(seconds=60); h.observe(at, sample(oom=4))
    assert (h.outside(at), verdict(sample(oom=4), h, at)[0]) == (0, "ok")


def test_attribute_before_observe_waits_for_increment():
    h = hist(); h.observe(T, sample(oom=3))
    h.attribute(1, T + timedelta(seconds=5))
    h.observe(T + timedelta(seconds=10), sample(oom=4))
    assert h.outside(T + timedelta(minutes=2)) == 0


def test_attribute_matches_recent_increment_not_old_one():
    h = hist()
    h.observe(T, sample(oom=0))
    h.observe(T + timedelta(seconds=10), sample(oom=1))
    h.observe(T + timedelta(minutes=50), sample(oom=2))
    h.attribute(1, T + timedelta(minutes=50, seconds=10))
    assert h.outside(T + timedelta(minutes=55)) == 1
    assert h.outside(T + timedelta(minutes=71)) == 0


def test_fail_beats_outside_psi_and_swap():
    h = MemHistory(grace_s=0, match_s=30)
    h.observe(T, sample(900, swap_used_mib=0, oom=1, full=20))
    at = T + timedelta(minutes=1); h.observe(at, sample(900, swap_used_mib=900, oom=2, full=20))
    assert verdict(sample(900, swap_used_mib=900, oom=2, full=20), h, at)[:2] == ("fail", "свободно меньше 1024 МиБ")


def test_threshold_above_total_is_drill():
    h = hist(); h.observe(T, sample(8000))
    state, detail, note = verdict(sample(8000), h, T, warn=1_000_000)
    assert (state, detail, note.endswith(MEM_DRILL_NOTE)) == ("warn", "свободно меньше 1000000 МиБ", True)
    assert MEM_DRILL_NOTE == "учения: порог выше всей памяти"


def test_details_constant_and_short():
    for d in (MEM_OOM_OUTSIDE_DETAIL, MEM_PSI_DETAIL, MEM_SWAP_DETAIL):
        assert len(d) <= 80 and not any(ch.isdigit() for ch in d)


START = T - timedelta(hours=1)
AGAIN = T - timedelta(seconds=5)


def run(r, name, i, before, after):
    return r.observe(name, i, before, after)


def test_restart_reason_oom_crash_unknown():
    r = RestartReasons()
    run(r, "student-01", insp(running=True, started=START), 5, 5)
    assert run(r, "student-01", insp(running=True, restarts=1, started=AGAIN), 6, 6) == ("oom", 1)
    assert r.reason("student-01", AGAIN) == "oom"
    run(r, "student-02", insp(running=True, started=START), 6, 6)
    assert run(r, "student-02", insp(running=True, restarts=1, started=AGAIN), 6, 6) == ("crash", 0)
    assert run(r, "student-03", insp(running=True, restarts=1, started=AGAIN), 6, 7) == (None, 0)


def test_restart_reason_in_restarting_window():                             # база — последний такт не в окне Restarting
    r = RestartReasons()
    run(r, "student-01", insp(running=True, started=START), 5, 5)
    run(r, "student-01", insp(running=True, restarting=True, restarts=1, oom=True, started=START), 6, 6)
    assert run(r, "student-01", insp(running=True, restarts=1, started=AGAIN), 6, 6) == ("oom", 1)


def test_woke_and_crashed_between_ticks_uses_fresh_base():
    r = RestartReasons()
    run(r, "student-04", insp(code=0, started=START), 5, 5)
    run(r, "student-04", insp(code=0, started=START), 9, 9)                 # чужие убийства во сне — в базе
    run(r, "student-04", insp(running=True, started=AGAIN - timedelta(seconds=3)), 9, 9)
    assert run(r, "student-04", insp(running=True, restarts=1, started=AGAIN), 9, 9) == ("crash", 0)


def test_two_kills_one_place_between_ticks():
    r = RestartReasons()
    run(r, "student-05", insp(running=True, started=START), 5, 5)
    assert run(r, "student-05", insp(running=True, restarts=2, started=AGAIN), 7, 7) == ("oom", 2)


def test_wake_by_gateway_is_not_restart():
    r = RestartReasons()
    run(r, "student-04", insp(running=True, restarts=2, started=START), 6, 6)
    assert run(r, "student-04", insp(running=True, restarts=0, started=AGAIN), 7, 7) == (None, 0)


def test_no_counter_no_oom_reason():
    r = RestartReasons()
    run(r, "core-container", insp(running=True, started=START), None, None)
    assert run(r, "core-container", insp(running=True, restarts=1, started=AGAIN), None, None) == (None, 0)
