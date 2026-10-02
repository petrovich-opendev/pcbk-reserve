"""Прогонщик отбора (Д8-R74): пробуждение, барьер, сроки, конец окна, пульс, замок, остановки, журнал сессий,
возобновление, сверка модели; клиент шлюза, разбор хода, стенд сервера, CLI.

FakeChat (ответы по агенту и реплике, задержки, сон места), FakeStand, FakeClock — из fakes.py; runner(...) — фабрика
(out по умолчанию — свой каталог под tmp_path). Автофикстура закрывает все прогонщики теста (П7): пульс и замок не
переживают тест. Потоки и ожидания — со сроками; набор — с faulthandler_timeout.
"""
import json
import signal
import subprocess
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import fakes
from fakes import (M1, M2, M3, M4, T0, FakeChat, FakeClock, FakeStand, fake_stand_raising, iso, now, runner,
                   write_config)
from pcbk_bench.__main__ import main
from pcbk_bench.gateway import GatewayChat, GatewayError, WorkplaceNotReady
from pcbk_bench.record import RunKey, RunRecord, parse_turn
from pcbk_bench.runner import RunRefused, load_records, plan_runs, status_main, turn_code, write_progress
from pcbk_bench.scenarios import BY_ID, RUN_TIMEOUT_S
from pcbk_bench.stand import ServerStand, StandError, mcp_connected

@pytest.fixture(autouse=True)
def _close_runners(tmp_path):
    fakes.OUT_BASE[0] = tmp_path / "outs"
    yield
    fakes.close_runners()
    fakes.OUT_BASE[0] = None


def cfg(**kw):
    return fakes.cfg(**kw)


def _join(t: threading.Thread, timeout: float = 30.0) -> None:
    t.join(timeout)
    assert not t.is_alive(), "поток не кончился в срок"


# --- план ---

def test_plan_is_rep_major_with_group_reps():
    keys = plan_runs(cfg(places={7: M1, 8: M2}))
    assert len(keys) == 64
    assert {k.rep for k in keys[:24]} == {1} and {k.rep for k in keys[24:48]} == {2}
    assert {k.scenario[0] for k in keys[48:]} == {"B", "C"}


# --- пробуждение и 503 ---

def test_wake_on_sleeping_place():                                               # Б-р3-1
    chat = FakeChat.honest(asleep_until_wake={7})                                   # 503 WorkplaceNotReady до wake
    rec = runner(chat=chat).run_one(RunKey(7, "B1", 1))
    assert rec.status == "done" and chat.wakes[7] >= 1
    assert chat.calls.index(("wake", 7)) < chat.calls.index(("new_session", 7))     # будит до разговора


def test_ensure_ready_before_each_run():                                         # Д8-R74: перед каждым прогоном места
    stand = FakeStand()
    r = runner(stand=stand, places={7: M1}, only=[RunKey(7, "B1", 1), RunKey(7, "B2", 1)])
    assert r.run() == "finished"
    assert stand.mcp_checks[7] == 3                                                 # prepare и два прогона


def test_place_falls_asleep_mid_turn_is_woken_not_stand_error():                 # Д8-R74: 503 — будить
    chat = FakeChat.honest(fall_asleep_after_send={7})
    rec = runner(chat=chat).run_one(RunKey(7, "B1", 1))
    assert rec.status == "done" and chat.wakes[7] == 1 and len(rec.turns) == 1


def test_keepalive_while_waiting():                                              # Б-р3-1: ждущее место будится
    clock = FakeClock(start=T0)
    chat = FakeChat.honest(delay_s={8: 25 * 60}, advance=clock)                     # место 8 — 25 мин на прогон
    r = runner(chat=chat, places={7: M1, 8: M2}, now=clock.now, clock=clock.mono, sleep=clock.sleep)
    r.run()
    assert chat.wakes_while_waiting(7) >= 2                                         # раз в 10 мин на барьере


def test_gateway_503_retried_during_reload():                                    # Review Focus 3, П6
    chat = FakeChat.honest(fail_first_send=GatewayError(503, "WorkplaceBusy"))    # повторы клиента кончились
    assert runner(chat=chat).run_one(RunKey(7, "B1", 1)).status == "done"


def test_gateway_500_is_stand_error():
    chat = FakeChat.honest(fail_first_send=GatewayError(500, "Boom"))
    rec = runner(chat=chat).run_one(RunKey(7, "B1", 1))
    assert rec.status == "stand_error" and chat.wakes[7] == 0


# --- барьер ---

def test_barrier_between_reps():                                                 # I-н6
    chat = FakeChat.honest(delay_s={8: 0.2})                                       # место 8 медленнее
    r = runner(chat=chat, places={7: M1, 8: M2}, sleep=lambda s: None)
    r.run()
    first_rep2 = min(i for i, k in enumerate(r.started) if k.rep == 2)
    assert all(k.rep == 1 for k in r.started[:first_rep2])
    assert r.started_at(first_rep2) >= max(r.finished_at(k) for k in r.started if k.rep == 1)


def test_barrier_skips_stopped_place():                                          # m-р3-3
    r = runner(chat=FakeChat.honest(fail_place={8: GatewayError(500, "Boom")}), places={7: M1, 8: M2})
    r.run()
    assert any(k.rep == 2 and k.place == 7 for k in r.started)
    assert r.progress()["by_place"]["8"]["status"] == "stopped"
    recs8 = [x for x in load_records(r.out) if x.key.place == 8]
    assert len(recs8) == 3 and {x.status for x in recs8} == {"stand_error"}       # три подряд — и всё


# --- сроки ---

def test_run_timeout_cuts_mid_turn():                                            # I-р3-10
    r = runner(chat=FakeChat.never_idle(), turn_timeout_s=900, run_timeout_s={"number": 0.3})
    out = []
    t0 = time.monotonic()
    t = threading.Thread(target=lambda: out.append(r.run_one(RunKey(7, "B1", 1))), daemon=True)
    t.start()
    t.join(5)                                                                       # без среза — ход висел бы 900 с
    rec = out[0] if out else None
    assert rec is not None and rec.status == "timeout" and time.monotonic() - t0 <= 0.5


def test_turn_timeout_aborts_and_marks():                                        # Review Focus 3
    chat = FakeChat.never_idle()
    rec = runner(chat=chat, turn_timeout_s=0.2).run_one(RunKey(7, "B1", 1))
    assert rec.status == "timeout" and chat.aborted == [rec.sessions[0]]


def test_window_end_from_window_start():                                         # I-н5, I-р3-10
    clock = FakeClock(start=T0 + timedelta(minutes=50))                             # «да» и карточки заняли 50 мин
    r = runner(window_start=T0, now=clock.now, chat=FakeChat.honest(advance=clock, minutes=10))
    assert r.window_end() == T0 + timedelta(minutes=165)                            # а не начало прогона + 130
    assert r.run() == "window"
    assert r.started
    for k in r.started:
        assert r.started_at_wall(k) + timedelta(seconds=RUN_TIMEOUT_S[BY_ID[k.scenario].group]) <= r.window_end()


def test_window_end_without_window_start_is_run_start_plus_130():
    clock = FakeClock(start=T0)
    r = runner(now=clock.now)
    assert r.window_end() == T0 + timedelta(minutes=130)


def test_archive_all_but_bench_within_deadline():                                # m-р4-3
    stand = FakeStand(saved_after_abort={7: "late-agent"})                         # сохранился уже после abort
    r = runner(chat=FakeChat.never_idle(), stand=stand, run_timeout_s={"create": 0.5}, archive_reserve_s=0.1)
    rec = r.run_one(RunKey(7, "A1", 1))
    assert rec.status == "timeout" and (7, "late-agent") in stand.archived
    assert stand.agents_listed(7) == ["bench-uzel"] and r.finished_at(rec.key) - r.started_at(0) <= 0.6


def test_archive_reserve_leaves_time_for_archive_wait():                         # m-р4-3: архив — внутри срока
    stand = FakeStand(saved_after_abort={7: "late-agent"}, archive_delay_s=0.2)    # уходит из списка через 0,2 с
    r = runner(chat=FakeChat.never_idle(), stand=stand, run_timeout_s={"create": 1.0}, archive_reserve_s=0.4)
    rec = r.run_one(RunKey(7, "A1", 1))
    assert rec.status == "timeout" and not any(n.startswith("архив") for n in rec.notes)   # дождался ухода
    assert r.finished_at(rec.key) - r.started_at(0) <= 1.1


# --- пульс и замок ---

def test_heartbeat_fresh_during_long_turn(tmp_path):                              # I-р3-11, I-р4-8
    r = runner(out=tmp_path, chat=FakeChat.honest(delay_s={7: 1.5}), heartbeat_s=0.2)
    r.prepare()                                                                     # пульс стартует здесь
    t = threading.Thread(target=r.run_one, args=(RunKey(7, "B1", 1),))
    t.start()
    time.sleep(1.0)                                                                 # ход ещё идёт
    assert status_main(tmp_path, stale_s=0.5) == 0
    _join(t)


def test_heartbeat_thread_is_daemon_and_stops_on_close(tmp_path):                # П7
    r = runner(out=tmp_path, heartbeat_s=0.05)
    r.prepare()
    pulse = r._pulse
    assert pulse is not None and pulse.daemon and pulse.is_alive()
    r.close()
    assert not pulse.is_alive()
    stamp = json.loads((tmp_path / "progress.json").read_text())["updated_at"]
    time.sleep(0.2)
    assert json.loads((tmp_path / "progress.json").read_text())["updated_at"] == stamp     # пульса больше нет


def test_second_runner_locked(tmp_path):                                          # I-р3-11, m-р4-1
    first = runner(out=tmp_path)
    first.prepare()                                                                 # ссылка держит замок
    with pytest.raises(RunRefused, match="locked"):
        runner(out=tmp_path).prepare()
    with pytest.raises(RunRefused, match="locked"):
        runner(out=tmp_path).run()
    assert json.loads((tmp_path / "progress.json").read_text())["state"] == "running"   # чужой progress цел
    first.close()
    runner(out=tmp_path).prepare()                                                  # замок снят — снова можно


def test_lock_file_holds_pid(tmp_path):
    import os
    r = runner(out=tmp_path)
    r.prepare()
    assert (tmp_path / ".lock").read_text().strip() == str(os.getpid())


def test_close_clears_pid_in_lock(tmp_path):                                     # итоговое ревью Д8а, I-1
    import os
    lock = tmp_path / ".lock"
    r = runner(out=tmp_path)
    r.prepare()
    assert lock.read_text().strip() == str(os.getpid())
    r.close()
    assert lock.read_text() == ""                                                    # устаревшего pid нет
    assert runner(out=tmp_path, only=[RunKey(7, "B1", 1)]).run() == "finished"
    assert lock.read_text() == ""                                                    # и после run()
    with pytest.raises(RunRefused, match="run_id"):
        runner(out=tmp_path, stand=FakeStand(run={"id": "d8-other"})).prepare()
    assert lock.read_text() == ""                                                    # и после отказа prepare()
    again = runner(out=tmp_path)
    again.prepare()
    assert lock.read_text().strip() == str(os.getpid())                              # следующий пишет свой


def test_refused_prepare_releases_lock_and_pulse(tmp_path):
    r = runner(out=tmp_path, stand=FakeStand(run={"id": "d8-other"}))
    with pytest.raises(RunRefused, match="run_id"):
        r.prepare()
    assert r._pulse is None
    runner(out=tmp_path).prepare()                                                  # замок свободен


# --- журнал сессий и возобновление ---

def test_sessions_journal_and_resume_aborts(tmp_path):                           # m-р3-4, m14
    chat = FakeChat.honest()
    runner(out=tmp_path, chat=chat, kill_after_session_create=True).run()           # сессия есть, конца нет
    assert (tmp_path / "sessions.log").read_text().count("ses_") == 1
    runner(out=tmp_path, chat=chat).prepare()
    assert chat.aborted == [chat.sessions[0]]
    assert f"{chat.sessions[0]}\t" in (tmp_path / "sessions.log").read_text().splitlines()[-1]


def test_sessions_journal_has_place_start_and_end(tmp_path):                     # П5
    chat = FakeChat.honest()
    rec = runner(out=tmp_path, chat=chat).run_one(RunKey(8, "B1", 1))
    lines = (tmp_path / "sessions.log").read_text().splitlines()
    assert lines == [f"{rec.sessions[0]}\t08\tstart", f"{rec.sessions[0]}\t08\tend"]


def test_resume_abort_goes_through_own_place(tmp_path):                          # П5
    (tmp_path / "sessions.log").write_text("ses_left8\t08\tstart\n")
    chat = FakeChat.honest()
    runner(out=tmp_path, chat=chat, places={7: M1, 8: M2}).prepare()
    assert chat.abort_places == [(8, "ses_left8")]                                  # через место 8, а не 7
    assert "ses_left8\t08\tend" in (tmp_path / "sessions.log").read_text()


def test_resume_prepare_order_sleeping_and_deleted(tmp_path):                    # I-р4-7, П5
    (tmp_path / "sessions.log").write_text("ses_gone\t07\tstart\n")                # сессию удалила уборка
    chat = FakeChat.honest(asleep_until_wake={7}, deleted_sessions={"ses_gone"})
    r = runner(out=tmp_path, chat=chat, places={7: M1})
    r.prepare()                                                                     # вход и пробуждение — до abort
    assert chat.calls.index(("login", 7)) < chat.calls.index(("wake", 7)) < chat.calls.index(("abort", "ses_gone"))
    assert "ses_gone\t07\tend" in (tmp_path / "sessions.log").read_text()          # 404 — «уже нет»


def test_resume_skips_done(tmp_path):
    out = tmp_path / "out"
    runner(out=out, only=[RunKey(7, "B1", 1)]).run()
    second = runner(out=out)
    second.run()
    assert RunKey(7, "B1", 1) not in second.started
    assert len(second.started) == 63


def test_resume_reruns_not_done(tmp_path):
    out = tmp_path / "out"
    runner(out=out, chat=FakeChat.never_idle(), turn_timeout_s=0.05, only=[RunKey(7, "B1", 1)]).run()
    assert load_records(out)[0].status == "timeout"
    again = runner(out=out, only=[RunKey(7, "B1", 1)])
    again.run()
    assert again.started == [RunKey(7, "B1", 1)] and load_records(out)[-1].status == "done"


# --- A: мост, архив, предел реплик, уточнение B3 ---

def test_create_run_flow_bridge_after_visible_and_archive():                     # m-н7
    stand = FakeStand(visible_after_s=0.1)
    r = runner(chat=FakeChat.honest(), stand=stand)
    rec = r.run_one(RunKey(7, "A1", 1))
    assert rec.status == "done" and rec.saved and rec.bridge is not None
    assert r.chats[7].sent_at("bridge") >= stand.visible_at(7, rec.saved[0]["name"])
    assert r.stand.archived == [(7, rec.saved[0]["name"])] and r.stand.agents_listed(7) == ["bench-uzel"]
    assert rec.saved[0]["flags"] == [] and rec.saved[0]["visible_at"] and rec.agent_file
    assert rec.visible_delay_s is not None and rec.saw_draft
    assert rec.calc["tag_now"]["items"][0]["tag"] == "20FAKE_104_PV"                 # калькулятор по мосту
    assert len(rec.sessions) == 2 and len(rec.llm_rows) == 2


def test_create_run_all_four_cards_reach_save():
    r = runner()
    for sid in ("A1", "A2", "A3", "A4"):
        rec = r.run_one(RunKey(7, sid, 1))
        assert rec.status == "done" and len(rec.saved) == 1 and rec.bridge is not None, sid
        assert len(rec.turns) <= BY_ID[sid].max_student_msgs
    assert r.stand.agents_listed(7) == ["bench-uzel"]


def test_b3_clarify_only_when_asked():                                           # m-н21
    asked = runner(chat=FakeChat.honest(b3_asks=True)).run_one(RunKey(7, "B3", 1))
    plain = runner(chat=FakeChat.honest(b3_asks=False)).run_one(RunKey(7, "B3", 1))
    assert len(asked.turns) == 2 and len(plain.turns) == 1


def test_calc_rate_pauses_and_retries():                                         # m10: rate — пауза, не сбой
    sleeps = []
    r = runner(stand=FakeStand(calc_rate=2), sleep=sleeps.append)
    rec = r.run_one(RunKey(7, "B1", 1))
    assert rec.status == "done" and rec.calc["tag_now"]["status"] == "ok" and sleeps.count(15.0) == 2


def test_mid_turn_gateway_failure_aborts_session():                              # место — один разговор за раз
    chat = FakeChat.honest(fail_wait={7: GatewayError(502, "BadGateway")})
    rec = runner(chat=chat).run_one(RunKey(7, "B1", 1))
    assert rec.status == "stand_error" and chat.aborted == [rec.sessions[0]]


def test_data_run_calc_and_rows():
    r = runner()
    rec = r.run_one(RunKey(7, "B2", 1))
    assert rec.status == "done" and set(rec.calc) == {"tag_period"}
    assert r.stand.calc_calls[-1] == ("tag_period", {"tags": ["20FAKE_101_PV"], "period": "prev_shift"})
    assert rec.llm_rows and {x["model"] for x in rec.llm_rows} == {"z-ai/glm-5.3"}
    assert RunRecord.from_json(rec.to_json()) == rec


# --- сверка модели ---

def test_model_mismatch_is_stand_error():                                        # Review Focus 2
    stand = FakeStand(rows_model="z-ai/glm-5.3")                                   # место 8 — deepseek
    assert runner(stand=stand).run_one(RunKey(8, "B1", 1)).status == "stand_error"


def test_model_match_place_8_is_done():
    assert runner().run_one(RunKey(8, "B1", 1)).status == "done"


# --- остановки ---

def test_cap_margin_stops_new_runs():
    stand = FakeStand(run={"spent_usd": 71.0, "cap_usd": 80.0})
    assert runner(stand=stand, stop_margin_usd=10.0).run() == "cap" and stand.sessions_started == 0


def test_cap_uses_smaller_of_config_and_proxy():
    stand = FakeStand(run={"spent_usd": 1.0, "cap_usd": 5.0})                      # прокси держит $5, конфиг — $80
    assert runner(stand=stand, cap_usd=80.0, stop_margin_usd=4.0).run() == "cap" and stand.sessions_started == 0


def test_cap_stops_mid_run_before_next_run():
    stand = FakeStand(cost_per_run={"create": 30.0})
    r = runner(stand=stand, places={7: M1}, cap_usd=80.0, stop_margin_usd=10.0)
    assert r.run() == "cap"
    assert [k.scenario for k in r.started] == ["A1", "A2", "A3"]                    # $90 + $10 ≥ $80 — A4 не начат


def test_run_block_gone_stops_new_runs():
    stand = FakeStand(run={"id": "d8-test"})
    r = runner(stand=stand, places={7: M1})
    r.prepare()
    stand.run_over["id"] = "d8-other"                                               # core пересоздан с другим прогоном
    assert r.run() == "stand" and not r.started


def test_projection_pauses_at_barrier_1():                                       # I11, I-н6
    stand = FakeStand(cost_per_run={"create": 1.8, "number": 0.2, "trap": 0.2})    # повтор 1: 4 × (7,2 + 1,6) = $35,2
    r = runner(stand=stand, places={7: M1, 8: M2, 9: M3, 10: M4}, cap_usd=80, stop_margin_usd=10)
    assert r.run() == "projection" and r.progress()["state"] == "paused"
    assert {k.rep for k in r.started} == {1}                                        # повтор 2 не начат
    assert r.projection() == pytest.approx(4 * (2 * 7.2 + 3 * 0.8 + 3 * 0.8))       # $76,8 > $80 − $10
    assert status_main(r.out, 120) == 2


def test_projection_ack_resumes(tmp_path):                                       # m-р4-7
    stand = FakeStand(cost_per_run={"create": 1.8, "number": 0.2, "trap": 0.2})
    places = {7: M1, 8: M2, 9: M3, 10: M4}
    assert runner(out=tmp_path, stand=stand, places=places, cap_usd=80, stop_margin_usd=10).run() == "projection"
    again = runner(out=tmp_path, stand=stand, places=places, cap_usd=80, stop_margin_usd=10,
                   reps={"create": 2, "number": 2, "trap": 2}, projection_ack=True)
    assert again.run() != "projection"                                      # снятый повтор 3: $70,4 — без новой паузы
    assert any(k.rep == 2 for k in again.started)


def test_projection_without_ack_pauses_again_on_resume(tmp_path):
    stand = FakeStand(cost_per_run={"create": 1.8, "number": 0.2, "trap": 0.2})
    places = {7: M1, 8: M2, 9: M3, 10: M4}
    assert runner(out=tmp_path, stand=stand, places=places).run() == "projection"
    again = runner(out=tmp_path, stand=stand, places=places)
    assert again.run() == "projection" and not again.started


def test_money_and_share_stop():                                                 # m2
    for code in ("pcbk_money_out", "pcbk_share_spent"):
        assert runner(chat=FakeChat.honest(error=code)).run() == "money"


def test_run_spent_402_is_capped_and_stops():
    r = runner(chat=FakeChat.honest(error="pcbk_run_spent"), places={7: M1})
    assert r.run() == "cap"
    recs = load_records(r.out)
    assert len(recs) == 1 and recs[0].status == "capped"


@pytest.mark.parametrize("code, reason", [("pcbk_money_out", "money"), ("pcbk_run_spent", "cap")])
def test_hard_stop_reaches_other_place_mid_conversation(code, reason):           # после 402 — ни реплики (р1, m5)
    chat = FakeChat.honest(error_place={7: code}, delay_s={7: 0.1, 8: 0.3})     # 8 — посреди хода
    r = runner(chat=chat, places={7: M1, 8: M2})
    assert r.run() == reason
    rec8 = next(x for x in load_records(r.out) if x.key.place == 8)
    sends8 = [c for c in chat.calls if c[0] == "send" and c[1] in rec8.sessions]
    assert rec8.status == "interrupted" and len(sends8) == 1                        # первый ход — и всё


def test_hard_stop_sends_no_more_messages():                                     # деньги кончились — реплик нет
    chat = FakeChat.honest(error="pcbk_money_out")
    r = runner(chat=chat, places={7: M1, 8: M2})
    assert r.run() == "money"
    sends = [c for c in chat.calls if c[0] == "send"]
    assert len(sends) <= 2                                                          # по ходу на место, не больше


def test_partial_place_stop_is_place_reason(capsys):                            # р1, I1
    r = runner(chat=FakeChat.honest(fail_place={8: GatewayError(500, "Boom")}), places={7: M1, 8: M2})
    assert r.run() == "place"
    p = json.loads((r.out / "progress.json").read_text())
    assert (p["state"], p["stop_reason"]) == ("stopped", "place") and p["by_place"]["8"]["status"] == "stopped"
    capsys.readouterr()
    assert status_main(r.out, 120) == 2
    assert "места остановлены: 08" in capsys.readouterr().out


def test_run_block_gone_mid_conversation_no_more_sends():                       # р1, I2
    stand = FakeStand()

    def gone(place, n):
        if n == 2:
            stand.run_over["id"] = "d8-gone"                                        # core пересоздан без прогона
    chat = FakeChat.honest(on_send=gone)
    r = runner(chat=chat, stand=stand, places={7: M1}, only=[RunKey(7, "A1", 1)])
    assert r.run() == "stand"
    assert [c for c in chat.calls if c[0] == "send"] == [("send", chat.sessions[0])] * 2   # третьей реплики нет
    assert load_records(r.out)[0].status == "interrupted" and r._hard.is_set()


@pytest.mark.parametrize("first_detector", ["guard", "cap"])
def test_run_block_gone_hard_stop_when_health_then_unreadable(first_detector):   # р1, I2: hard — не только сторож
    stand = FakeStand(health_fail_after_gone=True)

    def gone(place, n):
        if place == 8:
            stand.run_over["id"] = "d8-gone"
    if first_detector == "guard":       # место 7 замечает пропажу перед своей репликой, место 8 — посреди хода
        only = [RunKey(7, "B1", 1), RunKey(7, "B2", 1), RunKey(8, "A1", 1)]
        chat = FakeChat.honest(on_send=gone, delay_s={8: 0.4}, slow_new_session={7: 0.2, 8: 0.1})
    else:                               # место 8 замечает её проверкой потолка перед B2, место 7 — посреди A
        only = [RunKey(7, "A1", 1), RunKey(8, "B1", 1), RunKey(8, "B2", 1)]
        chat = FakeChat.honest(on_send=gone, delay_s={7: 0.4}, slow_new_session={8: 0.1})
    r = runner(chat=chat, stand=stand, places={7: M1, 8: M2}, only=only)
    assert r.run() == "stand" and r._hard.is_set()
    sends = [c for c in chat.calls if c[0] == "send"]
    a1 = next(x for x in load_records(r.out) if x.key.scenario == "A1")
    assert a1.status == "interrupted" and sum(1 for c in sends if c[1] in a1.sessions) == 1


def test_deadline_between_turns_sends_nothing():                                 # р1, m1
    clock = FakeClock(start=T0)
    chat = FakeChat.honest(b3_asks=True, delay_s={7: 600}, advance=clock)          # первый ход съел весь срок
    r = runner(chat=chat, places={7: M1}, now=clock.now, clock=clock.mono, sleep=clock.sleep,
               run_timeout_s={"number": 600})
    rec = r.run_one(RunKey(7, "B3", 1))
    assert rec.status == "timeout" and len(rec.turns) == 1
    assert [c for c in chat.calls if c[0] == "send"] == [("send", rec.sessions[0])]     # уточнения нет


def test_guard_read_before_deadline_check():                                     # р1б, п. 1
    clock = FakeClock(start=T0)
    stand = FakeStand(health_advance=(clock, 700))                                 # чтение /health/llm — 700 с
    chat = FakeChat.honest()
    r = runner(chat=chat, stand=stand, places={7: M1}, now=clock.now, clock=clock.mono, sleep=clock.sleep,
               run_timeout_s={"number": 600})
    rec = r.run_one(RunKey(7, "B1", 1))                                             # срок 600 с вышел, пока шло чтение
    assert rec.status == "timeout" and not [c for c in chat.calls if c[0] == "send"]


def test_guard_read_failure_lets_reply_go():                                     # р1б, п. 3: сбой чтения — не стоп
    stand = FakeStand(health_fail_calls={1})                                        # сторож первой реплики
    r = runner(stand=stand, places={7: M1})
    rec = r.run_one(RunKey(7, "B1", 1))
    assert rec.status == "done" and len(rec.turns) == 1 and not r._hard.is_set() and stand.health_calls == 1


def test_window_checked_after_slow_health():                                     # р1, m3
    clock = FakeClock(start=T0)
    stand = FakeStand(health_advance=(clock, 50 * 60))                             # каждый /health/llm — 50 мин
    r = runner(stand=stand, places={7: M1}, only=[RunKey(7, "B1", 1)], now=clock.now, clock=clock.mono,
               sleep=clock.sleep)
    assert r.run() == "window" and not r.started and stand.sessions_started == 0


def test_prepare_refuses_busy_place(monkeypatch):                                # р1, m4: чужой прогонщик, другой OUT
    import pcbk_bench.runner as mod
    monkeypatch.setattr(mod, "IDLE_WAIT_S", 0.05)
    chat = FakeChat.never_idle()
    other = chat.view(7)
    other.send(other.new_session(), "Что сейчас по 20FAKE_104_PV?", "bench-uzel")   # ход чужого прогонщика идёт
    with pytest.raises(RunRefused, match="place_busy"):
        runner(chat=chat, places={7: M1}).prepare()


def test_future_window_start_refused(tmp_path, capsys):                         # р1, m7
    with pytest.raises(RunRefused, match="window_start"):
        runner(window_start=now() + timedelta(minutes=10)).prepare()
    runner(window_start=now() + timedelta(minutes=4)).prepare()                    # в пределах 5 мин — можно
    cfg_path = write_config(tmp_path, window_start=iso(now() + timedelta(minutes=10)))
    assert main(["run", str(cfg_path)]) == 2                                        # до stand.json: его нет
    assert capsys.readouterr().err.strip().endswith("RunRefused: window_start")


def test_no_rows_is_stand_error():                                               # р1, m5
    rec = runner(stand=FakeStand(rows_none=True), sleep=lambda s: None).run_one(RunKey(7, "B1", 1))
    assert rec.status == "stand_error" and rec.llm_rows == []


def test_foreign_model_on_timeout_is_stand_error():                              # р1, m5
    stand = FakeStand(rows_model="z-ai/other")
    rec = runner(chat=FakeChat.never_idle(), stand=stand, turn_timeout_s=0.1).run_one(RunKey(7, "B1", 1))
    assert rec.status == "stand_error"


def test_rows_needed_on_timeout_after_finished_turn():                          # р1б, п. 2
    chat = FakeChat.honest(b3_asks=True, stall_after_turns={7: 1})                 # уточнение упирается в срок
    r = runner(chat=chat, stand=FakeStand(rows_none=True), places={7: M1}, turn_timeout_s=0.2, sleep=lambda s: None)
    rec = r.run_one(RunKey(7, "B3", 1))
    assert len(rec.turns) == 1 and rec.status == "stand_error" and rec.llm_rows == []


def test_rows_needed_for_each_session_with_turn():                              # р1, m6: мост без строк
    stand = FakeStand(rows_skip_bridge=True)
    rec = runner(stand=stand, sleep=lambda s: None).run_one(RunKey(7, "A1", 1))
    assert rec.bridge is not None and len(rec.sessions) == 2 and len(rec.llm_rows) == 1
    assert rec.status == "stand_error"


def test_stand_rows_pause_then_stop():
    stand = FakeStand(status_rows={"memory": "warn"})
    sleeps = []
    assert runner(stand=stand, sleep=sleeps.append).run() == "stand" and sleeps == [60, 60]


def test_stand_llm_fail_stops():
    sleeps = []
    assert runner(stand=FakeStand(status_rows={"llm": "fail"}), sleep=sleeps.append).run() == "stand"


def test_interrupt_aborts_live_session_and_marks():
    chat = FakeChat.never_idle()
    r = runner(chat=chat, places={7: M1}, only=[RunKey(7, "B1", 1)], turn_timeout_s=5)  # без abort — срок, не висим

    def fire():
        deadline = time.monotonic() + 10
        while not chat.sessions and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        r._on_signal(signal.SIGINT, None)
    threading.Thread(target=fire, daemon=True).start()
    assert r.run() == "interrupt"
    recs = load_records(r.out)
    assert recs[0].status == "interrupted" and set(chat.aborted) == {chat.sessions[0]}
    assert f"{chat.sessions[0]}\t07\tend" in (r.out / "sessions.log").read_text()


def test_interrupt_reaborts_every_tick():                                        # р1, m2: первый abort не дошёл
    chat = FakeChat.never_idle(ignore_aborts=1)
    r = runner(chat=chat, places={7: M1}, only=[RunKey(7, "B1", 1)], turn_timeout_s=5)

    def fire():
        deadline = time.monotonic() + 10
        while not chat.sessions and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        r._on_signal(signal.SIGINT, None)
    threading.Thread(target=fire, daemon=True).start()
    assert r.run() == "interrupt"
    assert load_records(r.out)[0].status == "interrupted" and chat.calls.count(("abort", chat.sessions[0])) >= 2


def test_interrupt_reabort_throttled(monkeypatch):                               # р1б, п. 4: не чаще ABORT_REPEAT_S
    import pcbk_bench.runner as mod
    monkeypatch.setattr(mod, "ABORT_WAIT_S", 0.1)                                  # ожидание после abort по сроку
    chat = FakeChat.never_idle(ignore_aborts=1000)                                 # место abort не видит вовсе
    r = runner(chat=chat, places={7: M1}, only=[RunKey(7, "B1", 1)], turn_timeout_s=1.6)

    def fire():
        deadline = time.monotonic() + 10
        while not chat.sessions and time.monotonic() < deadline:
            time.sleep(0.01)
        r._on_signal(signal.SIGINT, None)
    threading.Thread(target=fire, daemon=True).start()
    assert r.run() == "interrupt"
    aborts = chat.calls.count(("abort", chat.sessions[0]))
    assert 2 <= aborts <= 4                  # главный поток — в 0 и 1 с, место — по сроку хода 1,6 с; без предела — ~30


def test_interrupt_during_send_aborts_before_turn():                             # р1, m2: сигнал, пока реплика уходит
    holder = {}
    chat = FakeChat.never_idle(on_send=lambda place, n: holder["r"]._on_signal(signal.SIGINT, None))
    r = holder["r"] = runner(chat=chat, places={7: M1}, turn_timeout_s=2)
    rec = r.run_one(RunKey(7, "B1", 1))
    assert rec.status == "interrupted" and rec.turns == [] and chat.aborted == [rec.sessions[0]]


def test_run_restores_signal_handlers():
    before = signal.getsignal(signal.SIGINT)
    runner(only=[RunKey(7, "B1", 1)]).run()
    assert signal.getsignal(signal.SIGINT) is before


# --- подготовка ---

def test_prepare_refuses_place_outside_run():                                    # Review Focus 1, I3
    stand = FakeStand(run={"id": "d8-x", "callers": 2}, run_callers={"student-07", "student-06"})
    with pytest.raises(RunRefused, match="run_callers"):
        runner(stand=stand, places={7: M1, 8: M2}, run_id="d8-x").prepare()
    with pytest.raises(RunRefused, match="run_id"):
        runner(stand=FakeStand(run={"id": "d8-y", "callers": 2}), run_id="d8-x").prepare()
    with pytest.raises(RunRefused, match="run_callers"):
        runner(stand=FakeStand(run={"callers": 3}), places={7: M1, 8: M2}).prepare()


def test_prepare_refuses_wrong_place_model():                                    # Review Focus 2, m16
    with pytest.raises(RunRefused, match="place_model"):
        runner(stand=FakeStand(place_models={7: "pcbk/z-ai/glm-5.3", 8: "pcbk/z-ai/glm-5.3"}),
               places={7: M1, 8: M2}).prepare()


def test_prepare_waits_mcp_reads_units_and_refuses_name_taken(tmp_path):         # m-н8, m-н9, I-р3-8, I-р4-4
    stand = FakeStand(mcp_after_s={7: 0.1}, units={"20FAKE_107_PV": "°C"})
    r = runner(out=tmp_path, stand=stand, places={7: M1})
    r.prepare()
    assert stand.mcp_checks[7] >= 2 and r.tag_units["20FAKE_107_PV"] == "°C"
    assert json.loads((tmp_path / "tag_units.json").read_text())["20FAKE_107_PV"] == "°C"
    with pytest.raises(RunRefused, match="bench_agent"):
        runner(stand=FakeStand(save_answer={"status": "refused", "problems": [{"code": "name_taken"}]})).prepare()


def test_prepare_refuses_mcp_never_connected():
    stand = FakeStand(mcp_after_s={7: 1e9})
    r = runner(stand=stand, places={7: M1}, sleep=lambda s: None)
    r.poll_s = 0.0
    import pcbk_bench.runner as mod
    saved, mod.MCP_TIMEOUT_S = mod.MCP_TIMEOUT_S, 0.05
    try:
        with pytest.raises(RunRefused, match="not_ready"):
            r.prepare()
    finally:
        mod.MCP_TIMEOUT_S = saved


def test_prepare_archives_foreign_agents_and_saves_bench():
    stand = FakeStand()
    r = runner(stand=stand, places={7: M1})
    stand._add(7, "left-agent", 0.0)
    r.prepare()
    assert stand.archived == [(7, "left-agent")]
    assert stand.bench_saves and stand.bench_saves[0][1]["name"] == "bench-uzel"


def test_prepare_units_resume_from_file(tmp_path):                               # m10
    (tmp_path / "tag_units.json").write_text(json.dumps({"20FAKE_104_PV": "кгс/см2"}))
    stand = FakeStand()
    r = runner(out=tmp_path, stand=stand, places={7: M1})
    r.prepare()
    assert r.tag_units["20FAKE_104_PV"] == "кгс/см2"
    assert ("catalog_search", {"query": "20FAKE_104_PV", "limit": 10}) not in stand.calc_calls


# --- status ---

def test_status_exit_codes(tmp_path):                                            # Review Focus 3
    write_progress(tmp_path / "progress.json", {"state": "running", "updated_at": iso(now()),
                                                "last_turn_at": iso(now() - timedelta(seconds=800))})
    assert status_main(tmp_path, 120) == 0                                          # долгий ход — не тревога
    write_progress(tmp_path / "progress.json", {"state": "running", "updated_at": iso(now() - timedelta(seconds=121))})
    assert status_main(tmp_path, 120) == 1
    write_progress(tmp_path / "progress.json", {"state": "running", "updated_at": iso(now()), "turn_timeout_s": 900,
                                                "last_turn_at": iso(now() - timedelta(seconds=1201))})
    assert status_main(tmp_path, 120) == 1                                          # m-р4-6: главный поток завис
    write_progress(tmp_path / "progress.json", {"state": "paused", "stop_reason": "projection",
                                                "updated_at": iso(now())})
    assert status_main(tmp_path, 120) == 2
    write_progress(tmp_path / "progress.json", {"state": "finished", "updated_at": iso(now() - timedelta(days=1))})
    assert status_main(tmp_path, 120) == 0


def test_status_missing_file_is_one(tmp_path):
    assert status_main(tmp_path, 120) == 1
    assert main(["status", str(tmp_path)]) == 1


def test_progress_keys_after_run(tmp_path):
    r = runner(out=tmp_path, only=[RunKey(7, "B1", 1)])
    assert r.run() == "finished"
    p = json.loads((tmp_path / "progress.json").read_text())
    assert set(p) == {"run_id", "state", "started_at", "updated_at", "last_turn_at", "turn_timeout_s", "done", "total",
                      "rep", "by_place", "run_spent_usd", "projected_usd", "stop_reason"}
    assert (p["state"], p["done"], p["total"]) == ("finished", 1, 1)
    assert json.loads((tmp_path / "health_end.json").read_text())["run"]["id"] == "d8-test"


def test_progress_run_spent_from_health_end_after_cap(tmp_path):                 # итоговое ревью Д8а, m8
    r = runner(out=tmp_path, chat=FakeChat.honest(error="pcbk_run_spent"), places={7: M1})
    assert r.run() == "cap"                                                         # жёсткая остановка по 402
    spent = json.loads((tmp_path / "health_end.json").read_text())["run"]["spent_usd"]
    p = json.loads((tmp_path / "progress.json").read_text())
    assert spent > 0 and p["run_spent_usd"] == spent                                # не чтение до прогонов (0.0)


def test_progress_run_spent_ignores_foreign_run_block(tmp_path):
    stand = FakeStand()
    r = runner(out=tmp_path, stand=stand, places={7: M1})
    r.prepare()
    stand.run_over.update({"id": "d8-other", "spent_usd": 7.0})                     # core пересоздан с другим прогоном
    assert r.run() == "stand"
    assert json.loads((tmp_path / "health_end.json").read_text())["run"]["id"] == "d8-other"
    assert json.loads((tmp_path / "progress.json").read_text())["run_spent_usd"] == 0.0     # чужой расход не взят


# --- CLI ---

def test_cli_hides_exception_text(tmp_path, capsys):                             # m-р4-16
    cfg_path = write_config(tmp_path, window_start="2026-10-01T10:00:00+05:00")      # р1, m7: начало окна — не будущее
    with fake_stand_raising(RuntimeError("tag 20FAKE_104_PV value 3.42")):
        assert main(["run", str(cfg_path)]) == 1
    out = capsys.readouterr()
    assert "RuntimeError" in out.err and "20FAKE" not in out.err + out.out and "3.42" not in out.err + out.out


def test_cli_locked_is_two(tmp_path, capsys):
    cfg_path = write_config(tmp_path, window_start="2026-10-01T10:00:00+05:00")
    holder = runner(out=tmp_path / "out")
    holder.prepare()
    with fake_stand_raising(RuntimeError("x")):
        assert main(["run", str(cfg_path)]) == 2
    assert capsys.readouterr().err.strip().endswith("RunRefused: locked")


# --- разбор хода ---

def _msg(role, parts, created=1_000_000, completed=None, error=None):
    info = {"role": role, "time": {"created": created, **({"completed": completed} if completed else {})}}
    if error:
        info["error"] = error
    return {"info": info, "parts": parts}


def test_parse_turn_texts_tools_error_code():                                    # m21
    body = json.dumps({"error": {"message": "Потолок прогона", "type": "pcbk", "code": "pcbk_run_spent"}})
    messages = [
        _msg("user", [{"type": "text", "text": "старое"}]),
        _msg("assistant", [{"type": "text", "text": "старый ответ"}], completed=1_000_500),
        _msg("user", [{"type": "text", "text": "Что сейчас?"}], created=2_000_000),
        _msg("assistant", [{"type": "step-start"},
                           {"type": "tool", "tool": "pcbk_tag_now",
                            "state": {"status": "completed", "input": {"tags": ["20FAKE_104_PV"]},
                                      "output": json.dumps({"status": "ok", "items": []})}},
                           {"type": "tool", "tool": "bash", "state": {"status": "error", "input": {"command": "ls"},
                                                                      "error": "denied"}},
                           {"type": "text", "text": "  "}, {"type": "text", "text": "Сейчас 3,42 бар."}],
             created=2_000_100, completed=2_003_000,
             error={"name": "APIError", "data": {"message": "Payment Required", "statusCode": 402,
                                                 "responseBody": body}}),
    ]
    turn = parse_turn(messages, 2, "Что сейчас?")
    assert turn.student == "Что сейчас?" and turn.assistant == ["Сейчас 3,42 бар."]
    assert [(c.tool, c.status) for c in turn.tools] == [("pcbk_tag_now", "completed"), ("bash", "error")]
    assert turn.tools[0].output == {"status": "ok", "items": []} and turn.tools[1].output == "denied"
    assert turn.error == "APIError: Payment Required [pcbk_run_spent]" and turn_code(turn.error) == "capped"
    assert (turn.started, turn.ended) == (2000.0, 2003.0)


@pytest.mark.parametrize("error, code", [
    (None, None), ("MessageAbortedError: x", None), ("APIError: x [pcbk_turn_limit]", None),
    ("APIError: x [pcbk_money_out]", "money"), ("APIError: x [pcbk_key_limit]", "money"),
    ("APIError: x [pcbk_budget_spent]", "money"), ("APIError: x [pcbk_share_spent]", "money"),
    ("APIError: Потолок прогона отбора модели исчерпан — модель не отвечает.", "capped"),
    ("APIError: x [pcbk_upstream_down]", "stand_error"), ("APIError: tool pcbk_tag_now failed", None)])
def test_turn_code(error, code):
    assert turn_code(error) == code


def test_parse_turn_text_response_body_code():
    error = {"name": "APIError", "data": {"message": "x", "responseBody": "plain pcbk_money_out text"}}
    turn = parse_turn([_msg("user", []), _msg("assistant", [], error=error)], 0, "q")
    assert turn.error == "APIError: x [pcbk_money_out]" and turn.assistant == [] and turn.started == 1000.0


# --- клиент шлюза на подставном HTTP-сервере ---

class _Gate:
    """Ответы подставного шлюза: очередь (метод, путь) → [(код, тело)], журнал запросов с заголовками."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list[tuple[int, object]]] = {}
        self.seen: list[tuple[str, str, dict, bytes]] = []
        self.lock = threading.Lock()

    def add(self, method, path, *answers):
        self.routes.setdefault((method, path), []).extend(answers)

    def answer(self, method, path):
        with self.lock:
            queue = self.routes.get((method, path), [])
            if not queue:
                return 404, {"name": "NotFound", "data": {"message": "x"}}
            return queue.pop(0) if len(queue) > 1 else queue[0]


@pytest.fixture
def gate():
    state = _Gate()

    class Handler(BaseHTTPRequestHandler):
        def _go(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            with state.lock:
                state.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            code, data = state.answer(self.command, self.path)
            raw = b"" if data is None else json.dumps(data).encode()
            self.send_response(code)
            if self.path.endswith("/auth/login") and code == 200:
                self.send_header("Set-Cookie", "pcbk_session=tok123; Path=/pcbk/api/; HttpOnly")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = _go

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()
    server.server_close()
    _join(thread, 5)


def _client(gate, **kw):
    chat = GatewayChat(gate.base, "stand.pcbk.test:18443", "https://stand.pcbk.test:18443", timeout=5,
                       login="d8-probe-bench-07", password="pw-test", **kw)
    chat.retry_delay_s = chat.status_poll_s = chat.messages_poll_s = chat.wake_poll_s = 0.01
    gate.add("POST", "/pcbk/api/auth/login", (200, {"login": "d8-probe-bench-07", "place": "07"}))
    return chat


def test_gateway_login_headers_and_cookie(gate):
    chat = _client(gate)
    chat.login()
    gate.add("POST", "/pcbk/api/oc/session", (200, {"id": "ses_1"}))
    assert chat.new_session() == "ses_1"
    method, path, headers, body = gate.seen[-1]
    assert headers["host"] == "stand.pcbk.test:18443" and headers["sec-fetch-site"] == "same-origin"
    assert headers["origin"] == "https://stand.pcbk.test:18443" and headers["cookie"] == "pcbk_session=tok123"
    assert json.loads(body) == {}
    assert chat.place == "07"


def test_gateway_retries_busy_then_ok(gate):                                     # П6: WorkplaceBusy — повтор клиента
    chat = _client(gate)
    chat.login()
    busy = (503, {"name": "WorkplaceBusy", "data": {"message": "x"}})
    gate.add("POST", "/pcbk/api/oc/session/ses_1/prompt_async", busy, (429, {"name": "TooManyRequests"}), (204, None))
    chat.send("ses_1", "Что сейчас?", "bench-uzel")
    sends = [s for s in gate.seen if s[1].endswith("prompt_async")]
    assert len(sends) == 3
    assert json.loads(sends[-1][3]) == {"parts": [{"type": "text", "text": "Что сейчас?"}], "agent": "bench-uzel"}


def test_gateway_busy_exhausted_is_gateway_error(gate):
    chat = _client(gate)
    chat.login()
    gate.add("POST", "/pcbk/api/oc/session/ses_1/prompt_async", (503, {"name": "WorkplaceBusy"}))
    with pytest.raises(GatewayError) as err:
        chat.send("ses_1", "x", "bench-uzel")
    assert (err.value.status, err.value.name) == (503, "WorkplaceBusy") and not isinstance(err.value, WorkplaceNotReady)
    assert len([s for s in gate.seen if s[1].endswith("prompt_async")]) == 6         # 1 + 5 повторов


def test_gateway_not_ready_raises_without_retry(gate):
    chat = _client(gate)
    chat.login()
    gate.add("GET", "/pcbk/api/oc/session/status", (503, {"name": "WorkplaceNotReady"}))
    with pytest.raises(WorkplaceNotReady):
        chat.session_status()
    assert len([s for s in gate.seen if s[1].endswith("/session/status")]) == 1


def test_gateway_relogin_once_on_401(gate):
    chat = _client(gate)
    chat.login()
    gate.add("GET", "/pcbk/api/workplace", (401, {"name": "Unauthorized"}), (200, {"state": "ready"}))
    assert chat.workplace() == {"state": "ready"}
    assert [s[1] for s in gate.seen].count("/pcbk/api/auth/login") == 2


def test_gateway_wake_polls_until_ready(gate):
    chat = _client(gate)
    chat.login()
    gate.add("POST", "/pcbk/api/workplace/wake", (202, {"state": "starting"}))
    gate.add("GET", "/pcbk/api/workplace", (200, {"state": "starting"}), (200, {"state": "ready"}))
    chat.wake(timeout=5)
    gate.routes[("POST", "/pcbk/api/workplace/wake")] = [(202, {"state": "failed"})]
    with pytest.raises(WorkplaceNotReady):
        chat.wake(timeout=5)


def test_gateway_wait_idle_needs_new_answer_and_rejects_asks(gate):              # m-р4-15
    chat = _client(gate)
    chat.messages_poll_s = 0.0                                                      # сообщения — на каждом такте
    chat.login()
    gate.add("POST", "/pcbk/api/oc/session", (200, {"id": "ses_1"}))
    gate.add("POST", "/pcbk/api/oc/session/ses_1/prompt_async", (204, None))
    sid = chat.new_session()
    chat.send(sid, "раз", "bench-uzel")
    old = [_msg("user", [], 1), _msg("assistant", [], 2, completed=3)]
    new = old + [_msg("user", [], 4), _msg("assistant", [{"type": "text", "text": "ответ"}], 5, completed=6)]
    chat.send(sid, "два", "bench-uzel")
    # сразу после prompt_async сессии ещё нет в статусе, а последний ответ — прошлого хода: это не конец хода
    gate.add("GET", "/pcbk/api/oc/session/status", (200, {}), (200, {"ses_1": {"type": "busy"}}), (200, {}))
    gate.add("GET", "/pcbk/api/oc/session/ses_1/message", (200, old), (200, old), (200, new))
    gate.add("GET", "/pcbk/api/oc/question", (200, [{"id": "que_1", "sessionID": "ses_1"},
                                                     {"id": "que_2", "sessionID": "ses_other"}]))
    gate.add("GET", "/pcbk/api/oc/permission", (200, []))
    gate.add("POST", "/pcbk/api/oc/question/que_1/reject", (200, True))
    assert chat.wait_idle(sid, 5) == new
    paths = [s[1] for s in gate.seen]
    assert "/pcbk/api/oc/question/que_1/reject" in paths and "/pcbk/api/oc/question/que_2/reject" not in paths


def test_gateway_wait_idle_timeout(gate):
    chat = _client(gate)
    chat.login()
    gate.add("GET", "/pcbk/api/oc/session/status", (200, {"ses_1": {"type": "busy"}}))
    gate.add("GET", "/pcbk/api/oc/session/ses_1/message", (200, []))
    gate.add("GET", "/pcbk/api/oc/question", (200, []))
    gate.add("GET", "/pcbk/api/oc/permission", (200, []))
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        chat.wait_idle("ses_1", 0.2)
    assert time.monotonic() - t0 < 2


def test_gateway_password_not_in_repr(gate):
    chat = _client(gate)
    assert "pw-test" not in repr(vars(chat))


# --- стенд сервера (SSH подставной) ---

class _Ssh:
    def __init__(self, out="", code=0, raise_timeout=False, env=""):
        self.out, self.code, self.raise_timeout, self.env = out, code, raise_timeout, env
        self.calls = []

    def __call__(self, argv, input=None, capture_output=True, text=True, timeout=None):
        self.calls.append((argv, input, timeout))
        env_read = "grep -E" in argv[-1]
        if self.raise_timeout and not env_read:
            raise subprocess.TimeoutExpired(argv, timeout)
        out = self.env if env_read else self.out
        return subprocess.CompletedProcess(argv, self.code, out, "секретный вывод 20FAKE_104_PV")


def _server(ssh, tmp_path):
    return ServerStand(["ssh", "-o", "BatchMode=yes", "dev@host"], "http://127.0.0.1:18000", "https://127.0.0.1:28443",
                       "stand.example:28443", tmp_path, run=ssh)


def test_server_stand_timeouts_and_errors_are_stand_error(tmp_path):              # m-р4-6
    ssh = _Ssh(raise_timeout=True)
    with pytest.raises(StandError, match="срок 60"):
        _server(ssh, tmp_path).agent_rows(7)
    ssh = _Ssh(raise_timeout=True)
    with pytest.raises(StandError, match="срок 120"):
        _server(ssh, tmp_path).save_agent(7, {"name": "bench-uzel"})
    with pytest.raises(StandError) as err:
        _server(_Ssh(code=1), tmp_path).archive_agent(7, "bench-uzel")
    assert "20FAKE" not in str(err.value)


def test_server_stand_commands(tmp_path):
    ssh = _Ssh(out="[]")
    stand = _server(ssh, tmp_path)
    assert stand.llm_rows(["ses_a"]) == []
    argv, stdin, timeout = ssh.calls[-1]
    assert argv[:4] == ["ssh", "-o", "BatchMode=yes", "dev@host"] and len(argv) == 5 and timeout == 60
    assert argv[4].startswith("docker exec -i pcbk-core python -c ") and "mode=ro" in argv[4]
    assert json.loads(stdin) == ["ses_a"]
    ssh.out = '{"status": "ok", "name": "bench-uzel", "already": true}'
    assert stand.save_agent(7, {"name": "bench-uzel", "tags": ["20FAKE_101_PV"]})["already"] is True
    argv, stdin, timeout = ssh.calls[-1]
    assert argv[4].startswith('SD=/opt/pcbk-reserve/secrets; STU=172.31; '
                              '{ cat "$SD/student-07.data-token"; echo; cat; } | docker exec -i pcbk-core python -c ')
    assert "streamable_http_client" in argv[4] and timeout == 120
    assert json.loads(stdin)["name"] == "bench-uzel"                                # токен — на сервере, не в stdin
    ssh.out = ""
    stand.archive_agent(7, "bench-a1b2c3")
    assert ssh.calls[-1][0][4] == ("docker exec pcbk-core python -m pcbk_core.agents.admin archive --place 7 --name "
                                   "bench-a1b2c3 --yes")
    with pytest.raises(StandError):
        stand.archive_agent(7, "../etc")
    with pytest.raises(StandError):
        stand.agent_rows(11)
    ssh.out = "[]"
    assert stand.agent_rows(7) == []
    ssh.out = '{"model": "pcbk/z-ai/glm-5.3"}'
    stand.place_model(7)
    ssh.out = "student-07\n"
    stand.run_callers()
    ssh.out = ""
    stand.agent_file(7, "bench-uzel")
    stand.place_mcp_ok(7)
    assert len(ssh.calls) == 9 and not [c for c in ssh.calls if "$(" in c[0][4]]     # р1, m5: без «$(» нигде


def test_server_stand_remote_env_literal_and_checked(tmp_path):                # р1, m5
    ssh = _Ssh(out='{"model": "pcbk/z-ai/glm-5.3"}', env="STU_NET=10.9\nSECRETS_DIR=/srv/pcbk/secrets\n")
    stand = _server(ssh, tmp_path)
    stand.place_model(7)
    stand.place_model(8)
    env_read = "cd /opt/pcbk-reserve && grep -E '^(STU_NET|SECRETS_DIR)=' .env || true"
    assert [c[0][4] for c in ssh.calls].count(env_read) == 1                       # .env читается один раз
    assert ssh.calls[-1][0][4].startswith("SD=/srv/pcbk/secrets; STU=10.9; docker run ")
    for bad in ("STU_NET=$(reboot)\n", "SECRETS_DIR=/srv/a b\n", "SECRETS_DIR=relative\n"):
        ssh = _Ssh(out='{"model": "pcbk/z-ai/glm-5.3"}', env=bad)
        with pytest.raises(StandError):
            _server(ssh, tmp_path).place_model(7)
        assert len(ssh.calls) == 1                                                  # до команд дело не дошло


def test_server_stand_place_model_and_callers(tmp_path):
    ssh = _Ssh(out='{"model": "pcbk/z-ai/glm-5.3", "provider": {}}')
    assert _server(ssh, tmp_path).place_model(7) == "pcbk/z-ai/glm-5.3"
    cmd = ssh.calls[-1][0][4]
    assert "--network pcbk-stu-07" in cmd and "$SD/student-07.pw:/pw:ro" in cmd
    assert '"http://$STU.7.3:4096/config"' in cmd
    assert "opencode:{{PW}}" in cmd and "curlimages/curl:8.16.0" in cmd
    ssh = _Ssh(out="student-07, student-08\n")
    assert _server(ssh, tmp_path).run_callers() == {"student-07", "student-08"}
    assert "LLM_RUN_CALLERS" in ssh.calls[-1][0][4]


def test_mcp_connected_after_later_start():
    core = {"StartedAt": "2026-10-02T10:00:00.123456789Z"}
    place = {"StartedAt": "2026-10-02T09:00:00Z",
             "Health": {"Log": [{"End": "2026-10-02T09:59:59Z", "Output": "200 mcp=connected"},
                                {"End": "2026-10-02T10:00:05Z", "Output": "200 mcp=failed"}]}}
    assert not mcp_connected(core, place)                                           # подключение — до старта core
    place["Health"]["Log"].append({"End": "2026-10-02T10:00:10Z", "Output": "200 mcp=connected"})
    assert mcp_connected(core, place)
    assert not mcp_connected(core, {"StartedAt": "2026-10-02T10:00:20Z", "Health": place["Health"]})


# --- фильтр сценариев (П14) и снимок /health/llm (m-р3-13, П12) ---

def test_plan_scenarios_filter():                                                # П14
    keys = plan_runs(cfg(places={7: M1, 8: M2}, scenarios=("B3",)))
    assert [(k.place, k.scenario, k.rep) for k in keys] == [(n, "B3", rep) for rep in (1, 2, 3) for n in (7, 8)]
    assert {k.scenario for k in plan_runs(cfg(places={7: M1}, scenarios=("A1", "C2")))} == {"A1", "C2"}


def test_prepare_skips_bench_agent_without_bench_scenarios():                   # П14: сухой прогон Б4 — только B3
    stand = FakeStand(save_answer={"status": "refused", "problems": [{"code": "tag_unknown"}]})
    r = runner(stand=stand, places={7: M1}, scenarios=("B3",))
    assert r.run() == "finished" and stand.bench_saves == []
    assert {rec.key.scenario for rec in load_records(r.out)} == {"B3"}
    stand = FakeStand()
    runner(stand=stand, places={7: M1}, scenarios=("B1",)).prepare()             # слой 0: B1 — нужен bench-uzel
    assert stand.bench_saves and stand.bench_saves[0][1]["name"] == "bench-uzel"


def test_health_end_snapshot(tmp_path):                                          # m-р3-13, П12: регрессия А5
    stand = FakeStand(health={"key_spent_usd": 12.5}, run={"spent_usd": 2.0})
    assert runner(out=tmp_path, stand=stand, only=[RunKey(7, "B1", 1)]).run() == "finished"
    snap = json.loads((tmp_path / "health_end.json").read_text())
    assert snap["key_spent_usd"] == 12.5 and snap["run"]["id"] == "d8-test"          # блок run не затёрт


def test_calc_rate_waits_whole_window_then_notes():                             # Б3: сверка не пропускается молча
    from pcbk_bench.runner import NOTE_CALC_RATE
    from pcbk_bench.stand import CALC_RATE_PAUSE_S, CALC_RATE_WINDOW_S, CALC_TRIES
    sleeps: list[float] = []
    rec = runner(stand=FakeStand(calc_rate=10), sleep=sleeps.append).run_one(RunKey(7, "B1", 1))
    assert rec.calc["tag_now"]["status"] == "ok" and sleeps.count(15.0) == 10      # больше трёх попыток прежнего
    assert NOTE_CALC_RATE not in rec.notes
    sleeps.clear()
    rec = runner(stand=FakeStand(calc_rate=10_000), sleep=sleeps.append).run_one(RunKey(7, "B1", 1))
    assert rec.status == "done" and rec.calc["tag_now"]["status"] == "rate" and NOTE_CALC_RATE in rec.notes
    assert sleeps.count(CALC_RATE_PAUSE_S) == CALC_TRIES - 1                       # паузы — на всё окно частоты
    assert (CALC_TRIES - 1) * CALC_RATE_PAUSE_S >= CALC_RATE_WINDOW_S


def test_calc_rate_respects_run_deadline():                                       # пауза не выходит за срок прогона
    clock = FakeClock()
    stand = FakeStand(calc_rate=10_000)
    r = runner(stand=stand, clock=clock.mono, sleep=clock.sleep)
    answer = r._calc("tag_now", {"tags": ["20FAKE_104_PV"]}, deadline=clock.mono() + 40.0)
    assert answer["status"] == "rate" and len(stand.calc_calls) == 3                 # 0, 15, 30 с; 45 с — за сроком
