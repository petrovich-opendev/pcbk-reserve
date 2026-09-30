"""Роль llm: ручка /llm/v1/chat/completions с цепочкой отказов, учёт денег через журнал вызовов (строка вызова на
диске до OpenRouter, Д4а-R19), перекачка потока, опрос /key и /credits, выключатель и учение 402 — на двойнике."""
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from helpers import (LLM_SETTINGS, LLM_TOKEN, OPS_LLM_TOKEN, SES, TOKEN, auth, chat_body, make_llm_app,
                     make_llm_role, now_utc, serve_app, wait_until, wait_until_async)
from pcbk_core.llm import LLM_TOKENS_ERROR
from pcbk_core.llm.api import LLM_MESSAGES, LlmStream
from pcbk_core.llm.ledger import Ledger
from pcbk_core.llm.state import UPSTREAM_TEXTS
from pcbk_core.llm.turns import COMPACTION_CONTINUE
from pcbk_core.logs import setup_logging
from pcbk_core.main import egress_targets
from pcbk_core.secrets import BdrvConfig, TokenTable


def post(c, body, token=LLM_TOKEN, session=SES, parent=None):
    return c.post("/llm/v1/chat/completions", json=body, headers=auth(token, session, parent))


def test_stream_passes_through_and_is_charged(tmp_path, fake_or):                # блокер раунда 1
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        r = post(c, chat_body())
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.text.rstrip().endswith("data: [DONE]")
        sent = fake_or.requests[-1]["body"]
        assert (sent["provider"]["only"], sent["user"], sent["max_tokens"]) == (["fireworks"], "pcbk-student-01", 16000)
        assert sent["provider"]["max_price"] == {"prompt": 1.40, "completion": 4.40, "request": 0}   # У1
        assert sent["session_id"] == f"student-01:{SES}"
        row = role.ledger.recent(1)[0]
        assert (row.status, row.cost, row.cost_source, row.provider_ok, row.step) == ("ok", 0.0012, "stream", True, 1)
        assert row.cost_est > 0.07                                               # оценка GLM-5.3: 16 000 × $4,40/млн + вход
        j = role.health_json(now_utc())
        assert (j["spent_usd"], j["ledger_usd"], j["estimated_usd"], j["ops_usd"]) == \
               (pytest.approx(0.0012), pytest.approx(0.0012), 0.0, 0.0)
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 200
        j = role.health_json(now_utc())
        assert (j["spent_usd"], j["ops_usd"], j["shares"]) == \
               (pytest.approx(0.0024), pytest.approx(0.0012), {"over_80": 0, "spent": 0})


def test_401_and_data_token_is_not_llm_token(tmp_path, fake_or, capfd, restore_logging):   # П12, П19
    setup_logging()                                                             # F13 Д3б: без неё INFO не в stderr
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        assert post(c, chat_body(), token=None).status_code == 401
        r = post(c, chat_body(), token=TOKEN)                                   # токен данных Д3б
        assert (r.status_code, r.json()["error"]["code"], r.headers["www-authenticate"]) == \
               (401, "pcbk_unauthorized", "Bearer")
    err = capfd.readouterr().err
    assert err.count("outcome=unauthorized channel=llm path=/llm/v1/chat/completions") == 2
    assert TOKEN not in err and fake_or.requests == []


@pytest.mark.parametrize("model", ["z-ai/glm-5.3:nitro", "openrouter/auto", "~z-ai/glm-latest", "Z-AI/GLM-5.3"])
def test_model_refused_before_upstream(tmp_path, fake_or, model):               # Review Focus 4
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        r = post(c, chat_body(model=model))
    assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_model_refused")
    assert fake_or.requests == [] and role.ledger.recent(1)[0].status == "refused"


def test_share_and_total_spent_are_402_with_clear_text(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or, LLM_SHARE_USD=0.001)
    with TestClient(app) as c:
        assert post(c, chat_body()).status_code == 200                          # 0,0012 > 0,001
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_share_spent")
        assert "Ваша доля бюджета на модель" in r.json()["error"]["message"]
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).status_code == 200     # у ops своя доля
    app2, _ = make_llm_app(tmp_path / "2", fake_or, LLM_TOTAL_USD=0.001)
    with TestClient(app2) as c:
        post(c, chat_body())
        assert post(c, chat_body(), token=OPS_LLM_TOKEN).json()["error"]["code"] == "pcbk_budget_spent"
    assert len(fake_or.requests) == 3


def test_turn_limit_17th_call_refused(tmp_path, fake_or):                        # Review Focus 7
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        codes = [post(c, chat_body(steps=i)).status_code for i in range(16)]    # 1 user + 15 после инструмента
        title = post(c, chat_body(text="заголовок", tools=False))               # без инструментов — ход не трогает
        cont = post(c, chat_body(steps=15, tail_user=COMPACTION_CONTINUE))      # продолжение после сжатия — 17-й шаг
        sub = post(c, chat_body(text="задача подагента"), session="ses_" + "C" * 26, parent=SES)
        assert codes == [200] * 16 and title.status_code == 200
        assert (cont.status_code, cont.json()["error"]["code"]) == (403, "pcbk_turn_limit")
        assert sub.json()["error"]["code"] == "pcbk_turn_limit"                 # подагент — в ход родителя
        assert post(c, chat_body(text="новый вопрос")).status_code == 200        # новый ход
    assert len(fake_or.requests) == 18


def test_unknown_cost_counts_estimate_and_stops_share(tmp_path, fake_or):         # блокер раунда 1; Review Focus 2
    app, role = make_llm_app(tmp_path, fake_or, LLM_SHARE_USD=0.05)             # оценка вызова GLM-5.3 ≈ $0,071
    with TestClient(app) as c:
        assert post(c, chat_body(text="q FAKEGENNEVER")).status_code == 200
        wait_until(lambda: role.ledger.recent(1)[0].cost_source == "unknown")    # окно дозапроса прошло, цены нет
        r = post(c, chat_body(text="новый вопрос"))
        assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_share_spent")
        est = role.ledger.recent(2)[1].cost_est
        j = role.health_json(now_utc())
        assert (j["spent_usd"], j["estimated_usd"], j["cost_unknown_24h"]) == (pytest.approx(est), pytest.approx(est), 1)
    assert len(fake_or.requests) == 1


def test_inflight_reserve_refuses_share(tmp_path, fake_or):                       # Review Focus 6
    app, _ = make_llm_app(tmp_path, fake_or, LLM_SHARE_USD=0.05)
    with serve_app(app) as url, httpx.Client(trust_env=False) as a, httpx.Client(trust_env=False) as b:
        with a.stream("POST", url + "/llm/v1/chat/completions", json=chat_body(text="q FAKESLOW"),
                      headers=auth(LLM_TOKEN, SES)) as r1:
            chunks = r1.iter_raw()                  # держать итератор: брошенный, он закрыл бы соединение (httpcore)
            next(chunks)                                                         # первый вызов идёт, его оценка в доле
            r2 = b.post(url + "/llm/v1/chat/completions", json=chat_body(text="другой вопрос"),
                        headers=auth(LLM_TOKEN, "ses_" + "B" * 26))
    assert (r2.status_code, r2.json()["error"]["code"]) == (402, "pcbk_share_spent")
    assert len(fake_or.requests) == 1


def test_caller_concurrency_is_busy(tmp_path, fake_or):                           # Review Focus 6
    app, _ = make_llm_app(tmp_path, fake_or, LLM_CALLER_CONCURRENCY=1)
    with serve_app(app) as url, httpx.Client(trust_env=False) as a, httpx.Client(trust_env=False) as b:
        with a.stream("POST", url + "/llm/v1/chat/completions", json=chat_body(text="q FAKESLOW"),
                      headers=auth(LLM_TOKEN, SES)) as r1:
            chunks = r1.iter_raw()                  # держать итератор: брошенный, он закрыл бы соединение (httpcore)
            next(chunks)
            r2 = b.post(url + "/llm/v1/chat/completions", json=chat_body(), headers=auth(LLM_TOKEN, SES))
            r3 = b.post(url + "/llm/v1/chat/completions", json=chat_body(), headers=auth(OPS_LLM_TOKEN, SES))
    assert (r2.status_code, r2.headers.get("retry-after"), r2.json()["error"]["code"]) == (429, "2", "pcbk_busy")
    assert r3.status_code == 200                                                 # у ops свой счётчик


def test_llm_body_limit_is_one_mib(tmp_path, fake_or):                            # Review Focus 1, 6
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        r = post(c, chat_body(text="x" * 1_100_000))
        assert (r.status_code, r.json()["error"]["code"]) == (413, "pcbk_body_too_large")   # П19: форма OpenAI
        assert not any(ch.isdigit() for ch in r.json()["error"]["message"])     # без чисел (Global Constraints)
        assert post(c, chat_body(text="x" * 700_000)).status_code == 200
    assert len(fake_or.requests) == 1


@pytest.mark.parametrize("word,status,code", [("FAKE402", 402, "pcbk_money_out"), ("FAKE403KEY", 402, "pcbk_key_limit"),
    ("FAKE403REGION", 403, "pcbk_region"), ("FAKE401", 403, "pcbk_key_rejected")])
def test_money_signs_turn_row_red_until_success(tmp_path, fake_or, word, status, code):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        wait_until(lambda: role.checked_at is not None)                         # П13: первый опрос кончился раньше
        r = post(c, chat_body(text=f"q {word}"))
        assert (r.status_code, r.json()["error"]["code"]) == (status, code)
        assert role.health()[0] is False
        assert post(c, chat_body()).status_code == 200
        # внутри: после выхода из lifespan журнал закрыт, и здоровье красное по журналу, а не по OpenRouter
        assert role.health()[0] is True
    assert role.health_json(now_utc())["upstream"]["state"] == "ok"


def test_price_above_max_is_refused_and_recorded(tmp_path, fake_or, capfd, restore_logging):   # У1, ревью 5
    setup_logging()
    app, role = make_llm_app(tmp_path, fake_or)
    fake_or.set_price("z-ai/glm-5.3", 1.41, 4.40)                                # точка подорожала выше таблицы
    with TestClient(app) as c:
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (404, "pcbk_upstream_rejected")
        assert r.json()["error"]["message"] == LLM_MESSAGES["rejected"]         # постоянный текст без чисел
    assert "max_price" in capfd.readouterr().err                                # текст OpenRouter — в журнал
    row = role.ledger.recent(1)[0]
    assert (row.status, row.code, row.cost_source, row.step) == ("upstream_error", "pcbk_upstream_rejected", "none", 1)
    assert role.ledger.spent("student-01") == 0.0 and role.inflight.get("student-01", 0) == 0
    assert fake_or.requests[-1]["body"]["provider"]["max_price"] == {"prompt": 1.40, "completion": 4.40, "request": 0}


def test_in_flight_is_wait_not_money(tmp_path, fake_or):                         # Review Focus 3
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        r = post(c, chat_body(text="q FAKE402INFLIGHT"))
        assert role.health()[0] is True                                          # внутри: журнал ещё открыт
    assert (r.status_code, r.headers.get("retry-after"), r.json()["error"]["code"]) == (429, "1", "pcbk_upstream_wait")


def test_poll_clears_only_its_own_sign(tmp_path, fake_or):                        # Д4а-R14
    app, role = make_llm_app(tmp_path, fake_or)                                  # ключа управления нет; /key: limit_remaining null
    with TestClient(app) as c:
        post(c, chat_body(text="q FAKE402"))
        c.portal.call(role.poll)
        assert role.health_json(now_utc())["upstream"]["state"] == "money_out"   # /key «кончились деньги» не снимает
    app2, role2 = make_llm_app(tmp_path / "2", fake_or, mgmt="test-mgmt-key")
    with TestClient(app2) as c:
        post(c, chat_body(text="q FAKE402"))
        c.portal.call(role2.poll)                                                # /credits: остаток $30
        assert role2.health_json(now_utc())["upstream"]["state"] == "ok"
    app3, role3 = make_llm_app(tmp_path / "3", fake_or, mgmt="test-mgmt-key", DRILL_LLM="402")
    with TestClient(app3) as c:
        post(c, chat_body())
        c.portal.call(role3.poll)
        assert role3.health_json(now_utc())["upstream"]["text"] == "на счёте OpenRouter кончились деньги (учения)"


def test_mgmt_key_rejected_is_its_own_sign(tmp_path, fake_or):                    # Д4а-R16
    role = make_llm_role(tmp_path, fake_or, mgmt="test-openrouter-key")          # положен обычный ключ → /credits 403
    anyio.run(role.poll)
    j = role.health_json(now_utc())
    assert (j["mgmt_key"], j["account_remaining_usd"], j["upstream"]["state"]) == ("rejected", None, "ok")


def test_mid_stream_error_passes_and_errors_in_row(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        for _ in range(3):
            r = post(c, chat_body(text="q FAKEMIDERR"))
            assert r.status_code == 200 and '"error"' in r.text
    assert role.ledger.recent(1)[0].status == "upstream_error"
    assert role.health_json(now_utc())["upstream"]["state"] == "errors"


def test_mid_stream_402_is_money_out(tmp_path, fake_or):                         # Review Focus 3
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text="q FAKEMID402"))
    assert role.health_json(now_utc())["upstream"]["state"] == "money_out"


def test_no_usage_falls_back_to_generation(tmp_path, fake_or):                   # Review Focus 2
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text="q FAKENOUSAGE"))
        wait_until(lambda: role.ledger.recent(1)[0].cost_source != "pending")
    row = role.ledger.recent(1)[0]
    assert (row.cost, row.cost_source) == (0.002, "generation")


def test_client_disconnect_closes_upstream_and_costs_later(tmp_path, fake_or):  # Review Focus 1, 2
    app, role = make_llm_app(tmp_path, fake_or)
    with serve_app(app) as url, httpx.Client(trust_env=False) as cl:
        with cl.stream("POST", url + "/llm/v1/chat/completions", json=chat_body(text="q FAKESLOW"),
                       headers=auth(LLM_TOKEN, SES)) as r:
            next(r.iter_raw())                                                   # первый чанк — и обрыв
        wait_until(lambda: fake_or.aborted == 1, timeout=3)
        wait_until(lambda: role.ledger.recent(1)[0].cost_source == "generation")
    assert role.ledger.recent(1)[0].status == "cancelled"
    assert role.inflight.get("student-01", 0) == 0                              # учёт в finally не потерян при обрыве


@pytest.mark.parametrize("word", ["FAKE500", "FAKEBREAK", "FAKEMIDERR", "FAKE402"])
def test_inflight_back_to_zero(tmp_path, fake_or, word):                         # раунд 2: утечка счётчика = busy навсегда
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text=f"q {word}"))
        wait_until(lambda: role.inflight.get("student-01", 0) == 0, timeout=3)


def test_upstream_error_before_answer_refunds_step(tmp_path, fake_or):          # раунд 2: сбой провайдера не ест шаги
    app, role = make_llm_app(tmp_path, fake_or)
    body = chat_body(text="q FAKEFLAKY")
    with TestClient(app) as c:
        codes = [post(c, body).status_code for _ in range(3)]                    # как повторы OpenCode: тело то же
    assert codes == [502, 502, 200] and role.ledger.recent(1)[0].step == 1


def test_stopped_switch_and_missing_key(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or, LLM_STATE="stopped")
    with TestClient(app) as c:
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_stopped")
        assert c.get("/healthz/llm").json() == {"ok": False, "detail": "LLM-прокси остановлен администратором"}
        assert c.get("/healthz/live").status_code == 200
    role2 = make_llm_role(tmp_path / "2", fake_or, key=None)
    assert role2.health() == (False, "нет ключа OpenRouter") and role2.health_json(now_utc())["key"] == "missing"
    assert fake_or.requests == []


def test_drill_402_text_and_no_upstream(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or, DRILL_LLM="402")
    with TestClient(app) as c:
        r = post(c, chat_body())
    assert r.status_code == 402 and r.json()["error"]["message"].endswith("(учения)")
    assert role.health_json(now_utc())["upstream"]["text"] == "на счёте OpenRouter кончились деньги (учения)"
    assert fake_or.requests == []


def test_poll_sets_checked_at_usage_account_and_unreachable(tmp_path, fake_or):  # Review Focus 2
    t = datetime(2026, 10, 3, 18, tzinfo=timezone.utc)
    role = make_llm_role(tmp_path, fake_or, mgmt="test-mgmt-key", wallclock=lambda: t)
    assert role.health_json(t)["checked_at"] is None
    anyio.run(role.poll)
    j = role.health_json(t + timedelta(minutes=5))                               # checked_at — время опроса, не now
    assert (j["checked_at"], j["upstream"]["state"], j["account_remaining_usd"], j["key_usage_usd"]) == \
           (t.isoformat(), "ok", 30.0, 0.0)
    down = make_llm_role(tmp_path / "2", None, OPENROUTER_BASE_URL="http://127.0.0.1:9/api/v1", wallclock=lambda: t)
    anyio.run(down.poll)
    j = down.health_json(t)
    assert (j["upstream"]["state"], j["checked_at"]) == ("unreachable", t.isoformat())   # неудачная попытка — тоже время


def test_key_base_follows_key_fingerprint(tmp_path, fake_or):                   # раунд 2: смена ключа и off → on
    a = make_llm_role(tmp_path, fake_or)
    anyio.run(a.poll)                                                            # точка отсчёта ключа A
    a.ledger.flush()                                                             # llm_meta — в файле
    b = make_llm_role(tmp_path, fake_or, key="test-openrouter-key-b")           # та же core.db, ключ с прошлыми $50
    async def polls():
        await b.poll(); fake_or.add_usage(2.0, key="b"); await b.poll()
    anyio.run(polls)
    j = b.health_json(now_utc())
    assert (j["key_spent_usd"], j["spent_usd"]) == (2.0, 2.0)                   # прошлые $50 не в бюджете, новые $2 видны
    off = make_llm_role(tmp_path / "2", fake_or, LLM_KEY_DEDICATED="off")
    anyio.run(off.poll)
    assert off.ledger.meta_get(f"key_base_usd:{off.key_fingerprint}") is None   # при off точка не ставится
    on = make_llm_role(tmp_path / "2", fake_or)
    anyio.run(on.poll)
    assert on.health_json(now_utc())["key_spent_usd"] == 0.0                    # off → on: своя точка отсчёта


def test_key_base_waits_for_open_and_pending(tmp_path, fake_or):
    role = make_llm_role(tmp_path, fake_or)
    role.ledger.open_call(ts=now_utc(), caller="ops", session=None, model="m", step=1, estimate=0.5)
    anyio.run(role.poll)
    assert role.health_json(now_utc())["key_spent_usd"] is None                 # идёт вызов — точку не ставим


def test_key_drift_and_budget_from_key(tmp_path, fake_or):                        # Review Focus 6; Д4а-R13
    role = make_llm_role(tmp_path, fake_or, LLM_TOTAL_USD=5.0)
    async def two_polls():                                                       # один цикл событий на клиента httpx
        await role.poll()                                                        # точка отсчёта: usage 0 − учтено 0
        fake_or.add_usage(6.0)                                                   # OpenRouter насчитал $6 мимо журнала
        await role.poll()
    anyio.run(two_polls)
    j = role.health_json(now_utc())
    assert (j["key_spent_usd"], j["ledger_usd"], j["spent_usd"], j["reserved_usd"]) == (6.0, 0.0, 6.0, 0.0)
    assert role.budget.refusal("student-01") == "budget_spent" and role.health() == (False, "общий бюджет исчерпан")
    shared = make_llm_role(tmp_path / "2", fake_or, LLM_KEY_DEDICATED="off")    # ключ общий с Dify — сверки нет
    anyio.run(shared.poll)
    assert (shared.health_json(now_utc())["key_spent_usd"], shared.budget.refusal("student-01")) == (None, None)


def test_pending_resolved_after_restart(tmp_path, fake_or):                       # Review Focus 2
    led = Ledger(str(tmp_path / "core.db")); assert led.open()
    cid = led.open_call(ts=now_utc(), caller="ops", session=None, model="z-ai/glm-5.3", step=1, estimate=0.5)
    led.close_call(cid, status="cancelled", code="", http_status=200, provider=None, provider_ok=None,
                   generation_id="gen-old", usage=None, cost_source="pending", duration_ms=10)
    led.close()                                                                  # очередь писателя — в файле
    app, role = make_llm_app(tmp_path, fake_or)                                  # та же core.db — «перезапуск core»
    with TestClient(app) as c:
        wait_until(lambda: role.ledger.recent(1)[0].cost_source == "generation")
    assert role.ledger.spent("ops") == pytest.approx(0.002)


def test_health_does_no_io(tmp_path, fake_or, monkeypatch):                      # раунд 2: договор Role Д3а
    role = make_llm_role(tmp_path, fake_or)
    def no_io(*a, **k):
        raise AssertionError("ввод-вывод в health()")
    monkeypatch.setattr(role.ledger, "writable", no_io)
    monkeypatch.setattr(role.ledger, "_connect", no_io)
    assert role.health()[0] is True and role.health_json(now_utc())["ledger_ok"] is True
    role.ledger.writable_ok = False                                              # фоновая проверка нашла сбой
    assert role.health()[1].startswith("журнал вызовов не пишется")
    assert role.health_json(now_utc())["ledger_error"] == "write"


def test_ledger_failure_turns_health_red_and_refuses(tmp_path, fake_or, monkeypatch):   # Д4а-R19
    app, role = make_llm_app(tmp_path, fake_or)
    boom = lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database or disk is full"))
    with TestClient(app) as c:
        monkeypatch.setattr(role.ledger, "_connect", boom)
        r = post(c, chat_body())                                                 # строку вызова не записать
        assert (r.status_code, r.json()["error"]["code"], r.headers["retry-after"]) == (503, "pcbk_ledger", "5")
        assert not any(ch.isdigit() for ch in r.json()["error"]["message"])
        wait_until(lambda: role.ledger.consecutive_failures > 0)                 # не flush(): писатель повторяет
        ok, detail = role.health()
        assert ok is False and detail.startswith("журнал вызовов не пишется") and "disk is full" in detail
        j = role.health_json(now_utc())
        assert (j["ledger_ok"], j["ledger_error"]) == (False, "write")
        assert role.inflight.get("student-01", 0) == 0 and role.ledger.spent("student-01") == 0.0
        monkeypatch.undo()                                                       # иначе close() ждёт до 2 × срок
    assert fake_or.requests == []                                                # деньги важнее доступности


def test_dropped_ledger_op_keeps_health_red_and_refuses(tmp_path, fake_or, monkeypatch):   # доводка ревью задачи 4
    app, role = make_llm_app(tmp_path, fake_or)
    real, poisoned = role.ledger._connect, []

    def poison_once(*a, **k):                    # писатель зовёт без аргументов; исключение не из SQLite — сброс
        if not a and not k and not poisoned:
            poisoned.append(1)
            raise RuntimeError("отравленная операция")
        return real(*a, **k)

    with TestClient(app) as c:
        monkeypatch.setattr(role.ledger, "_connect", poison_once)
        post(c, chat_body(model="openrouter/auto"))                              # отказ — его строка брошена
        post(c, chat_body(model="openrouter/auto"))                              # следующая запись проходит
        wait_until(lambda: role.ledger.writes_dropped_total == 1 and role.ledger.queue_size == 0)
        assert role.ledger.consecutive_failures == 0                             # счётчик подряд уже зелёный
        assert role.health() == (False, "строка журнала вызовов потеряна — перезапустите core")
        j = role.health_json(now_utc())
        assert (j["ledger_ok"], j["ledger_error"]) == (False, "dropped")
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (503, "pcbk_ledger")   # flush → LedgerWriteLost
    assert fake_or.requests == [] and role.inflight.get("student-01", 0) == 0


def test_closed_ledger_refuses_without_upstream(tmp_path, fake_or):             # после close() flush() не ждёт
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        role.ledger.close()                                                      # журнал закрыт, писателя нет
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (503, "pcbk_ledger")
    assert fake_or.requests == []


def test_provider_mismatch_counted(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text="q FAKEPROVIDER"))
    assert role.health_json(now_utc())["provider_mismatch_24h"] == 1


def test_health_json_keys_and_no_secrets(tmp_path, fake_or):
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body())
        r = c.get("/health/llm")
    assert set(r.json()) == {"checked_at", "config_error", "tokens_error", "stopped", "key", "mgmt_key", "drill",
                             "ledger_ok", "ledger_error", "upstream",
                             "spent_usd", "reserved_usd", "ledger_usd", "estimated_usd", "key_spent_usd", "key_usage_usd",
                             "total_usd", "ops_usd", "shares", "pending", "pending_stuck", "cost_unknown_24h",
                             "provider_mismatch_24h", "account_remaining_usd", "models",
                             "ledger_queue", "ledger_fresh", "ledger_reset", "egress", "refresh_started_age_s"}
    assert r.json()["egress"] == {"alive": None, "last_ok_age_s": None}        # в pytest потока обновления нет
    assert (r.json()["refresh_started_age_s"], r.json()["ledger_error"]) == (None, None)
    assert set(r.json()["upstream"]) == {"state", "text", "since"}
    assert not any(s in r.text for s in (LLM_TOKEN, "test-openrouter-key", "student-01"))


def test_log_line_without_content(tmp_path, fake_or, capfd, restore_logging):   # П12
    setup_logging()
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        post(c, chat_body(text="секретный вопрос"))
    err = capfd.readouterr().err
    assert "llm caller=student-01 model=z-ai/glm-5.3" in err and "cost=0.0012" in err
    assert "секретный" not in err and LLM_TOKEN not in err and "test-openrouter-key" not in err


def test_egress_targets_include_openrouter():                                   # П26: здесь его ждёт Д5; П3: cfg None
    cfg = BdrvConfig("h", 1433, "Runtime", "u", "p")
    assert egress_targets(LLM_SETTINGS, cfg) == [("h", 1433), ("openrouter.ai", 443)]
    local = replace(LLM_SETTINGS, OPENROUTER_BASE_URL="http://fake-openrouter:8080/api/v1")
    assert egress_targets(local, cfg)[1] == ("fake-openrouter", 8080)
    assert egress_targets(LLM_SETTINGS, None) == [("openrouter.ai", 443)]         # bdrv.env не читается
    inst = BdrvConfig("h\\INST", None, "Runtime", "u", "p")
    assert egress_targets(LLM_SETTINGS, inst)[0] == ("h", None)                   # экземпляр — любой порт, как Д3б


def test_abort_before_first_byte_finishes_call(tmp_path, fake_or):               # П7: генератор не начат
    app, role = make_llm_app(tmp_path, fake_or)
    with serve_app(app) as url:
        with httpx.Client(trust_env=False, timeout=httpx.Timeout(5, read=0.3)) as cl:
            with pytest.raises(httpx.ReadTimeout):                               # двойник молчит 1 с — клиент уходит
                cl.post(url + "/llm/v1/chat/completions", json=chat_body(text="q FAKESLOWSTART"),
                        headers=auth(LLM_TOKEN, SES))
        wait_until(lambda: role.inflight.get("student-01", 0) == 0, timeout=5)
        wait_until(lambda: fake_or.aborted == 1, timeout=5)                      # поток к OpenRouter закрыт
    row = role.ledger.recent(1)[0]
    assert row.status == "cancelled" and row.cost_source in ("pending", "generation")   # не «none», не ноль


@pytest.mark.anyio
@pytest.mark.parametrize("when", ["unstarted", "at_yield"])
async def test_llm_stream_finishes_when_unstarted_or_at_yield(tmp_path, fake_or, when):   # П7
    role = make_llm_role(tmp_path, fake_or)
    call = role.begin_call(caller="student-01", session=SES, parent=None, model="z-ai/glm-5.3", step=1, estimate=0.07)
    up = await role.upstream.open_chat({"model": "z-ai/glm-5.3", "stream": True,
                                        "messages": [{"role": "user", "content": "q FAKESLOW"}]})
    first_body, sent = anyio.Event(), []

    async def receive():                                                         # клиент ушёл: сразу или после 1-го куска
        if when == "at_yield":
            await first_body.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message["type"])
        if message["type"] == "http.response.body" and message.get("body"):
            first_body.set()
            await anyio.sleep_forever()                                          # клиент не читает: генератор на yield

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "POST", "path": "/llm/v1/chat/completions", "headers": []}
    await LlmStream(call, up)(scope, receive, send)
    assert role.inflight.get("student-01", 0) == 0
    assert ("http.response.body" in sent) is (when == "at_yield")               # генератор стоял на yield / не начат
    await call.finish()                                                          # идемпотентна: второй раз — ничего
    assert role.inflight.get("student-01", 0) == 0
    rows = await anyio.to_thread.run_sync(role.ledger.recent, 5)                  # recent ждёт писателя — не в цикле
    assert len(rows) == 1 and rows[0].status == "cancelled"
    await wait_until_async(lambda: fake_or.aborted == 1, timeout=3)
    await role.upstream.aclose()


def test_tokens_error_keeps_loops_and_401(tmp_path, fake_or):                    # П23
    app, role = make_llm_app(tmp_path, fake_or)
    role.tokens, role.tokens_error = TokenTable.empty(), LLM_TOKENS_ERROR
    with TestClient(app) as c:
        wait_until(lambda: role.checked_at is not None)                         # опрос /key идёт
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (401, "pcbk_unauthorized")
        assert c.get("/health/llm").json()["tokens_error"] == "токены LLM не читаются"
    assert role.health() == (False, "токены LLM не читаются") and role.config_error is None
    assert fake_or.requests == []


def test_chat_deadline_closes_stream(tmp_path, fake_or):                         # общий срок вызова
    app, role = make_llm_app(tmp_path, fake_or)
    role.chat_deadline_s = 1.0                                                   # FAKESLOW идёт 10 с
    t = time.monotonic()
    with TestClient(app) as c:
        r = post(c, chat_body(text="q FAKESLOW"))
        assert r.status_code == 200 and "[DONE]" not in r.text and time.monotonic() - t < 5
        assert r.text.startswith(": OPENROUTER PROCESSING")                     # поток кончился чисто, без [DONE]
        wait_until(lambda: fake_or.aborted == 1, timeout=3)                      # поток к OpenRouter закрыт
    row = role.ledger.recent(1)[0]
    assert (row.status, row.code) == ("upstream_error", "pcbk_deadline") and row.cost_source != "none"
    assert role.inflight.get("student-01", 0) == 0


def test_fresh_db_with_spent_key_warns(tmp_path, fake_or):                       # ревью задачи 4: пропала core.db
    fake_or.add_usage(1.0)                                                       # ключ уже тратил
    role = make_llm_role(tmp_path, fake_or)                                      # журнал создан заново
    anyio.run(role.poll)
    j = role.health_json(now_utc())
    assert (j["ledger_fresh"], j["ledger_reset"], j["ledger_queue"]) == (True, True, 0)
    role.ledger.close()
    again = make_llm_role(tmp_path, fake_or)                                     # та же core.db — не новая
    anyio.run(again.poll)
    assert (again.health_json(now_utc())["ledger_fresh"], again.health_json(now_utc())["ledger_reset"]) == (False, False)


def test_llm_page_texts_fit_80_chars(tmp_path, fake_or):                         # П20, правило Д3а
    long = "х" * 200
    texts = [t + tail for t in UPSTREAM_TEXTS.values() for tail in ("", " (учения)")]
    role = make_llm_role(tmp_path, fake_or)
    role.config_error = long; texts.append(role.health()[1]); role.config_error = None
    role.tokens_error = LLM_TOKENS_ERROR; texts.append(role.health()[1]); role.tokens_error = None
    role.ledger.writable_ok, role.ledger.last_error = False, long; texts.append(role.health()[1])
    role.ledger.writes_dropped_total = 1; texts.append(role.health()[1])
    texts.append(make_llm_role(tmp_path / "s", fake_or, LLM_STATE="stopped").health()[1])
    texts.append(make_llm_role(tmp_path / "k", fake_or, key=None).health()[1])
    texts.append(make_llm_role(tmp_path / "b", fake_or, LLM_TOTAL_USD=0.0).health()[1])   # «общий бюджет исчерпан»
    assert all(len(t) <= 80 for t in texts), [t for t in texts if len(t) > 80]


def test_refusal_codes_never_look_retryable():                                   # Global Constraints: коды и тексты
    from pcbk_core.llm.api import REFUSALS
    for key, (status, code) in REFUSALS.items():
        assert key in LLM_MESSAGES and code.startswith("pcbk_")
        assert not any(w in code for w in ("exhausted", "unavailable", "overloaded", "rate"))
        text = LLM_MESSAGES[key]
        assert key == "turn_limit" or not any(ch.isdigit() for ch in text), key
    assert {s for s, _ in REFUSALS.values() if s in (429, 502, 503)} == {429, 502, 503}


def test_failed_call_without_tools_does_not_refund_turn(tmp_path, fake_or):     # step = 0: возвращать нечего
    app, _ = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        assert [post(c, chat_body(steps=i)).status_code for i in range(16)] == [200] * 16
        assert post(c, chat_body(text="заголовок FAKE500", tools=False)).status_code == 502
        cont = post(c, chat_body(steps=15, tail_user=COMPACTION_CONTINUE))
        assert (cont.status_code, cont.json()["error"]["code"]) == (403, "pcbk_turn_limit")   # шаг хода не вернулся


def test_checks_to_reserve_have_no_await():                                      # атомарность шагов 7–10
    import inspect
    from pcbk_core.llm import api
    source = inspect.getsource(api.chat)
    section = source[source.index("role.inflight.get(caller"):source.index("role.begin_call(")]
    assert "role.budget.refusal(caller)" in section and "role.turns.step(" in section
    assert "await" not in section                  # между budget.refusal() и open_call() — ни одного await
