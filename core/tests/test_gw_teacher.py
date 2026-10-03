# core/tests/test_gw_teacher.py — Д10-R100…R104; teacher_gateway, Gw, BASE, FRONT, ORIGIN, PLACE_NET — helpers
import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from helpers import (AGENT_DRAFT, BASE, FRONT, ORIGIN, PLACE_NET, PW, TEACHER_HISTORY, TEACHER_SES, Gw, agent_text,
                     login, make_gateway, now_utc, park_reload_worker, teacher_gateway, wait_until)
from pcbk_core.gateway import teacher_api
from pcbk_core.gateway.places import PlaceAnswer
from pcbk_core.gateway.teacher_api import JOURNAL_ROWS, TEACHER_PREFIX as P, TEACHER_TEXTS
from pcbk_core.gateway.teacher_api import TEACHER_PLACE_PATHS, TeacherGate, changes_of, floor_body_of, teacher_path

READS = ("overview", "journal", "places/03", "places/03/sessions", f"places/03/sessions/{TEACHER_SES}",
         "places/03/agents", f"places/03/agents/{AGENT_DRAFT.name}")
MOVE = {"changes": [{"place": "03", "from_cents": 10000, "to_cents": 9000},
                    {"place": "05", "from_cents": 10000, "to_cents": 11000}]}
WRITES = (("places/03/wake", {}), ("shares", MOVE))


@pytest.fixture
def tg(tmp_path):
    app, role = teacher_gateway(tmp_path, ready=(3,))                   # место 3 готово, место 5 спит
    park_reload_worker(role)
    assert role.agents.open(), role.agents.last_error                     # таблицы учёта — до первой строки теста
    role.agents.save(3, AGENT_DRAFT, agent_text(AGENT_DRAFT), now_utc())  # агент места 3: строка учёта и файл
    with TestClient(app, base_url=BASE, client=FRONT):
        yield Gw(app, role)


def post(c, path, body, origin=ORIGIN):
    return c.post(P + path, json=body, headers={"Origin": origin})


def test_student_gets_403_everywhere_and_nothing_is_touched(tg):        # Review Focus 2; неуспех 3
    s3 = tg.as_("stu-03")
    places, journal = len(tg.role.fake_places.requests), len(tg.role.teacher_log.recent(100))
    for path in READS:
        r = s3.get(P + path)
        assert (r.status_code, r.json()["data"]["message"]) == (403, TEACHER_TEXTS["teacher_only"]), path
    for path, body in WRITES:
        assert post(s3, path, body).status_code == 403, path
    assert s3.get(P + "places/03/sessions?place=03&login=teach").status_code == 403
    assert len(tg.role.fake_places.requests) == places and len(tg.role.teacher_log.recent(100)) == journal
    assert tg.role.llm.shares.cents(3) == 10000
    assert tg.role.teacher_stats.count_1h("role_refused") == len(READS) + len(WRITES) + 1
    assert tg.anon().get(P + "overview").status_code == 401
    assert tg.as_("teach", client=PLACE_NET).get(P + "overview").status_code == 404   # вне pcbk-front


def test_teacher_reads_place_only_by_three_gets(tg):                      # Review Focus 1
    t = tg.as_("teach")
    before = len(tg.role.fake_places.requests)
    assert t.get(P + "places/03/sessions").json()["sessions"][0]["id"] == TEACHER_SES
    body = t.get(P + f"places/03/sessions/{TEACHER_SES}").json()
    assert body["messages"][0]["parts"] == [{"kind": "text", "text": "что сейчас на 20FAKE_001_PV"}]
    sent = tg.role.fake_places.requests[before:]
    assert {(q.method, q.path, q.query) for q in sent} == {
        ("GET", "/experimental/session", "roots=true&archived=true"),
        ("GET", f"/session/{TEACHER_SES}", ""), ("GET", f"/session/{TEACHER_SES}/message", "")}
    assert all(q.url.host.endswith(".3.3") for q in sent)


def test_teacher_read_does_not_touch_place(tg):                          # Review Focus 1
    tg.role.mono.advance(100)
    tg.as_("teach").get(P + f"places/03/sessions/{TEACHER_SES}")
    assert tg.role.workplaces.idle_s(3) >= 100


def test_sleeping_place_not_woken_by_read(tg):                           # место 5 спит
    t = tg.as_("teach")
    r = t.get(P + "places/05/sessions")
    assert (r.status_code, r.json()["data"]["message"]) == (503, TEACHER_TEXTS["asleep"])
    assert ("start", 5) not in tg.role.fake_docker.calls                    # чтение место не будит
    assert post(t, "places/05/wake", {}, origin="https://evil.test").status_code == 403
    assert ("start", 5) not in tg.role.fake_docker.calls


def test_teacher_wake_is_student_wake(tmp_path, monkeypatch):            # I-2, I-4: вошедший клиент — задача живёт
    app, role = teacher_gateway(tmp_path)
    park_reload_worker(role)
    woken = []
    real_wake = role.workplaces.wake
    monkeypatch.setattr(role.workplaces, "wake", lambda n: woken.append(n) or real_wake(n))
    with TestClient(app, base_url=BASE, client=FRONT) as c:                 # цикл событий — на весь тест
        assert login(c, "teach").status_code == 200
        before = len(role.fake_places.requests)
        r = c.post(P + "places/05/wake", headers={"Origin": ORIGIN})
        assert (r.status_code, r.json()["state"]) == (202, "starting")
        wait_until(lambda: role.workplaces.view(5).state == "ready")
        assert woken == [5] and ("start", 5) in role.fake_docker.calls       # та же задача, что у студента: sp-ctl
        assert {(q.method, q.path) for q in role.fake_places.requests[before:]} <= {("GET", "/agent")}
        assert role.teacher_log.recent(1)[0].action == "wake"


def test_no_student_place_and_bad_paths(tg):
    t = tg.as_("teach")
    before = len(tg.role.fake_places.requests)
    for path in ("places/07/sessions", "places/07/agents", "places/07"):
        r = t.get(P + path)
        assert (r.status_code, r.json()["data"]["message"]) == (404, TEACHER_TEXTS["no_student"]), path
    for path in ("places/11/sessions", "places/3/sessions", "places/03/sessions/ses_short",
                 f"places/03/sessions/{TEACHER_SES}x", "places/03/agents/..%2Fx", "places/03/agents/Bad_Name"):
        assert t.get(P + path).status_code == 404, path
    assert len(tg.role.fake_places.requests) == before


def test_session_of_other_place_is_404(tg):
    tg.role.workplaces.set_ready_for_tests(5, tg.role.fake_docker.run(5))
    r = tg.as_("teach").get(P + f"places/05/sessions/{TEACHER_SES}")     # разговор есть только у места 3
    assert (r.status_code, r.json()["data"]["message"]) == (404, TEACHER_TEXTS["no_session"])


def test_journal_before_access_and_no_text(tg, caplog):                   # Review Focus 4
    t = tg.as_("teach")
    t.get(P + f"places/03/sessions/{TEACHER_SES}")
    row = tg.role.teacher_log.recent(1)[0]
    assert (row.login, row.action, row.place, row.target, row.outcome) == ("teach", "read", 3, TEACHER_SES,
                                                                           "requested")
    assert "20FAKE_001_PV" not in caplog.text and "что сейчас" not in caplog.text


def test_journal_down_closes_before_place(tmp_path):                      # учение journal_down
    app, role = teacher_gateway(tmp_path, ready=(3,), DRILL_TEACHER="journal_down")
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT):
        g = Gw(app, role)
        t = g.as_("teach")
        before = len(role.fake_places.requests)
        for path in ("places/03/sessions", f"places/03/sessions/{TEACHER_SES}", "places/03/agents"):
            r = t.get(P + path)
            assert (r.status_code, r.json()["data"]["message"]) == (503, TEACHER_TEXTS["journal"]), path
        assert len(role.fake_places.requests) == before
        assert g.anon(client=FRONT).get("/health/teacher").json()["store_ok"] is False


def test_journal_down_wins_over_asleep(tmp_path):                         # I-6: порядок журнал → готовность
    app, role = teacher_gateway(tmp_path, DRILL_TEACHER="journal_down")    # ни одно место не готово
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT):
        r = Gw(app, role).as_("teach").get(P + "places/05/sessions")
    assert (r.status_code, r.json()["data"]["message"]) == (503, TEACHER_TEXTS["journal"])
    assert ("start", 5) not in role.fake_docker.calls and role.fake_places.requests == []


def test_transcript_too_big_and_bad_answer(tg):                         # m-16
    t = tg.as_("teach")
    tg.role.fake_places.big[3] = 5 * 1024 * 1024
    r = t.get(P + f"places/03/sessions/{TEACHER_SES}")
    assert (r.status_code, r.json()["data"]["message"]) == (502, TEACHER_TEXTS["too_big"])
    del tg.role.fake_places.big[3]
    tg.role.fake_places.force_status[3] = (500, {})                          # место ответило не 200 и не 404
    r = t.get(P + f"places/03/sessions/{TEACHER_SES}")
    assert (r.status_code, r.json()["data"]["message"]) == (502, TEACHER_TEXTS["bad_answer"])
    assert tg.role.teacher_stats.count_1h("read_failed") == 2


def test_journal_route_shape(tg):                                        # I-1
    t = tg.as_("teach")
    t.get(P + f"places/03/sessions/{TEACHER_SES}")
    assert post(t, "shares", MOVE).status_code == 200
    rows = t.get(P + "journal").json()["rows"]
    assert [(r["action"], r["place"], r["outcome"]) for r in rows[:2]] == [("shares", None, "ok"), ("read", "03",
                                                                                                    "requested")]
    assert set(rows[1]) == {"id", "at", "login", "action", "place", "target", "outcome"}       # без detail
    assert rows[0]["detail"] == {"03": [10000, 9000], "05": [10000, 11000]}                     # detail — только у shares
    assert rows[1]["target"] == TEACHER_SES and len(rows) <= JOURNAL_ROWS
    assert len(t.get(P + "journal").json()["rows"]) == len(rows)            # чтение журнала в журнал не пишется


def test_agent_file_read_only_and_odd(tg):                               # Review Focus 5
    t = tg.as_("teach")
    cards = t.get(P + "places/03/agents").json()["agents"]
    assert [(c["name"], c["node"], c["tools"]) for c in cards] == [(AGENT_DRAFT.name, AGENT_DRAFT.node,
                                                                    list(AGENT_DRAFT.tools))]
    text = t.get(P + f"places/03/agents/{AGENT_DRAFT.name}").json()["text"]
    assert text == agent_text(AGENT_DRAFT)
    f = tg.role.agents.place_dir(3) / f"{AGENT_DRAFT.name}.md"
    f.unlink()
    f.symlink_to("/etc/hostname")                                           # ссылка вместо файла
    r = t.get(P + f"places/03/agents/{AGENT_DRAFT.name}")
    assert (r.status_code, r.json()["data"]["message"]) == (404, TEACHER_TEXTS["no_agent"])


def test_shares_post_rules(tg):                                          # Review Focus 3; Д10-R104
    t = tg.as_("teach")
    assert post(t, "shares", MOVE, origin="https://evil.test").status_code == 403
    r = post(t, "shares", MOVE)
    assert r.status_code == 200 and [p["share_cents"] for p in r.json()["places"]][2:5] == [9000, 10000, 11000]
    r = post(t, "shares", MOVE)                                             # «было» уже не то
    assert r.status_code == 409 and r.json()["name"] == "Conflict"
    for bad in ({"changes": []}, {"changes": [{"place": "3", "from_cents": 9000, "to_cents": 1}]},
                {"changes": [{"place": "03", "from_cents": 9000, "to_cents": True}]}, {"x": 1}, []):
        assert post(t, "shares", bad).status_code == 400, bad
    raw = t.post(P + "shares", content=b'{"changes": [], "changes": []}',
                 headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    assert raw.status_code == 400                                           # повтор ключа
    r = post(t, "shares", {"changes": [{"place": "03", "from_cents": 9000, "to_cents": 9001}]})
    assert r.status_code == 400 and "больше общего бюджета" in r.json()["data"]["message"]
    assert tg.role.teacher_stats.count_1h("change") == 1 and tg.role.teacher_stats.count_1h("change_failed") == 0


def test_manual_transfer_below_floor_is_400(tg):                         # I-1 р2: ручной перевод ниже пола
    tg.role.llm.shares.settled_of = lambda caller: 12.349 if caller == "student-03" else 0.0
    t = tg.as_("teach")
    r = post(t, "shares", {"changes": [{"place": "03", "from_cents": 10000, "to_cents": 1233},
                                       {"place": "05", "from_cents": 10000, "to_cents": 18767}]})
    assert (r.status_code, r.json()["data"]["message"]) == \
           (400, "Доля места 03 не может быть меньше уже оплаченного: 12,34 $")
    assert tg.role.llm.shares.cents(3) == 10000


def test_stop_place_route(tg):                                           # I-1 р2: пол берёт сервер
    tg.role.llm.shares.settled_of = lambda caller: 12.349 if caller == "student-03" else 0.0
    t = tg.as_("teach")
    assert post(t, "shares/floor", {"place": "03", "to": "05"}, origin="https://evil.test").status_code == 403
    assert post(tg.as_("stu-03"), "shares/floor", {"place": "03", "to": "05"}).status_code == 403
    for bad in ({"place": "03"}, {"place": "03", "to": "03"}, {"place": "03", "to": "05", "floor_cents": 1}, []):
        assert post(t, "shares/floor", bad).status_code == 400, bad         # числа пола клиент не передаёт
    r = post(t, "shares/floor", {"place": "03", "to": "05"})
    assert r.status_code == 200
    row = tg.role.teacher_log.recent(1)[0]                                   # m-2 р3: до второго вызова
    assert (row.action, row.outcome, row.detail) == \
        ("shares", "ok", {"03": [10000, 1234], "05": [10000, 18766], "kind": "floor"})
    p = {x["place"]: x for x in r.json()["places"]}
    assert (p["03"]["share_cents"], p["05"]["share_cents"], p["03"]["floor_cents"]) == (1234, 18766, 1234)
    r = post(t, "shares/floor", {"place": "03", "to": "05"})               # уже на полу
    assert r.status_code == 400
    assert r.json()["data"]["message"] == "Доля места 03 уже не выше оплаченного: 12,34 $"   # m-7 р3: сумма — пол
    oks = [x for x in tg.role.teacher_log.recent(5) if x.outcome == "ok"]    # сверху может быть строка refused
    assert oks[0].detail["kind"] == "floor" and tg.role.llm.shares.cents(3) == 1234


def test_overview_shape(tg):                                             # Д10-R101; m-11; B-1
    tg.role.store.add("gone", "student", 8, PW)                              # надгробие: учётка удалена, место закреплено
    tg.role.store.disable("gone")
    tg.role.store.remove("gone")
    body = tg.as_("teach").get(P + "overview").json()
    assert [p["place"] for p in body["places"]] == [f"{n:02d}" for n in range(1, 11)]
    p3, p7, p8 = body["places"][2], body["places"][6], body["places"][7]
    assert (p3["login"], p3["free"], p3["state"], p3["share_cents"], p3["floor_cents"], p3["agents"]) == \
           ("stu-03", False, "ready", 10000, 0, 1)
    assert (p7["login"], p7["free"], p7["disabled"], p7["share_cents"]) == (None, True, None, 10000)
    assert (p8["login"], p8["free"]) == (None, False)                       # за удалённым логином — не свободно
    assert body["budget"]["total_cents"] == 100000 and body["shares_ok"] is True


def test_teacher_still_refused_in_oc(tg):                                # прокси Д5б — 403, новый текст
    r = tg.as_("teach").post("/pcbk/api/oc/session", json={}, headers={"Origin": ORIGIN})
    assert (r.status_code, r.json()["data"]["message"]) == \
           (403, "Преподаватель читает разговоры студентов в кабинете — писать в них нельзя")


# --- сверх брифа: решения контроллера, договор с вебом А4 и охранные ветки ---

def test_refusal_names_web_relies_on(tg):                                # ревью А4, m-2: веб будит по имени
    t = tg.as_("teach")
    r = t.get(P + "places/05/sessions")
    assert (r.status_code, r.json()["name"]) == (503, "WorkplaceNotReady")   # кнопка «Разбудить для просмотра»
    view = t.get(P + "places/05").json()
    assert view == {"place": "05", "state": "sleeping", "detail": "Рабочее место спит"}   # причина — в detail
    tg.role.teacher_log.drill = "journal_down"
    r = t.get(P + "places/05/sessions")
    assert (r.status_code, r.json()["name"], r.json()["data"]["message"]) == \
           (503, "Unavailable", TEACHER_TEXTS["journal"])                    # журнал — не «спит»: кнопки нет
    tg.role.teacher_log.drill = ""


def test_shares_closed_when_floor_unknown(tg):                           # решение контроллера (ревью А2, m-2)
    t = tg.as_("teach")
    llm = tg.role.llm
    journal = len(tg.role.teacher_log.recent(100))
    for broken in ("ledger", "shares"):
        if broken == "ledger":
            llm.ledger.opened = False                                       # журнал вызовов не открыт — пол был бы 0
        else:
            llm.ledger.opened, llm.shares.loaded = True, False              # доли не загружены
        for path, body in (("shares", MOVE), ("shares/floor", {"place": "03", "to": "05"})):
            r = post(t, path, body)
            assert (r.status_code, r.json()["data"]["message"]) == (503, TEACHER_TEXTS["shares_down"]), (broken, path)
        o = t.get(P + "overview").json()
        assert o["shares_ok"] is False and o["budget"] is None
        assert {(p["share_cents"], p["floor_cents"], p["spent_usd"]) for p in o["places"]} == {(None, None, None)}
    llm.shares.loaded = True
    assert len(tg.role.teacher_log.recent(100)) == journal                  # ни строки ok, ни refused
    assert (llm.shares.cents(3), llm.shares.cents(5)) == (10000, 10000)
    assert tg.role.teacher_stats.count_1h("change") == 0 and tg.role.teacher_stats.count_1h("change_failed") == 0


def test_without_llm_overview_works_and_shares_say_no_llm(tmp_path):      # R101: без роли llm — null; R104: no_llm
    app, role = make_gateway(tmp_path, ready=(3,))
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT):
        t = Gw(app, role).as_("teach")
        o = t.get(P + "overview").json()
        assert (o["budget"], o["shares_ok"], o["places"][2]["share_cents"], o["places"][2]["login"]) == \
               (None, False, None, "stu-03")
        r = post(t, "shares", MOVE)
        assert (r.status_code, r.json()["data"]["message"]) == (503, TEACHER_TEXTS["no_llm"])


def test_teacher_ui_crash_report_accepted(tg):                           # решение контроллера: ui_crash кабинета
    t = tg.as_("teach")
    places, journal = len(tg.role.fake_places.requests), len(tg.role.teacher_log.recent(100))
    r = t.post("/pcbk/api/web/report", json={"kind": "ui_crash"}, headers={"Origin": ORIGIN})
    assert r.status_code == 204
    assert tg.role.web_reports.counts_1h(tg.role.monotonic())["ui_crash_1h"] == 1
    assert len(tg.role.fake_places.requests) == places and len(tg.role.teacher_log.recent(100)) == journal


def test_place_paths_are_three_gets():                                   # Review Focus 1: путь — только из шаблона
    assert len(TEACHER_PLACE_PATHS) == 3 and {m for m, _, _ in TEACHER_PLACE_PATHS.values()} == {"GET"}
    assert teacher_path("sessions") == ("GET", "/experimental/session", [("roots", "true"), ("archived", "true")])
    assert teacher_path("messages", TEACHER_SES) == ("GET", f"/session/{TEACHER_SES}/message", [])
    for kind, ses in (("prompt_async", TEACHER_SES), ("session", "../agent"), ("session", None),
                      ("messages", TEACHER_SES + "/x"), ("sessions", TEACHER_SES)):
        with pytest.raises(ValueError):
            teacher_path(kind, ses)


def test_body_forms():
    ok = json.dumps(MOVE).encode()
    assert changes_of(ok) == [(3, 10000, 9000), (5, 10000, 11000)]
    for bad in (b"{", b"[]", b'{"changes": [{"place": "03", "from_cents": 1, "to_cents": 2, "x": 1}]}',
                b'{"changes": [{"place": 3, "from_cents": 1, "to_cents": 2}]}',
                b'{"changes": [{"place": "03", "from_cents": 1.0, "to_cents": 2}]}',
                json.dumps({"changes": [MOVE["changes"][0]] * 11}).encode()):
        assert changes_of(bad) is None, bad
    assert floor_body_of(b'{"place": "03", "to": "10"}') == (3, 10)
    for bad in (b'{"place": "03", "to": "05", "to": "06"}', b'{"place": "00", "to": "05"}', b'{"place": "03"}',
                b'{"place": 3, "to": "05"}'):
        assert floor_body_of(bad) is None, bad


def test_no_answer_and_place_auth(tg):                                   # R102: ответ места
    t = tg.as_("teach")
    tg.role.fake_places.force_status[3] = (401, {})
    r = t.get(P + "places/03/sessions")
    assert (r.status_code, r.json()["name"]) == (502, "BadGateway")
    assert tg.role.stats.count_1h("place_auth_failed") == 1 and tg.role.workplaces.is_ready(3)
    del tg.role.fake_places.force_status[3]
    tg.role.fake_places.raise_for[3] = httpx.ConnectError("место упало")
    r = t.get(P + f"places/03/sessions/{TEACHER_SES}")
    assert (r.status_code, r.json()["name"], r.json()["data"]["message"]) == \
           (503, "WorkplaceNotReady", TEACHER_TEXTS["no_answer"])
    assert not tg.role.workplaces.is_ready(3) and tg.role.stats.count_1h("upstream_failed") == 1
    assert tg.role.teacher_stats.count_1h("read_failed") == 2 and tg.role.teacher_stats.count_1h("read") == 0


def test_other_session_in_answer_is_bad_answer(tg, monkeypatch):          # R102: чужой разговор в ответе места
    real = teacher_api._teacher_fetch

    async def fetch(role, n, kind, ses=None):
        answer = await real(role, n, kind, ses)
        if kind != "session":
            return answer
        other = {**json.loads(answer.body), "id": "ses_" + "O" * 26}
        return PlaceAnswer(answer.status, answer.content_type, json.dumps(other).encode())
    monkeypatch.setattr(teacher_api, "_teacher_fetch", fetch)
    r = tg.as_("teach").get(P + f"places/03/sessions/{TEACHER_SES}")
    assert (r.status_code, r.json()["data"]["message"]) == (502, TEACHER_TEXTS["bad_answer"])
    assert tg.role.teacher_stats.count_1h("read_failed") == 1 and tg.role.teacher_stats.count_1h("read") == 0


def test_reads_count_and_service_log_lines(tg, caplog):                   # R100: строка gw teacher, без текстов
    caplog.set_level(logging.INFO, logger="pcbk_core")
    t = tg.as_("teach")
    sessions = t.get(P + "places/03/sessions").json()["sessions"]
    assert sessions == [{"id": TEACHER_SES, "title": "Значение 20FAKE_001_PV", "created": 1, "updated": 3,
                         "archived": False}]
    body = t.get(P + f"places/03/sessions/{TEACHER_SES}").json()
    assert [(m["role"], m["agent"]) for m in body["messages"]] == [("user", "pomoshnik"), ("assistant", "pomoshnik")]
    assert body["messages"][1]["parts"] == [{"kind": "text", "text": TEACHER_HISTORY[1]["parts"][0]["text"]}]
    assert tg.role.teacher_stats.count_1h("read") == 2
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("gw teacher ")]
    assert any(line.startswith("gw teacher login=teach action=read place=03 outcome=ok status=200 ms=")
               for line in lines), lines
    for text in ("20FAKE_001_PV", "что сейчас", "Готово", "Значение"):
        assert text not in caplog.text, text
    s3 = tg.as_("stu-03")
    for _ in range(3):
        assert s3.get(P + "overview").status_code == 403
    refused = [r.getMessage() for r in caplog.records if "gw teacher refused" in r.getMessage()]
    assert refused == ["gw teacher refused login=stu-03 reason=role n=1 since_s=0"]   # одна строка на логин за минуту


def test_agent_odd_file_warns_without_name(tg, caplog):                  # R103: agent_odd без имени и пути
    t = tg.as_("teach")
    (tg.role.agents.place_dir(3) / f"{AGENT_DRAFT.name}.md").write_bytes(b"\xff\xfe" + b"x" * 10)   # не UTF-8
    r = t.get(P + f"places/03/agents/{AGENT_DRAFT.name}")
    assert (r.status_code, r.json()["data"]["message"]) == (404, TEACHER_TEXTS["no_agent"])
    assert "gw teacher agent_odd place=03" in caplog.text and AGENT_DRAFT.name not in caplog.text
    r = t.get(P + "places/03/agents/net-takogo")                            # неизвестное имя — 404 без WARNING
    assert (r.status_code, r.json()["data"]["message"]) == (404, TEACHER_TEXTS["no_agent"])
    assert caplog.text.count("agent_odd") == 1
    assert [x.target for x in tg.role.teacher_log.recent(2)] == ["net-takogo", AGENT_DRAFT.name]


def test_wake_under_journal_down_does_not_start(tmp_path):               # R100: журнал до пробуждения
    app, role = teacher_gateway(tmp_path, DRILL_TEACHER="journal_down")
    park_reload_worker(role)
    with TestClient(app, base_url=BASE, client=FRONT) as c:
        assert login(c, "teach").status_code == 200
        r = c.post(P + "places/05/wake", headers={"Origin": ORIGIN})
        assert (r.status_code, r.json()["data"]["message"]) == (503, TEACHER_TEXTS["journal"])
        assert ("start", 5) not in role.fake_docker.calls and not role.workplaces.is_waking(5)


@pytest.mark.anyio
async def test_teacher_gate_one_reader_two_waiting(monkeypatch):           # ворота кабинета (R102)
    gate, hold = TeacherGate(), asyncio.Event()
    entered = []

    async def reader(i):
        async with gate.slot(3):
            entered.append(i)
            await hold.wait()
    tasks = [asyncio.create_task(reader(i)) for i in range(3)]          # один читает, двое ждут
    await asyncio.sleep(0.01)
    with pytest.raises(teacher_api._Refused) as busy:                    # четвёртый — сразу busy
        async with gate.slot(3):
            pass
    assert busy.value.key == "busy" and entered == [0]
    async with gate.slot(5):                                             # соседнее место ворот не делит
        pass
    monkeypatch.setattr(teacher_api, "TEACHER_WAIT_S", 0.05)
    with pytest.raises(teacher_api._Refused):                            # очередь полна — busy без ожидания
        async with gate.slot(3):
            pass
    hold.set()
    await asyncio.gather(*tasks)
    assert entered == [0, 1, 2]
    hold.clear()
    task = asyncio.create_task(reader(9))
    await asyncio.sleep(0.01)
    with pytest.raises(teacher_api._Refused) as late:                    # не дождался за TEACHER_WAIT_S — busy
        async with gate.slot(3):
            pass
    assert late.value.key == "busy"
    hold.set()
    await task
