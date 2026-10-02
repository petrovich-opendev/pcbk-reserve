"""Сквозной разговор места: OpenCode → LLM-прокси (двойник OpenRouter) → служба данных по MCP; core на .2 в сети
места, токен данных файлом; отказы прокси в чате без шторма повторов; MCP переподключается после core."""
import json
import re
import secrets
import time

import pytest

from conftest import TEST_MGMT_KEY, TEST_OPENROUTER_KEY, system_text, turn_bodies, wait_for
from secret import Secret

Q = "что сейчас по тегу 20FAKE_001_PV?"
MCP_WORD = re.compile(r"\bmcp=([a-z_]+)")
PCBK3 = {"pcbk_catalog_search", "pcbk_tag_now", "pcbk_tag_period"}       # агент по умолчанию Д6–Д8 (Д6-R45)
PCBK5 = PCBK3 | {"pcbk_tag_series", "pcbk_calc_runtime"}                 # с Д9а — ряд и наработка (Д9-R89)
KONSTRUKTOR = {"pcbk_catalog_search", "pcbk_tag_now", "pcbk_save_agent"}  # инструменты Конструктора (Д7-R63)


@pytest.fixture(scope="module")
def place01(stack):                          # своя фикстура: running Д2 (test_students.py) держит и место 02
    stack.start("pcbk-student-01")
    wait_for(lambda: "mcp=connected" in stack.health_output("pcbk-student-01"), timeout=90)
    yield
    stack.stop("pcbk-student-01")


def tool_requests(stack, session):           # заголовков клиента прокси не пересылает — сессия только в теле
    return [r for r in stack.fake_requests()
            if r["body"].get("session_id") == f"student-01:{session}" and r["body"].get("tools")]


def tool_names(stack, session):              # имена инструментов во всех запросах к модели этой сессии
    return {t["function"]["name"] for r in stack.fake_requests()
            if r["body"].get("session_id") == f"student-01:{session}" for t in r["body"].get("tools", [])}


def settled(stack, session):                 # К10: строки пишет поток-писатель, заголовок OpenCode идёт фоном
    rows = lambda: [c for c in stack.llm_calls(50) if c["session"] == session]
    wait_for(lambda: bool(rows()) and all(r["status"] != "open" and r["cost_source"] != "pending" for r in rows()),
             timeout=30)
    return rows()


def test_mcp_unknown_only_while_starting(stack, place01):                       # «unknown» не держится при живом core
    words = [m.group(1) if (m := MCP_WORD.search(o)) else None for o in stack.health_log("pcbk-student-01")]
    assert "connected" in words, words
    first = words.index("connected")
    # до первого connected — только ленивый подъём MCP (unknown) или ещё не 200 (без слова); после — только connected
    assert set(words[:first]) <= {"unknown", None} and set(words[first:]) == {"connected"}, words
    assert words.count("unknown") <= 2, words                                     # не дольше первых тактов
    stack.wait_status(lambda d: next((c["state"], c["detail"]) for c in d["checks"]
                                     if c["component"] == "student-01") == ("ok", "работает"), timeout=60)


def ip_in(stack, name: str, network: str) -> str:
    """Адрес контейнера в сети — из inspect: адреса в pcbk-ctl и pcbk-front динамические."""
    return stack.inspect(name)["NetworkSettings"]["Networks"][network]["IPAddress"]


def test_place_reaches_only_core(stack, place01):                              # Review Focus 3
    stu = stack.stu_net
    assert stack.probe("pcbk-stu-01", f"{stu}.1.2", 8000) == 1                   # положительный контроль
    assert stack.probe("pcbk-stu-02", f"{stu}.2.2", 8000) == 1                   # .2.2 жив — ноль ниже не пустой
    assert stack.probe("pcbk-egress", "172.31.250.82", 8000) == 1               # core в сети выхода жив
    assert stack.probe("pcbk-egress", "172.31.250.84", 8080) == 1               # двойник OpenRouter жив
    # Д5а (Ш21): прокси сокета и core в pcbk-ctl, core и edge в pcbk-front. Зона front открывает ручки шлюза
    # всему, что пришло на адрес core в pcbk-front, и держится на том, что у места туда нет маршрута. У каждого
    # нуля — положительный контроль тем же адресом и портом из своей сети
    inner = [(ip_in(stack, "pcbk-sp-ctl", "pcbk-ctl"), 2375, "pcbk-ctl"),
             (ip_in(stack, "pcbk-core", "pcbk-ctl"), 8000, "pcbk-ctl"),
             (ip_in(stack, "pcbk-core", "pcbk-front"), 8000, "pcbk-front"),
             (ip_in(stack, "pcbk-edge", "pcbk-front"), 8443, "pcbk-front")]
    for addr, port, net in inner:
        assert stack.probe(net, addr, port) == 1, (net, addr, port)              # адрес жив из своей сети
    for addr, port in ((f"{stu}.2.2", 8000), ("172.31.250.82", 8000), ("172.31.250.84", 8080),
                       *((a, p) for a, p, _ in inner)):
        assert stack.probe("pcbk-stu-01", addr, port) == 0, addr                 # держит отсутствие маршрута
    assert stack.inspect("pcbk-core")["HostConfig"]["Sysctls"]["net.ipv4.ip_forward"] == "0"   # второй слой


def test_conversation_tool_via_mcp_and_cost(stack, place01):                    # Review Focus 5
    before = stack.data_event_count("student-01", "mcp")
    out = stack.converse(1, Q)
    assert (out["status"], out["error"]) == ("done", None) and out["final_text"].startswith("Готово:")
    tool = next(t for t in out["tools"] if t["tool"] == "pcbk_tag_now")
    assert tool["status"] == "completed" and json.loads(tool["output"])["status"] == "unavailable"   # историана нет — словами
    # ответ модели построен на результате инструмента: начало вывода pcbk_tag_now (до статуса) — в тексте ответа
    head = tool["output"][:tool["output"].index('"unavailable"') + len('"unavailable"')]
    assert head in out["final_text"], (head, out["final_text"])
    calls = settled(stack, out["session"])
    assert len(calls) >= 2 and {c["caller"] for c in calls} == {"student-01"}
    assert {c["cost_source"] for c in calls} == {"stream"}
    assert stack.data_event_count("student-01", "mcp") == before + 1
    e = stack.data_events(1, channel="mcp")[0]
    assert (e["caller"], e["channel"], e["tool"]) == ("student-01", "mcp", "tag_now")


def test_openrouter_key_never_in_workplace(stack, place01):                     # «неуспех 1»: ключа в месте нет
    keys = (TEST_OPENROUTER_KEY, TEST_MGMT_KEY)
    # положительный контроль: ключи — в секретах core, поиск их видит
    for key, path in zip(keys, ("/run/secrets/openrouter.key", "/run/secrets/openrouter-mgmt.key")):
        assert stack.exec("pcbk-core", "grep", "-c", "-F", key, path) == "1", path
    # свой разговор через прокси: ответ прокси и метка вопроса ложатся в данные места — источник для контроля ниже
    marker = f"pcbk-marker-{secrets.token_hex(4)}"
    assert stack.converse(1, f"Ответь одним словом: да {marker}", tools={"pcbk_*": False})["status"] == "done"
    pid = stack.opencode_pid("pcbk-student-01")
    # всё, что место хранит и видит: окружение OpenCode, секреты, конфигурация, данные (там же разговоры), /work, /tmp
    where = f"/proc/{pid}/environ /run/secrets /etc/pcbk-opencode /var/lib/opencode /work /tmp"
    grep = lambda pattern: stack.sh("pcbk-student-01", f"grep -r -l -a -F -e '{pattern}' {where}")
    # положительный контроль источника: свой токен LLM и метка разговора этого теста находятся тем же поиском;
    # найденное — списки файлов, сами токены в текст сбоя не попадают
    own_files = grep(stack.llm_token(1))[1].split()
    talk_files = grep(marker)[1].split()
    assert "/run/secrets/llm-token" in own_files and any(f.startswith("/var/lib/opencode/") for f in talk_files)
    for key in keys:
        code, found, _ = grep(key)
        assert (code, found) == (1, ""), key                                     # 1 — grep ничего не нашёл
    code, body = stack.oc(1, "GET", "/config", 1)
    body = Secret(body)                    # в теле /config — токен LLM места: ни в traceback, ни в --showlocals его нет
    own, leaked = stack.llm_token(1) in body, [k for k in keys if k in body]     # {file:} подставлен — ответ тот
    assert (code, own, leaked) == (200, True, [])


def test_conversation_without_tools_reads_no_data(stack, place01):             # задача 6, шаг 2 — тот же способ
    assert "mcp=connected" in stack.health_output("pcbk-student-01")            # иначе запрет проверял бы пустоту
    control = stack.converse(1, "Ответь одним словом: да")                       # положительный контроль: без запрета
    # у агента по умолчанию других инструментов нет (Д6-R45): без запрета — ровно пять инструментов данных (Д9а)
    assert tool_names(stack, control["session"]) - {"invalid"} == PCBK5
    before = stack.data_event_count("student-01", "mcp")
    out = stack.converse(1, "Ответь одним словом: да", tools={"pcbk_*": False})
    assert out["status"] == "done" and not [t for t in out["tools"] if t["tool"].startswith("pcbk_")]
    reqs = [r for r in stack.fake_requests() if r["body"].get("session_id") == f"student-01:{out['session']}"]
    assert reqs                                                                  # запросы сессии дошли до двойника
    assert not any(r["body"].get("tools") for r in reqs)                        # запрет снял все пять инструментов
    assert stack.data_event_count("student-01", "mcp") == before                # к данным не ходили


def test_default_agent_tools_are_data_only(stack, place01):                    # Д6-R45: ни read, ни question
    out = stack.converse(1, Q)
    assert tool_names(stack, out["session"]) - {"invalid"} == PCBK5   # «invalid» — служебный, если OpenCode его шлёт
    msgs = json.loads(stack.oc(1, "GET", f"/session/{out['session']}/message", 1)[1])
    assert {m["info"].get("agent") for m in msgs if m["info"]["role"] == "assistant"} == {"pomoshnik"}


def test_agent_tool_sets(stack, place01):                                       # агенты образа (Д7-R63, Д9-R89)
    # «что умеешь?» без слова FAKESAVE*: двойник зовёт pcbk_tag_now, а не запись агента (Д7б, m22)
    k = stack.converse(1, "что умеешь?", agent="konstruktor")
    # allow агента побеждает глобальный "pcbk_save_agent": "deny" — правила конфигурации идут раньше (О7)
    assert tool_names(stack, k["session"]) - {"invalid"} == KONSTRUKTOR       # ряд и наработка Конструктору не нужны
    assert k["status"] == "done" and not [t for t in k["tools"] if t["tool"] == "pcbk_save_agent"]
    p = stack.converse(1, "что умеешь?")                                        # агент по умолчанию места
    assert "pcbk_save_agent" not in tool_names(stack, p["session"])             # тем же способом, что у Конструктора
    assert tool_names(stack, p["session"]) - {"invalid"} == PCBK5                # MCP подключён — проверка не пуста
    ks, ps = (system_text(turn_bodies(stack, 1, out["session"])[0]) for out in (k, p))   # тела хода по сессии (I4)
    assert "Правила учебного стенда ПЦБК" in ks and "Правила учебного стенда ПЦБК" in ps   # AGENTS.md — у каждого
    assert "Ты — Конструктор агентов учебного стенда ПЦБК" in ks and "Конструктор агентов" not in ps


def test_model_question_is_rejected_and_reported(stack, place01):               # раунд 2: уточняющий вопрос модели
    # правило сессии даёт question (у агента по умолчанию его нет); converse идёт мимо шлюза
    out = stack.converse(1, "FAKEASK какой тег?", timeout=60, tools={"question": True})
    assert out["status"] == "asked" and out["asked"] == [{"kind": "question", "tool": "question"}]
    assert stack.oc(1, "GET", "/question", 1)[1].strip() == "[]"               # висящих вопросов нет
    assert json.loads(stack.oc(1, "GET", "/session/status", 1)[1]).get(out["session"]) is None   # сессия не busy


def test_turn_limit_shown_in_chat_without_retry_storm(stack, place01):          # Review Focus 2
    out = stack.converse(1, "FAKELOOP собери всё", timeout=240)
    assert len(tool_requests(stack, out["session"])) == 16
    assert "Предел шагов на ответ" in json.dumps(out["error"], ensure_ascii=False)
    time.sleep(15)                                                                # первый повтор OpenCode — через 2 с
    settled(stack, out["session"])
    assert len(tool_requests(stack, out["session"])) == 16
    assert stack.llm_refusals(out["session"], "pcbk_turn_limit") == 1            # повторов нет: их видно только в журнале


def test_budget_message_in_chat(stack, place01):                                # Review Focus 2
    stack.recreate("core", {"LLM_TOTAL_USD": "0.001"})                            # в бюджет упрётся первый или второй вызов хода
    try:
        # состояние MCP — из записи после старта нового core, а не из записи до пересоздания
        core_started = stack.started_at("pcbk-core")
        wait_for(lambda: "mcp=connected" in stack.health_output("pcbk-student-01", since=core_started), timeout=90)
        out = stack.converse(1, Q)
        assert "Общий бюджет стенда на модель" in json.dumps(out["error"], ensure_ascii=False)
        time.sleep(15)
        settled(stack, out["session"])
        assert stack.llm_refusals(out["session"], "pcbk_budget_spent") <= 2       # шаг и заголовок, без повторов
    finally:
        stack.recreate("core", {})


def test_mcp_reconnects_after_core_restart(stack):                               # Review Focus 1; последний в модуле
    if stack.inspect("pcbk-student-02")["State"]["Running"]:
        stack.stop("pcbk-student-02")                                             # иначе MCP уже подключён — тест пустой
    stack.stop("pcbk-core")
    try:
        stack.start("pcbk-student-02")                                            # место встаёт без core
        wait_for(lambda: "mcp=failed" in stack.health_output("pcbk-student-02"), timeout=90)
        data = stack.wait_status(lambda d: any(c["component"] == "student-02" and c["state"] == "warn"
                                               for c in d["checks"]), timeout=60)
        assert next(c["detail"] for c in data["checks"] if c["component"] == "student-02") == \
               "нет связи со службой данных (MCP)"
        seen = set()
        for _ in range(3):                                                        # три такта проверки здоровья (30 с)
            time.sleep(31)
            seen.add(stack.health_output("pcbk-student-02").strip())
        assert seen == {"200 mcp=failed"}                                         # не мигает: unknown не появляется
        stack.start("pcbk-core")
        core_started = stack.started_at("pcbk-core")
        wait_for(lambda: "mcp=connected" in stack.health_output("pcbk-student-02", since=core_started), timeout=150)
        out = stack.converse(2, Q)
        assert any(t["tool"] == "pcbk_tag_now" and t["status"] == "completed" for t in out["tools"])
    finally:                                                                      # один выход: место спит, core работает
        if stack.inspect("pcbk-student-02")["State"]["Running"]:
            stack.stop("pcbk-student-02")
        if not stack.inspect("pcbk-core")["State"]["Running"]:
            stack.start("pcbk-core")
