"""Связь с прежним `DataService` (план КДМ1, Task 7; `docs/kdm1/INTERFACES.md`, `analytics/data_bridge.py`):
проброс caller/channel ровно как у калькуляторов страницы, без ResolvedLink — ни одного вызова читающих тег
инструментов, отказ службы данных не оборачивается, пропуски/качество/период — как есть.

Двойник — `tests/helpers.make_service`/`tests/fakes.py` (тот же `DataService` + `FakeHistorian`, что
`test_gw_calc.py` использует для калькуляторов страницы: независимый эталон значений — статические таблицы
`fakes.LIVE_ROWS`/`SUMMARY`/`SERIES`/`STATES`, не повторный вызов data_bridge самого себя). Теги — только
`20FAKE_*`/`25FAKE_*` синтетики (ворота плана: `git diff --cached | grep -nE '\\b[0-9]{2}[A-Z]{2,4}[0-9]{2}'` не
должен ничего находить)."""
from datetime import datetime

import pytest

from helpers import make_service
from pcbk_core.analytics import data_bridge
from pcbk_core.analytics.models import RequestIdentity
from pcbk_core.analytics.reviews import ResolvedLink
from pcbk_core.data.sql import SERIES_DEFAULT_POINTS

pytestmark = pytest.mark.anyio

STUDENT = RequestIdentity(login="stu-03", role="student", place=3, caller="student-03")
TEACHER = RequestIdentity(login="teach", role="teacher", place=None, caller="teacher")
_APPROVED_AT = datetime.now()
LINK = ResolvedLink(link_id="EQ-FAKE-001", process_id="PROC-FAKE-001", historian_tag="20FAKE_001_PV", unit="л/час",
                    approved_by="tech1", approved_at=_APPROVED_AT, scope="PROC-FAKE-001")
STATE_LINK = ResolvedLink(link_id="EQ-FAKE-007", process_id="PROC-FAKE-001", historian_tag="25FAKE_007_CLS",
                          unit=None, approved_by="tech1", approved_at=_APPROVED_AT, scope="PROC-FAKE-001")


@pytest.fixture
def svc(tmp_path):
    service, fake = make_service(tmp_path)
    return service, fake


def test_caller_of_mirrors_identity_caller_student_and_teacher():
    assert data_bridge.caller_of(STUDENT) == "student-03"
    assert data_bridge.caller_of(TEACHER) == "teacher"


# --- catalog_search: не требует ResolvedLink, не включает чтение тега сама по себе ---

async def test_catalog_search_forwards_caller_channel_and_returns_matches(svc):
    service, _ = svc
    result = await data_bridge.catalog_search(service, STUDENT, "FAKE", 5)
    assert result["status"] == "ok"
    assert "20FAKE_001_PV" in [m["tag"] for m in result["matches"]]
    events = service.role.events.recent()
    assert len(events) == 1
    assert (events[0].caller, events[0].channel, events[0].tool) == ("student-03", "web", "catalog_search")


async def test_catalog_search_alone_never_triggers_a_tag_read(svc):                 # I-25 INTEGRATION_CASES
    """Поиск по подписи/display_label, даже находя точное совпадение имени тега, не читает значение — ровно
    одно событие (catalog_search), не два (ни tag_now, ни что-либо ещё)."""
    service, _ = svc
    await data_bridge.catalog_search(service, STUDENT, "20FAKE_001_PV", 5)
    events = service.role.events.recent()
    assert len(events) == 1 and events[0].tool == "catalog_search"


# --- tag_now/tag_period/tag_series/calc_runtime: только через ResolvedLink ---

async def test_tag_now_forwards_single_resolved_tag(svc):
    service, _ = svc
    result = await data_bridge.tag_now(service, STUDENT, LINK)
    assert result["status"] == "ok"
    assert [i["tag"] for i in result["items"]] == ["20FAKE_001_PV"]            # ровно тег из link, не из вопроса
    assert result["items"][0]["value"] == 12.5                                # fakes.LIVE_ROWS — независимый эталон
    e = service.role.events.recent()[0]
    assert (e.caller, e.channel, e.tool, e.tags) == ("student-03", "web", "tag_now", ("20FAKE_001_PV",))


async def test_tag_now_teacher_caller(svc):
    service, _ = svc
    await data_bridge.tag_now(service, TEACHER, LINK)
    assert service.role.events.recent()[0].caller == "teacher"


async def test_tag_period_forwards_preset_and_returns_summary_as_is(svc):
    service, _ = svc
    result = await data_bridge.tag_period(service, STUDENT, LINK, "prev_shift", None, None)
    assert result["items"][0]["avg"] == 12.4                                  # fakes.SUMMARY — независимый эталон
    assert result["period"]["preset"] == "prev_shift"
    e = service.role.events.recent()[0]
    assert (e.caller, e.channel, e.tool) == ("student-03", "web", "tag_period")


async def test_tag_period_own_start_end_forwarded_verbatim(svc):
    """Свой период (start/end) доходит до DataService как есть — без переупорядочивания/подмены (ровно то же
    тело, что получил бы прямой вызов svc.tag_period с теми же аргументами, независимый эталон — защищённый,
    не переписанный этой задачей DataService)."""
    service, _ = svc
    own = {"start": "2026-10-01T04:00", "end": "2026-10-01T10:00"}
    result = await data_bridge.tag_period(service, STUDENT, LINK, None, own["start"], own["end"])
    direct = await service.tag_period("student-03", "web", ["20FAKE_001_PV"], None, own["start"], own["end"])
    assert {k: v for k, v in result.items() if k != "cache"} == {k: v for k, v in direct.items() if k != "cache"}


async def test_tag_series_default_points_is_service_default_not_overridden(svc):
    service, _ = svc
    result = await data_bridge.tag_series(service, STUDENT, LINK, "last_8h", None, None, None)
    assert result["points"] == SERIES_DEFAULT_POINTS


async def test_tag_series_explicit_points_forwarded(svc):
    service, _ = svc
    result = await data_bridge.tag_series(service, STUDENT, LINK, "last_8h", None, None, 3)
    assert result["points"] == 3
    e = service.role.events.recent()[0]
    assert (e.caller, e.channel, e.tool) == ("student-03", "web", "tag_series")


async def test_calc_runtime_forwards_state_tag(svc):
    service, _ = svc
    result = await data_bridge.calc_runtime(service, STUDENT, STATE_LINK, "prev_shift", None, None)
    assert result["status"] == "ok"
    assert [i["tag"] for i in result["items"]] == ["25FAKE_007_CLS"]
    e = service.role.events.recent()[0]
    assert (e.caller, e.channel, e.tool) == ("student-03", "web", "calc_runtime")


# --- отказ прежней службы не оборачивается ---

async def test_historian_failure_surfaces_as_is_not_swallowed(svc):
    service, fake = svc
    fake.fail = RuntimeError("историан недоступен (двойник)")
    result = await data_bridge.tag_now(service, STUDENT, LINK)
    assert result["status"] == "error"                      # DataService._run: исключение → status=error, не "ok"
    direct_fake_result = await service.tag_now("student-03", "web", ["20FAKE_001_PV"])
    assert result["status"] == direct_fake_result["status"] == "error"


async def test_unknown_tag_link_gives_refusal_not_silent_empty(svc):
    """Согласованный тег, которого в каталоге историана нет (рассинхронизация между решением о связи и
    настоящим каталогом) — отказ/«нет значения» службы, не пустой успешный ответ."""
    service, _ = svc
    ghost = ResolvedLink(link_id="EQ-FAKE-999", process_id="PROC-FAKE-001", historian_tag="20FAKE_GHOST_PV",
                         unit=None, approved_by="tech1", approved_at=LINK.approved_at, scope="PROC-FAKE-001")
    result = await data_bridge.tag_now(service, STUDENT, ghost)
    assert result["status"] in ("refused", "error", "partial")
