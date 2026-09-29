from datetime import timedelta, timezone

from helpers import T0, insp, now_utc
from pcbk_watchdog.checks import (check_container, check_http, check_memory, check_tls,
                                  not_installed, tls_verdict)

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


def test_paused_is_fail():
    r = check_container("sp-ctl", "Прокси сокета серверного слоя", insp(running=True, paused=True),
                        sleeping_ok=False, now=T0)
    assert (r.state, r.detail) == ("fail", "приостановлен")


def test_failed_start_is_fail():
    r = stu(insp(code=127, error="OCI runtime create failed: unknown runtime runsc"))
    assert r.state == "fail" and r.detail.startswith("не запускается: OCI runtime create failed")


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
