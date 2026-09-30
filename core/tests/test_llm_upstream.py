"""Клиент OpenRouter на двойнике: поток, признаки «кончились деньги», max_price, учение 402 без сети, опрос."""
import logging
import time

import httpx
import pytest

from pcbk_core.llm.upstream import Upstream, UpstreamError, classify
from pcbk_core.logs import setup_logging

pytestmark = pytest.mark.anyio
BODY = {"model": "openai/gpt-6-luna", "stream": True, "messages": [{"role": "user", "content": "q"}]}


def body_with(word):
    return {**BODY, "messages": [{"role": "user", "content": f"q {word}"}]}


def test_classify_table():                                                       # Review Focus 3
    e = lambda msg, **meta: {"message": msg, **({"metadata": meta} if meta else {})}
    assert classify(402, e("Insufficient credits")) == "money_out"
    assert classify(402, e("x", limit_source="in_flight_budget")) == "in_flight"
    assert classify(403, e("Key limit exceeded")) == classify(402, e("Budget limit exceeded")) == "key_limit"
    assert classify(403, e("insufficient credits")) == "money_out"
    assert classify(403, e("This model is not available in your region.")) == "region"
    assert (classify(401, None), classify(429, None), classify(503, None), classify(400, e("bad"))) == \
           ("key_rejected", "rate", "server", "rejected")


async def test_open_chat_streams_with_generation_header(fake_or):
    up = Upstream(fake_or.url, "test-openrouter-key", None)
    r = await up.open_chat(BODY)
    data = b"".join([c async for c in r.aiter_raw()])
    await r.aclose()
    assert r.headers["x-generation-id"].startswith("gen-") and b"data: [DONE]" in data
    assert fake_or.requests[-1]["auth_ok"] is True


@pytest.mark.parametrize("word,kind,status", [("FAKE402", "money_out", 402), ("FAKE402INFLIGHT", "in_flight", 402),
    ("FAKE403KEY", "key_limit", 403), ("FAKE403REGION", "region", 403), ("FAKE401", "key_rejected", 401),
    ("FAKE500", "server", 500), ("FAKE404", "rejected", 404)])
async def test_errors_classified(fake_or, word, kind, status):                   # Review Focus 3
    with pytest.raises(UpstreamError) as e:
        await Upstream(fake_or.url, "test-openrouter-key", None).open_chat(body_with(word))
    assert (e.value.kind, e.value.status) == (kind, status)
    assert (e.value.retry_after == "1") is (word == "FAKE402INFLIGHT")


async def test_unreachable_is_fast():
    t = time.monotonic()
    with pytest.raises(UpstreamError) as e:
        await Upstream("http://127.0.0.1:9/api/v1", "k", None).open_chat(BODY)
    assert e.value.kind == "unreachable" and time.monotonic() - t < 2
    assert str(e.value) == "unreachable 0"                                       # П21: без текстов в str()


def test_constructor_does_no_io(monkeypatch):                                    # П22
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: pytest.fail("клиент в конструкторе"))
    Upstream("https://openrouter.ai/api/v1", "k", None)                          # вне цикла событий, без клиента


async def test_env_proxy_is_ignored(fake_or, monkeypatch):                        # П14
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")                          # прокси нет: через него — отказ
    up = Upstream(fake_or.url, "test-openrouter-key", None)
    assert "usage" in await up.key_info()
    await up.aclose()


async def test_price_above_max_price_is_rejected(fake_or):                       # У1
    fake_or.set_price("openai/gpt-6-luna", 0.30, 1.00)                           # цена точки выросла
    capped = {**BODY, "provider": {"only": ["azure"], "max_price": {"prompt": 0.22, "completion": 0.825}}}
    with pytest.raises(UpstreamError) as e:
        await Upstream(fake_or.url, "test-openrouter-key", None).open_chat(capped)
    assert (e.value.kind, e.value.status) == ("rejected", 404) and "max_price" in e.value.message
    wide = {**capped, "provider": {"only": ["azure"], "max_price": {"prompt": 0.30, "completion": 1.00}}}
    up = Upstream(fake_or.url, "test-openrouter-key", None)
    r = await up.open_chat(wide); await r.aclose(); await up.aclose()             # цена в пределе — ответ


async def test_drill_402_never_touches_network(fake_or):
    with pytest.raises(UpstreamError) as e:
        await Upstream(fake_or.url, "test-openrouter-key", None, drill="402").open_chat(BODY)
    assert (e.value.kind, e.value.status) == ("money_out", 402) and fake_or.requests == []


async def test_generation_cost_after_404s(fake_or):
    up = Upstream(fake_or.url, "test-openrouter-key", None)
    r = await up.open_chat(BODY); gen = r.headers["x-generation-id"]; await r.aclose()
    assert [await up.generation_cost(gen) for _ in range(3)] == [None, None, 0.002]


async def test_key_info_and_credits(fake_or):
    assert (await Upstream(fake_or.url, "test-openrouter-key", "test-mgmt-key").credits()) == 30.0
    assert (await Upstream(fake_or.url, "test-openrouter-key", None).credits()) is None
    assert "usage" in await Upstream(fake_or.url, "test-openrouter-key", None).key_info()
    with pytest.raises(UpstreamError) as e:
        await Upstream(fake_or.url, "wrong", None).key_info()
    assert e.value.kind == "key_rejected"
    with pytest.raises(UpstreamError) as e:
        await Upstream(fake_or.url, "test-openrouter-key", "test-openrouter-key").credits()   # обычный ключ вместо управления
    assert e.value.status == 403


async def test_httpx_quiet_and_no_key_in_log(fake_or, capfd, restore_logging):  # П21: адрес и ключ — не в журнал
    logging.getLogger().setLevel(logging.DEBUG)                                 # корень не глушит: только QUIET_LOGGERS
    setup_logging()
    assert not any(logging.getLogger(name).isEnabledFor(logging.INFO) for name in ("httpx", "httpcore"))
    up = Upstream(fake_or.url, "test-openrouter-key", None)
    with pytest.raises(UpstreamError) as e:
        await up.open_chat(body_with("FAKE403KEY"))
    await up.key_info(); await up.aclose()
    assert "Key limit" not in str(e.value) and "Key limit" in e.value.message      # текст OpenRouter — только атрибутом
    err = capfd.readouterr().err
    assert "test-openrouter-key" not in err and "HTTP Request" not in err
