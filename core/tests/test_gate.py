"""Ворота к историану: белый список литералов, полосы и места, срок, общий предел, защёлка входа."""
import asyncio
import time

import anyio
import pytest

from fakes import CLOCK_ROWS, WHITELIST, FakeHistorian
from helpers import FakeMono
from pcbk_core.data.gate import CONNECT_COOLDOWN_S, GateRefused, HistorianGate, SlidingWindow
from pcbk_core.data.historian import HistorianError
from pcbk_core.data.sql import catalog_sql, clock_sql, live_all_sql, live_sql
from pcbk_core.logs import setup_logging

pytestmark = pytest.mark.anyio
Q = live_sql(["20FAKE_001_PV"])
SLOW_Q = r"IN \('20FAKE_001_PV'\)$"          # медленны только запросы людей по одному тегу


def gate(fake, **kw):
    return HistorianGate(fake, allowed=WHITELIST, **kw)


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
    g = gate(fake, global_window=SlidingWindow(2, 300))
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
    g = gate(fake, deadline_s=2.5)
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
    fake = FakeHistorian(delay_s=0.2, fail=HistorianError("auth", "18456"))
    g = gate(fake, user_slots=1)
    rs = await asyncio.gather(*(g.run([Q]) for _ in range(3)), return_exceptions=True)
    assert [r.code for r in rs] == ["auth"] * 3 and len(fake.calls) == 1


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


async def test_driver_errors_carry_sent_and_log_no_detail(capfd):
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
                         "in_flight": {"user": 0, "background": 0}, "auth_latched": True}


def test_sliding_window_forgets_old_calls():
    w = SlidingWindow(2, 10.0)
    assert w.allow("k", 0.0) and w.allow("k", 1.0) and not w.allow("k", 5.0)
    assert w.allow("other", 5.0)                                         # ключи не делят окно
    assert w.allow("k", 10.5) and not w.allow("k", 10.6)                 # отказ не записывается
