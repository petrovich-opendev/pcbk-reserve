import json
import re
import threading
import time
from dataclasses import replace
from datetime import timedelta

import pytest

import pcbk_watchdog.main
from helpers import (CHROME, SETTINGS, T0, chrome_dom, http_get, http_status, now_utc,
                     start_test_server)
from pcbk_watchdog.checks import Check
from pcbk_watchdog.docker_api import DockerReader
from pcbk_watchdog.journal import Journal
from pcbk_watchdog.main import (LazyJournal, Settings, WatchState, load_components, run_checks,
                                run_loop, tick)
from pcbk_watchdog.page import Snapshot, is_stale


def test_settings_defaults_and_env():
    assert Settings.from_env({}) == Settings()
    s = Settings.from_env({"TICK_S": "1", "DISPLAY_TZ": "Asia/Yekaterinburg", "LISTEN_PORT": "9000"})
    assert (s.TICK_S, s.LISTEN_PORT, s.STALE_AFTER_S, s.tz.key) == (1.0, 9000, 30, "Asia/Yekaterinburg")
    for bad in ({"STALE_AFTER_S": "abc"}, {"TICK_S": "40"}, {"DISPLAY_TZ": "Нет/Такого"},
                {"STALE_AFTER_S": "12"}):   # срок такта min(11, 6) − 2·3 = 0 — отказ
        with pytest.raises(ValueError):
            Settings.from_env(bad)


def test_tick_budget_keeps_age_under_stale():
    # срок такта d ≤ min(STALE − TICK, STALE/2) с запасом на одну проверку (2 срока сокета):
    # возраст снимка до следующего ≤ max(TICK, d) + d ≤ STALE
    assert Settings().tick_budget_s == 9          # min(30 − 10, 15) − 2·max(3, 3)
    assert Settings(TICK_S=1).tick_budget_s == 9
    assert Settings(STALE_AFTER_S=60, DOCKER_TIMEOUT_S=5).tick_budget_s == 30 - 10


def test_run_checks_docker_down_marks_containers_unknown():
    comps = [{"id": "sp-ro", "title": "Прокси сокета сторожа", "kind": "docker_ping"},
             {"id": "sp-ctl", "title": "Прокси сокета серверного слоя", "kind": "container",
              "container": "pcbk-sp-ctl", "sleeping_ok": False}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.state) for c in checks] == [("sp-ro", "fail"), ("sp-ctl", "unknown")]
    assert "прокси сокета" in checks[1].detail


def test_run_checks_all_kinds(fake_proxy, tls_server):
    comps = [{"id": "edge", "title": "Входной прокси", "kind": "tls", "host": "127.0.0.1",
              "port": tls_server.port},
             {"id": "sp-ro", "title": "Прокси сокета сторожа", "kind": "docker_ping"},
             {"id": "sp-ctl", "title": "Прокси сокета серверного слоя", "kind": "container",
              "container": "pcbk-sp-ctl", "sleeping_ok": False},
             {"id": "memory", "title": "Память сервера", "kind": "memory"},
             {"id": "web", "title": "Веб", "kind": "http", "url": fake_proxy.url + "/v1.44/_ping"},
             {"id": "core", "title": "Серверный слой", "kind": "absent"}]
    settings = replace(SETTINGS, TLS_CAFILE=tls_server.cafile)
    checks = run_checks(comps, DockerReader(fake_proxy.url, 0.5), settings, now_utc())
    assert [(c.component, c.state) for c in checks] == [
        ("edge", "ok"), ("sp-ro", "ok"), ("sp-ctl", "ok"), ("memory", "ok"), ("web", "ok"),
        ("core", "absent")]
    assert checks[5].detail == "ещё не установлен" and "11718 МиБ" in checks[3].detail


def test_tick_bounded_when_proxy_hangs(silent_proxy_url):   # принимает соединение и молчит
    comps = [{"id": f"s{n}", "title": "x", "kind": "container", "container": f"pcbk-student-{n:02d}",
              "sleeping_ok": True} for n in range(1, 11)]
    started = time.monotonic()
    run_checks(comps, DockerReader(silent_proxy_url, timeout=0.5), SETTINGS, T0)
    assert time.monotonic() - started < 1.5


def scaled_settings(monkeypatch):
    """Настройки 1:10 (STALE 3 с, такт 1 с, сроки сокета 0,5 с): срок такта 0,5 с."""
    monkeypatch.setattr(pcbk_watchdog.main, "NET_CHECK_TIMEOUT_S", 0.5)
    return replace(SETTINGS, STALE_AFTER_S=3, TICK_S=1, DOCKER_TIMEOUT_S=0.5)


def students(count):
    return [{"id": f"s{n}", "title": "x", "kind": "container", "container": f"pcbk-student-{n:02d}",
             "sleeping_ok": True} for n in range(1, count + 1)]


def test_tick_stops_at_budget_when_proxy_slow(slow_proxy_url, monkeypatch):
    # после срока такта сетевые проверки не начинаются; срок — tick_budget_s
    settings = scaled_settings(monkeypatch)
    comps = students(10) + [{"id": "memory", "title": "Память сервера", "kind": "memory"}]
    started = time.monotonic()
    checks = run_checks(comps, DockerReader(slow_proxy_url, timeout=0.5), settings, now_utc())
    assert time.monotonic() - started < 1.2   # срок 0,5 с + одна начатая проверка 0,3 с
    assert checks[0].state == "ok" and checks[9].state == "unknown" and "такт" in checks[9].detail
    assert checks[10].state == "ok"   # дешёвые проверки без сети не пропускаются


def test_loop_keeps_snapshot_fresh_with_slow_proxy(slow_proxy_url, tmp_path, monkeypatch):
    # прокси отвечает, но медленно: снимок после первого такта не устаревает ни разу
    settings = replace(scaled_settings(monkeypatch), TICK_S=0.5)
    state, stop = WatchState(), threading.Event()
    loop = threading.Thread(target=run_loop, daemon=True, args=(
        state, Journal(str(tmp_path / "j.db")), students(20), DockerReader(slow_proxy_url, 0.5),
        settings, stop))
    loop.start()
    try:
        first_by = time.monotonic() + 3
        while state.snapshot is None and time.monotonic() < first_by:
            time.sleep(0.02)
        assert state.snapshot is not None
        stale, ticks, watch_until = [], set(), time.monotonic() + 4
        while time.monotonic() < watch_until:
            snap = state.snapshot
            ticks.add(snap.checked_at)
            if is_stale(snap, now_utc(), settings.STALE_AFTER_S):
                stale.append(round((now_utc() - snap.checked_at).total_seconds(), 2))
            time.sleep(0.02)
    finally:
        stop.set()
        loop.join(5)
    assert stale == [] and len(ticks) >= 4


def test_loop_survives_tick_exception(monkeypatch):
    calls, again = [], threading.Event()

    def flaky_tick(*args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("такт упал")
        again.set()
    monkeypatch.setattr(pcbk_watchdog.main, "tick", flaky_tick)
    stop = threading.Event()
    loop = threading.Thread(target=run_loop, daemon=True, args=(
        WatchState(), None, [], None, replace(SETTINGS, TICK_S=0.05), stop))
    loop.start()
    try:
        assert again.wait(2), "цикл остановился после исключения в такте"
    finally:
        stop.set()
        loop.join(2)
    assert not loop.is_alive()


def test_run_checks_survives_check_exception(monkeypatch):
    monkeypatch.setattr(pcbk_watchdog.main, "check_memory", lambda *a, **k: 1 / 0)
    out = run_checks([{"id": "memory", "title": "Память сервера", "kind": "memory"}],
                     DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert out[0].state == "unknown" and "ZeroDivisionError" in out[0].detail


def test_tick_updates_snapshot_and_journal(tmp_path):
    journal, state = Journal(str(tmp_path / "j.db")), WatchState()
    comps = [{"id": "memory", "title": "Память сервера", "kind": "memory"}]
    tick(state, journal, comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert state.snapshot.checked_at == T0
    assert [(c.component, c.state) for c in state.snapshot.checks] == [("memory", "ok")]
    assert [(e.component, e.state) for e in journal.recent()] == [("memory", "ok")]


def test_journal_failure_shows_on_page(tmp_path):
    ro = tmp_path / "ro"; ro.mkdir(); journal = Journal(str(ro / "j.db"))   # таблица создана
    (ro / "j.db").chmod(0o400); ro.chmod(0o500)                                # дальше запись невозможна
    state = WatchState()
    tick(state, journal, [], DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert any(c.component == "journal" and c.state == "fail" for c in state.snapshot.checks)


def test_journal_record_error_keeps_snapshot(tmp_path, monkeypatch):
    journal, state = Journal(str(tmp_path / "j.db")), WatchState()

    def broken(*a, **k):
        raise OSError("диск")
    monkeypatch.setattr(journal, "record", broken)
    comps = [{"id": "memory", "title": "Память сервера", "kind": "memory"}]
    tick(state, journal, comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.state, c.detail) for c in state.snapshot.checks] == [
        ("memory", "ok", "свободно 11718 МиБ"), ("journal", "fail", "OSError")]


def test_journal_unopenable_at_start_shows_on_page(tmp_path):
    ro = tmp_path / "ro"; ro.mkdir(); ro.chmod(0o500)   # журнал не создать
    journal, state = LazyJournal(str(ro / "j.db")), WatchState()
    tick(state, journal, [], DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.state) for c in state.snapshot.checks] == [("journal", "fail")]
    assert journal.recent() == []
    ro.chmod(0o700)                                     # каталог починили — журнал открылся
    tick(state, journal, [], DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert state.snapshot.checks == ()


def test_components_file_d1():
    comps = load_components("components.json")
    assert [c["id"] for c in comps][:4] == ["edge", "sp-ro", "sp-ctl", "memory"]
    assert comps[0]["kind"] == "tls"
    assert {c["id"] for c in comps if c["kind"] == "absent"} == \
           {f"student-{n:02d}" for n in range(1, 11)} | {"core", "historian", "llm"}


def test_components_file_d1_details():
    comps = {c["id"]: c for c in load_components("components.json")}
    assert (comps["edge"]["host"], comps["edge"]["port"], comps["edge"]["title"]) == \
           ("pcbk-edge", 8443, "Входной прокси")
    assert (comps["sp-ctl"]["container"], comps["sp-ctl"]["sleeping_ok"]) == ("pcbk-sp-ctl", False)
    assert comps["student-07"]["title"] == "Рабочее место 07"
    assert comps["llm"]["title"] == "OpenRouter и бюджет"


def test_load_components_rejects_bad_config(tmp_path):
    for bad in ([{"id": "a", "title": "A", "kind": "shell"}],
                [{"id": "a", "title": "A", "kind": "container", "container": "dify-api",
                  "sleeping_ok": False}],
                [{"id": "a", "title": "A", "kind": "absent"}, {"id": "a", "title": "B", "kind": "absent"}]):
        path = tmp_path / "c.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            load_components(str(path))


def test_healthz_follows_freshness():
    state = WatchState(); srv = start_test_server(state)
    assert http_status(srv, "/healthz") == 503 and http_status(srv, "/status.json") == 503
    state.snapshot = Snapshot(now_utc(), ())
    assert http_status(srv, "/healthz") == 200
    state.snapshot = Snapshot(now_utc() - timedelta(seconds=60), ())
    assert http_status(srv, "/healthz") == 503


def test_server_routes(tmp_path):
    journal = Journal(str(tmp_path / "j.db"))
    journal.started(now_utc())
    state = WatchState(); srv = start_test_server(state, journal)
    state.snapshot = Snapshot(now_utc(), (Check("edge", "Входной прокси", "ok", "отвечает"),))
    code, headers, body = http_get(srv, "/status")
    assert code == 200 and headers["content-type"] == "text/html; charset=utf-8"
    assert "script-src 'self'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"
    assert "Входной прокси" in body.decode() and "сторож запущен" in body.decode()
    code, headers, body = http_get(srv, "/status.js")
    assert code == 200 and headers["content-type"].startswith("text/javascript")
    assert b"export function startPolling" in body
    code, headers, body = http_get(srv, "/status.json")
    data = json.loads(body)
    assert code == 200 and data["stale"] is False and data["checks"][0]["component"] == "edge"
    assert http_status(srv, "/nope") == 404


@pytest.mark.skipif(CHROME is None, reason="нет google-chrome")
def test_browser_runs_status_js_on_stale_json(fake_page_server):
    # свежий HTML (полоса hidden), а /status.json отвечает stale: true
    dom = chrome_dom(fake_page_server.url + "/status", virtual_time_ms=8000)
    assert re.search(r'id="silence"(?![^>]*hidden)', dom)
    assert re.search(r'<main[^>]*data-stale', dom)   # старое под полосой — серым


@pytest.mark.skipif(CHROME is None, reason="нет google-chrome")
def test_browser_on_real_server_with_csp():
    # страница отрисована свежей, дальше снимок устарел: status.js под CSP сервера показывает полосу
    class Flip(WatchState):
        reads = 0

        @property
        def snapshot(self):
            self.reads += 1
            return Snapshot(now_utc() - timedelta(seconds=0 if self.reads == 1 else 60), ())

    srv = start_test_server(Flip())
    dom = chrome_dom(f"http://127.0.0.1:{srv.server_port}/status", virtual_time_ms=8000)
    assert re.search(r'id="silence"(?![^>]*hidden)', dom)
