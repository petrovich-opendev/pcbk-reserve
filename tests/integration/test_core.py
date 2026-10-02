"""Серверный слой в компоновке: строки сторожа, ручки здоровья, сеть выхода, секрет и список файлами."""
import ast
import json
import re
import time
from pathlib import Path

from conftest import wait_for

CORE = "http://172.31.250.82:8000"
CTL = "http://pcbk-sp-ctl:2375/v1.44"
ZONES_PY = Path(__file__).resolve().parents[2] / "core" / "pcbk_core" / "zones.py"


def row(data, cid):
    return next((c["state"], c["detail"]) for c in data["checks"] if c["component"] == cid)


def test_status_shows_core_alive_and_historian_down(stack):
    data = stack.wait_status(lambda d: row(d, "historian")[0] == "fail", timeout=90)
    assert row(data, "core") == ("ok", "жива") and row(data, "historian") == ("fail", "нет связи с историаном")


def test_core_health_routes_on_egress_address(stack):
    # в /healthz с Д5а и здоровье шлюза: первые секунды после подъёма стенда sp_ctl бывает down
    # (охрана выхода ещё не знает адрес pcbk-sp-ctl, Ш10) — ждём, а не один запрос (Ш4)
    wait_for(lambda: stack.http_host("GET", CORE + "/healthz")[0] == 200, timeout=60)
    code, body = stack.http_host("GET", CORE + "/healthz/data")
    assert code == 200 and json.loads(body)["ok"] is True
    # первая попытка каталога длится до срока входа (10 с): ждём её исход, а не порядок тестов
    deadline = time.monotonic() + 30
    while (j := json.loads(stack.http_host("GET", CORE + "/health/historian")[1]))["catalog_error"] is None:
        assert time.monotonic() < deadline, f"за 30 с каталог не попробовал загрузиться: {j}"
        time.sleep(1)
    assert j["catalog_error"] == "connect" and j["gate"]["refused_unlisted"] == 0


def test_edge_does_not_expose_core(stack):
    for path in ("/healthz/data", "/health/historian", "/api/data/tag_now", "/mcp",
                 "/llm/v1/chat/completions", "/health/llm"):
        assert stack.https(path)[0] == 404


STU_NETS = [f"pcbk-stu-{n:02d}" for n in range(1, 11)]


def test_egress_network_only_core(stack):
    n = stack.network("pcbk-egress")
    assert n["Internal"] is False
    assert n["Options"]["com.docker.network.bridge.name"] == "pcbk-egress"
    assert n["Options"].get("com.docker.network.bridge.enable_ip_masquerade", "true") == "true"
    # двойник OpenRouter — только в тестовом стенде (compose.test.yaml)
    assert sorted(c["Name"] for c in n["Containers"].values()) == ["pcbk-core", "pcbk-test-openrouter"]
    # тринадцать сетей: pcbk-front, pcbk-egress, pcbk-ctl (клиент sp-ctl, Д5-R31) и десять сетей мест на .2
    nets = stack.inspect("pcbk-core")["NetworkSettings"]["Networks"]
    assert set(nets) == {"pcbk-front", "pcbk-egress", "pcbk-ctl", *STU_NETS} and \
        nets["pcbk-egress"]["IPAddress"] == "172.31.250.82"
    assert {name: nets[name]["IPAddress"] for name in STU_NETS} == \
           {name: f"{stack.stu_net}.{n}.2" for n, name in enumerate(STU_NETS, 1)}


def test_core_in_workplace_networks_on_dot2(stack):                         # Д4б-R23, по компоновке сервера
    nets = stack.prod_config()["services"]["core"]["networks"]
    assert set(nets) == {"pcbk-front", "pcbk-egress", "pcbk-ctl", *STU_NETS}
    assert {name: nets[name]["ipv4_address"] for name in STU_NETS} == \
           {name: f"{stack.stu_net}.{n}.2" for n, name in enumerate(STU_NETS, 1)}
    # тот же адрес, что у места в extra_hosts: место зовёт core по имени (под gVisor DNS нет)
    places = stack.prod_config()["services"]
    assert [[tuple(re.split(r"[:=]", h, maxsplit=1)) for h in places[f"student-{n:02d}"]["extra_hosts"]]
            for n in range(1, 11)] == [[("core", f"{stack.stu_net}.{n}.2")] for n in range(1, 11)]


def test_only_core_declares_egress(stack):
    # по компоновке, а не по живой сети: ловит и остановленные службы, и службы профилей
    services = stack.prod_config()["services"]
    assert [name for name, s in services.items() if "pcbk-egress" in (s.get("networks") or {})] == ["core"]


def test_core_hardening(stack):
    c = stack.inspect("pcbk-core")
    hc = c["HostConfig"]
    assert (hc["ReadonlyRootfs"], hc["CapDrop"], hc["CapAdd"], hc["Privileged"]) == (True, ["ALL"], None, False)
    assert hc["SecurityOpt"] == ["no-new-privileges:true"]
    assert c["Config"]["User"] == "10003:10003"
    assert (hc["Memory"], hc["PidsLimit"]) == (512 * 1024 * 1024, 128)
    assert hc["Sysctls"] == {"net.ipv4.tcp_fastopen": "0", "net.ipv4.ip_forward": "0"}   # Д3б-R6, Д4б-R23 (К9)
    # в самом netns контейнера: без настройки он наследует пересылку хоста (на машине Docker — 1)
    assert stack.exec("pcbk-core", "cat", "/proc/sys/net/ipv4/ip_forward", "/proc/sys/net/ipv4/tcp_fastopen").split() == \
           ["0", "0"]
    assert hc["RestartPolicy"]["Name"] == "unless-stopped"
    assert set(hc["Tmpfs"]) == {"/tmp"}


def test_core_secret_and_list_are_readonly_files(stack):
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    for dest in ("/run/secrets/bdrv.env", "/app/data/whitelist.txt"):
        assert (mounts[dest]["Type"], mounts[dest]["RW"]) == ("bind", False), dest
    prod = stack.prod_config()["services"]["core"]
    assert "env_file" not in prod and "BDRV" not in json.dumps(prod.get("environment", {}))
    assert prod["pull_policy"] == "never"
    # двойник OpenRouter и частый опрос — только в compose.test.yaml; на сервере — настоящий OpenRouter
    assert not {"OPENROUTER_BASE_URL", "LLM_POLL_S"} & set(prod["environment"])
    # секреты core — ровно эти пятнадцать файлов: ключи OpenRouter — среди них, а не в окружении; пароли мест
    # (Д5а) знает только шлюз — секретами Compose student-NN-pw
    assert sorted(x["target"] for x in prod["secrets"]) == \
        ["bdrv.env", "core-tokens", "llm-tokens", "openrouter-mgmt.key", "openrouter.key",
         *(f"student-{n:02d}.pw" for n in range(1, 11))]
    assert {x["target"]: x["source"] for x in prod["secrets"] if x["target"].startswith("student-")} == \
        {f"student-{n:02d}.pw": f"student-{n:02d}-pw" for n in range(1, 11)}


def test_core_is_the_sp_ctl_client(stack):                         # Д5-R31: клиент sp-ctl — настоящий core
    assert stack.http_from_core("GET", CTL + "/_ping") == 200
    assert stack.http_as("pcbk-intruder", "pcbk-ctl", "GET", CTL + "/_ping") == 403


def test_tool_over_egress_answers_and_journals(stack):
    code, body = stack.http_host("POST", CORE + "/api/data/tag_now", {"tags": ["20FAKE_001_PV"]}, token=stack.core_token)
    assert code == 200 and json.loads(body)["status"] == "unavailable"          # историана нет — словами
    assert stack.http_host("POST", CORE + "/api/data/tag_now", {"tags": ["x"]})[0] == 401
    assert "outcome=unauthorized channel=http" in stack.logs("pcbk-core")


def test_core_state_volume_writable(stack):                                     # Review Focus 4
    # в тестовом стенде bdrv.env целый — 13 направлений: историан, OpenRouter (Д4а), pcbk-sp-ctl и десять мест
    # на STU.N.3:4096 (Д5а); bdrv.env не читается — было бы 12, без историана (П3, Ш3)
    assert "охрана выхода: разрешено 13 направлений" in stack.logs("pcbk-core")
    # core.db создаётся в начале lifespan роли (F23) — файл есть до первого вызова инструмента
    mounts = {m["Destination"]: m for m in stack.inspect("pcbk-core")["Mounts"]}
    assert mounts["/var/lib/pcbk-core"]["Type"] == "volume"
    # имя тома — с префиксом проекта (без name: в compose.yaml): down -v тестового проекта pcbk-test
    # не может задеть том выкладки pcbk-reserve_pcbk-core-data
    assert mounts["/var/lib/pcbk-core"]["Name"] == "pcbk-test_pcbk-core-data"
    assert stack.prod_config()["volumes"]["pcbk-core-data"]["name"] == "pcbk-test_pcbk-core-data"
    assert (mounts["/run/secrets/core-tokens"]["Type"], mounts["/run/secrets/core-tokens"]["RW"]) == ("bind", False)
    stack.exec("pcbk-core", "test", "-w", "/var/lib/pcbk-core")               # при отказе exec бросит исключение
    assert stack.exec("pcbk-core", "stat", "-c", "%u", "/var/lib/pcbk-core/core.db") == "10003"
    code, body = stack.http_host("GET", CORE + "/healthz/data")
    assert code == 200 and json.loads(body)["ok"] is True


def test_stop_core_turns_row_red_historian_unknown(stack):
    stack.stop("pcbk-core")
    try:
        data = stack.wait_status(lambda d: row(d, "core")[0] == "fail", timeout=40)
        assert row(data, "historian") == ("unknown", "служба данных не отвечает — свежесть неизвестна")
    finally:
        stack.start("pcbk-core")


def _zones_nets() -> dict[str, tuple[str, ...]]:
    """FRONT_NETS и ADMIN_NETS из текста zones.py (без импорта pcbk_core): литералы и «FRONT_NETS + (…)»."""
    found: dict[str, tuple[str, ...]] = {}
    for node in ast.parse(ZONES_PY.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
            if name not in ("FRONT_NETS", "ADMIN_NETS"):
                continue
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add) and isinstance(value.left, ast.Name):
                found[name] = found[value.left.id] + ast.literal_eval(value.right)
            else:
                found[name] = ast.literal_eval(value)
    return found


def test_front_subnet_matches_compose(stack):                        # Ш15: зоны Д5-R31 — те же подсети, что в компоновке
    zones = _zones_nets()
    networks = stack.prod_config()["networks"]
    assert networks["pcbk-front"]["ipam"]["config"][0]["subnet"] == zones["FRONT_NETS"][0]
    assert networks["pcbk-egress"]["ipam"]["config"][0]["subnet"] == zones["ADMIN_NETS"][-1]
