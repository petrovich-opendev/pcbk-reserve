"""Роль llm: ручка /llm/v1/chat/completions с цепочкой отказов, учёт денег через журнал вызовов (строка вызова на
диске до OpenRouter, Д4а-R19), перекачка потока, опрос /key и /credits, выключатель и учение 402 — на двойнике."""
import asyncio
import logging
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from helpers import (LLM_SETTINGS, LLM_TOKEN, LLM_TOKENS, OPS_LLM_TOKEN, SES, SETTINGS, TOKEN, auth, chat_body,
                     make_llm_app, make_llm_role, now_utc, serve_app, wait_until, wait_until_async)
from pcbk_core.app import create_app
from pcbk_core.llm import LLM_TOKENS_ERROR, LlmRole
from pcbk_core.llm.api import LLM_MESSAGES, LlmCall, LlmStream, forward
from pcbk_core.llm.ledger import Ledger
from pcbk_core.llm.request import body_bytes, rebuild_body, turn_mark
from pcbk_core.llm.shares import Shares
from pcbk_core.llm.state import UPSTREAM_TEXTS, UpstreamState
from pcbk_core.llm.turns import COMPACTION_CONTINUE
from pcbk_core.llm.upstream import Upstream, UpstreamError, classify
from pcbk_core.logs import setup_logging
from pcbk_core.main import egress_targets
from pcbk_core.secrets import BdrvConfig, TokenTable, key_fingerprint
from pcbk_core.settings import GatewaySettings


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
        monkeypatch.setattr(role.ledger, "flush", no_flush)
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (503, "pcbk_ledger")   # сразу, без потока и резерва
        monkeypatch.undo()                                                       # close() на выходе зовёт flush
    assert fake_or.requests == [] and role.inflight.get("student-01", 0) == 0
    assert role.ledger.recent(1)[0].step == 0                                    # отказ до резерва: шаг не взят


def no_flush(*a, **k):
    raise AssertionError("flush при известном сбое журнала")


def test_ledger_write_error_refuses_at_once_without_thread(tmp_path, fake_or, monkeypatch):   # ревью A, M2
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app) as c:
        wait_until(lambda: role.ledger.writable_ok is True)                      # первая фоновая проверка прошла
        role.ledger.writable_ok = False                                          # следующая нашла сбой
        monkeypatch.setattr(role.ledger, "flush", no_flush)
        r = post(c, chat_body())
        assert (r.status_code, r.headers["retry-after"], r.json()["error"]["code"]) == (503, "5", "pcbk_ledger")
        monkeypatch.undo()
    assert fake_or.requests == [] and role.inflight == {}


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
                             "ledger_queue", "ledger_fresh", "ledger_reset", "egress", "refresh_started_age_s",
                             "run", "run_brake"}                                 # Д8-R69, Д11-R117: 32 ключа
    assert len(r.json()) == 32 and r.json()["run"] is None                      # без LLM_RUN_* прогона нет
    assert r.json()["run_brake"] is None                                        # и тормоза прогона нет
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
    cfg, gw = BdrvConfig("h", 1433, "Runtime", "u", "p"), GatewaySettings()      # Д5: за ними — sp-ctl и места (Ш3)
    assert egress_targets(LLM_SETTINGS, gw, cfg)[:2] == [("h", 1433), ("openrouter.ai", 443)]
    local = replace(LLM_SETTINGS, OPENROUTER_BASE_URL="http://fake-openrouter:8080/api/v1")
    assert egress_targets(local, gw, cfg)[1] == ("fake-openrouter", 8080)
    assert egress_targets(LLM_SETTINGS, gw, None)[:1] == [("openrouter.ai", 443)]   # bdrv.env не читается
    inst = BdrvConfig("h\\INST", None, "Runtime", "u", "p")
    assert egress_targets(LLM_SETTINGS, gw, inst)[0] == ("h", None)               # экземпляр — любой порт, как Д3б


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


# --- доводка по ревью задачи 6 ---

@pytest.mark.anyio
async def test_usage_without_done_is_pending_not_stream(tmp_path, fake_or):      # ревью A, I1: цена — только с [DONE]
    role = make_llm_role(tmp_path, fake_or)
    role.cost_retry_delays = (60.0,)                                             # дозапрос не успеет — строка pending
    call = role.begin_call(caller="student-01", session=SES, parent=None, model="z-ai/glm-5.3", step=1, estimate=0.07)
    call.attach(httpx.Response(200, headers={"x-generation-id": "gen-x"}))
    call.observer.feed(b'data: {"id": "gen-x", "provider": "Fireworks", "usage": {"cost": 0.0012}}\n\n')
    call.completed = True                                                        # поток кончился, [DONE] не пришёл
    await call.finish()
    row = (await anyio.to_thread.run_sync(role.ledger.recent, 1))[0]
    assert (row.status, row.code, row.cost_source, row.generation_id) == \
           ("upstream_error", "pcbk_upstream_down", "pending", "gen-x")
    assert role.ledger.spent("student-01") == pytest.approx(0.07)                # по оценке, а не 0,0012
    for task in list(role._cost_tasks):
        task.cancel()


def test_deadline_in_open_chat_after_send_is_unknown_and_keeps_step(tmp_path, fake_or):   # ревью A, I1
    app, role = make_llm_app(tmp_path, fake_or)
    role.chat_deadline_s = 0.4                                                   # FAKESLOWSTART молчит 1 с до заголовков
    body = chat_body(text="q FAKESLOWSTART")
    with TestClient(app) as c:
        r = post(c, body)
        assert (r.status_code, r.json()["error"]["code"]) == (502, "pcbk_upstream_down")
        role.chat_deadline_s = 600.0
        assert post(c, {**body, "stream": False}).status_code == 200            # тот же ход: метка та же
        rows = role.ledger.recent(2)
    assert (rows[1].status, rows[1].code, rows[1].cost_source) == ("upstream_error", "pcbk_deadline", "unknown")
    assert rows[0].step == 2                                                     # тело ушло — шаг не возвращён
    assert role.ledger.spent("student-01") == pytest.approx(rows[1].cost_est + 0.0012)   # не $0 за оборванный


@pytest.mark.anyio
async def test_cancel_in_open_chat_after_send_is_unknown(tmp_path, fake_or):     # ревью A, I1: снят обработчик
    role = make_llm_role(tmp_path, fake_or)
    policy = role.models["z-ai/glm-5.3"]
    raw = chat_body(text="q FAKESLOWSTART")
    body = rebuild_body(raw, policy, caller="student-01", session=SES)
    assert role.turns.step("student-01", SES, turn_mark(body)) == 1
    call = role.begin_call(caller="student-01", session=SES, parent=None, model=policy.model, step=1,
                           estimate=policy.estimate_usd(body_bytes(body), body["max_tokens"]))
    task = asyncio.create_task(forward(role, call, body, raw))
    await wait_until_async(lambda: call.sent_upstream)
    await anyio.sleep(0.3)                                                       # запрос у двойника, заголовков нет
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = (await anyio.to_thread.run_sync(role.ledger.recent, 1))[0]
    assert (row.status, row.cost_source) == ("cancelled", "unknown") and role.inflight == {}
    assert role.turns.step("student-01", SES, turn_mark(body)) == 2              # шаг не возвращён


def test_read_timeout_after_send_is_unknown_not_free(tmp_path, fake_or):         # ревью A, I1: UpstreamError.sent
    app, role = make_llm_app(tmp_path, fake_or)
    client = httpx.AsyncClient(timeout=httpx.Timeout(5, read=0.3), trust_env=False)
    role.upstream = Upstream(fake_or.url, "test-openrouter-key", None, client=client)
    body = chat_body(text="q FAKESLOWSTART")
    with TestClient(app) as c:
        r = post(c, body)                                                        # ReadTimeout после отправки тела
        assert (r.status_code, r.json()["error"]["code"]) == (502, "pcbk_upstream_down")
        assert post(c, {**body, "stream": False}).status_code == 200
        rows = role.ledger.recent(2)
        c.portal.call(client.aclose)
    assert (rows[1].status, rows[1].cost_source, rows[0].step) == ("upstream_error", "unknown", 2)
    assert role.ledger.spent("student-01") == pytest.approx(rows[1].cost_est + 0.0012)


class NonNegative(dict):
    """inflight, который не пускает ноль и минус: ноль — удаление ключа, минус — двойное освобождение."""

    def __setitem__(self, key, value):
        assert value >= 1, (key, value)
        super().__setitem__(key, value)


def test_finish_settles_once_per_call_on_every_path(tmp_path, fake_or, monkeypatch, caplog):   # ревью A, I1
    settled, begun = [], []
    real_settle = LlmCall._settle

    def counting(self, *a, **k):
        settled.append(self.call_id)
        return real_settle(self, *a, **k)

    monkeypatch.setattr(LlmCall, "_settle", counting)
    app, role = make_llm_app(tmp_path, fake_or)
    real_begin = role.begin_call

    def begin(**kw):
        call = real_begin(**kw)
        begun.append(call.call_id)
        return call

    role.begin_call, role.inflight = begin, NonNegative()
    boom = lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error"))   # noqa: E731
    with caplog.at_level(logging.ERROR, logger="pcbk_core.llm"), TestClient(app) as c:
        for word in ("", "FAKE500", "FAKE402", "FAKE402INFLIGHT", "FAKEBREAK", "FAKEMIDERR", "FAKENOUSAGE",
                     "FAKEPROVIDER"):
            post(c, chat_body(text=f"q {word} {len(begun)}"))
        post(c, {**chat_body(text="без потока"), "stream": False})
        role.chat_deadline_s = 0.5
        post(c, chat_body(text="q FAKESLOW"))                                    # срок в перекачке
        post(c, chat_body(text="q FAKESLOWSTART"))                               # срок в open_chat
        role.chat_deadline_s = 600.0
        wait_until(lambda: not role._cost_tasks and role.ledger.queue_size == 0)  # дозапросы цен дописаны
        monkeypatch.setattr(role.ledger, "_connect", boom)
        assert post(c, chat_body(text="журнал")).status_code == 503               # R19: flush не успел
        monkeypatch.setattr(role.ledger, "_connect", Ledger._connect.__get__(role.ledger))
        wait_until(lambda: role.inflight == {} and role.ledger.queue_size == 0)
    assert len(begun) == 12 and sorted(settled) == sorted(begun) and len(set(settled)) == len(settled)
    assert "место одновременности уже свободно" not in caplog.text


@pytest.mark.parametrize("status,message,want", [
    (413, "Request Entity Too Large", (413, "pcbk_body_too_large")),
    (400, "This endpoint's maximum context length is 131072 tokens. However, you requested about 200000 tokens",
     (413, "pcbk_body_too_large")),                                              # переполнение — OpenCode сожмёт
    (400, "Invalid request", (400, "pcbk_upstream_rejected")),
    (404, "No endpoints found matching your max_price", (404, "pcbk_upstream_rejected")),
    (422, "Unprocessable", (422, "pcbk_upstream_rejected")),
    (409, "Conflict", (400, "pcbk_upstream_rejected")),
    (403, "Forbidden by moderation", (400, "pcbk_upstream_rejected")),
    (400, "Rate limit: too many tokens per minute", (400, "pcbk_upstream_rejected")),   # не переполнение
])
def test_upstream_4xx_mapping(tmp_path, fake_or, monkeypatch, status, message, want):   # ревью A, M1
    app, role = make_llm_app(tmp_path, fake_or)

    async def refuse(body):
        raise UpstreamError(classify(status, {"message": message}), status, message)

    monkeypatch.setattr(role.upstream, "open_chat", refuse)
    with TestClient(app) as c:
        r = post(c, chat_body())
    text = LLM_MESSAGES["too_large" if want[1] == "pcbk_body_too_large" else "rejected"]
    assert (r.status_code, r.json()["error"]["code"], r.json()["error"]["message"]) == (*want, text)
    row = role.ledger.recent(1)[0]
    assert (row.status, row.code, row.cost_source) == ("upstream_error", want[1], "none")


def test_refusal_texts_carry_no_client_input_and_no_digits(tmp_path, fake_or, capfd, restore_logging):   # ревью A, I2
    setup_logging()
    app, _ = make_llm_app(tmp_path, fake_or)
    marker = "zq-marker-429"
    base = chat_body()
    bodies = [chat_body(model=marker), [1], {"model": "z-ai/glm-5.3"}, {**base, "messages": [marker]},
              {**base, "messages": [{"role": marker, "content": "x"}]},
              {**base, "messages": [{"role": "user", "content": 429}]},
              {**base, "messages": [{"role": "assistant", "tool_calls": [{"id": marker}]}]},
              {**base, "messages": [{"role": "tool", "content": marker}]},
              {**base, "messages": [{"role": "user", "content": "x"}] * 4001}]
    with TestClient(app) as c:
        replies = [post(c, b) for b in bodies]
        nan = c.post("/llm/v1/chat/completions", headers={**auth(LLM_TOKEN, SES), "Content-Type": "application/json"},
                     content=b'{"model": "z-ai/glm-5.3", "messages": [{"role": "user", "content": "x"}], '
                             b'"tools": [{"type": "function", "function": {"name": "t", "parameters": {"x": NaN}}}]}')
        replies.append(nan)
    assert [r.status_code for r in replies] == [403] + [400] * 9
    texts = [r.json()["error"]["message"] for r in replies]
    assert texts[0] == "Эта модель не разрешена на учебном стенде."
    assert not [t for t in texts if marker in t or any(ch.isdigit() for ch in t)], texts
    assert f"model={marker}" in capfd.readouterr().err                          # имя — только в журнале процесса


@pytest.mark.anyio
@pytest.mark.parametrize("share,passing,refused", [(100.0, 3, (429, "pcbk_busy")), (0.1, 2, (402, "pcbk_share_spent"))])
async def test_concurrent_requests_hold_concurrency_and_share(tmp_path, fake_or, monkeypatch, share, passing,
                                                              refused):      # ревью A, M3: атомарность поведением
    app, role = make_llm_app(tmp_path, fake_or, LLM_SHARE_USD=share)             # оценка вызова ≈ $0,071
    gate, entered = asyncio.Event(), []

    async def locked(body):                                                      # OpenRouter «думает», пока не отпустят
        entered.append(body)
        await gate.wait()
        raise UpstreamError("server", 500)

    monkeypatch.setattr(role.upstream, "open_chat", locked)
    n = 10
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core") as cl:
        tasks = [asyncio.create_task(cl.post("/llm/v1/chat/completions", json=chat_body(text=f"вопрос {i}"),
                                             headers=auth(LLM_TOKEN, f"ses_{i:026d}"))) for i in range(n)]
        await wait_until_async(lambda: sum(t.done() for t in tasks) == n - passing)
        await anyio.sleep(0.1)
        assert len(entered) == passing and len(role.inflight) == 1 and role.inflight["student-01"] == passing
        estimate = role.models["z-ai/glm-5.3"].estimate_usd(body_bytes(entered[0]), entered[0]["max_tokens"])
        assert role.ledger.reserved_open() - share < estimate                   # перерасход доли — меньше одной оценки
        gate.set()
        replies = await asyncio.gather(*tasks)
    codes = sorted((r.status_code, r.json()["error"]["code"]) for r in replies)
    assert codes == sorted([(502, "pcbk_upstream_down")] * passing + [refused] * (n - passing))
    assert role.inflight == {}


def test_poll_failure_never_replaces_money_signs():                              # ревью B, Д4а-R14
    t = now_utc()
    for sign in ("money_out", "key_limit"):
        state = UpstreamState()
        state.record(sign, t, source="chat")
        state.record("unreachable", t, source="poll")                            # связь о деньгах ничего не говорит
        assert state.state == sign
    drill = UpstreamState(drill=True)
    drill.record("money_out", t, source="chat")
    for kind in ("unreachable", None, "key_limit", "key_rejected"):              # учение опрос не трогает
        drill.record(kind, t, source="poll")
    drill.clear_money(t, "money_out")
    assert drill.to_json()["text"] == "на счёте OpenRouter кончились деньги (учения)"


def test_drill_survives_unreachable_then_ok_polls(tmp_path, fake_or):          # ревью B: учения → недоступен → ок
    app, role = make_llm_app(tmp_path, fake_or, mgmt="test-mgmt-key", DRILL_LLM="402")
    working = role.upstream
    with TestClient(app) as c:
        wait_until(lambda: role.checked_at is not None)
        post(c, chat_body())                                                     # учение: кончились деньги
        role.upstream = Upstream("http://127.0.0.1:9/api/v1", "test-openrouter-key", "test-mgmt-key", drill="402")
        c.portal.call(role.poll)                                                 # OpenRouter недоступен
        role.upstream = working
        c.portal.call(role.poll)                                                 # снова отвечает, остаток $30
        j = role.health_json(now_utc())
    assert (j["upstream"]["state"], j["upstream"]["text"]) == ("money_out", "на счёте OpenRouter кончились деньги (учения)")


def test_unreachable_poll_keeps_money_out_until_credits(tmp_path, fake_or):      # ревью B: опрос снимает только свой
    app, role = make_llm_app(tmp_path, fake_or, mgmt="test-mgmt-key")
    working = role.upstream
    with TestClient(app) as c:
        wait_until(lambda: role.checked_at is not None)
        post(c, chat_body(text="q FAKE402"))
        role.upstream = Upstream("http://127.0.0.1:9/api/v1", "test-openrouter-key", "test-mgmt-key")
        c.portal.call(role.poll)
        assert role.health_json(now_utc())["upstream"]["state"] == "money_out"   # не «недоступен»
        role.upstream = working
        c.portal.call(role.poll)                                                 # /credits: остаток есть
        assert role.health_json(now_utc())["upstream"]["state"] == "ok"


def test_config_error_is_403_right_after_token(tmp_path, fake_or):              # ревью B: шаг 1а
    app, role = make_llm_app(tmp_path, fake_or, LLM_STATE="stopped")
    role.config_error = "настройки: LLM_MODELS: неизвестная модель «…»"
    with TestClient(app) as c:
        assert post(c, chat_body(), token=None).status_code == 401              # токен — раньше настройки
        r = post(c, chat_body(model="openrouter/auto"))                          # и выключатель, и чужая модель — позже
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_config")
        assert r.json()["error"]["message"] == LLM_MESSAGES["config"]
    assert fake_or.requests == [] and role.ledger.recent(1)[0].code == "pcbk_config"


def test_ledger_open_failure_is_config_error_without_poll(tmp_path, fake_or):   # ревью B: журнал не открылся
    llm = replace(LLM_SETTINGS, OPENROUTER_BASE_URL=fake_or.url)
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "нет" / "core.db"))      # каталога нет — файл не создать
    role = LlmRole(settings, llm, tokens=LLM_TOKENS, upstream=Upstream(fake_or.url, "test-openrouter-key", None),
                   ledger=Ledger(settings.DB_PATH), key_fingerprint=key_fingerprint("test-openrouter-key"))
    with TestClient(create_app(settings, [role])) as c:
        time.sleep(0.3)                                                          # опрос шёл бы сразу при старте
        r = post(c, chat_body())
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_config")
        j = c.get("/health/llm").json()
    assert role.checked_at is None and fake_or.requests == []                    # фоновых циклов, кроме журнала, нет
    assert role.config_error.startswith("журнал вызовов не открылся: ")
    assert role.health()[1].startswith("ошибка настройки: журнал вызовов не открылся")
    assert (j["config_error"], j["ledger_error"]) == (role.config_error, "write")


def test_health_json_egress_is_for_openrouter_host(tmp_path, fake_or, monkeypatch):   # ревью B: не имя историана
    asked = []

    def status(name=None):
        asked.append(name)
        return {"alive": True, "last_ok_age_s": 1.0}

    monkeypatch.setattr("pcbk_core.llm.refresh_status", status)
    role = make_llm_role(tmp_path, fake_or, OPENROUTER_BASE_URL="http://OR.Example:8080/api/v1")
    assert role.health_json(now_utc())["egress"] == {"alive": True, "last_ok_age_s": 1.0}
    assert asked == ["or.example"]                                               # хост так же, как в egress_targets
    assert egress_targets(role.llm, GatewaySettings(), None)[:1] == [("or.example", 8080)]


def test_health_llm_redacted_off_admin(tmp_path, fake_or):                              # Д4б-R23
    app, _ = make_llm_app(tmp_path, fake_or)                                           # П3: без сети (Ш7)
    money = {"account_remaining_usd", "key_usage_usd", "key_spent_usd"}
    for base, full in (("http://172.31.6.2:8000", False), ("http://172.31.250.82:8000", True),
                       ("http://172.31.250.34:8000", True)):
        with TestClient(app, base_url=base) as c:
            keys = set(c.get("/health/llm").json())
            assert (money & keys) == (money if full else set()) and "spent_usd" in keys, base


# --- Д10а, задача А2: LLM-прокси по долям мест (Д10-R98, B-1); FAKEDEAR — ответ двойника с ценой $0,5037 ---

def test_floor_share_refuses_next_call_uneven_spend(tmp_path, fake_or):  # Review Focus 3
    app, role = make_llm_app(tmp_path, fake_or, shares=True)
    with TestClient(app) as c:
        assert post(c, chat_body(text="q FAKEDEAR")).status_code == 200
        wait_until(lambda: not role.ledger.has_open_or_pending())
        assert role.ledger.spent("student-01") == pytest.approx(0.5037)     # 50,37 цента — не кратно центу
        floor = role.shares.floor_of(1)
        assert floor == 50
        role.shares.apply([(1, 10000, floor), (2, 10000, 20000 - floor)], by="teach", now=now_utc())
        r = post(c, chat_body(text="ещё вопрос"))                            # 0,5037 >= 0,50 — следующий же вызов
        assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_share_spent")
        role.shares.apply([(1, floor, floor + 10), (2, 20000 - floor, 19990 - floor)], by="teach", now=now_utc())
        assert post(c, chat_body(text="и ещё")).status_code == 200          # 0,5037 < 0,60
        wait_until(lambda: not role.ledger.has_open_or_pending())
        assert role.health_json(now_utc())["shares"] == {"over_80": 1, "spent": 0}   # 0,5049 из 0,60
    assert len(fake_or.requests) == 2


def test_stop_place_during_inflight_call_refuses_next(tmp_path, fake_or):  # B-1, I-1 р2: «Остановить место» во время вызова
    app, role = make_llm_app(tmp_path, fake_or, shares=True)
    with serve_app(app) as url, httpx.Client(trust_env=False) as a, httpx.Client(trust_env=False) as b:
        assert b.post(url + "/llm/v1/chat/completions", json=chat_body(text="q FAKEDEAR"),
                      headers=auth(LLM_TOKEN, SES)).status_code == 200
        wait_until(lambda: not role.ledger.has_open_or_pending())
        with a.stream("POST", url + "/llm/v1/chat/completions", json=chat_body(text="q FAKESLOW"),
                      headers=auth(LLM_TOKEN, "ses_" + "B" * 26)) as r1:
            chunks = r1.iter_raw()
            next(chunks)                                                     # вызов идёт: его оценка в spent
            assert role.ledger.has_open_or_pending_of("student-01")
            role.shares.apply_floor(1, 2, by="teach", now=now_utc())         # пол берёт сервер: 50, без оценки идущего
            assert (role.shares.cents(1), role.shares.cents(2)) == (50, 19950)
            for _ in chunks:                                                 # медленный вызов досчитан
                pass
        wait_until(lambda: not role.ledger.has_open_or_pending())
        r = b.post(url + "/llm/v1/chat/completions", json=chat_body(text="после"), headers=auth(LLM_TOKEN, SES))
    assert (r.status_code, r.json()["error"]["code"]) == (402, "pcbk_share_spent")   # спент 0,5049 >= 0,50
    assert len(fake_or.requests) == 2


def test_shares_load_failure_closes_llm(tmp_path, fake_or, monkeypatch):  # закрыто при сбое
    monkeypatch.setattr(Shares, "open", lambda self: False)
    app, role = make_llm_app(tmp_path, fake_or, shares=True)
    with TestClient(app) as c:
        r = post(c, chat_body())
    assert (r.status_code, r.json()["error"]["code"]) == (403, "pcbk_config")
    assert "доли бюджета не загружены" in role.config_error and fake_or.requests == []


# --- сверх брифа ---

def test_role_wires_shares_into_budget_and_lifespan_loads_them(tmp_path, fake_or):   # R98
    app, role = make_llm_app(tmp_path, fake_or, shares=True, shares_drill="shares_stale")
    assert role.budget.shares is role.shares and role.shares.settled_of == role.ledger.settled
    assert role.shares.drill == "shares_stale" and role.shares.loaded is False         # конструктор ничего не открывает
    with TestClient(app) as c:
        assert role.shares.loaded is True and role.config_error is None
        assert post(c, chat_body()).status_code == 200
    plain = make_llm_role(tmp_path / "plain", fake_or)
    assert plain.shares is None and plain.budget.shares is None


def test_shares_counters_follow_each_place_share(tmp_path, fake_or):      # счётчики /health/llm — от своей доли
    app, role = make_llm_app(tmp_path, fake_or, shares=True)
    with TestClient(app) as c:
        assert post(c, chat_body(text="q FAKEDEAR")).status_code == 200
        wait_until(lambda: not role.ledger.has_open_or_pending())
        assert role.health_json(now_utc())["shares"] == {"over_80": 0, "spent": 0}      # 0,5037 из 100
        role.shares.apply([(1, 10000, 60), (2, 10000, 19940)], by="teach", now=now_utc())
        assert role.health_json(now_utc())["shares"] == {"over_80": 1, "spent": 0}      # из 0,60 — больше 80 %
        role.shares.apply_floor(1, 2, by="teach", now=now_utc())
        assert role.health_json(now_utc())["shares"] == {"over_80": 0, "spent": 1}      # пол — исчерпана


def test_startup_line_names_allocated_shares(tmp_path, fake_or, caplog):
    app, role = make_llm_app(tmp_path, fake_or, shares=True)
    with caplog.at_level(logging.INFO, logger="pcbk_core.llm"), TestClient(app):
        pass
    assert "LLM-прокси: моделей 9, доли мест: распределено $1100 из $1000, по умолчанию $100" in caplog.text


def test_fake_dear_word_prices_body_stream_and_generation(fake_or):       # двойник: FAKEDEAR — $0,5037 везде
    head = {"Authorization": "Bearer test-openrouter-key"}
    with httpx.Client(trust_env=False, timeout=10) as c:
        body = c.post(fake_or.url + "/chat/completions", headers=head,
                      json=chat_body(text="q FAKEDEAR", stream=False)).json()
        assert body["usage"]["cost"] == 0.5037
        streamed = c.post(fake_or.url + "/chat/completions", headers=head, json=chat_body(text="q FAKEDEAR")).text
        assert '"cost": 0.5037' in streamed and streamed.rstrip().endswith("data: [DONE]")
        gen = body["id"]
        found = [c.get(fake_or.url + "/generation", params={"id": gen}, headers=head) for _ in range(3)]
        assert [r.status_code for r in found] == [404, 404, 200] and found[2].json()["data"]["total_cost"] == 0.5037
        plain = c.post(fake_or.url + "/chat/completions", headers=head, json=chat_body(stream=False)).json()
        assert plain["usage"]["cost"] == 0.0012                              # прочие слова не меняются
        usage = c.get(fake_or.url + "/key", headers=head).json()["data"]["usage"]
        assert usage == pytest.approx(3 * 0.5037 + 0.0012)                   # тело, поток, найденный дозапрос, обычный


def test_ledger_tolerates_short_series_of_write_failures(tmp_path, fake_or):     # Д22: занятие 15.10, 10 мест
    from pcbk_core.llm import LEDGER_FAILS_TOLERATED
    app, role = make_llm_app(tmp_path, fake_or)
    with TestClient(app):
        wait_until(lambda: role.ledger.writable_ok is True)
        role.ledger.consecutive_failures = LEDGER_FAILS_TOLERATED - 1              # короткая блокировка SQLite
        assert role.ledger_ok() is True and role.ledger_error() is None
        role.ledger.consecutive_failures = LEDGER_FAILS_TOLERATED                  # долгий сбой — прокси закрыт
        assert role.ledger_ok() is False and role.ledger_error() == "write"
        role.ledger.consecutive_failures = 0
