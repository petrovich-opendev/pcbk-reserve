# tests/integration/test_isolation_live.py — Д11-R125: изоляция всех мест одним кодом — стенд или сервер
"""Изоляция целиком (Д11-R125, неуспех 1). Без PCBK_LIVE_ISO — тестовый стенд (stack), с ним — сервер по JSON вне git
(договор — deploy/README.md, «Изоляция целиком (с Д11)»). Только чтение: одноразовые контейнеры --rm с ONESHOT_LABEL,
одна одноразовая сеть и слушатель отрицательного контроля (I-3 р2: свой, а не sshd машины) — удаляются в finally. Места
людей не будятся: сеть места проверяет одноразовый контейнер в ней, бодрствуют только два свидетеля из свободных мест.
В выводе — только коды, счёт и имена целей: ни значений секретов, ни адресов заказчика."""
import html
import importlib.util
import json
import os
import re
import secrets
import shlex
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from cdp import Browser
from conftest import ONESHOT_LABEL, REPO, locked_image, wait_for

PORTS_CLOSED = (22, 80, 443, 2375, 3389)
CONNECTED = re.compile(r"Established connection|Connected to")
# образ пробы — тот же, что у Stack.probe (images.lock, «# test»); на сервере остаётся инструментом (deploy/README.md)
PROBE_IMAGE = locked_image("curlimages/curl")
CTL_NET, LISTENER, LISTEN_PORT = "pcbk-iso-ctl", "pcbk-iso-listen", 9000
SERVICES = (("pcbk-sp-ctl", 2375), ("pcbk-sp-ro", 2375), ("pcbk-watchdog", 8090), ("pcbk-edge", 8443))
CORE, CORE_PORT, OC_PORT = "pcbk-core", 8000, 4096
FOREIGN_SECRETS = ("bdrv-pw", "openrouter.key")
PW, OC_PW_ENV = "BDRV_PW", "OPENCODE_SERVER_PASSWORD"          # имена переменных (значений в модуле нет)
CONF_KEYS = frozenset({"ssh", "runtime", "stu_net", "secrets_dir", "places", "witnesses", "lan_addr", "historian_addr",
                       "remote_dir", "listener_image", "ctl_subnet", "ctl_host_port"})
SHOT = {False: "d11b-04-isolation.png", True: "isolation-server.png"}

# значение секрета так, как его читает core, — во временный файл 0600 (argv: вид, файл, куда). env — пароль БДРВ
# разбором pcbk_core.secrets.read_env_file (сверка п. 25: export, одна пара кавычек по краям, последняя строка);
# key — ключ OpenRouter, как pcbk_core.secrets.read_key_file (пробелы и BOM по краям). Значение никуда не печатается;
# нет значения — код 3, строка не KEY=VALUE — код 2 (как ValueError у core, без содержимого)
VALUE_PY = r"""
import re, sys
kind, src, dst = sys.argv[1:4]
with open(src, encoding="utf-8") as f:
    text = f.read()
value = None
if kind == "env":
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if m is None:
            sys.exit(2)
        key, v = m.groups()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if key == "BDRV_PW":
            value = v
else:
    value = text.strip().strip("\ufeff").strip()
if not value:
    sys.exit(3)
with open(dst, "w", encoding="utf-8") as f:
    f.write(value)
"""
# docker inspect, раскрытый в строки: каждый ключ и каждая строка JSON — своей строкой, без экранирования JSON
# (кавычка и обратная косая — с косой, «<», «>», «&» — кодом u003c…): иначе пароль с такими знаками не найти
FLATTEN_PY = r"""
import json, sys
out = sys.stdout.buffer
def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            out.write(k.encode("utf-8", "surrogatepass") + b"\n")
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)
    elif isinstance(o, str):
        out.write(o.encode("utf-8", "surrogatepass") + b"\n")
walk(json.loads(sys.stdin.buffer.read()))
"""
# окружение самого OpenCode места: pid — по exe, как Stack.opencode_pid
ENVIRON_SH = ('for p in /proc/[0-9]*; do [ "$(readlink "$p/exe" 2>/dev/null)" = /usr/local/bin/opencode ] '
              '&& tr "\\0" "\\n" < "$p/environ"; done; true')
GREP = 'LC_ALL=C grep -a -c -F -f "$p"'

# счёт модуля для страницы отчёта (снимок d11b-04-isolation.png): только числа и номера мест
TALLY: dict = {"nets": 0, "core_open": 0, "witness_open": 0, "witness_end": 0, "listener_ctl": 0, "listener_closed": 0,
               "host_port": 0, "targets": 0, "opened": 0, "mounts": 0, "inspect_clean": 0, "environ_clean": 0,
               "woken": set(), "not_asleep": [], "ctl_left": False, "places": {}}


def place_row(n: int) -> dict:
    return TALLY["places"].setdefault(n, {"net": "—", "core": "—", "listener": "—", "targets": 0, "opened": 0,
                                          "mounts": "—", "hits": "—"})


class Iso:
    """Команды на машине мест: ssh — argv до команды (стенд — bash -c здесь, сервер — ssh), команда — одной строкой
    оболочки там."""

    def __init__(self, conf: dict, live: bool = False):
        self.c = conf
        self.live = live

    def sh(self, cmd: str, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run([*self.c["ssh"], cmd], capture_output=True, text=True, timeout=timeout)

    def inspect(self, name: str, kind: str = "container") -> dict:
        r = self.sh(f"docker {kind} inspect {shlex.quote(name)}")
        if r.returncode != 0:
            raise RuntimeError(f"docker {kind} inspect {name}: код {r.returncode}")
        return json.loads(r.stdout)[0]

    def probe(self, net: str, addr: str, port: int) -> int:
        """Способ Д1 из одноразового curl в сети net: 1 — соединение есть, 0 — нет."""
        cmd = (f"docker run --rm --pull never --runtime={shlex.quote(self.c['runtime'])} --label {ONESHOT_LABEL} "
               f"--network {shlex.quote(net)} --read-only --cap-drop ALL --security-opt no-new-privileges:true "
               f"{PROBE_IMAGE} -sv --connect-timeout 3 -m 4 telnet://{addr}:{port}; echo \"rc=$?\"")
        r = self.sh(cmd)
        m = re.search(r"rc=(\d+)\s*\Z", r.stdout)
        if m is None:                                                       # ssh или оболочка, а не ответ пробы
            raise RuntimeError(f"проба в {net}: нет кода docker run, код команды {r.returncode}")
        if int(m.group(1)) in (125, 126, 127):                              # сбой docker run — не «закрыто»
            raise RuntimeError(f"проба в {net}: docker run, код {m.group(1)}\n{r.stderr[-500:]}")
        return 1 if CONNECTED.search(r.stdout + r.stderr) else 0

    def number(self, cmd: str, what: str) -> int:
        """Вывод команды — ровно одно число; иначе ошибка без самого вывода (там могло быть значение)."""
        r = self.sh(cmd)
        if not re.fullmatch(r"\d+\n?", r.stdout):
            raise RuntimeError(f"{what}: в выводе не число, код {r.returncode}")
        return int(r.stdout)

    def value_cmd(self, name: str) -> str:
        """Оболочка: $p — временный файл 0600 со значением секрета name, как его читает core. Удаляет вызывающий."""
        kind, src = ("env", "bdrv.env") if name == "bdrv-pw" else ("key", name)
        path = shlex.quote(f"{self.c['secrets_dir'].rstrip('/')}/{src}")
        return f'p=$(mktemp) && chmod 600 "$p" && python3 -c {shlex.quote(VALUE_PY)} {kind} {path} "$p"'

    def ctrl_cmd(self, name: str) -> str:
        """Положительный контроль: голое значение находит само себя — `1`."""
        return f'{self.value_cmd(name)} && v=$(cat "$p") && printf "%s" "$v" | {GREP}; rm -f "$p"'

    def counts(self, name: str, target_cmd: str) -> tuple[int, int]:
        """(положительный контроль на голом значении, счёт строк цели с этим значением); значение в вывод не идёт."""
        ctrl = self.number(self.ctrl_cmd(name), f"{name}: положительный контроль")
        hits = self.number(f'{self.value_cmd(name)} && {{ {target_cmd}; }} | {GREP}; rm -f "$p"',
                           f"{name}: счёт в цели")
        return ctrl, hits

    def lines(self, target_cmd: str, grep_args: str) -> int:
        """Строк вывода цели по grep_args — цель не пуста: ноль совпадений с секретом в ней не вакуумен."""
        return self.number(f"{{ {target_cmd}; }} | LC_ALL=C grep -a -c {grep_args}; true", "строки цели")


def inspect_cmd(n: int) -> str:
    name = f"pcbk-student-{n:02d}"
    return f"docker inspect {name}; docker inspect {name} | python3 -c {shlex.quote(FLATTEN_PY)}"


def environ_cmd(n: int) -> str:
    return f"docker exec pcbk-student-{n:02d} sh -c {shlex.quote(ENVIRON_SH)}"


@pytest.fixture(scope="module")
def iso(request):
    path = os.environ.get("PCBK_LIVE_ISO")
    if path:
        conf = json.loads(Path(path).read_text(encoding="utf-8"))
        missing = sorted(CONF_KEYS - conf.keys())
        if missing:
            pytest.fail(f"PCBK_LIVE_ISO: нет ключей {missing}")
        return Iso(conf, live=True)
    return Iso(request.getfixturevalue("stack").iso_conf())


@contextmanager
def awake(iso, n):
    """Свидетель n бодрствует внутри блока; спал — уснёт снова (проверка — в отчёте модуля)."""
    name = f"pcbk-student-{n:02d}"
    was = iso.inspect(name)["State"]["Running"]
    try:
        if not was:
            r = iso.sh(f"docker start {name}")
            if r.returncode != 0:
                raise RuntimeError(f"{name}: docker start, код {r.returncode}")
            wait_for(lambda: (iso.inspect(name)["State"].get("Health") or {}).get("Status") == "healthy", 120)
            TALLY["woken"].add(n)
        yield
    finally:
        if not was:
            iso.sh(f"docker stop {name}")
            if iso.inspect(name)["State"]["Running"]:
                TALLY["not_asleep"].append(n)


def addresses(iso, name: str) -> list[tuple[str, str]]:
    """(сеть, адрес) контейнера name во всех его сетях."""
    nets = iso.inspect(name)["NetworkSettings"]["Networks"]
    return [(net, ep["IPAddress"]) for net, ep in sorted(nets.items()) if ep.get("IPAddress")]


def _drop_control(iso) -> None:
    """Слушатель и сеть отрицательного контроля — только свои, по метке (и остаток оборванного прогона)."""
    lab = shlex.quote(f"label={ONESHOT_LABEL}")
    iso.sh(f"for c in $(docker ps -aq --filter {lab} --filter name={LISTENER}); do docker rm -f \"$c\"; done; "
           f"for n in $(docker network ls -q --filter {lab} --filter name={CTL_NET}); do "
           "docker network rm \"$n\"; done; "
           "true")


@contextmanager
def control_network(iso):                                                   # I-3 р2: свой слушатель вместо sshd
    _drop_control(iso)
    try:
        r = iso.sh(f"docker network create --internal --label {ONESHOT_LABEL} "
                   f"--subnet {shlex.quote(iso.c['ctl_subnet'])} {CTL_NET}")
        if r.returncode != 0:
            raise RuntimeError(f"сеть {CTL_NET}: код {r.returncode}\n{r.stderr[-500:]}")
        r = iso.sh(f"docker run -d --rm --pull never --name {LISTENER} --label {ONESHOT_LABEL} --network {CTL_NET} "
                   "--read-only --cap-drop ALL --security-opt no-new-privileges:true --user 65534:65534 "
                   f"--entrypoint python {shlex.quote(iso.c['listener_image'])} "
                   f"-m http.server --bind 0.0.0.0 {LISTEN_PORT}")
        if r.returncode != 0:
            raise RuntimeError(f"слушатель {LISTENER}: код {r.returncode}\n{r.stderr[-500:]}")
        ip = iso.inspect(LISTENER)["NetworkSettings"]["Networks"][CTL_NET]["IPAddress"]
        gw = iso.inspect(CTL_NET, "network")["IPAM"]["Config"][0].get("Gateway")
        wait_for(lambda: iso.probe(CTL_NET, ip, LISTEN_PORT) == 1, 30)
        yield CTL_NET, ip, gw
    finally:                                                                # остаток — в отчёт, сбой теста не заслонён
        _drop_control(iso)
        left = iso.sh(f"docker network ls -q --filter name={CTL_NET}; docker ps -aq --filter name={LISTENER}")
        TALLY["ctl_left"] = left.returncode != 0 or left.stdout.strip() != ""


def test_networks_internal_isolated_no_ipv4(iso):
    for n in iso.c["places"]:
        info = iso.inspect(f"pcbk-stu-{n:02d}", "network")
        opts = info.get("Options") or {}
        assert info["Internal"] is True and opts.get("com.docker.network.bridge.gateway_mode_ipv4") == "isolated", n
        bridge = shlex.quote(opts.get("com.docker.network.bridge.name") or f"br-{info['Id'][:12]}")
        link = iso.sh(f"ip -o link show dev {bridge}")
        assert link.returncode == 0 and link.stdout.strip() != "", n          # мост есть: пустой адрес ниже не вакуумен
        addr = iso.sh(f"ip -4 -o addr show dev {bridge}")
        assert addr.returncode == 0 and addr.stdout.strip() == "", n
        TALLY["nets"] += 1
        place_row(n)["net"] = "да"


def test_negative_control_own_listener(iso):
    with control_network(iso) as (net, ip, gw):
        assert iso.probe(net, ip, LISTEN_PORT) == 1                         # проба видит открытое
        TALLY["listener_ctl"] += 1
        for n in iso.c["places"]:
            assert iso.probe(f"pcbk-stu-{n:02d}", ip, LISTEN_PORT) == 0, n   # из сети места — нет
            TALLY["listener_closed"] += 1
            place_row(n)["listener"] = "закрыт"
        if iso.c.get("ctl_host_port"):                                      # сервер: путь к хосту без изоляции (Д2)
            assert gw and iso.probe(net, gw, iso.c["ctl_host_port"]) == 1
            TALLY["host_port"] += 1


def closed_targets(iso, n: int, witness: int, services: list, core: list) -> list[tuple[str, str, int]]:
    """(имя цели, адрес, порт), закрытые из сети места n; в имени — без адресов ЛВС и историана."""
    stu = iso.c["stu_net"]
    gws = sorted({f"{stu}.{n}.1"} | {c.get("Gateway") for c in
                                      iso.inspect(f"pcbk-stu-{n:02d}", "network")["IPAM"]["Config"]} - {None})
    out = [(f"мост/шлюз {g.rsplit('.', 1)[-1]}:{p}", g, p) for g in gws for p in PORTS_CLOSED]
    out += [(f"core .2:{p}", f"{stu}.{n}.2", p) for p in PORTS_CLOSED]
    out += [(f"сосед {witness:02d}:{OC_PORT}", f"{stu}.{witness}.3", OC_PORT),
            ("ЛВС:22", iso.c["lan_addr"], 22), ("ЛВС:443", iso.c["lan_addr"], 443),
            ("историан:1433", iso.c["historian_addr"], 1433), ("интернет:443", "1.1.1.1", 443)]
    out += [(f"{name}@{net}:{port}", ip, port) for name, port, net, ip in services]
    out += [(f"core@{net}:{CORE_PORT}", ip, CORE_PORT) for net, ip in core if ip != f"{stu}.{n}.2"]
    return out


def test_every_place_network_isolated(iso):                                 # неуспех 1; места людей не будятся
    stu, (w1, w2) = iso.c["stu_net"], iso.c["witnesses"]
    services = [(name, port, net, ip) for name, port in SERVICES for net, ip in addresses(iso, name)]
    core = addresses(iso, CORE)
    with awake(iso, w1), awake(iso, w2):
        for w in (w1, w2):
            assert iso.probe(f"pcbk-stu-{w:02d}", f"{stu}.{w}.3", OC_PORT) == 1, w
            TALLY["witness_open"] += 1
        for n in iso.c["places"]:
            net, witness = f"pcbk-stu-{n:02d}", (w1 if n != w1 else w2)
            assert iso.probe(net, f"{stu}.{n}.2", CORE_PORT) == 1, n
            TALLY["core_open"] += 1
            row = place_row(n)
            row["core"] = "открыт"
            targets = closed_targets(iso, n, witness, services, core)
            opened = [label for label, addr, port in targets if iso.probe(net, addr, port) != 0]
            row["targets"], row["opened"] = len(targets), len(opened)
            TALLY["targets"] += len(targets)
            TALLY["opened"] += len(opened)
            assert opened == [], (n, opened)
        for w in (w1, w2):                                                  # свидетели живы до конца: нули не вакуумны
            assert iso.probe(f"pcbk-stu-{w:02d}", f"{stu}.{w}.3", OC_PORT) == 1, w
            TALLY["witness_end"] += 1


def test_every_place_has_only_own_secrets(iso):
    sd = os.path.normpath(iso.c["secrets_dir"])
    for n in iso.c["places"]:
        nn = f"{n:02d}"
        mounts = {m["Destination"]: m.get("Source") or "" for m in iso.inspect(f"pcbk-student-{nn}")["Mounts"]
                  if m["Destination"] == "/run/secrets" or m["Destination"].startswith("/run/secrets/")}
        own = {"/run/secrets/data-token": f"student-{nn}.data-token",
               "/run/secrets/llm-token": f"student-{nn}.llm-token", "/run/secrets/opencode-pw": f"student-{nn}.pw"}
        assert sorted(mounts) == sorted(own), n
        assert {d: os.path.normpath(s) for d, s in mounts.items()} == {d: f"{sd}/{f}" for d, f in own.items()}, n
        TALLY["mounts"] += 1
        place_row(n)["mounts"] = "3 своих"
        assert iso.lines(inspect_cmd(n), f"-x -F /pcbk-student-{nn}") >= 1, n   # inspect раскрыт: ноль не вакуумен
        for secret in FOREIGN_SECRETS:
            assert iso.counts(secret, inspect_cmd(n)) == (1, 0), (n, secret)
        TALLY["inspect_clean"] += 1
        place_row(n)["hits"] = "0"
    for w in iso.c["witnesses"]:                                           # окружение самого OpenCode
        with awake(iso, w):
            assert iso.lines(environ_cmd(w), f"'^{OC_PW_ENV}='") == 1, w            # ровно один OpenCode
            for secret in FOREIGN_SECRETS:
                assert iso.counts(secret, environ_cmd(w)) == (1, 0), (w, secret)
            TALLY["environ_clean"] += 1


def test_isolation_output_has_no_values(iso):
    """Команды значений печатают только число: положительный контроль — ровно «1», stderr пуст."""
    assert iso.number(f"grep -c . {shlex.quote(iso.c['secrets_dir'].rstrip('/') + '/bdrv.env')}", "bdrv.env") > 0
    for secret in FOREIGN_SECRETS:
        r = iso.sh(iso.ctrl_cmd(secret))
        only_count = r.stdout == "1\n" and r.stderr == ""                  # булево: вывод в сообщение не идёт
        assert only_count, secret


def test_probe_failure_is_error_not_closed():
    """I-6 р1: сбой docker run (неизвестный runtime — отказ демона до создания контейнера, код 125) и молчание ssh или
    оболочки (нет «rc=») — ошибка пробы, а не «закрыто». Только локально, контейнер не создаётся."""
    bad_runtime = Iso({"ssh": ["bash", "-c"], "runtime": "pcbk-no-such-runtime"})
    dead_shell = Iso({"ssh": ["false"], "runtime": "runc"})
    for probe in (bad_runtime, dead_shell):
        with pytest.raises(RuntimeError):
            probe.probe("none", "127.0.0.1", 9)


def test_value_parse_like_core(tmp_path):
    """VALUE_PY читает пароль БДРВ и ключ так же, как core: read_env_file и read_key_file — на сгенерированных значениях
    с кавычками, export, CRLF, повтором, комментарием, пробелами и BOM. Только локально; значения — не в вывод."""
    spec = importlib.util.spec_from_file_location("pcbk_core_secrets", REPO / "core" / "pcbk_core" / "secrets.py")
    core_secrets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core_secrets)
    gen = lambda tail="": secrets.token_urlsafe(12) + tail                 # noqa: E731
    env_cases = {
        "plain": f"BDRV_HOST=h\n{PW}={gen('<&>$`')}\n",
        "single": f"{PW}='{gen(chr(34) + ' x')}'\n",
        "double": f'{PW}="{gen(chr(39) + chr(92))}"\n',
        "export_crlf": f"  export {PW}={gen()}  \r\nBDRV_USER=u\r\n",
        "dup_comment": f"# {PW}={gen()}\n{PW}={gen()}\n\n{PW}={gen()}\n",
        "odd_quotes": f"{PW}='{gen()}\"\n",
    }
    key_cases = {"newline": f"{gen()}\n", "spaces_crlf": f"  {gen()} \r\n", "bom": f"\ufeff{gen()}\n"}
    same = {}
    for kind, cases in (("env", env_cases), ("key", key_cases)):
        for case, text in cases.items():
            d = tmp_path / f"{kind}-{case}"
            d.mkdir()
            name = "bdrv.env" if kind == "env" else "openrouter.key"
            (d / name).write_text(text, encoding="utf-8", newline="")
            out = tmp_path / f"{kind}-{case}.out"
            local = Iso({"ssh": ["bash", "-c"], "secrets_dir": str(d)})
            local.sh(f'{local.value_cmd("bdrv-pw" if kind == "env" else name)} && cp "$p" {shlex.quote(str(out))}; '
                     'rm -f "$p"')
            want = (core_secrets.read_env_file(str(d / name))[PW] if kind == "env"
                    else core_secrets.read_key_file(str(d / name)))
            same[f"{kind}-{case}"] = out.exists() and out.read_text(encoding="utf-8") == want
    assert same == {case: True for case in same}


REPORT_CSS = ("body{font:14px/1.45 system-ui,sans-serif;margin:24px;color:#1b1b1b;background:#fff}h1{font-size:20px}"
              "table{border-collapse:collapse;margin:10px 0}th,td{border:1px solid #bbb;padding:3px 8px;font-size:13px}"
              "th{background:#f0f0f0}td.n{text-align:right}ul{margin:6px 0 6px 20px;padding:0}")


def report(iso) -> tuple[list[str], str]:
    """Сводка модуля строками (для вывода) и страница HTML (для снимка): только числа и номера мест."""
    t, places = TALLY, list(iso.c["places"])
    n, extra = len(places), 1 if iso.c.get("ctl_host_port") else 0
    controls = t["core_open"] + t["witness_open"] + t["listener_ctl"] + t["host_port"]
    woken = ", ".join(f"{w:02d}" for w in sorted(t["woken"])) or "никого (свидетели уже работали)"
    lines = [
        f"Мест: {n}; сети Internal, изолированный шлюз, мост без IPv4 — {t['nets']} из {n}",
        f"Закрытых целей: {t['targets']} проб из сетей мест, открытых — {t['opened']}",
        f"Положительных контролей: {controls} из {n + 3 + extra} (N + {3 + extra}: core .2:{CORE_PORT} из сети места — "
        f"{t['core_open']}, свидетели из своей сети — {t['witness_open']}, свой слушатель из своей сети — "
        f"{t['listener_ctl']}" + (f", шлюз сети контроля к хосту — {t['host_port']}" if extra else "") + ")",
        f"Отрицательный контроль: свой слушатель из сети каждого места закрыт — {t['listener_closed']} из {n}",
        f"Свидетели живы и в конце обхода: {t['witness_end']} из 2",
        f"Секреты: ровно три своих — {t['mounts']} из {n}; пароль БДРВ и ключ OpenRouter в docker inspect — 0 "
        f"у {t['inspect_clean']} из {n}; в окружении OpenCode свидетелей — 0 у {t['environ_clean']} из 2 "
        "(положительный контроль на голом значении — 1)",
        f"Разбужены только свидетели: {woken}; снова спят — {'да' if not t['not_asleep'] else 'нет'}",
        f"Сеть и слушатель отрицательного контроля удалены — {'нет' if t['ctl_left'] else 'да'}",
    ]
    head = ("Место", "Сеть", f"core .2:{CORE_PORT}", "Свой слушатель", "Закрытых целей", "Открыто",
            "/run/secrets", "Совпадений в inspect")
    rows = []
    for p in places:
        r = place_row(p)
        cells = (f"{p:02d}", r["net"], r["core"], r["listener"], r["targets"], r["opened"], r["mounts"], r["hits"])
        rows.append("<tr>" + "".join(f"<td class=\"n\">{html.escape(str(c))}</td>" if isinstance(c, int)
                                     else f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")
    where = "сервер" if iso.live else "тестовый стенд"
    page = ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>Изоляция целиком</title>"
            f"<style>{REPORT_CSS}</style></head><body><h1>Изоляция целиком — {where} (Д11-R125)</h1><ul>"
            + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul><table><thead><tr>"
            + "".join(f"<th>{html.escape(h)}</th>" for h in head) + "</tr></thead><tbody>" + "".join(rows)
            + "</tbody></table></body></html>")
    return lines, page


def test_isolation_report(iso, tmp_path):
    """Последний в модуле: сводка в вывод и (PCBK_SHOTS) снимок страницы отчёта; свидетели снова спят, сеть и
    слушатель отрицательного контроля удалены."""
    lines, page = report(iso)
    print("\n".join(lines))
    where = os.environ.get("PCBK_SHOTS")
    if where:
        page_file = tmp_path / "isolation.html"
        page_file.write_text(page, encoding="utf-8")
        with Browser() as browser:
            tab = browser.page(isolated=True)
            tab.goto(page_file.as_uri())
            tab.wait_selector("table", 20)
            width, height = tab.eval("[Math.max(document.documentElement.scrollWidth, 1280), "
                                     "document.documentElement.scrollHeight]")
            tab._call("Emulation.setDeviceMetricsOverride",                  # вся страница — одним снимком
                      {"width": int(width), "height": int(height), "deviceScaleFactor": 1, "mobile": False})
            tab.screenshot(Path(where) / SHOT[iso.live])
            assert browser.handler_errors == [] and tab.console_errors() == [], browser.handler_errors
    assert (TALLY["not_asleep"], TALLY["ctl_left"]) == ([], False)
