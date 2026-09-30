"""Зоны адресов (Д4б-R23, Д5-R31, Ш25): куда пришло соединение (scope["server"]) и откуда токен места
(scope["client"]). Закрыто при сомнении: имя вместо адреса — чужое, кроме приложения с пометкой тестов."""
import ipaddress
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from helpers import SETTINGS, gateway_role, serve_app
from pcbk_core import zones
from pcbk_core.app import create_app
from pcbk_core.zones import caller_net_ok, is_admin, is_front


def app_with(names_ok: bool):
    return SimpleNamespace(state=SimpleNamespace(zone_names_ok=True) if names_ok else SimpleNamespace())


def test_zones():
    prod = app_with(False)
    for host, front, admin in (("172.31.250.34", True, True), ("127.0.0.1", True, True), ("::1", True, True),
                               ("172.31.250.82", False, True), ("172.31.6.2", False, False),
                               ("172.31.250.66", False, False), ("testserver", False, False)):   # имя — чужое
        scope = {"server": (host, 8000), "app": prod}
        assert (is_front(scope), is_admin(scope)) == (front, admin), host
    assert (is_front({"server": None, "app": prod}), is_admin({"server": None, "app": prod})) == (False, False)
    opted = {"server": ("testserver", 80), "app": app_with(True)}                    # явная пометка тестов
    assert (is_front(opted), is_admin(opted)) == (True, True)


def test_hostname_fails_closed_without_opt_in(tmp_path):                                # решение контролёра р2
    app = create_app(SETTINGS, [gateway_role(tmp_path)])                               # как в проде: без пометки
    assert getattr(app.state, "zone_names_ok", False) is False
    with TestClient(app) as c:                                                         # имя «testserver»
        assert c.get("/pcbk/api/auth/me").status_code == 404
        assert c.get("/health/gateway").status_code == 404


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
    assert is_front({"server": ("::ffff:172.31.250.34", 8000), "app": app_with(False)})   # IPv4 в IPv6 — как IPv4


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
