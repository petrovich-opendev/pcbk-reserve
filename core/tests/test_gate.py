"""Ворота к историану: белый список литералов, полосы и места, срок, общий предел, защёлка входа."""
import asyncio
import time
from datetime import datetime

import anyio
import pytds
import pytest

from fakes import CLOCK_ROWS, WHITELIST, FakeHistorian, RecordingConnect
from helpers import FakeMono
from pcbk_core.data.gate import (AUTH_LATCH_TEXT, CONNECT_COOLDOWN_S, P95_MIN_CALLS, PAUSE_AFTER_TIMEOUTS,
                                 STATS_WINDOW_S, USER_PAUSE_S, GateRefused, HistorianGate, SlidingWindow)
from pcbk_core.data.historian import HistorianError, tds_query
from pcbk_core.secrets import BdrvConfig
from pcbk_core.data.sql import allowed_literal, catalog_sql, clock_sql, lit_dt, live_all_sql, live_sql, summary_sql
from pcbk_core.logs import setup_logging

pytestmark = pytest.mark.anyio
Q = live_sql(["20FAKE_001_PV"])
SLOW_Q = r"IN \('20FAKE_001_PV'\)$"          # медленны только запросы людей по одному тегу


def gate(fake, **kw):
    return HistorianGate(fake, allowed=WHITELIST, **kw)


async def warmed(fake, **kw):
    """Ворота после первого удачного входа: как у службы, где первым идёт каталог."""
    g = gate(fake, **kw)
    await g.run([clock_sql()], lane="background")
    return g


async def test_gate_caps_user_concurrency_and_returns_sent():
    fake = FakeHistorian(delay_s=0.2)
    g = gate(fake)
    rs = await asyncio.gather(*(g.run([live_sql([n])]) for n in sorted(WHITELIST)[:5]))
    assert fake.max_concurrency == 2 and rs[0].sent == (sorted(WHITELIST)[0],)
    assert g.stats()["sent_names_total"] == 5


async def test_gate_refuses_foreign_literals_without_sql():
    fake = FakeHistorian()
    g = gate(fake)
    for sql in (live_sql(["16FAKE_009_PV"]),                                # имя вне списка
                "SELECT TagName FROM Live WHERE TagName = '20FAKE_001_PV'",  # имя из списка, но мимо IN
                "SELECT TagName FROM Live WHERE TagName = '16FAKE_009_PV'"):
        with pytest.raises(GateRefused) as e:
            await g.run([sql])
        assert e.value.code == "unlisted"
    assert fake.calls == [] and g.stats()["refused_unlisted"] == 3


async def test_gate_refuses_nameless_user_sql():                                # Review Focus 3
    fake = FakeHistorian()
    g = gate(fake)
    for sql in (live_all_sql(), catalog_sql()):
        with pytest.raises(GateRefused) as e:
            await g.run([sql])                                              # полоса людей
        assert e.value.code == "unlisted"
    assert fake.calls == []
    await g.run([live_all_sql()], lane="background")                          # фону — можно
    await g.run([clock_sql()])                                                # часы — любой полосе


async def test_gate_deadline_returns_timeout_sent_and_keeps_slot():             # Review Focus 2
    fake = FakeHistorian(delay_s=1.0)
    g = gate(fake, deadline_s=0.3)
    t = time.monotonic()
    with pytest.raises(HistorianError) as e:
        await g.run([Q])
    assert e.value.code == "timeout" and e.value.sent == ("20FAKE_001_PV",) and time.monotonic() - t < 0.5
    assert fake.timeouts[-1] <= 0.3                                         # драйвер ждёт не дольше срока
    assert g.stats()["in_flight"]["user"] == 1
    await anyio.sleep(0.9)
    assert g.stats()["in_flight"]["user"] == 0


async def test_q_min_scales_with_short_deadline():
    g = gate(FakeHistorian(), deadline_s=0.3)                                # q_min = min(5, 0,1)
    assert (await g.run([Q])).sent == ("20FAKE_001_PV",)


async def test_gate_late_acquire_is_busy_not_run():                             # Review Focus 2
    fake = FakeHistorian(delay_s=0.9)
    g = gate(fake, user_slots=1, q_min_s=0.4)
    first = asyncio.ensure_future(g.run([Q]))
    await anyio.sleep(0.05)
    with pytest.raises(GateRefused) as e:          # место свободно только через 0,85 с: с запасом 0,4 не успеть
        await g.run([Q], deadline_s=1.2)
    assert e.value.code == "busy" and len(fake.calls) == 1
    await first


async def test_background_lane_not_starved():                                   # Review Focus 2
    fake = FakeHistorian(delay_s=1.0, slow=SLOW_Q)
    g = await warmed(fake, global_window=SlidingWindow(2, 300))          # до первого входа — по одному
    users = [asyncio.ensure_future(g.run([Q])) for _ in range(2)]
    await anyio.sleep(0.05)
    t = time.monotonic()
    r = await g.run([clock_sql()], lane="background")
    assert time.monotonic() - t < 0.3 and g.stats()["in_flight"]["user"] == 2   # фон не ждал мест людей
    assert r.rows == [CLOCK_ROWS]
    await asyncio.gather(*users)
    with pytest.raises(GateRefused) as e:
        await g.run([Q])                                                     # окно людей выбрано
    assert e.value.code == "rate"


async def test_background_survives_twenty_waiting_users():                     # Review Focus 2
    fake = FakeHistorian(delay_s=1.0, slow=SLOW_Q)
    g = await warmed(fake, deadline_s=2.5)
    users = [asyncio.ensure_future(g.run([Q])) for _ in range(20)]         # 18 ждут места, не потоки
    await anyio.sleep(0.05)
    t = time.monotonic()
    await g.run([clock_sql()], lane="background")
    assert time.monotonic() - t < 0.3
    await asyncio.gather(*users, return_exceptions=True)


async def test_gate_total_concurrency_is_three():                              # Review Focus 2
    fake = FakeHistorian(delay_s=0.3)
    g = gate(fake)
    async def timed(lane):
        t = time.monotonic(); await g.run([clock_sql()] if lane == "background" else [Q], lane=lane)
        return time.monotonic() - t
    users = [asyncio.ensure_future(timed("user")) for _ in range(3)]
    backs = [asyncio.ensure_future(timed("background")) for _ in range(2)]
    await asyncio.gather(*users, *backs)
    assert fake.max_concurrency == 3
    assert max(b.result() for b in backs) >= 0.55                            # второй фоновый ждал своё место


async def test_global_window_counts_only_user_after_acquire():
    fake = FakeHistorian()
    g = gate(fake, global_window=SlidingWindow(2, 300), monotonic=lambda: 1.0)
    for _ in range(3):
        await g.run([clock_sql()], lane="background")
    await g.run([Q]); await g.run([Q])
    with pytest.raises(GateRefused) as e:
        await g.run([Q])
    assert e.value.code == "rate" and len(fake.calls) == 5


async def test_auth_error_latches_until_restart():                              # Review Focus 1
    mono = FakeMono(1000.0)
    fake = FakeHistorian(fail=HistorianError("auth", "18456"))
    g = gate(fake, monotonic=mono)
    for step in range(3):
        with pytest.raises(HistorianError) as e:
            await g.run([clock_sql()], lane="background")
        assert e.value.code == "auth"
        mono.advance(10 ** 6)                                               # время защёлку не снимает
    assert len(fake.calls) == 1 and g.stats()["auth_latched"] is True


async def test_sql_browser_login_error_pauses_not_latches():                  # ревью: LoginError — не пароль
    mono = FakeMono(1000.0)
    rec = RecordingConnect(fail_on_connect=pytds.LoginError("Instance INST not found on server h"))
    g = gate(tds_query(BdrvConfig("h\\INST", None, "Runtime", "u", "p"), connect=rec), monotonic=mono)
    with pytest.raises(HistorianError) as e:
        await g.run([clock_sql()], lane="background")
    assert e.value.code == "connect" and g.stats()["auth_latched"] is False
    with pytest.raises(HistorianError) as e:
        await g.run([clock_sql()], lane="background")
    assert e.value.detail == "пауза после обрыва" and rec.connect_count == 1
    mono.advance(CONNECT_COOLDOWN_S)
    rec.fail_on_connect, rec.results = None, [CLOCK_ROWS]
    assert (await g.run([clock_sql()], lane="background")).rows == [CLOCK_ROWS]
    assert rec.connect_count == 2 and g.stats()["auth_latched"] is False


async def test_connect_failure_pauses_attempts():
    mono = FakeMono(1000.0)
    fake = FakeHistorian(fail=HistorianError("connect", "refused"))
    g = gate(fake, monotonic=mono)
    for _ in range(2):
        with pytest.raises(HistorianError):
            await g.run([clock_sql()])
    assert len(fake.calls) == 1
    mono.advance(CONNECT_COOLDOWN_S)
    fake.fail = None
    await g.run([clock_sql()])
    assert len(fake.calls) == 2


# сверх брифа: очередь за местом не входит после отказа входа, разбор литералов не сбить,
# ошибки драйвера несут sent, в журнал не уходит текст SQL Server


async def test_waiting_calls_do_not_log_in_after_auth_failure():               # Review Focus 1
    fake = FakeHistorian()
    g = await warmed(fake, user_slots=1)                 # вход уже удавался: очередь только за местом
    fake.delay_s, fake.fail = 0.2, HistorianError("auth", "18456")
    rs = await asyncio.gather(*(g.run([Q]) for _ in range(3)), return_exceptions=True)
    assert [r.code for r in rs] == ["auth"] * 3 and len(fake.calls) == 2


async def test_gate_refuses_what_breaks_literal_parsing():
    fake = FakeHistorian()
    g = gate(fake)
    for sql in (Q + " /* ' */", Q + " -- '", Q + "; SELECT 1", 'SELECT TagName FROM Live WHERE TagName IN ("16FAKE_009_PV")',
                "SELECT TagName FROM Live WHERE TagName NOT IN ('20FAKE_001_PV')"):
        with pytest.raises(GateRefused) as e:
            await g.run([sql], lane="background")
        assert e.value.code == "unlisted"
    assert fake.calls == [] and g.stats()["refused_unlisted"] == 5


async def test_literal_ok_does_not_open_names_and_names_do_not_open_literals():
    fake = FakeHistorian()
    g = HistorianGate(fake, allowed=WHITELIST, literal_ok=lambda text: text == "16FAKE_009_PV")
    with pytest.raises(GateRefused):
        await g.run([live_sql(["16FAKE_009_PV"])])                     # имя в IN — только из белого списка
    with pytest.raises(GateRefused):                                     # имя вне IN — только через literal_ok
        await g.run([Q + " AND Description = '20FAKE_002_SP'"])
    assert fake.calls == []


async def test_driver_errors_carry_sent_and_log_no_detail(capfd, restore_logging):
    setup_logging()                                    # обработчик берёт sys.stderr в момент записи
    fake = FakeHistorian(fail=HistorianError("query", "Invalid object name '20FAKE_001_PV' for user 'FAKEUSER'"))
    g = gate(fake)
    with pytest.raises(HistorianError) as e:
        await g.run([live_sql(["20FAKE_001_PV", "20FAKE_002_SP"]), live_sql(["20FAKE_001_PV"])])
    assert e.value.code == "query" and e.value.sent == ("20FAKE_001_PV", "20FAKE_002_SP")
    fake.fail = HistorianError("auth", "Login failed for user 'FAKEUSER'", kind="OperationalError", msg_no=18456)
    with pytest.raises(HistorianError):
        await g.run([clock_sql()])
    with pytest.raises(GateRefused):
        await g.run([live_sql(["16FAKE_009_PV"])])
    err = capfd.readouterr().err
    assert "FAKEUSER" not in err and "16FAKE" not in err
    assert "msg_no=18456" in err and "ворота: SQL вне белого списка — отказ без SQL" in err
    assert g.stats() == {"sent_names_total": 2, "refused_unlisted": 1,
                         "in_flight": {"user": 0, "background": 0}, "auth_latched": True,
                         "p95_ms": None, "timeouts_5m": 0, "busy_5m": 0, "user_paused": False}


def test_sliding_window_forgets_old_calls():
    w = SlidingWindow(2, 10.0)
    assert w.allow("k", 0.0) and w.allow("k", 1.0) and not w.allow("k", 5.0)
    assert w.allow("other", 5.0)                                         # ключи не делят окно
    assert w.allow("k", 10.5) and not w.allow("k", 10.6)                 # отказ не записывается


# ревью задачи 3: форма инструкции, один вход до первой удачи, место до окна, текст защёлки

BYPASSES = [
    "SELECT TagName, DateTime, Value, Quality FROM Live WHERE NOT TagName IN ('20FAKE_001_PV')",
    Q + " OR 1 = 1",
    Q + " OR TagName = CHAR(49)",
    Q + " SELECT TagName, DateTime, Value, Quality FROM Live WHERE Value IS NOT NULL",   # вторая инструкция без ;
    Q + " -- x",
    Q + " /* x */",
]


@pytest.mark.parametrize("lane", ["user", "background"])
async def test_gate_refuses_statements_outside_template_forms(lane):
    fake = FakeHistorian()
    g = gate(fake)
    for sql in BYPASSES:
        with pytest.raises(GateRefused) as e:
            await g.run([sql], lane=lane)
        assert e.value.code == "unlisted"
    with pytest.raises(GateRefused):
        await g.run([clock_sql(), Q + " OR 1 = 1"], lane=lane)          # одна чужая инструкция — отказ всему
    assert fake.calls == [] and g.stats()["refused_unlisted"] == len(BYPASSES) + 1


async def test_first_login_is_single_until_success():                          # учётка общая с Dify
    fake = FakeHistorian(delay_s=0.2, fail=HistorianError("auth", "18456"))
    g = gate(fake)
    rs = await asyncio.gather(g.run([Q]), g.run([Q]), g.run([clock_sql()], lane="background"),
                              return_exceptions=True)
    assert [r.code for r in rs] == ["auth"] * 3 and len(fake.calls) == 1


async def test_first_login_waits_for_driver_not_deadline():
    fake = FakeHistorian(delay_s=0.7)
    g = gate(fake)
    with pytest.raises(HistorianError):
        await g.run([Q], deadline_s=0.3)                 # срок вышел, а вход ещё идёт
    with pytest.raises(GateRefused) as e:
        await g.run([Q], deadline_s=0.3)                 # второй вход не начинается, пока первый не вернулся
    assert e.value.code == "busy" and len(fake.calls) == 1
    await anyio.sleep(0.3)
    fake.delay_s = 0.0
    await g.run([Q])
    assert len(fake.calls) == 2


async def test_busy_call_does_not_spend_window():
    fake = FakeHistorian(delay_s=0.9)
    g = gate(fake, user_slots=1, q_min_s=0.4, global_window=SlidingWindow(2, 300))
    first = asyncio.ensure_future(g.run([Q]))
    await anyio.sleep(0.05)
    with pytest.raises(GateRefused) as e:
        await g.run([Q], deadline_s=1.2)
    assert e.value.code == "busy"
    await first
    fake.delay_s = 0.0
    await g.run([Q])                                     # второе место окна — этому вызову, не отказанному
    with pytest.raises(GateRefused) as e:
        await g.run([Q])
    assert e.value.code == "rate"


async def test_waiting_calls_recheck_connect_pause():
    fake = FakeHistorian()
    g = await warmed(fake, user_slots=1)
    fake.delay_s, fake.fail = 0.2, HistorianError("connect", "refused")
    rs = await asyncio.gather(*(g.run([Q]) for _ in range(3)), return_exceptions=True)
    assert [r.code for r in rs] == ["connect"] * 3 and len(fake.calls) == 2
    assert [r.detail for r in rs[1:]] == ["пауза после обрыва"] * 2


async def test_latch_text_names_both_causes():
    fake = FakeHistorian(fail=HistorianError("auth", "18456"))
    g = gate(fake)
    with pytest.raises(HistorianError):
        await g.run([clock_sql()])
    with pytest.raises(HistorianError) as e:
        await g.run([clock_sql()])
    assert AUTH_LATCH_TEXT in e.value.detail
    assert "обновите bdrv.env или проверьте, не была ли база историана недоступна" in AUTH_LATCH_TEXT


# Д3б, задача 2: сводка — даты и ww-значения через literal_ok, форма summary в реестре (F1)

T6, T14 = datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 14)          # те же, что в test_sql.py


@pytest.mark.anyio
async def test_gate_passes_summary_literals_only():
    g = HistorianGate(FakeHistorian(), allowed=WHITELIST, literal_ok=allowed_literal)
    assert (await g.run([summary_sql(["20FAKE_001_PV"], T6, T14)])).sent == ("20FAKE_001_PV",)
    assert g.stats()["refused_unlisted"] == 0                    # форма summary зарегистрирована (F1)
    with pytest.raises(GateRefused):
        await g.run(["SELECT TagName FROM AnalogHistory WHERE TagName = '20FAKE_001_PV' AND wwRetrievalMode = 'Delta'"])
    with pytest.raises(GateRefused):                              # имя вне списка и в верной форме
        await g.run([summary_sql(["16FAKE_009_PV"], T6, T14)])


async def test_gate_refuses_summary_with_non_ascii_digits():                   # ревью I1
    fake = FakeHistorian()
    g = HistorianGate(fake, allowed=WHITELIST, literal_ok=allowed_literal)
    good = summary_sql(["20FAKE_001_PV"], T6, T14)
    start = lit_dt(T6)
    # год и вторая цифра секунд — там strptime берёт любые цифры; форма на \d — тоже
    bads = [good.replace(start, start.replace("2026", "٢٠٢٦", 1).replace(":00.000", ":0٥.000")),
            good.replace(start, start.replace("2026", "２０２６", 1))]
    assert all(bad != good for bad in bads)
    for bad in bads:
        with pytest.raises(GateRefused) as e:
            await g.run([bad])
        assert e.value.code == "unlisted"
    assert fake.calls == [] and g.stats()["refused_unlisted"] == 2


# Д4б, задача 1: нагрузка историана — p95 и пауза полосы людей после сроков подряд (Д4б-R22, К8)

async def test_three_timeouts_pause_user_lane_not_background():
    mono = FakeMono(1000.0)
    fake = FakeHistorian(fail=HistorianError("timeout", "долго"))
    g = gate(fake, monotonic=mono)
    for _ in range(PAUSE_AFTER_TIMEOUTS):
        with pytest.raises(HistorianError):
            await g.run([Q])
    sent = len(fake.calls)
    with pytest.raises(GateRefused) as e:
        await g.run([Q])
    assert e.value.code == "busy" and len(fake.calls) == sent and g.stats()["user_paused"] is True
    fake.fail = None
    await g.run([clock_sql()], lane="background")                              # фон на паузе не стоит
    mono.advance(USER_PAUSE_S)
    await g.run([Q])
    assert g.stats()["user_paused"] is False and len(fake.calls) == sent + 2


async def test_p95_needs_five_calls():
    g = gate(FakeHistorian(delay_s=0.05))
    for _ in range(4):
        await g.run([Q])
    assert g.stats()["p95_ms"] is None
    await g.run([Q])
    assert 40 <= g.stats()["p95_ms"] <= 1000


# сверх брифа: пауза — после белого списка, счёт за окно, «подряд» и полоса, ожидание места в p95


async def test_pause_keeps_unlisted_and_counts_in_window():                    # К8
    mono = FakeMono(1000.0)
    fake = FakeHistorian(fail=HistorianError("timeout", "долго"))
    g = gate(fake, monotonic=mono)
    for _ in range(PAUSE_AFTER_TIMEOUTS):
        with pytest.raises(HistorianError):
            await g.run([Q])
    with pytest.raises(GateRefused) as e:
        await g.run([live_sql(["16FAKE_009_PV"])])                         # вне списка — unlisted и на паузе
    assert e.value.code == "unlisted"
    with pytest.raises(GateRefused) as e:
        await g.run([Q])
    assert e.value.code == "busy" and len(fake.calls) == PAUSE_AFTER_TIMEOUTS
    s = g.stats()
    assert (s["timeouts_5m"], s["busy_5m"], s["user_paused"], s["p95_ms"]) == (3, 1, True, None)  # дошли 3 из 5
    mono.advance(STATS_WINDOW_S)                                           # окно ушло: ни сроков, ни p95
    s = g.stats()
    assert (s["timeouts_5m"], s["busy_5m"], s["user_paused"], s["p95_ms"]) == (0, 0, False, None)


async def test_timeouts_must_be_in_a_row_on_user_lane():
    mono = FakeMono(1000.0)
    fake = FakeHistorian(fail=HistorianError("timeout", "долго"))
    g = gate(fake, monotonic=mono)
    for _ in range(PAUSE_AFTER_TIMEOUTS):
        with pytest.raises(HistorianError):
            await g.run([clock_sql()], lane="background")                  # фон паузы не знает
    assert (g.stats()["user_paused"], g.stats()["timeouts_5m"]) == (False, 0)
    timeout, query = fake.fail, HistorianError("query", "x")
    for fail in (timeout, timeout, None, timeout, timeout, query, timeout, timeout):
        fake.fail = fail                                                   # удача и ошибка обнуляют счёт
        try:
            await g.run([Q])
        except HistorianError:
            pass
    assert g.stats()["user_paused"] is False and g.stats()["timeouts_5m"] == 6
    fake.fail = timeout
    with pytest.raises(HistorianError):
        await g.run([Q])                                                   # третий подряд
    assert g.stats()["user_paused"] is True


async def test_p95_counts_wait_for_slot():                                     # К8: это видит студент
    fake = FakeHistorian(delay_s=0.1)
    g = await warmed(fake, user_slots=1)
    await asyncio.gather(*(g.run([Q]) for _ in range(P95_MIN_CALLS)))      # по одному: последний ждал 0,4 с
    assert g.stats()["p95_ms"] >= 450


# решения контроллера по вопросам задачи 1: счёт сроков и p95 — только по вызовам, дошедшим до драйвера


async def test_busy_refusals_between_timeouts_do_not_prevent_pause():
    fake = FakeHistorian()
    g = await warmed(fake, user_slots=1)
    fake.delay_s, fake.fail = 0.5, HistorianError("timeout", "долго")
    order: list[str] = []

    async def holder():                                  # держит место 0,5 с и получает срок от драйвера
        with pytest.raises(HistorianError) as e:
            await g.run([Q])
        order.append(e.value.code)

    async def late():                                    # не дождался места: busy без SQL между сроками
        await anyio.sleep(0.05)
        with pytest.raises(GateRefused) as e:
            await g.run([Q], deadline_s=0.3)
        order.append(e.value.code)

    for _ in range(PAUSE_AFTER_TIMEOUTS):
        assert g.stats()["user_paused"] is False
        await asyncio.gather(holder(), late())
    assert order == ["busy", "timeout"] * PAUSE_AFTER_TIMEOUTS
    s = g.stats()
    assert (s["user_paused"], s["timeouts_5m"], s["busy_5m"]) == (True, 3, 3)
    sent = len(fake.calls)
    with pytest.raises(GateRefused) as e:
        await g.run([Q])
    assert e.value.code == "busy" and len(fake.calls) == sent


async def test_p95_only_calls_that_reached_driver():
    fake = FakeHistorian(delay_s=0.1)
    g = gate(fake)
    for _ in range(P95_MIN_CALLS - 1):
        await g.run([Q])
    for _ in range(100):                                 # мгновенные отказы без SQL
        with pytest.raises(GateRefused):
            await g.run([live_sql(["16FAKE_009_PV"])])
    assert g.stats()["p95_ms"] is None                   # до драйвера дошли только четыре
    await g.run([Q])
    assert g.stats()["p95_ms"] >= 90                     # сто отказов по 0 мс его не тянут вниз


# доводка по ревью задачи 1: ждущий места после третьего срока не входит (I1), завис историан — срок ворот (M3)


@pytest.mark.parametrize("hung", [False, True], ids=["driver_timeout", "gate_deadline"])
async def test_caller_waiting_for_slot_at_third_timeout_gets_busy(hung):        # I1
    fake = FakeHistorian()
    g = await warmed(fake, user_slots=1)
    fake.fail = HistorianError("timeout", "долго")
    for _ in range(PAUSE_AFTER_TIMEOUTS - 1):
        with pytest.raises(HistorianError):
            await g.run([Q])
    # третий срок: его отдаёт сам драйвер через 0,3 с или ставят ворота (0,5 с), пока драйвер висит 1 с
    fake.delay_s, fake.fail = (1.0, None) if hung else (0.3, fake.fail)
    third = asyncio.ensure_future(g.run([Q], deadline_s=0.5 if hung else None))
    await anyio.sleep(0.05)
    sent = len(fake.calls)
    with pytest.raises(GateRefused) as e:
        await g.run([Q])                                 # ждёт места, пока его держит третий
    assert e.value.code == "busy" and len(fake.calls) == sent
    with pytest.raises(HistorianError) as e:
        await third
    assert e.value.code == "timeout" and g.stats()["user_paused"] is True


async def test_hung_historian_deadline_timeouts_count_toward_pause():         # M3
    fake = FakeHistorian()
    g = await warmed(fake, user_slots=PAUSE_AFTER_TIMEOUTS)
    fake.delay_s = 1.0                                   # драйвер дольше срока: срок ставят сами ворота
    for _ in range(PAUSE_AFTER_TIMEOUTS):
        t = time.monotonic()
        with pytest.raises(HistorianError) as e:
            await g.run([Q], deadline_s=0.3)
        assert e.value.code == "timeout" and time.monotonic() - t < 0.6
    s = g.stats()
    assert (s["user_paused"], s["timeouts_5m"], s["in_flight"]["user"]) == (True, PAUSE_AFTER_TIMEOUTS,
                                                                           PAUSE_AFTER_TIMEOUTS)
    sent = len(fake.calls)
    with pytest.raises(GateRefused) as e:
        await g.run([Q])
    assert e.value.code == "busy" and len(fake.calls) == sent
