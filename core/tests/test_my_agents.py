"""my_agents службы данных (Д13): живые агенты места вызывающего с инструкцией — для Конструктора («правка
агента»). Вызывающий — только student-01…10; без quarantined и archived; событие без текста инструкций."""
import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from helpers import AGENT_DRAFT, AGENT_NOW, SETTINGS, agent_catalog, agent_text, make_agents_root, make_role
from pcbk_core.agents.store import AgentMissing, StoreError
from pcbk_core.data import DataRole

JSON_COLUMNS = ("requested", "tags", "sent", "returned")


def _events(db_path: str):
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


def direct(tmp_path, **kw) -> DataRole:
    """Роль без сервера: каталог, журнал событий и учёт агентов открыты, как в lifespan."""
    root = make_agents_root(tmp_path / "agents")
    role, _ = make_role(replace(SETTINGS, AGENTS_ROOT=str(root)), db_dir=tmp_path, **kw)
    role.catalog = agent_catalog()
    assert role.events.open(), role.events.last_error
    assert role.agents.open(), role.agents.last_error
    return role


@pytest.mark.anyio
async def test_lists_live_oldest_first_with_prompt_and_excludes_quarantined_archived(tmp_path):
    role = direct(tmp_path)
    old = AGENT_DRAFT
    new = replace(AGENT_DRAFT, name="davlenie", title="Давление", node="Узел 2", purpose="Назначение 2",
                  tools=("tag_now",))
    gone = replace(AGENT_DRAFT, name="arhivnyi", title="Архивный")
    stuck = replace(AGENT_DRAFT, name="karantin", title="Карантинный")
    for draft in (old, new, gone, stuck):
        role.agents.save(1, draft, agent_text(draft, "student-01"), AGENT_NOW)
    role.agents.archive(1, gone.name, AGENT_NOW)
    role.agents.quarantine(1, f"{stuck.name}.md", AGENT_NOW)

    out = await role.my_agents.call("student-01")
    assert out["status"] == "ok"
    names = [a["name"] for a in out["agents"]]
    assert names == [old.name, new.name]                                         # старые первыми, без карантина/архива
    first = out["agents"][0]
    assert (first["title"], first["node"], first["purpose"], first["tools"], first["tags"]) == (
        old.title, old.node, old.purpose, list(old.tools), list(old.tags))
    assert first["prompt"] == old.prompt                                          # ровно инструкция, без шаблона
    assert "pcbk-reserve" not in first["prompt"] and "Правила стенда" not in first["prompt"]
    second = out["agents"][1]
    assert second["prompt"] == new.prompt and second["tools"] == ["tag_now"]


@pytest.mark.anyio
async def test_not_student_is_refused(tmp_path):
    role = direct(tmp_path)
    out = await role.my_agents.call("ops")
    assert out["status"] == "refused" and out["message"] == "Агентов читает только рабочее место студента"
    assert "agents" not in out


@pytest.mark.anyio
async def test_config_error_is_unavailable(tmp_path):
    role = direct(tmp_path, config_error="белый список не читается")
    out = await role.my_agents.call("student-01")
    assert (out["status"], out["message"]) == ("unavailable", "белый список не читается")


@pytest.mark.anyio
async def test_store_error_is_unavailable(tmp_path, monkeypatch):
    role = direct(tmp_path)

    def broken(place):
        raise StoreError("учёт агентов: OperationalError SQLITE_BUSY")

    monkeypatch.setattr(role.agents, "cards", broken)
    out = await role.my_agents.call("student-01")
    assert (out["status"], out["message"]) == ("unavailable", "Учёт агентов недоступен — сообщите преподавателю")


@pytest.mark.anyio
async def test_unreadable_file_falls_back_to_empty_prompt(tmp_path, monkeypatch):
    role = direct(tmp_path)
    role.agents.save(1, AGENT_DRAFT, agent_text(AGENT_DRAFT, "student-01"), AGENT_NOW)

    def odd(place, name):
        raise AgentMissing(odd=True)

    monkeypatch.setattr(role.agents, "read_file", odd)
    out = await role.my_agents.call("student-01")
    assert out["status"] == "ok" and out["agents"][0]["prompt"] == ""


@pytest.mark.anyio
async def test_event_has_no_prompt_text_or_name(tmp_path):
    role = direct(tmp_path)
    role.agents.save(1, AGENT_DRAFT, agent_text(AGENT_DRAFT, "student-01"), AGENT_NOW)
    await role.my_agents.call("student-01")
    ev = _events(role.settings.DB_PATH)(tool="my_agents")[-1]
    assert (ev["caller"], ev["outcome"], ev["agent"]) == ("student-01", "ok", None)
    blob = json.dumps(ev)
    assert AGENT_DRAFT.name not in blob and AGENT_DRAFT.prompt not in blob and "Сушильная" not in blob


@pytest.mark.anyio
async def test_mcp_call_outside_http_request_is_refused_without_event(tmp_path):
    role = direct(tmp_path)
    _, sc = await role.mcp.call_tool("my_agents", {})
    assert sc["status"] == "refused" and sc["message"] == "нужен токен"
    assert _events(role.settings.DB_PATH)(tool="my_agents") == []
