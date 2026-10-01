"""Карантин кривого файла агента и перезапуск места (Д7-R59, задача А6 Д7б): место не может прочесть файл агента
(ConfigInvalidError с путём) — после dispose воркера или при пробуждении. Файл из каталога агентов места — в карантин и
перезапуск (не больше QUARANTINE_MAX_PER_H за час); файл образа, путь с «..» и предел — место гаснет с текстом config,
без шторма dispose.

Места и sp-ctl — двойники Д5а; кривой файл — FakePlaces.bad_files: экземпляр места, созданный после start или dispose при
файле на диске, сломан, и чинит его только start без файла (как OpenCode 1.18.33, research/09, О3). Фикстуры — как
test_gw_reload (место 3 готово, proverka в очереди; место 5 спит), воркер — на часах теста, такты ведёт тест;
FakeDocker.on_start → FakePlaces.started, Workplaces.repair → AgentReloads.repair фикстуры (m3)."""
import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from helpers import (AGENT_DRAFT, ORIGIN, PW, SETTINGS, agent_text, gateway_role, non_root, oc_headers,
                     wait_until_async)
from pcbk_core.app import create_app
from pcbk_core.agents.store import NameTaken
from pcbk_core.gateway.places import PLACE_TEXTS
from pcbk_core.gateway.reload import (IDLE_SETTLE_S, PLACE_AGENTS_DIR, QUARANTINE_MAX_PER_H, RELOAD_RETRY_S,
                                      RELOAD_TICK_S, AgentReloads, agents_rel_path, config_error_path)
from pcbk_core.gateway.rules import EXAMPLE_IDS

NOW = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)
BAD = PLACE_AGENTS_DIR + "/proverka.md"
CONFIG_TEXT = "Настройка рабочего места сломана — сообщите администратору."
AGENT = {"name": "proverka", "native": False, "mode": "primary"}
BASE = "https://stand.test:8443"
SES = EXAMPLE_IDS["ses"]


class FakeClock:
    """Часы стены теста (как в test_gw_reload): идут только по advance(s)."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, s: float) -> None:
        self.now += timedelta(seconds=s)


async def quick_sleep(s: float) -> None:
    await asyncio.sleep(0)


def _build_env(tmp_path) -> SimpleNamespace:
    """Роль шлюза на двойниках: место 3 готово, строка saved «proverka» места 3 и метка очереди; место 5 спит. Воркер
    фикстуры — и крючок готовности, и repair мест (m3); новый процесс места в FakeDocker — FakePlaces.started."""
    role = gateway_role(tmp_path, ready=(3,))
    clock = FakeClock(NOW)
    role.wallclock = clock
    store = role.agents
    assert store.open(), store.last_error
    reloads = AgentReloads(role.workplaces, store, role.agent_stats, monotonic=role.mono, wallclock=clock,
                           sleep=quick_sleep)
    role.reloads = reloads
    role.workplaces.on_ready = reloads.place_ready
    role.workplaces.repair = reloads.repair
    role.fake_docker.on_start = role.fake_places.started
    draft = replace(AGENT_DRAFT, name="proverka")
    assert store.save(3, draft, agent_text(draft), NOW) == "saved"
    return SimpleNamespace(role=role, oc=role.fake_places, docker=role.fake_docker, mono=role.mono, clock=clock,
                           store=store, root=store.root, stats=role.agent_stats, reloads=reloads)


@pytest.fixture
def env(tmp_path):
    return _build_env(tmp_path)


@pytest.fixture
def env_asleep(tmp_path):
    e = _build_env(tmp_path)
    assert not e.role.workplaces.is_ready(5) and not e.docker.containers[5]["running"]
    return e


@pytest.fixture
async def client_a(env):
    """httpx.AsyncClient к приложению шлюза с cookie учётки места 3 (как в test_gw_reload)."""
    app = create_app(SETTINGS, [env.role])
    app.state.zone_names_ok = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        r = await client.post("/pcbk/api/auth/login", json={"login": "stu-03", "password": PW},
                              headers={"Origin": ORIGIN})
        assert r.status_code == 200, r.status_code
        yield client
    await env.role.login_gate.drain()


async def settle(env):                       # первый такт видит пустую карту, второй — через IDLE_SETTLE_S
    await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1); await env.reloads.tick()


def body(path=BAD, name="ConfigInvalidError"):
    return json.dumps({"name": name, "data": {"path": path, "issues": []}}).encode()


def place_calls(env, n):
    return [a for a, m in env.docker.calls if m == n and a in ("stop", "start")]


# --- тесты брифа (П3: view.detail) ---

def test_paths():
    assert config_error_path(body()) == BAD and config_error_path(body(name="NotFound")) is None
    assert config_error_path(b"<html>") is None and config_error_path(b'{"name":"ConfigInvalidError"}') is None
    assert agents_rel_path(BAD) == "proverka.md" and agents_rel_path(PLACE_AGENTS_DIR + "/sub/x.md") == "sub/x.md"
    for bad in (PLACE_AGENTS_DIR + "/../opencode.json", PLACE_AGENTS_DIR + "//x.md", PLACE_AGENTS_DIR + "/x.txt",
                "/etc/pcbk-opencode/opencode/opencode.json", PLACE_AGENTS_DIR + "/a b.md"):
        assert agents_rel_path(bad) is None, bad


@pytest.mark.anyio
async def test_quarantine_after_dispose(env):                 # место 3 готово, proverka в очереди
    bad = env.root / "student-03" / "proverka.md"
    bad.write_text("---\nsteps: 0\n---\nx\n"); env.oc.bad_files[3] = (BAD, bad)
    env.mono.advance(10)
    await settle(env)                                         # пустая карта продержалась — dispose
    await env.role.workplaces.wait_ready(3)
    assert not (env.root / "student-03" / "proverka.md").exists()
    assert len(list((env.root / "quarantine" / "student-03").iterdir())) == 1
    assert [a for a, n in env.docker.calls if n == 3 and a in ("stop", "start")][-2:] == ["stop", "start"]
    assert env.role.workplaces.is_ready(3) and env.stats.count_1h("quarantined") == 1
    assert env.store.list_place(3)[0].state == "quarantined"
    assert 3 in env.store.pending()                          # m1: reload_done не звался, место сверится после пробуждения


@pytest.mark.anyio
async def test_wake_repairs_once(env_asleep):                 # место 5 спит, в каталоге кривой файл
    bad = env_asleep.root / "student-05" / "bad.md"
    bad.write_text("x"); env_asleep.oc.bad_files[5] = (PLACE_AGENTS_DIR + "/bad.md", bad)
    await env_asleep.role.workplaces.wake(5); view = await env_asleep.role.workplaces.wait_ready(5)
    assert view.state == "ready" and env_asleep.stats.count_1h("quarantined") == 1 and not bad.exists()


@pytest.mark.anyio
async def test_image_error_not_moved(env_asleep, tmp_path):
    image = tmp_path / "image-opencode.json"; image.write_text("{}")     # файл «образа»: служба его не двигает
    env_asleep.oc.bad_files[5] = ("/etc/pcbk-opencode/opencode/opencode.json", image)
    await env_asleep.role.workplaces.wake(5); view = await env_asleep.role.workplaces.wait_ready(5)
    assert (view.state, view.detail) == ("failed", "Настройка рабочего места сломана — сообщите администратору.")
    assert env_asleep.stats.count_1h("image_config") == 1
    assert list((env_asleep.root / "quarantine").glob("student-05/*")) == []         # ничего не перенесено


@pytest.mark.anyio
async def test_traversal_is_image(env):
    assert await env.reloads.repair(3, body(PLACE_AGENTS_DIR + "/../opencode.json")) == "image"


@pytest.mark.anyio
async def test_cap_per_hour(env):
    for i in range(QUARANTINE_MAX_PER_H):
        (env.root / "student-03" / f"b{i}.md").write_text("x")
        assert await env.reloads.repair(3, body(PLACE_AGENTS_DIR + f"/b{i}.md")) == "quarantined"
    (env.root / "student-03" / "b9.md").write_text("x")
    assert await env.reloads.repair(3, body(PLACE_AGENTS_DIR + "/b9.md")) == "capped"
    assert (env.root / "student-03" / "b9.md").exists() and env.stats.count_1h("image_config") == 1
    env.mono.advance(3601)
    assert await env.reloads.repair(3, body(PLACE_AGENTS_DIR + "/b9.md")) == "quarantined"


@pytest.mark.anyio
async def test_other_400_keeps_old_behaviour(env_asleep):     # 400 не про конфигурацию — как Д5а: срок пробуждения
    env_asleep.oc.force_status[5] = (400, {})
    await env_asleep.role.workplaces.wake(5); view = await env_asleep.role.workplaces.wait_ready(5)
    assert view.detail == PLACE_TEXTS["timeout"] and env_asleep.stats.count_1h("quarantined") == 0


# --- сверх брифа ---

def test_config_text():
    assert PLACE_TEXTS["config"] == CONFIG_TEXT


def test_role_wires_repair(tmp_path):                         # GatewayRole: repair мест — воркер роли
    role = gateway_role(tmp_path)
    assert role.workplaces.repair == role.reloads.repair and role.workplaces.on_ready == role.reloads.place_ready


@pytest.mark.anyio
async def test_double_dispose_does_not_heal_only_start(env):  # двойник: как OpenCode 1.18.33 (О3)
    bad = env.root / "student-03" / "proverka.md"
    env.oc.bad_files[3] = (BAD, bad)
    w = env.role.workplaces
    assert (await w.request(3, "GET", "/agent")).status_code == 200      # экземпляр создан до файла — целый
    assert (await w.request(3, "POST", "/instance/dispose")).status_code == 200
    r = await w.request(3, "GET", "/agent")
    assert (r.status_code, config_error_path(r.content)) == (400, BAD)    # новый экземпляр — при файле
    assert (await w.request(3, "GET", "/global/health")).status_code == 200
    assert (await w.request(3, "POST", "/instance/dispose")).status_code == 400   # dispose не лечит
    bad.unlink()
    assert (await w.request(3, "GET", "/agent")).status_code == 400       # удаление файла не лечит
    env.docker.containers[3]["running"] = False; await w.spctl.start(3)    # новый процесс без файла
    assert (await w.request(3, "GET", "/agent")).status_code == 200
    env.oc.wrong_pw = True                                                 # m23: пароль — раньше экземпляра
    bad.write_text("x"); env.oc.started(3)
    assert (await w.request(3, "GET", "/agent")).status_code == 401


@pytest.mark.anyio
async def test_second_repaired_in_one_wake_is_config(env_asleep):   # m5: второй кривой файл — config, не круг
    e = env_asleep
    first, second = e.root / "student-05" / "a.md", e.root / "student-05" / "b.md"
    first.write_text("x"); second.write_text("x")
    e.oc.bad_files[5] = (PLACE_AGENTS_DIR + "/a.md", first)

    def on_start(n):                                          # OpenCode читает файлы по одному: после a — b
        if not first.exists():
            e.oc.bad_files[5] = (PLACE_AGENTS_DIR + "/b.md", second)
        e.oc.started(n)
    e.docker.on_start = on_start
    await e.role.workplaces.wake(5); view = await e.role.workplaces.wait_ready(5)
    assert (view.state, view.detail) == ("failed", CONFIG_TEXT)
    assert e.stats.count_1h("quarantined") == 2 and not first.exists() and not second.exists()
    assert place_calls(e, 5) == ["start", "stop", "start", "stop"]       # один повтор, затем место погашено
    assert not e.docker.containers[5]["running"] and e.role.stats.count_1h("wake_failed") == 1
    await e.role.workplaces.wake(5)                                      # положительный контроль: файлов нет
    assert (await e.role.workplaces.wait_ready(5)).state == "ready" and place_calls(e, 5)[-1] == "start"


@pytest.mark.anyio
async def test_image_after_dispose_no_dispose_storm(env):    # П5 в: image после dispose — место гаснет, dispose не идёт
    bad = env.root / "student-03" / "image.json"; bad.write_text("{}")
    env.oc.bad_files[3] = ("/etc/pcbk-opencode/opencode/opencode.json", bad)
    env.mono.advance(10); await settle(env)
    view = await env.role.workplaces.wait_ready(3)
    assert (view.state, view.detail) == ("failed", CONFIG_TEXT)
    assert place_calls(env, 3) == ["stop"] and not env.docker.containers[3]["running"]   # без start
    assert env.stats.count_1h("image_config") == 1 and env.stats.count_1h("quarantined") == 0
    assert bad.exists() and env.store.list_place(3)[0].state == "saved" and 3 in env.store.pending()
    for _ in range(5):
        env.mono.advance(RELOAD_RETRY_S); await settle(env)
    assert env.oc.dispose_calls[3] == 1 and env.stats.count_1h("image_config") == 1


@pytest.mark.anyio
async def test_capped_after_dispose_stops_place(env):        # предел за час — после dispose место гаснет, файл на месте
    for i in range(QUARANTINE_MAX_PER_H):
        (env.root / "student-03" / f"b{i}.md").write_text("x")
        assert await env.reloads.repair(3, body(PLACE_AGENTS_DIR + f"/b{i}.md")) == "quarantined"
    bad = env.root / "student-03" / "proverka.md"; env.oc.bad_files[3] = (BAD, bad)
    env.mono.advance(10); await settle(env)
    view = await env.role.workplaces.wait_ready(3)
    assert (view.state, view.detail) == ("failed", CONFIG_TEXT) and place_calls(env, 3) == ["stop"]
    assert bad.exists() and env.store.list_place(3)[0].state == "saved"
    assert env.stats.count_1h("image_config") == 1 and env.stats.count_1h("quarantined") == QUARANTINE_MAX_PER_H
    for _ in range(3):
        env.mono.advance(RELOAD_RETRY_S); await settle(env)
    assert env.oc.dispose_calls[3] == 1


@pytest.mark.anyio
async def test_restart_unready_synchronously_and_wake_joins(env):   # m7, П5 г
    w = env.role.workplaces
    view = w.restart(3)                                       # без await: «готово» снято до возврата
    assert not w.is_ready(3) and w.is_waking(3) and (view.state, view.detail) == ("starting", PLACE_TEXTS["starting"])
    task = w._tasks[3]
    assert (await w.wake(3)).state == "starting" and w._tasks[3] is task          # wake студента присоединяется
    assert (await w.wait_ready(3)).state == "ready" and place_calls(env, 3) == ["stop", "start"]


def hold_stops(env) -> asyncio.Event:
    """stop места в двойнике sp-ctl висит, пока тест не установит событие: перезапуск идёт секунды, как в жизни."""
    hold, real = asyncio.Event(), env.docker.__call__

    async def slow(request):
        if request.url.path.endswith("/stop"):
            await hold.wait()
        return await real(request)
    w = env.role.workplaces
    w.spctl._transport, w.spctl._client = httpx.MockTransport(slow), None
    return hold


@pytest.mark.anyio
async def test_prompt_at_gate_sees_restart(env, client_a, monkeypatch):    # m7: ход, ждавший ворота, — «не готово»
    bad = env.root / "student-03" / "proverka.md"; env.oc.bad_files[3] = (BAD, bad)
    env.mono.advance(10); await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1)
    real, hold = env.oc._answer, hold_stops(env)
    in_dispose, release = asyncio.Event(), asyncio.Event()

    async def answer(request, n):
        if request.url.path == "/instance/dispose":
            in_dispose.set(); await release.wait()
        return await real(request, n)
    monkeypatch.setattr(env.oc, "_answer", answer)
    tick = asyncio.create_task(env.reloads.tick())
    await asyncio.wait_for(in_dispose.wait(), 2)
    prompt = asyncio.create_task(client_a.post(f"/pcbk/api/oc/session/{SES}/prompt_async", headers=oc_headers(),
                                               json={"parts": [{"type": "text", "text": "x"}]}))
    await wait_until_async(lambda: env.reloads.gates.waiting(3) == 1)
    release.set(); await asyncio.wait_for(tick, 2)
    r = await prompt
    assert (r.status_code, r.json()["name"]) == (503, "WorkplaceNotReady")
    assert not [q for q in env.oc.requests if q.path.endswith("/prompt_async")]   # в сломанное место ход не ушёл
    hold.set()
    assert (await env.role.workplaces.wait_ready(3)).state == "ready"


@pytest.mark.anyio
async def test_saved_rows_rechecked_after_restart_not_before_retry(env):   # m4: после перезапуска — не раньше паузы
    second = replace(AGENT_DRAFT, name="vtoroy")
    assert env.store.save(3, second, agent_text(second), NOW) == "saved"
    bad = env.root / "student-03" / "proverka.md"; env.oc.bad_files[3] = (BAD, bad)
    env.oc.agents_after_dispose[3] = [{**AGENT, "name": "vtoroy"}]
    env.mono.advance(10); await settle(env)
    assert (await env.role.workplaces.wait_ready(3)).state == "ready"
    await env.reloads.drain()                                 # крючок готовности поставил место заново
    assert env.stats.count_1h("reload_failed") == 1 and env.oc.dispose_calls[3] == 1
    env.mono.advance(RELOAD_RETRY_S - 2 * IDLE_SETTLE_S); await settle(env)
    assert env.oc.dispose_calls[3] == 1                       # пауза после неудачи
    env.mono.advance(2 * IDLE_SETTLE_S); await settle(env)
    assert env.oc.dispose_calls[3] == 2
    assert {r.name: r.state for r in env.store.list_place(3)} == {"proverka": "quarantined", "vtoroy": "visible"}
    assert env.store.pending() == {} and env.stats.count_1h("not_visible") == 0


@non_root
@pytest.mark.anyio
async def test_dir_unavailable_is_image_not_counted(env, caplog):   # m6: сбой переноса — image, предел не тратится
    caplog.set_level(logging.INFO, logger="pcbk_core.gateway.reload")
    (env.root / "quarantine").chmod(0o500)                   # каталог карантина не пишется
    try:
        assert await env.reloads.repair(3, body()) == "image"
    finally:
        (env.root / "quarantine").chmod(0o700)
    assert (env.root / "student-03" / "proverka.md").exists() and env.store.list_place(3)[0].state == "saved"
    assert env.stats.count_1h("image_config") == 1 and env.stats.count_1h("quarantined") == 0
    assert "gw agents quarantine place=03 result=image error=DirUnavailable" in caplog.text
    for i in range(QUARANTINE_MAX_PER_H):                     # неудача не съела место в пределе
        (env.root / "student-03" / f"b{i}.md").write_text("x")
        assert await env.reloads.repair(3, body(PLACE_AGENTS_DIR + f"/b{i}.md")) == "quarantined"


@non_root
@pytest.mark.anyio
async def test_dir_unavailable_on_wake_is_config_not_timeout(env_asleep):   # m6 на пробуждении
    bad = env_asleep.root / "student-05" / "bad.md"; bad.write_text("x")
    env_asleep.oc.bad_files[5] = (PLACE_AGENTS_DIR + "/bad.md", bad)
    (env_asleep.root / "quarantine").chmod(0o500)
    try:
        await env_asleep.role.workplaces.wake(5); view = await env_asleep.role.workplaces.wait_ready(5)
    finally:
        (env_asleep.root / "quarantine").chmod(0o700)
    assert (view.state, view.detail) == ("failed", CONFIG_TEXT) and bad.exists()


@pytest.mark.anyio
async def test_missing_file_still_quarantined(env, caplog):  # файла уже нет — перезапуск всё равно нужен
    caplog.set_level(logging.INFO, logger="pcbk_core.gateway.reload")
    assert await env.reloads.repair(3, body(PLACE_AGENTS_DIR + "/nety.md")) == "quarantined"
    assert env.stats.count_1h("quarantined") == 1 and "result=missing" in caplog.text


@pytest.mark.anyio
async def test_not_config_body_counts_nothing(env):
    for raw in (body(name="NotFound"), b"<html>", b"[]", b'{"name":"ConfigInvalidError","data":{"path":1}}'):
        assert await env.reloads.repair(3, raw) == "not_config", raw
    assert env.stats.count_1h("image_config") == env.stats.count_1h("quarantined") == 0


@pytest.mark.anyio
async def test_quarantine_log_lines_without_names(env, caplog):   # строки журнала Д7-R59: без имени файла
    caplog.set_level(logging.INFO, logger="pcbk_core.gateway.reload")
    assert await env.reloads.repair(3, body()) == "quarantined"
    assert await env.reloads.repair(3, body("/etc/pcbk-opencode/opencode/opencode.json")) == "image"
    for i in range(QUARANTINE_MAX_PER_H - 1):
        await env.reloads.repair(3, body(PLACE_AGENTS_DIR + f"/c{i}.md"))
    assert await env.reloads.repair(3, body()) == "capped"
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("gw agents quarantine")]
    assert lines == ["gw agents quarantine place=03 result=moved", "gw agents quarantine place=03 result=image",
                     "gw agents quarantine place=03 result=missing", "gw agents quarantine place=03 result=missing",
                     "gw agents quarantine place=03 result=capped"]
    assert "proverka" not in caplog.text and "opencode.json" not in caplog.text


@pytest.mark.anyio
async def test_save_after_quarantine_is_name_taken(env):     # П5 а: повтор черновика после карантина — не «сохранено»
    assert await env.reloads.repair(3, body()) == "quarantined"
    draft = replace(AGENT_DRAFT, name="proverka")
    with pytest.raises(NameTaken) as e:
        env.store.save(3, draft, agent_text(draft), NOW + timedelta(seconds=5))
    assert "proverka" in e.value.existing and not (env.root / "student-03" / "proverka.md").exists()


@pytest.mark.anyio
async def test_worker_skips_restarting_place(env):            # П5 г: перезапуск — задача в _tasks, воркер его пропускает
    w = env.role.workplaces
    env.mono.advance(10); await env.reloads.tick(); env.mono.advance(IDLE_SETTLE_S + 0.1)
    hold = hold_stops(env)                                   # stop перезапуска висит, пока тест не отпустит
    w.restart(3)
    await asyncio.sleep(0)
    asked = len([r for r in env.oc.requests if r.url.host == "172.31.3.3"])
    for _ in range(3):
        env.mono.advance(RELOAD_TICK_S); await asyncio.wait_for(env.reloads.tick(), 2)
    assert len([r for r in env.oc.requests if r.url.host == "172.31.3.3"]) == asked and env.oc.dispose_calls[3] == 0
    hold.set()
    assert (await w.wait_ready(3)).state == "ready"
