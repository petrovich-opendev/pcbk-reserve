# core/tests/test_gw_halt.py — Д11-R117 (I-3 р1, I-1 р2): сон мест прогона, отказ пробуждения и перезапуска
import asyncio

import pytest
from fastapi.testclient import TestClient

from fake_places import FakeDocker, FakePlaces
from helpers import ORIGIN_HEADERS, Gw, gateway_role, make_gateway, make_llm_role, wait_until, wait_until_async
from pcbk_core import gateway as gateway_module
from pcbk_core.gateway.api import GW_MESSAGES
from pcbk_core.gateway.places import PLACE_TEXTS, WakeRefused

# make_llm_role без ключа OpenRouter: роль llm в приложение шлюза не входит, сети нет
RUN = {"LLM_RUN_ID": "d11-test", "LLM_RUN_USD": 5.0, "LLM_RUN_CALLERS": "student-03,student-04"}


def stops(docker):
    return {n for action, n in docker.calls if action == "stop"}


@pytest.mark.anyio
async def test_halt_stops_every_run_place_without_running_set(tmp_path):
    docker = FakeDocker(running={3, 5})
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, docker=docker, llm=llm)
    llm.brake.halt("memory", avail_mib=900)
    await gw._halt_once()
    assert stops(docker) == {3, 4} and docker.containers[5]["running"] is True


@pytest.mark.anyio
async def test_late_start_put_to_sleep_next_tick(tmp_path):                 # I-1 р2
    docker = FakeDocker()
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, docker=docker, llm=llm)
    llm.brake.halt("memory", avail_mib=900)
    docker.run(3)                                                           # отменённая _wake успела start (или docker start мимо шлюза)
    await gw.workplaces.tick()                                              # такт уборщика под защёлкой
    assert 3 in stops(docker) and docker.containers[3]["running"] is False


@pytest.mark.anyio
async def test_restart_refused_and_wake_checks_before_start(tmp_path):
    docker = FakeDocker()
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, docker=docker, llm=llm)
    llm.brake.halt("memory", avail_mib=900)
    gw.workplaces.restart(3)                                                # воркер перезагрузки агентов: тихий отказ (m-6 р3)
    assert gw.workplaces._tasks.get(3) is None
    await gw.workplaces._wake(3)                                            # задача, поставленная до защёлки
    assert ("start", 3) not in docker.calls


def test_halted_wake_refused_for_student_and_teacher(tmp_path):
    llm = make_llm_role(tmp_path, key=None, **RUN)
    app, role = make_gateway(tmp_path, docker=FakeDocker(), llm=llm)
    llm.brake.halt("memory", avail_mib=900)
    g = Gw(app, role)                                                       # учётки: stu-03 (место 3), stu-05 (5), teach
    with g.as_("stu-03") as c:
        r = c.post("/pcbk/api/workplace/wake", headers=ORIGIN_HEADERS)
    assert (r.status_code, r.json()["data"]["message"]) == (503, GW_MESSAGES["run_halted"])
    with g.as_("teach") as c:
        assert c.post("/pcbk/api/teacher/places/03/wake", headers=ORIGIN_HEADERS).status_code == 503
    with g.as_("stu-05") as c:
        assert c.post("/pcbk/api/workplace/wake", headers=ORIGIN_HEADERS).status_code == 202


# --- сверх брифа ---

@pytest.mark.anyio
async def test_wake_refused_without_touch_or_task_and_others_untouched(tmp_path):
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, llm=llm)
    w = gw.workplaces
    assert w.halted() == frozenset()                                        # без защёлки — пусто
    llm.brake.halt("oom")
    assert w.halted() == frozenset({3, 4})                                  # ops — не место; 03 и 04 — места прогона
    with pytest.raises(WakeRefused) as e:
        await w.wake(4)
    assert e.value.kind == "run_halted" and w._tasks.get(4) is None and 4 not in w._touched
    view = await w.wake(5)                                                  # место вне прогона будится как прежде
    assert view.state == "starting"
    await w.wait_ready(5)
    assert w.is_ready(5)
    llm.brake.clear()
    assert w.halted() == frozenset()


@pytest.mark.anyio
async def test_cancelled_wake_does_not_start_and_place_text(tmp_path):     # задача ждала замок, когда встала защёлка
    docker = FakeDocker()
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, docker=docker, llm=llm)
    w = gw.workplaces
    async with w.lock(3):                                                    # замок места держит воркер перезагрузки
        await w.wake(3)
        await asyncio.sleep(0)
        llm.brake.halt("memory", avail_mib=900)
    await w.wait_ready(3)
    assert ("start", 3) not in docker.calls and w.view(3).detail == PLACE_TEXTS["run_halted"]
    assert gw.stats.count_1h("wake_failed") == 0                            # отказ тормоза — не сбой места


@pytest.mark.anyio
async def test_halt_once_cancels_waking_task(tmp_path):
    docker = FakeDocker()
    oc = FakePlaces(agent_delay=30.0)                                       # GET /agent висит: задача ждёт готовности
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, docker=docker, oc=oc, llm=llm)
    w = gw.workplaces
    await w.wake(3)
    await wait_until_async(lambda: oc.agent_calls[3] >= 1)                  # start уже ушёл, ждём готовности
    assert w.is_waking(3) and w._locks[3].locked()
    llm.brake.halt("psi")
    await asyncio.wait_for(gw._halt_once(), 5)                              # задача снята, замок не ждётся до срока
    assert not w.is_waking(3) and docker.containers[3]["running"] is False
    assert w.view(3).detail == PLACE_TEXTS["run_halted"] and 3 not in w.running


@pytest.mark.anyio
async def test_stop_now_failure_retried_next_tick(tmp_path):
    docker = FakeDocker(running={3}, stop_status=500)
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, docker=docker, llm=llm)
    llm.brake.halt("memory")
    await gw._halt_once()
    assert docker.containers[3]["running"] is True and gw.stats.count_1h("stop_failed") == 2    # места 03 и 04
    docker.stop_status = 204
    await gw.workplaces.tick()                                              # без паузы повтора: тормоз срочный
    assert docker.containers[3]["running"] is False and 3 not in gw.workplaces.running


@pytest.mark.anyio
async def test_tick_without_halt_does_not_stop_run_places(tmp_path):
    docker = FakeDocker(running={3})
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, docker=docker, llm=llm)
    await gw.workplaces.tick()
    assert "stop" not in {a for a, _ in docker.calls}


def test_restart_skipped_line(tmp_path, gwlog):
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, llm=llm)
    llm.brake.halt("memory")
    view = gw.workplaces.restart(4, broken=True)
    assert view.state == "sleeping" and "gw halt restart place=04 skipped" in gwlog.text


def test_halt_watch_under_lifespan(tmp_path, monkeypatch):                   # защёлка — места спят без такта уборщика
    monkeypatch.setattr(gateway_module, "HALT_WATCH_S", 0.01)
    docker = FakeDocker(running={3, 4, 5})
    llm = make_llm_role(tmp_path, key=None, **RUN)
    app, role = make_gateway(tmp_path, docker=docker, llm=llm, GW_REAPER_TICK_S=600.0)
    with TestClient(app):
        wait_until(lambda: role.workplaces.ticks >= 1)                       # первый такт прошёл, следующий — через 600 с
        assert stops(docker) == set()
        llm.brake.halt("swap")
        wait_until(lambda: not docker.containers[3]["running"] and not docker.containers[4]["running"])
    assert docker.containers[5]["running"] is True


def test_no_halt_watch_without_run(tmp_path):
    llm = make_llm_role(tmp_path, key=None)
    app, role = make_gateway(tmp_path, llm=llm)
    with TestClient(app):
        assert role._halt_task is None and role.workplaces.halted() == frozenset()


@pytest.mark.anyio
async def test_latch_during_inspect_checked_before_start(tmp_path):        # проверка — перед самим start, после await
    docker = FakeDocker()
    llm = make_llm_role(tmp_path, key=None, **RUN)
    gw = gateway_role(tmp_path, docker=docker, llm=llm)
    w = gw.workplaces
    real = w.spctl.inspect

    async def inspect_then_latch(n):
        info = await real(n)
        llm.brake.halt("memory")                                            # защёлка встала, пока шёл inspect
        return info

    w.spctl.inspect = inspect_then_latch
    await w.wake(3)
    await w.wait_ready(3)
    assert ("inspect", 3) in docker.calls and ("start", 3) not in docker.calls
    assert w.view(3).detail == PLACE_TEXTS["run_halted"]
