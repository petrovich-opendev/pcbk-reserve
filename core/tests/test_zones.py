"""Зоны адресов (Д4б-R23, Д5-R31, Ш25; итоговое ревью Д5а, M3): зона — по адресу назначения (scope["server"]) и
источника (scope["client"]) сразу; токен места — по источнику. Закрыто при сомнении: адреса нет или имя вместо
адреса — чужое, кроме приложения с пометкой тестов."""
import ipaddress
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from helpers import ORIGIN, PW, SETTINGS, gateway_role, make_gateway, make_llm_app, serve_app
from pcbk_core import zones
from pcbk_core.app import create_app
from pcbk_core.zones import caller_net_ok, is_admin, is_front


def app_with(names_ok: bool):
    return SimpleNamespace(state=SimpleNamespace(zone_names_ok=True) if names_ok else SimpleNamespace())


# (назначение, источник, front, admin): законные источники — edge и сторож в pcbk-front, петля, хост сервера и сам
# core в pcbk-egress; источник из сети места — чужой при любом назначении (итоговое ревью Д5а, M3)
ZONE_CASES = (
    ("172.31.250.34", "172.31.250.35", True, True),                                    # edge → core в pcbk-front
    ("172.31.250.34", "172.31.250.36", True, True),                                    # сторож
    ("127.0.0.1", "127.0.0.1", True, True), ("::1", "::1", True, True),                # петля
    ("172.31.250.82", "172.31.250.81", False, True),                                   # хост сервера в pcbk-egress
    ("172.31.250.82", "172.31.250.82", False, True),                                   # сам core
    ("172.31.250.34", "172.31.250.81", False, True),                                   # хост на адрес в pcbk-front
    ("172.31.6.2", "172.31.6.3", False, False),                                        # место 06 на свой .2
    ("172.31.250.34", "172.31.6.3", False, False),                                     # место 06 на адрес в pcbk-front
    ("172.31.250.82", "172.31.6.3", False, False),                                     # … и в pcbk-egress
    ("127.0.0.1", "172.31.6.3", False, False),
    ("172.31.250.34", "172.31.250.66", False, False),                                  # из pcbk-ctl (sp-ctl)
    ("172.31.250.66", "172.31.250.67", False, False),
    ("testserver", "testclient", False, False),                                        # имя — чужое
)


def test_zones():
    prod = app_with(False)
    for host, client, front, admin in ZONE_CASES:
        scope = {"server": (host, 8000), "client": (client, 40000), "app": prod}
        assert (is_front(scope), is_admin(scope)) == (front, admin), (host, client)
    for server, client in ((None, ("127.0.0.1", 1)), (("127.0.0.1", 8000), None)):     # адреса нет — чужое
        scope = {"server": server, "client": client, "app": prod}
        assert (is_front(scope), is_admin(scope)) == (False, False), (server, client)
    opted = {"server": ("testserver", 80), "client": ("testclient", 50000), "app": app_with(True)}   # пометка тестов
    assert (is_front(opted), is_admin(opted)) == (True, True)
    opted["client"] = ("172.31.6.3", 40000)                                            # адрес сверяется и в тестах
    assert (is_front(opted), is_admin(opted)) == (False, False)


def test_hostname_fails_closed_without_opt_in(tmp_path):                                # решение контролёра р2
    app = create_app(SETTINGS, [gateway_role(tmp_path)])                               # как в проде: без пометки
    assert getattr(app.state, "zone_names_ok", False) is False
    with TestClient(app) as c:                                                         # имя «testserver»
        assert c.get("/pcbk/api/auth/me").status_code == 404
        assert c.get("/health/gateway").status_code == 404
        assert c.get("/health/web").status_code == 404


def test_uvicorn_scope_server_is_ip():                                                  # решение контролёра р2; Ш6, Ш25
    seen = {}

    async def probe(scope, receive, send):
        if scope["type"] == "lifespan":                                                # serve_app — lifespan="on"
            while (msg := await receive())["type"] != "lifespan.shutdown":
                await send({"type": "lifespan.startup.complete"})
            await send({"type": "lifespan.shutdown.complete"})
            return
        seen["server"], seen["client"] = scope["server"], scope["client"]
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    with serve_app(probe) as url:
        httpx.get(url + "/", trust_env=False)
    for key in ("server", "client"):
        assert ipaddress.ip_address(seen[key][0]).is_loopback, key                     # адрес, а не имя


# --- сверх брифа ---

def test_subnets_match_plan():
    """Первая сеть FRONT_NETS — pcbk-front, последняя ADMIN_NETS — pcbk-egress (их сверяет с compose.yaml А6)."""
    assert zones.FRONT_NETS[0] == "172.31.250.32/28" and zones.ADMIN_NETS[-1] == "172.31.250.80/28"
    assert zones.ADMIN_NETS[:len(zones.FRONT_NETS)] == zones.FRONT_NETS
    assert is_front({"server": ("::ffff:172.31.250.34", 8000), "client": ("::ffff:172.31.250.35", 1),
                     "app": app_with(False)})                                          # IPv4 в IPv6 — как IPv4
    assert not is_front({"server": ("::ffff:172.31.250.34", 8000), "client": ("::ffff:172.31.6.3", 1),
                         "app": app_with(False)})


def test_caller_net_ok():                                                               # Ш25
    prod = app_with(False)
    for caller, client, ok in (("student-01", ("172.31.1.3", 1), True), ("student-01", ("172.31.1.14", 1), True),
                               ("student-01", ("172.31.1.16", 1), False),             # за /28 своего места
                               ("student-01", ("172.31.2.3", 1), False), ("student-10", ("172.31.10.3", 1), True),
                               ("student-01", ("172.31.250.81", 1), True), ("student-01", ("127.0.0.1", 1), True),
                               ("student-01", ("10.9.9.9", 1), False), ("ops", ("10.9.9.9", 1), True),
                               ("student-01", ("testclient", 50000), True), ("student-01", None, True)):
        assert caller_net_ok({"client": client, "app": prod}, caller) is ok, (caller, client)
    other = SimpleNamespace(state=SimpleNamespace(pcbk_stu_net="10.50"))              # STU_NET шлюза не по умолчанию
    assert caller_net_ok({"client": ("10.50.1.3", 1), "app": other}, "student-01")
    assert not caller_net_ok({"client": ("172.31.1.3", 1), "app": other}, "student-01")
    assert caller_net_ok({"client": ("10.50.1.3", 1), "app": prod, zones.STU_NET_KEY: "10.50"}, "student-01")
    bad = SimpleNamespace(state=SimpleNamespace(pcbk_stu_net="нет"))
    assert not caller_net_ok({"client": ("172.31.1.3", 1), "app": bad}, "student-01")   # сеть не построить — закрыто


def test_malformed_student_id_refused():                                                # F7 (ревью A, M4)
    """id с префиксом student- без разбираемого номера — отказ из любой сети, в том числе из ADMIN_NETS и от
    клиента тестов; прочие id и student-NN — как прежде."""
    prod = app_with(False)
    for caller in ("student-1", "student-001", "student-ab", "student-", "student-01x"):
        for client in (("172.31.1.3", 1), ("127.0.0.1", 1), ("testclient", 50000), None):
            assert not caller_net_ok({"client": client, "app": prod}, caller), (caller, client)
    assert caller_net_ok({"client": ("127.0.0.1", 1), "app": prod}, "students-1")      # не префикс student-
    assert caller_net_ok({"client": ("172.31.1.3", 1), "app": prod}, "student-01")


# --- доводка итогового ревью Д5а (final-d5a-fix.md, R4) ---

def test_gateway_needs_front_client_too(tmp_path):                                      # M3: зона и по источнику
    """Адрес назначения — core в pcbk-front, а источник — сеть места: ручки шлюза не видны (404, и предел тела —
    тоже 404); из edge и сторожа — видны."""
    app, _ = make_gateway(tmp_path)
    big = {"content": b"x" * 70000, "headers": {"Origin": ORIGIN, "Content-Type": "application/json"}}
    with TestClient(app, base_url="http://172.31.250.34:8000", client=("172.31.6.3", 40000)) as place:
        for path in ("/pcbk/api/auth/me", "/pcbk/api/workplace", "/health/gateway", "/health/web"):
            r = place.get(path)
            assert (r.status_code, r.json()["name"]) == (404, "NotFound"), path
        r = place.post("/pcbk/api/auth/login", json={"login": "stu-03", "password": PW}, headers={"Origin": ORIGIN})
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")
        assert place.post("/pcbk/api/auth/login", **big).status_code == 404
        r = place.post("/pcbk/api/web/report", json={"kind": "ui_crash"}, headers={"Origin": ORIGIN})   # Д6б
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")
        r = place.post("/pcbk/api/auth/notice", json={"notice": "teacher-reads-1"}, headers={"Origin": ORIGIN})   # Б2-m4
        assert (r.status_code, r.json()["name"]) == (404, "NotFound")
    for client in (("172.31.250.35", 40000), ("172.31.250.36", 40000)):                # edge, сторож
        with TestClient(app, base_url="http://172.31.250.34:8000", client=client) as front:
            assert front.get("/pcbk/api/auth/me").status_code == 401, client
            assert front.get("/health/gateway").status_code == 200, client
            assert front.get("/health/web").status_code == 200, client
            assert front.post("/pcbk/api/web/report", json={"kind": "ui_crash"}).status_code == 401, client
            assert front.post("/pcbk/api/auth/notice", json={"notice": "teacher-reads-1"},
                              headers={"Origin": ORIGIN}).status_code == 401, client
            assert front.post("/pcbk/api/auth/login", **big).status_code == 413, client
    with TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 40000)) as loop:   # петля
        assert loop.get("/health/gateway").status_code == 200
        assert loop.get("/health/web").status_code == 200
    with TestClient(app, base_url="http://172.31.250.82:8000", client=("172.31.250.81", 40000)) as host:
        assert host.get("/health/gateway").status_code == 404                          # pcbk-egress — не front
        assert host.get("/health/web").status_code == 404
        assert host.post("/pcbk/api/web/report", json={"kind": "ui_crash"}).status_code == 404
        assert host.post("/pcbk/api/auth/notice", json={"notice": "teacher-reads-1"},
                         headers={"Origin": ORIGIN}).status_code == 404


def test_health_llm_full_only_for_admin_client(tmp_path, fake_or):                     # M3: /health/llm
    app, _ = make_llm_app(tmp_path, fake_or)
    money = {"account_remaining_usd", "key_usage_usd", "key_spent_usd"}
    for host, client, full in (("172.31.250.82", "172.31.250.81", True),               # хост сервера
                               ("172.31.250.34", "172.31.250.36", True),               # сторож
                               ("127.0.0.1", "127.0.0.1", True),                       # петля
                               ("172.31.250.82", "172.31.6.3", False),                 # место на адрес в pcbk-egress
                               ("172.31.250.34", "172.31.6.3", False)):                # … и в pcbk-front
        with TestClient(app, base_url=f"http://{host}:8000", client=(client, 40000)) as c:
            keys = set(c.get("/health/llm").json())
            assert (money & keys) == (money if full else set()) and "spent_usd" in keys, (host, client)
