"""save_agent службы данных (Д7-R57): проверка черновика, запись по шаблону в каталог места вызывающего, событие и
счётчики. Отказ — обычный результат, не isError; место — только из токена.

Фикстура served — роль с каталогом agent_catalog() под serve_role (каталог закреплён: загрузка двойника историана его
не заменит), корень агентов tmp_path/agents со student-01…11; served_no_catalog — то же, но каталог не грузится.
"""
import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace

import anyio
import pytest

import pcbk_core.data as data_module
from fakes import WHITELIST, FakeHistorian
from helpers import (SETTINGS, TOKEN, FakeMono, agent_catalog, make_agents_root, make_role, mcp_session, non_root,
                     serve_role, sha, wait_http, wait_until, wait_until_async)
from pcbk_core.agents.draft import AGENT_TOOLS
from pcbk_core.agents.store import MAX_AGENTS, AgentStore, DirUnavailable, StoreError
from pcbk_core.data import DataRole
from pcbk_core.data import save_agent as save_agent_module
from pcbk_core.data.catalog import Catalog
from pcbk_core.data.historian import HistorianError
from pcbk_core.data.save_agent import SAVE_LIMIT, SAVE_MESSAGES, SAVE_PROPERTIES, SaveAgent, refused_message
from pcbk_core.data.service import MESSAGES
from pcbk_core.places import PLACES
from pcbk_core.secrets import TokenTable

# тестовые токены — не 48 шестнадцатеричных знаков (шаблон проверки на секреты)
TOKEN3 = "test-token-student-03"
TOKEN12 = "test-token-student-12"                                        # Д15б: токен есть, места нет (12 — вне PLACES)
OPS = "test-token-ops"
TOKENS = TokenTable({"student-01": sha(TOKEN), "student-03": sha(TOKEN3), "student-12": sha(TOKEN12),
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
    record = next(r for r in caplog.records if "tool=save_agent" in r.getMessage())
    line = record.getMessage()
    assert "proverka" not in line and "Проверка" not in line and "outcome=ok" in line
    assert "20FAKE" not in line and "помощник" not in line and "student-03/" not in line
    # доводка Д7а (А4, m1): строка целиком — формат Д7-R57 без лишних частей, уровень INFO
    assert re.fullmatch(r"tool=save_agent caller=student-03 channel=mcp outcome=ok codes=saved ms=\d+", line), line
    assert record.levelno == logging.INFO


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
async def test_place_outside_1_11_cannot_save(served):                       # student-12: токен есть, места нет
    _, out = await save(served.url, TOKEN12)
    assert (out["status"], out["message"], out["problems"]) == ("refused", SAVE_MESSAGES["not_student"], [])
    ev = served.events(tool="save_agent")[-1]
    assert (ev["caller"], ev["outcome"], ev["summary"]) == ("student-12", "refused", "refused: not_student")
    assert served.role.agent_stats.count_1h("refused") == 1
    assert all(not any((served.root / f"student-{n:02d}").iterdir()) for n in PLACES)


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
    # итоговое ревью Д7а, I-2: error считается как unavailable — строка сторожа не ok при сплошных сбоях
    assert (role.agent_stats.count_1h("unavailable"), role.agent_stats.count_1h("saved"),
            role.agent_stats.count_1h("refused")) == (1, 0, 0)


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
    assert role.agent_stats.count_1h("unavailable") == 1                      # I-2


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


# --- итоговая доводка Д7а ---

def _store_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("учёт агентов: запись")]


def test_store_flag_clears_without_restart(tmp_path, monkeypatch, caplog):  # I-1: один SQLITE_BUSY — не до перезапуска
    monkeypatch.setattr(data_module, "EVENTS_CHECK_S", 0.05)
    caplog.set_level(logging.INFO, logger="pcbk_core.data")
    role, root = _role(tmp_path, FakeHistorian(fail=HistorianError("query", "каталог не читается")))

    def busy(*a, **k):
        raise StoreError("учёт агентов: OperationalError SQLITE_BUSY")

    with serve_role(role, wait_catalog=False) as url:
        with monkeypatch.context() as m:
            m.setattr(role.agents, "save", busy)
            _, out = asyncio.run(save(url, TOKEN, {**ARGS, "tags": []}))
        assert (out["status"], out["message"]) == ("unavailable", SAVE_MESSAGES["store"])
        # больше ни одного вызова save_agent: флаг снимает фоновая проверка учёта за период
        wait_until(lambda: role.agent_stats.store_error is None, 5)
        time.sleep(0.3)                                                     # ещё несколько проверок — без строк
    assert _store_lines(caplog) == ["учёт агентов: запись снова проходит"]
    assert files_of(root, 1) == []


def test_periodic_check_flags_broken_store(tmp_path, monkeypatch, caplog):  # I-1: проверка ставит и снимает флаг
    monkeypatch.setattr(data_module, "EVENTS_CHECK_S", 0.05)
    caplog.set_level(logging.INFO, logger="pcbk_core.data")
    role, _ = _role(tmp_path, FakeHistorian(fail=HistorianError("query", "каталог не читается")))

    def broken() -> bool:
        role.agents.last_error = "OperationalError"
        return False

    with serve_role(role, wait_catalog=False):
        assert role.agent_stats.store_error is None                         # lifespan открыл учёт — флага нет
        with monkeypatch.context() as m:
            m.setattr(role.agents, "open", broken)
            wait_until(lambda: role.agent_stats.store_error == "OperationalError", 5)
            time.sleep(0.3)
        wait_until(lambda: role.agent_stats.store_error is None, 5)
        time.sleep(0.3)
    assert _store_lines(caplog) == ["учёт агентов: запись не проходит (OperationalError)",
                                    "учёт агентов: запись снова проходит"]          # только смена


@pytest.mark.anyio
async def test_store_answer_clears_flag(tmp_path, monkeypatch):            # I-1: учёт ответил — флаг снят
    role, root = direct(tmp_path)
    stats = role.agent_stats
    assert (await role.save_agent.call("student-01", ARGS))["status"] == "ok"
    stats.store_failed("учёт агентов: OperationalError SQLITE_BUSY")
    out = await role.save_agent.call("student-01", ARGS)
    assert (out["status"], out["already"], stats.store_error) == ("ok", True, None)        # already — только чтение
    stats.store_failed("учёт агентов: OperationalError SQLITE_BUSY")
    out = await role.save_agent.call("student-01", {**ARGS, "title": "Другое"})
    assert (out["problems"][0]["code"], stats.store_error) == ("name_taken", None)
    stats.store_failed("учёт агентов: OperationalError SQLITE_BUSY")
    with monkeypatch.context() as m:                                         # каталог места не читается — не учёт
        m.setattr(role.agents, "_scan", lambda place: (_ for _ in ()).throw(DirUnavailable("каталог места")))
        out = await role.save_agent.call("student-01", {**ARGS, "name": "drugoy"})
    assert (out["status"], out["message"], stats.store_error) == ("unavailable", SAVE_MESSAGES["dir"], None)


@pytest.mark.anyio
async def test_save_does_not_block_event_loop(tmp_path):                   # А4 m3: запись — в потоке
    role, root = direct(tmp_path)
    started, release, waited = threading.Event(), threading.Event(), []
    write_new = role.agents._write_new

    def slow(*a, **k):
        started.set()
        waited.append(release.wait(2))              # release ставит корутина теста: цикл событий стоит — не дождаться
        return write_new(*a, **k)

    role.agents._write_new = slow
    task = asyncio.create_task(role.save_agent.call("student-01", ARGS))
    await wait_until_async(started.is_set)
    release.set()
    assert (await task)["status"] == "ok" and waited == [True]


def _slow_first_write(role) -> tuple[threading.Event, threading.Event]:
    """Первая запись файла агента ждёт release (до 5 с), последующие — сразу; started — первая запись началась."""
    started, release, calls = threading.Event(), threading.Event(), []
    write_new = role.agents._write_new

    def slow(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            release.wait(5)
        return write_new(*a, **k)

    role.agents._write_new = slow
    return started, release


def _tool_lines(caplog) -> list[str]:
    return [r.getMessage().split(" ms=")[0] for r in caplog.records if r.getMessage().startswith("tool=save_agent")]


@pytest.mark.anyio
async def test_cancel_mid_write_keeps_lock_and_event(tmp_path, caplog):   # m3: отмена посреди записи
    caplog.set_level(logging.INFO, logger="pcbk_core.data")
    role, root = direct(tmp_path)
    started, release = _slow_first_write(role)
    task = asyncio.create_task(role.save_agent.call("student-01", ARGS))
    await wait_until_async(started.is_set)
    task.cancel(); await asyncio.sleep(0.05)
    second = asyncio.create_task(role.save_agent.call("student-01", ARGS))
    await asyncio.sleep(0.2)
    assert not task.done() and not second.done()        # поток пишет — замок места держится, второй вызов ждёт
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    out = await second
    assert (out["status"], out["already"]) == ("ok", True) and files_of(root, 1) == ["proverka.md"]
    ev = _events(role.settings.DB_PATH)(tool="save_agent")
    assert sorted((e["outcome"], e["summary"]) for e in ev) == [("ok", "already"), ("ok", "saved")]
    assert role.agent_stats.count_1h("saved") == 1 and role.agent_stats.count_1h("unavailable") == 0
    assert sorted(_tool_lines(caplog)) == ["tool=save_agent caller=student-01 channel=mcp outcome=ok codes=already",
                                           "tool=save_agent caller=student-01 channel=mcp outcome=ok codes=saved"]


@pytest.mark.anyio
async def test_cancel_scope_mid_write_still_finishes(tmp_path, caplog):    # m3: отмена anyio, как у SDK MCP
    caplog.set_level(logging.INFO, logger="pcbk_core.data")
    role, root = direct(tmp_path)
    started, release = _slow_first_write(role)
    scopes, caught = [], []

    async def run():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await role.save_agent.call("student-01", ARGS)
        caught.append(scope.cancelled_caught)

    async with anyio.create_task_group() as tg:
        tg.start_soon(run)
        await wait_until_async(started.is_set)
        scopes[0].cancel()                              # anyio повторяет отмену на каждом ожидании задачи
        await anyio.sleep(0.05)
        release.set()
    assert caught == [True] and files_of(root, 1) == ["proverka.md"]
    ev = _events(role.settings.DB_PATH)(tool="save_agent")
    assert [(e["outcome"], e["summary"]) for e in ev] == [("ok", "saved")]
    assert _tool_lines(caplog) == ["tool=save_agent caller=student-01 channel=mcp outcome=ok codes=saved"]


@pytest.mark.anyio
async def test_cancel_before_write_is_error_event(tmp_path, caplog):      # m3: отменён, пока ждал замок места
    caplog.set_level(logging.INFO, logger="pcbk_core.data")
    role, root = direct(tmp_path)
    started, release = _slow_first_write(role)
    first = asyncio.create_task(role.save_agent.call("student-01", ARGS))
    await wait_until_async(started.is_set)
    waiting = asyncio.create_task(role.save_agent.call("student-01", {**ARGS, "name": "drugoy"}))
    await asyncio.sleep(0.05)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    release.set()
    assert (await first)["status"] == "ok" and files_of(root, 1) == ["proverka.md"]
    ev = _events(role.settings.DB_PATH)(tool="save_agent")
    assert sorted((e["agent"], e["outcome"], e["summary"]) for e in ev) == [
        ("drugoy", "error", "error: cancelled"), ("proverka", "ok", "saved")]
    assert role.agent_stats.count_1h("unavailable") == 0               # отмена клиентом — не сбой службы
    line = next(r for r in caplog.records if "codes=cancelled" in r.getMessage())
    assert line.levelno == logging.WARNING


@pytest.mark.anyio
async def test_check_sees_save_failure_during_probe(tmp_path, monkeypatch, caplog):  # I-1: «было» — после потока
    caplog.set_level(logging.INFO, logger="pcbk_core.data")
    role, _ = direct(tmp_path)
    real = role.agents.open

    def open_while_save_fails() -> bool:
        ok = real()
        role.agent_stats.store_failed("учёт агентов: OperationalError SQLITE_BUSY")   # save_agent упал за время проверки
        return ok

    monkeypatch.setattr(role.agents, "open", open_while_save_fails)
    await role.check_agents()
    assert role.agent_stats.store_error is None
    assert _store_lines(caplog) == ["учёт агентов: запись снова проходит"]          # смена не потерялась


@non_root
@pytest.mark.anyio
async def test_check_sees_readonly_store(tmp_path, caplog):                # I-1: open() на файле только для чтения проходит
    caplog.set_level(logging.INFO, logger="pcbk_core.data")
    role, _ = direct(tmp_path)
    db = tmp_path / "core.db"
    db.chmod(0o444)
    try:
        assert role.agents.open()                                           # одного open() мало — нужна проба записи
        await role.check_agents()
        assert role.agent_stats.store_error == "OperationalError SQLITE_READONLY"
    finally:                                    # -wal и -shm, созданные при 0444, — тоже 0444: SQLite берёт права файла базы
        for f in tmp_path.glob("core.db*"):
            f.chmod(0o644)
    await role.check_agents()
    assert role.agent_stats.store_error is None
    assert _store_lines(caplog) == ["учёт агентов: запись не проходит (OperationalError SQLITE_READONLY)",
                                    "учёт агентов: запись снова проходит"]


def test_save_schema_lists_d9_tools():                               # Д9-R89/Д13/Д19/Д21: схема — из AGENT_TOOLS
    assert SAVE_PROPERTIES["tools"]["items"]["enum"] == list(AGENT_TOOLS)
    assert SAVE_PROPERTIES["tools"]["maxItems"] == 9                          # Д21: плюс calc_spc
