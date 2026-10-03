"""Прогон отбора модели в LLM-прокси (Д8-R69): потолок поверх долей и общего бюджета, точка отсчёта в llm_meta,
отказ 402 pcbk_run_spent, резервы идущих вызовов, блок run в /health/llm — на двойнике OpenRouter."""
import logging
import sqlite3
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from helpers import (LLM_SETTINGS, LLM_TOKEN, LLM_TOKENS, OPS_LLM_TOKEN, SES, SETTINGS, auth, chat_body, make_llm_app,
                     make_llm_role, wait_until)
from pcbk_core.app import create_app
from pcbk_core.llm import RUN_BASE_KEY, RUN_WINDOW_S, LlmRole
from pcbk_core.llm.api import LLM_MESSAGES, REFUSALS
from pcbk_core.llm.ledger import Budget, Ledger, LedgerWriteLost, RunSpec, Usage
from pcbk_core.llm.upstream import Upstream

# make_llm_app, chat_body, auth, OPS_LLM_TOKEN Д4а; двойник берёт $0.0012 за вызов
RUN = {"LLM_RUN_ID": "d8-test", "LLM_RUN_USD": 0.002, "LLM_RUN_CALLERS": "student-01,ops"}


def post(c, body, token=LLM_TOKEN):
    return c.post("/llm/v1/chat/completions", json=body, headers=auth(token, SES))


def test_run_cap_refuses_run_callers_only(tmp_path, fake_or):                   # Review Focus 1
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    with TestClient(app) as c:
        assert [post(c, chat_body()).status_code for _ in range(2)] == [200, 200]   # $0.0024 ≥ $0.002
        r = post(c, chat_body())
    assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_run_spent")
    assert LLM_MESSAGES["run_spent"] in r.text and len(fake_or.requests) == 2       # отказ — до OpenRouter


def test_caller_outside_run_unaffected(tmp_path, fake_or):
    app, _ = make_llm_app(tmp_path, fake_or, **{**RUN, "LLM_RUN_CALLERS": "student-01"})
    with TestClient(app) as c:
        post(c, chat_body()); post(c, chat_body())
        assert post(c, chat_body()).status_code == 402
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 200


def test_run_base_excludes_spent_before_run(tmp_path, fake_or):
    app, _ = make_llm_app(tmp_path, fake_or)                                       # до прогона: $0.0024
    with TestClient(app) as c:
        post(c, chat_body()); post(c, chat_body())
    app, _ = make_llm_app(tmp_path, fake_or, **RUN)                                # тот же core.db
    with TestClient(app) as c:
        assert c.get("/health/llm").json()["run"]["spent_usd"] == 0
        assert post(c, chat_body()).status_code == 200


def test_run_base_survives_restart(tmp_path, fake_or):                           # Review Focus 1; доводка р1, I2
    app, _ = make_llm_app(tmp_path, fake_or)                                       # до прогона: $0.0012
    with TestClient(app) as c:
        post(c, chat_body())
    app, _ = make_llm_app(tmp_path, fake_or, **RUN)
    with TestClient(app) as c:
        post(c, chat_body()); post(c, chat_body())                                 # прогон: $0.0024 ≥ $0.002
    app, _ = make_llm_app(tmp_path, fake_or, **RUN)                                # перезапуск: точка — с диска
    with TestClient(app) as c:
        assert c.get("/health/llm").json()["run"]["spent_usd"] == pytest.approx(0.0024)   # не 0 и не 0.0036
        assert post(c, chat_body()).status_code == 402
    app, _ = make_llm_app(tmp_path, fake_or, **{**RUN, "LLM_RUN_ID": "d8-next"})   # новый id — новая точка
    with TestClient(app) as c:
        assert c.get("/health/llm").json()["run"]["spent_usd"] == 0
        assert post(c, chat_body()).status_code == 200


def test_run_counts_open_reserves(tmp_path, fake_or):                            # Review Focus 1
    # резерв FAKESLOW (оценка Д4а-R13, ≈$0.07 у glm-5.3) уже в расходе прогона, пока вызов идёт
    app, role = make_llm_app(tmp_path, fake_or, **{**RUN, "LLM_RUN_USD": 0.01})
    with TestClient(app) as c:
        slow = threading.Thread(target=post, args=(c, chat_body("FAKESLOW")))
        slow.start()
        wait_until(lambda: role.inflight.get("student-01") == 1)
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 402
        slow.join(30)
        assert not slow.is_alive()                                                  # m3: поток не висит молча
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 200         # цена пришла: $0.0012


def test_run_base_counts_unpriced_calls(tmp_path, fake_or, caplog):              # доводка р1, I1 (вместо m18)
    # вызов без цены у места прогона на старте прокси не закрывает: точка — по оплаченному, его цена — в прогоне
    app, role = make_llm_app(tmp_path, fake_or)
    role.cost_retry_delays = (30.0,)                                                # m-н15: строка остаётся pending
    with TestClient(app) as c:
        post(c, chat_body("FAKEGENNEVER"))
    app, role = make_llm_app(tmp_path, fake_or, **{**RUN, "LLM_RUN_USD": 1.0})
    role.cost_retry_delays = (30.0,)                                                # и после старта — pending
    with caplog.at_level(logging.WARNING, logger="pcbk_core.llm"), TestClient(app) as c:
        est = role.ledger.estimated("student-01")
        assert role.config_error is None and est > 0 and role.ledger.pending_to_resolve()
        assert c.get("/health/llm").json()["run"]["spent_usd"] == pytest.approx(est)
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 200
        assert post(c, chat_body()).status_code == 200
    assert "вызовы без цены у мест прогона без точки отсчёта, мест: 1" in caplog.text


def test_run_json_contract(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    with TestClient(app) as c:
        post(c, chat_body())
        run = c.get("/health/llm").json()["run"]
    assert set(run) == {"id", "cap_usd", "spent_usd", "callers", "calls_10m", "idle_s"}
    assert (run["id"], run["cap_usd"], run["callers"], run["calls_10m"]) == ("d8-test", 0.002, 2, 1)
    assert run["spent_usd"] == pytest.approx(0.0012) and 0 <= run["idle_s"] < 5
    last = role.last_run_call_mono
    assert role.run_json(last + 601)["calls_10m"] == 0
    assert role.run_json(last + 900)["idle_s"] == pytest.approx(900, abs=0.1)


def test_no_run_is_null(tmp_path, fake_or):
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        assert c.get("/health/llm").json()["run"] is None


def test_run_base_not_durable_is_config_error(tmp_path, fake_or, monkeypatch):     # Review Focus 1
    # р1б: ошибка настройки — только брошенная запись (LedgerWriteLost); «не дождались диска» — нет, см.
    # test_run_base_timeout_is_warning_not_error
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    calls = []
    def lost(timeout):
        calls.append(timeout)
        raise LedgerWriteLost("строки нет в файле")
    monkeypatch.setattr(role.ledger, "flush", lost)
    with TestClient(app) as c:
        assert len(calls) == 1                                                      # без повторов
        r = post(c, chat_body())
    assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_config")
    assert "точка отсчёта не записана" in role.config_error and fake_or.requests == []


def test_run_base_key_on_disk_per_id_and_caller(tmp_path, fake_or):             # точка — в llm_meta, на диске
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(), token=OPS_LLM_TOKEN)                                   # ops до прогона: $0.0012
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    with TestClient(app):
        pass
    disk = Ledger(role.settings.DB_PATH)                                            # новый процесс читает файл
    assert disk.open()
    key = RUN_BASE_KEY.format(id="d8-test", caller="ops")
    assert key == "run_base_usd:d8-test:ops"
    assert (disk.meta_get(key), disk.meta_get(RUN_BASE_KEY.format(id="d8-test", caller="student-01"))) == \
           (pytest.approx(0.0012), 0.0)
    assert role.budget.run_base == {"ops": pytest.approx(0.0012), "student-01": 0.0}
    disk.close()


def run_keys(*callers):
    return [RUN_BASE_KEY.format(id="d8-test", caller=c) for c in callers]


def test_run_base_timeout_is_warning_not_error(tmp_path, fake_or, monkeypatch, caplog):   # доводка р1б, п. 1
    # не дождались диска: WARNING, без config_error; вызов не уходит — flush до OpenRouter (Д4а-R19) ждёт тот же диск
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    def slow_disk(timeout):
        raise TimeoutError
    monkeypatch.setattr(role.ledger, "flush", slow_disk)
    with caplog.at_level(logging.WARNING, logger="pcbk_core.llm"), TestClient(app) as c:
        r = post(c, chat_body())
    assert role.config_error is None and "точка отсчёта ещё не на диске" in caplog.text
    assert (r.status_code, r.json()["error"]["code"]) == (503, "pcbk_ledger") and fake_or.requests == []


def test_run_base_disk_stalled_then_written(tmp_path, fake_or, monkeypatch, caplog):     # доводка р1б, п. 1
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    led, real = role.ledger, role.ledger._connect
    def stalled(*a, **k):                       # писатель (без аргументов) упирается в блокировку дольше срока
        if not a and not k:
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **k)                    # проба записи (writable) идёт: сбой — у писателя
    led.retry_pause_s = 0.05
    monkeypatch.setattr(led, "_connect", stalled)
    with caplog.at_level(logging.WARNING, logger="pcbk_core.llm"), TestClient(app) as c:
        assert role.config_error is None and "точка отсчёта ещё не на диске" in caplog.text
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (503, "pcbk_ledger")   # пока диск стоит — мимо OpenRouter
        monkeypatch.setattr(led, "_connect", real)                                  # диск ожил
        wait_until(lambda: led.queue_size == 0 and led.consecutive_failures == 0)
        assert post(c, chat_body()).status_code == 200
    assert len(fake_or.requests) == 1
    disk = Ledger(role.settings.DB_PATH)
    assert disk.open() and [disk.meta_get(k) for k in run_keys("ops", "student-01")] == [0.0, 0.0]
    disk.close()


def test_run_base_returned_not_assigned_in_thread(tmp_path, fake_or):            # доводка р1б, п. 2 (m4)
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    assert role.ledger.opened                                                       # журнал открыт, как в lifespan
    assert role.ensure_run_base() == ({"ops": 0.0, "student-01": 0.0}, None)
    assert (role.budget.run_base, role.config_error) == ({}, None)                 # присваивает только lifespan
    assert [role.ledger.meta_get(k) for k in run_keys("ops", "student-01")] == [0.0, 0.0]


def test_run_base_closed_journal_at_once(tmp_path, fake_or):                     # доводка р1б, п. 3
    db = str(tmp_path / "нет-каталога" / "core.db")                                # журнал не откроется
    llm = replace(LLM_SETTINGS, OPENROUTER_BASE_URL=fake_or.url, **RUN)
    settings = replace(SETTINGS, DB_PATH=db)
    role = LlmRole(settings, llm, tokens=LLM_TOKENS, upstream=Upstream(llm.OPENROUTER_BASE_URL, "test-openrouter-key",
                                                                        None), ledger=Ledger(db))
    started = time.monotonic()
    with TestClient(create_app(settings, [role])) as c:
        took = time.monotonic() - started
        r = post(c, chat_body())
    assert took < 1.0                                                               # закрытый журнал — сразу
    assert "журнал вызовов не открылся" in role.config_error
    assert "прогон: точка отсчёта не записана" in role.config_error
    assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_config") and fake_or.requests == []


def test_run_base_includes_final_estimates(tmp_path, fake_or):                   # доводка р1б, п. 4
    app, role = make_llm_app(tmp_path, fake_or)                                     # дозапрос цены — 3 × 0,01 с
    with TestClient(app) as c:
        post(c, chat_body("FAKEGENNEVER"))                                          # цена не найдётся — unknown
        wait_until(lambda: role.ledger.recent(1)[0].cost_source == "unknown")
    est = role.ledger.estimated("student-01")
    assert est > 0.07 and role.ledger.settled("student-01") == pytest.approx(est)   # окончательная оценка — оплачено
    app, role = make_llm_app(tmp_path, fake_or, **{**RUN, "LLM_RUN_USD": 1.0})
    with TestClient(app) as c:
        assert c.get("/health/llm").json()["run"]["spent_usd"] == 0
    assert role.budget.run_base["student-01"] == pytest.approx(est)


def test_run_settings_error_closes_proxy(tmp_path, fake_or):                     # настройки мимо from_env
    for n, bad in enumerate(({"LLM_RUN_ID": "d8"}, {**RUN, "LLM_RUN_CALLERS": "student-01,student-11"},
                             {**RUN, "LLM_RUN_USD": 2000.0})):
        app, role = make_llm_app(tmp_path / f"case-{n}", fake_or, **bad)
        with TestClient(app) as c:
            r = post(c, chat_body())
            assert c.get("/health/llm").json()["run"] is None
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_config"), bad
        assert role.config_error.startswith("настройки: LLM_RUN_"), bad
    assert fake_or.requests == []


def test_run_activity_only_run_callers(tmp_path, fake_or):                       # метки — только вызывающим прогона
    app, role = make_llm_app(tmp_path, fake_or, **{**RUN, "LLM_RUN_USD": 1.0, "LLM_RUN_CALLERS": "student-01"})
    with TestClient(app) as c:
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 200
        run = c.get("/health/llm").json()["run"]
        assert (run["calls_10m"], run["spent_usd"], role.last_run_call_mono) == (0, 0.0, None)
        assert post(c, chat_body()).status_code == 200
        run = c.get("/health/llm").json()["run"]
    assert (run["calls_10m"], run["spent_usd"]) == (1, pytest.approx(0.0012))
    assert role.started_mono <= role.last_run_call_mono


def test_run_idle_from_start_without_calls(tmp_path, fake_or):                    # тишина без единого вызова
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    assert role.run_json(role.started_mono + 301.04) == {"id": "d8-test", "cap_usd": 0.002, "spent_usd": 0.0,
                                                          "callers": 2, "calls_10m": 0, "idle_s": 301.0}
    assert RUN_WINDOW_S == 600
    role.last_run_call_mono = role.started_mono + 1000.0                            # был вызов — тишина от него
    role.run_calls.append(role.last_run_call_mono)
    later = role.run_json(role.started_mono + 1100.0)
    assert (later["idle_s"], later["calls_10m"]) == (100.0, 1)


def test_run_counts_calls_under_drill(tmp_path, fake_or):                         # учения Д8-R77: вызовы видны
    app, role = make_llm_app(tmp_path, fake_or, DRILL_LLM="402", **{**RUN, "LLM_RUN_USD": 1.0})
    with TestClient(app) as c:
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_money_out")
        post(c, chat_body("заголовок", tools=False))
        j = c.get("/health/llm").json()
    assert (j["run"]["calls_10m"], j["run"]["spent_usd"], j["upstream"]["state"]) == (2, 0.0, "money_out")
    assert fake_or.requests == []                                                   # денег нет: OpenRouter не звали


def test_run_refusal_logged_without_upstream(tmp_path, fake_or):                 # отказ — строка журнала, без денег
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    with TestClient(app) as c:
        post(c, chat_body()); post(c, chat_body())
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 402        # ops — тоже вызывающий прогона
        run = c.get("/health/llm").json()["run"]
    row = role.ledger.recent(1)[0]
    assert (row.status, row.code, row.http_status, row.caller, row.cost) == \
           ("refused", "pcbk_run_spent", 402, "ops", 0.0)
    assert run["calls_10m"] == 2                                                    # отказ — не вызов модели
    assert REFUSALS["run_spent"] == (402, "pcbk_run_spent")


T = datetime(2026, 10, 8, 9, tzinfo=timezone.utc)


def spend(led, caller, cost, *, close=True):
    cid = led.open_call(ts=T, caller=caller, session=None, model="m", step=1, estimate=0.5)
    if close:
        led.close_call(cid, status="ok", code="", http_status=200, provider="Fireworks", provider_ok=True,
                       generation_id="g", usage=Usage(cost, 1, 1, 0, 0, 0), cost_source="stream", duration_ms=1)


def test_budget_refusal_order_with_run(tmp_path):                                # бюджет → прогон → доля
    led = Ledger(str(tmp_path / "core.db"))
    assert led.open()
    spend(led, "student-01", 10.0)
    spend(led, "student-02", 3.0)
    run = RunSpec("d8", 5.0, frozenset({"student-01", "student-02"}))
    b = Budget(led, total_usd=1000.0, share_usd=100.0, ops_share_usd=100.0, run=run)
    b.run_base = {"student-01": 8.0, "student-02": 3.0}                             # прогон: $2 + $0
    assert (b.run_spent(), b.refusal("student-01")) == (pytest.approx(2.0), None)
    spend(led, "student-02", 3.0)                                                   # прогон: $2 + $3 = $5
    assert [b.refusal(c) for c in ("student-01", "student-02", "student-03")] == ["run_spent", "run_spent", None]
    small = Budget(led, total_usd=1000.0, share_usd=4.0, ops_share_usd=100.0, run=run)
    small.run_base = dict(b.run_base)
    assert (small.refusal("student-01"), small.refusal("student-03")) == ("run_spent", None)   # прогон раньше доли
    small.run_base = {"student-01": 10.0, "student-02": 6.0}                        # прогон пуст — держит доля
    assert (small.run_spent(), small.refusal("student-01")) == (0.0, "share_spent")
    tight = Budget(led, total_usd=16.0, share_usd=100.0, ops_share_usd=100.0, run=run)
    assert tight.refusal("student-01") == "budget_spent"                           # общий бюджет — первым
    spend(led, "student-01", 0.0, close=False)                                      # идущий вызов: резерв $0.5
    assert b.run_spent() == pytest.approx(5.5)
    assert Budget(led, total_usd=1000.0, share_usd=100.0, ops_share_usd=100.0).run_spent() == 0.0
    led.close()


def test_budget_run_without_base_counts_from_zero(tmp_path):                      # закрыто при сомнении
    led = Ledger(str(tmp_path / "core.db"))
    assert led.open()
    spend(led, "ops", 1.5)
    b = Budget(led, total_usd=1000.0, share_usd=100.0, ops_share_usd=100.0,
               run=RunSpec("d8", 1.0, frozenset({"ops"})))
    assert (b.run_spent(), b.refusal("ops")) == (pytest.approx(1.5), "run_spent")
    led.close()


def test_has_open_or_pending_of(tmp_path):
    led = Ledger(str(tmp_path / "core.db"))
    assert led.open()
    spend(led, "student-01", 0.0, close=False)
    assert (led.has_open_or_pending_of("student-01"), led.has_open_or_pending_of("ops")) == (True, False)
    led.close()


def test_run_spec_from_replace_settings():
    from pcbk_core.llm.ledger import run_spec
    from pcbk_core.settings import LlmSettings
    assert run_spec(replace(LlmSettings(), **RUN)) == RunSpec("d8-test", 0.002, frozenset({"student-01", "ops"}))
    assert run_spec(LlmSettings()) is None


# --- тормоз прогона (Д11-R117) в цепочке отказов прогона ---

def test_run_halted_before_run_spent_and_logged(tmp_path, fake_or):            # бюджет → тормоз → потолок → доля
    app, role = make_llm_app(tmp_path, fake_or, **RUN)
    with TestClient(app) as c:
        post(c, chat_body()); post(c, chat_body())                                 # потолок прогона исчерпан
        assert post(c, chat_body()).json()["error"]["code"] == "pcbk_run_spent"
        role.brake.halt("memory")
        r = post(c, chat_body(), token=OPS_LLM_TOKEN)                              # ops — вызывающий прогона
        run = c.get("/health/llm").json()["run"]
    assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_run_halted") and len(fake_or.requests) == 2
    row = role.ledger.recent(1)[0]
    assert (row.status, row.code, row.http_status, row.caller, row.cost) == ("refused", "pcbk_run_halted", 402, "ops", 0.0)
    assert run["calls_10m"] == 2                                                    # отказ — не вызов модели


def test_run_brake_from_role_settings(tmp_path, fake_or):                       # пороги и пути — из LlmSettings
    role = make_llm_role(tmp_path, fake_or, **RUN, LLM_RUN_MEM_FAIL_MIB=2048, LLM_RUN_SWAP_GROW_MIB=256,
                         LLM_RUN_SWAP_BASE_MIB=100, LLM_RUN_PSI_FULL_MAX=7.5, LLM_RUN_MEMINFO="/x/m",
                         LLM_RUN_VMSTAT="/x/v", LLM_RUN_PRESSURE="/x/p")
    b = role.brake
    assert (b.meminfo_path, b.vmstat_path, b.pressure_path, b.run_id) == ("/x/m", "/x/v", "/x/p", "d8-test")
    assert (b.fail_mib, b.swap_grow_mib, b.swap_base_cfg, b.psi_full_max) == (2048, 256, 100, 7.5)
    assert b.json(role.monotonic())["swap_base_mib"] == 100 and role.budget.halted() is False
