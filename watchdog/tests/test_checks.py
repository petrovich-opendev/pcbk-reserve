from datetime import timedelta, timezone

from helpers import T0, insp, now_utc
from pcbk_watchdog.checks import (check_container, check_historian, check_http, check_memory,
                                  check_tls, not_installed, tls_verdict)
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


def test_check_http_reason_fallbacks(fake_core):
    url = fake_core.base + "/healthz/data"
    fake_core.set_health(200, {"ok": True, "detail": "каталог: 6"})
    assert check_http("web", "Веб", url).detail == "отвечает"                  # по умолчанию, как в Д1
    fake_core.set_health(503, {"ok": False, "detail": "ф" * 100})
    assert check_http("core", "Служба данных", url).detail == "ф" * 80          # первые 80 знаков
    for body in ({"ok": False}, {"detail": ["не строка"]}, ["detail"], {"detail": ""}):
        fake_core.set_health(500, body)
        r = check_http("core", "Служба данных", url)
        assert (r.state, r.detail) == ("fail", "отвечает ошибкой HTTP 500")
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


def test_historian_bad_reply_is_unknown(fake_core):
    down = ("unknown", "служба данных не отвечает — свежесть неизвестна")
    r = check_historian("historian", "Историан БДРВ", fake_core.base + "/nope", T0, 300, 900, 120, 0.5)
    assert (r.state, r.detail) == down                                            # HTTP ≠ 200
    for obj in ("не json", [fresh(5)], {"checked_at": T0.isoformat()}, fresh("5"), fresh(True),
                fresh(5, checked_at="вчера"), fresh(5, checked_at=12)):
        if isinstance(obj, str):
            fake_core.set_raw(obj)
        else:
            fake_core.set(obj)
        r = check_historian("historian", "Историан БДРВ", fake_core.url, T0, 300, 900, 120, 0.5)
        assert (r.state, r.detail) == down, obj


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
