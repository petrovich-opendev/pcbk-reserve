import json
import re
import threading
import time
from dataclasses import replace
from datetime import timedelta

import pytest

import pcbk_watchdog.main
from helpers import (CHROME, SETTINGS, T0, NoJournalWrite, chrome_dom, gw, http_get, http_status, insp, llm_obj,
                     now_utc, put_build, start_test_server, wait_until)
from pcbk_watchdog.checks import HTTP_CHECK_SPANS, NET_CHECK_TIMEOUT_S, Check
from pcbk_watchdog.docker_api import DockerReader
from pcbk_watchdog.journal import Journal
from pcbk_watchdog.memory import MEM_SENSOR_SILENT_DETAIL
from pcbk_watchdog.main import (LazyJournal, MemState, Settings, WatchState, load_components, run_checks,
                                run_loop, tick)
from pcbk_watchdog.page import Snapshot, is_stale


def test_settings_defaults_and_env():
    assert Settings.from_env({}) == Settings()
    s = Settings.from_env({"TICK_S": "1", "DISPLAY_TZ": "Asia/Yekaterinburg", "LISTEN_PORT": "9000"})
    assert (s.TICK_S, s.LISTEN_PORT, s.STALE_AFTER_S, s.tz.key) == (1.0, 9000, 30, "Asia/Yekaterinburg")
    assert Settings().DRILL_FREEZE_LOOP is False                      # учения выключены
    assert Settings.from_env({"DRILL_FREEZE_LOOP": "1"}).DRILL_FREEZE_LOOP is True
    assert Settings.from_env({"DRILL_FREEZE_LOOP": "0"}).DRILL_FREEZE_LOOP is False
    for bad in ({"STALE_AFTER_S": "abc"}, {"TICK_S": "40"}, {"DISPLAY_TZ": "Нет/Такого"},
                {"STALE_AFTER_S": "12"},    # срок такта min(11, 6) − 2·3 = 0 — отказ
                {"DRILL_FREEZE_LOOP": "yes"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)


def test_tick_budget_keeps_age_under_stale():
    # срок такта d ≤ min(STALE − TICK, STALE/2) с запасом на одну проверку (2 срока сокета):
    # возраст снимка до следующего ≤ max(TICK, d) + d ≤ STALE
    assert Settings().tick_budget_s == 9          # min(30 − 10, 15) − 2·max(3, 3)
    assert Settings(TICK_S=1).tick_budget_s == 9
    assert Settings(STALE_AFTER_S=60, DOCKER_TIMEOUT_S=5).tick_budget_s == 30 - 10
    # запас на начатую проверку — 2 срока сокета; HTTP-проверка целиком идёт не дольше
    # HTTP_CHECK_SPANS сроков, поэтому больше запаса константа быть не может
    s = Settings()
    socket_s = max(s.DOCKER_TIMEOUT_S, NET_CHECK_TIMEOUT_S)
    reserve = (min(s.STALE_AFTER_S - s.TICK_S, s.STALE_AFTER_S / 2) - s.tick_budget_s) / socket_s
    assert HTTP_CHECK_SPANS <= reserve == 2


def test_run_checks_docker_down_marks_containers_unknown():
    comps = [{"id": "sp-ro", "title": "Прокси сокета сторожа", "kind": "docker_ping"},
             {"id": "sp-ctl", "title": "Прокси сокета серверного слоя", "kind": "container",
              "container": "pcbk-sp-ctl", "sleeping_ok": False}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.state) for c in checks] == [("sp-ro", "fail"), ("sp-ctl", "unknown")]
    assert "прокси сокета" in checks[1].detail


def test_run_checks_all_kinds(fake_proxy, tls_server, fake_core):
    comps = [{"id": "edge", "title": "Входной прокси", "kind": "tls", "host": "127.0.0.1",
              "port": tls_server.port},
             {"id": "sp-ro", "title": "Прокси сокета сторожа", "kind": "docker_ping"},
             {"id": "sp-ctl", "title": "Прокси сокета серверного слоя", "kind": "container",
              "container": "pcbk-sp-ctl", "sleeping_ok": False},
             {"id": "memory", "title": "Память сервера", "kind": "memory"},
             {"id": "web", "title": "Веб", "kind": "http", "url": fake_proxy.url + "/v1.44/_ping"},
             {"id": "core", "title": "Служба данных", "kind": "http",
              "url": fake_core.base + "/healthz/data", "ok_detail": "жива"},
             {"id": "historian", "title": "Историан БДРВ", "kind": "historian", "url": fake_core.url},
             {"id": "llm", "title": "OpenRouter и бюджет", "kind": "absent"}]
    now = now_utc()
    # пороги — из настроек: 100 с при HIST_WARN_S=60 — предупреждение
    fake_core.set({"checked_at": now.isoformat(), "age_s": 100.0, "skew_s": 0.0, "error": None,
                   "error_text": None, "tags": 8, "catalog_age_s": 60.0})
    settings = replace(SETTINGS, TLS_CAFILE=tls_server.cafile, HIST_WARN_S=60, HIST_FAIL_S=120)
    checks = run_checks(comps, DockerReader(fake_proxy.url, 0.5), settings, now)
    assert [(c.component, c.state) for c in checks] == [
        ("edge", "ok"), ("sp-ro", "ok"), ("sp-ctl", "ok"), ("memory", "ok"), ("web", "ok"),
        ("core", "ok"), ("historian", "warn"), ("llm", "absent")]
    assert checks[7].detail == "ещё не установлен" and "11718 МиБ" in checks[3].detail
    assert (checks[4].detail, checks[5].detail) == ("отвечает", "жива")
    assert checks[6].detail == "последняя метка старше 60 с"
    # давний опрос — по HIST_STALE_S
    fake_core.set({"checked_at": (now - timedelta(seconds=31)).isoformat(), "age_s": 1.0,
                   "skew_s": 0.0, "error": None, "error_text": None, "tags": 8, "catalog_age_s": 60.0})
    checks = run_checks(comps[6:7], DockerReader(fake_proxy.url, 0.5),
                        replace(settings, HIST_STALE_S=30), now)
    assert (checks[0].state, checks[0].detail) == ("unknown", "служба давно не опрашивала историан")


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


def test_drill_freeze_loop_goes_stale_while_http_serves(tmp_path, monkeypatch, caplog):
    # учения «цикл молчит при живом HTTP»: первый такт есть, дальше снимок стареет
    monkeypatch.setattr(pcbk_watchdog.main, "NET_CHECK_TIMEOUT_S", 0.1)
    settings = replace(SETTINGS, STALE_AFTER_S=1, TICK_S=0.2, DOCKER_TIMEOUT_S=0.1,
                       DRILL_FREEZE_LOOP=True)
    state, stop = WatchState(), threading.Event()
    srv = start_test_server(state, settings=settings)
    comps = [{"id": "memory", "title": "Память сервера", "kind": "memory"}]
    caplog.set_level("INFO", logger="pcbk_watchdog")
    loop = threading.Thread(target=run_loop, daemon=True, args=(
        state, Journal(str(tmp_path / "j.db")), comps, DockerReader("http://127.0.0.1:9", 0.1),
        settings, stop))
    loop.start()
    try:
        first_by = time.monotonic() + 2
        while state.snapshot is None and time.monotonic() < first_by:
            time.sleep(0.02)
        first = state.snapshot
        assert first is not None and http_status(srv, "/healthz") == 200
        time.sleep(1.5)                                  # > STALE_AFTER_S, при такте 0,2 с
        assert state.snapshot is first                   # тактов больше не было
        assert http_status(srv, "/healthz") == 503
        code, _, body = http_get(srv, "/status")
        assert code == 200 and re.search(r'id="silence"(?![^>]*hidden)', body.decode())
        assert json.loads(http_get(srv, "/status.json")[2])["stale"] is True
    finally:
        stop.set()
        loop.join(2)
    assert not loop.is_alive()                           # замерший цикл выходит по stop
    assert len([r for r in caplog.records if "DRILL_FREEZE_LOOP" in r.getMessage()]) == 1


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


def test_components_file_d4a():                    # было test_components_file_d3a: всё прежнее, кроме строки absent
    comps = {c["id"]: c for c in load_components("components.json")}
    assert (comps["core"]["url"], comps["core"]["ok_detail"], comps["core"]["title"]) == \
           ("http://pcbk-core:8000/healthz/data", "жива", "Служба данных")
    assert comps["historian"]["kind"] == "historian"
    assert (comps["historian"]["url"], comps["historian"]["title"]) == \
           ("http://pcbk-core:8000/health/historian", "Историан БДРВ")      # П17: проверка Д3а сохранена
    assert (comps["llm"]["kind"], comps["llm"]["title"], comps["llm"]["url"]) == \
           ("llm", "LLM-прокси и бюджет", "http://pcbk-core:8000/health/llm")
    assert not [c for c in comps.values() if c["kind"] == "absent"]
    assert list(comps)[:4] == ["edge", "sp-ro", "sp-ctl", "memory"]
    students = [c for i, c in comps.items() if i.startswith("student-")]
    assert [(c["id"], c["title"], c["kind"], c["container"], c["sleeping_ok"]) for c in students] == \
           [(f"student-{n:02d}", f"Рабочее место {n:02d}", "container", f"pcbk-student-{n:02d}", True)
            for n in range(1, 11)]


def test_components_file_d4b():
    comps = load_components("components.json")
    ids = [c["id"] for c in comps]
    assert ids[-8:-3] == ["core", "core-container", "historian", "llm", "gateway"]       # Д10а: строка teacher — последней
    cc = comps[ids.index("core-container")]
    assert (cc["kind"], cc["container"], cc["sleeping_ok"], cc["title"]) == \
           ("container", "pcbk-core", False, "Серверный слой — контейнер")
    # ревью задачи 3, I1: свой текст unhealthy только у core-container, у мест — текст Д2 по умолчанию
    assert cc["unhealthy_detail"] == "процесс core не отвечает на проверку здоровья"
    assert len(cc["unhealthy_detail"]) <= 80
    assert [c["id"] for c in comps if "unhealthy_detail" in c] == ["core-container"]


def test_components_file_d5a():
    comps = load_components("components.json")
    ids = [c["id"] for c in comps]
    g = comps[ids.index("gateway")]
    assert ids[-4] == "gateway"
    assert (g["title"], g["kind"], g["url"]) == ("Шлюз и вход", "gateway", "http://pcbk-core:8000/health/gateway")


def test_components_have_web_row():
    comps = load_components("components.json")
    assert comps[-3] == {"id": "web", "title": "Веб-чат", "kind": "web", "url": "http://pcbk-edge:8080/chat/",
                         "reports_url": "http://pcbk-core:8000/health/web"}                # Д6б: отчёты браузеров


def test_components_have_agents_row():
    assert load_components("components.json")[-2] == {"id": "agents", "title": "Агенты студентов", "kind": "agents",
                                                      "url": "http://pcbk-core:8000/health/agents"}


def test_components_have_teacher_row():                                        # задача А1 Д10а (Д10-R96)
    assert load_components("components.json")[-1] == {"id": "teacher", "title": "Кабинет преподавателя и доли",
                                                      "kind": "teacher", "url": "http://pcbk-core:8000/health/teacher"}


def test_teacher_stale_setting():
    assert Settings.from_env({}).TEACHER_STALE_S == 120
    with pytest.raises(ValueError, match="TEACHER_STALE_S"):
        Settings.from_env({"TEACHER_STALE_S": "0"})


def test_teacher_stale_bounds_and_tick_budget():                               # сверх брифа
    assert Settings.from_env({"TEACHER_STALE_S": "20"}).TEACHER_STALE_S == 20   # ручка учения
    for bad in ({"TEACHER_STALE_S": "-5"}, {"TEACHER_STALE_S": "abc"}, {"TEACHER_STALE_S": "1.5"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)
    # как AGENTS_STALE_S: проверка кабинета — один HTTP-запрос, правило срока такта порог не затрагивает
    assert Settings(TEACHER_STALE_S=1).tick_budget_s == Settings().tick_budget_s


def test_run_checks_teacher_kind(json_site):
    # вид teacher идёт через _check_one в check_teacher с порогом TEACHER_STALE_S; note доходит до снимка
    assert pcbk_watchdog.main.KINDS["teacher"] == ("url",)
    assert "teacher" not in pcbk_watchdog.main.LOCAL_KINDS
    reply = {"checked_at": (T0 - timedelta(seconds=30)).isoformat(), "shares_loaded": True, "shares_ok": True,
             "shares_drift": 0, "shares_sum_usd": 999.5, "total_usd": 1000.0, "store_ok": True, "reads_1h": 2,
             "read_failed_1h": 0, "changes_1h": 4, "change_failed_1h": 0, "role_refused_1h": 0, "drill": ""}
    comps = [{"id": "teacher", "title": "Кабинет преподавателя и доли", "kind": "teacher",
              "url": json_site(200, reply)}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.title, c.state, c.detail, c.note) for c in checks] == [
        ("teacher", "Кабинет преподавателя и доли", "ok", "доли сходятся, журнал действий пишется",
         "распределено $999.50 из $1000; прочитано за час: 2; изменений за час: 4")]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), replace(SETTINGS, TEACHER_STALE_S=20), T0)
    assert (checks[0].state, checks[0].detail) == ("fail", "сверка долей молчит")
    comps[0]["url"] = "http://127.0.0.1:9/health/teacher"
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert (checks[0].state, checks[0].detail) == ("fail", "кабинет преподавателя не отвечает")


def test_run_checks_web_kind(web_site):                                        # M2 предпроверки Д6а
    # вид web идёт через _check_one в check_web; note доходит до снимка
    assert pcbk_watchdog.main.KINDS["web"] == ("url",)
    assert "web" not in pcbk_watchdog.main.LOCAL_KINDS
    root, url = web_site
    put_build(root)
    comps = [{"id": "web", "title": "Веб-чат", "kind": "web", "url": url}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.title, c.state, c.detail, c.note) for c in checks] == [
        ("web", "Веб-чат", "ok", "страница чата отдаётся", "сборка abc123")]
    comps[0]["url"] = "http://127.0.0.1:9/chat/"
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert (checks[0].state, checks[0].detail) == ("fail", "не отвечает")


def test_run_checks_web_reports_url(web_site, fake_core):                       # Д6б: reports_url доходит до check_web
    root, url = web_site
    put_build(root)
    fake_core.set_web(200, {"boot_failed_1h": 0, "ui_crash_1h": 2, "turn_silent_1h": 0, "stream_down_1h": 0})
    comps = [{"id": "web", "title": "Веб-чат", "kind": "web", "url": url, "reports_url": fake_core.base + "/health/web"}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.state, c.detail, c.note) for c in checks] == [("warn", "чат падает в браузере", "2 за час")]


def test_agents_stale_setting():                                               # задача А1 Д7а
    assert Settings().AGENTS_STALE_S == 60
    assert Settings.from_env({"AGENTS_STALE_S": "20"}).AGENTS_STALE_S == 20     # ручка учения
    for bad in ({"AGENTS_STALE_S": "0"}, {"AGENTS_STALE_S": "-5"}, {"AGENTS_STALE_S": "abc"},
                {"AGENTS_STALE_S": "1.5"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)
    # как GW_STALE_S: проверка агентов — один HTTP-запрос, правило срока такта порог не затрагивает
    assert Settings(AGENTS_STALE_S=1).tick_budget_s == Settings().tick_budget_s


def test_run_silent_setting():                                                  # задача А1 Д8а
    assert Settings().LLM_RUN_SILENT_S == 300
    assert Settings.from_env({"LLM_RUN_SILENT_S": "10"}).LLM_RUN_SILENT_S == 10
    for bad in ({"LLM_RUN_SILENT_S": "0"}, {"LLM_RUN_SILENT_S": "abc"}, {"LLM_RUN_SILENT_S": "1.5"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)


def test_run_checks_llm_passes_run_silent(fake_core):                            # сверх брифа: _check_one → check_llm
    fake_core.set_llm(llm_obj(run={"id": "d8-drill", "cap_usd": 1.0, "spent_usd": 0.0, "callers": 1,
                                   "calls_10m": 0, "idle_s": 11.0}))
    comps = [{"id": "llm", "title": "LLM-прокси и бюджет", "kind": "llm", "url": fake_core.base + "/health/llm"}]
    reader = DockerReader("http://127.0.0.1:9", 0.5)
    note = "расход $12.35 из $1000 · прогон d8-drill: $0.00 из $1.00, вызовов за 10 мин: 0"
    quick = replace(SETTINGS, LLM_RUN_SILENT_S=10)
    assert [(c.state, c.detail, c.note) for c in run_checks(comps, reader, quick, T0)] == \
        [("warn", "прогон отбора молчит — к модели давно никто не обращался", note)]
    assert [(c.state, c.note) for c in run_checks(comps, reader, SETTINGS, T0)] == [("ok", note)]
    with pytest.raises(ValueError):
        replace(SETTINGS, LLM_RUN_SILENT_S=-1)


def test_run_checks_agents_kind(json_site):
    # вид agents идёт через _check_one в check_agents с порогом AGENTS_STALE_S; note доходит до снимка
    assert pcbk_watchdog.main.KINDS["agents"] == ("url",)
    assert "agents" not in pcbk_watchdog.main.LOCAL_KINDS
    reply = {"checked_at": (T0 - timedelta(seconds=30)).isoformat(), "store_ok": True, "dir_error": None, "pending": 1,
             "pending_oldest_s": 4.0, "saved_1h": 3, "refused_1h": 0, "unavailable_1h": 0, "visible_1h": 3,
             "not_visible_1h": 0, "quarantined_1h": 0, "reload_failed_1h": 0, "reload_delayed_1h": 0,
             "image_config_1h": 0, "drill": ""}
    comps = [{"id": "agents", "title": "Агенты студентов", "kind": "agents", "url": json_site(200, reply)}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.title, c.state, c.detail, c.note) for c in checks] == [
        ("agents", "Агенты студентов", "ok", "агенты сохраняются", "сохранено за час: 3; ждут перезагрузки: 1")]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), replace(SETTINGS, AGENTS_STALE_S=20), T0)
    assert (checks[0].state, checks[0].detail) == ("fail", "перезагрузка агентов молчит")
    comps[0]["url"] = "http://127.0.0.1:9/health/agents"
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert (checks[0].state, checks[0].detail) == ("fail", "служба агентов не отвечает")


def test_gw_stale_setting():
    assert Settings.from_env({"GW_STALE_S": "20"}).GW_STALE_S == 20             # ручка учения
    with pytest.raises(ValueError):
        Settings.from_env({"GW_STALE_S": "0"})


def test_gw_stale_default_and_bounds():
    assert Settings().GW_STALE_S == 120
    for bad in ({"GW_STALE_S": "-5"}, {"GW_STALE_S": "abc"}, {"GW_STALE_S": "1.5"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)
    # как HIST_*: проверка шлюза — один HTTP-запрос, правило срока такта порог не затрагивает
    assert Settings(GW_STALE_S=1).tick_budget_s == Settings().tick_budget_s


def test_run_checks_gateway_kind(fake_core):
    # вид gateway идёт через _check_one в check_gateway с порогом GW_STALE_S; note доходит до снимка
    assert pcbk_watchdog.main.KINDS["gateway"] == ("url",)
    assert "gateway" not in pcbk_watchdog.main.LOCAL_KINDS
    fake_core.set_gateway(gw(age=30, running_n=3))
    comps = [{"id": "gateway", "title": "Шлюз и вход", "kind": "gateway", "url": fake_core.base + "/health/gateway"}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.title, c.state, c.detail, c.note) for c in checks] == [
        ("gateway", "Шлюз и вход", "ok", "принимает вход", "работает мест: 3")]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), replace(SETTINGS, GW_STALE_S=20), T0)
    assert (checks[0].state, checks[0].detail) == ("fail", "уборщик простоя молчит — места не засыпают")
    comps[0]["url"] = "http://127.0.0.1:9/health/gateway"
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert (checks[0].state, checks[0].detail) == ("fail", "шлюз не отвечает")


def test_run_checks_passes_unhealthy_detail():                                  # ревью задачи 3, I1
    class Docker:
        def inspect(self, name):
            return insp(running=True, health="unhealthy")
    comps = [{"id": "core-container", "title": "Серверный слой — контейнер", "kind": "container",
              "container": "pcbk-core", "sleeping_ok": False, "unhealthy_detail": "процесс core не отвечает"},
             {"id": "student-01", "title": "Рабочее место 01", "kind": "container",
              "container": "pcbk-student-01", "sleeping_ok": True}]
    checks = run_checks(comps, Docker(), SETTINGS, T0)
    assert [(c.component, c.state, c.detail) for c in checks] == [
        ("core-container", "fail", "процесс core не отвечает"), ("student-01", "fail", "OpenCode не отвечает")]


def test_absent_kind_stays_for_synthetic_sets():   # П17: вида absent нет в components.json, но он есть
    assert pcbk_watchdog.main.KINDS["absent"] == () and pcbk_watchdog.main.LOCAL_KINDS == {"memory", "absent"}
    assert pcbk_watchdog.main.KINDS["llm"] == ("url",)


def test_run_checks_llm_kind(fake_core):
    # вид llm идёт через _check_one в check_llm; note доходит до снимка
    fake_core.set_llm(llm_obj(spent_usd=501.0, ledger_usd=501.0))
    comps = [{"id": "llm", "title": "LLM-прокси и бюджет", "kind": "llm", "url": fake_core.base + "/health/llm"}]
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert [(c.component, c.title, c.state, c.detail, c.note) for c in checks] == [
        ("llm", "LLM-прокси и бюджет", "warn", "израсходовано больше 50 % бюджета", "расход $501.00 из $1000")]
    comps[0]["url"] = "http://127.0.0.1:9/health/llm"
    checks = run_checks(comps, DockerReader("http://127.0.0.1:9", 0.5), SETTINGS, T0)
    assert (checks[0].state, checks[0].detail) == ("fail", "LLM-прокси не отвечает")


def test_hist_settings_validation():
    assert Settings.from_env({"HIST_WARN_S": "60", "HIST_FAIL_S": "120"}).HIST_FAIL_S == 120   # ручка учения
    for warn, fail in ((0, 900), (900, 300), (300, 300)):
        with pytest.raises(ValueError):
            replace(SETTINGS, HIST_WARN_S=warn, HIST_FAIL_S=fail)
    assert (Settings().HIST_WARN_S, Settings().HIST_FAIL_S, Settings().HIST_STALE_S) == (300, 900, 140)
    for bad in ({"HIST_STALE_S": "0"}, {"HIST_STALE_S": "-5"}, {"HIST_WARN_S": "-1"},
                {"HIST_FAIL_S": "abc"}):
        with pytest.raises(ValueError):
            Settings.from_env(bad)
    # HIST_* правило срока такта не затрагивают
    assert Settings(HIST_WARN_S=1, HIST_FAIL_S=2, HIST_STALE_S=1).tick_budget_s == Settings().tick_budget_s


def test_components_file_d1_details():
    comps = {c["id"]: c for c in load_components("components.json")}
    assert (comps["edge"]["host"], comps["edge"]["port"], comps["edge"]["title"]) == \
           ("pcbk-edge", 8443, "Входной прокси")
    assert (comps["sp-ctl"]["container"], comps["sp-ctl"]["sleeping_ok"]) == ("pcbk-sp-ctl", False)
    assert comps["student-07"]["title"] == "Рабочее место 07"
    assert comps["llm"]["title"] == "LLM-прокси и бюджет"


def test_load_components_rejects_bad_config(tmp_path):
    for bad in ([{"id": "a", "title": "A", "kind": "shell"}],
                [{"id": "a", "title": "A", "kind": "container", "container": "dify-api",
                  "sleeping_ok": False}],
                [{"id": "a", "title": "A", "kind": "absent"}, {"id": "a", "title": "B", "kind": "absent"}],
                [{"id": "a", "title": "A", "kind": "historian"}],                      # нет url
                [{"id": "a", "title": "A", "kind": "llm"}],                            # нет url
                [{"id": "a", "title": "A", "kind": "gateway"}],                        # нет url
                [{"id": "a", "title": "A", "kind": "web"}],                            # нет url
                [{"id": "a", "title": "A", "kind": "http", "url": "http://x/", "ok_detail": ""}],
                [{"id": "a", "title": "A", "kind": "http", "url": "http://x/", "ok_detail": 1}],
                [{"id": "a", "title": "A", "kind": "historian", "url": "http://x/", "ok_detail": "жив"}],
                # ревью задачи 3, I1: unhealthy_detail — непустая строка до 80 знаков и только у вида container
                [{"id": "a", "title": "A", "kind": "http", "url": "http://x/", "unhealthy_detail": "не отвечает"}],
                [{"id": "a", "title": "A", "kind": "container", "container": "pcbk-core", "sleeping_ok": False,
                  "unhealthy_detail": " "}],
                [{"id": "a", "title": "A", "kind": "container", "container": "pcbk-core", "sleeping_ok": False,
                  "unhealthy_detail": 1}],
                [{"id": "a", "title": "A", "kind": "container", "container": "pcbk-core", "sleeping_ok": False,
                  "unhealthy_detail": "ж" * 81}],
                # Д6б (preflight Н10): reports_url — строка http:// с хостом и только у вида web
                [{"id": "a", "title": "A", "kind": "web", "url": "http://x/chat/", "reports_url": 1}],
                [{"id": "a", "title": "A", "kind": "web", "url": "http://x/chat/", "reports_url": "https://x/health/web"}],
                [{"id": "a", "title": "A", "kind": "web", "url": "http://x/chat/", "reports_url": "http:///health/web"}],
                [{"id": "a", "title": "A", "kind": "web", "url": "http://x/chat/", "reports_url": ""}],
                [{"id": "a", "title": "A", "kind": "gateway", "url": "http://x/", "reports_url": "http://x/health/web"}]):
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


# Д11-R115, m-10 р1: счётчик oom_kill — до и после осмотра, проводка MemState через run_loop → tick → run_checks
class RestartingDocker:
    """Первый осмотр — место работает; дальше — перезапущено, и за этот осмотр счётчик oom_kill вырос.

    at — «сейчас» проверок: T0 для run_checks, часы машины для run_loop (свежий перезапуск — моложе 15 мин)."""
    def __init__(self, vm, at=T0):
        self.vm, self.n, self.at = vm, 0, at
    def inspect(self, name):
        self.n += 1
        if self.n == 1:
            return insp(running=True, started=self.at - timedelta(hours=1))
        if self.n == 2:
            self.vm.write_text("oom_kill 6\n")
        return insp(running=True, restarts=1, started=self.at - timedelta(seconds=5))
    def ping(self):
        pass


COMPS = [{"id": "memory", "title": "Память сервера", "kind": "memory"},
         {"id": "student-01", "title": "Рабочее место 01", "kind": "container", "container": "pcbk-student-01",
          "sleeping_ok": True}]


def settings_with(tmp_path, **kw):
    vm = tmp_path / "vmstat"; vm.write_text("oom_kill 5\n")
    return vm, replace(SETTINGS, VMSTAT_PATH=str(vm), PRESSURE_PATH=str(tmp_path / "none"), **kw)


def test_run_checks_reads_oom_around_inspect(tmp_path):
    vm, settings = settings_with(tmp_path)
    docker, mem = RestartingDocker(vm), MemState(settings)
    run_checks(COMPS, docker, settings, T0 - timedelta(seconds=10), mem=mem)
    assert run_checks(COMPS, docker, settings, T0, mem=mem)[1].detail.startswith("перезапущен: убит по памяти")
    assert run_checks(COMPS, docker, settings, T0 + timedelta(minutes=1), mem=mem)[0].state == "ok"


def test_run_checks_own_kill_not_outside_after_grace(tmp_path):              # доводка р1 (ревью А1, I-2): attribute
    vm, settings = settings_with(tmp_path)
    docker, mem = RestartingDocker(vm), MemState(settings)
    run_checks(COMPS, docker, settings, T0 - timedelta(seconds=10), mem=mem)
    run_checks(COMPS, docker, settings, T0, mem=mem)
    run_checks(COMPS, docker, settings, T0 + timedelta(seconds=10), mem=mem)
    rows = run_checks(COMPS, docker, settings, T0 + timedelta(seconds=40), mem=mem)   # > grace 20 + такт 10
    assert (rows[0].state, mem.history.outside(T0 + timedelta(seconds=40))) == ("ok", 0)


class LongRestartingDocker:
    """Место убито по памяти и дольше match_s в окне Restarting (откат политики перезапуска), затем работает."""
    def __init__(self):
        self.n = 0
    def inspect(self, name):
        self.n += 1
        if self.n == 1:
            return insp(running=True, started=T0 - timedelta(hours=1))
        if self.n <= 4:
            return insp(running=True, restarting=True, restarts=1, oom=True, started=T0 - timedelta(hours=1))
        return insp(running=True, restarts=1, started=T0 + timedelta(seconds=35))


def test_run_checks_late_restart_credit_does_not_hide_next_kill(tmp_path):   # m-5 ревью А1: v_after в attribute
    vm, settings = settings_with(tmp_path)
    docker, mem = LongRestartingDocker(), MemState(settings)
    run_checks(COMPS, docker, settings, T0 - timedelta(seconds=10), mem=mem)
    vm.write_text("oom_kill 6\n")                                           # место убито: прирост замерен в T0
    for s in (0, 10, 20):
        run_checks(COMPS, docker, settings, T0 + timedelta(seconds=s), mem=mem)
    rows = run_checks(COMPS, docker, settings, T0 + timedelta(seconds=40), mem=mem)   # работает — позже match_s
    assert rows[1].detail.startswith("перезапущен: убит по памяти")
    vm.write_text("oom_kill 7\n")                                           # убийство вне мест (Dify)
    run_checks(COMPS, docker, settings, T0 + timedelta(seconds=50), mem=mem)
    assert mem.history.outside(T0 + timedelta(seconds=80)) == 2             # кредит его не поглотил


class VmstatLostDocker:
    """Место работает, к следующему осмотру перезапущено — а /proc/vmstat к этому такту перестал читаться."""
    def __init__(self):
        self.n = 0
    def inspect(self, name):
        self.n += 1
        if self.n == 1:
            return insp(running=True, started=T0 - timedelta(hours=1))
        return insp(running=True, restarts=1, started=T0 - timedelta(seconds=5))


def test_run_checks_vmstat_lost_is_unknown_not_zero(tmp_path):              # доводка р1 (ревью А1, I-1)
    vm, settings = settings_with(tmp_path)
    docker, mem = VmstatLostDocker(), MemState(settings)
    run_checks(COMPS, docker, settings, T0 - timedelta(seconds=10), mem=mem)
    vm.unlink()
    memory, place = run_checks(COMPS, docker, settings, T0, mem=mem)
    assert (memory.state, memory.detail) == ("unknown", MEM_SENSOR_SILENT_DETAIL)
    assert "нет данных: vmstat" in memory.note.split(" · ")
    assert place.detail.startswith("перезапущен после сбоя")                 # причина неизвестна, не «0 убийств»


def test_run_loop_wires_mem_state(tmp_path):                                # проводка, а не только run_checks
    vm, settings = settings_with(tmp_path, TICK_S=0.05)
    state, stop = WatchState(), threading.Event()
    t = threading.Thread(target=run_loop, args=(state, NoJournalWrite(), COMPS, RestartingDocker(vm, now_utc()),
                                                settings, stop))
    t.start()
    try:
        wait_until(lambda: state.snapshot
                   and state.snapshot.checks[1].detail.startswith("перезапущен: убит по памяти"), 5)
    finally:
        stop.set(); t.join(5)


def test_memory_settings():
    s = Settings.from_env({})
    assert (s.VMSTAT_PATH, s.PRESSURE_PATH, s.SWAP_GROW_WARN_MIB, s.PSI_FULL_MAX) == \
           ("/proc/vmstat", "/proc/pressure/memory", 512, 10.0)
    for name, bad in (("SWAP_GROW_WARN_MIB", "0"), ("PSI_FULL_MAX", "0"), ("PSI_FULL_MAX", "101")):
        with pytest.raises(ValueError, match=name):
            Settings.from_env({name: bad})
