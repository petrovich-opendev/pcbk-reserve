"""Наблюдатель потока SSE: любая нарезка, CRLF, событие ошибки; странное событие не бросает и не течёт в журнал."""
import pytest

from pcbk_core.llm.ledger import Usage
from pcbk_core.llm.sse import MAX_EVENT_BYTES, SseObserver
from pcbk_core.logs import setup_logging

STREAM = (": OPENROUTER PROCESSING\n\n"
          'data: {"id":"gen-1","provider":"Fireworks","choices":[{"index":0,"delta":{"content":"Да"}}]}\n\n'
          'data: {"id":"gen-1","provider":"Fireworks","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
          '"usage":{"prompt_tokens":10,"completion_tokens":2,"cost":0.0012}}\n\ndata: [DONE]\n\n').encode()


@pytest.mark.parametrize("size", [1, 7, 64, len(STREAM)])
def test_observer_any_chunking(size):
    o = SseObserver()
    for i in range(0, len(STREAM), size):
        o.feed(STREAM[i:i + size])
    assert (o.generation_id, o.provider, o.usage["cost"], o.done, o.error) == ("gen-1", "Fireworks", 0.0012, True, None)


def test_observer_crlf_and_error_event():
    o = SseObserver()
    o.feed(b'data: {"id":"gen-2","choices":[]}\r\n\r\ndata: {"error":{"code":402,"message":"Insufficient credits"}}\r\n\r\n')
    assert o.error["code"] == 402 and not o.done and o.usage is None


def test_observer_odd_events_never_raise_or_leak(capfd, restore_logging):         # П21
    setup_logging()
    o = SseObserver()
    for ev in (b'data: {"usage": "SECRET-1"}\n\n', b'data: {"choices": "SECRET-2", "id": 7}\n\n',
               b'data: {"provider": ["SECRET-3"]}\n\n', b'data: \xff\xfeSECRET-4\n\n'):
        o.feed(ev)                                                               # не бросает
    assert o.bad_events >= 1 and (o.usage, o.generation_id, o.provider) == (None, None, None)
    assert Usage.from_openrouter({"cost": "SECRET-5", "prompt_tokens": "x"}).cost is None
    assert "SECRET" not in capfd.readouterr().err


def usage_event(cost: bytes) -> bytes:
    return b'data: {"id":"gen-1","choices":[],"usage":{"prompt_tokens":10,"cost":' + cost + b'}}\n\n'


@pytest.mark.parametrize("feeds,cost,bad", [                                    # ловушки денег
    ([usage_event(b"0.001"), usage_event(b"0.002")], 0.002, 0),                  # два чанка usage — последний
    ([usage_event(b"NaN")], None, 0),
    ([usage_event(b"-1")], None, 0),
    ([usage_event(b'"0.5"')], None, 0),
    ([usage_event(b"1e400")], None, 0),                                          # бесконечность
    ([usage_event(b"1" + b"0" * 400)], None, 0),                                 # целое больше float
    ([b"data: {" + b"x" * (MAX_EVENT_BYTES + 10), b"}\n\n", usage_event(b"0.003")], 0.003, 1),   # событие > 1 МиБ
    ([b'data: {"choices": "x", "usage": {"cost": 0.004}}\n\n'], 0.004, 1),     # usage рядом с кривым choices
])
def test_observer_money_traps(feeds, cost, bad):
    o = SseObserver()
    for f in feeds:
        o.feed(f)
    assert (Usage.from_openrouter(o.usage).cost, o.bad_events) == (cost, bad)
