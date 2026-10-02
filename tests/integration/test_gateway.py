"""Шлюз к OpenCode в компоновке (Д5б): вызовы /pcbk/api/oc/* и поток /event через настоящий edge к настоящему
OpenCode мест — опасные ручки и близнецы, искажённые пути, вырезанные поля, чужие id и агенты, зависшее и спящее
место, поток без буфера; edge — путь до «?», пределы соединений, заголовки ответов.

Места этого модуля — 6 (А), 7 (Б), 8 (спит); test_entry — 1, 3, 4, 5. Модули идут по очереди, и каждый гасит свои
места к концу, в том числе в finally фикстуры users (Ш16). Истина о состоянии места — stack.oc (из сети места, мимо
шлюза). Пароли и cookie — только в запросах: в assert — коды, имена и индексы, не значения.
"""
import http.client
import json
import re
import secrets
import socket
import ssl
import threading
import time
from contextlib import contextmanager

import pytest

from conftest import HOST, PORT, STAND_HOST, STAND_ORIGIN, Secret, failure, leaks, secret_forms, wait_for
from gateway_cases import (ALLOW_PERM, ALLOWED, EVENT_TYPES, FORBIDDEN, MANGLED, METHODS, MSG_FAKE, OC, SES_FAKE,
                           check_forbidden, check_mangled, expected_refusals)
from gwclient import GwClient
from test_students import PROBE_AGENT

# пароли — Secret: в трассировке pytest «<скрыт>» (итоговое ревью Д6а, P7)
PW = {n: Secret(secrets.token_urlsafe(12)) for n in ("gw-a", "gw-b", "gw-c", "gw-t")}
OWN_PLACES = (6, 7, 8)
# пределы edge на /pcbk/api/oc/ (edge/pcbk.conf.template): на cookie сессии и всего
EDGE_OC_PER_SESSION = 10
EDGE_OC_TOTAL = 64
EDGE_OC_RATE, EDGE_OC_BURST = 20, 200                   # limit_req на cookie (итоговая доводка Д5б, N1)
EDGE_BODY_TIMEOUT_S = 10                                # client_body_timeout у /pcbk/api/oc/ (N2)
TEACHER_TEXT = "Просмотр разговоров студентов — в следующих выпусках стенда"
LOG_WAIT_S = 10
SETTLE_S = 30
# cookie всех сессий модуля (вход, вторая сессия, преподаватель, место 08) — для проверки журнала core на секреты
COOKIES: list[str] = []
REFUSED_RULES = re.compile(r" gw refused .* reason=(?:not_whitelisted|bad_path) ")
REFUSED_ORIGIN = re.compile(r" gw refused .* reason=origin ")
PART_ONLY_TEXT = "На учебном стенде в сообщении допустим только текст"


@pytest.fixture(scope="module")
def users(stack):
    try:
        for login, place in (("gw-a", 6), ("gw-b", 7), ("gw-c", 8), ("gw-t", None)):
            stack.add_account(login, "teacher" if place is None else "student", place, PW[login])
        a, b = (stack.gw_client() for _ in range(2))
        a.login("gw-a", PW["gw-a"]); b.login("gw-b", PW["gw-b"])
        COOKIES.extend((a.cookie, b.cookie))
        states = (a.wake_and_wait(90)["state"], b.wake_and_wait(90)["state"])
        assert states == ("ready", "ready")
        yield a, b
    finally:                                     # Ш16: места модуля — в сон, даже при сбое
        for n in OWN_PLACES:
            if stack.inspect(f"pcbk-student-{n:02d}")["State"]["Running"]:
                stack.stop(f"pcbk-student-{n:02d}")


def direct_session(stack, n, sid):                                                     # истина — мимо шлюза
    code, body = stack.oc(n, "GET", f"/session/{sid}", n)
    assert code == 200
    return json.loads(body)


def gw_oc_lines(stack):
    return stack.logs("pcbk-core").count(" gw oc ")


def settled(count, timeout: float = SETTLE_S):
    """Значение count(), когда оно перестало расти (строка журнала пишется до ответа, docker logs видит её чуть
    позже); не успокоилось за timeout — AssertionError."""
    deadline = time.monotonic() + timeout
    last = count()
    while True:
        time.sleep(1)
        now = count()
        if now == last:
            return now
        if time.monotonic() > deadline:
            raise AssertionError(f"журнал растёт дольше {timeout} с")
        last = now


def settled_oc_lines(stack):
    return settled(lambda: gw_oc_lines(stack))


def refused_counts(stack):
    """Строки «gw refused» журнала core: (по правилам — not_whitelisted и bad_path, по источнику — origin)."""
    logs = stack.logs("pcbk-core")
    return len(REFUSED_RULES.findall(logs)), len(REFUSED_ORIGIN.findall(logs))


@contextmanager
def dropping_port():
    """Порт 127.0.0.1, который принимает соединение и сразу его закрывает: TLS рвётся посреди вызова клиента."""
    srv = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.close()
    threading.Thread(target=serve, daemon=True).start()
    try:
        yield srv.getsockname()[1]
    finally:
        srv.close()


def test_gw_client_hides_password_in_tracebacks():                                      # без стенда: итоговое ревью Д6а, P7
    probe = Secret("pcbk-probe-" + secrets.token_hex(8))
    with dropping_port() as port:
        c = GwClient(f"https://127.0.0.1:{port}", timeout=5)
        # контроль: тело с открытой строкой — пароль в трассировке виден (кадры call и http.client)
        assert leaks(failure(c.call, "POST", "/pcbk/api/auth/login", {"login": "gw-a", "password": str(probe)}), probe)
        assert not leaks(failure(c.login, "gw-a", probe), probe)                       # вход: login, call, http.client
        assert not leaks(failure(c.call, "POST", "/pcbk/api/auth/login", {"login": "gw-a", "password": probe}), probe)
    assert {type(v) for v in PW.values()} == {Secret}                                   # пароли модуля


def test_event_types_match_core(stack):
    out = stack._docker("exec", "pcbk-core", "python", "-c",
                        "from pcbk_core.gateway.rules import ALLOWED_EVENTS as A; print(' '.join(sorted(A)))").stdout
    assert set(out.split()) == EVENT_TYPES


# сверка таблицы случаев с правилами шлюза в образе core (argv — случаи JSON): каждая пара ALLOWED — своё правило, все
# правила покрыты; FORBIDDEN (путь до «?») — отказ правил; METHODS — те же
CASES_PY = """\
import json, sys
from pcbk_core.gateway.rules import METHODS, RULES, Refused, match
cases = json.loads(sys.argv[1])
prefix = cases["prefix"]
allowed = sorted({match(m, p[len(prefix):])[0].name for m, p in cases["allowed"]})
refused = []
for m, p in cases["forbidden"]:
    try:
        match(m, p[len(prefix):].split("?", 1)[0])
    except Refused as r:
        refused.append(r.reason)
print(json.dumps({"allowed": allowed, "rules": sorted(r.name for r in RULES), "refused": refused,
                  "methods": list(METHODS)}))
"""


def test_cases_match_core_rules(stack):
    cases = {"prefix": OC, "allowed": ALLOWED, "forbidden": [(m, p) for m, p, _ in FORBIDDEN if p != OC]}
    got = json.loads(stack._docker("exec", "pcbk-core", "python", "-c", CASES_PY, json.dumps(cases)).stdout)
    assert got["allowed"] == got["rules"] and len(got["rules"]) == len(ALLOWED) == 15
    assert len(got["refused"]) == len(FORBIDDEN) - 1 and set(got["refused"]) <= {"not_whitelisted", "bad_path"}
    assert tuple(got["methods"]) == METHODS


def test_forbidden_forward_nothing(stack, users):                                       # Review Focus 1
    a, _ = users
    assert a.call("POST", "/pcbk/api/oc/session")[0] == 200                           # положительный не-GET
    before = settled_oc_lines(stack)
    rules0, origin0 = settled(lambda: refused_counts(stack))
    assert check_forbidden(a) == []
    assert check_mangled(a) == []                                                       # edge — 400
    assert settled_oc_lines(stack) == before                                            # к месту не ушло ничего
    rules1, origin1 = settled(lambda: refused_counts(stack))
    assert (rules1 - rules0, origin1 - origin0) == (expected_refusals(), 0)             # журнал Б7 — то же число
    cookie = {"Cookie": f"pcbk_session={a.cookie}"}
    for path in MANGLED:                                                                # мимо edge, из pcbk-front — шлюз 403
        code = stack.http_front("GET", path, headers=cookie)
        assert code == 403, path


def test_edge_checks_path_not_query(stack, users):                                     # решение контроллера 2
    a, _ = users
    code, body, _ = a.call("GET", "/pcbk/api/oc/session%2Fstatus")                    # %2F в пути — edge
    assert (code, body["name"], body["data"]["message"]) == (400, "BadRequest", "неверный адрес")
    before = settled_oc_lines(stack)
    code, body, _ = a.call("GET", "/pcbk/api/oc/session/status?x=%2F..%2F&y=//z&location[directory]=/./etc")
    assert (code, isinstance(body, dict)) == (200, True)                              # в строке запроса — до шлюза
    wait_for(lambda: gw_oc_lines(stack) > before, timeout=LOG_WAIT_S)
    assert a.call("GET", "/pcbk/api/workplace?next=%2F..%2F")[0] == 200               # то же у путей Д5а


def test_oc_responses_not_pages(stack, users):                                         # решение контроллера 3
    a, _ = users
    cases = [(200, a.call("GET", "/pcbk/api/oc/session/status")),                     # ответ места
             (403, a.call("GET", "/pcbk/api/oc/config")),                              # отказ шлюза
             (400, a.call("GET", "/pcbk/api/oc//session/status")),                     # ответ самого edge
             (403, a.call("GET", "/%70cbk/api/oc/session/status")),                    # L8: путь после нормализации —
             (400, a.call("GET", "/foo/../pcbk/api/oc/session/status")),               # CSP по $uri, не по сырому адресу
             (413, a.call("POST", "/pcbk/api/oc/session", {"x": "x" * 600_000}))]      # переход error_page (/pcbk-errors/)
    for expected, (code, _, headers) in cases:
        assert (code, headers.get_all("Content-Security-Policy"), headers.get_all("X-Content-Type-Options"),
                headers.get_all("Cache-Control")) == (expected, ["default-src 'none'"], ["nosniff"], ["no-store"])
    code, headers = stack.https_headers("/status")                                    # у страницы сторожа — только свой CSP
    csp = headers.get_all("Content-Security-Policy") or []
    assert code == 200 and len(csp) == 1 and "script-src 'self'" in csp[0]


def test_config_blocked_but_live_in_place(stack, users):
    a, _ = users
    tokens = (stack.llm_token(6), stack.data_token(6))
    # положительный контроль: мимо шлюза ручка жива и отдаёт подставленные {file:} — /config оба токена места,
    # /config/providers — токен LLM (apiKey провайдера); через шлюз — 403 и ни одного. Значения — не в assert
    for path, direct_has in (("/config", (True, True)), ("/config/providers", (True, False))):   # Ш14
        code, direct = stack.oc(6, "GET", path, 6)
        present = tuple(t in direct for t in tokens)
        gw_code, body, _ = a.call("GET", "/pcbk/api/oc" + path)
        text = json.dumps(body)
        leaked = tuple(t in text for t in tokens)
        assert (code, present, gw_code, leaked) == (200, direct_has, 403, (False, False)), path


def test_archived_null_goes_to_place(stack, users):                                     # Ш13: шлюз пересылает, место решает
    a, _ = users
    sid = a.call("POST", "/pcbk/api/oc/session")[1]["id"]
    stamp = int(time.time() * 1000)
    assert a.call("PATCH", f"/pcbk/api/oc/session/{sid}", {"time": {"archived": stamp}})[0] == 200
    assert direct_session(stack, 6, sid)["time"].get("archived") == stamp              # в архиве — истина мимо шлюза
    line = "rule=session_update status="
    before = stack.logs("pcbk-core").count(line)
    code, _, _ = a.call("PATCH", f"/pcbk/api/oc/session/{sid}", {"time": {"archived": None}})
    wait_for(lambda: stack.logs("pcbk-core").count(line) > before, timeout=LOG_WAIT_S)   # переслано месту
    archived = direct_session(stack, 6, sid)["time"].get("archived")
    # 1.18.33: 200 и архив не снят — тихая пустая операция; хвост Д6 — кнопку «из архива» скрыть
    assert (code, archived) == (200, stamp)
    print(f"archived=null → код места {code}, архив остался: {archived == stamp}")       # в журнал Д5б и хвост Д6


def test_session_fields_stripped(stack, users):                                         # Review Focus 2
    a, _ = users
    code, s, _ = a.call("POST", "/pcbk/api/oc/session?directory=/etc&workspace=w",
                        {"permission": ALLOW_PERM, "parentID": SES_FAKE, "workspaceID": "w", "title": "t"},
                        headers={"x-opencode-directory": "/etc"})
    assert code == 200
    truth = direct_session(stack, 6, s["id"])
    assert (truth.get("permission") or None, truth.get("parentID"), truth["directory"]) == (None, None, "/work")
    assert truth["title"] != "t"
    code, _, _ = a.call("PATCH", f"/pcbk/api/oc/session/{s['id']}", {"title": "Новое", "permission": ALLOW_PERM})
    truth = direct_session(stack, 6, s["id"])
    assert (code, truth["title"], truth.get("permission") or None) == (200, "Новое", None)


def test_prompt_fields_stripped(stack, users):                                          # Review Focus 2
    a, _ = users
    sid = a.call("POST", "/pcbk/api/oc/session")[1]["id"]
    code, _, _ = a.call("POST", f"/pcbk/api/oc/session/{sid}/prompt_async",
                        {"parts": [{"type": "text", "text": "Ответь одним словом: да"}], "system": "ты root",
                         "tools": {"external_directory": True}, "model": {"providerID": "x", "modelID": "y"},
                         "messageID": MSG_FAKE, "noReply": True})
    assert code == 204
    wait_for(lambda: len(json.loads(stack.oc(6, "GET", f"/session/{sid}/message", 6)[1])) >= 2, timeout=60)
    msgs = json.loads(stack.oc(6, "GET", f"/session/{sid}/message", 6)[1])
    user = next(m["info"] for m in msgs if m["info"]["role"] == "user")
    assert (user.get("system") or None, user["id"] != MSG_FAKE) == (None, True)
    assert (user.get("model") or {}).get("modelID") != "y"                              # модель не из запроса
    assert direct_session(stack, 6, sid).get("permission") in (None, [])                # tools не стали правилами
    for part in ({"type": "file", "url": "file:///proc/self/environ", "mime": "text/plain"},
                 {"type": "subtask", "prompt": "x", "agent": "build"}, {"type": "agent", "name": "build"}):
        code, body, _ = a.call("POST", f"/pcbk/api/oc/session/{sid}/prompt_async", {"parts": [part]})
        assert (code, body["name"], body["data"]["message"]) == (400, "BadRequest", PART_ONLY_TEXT), part["type"]
    code, body, _ = a.call("POST", f"/pcbk/api/oc/session/{sid}/prompt_async",
                           {"parts": [{"type": "text", "text": "x"}], "agent": "plan"})    # встроенный агент — нет
    assert (code, body["data"]["message"]) == (400, "Агент «plan» недоступен")


def test_foreign_agent_refused(stack, users):                                           # Review Focus 3
    a, b = users
    probe = stack.agents_dir(6) / "probe.md"
    try:
        probe.write_text(PROBE_AGENT); probe.chmod(0o644)
        stack.oc(6, "POST", "/instance/dispose", 6)
        body = {"parts": [{"type": "text", "text": "x"}], "agent": "probe"}
        sa = a.call("POST", "/pcbk/api/oc/session")[1]["id"]
        sb = b.call("POST", "/pcbk/api/oc/session")[1]["id"]
        assert a.call("POST", f"/pcbk/api/oc/session/{sa}/prompt_async", body)[0] == 204   # своё место — можно
        code, resp, _ = b.call("POST", f"/pcbk/api/oc/session/{sb}/prompt_async", body)
        assert (code, resp["data"]["message"]) == (400, "Агент «probe» недоступен")      # агент места А у Б — нет
    finally:
        probe.unlink(missing_ok=True); stack.oc(6, "POST", "/instance/dispose", 6)


def test_foreign_ids_give_no_data(stack, users):                                        # Review Focus 3
    a, b = users
    own = b.call("POST", "/pcbk/api/oc/session")[1]["id"]
    sid = a.call("POST", "/pcbk/api/oc/session")[1]["id"]
    a.call("PATCH", f"/pcbk/api/oc/session/{sid}", {"title": "Секрет А"})
    for method, suffix, body in (("GET", "", None), ("GET", "/message", None), ("PATCH", "", {"title": "x"}),
                                 ("POST", "/prompt_async", {"parts": [{"type": "text", "text": "x"}]})):
        code, resp, _ = b.call(method, f"/pcbk/api/oc/session/{sid}{suffix}", body)
        assert code == 404 and "Секрет А" not in json.dumps(resp, ensure_ascii=False), (method, suffix)   # requireSession
    assert b.call("POST", f"/pcbk/api/oc/session/{sid}/abort")[:2] == (200, True)                      # research/07 §4
    code, listed, _ = b.call("GET", "/pcbk/api/oc/experimental/session?roots=true&archived=true&place=06",
                             headers={"X-Pcbk-Place": "06"})
    assert code == 200 and own in json.dumps(listed) and sid not in json.dumps(listed)   # положительный контроль
    truth = direct_session(stack, 6, sid)                                              # у А ничего не изменилось
    assert truth["title"] == "Секрет А"
    assert json.loads(stack.oc(6, "GET", f"/session/{sid}/message", 6)[1]) == []


def test_origin_and_teacher(stack, users):
    evil = GwClient(f"https://{HOST}:{PORT}", origin="https://evil.example")
    evil.cookie = users[0].cookie
    code, body, _ = evil.call("POST", "/pcbk/api/oc/session")
    assert (code, body["data"]["message"]) == (403, "Запрос не с нашей страницы — отклонён")
    t = stack.gw_client(); t.login("gw-t", PW["gw-t"])
    COOKIES.append(t.cookie)
    code, body, _ = t.call("GET", "/pcbk/api/oc/session/status")
    assert (code, body["name"], body["data"]["message"]) == (403, "Forbidden", TEACHER_TEXT)   # N7: отказ — «преподаватель»


def test_duplicate_cookies_on_long_path_through_edge(stack, users):                     # Review Focus Д5а 2; Д5-R25, Д5-R30
    a, _ = users
    sid = a.call("POST", "/pcbk/api/oc/session")[1]["id"]
    extra = stack.gw_client(); extra.login("gw-a", PW["gw-a"])                        # вторая сессия того же логина
    COOKIES.append(extra.cookie)
    both = {"Cookie": f"pcbk_session={a.cookie}; pcbk_session={extra.cookie}"}
    code, body, headers = a.call("POST", f"/pcbk/api/oc/session/{sid}/prompt_async",   # самый длинный путь: 64 знака
                                 {"parts": [{"type": "text", "text": "x"}]}, headers=both)
    deletes = headers.get_all("Set-Cookie") or []
    assert (code, body["name"]) == (401, "Unauthorized")                               # не 503 от edge
    assert len(deletes) == 12 * 3 * 2 and any("Domain=pcbk.test" in d for d in deletes)
    assert "too big header" not in stack.logs("pcbk-edge")
    a.login("gw-a", PW["gw-a"])                                                        # обе сессии закрыты — вход заново
    COOKIES.append(a.cookie)


def test_sleeping_place_503_no_wake(stack, users):
    c = stack.gw_client(); c.login("gw-c", PW["gw-c"])
    COOKIES.append(c.cookie)
    code, body, _ = c.call("GET", "/pcbk/api/oc/session/status")
    assert (code, body["name"]) == (503, "WorkplaceNotReady")
    assert stack.inspect("pcbk-student-08")["State"]["Running"] is False


def test_event_stream_through_edge(stack, users):                                       # Review Focus 4
    a, _ = users
    sid = a.call("POST", "/pcbk/api/oc/session")[1]["id"]
    send = lambda: a.call("POST", f"/pcbk/api/oc/session/{sid}/prompt_async", {"parts": [{"type": "text", "text": "да?"}]})
    events, lasted = a.events(25, on_open=send)
    types = [e["type"] for e in events]
    assert types[0] == "server.connected" and types.count("server.heartbeat") >= 2 and lasted >= 24
    assert "session.status" in types and set(types) <= EVENT_TYPES


@contextmanager
def slow_bodies(cookies):
    """По соединению к edge на каждую cookie (None — без cookie): заголовки POST /pcbk/api/oc/session с
    Content-Length: 64 и без тела. edge ждёт тело (proxy_request_buffering on) — запрос держит слот limit_conn, а в
    core не уходит ничего."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    socks = []
    try:
        for cookie in cookies:
            s = ctx.wrap_socket(socket.create_connection((HOST, PORT), timeout=10))
            socks.append(s)
            jar = "" if cookie is None else f"Cookie: pcbk_session={cookie}\r\n"
            s.sendall((f"POST /pcbk/api/oc/session HTTP/1.1\r\nHost: {STAND_HOST}\r\n{jar}Origin: {STAND_ORIGIN}\r\n"
                       "Content-Type: application/json\r\nContent-Length: 64\r\n\r\n").encode("ascii"))
        time.sleep(1)                                                                  # edge разобрал заголовки всех
        yield
    finally:
        for s in socks:
            s.close()


def core_creates(stack):
    return stack.logs("pcbk-core").count("rule=session_create ")


def test_edge_limits_oc_connections(stack, users):                                     # решение контроллера 3
    a, b = users
    created = core_creates(stack)
    with slow_bodies([a.cookie] * EDGE_OC_PER_SESSION):                               # предел на cookie сессии
        code, body, _ = a.call("GET", "/pcbk/api/oc/session/status")
        assert (code, body["name"], body["data"]["message"]) == \
               (503, "WorkplaceBusy", "Слишком много одновременных запросов — повторите позже")
        assert b.call("GET", "/pcbk/api/oc/session/status")[0] == 200                 # сосед не задет
        assert core_creates(stack) == created                                          # медленные тела — не в core
        t = time.monotonic()                                                           # N2: тело, не пришедшее за 10 с,
        wait_for(lambda: a.call("GET", "/pcbk/api/oc/session/status")[0] == 200,       # edge обрывает — слоты свободны,
                 timeout=EDGE_BODY_TIMEOUT_S + 10)                                      # хотя клиенты соединений не закрыли
        assert time.monotonic() - t < EDGE_BODY_TIMEOUT_S + 5
    assert 'limiting connections by zone "pcbk_oc_session"' in stack.logs("pcbk-edge")
    wait_for(lambda: a.call("GET", "/pcbk/api/oc/session/status")[0] == 200, timeout=LOG_WAIT_S)
    with slow_bodies([None] * EDGE_OC_PER_SESSION):                                   # L1: без cookie — одна корзина
        code, body, _ = stack.gw_client().call("GET", "/pcbk/api/oc/session/status")  # 11-й без cookie
        assert (code, body["name"]) == (503, "WorkplaceBusy")
        assert a.call("GET", "/pcbk/api/oc/session/status")[0] == 200                 # с настоящей cookie — проходит
    wait_for(lambda: stack.gw_client().call("GET", "/pcbk/api/oc/session/status")[0] == 401, timeout=LOG_WAIT_S)
    others = [secrets.token_urlsafe(32) for _ in range(EDGE_OC_TOTAL)]                 # свой вид, каждая — своя корзина
    with slow_bodies(others):                                                          # общий предел location
        code, body, _ = b.call("GET", "/pcbk/api/oc/session/status")
        assert (code, body["name"]) == (503, "WorkplaceBusy")
        mem = stack._docker("stats", "--no-stream", "--format", "{{.MemUsage}}", "pcbk-edge").stdout.strip()
        print(f"edge при {EDGE_OC_TOTAL} медленных телах: {mem}")                      # замер для отчёта Б5
    assert 'limiting connections by zone "pcbk_oc_all"' in stack.logs("pcbk-edge")
    wait_for(lambda: b.call("GET", "/pcbk/api/oc/session/status")[0] == 200, timeout=LOG_WAIT_S)
    assert core_creates(stack) == created


def flood(cookie: str, total: int, threads: int = 4) -> tuple[list[int], dict]:
    """total запросов GET /pcbk/api/oc/config (отказ правил — строка «gw refused» в core) одной cookie: threads потоков,
    у каждого своё соединение keep-alive (HTTP/1.1), иначе рукопожатия TLS растянули бы поток дольше запаса. Коды и
    по одному телу каждого кода (JSON)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    codes: list[int] = []
    bodies: dict = {}
    lock = threading.Lock()

    def worker(n: int) -> None:
        conn = http.client.HTTPSConnection(HOST, PORT, context=ctx, timeout=20)
        try:
            for _ in range(n):
                for attempt in range(2):                                               # edge закрыл keep-alive — заново
                    try:
                        conn.putrequest("GET", "/pcbk/api/oc/config", skip_host=True, skip_accept_encoding=True)
                        conn.putheader("Host", STAND_HOST)
                        conn.putheader("Cookie", f"pcbk_session={cookie}")
                        conn.endheaders()
                        r = conn.getresponse()
                        raw = r.read()
                        break
                    except (http.client.HTTPException, OSError):
                        conn.close()
                        conn = http.client.HTTPSConnection(HOST, PORT, context=ctx, timeout=20)
                        if attempt:
                            raise
                with lock:
                    codes.append(r.status)
                    bodies.setdefault(r.status, (r.getheader("Content-Type"), json.loads(raw)))
        finally:
            conn.close()

    workers = [threading.Thread(target=worker, args=(total // threads,)) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return codes, bodies


def test_edge_limits_oc_rate(stack, users):                                             # итоговая доводка Д5б, N1
    f = stack.gw_client(); f.login("gw-a", PW["gw-a"])                                 # своя cookie — своя корзина
    COOKIES.append(f.cookie)
    rules0, _ = settled(lambda: refused_counts(stack))
    t = time.monotonic()
    codes, bodies = flood(f.cookie, 300)
    elapsed = time.monotonic() - t
    rules1, _ = settled(lambda: refused_counts(stack))
    forbidden, busy = codes.count(403), codes.count(503)
    assert (len(codes), forbidden + busy) == (300, 300) and busy > 0                    # часть — 503 от edge
    assert bodies[503] == ("application/json", {"name": "WorkplaceBusy", "data": {           # как занятость места:
        "message": "Слишком много одновременных запросов — повторите позже"}})             # чат Д6 его повторит
    assert rules1 - rules0 == forbidden <= EDGE_OC_BURST + 1 + EDGE_OC_RATE * elapsed   # журнал core — не больше запаса
    assert "limiting requests" in stack.logs("pcbk-edge")
    print(f"поток 300 запросов за {elapsed:.1f} с: 403 — {forbidden}, 503 — {busy}")
    f.logout()                                                                         # сессия учётки — не лишняя (N2)


def check_no_secrets_in_core_logs(stack, a):
    """Журнал core — без значений секретов модуля: строки запроса, cookie всех сессий, паролей учёток, паролей мест
    (сырой, base64 и Basic), токенов LLM и данных мест. В assert — полнота списка и индексы, не значения."""
    logs = stack.logs("pcbk-core")
    read = "gw refused" in logs and "gw oc login=gw-a" in logs                          # журнал прочитан
    assert read                                                                         # без печати журнала
    cookies = [*dict.fromkeys([*COOKIES, a.cookie])]                   # все сессии модуля (L11): в полном прогоне — 7
    forbidden = ["U0VDUkVUVkFMVUU", *cookies, *PW.values(),
                 *(f for n in OWN_PLACES for f in (*secret_forms(stack.password(n)), stack.llm_token(n),
                                                   stack.data_token(n)))]
    complete = len(cookies) >= 2 and len(forbidden) == 1 + len(cookies) + len(PW) + 5 * len(OWN_PLACES) \
        and all(forbidden)                                                               # ни одного пустого
    leaked = [i for i, s in enumerate(forbidden) if s in logs]                          # индексы, не значения
    assert (complete, leaked) == (True, [])


def test_secrets_not_in_core_logs_b(stack, users):                                      # Review Focus 5
    a, _ = users
    line = "path=/config reason=not_whitelisted"                                        # строка запроса в журнал не идёт
    before = stack.logs("pcbk-core").count(line)
    a.call("GET", "/pcbk/api/oc/config?auth_token=U0VDUkVUVkFMVUU=")
    wait_for(lambda: stack.logs("pcbk-core").count(line) > before, timeout=LOG_WAIT_S)
    check_no_secrets_in_core_logs(stack, a)


def test_hung_place_is_workplace_not_ready(stack, users):                               # Review Focus 5
    a, _ = users
    stack.pause("pcbk-student-06")
    try:
        t = time.monotonic()
        code, body, _ = a.call("GET", "/pcbk/api/oc/session/status")                   # не «Серверный слой не отвечает»
        assert (code, body["name"], body["data"]["message"]) == \
               (503, "WorkplaceNotReady", "Рабочее место не отвечает — разбудите его на странице входа")
        assert time.monotonic() - t < 30
    finally:
        stack.unpause("pcbk-student-06")
    assert a.wake_and_wait(90)["state"] == "ready"


def test_event_ends_when_place_stops(stack, users):
    _, b = users
    stopper = threading.Timer(3, lambda: stack.stop("pcbk-student-07"))
    stopper.start()
    try:
        events, lasted = b.events(40)
    finally:
        stopper.join()
    assert events and events[0]["type"] == "server.connected"                          # поток был открыт
    assert 3 < lasted < 20                                                              # конец потока места — конец браузеру
    assert b.call("GET", "/pcbk/api/oc/session/status")[1]["name"] == "WorkplaceNotReady"


def test_secrets_not_in_core_logs_after_failures(stack, users):                         # N6: после исключений мест
    """Зависшее и остановленное место дают исключения httpx в прокси и потоке — журнал core сверяется ещё раз, до
    пересоздания core последним тестом (новый контейнер — новый журнал)."""
    a, _ = users
    assert "error=" in stack.logs("pcbk-core")                                          # исключения мест в журнале есть
    check_no_secrets_in_core_logs(stack, a)


def test_event_is_not_activity(stack, users):                                           # Review Focus 4; последний
    stack.recreate("core", {"GW_IDLE_STOP_S": "60", "GW_REAPER_TICK_S": "5"})
    try:
        c = stack.gw_client(); c.login("gw-a", PW["gw-a"])
        assert c.wake_and_wait(90)["state"] == "ready"
        events, lasted = c.events(120)                                                  # вкладка открыта, действий нет
        assert lasted < 90 and stack.inspect("pcbk-student-06")["State"]["Running"] is False
    finally:
        stack.recreate("core", {})
