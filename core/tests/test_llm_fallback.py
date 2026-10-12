"""Запасная модель LLM-прокси (Д14): разбор LLM_FALLBACK (models.py) и повтор на двойнике OpenRouter —
подождите/недоступен до первого байта переключает без потери запроса места, деньги/rejected/переполнение/
поток-уже-начат не переключают, второго повтора нет."""
import pytest
from fastapi.testclient import TestClient

from helpers import LLM_TOKEN, SES, auth, chat_body, make_llm_app, now_utc
from pcbk_core.llm.models import parse_fallback, select_models
from pcbk_core.llm.upstream import UpstreamError

# --- parse_fallback (без сети) ---


def test_parse_fallback_empty_and_valid():
    models = select_models("")
    assert parse_fallback("", models) == {}
    assert parse_fallback("   ", models) == {}
    assert parse_fallback(" z-ai/glm-5.3 = z-ai/glm-5.3-flash , openai/gpt-6-luna=openai/gpt-6-sol ", models) == \
        {"z-ai/glm-5.3": "z-ai/glm-5.3-flash", "openai/gpt-6-luna": "openai/gpt-6-sol"}


@pytest.mark.parametrize("raw", [
    "zq-raw/model=z-ai/glm-5.3-flash",                                      # основная неизвестна
    "z-ai/glm-5.3=zq-raw/model",                                            # запасная неизвестна
    "z-ai/glm-5.3=z-ai/glm-5.3",                                            # совпадают
    "z-ai/glm-5.3=z-ai/glm-5.3-flash,z-ai/glm-5.3=openai/gpt-6-sol",        # основная названа дважды
    "z-ai/glm-5.3",                                                         # без «=»
    "z-ai/glm-5.3=a=b",                                                     # два «=»
])
def test_parse_fallback_refused(raw):
    with pytest.raises(ValueError, match=r"^LLM_FALLBACK:"):
        parse_fallback(raw, select_models(""))


def test_parse_fallback_restricted_by_llm_models():
    models = select_models("z-ai/glm-5.3,z-ai/glm-5.3-flash")
    assert parse_fallback("z-ai/glm-5.3=z-ai/glm-5.3-flash", models) == {"z-ai/glm-5.3": "z-ai/glm-5.3-flash"}
    with pytest.raises(ValueError, match=r"^LLM_FALLBACK: запасная модель"):
        parse_fallback("z-ai/glm-5.3=openai/gpt-6-luna", models)           # не входит в LLM_MODELS


# --- повтор на двойнике: подмена role.upstream.open_chat по имени модели в теле ---

PRIMARY, FALLBACK = "z-ai/glm-5.3", "z-ai/glm-5.3-flash"


def post(c, body, token=LLM_TOKEN, session=SES):
    return c.post("/llm/v1/chat/completions", json=body, headers=auth(token, session))


def test_fallback_retries_once_on_rate_then_succeeds(tmp_path, fake_or):             # требования 2–4
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    real_open_chat = role.upstream.open_chat
    seen = []

    async def once_rate_limited(body):
        seen.append(body)
        if body["model"] == PRIMARY:
            raise UpstreamError("rate", 429, "Rate limited", retry_after="3")
        return await real_open_chat(body)

    role.upstream.open_chat = once_rate_limited
    with TestClient(app) as c:
        r = post(c, chat_body())
    assert r.status_code == 200 and r.text.rstrip().endswith("data: [DONE]")
    assert [b["model"] for b in seen] == [PRIMARY, FALLBACK]                         # место видит только успех
    assert seen[1]["reasoning"] == {"effort": "low"}                                 # тело запасной — её политикой
    rows = role.ledger.recent(2)
    assert (rows[0].model, rows[0].status, rows[0].cost_source) == (FALLBACK, "ok", "stream")
    assert (rows[1].model, rows[1].status, rows[1].code, rows[1].cost, rows[1].cost_source) == \
        (PRIMARY, "upstream_error", "pcbk_upstream_wait", 0.0, "none")
    assert role.inflight == {}                                                       # резерв основной не висит
    j = role.health_json(now_utc())
    assert (j["pending"], j["reserved_usd"], j["spent_usd"]) == (0, 0.0, pytest.approx(rows[0].cost))


def test_fallback_log_line_carries_fallback_from_and_reason(tmp_path, fake_or, capfd, restore_logging):
    from pcbk_core.logs import setup_logging
    setup_logging()
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    real_open_chat = role.upstream.open_chat

    async def once_rate_limited(body):
        if body["model"] == PRIMARY:
            raise UpstreamError("rate", 429, "Rate limited", retry_after="3")
        return await real_open_chat(body)

    role.upstream.open_chat = once_rate_limited
    with TestClient(app) as c:
        post(c, chat_body())
    err = capfd.readouterr().err
    assert f"model={FALLBACK}" in err and f"fallback_from={PRIMARY} fallback_reason=rate" in err
    assert f"model={PRIMARY} " in err and "fallback_from=" not in err.split(f"model={PRIMARY} ")[1].split("\n")[0]


def test_fallback_also_fails_client_gets_fallback_refusal_no_further_retry(tmp_path, fake_or):   # требование 2, «второго повтора нет»
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    seen = []

    async def always_rate_limited(body):
        seen.append(body["model"])
        raise UpstreamError("rate", 429, "Rate limited", retry_after="3")

    role.upstream.open_chat = always_rate_limited
    with TestClient(app) as c:
        r = post(c, chat_body())
    assert seen == [PRIMARY, FALLBACK]                                              # ровно один повтор
    assert (r.status_code, r.json()["error"]["code"]) == (429, "pcbk_upstream_wait")
    rows = role.ledger.recent(2)
    assert {rows[0].model, rows[1].model} == {PRIMARY, FALLBACK}
    assert all(row.status == "upstream_error" and row.cost_source == "none" for row in rows)
    assert role.inflight == {}


@pytest.mark.parametrize("kind,status", [("rate", 429), ("server", 500), ("unreachable", 0), ("in_flight", 402)])
def test_fallback_triggers_on_every_retryable_kind(tmp_path, fake_or, kind, status):   # требование 2: wait/down
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    real_open_chat = role.upstream.open_chat
    seen = []

    async def fails_once(body):
        seen.append(body["model"])
        if body["model"] == PRIMARY:
            raise UpstreamError(kind, status, "отказ")
        return await real_open_chat(body)

    role.upstream.open_chat = fails_once
    with TestClient(app) as c:
        r = post(c, chat_body())
    assert seen == [PRIMARY, FALLBACK] and r.status_code == 200


@pytest.mark.parametrize("kind,status,message", [
    ("money_out", 402, "Insufficient credits"), ("key_limit", 403, "Key limit exceeded"),
    ("key_rejected", 401, "User not found"), ("region", 403, "not available in your region"),
    ("rejected", 400, "Invalid request"),
])
def test_fallback_not_triggered_for_money_key_region_rejected(tmp_path, fake_or, kind, status, message):  # требование 2
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    seen = []

    async def refuse(body):
        seen.append(body["model"])
        raise UpstreamError(kind, status, message)

    role.upstream.open_chat = refuse
    with TestClient(app) as c:
        post(c, chat_body())
    assert seen == [PRIMARY]                                                        # запасная не вызывалась


def test_fallback_not_triggered_for_context_overflow(tmp_path, fake_or):             # требование 2: переполнение
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    seen = []

    async def overflow(body):
        seen.append(body["model"])
        raise UpstreamError("rejected", 400, "This endpoint's maximum context length is 131072 tokens.")

    role.upstream.open_chat = overflow
    with TestClient(app) as c:
        r = post(c, chat_body())
    assert seen == [PRIMARY] and r.json()["error"]["code"] == "pcbk_body_too_large"


def test_fallback_not_triggered_after_stream_started(tmp_path, fake_or):             # требование 2: поток уже начат
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    with TestClient(app) as c:
        r = post(c, chat_body(text="q FAKEMIDERR"))                                  # 502 посреди потока, после контента
    assert len(fake_or.requests) == 1 and r.status_code == 200                       # место получило начатый поток как есть


def test_fallback_empty_llm_fallback_behaves_as_before(tmp_path, fake_or):           # требование 6: пусто — без повтора
    app, role = make_llm_app(tmp_path, fake_or)                                      # LLM_FALLBACK по умолчанию ""
    assert role.fallback == {}
    with TestClient(app) as c:
        r = post(c, chat_body(text="q FAKE500"))
    assert len(fake_or.requests) == 1
    assert (r.status_code, r.json()["error"]["code"]) == (502, "pcbk_upstream_down")


def test_fallback_primary_without_pair_behaves_as_before(tmp_path, fake_or):         # требование 6: основная без пары
    app, role = make_llm_app(tmp_path, fake_or,
                             LLM_FALLBACK="openai/gpt-6-luna=openai/gpt-6-sol")       # не для PRIMARY
    with TestClient(app) as c:
        r = post(c, chat_body(text="q FAKE500", model=PRIMARY))
    assert len(fake_or.requests) == 1
    assert (r.status_code, r.json()["error"]["code"]) == (502, "pcbk_upstream_down")


def test_fallback_blocked_by_budget_gets_original_refusal_without_calling_it(tmp_path, fake_or):   # требование 3
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    seen = []

    async def rate_limited(body):
        seen.append(body["model"])
        raise UpstreamError("rate", 429, "Rate limited", retry_after="3")

    role.upstream.open_chat = rate_limited
    real_refusal, checks = role.budget.refusal, []

    def refusal_once_then_share_spent(caller):                                      # 1-й вызов (основная) — как есть,
        checks.append(caller)                                                       # 2-й (запасная) — доля исчерпана
        return real_refusal(caller) if len(checks) == 1 else "share_spent"

    role.budget.refusal = refusal_once_then_share_spent
    with TestClient(app) as c:
        r = post(c, chat_body())
    assert seen == [PRIMARY]                                                         # запасная не вызывалась вовсе
    assert (r.status_code, r.json()["error"]["code"]) == (429, "pcbk_upstream_wait")  # отказ как у основной
    assert role.inflight == {}


def test_fallback_402_no_retry_through_fake_openrouter(tmp_path, fake_or):           # требование 6, двойник целиком
    app, role = make_llm_app(tmp_path, fake_or, LLM_FALLBACK=f"{PRIMARY}={FALLBACK}")
    with TestClient(app) as c:
        r = post(c, chat_body(text="q FAKE402"))
    assert len(fake_or.requests) == 1 and r.json()["error"]["code"] == "pcbk_money_out"
