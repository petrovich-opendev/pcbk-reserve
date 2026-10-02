import http.client
import itertools
import json
import re
import time
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from helpers import T0, gw, insp, llm_obj, now_utc, put_build
from pcbk_watchdog.checks import (ACCOUNT_WARN_USD, AGENTS_DRILL_MODES, AGENTS_KEYS, BUDGET_STEPS, DRIFT_WARN_USD,
                                  EGRESS_STALE_S, AgentsReply, agents_verdict, check_agents,
                                  GW_DRILL_MODES, GW_KEYS_D5A, GW_KEYS_D5B, GW_OFF_CONTRACT_DETAIL,
                                  GW_SP_CTL_STATES, LLM_STALE_S, MAX_BODY_BYTES,
                                  LOGIN_FAILS_WARN, REASON_CHARS, WAKE_FAILS_FAIL, Check, GatewayReply, _http_get,
                                  check_container, check_gateway,
                                  check_historian, check_http, check_llm, check_memory, check_tls, check_web,
                                  fmt_total, fmt_usd, not_installed, tls_verdict,
                                  LLM_RUN_SILENT_S, RUN_CAP_DETAIL, RUN_KEYS, RUN_SILENT_DETAIL, LlmReply,
                                  DRILL_NOTE, FAILING_MAX, TOOL_FAILING_DETAIL, llm_note, llm_verdict,
                                  TEACHER_DETAILS, TEACHER_DRILL_MODES, TEACHER_KEYS, TEACHER_STUDENT_FAIL,
                                  TeacherReply, check_teacher, teacher_verdict)
from pcbk_watchdog import checks as checks_module
from pcbk_watchdog.journal import Journal

MEMINFO = "MemTotal: 16384000 kB\nMemAvailable: {} kB\n"


def test_memory_ok_warn_fail():
    assert check_memory(MEMINFO.format(11_700 * 1024)).state == "ok"
    assert check_memory(MEMINFO.format(1_500 * 1024)).state == "warn"
    assert check_memory(MEMINFO.format(900 * 1024)).state == "fail"
    assert "11700 МиБ" in check_memory(MEMINFO.format(11_700 * 1024)).detail


def test_memory_warn_fail_detail_is_constant_threshold():
    # в warn/fail текст не зависит от числа — иначе журнал пишет событие каждый такт
    assert check_memory(MEMINFO.format(1_500 * 1024)).detail == "свободно меньше 2048 МиБ"
    assert check_memory(MEMINFO.format(1_400 * 1024)).detail == "свободно меньше 2048 МиБ"
    assert check_memory(MEMINFO.format(900 * 1024)).detail == "свободно меньше 1024 МиБ"
    custom = check_memory(MEMINFO.format(3_000 * 1024), warn_mib=4096, fail_mib=512)
    assert (custom.state, custom.detail) == ("warn", "свободно меньше 4096 МиБ")
    custom = check_memory(MEMINFO.format(300 * 1024), warn_mib=4096, fail_mib=512)
    assert (custom.state, custom.detail) == ("fail", "свободно меньше 512 МиБ")


def test_memory_without_field_is_unknown():
    assert check_memory("MemTotal: 1 kB\n").state == "unknown"


def stu(i):
    return check_container("student-01", "Рабочее место 01", i, sleeping_ok=True, now=T0)


def test_student_states():
    assert (stu(insp(running=True)).state, stu(insp(running=True)).detail) == ("ok", "работает")
    for code in (0, 137, 143):
        assert (stu(insp(code=code)).state, stu(insp(code=code)).detail) == ("ok", "спит")
    assert (stu(insp(oom=True, code=137)).state, stu(insp(oom=True, code=137)).detail) == ("fail", "убит по памяти")
    assert stu(insp(running=True, restarting=True, restarts=1)).state == "warn"   # так ставит Docker


def test_recent_policy_restart_is_warn_then_ok():
    fresh = stu(insp(running=True, restarts=2, started=T0 - timedelta(minutes=3)))
    assert fresh.state == "warn" and "перезапущен после сбоя" in fresh.detail
    assert "в 11:57 UTC+00:00" in fresh.detail   # время — с явным смещением
    old = stu(insp(running=True, restarts=2, started=T0 - timedelta(minutes=30)))
    assert old.state == "ok" and "сбоев с последнего запуска: 2" in old.detail


def test_restart_time_in_zone_of_now_with_offset():
    now = T0.astimezone(timezone(timedelta(hours=5)))
    r = check_container("student-01", "Рабочее место 01",
                        insp(running=True, restarts=1, started=T0 - timedelta(minutes=3)),
                        sleeping_ok=True, now=now)
    assert r.state == "warn" and "в 16:57 UTC+05:00" in r.detail


def test_crash_loop_is_fail():
    for i in (insp(running=True, restarts=3, started=T0 - timedelta(seconds=20)),
              insp(running=True, restarting=True, restarts=5, started=T0 - timedelta(minutes=2))):
        assert stu(i).state == "fail" and "падает в цикле" in stu(i).detail


def test_crash_loop_detail_is_constant(tmp_path):
    # число перезапусков в тексте дало бы новое событие журнала на каждый перезапуск
    loops = [stu(insp(running=True, restarts=n, started=T0 - timedelta(seconds=20)))
             for n in (3, 4, 5, 11, 22)]
    assert {(c.state, c.detail) for c in loops} == {("fail", "падает в цикле (перезапуски подряд)")}
    journal = Journal(str(tmp_path / "journal.db"))
    assert sum(len(journal.record([c], T0 + timedelta(seconds=10 * i)))
               for i, c in enumerate(loops)) == 1


def test_health_unhealthy_is_fail():
    r = stu(insp(running=True, health="unhealthy"))
    assert (r.state, r.detail) == ("fail", "OpenCode не отвечает")
    fresh = stu(insp(running=True, restarts=1, started=T0 - timedelta(minutes=3), health="unhealthy"))
    assert (fresh.state, fresh.detail) == ("fail", "OpenCode не отвечает")    # сильнее «перезапущен после сбоя»


def test_health_row_only_for_running_container():                            # Review Focus 3
    starting = stu(insp(running=True, health="starting", last_end=None, started=T0 - timedelta(seconds=30)))
    assert (starting.state, starting.detail) == ("ok", "работает")
    assert stu(insp(running=True, health="healthy")).detail == "работает"
    stale = stu(insp(code=143, health="unhealthy", last_end=T0 - timedelta(hours=2)))
    assert (stale.state, stale.detail) == ("ok", "спит")
    assert stu(insp(running=True, paused=True, health="unhealthy")).detail == "приостановлен"
    loop = stu(insp(running=True, restarts=3, started=T0 - timedelta(seconds=20), health="unhealthy"))
    assert loop.state == "fail" and "падает в цикле" in loop.detail


def test_health_silence_is_fail():                                            # Review Focus 3
    silent = stu(insp(running=True, health="healthy", last_end=T0 - timedelta(minutes=5)))
    assert (silent.state, silent.detail) == ("fail", "проверка здоровья молчит")
    empty = stu(insp(running=True, health="starting", last_end=None))          # запуск час назад, журнала нет
    assert (empty.state, empty.detail) == ("fail", "проверка здоровья молчит")
    assert stu(insp(running=True, health="healthy", last_end=T0 - timedelta(seconds=30))).state == "ok"
    restarted = stu(insp(running=True, health="healthy", last_end=T0 - timedelta(minutes=5),
                         started=T0 - timedelta(seconds=20)))                   # старый журнал, свежий старт
    assert restarted.state == "ok"


# Д4б, задача 3: MCP места по выводу проверки здоровья образа :d4 («200 mcp=<слово>»)

def test_mcp_state_from_health_output():                                       # Review Focus 1
    seen = {out: stu(insp(running=True, health="healthy", output=out))
            for out in ("200 mcp=connected\n", "200 mcp=failed\n", "200\n", "200 mcp=unknown\n")}
    assert [c.state for c in seen.values()] == ["ok", "warn", "ok", "ok"]
    assert seen["200 mcp=failed\n"].detail == "нет связи со службой данных (MCP)"


def test_mcp_unknown_five_in_a_row_is_warn():                                   # раунд 1: молчание MCP; К30
    old = stu(insp(running=True, health="healthy", log=["200 mcp=unknown\n"] * 5))          # старейшая — T0 − 130 с
    start = stu(insp(running=True, health="healthy", log=["200 mcp=unknown\n"] * 5, log_step_s=2))   # старт: T0 − 18 с
    four = stu(insp(running=True, health="healthy", log=["200 mcp=connected\n"] + ["200 mcp=unknown\n"] * 4))
    assert (old.state, old.detail, start.state, four.state) == ("warn", "состояние MCP не читается", "ok", "ok")


# сверх брифа: слова, последняя запись, место строк в таблице, граница 120 с

def test_mcp_words_and_last_entry_only():
    down = ("warn", "нет связи со службой данных (MCP)")
    cases = [(dict(output="200 mcp=disabled\n"), ("ok", "работает")),               # MCP выключен в настройке
             (dict(output="200 mcp=needs_auth\n"), down),
             (dict(output="200 mcp=failed"), down),                                   # без перевода строки
             (dict(output="200 xmcp=failed\n"), ("ok", "работает")),                  # не слово mcp=
             (dict(output="200 mcp=Failed\n"), ("ok", "работает")),                   # слово — только [a-z_]
             (dict(output="200 mcp=\n"), ("ok", "работает")),
             (dict(log=["200 mcp=failed\n", "200 mcp=connected\n"]), ("ok", "работает")),   # новые — последними
             (dict(log=["200 mcp=connected\n", "200 mcp=failed\n"]), down),
             (dict(log=["200 mcp=failed\n", "200\n"]), ("ok", "работает"))]          # последняя — образ :d2
    for kw, want in cases:
        r = stu(insp(running=True, health="healthy", **kw))
        assert (r.state, r.detail) == want, kw


def test_mcp_rows_only_for_healthy_and_after_unhealthy():
    failed = dict(running=True, output="200 mcp=failed\n")
    assert stu(insp(health="unhealthy", **failed)).detail == "OpenCode не отвечает"          # строка выше
    assert stu(insp(health="starting", **failed)).detail == "работает"                        # только healthy
    loop = stu(insp(health="healthy", restarts=3, started=T0 - timedelta(seconds=20), **failed))
    assert loop.detail == "падает в цикле (перезапуски подряд)"
    assert stu(insp(health="healthy", last_end=T0 - timedelta(minutes=5), **failed)).detail == \
           "проверка здоровья молчит"
    fresh = stu(insp(health="healthy", restarts=1, started=T0 - timedelta(minutes=3), **failed))
    assert (fresh.state, fresh.detail) == ("warn", "нет связи со службой данных (MCP)")   # выше «перезапущен»
    unknown = dict(running=True, health="healthy", log=["200 mcp=unknown\n"] * 5)
    fresh = stu(insp(restarts=1, started=T0 - timedelta(minutes=3), **unknown))
    assert (fresh.state, fresh.detail) == ("warn", "состояние MCP не читается")
    assert stu(insp(code=0, health="healthy", output="200 mcp=failed\n")).detail == "спит"


def test_mcp_unknown_needs_five_and_120_s():
    unknown = ["200 mcp=unknown\n"]
    edge = stu(insp(running=True, health="healthy", log=unknown * 5, log_step_s=27.5))      # ровно 120 с — ещё норма
    past = stu(insp(running=True, health="healthy", log=unknown * 5, log_step_s=27.6))      # 120,4 с
    four = stu(insp(running=True, health="healthy", log=unknown * 4, log_step_s=60))        # четыре записи
    bare = stu(insp(running=True, health="healthy", log=["200\n"] + unknown * 4))           # одна без слова
    assert [(c.state, c.detail) for c in (edge, past, four, bare)] == [
        ("ok", "работает"), ("warn", "состояние MCP не читается"), ("ok", "работает"), ("ok", "работает")]


def test_mcp_details_constant_and_short(tmp_path):
    rows = [stu(insp(running=True, health="healthy", output=f"200 mcp={w}\n")) for w in ("failed", "needs_auth")]
    rows += [stu(insp(running=True, health="healthy", log=["200 mcp=unknown\n"] * 5, log_step_s=s)) for s in (30, 60)]
    assert all(len(r.detail) <= 80 for r in rows)
    journal = Journal(str(tmp_path / "journal.db"))
    assert sum(len(journal.record([r], T0 + timedelta(seconds=10 * i))) for i, r in enumerate(rows)) == 2


# доводка по ревью задачи 3

def test_unhealthy_detail_per_component():                                     # I1
    core = check_container("core-container", "Серверный слой — контейнер", insp(running=True, health="unhealthy"),
                           sleeping_ok=False, now=T0, unhealthy_detail="процесс core не отвечает на проверку здоровья")
    assert (core.state, core.detail) == ("fail", "процесс core не отвечает на проверку здоровья")
    assert stu(insp(running=True, health="unhealthy")).detail == "OpenCode не отвечает"      # по умолчанию — текст Д2


def test_mcp_rows_ignore_log_of_previous_run():                                 # M1: журнал прошлого запуска
    woke = T0 - timedelta(seconds=8)
    unknown = stu(insp(running=True, health="healthy", started=woke, last_end=T0 - timedelta(seconds=2),
                       log=["200 mcp=unknown\n"] * 5))            # четыре старые (до T0 − 122 с) и одна новая
    failed = stu(insp(running=True, health="healthy", started=woke, last_end=T0 - timedelta(seconds=60),
                      log=["200 mcp=failed\n"]))                   # новых записей ещё нет
    assert [(c.state, c.detail) for c in (unknown, failed)] == [("ok", "работает")] * 2
    mixed = stu(insp(running=True, health="healthy", started=woke, last_end=T0 - timedelta(seconds=2),
                     log=["200 mcp=failed\n", "200 mcp=connected\n"]))   # старая failed, новая connected
    assert mixed.detail == "работает"
    at_start = stu(insp(running=True, health="healthy", started=woke, last_end=woke, log=["200 mcp=failed\n"]))
    assert at_start.detail == "нет связи со службой данных (MCP)"          # End ровно в момент старта — этот запуск


def test_paused_is_fail():
    r = check_container("sp-ctl", "Прокси сокета серверного слоя", insp(running=True, paused=True),
                        sleeping_ok=False, now=T0)
    assert (r.state, r.detail) == ("fail", "приостановлен")


def test_failed_start_is_fail():
    r = stu(insp(code=127, error="OCI runtime create failed: unknown runtime runsc"))
    assert r.state == "fail" and r.detail.startswith("не запускается: OCI runtime create failed")


def test_failed_start_hides_host_paths():
    # пути выкладки (каталог секретов) на страницу и в журнал не попадают
    err = ('error mounting "/opt/pcbk-reserve/secrets/student-01.pw" to rootfs at "/run/secrets/pw": '
           "mkdir /opt/pcbk-reserve/secrets: permission denied")
    r = stu(insp(code=127, error=err))
    assert r.state == "fail" and "/opt/" not in r.detail and "student-01.pw" not in r.detail
    assert r.detail == ('не запускается: error mounting "…" to rootfs at "/run/secrets/pw": '
                        "mkdir …: permission denied")
    # сперва маска, потом обрезка до 80 знаков: иначе начало пути уцелело бы
    cut = stu(insp(code=127, error="x" * 75 + " /opt/pcbk-reserve/secrets/student-01.pw"))
    assert cut.detail == "не запускается: " + "x" * 75 + " …"


def test_odd_exit_code_is_fail_even_for_student():
    assert stu(insp(code=1)).state == "fail" and "код 1" in stu(insp(code=1)).detail


def test_infra_stopped_is_fail():
    r = check_container("sp-ctl", "Прокси сокета серверного слоя", insp(code=0),
                        sleeping_ok=False, now=T0)
    assert r.state == "fail" and "код 0" in r.detail


def test_missing_container_is_fail():
    r = check_container("student-03", "Рабочее место 03", None, sleeping_ok=True, now=T0)
    assert (r.state, r.detail) == ("fail", "нет контейнера")


def test_not_installed_is_absent_not_fail():
    assert not_installed("core", "Серверный слой").state == "absent"


def test_check_http_down_is_fail():
    assert check_http("edge", "Входной прокси", "http://127.0.0.1:9/healthz", timeout=0.5).state == "fail"


def test_check_http_ok_detail_and_reason(fake_core):
    url = fake_core.base + "/healthz/data"
    fake_core.set_health(200, {"ok": True, "detail": "каталог: 6"})
    assert check_http("core", "Служба данных", url, ok_detail="жива").detail == "жива"
    fake_core.set_health(503, {"ok": False, "detail": "белый список пуст или не найден"})
    r = check_http("core", "Служба данных", url, ok_detail="жива")
    assert (r.state, r.detail) == ("fail", "белый список пуст или не найден")


def test_check_http_reason_only_for_503(fake_core):
    url = fake_core.base + "/healthz/data"
    fake_core.set_health(200, {"ok": True, "detail": "каталог: 6"})
    assert check_http("web", "Веб", url).detail == "отвечает"                  # по умолчанию, как в Д1
    fake_core.set_health(503, {"ok": False, "detail": "ф" * 100})
    assert check_http("core", "Служба данных", url).detail == "ф" * 80          # первые 80 знаков
    for body in ({"ok": False}, {"detail": ["не строка"]}, ["detail"], {"detail": ""}):
        fake_core.set_health(503, body)
        r = check_http("core", "Служба данных", url)
        assert (r.state, r.detail) == ("fail", "отвечает ошибкой HTTP 503")
    for code in (404, 500, 502):                                                 # причина — только при 503 (Д3а-R5)
        fake_core.set_health(code, {"detail": "Not Found"})
        r = check_http("core", "Служба данных", url)
        assert (r.state, r.detail) == ("fail", f"отвечает ошибкой HTTP {code}")
    r = check_http("core", "Служба данных", fake_core.base + "/nope")             # не JSON
    assert (r.state, r.detail) == ("fail", "отвечает ошибкой HTTP 404")


def fresh(age, **kw):
    return {"checked_at": (T0 - timedelta(seconds=10)).isoformat(), "age_s": age, "skew_s": 0.0,
            "error": None, "error_text": None, "tags": 8, "catalog_age_s": 3600.0, **kw}


def hist(fake_core, obj):
    fake_core.set(obj)
    return check_historian("historian", "Историан БДРВ", fake_core.url, T0, 300, 900, 120, timeout=0.5)


def test_historian_verdicts(fake_core):
    cases = [(fresh(42.4), ("ok", "последняя метка 42 с назад")),
             (fresh(300), ("warn", "последняя метка старше 300 с")),
             (fresh(901), ("fail", "последняя метка старше 900 с")),
             (fresh(None, error="auth", error_text="историан отклонил учётные данные — обновите bdrv.env и перезапустите службу"),
              ("fail", "историан отклонил учётные данные — обновите bdrv.env и перезапустите службу")),
             (fresh(10, catalog_age_s=200000.0), ("warn", "каталог тегов старше двух суток")),
             (fresh(1, checked_at=(T0 - timedelta(seconds=121)).isoformat()),
              ("unknown", "служба давно не опрашивала историан")),
             (fresh(None, checked_at=None), ("unknown", "служба ещё не опрашивала историан"))]
    for obj, want in cases:
        r = hist(fake_core, obj)
        assert (r.state, r.detail) == want


def test_historian_boundaries_and_order(fake_core):
    cases = [(fresh(899.9), ("warn", "последняя метка старше 300 с")),
             (fresh(900), ("fail", "последняя метка старше 900 с")),             # порог — «не меньше»
             (fresh(250, catalog_age_s=172800.0), ("ok", "последняя метка 250 с назад")),  # ровно двое суток
             (fresh(5, catalog_age_s=None), ("ok", "последняя метка 5 с назад")),
             (fresh(400, catalog_age_s=200000.0), ("warn", "последняя метка старше 300 с")),
             (fresh(5, checked_at=(T0 - timedelta(seconds=120)).isoformat()),    # ровно stale_s — ещё свежо
              ("ok", "последняя метка 5 с назад")),
             (fresh(5, checked_at="2026-09-29T11:59:50Z"), ("ok", "последняя метка 5 с назад")),
             # давний опрос важнее ошибки в нём: сперва — свежесть самого опроса
             (fresh(5, error="timeout", error_text="историан не ответил вовремя",
                    checked_at=(T0 - timedelta(seconds=200)).isoformat()),
              ("unknown", "служба давно не опрашивала историан")),
             (fresh(5, error="connect", error_text="нет связи с историаном"), ("fail", "нет связи с историаном")),
             (fresh(5, error="auth", error_text="т" * 100), ("fail", "т" * 80)),
             (fresh(5, error="new_code", error_text=None), ("fail", "new_code"))]
    for obj, want in cases:
        r = hist(fake_core, obj)
        assert (r.component, r.title, r.state, r.detail) == ("historian", "Историан БДРВ", *want), obj


def test_historian_core_down_or_garbage_is_unknown(fake_core):
    r = check_historian("historian", "Историан БДРВ", "http://127.0.0.1:9/x", T0, 300, 900, 120, 0.5)
    assert (r.state, r.detail) == ("unknown", "служба данных не отвечает — свежесть неизвестна")
    fake_core.set_raw("не json")
    assert check_historian("historian", "Историан БДРВ", fake_core.url, T0, 300, 900, 120, 0.5).state == "unknown"


# Д4б, задача 1: нагрузка историана и ошибки инструментов (Д4б-R22)

def test_historian_load_rows(fake_core):
    gate = {"user_paused": False, "p95_ms": 100}
    cases = [(fresh(10, gate={**gate, "user_paused": True}),
              ("warn", "запросы людей к историану на паузе после сроков подряд", "")),
             (fresh(10, gate={**gate, "p95_ms": 6200}), ("warn", "историан отвечает медленно: p95 больше 5 с", "")),
             (fresh(10, gate=gate, tools={"recent": 50, "errors": 12}), ("warn", "инструменты отвечают ошибкой", "12 из 50")),
             (fresh(10, gate=gate, tools={"recent": 5, "errors": 5}), ("ok", "последняя метка 10 с назад", "")),
             (fresh(10), ("ok", "последняя метка 10 с назад", ""))]             # служба :d4a — без новых полей
    for obj, want in cases:
        r = hist(fake_core, obj)
        assert (r.state, r.detail, r.note) == want


# сверх брифа: место в таблице, пороги, поля :d4a и ответ не по договору

def test_historian_load_rows_order_and_edges(fake_core):
    gate = {"user_paused": False, "p95_ms": None, "sent_names_total": 3}
    tools = {"recent": 10, "errors": 2}
    old_gate = {"sent_names_total": 0, "refused_unlisted": 0, "in_flight": {"user": 0, "background": 0},
                "auth_latched": False}
    cases = [(fresh(400, gate={**gate, "user_paused": True}), ("warn", "последняя метка старше 300 с", "")),
             (fresh(10, gate={**gate, "user_paused": True, "p95_ms": 9000}, tools=tools, catalog_age_s=200000.0),
              ("warn", "запросы людей к историану на паузе после сроков подряд", "")),
             (fresh(10, gate={**gate, "p95_ms": 9000}, tools=tools),
              ("warn", "историан отвечает медленно: p95 больше 5 с", "")),
             (fresh(10, gate=gate, tools=tools, catalog_age_s=200000.0),
              ("warn", "инструменты отвечают ошибкой", "2 из 10")),                # ровно 20 % при 10 вызовах
             (fresh(10, gate=gate, tools={"recent": 15, "errors": 3}),
              ("warn", "инструменты отвечают ошибкой", "3 из 15")),
             (fresh(10, gate={**gate, "p95_ms": 5000}, tools={"recent": 10, "errors": 1}),
              ("ok", "последняя метка 10 с назад", "")),                          # p95 ровно 5 с — ещё не медленно
             (fresh(10, gate={**gate, "p95_ms": 5000}, tools={"recent": 9, "errors": 9}),
              ("ok", "последняя метка 10 с назад", "")),                          # меньше 10 вызовов — не судим
             (fresh(10, gate=old_gate, catalog_age_s=200000.0), ("warn", "каталог тегов старше двух суток", "")),
             (fresh(10, gate=old_gate), ("ok", "последняя метка 10 с назад", ""))]
    for obj, want in cases:
        r = hist(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj
        assert len(r.detail) <= 80
    for obj in (fresh(10, gate=[]), fresh(10, gate={"user_paused": "да"}), fresh(10, gate={"p95_ms": "6200"}),
                fresh(10, gate={"p95_ms": float("nan")}), fresh(10, gate={"p95_ms": True}), fresh(10, tools=[]),
                fresh(10, tools={"recent": 50}), fresh(10, tools={"recent": 50, "errors": 60}),
                fresh(10, tools={"recent": 50.0, "errors": 12}), fresh(10, tools={"recent": 50, "errors": -1})):
        r = hist(fake_core, obj)
        assert (r.state, r.detail) == OFF_CONTRACT, obj


DOWN = ("unknown", "служба данных не отвечает — свежесть неизвестна")
OFF_CONTRACT = ("unknown", "служба данных ответила не по договору — свежесть неизвестна")


def test_historian_bad_reply_is_unknown(fake_core):
    for url in (fake_core.base + "/nope", fake_core.base + "/healthz/data"):     # HTTP ≠ 200
        fake_core.set_health(503, fresh(5))                                       # тело по договору не спасает
        r = check_historian("historian", "Историан БДРВ", url, T0, 300, 900, 120, 0.5)
        assert (r.state, r.detail) == DOWN, url
    for raw in ("не json", "", "{\"checked_at\": ", "[" * 100_000):
        fake_core.set_raw(raw)
        r = check_historian("historian", "Историан БДРВ", fake_core.url, T0, 300, 900, 120, 0.5)
        assert (r.state, r.detail) == DOWN, raw


def test_historian_off_contract_reply_has_own_text(fake_core):
    # JSON пришёл, но не по договору: состояние то же, текст — свой
    for obj in ([fresh(5)], "строка", 7, None, {"checked_at": T0.isoformat()}, fresh("5"),
                fresh(True), fresh(5, checked_at="вчера"), fresh(5, checked_at=12),
                fresh(5, catalog_age_s="много"), fresh(float("-inf")), fresh(float("nan"))):
        fake_core.set(obj)
        r = check_historian("historian", "Историан БДРВ", fake_core.url, T0, 300, 900, 120, 0.5)
        assert (r.state, r.detail) == OFF_CONTRACT, obj


# Д9а, задача А1: серия ошибок по инструменту и учение DRILL_DATA (Д9-R83)

def test_historian_failing_tools_row(fake_core):                                  # Review Focus 3
    gate = {"user_paused": False, "p95_ms": 100}
    cases = [(fresh(10, gate=gate, tools={"recent": 3, "errors": 3, "failing": ["tag_series"]}),
              ("warn", TOOL_FAILING_DETAIL, "tag_series")),                       # мало вызовов — доля молчит, серия нет
             (fresh(10, gate=gate, tools={"recent": 40, "errors": 6, "failing": ["calc_runtime", "tag_series"]}),
              ("warn", TOOL_FAILING_DETAIL, "calc_runtime, tag_series")),
             (fresh(10, gate=gate, tools={"recent": 50, "errors": 12, "failing": ["tag_now"]}),
              ("warn", "инструменты отвечают ошибкой", "12 из 50")),             # доля — раньше серии
             (fresh(10, gate=gate, tools={"recent": 40, "errors": 0, "failing": []}),
              ("ok", "последняя метка 10 с назад", "")),
             (fresh(10, gate=gate, tools={"recent": 40, "errors": 0}),
              ("ok", "последняя метка 10 с назад", ""))]                          # core старше Д9а
    for obj, want in cases:
        r = hist(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj
        assert len(r.detail) <= 80
    for bad in ({"recent": 3, "errors": 3, "failing": "tag_now"}, {"recent": 3, "errors": 3, "failing": [1]},
                {"recent": 3, "errors": 3, "failing": ["DROP TABLE"]},
                {"recent": 3, "errors": 3, "failing": ["tag_now"] * 9}):
        assert (hist(fake_core, fresh(10, gate=gate, tools=bad)).state,
                hist(fake_core, fresh(10, gate=gate, tools=bad)).detail) == OFF_CONTRACT, bad


def test_historian_drill_note(fake_core):                                         # m4 критики р1
    gate = {"user_paused": False, "p95_ms": 100}
    r = hist(fake_core, fresh(10, gate=gate, tools={"recent": 3, "errors": 3, "failing": ["tag_series"]},
                              drill="tool_error"))
    assert (r.state, r.detail, r.note) == ("warn", TOOL_FAILING_DETAIL, "tag_series; учения: tool_error")
    r = hist(fake_core, fresh(10, gate=gate, tools={"recent": 0, "errors": 0, "failing": []}, drill="tool_error"))
    assert (r.state, r.note) == ("ok", "учения: tool_error")
    assert hist(fake_core, fresh(10, drill=None)).note == ""
    assert (hist(fake_core, fresh(10, drill="other")).state,) == ("unknown",)


# сверх брифа: учение — в note строки любого состояния; вид failing проверяется и при сработавшей доле


def test_historian_drill_note_any_state_and_failing_edges(fake_core):
    gate = {"user_paused": False, "p95_ms": 100}
    cases = [(fresh(901, drill="tool_error"), ("fail", "последняя метка старше 900 с", DRILL_NOTE)),
             (fresh(None, checked_at=None, drill="tool_error"),
              ("unknown", "служба ещё не опрашивала историан", DRILL_NOTE)),
             (fresh(10, gate=gate, tools={"recent": 50, "errors": 12, "failing": ["tag_now"]}, drill="tool_error"),
              ("warn", "инструменты отвечают ошибкой", f"12 из 50; {DRILL_NOTE}")),
             (fresh(10, drill=""), ("ok", "последняя метка 10 с назад", "")),
             (fresh(10, gate=gate, tools={"recent": 8, "errors": 8, "failing": [f"tool_{c}" for c in "abcdefgh"]}),
              ("warn", TOOL_FAILING_DETAIL, ", ".join(f"tool_{c}" for c in "abcdefgh")))]
    assert FAILING_MAX == 8
    for obj, want in cases:
        r = hist(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj
    for obj in (fresh(10, gate=gate, tools={"recent": 50, "errors": 12, "failing": "tag_now"}),
                fresh(10, gate=gate, tools={"recent": 3, "errors": 3, "failing": ["t" * 33]}),
                fresh(10, gate=gate, tools={"recent": 3, "errors": 3, "failing": [""]}),
                fresh(10, gate=gate, tools={"recent": 3, "errors": 3, "failing": None}),
                fresh(10, drill=5), fresh(10, drill=["tool_error"]), fresh(None, checked_at=None, drill="other")):
        r = hist(fake_core, obj)
        assert (r.state, r.detail) == OFF_CONTRACT, obj


# срок сокета в тестах сроков; вся проверка — не дольше двух таких сроков (правило срока такта)
SOCK_S = 0.3
BOUND_S = 2 * SOCK_S + 0.25   # запас на планировщик


def headers_then_stall(conn, stop):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{")
    stop.wait(3)   # потом закрыть: сломанный срок даст провал теста, а не зависание


def drip(head: bytes):
    """Ответ по байту раз в 0,1 с: каждое чтение сокета укладывается в свой срок.
    Через 3 с двойник закрывает соединение — чтобы старый код не висел вечно."""
    def respond(conn, stop):
        conn.sendall(head)
        until = time.monotonic() + 3
        while time.monotonic() < until and not stop.wait(0.1):
            conn.sendall(b"x")
    return respond


def timed(fn, *args, **kw):
    started = time.monotonic()
    result = fn(*args, **kw)
    return result, time.monotonic() - started


def test_historian_body_stall_is_bounded(raw_http):
    url = raw_http(headers_then_stall)
    r, took = timed(check_historian, "historian", "Историан БДРВ", url, T0, 300, 900, 120, SOCK_S)
    assert (r.state, r.detail) == DOWN and took < BOUND_S


def test_http_checks_bounded_by_one_deadline(raw_http):
    # заголовки пришли, тело капает: срок на операцию сокета не истекает никогда
    slow_body = raw_http(drip(b"HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\n{"))
    r, took = timed(check_historian, "historian", "Историан БДРВ", slow_body, T0, 300, 900, 120, SOCK_S)
    assert (r.state, r.detail) == DOWN and took < BOUND_S
    slow_503 = raw_http(drip(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 500\r\n\r\n{"))
    r, took = timed(check_http, "core", "Служба данных", slow_503, timeout=SOCK_S)
    assert (r.state, r.detail) == ("fail", "отвечает ошибкой HTTP 503") and took < BOUND_S
    # заголовки капают
    slow_head = raw_http(drip(b"HTTP/1.1 200 OK\r\nX-Slow: "))
    r, took = timed(check_http, "core", "Служба данных", slow_head, timeout=SOCK_S)
    assert (r.state, r.detail) == ("fail", "не отвечает") and took < BOUND_S
    r, took = timed(check_historian, "historian", "Историан БДРВ", slow_head, T0, 300, 900, 120, SOCK_S)
    assert (r.state, r.detail) == DOWN and took < BOUND_S


def test_tls_verdict_ok_warn_fail():
    assert tls_verdict(T0 + timedelta(days=90), T0)[0] == "ok"
    state, detail = tls_verdict(T0 + timedelta(days=10), T0)
    assert state == "warn" and "10 дн." in detail
    assert tls_verdict(T0 - timedelta(days=1), T0)[0] == "fail"


def test_tls_wrong_certificate_is_fail(tls_server, other_cert):   # сервер отдаёт один, доверяем другому
    r = check_tls("edge", "Входной прокси", "127.0.0.1", tls_server.port, other_cert, T0)
    assert (r.state, r.detail) == ("fail", "отдаёт не тот сертификат")


def test_tls_matching_certificate_reports_days(tls_server):
    r = check_tls("edge", "Входной прокси", "127.0.0.1", tls_server.port, tls_server.cafile, now_utc())
    assert r.state == "ok" and "дн." in r.detail


def test_tls_expired_certificate_fails_handshake(expired_tls_server):
    # при CERT_REQUIRED просроченный сертификат рвёт рукопожатие (код 10) раньше tls_verdict
    r = check_tls("edge", "Входной прокси", "127.0.0.1", expired_tls_server.port,
                  expired_tls_server.cafile, now_utc())
    assert (r.state, r.detail) == ("fail", "сертификат истёк")


def test_tls_not_yet_valid_certificate(future_tls_server):
    r = check_tls("edge", "Входной прокси", "127.0.0.1", future_tls_server.port,
                  future_tls_server.cafile, now_utc())
    assert (r.state, r.detail) == ("fail", "сертификат ещё не действителен")


def up(state, text):
    return {"state": state, "text": text, "since": None}


def spent(x, **kw):                                  # расход x по журналу, без оценок и сверки
    return llm_obj(**{"spent_usd": x, "ledger_usd": x, **kw})


def llm(fake_core, obj):
    fake_core.set_llm(obj)
    return check_llm("llm", "LLM-прокси и бюджет", fake_core.base + "/health/llm", T0, timeout=0.5)


def test_llm_verdicts(fake_core):                                                # Review Focus 2, 5, 6
    cases = [
        (llm_obj(), ("ok", "OpenRouter отвечает", "расход $12.35 из $1000")),
        (spent(500.0), ("ok", "OpenRouter отвечает", "расход $500.00 из $1000")),   # ровно 50 % — ещё не «больше»
        (spent(501.0), ("warn", "израсходовано больше 50 % бюджета", "расход $501.00 из $1000")),
        (spent(801.0), ("warn", "израсходовано больше 80 % бюджета", "расход $801.00 из $1000")),
        (spent(951.0), ("warn", "израсходовано больше 95 % бюджета", "расход $951.00 из $1000")),
        (spent(1000.0), ("fail", "бюджет $1000 исчерпан — модель не отвечает", "расход $1000.00 из $1000")),
        # полоса не прячет остальное (раунд 1): те же $600 и одно из предупреждений
        (spent(600.0, account_remaining_usd=5.0),
         ("warn", "на счёте OpenRouter меньше $20", "расход $600.00 из $1000 · на счёте $5.00")),
        (spent(600.0, provider_mismatch_24h=1),
         ("warn", "за сутки были ответы не от закреплённого провайдера", "расход $600.00 из $1000")),
        (spent(600.0, cost_unknown_24h=1, estimated_usd=0.49),
         ("warn", "цена неизвестна у части вызовов — в расходе по верхней оценке",
          "расход $600.00 из $1000 · по оценке $0.4900")),
        (spent(600.0, pending=1, pending_stuck=1, estimated_usd=0.49),
         ("warn", "цена ждёт дозапроса дольше 15 минут", "расход $600.00 из $1000 · по оценке $0.4900")),
        (llm_obj(spent_usd=14.0, key_spent_usd=14.0),                            # ledger_usd 12.35: разница $1.65
         ("warn", "OpenRouter насчитал больше, чем учёл прокси", "расход $14.00 из $1000 · по ключу OpenRouter $14.00")),
        (llm_obj(key_spent_usd=12.9), ("ok", "OpenRouter отвечает", "расход $12.35 из $1000 · по ключу OpenRouter $12.90")),
        (spent(600.0, mgmt_key="rejected"),
         ("warn", "ключ управления OpenRouter отклонён — остаток счёта не виден", "расход $600.00 из $1000")),
        (llm_obj(shares={"over_80": 2, "spent": 1}),
         ("ok", "OpenRouter отвечает", "расход $12.35 из $1000 · доля больше 80 %: 2 · доля исчерпана: 1")),
        (llm_obj(ledger_ok=False), ("fail", "журнал вызовов не пишется", "расход $12.35 из $1000")),
        # ревью задач 2а и 4: молчание потока адресов и пропавшая core.db
        (llm_obj(egress={"alive": False, "last_ok_age_s": 40.0}),
         ("fail", "охрана выхода: поток обновления адресов остановлен", "расход $12.35 из $1000")),
        (llm_obj(egress={"alive": True, "last_ok_age_s": 301.0}),
         ("warn", "охрана выхода: адреса OpenRouter не обновлялись больше 5 минут", "расход $12.35 из $1000")),
        (llm_obj(egress={"alive": None, "last_ok_age_s": None}), ("ok", "OpenRouter отвечает", "расход $12.35 из $1000")),
        (llm_obj(ledger_fresh=True, ledger_reset=True),
         ("warn", "журнал вызовов начат заново при ненулевом расходе ключа", "расход $12.35 из $1000")),
        (llm_obj(config_error="ключ OpenRouter не читается"),
         ("fail", "ошибка настройки: ключ OpenRouter не читается", "расход $12.35 из $1000")),
        (llm_obj(tokens_error="токены LLM не читаются"),                        # П23: не config_error
         ("fail", "токены LLM не читаются", "расход $12.35 из $1000")),
        # резервы идущих вызовов полосу не двигают (раунд 2): $499.90 без резервов — ещё норма
        (spent(499.9, reserved_usd=2.0, ledger_usd=501.9, estimated_usd=2.0),
         ("ok", "OpenRouter отвечает", "расход $499.90 из $1000 · идёт на оценку $2.00")),
        (llm_obj(checked_at=(T0 - timedelta(seconds=200)).isoformat()),
         ("unknown", "LLM-прокси давно не опрашивал OpenRouter", "расход $12.35 из $1000")),
        (llm_obj(upstream=up("unreachable", "OpenRouter недоступен")),
         ("fail", "OpenRouter недоступен", "расход $12.35 из $1000")),
        (llm_obj(stopped=True), ("fail", "LLM-прокси остановлен администратором", "расход $12.35 из $1000")),
        (llm_obj(key="missing"), ("fail", "нет ключа OpenRouter", "расход $12.35 из $1000")),
        (llm_obj(upstream=up("money_out", "на счёте OpenRouter кончились деньги (учения)")),
         ("fail", "на счёте OpenRouter кончились деньги (учения)", "расход $12.35 из $1000")),
        (llm_obj(upstream=up("errors", "OpenRouter отвечает ошибками подряд")),
         ("warn", "OpenRouter отвечает ошибками подряд", "расход $12.35 из $1000")),
        (llm_obj(checked_at=None, upstream=up("unknown", "OpenRouter ещё не опрашивался")),
         ("unknown", "OpenRouter ещё не опрашивался", "расход $12.35 из $1000")),
        (llm_obj(provider_mismatch_24h=2),
         ("warn", "за сутки были ответы не от закреплённого провайдера", "расход $12.35 из $1000")),
        (spent(0.0012, ops_usd=0.0012, account_remaining_usd=5.0),
         ("warn", "на счёте OpenRouter меньше $20",
          "расход $0.0012 из $1000 · отбор и проверки $0.0012 · на счёте $5.00")),
    ]
    for obj, want in cases:
        r = llm(fake_core, obj)
        assert (r.state, r.detail, r.note) == want


def test_llm_stale_rule():                                                      # вывод LLM_STALE_S, как у HIST_STALE_S
    assert LLM_STALE_S >= 60 + 2 * 15 + 10 + 60                                  # LLM_POLL_S, 2 × POLL_TIMEOUT_S, такт, запас


def test_llm_down_is_fail(fake_core):
    r = check_llm("llm", "LLM-прокси и бюджет", "http://127.0.0.1:9/health/llm", T0, timeout=0.5)
    assert (r.state, r.detail, r.note) == ("fail", "LLM-прокси не отвечает", "")


def test_llm_details_fit_80_chars(fake_core):                                    # П20, правило Д3а
    objs = [llm_obj(config_error="х" * 200), llm_obj(tokens_error="токены LLM не читаются"),
            llm_obj(upstream=up("region", "OpenRouter отказывает по региону (учения)")), spent(1000.0),
            spent(951.0), llm_obj(mgmt_key="rejected"), llm_obj(cost_unknown_24h=1), llm_obj(pending_stuck=1),
            llm_obj(provider_mismatch_24h=1), llm_obj(key_spent_usd=99.0), llm_obj(account_remaining_usd=1.0),
            llm_obj(ledger_reset=True), llm_obj(egress={"alive": False, "last_ok_age_s": None}),
            llm_obj(egress={"alive": True, "last_ok_age_s": 999.0}), llm_obj(ledger_error="dropped"),
            llm_obj(egress={"alive": True, "last_ok_age_s": None}, refresh_started_age_s=999.0),
            llm_obj(upstream=up("unknown", "OpenRouter ещё не опрашивался"))]
    details = [llm(fake_core, o).detail for o in objs] + ["LLM-прокси не отвечает", "LLM-прокси ответил не по договору"]
    assert all(len(d) <= 80 for d in details), [d for d in details if len(d) > 80]


def test_fmt_usd_and_total():
    assert [fmt_usd(x) for x in (0, 0.0, -0.0, 0.0012, 0.49, 0.99994, 1, 12.3456, 999.999, 1234567.891)] == \
           ["$0.00", "$0.00", "$0.00", "$0.0012", "$0.4900", "$0.9999", "$1.00", "$12.35", "$1000.00", "$1234567.89"]
    assert (fmt_total(1000.0), fmt_total(1000), fmt_total(250.0)) == ("$1000", "$1000", "$250")


def test_fmt_usd_tiny_negative_and_edges():                                     # ревью задачи 7: I2, M3
    # ненулевой расход не пишется нулём (I2)
    assert [fmt_usd(x) for x in (0.00001, 0.00009999, 0.0001, 0.00012)] == \
           ["<$0.0001", "<$0.0001", "$0.0001", "$0.0001"]
    # минус — перед знаком доллара; округлённый до нуля минус не пишется
    assert [fmt_usd(x) for x in (-0.5, -12.345, -0.001)] == ["-$0.50", "-$12.35", "$0.00"]
    # у четырёх знаков доллар — порог: 0.99996 не становится «$1.0000»
    assert fmt_usd(0.99996) == "$0.9999"
    # порог не переходится ни вверх, ни вниз; на самом пороге — как есть
    assert [fmt_usd(x, keep=(1000.0,)) for x in (999.996, 1000.0, 1000.004, 999.994)] == \
           ["$999.99", "$1000.00", "$1000.01", "$999.99"]
    assert [fmt_usd(x, keep=(500.0, 1000.0)) for x in (500.004, 499.996, 500.0, 750.004)] == \
           ["$500.01", "$499.99", "$500.00", "$750.00"]
    assert [fmt_usd(x, keep=(20.0,)) for x in (19.996, 20.004, 19.5)] == ["$19.99", "$20.01", "$19.50"]


def test_llm_constants():
    assert (EGRESS_STALE_S, LLM_STALE_S, ACCOUNT_WARN_USD, DRIFT_WARN_USD, BUDGET_STEPS) == \
           (300, 180, 20.0, 1.0, (95, 80, 50))


def test_llm_boundaries(fake_core):
    ok = ("ok", "OpenRouter отвечает")
    cases = [(llm_obj(checked_at=(T0 - timedelta(seconds=180)).isoformat()), ok),      # ровно LLM_STALE_S — ещё свежо
             (llm_obj(checked_at="2026-09-29T11:59:50Z"), ok),
             (llm_obj(checked_at="2026-09-29T11:59:50"), ok),                            # без пояса — UTC, как у историана
             (llm_obj(egress={"alive": True, "last_ok_age_s": 300.0}), ok),              # ровно EGRESS_STALE_S
             (llm_obj(account_remaining_usd=20.0), ok),                                  # ровно $20 — не «меньше»
             (llm_obj(account_remaining_usd=19.99), ("warn", "на счёте OpenRouter меньше $20")),
             (spent(12.5, key_spent_usd=13.5), ok),                                     # разница ровно $1 — не «больше»
             (llm_obj(key_spent_usd=10.0), ok),                                          # OpenRouter насчитал меньше
             (spent(800.0), ("warn", "израсходовано больше 50 % бюджета")),
             (spent(950.0), ("warn", "израсходовано больше 80 % бюджета")),
             (spent(999.99), ("warn", "израсходовано больше 95 % бюджета")),
             (spent(1200.0), ("fail", "бюджет $1000 исчерпан — модель не отвечает")),
             (spent(30.0, total_usd=50.0), ("warn", "израсходовано больше 50 % бюджета")),  # полосы — от total_usd
             (llm_obj(mgmt_key="error"), ok),                                            # остаток просто неизвестен
             (llm_obj(ledger_fresh=True, ledger_reset=False), ok),                       # новая база при нулевом расходе
             # опрос был, а OpenRouter ни разу не ответил (ревью задачи 7, I1) — не «норма»
             (llm_obj(upstream=up("unknown", "OpenRouter ещё не опрашивался")),
              ("unknown", "OpenRouter ещё ни разу не ответил на опрос")),
             # поток адресов жив, удачного круга не было: ровно EGRESS_STALE_S с запуска — ещё ждём
             (llm_obj(egress={"alive": True, "last_ok_age_s": None}, refresh_started_age_s=300.0), ok),
             (llm_obj(egress={"alive": True, "last_ok_age_s": None}, refresh_started_age_s=300.1),
              ("fail", "охрана выхода: адреса OpenRouter ни разу не получены")),
             (llm_obj(egress={"alive": None, "last_ok_age_s": None}, refresh_started_age_s=None), ok),  # как в pytest
             (llm_obj(ledger_ok=False, ledger_error="write"), ("fail", "журнал вызовов не пишется")),
             (llm_obj(ledger_ok=False, ledger_error="dropped"),
              ("fail", "строка журнала вызовов потеряна — перезапустите core")),
             (llm_obj(ledger_error="dropped"), ("fail", "строка журнала вызовов потеряна — перезапустите core")),
             (llm_obj(ledger_error="write"), ("fail", "журнал вызовов не пишется"))]
    for obj, want in cases:
        r = llm(fake_core, obj)
        assert (r.state, r.detail) == want, obj


def test_llm_order(fake_core):
    # сбои, затем «неизвестно», затем деньги мимо учёта и провайдер, полосы — последними
    stale = (T0 - timedelta(seconds=999)).isoformat()
    cases = [(llm_obj(stopped=True, config_error="x"), "LLM-прокси остановлен администратором"),
             (llm_obj(config_error="x", tokens_error="токены LLM не читаются"), "ошибка настройки: x"),
             (llm_obj(tokens_error="что угодно", key="missing"), "токены LLM не читаются"),   # текст постоянный
             (llm_obj(key="missing", ledger_ok=False), "нет ключа OpenRouter"),
             (llm_obj(ledger_ok=False, egress={"alive": False, "last_ok_age_s": None}), "журнал вызовов не пишется"),
             (spent(1000.0, egress={"alive": False, "last_ok_age_s": None}),
              "охрана выхода: поток обновления адресов остановлен"),
             (llm_obj(ledger_error="dropped", egress={"alive": False, "last_ok_age_s": None}),
              "строка журнала вызовов потеряна — перезапустите core"),
             (llm_obj(ledger_ok=False, egress={"alive": True, "last_ok_age_s": None}, refresh_started_age_s=999.0),
              "журнал вызовов не пишется"),
             (spent(1000.0, egress={"alive": True, "last_ok_age_s": None}, refresh_started_age_s=999.0),
              "охрана выхода: адреса OpenRouter ни разу не получены"),
             (llm_obj(checked_at=None, upstream=up("unknown", "OpenRouter ещё не опрашивался")),
              "OpenRouter ещё не опрашивался"),
             (llm_obj(upstream=up("unknown", "OpenRouter ещё не опрашивался"), checked_at=stale),
              "OpenRouter ещё ни разу не ответил на опрос"),
             (llm_obj(upstream=up("unknown", "OpenRouter ещё не опрашивался"), account_remaining_usd=1.0),
              "OpenRouter ещё ни разу не ответил на опрос"),
             (spent(1000.0, upstream=up("key_limit", "исчерпан лимит ключа OpenRouter")),
              "бюджет $1000 исчерпан — модель не отвечает"),
             (spent(1000.0, checked_at=None), "бюджет $1000 исчерпан — модель не отвечает"),
             (llm_obj(upstream=up("key_rejected", "OpenRouter отклонил ключ стенда"), checked_at=stale),
              "OpenRouter отклонил ключ стенда"),
             (llm_obj(upstream=up("unreachable", "OpenRouter недоступен"), checked_at=None), "OpenRouter недоступен"),
             (llm_obj(checked_at=stale, upstream=up("errors", "OpenRouter отвечает ошибками подряд")),
              "LLM-прокси давно не опрашивал OpenRouter"),
             (spent(990.0, checked_at=stale), "LLM-прокси давно не опрашивал OpenRouter"),
             (llm_obj(upstream=up("errors", "OpenRouter отвечает ошибками подряд"), account_remaining_usd=1.0),
              "OpenRouter отвечает ошибками подряд"),
             (llm_obj(account_remaining_usd=1.0, provider_mismatch_24h=1), "на счёте OpenRouter меньше $20"),
             (llm_obj(provider_mismatch_24h=1, cost_unknown_24h=1), "за сутки были ответы не от закреплённого провайдера"),
             (llm_obj(cost_unknown_24h=1, pending_stuck=1), "цена неизвестна у части вызовов — в расходе по верхней оценке"),
             (llm_obj(pending_stuck=1, ledger_reset=True), "цена ждёт дозапроса дольше 15 минут"),
             (llm_obj(ledger_reset=True, egress={"alive": True, "last_ok_age_s": 999.0}),
              "журнал вызовов начат заново при ненулевом расходе ключа"),
             (llm_obj(egress={"alive": True, "last_ok_age_s": 999.0}, key_spent_usd=99.0),
              "охрана выхода: адреса OpenRouter не обновлялись больше 5 минут"),
             (llm_obj(key_spent_usd=99.0, mgmt_key="rejected"), "OpenRouter насчитал больше, чем учёл прокси"),
             (spent(990.0, mgmt_key="rejected"), "ключ управления OpenRouter отклонён — остаток счёта не виден")]
    for obj, want in cases:
        assert llm(fake_core, obj).detail == want, obj


def test_llm_note_parts_in_order(fake_core):
    full = spent(900.0, reserved_usd=2.0, estimated_usd=2.49, key_spent_usd=900.5, ops_usd=12.3456,
                 shares={"over_80": 3, "spent": 1}, account_remaining_usd=250.0)
    assert llm(fake_core, full).note == ("расход $900.00 из $1000 · идёт на оценку $2.00 · по оценке $0.4900"
                                         " · по ключу OpenRouter $900.50 · отбор и проверки $12.35"
                                         " · доля больше 80 %: 3 · доля исчерпана: 1 · на счёте $250.00")
    # оценка вся — у идущих вызовов: «по оценке» нет, и шум сложения не даёт «$0.0000»
    assert llm(fake_core, llm_obj(reserved_usd=0.3, estimated_usd=0.1 + 0.2)).note == \
           "расход $12.35 из $1000 · идёт на оценку $0.3000"
    assert llm(fake_core, llm_obj(key_spent_usd=0.0, account_remaining_usd=0.0)).note == \
           "расход $12.35 из $1000 · по ключу OpenRouter $0.00 · на счёте $0.00"   # ноль — тоже известное число


def test_llm_note_mgmt_error_and_drill(fake_core):                              # ревью задачи 7: M1, M2
    assert llm(fake_core, llm_obj(mgmt_key="error")).note == "расход $12.35 из $1000 · остаток счёта не виден"
    assert llm(fake_core, llm_obj(mgmt_key="rejected")).note == "расход $12.35 из $1000"   # своя строка detail
    r = llm(fake_core, llm_obj(drill="402", mgmt_key="error",
                               upstream=up("money_out", "на счёте OpenRouter кончились деньги (учения)")))
    assert (r.state, r.detail, r.note) == ("fail", "на счёте OpenRouter кончились деньги (учения)",
                                           "расход $12.35 из $1000 · остаток счёта не виден · учения DRILL_LLM=402")


def test_llm_note_number_agrees_with_words(fake_core):                          # ревью задачи 7: M3
    cases = [(spent(999.996), ("warn", "израсходовано больше 95 % бюджета", "расход $999.99 из $1000")),
             (spent(500.004), ("warn", "израсходовано больше 50 % бюджета", "расход $500.01 из $1000")),
             (spent(499.996), ("ok", "OpenRouter отвечает", "расход $499.99 из $1000")),
             (spent(950.004), ("warn", "израсходовано больше 95 % бюджета", "расход $950.01 из $1000")),
             (spent(0.00004), ("ok", "OpenRouter отвечает", "расход <$0.0001 из $1000")),
             (llm_obj(account_remaining_usd=19.996),
              ("warn", "на счёте OpenRouter меньше $20", "расход $12.35 из $1000 · на счёте $19.99")),
             (llm_obj(key_spent_usd=-0.5), ("ok", "OpenRouter отвечает", "расход $12.35 из $1000 · по ключу OpenRouter -$0.50"))]
    for obj, want in cases:
        r = llm(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj


def test_llm_number_changes_are_not_journal_events(fake_core, tmp_path):        # Review Focus 5
    journal = Journal(str(tmp_path / "j.db"))
    rows = [llm(fake_core, spent(x)) for x in (501.0, 502.5, 600.0, 799.99)]
    assert {(r.state, r.detail) for r in rows} == {("warn", "израсходовано больше 50 % бюджета")}
    assert len({r.note for r in rows}) == 4
    assert sum(len(journal.record([r], T0 + timedelta(seconds=10 * i))) for i, r in enumerate(rows)) == 1


def test_llm_bad_reply(fake_core):
    down = ("fail", "LLM-прокси не отвечает", "")
    for url in (fake_core.base + "/nope", fake_core.base + "/healthz/data"):     # HTTP ≠ 200 — 404 и 503
        fake_core.set_health(503, llm_obj())
        r = check_llm("llm", "LLM-прокси и бюджет", url, T0, timeout=0.5)
        assert (r.state, r.detail, r.note) == down, url
    for raw in ("не json", "", "{\"checked_at\": ", "[" * 100_000):
        fake_core.set_llm_raw(raw)
        r = check_llm("llm", "LLM-прокси и бюджет", fake_core.base + "/health/llm", T0, timeout=0.5)
        assert (r.state, r.detail, r.note) == down, raw[:20]


def test_llm_off_contract_reply_has_own_text(fake_core):
    # JSON пришёл, но не по договору (другая версия core): сбой со своим постоянным текстом, без чисел
    without = {k: v for k, v in llm_obj().items() if k != "spent_usd"}
    for obj in ([llm_obj()], "строка", 7, None, {}, without, llm_obj(spent_usd="12"), llm_obj(spent_usd=True),
                llm_obj(spent_usd=float("nan")), llm_obj(total_usd=0.0), llm_obj(total_usd=float("inf")),
                llm_obj(checked_at="вчера"), llm_obj(checked_at=12), llm_obj(upstream="ok"),
                llm_obj(upstream=up("new_state", "что-то новое")), llm_obj(upstream=up("ok", None)),
                llm_obj(egress=None), llm_obj(egress={"alive": "yes", "last_ok_age_s": None}),
                llm_obj(shares={"over_80": "2", "spent": 0}), llm_obj(stopped="false"), llm_obj(ledger_ok=None),
                llm_obj(key_spent_usd="1"), llm_obj(account_remaining_usd=float("-inf")),
                llm_obj(config_error=5), llm_obj(pending_stuck=None), llm_obj(provider_mismatch_24h=True),
                llm_obj(upstream=up("ok", "")), llm_obj(upstream=up("errors", "  ")),               # M7
                llm_obj(ledger_error="other"), llm_obj(ledger_error=1), llm_obj(drill=None),
                llm_obj(refresh_started_age_s="1"), llm_obj(spent_usd=1e300, ledger_usd=1e300),
                llm_obj(egress={"alive": True, "last_ok_age_s": None}, refresh_started_age_s=None),
                {k: v for k, v in llm_obj().items() if k != "ledger_error"},
                {k: v for k, v in llm_obj().items() if k != "refresh_started_age_s"}):
        r = llm(fake_core, obj)
        assert (r.state, r.detail, r.note) == ("fail", "LLM-прокси ответил не по договору", ""), obj


# настоящие ответы /health/llm кода задачи 6 (источник — в поле source файла); сторож core не импортирует
REAL_HEALTH_LLM = json.loads((Path(__file__).parent / "fixtures" / "health_llm_samples.json").read_text("utf-8"))


def test_llm_junction_with_real_health_json(fake_core):                         # стык задач 6 и 7
    samples = REAL_HEALTH_LLM["samples"]
    want = {"not_polled": ("unknown", "OpenRouter ещё не опрашивался", "расход $0.00 из $1000"),
            "working": ("ok", "OpenRouter отвечает", "расход $0.0024 из $1000 · по ключу OpenRouter $0.0024"
                        " · отбор и проверки $0.0012 · на счёте $30.00"),
            "drill_402": ("fail", "на счёте OpenRouter кончились деньги (учения)",
                          "расход $0.00 из $1000 · по ключу OpenRouter $0.00 · на счёте $30.00 · учения DRILL_LLM=402"),
            # Д8а: LLM_RUN_* заданы (d8-test, $1, student-01 и ops), два вызова
            "run_active": ("ok", "OpenRouter отвечает", "расход $0.0024 из $1000 · по ключу OpenRouter $0.0024"
                           " · отбор и проверки $0.0012 · прогон d8-test: $0.0024 из $1.00, вызовов за 10 мин: 2"
                           " · на счёте $30.00")}
    assert set(samples) == set(want)
    for name, obj in samples.items():
        # помощник тестов сторожа держит тот же договор, что и настоящий ответ: 31 ключ и вложенные
        assert set(obj) == set(llm_obj()) and len(obj) == 31, name
        assert obj["run"] is None or set(obj["run"]) == RUN_KEYS, name
        assert (set(obj["upstream"]), set(obj["egress"]), set(obj["shares"])) == \
               ({"state", "text", "since"}, {"alive", "last_ok_age_s"}, {"over_80", "spent"}), name
        at = datetime.fromisoformat(obj["checked_at"]) if obj["checked_at"] else T0
        fake_core.set_llm(obj)
        r = check_llm("llm", "LLM-прокси и бюджет", fake_core.base + "/health/llm", at + timedelta(seconds=5),
                      timeout=0.5)
        assert (r.state, r.detail, r.note) == want[name], name


# --- прогон отбора модели в строке «LLM-прокси и бюджет» (задача А1 Д8а, Д8-R69) ---

RUN = {"id": "d8-1008", "cap_usd": 80.0, "spent_usd": 12.4, "callers": 4, "calls_10m": 37, "idle_s": 3.0}


def test_llm_run_note():
    assert "прогон d8-1008: $12.40 из $80, вызовов за 10 мин: 37" in llm_note(LlmReply.parse(llm_obj(run=RUN)))
    small = {**RUN, "cap_usd": 1.0, "spent_usd": 0.0, "calls_10m": 0}
    assert "прогон d8-1008: $0.00 из $1.00" in llm_note(LlmReply.parse(llm_obj(run=small)))


@pytest.mark.parametrize("patch,want", [
    ({}, "ok"),
    ({"spent_usd": 80.0}, ("warn", RUN_CAP_DETAIL)),
    ({"idle_s": 301.0}, ("warn", RUN_SILENT_DETAIL)),
])
def test_llm_run_verdicts(patch, want):                                           # Review Focus 3
    state, detail = llm_verdict(LlmReply.parse(llm_obj(run={**RUN, **patch})), T0, 300.0)
    assert (state if want == "ok" else (state, detail)) == want


def test_llm_run_silence_threshold_param():
    r = LlmReply.parse(llm_obj(run={**RUN, "idle_s": 11.0}))
    assert llm_verdict(r, T0, 10.0) == ("warn", RUN_SILENT_DETAIL) and llm_verdict(r, T0, 300.0)[0] == "ok"


@pytest.mark.parametrize("bad", [{**RUN, "extra": 1}, {k: v for k, v in RUN.items() if k != "idle_s"},
                                 {**RUN, "cap_usd": 0}, {**RUN, "calls_10m": -1}, []])
def test_llm_run_off_contract(bad):
    with pytest.raises((ValueError, TypeError, KeyError)):
        LlmReply.parse(llm_obj(run=bad))


def test_llm_without_run_key_is_none():                                           # core :d7b во время выкладки
    obj = llm_obj()
    del obj["run"]
    assert LlmReply.parse(obj).run is None


# сверх брифа

def test_llm_run_rows_order():                                       # Д8-R69: после fail и unknown, до errors
    silent, capped = {**RUN, "idle_s": 999.0}, {**RUN, "spent_usd": 80.0, "idle_s": 999.0}
    cases = [
        # учения 402 и сбои — раньше строк прогона (учение Д8-R77, шаг 2)
        (llm_obj(drill="402", run=silent, upstream=up("money_out", "на счёте OpenRouter кончились деньги (учения)")),
         ("fail", "на счёте OpenRouter кончились деньги (учения)")),
        (llm_obj(config_error="прогон: точка отсчёта не записана", run=capped),
         ("fail", "ошибка настройки: прогон: точка отсчёта не записана")),
        (spent(1000.0, run=capped), ("fail", "бюджет $1000 исчерпан — модель не отвечает")),
        (llm_obj(checked_at=None, run=silent), ("unknown", "OpenRouter ещё не опрашивался")),
        (llm_obj(checked_at=(T0 - timedelta(seconds=LLM_STALE_S + 1)).isoformat(), run=silent),
         ("unknown", "LLM-прокси давно не опрашивал OpenRouter")),
        # потолок раньше тишины; оба — раньше errors, счёта, провайдера и полос бюджета
        (llm_obj(run=capped), ("warn", RUN_CAP_DETAIL)),
        (llm_obj(run=silent, upstream=up("errors", "OpenRouter отвечает с ошибками")), ("warn", RUN_SILENT_DETAIL)),
        (spent(960.0, run=silent, account_remaining_usd=1.0, provider_mismatch_24h=2), ("warn", RUN_SILENT_DETAIL)),
        (spent(960.0, run={**RUN, "spent_usd": 79.99}), ("warn", "израсходовано больше 95 % бюджета")),
        (llm_obj(run={**RUN, "idle_s": 300.0}), ("ok", "OpenRouter отвечает")),       # ровно порог — ещё не «дольше»
    ]
    for obj, want in cases:
        assert llm_verdict(LlmReply.parse(obj), T0, 300.0) == want, obj
    assert LLM_RUN_SILENT_S == 300
    assert llm_verdict(LlmReply.parse(llm_obj(run={**RUN, "idle_s": 300.5})), T0) == ("warn", RUN_SILENT_DETAIL)


def test_llm_run_note_place_and_rounding():
    full = spent(900.0, ops_usd=1.5, shares={"over_80": 3, "spent": 0}, account_remaining_usd=250.0,
                 run={**RUN, "spent_usd": 79.996})
    assert llm_note(LlmReply.parse(full)) == ("расход $900.00 из $1000 · отбор и проверки $1.50"
                                              " · прогон d8-1008: $79.99 из $80, вызовов за 10 мин: 37"
                                              " · доля больше 80 %: 3 · на счёте $250.00")   # не «$80.00 из $80»
    odd = LlmReply.parse(llm_obj(run={**RUN, "cap_usd": 80.5, "spent_usd": 80.4}))      # доводка р1, m1
    assert "прогон d8-1008: $80.40 из $80.50, " in llm_note(odd)                   # не «из $80»
    for cap, shown in ((5.0, "$5.00"), (9.99, "$9.99"), (10.0, "$10"), (0.002, "$0.0020"), (10.5, "$10.50"),
                       (80, "$80")):
        r = LlmReply.parse(llm_obj(run={**RUN, "cap_usd": cap, "spent_usd": 0.0}))
        assert f"прогон d8-1008: $0.00 из {shown}, " in llm_note(r), cap
    assert "прогон" not in llm_note(LlmReply.parse(llm_obj()))


def test_llm_run_through_check(fake_core):                                        # тот же договор по HTTP
    fake_core.set_llm(llm_obj(run={**RUN, "idle_s": 11.0}))
    url = fake_core.base + "/health/llm"
    r = check_llm("llm", "LLM-прокси и бюджет", url, T0, timeout=0.5, run_silent_s=10)
    assert (r.state, r.detail) == ("warn", RUN_SILENT_DETAIL) and "вызовов за 10 мин: 37" in r.note
    assert check_llm("llm", "LLM-прокси и бюджет", url, T0, timeout=0.5).state == "ok"   # умолчание — 300 с
    for bad in ({**RUN, "id": "D8"}, {**RUN, "id": ""}, {**RUN, "id": 5}, {**RUN, "callers": True},
                {**RUN, "spent_usd": "1"}, {**RUN, "idle_s": float("nan")}, {**RUN, "spent_usd": -0.01},
                {**RUN, "callers": 1.0}, "run"):
        fake_core.set_llm(llm_obj(run=bad))
        r = check_llm("llm", "LLM-прокси и бюджет", url, T0, timeout=0.5)
        assert (r.state, r.detail, r.note) == ("fail", "LLM-прокси ответил не по договору", ""), bad


# строка «Шлюз и вход» (задача А1 Д5а); gw — ответ /health/gateway из helpers
def gate_check(fake_core, obj):
    fake_core.set_gateway(obj)
    return check_gateway("gateway", "Шлюз и вход", fake_core.base + "/health/gateway", T0, 120, timeout=0.5)


def test_gateway_verdicts(fake_core):
    cases = [(gw(), ("ok", "принимает вход", "работает мест: 1")),
             (gw(config_error="настройки шлюза: GW_ORIGIN — нужен https://хост[:порт]"),
              ("fail", "ошибка настройки: настройки шлюза: GW_ORIGIN — нужен https://хост[:порт]", "")),
             (gw(store_ok=False), ("fail", "учётные записи недоступны", "")),
             (gw(age=121), ("fail", "уборщик простоя молчит — места не засыпают", "")),
             (gw(checked_at=None), ("unknown", "уборщик простоя ещё не проходил", "")),
             (gw(sp_ctl="down"), ("fail", "нет связи с прокси сокета — места не будятся", "")),
             (gw(wake_ok_1h=0, wake_failed_1h=3), ("fail", "рабочие места не поднимаются", "3 за час")),
             (gw(place_auth_failed_1h=1, wake_failed_1h=1), ("warn", "рабочее место не приняло пароль шлюза", "1 за час")),
             (gw(origin_refused_1h=4), ("warn", "вход отклоняется проверкой источника — проверьте GW_ORIGIN", "4 за час")),
             (gw(cookies_duplicate_1h=1, origin_refused_1h=1), ("warn", "подброшена cookie сессии", "1 за час")),
             (gw(login_busy_1h=2), ("warn", "вход перегружен", "2 за час")),
             (gw(wake_failed_1h=2), ("warn", "рабочее место не поднялось по входу", "2 за час")),
             (gw(stop_failed_1h=1), ("warn", "уборщик не смог усыпить место", "1 за час")),
             (gw(logins_failed_1h=30), ("warn", "много неверных паролей", "30 за час")),
             (gw(logins_failed_1h=29), ("ok", "принимает вход", "работает мест: 1")),
             (gw(drill="freeze_reaper", age=121),
              ("fail", "уборщик простоя молчит — места не засыпают", "учения: freeze_reaper"))]
    for obj, want in cases:
        r = gate_check(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj


def test_gateway_unreachable_or_garbage_is_fail(fake_core):
    r = check_gateway("gateway", "Шлюз и вход", "http://127.0.0.1:9/health/gateway", T0, 120, timeout=0.5)
    assert (r.state, r.detail) == ("fail", "шлюз не отвечает")
    fake_core.set_gateway_raw("не json")
    r = check_gateway("gateway", "Шлюз и вход", fake_core.base + "/health/gateway", T0, 120, timeout=0.5)
    assert (r.state, r.detail) == ("fail", "шлюз не отвечает")
    bad = gw(); del bad["store_ok"]
    for obj in (bad, gw(store_ok="да"), gw(running_n="1"), [1, 2]):                   # Ш8: не KeyError, а внятная строка
        fake_core.set_gateway(obj)
        r = check_gateway("gateway", "Шлюз и вход", fake_core.base + "/health/gateway", T0, 120, timeout=0.5)
        assert (r.state, r.detail) == ("fail", "шлюз ответил не по договору"), obj


def test_gateway_constants():
    assert (WAKE_FAILS_FAIL, LOGIN_FAILS_WARN, REASON_CHARS) == (3, 30, 80)
    assert GW_OFF_CONTRACT_DETAIL == "шлюз ответил не по договору"
    assert GW_SP_CTL_STATES == {"ok", "down", "unknown"}                       # sp_ctl: Literal Д5а (А3)
    assert GW_DRILL_MODES == {"freeze_reaper", "bad_place_pw"}                  # DRILL_GATEWAY, кроме ""


def test_gateway_http_errors_are_down(fake_core):
    down = ("fail", "шлюз не отвечает", "")
    for url in (fake_core.base + "/nope", fake_core.base + "/healthz/data"):     # HTTP ≠ 200 — 404 и 503
        fake_core.set_health(503, gw())
        r = check_gateway("gateway", "Шлюз и вход", url, T0, 120, timeout=0.5)
        assert (r.state, r.detail, r.note) == down, url
    for raw in ("", "{\"checked_at\": ", "[" * 100_000):
        fake_core.set_gateway_raw(raw)
        r = check_gateway("gateway", "Шлюз и вход", fake_core.base + "/health/gateway", T0, 120, timeout=0.5)
        assert (r.state, r.detail, r.note) == down, raw[:20]


def test_gateway_boundaries_and_order(fake_core):
    ok = ("ok", "принимает вход", "работает мест: 1")
    cases = [(gw(age=120), ok),                                                   # ровно stale_s — ещё свежо
             (gw(age=-30), ok),                                                   # часы core впереди — не молчание
             (gw(checked_at="2026-09-29T11:59:55"), ok),                          # без пояса — договор ISO UTC
             (gw(wake_ok_1h=0, wake_failed_1h=2), ("warn", "рабочее место не поднялось по входу", "2 за час")),
             (gw(wake_ok_1h=1, wake_failed_1h=5), ("warn", "рабочее место не поднялось по входу", "5 за час")),
             (gw(running_n=0, ready_n=0, wake_ok_1h=0), ("ok", "принимает вход", "работает мест: 0")),
             # первая подходящая сверху
             (gw(config_error="x", store_ok=False, checked_at=None, sp_ctl="down"),
              ("fail", "ошибка настройки: x", "")),
             (gw(store_ok=False, checked_at=None, sp_ctl="down"), ("fail", "учётные записи недоступны", "")),
             (gw(checked_at=None, sp_ctl="down", wake_ok_1h=0, wake_failed_1h=3),
              ("unknown", "уборщик простоя ещё не проходил", "")),
             (gw(age=121, sp_ctl="down"), ("fail", "уборщик простоя молчит — места не засыпают", "")),
             (gw(sp_ctl="down", wake_ok_1h=0, wake_failed_1h=3),
              ("fail", "нет связи с прокси сокета — места не будятся", "")),
             (gw(wake_ok_1h=0, wake_failed_1h=4, place_auth_failed_1h=2),
              ("fail", "рабочие места не поднимаются", "4 за час")),
             (gw(place_auth_failed_1h=2, cookies_duplicate_1h=1, origin_refused_1h=1, login_busy_1h=1,
                 wake_failed_1h=1, stop_failed_1h=1, logins_failed_1h=99),
              ("warn", "рабочее место не приняло пароль шлюза", "2 за час")),
             (gw(origin_refused_1h=3, login_busy_1h=1, wake_failed_1h=1),
              ("warn", "вход отклоняется проверкой источника — проверьте GW_ORIGIN", "3 за час")),
             (gw(login_busy_1h=5, wake_failed_1h=1, stop_failed_1h=1), ("warn", "вход перегружен", "5 за час")),
             (gw(wake_failed_1h=1, stop_failed_1h=1, logins_failed_1h=40),
              ("warn", "рабочее место не поднялось по входу", "1 за час")),
             (gw(stop_failed_1h=2, logins_failed_1h=40), ("warn", "уборщик не смог усыпить место", "2 за час"))]
    for obj, want in cases:
        r = gate_check(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj


def test_gateway_drill_goes_first_in_note(fake_core):
    cases = [(gw(drill="freeze_reaper"), ("ok", "принимает вход", "учения: freeze_reaper; работает мест: 1")),
             (gw(drill="bad_place_pw", place_auth_failed_1h=2),
              ("warn", "рабочее место не приняло пароль шлюза", "учения: bad_place_pw; 2 за час")),
             (gw(drill="freeze_reaper", checked_at=None),
              ("unknown", "уборщик простоя ещё не проходил", "учения: freeze_reaper")),
             (gw(drill="bad_place_pw", config_error="x"), ("fail", "ошибка настройки: x", "учения: bad_place_pw"))]
    for obj, want in cases:
        r = gate_check(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj


def test_gateway_details_fit_80_chars_and_config_error_is_cut(fake_core):       # правило Д3а
    objs = [gw(config_error="х" * 200), gw(store_ok=False), gw(checked_at=None), gw(age=999), gw(sp_ctl="down"),
            gw(wake_ok_1h=0, wake_failed_1h=3), gw(place_auth_failed_1h=1), gw(cookies_duplicate_1h=1),
            gw(origin_refused_1h=1), gw(login_busy_1h=1), gw(wake_failed_1h=1), gw(stop_failed_1h=1),
            gw(logins_failed_1h=30), gw()]
    details = [gate_check(fake_core, o).detail for o in objs] + ["шлюз не отвечает", GW_OFF_CONTRACT_DETAIL]
    assert all(len(d) <= REASON_CHARS for d in details), [d for d in details if len(d) > REASON_CHARS]
    assert gate_check(fake_core, gw(config_error="х" * 200)).detail == ("ошибка настройки: " + "х" * 200)[:80]


def test_gateway_number_changes_are_not_journal_events(fake_core, tmp_path):    # числа — только в note
    journal = Journal(str(tmp_path / "j.db"))
    rows = [gate_check(fake_core, gw(origin_refused_1h=k)) for k in (1, 2, 7, 30)]
    rows += [gate_check(fake_core, gw(running_n=n)) for n in (1, 4, 10)]
    assert [(r.state, r.detail) for r in rows] == \
           [("warn", "вход отклоняется проверкой источника — проверьте GW_ORIGIN")] * 4 + [("ok", "принимает вход")] * 3
    assert len({r.note for r in rows}) == 7
    assert sum(len(journal.record([r], T0 + timedelta(seconds=10 * i))) for i, r in enumerate(rows)) == 2


def test_gateway_off_contract_reply_has_own_text(fake_core):                     # Ш8
    keys = list(gw())
    assert len(keys) == 15
    missing = [{k: v for k, v in gw().items() if k != key} for key in keys]
    for obj in (*missing, [gw()], "строка", 7, None, {}, gw(checked_at=12), gw(checked_at="вчера"),
                gw(config_error=5), gw(store_ok=None), gw(store_ok=1), gw(sp_ctl=None), gw(sp_ctl=0),
                gw(running_n=True), gw(ready_n=1.0), gw(wake_ok_1h=None), gw(wake_failed_1h="0"),
                gw(stop_failed_1h=0.5), gw(place_auth_failed_1h=False), gw(logins_failed_1h=[]),
                gw(origin_refused_1h={}), gw(login_busy_1h="1"), gw(cookies_duplicate_1h=None), gw(drill=None),
                # ключи Д5б — только полным набором (gw5): у gw() с одним ключом отказ был бы за неполноту (Б1-m1)
                gw5(refused_1h="7"), gw5(streams=None), gw5(stream_broken_1h=True), gw5(upstream_failed_1h=1.5),
                # доводка по ревью А1: I1 — sp_ctl вне договора; M1 — отрицательные счётчики;
                # M2 — пустая причина настройки; M3 — неизвестный режим учений
                gw(sp_ctl="DOWN"), gw(sp_ctl=""), gw(sp_ctl="up"), gw(sp_ctl="down "),
                gw(wake_ok_1h=-1), gw(running_n=-1), gw(ready_n=-2), gw(wake_failed_1h=-3), gw(stop_failed_1h=-1),
                gw(place_auth_failed_1h=-1), gw(logins_failed_1h=-30), gw(origin_refused_1h=-1),
                gw(login_busy_1h=-1), gw(cookies_duplicate_1h=-1), gw5(refused_1h=-1), gw5(streams=-1),
                gw5(stream_broken_1h=-1), gw5(upstream_failed_1h=-1),
                gw(config_error=""), gw(config_error="   "), gw(config_error="\n\t"),
                gw(drill="x"), gw(drill=" "), gw(drill="FREEZE_REAPER"), gw(drill="freeze_reaper "),
                gw(drill="freeze_poll")):
        r = gate_check(fake_core, obj)
        assert (r.state, r.detail, r.note) == ("fail", GW_OFF_CONTRACT_DETAIL, ""), obj
        with pytest.raises(ValueError):                                           # не KeyError и не TypeError
            GatewayReply.parse(obj)


def test_gateway_reply_accepts_whole_contract(fake_core):                       # доводка по ревью А1
    # каждое значение из договора разбирается; sp_ctl=unknown (до первого пинга) строкой таблицы не является
    for state in sorted(GW_SP_CTL_STATES):
        assert GatewayReply.parse(gw(sp_ctl=state)).sp_ctl == state
    for mode in ("", *sorted(GW_DRILL_MODES)):
        assert GatewayReply.parse(gw(drill=mode)).drill == mode
    zeros = gw5(running_n=0, ready_n=0, wake_ok_1h=0)
    assert GatewayReply.parse(zeros).running_n == 0
    assert (gate_check(fake_core, gw(sp_ctl="unknown")).state, gate_check(fake_core, gw(sp_ctl="unknown")).detail) == \
           ("ok", "принимает вход")
    r = gate_check(fake_core, gw(config_error="x \n"))                         # не пустая — строка настройки
    assert (r.state, r.detail) == ("fail", "ошибка настройки: x")


def test_gateway_reply_d5b_keys_are_optional():                                  # ответ core :d5a во время выкладки
    old = GatewayReply.parse(gw())
    assert (old.refused_1h, old.streams, old.stream_broken_1h, old.upstream_failed_1h) == (None, None, None, None)
    new = GatewayReply.parse(gw(refused_1h=7, streams=2, stream_broken_1h=1, upstream_failed_1h=0))
    assert (new.refused_1h, new.streams, new.stream_broken_1h, new.upstream_failed_1h) == (7, 2, 1, 0)
    assert (new.checked_at, new.store_ok, new.running_n, new.drill) == (T0 - timedelta(seconds=5), True, 1, "")


# --- Б1 Д5б: счётчики прокси на строке «Шлюз и вход» ---

def test_gateway_proxy_rows_and_note(fake_core):
    cases = [(gw(refused_1h=7, streams=2, stream_broken_1h=0, upstream_failed_1h=0),
              ("ok", "принимает вход", "работает мест: 1; потоков: 2; отказов за час: 7")),
             (gw(refused_1h=0, streams=0, stream_broken_1h=0, upstream_failed_1h=2),
              ("warn", "рабочее место не отвечает шлюзу", "2 за час")),
             (gw(refused_1h=0, streams=1, stream_broken_1h=1, upstream_failed_1h=0),
              ("warn", "поток событий рвётся", "1 за час")),
             (gw(), ("ok", "принимает вход", "работает мест: 1"))]                    # core :d5a
    for obj, want in cases:
        r = gate_check(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj


D5B_KEYS = ("refused_1h", "streams", "stream_broken_1h", "upstream_failed_1h")


def gw5(age=5, **kw):
    """Ответ core :d5b — 15 ключей Д5а и четыре ключа прокси, всё в норме."""
    return gw(age, **{"refused_1h": 0, "streams": 0, "stream_broken_1h": 0, "upstream_failed_1h": 0, **kw})


def test_gateway_d5b_keys_all_or_none(fake_core):                                # решение контроллера 1
    assert (GW_KEYS_D5A, GW_KEYS_D5B) == (set(gw()), set(D5B_KEYS))
    assert GW_KEYS_D5A | GW_KEYS_D5B == {f.name for f in fields(GatewayReply)}      # Б1-m3: договор = поля разбора
    assert len(gw5()) == 19 and GatewayReply.parse(gw5()).streams == 0
    partial = [{k: v for k, v in gw5().items() if k not in dropped}                # 1–3 из 4 ключей
               for n in (1, 2, 3) for dropped in itertools.combinations(D5B_KEYS, n)]
    assert len(partial) == 14
    for obj in (*partial,
                gw(extra=0), gw5(extra=0), gw(refused=0), gw5(streams_1h=0),         # лишний неизвестный ключ
                *(gw5(**{k: -1}) for k in D5B_KEYS),                                 # отрицательный
                *(gw5(**{k: v}) for k in D5B_KEYS for v in (True, False, 1.0, 0.5, "1", None, [], {}))):
        r = gate_check(fake_core, obj)
        assert (r.state, r.detail, r.note) == ("fail", GW_OFF_CONTRACT_DETAIL, ""), obj
        with pytest.raises(ValueError):
            GatewayReply.parse(obj)


def test_gateway_proxy_rows_order(fake_core):                                    # решение контроллера 2
    cases = [(gw5(place_auth_failed_1h=1, upstream_failed_1h=5, stream_broken_1h=6),
              ("warn", "рабочее место не приняло пароль шлюза", "1 за час")),
             (gw5(upstream_failed_1h=5, stream_broken_1h=6, cookies_duplicate_1h=1, origin_refused_1h=1,
                  login_busy_1h=1, wake_failed_1h=1, stop_failed_1h=1, logins_failed_1h=99),
              ("warn", "рабочее место не отвечает шлюзу", "5 за час")),
             (gw5(stream_broken_1h=6, cookies_duplicate_1h=1, origin_refused_1h=1, login_busy_1h=1,
                  wake_failed_1h=1, stop_failed_1h=1, logins_failed_1h=99),
              ("warn", "поток событий рвётся", "6 за час")),
             # тяжёлые строки Д5а выше по-прежнему выигрывают
             (gw5(sp_ctl="down", upstream_failed_1h=3, stream_broken_1h=2),
              ("fail", "нет связи с прокси сокета — места не будятся", "")),
             (gw5(wake_ok_1h=0, wake_failed_1h=3, upstream_failed_1h=3),
              ("fail", "рабочие места не поднимаются", "3 за час")),
             (gw5(age=121, stream_broken_1h=2), ("fail", "уборщик простоя молчит — места не засыпают", "")),
             (gw5(checked_at=None, upstream_failed_1h=1), ("unknown", "уборщик простоя ещё не проходил", "")),
             # отказы и открытые потоки строку не красят — только note
             (gw5(refused_1h=500, streams=30), ("ok", "принимает вход", "работает мест: 1; потоков: 30; отказов за час: 500")),
             (gw5(running_n=0, ready_n=0), ("ok", "принимает вход", "работает мест: 0; потоков: 0; отказов за час: 0")),
             (gw5(logins_failed_1h=30, refused_1h=4), ("warn", "много неверных паролей", "30 за час")),
             # учения — первыми в note и у строк Д5б
             (gw5(drill="freeze_reaper", refused_1h=7, streams=2),
              ("ok", "принимает вход", "учения: freeze_reaper; работает мест: 1; потоков: 2; отказов за час: 7")),
             (gw5(drill="bad_place_pw", stream_broken_1h=2), ("warn", "поток событий рвётся", "учения: bad_place_pw; 2 за час"))]
    for obj, want in cases:
        r = gate_check(fake_core, obj)
        assert (r.state, r.detail, r.note) == want, obj


def test_gateway_proxy_rows_constant_text(fake_core, tmp_path):                  # решение контроллера 4
    rows = [gate_check(fake_core, gw5(upstream_failed_1h=k)) for k in (1, 2, 40)]
    rows += [gate_check(fake_core, gw5(stream_broken_1h=k)) for k in (3, 5, 70)]
    rows += [gate_check(fake_core, gw5(streams=s, refused_1h=k)) for s, k in ((0, 0), (5, 1), (30, 200))]
    assert [(r.state, r.detail) for r in rows] == [("warn", "рабочее место не отвечает шлюзу")] * 3 + \
           [("warn", "поток событий рвётся")] * 3 + [("ok", "принимает вход")] * 3
    assert all(len(r.detail) <= REASON_CHARS for r in rows) and len({r.note for r in rows}) == 9
    journal = Journal(str(tmp_path / "j.db"))
    assert sum(len(journal.record([r], T0 + timedelta(seconds=10 * i))) for i, r in enumerate(rows)) == 3


# --- строка «Веб-чат» (задача А1 Д6а, Д6-R46): страница чата и опись сборки через 8080 edge ---------------------

def row(c):
    return (c.state, c.detail, c.note)


def test_web_verdicts(web_site):
    root, url = web_site                                                   # url = http://127.0.0.1:N/chat/
    put_build(root)
    assert row(check_web("web", "Веб-чат", url)) == ("ok", "страница чата отдаётся", "сборка abc123")
    (root / "chat/assets/index-1.css").unlink()
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "сборка чата неполная", "нет assets/index-1.css")
    put_build(root, meta="zzz")
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "сборка чата неполная", "страница и опись разных сборок")
    put_build(root, files={"../x.js": b"x"})
    assert row(check_web("web", "Веб-чат", url))[1:] == ("сборка чата неполная", "опись неверна")
    put_build(root, files={"assets/index-1.js": b"x", "boot.js": b"b"})    # загрузчик Д6б — законный путь описи
    assert row(check_web("web", "Веб-чат", url))[0] == "ok"
    put_build(root, files={f"assets/f{i}.js": b"x" for i in range(9)})
    assert row(check_web("web", "Веб-чат", url))[2] == "опись неверна"
    put_build(root, meta="")
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "страница чата не отдаётся", "нет метки сборки")
    (root / "chat/index.html").unlink()
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "страница чата не отдаётся", "HTTP 404")
    assert row(check_web("web", "Веб-чат", "http://127.0.0.1:9/chat/"))[:2] == ("fail", "не отвечает")


def test_web_check_keeps_deadline(slow_site):                              # раунд 2, В1: каждый ответ — через 2,5 с
    t0 = time.monotonic()                                                   # без общего срока: 2,5 + 2,5 + 2,5 = 7,5 с
    c = check_web("web", "Веб-чат", slow_site, timeout=3.0)
    assert time.monotonic() - t0 < 2 * 3.0 + 0.5                           # один срок на всю проверку
    assert (c.state, c.detail) == ("fail", "не отвечает")


def test_http_get_absolute_deadline(slow_site):                             # срок соединения и чтения — не дальше deadline
    t0 = time.monotonic()
    with pytest.raises((OSError, http.client.HTTPException)):
        _http_get(slow_site, 3.0, lambda code: True, deadline=time.monotonic() + 1.0)
    assert time.monotonic() - t0 < 1.0 + 0.5
    accepted = len(slow_site.accepted)                                     # M4: «без соединения» — по счёту двойника
    with pytest.raises(TimeoutError):
        _http_get(slow_site, 3.0, lambda code: True, deadline=time.monotonic() - 0.1)   # срок уже вышел — без соединения
    time.sleep(0.2)                                                         # опоздавшее соединение успело бы дойти
    assert accepted == 1 and len(slow_site.accepted) == accepted           # счёт живой: первый запрос учтён


def test_http_get_connect_bounded_by_deadline(full_backlog_url):           # доводка 1: соединение — не дольше остатка
    t0 = time.monotonic()                                                   # очередь забита: connect висит, пока не срок
    with pytest.raises(OSError):
        _http_get(full_backlog_url, 3.0, lambda code: True, deadline=time.monotonic() + 0.5)
    assert time.monotonic() - t0 < 0.5 + 0.4                               # полный timeout соединения — 3 с


def test_web_meta_only_in_contract_form(web_site):                          # доводка 1: WEB_META_RE
    root, url = web_site
    put_build(root)
    for head in ('<meta name="viewport" content="width=device-width, initial-scale=1">',
                 '<meta name="viewport" content="abc123">'):                # чужое имя с содержимым, как у метки
        (root / "chat/index.html").write_text(f"<!doctype html><head>{head}</head>")
        assert row(check_web("web", "Веб-чат", url)) == ("fail", "страница чата не отдаётся", "нет метки сборки"), head
    for meta in ("abc 123", "a" * 65):                                      # содержимое вне [A-Za-z0-9._-]{1,64}
        put_build(root, meta=meta)
        assert row(check_web("web", "Веб-чат", url)) == ("fail", "страница чата не отдаётся", "нет метки сборки"), meta
    put_build(root, build="a" * 64)                                         # граница: 64 знака — ещё метка
    assert row(check_web("web", "Веб-чат", url)) == ("ok", "страница чата отдаётся", "сборка " + "a" * 64)


def test_web_meta_beyond_body_limit(web_site):                              # доводка 1: тело страницы — до MAX_BODY_BYTES
    root, url = web_site
    put_build(root)
    head, tag = "<!doctype html><head><!--", '<meta name="pcbk-build" content="abc123">'
    fits = MAX_BODY_BYTES - len(head) - len("-->") - len(tag)              # метка кончается ровно на пределе
    for filler, want in ((fits, ("ok", "страница чата отдаётся", "сборка abc123")),
                         (fits + 1, ("fail", "страница чата не отдаётся", "нет метки сборки")),
                         (MAX_BODY_BYTES, ("fail", "страница чата не отдаётся", "нет метки сборки"))):
        (root / "chat/index.html").write_text(head + "x" * filler + "-->" + tag + "</head>")
        assert row(check_web("web", "Веб-чат", url)) == want, filler


def test_web_manifest_rows_and_body_stall(web_site, raw_http):             # строки таблицы Д6-R46 сверх test_web_verdicts
    root, url = web_site
    put_build(root)
    manifest = root / "chat/build.json"
    sha = json.loads(manifest.read_text())["files"]["assets/index-1.js"]
    for text in ("не JSON", "[1]", "[" * 60_000,                           # глубина — RecursionError, не падение проверки
                 json.dumps({"build": 1, "files": {"assets/index-1.js": sha}}),
                 json.dumps({"build": "abc123", "files": ["assets/index-1.js"]}),
                 json.dumps({"build": "abc123", "files": {}}),               # пустая опись ничего не проверила бы
                 json.dumps({"build": "abc123", "files": {"assets/index-1.js": "x"}}),        # не sha256
                 json.dumps({"build": "abc123", "files": {"assets/..": sha}}),                # сам каталог /chat/
                 json.dumps({"build": "abc123", "files": {"assets/sub/x.js": sha}}),
                 json.dumps({"build": "abc123", "files": {"boot.js.map": sha}})):
        manifest.write_text(text)
        assert row(check_web("web", "Веб-чат", url)) == ("fail", "сборка чата неполная", "опись неверна"), text[:60]
    manifest.unlink()                                                       # опись не 200
    assert row(check_web("web", "Веб-чат", url)) == ("fail", "сборка чата неполная", "опись неверна")
    put_build(root, files={f"assets/f{i}.js": b"x" for i in range(7)} | {"boot.js": b"b"})   # 8 записей с загрузчиком
    assert row(check_web("web", "Веб-чат", url)) == ("ok", "страница чата отдаётся", "сборка abc123")
    # тело страницы оборвал срок: это «не отвечает» (строка «срок» таблицы), а не «нет метки сборки»
    r, took = timed(check_web, "web", "Веб-чат", raw_http(headers_then_stall) + "/chat/", timeout=SOCK_S)
    assert row(r) == ("fail", "не отвечает", "") and took < BOUND_S


# --- строка «Веб-чат» с отчётами браузеров (задача Б1 Д6б, Д6-R50); /health/web — fake_core.set_web (preflight Ф2) ---

WEB_ZERO = {"boot_failed_1h": 0, "ui_crash_1h": 0, "turn_silent_1h": 0, "stream_down_1h": 0}
NO_REPORTS = ("ok", "страница чата отдаётся", "сборка abc123; отчёты браузеров недоступны")


def test_web_report_lines(web_site, fake_core):
    root, url = web_site; put_build(root); u = fake_core.base + "/health/web"
    zero = WEB_ZERO
    for reply, want in (({**zero, "boot_failed_1h": 1, "ui_crash_1h": 1}, ("warn", "чат не запускается в браузере", "1 за час")),
                        ({**zero, "ui_crash_1h": 1, "turn_silent_1h": 2}, ("warn", "чат падает в браузере", "1 за час")),
                        ({**zero, "turn_silent_1h": 2, "stream_down_1h": 3}, ("warn", "ответ модели не пришёл вовремя", "2 за час")),
                        ({**zero, "stream_down_1h": 3}, ("warn", "браузеры теряют связь с сервером", "3 за час")),
                        (zero, ("ok", "страница чата отдаётся", "сборка abc123"))):
        fake_core.set_web(200, reply)
        assert row(check_web("web", "Веб-чат", url, reports_url=u)) == want, reply
    assert row(check_web("web", "Веб-чат", url)) == ("ok", "страница чата отдаётся", "сборка abc123")   # без reports_url — как Д6а


def test_web_row_without_reports(web_site, fake_core, raw_http):                    # Review Focus 5
    root, url = web_site; put_build(root); u = fake_core.base + "/health/web"
    assert row(check_web("web", "Веб-чат", url, reports_url=u)) == NO_REPORTS         # core :d6a — ручки нет (404 текстом)
    for status, body in ((404, {"name": "NotFound"}), (200, {"ui_crash_1h": "x"}), (200, {"extra": 1}),
                         (503, WEB_ZERO), (200, [WEB_ZERO]), (200, {**WEB_ZERO, "extra": 1}),
                         (200, {**WEB_ZERO, "ui_crash_1h": -1}), (200, {**WEB_ZERO, "ui_crash_1h": True}),
                         (200, {**WEB_ZERO, "ui_crash_1h": 1.0}), (200, {k: v for k, v in WEB_ZERO.items() if k != "stream_down_1h"})):
        fake_core.set_web(status, body)
        assert row(check_web("web", "Веб-чат", url, reports_url=u)) == NO_REPORTS, (status, body)
    assert row(check_web("web", "Веб-чат", url, reports_url="http://127.0.0.1:9/health/web")) == NO_REPORTS   # нет связи
    r, took = timed(check_web, "web", "Веб-чат", url, timeout=SOCK_S, reports_url=raw_http(headers_then_stall) + "/health/web")
    assert row(r) == NO_REPORTS and took < BOUND_S                                    # тело отчётов оборвал срок
    fake_core.set_web(200, {**WEB_ZERO, "boot_failed_1h": 4})
    (root / "chat/index.html").unlink()                                               # статика важнее отчётов
    assert row(check_web("web", "Веб-чат", url, reports_url=u))[0] == "fail"


def test_web_reports_keep_check_deadline(web_site, fake_core):                       # раунд 1 Important 3, раунд 2 В1
    root, url = web_site; put_build(root)
    fake_core.set_web(200, WEB_ZERO, delay_s=10)                                      # /health/web тянет 10 с
    t0 = time.monotonic()
    r = check_web("web", "Веб-чат", url, timeout=3.0, reports_url=fake_core.base + "/health/web")
    assert time.monotonic() - t0 < 2 * 3.0 + 0.5                                     # статика и отчёты — в одном сроке
    assert row(r) == NO_REPORTS


def test_web_reports_share_static_deadline(web_site, fake_core, monkeypatch):      # сверх брифа: срок именно общий
    """Страница тянет 0,8 с: со своим сроком у запроса отчётов проверка шла бы 0,8 + 2 × 1 с, с общим — 2 с."""
    root, url = web_site; put_build(root)
    fake_core.set_web(200, WEB_ZERO, delay_s=10)
    real = checks_module._http_get

    def slow_page(target, *args, **kw):
        if target == url:
            time.sleep(0.8)
        return real(target, *args, **kw)
    monkeypatch.setattr(checks_module, "_http_get", slow_page)
    r, took = timed(check_web, "web", "Веб-чат", url, timeout=1.0, reports_url=fake_core.base + "/health/web")
    assert row(r) == NO_REPORTS and took < 2 * 1.0 + 0.4


# --- строка «Агенты студентов» (задача А1 Д7а, Д7-R60): ответ /health/agents core ---------------------------------

NOW = datetime(2026, 10, 7, 9, tzinfo=timezone.utc)
BASE = {"checked_at": NOW.isoformat(), "store_ok": True, "dir_error": None, "pending": 0, "pending_oldest_s": None,
        "saved_1h": 2, "refused_1h": 1, "unavailable_1h": 0, "visible_1h": 2, "not_visible_1h": 0, "quarantined_1h": 0,
        "reload_failed_1h": 0, "reload_delayed_1h": 0, "image_config_1h": 0, "drill": ""}

@pytest.mark.parametrize("patch,want", [
    ({}, ("ok", "агенты сохраняются", "сохранено за час: 2; ждут перезагрузки: 0")),
    ({"image_config_1h": 1}, ("fail", "настройка рабочего места сломана — место не поднимается", "1 за час")),
    ({"dir_error": "каталог агентов не пишется"}, ("fail", "каталог агентов недоступен — агенты не сохраняются", "каталог агентов не пишется")),
    ({"store_ok": False}, ("fail", "учёт агентов недоступен", "")),
    ({"checked_at": None}, ("unknown", "перезагрузка агентов ещё не проходила", "")),
    ({"checked_at": (NOW - timedelta(seconds=61)).isoformat()}, ("fail", "перезагрузка агентов молчит", "")),
    ({"quarantined_1h": 1}, ("warn", "файл агента сломал рабочее место — в карантине, место перезапущено", "1 за час")),
    ({"not_visible_1h": 2}, ("warn", "сохранённый агент не появился в рабочем месте", "2 за час")),
    ({"reload_failed_1h": 1}, ("warn", "перезагрузка рабочего места не удалась", "1 за час")),
    ({"pending": 1, "pending_oldest_s": 900}, ("warn", "агент ждёт перезагрузки места дольше 15 минут", "мест: 1")),
    ({"unavailable_1h": 2}, ("warn", "save_agent отвечает «недоступно» — агенты не сохраняются", "2 за час")),
    ({"saved_1h": 0, "refused_1h": 6}, ("warn", "save_agent отказывает — агенты не сохраняются", "6 за час")),
    ({"reload_delayed_1h": 1}, ("ok", "агенты сохраняются", "сохранено за час: 2; ждут перезагрузки: 0; ждали дольше 15 мин: 1")),
])
def test_agents_verdicts(patch, want):
    assert agents_verdict(AgentsReply.parse({**BASE, **patch}), NOW, 60) == want

@pytest.mark.parametrize("bad", [{**BASE, "extra": 1}, {k: v for k, v in BASE.items() if k != "drill"},
                                 {**BASE, "saved_1h": -1}, {**BASE, "drill": "other"}, [], {**BASE, "dir_error": ""}])
def test_agents_off_contract(bad):
    with pytest.raises(ValueError):
        AgentsReply.parse(bad)

def test_check_agents_codes(json_site):
    c = check_agents("agents", "Агенты студентов", json_site(404, {"name": "NotFound"}), NOW, 60)
    assert (c.state, c.detail) == ("unknown", "служба агентов ещё не выложена")
    c = check_agents("agents", "Агенты студентов", json_site(200, {**BASE, "drill": "freeze_reload"}), NOW, 60)
    assert c.note.startswith("учения: freeze_reload")
    c = check_agents("agents", "Агенты студентов", "http://127.0.0.1:9/x", NOW, 60)
    assert (c.state, c.detail) == ("fail", "служба агентов не отвечает")


# --- сверх брифа ---

AGENTS_DOWN = ("fail", "служба агентов не отвечает", "")
AGENTS_OFF = ("fail", "служба агентов ответила не по договору", "")


def agents_row(json_site, obj, code=200, stale_s=60):
    return row(check_agents("agents", "Агенты студентов", json_site(code, obj), NOW, stale_s, timeout=0.5))


def test_agents_contract_constants():
    assert AGENTS_KEYS == set(BASE) and len(AGENTS_KEYS) == 15
    assert AGENTS_KEYS == {f.name for f in fields(AgentsReply)}                    # договор = поля разбора
    assert AGENTS_DRILL_MODES == {"freeze_reload"}                                 # DRILL_AGENTS, кроме ""
    r = AgentsReply.parse({**BASE, "pending": 3, "pending_oldest_s": 12.5, "dir_error": None})
    assert (r.checked_at, r.pending, r.pending_oldest_s, r.store_ok) == (NOW, 3, 12.5, True)


def test_agents_verdict_order_and_bounds():                                    # первая подходящая строка таблицы Д7-R60
    every = {"image_config_1h": 1, "dir_error": "нет каталога агентов", "store_ok": False, "checked_at": None,
             "quarantined_1h": 1, "not_visible_1h": 1, "reload_failed_1h": 1, "pending": 2, "pending_oldest_s": 950,
             "unavailable_1h": 1, "saved_1h": 0, "refused_1h": 9}
    order = [("image_config_1h", "настройка рабочего места сломана — место не поднимается"),
             ("dir_error", "каталог агентов недоступен — агенты не сохраняются"),
             ("store_ok", "учёт агентов недоступен"),
             ("checked_at", "перезагрузка агентов ещё не проходила"),
             ("quarantined_1h", "файл агента сломал рабочее место — в карантине, место перезапущено"),
             ("not_visible_1h", "сохранённый агент не появился в рабочем месте"),
             ("reload_failed_1h", "перезагрузка рабочего места не удалась"),
             ("pending_oldest_s", "агент ждёт перезагрузки места дольше 15 минут"),
             ("unavailable_1h", "save_agent отвечает «недоступно» — агенты не сохраняются"),
             ("refused_1h", "save_agent отказывает — агенты не сохраняются")]
    obj = {**BASE, **every}
    for key, detail in order:                                                      # снимаем по одной сверху вниз
        assert agents_verdict(AgentsReply.parse(obj), NOW, 60)[1] == detail, key
        obj[key] = BASE[key]
    assert agents_verdict(AgentsReply.parse(obj), NOW, 60)[:2] == ("ok", "агенты сохраняются")
    stale = {**BASE, "checked_at": (NOW - timedelta(seconds=61)).isoformat(), "quarantined_1h": 3}
    assert agents_verdict(AgentsReply.parse(stale), NOW, 60)[1] == "перезагрузка агентов молчит"   # молчание выше warn
    assert agents_verdict(AgentsReply.parse({**stale, "store_ok": False}), NOW, 60)[1] == "учёт агентов недоступен"
    for patch, state in (({"checked_at": (NOW - timedelta(seconds=60)).isoformat()}, "ok"),   # ровно порог — ещё не молчит
                         ({"checked_at": (NOW - timedelta(seconds=61)).isoformat()}, "fail"),
                         ({"pending": 1, "pending_oldest_s": 899.9}, "ok"),
                         ({"pending": 4, "pending_oldest_s": 900.0}, "warn"),
                         ({"saved_1h": 0, "refused_1h": 5}, "ok"),
                         ({"saved_1h": 1, "refused_1h": 40}, "ok"),            # хоть одно сохранение — служба жива
                         ({"saved_1h": 0, "refused_1h": 6}, "warn")):
        assert agents_verdict(AgentsReply.parse({**BASE, **patch}), NOW, 60)[0] == state, patch
    assert agents_verdict(AgentsReply.parse({**BASE, "checked_at": (NOW - timedelta(seconds=61)).isoformat()}),
                          NOW, 120)[0] == "ok"                                     # порог — из AGENTS_STALE_S
    assert agents_verdict(AgentsReply.parse({**BASE, "pending": 4, "pending_oldest_s": 1200}), NOW, 60)[2] == "мест: 4"


def test_agents_details_fit_80_chars_and_numbers_only_in_note(tmp_path):        # правило журнала Д1
    objs = [{**BASE, **p} for p in ({"image_config_1h": 1}, {"dir_error": "нет каталога агентов"}, {"store_ok": False},
                                     {"checked_at": None}, {"checked_at": "2026-10-07T08:00:00+00:00"},
                                     {"quarantined_1h": 1}, {"not_visible_1h": 1}, {"reload_failed_1h": 1},
                                     {"pending": 1, "pending_oldest_s": 900}, {"unavailable_1h": 1},
                                     {"saved_1h": 0, "refused_1h": 6}, {})]
    details = [agents_verdict(AgentsReply.parse(o), NOW, 60)[1] for o in objs]
    details += ["служба агентов не отвечает", "служба агентов ещё не выложена", "служба агентов ответила не по договору"]
    assert all(len(d) <= REASON_CHARS for d in details), [d for d in details if len(d) > REASON_CHARS]
    assert not [d for d in details if any(ch.isdigit() for ch in d) and "15 минут" not in d]   # кроме порога в тексте
    journal = Journal(str(tmp_path / "j.db"))
    rows = [Check("agents", "Агенты студентов", *agents_verdict(AgentsReply.parse({**BASE, "not_visible_1h": k}), NOW, 60))
            for k in (1, 2, 7)]
    rows += [Check("agents", "Агенты студентов", *agents_verdict(AgentsReply.parse({**BASE, "saved_1h": k}), NOW, 60))
             for k in (0, 3, 9)]
    assert len({r.note for r in rows}) == 6
    assert sum(len(journal.record([r], NOW + timedelta(seconds=10 * i))) for i, r in enumerate(rows)) == 2


def test_agents_dir_error_note_is_safe(json_site):                                # note — без путей и не длиннее 80
    assert agents_row(json_site, {**BASE, "dir_error": "каталог места отсутствует или не пишется"}) == \
           ("fail", "каталог агентов недоступен — агенты не сохраняются", "каталог места отсутствует или не пишется")
    r = agents_row(json_site, {**BASE, "dir_error": "сбой /opt/pcbk-reserve/agents/student-03 " + "х" * 100})
    assert r[2].startswith("сбой … ") and len(r[2]) <= REASON_CHARS and "student-03" not in r[2]


def test_agents_off_contract_reply_has_own_text(json_site):                     # Ш8: как у строки шлюза
    missing = [{k: v for k, v in BASE.items() if k != key} for key in BASE]
    for obj in (*missing, "строка", 7, None, {}, [BASE], {**BASE, "extra": 0}, {**BASE, "streams": 0},
                {**BASE, "checked_at": 12}, {**BASE, "checked_at": "вчера"}, {**BASE, "store_ok": None},
                {**BASE, "store_ok": 1}, {**BASE, "dir_error": 5}, {**BASE, "dir_error": "   "},
                {**BASE, "dir_error": "\n\t"}, {**BASE, "pending": True}, {**BASE, "pending": 1.0},
                {**BASE, "pending": -1}, {**BASE, "pending": None}, {**BASE, "pending_oldest_s": "900"},
                {**BASE, "pending_oldest_s": -1}, {**BASE, "pending_oldest_s": True}, {**BASE, "pending_oldest_s": []},
                {**BASE, "pending_oldest_s": 10 ** 400},                         # целое больше float — не падение проверки
                *({**BASE, k: v} for k in BASE if k.endswith("_1h") for v in (-1, True, 1.5, "1", None)),
                {**BASE, "drill": None}, {**BASE, "drill": "FREEZE_RELOAD"}, {**BASE, "drill": "freeze_reaper"},
                {**BASE, "drill": "freeze_reload "}, {**BASE, "drill": " "}):
        assert agents_row(json_site, obj) == AGENTS_OFF, obj
        with pytest.raises(ValueError):                                            # не KeyError и не TypeError
            AgentsReply.parse(obj)
    assert len(missing) == 15
    for bad in (float("nan"), float("inf")):                                       # json.dumps пишет NaN/Infinity
        with pytest.raises(ValueError):
            AgentsReply.parse({**BASE, "pending_oldest_s": bad})


def test_agents_http_errors_are_down(json_site, raw_http):
    for code in (500, 503, 401, 302):                                              # 404 — своя строка
        assert agents_row(json_site, BASE, code=code) == AGENTS_DOWN, code

    def text_reply(conn, stop):
        body = "не json".encode()
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
    r = check_agents("agents", "Агенты студентов", raw_http(text_reply) + "/health/agents", NOW, 60, timeout=0.5)
    assert row(r) == AGENTS_DOWN
    r, took = timed(check_agents, "agents", "Агенты студентов", raw_http(headers_then_stall) + "/health/agents", NOW, 60,
                    timeout=SOCK_S)
    assert row(r) == AGENTS_DOWN and took < BOUND_S                                # тело оборвал общий срок
    assert agents_row(json_site, {"detail": "Not Found"}, code=404) == \
           ("unknown", "служба агентов ещё не выложена", "")                         # core :d6b — 404 FastAPI


def test_agents_drill_goes_first_in_note(json_site):
    drill = {**BASE, "drill": "freeze_reload"}
    assert agents_row(json_site, drill) == \
           ("ok", "агенты сохраняются", "учения: freeze_reload; сохранено за час: 2; ждут перезагрузки: 0")
    assert agents_row(json_site, {**drill, "checked_at": (NOW - timedelta(seconds=90)).isoformat()}) == \
           ("fail", "перезагрузка агентов молчит", "учения: freeze_reload")       # учение молчания — с пометкой
    assert agents_row(json_site, {**drill, "not_visible_1h": 3}) == \
           ("warn", "сохранённый агент не появился в рабочем месте", "учения: freeze_reload; 3 за час")


# --- сверка с вебом (итоговая доводка Д7б, m-1): fail строки агентов, который баннер студентов не показывает ---

REPO = Path(__file__).resolve().parents[2]
COMPONENTS = json.loads((Path(__file__).resolve().parents[1] / "components.json").read_text("utf-8"))


def web_owner_fails(status_ts: str) -> dict[str, str]:
    """OWNER_FAILS из web/src/status.ts: пары ["строка", "detail"] внутри new Map([...]) — простым разбором (как сверка
    BUILTIN_AGENT_TITLES в core)."""
    block = re.search(r"const OWNER_FAILS\b[^=]*= new Map\(\[(.*?)\]\);", status_ts, re.S)
    assert block is not None, "OWNER_FAILS не найден в web/src/status.ts"
    pairs = re.findall(r'\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\]', block.group(1))
    assert len(pairs) == block.group(1).count("["), "запись OWNER_FAILS не того вида"
    return dict(pairs)


def test_web_owner_fails_parser_self_check():                                     # положительный контроль разборщика
    sample = ('const OWNER_FAILS: ReadonlyMap<string, string> = new Map([\n  ["a", "б — в"],\n  ["c", "д"],\n]);\n'
              'const OTHER = new Map([["x", "y"]]);\n')
    assert web_owner_fails(sample) == {"a": "б — в", "c": "д"}


def test_web_owner_fails_match_agents_row():
    """Веб прячет от студентов fail строки «Агенты студентов» по detail дословно: разойдутся тексты — красный баннер
    одного сломанного места снова у всех десяти студентов. Сверяются detail таблицы Д7-R60 (image_config_1h) и id строки
    вида agents в components.json."""
    web = REPO / "web"
    if not web.is_dir():
        pytest.skip("в копии нет web/ — сверка с вебом идёт в копии с web")
    state, detail, _ = agents_verdict(AgentsReply.parse({**BASE, "image_config_1h": 1}), NOW, 60)
    ids = [c["id"] for c in COMPONENTS if c["kind"] == "agents"]
    assert state == "fail" and ids == ["agents"]
    assert web_owner_fails((web / "src/status.ts").read_text("utf-8")) == {"agents": detail}


# --- строка «Кабинет преподавателя и доли» (задача А1 Д10а, Д10-R96): ответ /health/teacher core -------------------

NOW_T = datetime(2026, 10, 11, 9, tzinfo=timezone.utc)
T_BASE = {"checked_at": NOW_T.isoformat(), "shares_loaded": True, "shares_ok": True, "shares_drift": 0,
          "shares_sum_usd": 1000.0, "total_usd": 1000.0, "store_ok": True, "reads_1h": 3, "read_failed_1h": 0,
          "changes_1h": 1, "change_failed_1h": 0, "role_refused_1h": 0, "drill": ""}


@pytest.mark.parametrize("patch,want", [
    ({}, ("ok", "доли сходятся, журнал действий пишется",
          "распределено $1000.00 из $1000; прочитано за час: 3; изменений за час: 1")),
    ({"store_ok": False, "checked_at": None}, ("fail", "журнал действий преподавателя не пишется — кабинет закрыт", "")),
    ({"checked_at": None, "shares_loaded": False}, ("unknown", "сверка долей ещё не проходила", "")),
    ({"shares_loaded": False}, ("fail", "доли бюджета не загружены — LLM-прокси закрыт для студентов", "")),
    ({"shares_ok": False}, ("fail", "сверка долей не прочла таблицу — расхождение не видно", "")),     # m-3
    ({"shares_drift": 2}, ("fail", "LLM-прокси считает доли не так, как сохранено", "мест: 2")),
    ({"checked_at": (NOW_T - timedelta(seconds=121)).isoformat()}, ("fail", "сверка долей молчит", "")),
    ({"shares_sum_usd": 1000.02}, ("warn", "сумма долей мест больше общего бюджета — держит общий бюджет",
                                   "распределено $1000.02 из $1000")),
    ({"changes_1h": 0, "change_failed_1h": 3}, ("warn", "изменения преподавателя не проходят", "3 за час")),
    ({"read_failed_1h": 3}, ("warn", "разговоры студентов не читаются", "3 за час")),
    ({"role_refused_1h": 10}, ("warn", "кабинет преподавателя открывают без роли преподавателя", "10 за час")),
])
def test_teacher_verdicts(patch, want):
    assert teacher_verdict(TeacherReply.parse({**T_BASE, **patch}), NOW_T, 120) == want


@pytest.mark.parametrize("bad", [{**T_BASE, "extra": 1}, {k: v for k, v in T_BASE.items() if k != "drill"},
                                 {**T_BASE, "reads_1h": -1}, {**T_BASE, "drill": "other"}, {**T_BASE, "total_usd": 0},
                                 {**T_BASE, "shares_sum_usd": -0.01}, {**T_BASE, "shares_ok": "yes"},
                                 {**T_BASE, "shares_loaded": None}, []])
def test_teacher_off_contract(bad):
    with pytest.raises(ValueError):
        TeacherReply.parse(bad)


def test_teacher_student_fail_is_a_row_detail():                        # I-3: этот текст видит баннер студента
    assert TEACHER_STUDENT_FAIL in TEACHER_DETAILS
    assert teacher_verdict(TeacherReply.parse({**T_BASE, "shares_loaded": False}), NOW_T, 120)[1] == TEACHER_STUDENT_FAIL


def test_check_teacher_codes(json_site):
    c = check_teacher("teacher", "Кабинет преподавателя и доли", json_site(404, {"name": "NotFound"}), NOW_T, 120)
    assert (c.state, c.detail) == ("unknown", "кабинет преподавателя ещё не выложен")
    c = check_teacher("teacher", "Кабинет преподавателя и доли",
                      json_site(200, {**T_BASE, "shares_drift": 2, "drill": "shares_stale"}), NOW_T, 120)
    assert (c.state, c.note) == ("fail", "учения: shares_stale; мест: 2")
    c = check_teacher("teacher", "Кабинет преподавателя и доли", json_site(200, {**T_BASE, "x": 1}), NOW_T, 120)
    assert (c.state, c.detail) == ("fail", "кабинет преподавателя ответил не по договору")
    c = check_teacher("teacher", "Кабинет преподавателя и доли", "http://127.0.0.1:9/x", NOW_T, 120)
    assert (c.state, c.detail) == ("fail", "кабинет преподавателя не отвечает")


def test_teacher_details_fit_80_chars():                                 # правило журнала Д1
    for detail in TEACHER_DETAILS:
        assert len(detail) <= 80 and not any(ch.isdigit() for ch in detail), detail


# --- сверх брифа ---

TEACHER_DOWN = ("fail", "кабинет преподавателя не отвечает", "")
TEACHER_OFF = ("fail", "кабинет преподавателя ответил не по договору", "")


def teacher_row(json_site, obj, code=200, stale_s=120):
    return row(check_teacher("teacher", "Кабинет преподавателя и доли", json_site(code, obj), NOW_T, stale_s,
                             timeout=0.5))


def test_teacher_contract_constants():
    assert TEACHER_KEYS == set(T_BASE) and len(TEACHER_KEYS) == 13
    assert TEACHER_KEYS == {f.name for f in fields(TeacherReply)}                  # договор = поля разбора
    assert TEACHER_DRILL_MODES == {"shares_stale", "journal_down"}                 # DRILL_TEACHER, кроме ""
    r = TeacherReply.parse({**T_BASE, "shares_sum_usd": 0, "total_usd": 50, "checked_at": None})
    assert (r.checked_at, r.shares_sum_usd, r.total_usd, r.store_ok) == (None, 0, 50, True)   # целые деньги — тоже числа
    assert len(TEACHER_DETAILS) == len(set(TEACHER_DETAILS)) == 14               # 11 строк таблицы и 3 ответа ручки


def test_teacher_verdict_order_and_bounds():                                   # первая подходящая строка таблицы R96
    every = {"store_ok": False, "checked_at": None, "shares_loaded": False, "shares_ok": False, "shares_drift": 3,
             "shares_sum_usd": 2000.0, "changes_1h": 0, "change_failed_1h": 5, "read_failed_1h": 4,
             "role_refused_1h": 11}
    order = [("store_ok", "журнал действий преподавателя не пишется — кабинет закрыт"),
             ("checked_at", "сверка долей ещё не проходила"),
             ("shares_loaded", "доли бюджета не загружены — LLM-прокси закрыт для студентов"),
             ("shares_ok", "сверка долей не прочла таблицу — расхождение не видно"),
             ("shares_drift", "LLM-прокси считает доли не так, как сохранено"),
             ("shares_sum_usd", "сумма долей мест больше общего бюджета — держит общий бюджет"),
             ("change_failed_1h", "изменения преподавателя не проходят"),
             ("read_failed_1h", "разговоры студентов не читаются"),
             ("role_refused_1h", "кабинет преподавателя открывают без роли преподавателя")]
    obj = {**T_BASE, **every}
    for key, detail in order:                                                      # снимаем по одной сверху вниз
        assert teacher_verdict(TeacherReply.parse(obj), NOW_T, 120)[1] == detail, key
        obj[key] = T_BASE[key]
    assert teacher_verdict(TeacherReply.parse(obj), NOW_T, 120)[:2] == ("ok", "доли сходятся, журнал действий пишется")
    stale = {**T_BASE, "checked_at": (NOW_T - timedelta(seconds=121)).isoformat()}
    assert teacher_verdict(TeacherReply.parse({**stale, "shares_drift": 1}), NOW_T, 120)[1] == \
           "LLM-прокси считает доли не так, как сохранено"                         # расхождение выше молчания
    assert teacher_verdict(TeacherReply.parse({**stale, "read_failed_1h": 9}), NOW_T, 120)[1] == \
           "сверка долей молчит"                                                   # молчание выше warn
    assert teacher_verdict(TeacherReply.parse({**stale, "store_ok": False}), NOW_T, 120)[1] == \
           "журнал действий преподавателя не пишется — кабинет закрыт"
    for patch, state in (({"checked_at": (NOW_T - timedelta(seconds=120)).isoformat()}, "ok"),   # ровно порог
                         ({"checked_at": (NOW_T - timedelta(seconds=121)).isoformat()}, "fail"),
                         ({"shares_sum_usd": 1000.005}, "ok"),                    # запас полцента: ещё не больше
                         ({"shares_sum_usd": 1000.0051}, "warn"),
                         ({"shares_sum_usd": 999.99}, "ok"),
                         ({"changes_1h": 0, "change_failed_1h": 2}, "ok"),
                         ({"changes_1h": 1, "change_failed_1h": 30}, "ok"),       # хоть одно изменение прошло
                         ({"changes_1h": 0, "change_failed_1h": 3}, "warn"),
                         ({"read_failed_1h": 2}, "ok"), ({"read_failed_1h": 3}, "warn"),
                         ({"role_refused_1h": 9}, "ok"), ({"role_refused_1h": 10}, "warn")):
        assert teacher_verdict(TeacherReply.parse({**T_BASE, **patch}), NOW_T, 120)[0] == state, patch
    assert teacher_verdict(TeacherReply.parse({**T_BASE, "checked_at": (NOW_T - timedelta(seconds=121)).isoformat()}),
                           NOW_T, 300)[0] == "ok"                                  # порог — из TEACHER_STALE_S


def test_teacher_notes_money_and_numbers():
    assert teacher_verdict(TeacherReply.parse({**T_BASE, "shares_sum_usd": 812.34, "reads_1h": 0, "changes_1h": 7}),
                           NOW_T, 120) == ("ok", "доли сходятся, журнал действий пишется",
                                           "распределено $812.34 из $1000; прочитано за час: 0; изменений за час: 7")
    assert teacher_verdict(TeacherReply.parse({**T_BASE, "shares_sum_usd": 0}), NOW_T, 120)[2].startswith(
        "распределено $0.00 из $1000;")
    # учение «бюджет стенда израсходован» (compose.test.yaml, LLM_TOTAL_USD=0.001): бюджет не целый — с центами
    assert teacher_verdict(TeacherReply.parse({**T_BASE, "total_usd": 0.001}), NOW_T, 120) == \
           ("warn", "сумма долей мест больше общего бюджета — держит общий бюджет", "распределено $1000.00 из $0.0010")
    assert teacher_verdict(TeacherReply.parse({**T_BASE, "shares_sum_usd": 1000, "total_usd": 1000}), NOW_T, 120)[2] \
        .startswith("распределено $1000.00 из $1000;")                              # целые из JSON — как дробные


def test_teacher_details_constant_numbers_only_in_note(tmp_path):              # правило журнала Д1
    objs = [{**T_BASE, **p} for p in ({"store_ok": False}, {"checked_at": None}, {"shares_loaded": False},
                                       {"shares_ok": False}, {"shares_drift": 1},
                                       {"checked_at": "2026-10-11T08:00:00+00:00"}, {"shares_sum_usd": 1500.0},
                                       {"changes_1h": 0, "change_failed_1h": 3}, {"read_failed_1h": 3},
                                       {"role_refused_1h": 10}, {})]
    details = [teacher_verdict(TeacherReply.parse(o), NOW_T, 120)[1] for o in objs]
    assert set(details) | {"кабинет преподавателя не отвечает", "кабинет преподавателя ещё не выложен",
                           "кабинет преподавателя ответил не по договору"} == set(TEACHER_DETAILS)
    journal = Journal(str(tmp_path / "j.db"))
    rows = [Check("teacher", "Кабинет преподавателя и доли",
                  *teacher_verdict(TeacherReply.parse({**T_BASE, "shares_drift": k}), NOW_T, 120)) for k in (1, 2, 7)]
    rows += [Check("teacher", "Кабинет преподавателя и доли",
                   *teacher_verdict(TeacherReply.parse({**T_BASE, "reads_1h": k}), NOW_T, 120)) for k in (0, 3, 9)]
    assert len({r.note for r in rows}) == 6
    assert sum(len(journal.record([r], NOW_T + timedelta(seconds=10 * i))) for i, r in enumerate(rows)) == 2


def test_teacher_off_contract_reply_has_own_text(json_site):                   # Ш8: как у строки агентов
    missing = [{k: v for k, v in T_BASE.items() if k != key} for key in T_BASE]
    counts = [k for k in T_BASE if k.endswith("_1h")] + ["shares_drift"]
    for obj in (*missing, "строка", 7, None, {}, [T_BASE], {**T_BASE, "extra": 0}, {**T_BASE, "pending": 0},
                {**T_BASE, "checked_at": 12}, {**T_BASE, "checked_at": "вчера"}, {**T_BASE, "checked_at": True},
                *({**T_BASE, k: v} for k in ("shares_loaded", "shares_ok", "store_ok") for v in (None, 1, 0, "true")),
                *({**T_BASE, k: v} for k in counts for v in (-1, True, 1.5, "1", None)),
                *({**T_BASE, k: v} for k in ("shares_sum_usd", "total_usd") for v in ("1000", None, True, [], 10 ** 400)),
                {**T_BASE, "shares_sum_usd": -0.01}, {**T_BASE, "total_usd": 0}, {**T_BASE, "total_usd": -5},
                {**T_BASE, "shares_drift": 11}, {**T_BASE, "shares_drift": 10 ** 4000},   # мест больше десяти (р1, m-1)
                {**T_BASE, "drill": None}, {**T_BASE, "drill": "SHARES_STALE"}, {**T_BASE, "drill": "freeze_reload"},
                {**T_BASE, "drill": "journal_down "}, {**T_BASE, "drill": " "}):
        assert teacher_row(json_site, obj) == TEACHER_OFF, obj
        with pytest.raises(ValueError):                                            # не KeyError и не TypeError
            TeacherReply.parse(obj)
    assert len(missing) == 13
    for key in ("shares_sum_usd", "total_usd"):
        for bad in (float("nan"), float("inf")):                                   # json.dumps пишет NaN/Infinity
            with pytest.raises(ValueError):
                TeacherReply.parse({**T_BASE, key: bad})


def test_teacher_http_errors_are_down(json_site, raw_http):
    for code in (500, 503, 401, 302):                                              # 404 — своя строка
        assert teacher_row(json_site, T_BASE, code=code) == TEACHER_DOWN, code

    def text_reply(conn, stop):
        body = "не json".encode()
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
    r = check_teacher("teacher", "Кабинет преподавателя и доли", raw_http(text_reply) + "/health/teacher", NOW_T, 120,
                      timeout=0.5)
    assert row(r) == TEACHER_DOWN
    r, took = timed(check_teacher, "teacher", "Кабинет преподавателя и доли",
                    raw_http(headers_then_stall) + "/health/teacher", NOW_T, 120, timeout=SOCK_S)
    assert row(r) == TEACHER_DOWN and took < BOUND_S                               # тело оборвал общий срок
    assert teacher_row(json_site, {"detail": "Not Found"}, code=404) == \
           ("unknown", "кабинет преподавателя ещё не выложен", "")                    # core :d9a — 404 FastAPI


def test_teacher_drill_goes_first_in_note(json_site):
    stale = {**T_BASE, "drill": "shares_stale"}
    assert teacher_row(json_site, stale) == \
           ("ok", "доли сходятся, журнал действий пишется",
            "учения: shares_stale; распределено $1000.00 из $1000; прочитано за час: 3; изменений за час: 1")
    down = {**T_BASE, "drill": "journal_down", "store_ok": False}
    assert teacher_row(json_site, down) == \
           ("fail", "журнал действий преподавателя не пишется — кабинет закрыт", "учения: journal_down")
    assert teacher_row(json_site, {**down, "store_ok": True, "checked_at": None}) == \
           ("unknown", "сверка долей ещё не проходила", "учения: journal_down")


def test_teacher_huge_money_is_off_contract_not_check_error(json_site):          # доводка р1, I-1: как у check_llm
    # fmt_usd (Decimal, 28 знаков) не форматирует сумму от 1e26: строка — «не по договору», а не «ошибка проверки»
    for obj in ({**T_BASE, "shares_sum_usd": 1e30}, {**T_BASE, "shares_sum_usd": 1e26},
                {**T_BASE, "shares_sum_usd": 1e30, "total_usd": 1e300}):
        TeacherReply.parse(obj)                                                    # разбор пропускает: число по договору
        assert teacher_row(json_site, obj) == TEACHER_OFF, obj
    assert teacher_row(json_site, {**T_BASE, "shares_sum_usd": 5e-324, "total_usd": 1e300})[:2] == \
           ("ok", "доли сходятся, журнал действий пишется")                         # малые и большие, но форматируемые
    assert teacher_row(json_site, {**T_BASE, "shares_sum_usd": 1e25})[:2] == \
           ("warn", "сумма долей мест больше общего бюджета — держит общий бюджет")


def test_teacher_drift_bounded_by_places(json_site):                             # доводка р1, m-1: note не растёт
    assert teacher_row(json_site, {**T_BASE, "shares_drift": 10}) == \
           ("fail", "LLM-прокси считает доли не так, как сохранено", "мест: 10")       # все десять мест — по договору
    for drift in (11, 10 ** 4000):
        assert teacher_row(json_site, {**T_BASE, "shares_drift": drift}) == TEACHER_OFF, drift
