"""report_event службы данных (Д13): агент записывает находку (отклонение от задания или заметку) в agent_findings.
Порядок проверок — как у save_agent: настройка → частота → только студент → поля (agent, kind, title, tags,
period, value/setpoint, text)."""
import json
import math
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from helpers import AGENT_DRAFT, AGENT_NOW, SETTINGS, agent_catalog, agent_text, make_agents_root, make_role
from pcbk_core.agents.findings import FindingsError
from pcbk_core.agents.store import StoreError
from pcbk_core.data import DataRole
from pcbk_core.data.report_event import REPORT_LIMIT, REPORT_MESSAGES

ARGS = {"agent": "sushka-bdm2", "kind": "deviation", "title": "Отклонение за смену", "tags": ["20FAKE_001_PV"],
       "period_start": "2026-10-07T06:00:00+00:00", "period_end": "2026-10-07T06:30:00+00:00", "value": 85.0,
       "setpoint": 90.0, "text": "Измерение выше задания на всём интервале."}
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
    """Роль без сервера, с живым агентом «sushka-bdm2» места 1 (ARGS["agent"]) — как в lifespan."""
    root = make_agents_root(tmp_path / "agents")
    role, _ = make_role(replace(SETTINGS, AGENTS_ROOT=str(root)), db_dir=tmp_path, **kw)
    role.catalog = agent_catalog()
    assert role.events.open(), role.events.last_error
    assert role.agents.open(), role.agents.last_error
    assert role.findings.open(), role.findings.last_error
    role.agents.save(1, AGENT_DRAFT, agent_text(AGENT_DRAFT, "student-01"), AGENT_NOW)
    return role


@pytest.mark.anyio
async def test_ok_writes_finding_and_event(tmp_path):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", ARGS)
    assert set(out) == {"tool", "status", "message", "notes", "id"}
    assert out["tool"] == "report_event" and out["status"] == "ok" and isinstance(out["id"], int)
    rows = role.findings.recent(1)
    assert len(rows) == 1
    row = rows[0]
    assert (row.id, row.agent, row.kind, row.title, row.tags, row.period_start, row.period_end, row.value,
           row.setpoint, row.text) == (out["id"], "sushka-bdm2", "deviation", "Отклонение за смену",
           ("20FAKE_001_PV",), ARGS["period_start"], ARGS["period_end"], 85.0, 90.0, ARGS["text"])
    ev = _events(role.settings.DB_PATH)(tool="report_event")[-1]
    assert (ev["caller"], ev["agent"], ev["outcome"], ev["tags"]) == ("student-01", "sushka-bdm2", "ok",
                                                                      ["20FAKE_001_PV"])
    assert "Отклонение" not in json.dumps(ev) and ARGS["text"] not in json.dumps(ev)   # текст — не в журнал


@pytest.mark.anyio
async def test_value_and_setpoint_null_is_ok(tmp_path):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "kind": "note", "value": None, "setpoint": None,
                                                       "period_start": None, "period_end": None})
    assert out["status"] == "ok"
    row = role.findings.recent(1)[0]
    assert (row.kind, row.value, row.setpoint, row.period_start, row.period_end) == ("note", None, None, None, None)


@pytest.mark.anyio
async def test_agent_unknown(tmp_path):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "agent": "net-takogo"})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["agent_unknown"])
    assert role.findings.recent(1) == []


@pytest.mark.anyio
async def test_agent_of_other_place_is_unknown(tmp_path):
    role = direct(tmp_path)                                               # sushka-bdm2 живёт на месте 1
    out = await role.report_event.call("student-02", ARGS)
    assert out["status"] == "refused" and out["message"] == REPORT_MESSAGES["agent_unknown"]


@pytest.mark.anyio
async def test_agent_archived_is_unknown(tmp_path):
    role = direct(tmp_path)
    role.agents.archive(1, "sushka-bdm2", AGENT_NOW)
    out = await role.report_event.call("student-01", ARGS)
    assert out["status"] == "refused" and out["message"] == REPORT_MESSAGES["agent_unknown"]


@pytest.mark.parametrize("kind", ["warning", "DEVIATION", "", None, 1])
@pytest.mark.anyio
async def test_kind_must_be_deviation_or_note(tmp_path, kind):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "kind": kind})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["kind"])


@pytest.mark.anyio
async def test_kind_note_is_accepted(tmp_path):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "kind": "note"})
    assert out["status"] == "ok"


@pytest.mark.anyio
async def test_title_and_text_reject_lone_surrogates(tmp_path):            # как у полей черновика (round 1, m5)
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "title": "Отклонение\ud800"})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["title"])
    out = await role.report_event.call("student-01", {**ARGS, "text": ARGS["text"] + "\udfff"})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["text"])


@pytest.mark.parametrize("title", ["ab", "x" * 121, "две\nстроки", 5])
@pytest.mark.anyio
async def test_title_one_line_within_limits(tmp_path, title):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "title": title})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["title"])


@pytest.mark.parametrize("tags", [[], ["a", "b", "c", "d", "e"], "20FAKE_001_PV", [1]])
@pytest.mark.anyio
async def test_tags_count_and_type(tmp_path, tags):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "tags": tags})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["tags_count"])


@pytest.mark.parametrize("tag", ["20FAKE_002_PV", "net-takogo-tega"])        # вне списка / неизвестный
@pytest.mark.anyio
async def test_foreign_tag_is_refused(tmp_path, tag):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "tags": [tag]})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["tag_unknown"])


@pytest.mark.anyio
async def test_tags_catalog_not_loaded_is_unavailable(tmp_path):
    role = direct(tmp_path)
    from pcbk_core.data.catalog import Catalog
    role.catalog = Catalog.empty()
    out = await role.report_event.call("student-01", ARGS)
    assert out["status"] == "unavailable"


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, "85", True])
@pytest.mark.anyio
async def test_value_must_be_finite_number(tmp_path, value):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "value": value})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["number"])


@pytest.mark.parametrize("period", ["2026-10-07T06:00:00", "не дата", 5])      # без пояса / не дата / не строка
@pytest.mark.anyio
async def test_period_needs_timezone(tmp_path, period):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "period_start": period})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["period"])


@pytest.mark.parametrize("text", ["коротко", "x" * 601, "две\nстроки", 5])
@pytest.mark.anyio
async def test_text_one_line_within_limits(tmp_path, text):
    role = direct(tmp_path)
    out = await role.report_event.call("student-01", {**ARGS, "text": text})
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["text"])


@pytest.mark.anyio
async def test_not_student_is_refused(tmp_path):
    role = direct(tmp_path)
    out = await role.report_event.call("ops", ARGS)
    assert (out["status"], out["message"]) == ("refused", REPORT_MESSAGES["not_student"])


@pytest.mark.anyio
async def test_config_error_is_unavailable_first(tmp_path):
    role = direct(tmp_path, config_error="белый список не читается")
    out = await role.report_event.call("ops", ARGS)                      # настройка — раньше «только студент»
    assert (out["status"], out["message"]) == ("unavailable", "белый список не читается")


@pytest.mark.anyio
async def test_rate_limit_counts_all_calls(tmp_path):
    role = direct(tmp_path)
    for _ in range(REPORT_LIMIT):
        assert (await role.report_event.call("student-01", {**ARGS, "agent": "net-takogo"}))["status"] == "refused"
    out = await role.report_event.call("student-01", ARGS)
    assert out["status"] == "rate" and role.findings.recent(1) == []
    assert (await role.report_event.call("student-02", ARGS))["status"] != "rate"   # окно — на вызывающего


@pytest.mark.anyio
async def test_store_error_is_unavailable(tmp_path, monkeypatch):
    role = direct(tmp_path)

    def broken(*a, **k):
        raise FindingsError("находки агентов: OperationalError SQLITE_BUSY")

    monkeypatch.setattr(role.findings, "add", broken)
    out = await role.report_event.call("student-01", ARGS)
    assert (out["status"], out["message"]) == ("unavailable", REPORT_MESSAGES["store"])


@pytest.mark.anyio
async def test_agent_store_error_is_unavailable(tmp_path, monkeypatch):
    role = direct(tmp_path)

    def broken(place):
        raise StoreError("учёт агентов: OperationalError SQLITE_BUSY")

    monkeypatch.setattr(role.agents, "list_place", broken)
    out = await role.report_event.call("student-01", ARGS)
    assert (out["status"], out["message"]) == ("unavailable", REPORT_MESSAGES["store"])


@pytest.mark.anyio
async def test_mcp_call_outside_http_request_is_refused_without_event(tmp_path):
    role = direct(tmp_path)
    _, sc = await role.mcp.call_tool("report_event", ARGS)
    assert sc["status"] == "refused" and sc["message"] == "нужен токен"
    assert _events(role.settings.DB_PATH)(tool="report_event") == []
