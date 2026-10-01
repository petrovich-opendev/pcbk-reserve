"""save_agent службы данных (Д7-R57): проверка черновика, запись по шаблону в каталог места вызывающего, событие и
счётчики. Отказ — обычный результат, не isError; место — только из токена.

Фикстура served — роль с каталогом agent_catalog() под serve_role (каталог закреплён: загрузка двойника историана его
не заменит), корень агентов tmp_path/agents со student-01…10; served_no_catalog — то же, но каталог не грузится.
"""
import asyncio
import json
import logging
import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace

import pytest

from fakes import WHITELIST, FakeHistorian
from helpers import (SETTINGS, TOKEN, FakeMono, agent_catalog, make_agents_root, make_role, mcp_session, non_root,
                     serve_role, sha, wait_http, wait_until)
from pcbk_core.agents.store import MAX_AGENTS, AgentStore, StoreError
from pcbk_core.data import DataRole
from pcbk_core.data import save_agent as save_agent_module
from pcbk_core.data.catalog import Catalog
from pcbk_core.data.historian import HistorianError
from pcbk_core.data.save_agent import SAVE_LIMIT, SAVE_MESSAGES, SaveAgent, refused_message
from pcbk_core.data.service import MESSAGES
from pcbk_core.secrets import TokenTable

# тестовые токены — не 48 шестнадцатеричных знаков (шаблон проверки на секреты)
TOKEN3 = "test-token-student-03"
TOKEN11 = "test-token-student-11"
OPS = "test-token-ops"
TOKENS = TokenTable({"student-01": sha(TOKEN), "student-03": sha(TOKEN3), "student-11": sha(TOKEN11),
                     "ops": sha(OPS)})

ARGS = {"name": "proverka", "title": "Проверка Д7", "node": "Тестовый узел", "purpose": "Проверка записи агента службой",
        "tools": ["catalog_search", "tag_now"], "tags": ["20FAKE_001_PV"], "prompt": "Ты — помощник. " * 20,
        "student_reply": "да"}
OK_KEYS = {"tool", "status", "message", "problems", "notes", "name", "title", "already"}
OTHER_KEYS = {"tool", "status", "message", "problems", "notes"}
JSON_COLUMNS = ("requested", "tags", "sent", "returned")


def _events(db_path: str):
    """Строки agent_events по порядку записи; столбцы JSON — списками."""
    def events(tool: str | None = None) -> list[dict]:
        with closing(sqlite3.connect(db_path)) as db:
            db.row_factory = sqlite3.Row
            sql = "SELECT * FROM agent_events" + (" WHERE tool = ?" if tool else "") + " ORDER BY id"
            rows = [dict(r) for r in db.execute(sql, (tool,) if tool else ())]
        for row in rows:
            for key in JSON_COLUMNS:
                row[key] = json.loads(row[key]) if row[key] is not None else None
        return rows
    return events


def _role(tmp_path, fake: FakeHistorian | None = None, **kw) -> tuple[DataRole, object]:
    root = make_agents_root(tmp_path / "agents")
    role, _ = make_role(replace(SETTINGS, AGENTS_ROOT=str(root)), fake, db_dir=tmp_path, tokens=TOKENS, **kw)
    return role, root


def settle(role: DataRole) -> None:
    """Первая попытка каталога и первый опрос свежести записаны (их строки журнала — раньше событий): у закреплённого
    каталога нет живых тегов, и запись «свежесть: …» не попадает в вывод между фазами теста."""
    wait_until(lambda: role.events.count(channel="system") >= 2)


@pytest.fixture
def served(tmp_path):
    role, root = _role(tmp_path)
    pinned = agent_catalog()
    load = role._catalog_loaded
    role._catalog_loaded = lambda catalog, clock, mono: load(pinned, clock, mono)    # каталог двойника не заменит
    with serve_role(role) as url:
        assert role.catalog is pinned
        settle(role)
        yield SimpleNamespace(url=url, root=root, role=role, events=_events(role.settings.DB_PATH))


@pytest.fixture
def served_no_catalog(tmp_path):
    role, root = _role(tmp_path, FakeHistorian(fail=HistorianError("query", "каталог не читается")))
    with serve_role(role, wait_catalog=False) as url:
        settle(role)
        assert not role.catalog.loaded and role.catalog_failures >= 1
        yield SimpleNamespace(url=url, root=root, role=role, events=_events(role.settings.DB_PATH))


def direct(tmp_path, **kw) -> tuple[DataRole, object]:
    """Роль без сервера: каталог agent_catalog(), журнал событий и учёт агентов открыты, как в lifespan, —
    SaveAgent.call зовётся напрямую."""
    role, root = _role(tmp_path, **kw)
    role.catalog = agent_catalog()
    assert role.events.open(), role.events.last_error
    assert role.agents.open(), role.agents.last_error
    return role, root


async def save(url, token, args=ARGS):
    async with mcp_session(url + "/mcp", token) as s:
        r = await s.call_tool("save_agent", args)
    return r.isError, r.structuredContent


def files_of(root, place: int) -> list[str]:
    return sorted(p.name for p in (root / f"student-{place:02d}").iterdir())


# --- бриф ---

@pytest.mark.anyio
async def test_saves_into_callers_place(served):
    is_error, out = await save(served.url, TOKEN3)
    assert not is_error and (out["status"], out["name"], out["already"]) == ("ok", "proverka", False)
    assert (served.root / "student-03" / "proverka.md").exists() and not (served.root / "student-01" / "proverka.md").exists()
    ev = served.events(tool="save_agent")[-1]
    assert (ev["caller"], ev["agent"], ev["outcome"], ev["summary"]) == ("student-03", "proverka", "ok", "saved")
    assert served.role.agent_stats.count_1h("saved") == 1


@pytest.mark.anyio
async def test_refusal_is_result_not_error(served):
    is_error, out = await save(served.url, TOKEN, {**ARGS, "tools": ["bash"], "name": "build"})
    assert not is_error and out["status"] == "refused"
    assert {(p["field"], p["code"]) for p in out["problems"]} == {("tools", "tool_not_allowed"), ("name", "name_reserved")}
    assert not any((served.root / "student-01").iterdir()) and served.role.agent_stats.count_1h("refused") == 1


@pytest.mark.anyio
async def test_place_from_token_only(served):           # Review Focus 4: в аргументах места нет
    await save(served.url, TOKEN3, {**ARGS, "place": 4, "caller": "student-04"})
    assert not any((served.root / "student-04").iterdir())
    assert files_of(served.root, 3) == ["proverka.md"]


@pytest.mark.anyio
async def test_ops_cannot_save(served):
    _, out = await save(served.url, OPS)
    assert (out["status"], out["message"]) == ("refused", "Агента сохраняет только рабочее место студента")


@pytest.mark.anyio
async def test_retry_already_and_name_taken(served):
    await save(served.url, TOKEN3)
    assert (await save(served.url, TOKEN3))[1]["already"] is True
    out = (await save(served.url, TOKEN3, {**ARGS, "title": "Другое"}))[1]
    assert out["problems"][0]["code"] == "name_taken" and out["problems"][0]["existing"] == ["proverka"]
    assert [e["summary"] for e in served.events(tool="save_agent")] == ["saved", "already", "refused: name_taken"]
    assert served.role.agent_stats.count_1h("saved") == 1                    # already — не новый агент


@pytest.mark.anyio
async def test_extra_argument_ignored(served):
    await save(served.url, TOKEN3, {**ARGS, "model": "openai/x", "foo": "bar"})
    text = (served.root / "student-03" / "proverka.md").read_text("utf-8")
    assert "openai/x" not in text and "foo" not in text


@pytest.mark.anyio
async def test_rate_limit(served):
    for _ in range(SAVE_LIMIT):
        await save(served.url, TOKEN, {**ARGS, "name": "build"})
    assert (await save(served.url, TOKEN))[1]["status"] == "rate"
    assert served.events(tool="save_agent")[-1]["outcome"] == "rate" and not any((served.root / "student-01").iterdir())
    assert (await save(served.url, TOKEN3))[1]["status"] == "ok"            # окно — на вызывающего
    async with mcp_session(served.url + "/mcp", OPS) as s:                   # частота — раньше «только место студента»
        outs = [(await s.call_tool("save_agent", ARGS)).structuredContent["status"] for _ in range(SAVE_LIMIT + 1)]
    assert outs == ["refused"] * SAVE_LIMIT + ["rate"]


@pytest.mark.anyio
async def test_catalog_not_loaded(served_no_catalog):
    assert (await save(served_no_catalog.url, TOKEN))[1]["status"] == "unavailable"
    assert served_no_catalog.role.agent_stats.count_1h("unavailable") == 1             # m9
    assert (await save(served_no_catalog.url, TOKEN, {**ARGS, "tags": []}))[1]["status"] == "ok"


@pytest.mark.anyio
async def test_numeric_limit_is_flag_not_refusal(served):                   # I6
    _, out = await save(served.url, TOKEN3, {**ARGS, "prompt": ARGS["prompt"] + "Нормальная температура — 85–95 °C."})
    assert out["status"] == "ok"
    assert served.events(tool="save_agent")[-1]["summary"] == "saved; flags: prompt_numeric_limit"
    assert served.role.agents.flags_of(3, "proverka") == ("prompt_numeric_limit",)


@pytest.mark.anyio
async def test_too_many_agents_text(served):                                 # m9
    for i in range(MAX_AGENTS):
        (served.root / "student-01" / f"m{i:02d}.md").write_text("x")
    p = (await save(served.url, TOKEN))[1]["problems"][0]
    assert (p["code"], p["text"]) == ("too_many_agents",
        "У этого рабочего места уже 20 агентов — новый не сохранить; обратитесь к преподавателю")


@non_root
@pytest.mark.anyio
async def test_dir_unavailable(served):
    (served.root / "student-01").chmod(0o555)
    _, out = await save(served.url, TOKEN)
    assert (out["status"], out["message"]) == ("unavailable", "Каталог агентов недоступен — сообщите преподавателю")
    assert served.role.agent_stats.count_1h("unavailable") == 1 and served.role.agent_stats.store_error is None


def test_log_line_has_no_name(served, caplog):           # строка журнала без имени агента и полей
    caplog.set_level(logging.INFO, logger="pcbk_core.data")     # П4: строка ok — INFO, корень тестов — WARNING
    asyncio.run(save(served.url, TOKEN3))
    line = next(r.getMessage() for r in caplog.records if "tool=save_agent" in r.getMessage())
    assert "proverka" not in line and "Проверка" not in line and "outcome=ok" in line
    assert "20FAKE" not in line and "помощник" not in line and "student-03/" not in line
    assert line.startswith("tool=save_agent caller=student-03 channel=mcp outcome=ok codes=saved ms=")


# --- сверх брифа: ответ и событие ---

@pytest.mark.anyio
async def test_answer_and_event_shape(served):
    _, ok = await save(served.url, TOKEN3, {**ARGS, "tags": [" 20fake_001_pv "]})
    assert set(ok) == OK_KEYS and (ok["tool"], ok["title"], ok["problems"], ok["notes"]) == (
        "save_agent", "Проверка Д7", [], [])
    assert ok["message"] == SAVE_MESSAGES["ok"] == ("Агент сохранён. Он появится в «Мои агенты» после окончания этого "
                                                   "ответа.")
    ev = served.events(tool="save_agent")[-1]
    assert (ev["channel"], ev["requested"], ev["tags"], ev["sent"], ev["cache"], ev["row_count"], ev["cost"]) == (
        "mcp", ["proverka"], ["20FAKE_001_PV"], [], "none", 0, 0.0)          # теги — канонические из каталога
    text = (served.root / "student-03" / "proverka.md").read_text("utf-8")
    assert "- `20FAKE_001_PV` — Температура \"сукна\" ## x y, °C\n" in text   # описание — из каталога службы
    assert "# pcbk-reserve: student-03 " in text
    _, refused = await save(served.url, TOKEN3, {**ARGS, "name": "build", "tools": ["bash"]})
    assert set(refused) == OTHER_KEYS and refused["message"] == "Черновик не сохранён: 2 замечания"
    assert served.events(tool="save_agent")[-1]["summary"] == "refused: name_reserved, tool_not_allowed"
    assert served.events(tool="save_agent")[-1]["agent"] == "build"


@pytest.mark.parametrize("n, text", [(1, "1 замечание"), (2, "2 замечания"), (4, "4 замечания"), (5, "5 замечаний"),
                                     (11, "11 замечаний"), (12, "12 замечаний"), (21, "21 замечание"),
                                     (22, "22 замечания"), (25, "25 замечаний")])
def test_refused_message_counts(n, text):
    assert refused_message(n) == "Черновик не сохранён: " + text


@pytest.mark.anyio
async def test_missing_fields_are_all_wrong_type(tmp_path):
    role, root = direct(tmp_path)
    out = await role.save_agent.call("student-01", {})
    assert out["status"] == "refused" and out["message"] == "Черновик не сохранён: 8 замечаний"
    assert [(p["field"], p["code"]) for p in out["problems"]] == [
        (f, "wrong_type") for f in ("name", "title", "node", "purpose", "tools", "tags", "prompt", "student_reply")]
    assert _events(role.settings.DB_PATH)(tool="save_agent")[-1]["agent"] is None and files_of(root, 1) == []


# --- сверх брифа: охранные ветки Д7-R57 ---

@pytest.mark.anyio
async def test_place_outside_1_10_cannot_save(served):                       # student-11: токен есть, места нет
    _, out = await save(served.url, TOKEN11)
    assert (out["status"], out["message"], out["problems"]) == ("refused", SAVE_MESSAGES["not_student"], [])
    ev = served.events(tool="save_agent")[-1]
    assert (ev["caller"], ev["outcome"], ev["summary"]) == ("student-11", "refused", "refused: not_student")
    assert served.role.agent_stats.count_1h("refused") == 1
    assert all(not any((served.root / f"student-{n:02d}").iterdir()) for n in range(1, 11))


@pytest.mark.anyio
async def test_place_ignores_place_keys_in_raw(tmp_path):                    # место — только из вызывающего
    role, root = direct(tmp_path)
    out = await role.save_agent.call("student-03", {**ARGS, "place": 4, "caller": "student-04",
                                                    "student": "student-04"})
    assert out["status"] == "ok" and files_of(root, 3) == ["proverka.md"] and files_of(root, 4) == []
    assert "student-04" not in (root / "student-03" / "proverka.md").read_text("utf-8")


@pytest.mark.anyio
async def test_config_error_is_unavailable_first(tmp_path):                 # порядок: 1) настройка раньше частоты
    role, root = direct(tmp_path, config_error="белый список не читается")
    outs = [await role.save_agent.call(c, ARGS) for c in ["ops"] + ["student-01"] * (SAVE_LIMIT + 1)]
    assert {(o["status"], o["message"]) for o in outs} == {("unavailable", "белый список не читается")}
    ev = _events(role.settings.DB_PATH)(tool="save_agent")
    assert len(ev) == SAVE_LIMIT + 2
    assert {(e["outcome"], e["summary"]) for e in ev} == {("unavailable", "unavailable: config")}
    assert role.agent_stats.count_1h("unavailable") == SAVE_LIMIT + 2 and files_of(root, 1) == []


@pytest.mark.anyio
async def test_catalog_not_loaded_answer(tmp_path):
    role, root = direct(tmp_path)
    role.catalog = Catalog.empty()
    out = await role.save_agent.call("student-01", {**ARGS, "tools": ["bash"]})
    assert (out["status"], out["message"]) == ("unavailable", MESSAGES["catalog"])
    assert [p["code"] for p in out["problems"]] == ["tool_not_allowed"]     # прочие замечания — сразу
    ev = _events(role.settings.DB_PATH)(tool="save_agent")[-1]
    assert (ev["outcome"], ev["summary"]) == ("unavailable", "unavailable: catalog_not_loaded")
    assert files_of(root, 1) == []


@pytest.mark.anyio
async def test_store_error_is_unavailable_and_flagged(tmp_path, monkeypatch):
    role, root = direct(tmp_path)

    def broken(*a, **k):
        raise StoreError("учёт агентов: OperationalError SQLITE_BUSY")

    with monkeypatch.context() as m:
        m.setattr(role.agents, "save", broken)
        out = await role.save_agent.call("student-01", ARGS)
    assert (out["status"], out["message"]) == ("unavailable", SAVE_MESSAGES["store"])
    assert role.agent_stats.store_error is not None and role.agent_stats.count_1h("unavailable") == 1
    ev = _events(role.settings.DB_PATH)(tool="save_agent")[-1]
    assert (ev["outcome"], ev["summary"]) == ("unavailable", "unavailable: store_error")
    assert (await role.save_agent.call("student-01", ARGS))["status"] == "ok"
    assert role.agent_stats.store_error is None                                # запись прошла — учёт снова пишется


@pytest.mark.anyio
async def test_render_failure_is_error_without_file(tmp_path, monkeypatch):
    role, root = direct(tmp_path)

    def too_big(*a, **k):
        raise ValueError("файл агента больше MAX_FILE_BYTES")

    monkeypatch.setattr(save_agent_module, "render_agent_file", too_big)
    out = await role.save_agent.call("student-01", ARGS)
    assert (out["status"], set(out)) == ("error", OTHER_KEYS) and files_of(root, 1) == []
    ev = _events(role.settings.DB_PATH)(tool="save_agent")[-1]
    assert (ev["outcome"], ev["summary"]) == ("error", "error: render_failed")
    assert sum(role.agent_stats.count_1h(k) for k in ("saved", "refused", "unavailable")) == 0


@pytest.mark.anyio
async def test_same_place_saves_are_serialized(tmp_path):                   # замок места: повтор — already
    role, root = direct(tmp_path)
    write_new = role.agents._write_new

    def slow(*a, **k):
        time.sleep(0.3)                     # первый ещё пишет файл — второй без замка прочитал бы «строки нет»
        return write_new(*a, **k)

    role.agents._write_new = slow
    outs = await asyncio.gather(role.save_agent.call("student-01", ARGS), role.save_agent.call("student-01", ARGS))
    assert sorted((o["status"], o["already"]) for o in outs) == [("ok", False), ("ok", True)]
    assert files_of(root, 1) == ["proverka.md"] and role.agent_stats.count_1h("saved") == 1


@pytest.mark.anyio
async def test_unexpected_failure_is_error_answer(tmp_path, monkeypatch):
    role, root = direct(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("proverka 20FAKE_001_PV")        # текст исключения — не в ответ и не в событие

    monkeypatch.setattr(save_agent_module, "check_draft", boom)
    out = await role.save_agent.call("student-01", ARGS)
    assert (out["status"], out["message"]) == ("error", SAVE_MESSAGES["error"])
    ev = _events(role.settings.DB_PATH)(tool="save_agent")[-1]
    assert (ev["outcome"], ev["summary"]) == ("error", "error: RuntimeError")


# --- сверх брифа: роль ---

def test_role_builds_default_store_without_io(tmp_path):
    role, _ = make_role(replace(SETTINGS, AGENTS_ROOT=str(tmp_path / "agents")), db_dir=tmp_path)
    assert isinstance(role.agents, AgentStore) and isinstance(role.save_agent, SaveAgent)
    assert (role.agents.db_path, str(role.agents.root)) == (role.settings.DB_PATH, str(tmp_path / "agents"))
    assert not (tmp_path / "core.db").exists() and not (tmp_path / "agents").exists()     # F4


def test_agents_open_failure_keeps_data_health(tmp_path):                    # П13: не config_error
    root = make_agents_root(tmp_path / "agents")
    settings = replace(SETTINGS, DB_PATH=str(tmp_path / "core.db"), AGENTS_ROOT=str(root))
    broken = AgentStore(str(tmp_path / "no-such-dir" / "core.db"), root)
    role = DataRole(settings, FakeHistorian(), WHITELIST, monotonic=FakeMono(1000.0), tokens=TOKENS, agents=broken)
    assert role.agents is broken
    with serve_role(role) as url:
        code, body = wait_http(url + "/healthz/data")
    assert code == 200 and role.config_error is None
    assert role.agent_stats.store_error is not None and "no-such-dir" not in role.agent_stats.store_error


@pytest.mark.anyio
async def test_mcp_call_outside_http_request_is_refused_without_event(tmp_path):
    role, root = direct(tmp_path)
    _, sc = await role.mcp.call_tool("save_agent", ARGS)
    assert (sc["status"], sc["message"]) == ("refused", "нужен токен") and set(sc) == OTHER_KEYS
    assert _events(role.settings.DB_PATH)(tool="save_agent") == [] and files_of(root, 1) == []
