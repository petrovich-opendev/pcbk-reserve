"""Прокси входа 8443 и сторож в компоновке: страница, своя страница при смерти сторожа, сети."""
import re
import time

DECLARED_ENV = {                   # наши переменные сверх переменных базового образа — ровно эти
    "pcbk-watchdog": {"LISTEN_PORT", "TICK_S", "STALE_AFTER_S", "DISPLAY_TZ", "DOCKER_URL",
                      "DOCKER_TIMEOUT_S", "MEM_WARN_MIB", "MEM_FAIL_MIB", "TLS_CAFILE",
                      "JOURNAL_PATH", "MEMINFO_PATH", "COMPONENTS_PATH", "DRILL_FREEZE_LOOP",
                      "HIST_WARN_S", "HIST_FAIL_S", "HIST_STALE_S"},
    "pcbk-core": {"CORE_PORT", "FRESH_POLL_S", "CATALOG_DEADLINE_S", "DRILL_FRESHNESS"},
    "pcbk-edge": set(),
    "pcbk-sp-ro": set(),
    "pcbk-sp-ctl": set(),
    "pcbk-student-01": set(),
}
IMAGES = {"pcbk-watchdog": "pcbk-reserve/watchdog:d3a", "pcbk-core": "pcbk-reserve/core:d3a",
          "pcbk-edge": "nginx:1.30.5-alpine",
          "pcbk-sp-ro": "wollomatic/socket-proxy:1.13.1", "pcbk-sp-ctl": "wollomatic/socket-proxy:1.13.1",
          "pcbk-student-01": "pcbk-reserve/student:d2"}
WATCHDOG_PATHS = ("/status", "/status.json", "/status.js")


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
        for path in WATCHDOG_PATHS:
            code, body = stack.https(path)
            assert code in (502, 504) and "Сторож не отвечает" in body and "nginx" not in body.lower(), path
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
        elapsed = time.monotonic() - started
        # ≥ 4 с — ответ дал срок чтения proxy_read_timeout, а не мгновенный отказ соединения
        assert code == 504 and "Сторож не отвечает" in body and 4 <= elapsed < 10, elapsed
    finally:
        stack.unpause("pcbk-watchdog")


def test_only_edge_publishes_one_port(stack):
    published = {n: [p for p in (stack.inspect(n)["NetworkSettings"]["Ports"] or {}).values() if p]
                 for n in stack.containers()}
    assert {n: len(p) for n, p in published.items() if p} == {"pcbk-edge": 1}


def test_production_config_publishes_only_edge_8443(stack):
    # compose.test.yaml подменяет порты edge — боевые видны только в compose.yaml без него
    services = stack.config(files=("compose.yaml",))["services"]
    ports = {name: s["ports"] for name, s in services.items() if s.get("ports")}
    assert list(ports) == ["edge"]
    assert [(p["target"], str(p["published"]), p.get("protocol", "tcp")) for p in ports["edge"]] == \
        [(8443, "8443", "tcp")]
    assert not [name for name, s in services.items() if s.get("network_mode") == "host"]


def test_status_headers_single_no_store_nosniff(stack):
    for path in ("/status", "/status.json"):
        code, headers = stack.https_headers(path)
        cache = headers.get_all("Cache-Control") or []
        assert code == 200 and len(cache) == 1 and "no-store" in cache[0], (path, cache)
        assert headers.get_all("X-Content-Type-Options") == ["nosniff"], path


def test_edge_tmpfs_root_owned_0755(stack):
    paths = ("/etc/nginx/conf.d", "/var/cache/nginx", "/var/run")
    # режим задан явно, а не унаследован от каталога образа (у runc он копируется с каталога)
    assert stack.inspect("pcbk-edge")["HostConfig"]["Tmpfs"] == {p: "mode=0755" for p in paths}
    for path in paths:
        assert stack.exec("pcbk-edge", "stat", "-L", "-c", "%a %U", path) == "755 root", path


def test_edge_limits(stack):
    host = stack.inspect("pcbk-edge")["HostConfig"]
    assert (host["Memory"], host["PidsLimit"]) == (64 * 1024 * 1024, 64)


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
    for name, image in IMAGES.items():
        added = stack.env_names(name) - stack.image_env_names(image)
        assert added == DECLARED_ENV[name], f"{name}: сверх образа {added}"
        assert not {n for n in stack.env_names(name) if re.search(r"PASSWORD|SECRET|TOKEN|APIKEY", n)}
