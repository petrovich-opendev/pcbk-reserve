"""Прокси входа 8443 и сторож в компоновке: страница, своя страница при смерти сторожа, сети."""
import re
import time

DECLARED_ENV = {                   # наши переменные сверх переменных базового образа
    "pcbk-watchdog": {"LISTEN_PORT", "TICK_S", "STALE_AFTER_S", "DISPLAY_TZ", "DOCKER_URL",
                      "DOCKER_TIMEOUT_S", "MEM_WARN_MIB", "MEM_FAIL_MIB", "TLS_CAFILE",
                      "JOURNAL_PATH", "MEMINFO_PATH", "COMPONENTS_PATH"},
    "pcbk-edge": set(),
}


def test_edge_serves_status_from_watchdog(stack):
    code, body = stack.https("/status")
    assert code == 200 and "Входной прокси" in body and "ещё не установлен" in body
    assert stack.https("/status.js")[0] == 200


def test_root_shows_stand_warning(stack):
    assert "учебный стенд" in stack.https("/")[1].lower()


def test_watchdog_checks_edge_tls(stack):
    data = stack.wait_status(lambda d: any(c["component"] == "edge" and c["state"] != "unknown"
                                           for c in d["checks"]), timeout=30)
    edge = next(c for c in data["checks"] if c["component"] == "edge")
    assert edge["state"] == "ok" and "дн." in edge["detail"]


def test_edge_shows_watchdog_down_page(stack):
    stack.stop("pcbk-watchdog")
    try:
        code, body = stack.https("/status")
        assert code in (502, 504) and "Сторож не отвечает" in body and "nginx" not in body.lower()
    finally:
        stack.start("pcbk-watchdog")


def test_edge_starts_without_watchdog(stack):
    stack.stop("pcbk-watchdog")
    try:
        stack.restart("pcbk-edge")
        assert stack.https("/status")[0] in (502, 504)
    finally:
        stack.start("pcbk-watchdog")


def test_hung_watchdog_gives_down_page_within_timeout(stack):
    stack.pause("pcbk-watchdog")
    try:
        started = time.monotonic()
        code, body = stack.https("/status")
        assert code == 504 and "Сторож не отвечает" in body and time.monotonic() - started < 10
    finally:
        stack.unpause("pcbk-watchdog")


def test_only_edge_publishes_one_port(stack):
    published = {n: [p for p in (stack.inspect(n)["NetworkSettings"]["Ports"] or {}).values() if p]
                 for n in stack.containers()}
    assert {n: len(p) for n, p in published.items() if p} == {"pcbk-edge": 1}


def test_networks_d1_front(stack):
    assert set(stack.inspect("pcbk-edge")["NetworkSettings"]["Networks"]) == {"pcbk-public", "pcbk-front"}
    front = stack.network("pcbk-front")
    assert front["Internal"] is True
    assert front["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
    assert stack.network("pcbk-public")["Options"]["com.docker.network.bridge.enable_ip_masquerade"] == "false"
    # положительный контроль: у публичной сети адрес на мосту есть — проверка видит адреса
    assert stack.host_bridge_addresses("pcbk-public")
    assert stack.host_bridge_addresses("pcbk-front") == []


def test_env_holds_only_known_names(stack):
    for name, image in (("pcbk-watchdog", "pcbk-reserve/watchdog:d1"), ("pcbk-edge", "nginx:1.30.5-alpine")):
        extra = stack.env_names(name) - stack.image_env_names(image) - DECLARED_ENV[name]
        assert extra == set(), f"{name}: лишние переменные {extra}"
        assert not {n for n in stack.env_names(name) if re.search(r"PASSWORD|SECRET|TOKEN|APIKEY", n)}
