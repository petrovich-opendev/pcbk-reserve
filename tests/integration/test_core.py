"""Серверный слой в компоновке: строки сторожа, ручки здоровья, сеть выхода, секрет и список файлами."""
import json

CORE = "http://172.31.250.82:8000"


def row(data, cid):
    return next((c["state"], c["detail"]) for c in data["checks"] if c["component"] == cid)


def test_status_shows_core_alive_and_historian_down(stack):
    data = stack.wait_status(lambda d: row(d, "historian")[0] == "fail", timeout=90)
    assert row(data, "core") == ("ok", "жива") and row(data, "historian") == ("fail", "нет связи с историаном")


def test_core_health_routes_on_egress_address(stack):
    assert stack.http_host("GET", CORE + "/healthz")[0] == 200
    code, body = stack.http_host("GET", CORE + "/healthz/data")
    assert code == 200 and json.loads(body)["ok"] is True
    j = json.loads(stack.http_host("GET", CORE + "/health/historian")[1])
    assert j["catalog_error"] == "connect" and j["gate"]["refused_unlisted"] == 0


def test_edge_does_not_expose_core(stack):
    for path in ("/healthz/data", "/health/historian", "/api/data/tag_now", "/mcp"):
        assert stack.https(path)[0] == 404


def test_egress_network_only_core(stack):
    n = stack.network("pcbk-egress")
    assert n["Internal"] is False
    assert n["Options"].get("com.docker.network.bridge.enable_ip_masquerade", "true") == "true"
    assert [c["Name"] for c in n["Containers"].values()] == ["pcbk-core"]
    nets = stack.inspect("pcbk-core")["NetworkSettings"]["Networks"]
    assert set(nets) == {"pcbk-front", "pcbk-egress"} and nets["pcbk-egress"]["IPAddress"] == "172.31.250.82"


def test_core_secret_and_list_are_readonly_files(stack):
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    for dest in ("/run/secrets/bdrv.env", "/app/data/whitelist.txt"):
        assert (mounts[dest]["Type"], mounts[dest]["RW"]) == ("bind", False), dest
    prod = stack.prod_config()["services"]["core"]
    assert "env_file" not in prod and "BDRV" not in json.dumps(prod.get("environment", {}))
    assert prod["pull_policy"] == "never"


def test_oneshot_alias_keeps_sp_ctl_controls(stack):               # настоящий pcbk-core уже работает
    assert stack.http_as("pcbk-core", "pcbk-ctl", "GET", "http://pcbk-sp-ctl:2375/v1.44/_ping") == 200
    assert stack.http_as("pcbk-intruder", "pcbk-ctl", "GET", "http://pcbk-sp-ctl:2375/v1.44/_ping") == 403


def test_stop_core_turns_row_red_historian_unknown(stack):
    stack.stop("pcbk-core")
    try:
        data = stack.wait_status(lambda d: row(d, "core")[0] == "fail", timeout=40)
        assert row(data, "historian") == ("unknown", "служба данных не отвечает — свежесть неизвестна")
    finally:
        stack.start("pcbk-core")
