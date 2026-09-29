"""Две копии узкого прокси сокета: кто, каким методом и к чему проходит; сети стенда."""
import pytest

RO = "http://pcbk-sp-ro:2375/v1.44"
CTL = "http://pcbk-sp-ctl:2375/v1.44"
PASSED = (200, 204, 304, 404)      # прокси пропустил; ответ уже от Docker
# таблица сетей из плана — одно место правды в compose.yaml, здесь сверка
SUBNETS = {"pcbk-public": "172.31.250.16/28", "pcbk-front": "172.31.250.32/28",
           "pcbk-ro": "172.31.250.48/28", "pcbk-ctl": "172.31.250.64/28"}


def test_sp_ro_serves_watchdog_reads_only(stack):
    assert stack.http_from_watchdog("GET", RO + "/containers/pcbk-sp-ctl/json") == 200
    assert stack.http_from_watchdog("GET", RO + "/containers/pcbk-student-01/json") in PASSED
    for path in ("/containers/pcbk-test-foreign/json", "/containers/pcbk-watchdog/json",
                 "/containers/pcbk-sp-ctl/logs", "/containers/json", "/events"):
        assert stack.http_from_watchdog("GET", RO + path) == 403
    assert stack.http_from_watchdog("POST", RO + "/containers/pcbk-student-01/start") == 405


def test_sp_ro_refuses_other_clients(stack):
    assert stack.http_as("pcbk-intruder", "pcbk-ro", "GET", RO + "/containers/pcbk-sp-ctl/json") == 403


def test_sp_ctl_passes_only_student_start_stop(stack):
    for verb in ("start", "stop"):
        assert stack.http_as("pcbk-core", "pcbk-ctl", "POST",
                             CTL + f"/containers/pcbk-student-01/{verb}") in PASSED
    assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", CTL + "/containers/pcbk-sp-ro/stop") == 403
    assert stack.http_as("pcbk-core", "pcbk-ctl", "GET", CTL + "/containers/pcbk-test-foreign/json") == 403
    assert stack.http_as("pcbk-core", "pcbk-ctl", "GET", CTL + "/containers/json") == 403
    assert stack.http_as("pcbk-intruder", "pcbk-ctl", "GET", CTL + "/_ping") == 403


@pytest.mark.parametrize("method,path", [
    ("POST", "/containers/create"),
    ("POST", "/containers/pcbk-student-01/exec"),
    ("POST", "/containers/pcbk-student-01/kill"),
    ("POST", "/containers/pcbk-student-01/update"),
    ("POST", "/containers/pcbk-student-01/start/../../create"),
    ("POST", "/containers/pcbk-student-01%2F..%2F..%2Fcreate"),
    ("POST", "/volumes/create"),
    ("POST", "/images/create"),
    ("DELETE", "/containers/pcbk-student-01"),
])
def test_sp_ctl_refuses_dangerous_calls(stack, method, path):
    assert stack.http_as("pcbk-core", "pcbk-ctl", method, CTL + path) in (403, 405)


def test_container_networks_exact(stack):
    expected = {"pcbk-edge": {"pcbk-public", "pcbk-front"}, "pcbk-watchdog": {"pcbk-front", "pcbk-ro"},
                "pcbk-sp-ro": {"pcbk-ro"}, "pcbk-sp-ctl": {"pcbk-ctl"}}
    for name, nets in expected.items():
        assert set(stack.inspect(name)["NetworkSettings"]["Networks"]) == nets
    for net in ("pcbk-front", "pcbk-ro", "pcbk-ctl"):
        n = stack.network(net)
        assert n["Internal"] is True
        assert n["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    assert stack.host_bridge_addresses("pcbk-front", "pcbk-ro", "pcbk-ctl") == []


def test_network_subnets_match_table(stack):
    for net, subnet in SUBNETS.items():
        assert [c["Subnet"] for c in stack.network(net)["IPAM"]["Config"]] == [subnet], net


def test_status_json_overall_ok(stack):
    data = stack.wait_status(lambda d: d["overall"] == "ok", timeout=30)
    assert {c["component"] for c in data["checks"] if c["state"] == "absent"} >= {"core", "student-01"}


def test_watchdog_sees_socket_proxy_loss(stack):
    stack.stop("pcbk-sp-ro")
    try:
        data = stack.wait_status(lambda d: d["overall"] == "fail", timeout=30)
        states = {c["component"]: c["state"] for c in data["checks"]}
        assert states["sp-ro"] == "fail" and states["sp-ctl"] == "unknown"
    finally:
        stack.start("pcbk-sp-ro")
