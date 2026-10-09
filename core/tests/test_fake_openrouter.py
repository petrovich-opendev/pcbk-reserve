"""Ручка /_delay двойника OpenRouter (Д11б, Б2; итоговое ревью Д11б, m-3): задержка вызова чата для учений тормоза
прогонщика на стенде — 204/400 на границах, задержка с потоком и без, ошибки до потока её не ждут, DELETE снимает."""
import json
import time
import urllib.error
import urllib.request

import pytest

from fake_openrouter import DELAY_MAX_S

DELAY_S = 0.4
CHAT = {"model": "z-ai/glm-5.3", "messages": [{"role": "user", "content": "вопрос"}]}


def call(fake_or, method, path, payload=None, key="test-openrouter-key"):
    """(код, тело) — ответ с ошибкой HTTP тоже результат."""
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(fake_or.base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read()


def timed_chat(fake_or, **extra):
    started = time.monotonic()
    code, body = call(fake_or, "POST", "/api/v1/chat/completions", {**CHAT, **extra})
    return code, body, time.monotonic() - started


@pytest.mark.parametrize("s", [0, DELAY_S, DELAY_MAX_S])
def test_delay_accepts_bounds(fake_or, s):
    assert call(fake_or, "POST", "/_delay", {"s": s})[0] == 204


@pytest.mark.parametrize("payload", [{"s": -0.1}, {"s": DELAY_MAX_S + 0.1}, {"s": True}, {"s": "2"}, {}, {"s": None}])
def test_delay_refuses_bad_values(fake_or, payload):
    assert call(fake_or, "POST", "/_delay", payload)[0] == 400


@pytest.mark.parametrize("stream", [False, True])
def test_delay_holds_every_chat_call(fake_or, stream):
    assert call(fake_or, "POST", "/_delay", {"s": DELAY_S})[0] == 204
    code, body, took = timed_chat(fake_or, stream=stream)
    assert code == 200 and took >= DELAY_S
    assert (b"[DONE]" in body) if stream else ("choices" in json.loads(body))


def test_errors_before_stream_do_not_wait(fake_or):
    call(fake_or, "POST", "/_delay", {"s": DELAY_MAX_S})
    code, _, took = timed_chat(fake_or, messages=[{"role": "user", "content": "FAKE500"}])
    assert code == 500 and took < 5


def test_delete_delay_restores_fast_answers(fake_or):
    call(fake_or, "POST", "/_delay", {"s": DELAY_MAX_S})
    assert call(fake_or, "DELETE", "/_delay")[0] == 204
    code, _, took = timed_chat(fake_or, stream=True)
    assert code == 200 and took < 5
