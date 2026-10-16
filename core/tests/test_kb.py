"""Поправки технологов к базе знаний (Д25): хранилище kb_corrections, MCP /mcp-kb с токеном места, отзыв только своей
поправки, команда оператора. Решение владельца 16.10: поправка сразу видна всем, без подтверждения."""
import json
import sqlite3
import threading
from datetime import datetime, timezone

import pytest
from helpers import TOKEN, mcp_session, serve_app, sha
from pcbk_core.app import create_app
from pcbk_core.kb import KbRole
from pcbk_core.kb.__main__ import main as kb_cli
from pcbk_core.kb.mcp_server import KB_MCP_PATH, NOTICE, TOOL_DESCRIPTIONS
from pcbk_core.kb.store import (ACTIVE_PER_CALLER, LIST_MAX, STATEMENT_MIN, CorrectionRefused, CorrectionStore,
                                clean)
from pcbk_core.secrets import TokenTable
from pcbk_core.settings import Settings

NOW = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
TOKEN_02 = "test-token-student-02"
TOKENS = TokenTable({"student-01": sha(TOKEN), "student-02": sha(TOKEN_02)})
STMT = "На КДМ-1 массу 1 м² приводят к абсолютно сухому веществу"


def store(tmp_path) -> CorrectionStore:
    s = CorrectionStore(tmp_path / "core.db")
    assert s.open() and s.open()                                                 # идемпотентна
    return s


# --- хранилище ---

def test_add_then_visible_to_everyone_newest_first(tmp_path):
    s = store(tmp_path)
    a = s.add("student-01", "v1:claim:DEF-basis-weight", STMT, "в базе не сказано", "методика лаборатории", NOW)
    b = s.add("student-02", "v1:term:moisture", "Влажность после сушки на КДМ-1 держат 6–8 %", None, None, NOW)
    found, total = s.active()
    assert [c.id for c in found] == [b.id, a.id] and total == 2
    assert (a.author, b.author, a.basis, b.wrong) == ("место 01", "место 02", "методика лаборатории", None)
    assert a.ts == "2026-10-16T01:00:00+00:00"


def test_query_matches_words_without_case_in_record_wrong_statement(tmp_path):
    s = store(tmp_path)
    s.add("student-01", "v1:claim:DEF-basis-weight", STMT, None, None, NOW)
    s.add("student-01", "v1:term:moisture", "Влажность после сушки держат в пределах 6–8 %", None, None, NOW)
    assert [c.record for c in s.active("АБСОЛЮТНО сухому")[0]] == ["v1:claim:DEF-basis-weight"]
    assert [c.record for c in s.active("DEF-basis-weight")[0]] == ["v1:claim:DEF-basis-weight"]
    assert s.active("влажность сушки")[1] == 1 and s.active("нет такого")[1] == 0
    assert s.active("  ")[1] == 2                                                # пустой запрос — все


@pytest.mark.parametrize("kwargs,reason", [
    ({"record": None}, "record: обязательно"),
    ({"record": "  "}, "record: обязательно"),
    ({"record": 5}, "record: строка"),
    ({"record": "x" * 161}, "record: не длиннее 160 знаков"),
    ({"statement": None}, "statement: обязательно"),
    ({"statement": "коротко"}, f"statement: не короче {STATEMENT_MIN} знаков"),
    ({"statement": "x" * 1001}, "statement: не длиннее 1000 знаков"),
    ({"wrong": "x" * 601}, "wrong: не длиннее 600 знаков"),
    ({"basis": ["x"]}, "basis: строка"),
])
def test_add_refuses_bad_input_without_writing(tmp_path, kwargs, reason):
    s = store(tmp_path)
    args = {"record": "v1:claim:X", "statement": STMT, "wrong": None, "basis": None, **kwargs}
    with pytest.raises(CorrectionRefused) as e:
        s.add("student-01", args["record"], args["statement"], args["wrong"], args["basis"], NOW)
    assert str(e.value) == reason and s.active()[1] == 0


def test_clean_collapses_control_chars_and_escapes_lone_surrogate():
    assert clean("  а\nб\t\x00в г  ", "f", 50, required=True) == "а б в г"
    assert clean("x\ud800y", "f", 50, required=True) == "x\\ud800y"
    assert clean("", "f", 50, required=False) is None and clean(None, "f", 50, required=False) is None


def test_active_limit_per_place_and_list_cap(tmp_path, monkeypatch):
    s = store(tmp_path)
    for i in range(ACTIVE_PER_CALLER):
        s.add("student-01", f"v1:claim:X{i}", STMT, None, None, NOW)
    with pytest.raises(CorrectionRefused, match="уже 30 действующих"):
        s.add("student-01", "v1:claim:Y", STMT, None, None, NOW)
    s.add("student-02", "v1:claim:Y", STMT, None, None, NOW)                      # у другого места — можно
    monkeypatch.setattr("pcbk_core.kb.store.LIST_MAX", 5)
    found, total = s.active()
    assert (len(found), total) == (5, ACTIVE_PER_CALLER + 1) and LIST_MAX == 50


def test_withdraw_own_only_operator_any_and_row_kept(tmp_path):
    s = store(tmp_path)
    a = s.add("student-01", "v1:claim:X", STMT, None, None, NOW)
    with pytest.raises(CorrectionRefused, match="внесло другое место"):
        s.withdraw(a.id, "student-02", NOW)
    with pytest.raises(CorrectionRefused, match="поправки №99 нет"):
        s.withdraw(99, "student-01", NOW)
    with pytest.raises(CorrectionRefused, match="целое число"):
        s.withdraw("1", "student-01", NOW)
    w = s.withdraw(a.id, "student-01", NOW)
    assert (w.withdrawn_by, s.active()[1]) == ("student-01", 0)
    with pytest.raises(CorrectionRefused, match="уже отозвана"):
        s.withdraw(a.id, "student-01", NOW)
    b = s.add("student-02", "v1:claim:Y", STMT, None, None, NOW)
    assert s.withdraw(b.id, "operator", NOW, operator=True).withdrawn_by == "operator"
    assert [c.withdrawn_ts is not None for c in s.all()] == [True, True]          # строки остаются


def test_store_error_is_class_and_code_only(tmp_path):
    s = CorrectionStore(tmp_path / "нет" / "core.db")
    assert s.open() is False and s.last_error == "OperationalError"
    with pytest.raises(Exception) as e:
        s.active()
    assert str(e.value).startswith("поправки базы знаний: OperationalError")


def test_concurrent_adds_keep_per_place_limit(tmp_path):
    s = store(tmp_path)
    errors: list[str] = []

    def add(i: int) -> None:
        try:
            s.add("student-01", f"v1:claim:X{i}", STMT, None, None, NOW)
        except CorrectionRefused as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(ACTIVE_PER_CALLER + 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert s.active()[1] == ACTIVE_PER_CALLER and len(errors) == 5                # предел — в той же транзакции


# --- MCP /mcp-kb ---

@pytest.fixture
def kb_server(tmp_path):
    role = KbRole(str(tmp_path / "core.db"), TOKENS, wallclock=lambda: NOW)
    with serve_app(create_app(Settings(DB_PATH=str(tmp_path / "core.db")), [role])) as url:
        yield url + KB_MCP_PATH, role


def _result(r) -> dict:
    return json.loads(r.content[0].text)


@pytest.mark.anyio
async def test_mcp_tools_schema_and_round_trip(kb_server):
    url, role = kb_server
    async with mcp_session(url, TOKEN) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert set(tools) == {"corrections", "correction_add", "correction_withdraw"}
        assert tools["corrections"].annotations.readOnlyHint is True
        assert tools["correction_add"].inputSchema["required"] == ["record", "statement"]
        assert tools["correction_withdraw"].inputSchema["required"] == ["id"]
        assert tools["correction_add"].description == TOOL_DESCRIPTIONS["correction_add"]
        added = _result(await s.call_tool("correction_add", {"record": "v1:claim:DEF-basis-weight",
                                                             "statement": STMT, "basis": "методика"}))
    assert (added["status"], added["id"]) == ("ok", 1) and "видна всем студентам" in added["message"]
    async with mcp_session(url, TOKEN_02) as s:                                    # другое место видит сразу
        seen = _result(await s.call_tool("corrections", {"query": "basis-weight"}))
        refused = _result(await s.call_tool("correction_withdraw", {"id": 1}))
    assert (seen["status"], seen["total"], seen["notice"]) == ("ok", 1, NOTICE)
    assert seen["corrections"] == [{"id": 1, "record": "v1:claim:DEF-basis-weight", "wrong": None, "statement": STMT,
                                    "basis": "методика", "author": "место 01", "date": "2026-10-16"}]
    assert refused["status"] == "refused" and "другое место" in refused["message"]
    async with mcp_session(url, TOKEN) as s:
        done = _result(await s.call_tool("correction_withdraw", {"id": 1}))
        empty = _result(await s.call_tool("corrections", {}))
    assert done["status"] == "ok" and (empty["total"], empty["corrections"]) == (0, [])


@pytest.mark.anyio
async def test_mcp_bad_input_is_russian_refusal(kb_server):
    url, _ = kb_server
    async with mcp_session(url, TOKEN) as s:
        r = _result(await s.call_tool("correction_add", {"record": "v1:claim:X", "statement": "мало"}))
        w = _result(await s.call_tool("correction_withdraw", {"id": "один"}))
    assert (r["status"], r["message"]) == ("refused", f"statement: не короче {STATEMENT_MIN} знаков")
    assert w["status"] == "refused" and "целое число" in w["message"]


def test_mcp_kb_needs_token_and_post(kb_server):
    import urllib.error
    import urllib.request
    url, _ = kb_server
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def code(method: str, token: str | None) -> int:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, b"{}" if method == "POST" else None, method=method, headers=headers)
        try:
            with opener.open(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            with e:
                return e.code

    assert (code("POST", None), code("POST", "wrong-token"), code("GET", TOKEN)) == (401, 401, 405)


@pytest.mark.anyio
async def test_mcp_store_failure_is_unavailable_not_500(tmp_path):
    role = KbRole(str(tmp_path / "нет" / "core.db"), TOKENS)
    with serve_app(create_app(Settings(DB_PATH=str(tmp_path / "core.db")), [role])) as url:
        assert role.health()[0] is False and role.health()[1].startswith("поправки базы знаний: хранилище не открыто")
        async with mcp_session(url + KB_MCP_PATH, TOKEN) as s:
            r = _result(await s.call_tool("corrections", {}))
    assert r["status"] == "unavailable" and "недоступны" in r["message"]


# --- команда оператора ---

def test_cli_list_and_withdraw(tmp_path, capsys):
    s = store(tmp_path)
    s.add("student-03", "v1:claim:X", STMT, "неверно", None, NOW)
    db = str(tmp_path / "core.db")
    assert kb_cli(["--db", db, "list"]) == 0
    out = capsys.readouterr().out
    assert "№1" in out and "место 03" in out and "действует" in out and "всего: 1" in out
    assert kb_cli(["--db", db, "withdraw", "1"]) == 0
    assert kb_cli(["--db", db, "withdraw", "1"]) == 1 and "уже отозвана" in capsys.readouterr().err
    assert kb_cli(["--db", db, "list"]) == 0 and "всего: 0" in capsys.readouterr().out
    assert kb_cli(["--db", db, "list", "--all"]) == 0 and "operator" in capsys.readouterr().out
    row = sqlite3.connect(db).execute("SELECT withdrawn_by FROM kb_corrections").fetchone()
    assert row == ("operator",)
