"""Места шлюза (Д5-R27, Д5-R28): прокси сокета, замок места, пробуждение одно на место, «упало» по Restarting и
StartedAt, готовность, уборщик простоя с перепроверкой под замком. Места и sp-ctl — двойники httpx.MockTransport."""
import asyncio
import importlib
import logging
import re
import sys
import time
from datetime import datetime, timezone

import httpx
import pytest

from fake_places import FakeDocker, FakePlaces
from helpers import CORE_DIR, FakeMono, gateway_role, make_places, ready_place, wait_until_async
from pcbk_core.gateway import places
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
    await wait_until_async(lambda: ("stop", 3) in w.docker.calls)                      # уборщик взял замок, stop идёт
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
    await wait_until_async(lambda: any(r.url.path == "/session/status" for r in w.oc.requests))   # ждёт ответа
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
    w.mono.advance(w.gw.GW_REAPER_TICK_S + 1)                  # подтверждение устарело — пробуждение сверяет inspect
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
    await wait_until_async(lambda: docker.calls.count(("inspect", 3)) == 2)            # второй inspect ушёл
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


# --- доводка по ревью ---

async def test_bogus_session_status_does_not_break_tick(tmp_path, monkeypatch, caplog):   # M1
    oc = FakePlaces()
    oc.status_raw = {1: b'{"ses_x": {"type": ["busy"]}}',                           # тип — список, не строка
                     2: b"[" * 100000 + b"]" * 100000,                                 # глубже предела разбора JSON
                     3: b'[{"type": "busy"}]'}                                         # не объект
    w = make_places(tmp_path, FakeDocker(running={1, 2, 3, 4}), oc, GW_IDLE_STOP_S=60.0)
    await w.tick(); w.mono.advance(61); await w.tick()
    assert w.ticks == 2 and w.docker.stopped == [1, 2, 3, 4]                           # неверный ответ — «не занято»
    real = w._session_busy

    async def boom(n):                                                                 # защита в глубину: gather
        if n == 5:
            raise RuntimeError("не должно уйти из такта")
        return await real(n)
    monkeypatch.setattr(w, "_session_busy", boom)
    for n in (5, 6):
        w.docker.run(n)
    await w.tick(); w.mono.advance(61); await w.tick()
    assert w.ticks == 4 and w.docker.stopped == [1, 2, 3, 4, 6]
    assert "gw tick place=05 step=reap error=RuntimeError" in caplog.text
    assert "не должно уйти" not in caplog.text                                         # только класс


async def test_failed_stop_retried_with_backoff(tmp_path):                              # M2
    docker = FakeDocker(running={6}, stop_status=500)
    w = make_places(tmp_path, docker, GW_IDLE_STOP_S=240.0)                            # такт 30 с: паузы 60, 120, 240
    await w.tick(); w.mono.advance(240)
    for _ in range(20):                                                                # 600 с тактов по 30 с
        await w.tick(); w.mono.advance(30)
    assert [c for c in docker.calls if c == ("stop", 6)] == [("stop", 6)] * 4           # 0, +60, +180, +420 — не 20
    assert w.stats.count_1h("stop_failed") == 4
    assert 6 in w.running and not w.is_ready(6)                                        # работает, но не «готово»
    docker.stop_status = 204
    for _ in range(8):                                                                 # следующий повтор — +660 с
        await w.tick()
        if docker.stopped:
            break
        w.mono.advance(30)
    assert docker.stopped == [6] and 6 not in w.running
    assert w.stats.count_1h("stop_failed") == 4 and w.mono() - 1000 - 240 == 660        # ровно в разрешённый такт


async def test_attempt_deadlines_hold(tmp_path, monkeypatch):                          # M4: двойник сроков httpx не соблюдает
    for name in ("PING_TIMEOUT_S", "ACTION_TIMEOUT_S", "READY_ATTEMPT_S", "BUSY_CHECK_S"):
        monkeypatch.setattr(places, name, 0.1)

    async def silent(request):
        await asyncio.sleep(5)
        return httpx.Response(200)
    sp = SpCtl("http://pcbk-sp-ctl:2375", transport=httpx.MockTransport(silent))
    t = time.monotonic()
    assert not await sp.ping()
    for call in (sp.inspect, sp.start, sp.stop):
        with pytest.raises(SpCtlError) as e:
            await call(1)
        assert e.value.kind == "down"
    assert time.monotonic() - t < 1.5                                                   # 4 × 0,1 с, а не 4 × 5 с
    w = make_places(tmp_path, FakeDocker(running={2}), FakePlaces(status_delay=5), GW_IDLE_STOP_S=60.0)
    await w.tick(); w.mono.advance(61)
    t = time.monotonic()
    await w.tick()                                                                     # /session/status молчит
    assert time.monotonic() - t < 1.0 and w.docker.stopped == [2]
    w = make_places(tmp_path, oc=FakePlaces(agent_delay=5), GW_WAKE_TIMEOUT_S=1.0)
    t = time.monotonic()
    await w.wake(3)
    assert (await w.wait_ready(3)).detail == PLACE_TEXTS["timeout"] and time.monotonic() - t < 1.0


async def test_restore_waits_for_an_answered_inspect(tmp_path):                        # M5
    class RefusedInspect(FakeDocker):                                                  # ping жив, inspect — 500
        refuse = True

        async def __call__(self, request):
            if self.refuse and request.url.path.endswith("/json"):
                return httpx.Response(500)
            return await super().__call__(request)

    docker = RefusedInspect(running={2})
    w = make_places(tmp_path, docker)
    await w.tick()
    assert w.sp_ctl == "down" and not w.is_ready(2)             # ping жив, места не видны — «down» (R6)
    docker.refuse = False
    await w.tick()
    assert w.is_ready(2)                                                               # проход с ответом — первый


async def test_places_on_configured_net(tmp_path):                                      # M6
    w = make_places(tmp_path, STU_NET="10.20")
    await w.wake(3)
    assert (await w.wait_ready(3)).state == "ready" and w.oc.requests[-1].url.host == "10.20.3.3"
    w = make_places(tmp_path, STU_NET="10.20")
    w.oc.stu_net = "172.31"                                                            # место не в той сети
    await w.wake(3)
    assert (await w.wait_ready(3)).state == "failed" and w.oc.requests == []


async def test_set_ready_for_tests(tmp_path):                                           # хук тестов А4 и Д5б
    w = make_places(tmp_path)
    ready_place(w, w.docker, 4)
    assert w.is_ready(4) and 4 in w.running and w.docker.containers[4]["running"]
    w.mark_unready(4)
    w.set_ready_for_tests(4)                                                           # без StartedAt — возьмёт такт
    await w.tick()
    assert w.is_ready(4) and starts(w) == [] and w.oc.agent_calls[4] == 0              # первый такт не трогает
    w.docker.containers[4]["started_at"] = "2026-10-05T09:00:00.000000002Z"           # перезапуск извне
    await w.tick()
    assert not w.is_ready(4)                                                           # StartedAt взят из inspect


async def test_failed_stop_after_failed_wake_keeps_place(tmp_path):                    # M2 в _stop_failed_wake
    docker = FakeDocker(stop_status=500)
    w = make_places(tmp_path, docker, FakePlaces(never_ready=True), GW_WAKE_TIMEOUT_S=10.0, GW_IDLE_STOP_S=60.0)
    await w.wake(4)
    assert (await w.wait_ready(4)).detail == PLACE_TEXTS["timeout"]
    assert 4 in w.running and not w.is_ready(4) and w.stats.count_1h("stop_failed") == 1   # запущено нами — работает
    docker.stop_status = 204
    w.mono.advance(61)
    await w.tick()                                                                     # простой вышел, пауза (60 с) тоже
    assert docker.stopped == [4] and 4 not in w.running


# --- доводка итогового ревью Д5а (final-d5a-fix.md) ---

def place_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "pcbk_core.gateway.places"]


async def test_wake_on_fresh_ready_does_no_work(tmp_path, caplog):                     # M4, код (R5); M-1
    """Место готово, и inspect такта подтвердил это не дольше такта назад: 20 wake подряд — ни inspect, ни строки
    журнала, но +20 wake_ok в памяти и активность (touch). Подтверждение старше такта или снятый флаг — снова
    сверка inspect; её «готово» тоже подтверждение."""
    caplog.set_level(logging.INFO, logger="pcbk_core.gateway.places")
    w = make_places(tmp_path, FakeDocker(running={3}))
    await w.tick()                                                                     # «готово» вернул и подтвердил такт
    assert w.is_ready(3)
    inspects, lines = w.docker.calls.count(("inspect", 3)), len(place_lines(caplog))
    w.mono.advance(w.gw.GW_REAPER_TICK_S)                                              # ровно такт — ещё свежее
    for _ in range(20):
        assert (await w.wake(3)).state == "ready"
    assert (w.docker.calls.count(("inspect", 3)), len(place_lines(caplog))) == (inspects, lines)
    assert w.stats.count_1h("wake_ok") == 20 and w._tasks == {} and w.idle_s(3) == 0.0  # пробуждение — активность
    w.mono.advance(1)                                                                  # подтверждение старше такта
    await w.wake(3)
    assert (await w.wait_ready(3)).state == "ready" and w.docker.calls.count(("inspect", 3)) == inspects + 1
    assert w.stats.count_1h("wake_ok") == 21 and "gw wake place=03 result=ready" in "\n".join(place_lines(caplog))
    lines = len(place_lines(caplog))
    for _ in range(19):                                                                # подтвердило само пробуждение
        await w.wake(3)
    assert (w.docker.calls.count(("inspect", 3)), w.stats.count_1h("wake_ok")) == (inspects + 1, 40)
    assert len(place_lines(caplog)) == lines
    w.mark_unready(3)                                                                  # Д5б: место не ответило
    await w.wake(3)
    assert (await w.wait_ready(3)).state == "ready" and w.docker.calls.count(("inspect", 3)) == inspects + 2


class RefusingInspect(FakeDocker):
    """sp-ctl отвечает на ping, а inspect мест из refuse — 403 (правило прокси сломано или сменилось)."""
    refuse = frozenset(range(1, 11))

    async def __call__(self, request):
        m = re.search(r"pcbk-student-([0-9]{2})/json$", request.url.path)
        if m and int(m[1]) in self.refuse:
            return httpx.Response(403, json={"message": "запрещено прокси"})
        return await super().__call__(request)


async def test_inspect_refused_while_ping_alive_is_down(tmp_path, caplog):               # M5 (R6)
    """ping прошёл, а ни один inspect такта — нет: sp_ctl = "down" (строка сторожа — «нет связи с прокси сокета»,
    fail), короткий такт; один ответ из десяти — связь есть."""
    role = gateway_role(tmp_path, docker=RefusingInspect(running={2}))
    w, docker = role.workplaces, role.fake_docker
    await w.tick()
    assert ("ping", 0) in docker.calls and w.sp_ctl == "down" and w.next_tick_s() == 5.0
    assert role.health() == (False, "нет связи с прокси сокета — места не будятся")
    assert role.health_json(role.wallclock())["sp_ctl"] == "down"
    assert "gw sp-ctl down step=inspect" in caplog.text
    docker.refuse = frozenset(range(2, 11))                                            # место 01 видно
    await w.tick()
    assert w.sp_ctl == "ok" and role.health() == (True, "принимает вход")
    await w.aclose(); await w.spctl.aclose()


# --- остаток после повторного ревью R1–R9 ---

def watchdog_checks():
    """Правила сторожа (watchdog/pcbk_watchdog/checks.py, только stdlib) — из соседнего каталога репозитория,
    как тест моделей читает student/: стык с настоящим health_json без копии правил. Пакетом, а не отдельным файлом:
    с Д11а (А1) checks импортирует соседний модуль относительно (from .memory import …)."""
    root = str(CORE_DIR.parent / "watchdog")
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("pcbk_watchdog.checks")


async def test_long_session_ready_places_keep_row_from_fail(tmp_path):                  # M-1 (вариант «а»)
    """Долгое занятие: место 03 поднято давно (такт вернул и подтвердил «готово»), его страница будит его — быстрый
    путь; место 04 не создано и трижды не поднялось. Строка сторожа — warn «не поднялось по входу», а не fail
    «рабочие места не поднимаются»: быстрый путь считает wake_ok."""
    role = gateway_role(tmp_path, docker=FakeDocker(running={3}, missing={4}))
    w = role.workplaces
    await w.tick()
    for _ in range(3):
        assert (await w.wake(3)).state == "ready"
        await w.wake(4)
        assert (await w.wait_ready(4)).state == "failed"
    assert role.fake_docker.calls.count(("start", 3)) == 0                             # 03 не будилось заново
    checks = watchdog_checks()
    reply = checks.GatewayReply.parse(role.health_json(role.wallclock()))
    assert (reply.wake_ok_1h, reply.wake_failed_1h) == (3, 3)
    verdict = checks.gateway_verdict(reply, datetime.now(timezone.utc), 120)
    assert verdict == ("warn", "рабочее место не поднялось по входу", "3 за час"), verdict
    await w.aclose(); await w.spctl.aclose()


async def test_steady_tick_confirms_ready(tmp_path):                                    # M-3: подтверждение тактом
    """Подтверждение устарело, штатный такт (не первый проход) видит место готовым и подтверждает снова: N wake
    после него — без inspect."""
    w = make_places(tmp_path, FakeDocker(running={3}))
    await w.tick()                                                                     # первый проход — возврат «готово»
    w.mono.advance(w.gw.GW_REAPER_TICK_S + 1)                                          # подтверждение устарело
    await w.tick()                                                                     # штатный такт
    inspects = w.docker.calls.count(("inspect", 3))
    for _ in range(10):
        assert (await w.wake(3)).state == "ready"
    assert (w.docker.calls.count(("inspect", 3)), w.stats.count_1h("wake_ok")) == (inspects, 10)
    assert w._tasks == {}
