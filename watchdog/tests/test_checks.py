import time
from datetime import timedelta, timezone

from helpers import T0, insp, llm_obj, now_utc
from pcbk_watchdog.checks import (ACCOUNT_WARN_USD, BUDGET_STEPS, DRIFT_WARN_USD, EGRESS_STALE_S,
                                  LLM_STALE_S, check_container, check_historian, check_http, check_llm,
                                  check_memory, check_tls, fmt_total, fmt_usd, not_installed,
                                  tls_verdict)
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
            llm_obj(egress={"alive": True, "last_ok_age_s": 999.0})]
    details = [llm(fake_core, o).detail for o in objs] + ["LLM-прокси не отвечает"]
    assert all(len(d) <= 80 for d in details), [d for d in details if len(d) > 80]


def test_fmt_usd_and_total():
    assert [fmt_usd(x) for x in (0, 0.0, 0.0012, 0.49, 0.99994, 1, 12.3456, 999.999)] == \
           ["$0.00", "$0.00", "$0.0012", "$0.4900", "$0.9999", "$1.00", "$12.35", "$1000.00"]
    assert (fmt_total(1000.0), fmt_total(1000), fmt_total(250.0)) == ("$1000", "$1000", "$250")


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
             (llm_obj(upstream=up("unknown", "OpenRouter ещё не опрашивался")),           # таблица: «иначе» — ok
              ("ok", "OpenRouter ещё не опрашивался"))]
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
                llm_obj(config_error=5), llm_obj(pending_stuck=None), llm_obj(provider_mismatch_24h=True)):
        r = llm(fake_core, obj)
        assert (r.state, r.detail, r.note) == ("fail", "LLM-прокси ответил не по договору", ""), obj
