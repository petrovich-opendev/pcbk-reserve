"""Места шлюза (Д5-R27, Д5-R28): прокси сокета, замок места, пробуждение одно на место, «упало» по Restarting и
StartedAt, готовность, уборщик простоя с перепроверкой под замком. Места и sp-ctl — двойники httpx.MockTransport."""
import asyncio
import time

import httpx
import pytest

from fake_places import FakeDocker, FakePlaces
from helpers import FakeMono, make_places
from pcbk_core.gateway.places import PLACE_TEXTS, PlaceView, SpCtl, SpCtlError
from pcbk_core.gateway.stats import GatewayStats

pytestmark = pytest.mark.anyio


def starts(w):
    return [c for c in w.docker.calls if c[0] in ("start", "stop")]


async def test_wake_single_flight(tmp_path):                                           # Review Focus 3
    w = make_places(tmp_path, oc=FakePlaces(ready_after=3))
    v1, v2 = await asyncio.gather(w.wake(3), w.wake(3))
    assert (v1.state, v2.state) == ("starting", "starting")
    assert (await w.wait_ready(3)).state == "ready" and w.is_ready(3)          # холодный старт — не «упало»
    assert starts(w) == [("start", 3)] and w.oc.agent_calls[3] == 3
    assert all(r.headers["authorization"] == w.basic(3) for r in w.oc.requests)
    assert w.stats.count_1h("wake_ok") == 1


async def test_first_tick_restores_ready_in_parallel(tmp_path):                         # Д5-R27
    w = make_places(tmp_path, FakeDocker(running={3, 5}), FakePlaces(agent_delay=0.5))
    t = time.monotonic()
    await w.tick()                                                                     # первый такт
    assert w.is_ready(3) and w.is_ready(5) and w.ticks == 1
    assert time.monotonic() - t < 0.9                                                  # параллельно: подряд было бы 1,0 с
    assert starts(w) == []                                                             # не будит и не гасит


async def test_wake_failures_named_and_place_stopped(tmp_path):
    cases = [(FakeDocker(down=True), FakePlaces(), "down", False),
             (FakeDocker(start_status=409), FakePlaces(), "refused", False),
             (FakeDocker(missing={4}), FakePlaces(), "missing", False),
             (FakeDocker(), FakePlaces(never_ready=True), "timeout", True),
             (FakeDocker(), FakePlaces(wrong_pw=True), "place_auth", True),
             (FakeDocker(exit_on_start={4}), FakePlaces(never_ready=True), "crashed", True)]
    for docker, oc, kind, stop_expected in cases:
        w = make_places(tmp_path, docker, oc, GW_WAKE_TIMEOUT_S=10.0)
        await w.wake(4)
        await w.wait_ready(4)
        v = w.view(4)                                                                  # сразу, без такта
        assert (v.state, v.detail) == ("failed", PLACE_TEXTS[kind]), kind
        assert (("stop", 4) in docker.calls) is stop_expected, kind
        assert 4 not in w.running and w.stats.count_1h("wake_failed") == 1
        assert w.stats.count_1h("place_auth_failed") == (1 if kind == "place_auth" else 0)


async def test_no_password_file(tmp_path):
    w = make_places(tmp_path, GW_PLACE_PW_DIR=str(tmp_path / "нет"))
    await w.wake(2)
    assert (await w.wait_ready(2)).detail == PLACE_TEXTS["no_password"] and starts(w) == []


async def test_wake_during_reaper_stop(tmp_path):                                       # Review Focus 3
    w = make_places(tmp_path, FakeDocker(running={3}, stop_delay=0.2), GW_IDLE_STOP_S=60.0)
    await w.tick()                                                                     # место увидено работающим
    w.mono.advance(61)
    reaper = asyncio.create_task(w.tick())
    await asyncio.sleep(0.05)                                                          # уборщик взял замок, stop идёт
    assert not w.is_ready(3)                                                           # «готово» снято до stop
    await w.wake(3)
    await reaper
    assert (await w.wait_ready(3)).state == "ready"
    assert starts(w) == [("stop", 3), ("start", 3)] and w.stats.count_1h("wake_failed") == 0
    assert w.docker.containers[3]["running"] and 3 in w.running                        # start после stop, не 304 до него


async def test_reaper_rechecks_idle_under_lock(tmp_path):                               # Review Focus 3
    w = make_places(tmp_path, FakeDocker(running={5}), FakePlaces(status_delay=0.2), GW_IDLE_STOP_S=60.0)
    await w.tick()
    w.mono.advance(61)
    reaper = asyncio.create_task(w.tick())                                             # отбор: место 5 простаивает
    await asyncio.sleep(0.05)                                                          # уборщик ждёт /session/status
    w.touch(5)                                                                         # действие студента в этом окне
    await reaper
    assert ("stop", 5) not in w.docker.calls and w.is_ready(5)


async def test_reaper_stops_idle_not_busy_and_poll_is_not_activity(tmp_path):         # Review Focus 3
    w = make_places(tmp_path, FakeDocker(running={1, 2, 3}), FakePlaces(busy={2}), GW_IDLE_STOP_S=1800.0)
    await w.tick()                                                                     # шлюз увидел места — отсчёт отсюда
    w.mono.advance(1799); await w.tick()
    assert w.docker.stopped == []
    w.touch(3); w.mono.advance(2); w.view(1)                  # опрос состояния — не активность
    await w.tick()
    assert w.docker.stopped == [1]                             # 2 занято, 3 тронуто 2 с назад
    w.mono.advance(1800); await w.tick()
    assert w.docker.stopped == [1, 2, 3]                       # занятое — не дольше двойного срока


async def test_first_tick_restores_ready_and_external_stop_clears_it(tmp_path):
    w = make_places(tmp_path, FakeDocker(running={2}))
    await w.tick()
    assert w.is_ready(2)                                       # после перезапуска core вкладки не получат 503
    w.docker.containers[2]["running"] = False                  # остановлено извне
    await w.tick()
    assert not w.is_ready(2) and w.view(2).state == "sleeping"


async def test_hung_sp_ctl_short_tick(tmp_path):
    w = make_places(tmp_path, FakeDocker(running={1}, hang=True))
    await w.tick()
    assert w.sp_ctl == "down" and w.checked_at is not None
    assert [c for c in w.docker.calls if c[0] == "inspect"] == []


async def test_next_tick_short_while_sp_ctl_down(tmp_path):                          # Ш10
    docker = FakeDocker(running={1}, down=True)
    w = make_places(tmp_path, docker)
    await w.tick()
    assert w.sp_ctl == "down" and w.next_tick_s() == 5.0                               # GW_REAPER_TICK_S = 30
    docker.down = False                                                                # адрес появился в кэше охраны
    await w.tick()
    assert w.sp_ctl == "ok" and w.next_tick_s() == 30.0
    assert w.is_ready(1)                                       # «готово» вернул первый проход с живым sp-ctl


async def test_stop_failure_counted(tmp_path):
    w = make_places(tmp_path, FakeDocker(running={6}, stop_status=500), GW_IDLE_STOP_S=60.0)
    await w.tick(); w.mono.advance(61); await w.tick()
    assert w.stats.count_1h("stop_failed") == 1


def test_place_name_checked():
    with pytest.raises(ValueError):
        SpCtl("http://pcbk-sp-ctl:2375").path(11, "start")    # имя вне диапазона — не в путь


# --- сверх брифа: договоры, которые тесты выше не задевают ---

async def test_running_place_without_flag_woken_by_304(tmp_path):                      # Д5-R27: без перезапуска
    w = make_places(tmp_path, FakeDocker(running={7}))
    before = w.docker.containers[7]["started_at"]
    await w.wake(7)
    assert (await w.wait_ready(7)).state == "ready"
    assert starts(w) == [("start", 7)] and w.docker.containers[7]["started_at"] == before
    await w.wake(7)                                                                    # «готово» при том же StartedAt
    assert (await w.wait_ready(7)).to_json() == {"place": "07", "state": "ready", "detail": PLACE_TEXTS["ready"]}
    assert starts(w) == [("start", 7)] and w.stats.count_1h("wake_ok") == 2


async def test_stale_tick_inspect_keeps_fresh_ready(tmp_path):                        # такт вне замка
    class SlowInspect(FakeDocker):                                                     # ответ о месте 3 — с опозданием
        delay = 0.0

        async def __call__(self, request):
            response = await super().__call__(request)                                 # состояние на миг запроса
            if self.delay and request.url.path.endswith("pcbk-student-03/json"):
                delay, self.delay = self.delay, 0.0
                await asyncio.sleep(delay)
            return response

    docker = SlowInspect()
    w = make_places(tmp_path, docker)
    await w.tick()
    docker.delay = 0.3
    ticking = asyncio.create_task(w.tick())                                            # inspect: «место 3 стоит»
    await asyncio.sleep(0.05)
    await w.wake(3)
    assert (await w.wait_ready(3)).state == "ready"                                    # пока такт ждёт ответа
    await ticking
    assert w.is_ready(3) and 3 in w.running                                            # запоздалый inspect не снял


async def test_crash_by_changed_started_at(tmp_path):                                   # «упало» без Restarting
    docker = FakeDocker()

    class Restarted(FakePlaces):                                                       # Docker перезапустил место
        async def __call__(self, request):
            docker.containers[8]["started_at"] = "2026-10-05T09:00:00.000000001Z"
            return await super().__call__(request)

    w = make_places(tmp_path, docker, Restarted(never_ready=True))
    await w.wake(8)
    v = await w.wait_ready(8)
    assert (v.state, v.detail) == ("failed", PLACE_TEXTS["crashed"]) and ("stop", 8) in docker.calls


async def test_bad_place_pw_drill(tmp_path):                                            # Д5-R29: пароль портит шлюз
    w = make_places(tmp_path, DRILL_GATEWAY="bad_place_pw")
    assert w.basic(4) != make_places(tmp_path).basic(4)
    await w.wake(4)
    assert (await w.wait_ready(4)).detail == PLACE_TEXTS["place_auth"]
    assert w.stats.count_1h("place_auth_failed") == 1


async def test_spctl_codes(tmp_path):
    docker = FakeDocker(running={1}, missing={9})
    sp = SpCtl("http://pcbk-sp-ctl:2375", transport=httpx.MockTransport(docker))
    assert await sp.ping() and sp.path(2, "inspect") == "/v1.44/containers/pcbk-student-02/json"
    assert (await sp.start(1), await sp.start(2)) == (304, 204)
    assert await sp.inspect(9) is None
    for n, status, kind in ((9, 204, "missing"), (3, 403, "refused"), (3, 500, "refused")):
        docker.start_status = status
        with pytest.raises(SpCtlError) as e:
            await sp.start(n)
        assert e.value.kind == kind
    docker.down = True
    assert not await sp.ping()
    with pytest.raises(SpCtlError) as e:
        await sp.stop(1)
    assert e.value.kind == "down"
    await sp.aclose()


def test_stats_window_and_unknown_kind():
    mono = FakeMono(1000.0)
    s = GatewayStats(monotonic=mono)
    s.event("wake_ok"); mono.advance(3599); s.event("wake_ok")
    assert s.count_1h("wake_ok") == 2 and s.count_1h("stop_failed") == 0
    mono.advance(2)
    assert s.count_1h("wake_ok") == 1                                                   # окно 3600 с
    with pytest.raises(ValueError):
        s.event("wake_okk")                                                            # опечатка не проходит молча


def test_place_view_json():
    assert PlaceView(3, "sleeping", PLACE_TEXTS["sleeping"]).to_json() == {
        "place": "03", "state": "sleeping", "detail": "Рабочее место спит"}
