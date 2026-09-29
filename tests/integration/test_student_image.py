"""Образ рабочего места без компоновки: конфигурация, метаданные, точка входа, проверка здоровья."""
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from workplace import IMAGE, ONESHOT_LABEL, STUB_PREFIX, WORKPLACE_ENV

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "student" / "config"
SEC = 10 ** 9
# без этих правил студент одобряет чтение вне /work, и пароль из /proc/self/environ уходит в разговор
PERMISSION = {"external_directory": "deny", "read": {"*": "allow", "*.env": "deny", "*.env.*": "deny"}}
# поддельная модель на Bun из бинарника OpenCode: первый ход — read /proc/self/environ, потом текст
FAKE_LLM_JS = """\
const chunk = (delta, finish) => "data: " + JSON.stringify({id: "c1", object: "chat.completion.chunk",
  created: 0, model: "stub", choices: [{index: 0, delta, finish_reason: finish}],
  ...(finish ? {usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2}} : {})}) + "\\n\\n"
Bun.serve({hostname: "0.0.0.0", port: 8000, async fetch(req) {
  const body = await req.json().catch(() => ({}))
  const call = (body.tools || []).some(t => t.function?.name === "read")
    && !(body.messages || []).some(m => m.role === "tool")
  const delta = call
    ? {role: "assistant", tool_calls: [{index: 0, id: "call_1", type: "function",
        function: {name: "read", arguments: JSON.stringify({filePath: "/proc/self/environ"})}}]}
    : {role: "assistant", content: "готово"}
  if (body.stream === false)
    return Response.json({id: "c1", object: "chat.completion", created: 0, model: "stub",
      choices: [{index: 0, message: {role: "assistant", content: "готово"}, finish_reason: "stop"}],
      usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2}})
  return new Response(chunk(delta, null) + chunk({}, call ? "tool_calls" : "stop") + "data: [DONE]\\n\\n",
    {headers: {"content-type": "text/event-stream"}})
}})
"""
# клиент изнутри места: пароль — из файла, не в argv; печатает правила, разговор и висящие запросы
CLIENT_JS = """\
const pw = (await Bun.file("/run/secrets/opencode-pw").text()).trim()
const headers = {authorization: "Basic " + btoa("opencode:" + pw), "content-type": "application/json"}
const api = async (method, path, body) => {
  const r = await fetch("http://127.0.0.1:4096" + path, {method, headers, body: body && JSON.stringify(body)})
  const text = await r.text()
  return {status: r.status, body: text ? JSON.parse(text) : null}
}
const out = {permission: (await api("GET", "/config")).body.permission ?? null}
const ses = (await api("POST", "/session", {})).body.id
out.prompt = (await api("POST", `/session/${ses}/prompt_async`,
  {parts: [{type: "text", text: "Прочитай /proc/self/environ"}]})).status
const deadline = Date.now() + 20000
while (Date.now() < deadline) {
  out.asked = (await api("GET", "/permission")).body
  out.messages = (await api("GET", `/session/${ses}/message`)).body
  const busy = (await api("GET", "/session/status")).body[ses]?.type === "busy"
  const read = out.messages.flatMap(m => m.parts).find(p => p.type === "tool" && p.tool === "read")
  if (out.asked.length || (!busy && read && ["completed", "error"].includes(read.state.status))) break
  await Bun.sleep(500)
}
console.log(JSON.stringify(out))
"""


def docker(*args, timeout=60):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def test_config_files():
    cfg = json.loads((CONFIG / "opencode.json").read_text())
    assert set(cfg) == {"autoupdate", "share", "snapshot", "enabled_providers", "model", "permission", "provider"}
    assert (cfg["autoupdate"], cfg["share"], cfg["snapshot"], cfg["enabled_providers"], cfg["model"]) == \
           (False, "disabled", False, ["pcbk"], "pcbk/stub")
    assert cfg["permission"] == PERMISSION
    assert list(cfg["permission"]["read"]) == ["*", "*.env", "*.env.*"]    # побеждает последнее совпадение
    pcbk = cfg["provider"]["pcbk"]
    assert (pcbk["npm"], list(pcbk["models"])) == ("@ai-sdk/openai-compatible", ["stub"])
    assert pcbk["options"] == {"baseURL": "http://core:8000/llm/v1", "apiKey": "{file:/run/secrets/llm-token}"}
    assert (CONFIG / ".gitignore").read_bytes() == b""
    assert subprocess.run(["git", "check-ignore", "-q", "student/config/.gitignore"], cwd=ROOT).returncode == 1
    tools = sorted((CONFIG / "tools").iterdir())
    assert [p.name for p in tools] == ["apply_patch.ts", "bash.ts", "edit.ts", "write.ts"]
    for p in tools:
        src = p.read_text()
        assert "export default" in src and not re.search(r"\b(import|require)\b", src), p.name
        assert re.search(r'description:\s*"' + STUB_PREFIX, src) and re.search(r"args:\s*\{\s*\}", src), p.name


def test_image_metadata(student_image):
    cfg = json.loads(docker("image", "inspect", IMAGE).stdout)[0]["Config"]
    assert dict(item.split("=", 1) for item in cfg["Env"]) == WORKPLACE_ENV     # ровно, со значениями
    assert (cfg["User"], cfg["WorkingDir"], cfg["Entrypoint"]) == \
           ("10001:10001", "/work", ["/usr/local/bin/pcbk-entrypoint"])
    hc = cfg["Healthcheck"]
    assert hc["Test"] == ["CMD", "/usr/local/bin/pcbk-health"]
    assert (hc["Interval"], hc["Timeout"], hc["StartPeriod"], hc["StartInterval"], hc["Retries"]) == \
           (30 * SEC, 5 * SEC, 20 * SEC, 2 * SEC, 3)
    out = docker("run", "--rm", "--network", "none", "--entrypoint", "bash", IMAGE,
                 "-c", "opencode --version; rg --version").stdout.splitlines()
    assert out[0] == "1.18.33" and out[1].startswith("ripgrep 15.1.0")


@pytest.mark.parametrize("case", ["missing", "empty", "newline", "unreadable"])
def test_entrypoint_refuses_without_password(student_image, tmp_path, case):   # Review Focus 1
    mount = []
    if case != "missing":
        pw = tmp_path / "pw"
        pw.write_text({"empty": "", "newline": "\n", "unreadable": "not-for-uid-10001"}[case])
        pw.chmod(0o600 if case == "unreadable" else 0o444)   # 0600 чужого uid — как umask 077 без chmod
        mount = ["-v", f"{pw}:/run/secrets/opencode-pw:ro"]
    r = docker("run", "--rm", "--network", "none", "--read-only", *mount, IMAGE, timeout=30)
    assert r.returncode == 78 and "нет пароля" in r.stderr


def test_health_prints_only_code(student_image, tmp_path):
    pw = tmp_path / "pw"
    pw.write_text("probe-secret")
    pw.chmod(0o444)
    r = docker("run", "--rm", "--network", "none", "--read-only", "-v", f"{pw}:/run/secrets/opencode-pw:ro",
               "--entrypoint", "/usr/local/bin/pcbk-health", IMAGE, timeout=30)
    assert (r.returncode, r.stdout, r.stderr) == (1, "000\n", "")     # сервера нет — только код


@pytest.mark.parametrize("broken", [False, True])
def test_health_needs_working_instance(student_image, tmp_path, broken):         # Review Focus 3
    for name, value in (("pw", "probe-secret"), ("llm", "probe-token")):
        (tmp_path / name).write_text(value)
        (tmp_path / name).chmod(0o444)
    args = ["--read-only", "--network", "none", "--label", ONESHOT_LABEL,
            "--tmpfs", "/tmp:exec,mode=1777", "--tmpfs", "/var/lib/opencode:exec,uid=10001,gid=10001",
            "--tmpfs", "/work:uid=10001,gid=10001",
            "-v", f"{tmp_path / 'pw'}:/run/secrets/opencode-pw:ro",
            "-v", f"{tmp_path / 'llm'}:/run/secrets/llm-token:ro"]
    if broken:                          # конфигурация без .gitignore — как образ из неполного клона
        cfg = tmp_path / "config"
        shutil.copytree(CONFIG, cfg, ignore=shutil.ignore_patterns(".gitignore"))
        for p in [cfg, *cfg.rglob("*")]:
            p.chmod(0o755 if p.is_dir() else 0o644)
        args += ["-v", f"{cfg}:/etc/pcbk-opencode/opencode:ro"]
    cid = docker("run", "-d", *args, IMAGE).stdout.strip()
    try:
        codes, deadline = [], time.monotonic() + 30
        while codes[-1:] != ["200"] and time.monotonic() < deadline:
            codes.append(docker("exec", cid, "/usr/local/bin/pcbk-health").stdout.strip())
            time.sleep(1)
        assert all(re.fullmatch(r"\d{3}", c) for c in codes)
        assert (codes[-1] == "200") is (not broken)     # /global/health здесь дал бы 200 и сломанному
    finally:
        docker("rm", "-f", cid)


@pytest.mark.parametrize("rules", [True, False])
def test_external_read_refused_without_asking(student_image, tmp_path, rules):
    """Модель просит read /proc/self/environ: с правилами образа — отказ без вопроса и пароля в разговоре.

    rules=False — отрицательный контроль: конфигурация без блока permission, OpenCode спрашивает студента.
    """
    for name, value in (("pw", "probe-secret"), ("llm", "probe-token"), ("llm.js", FAKE_LLM_JS)):
        (tmp_path / name).write_text(value)
        (tmp_path / name).chmod(0o444)
    net = f"pcbk-test-llm-{uuid.uuid4().hex[:8]}"
    place = ["--read-only", "--network", net, "--label", ONESHOT_LABEL,
             "--tmpfs", "/tmp:exec,mode=1777", "--tmpfs", "/var/lib/opencode:exec,uid=10001,gid=10001",
             "--tmpfs", "/work:uid=10001,gid=10001",
             "-v", f"{tmp_path / 'pw'}:/run/secrets/opencode-pw:ro",
             "-v", f"{tmp_path / 'llm'}:/run/secrets/llm-token:ro"]
    if not rules:
        cfg = tmp_path / "config"
        shutil.copytree(CONFIG, cfg)
        data = json.loads((cfg / "opencode.json").read_text())
        data.pop("permission", None)
        (cfg / "opencode.json").write_text(json.dumps(data))
        for p in [cfg, *cfg.rglob("*")]:
            p.chmod(0o755 if p.is_dir() else 0o644)
        place += ["-v", f"{cfg}:/etc/pcbk-opencode/opencode:ro"]
    cids = []
    assert docker("network", "create", "--internal", "--label", ONESHOT_LABEL, net).returncode == 0
    try:
        # модель — тот же образ: бинарник OpenCode с BUN_BE_BUN=1 работает как Bun
        cids.append(docker("run", "-d", "--network", net, "--network-alias", "core", "--label", ONESHOT_LABEL,
                           "--read-only", "--no-healthcheck", "--tmpfs", "/tmp:exec,mode=1777",
                           "-e", "BUN_BE_BUN=1", "-v", f"{tmp_path / 'llm.js'}:/fake/llm.js:ro",
                           "--entrypoint", "/usr/local/bin/opencode", IMAGE, "/fake/llm.js").stdout.strip())
        cid = docker("run", "-d", *place, IMAGE).stdout.strip()
        cids.append(cid)
        deadline = time.monotonic() + 30
        while docker("exec", cid, "/usr/local/bin/pcbk-health").stdout.strip() != "200":
            assert time.monotonic() < deadline, "место не ответило 200 за 30 с"
            time.sleep(1)
        r = docker("exec", "-e", "BUN_BE_BUN=1", cid, "/usr/local/bin/opencode", "-e", CLIENT_JS, timeout=60)
        out = json.loads(r.stdout)
        tools = [p for m in out["messages"] for p in m["parts"] if p["type"] == "tool"]
        assert out["prompt"] == 204 and [p["tool"] for p in tools] == ["read"]
        assert tools[0]["state"]["input"] == {"filePath": "/proc/self/environ"}
        if rules:
            assert out["permission"] == PERMISSION                      # правила дошли до сервера
            assert out["asked"] == [] and tools[0]["state"]["status"] == "error"
            assert "rule which prevents you" in tools[0]["state"]["error"]    # отказ по правилу, не сбой
            assert "probe-secret" not in json.dumps(out["messages"])
        else:
            assert [(a["permission"], a["metadata"]["filepath"]) for a in out["asked"]] == \
                   [("external_directory", "/proc/self/environ")]
    finally:
        if cids:
            docker("rm", "-f", *cids)
        docker("network", "rm", net)
