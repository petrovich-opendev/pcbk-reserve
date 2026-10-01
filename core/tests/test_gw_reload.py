"""Перезагрузка рабочего места после конца хода (задача А5 Д7а, Д7-R58): очередь agent_reloads без спящих мест, ворота
prompt_async (читатели и писатель), крючок готовности, пульс воркера, учения freeze_reload, ключи pending,
pending_oldest_s и checked_at строки «Агенты студентов». Места и sp-ctl — двойники Д5а.

Фикстуры (предпроверка m9): фоновый run() воркера в них не идёт — такты ведёт тест сам. env и env_sync_async — без
lifespan; env_sync — под TestClient, воркер припаркован (helpers.park_reload_worker), первый такт уборщика позади.
Часы стены — FakeClock(NOW) у воркера и у роли (pending_oldest_s в /health/agents), монотонные — FakeMono роли."""
import asyncio
import logging
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from helpers import (AGENT_DRAFT, ORIGIN, PW, SETTINGS, agent_text, gateway_role, make_agents_root, make_gateway,
                     oc_headers, park_reload_worker, ready_place, wait_until, wait_until_async)
from pcbk_core.agents.store import AgentStore, PendingReload, StoreError
from pcbk_core.app import create_app
from pcbk_core.gateway import reload
from pcbk_core.gateway.places import PLACE_CONNECTIONS, PROXY_PARALLEL
from pcbk_core.gateway.reload import (DIR_CHECK_S, IDLE_SETTLE_S, RELOAD_DELAYED_S, RELOAD_RETRY_S, RELOAD_TICK_S,
                                      TURN_START_GRACE_S, VISIBLE_TRIES, AgentReloads)
from pcbk_core.gateway.rules import EXAMPLE_IDS
from pcbk_core.gateway.stats import MAX_STREAMS_PER_LOGIN

AGENT = {"name": "proverka", "native": False, "mode": "primary"}
NOW = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)
FRONT = ("172.31.250.35", 40000)            # edge в pcbk-front (как в test_gw_agents_health)
BASE = "https://stand.test:8443"            # ORIGIN шлюза тестов: cookie Secure клиент шлёт
SES = EXAMPLE_IDS["ses"]


class FakeClock:
    """Часы стены теста: вызываемые, идут только по advance(s)."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, s: float) -> None:
        self.now += timedelta(seconds=s)


async def quick_sleep(s: float) -> None:
    """Пауза воркера между попытками GET /agent: только отдать цикл."""
    await asyncio.sleep(0)


def _build_env(tmp_path, *, ready=(3,), save=True, sleep=quick_sleep, agents_root=None, **gw) -> SimpleNamespace:
    """Роль шлюза на двойниках (helpers.gateway_role), учёт агентов открыт, воркер — на часах теста; при save — строка
    saved «proverka» места 3 и метка очереди через store.save. Приложение — без lifespan; health() — GET /health/agents
    из зоны front."""
    role = gateway_role(tmp_path, ready=ready, agents_root=agents_root, **gw)
    clock = FakeClock(NOW)
    role.wallclock = clock                  # pending_oldest_s в /health/agents — по часам теста
    store = role.agents
    assert store.open(), store.last_error
    reloads = AgentReloads(role.workplaces, store, role.agent_stats, monotonic=role.mono, wallclock=clock, sleep=sleep,
                           drill=role.gw.DRILL_AGENTS)
    role.reloads = reloads
    role.workplaces.on_ready = reloads.place_ready
    if save:
        draft = replace(AGENT_DRAFT, name="proverka")
        assert store.save(3, draft, agent_text(draft), NOW) == "saved"
    app = create_app(SETTINGS, [role])
    app.state.zone_names_ok = True

    async def health() -> dict:
        transport = httpx.ASGITransport(app=app, client=FRONT)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.get("/health/agents")
        assert r.status_code == 200
        return r.json()

    return SimpleNamespace(role=role, app=app, oc=role.fake_places, docker=role.fake_docker, mono=role.mono,
                           clock=clock, store=store, db=Path(store.db_path), root=store.root, stats=role.agent_stats,
                           reloads=reloads, health=health)


def _spy_pending(env, monkeypatch) -> None:
    """env.pending_calls — по единице на вызов store.pending() (m25: часы стены теста стоят, checked_at — не мерило)."""
    env.pending_calls = []
    real = env.store.pending
    monkeypatch.setattr(env.store, "pending", lambda: env.pending_calls.append(1) or real())


@pytest.fixture
def env(tmp_path):
    return _build_env(tmp_path)


@pytest.fixture
def env_sync_async(tmp_path):
    """env с помощником health(); без lifespan — фонового воркера нет (m9)."""
    return _build_env(tmp_path)


@pytest.fixture
def env_sync(tmp_path):
    """То же окружение под TestClient из зоны front: get — GET приложения, run(coro) — корутина в цикле TestClient.
    Воркер припаркован (m9): такты ведёт тест; первый такт уборщика позади."""
    e = _build_env(tmp_path)
    park_reload_worker(e.role)
    with TestClient(e.app, client=FRONT) as c:
        wait_until(lambda: e.role.workplaces.ticks >= 1)
        e.get = c.get
        e.run = lambda coro: c.portal.call(lambda: coro)
        yield e


@pytest.fixture
def env_freeze(tmp_path, monkeypatch):
    """DRILL_AGENTS=freeze_reload, RELOAD_TICK_S = 0,05 и настоящие паузы; шпион store.pending (m25)."""
    monkeypatch.setattr(reload, "RELOAD_TICK_S", 0.05)
    e = _build_env(tmp_path, sleep=asyncio.sleep, DRILL_AGENTS="freeze_reload")
    _spy_pending(e, monkeypatch)
    return e


@pytest.fixture
def env_freeze_empty(tmp_path, monkeypatch):
    """То же с пустым учётом."""
    monkeypatch.setattr(reload, "RELOAD_TICK_S", 0.05)
    e = _build_env(tmp_path, save=False, sleep=asyncio.sleep, DRILL_AGENTS="freeze_reload")
    _spy_pending(e, monkeypatch)
    return e


@pytest.fixture
def env_ticking(tmp_path, monkeypatch):
    """Отрицательный контроль учений: без DRILL_AGENTS, RELOAD_TICK_S = 0,05, шпион store.pending."""
    monkeypatch.setattr(reload, "RELOAD_TICK_S", 0.05)
    e = _build_env(tmp_path, sleep=asyncio.sleep)
    _spy_pending(e, monkeypatch)
    return e


@pytest.fixture
def env_fresh(tmp_path):
    """Место 3 работает в FakeDocker, но уборщик ещё не проходил (ticks == 0) и «готово» у шлюза нет; очередь места 3 —
    метка администратора (reload --place 3), строк агентов нет."""
    e = _build_env(tmp_path, ready=(), save=False)
    e.docker.run(3)
    e.store.mark_reload(3, NOW)
    assert e.role.workplaces.ticks == 0 and not e.role.workplaces.is_ready(3)
    return e


@pytest.fixture
async def client_a(env):
    """httpx.AsyncClient к приложению шлюза с cookie учётки места 3 (stu-03) и id сессии OpenCode."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url=BASE) as client:
        r = await client.post("/pcbk/api/auth/login", json={"login": "stu-03", "password": PW},
                              headers={"Origin": ORIGIN})
        assert r.status_code == 200, r.status_code
        yield client, SES
    await env.role.login_gate.drain()


def place_requests(oc, n):
    return [r for r in oc.requests if r.url.host == f"172.31.{n}.3"]


async def settle(env):                       # первый такт видит пустую карту, второй — через IDLE_SETTLE_S
    await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1); await env.reloads.tick()


def prompt_of(client, sid):
    return lambda: client.post(f"/pcbk/api/oc/session/{sid}/prompt_async", headers=oc_headers(),
                               json={"parts": [{"type": "text", "text": "x"}]})


# --- тесты брифа ---

@pytest.mark.anyio
async def test_reload_after_idle_marks_visible(env):
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(TURN_START_GRACE_S + 0.1)
    await settle(env)
    assert env.oc.dispose_calls[3] == 1 and env.store.list_place(3)[0].state == "visible"
    assert env.store.pending() == {} and env.stats.count_1h("visible") == 1 and env.reloads.checked_at is not None


@pytest.mark.anyio
async def test_idle_must_settle(env):                                       # m15
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(TURN_START_GRACE_S + 0.1)
    await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 0                                     # пустая карта только что
    env.mono.advance(IDLE_SETTLE_S - 1.0); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 0
    env.mono.advance(1.1); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_no_reload_while_busy(env):
    env.oc.busy.add(3); env.mono.advance(10)
    await settle(env)
    assert env.oc.dispose_calls[3] == 0 and env.store.pending() != {}
    env.oc.busy.discard(3); env.oc.agents_after_dispose[3] = [AGENT]
    await settle(env)
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_no_reload_within_grace(env):
    env.mono.advance(10); env.reloads.note_turn_start(3)
    await env.reloads.tick()                                                # П11: пустая карта сразу после хода —
    env.mono.advance(TURN_START_GRACE_S - 0.5)                              # успокоение прошло, отсрочка — нет
    await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 0
    env.mono.advance(IDLE_SETTLE_S); env.oc.agents_after_dispose[3] = [AGENT]
    await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_prompt_waits_for_gate(env, client_a, monkeypatch):
    client, sid = client_a; prompt = prompt_of(client, sid)
    assert env.reloads.gates.try_reload(3)                                  # идёт перезагрузка
    task = asyncio.create_task(prompt())
    await asyncio.sleep(0.2)
    assert not task.done() and not [r for r in place_requests(env.oc, 3) if r.url.path.endswith("/prompt_async")]
    env.reloads.gates.end_reload(3)
    assert (await task).status_code == 204
    assert env.reloads.gates.try_reload(3)
    monkeypatch.setattr("pcbk_core.gateway.reload.RELOAD_WAIT_S", 0.3)
    r = await prompt()
    assert (r.status_code, r.json()["name"], r.json()["data"]["message"]) == (
        503, "WorkplaceBusy", "Обновляю список агентов рабочего места — повторите через несколько секунд")
    assert env.role.stats.count_1h("refused") == 0
    env.reloads.gates.end_reload(3)


@pytest.mark.anyio
async def test_two_prompts_do_not_block_each_other(env, client_a, monkeypatch):   # I1: две вкладки, перезагрузки нет
    client, sid = client_a; prompt = prompt_of(client, sid)
    monkeypatch.setattr("pcbk_core.gateway.reload.RELOAD_WAIT_S", 0.3)    # П12: замок-читатель дал бы 503 второй
    env.oc.hang_for[3] = 1.0                                                # ответ места на prompt_async — 1 с
    r1, r2 = await asyncio.gather(prompt(), prompt())
    assert (r1.status_code, r2.status_code) == (204, 204)
    assert env.oc.max_active[3] == 2                                        # П12: обе пересылки у места разом


@pytest.mark.anyio
async def test_gate_wait_not_in_move_deadline(env, client_a, monkeypatch):  # I1: H1 цел
    client, sid = client_a; prompt = prompt_of(client, sid)
    monkeypatch.setattr("pcbk_core.gateway.proxy.PROXY_DEADLINE_S", 1.0)
    monkeypatch.setattr("pcbk_core.gateway.reload.RELOAD_WAIT_S", 3.0)
    assert env.reloads.gates.try_reload(3)
    task = asyncio.create_task(prompt()); await asyncio.sleep(1.5)          # ворота дольше срока хода
    env.reloads.gates.end_reload(3)
    assert (await task).status_code == 204                                  # срок хода начался после ворот


@pytest.mark.anyio
async def test_reload_yields_to_waiting_prompt(env):                       # m2
    gates, done = env.reloads.gates, asyncio.Event()

    async def reader():
        async with gates.turn(3):
            await done.wait()
    assert gates.try_reload(3)
    task = asyncio.create_task(reader()); await asyncio.sleep(0)
    assert gates.waiting(3) == 1
    gates.end_reload(3)
    assert not gates.try_reload(3)              # без await: читатель ещё не проснулся, но он впереди
    await asyncio.sleep(0); assert gates.active(3) == 1
    done.set(); await task
    assert gates.try_reload(3)


@pytest.mark.anyio
async def test_unready_running_place_skipped_not_counted(env):
    env.role.workplaces.mark_unready(3); env.mono.advance(10)
    await settle(env)
    assert place_requests(env.oc, 3) == [] and env.store.pending() != {}
    assert env.reloads.pending_view(NOW) == (0, None)


@pytest.mark.anyio
async def test_asleep_place_stays_queued_not_counted(env):                 # I-р3-2
    env.docker.containers[3]["running"] = False; await env.role.workplaces.tick()   # уборщик увидел остановку
    await env.reloads.tick()
    assert 3 in env.store.pending() and env.store.list_place(3)[0].state == "saved"
    assert place_requests(env.oc, 3) == [] and env.reloads.pending_view(NOW) == (0, None)


@pytest.mark.anyio
async def test_saved_rows_checked_after_wake(env):                         # Б2: крючок готовности
    env.docker.containers[3]["running"] = False; await env.role.workplaces.tick(); await env.reloads.tick()
    since = env.store.pending()[3].since; env.clock.advance(3600)
    env.oc.agents_after_dispose[3] = [AGENT]
    await env.role.workplaces.wake(3); await env.role.workplaces.wait_ready(3)
    await wait_until_async(lambda: env.store.pending()[3].since > since)    # m-н6: since — от пробуждения
    env.mono.advance(10); await settle(env)
    assert env.store.list_place(3)[0].state == "visible" and env.store.pending() == {}


def test_admin_archive_sleeping_place(env_sync):                           # I-р3-2: метка цела, строка ok
    env_sync.docker.containers[3]["running"] = False; env_sync.run(env_sync.role.workplaces.tick())
    env_sync.store.archive(3, None, NOW)                                    # archive ставит место в очередь
    env_sync.run(env_sync.reloads.tick())
    body = env_sync.get("/health/agents").json()
    assert (body["pending"], body["pending_oldest_s"]) == (0, None) and 3 in env_sync.store.pending()
    env_sync.run(env_sync.role.workplaces.wake(3)); env_sync.run(env_sync.role.workplaces.wait_ready(3))
    env_sync.mono.advance(10); env_sync.run(settle(env_sync))               # такт дожидается крючка готовности (m8)
    assert env_sync.oc.dispose_calls[3] == 1 and env_sync.store.pending() == {}   # архивный агент ушёл из кэша


@pytest.mark.anyio
async def test_admin_reload_without_expected_names(env):                   # reload администратора: dispose всё равно
    env.store.archive(3, None, NOW); env.store.mark_reload(3, NOW); env.mono.advance(10)
    await settle(env)
    assert env.oc.dispose_calls[3] == 1 and env.store.pending() == {}


@pytest.mark.anyio
async def test_not_visible_after_tries(env):
    env.oc.agents_after_dispose[3] = []; env.mono.advance(10)
    await settle(env)
    assert env.store.list_place(3)[0].state == "not_visible" and env.store.pending() == {}
    assert env.stats.count_1h("not_visible") == 1


@pytest.mark.anyio
async def test_dispose_failure_retried_later(env):
    env.oc.dispose_status[3] = 500; env.mono.advance(10)
    await settle(env)
    assert env.stats.count_1h("reload_failed") == 1 and env.oc.dispose_calls[3] == 1
    del env.oc.dispose_status[3]; env.mono.advance(RELOAD_RETRY_S - 1)
    await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 1                                     # пауза до повтора
    env.mono.advance(2); env.oc.agents_after_dispose[3] = [AGENT]
    await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 2 and env.store.list_place(3)[0].state == "visible"


@pytest.mark.anyio
async def test_delayed_counted_once(env):
    env.oc.busy.add(3); env.clock.advance(RELOAD_DELAYED_S + 1); env.mono.advance(RELOAD_DELAYED_S + 1)
    await env.reloads.tick(); await env.reloads.tick()
    assert env.stats.count_1h("reload_delayed") == 1


@pytest.mark.anyio
async def test_queue_survives_restart(env):
    fresh = AgentReloads(env.role.workplaces, AgentStore(env.db, env.root), env.stats, monotonic=env.mono)
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    await fresh.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1); await fresh.tick()
    assert env.store.list_place(3)[0].state == "visible"


@pytest.mark.anyio
async def test_heartbeat_with_empty_queue(env):                           # Б-н1: пульс без очереди (удача)
    env.store.archive(3, None, NOW); env.store.reload_done(3, env.store.pending()[3].seq)
    assert env.store.pending() == {}
    await env.reloads.tick(); first = env.reloads.checked_at
    assert first is not None
    env.clock.advance(5); await env.reloads.tick()
    assert env.reloads.checked_at != first


@pytest.mark.anyio
async def test_spctl_silent_first_tick_keeps_mark(env_fresh):              # I-р3-2: core запущен, sp-ctl молчит
    env_fresh.docker.down = True
    await env_fresh.role.workplaces.tick()                                  # sp_ctl «down», running пуст
    assert env_fresh.role.workplaces.sp_ctl == "down" and env_fresh.role.workplaces.running == set()
    await env_fresh.reloads.tick(); await env_fresh.reloads.tick()
    assert 3 in env_fresh.store.pending()                                   # метка администратора цела
    env_fresh.docker.down = False; env_fresh.oc.agents_after_dispose[3] = []
    await env_fresh.role.workplaces.tick()                                  # возврат «готово» (_restore)
    await wait_until_async(lambda: env_fresh.role.workplaces.is_ready(3))
    env_fresh.mono.advance(10); await settle(env_fresh)                     # такт дожидается крючка готовности (m8)
    assert env_fresh.oc.dispose_calls[3] == 1 and env_fresh.store.pending() == {}


@pytest.mark.anyio
async def test_mark_during_reload_not_lost(env):                           # I-н2б
    env.oc.agents_after_dispose[3] = [AGENT]
    env.oc.on_dispose = lambda n: env.store.mark_reload(n, NOW)             # admin reload во время перезагрузки
    env.mono.advance(10); await settle(env)
    assert env.oc.dispose_calls[3] == 1 and 3 in env.store.pending()
    env.oc.on_dispose = None; env.mono.advance(IDLE_SETTLE_S + 0.1); await settle(env)
    assert env.oc.dispose_calls[3] == 2 and env.store.pending() == {}


@pytest.mark.anyio
async def test_place_lock_held_during_dispose(env):                        # m-р3-5
    seen = []
    env.oc.on_dispose = lambda n: seen.append(env.role.workplaces.lock(n).locked())
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10); await settle(env)
    assert seen == [True] and not env.role.workplaces.lock(3).locked()


@pytest.mark.anyio
async def test_waiting_released_on_cancel(env):                            # m-р3-4
    gates = env.reloads.gates
    assert gates.try_reload(3)

    async def reader():
        async with gates.turn(3):
            pass
    task = asyncio.create_task(reader()); await asyncio.sleep(0)
    assert gates.waiting(3) == 1
    task.cancel(); await asyncio.gather(task, return_exceptions=True)
    assert gates.waiting(3) == 0
    gates.end_reload(3); assert gates.try_reload(3)


@pytest.mark.anyio
async def test_store_failure_flags_row(env_sync_async, monkeypatch):       # I-р3-1
    env = env_sync_async
    with monkeypatch.context() as m:                                        # m10: undo снял бы и подмены фикстур
        m.setattr(env.store, "pending", lambda: (_ for _ in ()).throw(StoreError("disk")))
        await env.reloads.tick()
        assert env.reloads.checked_at is None and env.stats.worker_store_error is not None
        env.stats.store_ok()                                                # роль «данные» записала удачно
        body = await env.health()
        assert body["store_ok"] is False                                    # флаг воркера не стёрт
    await env.reloads.tick()
    assert env.reloads.checked_at is not None and (await env.health())["store_ok"] is True


@pytest.mark.anyio
async def test_store_error_on_step_g_no_redispose(env, monkeypatch):       # I-р3-1
    env.oc.agents_after_dispose[3] = [AGENT]
    with monkeypatch.context() as m:                                        # m10
        m.setattr(env.store, "reload_done", lambda *a: (_ for _ in ()).throw(StoreError("disk")))
        env.mono.advance(10); await settle(env)
        assert env.oc.dispose_calls[3] == 1 and env.stats.count_1h("reload_failed") == 1
        for _ in range(3):
            env.mono.advance(RELOAD_TICK_S); await env.reloads.tick()
        assert env.oc.dispose_calls[3] == 1                                 # не каждые 2 с
    env.mono.advance(RELOAD_RETRY_S); await settle(env)
    assert env.oc.dispose_calls[3] == 2 and env.store.pending() == {}


@pytest.mark.anyio
async def test_no_dispose_while_place_lock_held(env):                      # m-н12
    env.mono.advance(10); await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1)
    async with env.role.workplaces.lock(3):                                 # идёт пробуждение или остановка
        await asyncio.wait_for(env.reloads.tick(), 2)                       # m4: ждать замок такт не должен
    assert env.oc.dispose_calls[3] == 0


@pytest.mark.anyio
async def test_prompt_after_gate_rechecks_ready(env, client_a):            # m-н4
    client, sid = client_a; prompt = prompt_of(client, sid)
    assert env.reloads.gates.try_reload(3)
    task = asyncio.create_task(prompt()); await asyncio.sleep(0.1)
    env.role.workplaces.mark_unready(3); env.reloads.gates.end_reload(3)    # место ушло в перезапуск
    r = await task
    assert (r.status_code, r.json()["name"]) == (503, "WorkplaceNotReady")


@pytest.mark.anyio
async def test_heartbeat_after_pending_and_per_place(env, monkeypatch):    # I-р3-1, m3 (вместо прежнего m3)
    calls, stamps = [], []
    real = env.store.pending
    monkeypatch.setattr(env.store, "pending", lambda: calls.append(env.reloads.checked_at) or real())

    async def place_pass(n, pending):                                       # каждое место «идёт» 20 с
        stamps.append(env.reloads.checked_at); env.clock.advance(20)
    monkeypatch.setattr(env.reloads, "_place_pass", place_pass)
    for n in (5, 6):
        env.store.mark_reload(n, NOW); ready_place(env.role.workplaces, env.docker, n)
    await env.reloads.tick()
    assert calls == [None]                                                  # пульс — после удачного pending()
    assert len(stamps) == 3 and None not in stamps and len(set(stamps)) == 3     # и перед каждым местом


def test_place_connections_include_worker():                               # m4
    assert PLACE_CONNECTIONS == MAX_STREAMS_PER_LOGIN + PROXY_PARALLEL + 2


def test_health_reports_pending(env_sync):                                 # TestClient: GET /health/agents
    env_sync.run(env_sync.reloads.tick())                                   # m9: воркер припаркован — такт ведёт тест
    body = env_sync.get("/health/agents").json()
    assert (body["pending"], body["pending_oldest_s"] is not None) == (1, True)


@pytest.mark.anyio
async def test_freeze_drill(env_freeze):
    task = asyncio.create_task(env_freeze.reloads.run()); await asyncio.sleep(0.3)
    first = env_freeze.reloads.checked_at; await asyncio.sleep(0.3)
    assert first is not None and env_freeze.reloads.checked_at == first
    assert len(env_freeze.pending_calls) == 1                              # m25: после первого прохода — ни одного
    task.cancel(); await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_freeze_drill_empty_queue(env_freeze_empty):                 # Б-н1: учения и без очереди
    task = asyncio.create_task(env_freeze_empty.reloads.run()); await asyncio.sleep(0.3)
    first = env_freeze_empty.reloads.checked_at; await asyncio.sleep(0.3)
    assert first is not None and env_freeze_empty.reloads.checked_at == first
    assert len(env_freeze_empty.pending_calls) == 1                        # m25
    task.cancel(); await asyncio.gather(task, return_exceptions=True)


# --- сверх брифа ---

@pytest.mark.anyio
async def test_run_ticks_without_drill(env_ticking):                       # отрицательный контроль учений (m25)
    task = asyncio.create_task(env_ticking.reloads.run()); await asyncio.sleep(0.3)
    task.cancel(); await asyncio.gather(task, return_exceptions=True)
    assert len(env_ticking.pending_calls) >= 2                             # без учений проходы идут дальше первого


@pytest.mark.anyio
async def test_grace_counts_from_turn_end(env, client_a):                  # П11: отсрочка — от ответа места
    client, sid = client_a
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    env.oc.hang_for[3] = 0.3                                                # место отвечает на prompt_async медленно
    task = asyncio.create_task(prompt_of(client, sid)())
    await wait_until_async(lambda: env.oc.active[3] == 1)                  # пересылка у места
    env.mono.advance(TURN_START_GRACE_S + IDLE_SETTLE_S + 1)                # вызов идёт дольше отсрочки
    assert (await task).status_code == 204
    env.oc.hang_for[3] = 0
    await settle(env)
    assert env.oc.dispose_calls[3] == 0                                     # 204 отдан, busy места ещё мог не стоять
    env.mono.advance(TURN_START_GRACE_S); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_turn_restarts_idle_settle(env, client_a):                   # П11: пустая карта до хода не в счёт
    client, sid = client_a
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    await env.reloads.tick()                                                # пустая карта замечена до хода
    env.mono.advance(IDLE_SETTLE_S + 1)
    assert (await prompt_of(client, sid)()).status_code == 204              # короткий ход между тактами
    env.mono.advance(TURN_START_GRACE_S + 0.1); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 0                                     # успокоение — заново от хода
    env.mono.advance(IDLE_SETTLE_S + 0.1); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_busy_under_gate_aborts_reload(env, monkeypatch):            # шаг а: проверка под воротами
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1)
    real, statuses = env.oc._answer, []

    async def answer(request, n):
        if request.url.path == "/session/status":
            statuses.append(n)
            if len(statuses) == 2:                                          # ход начался между проверками
                env.oc.busy.add(3)
        return await real(request, n)
    monkeypatch.setattr(env.oc, "_answer", answer)
    await asyncio.wait_for(env.reloads.tick(), 2)                           # m4
    assert len(statuses) == 2 and env.oc.dispose_calls[3] == 0
    assert env.reloads.gates.try_reload(3) and not env.role.workplaces.lock(3).locked()    # ворота и замок отпущены
    env.reloads.gates.end_reload(3)
    env.oc.busy.discard(3); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 0                                     # занятость сняла и пустую карту
    env.mono.advance(IDLE_SETTLE_S + 0.1); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_unknown_status_is_not_idle(env):                            # свой разбор /session/status: «не знаю»
    env.oc.status_raw[3] = b"[]"; env.mono.advance(10)
    await settle(env)
    assert env.oc.dispose_calls[3] == 0
    env.oc.status_raw[3] = b'{"ses_x": {"type": "weird"}}'                  # непустая карта без busy — не пустая
    await settle(env)
    assert env.oc.dispose_calls[3] == 0
    del env.oc.status_raw[3]; env.oc.agents_after_dispose[3] = [AGENT]
    await settle(env)
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_agent_list_unanswered_is_failure(env):                      # шаг в: не 200 за все попытки
    env.oc.check_status[3] = 503; env.mono.advance(10)
    await settle(env)
    agent_calls = [r for r in place_requests(env.oc, 3) if r.path == "/agent"]
    assert env.oc.dispose_calls[3] == 1 and len(agent_calls) == VISIBLE_TRIES
    assert env.stats.count_1h("reload_failed") == 1 and env.store.list_place(3)[0].state == "saved"
    assert 3 in env.store.pending() and not env.role.workplaces.lock(3).locked()


@pytest.mark.anyio
async def test_config_error_after_dispose_is_failure(env, monkeypatch):    # шаг в: repair до А6 — заглушка not_config
    real = env.oc._answer

    async def answer(request, n):
        if request.url.path == "/agent" and env.oc.dispose_calls[n]:
            return httpx.Response(400, json={"name": "ConfigInvalidError",
                                             "data": {"path": "/etc/pcbk-opencode/opencode/agents/proverka.md"}})
        return await real(request, n)
    monkeypatch.setattr(env.oc, "_answer", answer)
    assert await env.reloads.repair(3, b"{}") == "not_config"
    env.mono.advance(10); await settle(env)
    agent_calls = [r for r in place_requests(env.oc, 3) if r.path == "/agent"]
    assert env.oc.dispose_calls[3] == 1 and len(agent_calls) == 1          # конфигурация сломана — без повторов
    assert env.stats.count_1h("reload_failed") == 1 and env.stats.count_1h("quarantined") == 0
    assert env.store.list_place(3)[0].state == "saved" and 3 in env.store.pending()


@pytest.mark.anyio
async def test_exception_restarts_settle(env, monkeypatch):                # m7: исключение снимает idle_since
    env.oc.agents_after_dispose[3] = [AGENT]
    with monkeypatch.context() as m:
        m.setattr(env.store, "mark_visible", lambda *a: (_ for _ in ()).throw(StoreError("disk")))
        env.mono.advance(10); await settle(env)
        assert env.oc.dispose_calls[3] == 1 and env.stats.worker_store_error is not None
        assert env.stats.count_1h("reload_failed") == 1
    env.mono.advance(RELOAD_RETRY_S + 0.1); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 1                                     # пустая карта — заново
    env.mono.advance(IDLE_SETTLE_S + 0.1); await env.reloads.tick()
    assert env.oc.dispose_calls[3] == 2 and env.store.pending() == {} and env.stats.worker_store_error is None


@pytest.mark.anyio
async def test_not_visible_rechecked_on_next_reload(env):                  # not_visible оживает следующей перезагрузкой
    env.oc.agents_after_dispose[3] = []; env.mono.advance(10); await settle(env)
    assert env.store.list_place(3)[0].state == "not_visible"
    env.store.mark_reload(3, NOW); env.oc.agents_after_dispose[3] = [AGENT]
    env.mono.advance(IDLE_SETTLE_S + 0.1); await settle(env)
    assert env.store.list_place(3)[0].state == "visible" and env.stats.count_1h("visible") == 1
    env.store.mark_reload(3, NOW); env.mono.advance(IDLE_SETTLE_S + 0.1); await settle(env)
    assert env.oc.dispose_calls[3] == 3 and env.stats.count_1h("visible") == 1    # уже видимый — не считается снова


@pytest.mark.anyio
async def test_place_ready_hook_marks_only_needed(env):                    # крючок готовности: очередь или saved
    env.store.reload_done(3, env.store.pending()[3].seq)                    # у места 3 строка saved, очереди нет
    env.store.mark_reload(6, NOW)                                           # у места 6 — метка без строк
    env.clock.advance(60)
    for n in (3, 5, 6):                                                     # у места 5 — ни строк, ни метки
        env.role.workplaces.on_ready(n)
    await env.reloads.drain()
    pending = env.store.pending()
    assert sorted(pending) == [3, 6] and pending[6].seq == 2
    assert pending[3].since == pending[6].since == NOW + timedelta(seconds=60)   # since — от готовности (m-н6)


@pytest.mark.anyio
async def test_restore_hook_resets_since(env_fresh):                       # крючок и при возврате «готово» (_restore)
    env_fresh.clock.advance(600)
    await env_fresh.role.workplaces.tick()                                  # первый проход: место работает — «готово»
    assert env_fresh.role.workplaces.is_ready(3)
    await env_fresh.reloads.drain()
    assert env_fresh.store.pending()[3] == PendingReload(NOW + timedelta(seconds=600), 2)   # since — от готовности


@pytest.mark.anyio
async def test_tick_waits_for_ready_hook(env, monkeypatch):                # m8: метка крючка — до чтения очереди
    real = env.store.mark_reload

    def slow(*a, **kw):                                                     # запись метки крючка идёт 0,3 с
        time.sleep(0.3)
        real(*a, **kw)
    monkeypatch.setattr(env.store, "mark_reload", slow)
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    env.role.workplaces.on_ready(3)                                         # место снова готово
    await settle(env)
    await env.reloads.drain()
    assert env.oc.dispose_calls[3] == 1 and env.store.pending() == {}       # перезагрузка — по метке крючка (seq 2)


@pytest.mark.anyio
async def test_pending_view_age_and_ready_only(env):
    env.store.mark_reload(5, NOW + timedelta(seconds=40))                   # место 5 спит
    env.clock.advance(100); await env.reloads.tick()
    assert env.reloads.pending_view(env.clock()) == (1, 100)
    ready_place(env.role.workplaces, env.docker, 5)
    assert env.reloads.pending_view(env.clock()) == (2, 100)               # старейшая метка — места 3
    assert env.reloads.pending_view(NOW) == (2, 0)                          # часы назад — не меньше нуля


@pytest.mark.anyio
async def test_dir_problem_on_first_pass_then_every_dir_check(tmp_path):  # П3: проверка каталога — первым тактом
    e = _build_env(tmp_path, save=False, agents_root=tmp_path / "late")
    await e.reloads.tick()
    assert e.stats.dir_error == "нет каталога агентов"
    make_agents_root(tmp_path / "late"); await e.reloads.tick()
    assert e.stats.dir_error == "нет каталога агентов"                     # не чаще DIR_CHECK_S
    e.mono.advance(DIR_CHECK_S); await e.reloads.tick()
    assert e.stats.dir_error is None


@pytest.mark.anyio
async def test_reload_log_lines_without_names(env, caplog):               # строки журнала Д7-R58
    caplog.set_level(logging.INFO, logger="pcbk_core.gateway.reload")
    env.oc.dispose_status[3] = 500; env.mono.advance(10); await settle(env)
    del env.oc.dispose_status[3]; env.oc.agents_after_dispose[3] = []
    env.mono.advance(RELOAD_RETRY_S + 0.1); env.clock.advance(12); await env.reloads.tick()
    env.store.mark_reload(3, NOW); env.oc.agents_after_dispose[3] = [AGENT]
    env.mono.advance(IDLE_SETTLE_S + 0.1); await settle(env)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("gw agents reload")]
    assert lines == ["gw agents reload place=03 result=failed step=dispose error=status_500",
                     "gw agents reload place=03 result=not_visible n=1",
                     "gw agents reload place=03 result=visible n=1 wait_s=12"]
    assert "proverka" not in caplog.text


@pytest.mark.anyio
async def test_delayed_log_line(env, caplog):
    caplog.set_level(logging.INFO, logger="pcbk_core.gateway.reload")
    env.oc.busy.add(3); env.clock.advance(RELOAD_DELAYED_S + 30); await env.reloads.tick()
    assert [r.getMessage() for r in caplog.records if "result=delayed" in r.getMessage()] == [
        f"gw agents reload place=03 result=delayed wait_s={int(RELOAD_DELAYED_S) + 30}"]


def test_role_wires_reloads(tmp_path):                                     # GatewayRole: учёт, воркер, крючок
    role = gateway_role(tmp_path)
    assert role.reloads.store is role.agents and role.reloads.stats is role.agent_stats
    assert role.workplaces.on_ready == role.reloads.place_ready
    assert role.agents.root == tmp_path / "agents" and role.agents.db_path == str(tmp_path / "core.db")   # П3
    with sqlite3.connect(tmp_path / "core.db") as db:                       # конструктор учёт не открывает
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name = 'agent_reloads'").fetchone() == (0,)
    assert role.reloads.checked_at is None and role.reloads.pending_view(NOW) == (0, None)


def test_worker_runs_in_lifespan_not_with_config_error(tmp_path):
    app, role = make_gateway(tmp_path / "a", ready=(3,))
    with TestClient(app, client=FRONT) as c:
        wait_until(lambda: role.reloads.checked_at is not None)             # первый проход — сразу после запуска
        assert c.get("/health/agents").json()["checked_at"] is not None
        task = role._reload_task
        assert task is not None and not task.done()
    assert task.done() and role._reload_task is None                        # остановка роли снимает задачу
    app, role = make_gateway(tmp_path / "b", config_error="GW_ORIGIN не задан")
    with TestClient(app, client=FRONT) as c:
        assert role._reload_task is None and c.get("/health/agents").json()["checked_at"] is None


def test_agents_open_failure_is_worker_flag_not_config_error(tmp_path):    # П13
    app, role = make_gateway(tmp_path, ready=(3,))

    def broken() -> bool:
        role.agents.last_error = "OperationalError"
        return False
    role.agents.open = broken
    with TestClient(app, client=FRONT) as c:
        wait_until(lambda: role.workplaces.ticks >= 1)
        assert role.config_error is None and role.health() == (True, "принимает вход")
        assert role.agent_stats.worker_store_error is not None and c.get("/health/agents").json()["store_ok"] is False
        assert role._reload_task is not None                                # воркер идёт: строка «учёт недоступен»


# --- доводка р1 (ревью 4c1ed44) ---

@pytest.mark.anyio
async def test_turn_during_rows_read(env, client_a, monkeypatch):          # К1: ход целиком прошёл ворота, пока
    client, sid = client_a                                                  # воркер читал строки места
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    await env.reloads.tick()                                                # пустая карта замечена
    env.mono.advance(IDLE_SETTLE_S + 0.1)                                   # успокоение прошло, отсрочки нет
    real = env.store.list_place
    started, release = threading.Event(), threading.Event()

    def slow(n):
        started.set(); release.wait(5); return real(n)
    monkeypatch.setattr(env.store, "list_place", slow)
    tick = asyncio.create_task(env.reloads.tick())
    await wait_until_async(started.is_set)
    r = await prompt_of(client, sid)()                                      # короткий ход: 204, busy у места ещё нет
    assert r.status_code == 204
    release.set()
    await asyncio.wait_for(tick, 2)
    assert env.oc.dispose_calls[3] == 0, "dispose через миллисекунды после 204 — оборванный ход"
    assert env.reloads.gates.try_reload(3) and not env.role.workplaces.lock(3).locked()    # ворота отпущены
    env.reloads.gates.end_reload(3)
    env.mono.advance(TURN_START_GRACE_S + 0.1); await settle(env)            # после отсрочки — как обычно
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_place_ready_during_rows_read(env, monkeypatch):            # К1: место заново готово за время чтения —
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)          # успокоение заново
    await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1)
    real = env.store.list_place
    started, release = threading.Event(), threading.Event()

    def slow(n):
        started.set(); release.wait(5); return real(n)
    monkeypatch.setattr(env.store, "list_place", slow)
    tick = asyncio.create_task(env.reloads.tick())
    await wait_until_async(started.is_set)
    env.role.workplaces.on_ready(3)                                         # крючок готовности (перезапуск места)
    release.set()
    await asyncio.wait_for(tick, 2)
    assert env.oc.dispose_calls[3] == 0
    monkeypatch.setattr(env.store, "list_place", real)
    await settle(env)
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_prompt_waits_for_worker_reload(env, client_a, monkeypatch):  # В2: воркер держит ворота на dispose
    client, sid = client_a
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1)
    real = env.oc._answer
    in_dispose, release = asyncio.Event(), asyncio.Event()

    async def answer(request, n):
        if request.url.path == "/instance/dispose":
            in_dispose.set(); await release.wait()
        return await real(request, n)
    monkeypatch.setattr(env.oc, "_answer", answer)
    tick = asyncio.create_task(env.reloads.tick())
    await asyncio.wait_for(in_dispose.wait(), 2)
    prompt = asyncio.create_task(prompt_of(client, sid)())
    await asyncio.sleep(0.2)
    assert not [r for r in place_requests(env.oc, 3) if r.url.path.endswith("/prompt_async")]
    assert env.reloads.gates.waiting(3) == 1                                 # ждёт у ворот, а не у места
    release.set(); await asyncio.wait_for(tick, 2)
    assert (await prompt).status_code == 204 and env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_no_reload_while_prompt_in_flight(env, client_a):           # В2: active > 0 — воркер не входит
    client, sid = client_a
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1)
    env.oc.hang_for[3] = 0.5                                                # prompt_async висит у места
    prompt = asyncio.create_task(prompt_of(client, sid)())
    await wait_until_async(lambda: env.reloads.gates.active(3) == 1)
    env.oc.hang_for[3] = 0
    await asyncio.wait_for(env.reloads.tick(), 2)
    assert env.oc.dispose_calls[3] == 0
    assert (await prompt).status_code == 204


@pytest.mark.parametrize("trouble", ["status_500", "no_answer"])
@pytest.mark.anyio
async def test_status_error_is_not_idle(env, monkeypatch, trouble):        # В3: «не знаю» — не «пусто»
    real = env.oc._answer

    async def answer(request, n):
        if request.url.path == "/session/status":
            if trouble == "no_answer":
                raise httpx.ConnectError("нет ответа", request=request)
            return httpx.Response(500, json={"name": "Boom"})
        return await real(request, n)
    monkeypatch.setattr(env.oc, "_answer", answer)
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    await settle(env); await settle(env)
    assert len([r for r in place_requests(env.oc, 3) if r.url.path == "/session/status"]) == 4   # проверка шла
    assert not [r for r in place_requests(env.oc, 3) if r.url.path == "/instance/dispose"]
    monkeypatch.setattr(env.oc, "_answer", real)                            # положительный контроль: место ответило
    await settle(env)
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_waking_place_skipped(env, monkeypatch):                     # m1: is_waking на входе и на шаге а
    w, never = env.role.workplaces, asyncio.Event()
    env.oc.agents_after_dispose[3] = [AGENT]; env.mono.advance(10)
    await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1)
    w._tasks[3] = asyncio.create_task(never.wait())                         # задача пробуждения создана, замок не взят
    asked = len(place_requests(env.oc, 3))
    await asyncio.wait_for(env.reloads.tick(), 2)
    assert len(place_requests(env.oc, 3)) == asked                          # вход: место не спрашивали вовсе
    never.set(); await w._tasks[3]
    assert w.is_ready(3) and not w.is_waking(3) and not w.lock(3).locked()
    real, statuses = env.oc._answer, []

    async def answer(request, n):                                           # шаг а: пробуждение — под воротами
        if request.url.path == "/session/status":
            statuses.append(n)
            if len(statuses) == 3:                                          # 1 — такт; 2, 3 — до ворот и под ними
                w._tasks[3] = asyncio.create_task(never.wait())
        return await real(request, n)
    never.clear(); monkeypatch.setattr(env.oc, "_answer", answer)
    await env.reloads.tick()                                                # пустая карта — заново (вход её снял)
    env.mono.advance(IDLE_SETTLE_S + 0.1)
    await asyncio.wait_for(env.reloads.tick(), 2)
    assert len(statuses) == 3 and env.oc.dispose_calls[3] == 0 and not w.lock(3).locked()
    never.set(); await w._tasks[3]
