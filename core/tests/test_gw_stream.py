"""Поток событий /pcbk/api/oc/event (Д5-R35, задача Б4): фильтр типов событий сырыми байтами, читатель места в
ограниченной очереди, перепроверка сессии, слот потока — только после 200 места и ровно одно освобождение, срок
заголовков места, тишина и большое событие рвут поток, поток — не активность места. Места и sp-ctl — двойники
задачи А3 (поток /event — _EventBody)."""
import json
import socket
import threading
import time
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from helpers import (ORIGIN, SETTINGS, DummyRole, gateway_role, login, login_cookie, make_gateway, serve_app,
                     wait_until)
from pcbk_core import main
from pcbk_core.app import create_app
from pcbk_core.gateway import places, proxy
from pcbk_core.gateway.accounts import Account
from pcbk_core.gateway.proxy import (PROXY_MESSAGES, SSE_MAX_EVENT_BYTES, STREAM_QUEUE_MAX, filter_sse,
                                     stream_events)
from pcbk_core.gateway.rules import RULE_MESSAGES
from pcbk_core.gateway.stats import MAX_STREAMS_PER_LOGIN

pytestmark = pytest.mark.anyio
EV = lambda t, **p: b"data: " + json.dumps({"id": "e", "type": t, "properties": p}).encode() + b"\n\n"
BASE = "https://stand.test:8443"


async def aiter_of(chunks):
    for chunk in chunks:
        yield chunk


async def collect(chunks):
    return [x async for x in filter_sse(aiter_of(chunks))]


# --- тесты брифа ---

async def test_filter_sse_passes_allowed_drops_noise():                                 # Review Focus 4
    stream = EV("server.connected") + EV("plugin.added") + EV("message.part.delta", delta="При") + \
             EV("catalog.updated") + b"data: {not json}\n\n" + EV("server.heartbeat")
    out = await collect([stream[:7], stream[7:40], stream[40:]])                        # границы кусков — посреди событий
    assert [json.loads(e[6:])["type"] for e in out] == ["server.connected", "message.part.delta", "server.heartbeat"]
    assert out[1] == EV("message.part.delta", delta="При")                               # байты как у места


async def test_filter_sse_crlf_and_oversize():
    assert len(await collect([EV("session.idle").replace(b"\n\n", b"\r\n\r\n")])) == 1
    with pytest.raises(ValueError):
        await collect([b"data: " + b"x" * (SSE_MAX_EVENT_BYTES + 1)])


def test_stream_ends_with_place_and_is_not_activity(tmp_path):                          # Review Focus 4
    app, role = make_gateway(tmp_path, ready=(3,))
    role.fake_places.events[3] = [EV("server.connected"), EV("session.status"), EV("server.instance.disposed")]
    with TestClient(app, base_url="https://stand.test:8443") as c:
        login(c, "stu-03")
        role.mono.advance(500)
        with c.stream("GET", "/pcbk/api/oc/event") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            assert r.headers["x-accel-buffering"] == "no" and r.headers["cache-control"] == "no-cache"
            body = b"".join(r.iter_bytes())                                              # место закрыло — конец
        assert body.count(b"data: ") == 3
        assert role.workplaces.idle_s(3) >= 500 and role.stats.streams == 0


def test_stream_survives_pauses(tmp_path):                                              # р2 major; Review Focus 4
    app, role = make_gateway(tmp_path, ready=(3,), stream_recheck_s=0.1)
    role.fake_places.events[3] = [EV("server.connected"), ("pause", 0.35), EV("server.heartbeat"),
                                  ("pause", 0.35), EV("session.idle")]
    with TestClient(app, base_url="https://stand.test:8443") as c:
        login(c, "stu-03")
        with c.stream("GET", "/pcbk/api/oc/event") as r:
            body = b"".join(r.iter_bytes())
    assert body.count(b"data: ") == 3                                                    # паузы длиннее срока — поток цел


def open_stream(url, cookie):
    c = httpx.Client(base_url=url)
    return c, c.stream("GET", "/pcbk/api/oc/event", headers={"Cookie": f"pcbk_session={cookie}"})


def test_client_disconnect_closes_upstream(tmp_path):                                   # Review Focus 4
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    fp.events[3] = "endless"                                                             # пульс раз в 0,05 с без конца
    with serve_app(app) as url:
        cookie = login_cookie(url, "stu-03")
        c, s = open_stream(url, cookie)
        with s as r:
            next(r.iter_bytes())
        c.close()
        wait_until(lambda: fp.event_closed[3], timeout=3)
    assert role.stats.streams == 0


def test_client_gone_while_place_opens(tmp_path):                                       # р3; Review Focus 4
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    fp.events[3] = "endless"
    with serve_app(app) as url:
        cookie = login_cookie(url, "stu-03")
        c, s = open_stream(url, cookie)
        with s as r:                                                                     # первая вкладка держит слот
            first = r.iter_bytes()                                                       # временный итератор httpx при
            next(first)                                                                  # сборке закрыл бы соединение
            fp.hang_for[3] = 0.3                                                         # вторая: заголовки места через 0,3 с
            with pytest.raises(httpx.ReadTimeout):                                       # клиент ушёл раньше
                httpx.get(url + "/pcbk/api/oc/event", headers={"Cookie": f"pcbk_session={cookie}"},
                          timeout=httpx.Timeout(2.0, read=0.1))
            wait_until(lambda: fp.event_starts[3] == 2 and fp.event_open[3] == 1, timeout=3)
            assert role.stats.streams_of("stu-03") == 1                                  # возвращён ровно один слот
        c.close()
        wait_until(lambda: role.stats.streams == 0, timeout=3)


def test_stream_ends_on_logout(tmp_path, gwlog):                                       # Review Focus 4
    app, role = make_gateway(tmp_path, ready=(3,), stream_recheck_s=0.1)
    role.fake_places.events[3] = "endless"
    with serve_app(app) as url:
        cookie = login_cookie(url, "stu-03")
        c, s = open_stream(url, cookie)
        with s as r:
            chunks = r.iter_bytes()
            next(chunks)
            httpx.post(url + "/pcbk/api/auth/logout", headers={"Cookie": f"pcbk_session={cookie}", "Origin": ORIGIN})
            t = time.monotonic()
            for _ in chunks:
                assert time.monotonic() - t < 2
        c.close()
    assert "closed_by=session" in gwlog.text                                         # поток uvicorn пишет в тот же журнал


def test_stream_slot_not_leaked(tmp_path):                                              # р2 major; Review Focus 4
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    with TestClient(app, base_url="https://stand.test:8443") as c:
        login(c, "stu-03")
        role.workplaces.mark_unready(3)
        assert [c.get("/pcbk/api/oc/event").status_code for _ in range(5)] == [503] * 5   # место спит
        role.workplaces.set_ready_for_tests(3)
        fp.raise_for[3] = httpx.ConnectError("x")
        assert [c.get("/pcbk/api/oc/event").status_code for _ in range(5)] == [503] * 5   # у «готового» — нет связи
        role.workplaces.set_ready_for_tests(3); fp.raise_for.pop(3)
        fp.force_status[3] = (401, {})
        assert c.get("/pcbk/api/oc/event").status_code == 502
        fp.force_status.pop(3)
        fp.hang_for[3] = 30.0                                                            # заголовков нет
        with patch.object(places, "STREAM_OPEN_S", 0.2):
            assert c.get("/pcbk/api/oc/event").status_code == 503
        fp.hang_for.pop(3)
        assert role.stats.streams_of("stu-03") == 0                                      # ни один отказ слот не держит
        role.workplaces.set_ready_for_tests(3)
        # TestClient отдаёт ответ, лишь когда приложение его допишет: слот во время потока видит проба двойника
        held = []
        fp.events[3] = [EV("server.connected"), lambda: held.append(role.stats.streams_of("stu-03"))]
        with c.stream("GET", "/pcbk/api/oc/event") as r:
            assert r.status_code == 200 and held == [1]
            b"".join(r.iter_bytes())
    assert role.stats.streams == 0


def test_event_needs_same_origin_fetch(tmp_path):                                      # р4: слоты не занять с *.pcbk.ru
    app, role = make_gateway(tmp_path, ready=(3,))
    role.fake_places.events[3] = [EV("server.connected")]
    with TestClient(app, base_url="https://stand.test:8443") as c:
        login(c, "stu-03")
        assert c.get("/pcbk/api/oc/event", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
        assert role.stats.streams_of("stu-03") == 0 and role.fake_places.event_starts.get(3, 0) == 0


def test_stream_limit_per_login(tmp_path):
    app, role = make_gateway(tmp_path, ready=(3,))
    role.fake_places.events[3] = "endless"
    with serve_app(app) as url:
        cookie = login_cookie(url, "stu-03")
        opened = [open_stream(url, cookie) for _ in range(3)]
        heads = [s.__enter__() for _, s in opened]
        with httpx.stream("GET", url + "/pcbk/api/oc/event", headers={"Cookie": f"pcbk_session={cookie}"}) as r4:
            assert r4.status_code == 429                                                 # поломка — не 200 без конца
            r4.read()
        assert (r4.status_code, r4.json()["data"]["message"]) == (429, "Открыто слишком много вкладок чата — закройте лишние")
        assert role.fake_places.event_starts[3] == 3                                     # четвёртый место не спрашивал
        for (c, s), _ in zip(opened, heads):
            s.__exit__(None, None, None); c.close()


def test_silent_place_is_stream_broken(tmp_path, monkeypatch):
    monkeypatch.setattr(places, "SSE_READ_TIMEOUT_S", 0.3)
    app, role = make_gateway(tmp_path, ready=(3,), stream_recheck_s=0.1)
    role.fake_places.events[3] = "silent"                                                # connected, затем тишина
    with TestClient(app, base_url="https://stand.test:8443") as c:
        login(c, "stu-03")
        with c.stream("GET", "/pcbk/api/oc/event") as r:
            b"".join(r.iter_bytes())
    assert role.stats.count_1h("stream_broken") == 1


# --- решения контроллера ---

def test_event_with_body_refused_before_place(tmp_path, gwlog):                         # решение 1
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        r = c.request("GET", "/pcbk/api/oc/event", content=b"x")
        assert (r.status_code, r.json()) == (400, {"name": "BadRequest", "data": {"message": RULE_MESSAGES["bad_body"]}})
        r = c.request("GET", "/pcbk/api/oc/event", content=iter([b"x"]))                # Transfer-Encoding: chunked
        assert (r.status_code, r.json()["data"]["message"]) == (400, RULE_MESSAGES["bad_body"])
        assert role.stats.count_1h("refused") == 2 and fp.event_starts.get(3, 0) == 0
        assert role.stats.streams_of("stu-03") == 0
        assert "reason=bad_body status=400" in gwlog.text
        fp.events[3] = [EV("server.connected")]
        with c.stream("GET", "/pcbk/api/oc/event", headers={"Content-Length": "0"}) as r:   # пустое тело — не тело
            assert r.status_code == 200 and b"".join(r.iter_bytes()) == EV("server.connected")


def test_session_checked_once_per_period_not_per_event(tmp_path, monkeypatch):          # решение 2
    app, role = make_gateway(tmp_path, ready=(3,), stream_recheck_s=1.0)
    role.fake_places.events[3] = [EV("message.part.delta", delta="x")] * 200
    calls = []
    real = role.store.session
    monkeypatch.setattr(role.store, "session", lambda token: calls.append(1) or real(token))
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        before = len(calls)
        with c.stream("GET", "/pcbk/api/oc/event") as r:
            body = b"".join(r.iter_bytes())
    assert body.count(b"data: ") == 200
    assert len(calls) - before - 1 <= 2                                                  # 1 — вход запроса (current_account)


def test_session_end_closes_stream_within_recheck(tmp_path, gwlog):                     # решение 2
    app, role = make_gateway(tmp_path, ready=(3,), stream_recheck_s=0.2)
    role.fake_places.events[3] = "endless"                                               # события идут непрерывно
    with serve_app(app) as url:
        cookie = login_cookie(url, "stu-03")
        c, s = open_stream(url, cookie)
        with s as r:
            chunks = r.iter_bytes()
            next(chunks)
            role.store.close_session(cookie)                                             # сессия удалена мимо ручки выхода
            t = time.monotonic()
            for _ in chunks:
                assert time.monotonic() - t < 2
            waited = time.monotonic() - t
        c.close()
    assert waited <= 0.2 + 0.2 + 0.3                                                     # срок перепроверки + срок ожидания + запас
    assert "closed_by=session" in gwlog.text and role.stats.streams == 0


async def test_release_is_idempotent(tmp_path):                                         # решение 3
    role = gateway_role(tmp_path, ready=(3,))
    fp = role.fake_places
    fp.events[3] = "endless"
    account = Account("stu-03", "student", 3, False)
    first = await stream_events(role, account, "token", 3)
    second = await stream_events(role, account, "token", 3)
    try:
        assert (first.status_code, second.status_code) == (200, 200)
        assert role.stats.streams_of("stu-03") == 2 and fp.event_open[3] == 2
        await first.background()
        await first.background()                                                         # повтор — ничего
        assert role.stats.streams_of("stu-03") == 1 and fp.event_open[3] == 1           # слот второго потока цел
        await second.background()
        assert role.stats.streams == 0 and fp.event_open[3] == 0
    finally:
        await role.workplaces.aclose()


def test_place_end_of_one_tab_keeps_other_tab_slot(tmp_path):                          # решение 3: finally + background
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    fp.events[3] = "endless"
    with serve_app(app) as url:
        cookie = login_cookie(url, "stu-03")
        c, s = open_stream(url, cookie)
        with s as r:
            first = r.iter_bytes()                                                       # вкладка держит поток
            next(first)
            fp.events[3] = [EV("server.connected")]                                      # вторая вкладка: место закроет сразу
            r2 = httpx.get(url + "/pcbk/api/oc/event", headers={"Cookie": f"pcbk_session={cookie}"})
            assert r2.status_code == 200 and r2.content == EV("server.connected")
            wait_until(lambda: fp.event_open[3] == 1, timeout=3)
            time.sleep(0.1)                                                              # второй вызов release — позади
            assert role.stats.streams_of("stu-03") == 1
        c.close()
        wait_until(lambda: role.stats.streams == 0, timeout=3)


def test_tab_race_after_place_answered(tmp_path):                                      # шаг 4: гонка вкладок
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    fp.events[3] = "endless"
    statuses, hold = [], threading.Event()

    def tab() -> None:
        with httpx.stream("GET", url + "/pcbk/api/oc/event", headers={"Cookie": f"pcbk_session={cookie}"},
                          timeout=5) as r:
            statuses.append(r.status_code)
            if r.status_code == 200:
                hold.wait(5)

    with serve_app(app) as url:
        cookie = login_cookie(url, "stu-03")
        tabs = [open_stream(url, cookie) for _ in range(2)]
        heads = [s.__enter__() for _, s in tabs]
        fp.hang_for[3] = 0.3                                                             # обе новые вкладки прошли шаг 1
        racers = [threading.Thread(target=tab) for _ in range(2)]
        for t in racers:
            t.start()
        wait_until(lambda: len(statuses) == 2, timeout=5)
        assert sorted(statuses) == [200, 429] and fp.event_starts[3] == 4
        wait_until(lambda: fp.event_open[3] == 3, timeout=3)                             # поток проигравшей закрыт
        assert role.stats.streams_of("stu-03") == MAX_STREAMS_PER_LOGIN
        hold.set()
        for t in racers:
            t.join(5)
        for (c, s), _ in zip(tabs, heads):
            s.__exit__(None, None, None); c.close()
        wait_until(lambda: role.stats.streams == 0 and fp.event_open[3] == 0, timeout=3)


async def test_race_loser_closes_place_stream_before_answer(tmp_path, monkeypatch):     # шаг 4: finally обработчика
    role = gateway_role(tmp_path, ready=(3,))
    fp = role.fake_places
    fp.events[3] = "endless"
    monkeypatch.setattr(role.stats, "stream_opened", lambda login: False)              # слоты заняли, пока место отвечало
    try:
        r = await stream_events(role, Account("stu-03", "student", 3, False), "token", 3)
        assert r.status_code == 429 and fp.event_starts[3] == 1
        assert fp.event_open[3] == 0                                                     # закрыт до ответа, не сборщиком
    finally:
        await role.workplaces.aclose()


def test_stream_neither_wakes_nor_touches(tmp_path, monkeypatch):                       # решение 5, Д5-R35
    app, role = make_gateway(tmp_path, ready=(3,))
    touched = []
    monkeypatch.setattr(role.workplaces, "touch", lambda n: touched.append(n))
    role.fake_places.events[3] = [EV("server.connected"), EV("session.idle")]
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        with c.stream("GET", "/pcbk/api/oc/event") as r:
            assert r.status_code == 200 and b"".join(r.iter_bytes()).count(b"data: ") == 2
        role.workplaces.mark_unready(3)
        r = c.get("/pcbk/api/oc/event")
        assert (r.status_code, r.json()["data"]["message"]) == (503, PROXY_MESSAGES["not_ready"])
    assert touched == [] and [x for x in role.fake_docker.calls if x[0] == "start"] == []
    assert role.fake_places.event_starts[3] == 1                                         # спящее место поток не открыл


# --- сверх брифа ---

async def test_filter_sse_limit_is_exact_and_split_terminators():
    head = b'data: {"type":"session.idle","properties":{"x":"'
    tail = b'"}}'
    exact = head + b"y" * (SSE_MAX_EVENT_BYTES - len(head) - len(tail)) + tail
    assert len(exact) == SSE_MAX_EVENT_BYTES
    assert await collect([exact[:100], exact[100:] + b"\n", b"\n"]) == [exact + b"\n\n"]    # ровно предел — проходит
    over = exact[:-len(tail)] + b"y" + tail
    with pytest.raises(ValueError):
        await collect([over + b"\n\n"])
    got = []
    with pytest.raises(ValueError):                                                     # события до большого — отданы
        async for event in filter_sse(aiter_of([EV("server.connected") + over + b"\n\n" + EV("session.idle")])):
            got.append(event)
    assert got == [EV("server.connected")]
    crlf = EV("session.idle").replace(b"\n\n", b"\r\n\r\n")
    assert await collect([crlf[:-3], crlf[-3:-1], crlf[-1:]]) == [crlf[:-4] + b"\n\n"]    # конец события на стыке кусков
    multi = b'data: {"type":\ndata: "session.idle"}\n\n'                                  # данные в двух строках data:
    assert await collect([multi]) == [multi]
    assert await collect([b": comment\n\n", b"event: x\n\n", b"data: [1]\n\n", b'data: {"type": 1}\n\n',
                          b"data: " + b"[" * 100000 + b"\n\n", b'data: {"type":"session.idle"']) == []


def test_oversize_event_breaks_stream(tmp_path, gwlog):                                  # Д5-R35
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    big = b"data: " + json.dumps({"type": "message.part.updated", "properties": {"x": "y" * SSE_MAX_EVENT_BYTES}}).encode()
    fp.events[3] = [EV("server.connected"), big + b"\n\n", EV("session.idle")]
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        with c.stream("GET", "/pcbk/api/oc/event") as r:
            body = b"".join(r.iter_bytes())
    assert body == EV("server.connected")
    assert role.stats.count_1h("stream_broken") == 1 and fp.event_open[3] == 0
    assert "closed_by=oversize" in gwlog.text and role.stats.streams == 0


def test_place_error_mid_stream_logged_by_class(tmp_path, gwlog):                        # Ш11
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    fp.events[3] = [EV("server.connected"), ("raise", httpx.ReadError("zqMARKqz basic b3BlbmNvZGU6"))]
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        with c.stream("GET", "/pcbk/api/oc/event") as r:
            body = b"".join(r.iter_bytes())
    text = gwlog.text
    assert body == EV("server.connected") and "closed_by=place" in text and "error=ReadError" in text
    assert "zqMARKqz" not in text and "b3BlbmNvZGU6" not in text
    assert role.stats.count_1h("stream_broken") == 0 and role.workplaces.is_ready(3) and fp.event_open[3] == 0


def test_place_gets_rebuilt_event_request(tmp_path):                                    # Д5-R32, Д5-R36
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        with c.stream("GET", "/pcbk/api/oc/event?directory=/etc&auth_token=b3BlbmNvZGU6eA==",
                      headers={"Authorization": "Basic Zm9vOmJhcg==", "Accept": "application/json",
                               "Last-Event-ID": "7"}) as r:
            b"".join(r.iter_bytes())
    [req] = [x for x in fp.requests if x.path == "/event"]
    assert (req.method, req.query) == ("GET", "")
    assert req.headers["authorization"] == role.workplaces.basic(3) and req.headers["accept"] == "text/event-stream"
    assert req.headers["accept-encoding"] == "identity"
    assert not {"cookie", "last-event-id", "origin", "x-opencode-directory"} & set(req.headers)


def test_event_open_answers_mapped(tmp_path, gwlog):                                    # Д5-R36 для потока
    app, role = make_gateway(tmp_path, ready=(3,))
    fp = role.fake_places
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        fp.force_status[3] = (500, {})                                                   # место ответило, но не 200
        r = c.get("/pcbk/api/oc/event")
        assert (r.status_code, r.json()) == (503, {"name": "WorkplaceNotReady",
                                                   "data": {"message": PROXY_MESSAGES["no_answer"]}})
        assert role.workplaces.is_ready(3) and role.stats.count_1h("upstream_failed") == 0
        fp.force_status[3] = (200, {"content-encoding": "gzip"})                         # сжатый поток не разобрать
        assert c.get("/pcbk/api/oc/event").status_code == 503
        fp.force_status.pop(3)
        role.workplaces.set_ready_for_tests(3)
        fp.raise_for[3] = httpx.PoolTimeout("x")                                         # пул места занят — место живо
        r = c.get("/pcbk/api/oc/event")
        assert (r.status_code, r.json()["name"]) == (503, "WorkplaceBusy") and role.workplaces.is_ready(3)
        fp.raise_for[3] = httpx.RemoteProtocolError("zqMARKqz")                          # Ш11: любая httpx.HTTPError
        r = c.get("/pcbk/api/oc/event")
        assert (r.status_code, r.json()["name"]) == (503, "WorkplaceNotReady") and not role.workplaces.is_ready(3)
        fp.raise_for.pop(3)
        role.workplaces.set_ready_for_tests(3)
        fp.force_status[3] = (401, {})
        r = c.get("/pcbk/api/oc/event")
        assert (r.status_code, r.json()["data"]["message"]) == (502, PROXY_MESSAGES["place_auth"])
        assert "www-authenticate" not in r.headers
    text = gwlog.text
    assert "rule=event status=503" in text and "place_status=500" in text and "error=RemoteProtocolError" in text
    assert "zqMARKqz" not in text
    assert role.stats.count_1h("place_auth_failed") == 1 and role.stats.streams == 0 and fp.event_open[3] == 0
    assert role.stats.count_1h("upstream_failed") == 2                                   # сжатый поток и обрыв


def test_teacher_has_no_stream(tmp_path):
    app, role = make_gateway(tmp_path, ready=(3,))
    with TestClient(app, base_url=BASE) as c:
        login(c, "teach")
        r = c.get("/pcbk/api/oc/event")
        assert (r.status_code, r.json()["data"]["message"]) == (403, PROXY_MESSAGES["teacher"])
    assert role.fake_places.event_starts.get(3, 0) == 0 and role.stats.streams == 0


def test_stream_constants():
    assert (SSE_MAX_EVENT_BYTES, STREAM_QUEUE_MAX, MAX_STREAMS_PER_LOGIN) == (512 * 1024, 1, 3)   # очередь — решение 4
    assert (places.SSE_READ_TIMEOUT_S, places.STREAM_OPEN_S) == (60.0, 10.0)
    assert proxy.PROXY_MESSAGES["streams"] == "Открыто слишком много вкладок чата — закройте лишние"


# --- доводка Б4 (решения контроллера по оговоркам) ---

def test_streams_refusals_rolled_per_minute(tmp_path, gwlog):                           # доводка 3: как not_ready
    app, role = make_gateway(tmp_path, ready=(3,))
    role.fake_places.events[3] = "endless"
    with serve_app(app) as url:
        cookie = login_cookie(url, "stu-03")
        opened = [open_stream(url, cookie) for _ in range(3)]
        heads = [s.__enter__() for _, s in opened]

        def extra() -> int:                                                              # поломка — не 200 без конца
            with httpx.stream("GET", url + "/pcbk/api/oc/event", headers={"Cookie": f"pcbk_session={cookie}"},
                              trust_env=False) as r:
                return r.status_code

        assert [extra() for _ in range(3)] == [429] * 3
        text = gwlog.text
        assert text.count("reason=streams") == 1
        assert "place=03 method=GET path=/event reason=streams status=429 n=1 since_s=0" in text
        role.mono.advance(proxy.NOT_READY_LOG_S)
        assert extra() == 429
        assert "reason=streams status=429 n=3 since_s=60" in gwlog.text                 # два несписанных и этот
        assert role.stats.count_1h("refused") == 0                                       # без счётчика, как not_ready
        for (c, s), _ in zip(opened, heads):
            s.__exit__(None, None, None); c.close()


def test_serve_stops_gracefully(monkeypatch, restore_logging):                          # доводка 2: параметр serve()
    from pcbk_core import egress
    got = {}
    monkeypatch.setattr(egress, "install_guard", lambda targets: None)                  # охрана — не в процессе pytest
    monkeypatch.setattr(egress, "start_refresh", lambda: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: got.update(kw))
    main.serve(SETTINGS)
    assert egress.egress_guard_installed() is False
    assert got["timeout_graceful_shutdown"] == main.GRACEFUL_SHUTDOWN_S <= 2.0
    assert got == main.uvicorn_options(SETTINGS)


class _LifespanMark(DummyRole):
    """Роль-метка: lifespan отмечает вход и выход."""

    def __init__(self):
        super().__init__("mark", (True, "ok"))
        self.entered = self.exited = False

    def lifespan(self):
        @asynccontextmanager
        async def span():
            self.entered = True
            try:
                yield
            finally:
                self.exited = True
        return span()


def test_open_stream_does_not_hold_shutdown(tmp_path):                                  # доводка 2: uvicorn, как serve()
    role = gateway_role(tmp_path, ready=(3,))
    fp = role.fake_places
    fp.events[3] = "endless"
    mark = _LifespanMark()
    app = create_app(SETTINGS, [role, mark])
    app.state.zone_names_ok = True
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="on", **main.uvicorn_options(SETTINGS)))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="serve-stop", daemon=True)
    thread.start()
    c = None
    try:
        wait_until(lambda: server.started, timeout=10)
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        c, s = open_stream(url, login_cookie(url, "stu-03"))
        r = s.__enter__()
        chunks = r.iter_bytes()
        next(chunks)
        assert role.stats.streams == 1 and fp.event_open[3] == 1
        t = time.monotonic()
        server.should_exit = True                                                        # как SIGTERM (handle_exit)
        thread.join(timeout=5)
        stopped = time.monotonic() - t
        assert not thread.is_alive() and stopped <= 3.0
        assert mark.exited and role._reaper is None                                      # lifespan ролей пройден
        assert role.stats.streams == 0 and fp.event_open[3] == 0                         # слот и поток места отданы
    finally:
        server.force_exit = True                                                         # поломка не вешает прогон
        thread.join(timeout=10)
        if c is not None:
            c.close()
        sock.close()
