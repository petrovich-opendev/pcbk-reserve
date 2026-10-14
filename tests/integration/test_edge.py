"""Прокси входа 8443 и сторож в компоновке: страница, своя страница при смерти сторожа, сети, статика чата; «Мои
агенты» (Д7б)."""
import hashlib
import json
import re
import secrets
import time

from conftest import REPO
from secret import Secret

DECLARED_ENV = {                   # наши переменные сверх переменных базового образа — ровно эти
    "pcbk-watchdog": {"LISTEN_PORT", "TICK_S", "STALE_AFTER_S", "DISPLAY_TZ", "DOCKER_URL",
                      "DOCKER_TIMEOUT_S", "MEM_WARN_MIB", "MEM_FAIL_MIB", "TLS_CAFILE",
                      "JOURNAL_PATH", "MEMINFO_PATH", "COMPONENTS_PATH", "DRILL_FREEZE_LOOP",
                      "HIST_WARN_S", "HIST_FAIL_S", "HIST_STALE_S",
                      "GW_STALE_S",                                    # строка «Шлюз и вход» (Д5а)
                      "AGENTS_STALE_S",                                # строка «Агенты студентов» (Д7а, П10)
                      "LLM_RUN_SILENT_S",                              # «прогон отбора молчит» (Д8а)
                      "TEACHER_STALE_S",                               # «Кабинет преподавателя и доли» (Д10а)
                      # «Память сервера» v2 (Д11а): счётчик убийств и давление, пороги swap и давления
                      "VMSTAT_PATH", "PRESSURE_PATH", "SWAP_GROW_WARN_MIB", "PSI_FULL_MAX"},
    "pcbk-core": {"CORE_PORT", "FRESH_POLL_S", "CATALOG_DEADLINE_S", "DRILL_FRESHNESS",
                  "DRILL_DATA",                                    # учения службы данных (Д9а, Д9-R83)
                  "LLM_MODELS", "LLM_FALLBACK",                 # запасная модель при отказе основной (Д14)
                  "LLM_STATE", "DRILL_LLM", "LLM_OPS_SHARE_USD", "LLM_KEY_DEDICATED",
                  # шлюз (Д5а): сети мест для сверки токенов и адресов мест, origin входа, простой, такт, учения
                  "STU_NET", "GW_ORIGIN", "GW_IDLE_STOP_S", "GW_REAPER_TICK_S", "DRILL_GATEWAY",
                  "DRILL_AGENTS",                                  # учения агентов студентов (Д7а, П10)
                  "DRILL_TEACHER",                                 # учения кабинета преподавателя (Д10а)
                  "LLM_RUN_ID", "LLM_RUN_USD", "LLM_RUN_CALLERS",  # прогон отбора модели (Д8-R69)
                  # тормоз прогона (Д11-R117): пороги — вложенным умолчанием compose.yaml
                  "LLM_RUN_MEM_FAIL_MIB", "LLM_RUN_SWAP_GROW_MIB", "LLM_RUN_SWAP_BASE_MIB", "LLM_RUN_PSI_FULL_MAX",
                  # общий бюджет LLM-прокси, $ (Д15б): теперь в compose.yaml — не только тестовый стенд
                  "LLM_TOTAL_USD",
                  # аналитический чат технолога КДМ-1 (Task 9): ANALYTICS_CORPUS_DIR/ANALYTICS_STATE_PATH сюда
                  # не передаются (путь внутри контейнера фиксирован умолчанием settings.py, как LESSONS_ROOT)
                  "ANALYTICS_ENABLED", "ANALYTICS_LLM_MODELS", "ANALYTICS_LLM_EXPORT_POLICY",
                  "ANALYTICS_REVIEWERS", "ANALYTICS_TEACHER_PAYER", "ANALYTICS_HISTORY_RETENTION_DAYS",
                  "ANALYTICS_EVIDENCE_RETENTION_DAYS", "ANALYTICS_JOB_TIMEOUT_S",
                  # только тестовый стенд (compose.test.yaml): двойник OpenRouter, частый опрос;
                  # обёртка запуска и двойник историана (Д8-R75)
                  "OPENROUTER_BASE_URL", "LLM_POLL_S", "PYTHONPATH", "PCBK_TWIN_HISTORIAN",
                  # двойники /proc тормоза прогона (Д11-R123а) — только тестовый стенд
                  "LLM_RUN_MEMINFO", "LLM_RUN_VMSTAT", "LLM_RUN_PRESSURE"},
    "pcbk-edge": set(),
    "pcbk-sp-ro": set(),
    "pcbk-sp-ctl": set(),
    "pcbk-student-01": set(),
}
IMAGES = {"pcbk-watchdog": "pcbk-reserve/watchdog:d15b", "pcbk-core": "pcbk-reserve/core:d17",
          "pcbk-edge": "nginx:1.30.5-alpine",
          "pcbk-sp-ro": "wollomatic/socket-proxy:1.13.1", "pcbk-sp-ctl": "wollomatic/socket-proxy:1.13.1",
          "pcbk-student-01": "pcbk-reserve/student:d15b"}
WATCHDOG_PATHS = ("/status", "/status.json", "/status.js")
# CSP страницы чата — строка Global Constraints Д6 (Д6-R41)
CHAT_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
            "font-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
IMMUTABLE = "public, max-age=31536000, immutable"


def test_edge_serves_status_from_watchdog(stack):
    code, body = stack.https("/status")
    assert code == 200 and "Входной прокси" in body and "LLM-прокси и бюджет" in body
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


def test_edge_temp_files_on_disk_volume(stack):
    """Д5б: временные файлы тел и ответов core — на томе (диск), не в tmpfs 64m; каталоги создал мастер для рабочих."""
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-edge")["Mounts"]}
    temp = mounts["/var/lib/pcbk-edge"]
    assert (temp["Type"], temp["Name"], temp["RW"]) == ("volume", "pcbk-test_pcbk-edge-temp", True)
    assert "/var/lib/pcbk-edge" not in stack.inspect("pcbk-edge")["HostConfig"]["Tmpfs"]
    for d in ("client", "proxy"):
        assert stack.exec("pcbk-edge", "stat", "-c", "%a %U", f"/var/lib/pcbk-edge/{d}") == "700 nginx", d


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


def test_edge_serves_chat_under_csp(stack):                                # Д6-R39, R41; раунд 2, В3: кэш и сжатие
    build = json.loads((stack.static_dir / "chat/build.json").read_text(encoding="utf-8"))
    assert build["build"].startswith("test-")                              # сборка фикстуры, а не общего дерева
    code, body, h = stack.https_full("/chat/")
    assert (code, h["content-security-policy"], h["cache-control"], h["x-content-type-options"], h["referrer-policy"]) \
        == (200, CHAT_CSP, "no-store", "nosniff", "no-referrer")
    assert h["content-type"] == "text/html; charset=utf-8"
    assert f'<meta name="pcbk-build" content="{build["build"]}">' in body
    assets = [p for p in build["files"] if p.startswith("assets/")]
    assert assets                                                          # пустая опись — цикл ничего бы не проверил
    for path in assets:
        code, _, h = stack.https_full("/chat/" + path, headers={"Accept-Encoding": "gzip"})
        assert code == 200 and h["content-security-policy"] == CHAT_CSP, path
        assert (h["cache-control"], h["x-content-type-options"], h["referrer-policy"]) == \
            (IMMUTABLE, "nosniff", "no-referrer"), path
        assert h["content-type"].split(";")[0] in ("application/javascript", "text/javascript", "text/css"), path
        assert (h.get("content-encoding"), h.get("vary")) == ("gzip", "Accept-Encoding"), path
        code, raw, h = stack.https_full("/chat/" + path)                   # без Accept-Encoding — как есть
        assert code == 200 and h.get("content-encoding") is None and h["cache-control"] == IMMUTABLE, path
        assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == build["files"][path], path
    code, _, h = stack.https_full("/chat/assets/missing-00000000.js")
    assert (code, h["cache-control"]) == (404, "no-store")                 # отказ не кэшируется на год
    for path in ("/chat/build.json", "/chat/index.html"):                  # опись — только 8080; страница — только /chat/
        code, _, h = stack.https_full(path)
        assert (code, h["content-type"]) == (404, "text/plain; charset=utf-8"), path   # тип — не по расширению
    code, _, h = stack.https_full("/chat")
    assert (code, h["location"]) == (301, "/chat/")                        # относительный: туннель не уводит на 8443
    # CSP R41 и заголовки — у всех ответов /chat*, и у перенаправления и 404 (итоговое ревью Д6а, P3)
    for path, want in (("/chat", 301), ("/chat/build.json", 404), ("/chat/index.html", 404),
                       ("/chat/assets/missing-00000000.js", 404)):
        code, _, h = stack.https_full(path)
        assert (code, h.get("content-security-policy"), h.get("cache-control"), h.get("x-content-type-options"),
                h.get("referrer-policy")) == (want, CHAT_CSP, "no-store", "nosniff", "no-referrer"), path
    code, _, h = stack.https_full("/favicon.ico")
    assert (code, h["cache-control"]) == (204, "no-store")                 # решение по А4: иконки нет — не 404
    assert stack.http_from_watchdog("GET", "http://pcbk-edge:8080/chat/build.json") == 200   # из сети сторожа (M13)


def test_edge_serves_boot_js_no_store(stack):                               # Д6-R51: загрузчик без хеша — без кэша
    build = json.loads((stack.static_dir / "chat/build.json").read_text(encoding="utf-8"))
    assert "boot.js" in build["files"]                                     # в описи — иначе сверять не с чем
    code, raw, h = stack.https_full("/chat/boot.js", headers={"Accept-Encoding": "gzip"})
    assert (code, h["content-security-policy"], h["cache-control"], h["x-content-type-options"], h["referrer-policy"]) \
        == (200, CHAT_CSP, "no-store", "nosniff", "no-referrer")
    assert h["content-type"] == "application/javascript; charset=utf-8"    # русский текст сбоя — charset
    assert h.get("content-encoding") is None                               # как есть, без gzip ассетов
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == build["files"]["boot.js"]


def test_edge_routes_web_report(stack):                                     # Д6-R50: отчёты браузеров через edge
    gw = stack.gw_client()                                                  # без сессии, Origin стенда
    code, body, h = gw.call("POST", "/pcbk/api/web/report", {"kind": "turn_silent"})
    assert (code, body["name"], h["content-type"]) == (401, "Unauthorized", "application/json"), (code, body)
    code, body, h = gw.call("POST", "/pcbk/api/web/report", {"kind": "x" * 2048})   # тело 2 КиБ — до core не дошло
    assert (code, body["name"], h["content-type"]) == (413, "PayloadTooLarge", "application/json"), (code, body)
    code, body, h = gw.call("POST", "/pcbk/api/web/other", {"kind": "turn_silent"})
    assert (code, body["name"], h["content-type"]) == (404, "NotFound", "application/json"), (code, body)
    code, body, _ = gw.call("POST", "/pcbk/api/web//report", {"kind": "turn_silent"})   # сырой путь — как у прочих
    assert (code, body["name"]) == (400, "BadRequest"), (code, body)
    # предел частоты (корзина «-» без cookie, 1 r/s, запас 10): отказ — 509 → busy.json, 503 WorkplaceBusy, не 429
    codes = [gw.call("POST", "/pcbk/api/web/report", {"kind": "turn_silent"})[:2] for _ in range(20)]
    assert {c for c, _ in codes} == {401, 503}, [c for c, _ in codes]
    assert {b["name"] for c, b in codes if c == 503} == {"WorkplaceBusy"}


def test_edge_routes_my_agents(stack):                                     # Д7б, Д7-R64; preflight П13
    """«Мои агенты» через edge: без cookie — 401 JSON шлюза (тело и текст шлюза, а не страница edge); преподаватель (без
    места) — 403 JSON с текстом Д10-R100 (агенты студентов — в кабинете); «//» в сыром пути — 400 edge до core. Ответ
    200 с cookie студента — в test_web (web-a): своей учётки студента у модуля нет — свободных мест на стенде нет (П13)."""
    gw = stack.gw_client()                                                  # без сессии, Origin стенда
    code, body, h = gw.call("GET", "/pcbk/api/agents")
    assert (code, body, h["content-type"]) == \
        (401, {"name": "Unauthorized", "data": {"message": "Нужно войти"}}, "application/json"), (code, body)
    code, body, h = gw.call("GET", "/pcbk/api//agents")                    # сырой путь — как у прочих ручек
    assert (code, body["name"], h["content-type"]) == (400, "BadRequest", "application/json"), (code, body)
    password = Secret(secrets.token_urlsafe(12))
    stack.add_account("edge-t", "teacher", None, password)
    t = stack.gw_client()
    t.login("edge-t", password)
    try:
        code, body, h = t.call("GET", "/pcbk/api/agents")
        assert (code, body, h["content-type"]) == \
            (403, {"name": "Forbidden", "data": {"message": "Агенты студентов — в кабинете преподавателя, только на "
                                                            "чтение"}}, "application/json"), (code, body)
    finally:
        t.logout()


def test_edge_limits_me_and_notice_by_cookie_or_address(stack):            # итоговая доводка Д6б, m2; preflight Н17
    """«Кто я» и подтверждение предупреждения — своя зона: ключ — cookie сессии своего вида, без неё — адрес клиента
    (общей корзины «-» нет). 30 запросов /me без cookie с одного адреса (класс открыл страницу входа) укладываются в
    предел адреса; дальше — 503 WorkplaceBusy, а не 429 входа; запросы с cookie в это время — своя корзина. Корзину
    входа по IP эти ручки не тратят: выход с того же адреса — ответ шлюза (выход, а не вход: неудачный вход растил бы
    счётчики строки «Шлюз и вход» общего стенда)."""
    gw = stack.gw_client()                                                  # без сессии, Origin стенда
    cookie = {"Cookie": Secret(f"pcbk_session={secrets.token_urlsafe(32)}")}   # своего вида, сессии нет — 401 шлюза
    codes = [gw.call("GET", "/pcbk/api/auth/me")[0] for _ in range(30)]
    assert set(codes) == {401}, codes                                       # класс за одним адресом — в пределе
    bare, own = [], []
    for i in range(60):                     # адрес исчерпан, а cookie — своя корзина (её 20 запросов — в запасе 40)
        bare.append(gw.call("GET", "/pcbk/api/auth/me")[:2])
        if i % 3 == 2:
            own.append(gw.call("GET", "/pcbk/api/auth/me", headers=cookie)[:2])
    assert {c for c, _ in bare} == {401, 503}, [c for c, _ in bare]
    assert {b["name"] for c, b in bare if c == 503} == {"WorkplaceBusy"}
    assert {(c, b["name"]) for c, b in own} == {(401, "Unauthorized")}, [c for c, _ in own]
    assert any(bare[i][0] == 503 for i in range(2, 60, 3))                  # cookie прошла там, где адрес уже отказывал
    notice = [gw.call("POST", "/pcbk/api/auth/notice", {"notice": "teacher-reads-1"})[:2] for _ in range(20)]
    assert 503 in {c for c, _ in notice} and {c for c, _ in notice} <= {401, 503}, [c for c, _ in notice]   # та же зона
    code, body, _ = gw.call("POST", "/pcbk/api/auth/notice", {"notice": "teacher-reads-1"}, headers=cookie)
    assert (code, body["name"]) == (401, "Unauthorized")
    code, body, _ = gw.call("POST", "/pcbk/api/auth/logout")
    assert (code, body) == (200, {}), (code, body)                          # корзина входа по IP не тронута
    assert 'by zone "pcbk_auth_session"' in stack.logs("pcbk-edge")        # отказы — этой зоны, а не входа
    time.sleep(10)      # корзина адреса опустеет (запас 40, 5 r/s): следующие модули открывают /login без cookie


# Д12-R137: компоновка сервера ничем не держится за Dify (успех 3)


def dify_ties(cfg: dict, roots: list[str]) -> list[str]:
    """Что в итоговой компоновке связывает стенд с чужим: сети, тома, привязки, env_file, configs, секреты, режим сети."""
    inside = lambda p: any(p == r or p.startswith(r.rstrip("/") + "/") for r in roots)
    out = []
    for name, s in cfg["services"].items():
        if s.get("network_mode") or s.get("external_links") or s.get("links"):
            out.append(f"{name}: сеть хоста или ссылки")
        if s.get("env_file"):
            out.append(f"{name}: env_file")
        out += [f"{name}: зависит от {d}" for d in set(s.get("depends_on") or {}) - set(cfg["services"])]
        for v in s.get("volumes") or []:
            if v["type"] == "bind" and v["source"] != "/var/run/docker.sock" and not inside(v["source"]):
                out.append(f"{name}: привязка {v['source']}")
            if v["type"] == "volume" and not v["source"].startswith("pcbk-"):
                out.append(f"{name}: том {v['source']}")
    out += [f"сеть {n}" for n, net in cfg["networks"].items()
            if net.get("external") or not (net.get("name") or n).startswith("pcbk-")]
    out += [f"том {n}" for n, v in cfg["volumes"].items()
            if v.get("external") and not re.fullmatch(r"pcbk-student-(0[1-9]|1[01])-(state|work)", v.get("name") or n)]
    out += [f"секрет {n}" for n, sec in (cfg.get("secrets") or {}).items() if not inside(sec.get("file", ""))]
    out += [f"configs {n}" for n in (cfg.get("configs") or {})]
    return out


def test_production_config_independent_of_dify(stack, tmp_path):
    """Сети, тома, привязки, секреты compose.yaml — только свои; env_file и configs нет; слова dify нет вне комментариев."""
    env = stack.test_env()
    # LESSONS_DIR (Д15а) — образец пакета в git репозитория, а не временный каталог теста: своя строка roots
    roots = [env[k] for k in ("TLS_DIR", "DATA_DIR", "AGENTS_DIR", "SECRETS_DIR", "LESSONS_DIR")] + [str(REPO / "edge")]
    if env.get("ANALYTICS_CORPUS_DIR"):                   # корпус аналитики стенда (Task 9), если задан
        roots.append(env["ANALYTICS_CORPUS_DIR"])
    assert dify_ties(stack.prod_config(), roots) == []
    text = "\n".join(line.split("#", 1)[0] for line in (REPO / "compose.yaml").read_text(encoding="utf-8").splitlines())
    assert "dify" not in text.lower()
    bad = tmp_path / "compose.bad.yaml"                                   # положительный контроль
    bad.write_text("services:\n  core:\n    networks: {dify: {}}\n"
                   "    volumes: [{type: bind, source: /opt/dify/docker/volumes, target: /x}]\n"
                   "networks:\n  dify: {name: docker_default, external: true}\n", encoding="utf-8")
    ties = dify_ties(stack.config(files=("compose.yaml", str(bad))), roots)
    assert "сеть dify" in ties and any("/opt/dify" in t for t in ties), ties
