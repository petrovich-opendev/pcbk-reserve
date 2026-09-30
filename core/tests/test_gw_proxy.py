"""Прокси вызовов адаптера /pcbk/api/oc/* к месту студента (Д5-R32…R37, задача Б3): порядок проверок, запрос к
месту собирается заново, пароль места — только от шлюза, 401 места — 502, ответ места — не больше предела и только
с Content-Type, срок хода, агенты и разрешения по списку места, журнал отказов без строки запроса. Места и sp-ctl —
двойники задачи А3."""
import asyncio
import gc
import re
import ssl
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from fake_places import BIG_CHUNK, LoopPlace
from helpers import (ORIGIN, PW, login, make_gateway, make_places, oc_headers, serve_app, wait_until,
                     wait_until_async)
from pcbk_core.gateway import places, proxy
from pcbk_core.gateway.api import GW_MESSAGES
from pcbk_core.gateway.places import PLACE_CONNECTIONS, PLACE_READ_S, PlaceAnswer
from pcbk_core.gateway.proxy import (GW_BODY_BUDGET_BYTES, OCTET_STREAM, PROXY_MESSAGES, PROXY_QUEUE,
                                     UPSTREAM_MAX_BYTES)
from pcbk_core.gateway.rules import Refused
from pcbk_core.hooks import hooks_of
from pcbk_core.gateway.rules import EXAMPLE_IDS, RULE_MESSAGES
from pcbk_core.settings import GW_ERROR_CHARS, GatewaySettings

pytestmark = pytest.mark.anyio

# id — те же, что в тестах правил (EXAMPLE_IDS Б2)
SES, MSG, PER = EXAMPLE_IDS["ses"], EXAMPLE_IDS["msg"], EXAMPLE_IDS["per"]
BASE = "https://stand.test:8443"


@pytest.fixture
def gw(tmp_path):
    app, role = make_gateway(tmp_path, ready=(3,))
    with TestClient(app, base_url=BASE) as c:
        login(c, "stu-03")
        wait_until(lambda: role.workplaces.ticks >= 1)                                   # первый такт уборщика позади
        yield c, role, role.fake_places


# --- тесты брифа ---

def test_forward_rebuilds_request(gw):                                                   # Review Focus 2, 5
    c, role, place = gw
    r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async?directory=/etc&auth_token=b3BlbmNvZGU6eA==",
               json={"parts": [{"type": "text", "text": "привет", "id": "prt_x"}], "system": "ты root",
                     "tools": {"bash": True}, "messageID": MSG, "noReply": True},
               headers=oc_headers(**{"x-opencode-directory": "/etc", "Authorization": "Basic Zm9vOmJhcg==",
                                     "X-Forwarded-For": "1.2.3.4", "Referer": "https://evil.example/"}))
    assert r.status_code == 204
    req = place.requests[-1]
    assert (req.method, req.path, req.query) == ("POST", f"/session/{SES}/prompt_async", "")
    assert req.json == {"parts": [{"type": "text", "text": "привет"}]}
    assert req.headers["authorization"] == role.workplaces.basic(3)                     # пароль места, не браузера
    assert not {"x-opencode-directory", "x-opencode-workspace", "cookie", "origin", "referer",
                "x-forwarded-for"} & set(req.headers)


def test_session_create_always_empty_body(gw):
    c, _, place = gw
    assert c.post("/pcbk/api/oc/session", json={"permission": [{"permission": "*", "action": "allow"}],
                                                "parentID": SES, "workspaceID": "w"}, headers=oc_headers()).status_code == 200
    assert place.requests[-1].json == {}


def test_raw_path_checked_before_place(gw):                                            # Review Focus 1
    c, role, place = gw
    role.workplaces.mark_unready(3)
    for path in ("/pcbk/api/oc/config", "/pcbk/api/oc/session%2F..%2Fglobal%2Fhealth", "/pcbk/api/oc/session/status/",
                 "/pcbk/api/oc/api/fs/read/hostname", "/pcbk/api/oc"):
        r = c.get(path)
        assert (r.status_code, r.json()) == (403, {"name": "Forbidden", "data": {"message": "Вызов не разрешён"}}), path
    r = c.post(f"/pcbk/api/oc/session/{SES}/message", json={"parts": [{"type": "text", "text": "x"}]}, headers=oc_headers())
    assert r.status_code == 403                                                          # близнец GET …/message
    r = c.get("/pcbk/api/oc/session/status")
    assert (r.status_code, r.json()["name"]) == (503, "WorkplaceNotReady")
    assert place.requests == [] and [x for x in role.fake_docker.calls if x[0] == "start"] == []   # не будит


def test_place_401_is_502_without_www_authenticate(gw):                                  # Review Focus 5
    c, role, place = gw
    place.force_status[3] = (401, {"www-authenticate": 'Basic realm="Secure Area"'})
    r = c.get("/pcbk/api/oc/session/status")
    assert (r.status_code, r.json()["name"]) == (502, "BadGateway") and "www-authenticate" not in r.headers
    assert role.stats.count_1h("place_auth_failed") == 1


def test_place_exception_not_logging_basic(gw, gwlog):                                  # Review Focus 5
    c, role, place = gw
    basic = role.workplaces.basic(3)
    place.raise_for[3] = httpx.ConnectError(f"boom {basic}")                           # даже если текст несёт заголовок
    r = c.get("/pcbk/api/oc/session/status")
    assert (r.status_code, r.json()["name"]) == (503, "WorkplaceNotReady")
    assert role.stats.count_1h("upstream_failed") == 1 and not role.workplaces.is_ready(3)
    b64 = basic.split()[1]
    assert b64 not in gwlog.text and "ConnectError" in gwlog.text
    role.workplaces.set_ready_for_tests(3)
    place.raise_for[3] = httpx.RemoteProtocolError(f"место упало посреди ответа {basic}")   # Ш11
    r = c.get("/pcbk/api/oc/session/status")
    assert (r.status_code, r.json()["name"]) == (503, "WorkplaceNotReady")            # не 500 и не HTML
    assert b64 not in gwlog.text and "RemoteProtocolError" in gwlog.text


def test_proxy_deadline(gw, monkeypatch):                                                # Review Focus 5
    c, role, place = gw
    monkeypatch.setattr(proxy, "PROXY_DEADLINE_S", 0.3)
    place.agents[3] = [{"name": "probe", "native": False, "mode": "primary"}]
    place.hang_for[3] = 5.0                                                              # место молчит на любом запросе
    t = time.monotonic()
    r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async",
               json={"parts": [{"type": "text", "text": "x"}], "agent": "probe"}, headers=oc_headers())
    assert (r.status_code, r.json()["data"]["message"]) == (503, PROXY_MESSAGES["no_answer"])
    assert time.monotonic() - t < 2


def test_summarize_answered_locally(gw):
    c, _, place = gw
    r = c.post(f"/pcbk/api/oc/session/{SES}/summarize", headers=oc_headers())
    assert (r.status_code, r.json()) == (200, True) and place.requests == []


def test_agent_only_nonnative_primary(gw):
    c, _, place = gw
    place.agents[3] = [{"name": "build", "native": True, "mode": "primary"},
                       {"name": "probe", "native": False, "mode": "primary"},
                       {"name": "helper", "native": False, "mode": "subagent"},
                       {"name": "vague", "mode": "primary"}]                             # native не задан — нельзя
    body = lambda a: {"parts": [{"type": "text", "text": "x"}], "agent": a}
    assert c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", json=body("probe"), headers=oc_headers()).status_code == 204
    assert place.requests[-1].json["agent"] == "probe"
    for name in ("build", "plan", "helper", "vague", "nope"):
        assert c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", json=body(name), headers=oc_headers()).status_code == 400


def test_permission_policy(gw):
    c, _, place = gw
    per2 = "per_" + "Z9" * 13
    place.pending[3] = [{"id": PER, "permission": "doom_loop"}, {"id": per2, "permission": "external_directory"}]
    reply = lambda pid, r: c.post(f"/pcbk/api/oc/permission/{pid}/reply", json={"reply": r, "message": "ок"},
                                  headers=oc_headers())
    assert reply(PER, "once").status_code == 200 and place.requests[-1].json == {"reply": "once"}
    assert reply(per2, "once").status_code == 403
    assert reply(per2, "always").status_code == 400
    assert reply(per2, "reject").status_code == 200 and place.requests[-1].json == {"reply": "reject"}


def test_teacher_origin_and_zone(gw):
    c, role, _ = gw
    assert c.post("/pcbk/api/oc/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert (role.stats.count_1h("refused"), role.stats.count_1h("origin_refused")) == (1, 0)   # у прокси — refused
    assert c.get("/pcbk/api/oc/session/status", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403   # р4
    assert c.get("/pcbk/api/oc/session/status", headers={"Sec-Fetch-Site": "none"}).status_code == 200
    r = c.post("/pcbk/api/oc/session", content=b"x" * (524288 + 1),
               headers=oc_headers(**{"Content-Type": "application/json"}))
    assert (r.status_code, r.json()["name"]) == (413, "PayloadTooLarge")              # refusal= у body_limit
    # доводка G6: клиент из сети места — без with (второй lifespan той же роли не нужен), с действующей cookie
    session = {"Cookie": f"pcbk_session={c.cookies.get('pcbk_session')}", "Origin": ORIGIN}
    place_side = TestClient(c.app, base_url="http://172.31.3.2:8000")
    for method, path in (("GET", "/pcbk/api/oc/session/status"), ("POST", "/pcbk/api/oc/session"),
                         ("GET", "/pcbk/api/oc/event"), ("GET", "/pcbk/api/oc"), ("GET", "/pcbk/api/oc/config")):
        r = place_side.request(method, path, headers=session)
        assert (r.status_code, r.json()["name"]) == (404, "NotFound"), path            # из сети места — не видно
    assert c.get("/pcbk/api/oc/session/status").status_code == 200                     # сессия жива
    login(c, "teach")
    assert c.get("/pcbk/api/oc/session/status").status_code == 403


def test_activity_only_for_student_actions(gw):
    c, role, _ = gw
    role.mono.advance(100)
    c.get("/pcbk/api/oc/session/status")
    assert role.workplaces.idle_s(3) >= 100                                              # GET адаптера — не активность
    c.post(f"/pcbk/api/oc/session/{SES}/abort", headers=oc_headers())
    assert role.workplaces.idle_s(3) < 1


def test_refusal_log_has_no_query(gw, gwlog):                                           # Review Focus 5
    c, role, _ = gw
    c.get("/pcbk/api/oc/config?auth_token=U0VDUkVUVkFMVUU=")
    assert "gw refused" in gwlog.text and "path=/config" in gwlog.text
    assert "U0VDUkVUVkFMVUU" not in gwlog.text and "auth_token" not in gwlog.text
    assert role.stats.count_1h("refused") == 1


def test_not_ready_logged_once_per_minute(gw, gwlog):
    c, role, _ = gw
    role.workplaces.mark_unready(3)
    for _ in range(30):
        c.get("/pcbk/api/oc/session/status")
    assert gwlog.text.count("reason=not_ready") == 1


# --- решения контроллера для Б3 ---

def test_oc_body_limit_by_zone(tmp_path):                                               # решение 1: respond= по зоне
    app, _ = make_gateway(tmp_path, ready=(3,))
    big = {"content": b"x" * (524288 + 1), "headers": {"Origin": ORIGIN, "Content-Type": "application/json"}}
    url = f"/pcbk/api/oc/session/{SES}/prompt_async"
    with TestClient(app, base_url="http://172.31.6.2:8000") as place:                  # адрес core в сети места 06
        r = place.post(url, **big)
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")                 # не 413: ручка не выдана
    with TestClient(app, base_url="http://172.31.250.34:8000") as front:               # адрес core в pcbk-front
        r = front.post(url, **big)
        assert (r.status_code, r.json()) == (413, {"name": "PayloadTooLarge",
                                                   "data": {"message": "Запрос слишком большой"}})


def test_oc_body_limit_is_512k_not_64k(gw):                                             # Д5-R36: 512 КиБ у /pcbk/api/oc/
    c, _, place = gw
    parts = [{"type": "text", "text": "x" * 20000} for _ in range(4)]                   # ~80 КБ — больше 64 КиБ
    r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", json={"parts": parts}, headers=oc_headers())
    assert r.status_code == 204 and place.requests[-1].json == {"parts": parts}
    assert c.post("/pcbk/api/auth/login", content=b"x" * 70000,
                  headers={"Origin": ORIGIN}).status_code == 413                         # у входа — прежние 64 КиБ


def test_oc_body_limit_from_settings(tmp_path):
    app, _ = make_gateway(tmp_path, ready=(3,), GW_MAX_BODY_BYTES=65536)
    with TestClient(app, base_url=BASE) as c:
        r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", content=b"x" * 70000, headers=oc_headers())
        assert (r.status_code, r.json()["name"]) == (413, "PayloadTooLarge")


def test_gw_max_body_setting():
    assert GatewaySettings().GW_MAX_BODY_BYTES == 524288
    assert GatewaySettings.from_env({"GW_MAX_BODY_BYTES": "65536"}).GW_MAX_BODY_BYTES == 65536
    for bad in ("65535", "abc", "1e6", "-1", "524288.5", "0x10000"):
        with pytest.raises(ValueError) as e:
            GatewaySettings.from_env({"GW_MAX_BODY_BYTES": bad})
        text = str(e.value)
        assert text.startswith("GW_MAX_BODY_BYTES") and bad not in text and len(text) <= GW_ERROR_CHARS, bad


def test_raw_prefix_checked_not_decoded(gw, gwlog):                                     # решение 2
    c, role, place = gw
    for path in ("/pcbk/api/o%63/session/status", "/pcbk/api/%6Fc/session/status", "/pcbk/api/oc%2Fsession/status",
                 "/pcbk/api/oc%2Fsession%2Fstatus"):
        r = c.get(path)                                                                  # Starlette ведёт по раскодированному
        assert (r.status_code, r.json()["data"]["message"]) == (403, "Вызов не разрешён"), path
    assert place.requests == [] and gwlog.text.count("reason=bad_path") == 4


def test_raw_rest_requires_literal_prefix():                                            # решение 2: не срез по длине
    rest = lambda raw: proxy._raw_rest({"raw_path": raw})
    assert (rest(b"/pcbk/api/oc"), rest(b"/pcbk/api/oc/session/status?x=1")) == ("", "/session/status")
    for raw in (b"/pcbk/api/o%63/session/status", b"/pcbk/api/%6Fc/x", b"/pcbk/api/ocx/session", b"/pcbk/api/oc\xff",
                b"/pcbk/api/oc/\xd0\xb0", "/pcbk/api/oc/session", None):
        assert rest(raw) is None, raw                                                    # срез дал бы «63/session/…»
    assert proxy._raw_rest({}) is None


def test_bad_json_body_is_400(gw, gwlog):                                               # решение 3
    c, role, place = gw
    url = f"/pcbk/api/oc/session/{SES}/prompt_async"
    bodies = (b"1" * 5000,                                                               # целое длиннее 4300 цифр
              b"[" * 100000,                                                             # RecursionError
              b'{"parts": [{"type": "text", "text": "\xff"}]}',                          # не UTF-8
              b"\xff\xfe\x00",                                                           # BOM UTF-16, обрыв
              b"{",
              b'{"parts": [{"type": "text", "text": "\\ud800"}]}')                       # одиночный суррогат
    for raw in bodies:
        r = c.post(url, content=raw, headers=oc_headers(**{"Content-Type": "application/json"}))
        assert (r.status_code, r.json()["name"]) == (400, "BadRequest"), raw[:20]
        message = r.json()["data"]["message"]
        assert message in RULE_MESSAGES.values() and not re.search(r"[0-9]", message), raw[:20]
    assert place.requests == [] and "4300" not in gwlog.text and "Traceback" not in gwlog.text
    assert role.stats.count_1h("refused") == len(bodies)


def test_messages_shared_with_rules():                                                   # решение 4
    assert PROXY_MESSAGES["not_whitelisted"] is RULE_MESSAGES["not_whitelisted"]
    assert PROXY_MESSAGES == {
        "not_whitelisted": "Вызов не разрешён",
        "teacher": "Просмотр разговоров студентов — в следующих выпусках стенда",
        "not_ready": "Рабочее место спит — разбудите его на странице входа",
        "no_answer": "Рабочее место не отвечает — разбудите его на странице входа",
        "place_auth": "Рабочее место не приняло пароль шлюза — сообщите администратору",
        "agent": "Агент «{agent}» недоступен",
        "permission": "Это разрешение даёт только преподаватель",
        "too_big": "Ответ рабочего места слишком большой",
        "streams": "Открыто слишком много вкладок чата — закройте лишние",
        "busy": "Слишком много одновременных запросов — повторите позже",
    }


def test_agent_name_in_answer_only_after_check(gw, gwlog):                             # решение 5 (Ш12)
    c, role, place = gw
    place.agents[3] = [{"name": "probe", "native": False, "mode": "primary"}]
    url = f"/pcbk/api/oc/session/{SES}/prompt_async"
    body = lambda a: {"parts": [{"type": "text", "text": "x"}], "agent": a}
    r = c.post(url, json=body("nope-agent"), headers=oc_headers())
    assert (r.status_code, r.json()) == (400, {"name": "BadRequest", "data": {"message": "Агент «nope-agent» недоступен"}})
    for bad in ("x y", "a" * 65, "агент", "zqMARKqz{agent}"):
        r = c.post(url, json=body(bad), headers=oc_headers())
        assert (r.status_code, r.json()["data"]["message"]) == (400, RULE_MESSAGES["agent_name"]), bad
    text = gwlog.text
    assert "reason=agent status=400" in text and "nope-agent" not in text and "zqMARKqz" not in text
    assert [x.path for x in place.requests] == ["/agent"]                                # имя не по шаблону — к месту ни шагу
    assert role.stats.count_1h("refused") == 5


def test_place_answer_cut_at_limit(gw):                                                 # решение 7
    c, role, place = gw
    place.big[3] = 3 * UPSTREAM_MAX_BYTES
    r = c.get(f"/pcbk/api/oc/session/{SES}/message")
    assert (r.status_code, r.json()) == (502, {"name": "BadGateway", "data": {"message": PROXY_MESSAGES["too_big"]}})
    assert UPSTREAM_MAX_BYTES < place.big_sent[3] <= UPSTREAM_MAX_BYTES + BIG_CHUNK      # дальше предела + 1 КиБ — нет
    assert role.workplaces.is_ready(3)
    place.big[3] = UPSTREAM_MAX_BYTES                                                   # ровно предел — проходит
    r = c.get(f"/pcbk/api/oc/session/{SES}/message")
    assert r.status_code == 200 and len(r.content) == UPSTREAM_MAX_BYTES
    del place.big[3]
    place.force_status[3] = (200, {"content-length": str(UPSTREAM_MAX_BYTES + 1)})     # объявленная длина — без чтения
    r = c.get(f"/pcbk/api/oc/session/{SES}/message")
    assert (r.status_code, r.json()["data"]["message"]) == (502, PROXY_MESSAGES["too_big"])


# --- мутации и сверх брифа ---

def test_zone_config_login_before_rules(tmp_path, gwlog):                               # Д5-R32: шаги 1–3 до правила
    app, role = make_gateway(tmp_path, ready=(3,))
    with TestClient(app, base_url=BASE) as c:
        for path in ("/pcbk/api/oc/config", "/pcbk/api/oc/session/status", "/pcbk/api/oc/session%2F.."):
            r = c.get(path)
            assert (r.status_code, r.json()["name"]) == (401, "Unauthorized"), path    # без входа правила не видны
    assert role.stats.count_1h("refused") == 0 and "gw refused" not in gwlog.text and role.fake_places.requests == []
    app, _ = make_gateway(tmp_path / "cfg", ready=(3,), config_error="нет каталога паролей")
    with TestClient(app, base_url=BASE) as c:
        r = c.get("/pcbk/api/oc/config")
        assert (r.status_code, r.json()["name"]) == (503, "Unavailable")              # config_error — до входа


def test_touch_before_forward(gw):                                                      # мутация «touch после пересылки»
    c, role, place = gw
    role.mono.advance(100)
    r = c.post(f"/pcbk/api/oc/session/{SES}/message", json={"parts": [{"type": "text", "text": "x"}]},
               headers=oc_headers())
    assert r.status_code == 403 and role.workplaces.idle_s(3) >= 100                   # отказ правил — не активность
    place.raise_for[3] = httpx.ConnectError("место упало")
    r = c.post(f"/pcbk/api/oc/session/{SES}/abort", headers=oc_headers())
    assert r.status_code == 503 and role.workplaces.idle_s(3) < 1                      # активность — до пересылки


def test_only_content_type_from_place(gw):                                              # мутация «заголовки места проходят»
    c, _, place = gw
    place.force_status[3] = (200, {"content-type": "application/json; charset=utf-8", "set-cookie": "pcbk_session=x",
                                   "x-opencode-directory": "/etc", "www-authenticate": "Basic",
                                   "access-control-allow-origin": "*"})
    r = c.get("/pcbk/api/oc/session/status")
    assert (r.status_code, r.json()) == (200, {"name": "Forced"})
    assert set(r.headers) == {"content-type", "content-length"}
    assert r.headers["content-type"] == "application/json; charset=utf-8"


def test_place_status_and_body_pass_as_is(gw):
    c, role, place = gw
    place.force_status[3] = (500, {"content-type": "application/json"})
    r = c.get(f"/pcbk/api/oc/session/{SES}")
    assert (r.status_code, r.json()) == (500, {"name": "Forced"}) and role.workplaces.is_ready(3)


def test_query_only_rule_keys(gw):
    c, _, place = gw
    r = c.get("/pcbk/api/oc/experimental/session?directory=/etc&roots=true&auth_token=x&archived=false&roots=false"
              "&workspace=w")
    assert r.status_code == 200 and place.requests[-1].query == "roots=true&archived=false"


def test_not_ready_counts_nothing_and_rolls_per_minute(gw, gwlog):                     # Д5-R36, Д5-R37
    c, role, _ = gw
    role.workplaces.mark_unready(3)
    for _ in range(5):
        r = c.get("/pcbk/api/oc/session/status")
    assert (r.status_code, r.json()) == (503, {"name": "WorkplaceNotReady",
                                               "data": {"message": PROXY_MESSAGES["not_ready"]}})
    role.mono.advance(60)
    c.get("/pcbk/api/oc/session/status")
    text = gwlog.text
    assert text.count("reason=not_ready") == 2 and "reason=not_ready status=503 n=1 since_s=0" in text
    assert "reason=not_ready status=503 n=5 since_s=60" in text                        # G8: сколько копились
    role.mono.advance(1)
    c.get("/pcbk/api/oc/session/status")                                                 # свёрнут
    role.mono.advance(3600)
    c.get("/pcbk/api/oc/session/status")
    assert "reason=not_ready status=503 n=2 since_s=3600" in gwlog.text                 # старое число — с давностью
    assert (role.stats.count_1h("refused"), role.stats.count_1h("upstream_failed")) == (0, 0)


def test_odd_place_lists_refuse_not_crash(gw):                                          # список места — не доверие
    c, _, place = gw
    place.agents[3] = [{"name": "probe", "native": False, "mode": ["primary"]}, "probe",
                       {"name": ["probe"], "native": False, "mode": "primary"},
                       {"name": "probe", "native": 0, "mode": "primary"}]
    body = {"parts": [{"type": "text", "text": "x"}], "agent": "probe"}
    assert c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", json=body, headers=oc_headers()).status_code == 400
    place.agents[3] = {"probe": {"native": False, "mode": "primary"}}                  # не список
    assert c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", json=body, headers=oc_headers()).status_code == 400
    place.pending[3] = [{"id": PER, "permission": ["doom_loop"]}, {"id": [PER], "permission": "doom_loop"}, "x"]
    r = c.post(f"/pcbk/api/oc/permission/{PER}/reply", json={"reply": "once"}, headers=oc_headers())
    assert (r.status_code, r.json()["data"]["message"]) == (403, PROXY_MESSAGES["permission"])
    assert all(x.method == "GET" for x in place.requests)                                # пересылки не было


def test_place_401_on_check_is_502(gw):
    c, role, place = gw
    place.force_status[3] = (401, {})
    r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", json={"parts": [{"type": "text", "text": "x"}],
                                                                 "agent": "probe"}, headers=oc_headers())
    assert (r.status_code, r.json()["data"]["message"]) == (502, PROXY_MESSAGES["place_auth"])
    r = c.post(f"/pcbk/api/oc/permission/{PER}/reply", json={"reply": "once"}, headers=oc_headers())
    assert r.status_code == 502 and role.stats.count_1h("place_auth_failed") == 2
    assert [x.path for x in place.requests] == ["/agent", "/permission"]                 # пересылки не было


def test_place_asked_not_to_compress(gw):
    c, role, place = gw
    assert c.get("/pcbk/api/oc/session/status").status_code == 200
    assert place.requests[-1].headers["accept-encoding"] == "identity"                 # OpenCode сжимает лишь по просьбе
    place.force_status[3] = (200, {"content-encoding": "gzip", "content-type": "application/json"})
    r = c.get("/pcbk/api/oc/session/status")
    assert (r.status_code, r.json()["name"]) == (503, "WorkplaceNotReady")            # размер сжатого не узнать
    assert role.stats.count_1h("upstream_failed") == 1 and not role.workplaces.is_ready(3)


def test_missing_place_password_is_no_answer(gw, tmp_path):
    c, role, _ = gw
    (tmp_path / "place-secrets" / "student-03.pw").unlink()
    r = c.get("/pcbk/api/oc/session/status")
    assert (r.status_code, r.json()["data"]["message"]) == (503, PROXY_MESSAGES["no_answer"])
    assert role.stats.count_1h("upstream_failed") == 1


def test_other_methods_refused(gw):
    c, _, place = gw
    for method in ("PUT", "DELETE", "OPTIONS"):
        r = c.request(method, f"/pcbk/api/oc/session/{SES}", headers=oc_headers())
        assert (r.status_code, r.json()["name"]) == (403, "Forbidden"), method
    r = c.request("TRACE", f"/pcbk/api/oc/session/{SES}")
    assert (r.status_code, r.json()["name"]) == (404, "NotFound")                     # вне METHODS — как нет ручки
    assert place.requests == []


def test_forward_log_line(gw, gwlog):
    c, _, _ = gw
    assert c.get("/pcbk/api/oc/session/status?auth_token=U0VDUkVUVkFMVUU=").status_code == 200
    text = gwlog.text
    assert re.search(r"gw oc login=stu-03 place=03 rule=session_status status=200 ms=[0-9]+", text)
    assert "U0VDUkVUVkFMVUU" not in text and "auth_token" not in text


# --- доводка 1 (ревью A и B) ---

STATUS = "/pcbk/api/oc/session/status"


async def cookie_of(client: httpx.AsyncClient, name: str) -> dict:
    """Вход по HTTP (serve_app): заголовок Cookie с pcbk_session. Сам клиент cookie Secure по http не шлёт."""
    r = await client.post("/pcbk/api/auth/login", json={"login": name, "password": PW}, headers={"Origin": ORIGIN})
    assert r.status_code == 200
    line = next(h for h in r.headers.get_list("set-cookie") if h.startswith("pcbk_session="))
    return {"Cookie": line.split(";", 1)[0]}


async def loop_places(monkeypatch, hold: asyncio.Event) -> tuple[LoopPlace, LoopPlace]:
    """Места 3 (ждёт hold) и 5 на петле: 127.0.3.3 и 127.0.5.3 на одном порту (PLACE_PORT подменён)."""
    p3, p5 = LoopPlace(hold), LoopPlace()
    port = await p3.start("127.0.3.3")
    await p5.start("127.0.5.3", port)
    monkeypatch.setattr(places, "PLACE_PORT", port)
    return p3, p5


async def test_flood_of_one_place_spares_neighbour(tmp_path, monkeypatch):             # G1 (проба A-P2)
    hold = asyncio.Event()
    p3, p5 = await loop_places(monkeypatch, hold)
    app, role = make_gateway(tmp_path, ready=(3, 5), STU_NET="127.0")
    role.workplaces._transport = None                                                    # настоящие сокеты
    try:
        with serve_app(app) as url:
            wait_until(lambda: role.workplaces.ticks >= 1)
            async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=10) as client:
                c3, c5 = await cookie_of(client, "stu-03"), await cookie_of(client, "stu-05")
                flood = [asyncio.create_task(client.get(STATUS, headers=c3)) for _ in range(20)]
                try:
                    await wait_until_async(lambda: sum(t.done() for t in flood) >= 20 - PROXY_QUEUE - 2
                                           and p3.active == 2)
                    t = time.monotonic()
                    r5 = await client.get(STATUS, headers=c5)
                    assert r5.status_code == 200 and time.monotonic() - t < 1            # соседу потоп не мешает
                finally:
                    hold.set()                                                           # uvicorn не ждёт висящих
                    answers = await asyncio.gather(*flood)
    finally:
        await p3.close()
        await p5.close()
    assert sorted(r.status_code for r in answers) == [200] * (PROXY_QUEUE + 2) + [503] * (20 - PROXY_QUEUE - 2)
    assert {r.json()["data"]["message"] for r in answers if r.status_code == 503} == {PROXY_MESSAGES["busy"]}
    assert {r.json()["name"] for r in answers if r.status_code == 503} == {"WorkplaceBusy"}
    assert p3.max_active == 2 and role.workplaces.is_ready(3) and role.workplaces.is_ready(5)
    assert (role.stats.count_1h("upstream_failed"), role.stats.count_1h("refused")) == (0, 20 - PROXY_QUEUE - 2)


async def test_place_clients_have_own_pools(tmp_path, monkeypatch):                     # G1: пул — свой у места
    hold = asyncio.Event()
    p3, p5 = await loop_places(monkeypatch, hold)
    w = make_places(tmp_path, STU_NET="127.0")
    w._transport = None
    stuck = [asyncio.create_task(w.fetch(3, "GET", "/session/status", max_bytes=1024)) for _ in range(120)]
    try:
        await wait_until_async(lambda: p3.conns >= PLACE_CONNECTIONS)
        t = time.monotonic()
        answer = await w.fetch(5, "GET", "/session/status", max_bytes=1024)             # общий пул в 100 был бы занят
        assert answer.status == 200 and time.monotonic() - t < 2
        assert p3.max_conns == PLACE_CONNECTIONS                                         # больше пул места не открывает
    finally:
        hold.set()
        done = await asyncio.gather(*stuck, return_exceptions=True)
        await w.aclose()
        await p3.close()
        await p5.close()
    assert all(isinstance(d, PlaceAnswer) and d.status == 200 for d in done)


async def test_queue_past_deadline_is_busy_not_silence(tmp_path, monkeypatch):         # G1: не дождался очереди
    app, role = make_gateway(tmp_path, ready=(3,))
    place = role.fake_places
    place.hang_for[3] = 1.0
    with serve_app(app) as url:
        wait_until(lambda: role.workplaces.ticks >= 1)
        async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=10) as client:
            c3 = await cookie_of(client, "stu-03")
            first = [asyncio.create_task(client.get(STATUS, headers=c3)) for _ in range(2)]
            await wait_until_async(lambda: place.active[3] == 2)
            monkeypatch.setattr(proxy, "PROXY_DEADLINE_S", 0.3)                         # третьему — короткий срок
            t = time.monotonic()
            r = await client.get(STATUS, headers=c3)
            waited = time.monotonic() - t
            answers = await asyncio.gather(*first)
    assert (r.status_code, r.json()["name"]) == (503, "WorkplaceBusy") and 0.25 < waited < 0.9
    assert [a.status_code for a in answers] == [200, 200] and place.max_active[3] == 2
    assert role.workplaces.is_ready(3)
    assert (role.stats.count_1h("upstream_failed"), role.stats.count_1h("refused")) == (0, 1)


async def test_slot_held_until_answer_sent(tmp_path, monkeypatch):                      # G1: ответ в памяти — со слотом
    app, role = make_gateway(tmp_path, ready=(3,))
    place, sent, got = role.fake_places, asyncio.Event(), []

    async def call(send) -> None:                                                        # ASGI напрямую: отправкой правит тест
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": STATUS, "raw_path": STATUS.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"127.0.0.1:8000"), (b"cookie", f"pcbk_session={token}".encode())],
                 "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 40000)}

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}
        await app(scope, receive, send)

    async def slow(message: dict) -> None:                                               # браузер не забирает тело
        if message["type"] == "http.response.body":
            await sent.wait()

    async def keep(message: dict) -> None:
        got.append(message)

    async with role.lifespan():
        token = await asyncio.to_thread(role.store.open_session, "stu-03")
        slow_calls = [asyncio.create_task(call(slow)) for _ in range(2)]
        await wait_until_async(lambda: len(place.requests) == 2)                         # ответы места уже получены
        monkeypatch.setattr(proxy, "PROXY_DEADLINE_S", 0.3)
        await call(keep)
        assert got[0]["status"] == 503 and b"WorkplaceBusy" in got[1]["body"] and len(place.requests) == 2
        sent.set()
        await asyncio.gather(*slow_calls)
        got.clear()
        await call(keep)
        assert got[0]["status"] == 200                                                   # отправка вернула слоты


async def test_place_response_always_releases_slot():                                   # G1: слот не теряется
    released = []
    r = proxy._PlaceResponse(b"{}", 200, None, lambda: released.append("send"))

    async def gone(message: dict) -> None:
        raise OSError("браузер ушёл")
    with pytest.raises(OSError):
        await r({"type": "http"}, None, gone)
    r2 = proxy._PlaceResponse(b"{}", 200, None, lambda: released.append("gc"))
    del r2                                                                               # не отправлялся вовсе
    gc.collect()
    assert released == ["send", "gc"]


async def test_place_answer_sent_in_chunks():                                           # G1: буфер сокета не копит тело
    body, sent, released = bytearray(b"x" * (3 * proxy.SEND_CHUNK + 1)), [], []
    r = proxy._PlaceResponse(body, 200, {"content-type": "application/json"}, lambda: released.append(len(sent)))

    async def send(message: dict) -> None:
        sent.append(message)
    await r({"type": "http"}, None, send)
    chunks = [m["body"] for m in sent[1:]]
    assert sent[0]["status"] == 200 and (b"content-length", str(len(body)).encode()) in sent[0]["headers"]
    assert [len(c) for c in chunks] == [proxy.SEND_CHUNK] * 3 + [1, 0] and b"".join(chunks) == body
    assert [m["more_body"] for m in sent[1:]] == [True] * 4 + [False] and released == [len(sent)]   # слот — в конце


def test_pool_timeout_is_busy(gw, gwlog):                                                # G1
    c, role, place = gw
    place.raise_for[3] = httpx.PoolTimeout("пул места занят")
    r = c.get(STATUS)
    assert (r.status_code, r.json()) == (503, {"name": "WorkplaceBusy", "data": {"message": PROXY_MESSAGES["busy"]}})
    assert role.workplaces.is_ready(3) and "reason=busy status=503" in gwlog.text
    assert (role.stats.count_1h("upstream_failed"), role.stats.count_1h("refused")) == (0, 1)


def test_deadline_spans_check_and_forward(gw, monkeypatch):                              # G2: срок — на весь ход
    c, role, place = gw
    monkeypatch.setattr(proxy, "PROXY_DEADLINE_S", 0.6)
    place.agents[3] = [{"name": "probe", "native": False, "mode": "primary"}]
    place.hang_for[3] = 0.4                                                              # каждый шаг — меньше срока
    r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async",
               json={"parts": [{"type": "text", "text": "x"}], "agent": "probe"}, headers=oc_headers())
    assert (r.status_code, r.json()["data"]["message"]) == (503, PROXY_MESSAGES["no_answer"])
    assert [x.path for x in place.requests] == ["/agent", f"/session/{SES}/prompt_async"]
    assert role.stats.count_1h("upstream_failed") == 1 and not role.workplaces.is_ready(3)


def test_counters_exact_on_limit_and_auth(gw):                                          # G2: счётчики точны
    c, role, place = gw
    url = f"/pcbk/api/oc/session/{SES}/message"
    place.force_status[3] = (200, {"content-length": str(UPSTREAM_MAX_BYTES)})
    assert c.get(url).status_code == 200                                                 # ровно предел — проходит
    place.force_status[3] = (200, {"content-length": str(UPSTREAM_MAX_BYTES + 1)})
    r = c.get(url)
    assert (r.status_code, r.json()["data"]["message"]) == (502, PROXY_MESSAGES["too_big"])
    place.force_status[3] = (401, {})
    r = c.get(url)
    assert (r.status_code, r.json()["data"]["message"]) == (502, PROXY_MESSAGES["place_auth"])
    assert (role.stats.count_1h("upstream_failed"), role.stats.count_1h("refused"),
            role.stats.count_1h("place_auth_failed")) == (0, 0, 1)
    assert role.workplaces.is_ready(3)


def test_place_cookies_not_returned(gw):                                                 # G3
    c, _, place = gw
    place.force_status[3] = (200, {"set-cookie": "sid=place-secret; Path=/"})
    r = c.get(STATUS)
    assert r.status_code == 200 and "set-cookie" not in r.headers and "sid" not in c.cookies
    del place.force_status[3]
    assert c.get(STATUS).status_code == 200
    assert "cookie" not in place.requests[-1].headers                                    # месту не вернулась


def test_place_redirect_not_followed(gw):                                                # G4
    c, _, place = gw
    for target in ("http://172.31.5.3:4096/session/status",                              # чужое место
                   "http://pcbk-sp-ctl:2375/v1.44/containers/pcbk-student-05/start"):    # прокси сокета
        place.force_status[3] = (307, {"location": target})
        r = c.get(STATUS, follow_redirects=False)
        assert r.status_code == 307 and "location" not in r.headers, target
    assert {x.url.host for x in place.requests} == {"172.31.3.3"} and len(place.requests) == 2


def test_post_without_origin_refused(gw):                                                # G5
    c, role, place = gw
    r = c.post(f"/pcbk/api/oc/session/{SES}/abort")                                     # ни Origin, ни Sec-Fetch-Site
    assert (r.status_code, r.json()) == (403, {"name": "Forbidden", "data": {"message": GW_MESSAGES["origin"]}})
    assert role.stats.count_1h("refused") == 1 and place.requests == []


def test_place_content_type_narrowed(gw):                                                # G7
    c, _, place = gw
    cases = {"application/json": "application/json",
             "application/json; charset=utf-8": "application/json; charset=utf-8",
             "text/plain;charset=utf-8": "text/plain;charset=utf-8", "Application/JSON": "Application/JSON",
             "text/html; charset=utf-8": OCTET_STREAM, "image/svg+xml": OCTET_STREAM,
             "text/event-stream": OCTET_STREAM, "application/jsonp": OCTET_STREAM,
             "application/json-patch+json": OCTET_STREAM, "text/xml": OCTET_STREAM}
    for given, shown in cases.items():
        place.force_status[3] = (200, {"content-type": given})
        r = c.get(STATUS)
        assert (r.status_code, r.headers["content-type"], r.content) == (200, shown, b'{"name":"Forced"}'), given
    del place.force_status[3]
    r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", json={"parts": [{"type": "text", "text": "x"}]},
               headers=oc_headers())
    assert r.status_code == 204 and "content-type" not in r.headers                      # нет — и не придумывается


def test_not_ready_rolled_per_place(gw, gwlog):                                          # G9
    c, role, _ = gw
    role.workplaces.mark_unready(3)
    c.get(STATUS)
    login(c, "stu-05")                                                                   # место 5 не готово
    c.get(STATUS)
    text = gwlog.text
    assert text.count("reason=not_ready") == 2
    assert "place=03 method=GET path=/session/status reason=not_ready" in text
    assert "place=05 method=GET path=/session/status reason=not_ready" in text


def test_bad_body_is_not_activity(gw):                                                   # G9
    c, role, place = gw
    role.mono.advance(100)
    r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async", content=b"{", headers=oc_headers())
    assert r.status_code == 400 and role.workplaces.idle_s(3) >= 100 and place.requests == []


def test_oc_lines_for_mapped_answers(gw, gwlog):                                         # G9
    c, _, place = gw
    assert c.post(f"/pcbk/api/oc/session/{SES}/summarize", headers=oc_headers()).status_code == 200
    place.force_status[3] = (200, {"content-length": str(UPSTREAM_MAX_BYTES + 1)})
    assert c.get(STATUS).status_code == 502
    place.force_status[3] = (401, {})
    assert c.get(STATUS).status_code == 502
    text = gwlog.text
    assert re.search(r"gw oc login=stu-03 place=03 rule=summarize status=200 ms=[0-9]+", text)
    assert re.search(r"gw oc login=stu-03 place=03 rule=session_status status=502 ms=[0-9]+ reason=too_big", text)
    assert re.search(r"gw oc login=stu-03 place=03 rule=session_status status=502 ms=[0-9]+ reason=place_auth", text)


def test_check_place_status_logged(gw, gwlog):                                          # G10
    c, _, place = gw
    place.check_status[3] = 500
    r = c.post(f"/pcbk/api/oc/session/{SES}/prompt_async",
               json={"parts": [{"type": "text", "text": "x"}], "agent": "probe"}, headers=oc_headers())
    assert (r.status_code, r.json()["data"]["message"]) == (400, "Агент «probe» недоступен")    # текст прежний
    r = c.post(f"/pcbk/api/oc/permission/{PER}/reply", json={"reply": "once"}, headers=oc_headers())
    assert (r.status_code, r.json()["data"]["message"]) == (403, PROXY_MESSAGES["permission"])
    del place.check_status[3]
    c.post(f"/pcbk/api/oc/session/{SES}/prompt_async",
           json={"parts": [{"type": "text", "text": "x"}], "agent": "probe"}, headers=oc_headers())
    lines = [line for line in gwlog.text.splitlines() if "gw refused" in line]
    assert lines[0].endswith("reason=agent status=400 place_status=500")
    assert lines[1].endswith("reason=permission status=403 place_status=500")
    assert lines[2].endswith("reason=agent status=400")                                  # 200 места — без кода


# --- доводка 2 (повторное ревью) ---

def test_queue_wait_leaves_place_its_read_time():                                        # H1: соотношение сроков
    assert proxy.PROXY_QUEUE_WAIT_S == proxy.PROXY_DEADLINE_S - PLACE_READ_S > 0


async def test_queue_does_not_eat_place_deadline(tmp_path, monkeypatch):                # H1: проба ревью
    app, role = make_gateway(tmp_path, ready=(3,))
    place = role.fake_places
    place.hang_for[3] = 0.6                                                              # место отвечает за 0,6 с
    monkeypatch.setattr(proxy, "PROXY_DEADLINE_S", 1.0)
    monkeypatch.setattr(proxy, "PROXY_QUEUE_WAIT_S", 0.3)                                # месту остаётся 0,7 с
    with serve_app(app) as url:
        wait_until(lambda: role.workplaces.ticks >= 1)
        async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=10) as client:
            c3 = await cookie_of(client, "stu-03")
            answers = await asyncio.gather(*(client.get(STATUS, headers=c3) for _ in range(3)))
    assert sorted((r.status_code, r.json().get("name") if r.status_code != 200 else "")
                  for r in answers) == [(200, ""), (200, ""), (503, "WorkplaceBusy")]
    assert role.workplaces.is_ready(3) and role.stats.count_1h("upstream_failed") == 0


async def test_body_budget_on_oc(tmp_path):                                             # H2
    app, role = make_gateway(tmp_path)
    path = f"/pcbk/api/oc/session/{SES}/prompt_async"
    budget, size, go = hooks_of(app).budget_for(path), 524288, asyncio.Event()
    assert budget.total == GW_BODY_BUDGET_BYTES and hooks_of(app).budget_for("/pcbk/api/auth/login") is None
    body = b"x" * size

    async def call(server=("127.0.0.1", 8000), client=("127.0.0.1", 40000)) -> tuple[int, int, dict]:
        reads, sent = [], []

        async def receive() -> dict:
            reads.append(1)
            await go.wait()
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict) -> None:
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"core"), (b"origin", ORIGIN.encode()), (b"content-length", str(size).encode())],
                 "server": server, "client": client}
        await app(scope, receive, send)
        return sent[0]["status"], len(reads), sent

    calls = [asyncio.create_task(call()) for _ in range(40)]
    await wait_until_async(lambda: sum(t.done() for t in calls) == 40 - GW_BODY_BUDGET_BYTES // size)
    assert budget.used == GW_BODY_BUDGET_BYTES                                           # разом — не больше 32 тел
    refused = [t.result() for t in calls if t.done()]
    assert {(status, reads) for status, reads, _ in refused} == {(503, 0)}                # отказ — без чтения
    assert b"WorkplaceBusy" in refused[0][2][1]["body"] and role.stats.count_1h("refused") == 8
    status, reads, sent = await call(server=("172.31.6.2", 8000), client=("172.31.6.3", 40000))
    assert (status, reads) == (404, 0) and b"NotFound" in sent[1]["body"]                # из сети места — 404
    assert (await call())[:2] == (503, 0)                                                # и дальше — сразу отказ
    go.set()
    results = await asyncio.gather(*calls)
    assert sorted(status for status, _, _ in results) == [401] * 32 + [503] * 8        # прочитаны — дальше вход
    assert budget.used == 0
    go.clear()
    async with asyncio.timeout(2):                                                       # бюджет свободен, а из сети
        assert (await call(server=("172.31.6.2", 8000), client=("172.31.6.3", 40000)))[:2] == (404, 0)   # места — нет
    assert budget.used == 0
    hanging = asyncio.create_task(call())                                                # обрыв: байты — назад
    await wait_until_async(lambda: budget.used == size)
    hanging.cancel()
    await asyncio.gather(hanging, return_exceptions=True)
    assert budget.used == 0


async def test_queue_accepts_again_after_expired_waits():                               # H3
    gate = proxy._PlaceGate()
    held = [await gate.acquire(3) for _ in range(2)]                                     # слоты заняты
    for _ in range(PROXY_QUEUE + 1):
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await gate.acquire(3)
    waiters = [asyncio.create_task(gate.acquire(3)) for _ in range(PROXY_QUEUE)]
    await asyncio.sleep(0.01)
    assert not any(t.done() for t in waiters)                                            # все восемь ждут, не busy
    with pytest.raises(Refused) as e:
        await gate.acquire(3)
    assert e.value.reason == "busy"
    for release in held:
        release()
    await wait_until_async(lambda: sum(t.done() for t in waiters) == 2)
    for t in waiters:
        t.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)
    assert gate._waiting[3] == 0


async def test_aclose_closes_every_place_client(tmp_path):                             # H4
    w = make_places(tmp_path)
    for n in (1, 3, 10):
        assert (await w.fetch(n, "GET", "/session/status", max_bytes=1024)).status == 200
    clients = dict(w._clients)
    assert set(clients) == {1, 3, 10}
    await w.aclose()
    assert w._clients == {} and all(c.is_closed for c in clients.values())


async def test_place_clients_share_tls_context(tmp_path, monkeypatch):                 # H5
    made, real = [], ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: made.append(1) or real(*a, **k))
    w = make_places(tmp_path)
    w._transport = None                                                                  # настоящие транспорты httpx
    clients = [w._http(n) for n in range(1, 11)]
    assert len(made) == 1 and len({id(c) for c in clients}) == 10
    await w.aclose()


def test_missing_content_type_on_body_is_octet_stream(gw):                             # H6
    c, _, place = gw
    place.force_status[3] = (200, {"content-type": None})
    r = c.get(STATUS)
    assert (r.status_code, r.headers["content-type"], r.content) == (200, OCTET_STREAM, b'{"name":"Forced"}')
