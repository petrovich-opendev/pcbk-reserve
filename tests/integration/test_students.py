"""Десять рабочих мест в компоновке: свои объекты, наблюдение сторожа, жёсткость, сеть, пароль, заглушки."""
import json
import os
import re
import socket
import struct
from pathlib import Path

import pytest

from workplace import IMAGE, MOUNTS, STUB_PREFIX, WORKPLACE_ENV

ROOT = Path(__file__).resolve().parents[2]
RO = "http://pcbk-sp-ro:2375/v1.44"
CTL = "http://pcbk-sp-ctl:2375/v1.44"
WORKPLACES = {f"pcbk-student-{n:02d}" for n in range(1, 11)}
HISTORIAN_ADDR = os.environ.get("HISTORIAN_ADDR", "192.0.2.10")   # адрес заказчика — только из окружения
RUNTIME_ENV = {"HOSTNAME", "PWD", "SHLVL", "_", "OLDPWD"}         # добавляют Docker и bash при exec
PROBE_AGENT = "---\ndescription: Проба видимости агента\nmode: primary\n---\nТы — проба.\n"
BYPASS_TOOL = 'export default { description: "BYPASS real shell", args: {}, async execute() { return "x" } }'
BYPASS_CONFIG = '{"agent": {"bypass": {"description": "обход", "mode": "primary"}}}'
# ключи блока места в compose config (не-null): якорь x-student и поля самого места
STUDENT_KEYS = {"image", "pull_policy", "runtime", "profiles", "user", "init", "ipc", "read_only", "cap_drop",
                "security_opt", "tmpfs", "mem_limit", "memswap_limit", "cpus", "pids_limit", "restart",
                "stop_grace_period", "labels", "container_name", "networks", "extra_hosts", "volumes", "secrets"}


@pytest.fixture(scope="module")
def asleep(stack):                       # модуль не зависит от порядка файлов: работающие места — в сон
    for name in sorted(WORKPLACES):
        if stack.inspect(name)["State"]["Running"]:
            stack.stop(name)


@pytest.fixture(scope="module")
def running(stack):                                                # места 01 и 02 работают до конца модуля
    for name in ("pcbk-student-01", "pcbk-student-02"):
        stack.start(name)
    yield
    for name in ("pcbk-student-01", "pcbk-student-02"):
        stack.stop(name)


def student_rows(data):
    return {c["component"]: (c["state"], c["detail"]) for c in data["checks"] if c["component"].startswith("student-")}


def norm(service: dict, n: int) -> dict:
    """Блок места с номером, заменённым меткой: блоки 02–10 обязаны совпасть с блоком 01."""
    text = json.dumps(service, sort_keys=True)
    for old, new in ((f"student-{n:02d}", "student-NN"), (f"stu-{n:02d}", "stu-NN"), (f".{n}.", ".N.")):
        text = text.replace(old, new)
    return json.loads(text)


def test_every_workplace_is_watched(stack, asleep):                # Review Focus 4
    in_compose = {s["container_name"] for s in stack.prod_config()["services"].values()
                  if s.get("labels", {}).get("pcbk.role") == "student"}
    comps = json.loads((ROOT / "watchdog" / "components.json").read_text())
    watched = {c["container"] for c in comps if c["kind"] == "container" and c["sleeping_ok"]}
    assert in_compose == watched == WORKPLACES
    # ждём целевое состояние, а не любое ok: старый снимок с «работает» не проходит
    stack.wait_status(lambda d: len(student_rows(d)) == 10 and
                      set(student_rows(d).values()) == {("ok", "спит")}, timeout=30)


def test_each_workplace_uses_own_objects(stack):                   # Review Focus 2
    cfg, stu = stack.prod_config(), stack.stu_net
    for n in range(1, 11):
        nn = f"{n:02d}"
        s = cfg["services"][f"student-{nn}"]
        assert s["container_name"] == f"pcbk-student-{nn}"
        assert sorted((x["source"], x["target"]) for x in s["secrets"]) == \
               [(f"student-{nn}-llm", "llm-token"), (f"student-{nn}-pw", "opencode-pw")]
        assert (cfg["secrets"][f"student-{nn}-pw"]["file"], cfg["secrets"][f"student-{nn}-llm"]["file"]) == \
               (str(stack.secrets_dir / f"student-{nn}.pw"), str(stack.secrets_dir / f"student-{nn}.llm-token"))
        assert {v["target"]: v["source"] for v in s["volumes"]} == {
            "/var/lib/opencode": f"pcbk-student-{nn}-state", "/work": f"pcbk-student-{nn}-work",
            "/etc/pcbk-opencode/opencode/agents": str(stack.agents_dir(n))}
        assert [(cfg["volumes"][f"pcbk-student-{nn}-{k}"]["name"], cfg["volumes"][f"pcbk-student-{nn}-{k}"]["external"])
                for k in ("state", "work")] == [(f"pcbk-student-{nn}-state", True), (f"pcbk-student-{nn}-work", True)]
        # только своя сеть: ни соседа, ни pcbk-ctl, ни pcbk-ro
        assert set(s["networks"]) == {f"pcbk-stu-{nn}"}
        assert s["networks"][f"pcbk-stu-{nn}"]["ipv4_address"] == f"{stu}.{n}.3"
        assert cfg["networks"][f"pcbk-stu-{nn}"]["ipam"]["config"][0]["subnet"] == f"{stu}.{n}.0/28"
        c = stack.inspect(f"pcbk-student-{nn}")                    # созданный контейнер — как на сервере
        assert set(c["NetworkSettings"]["Networks"]) == {f"pcbk-stu-{nn}"}
        assert [tuple(re.split(r"[:=]", h, maxsplit=1)) for h in c["HostConfig"]["ExtraHosts"]] == \
               [("core", f"{stu}.{n}.2")]
        assert len(c["Mounts"]) == 5 and all(f"student-{nn}" in m["Source"] for m in c["Mounts"])


def test_sp_ctl_really_starts_and_stops_student(stack):
    url = CTL + "/containers/pcbk-student-03"
    try:
        assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/start") == 204
        assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/start") == 304
        assert stack.http_as("pcbk-core", "pcbk-ctl", "POST", url + "/stop") == 204
        assert stack.inspect("pcbk-student-03")["State"]["Running"] is False
    finally:
        stack.stop("pcbk-student-03")                              # будил тест — он и усыпляет, даже при сбое
    stack.wait_status(lambda d: student_rows(d).get("student-03") == ("ok", "спит"), timeout=30)


def test_student_hardening(stack, running):                        # Review Focus 5
    c = stack.inspect("pcbk-student-01")
    hc = c["HostConfig"]
    assert (hc["ReadonlyRootfs"], hc["CapDrop"], hc["CapAdd"], hc["Privileged"]) == (True, ["ALL"], None, False)
    assert hc["SecurityOpt"] == ["no-new-privileges:true"]         # без seccomp=unconfined, apparmor=unconfined
    assert (hc["UsernsMode"], hc["CgroupnsMode"], hc.get("Sysctls") or {}) == ("", "private", {})
    assert (hc["Memory"], hc["MemorySwap"], hc["PidsLimit"], hc["NanoCpus"], hc["Init"]) == \
           (1024 ** 3, 1024 ** 3, 512, 10 ** 9, True)
    assert (hc["PidMode"], hc["IpcMode"], hc["Devices"] or [], hc["PortBindings"] or {}) == ("", "private", [], {})
    assert hc["RestartPolicy"]["Name"] == "unless-stopped"
    assert {m["Destination"] for m in c["Mounts"]} == MOUNTS and set(hc["Tmpfs"]) == {"/tmp"}
    assert stack.sh("pcbk-student-01", "ls -A /run/secrets")[1].split() == ["llm-token", "opencode-pw"]
    pid = stack.opencode_pid("pcbk-student-01")
    status = dict(line.split(":", 1) for line in
                  stack.sh("pcbk-student-01", f"cat /proc/{pid}/status")[1].splitlines() if ":" in line)
    assert {k: status[k].split() for k in ("CapEff", "CapBnd", "NoNewPrivs")} == \
           {"CapEff": ["0000000000000000"], "CapBnd": ["0000000000000000"], "NoNewPrivs": ["1"]}
    assert status["Uid"].split() == ["10001"] * 4
    assert stack.exec("pcbk-student-01", "cat", "/proc/1/comm") == "docker-init"
    image_hc = json.loads(stack._docker("image", "inspect", IMAGE).stdout)[0]["Config"]["Healthcheck"]
    assert image_hc and image_hc["Test"][0] == "CMD"               # иначе сравнение ниже пустое
    for name in sorted(WORKPLACES):                                # окружение — ровно образ, со значениями
        cfg = stack.inspect(name)["Config"]
        assert dict(e.split("=", 1) for e in cfg["Env"]) == WORKPLACE_ENV, name
        assert cfg["Healthcheck"] == image_hc, name                # проверка здоровья — ровно образа
    services = stack.prod_config()["services"]                    # без compose.test.yaml
    s = services["student-01"]
    assert (s["runtime"], s["read_only"], s["cap_drop"], s["init"], s["profiles"], s["ipc"]) == \
           ("runsc", True, ["ALL"], True, ["students"], "private")
    assert s["security_opt"] == ["no-new-privileges:true"] and not s.get("ports") and not s.get("environment")
    # compose config пишет command и entrypoint: null у каждой службы — запрещено любое не-null значение
    forbidden = ("command", "entrypoint", "privileged", "cap_add", "devices", "pid", "healthcheck")
    assert [k for k in forbidden if s.get(k) is not None] == []
    # ровно эти ключи: новый ключ в якоре или блоке (userns_mode, sysctls, cgroup…) — повод для ревью
    assert {k for k, v in s.items() if v is not None} == STUDENT_KEYS
    assert (int(s["mem_limit"]), int(s["memswap_limit"]), s["pids_limit"], float(s["cpus"])) == \
           (1024 ** 3, 1024 ** 3, 512, 1.0)
    agents = next(v for v in s["volumes"] if v["target"] == "/etc/pcbk-opencode/opencode/agents")
    assert agents["read_only"] is True and not agents.get("bind", {}).get("create_host_path")
    for n in range(2, 11):                                         # блоки 02–10 — копия 01 с точностью до номера
        assert norm(services[f"student-{n:02d}"], n) == norm(s, 1), n


def test_opencode_requires_password(stack, running):
    stack.wait_oc(1, timeout=30)                                   # первые секунды запросы висят
    assert stack.oc(1, "GET", "/global/health", None)[0] == 401
    assert stack.oc(1, "GET", "/global/health", 1)[0] == 200
    assert stack.oc(1, "GET", "/global/health", 2)[0] == 401      # Review Focus 2


def last_descriptions(stack, model):
    code, body = stack.oc(1, "GET", f"/experimental/tool?provider=pcbk&model={model}", 1)
    assert code == 200
    # id повторяется: сначала встроенный, потом заглушка; модель получает последнюю запись
    return {t["id"]: t["description"] for t in json.loads(body)}


def test_stubs_replace_builtin_tools(stack, running):
    stub = last_descriptions(stack, "stub")
    assert all(stub[t].startswith(STUB_PREFIX) for t in ("bash", "edit", "write"))
    assert not {"apply_patch", "multiedit", "patch"} & set(stub)
    gpt = last_descriptions(stack, "gpt-5")                        # apply_patch есть только у gpt-*
    assert gpt["apply_patch"].startswith(STUB_PREFIX) and gpt["bash"].startswith(STUB_PREFIX)
    assert not {"edit", "write", "multiedit", "patch"} & set(gpt)


def agent_names(stack):
    code, body = stack.oc(1, "GET", "/agent", 1)
    assert code == 200
    return {a["name"] for a in json.loads(body)}


def test_project_dir_cannot_override_stubs(stack, running):        # Review Focus 5
    try:
        rc = stack.sh("pcbk-student-01", "mkdir -p /work/.opencode/tools && "
                      f"cat > /work/.opencode/tools/bash.ts <<'EOF'\n{BYPASS_TOOL}\nEOF\n"
                      f"cat > /work/opencode.json <<'EOF'\n{BYPASS_CONFIG}\nEOF")[0]
        assert rc == 0                                             # /work пишется — обход был бы возможен
        assert stack.oc(1, "POST", "/instance/dispose", 1)[0] == 200
        descs = last_descriptions(stack, "stub")
        assert descs["bash"].startswith(STUB_PREFIX) and "BYPASS real shell" not in descs.values()
        assert "bypass" not in agent_names(stack)
    finally:
        stack.sh("pcbk-student-01", "rm -rf /work/.opencode /work/opencode.json")
        stack.oc(1, "POST", "/instance/dispose", 1)
    assert stack.sh("pcbk-student-01", "ls -A /work")[1].strip() == ""


def test_agent_file_visible_after_dispose(stack, running):
    assert "probe" not in agent_names(stack)                       # экземпляр создан до записи файла
    probe = stack.agents_dir(1) / "probe.md"
    try:
        probe.write_text(PROBE_AGENT)
        probe.chmod(0o644)
        assert "probe" not in agent_names(stack)                   # без dispose не виден
        assert stack.oc(1, "POST", "/instance/dispose", 1)[0] == 200
        assert "probe" in agent_names(stack)
    finally:
        probe.unlink(missing_ok=True)
        stack.oc(1, "POST", "/instance/dispose", 1)


def test_readonly_where_it_matters(stack, running):
    for d in ("/home/pcbk", "/etc/pcbk-opencode/opencode", "/etc/pcbk-opencode/opencode/tools",
              "/etc/pcbk-opencode/opencode/agents", "/usr/local/bin", "/run/secrets"):
        assert stack.sh("pcbk-student-01", f"touch {d}/.pcbk-w")[0] != 0, f"{d} пишется"
    for d in ("/var/lib/opencode", "/work", "/tmp"):
        assert stack.sh("pcbk-student-01", f"touch {d}/.pcbk-w && rm {d}/.pcbk-w")[0] == 0, f"{d} не пишется"
    assert list(stack.agents_dir(1).iterdir()) == []


def routes(table: str) -> list[tuple[str, str]]:
    """/proc/net/route → (сеть, маска); адреса там — шестнадцатеричные, порядок байт хоста (x86 — little-endian)."""
    addr = lambda h: socket.inet_ntoa(struct.pack("<I", int(h, 16)))
    return [(addr(f[1]), addr(f[7])) for f in (line.split() for line in table.splitlines()[1:]) if f]


def test_no_route_anywhere(stack, running):
    stu = stack.stu_net
    nets = [f"pcbk-stu-{n:02d}" for n in range(1, 11)]
    assert stack.host_bridge_addresses(*nets) == []                # главное: у мостов нет адреса хоста
    for n, net in enumerate(nets, 1):
        info = stack.network(net)
        assert info["Internal"] is True
        assert info["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
        assert [(c["Subnet"], c["IPRange"]) for c in info["IPAM"]["Config"]] == \
               [(f"{stu}.{n}.0/28", f"{stu}.{n}.8/29")]
    eps = stack.inspect("pcbk-student-01")["NetworkSettings"]["Networks"]
    assert list(eps) == ["pcbk-stu-01"] and eps["pcbk-stu-01"]["IPAddress"] == f"{stu}.1.3"
    assert stack.sh("pcbk-student-01", "getent hosts core")[1].split()[0] == f"{stu}.1.2"
    assert "eth0" not in stack.sh("pcbk-student-01", "cat /proc/net/if_inet6")[1]   # у места нет IPv6
    assert routes(stack.sh("pcbk-student-01", "cat /proc/net/route")[1]) == \
           [(f"{stu}.1.0", "255.255.255.240")]                    # маршрута по умолчанию нет
    assert stack.probe("pcbk-stu-01", f"{stu}.1.3", 4096) == 1    # положительный контроль: своё место
    assert stack.probe("pcbk-stu-02", f"{stu}.2.3", 4096) == 1    # место 02 живо — его 0 ниже не пустой
    gateway = stack.network("pcbk-stu-01")["IPAM"]["Config"][0].get("Gateway")
    targets = [(a, p) for a in sorted({f"{stu}.1.1", gateway} - {None}) for p in (22, 80, 443, 2375, 3389)]
    targets += [(stack.host_lan(), 22), (stack.host_lan(), 443), (HISTORIAN_ADDR, 1433),
                ("1.1.1.1", 443), (f"{stu}.2.3", 4096)]
    targets += [(ep["IPAddress"], port) for name, port in
                (("pcbk-sp-ctl", 2375), ("pcbk-sp-ro", 2375), ("pcbk-watchdog", 8090), ("pcbk-edge", 8443))
                for ep in stack.inspect(name)["NetworkSettings"]["Networks"].values()]
    assert [t for t in targets if stack.probe("pcbk-stu-01", *t) != 0] == []


def test_password_not_visible_to_watchdog(stack, running):
    stack.wait_healthy("pcbk-student-01", timeout=60)              # в Health.Log уже есть вывод проверки
    body = stack.body_from_watchdog(RO + "/containers/pcbk-student-01/json")
    log = json.loads(body)["State"]["Health"]["Log"]
    assert all(re.fullmatch(r"\d{3}\n?", e["Output"]) for e in log) and log[-1]["Output"].strip() == "200"
    r = stack._docker("logs", "pcbk-student-01")
    logs = r.stdout + r.stderr
    # булевы — до assert: при сбое pytest печатает только их, а не тело inspect или журнал
    leaked = any(secret in text for secret in (stack.password(1), stack.llm_token(1)) for text in (body, logs))
    env_name = "OPENCODE_SERVER_PASSWORD" in body
    assert (leaked, env_name) == (False, False)


def test_env_holds_only_own_secret(stack, running):
    pid = stack.opencode_pid("pcbk-student-01")
    raw = stack.sh("pcbk-student-01", f"cat /proc/{pid}/environ")[1]
    pairs = [item.split("=", 1) for item in raw.split("\0") if item]
    extra = {k for k, _ in pairs} - stack.env_names("pcbk-student-01") - RUNTIME_ENV
    assert extra == {"OPENCODE_SERVER_PASSWORD"}, sorted(extra)      # печатаются только имена
    values = {v for _, v in pairs}
    own = stack.password(1) in values
    foreign = bool(values & ({stack.password(n) for n in range(2, 11)} | {stack.llm_token(1)}))
    assert (own, foreign) == (True, False)


def test_hung_workplace_row(stack, running):                       # Review Focus 3; последний в модуле
    row = lambda d: student_rows(d).get("student-01")
    stack.wait_status(lambda d: row(d) == ("ok", "работает"), timeout=60)
    pid = stack.opencode_pid("pcbk-student-01")
    stack.sh("pcbk-student-01", f"kill -STOP {pid}")
    try:                                                           # HEALTHCHECK → Docker → sp-ro → сторож → /status.json
        stack.wait_status(lambda d: row(d) == ("fail", "OpenCode не отвечает"), timeout=150)
    finally:
        stack.sh("pcbk-student-01", f"kill -CONT {pid}")
    stack.wait_status(lambda d: row(d) == ("ok", "работает"), timeout=60)
