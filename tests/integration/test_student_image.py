"""Образ рабочего места без компоновки: конфигурация, метаданные, точка входа, проверка здоровья."""
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from workplace import IMAGE, MODELS, ONESHOT_LABEL, OR_MODELS, STUB_PREFIX, WORKPLACE_ENV

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "student" / "config"
SEC = 10 ** 9
# без этих правил студент одобряет чтение вне /work, и пароль из /proc/self/environ уходит в разговор;
# "task": "deny" убирает инструмент task из запроса к модели — подагентов нет (Д4б-R20); "webfetch": "deny" —
# интернета у стенда нет, тело каждого запроса короче (оценка — байт на токен), ручки core без токена из места
# не читаются (до Д5); "pcbk_save_agent": "deny" — второй слой, не граница (Д7-R63): правила конфигурации идут до
# правил агента, allow у konstruktor побеждает, у агентов без своего правила инструмента записи нет.
# "*": "deny" — первым (итоговая доводка Д7б, I-1): агент без своих правил (файл без permission или с битым YAML —
# gray-matter отдаёт из кэша пустой frontmatter) получает mode: all и умолчание OpenCode "*": allow, то есть bash
# (env печатает пароль места). Порядок ключей OpenCode сохраняет (propertyOrder: "original"), побеждает последнее
# совпадение: read ниже снова разрешает чтение, а агенты образа и шаблона А2 несут свои "*": deny и allow позже
PERMISSION = {"*": "deny", "external_directory": "deny", "task": "deny", "webfetch": "deny",
              "read": {"*": "allow", "*.env": "deny", "*.env.*": "deny"}, "pcbk_save_agent": "deny"}
# MCP службы данных: токен места — файлом, без OAuth (Д4б-R19)
MCP = {"pcbk": {"type": "remote", "url": "http://core:8000/mcp", "enabled": True, "oauth": False,
                "headers": {"Authorization": "Bearer {file:/run/secrets/data-token}"}}}
# агент по умолчанию (Д6-R45): фронтматтер файла — побайтно; ни name, ни model, ни question
FRONT = ('# pcbk-reserve: агент по умолчанию, редакция 1 (Д6а); меняется только пересборкой образа\n'
         'description: "Помощник по данным: текущие значения и сводки тегов БДРВ"\nmode: primary\nsteps: 8\n'
         'permission:\n  "*": deny\n  pcbk_catalog_search: allow\n  pcbk_tag_now: allow\n  pcbk_tag_period: allow\n')
# Конструктор агентов (Д7-R63, research/09 §3.1): фронтматтер — побайтно; модели нет — модель стенда
K_FRONT = ('# pcbk-reserve: встроенный агент образа, редакция 1 (Д7); меняется только пересборкой образа\n'
           'description: "Конструктор агентов: за один разговор собирает с технологом его агента по узлу"\n'
           'mode: primary\nsteps: 12\npermission:\n  "*": deny\n  pcbk_catalog_search: allow\n'
           '  pcbk_tag_now: allow\n  pcbk_save_agent: allow\n')
# пробный агент теста external_directory — только в тесте, не в образе: без блока permission наследует правила
# opencode.json — образа (точка монтирования агентов Д7) или копии без правил (у pomoshnik read нет вовсе,
# охрану проверяет он)
PROBE_READ = '---\ndescription: "probe"\nmode: primary\n---\nprobe\n'
# тот же пробный агент с битым YAML (итоговая доводка Д7б, I-1; находка Б4): текст, который sanitize OpenCode не
# меняет, — место грузит его уже первой загрузкой как агента без своих правил: mode all, без описания
BROKEN_PROBE = '---\ndescription: "x\n---\nx\n'
# окно каждой модели: раннее сжатие разговора — защита бюджета (Д4б-R20)
LIMIT = {"limit": {"context": 131072, "output": 16000}}
# поддельная модель на Bun из бинарника OpenCode: первый ход — read /proc/self/environ, потом текст;
# имена инструментов каждого запроса с инструментами — строкой «tools [...]» в свой журнал;
# вне /llm/ — 404: иначе она отвечала бы и на /mcp, OpenCode ждал бы MCP свои 30 с (DEFAULT_TIMEOUT,
# mcp/index.ts:38) и только потом звал модель, а срок CLIENT_JS — 20 с; с 404 MCP сразу failed
FAKE_LLM_JS = """\
const chunk = (delta, finish) => "data: " + JSON.stringify({id: "c1", object: "chat.completion.chunk",
  created: 0, model: "stub", choices: [{index: 0, delta, finish_reason: finish}],
  ...(finish ? {usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2}} : {})}) + "\\n\\n"
Bun.serve({hostname: "0.0.0.0", port: 8000, async fetch(req) {
  if (!new URL(req.url).pathname.startsWith("/llm/")) return new Response("", {status: 404})
  const body = await req.json().catch(() => ({}))
  if ((body.tools || []).length) console.log("tools " + JSON.stringify(body.tools.map(t => t.function?.name)))
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
# двойник core, который держит /mcp HOLD_MCP_S с, потом 404: ленивый подъём MCP места дольше секунды, что ждёт
# GET /mcp проверки здоровья (ветка unknown); два транспорта OpenCode (StreamableHTTP, затем SSE) — вдвое дольше
HOLD_MCP_S = 8
HOLD_MCP_JS = f"""\
Bun.serve({{hostname: "0.0.0.0", port: 8000, async fetch(req) {{
  if (new URL(req.url).pathname === "/mcp") await Bun.sleep({HOLD_MCP_S * 1000})
  return new Response("", {{status: 404}})
}}}})
"""
# сервер на 127.0.0.1:4096, который сразу сбрасывает соединение (RST)
RESET_SERVER_JS = ('require("node:net").createServer(s => s.resetAndDestroy())'
                   '.listen(4096, "127.0.0.1", () => console.log("up"))')
# клиент изнутри места: пароль — из файла, не в argv; печатает правила, агентов (имя, mode, native, описание),
# разговор и висящие запросы; ход ведёт пробный агент probe-read (у агента по умолчанию read нет)
CLIENT_JS = """\
const pw = (await Bun.file("/run/secrets/opencode-pw").text()).trim()
const headers = {authorization: "Basic " + btoa("opencode:" + pw), "content-type": "application/json"}
const api = async (method, path, body) => {
  const r = await fetch("http://127.0.0.1:4096" + path, {method, headers, body: body && JSON.stringify(body)})
  const text = await r.text()
  return {status: r.status, body: text ? JSON.parse(text) : null}
}
const out = {permission: (await api("GET", "/config")).body.permission ?? null}
out.agents = ((await api("GET", "/agent")).body ?? []).map(a =>
  ({name: a.name, mode: a.mode, native: a.native, description: a.description ?? null}))
const ses = (await api("POST", "/session", {})).body.id
out.prompt = (await api("POST", `/session/${ses}/prompt_async`,
  {parts: [{type: "text", text: "Прочитай /proc/self/environ"}], agent: "probe-read"})).status
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


def ok(r: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    """Код 0 — иначе падение с хвостом stderr, а не непонятная ошибка ниже."""
    assert r.returncode == 0, f"{' '.join(r.args[:4])}…: код {r.returncode}\n{r.stderr[-2000:]}"
    return r


def run_place(tmp_path: Path, network: str, config: Path | None = None, agents: Path | None = None) -> str:
    """Место с OpenCode, как в компоновке: пароль, токены LLM и данных — файлами, tmpfs; id контейнера.

    Без файла data-token ссылка {file:…} в заголовке MCP делает конфигурацию неверной (Д2-решение 2).
    config — свой каталог конфигурации поверх образа (файлы 0644, каталоги 0755); agents — каталог агентов
    в точку монтирования Д7 …/opencode/agents, конфигурация образа при этом своя.
    """
    for name, value in (("pw", "probe-secret"), ("llm", "probe-token"), ("data", "probe-data-token")):
        (tmp_path / name).write_text(value)
        (tmp_path / name).chmod(0o444)
    args = ["--read-only", "--network", network, "--label", ONESHOT_LABEL,
            "--tmpfs", "/tmp:exec,mode=1777", "--tmpfs", "/var/lib/opencode:exec,uid=10001,gid=10001",
            "--tmpfs", "/work:uid=10001,gid=10001",
            "-v", f"{tmp_path / 'pw'}:/run/secrets/opencode-pw:ro",
            "-v", f"{tmp_path / 'llm'}:/run/secrets/llm-token:ro",
            "-v", f"{tmp_path / 'data'}:/run/secrets/data-token:ro"]
    if config is not None:
        args += ["-v", f"{config}:/etc/pcbk-opencode/opencode:ro"]
    if agents is not None:
        args += ["-v", f"{agents}:/etc/pcbk-opencode/opencode/agents:ro"]
    return ok(docker("run", "-d", *args, IMAGE)).stdout.strip()


def test_config_files():
    cfg = json.loads((CONFIG / "opencode.json").read_text())
    assert set(cfg) == {"autoupdate", "share", "snapshot", "enabled_providers", "model", "permission", "provider",
                        "mcp", "default_agent", "agent"}
    assert (cfg["autoupdate"], cfg["share"], cfg["snapshot"]) == (False, "disabled", False)
    assert (cfg["enabled_providers"], cfg["model"], cfg["permission"], cfg["mcp"]) == \
           (["pcbk", "pcbk-or"], "pcbk/z-ai/glm-5.3", PERMISSION, MCP)
    assert list(cfg["permission"]["read"]) == ["*", "*.env", "*.env.*"]    # побеждает последнее совпадение
    assert set(cfg["provider"]) == {"pcbk", "pcbk-or"}
    for pid, npm, names in (("pcbk", "@ai-sdk/openai-compatible", MODELS),
                            ("pcbk-or", "@openrouter/ai-sdk-provider", OR_MODELS)):
        p = cfg["provider"][pid]
        assert set(p) == {"npm", "options", "models"}, pid
        assert (p["npm"], list(p["models"])) == (npm, names)
        assert p["options"] == {"baseURL": "http://core:8000/llm/v1", "apiKey": "{file:/run/secrets/llm-token}"}
        assert all(m == LIMIT for m in p["models"].values()), pid
    assert (CONFIG / ".gitignore").read_bytes() == b""
    assert subprocess.run(["git", "check-ignore", "-q", "student/config/.gitignore"], cwd=ROOT).returncode == 1
    tools = sorted((CONFIG / "tools").iterdir())
    assert [p.name for p in tools] == ["apply_patch.ts", "bash.ts", "edit.ts", "write.ts"]
    for p in tools:
        src = p.read_text()
        assert "export default" in src and not re.search(r"\b(import|require)\b", src), p.name
        assert re.search(r'description:\s*"' + STUB_PREFIX, src) and re.search(r"args:\s*\{\s*\}", src), p.name


def test_default_agent_file():                                                  # Д6-R45
    cfg = json.loads((CONFIG / "opencode.json").read_text())
    assert cfg["default_agent"] == "pomoshnik"
    assert cfg["agent"] == {n: {"disable": True} for n in ("build", "plan", "general", "explore")}
    text = (CONFIG / "agent" / "pomoshnik.md").read_text()
    _, front, body = text.split("---\n", 2)
    assert front == FRONT                                                # ни name, ни model, ни question
    for rule in ("только из ответов этих инструментов", "Не составляй имя тега сам", "не называй их",
                 "Смысл _MV у регуляторов не", "общие знания, не норматив ПЦБК"):        # пометка — как в AGENTS.md
        assert rule in body, rule
    assert "не из данных стенда" not in body


def test_konstruktor_file():                                                    # Д7-R63
    _, front, body = (CONFIG / "agent" / "konstruktor.md").read_text().split("---\n", 2)
    assert front == K_FRONT                                              # ни name, ни model, ни question
    flat = " ".join(body.split())                      # фразы сверяются без переносов строк (Д7б, П7)
    assert flat.startswith("Ты — Конструктор агентов учебного стенда ПЦБК.")   # первая строка — её ждёт Б4
    for rule in ("Имена тегов бери ТОЛЬКО из ответов pcbk_catalog_search", "Черновик агента",
                 "Сохраняй только после явного согласия", "status \"unavailable\", \"rate\" или \"error\"",
                 "общие знания, не норматив ПЦБК", "Больше двух повторных попыток не делай",
                 "в этом ходе pcbk_save_agent больше не вызывай",                      # Д7б, m9
                 "дословно, одной строкой", "только его слова согласия",             # student_reply ≤200 знаков
                 "Он появится в списке „Мои агенты“ через несколько секунд",         # Д7-R63, поправка 2
                 "служба отклоняет такие слова и в запрете",                         # доводка р1, I1: prompt_forbidden
                 "В инструкции и назначении агента НЕЛЬЗЯ",                          # m3: норма-число и в purpose
                 "«konstruktor» и не с «pcbk» в начале",                             # m1: name_reserved службы
                 "Сохранённого агента поменять нельзя"):                             # m2: name_taken — только новый
        assert rule in flat, rule
    assert "через минуту" not in flat                                  # при сбое — пересказ message службы (р1)
    stand = (CONFIG / "AGENTS.md").read_text()                       # правила стенда в системном тексте всех агентов
    assert stand.startswith("# Правила учебного стенда ПЦБК (для всех агентов)")
    for rule in ("Норматива в данных стенда нет", "общие знания, не норматив ПЦБК", "Шкала прибора"):
        assert rule in " ".join(stand.split()), rule
    permission = json.loads((CONFIG / "opencode.json").read_text())["permission"]
    assert permission["pcbk_save_agent"] == "deny" and list(permission)[-1] == "pcbk_save_agent"   # Д7б, П8
    assert list(permission)[0] == "*" and permission["*"] == "deny"     # итоговая доводка Д7б, I-1: запрет — первым
    assert sorted(p.name for p in (CONFIG / "agent").iterdir()) == ["konstruktor.md", "pomoshnik.md"]


def config_with_probe(tmp_path: Path, rules: bool) -> Path:
    """Копия конфигурации образа + agent/probe-read.md; rules=False — без блока permission (отрицательный контроль)."""
    cfg = tmp_path / "config"
    shutil.copytree(CONFIG, cfg)
    (cfg / "agent" / "probe-read.md").write_text(PROBE_READ)
    if not rules:
        data = json.loads((cfg / "opencode.json").read_text())
        data.pop("permission", None)
        (cfg / "opencode.json").write_text(json.dumps(data))
    for p in [cfg, *cfg.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    return cfg


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


def test_health_survives_reset_connection(student_image):
    """Сервер рвёт соединение (RST): в журнале здоровья — одна строка с кодом, без ошибок bash и SIGPIPE."""
    cid = ok(docker("run", "-d", "--network", "none", "--read-only", "--no-healthcheck", "--label", ONESHOT_LABEL,
                    "-e", "BUN_BE_BUN=1", "--entrypoint", "/usr/local/bin/opencode", IMAGE,
                    "-e", RESET_SERVER_JS)).stdout.strip()
    try:
        deadline = time.monotonic() + 10
        while "up" not in docker("logs", cid).stdout:        # иначе 000 от «порт закрыт» — пустая проверка
            assert time.monotonic() < deadline, "сервер со сбросом не поднялся за 10 с"
            time.sleep(0.2)
        runs = [docker("exec", cid, "/usr/local/bin/pcbk-health") for _ in range(20)]
        assert {(r.returncode, r.stdout, r.stderr) for r in runs} == {(1, "000\n", "")}
    finally:
        docker("rm", "-f", cid)


def test_no_setuid_binaries(student_image):
    """Ни одного setuid/setgid-файла: su, mount, passwd и прочие — не лазейка к root."""
    r = ok(docker("run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "find", IMAGE,
                  "/", "-xdev", "-perm", "/6000", "-type", "f"))
    assert r.stdout == ""


@pytest.mark.parametrize("broken", [False, True])
def test_health_needs_working_instance(student_image, tmp_path, broken):         # Review Focus 3
    cfg = None
    if broken:                          # конфигурация без .gitignore — как образ из неполного клона
        cfg = tmp_path / "config"
        shutil.copytree(CONFIG, cfg, ignore=shutil.ignore_patterns(".gitignore"))
        for p in [cfg, *cfg.rglob("*")]:
            p.chmod(0o755 if p.is_dir() else 0o644)
    cid = run_place(tmp_path, network="none", config=cfg)
    try:
        codes, deadline = [], time.monotonic() + 30
        while not (codes and codes[-1].startswith("200")) and time.monotonic() < deadline:
            codes.append(docker("exec", cid, "/usr/local/bin/pcbk-health").stdout.strip())
            time.sleep(1)
        assert all(re.fullmatch(r"\d{3}( mcp=[a-z_]+)?", c) for c in codes)
        assert codes[-1].startswith("200") is (not broken)     # /global/health здесь дал бы 200 и сломанному
    finally:
        docker("rm", "-f", cid)


def test_health_reports_mcp_without_core(student_image, tmp_path):             # Review Focus 1
    """core недоступен: код и слово MCP — «200 mcp=failed»; переподключение — не чаще раза в 60 с."""
    cid = run_place(tmp_path, network="none")
    try:
        outs, deadline = [], time.monotonic() + 45
        while "200 mcp=failed" not in outs and time.monotonic() < deadline:
            outs.append(docker("exec", cid, "/usr/local/bin/pcbk-health").stdout.strip())
            time.sleep(2)
        assert "200 mcp=failed" in outs and all(re.fullmatch(r"000|\d{3}|200 mcp=[a-z_]+", o) for o in outs), outs
        def mark() -> str:                                      # время метки попытки connect, с
            return docker("exec", cid, "stat", "-c", "%Y", "/tmp/pcbk-mcp-retry").stdout.strip()

        first = mark()
        assert first.isdigit()                                  # попытка connect была — метка есть
        r = docker("exec", cid, "/usr/local/bin/pcbk-health")
        assert (r.returncode, r.stdout, r.stderr) == (0, "200 mcp=failed\n", "")    # ни пароля, ни ошибок bash
        assert mark() == first                                  # не чаще раза в 60 с
        # граница — «не меньше 50 с»: 48 с — рано (запас на секунду между exec), 50 с — новая попытка;
        # метка на час в будущем (часы шагнули назад) — старая, иначе попыток не было бы час
        now = int(ok(docker("exec", cid, "date", "+%s")).stdout)
        for age, retried in ((48, False), (50, True), (-3600, True)):
            ok(docker("exec", cid, "touch", "-d", f"@{now - age}", "/tmp/pcbk-mcp-retry"))
            assert docker("exec", cid, "/usr/local/bin/pcbk-health").stdout == "200 mcp=failed\n"
            assert (mark() != str(now - age)) is retried, age
    finally:
        docker("rm", "-f", cid)


def test_health_reports_unknown_while_mcp_starts(student_image, tmp_path):      # Д4б-R18, ветка unknown
    """core держит /mcp дольше секунды: GET /mcp ждёт ленивого подъёма MCP — «200 mcp=unknown»; core ответил
    404 — «200 mcp=failed». Слово unknown — только пока MCP поднимается, а не вместо ответа."""
    (tmp_path / "core.js").write_text(HOLD_MCP_JS)
    (tmp_path / "core.js").chmod(0o444)
    net = f"pcbk-test-mcp-{uuid.uuid4().hex[:8]}"
    cids = []
    assert docker("network", "create", "--internal", "--label", ONESHOT_LABEL, net).returncode == 0
    try:
        cids.append(ok(docker("run", "-d", "--network", net, "--network-alias", "core", "--label", ONESHOT_LABEL,
                              "--read-only", "--no-healthcheck", "--tmpfs", "/tmp:exec,mode=1777",
                              "-e", "BUN_BE_BUN=1", "-v", f"{tmp_path / 'core.js'}:/fake/core.js:ro",
                              "--entrypoint", "/usr/local/bin/opencode", IMAGE, "/fake/core.js")).stdout.strip())
        cid = run_place(tmp_path, network=net)
        cids.append(cid)
        outs, deadline = [], time.monotonic() + 4 * HOLD_MCP_S + 30
        while "200 mcp=failed" not in outs and time.monotonic() < deadline:
            outs.append(docker("exec", cid, "/usr/local/bin/pcbk-health").stdout.strip())
            time.sleep(1)
        assert all(re.fullmatch(r"\d{3}( mcp=[a-z_]+)?", o) for o in outs), outs
        words = [o for o in outs if o.startswith("200")]
        # сначала unknown (MCP ещё поднимается), потом failed; между ними ничего другого
        assert words and words[0] == "200 mcp=unknown" and words[-1] == "200 mcp=failed", outs
        assert set(words) == {"200 mcp=unknown", "200 mcp=failed"}, outs
    finally:
        if cids:
            docker("rm", "-f", *cids)
        docker("network", "rm", net)


def test_health_never_disposes():                                               # Review Focus 1; Д4б-R18
    """Переподключает только connect одного MCP: пересоздание экземпляра порвало бы /event и ход."""
    text = (ROOT / "student" / "pcbk-health").read_text()
    assert "/mcp/pcbk/connect" in text and "dispose" not in text and "/instance" not in text


@pytest.mark.parametrize("rules,probe", [(True, PROBE_READ), (False, PROBE_READ), (True, BROKEN_PROBE)],
                         ids=["True", "False", "broken-yaml"])
def test_external_read_refused_without_asking(student_image, tmp_path, rules, probe):
    """Модель просит read /proc/self/environ: с правилами самого образа — отказ без вопроса и пароля в разговоре.

    Ход ведёт пробный агент probe-read без своих прав — он наследует правила opencode.json (Д6-R45): у агента
    по умолчанию read нет, и на нём охрана external_directory проверяла бы пустоту.
    rules=True — конфигурация образа как есть, probe-read — в точке монтирования агентов Д7: проверяются
    правила, зашитые в образ, а не копия дерева. Ходу доступно только чтение: "*": "deny" образа первым убирает
    из запроса к модели всё, что не разрешено ниже (итоговая доводка Д7б, I-1; MCP двойник отвечает 404).
    rules=False — отрицательный контроль: копия конфигурации без блока permission, OpenCode спрашивает студента, а
    модель получает bash и edit.
    broken-yaml — файл probe-read с битым YAML в точке монтирования агентов: место грузит его агентом mode all без
    описания и своих правил, и у его хода — тоже только чтение (I-1, находка Б4).
    """
    (tmp_path / "llm.js").write_text(FAKE_LLM_JS)
    (tmp_path / "llm.js").chmod(0o444)
    net = f"pcbk-test-llm-{uuid.uuid4().hex[:8]}"
    cfg = agents = None
    if rules:
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "probe-read.md").write_text(probe)
        agents.chmod(0o755)
        (agents / "probe-read.md").chmod(0o644)
    else:
        cfg = config_with_probe(tmp_path, rules)
    cids = []
    assert docker("network", "create", "--internal", "--label", ONESHOT_LABEL, net).returncode == 0
    try:
        # модель — тот же образ: бинарник OpenCode с BUN_BE_BUN=1 работает как Bun
        cids.append(ok(docker("run", "-d", "--network", net, "--network-alias", "core", "--label", ONESHOT_LABEL,
                              "--read-only", "--no-healthcheck", "--tmpfs", "/tmp:exec,mode=1777",
                              "-e", "BUN_BE_BUN=1", "-v", f"{tmp_path / 'llm.js'}:/fake/llm.js:ro",
                              "--entrypoint", "/usr/local/bin/opencode", IMAGE, "/fake/llm.js")).stdout.strip())
        cid = run_place(tmp_path, network=net, config=cfg, agents=agents)
        cids.append(cid)
        deadline = time.monotonic() + 30
        while not docker("exec", cid, "/usr/local/bin/pcbk-health").stdout.startswith("200"):
            assert time.monotonic() < deadline, "место не ответило 200 за 30 с"
            time.sleep(1)
        r = ok(docker("exec", "-e", "BUN_BE_BUN=1", cid, "/usr/local/bin/opencode", "-e", CLIENT_JS, timeout=60))
        out = json.loads(r.stdout)
        # инструменты в запросах к модели: запрет по правилу с шаблоном * убирает их из запроса
        sent = [json.loads(line[len("tools "):]) for line in ok(docker("logs", cids[0])).stdout.splitlines()
                if line.startswith("tools ")]
        assert sent and all("read" in names for names in sent)         # журнал модели прочитан
        tools = [p for m in out["messages"] for p in m["parts"] if p["type"] == "tool"]
        assert out["prompt"] == 204 and [p["tool"] for p in tools] == ["read"]
        assert tools[0]["state"]["input"] == {"filePath": "/proc/self/environ"}
        agent = next(a for a in out["agents"] if a["name"] == "probe-read")
        if rules:
            assert out["permission"] == PERMISSION                      # правила образа дошли до сервера
            assert list(out["permission"])[0] == "*"                    # порядок ключей сервер сохранил
            assert out["asked"] == [] and tools[0]["state"]["status"] == "error"
            assert "rule which prevents you" in tools[0]["state"]["error"]    # отказ по правилу, не сбой
            assert "probe-secret" not in json.dumps(out["messages"])
            assert set().union(*sent) - {"invalid"} == {"read"}         # список разрешённого, не «чего нет»
            want = ("all", None) if probe == BROKEN_PROBE else ("primary", "probe")
            assert (agent["mode"], agent["native"], agent["description"]) == (want[0], False, want[1])
        else:
            assert {"task", "webfetch", "bash", "edit"} <= set().union(*sent)   # без правил — есть: проверка видит
            assert [(a["permission"], a["metadata"]["filepath"]) for a in out["asked"]] == \
                   [("external_directory", "/proc/self/environ")]
    finally:
        if cids:
            docker("rm", "-f", *cids)
        docker("network", "rm", net)
