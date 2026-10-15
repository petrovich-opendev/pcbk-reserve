# bench/tests/test_match.py — Д9-R91, Д9-R93
import json
import os
import stat
import sys
import time
from datetime import date, datetime, timedelta

import pytest

from fakes import FakeChat, FakeStand, twin_cards
from pcbk_bench.gateway import WorkplaceNotReady
from pcbk_bench.match import (BASIC_KINDS, CALC_AGENT, HARD, MatchResult, MatchRunner, build_cases, calc_agent_args,
                              cases_from_agent, judge_case, match_failures, node_tags, probe_calc, render_match)
from pcbk_bench.record import ToolCall, Turn

TAG = "20FAKE_201_PV"
TODAY = date(2026, 10, 10)
CASES = {c.kind: c for c in build_cases([TAG], ["20FAKE_210_RUN"], TODAY)}
PERIODS = {"prev_shift": ("2026-10-09T22:00:00+05:00", "2026-10-10T06:00:00+05:00"),
           "last_24h": ("2026-10-09T12:00:00+05:00", "2026-10-10T12:00:00+05:00"),
           "yesterday": ("2026-10-09T00:00:00+05:00", "2026-10-10T00:00:00+05:00")}


NOW_END = PERIODS["last_24h"][1]                # «сейчас» тестовых данных — 10.10 12:00 историана


def period_call(preset=None, span=None, avg=81.234, mn=70.0, mx=90.5, status="ok"):
    # доводка р1 (I-1, решение контроллера): период, кончающийся «сейчас» (12:00), — open, иной — нет; и свой тоже
    start, end = span or PERIODS[preset]
    out = {"tool": "tag_period", "status": "ok" if status == "ok" else status,
           "period": {"preset": preset, "start": start, "end": end, "tz": "UTC+05:00", "open": end == NOW_END},
           "items": [{"tag": TAG, "status": status, "avg": avg, "min": mn, "max": mx, "range": mx - mn}]}
    inp = {"tags": [TAG], **({"period": preset} if preset else {"start": start[:19], "end": end[:19]})}
    return ToolCall("pcbk_tag_period", inp, out, "completed")


def turn(text, *calls, error=None):
    return Turn("вопрос", [text], list(calls), error, 0.0, 1.0)


def test_build_cases_matrix():
    cases = build_cases([TAG, "20FAKE_202_PV"], ["20FAKE_210_RUN"], TODAY)
    assert [c.id for c in cases[:6]] == [f"{TAG}:{k}" for k in ("now", "avg_prev_shift", "minmax_last_24h",
                                                                 "range_yesterday", "avg_span", "profile_last_8h")]
    assert len(cases) == 13 and cases[-1].id == "20FAKE_210_RUN:runtime_yesterday"
    span = CASES["avg_span"]
    assert (span.start, span.end) == ("2026-10-09T08:00:00", "2026-10-09T12:00:00")
    assert span.question == f"Какое среднее по {TAG} 09.10.2026 с 08:00 до 12:00?"


def test_match_avg_prev_shift():                                                  # Review Focus 1
    call = period_call("prev_shift")
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее за прошлую смену — 81,2 м/мин.", call), call.output)
    assert (r.verdict, r.hard) == ("match", False)


def test_mismatch_off_by_one():
    call = period_call("prev_shift")
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее — 82,2 м/мин, минимум 70, максимум 90,5.", call), call.output)
    assert (r.verdict, r.hard) == ("mismatch", True) and "mismatch" in HARD


def test_wrong_period_is_hard():                                                  # Review Focus 2
    call = period_call("last_24h")
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее — 81,2 м/мин.", call), call.output)
    assert (r.verdict, r.hard) == ("wrong_period", True)


def test_span_24h_counts_as_last_24h():
    call = period_call(span=PERIODS["last_24h"])
    r = judge_case(CASES["minmax_last_24h"], turn("Минимум 70, максимум 90,5.", call), call.output)
    assert r.verdict == "match"


def test_custom_span_within_minute_ok():
    call = period_call(span=("2026-10-09T08:00:30+05:00", "2026-10-09T12:00:00+05:00"))
    assert judge_case(CASES["avg_span"], turn("Среднее 81,23.", call), call.output).verdict == "match"
    call = period_call(span=("2026-10-09T09:00:00+05:00", "2026-10-09T12:00:00+05:00"))
    assert judge_case(CASES["avg_span"], turn("Среднее 81,23.", call), call.output).verdict == "wrong_period"


def test_open_period_drift_is_note():
    call = period_call("last_24h")
    calc = period_call("last_24h", mn=69.0).output
    r = judge_case(CASES["minmax_last_24h"], turn("Минимум 70, максимум 90,5.", call), calc)
    assert (r.verdict, r.notes) == ("match", ["calc_moved"])


def test_closed_period_calc_differs_is_service():                                 # Review Focus 3
    call = period_call("prev_shift")
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее 81,2.", call), period_call("prev_shift", avg=80.0).output)
    assert (r.verdict, r.hard) == ("service", False)


def test_no_data_is_soft():                                                       # I7 критики р1
    call = period_call("prev_shift", status="no_data")
    r = judge_case(CASES["avg_prev_shift"], turn("Данных за смену нет.", call), call.output)
    assert (r.verdict, r.hard) == ("no_data", False)


def test_tool_unavailable_is_service():
    call = period_call("prev_shift", status="unavailable")
    r = judge_case(CASES["avg_prev_shift"], turn("Служба недоступна.", call), call.output)
    assert (r.verdict, r.hard) == ("service", False)


def test_no_tool_with_number_is_mismatch():
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее около 81 м/мин."), None)
    assert (r.verdict, r.hard, r.notes) == ("mismatch", True, ["no_tool"])


def test_no_number_soft():
    call = period_call("prev_shift")
    r = judge_case(CASES["avg_prev_shift"], turn("Посчитал, смотрите выше.", call), call.output)
    assert (r.verdict, r.hard) == ("no_number", False)


def test_hours_and_profile_keys():
    run = ToolCall("pcbk_calc_runtime", {"tags": ["20FAKE_210_RUN"], "period": "yesterday"},
                   {"status": "ok", "period": {"preset": "yesterday", "start": PERIODS["yesterday"][0],
                                               "end": PERIODS["yesterday"][1], "open": False},
                    "items": [{"tag": "20FAKE_210_RUN", "status": "ok",
                               "states": [{"value": 1, "hours": 16.4833, "percent": 68.7, "count": 8},
                                          {"value": 0, "hours": 7.5167, "percent": 31.3, "count": 8}]}]}, "completed")
    r = judge_case(CASES["runtime_yesterday"], turn("Работал 16,5 ч, стоял 7,5 ч.", run), run.output)
    assert r.verdict == "match"
    assert judge_case(CASES["runtime_yesterday"], turn("Стоял 7,5 ч.", run), run.output).verdict == "mismatch"  # m-2 Б2
    series = ToolCall("pcbk_tag_series", {"tags": [TAG], "period": "last_8h", "points": 24},
                      {"status": "ok", "period": {"preset": "last_8h", "start": "2026-10-10T04:00:00+05:00",
                                                  "end": "2026-10-10T12:00:00+05:00", "open": True},
                       "items": [{"tag": TAG, "status": "ok", "profile": {"first": 600.512, "last": 612.25,
                                                                         "change": 11.738}}]}, "completed")
    text = "В начале 600,51 м/мин, в конце 612,25 м/мин, рост на 11,74."
    assert judge_case(CASES["profile_last_8h"], turn(text, series), series.output).verdict == "match"
    assert judge_case(CASES["profile_last_8h"], turn("В начале 600,51, в конце 613.", series),
                      series.output).verdict == "mismatch"


def test_now_other_time_is_note():
    now = ToolCall("pcbk_tag_now", {"tags": [TAG]}, {"status": "ok", "items": [
        {"tag": TAG, "status": "ok", "value": 612.3, "time": "2026-10-10T11:59:40+05:00"}]}, "completed")
    calc = {"status": "ok", "items": [{"tag": TAG, "status": "ok", "value": 613.0,
                                       "time": "2026-10-10T12:00:40+05:00"}]}
    r = judge_case(CASES["now"], turn("Сейчас 612,3 м/мин.", now), calc)
    assert (r.verdict, r.notes) == ("match", ["calc_moved"])


def test_render_match_aliases_no_tags():                                          # Review Focus 4
    call = period_call("prev_shift")
    res = [judge_case(CASES["avg_prev_shift"], turn("Среднее 81,2.", call), call.output)]
    md = render_match(res, list(CASES.values()))
    assert TAG not in md and "ТЕГ-1" in md and "match" in md and "жёстких (неуспех 4) 0" in md


def test_calc_agent_args_have_all_tools_no_digits():
    a = calc_agent_args(list(CASES.values()))
    assert a["tools"] == ["catalog_search", "tag_now", "tag_period", "tag_series", "calc_runtime", "calc_deviation",
                          "equipment", "calc_spc", "report_event"]
    assert a["tags"] == [TAG, "20FAKE_210_RUN"] and not any(ch.isdigit() for ch in a["prompt"])


# --- доводка р1: ложный match (I-1 — I-4 ревью) ---

MINMAX = "Минимум 70, максимум 90,5."
PROFILE = "В начале 600,51, в конце 612,25."


def series_call(span, open_):
    start, end = span
    out = {"tool": "tag_series", "status": "ok",
           "period": {"preset": None, "start": start, "end": end, "tz": "UTC+05:00", "open": open_},
           "items": [{"tag": TAG, "status": "ok", "unit": "м/мин",
                      "profile": {"first": 600.512, "last": 612.25, "change": 11.738}}]}
    return ToolCall("pcbk_tag_series", {"tags": [TAG], "start": start[:19], "end": end[:19], "points": 24}, out,
                    "completed")


@pytest.mark.parametrize("kind,call,text", [
    ("minmax_last_24h", period_call(span=PERIODS["yesterday"]), MINMAX),                  # вчерашние сутки 00–00
    ("minmax_last_24h", period_call(span=("2026-10-09T07:00:00+05:00", "2026-10-10T07:00:00+05:00")),   # UTC без
     MINMAX),                                                                             # пояса: сдвиг на 5 ч
    ("profile_last_8h", series_call(PERIODS["prev_shift"], False), PROFILE),              # прошлая смена 22–06
])
def test_custom_window_not_ending_now_is_wrong_period(kind, call, text):                 # I-1 (решение контроллера)
    r = judge_case(CASES[kind], turn(text, call), call.output)
    assert (r.verdict, r.hard) == ("wrong_period", True)


def test_custom_window_ending_now_needs_its_length():                                    # I-4, M12
    short = period_call(span=("2026-10-09T13:00:00+05:00", NOW_END))                   # 23 ч, кончается «сейчас»
    assert short.output["period"]["open"] is True
    assert judge_case(CASES["minmax_last_24h"], turn(MINMAX, short), short.output).verdict == "wrong_period"
    eight = series_call(("2026-10-10T04:00:00+05:00", NOW_END), True)                   # свои 8 ч до «сейчас»
    assert judge_case(CASES["profile_last_8h"], turn(PROFILE, eight), eight.output).verdict == "match"


def test_no_data_with_number_is_mismatch():                                              # I-2
    call = period_call("prev_shift", status="no_data")
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее за смену — 81,2 м/мин.", call), call.output)
    assert (r.verdict, r.hard, r.notes) == ("mismatch", True, ["no_data_number"])


def test_service_with_number_keeps_verdict_and_notes_it():                               # I-2
    call = period_call("prev_shift", status="unavailable")
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее за смену — 81,2 м/мин.", call), call.output)
    assert (r.verdict, r.hard, r.notes) == ("service", False, ["tool_unavailable", "number_in_text"])


def test_period_checked_before_no_data():                                                # I-2, M7
    call = period_call("last_24h", status="no_data")
    r = judge_case(CASES["avg_prev_shift"], turn("Данных за сутки нет.", call), call.output)
    assert (r.verdict, r.hard) == ("wrong_period", True)


def test_no_tool_percent_is_mismatch():                                                  # I-3
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее за смену — 45,2 %."), None)
    assert (r.verdict, r.hard, r.notes) == ("mismatch", True, ["no_tool"])
    r = judge_case(CASES["avg_prev_shift"], turn("Данных нет: доля достоверных 0 %."), None)
    assert (r.verdict, r.notes) == ("no_number", ["no_tool"])                         # доля достоверности — не величина


def test_swapped_min_max_is_mismatch():                                                  # I-4, M1: подпись поля
    call = period_call("last_24h")
    r = judge_case(CASES["minmax_last_24h"], turn("Минимум 90,5, максимум 70.", call), call.output)
    assert (r.verdict, r.hard) == ("mismatch", True)


@pytest.mark.parametrize("text,avg", [("За 8 часов смены посчитал, смотрите выше.", 8.0),
                                      ("Смена 2, посчитал.", 2.0)])
def test_non_measure_numbers_are_not_keys(text, avg):                                    # I-4, M4
    call = period_call("prev_shift", avg=avg)
    assert judge_case(CASES["avg_prev_shift"], turn(text, call), call.output).verdict == "no_number"


def test_last_call_of_case_decides():                                                    # I-4, M5
    good, other = period_call("prev_shift"), period_call("last_24h")
    text = "Среднее за прошлую смену — 81,2 м/мин."
    assert judge_case(CASES["avg_prev_shift"], turn(text, good, other), other.output).verdict == "wrong_period"
    assert judge_case(CASES["avg_prev_shift"], turn(text, other, good), good.output).verdict == "match"


def test_open_flag_not_kind_decides_calc_moved():                                        # I-4, M8 (m-15)
    shift = period_call("prev_shift")
    shift.output["period"]["open"] = True                                         # первые 5 мин после смены
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее 81,2.", shift), period_call("prev_shift", avg=80.0).output)
    assert (r.verdict, r.notes) == ("match", ["calc_moved"])
    day = period_call("last_24h")
    day.output["period"]["open"] = False                                          # готовые сутки, но вывод закрыт
    r = judge_case(CASES["minmax_last_24h"], turn(MINMAX, day), period_call("last_24h", mn=69.0).output)
    assert (r.verdict, r.notes) == ("service", ["calc_differs"])


def test_percent_unit_value_is_a_quantity():                                             # I-4, M10
    call = period_call("prev_shift", avg=45.2)
    call.output["items"][0]["unit"] = "%"
    assert judge_case(CASES["avg_prev_shift"], turn("Среднее за смену — 45,2 %.", call), call.output).verdict == "match"


# --- заглушка стенда для пробы и случаев окна (I7, m-7) ---

SOURCE = 10
SERIES_START = datetime.fromisoformat("2026-10-10T04:00:00+05:00")
CATALOG = [{"tag": "20FAKE_201_PV", "kind": "analog", "live": True, "unit": "м/мин"},
           {"tag": "20FAKE_202_PV", "kind": "analog", "live": True, "unit": "%"},
           {"tag": "20FAKE_104_PV", "kind": "analog", "live": True, "unit": "бар"},
           {"tag": "20FAKE_106_SP", "kind": "analog", "live": False, "unit": "т/ч"},
           {"tag": "20FAKE_210_RUN", "kind": "discrete", "live": True, "unit": ""}]


def _series(args):
    """Ряд last_8h: 8 точек с шагом 8/7 ч, первая и последняя метки — на границах периода (m-6)."""
    step = timedelta(hours=8) / 7
    points = [[(SERIES_START + i * step).isoformat(), 600.0 + i] for i in range(8)]
    return {"tool": "tag_series", "status": "ok", "points": 8,
            "period": {"preset": "last_8h", "start": SERIES_START.isoformat(),
                       "end": (SERIES_START + timedelta(hours=8)).isoformat(), "tz": "UTC+05:00", "open": True},
            "items": [{"tag": args["tags"][0], "status": "ok", "unit": "м/мин", "points": points,
                       "profile": {"first": 600.0, "last": 607.0, "change": 7.0}}]}


def _runtime(args):
    return {"tool": "calc_runtime", "status": "ok",
            "period": {"preset": "yesterday", "start": "2026-10-09T00:00:00+05:00",
                       "end": "2026-10-10T00:00:00+05:00", "tz": "UTC+05:00", "open": False},
            "items": [{"tag": args["tags"][0], "status": "ok", "covered_percent": 100.0, "period_hours": 24.0,
                       "states": [{"value": 1, "label": "работает (по суффиксу)", "hours": 18.0, "percent": 75.0,
                                   "count": 8},
                                  {"value": 0, "label": "стоит (по суффиксу)", "hours": 6.0, "percent": 25.0,
                                   "count": 8}]}]}


def _agent_text(tags):
    rows = "\n".join(f"- `{t}` — Описание тега стенда, ед." for t in tags)
    return ("---\nmode: primary\n---\nТы — агент «Узел» учебного стенда ПЦБК. Узел: тестовый.\n\n"
            "Теги узла перечислены ниже.\n\n"
            f"## Теги узла (каталог историана; проверено службой 2026-10-09)\n{rows}\n\n## Правила\n- без норм\n")


@pytest.fixture
def fake_stand():
    stand = FakeStand(matches=CATALOG, calc_answers={"tag_series": _series, "calc_runtime": _runtime})
    stand.add_agent(SOURCE, "uzel-old")                                           # старый видимый агент места
    stand.add_agent(SOURCE, "uzel-new")                                           # самый новый видимый
    stand.agent_files = {(SOURCE, "uzel-old"): _agent_text(["20FAKE_104_PV"]),
                         (SOURCE, "uzel-new"): _agent_text(["20FAKE_201_PV", "20FAKE_106_SP", "20FAKE_210_RUN"])}
    return stand


def test_probe_calc_codes_only(fake_stand):                                       # Review Focus 4
    # fake_stand: catalog_search отдаёт 20FAKE_201_PV (живой, аналоговый) и 20FAKE_210_RUN; ряд — 8 точек с шагом
    # 8/7 ч, первая и последняя метки — на границах периода last_8h (m-6)
    codes = probe_calc(fake_stand)
    assert codes == ["series: ok rows=8/8 grid=even ends=yes",
                     "runtime: ok states=2 covered=100.0 sum_hours_ok=да"]
    assert fake_stand.seen_tags                                                   # положительный контроль: теги были
    assert not any(t in " ".join(codes) for t in fake_stand.seen_tags)
    fake_stand.search_status = "error"                                            # m-7: поиск отказал (учение)
    assert probe_calc(fake_stand) == ["series: error", "runtime: error"]
    fake_stand.search_status, fake_stand.matches = "ok", []                       # поиск ответил, живого тега нет
    assert probe_calc(fake_stand) == ["series: no_tag", "runtime: no_tag"]


def test_probe_calc_uneven_and_sum_hours():                                        # Д9-R93: grid, ends, sum_hours_ok
    def series(args):
        out = _series(args)
        pts = out["items"][0]["points"]
        pts[3][0] = (datetime.fromisoformat(pts[3][0]) + timedelta(seconds=5)).isoformat()
        out["items"][0]["points"] = pts[:-1]                                      # 7 точек, последняя — не конец
        return out

    def runtime(args):
        out = _runtime(args)
        out["items"][0]["states"][0]["hours"] = 18_000.0                           # не часы, а миллисекунды/1000
        return out

    stand = FakeStand(matches=CATALOG, calc_answers={"tag_series": series, "calc_runtime": runtime})
    assert probe_calc(stand) == ["series: ok rows=7/8 grid=uneven ends=no",
                                 "runtime: ok states=2 covered=100.0 sum_hours_ok=нет"]


def test_cases_from_agent_live_and_topup(fake_stand, tmp_path, capsys, monkeypatch):    # Review Focus 3, I7
    analog, discrete, from_agent, skipped = cases_from_agent(fake_stand, SOURCE)
    assert analog == ["20FAKE_201_PV", "20FAKE_202_PV", "20FAKE_104_PV"]
    assert discrete == ["20FAKE_210_RUN"] and (from_agent, skipped) == (1, 0)
    cfg = _write_config(tmp_path)
    _patch_cli(monkeypatch, fake_stand)
    from pcbk_bench.__main__ import main
    assert main(["match-cases", str(cfg)]) == 0
    assert capsys.readouterr().out.strip() == "случаев 19: аналоговых 3 (из агента 1), состояний 1"
    path = tmp_path / "match.cases.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"analog": analog, "discrete": discrete}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_cases_from_agent_discrete_from_search():                                 # нет тега состояния у агента
    stand = FakeStand(matches=CATALOG + [{"tag": "20FAKE_211_OPN", "kind": "discrete", "live": True, "unit": ""}])
    stand.add_agent(SOURCE, "uzel-a")
    stand.agent_files = {(SOURCE, "uzel-a"): _agent_text(["20FAKE_104_PV"])}
    analog, discrete, from_agent, skipped = cases_from_agent(stand, SOURCE)
    assert (analog, discrete, from_agent, skipped) == (["20FAKE_104_PV", "20FAKE_201_PV", "20FAKE_202_PV"],
                                                       ["20FAKE_210_RUN"], 1, 0)
    stand.matches = [m for m in stand.matches if m["tag"] != "20FAKE_210_RUN"]   # «_run» пуст — «_opn»
    assert cases_from_agent(stand, SOURCE)[1] == ["20FAKE_211_OPN"]


# --- прогонщик на заглушках fakes.py ---

PLACE = 9


def match_runner(tmp_path, chat=None, stand=None, cases=None, **kw):
    chat = chat if chat is not None else FakeChat.honest()
    stand = stand if stand is not None else FakeStand()
    chat.bind(stand, twin_cards(), time.monotonic)
    cases = cases if cases is not None else [CASES["now"], CASES["avg_prev_shift"]]
    runner = MatchRunner(chat.view(PLACE), stand, PLACE, cases, tmp_path / "out", sleep=lambda s: None, **kw)
    return runner, chat, stand


def test_runner_wakes_once_and_retries(tmp_path):                                 # Review Focus 5
    chat = FakeChat.honest(fail_first_send=WorkplaceNotReady(503, "WorkplaceNotReady"))
    runner, chat, stand = match_runner(tmp_path, chat=chat)
    try:
        results = runner.run()
    finally:
        runner.close()
    assert chat.wakes[PLACE] == 1
    assert [r.verdict for r in results] == ["match", "match"]
    sends = [c for c in chat.calls if c[0] == "send"]
    assert len(sends) == 2 and len(chat.sessions) == 3                           # первый случай повторён в новой сессии


def test_runner_turn_timeout_aborts(tmp_path):                                    # Review Focus 5
    runner, chat, stand = match_runner(tmp_path, chat=FakeChat.never_idle(), cases=[CASES["now"]],
                                       turn_timeout_s=0.05)
    try:
        results = runner.run()
    finally:
        runner.close()
    assert [(r.verdict, r.hard) for r in results] == [("timeout", False)]
    assert results[0].session in chat.aborted


def test_prepare_name_taken_archives_and_resaves(tmp_path):                       # прошлый прогон не убрал агента
    stand = FakeStand()
    first = iter([{"tool": "save_agent", "status": "refused", "problems": [{"code": "name_taken"}]}])
    real = stand.save_agent
    stand.save_agent = lambda place, args: next(first, None) or real(place, args)
    runner, chat, stand = match_runner(tmp_path, stand=stand)
    runner.prepare()
    runner.close()
    assert stand.archived == [(PLACE, CALC_AGENT), (PLACE, CALC_AGENT)]          # перед повтором и в close()


def test_runner_network_error_is_service(tmp_path):                               # обрыв связи — не модель
    chat = FakeChat.honest(fail_place={PLACE: ConnectionResetError()})
    runner, chat, stand = match_runner(tmp_path, chat=chat)
    try:
        results = runner.run()
    finally:
        runner.close()
    assert [(r.verdict, r.notes, r.hard) for r in results] == [("service", ["network"], False)] * 2
    assert (PLACE, CALC_AGENT) in stand.archived


def test_runner_writes_jsonl_and_archives(tmp_path, capsys):
    runner, chat, stand = match_runner(tmp_path)
    runner.prepare()
    assert stand.bench_saves[0][1]["name"] == CALC_AGENT
    try:
        results = runner.run()
    finally:
        runner.close()
    lines = [json.loads(x) for x in (tmp_path / "out" / "match.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["case"] for x in lines] == [CASES["now"].id, CASES["avg_prev_shift"].id]
    assert [x["verdict"] for x in lines] == [r.verdict for r in results] == ["match", "match"]
    assert all(x["session"] and x["turn"]["tools"] for x in lines)
    assert (PLACE, CALC_AGENT) in stand.archived and CALC_AGENT not in stand.agents_listed(PLACE)
    assert capsys.readouterr().err.splitlines() == ["случай 1/2: match", "случай 2/2: match"]
    assert stat.S_IMODE(os.stat(tmp_path / "out" / "match.jsonl").st_mode) == 0o600


# --- CLI на заглушках ---

def _write_config(tmp_path):
    raw = {"stand": "stand.json", "place": PLACE, "login": "d9-probe-show", "cases": "match.cases.json",
           "out_dir": "out", "turn_timeout_s": 30, "source": {"place": SOURCE}}
    path = tmp_path / "match.config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _patch_cli(monkeypatch, stand, chat=None):
    import pcbk_bench.__main__ as cli

    monkeypatch.setattr(cli, "match_stand", lambda mcfg: stand)
    if chat is not None:
        chat.bind(stand, twin_cards(), time.monotonic)
        monkeypatch.setattr(cli, "match_connect", lambda mcfg: (stand, chat.view(mcfg.place)))


def test_cli_prints_no_tags(fake_stand, tmp_path, capsys, monkeypatch):           # Review Focus 4
    from pcbk_bench.__main__ import main
    cfg = _write_config(tmp_path)
    _patch_cli(monkeypatch, fake_stand, FakeChat.honest())
    out = []
    # match — 3: FakeChat Д8 на виды Д9 отвечает не по ним, автотест — не успех (I-2 итогового ревью Д9б)
    for argv, code in ((["match-cases", str(cfg)], 0), (["match", str(cfg)], 3), (["probe-calc", str(cfg)], 0)):
        assert main(argv) == code, argv
        captured = capsys.readouterr()
        out.append(captured.out)
        assert "20FAKE" not in captured.out and "20FAKE" not in captured.err
    assert "случаев 19" in out[0] and "ТЕГ-4" in out[1] and out[2].startswith("series: ok")
    assert any(t.startswith("20FAKE") for t in fake_stand.seen_tags)              # положительный контроль
    assert "20FAKE" in (tmp_path / "out" / "match.jsonl").read_text(encoding="utf-8")
    md = (tmp_path / "out" / "match.md").read_text(encoding="utf-8")
    assert "20FAKE" not in md and "случаев 19" in md


def test_cli_match_only_basic(fake_stand, tmp_path, capsys, monkeypatch):
    from pcbk_bench.__main__ import main
    cfg = _write_config(tmp_path)
    (tmp_path / "match.cases.json").write_text(json.dumps({"analog": [TAG], "discrete": ["20FAKE_210_RUN"]}),
                                               encoding="utf-8")
    _patch_cli(monkeypatch, fake_stand, FakeChat.honest())
    assert main(["match", str(cfg), "--only", "basic"]) == 3                      # FakeChat Д8 — не успех (I-2)
    out = capsys.readouterr().out
    assert "случаев 5" in out and "profile_last_8h" not in out and "runtime_yesterday" not in out
    assert "успех: нет (hard" in out


# --- итоговая доводка Д9б ---

def runtime_call(states, hours=24.0):
    """Вывод calc_runtime за вчера (закрытый период) по тегу состояния случая."""
    return ToolCall("pcbk_calc_runtime", {"tags": ["20FAKE_210_RUN"], "period": "yesterday"},
                    {"status": "ok", "period": {"preset": "yesterday", "start": PERIODS["yesterday"][0],
                                                "end": PERIODS["yesterday"][1], "open": False},
                     "items": [{"tag": "20FAKE_210_RUN", "status": "ok", "states": states, "covered_percent": 100.0,
                                "period_hours": hours}]}, "completed")


FULL_DAY = [{"value": 1, "label": "работает (по суффиксу)", "hours": 24.0, "percent": 100.0, "count": 0}]


@pytest.mark.parametrize("text", ["Данных за 24 часа нет.", "За вчера (24 ч): работал 20 ч, стоял 4 ч.",
                                  "Вчера за сутки (24 часа) машина работала 22,5 ч, стояла 1,5 ч.",
                                  "Из 24 ч периода: работа — 18 ч, останов — 6 ч.",
                                  "Данных за вчерашние 24 часа нет."])
def test_period_length_is_not_state_hours(text):                                       # I-1 итогового ревью
    run = runtime_call(FULL_DAY)
    r = judge_case(CASES["runtime_yesterday"], turn(text, run), run.output)
    assert r.verdict != "match" and r.verdict in ("mismatch", "no_number"), (text, r.verdict)


@pytest.mark.parametrize("text", ["Работает: 24 ч (100 % периода), переходов 0.",
                                  "работает (по суффиксу): 24 ч (100 % периода), переходов 0."])
def test_full_day_state_hours_match(text):                                              # I-1: положительный контроль
    run = runtime_call(FULL_DAY)
    assert judge_case(CASES["runtime_yesterday"], turn(text, run), run.output).verdict == "match"


ALL = build_cases([TAG, "20FAKE_202_PV", "20FAKE_104_PV"], ["20FAKE_210_RUN"], TODAY)          # 19 случаев окна


@pytest.mark.parametrize("verdict,note", [("no_number", None), ("no_data", None), ("timeout", "turn_error")])
def test_soft_only_run_is_not_success(verdict, note):                                   # I-2: нижняя граница
    res = [MatchResult(c.id, verdict, [note] if note else []) for c in ALL]
    fails = match_failures(res, ALL)
    assert fails[:7] == [f"no_match:{k}" for k in ("now", "avg_prev_shift", "minmax_last_24h", "range_yesterday",
                                                   "avg_span", "profile_last_8h", "runtime_yesterday")]
    assert ("cut" in fails) is (verdict == "timeout")                                   # хвост — timeout: оборван
    md = render_match(res, ALL)
    assert "жёстких (неуспех 4) 0; service 0" in md and "\nуспех: нет (no_match:now" in md


def test_kind_without_match_is_not_success():                                          # I-2
    res = [MatchResult(c.id, "no_number" if c.kind == "avg_span" else "match") for c in ALL]
    assert match_failures(res, ALL) == ["no_match:avg_span"]
    assert render_match(res, ALL).rstrip().endswith("успех: нет (no_match:avg_span)")


def test_run_cut_by_cap_is_not_success():                                              # I-2: обрыв по потолку
    basic = [c for c in ALL if c.kind in BASIC_KINDS]                                   # 15: виды закрыты тегами 1–2
    res = [MatchResult(c.id, "match") if i < 10 else MatchResult(c.id, "timeout", ["turn_error"])
           for i, c in enumerate(basic)]
    assert match_failures(res, basic) == ["cut"]
    assert match_failures(res[:10], basic) == ["cut"]                                   # хвоста плана нет вовсе
    mid = res[:10] + [MatchResult(c.id, "match") for c in basic[10:]]
    mid[4] = MatchResult(basic[4].id, "timeout", ["turn_timeout"])                     # срок хода посреди плана
    assert match_failures(mid, basic) == []


def test_honest_run_is_success_and_hard_is_not():                                      # I-2
    res = [MatchResult(c.id, "match") for c in ALL]
    assert match_failures(res, ALL) == [] and render_match(res, ALL).rstrip().endswith("успех: да")
    res[1] = MatchResult(ALL[1].id, "mismatch")
    res[2] = MatchResult(ALL[2].id, "service", ["calc_differs"])
    assert match_failures(res, ALL) == ["hard", "service"]


class _StubRunner:
    """Прогонщик CLI без шлюза: вердикты по виду (results_by_kind), прочие — match."""
    results_by_kind: dict = {}

    def __init__(self, chat, stand, place, cases, out, **kw):
        self.cases = cases
        out.mkdir(mode=0o700, parents=True, exist_ok=True)                        # как MatchRunner.prepare()

    def run(self):
        return [MatchResult(c.id, self.results_by_kind.get(c.kind, "match")) for c in self.cases]

    def close(self):
        pass


@pytest.mark.parametrize("by_kind,code,line", [({}, 0, "успех: да"),
                                              ({"profile_last_8h": "no_number"}, 3,
                                               "успех: нет (no_match:profile_last_8h)")])
def test_cli_match_exit_code_says_success(fake_stand, tmp_path, capsys, monkeypatch, by_kind, code, line):   # I-2
    import pcbk_bench.__main__ as cli
    cfg = _write_config(tmp_path)
    (tmp_path / "match.cases.json").write_text(json.dumps({"analog": [TAG], "discrete": ["20FAKE_210_RUN"]}),
                                               encoding="utf-8")
    _patch_cli(monkeypatch, fake_stand, FakeChat.honest())
    monkeypatch.setattr(_StubRunner, "results_by_kind", by_kind)
    monkeypatch.setattr(cli, "MatchRunner", _StubRunner)
    assert cli.main(["match", str(cfg)]) == code
    assert capsys.readouterr().out.rstrip().endswith(line)
    assert (tmp_path / "out" / "match.md").read_text(encoding="utf-8").rstrip().endswith(line)


def test_cli_match_cap_refusals_are_not_success(fake_stand, tmp_path, capsys, monkeypatch):   # I-2: потолок
    from pcbk_bench.__main__ import main
    cfg = _write_config(tmp_path)
    (tmp_path / "match.cases.json").write_text(json.dumps({"analog": [TAG], "discrete": []}), encoding="utf-8")
    _patch_cli(monkeypatch, fake_stand, FakeChat.honest(error="pcbk_run_spent"))      # каждый ход — отказ прокси
    assert main(["match", str(cfg)]) == 3
    out = capsys.readouterr().out
    assert "match 0; жёстких (неуспех 4) 0; service 0; нет данных 0; прочих 6" in out
    assert out.rstrip().endswith(", cut)")



# --- I-3 итогового ревью: «живой» ≠ «с историей» ---

NO_HISTORY = {"20FAKE_201_PV", "20FAKE_210_RUN"}                                  # живые, но истории за период нет
CATALOG_I3 = CATALOG + [{"tag": "20FAKE_211_RUN", "kind": "discrete", "live": True, "unit": ""}]


def _no_history(answer):
    """Ответ калькулятора: у тега из NO_HISTORY элемент no_data (partial, без чисел), как у службы."""
    def call(args):
        tag = args["tags"][0]
        if tag in NO_HISTORY:
            return {"tool": "x", "status": "partial", "notes": [],
                    "items": [{"tag": tag, "status": "no_data", "text": "за период данных нет"}]}
        return answer(args)
    return call


def _stand_i3():
    return FakeStand(matches=CATALOG_I3, calc_status={t: "no_data" for t in NO_HISTORY},
                     calc_answers={"tag_series": _no_history(_series), "calc_runtime": _no_history(_runtime)})


def test_probe_skips_live_tag_without_history():                                  # I-3
    stand = _stand_i3()
    assert probe_calc(stand) == ["series: ok rows=8/8 grid=even ends=yes",
                                 "runtime: ok states=2 covered=100.0 sum_hours_ok=да"]
    probed = [(tool, args["tags"][0]) for tool, args in stand.calc_calls if tool != "catalog_search"]
    assert probed == [("tag_series", "20FAKE_201_PV"), ("tag_series", "20FAKE_202_PV"),
                      ("calc_runtime", "20FAKE_210_RUN"), ("calc_runtime", "20FAKE_211_RUN")]


def test_probe_no_history_anywhere_is_unresolved(monkeypatch):                    # I-3: «не решена», не «не прошла»
    monkeypatch.setattr(sys.modules[__name__], "NO_HISTORY",
                        {"20FAKE_201_PV", "20FAKE_202_PV", "20FAKE_104_PV", "20FAKE_210_RUN", "20FAKE_211_RUN"})
    stand = _stand_i3()
    assert probe_calc(stand) == ["series: unresolved", "runtime: unresolved"]
    assert sum(1 for tool, _ in stand.calc_calls if tool == "tag_series") == 3   # не больше трёх тегов на вид


def test_cases_skip_live_tags_without_history(tmp_path, capsys, monkeypatch):     # I-3: добор случаев
    stand = _stand_i3()
    stand.add_agent(SOURCE, "uzel")
    stand.agent_files = {(SOURCE, "uzel"): _agent_text(["20FAKE_201_PV", "20FAKE_106_SP", "20FAKE_210_RUN"])}
    assert cases_from_agent(stand, SOURCE) == (["20FAKE_202_PV", "20FAKE_104_PV"], ["20FAKE_211_RUN"], 0, 2)
    checked = [args["tags"][0] for tool, args in stand.calc_calls if tool in ("tag_period", "calc_runtime")]
    assert checked == ["20FAKE_201_PV", "20FAKE_202_PV", "20FAKE_104_PV", "20FAKE_210_RUN", "20FAKE_211_RUN"]
    cfg = _write_config(tmp_path)
    _patch_cli(monkeypatch, stand)
    from pcbk_bench.__main__ import main
    assert main(["match-cases", str(cfg)]) == 0
    assert capsys.readouterr().out.strip() == ("случаев 13: аналоговых 2 (из агента 0), состояний 1; "
                                               "без данных за период пропущено 2")



# --- итоговая доводка Д9б: Minor вердиктов (m-1, m-3 итога; m-1…m-3 ревью Б1) ---

def test_hours_and_minutes_are_one_number():                                      # m-1 итогового ревью
    run = runtime_call([{"value": 1, "label": "работает", "hours": 16.55, "percent": 69.0, "count": 8},
                        {"value": 0, "label": "стоит", "hours": 7.45, "percent": 31.0, "count": 8}])
    case = CASES["runtime_yesterday"]
    assert judge_case(case, turn("Работает 16 ч 33 мин, стоит 7 ч 27 мин.", run), run.output).verdict == "match"
    assert judge_case(case, turn("Работает 16 часов 33 минуты.", run), run.output).verdict == "match"
    assert judge_case(case, turn("Работает 16 ч 40 мин, стоит 7 ч 20 мин.", run), run.output).verdict == "mismatch"
    late = runtime_call([{"value": 1, "label": "работает", "hours": 16.5417, "percent": 68.9, "count": 8}])
    assert judge_case(case, turn("Работает 16 ч 33 мин.", late), late.output).verdict == "match"      # до минуты


def test_no_data_with_percent_is_mismatch():                                      # m-3 итогового ревью
    call = period_call("prev_shift", status="no_data")
    r = judge_case(CASES["avg_prev_shift"], turn("Данных за смену нет; в среднем держалось 45,2 %.", call),
                   call.output)
    assert (r.verdict, r.notes) == ("mismatch", ["no_data_number"])
    r = judge_case(CASES["avg_prev_shift"], turn("Данных за смену нет: доля достоверных 0 %.", call), call.output)
    assert r.verdict == "no_data"                                                 # доля достоверности — не величина


def test_first_last_and_range_need_their_labels():                                # m-1 ревью Б1
    eight = series_call(("2026-10-10T04:00:00+05:00", NOW_END), True)
    case = CASES["profile_last_8h"]
    assert judge_case(case, turn("В начале 612,25, в конце 600,51.", eight), eight.output).verdict == "mismatch"
    text = "20FAKE_201_PV: в начале периода 600,51 м/мин (04:00), в конце 612,25 м/мин (12:00), изменение 11,74."
    assert judge_case(case, turn(text, eight), eight.output).verdict == "match"   # формат двойника (R92)
    day = period_call("yesterday", mn=0.0, mx=20.5)                               # минимум 0: размах = максимум
    case = CASES["range_yesterday"]
    assert judge_case(case, turn("Размах 18, максимум 20,5.", day), day.output).verdict == "mismatch"
    assert judge_case(case, turn("Минимум 0, максимум 20,5, размах 20,5.", day), day.output).verdict == "match"


def test_question_hours_are_not_values():                                         # m-2 ревью Б1
    span = period_call(span=("2026-10-09T08:00:00+05:00", "2026-10-09T12:00:00+05:00"), avg=12.0)
    case = CASES["avg_span"]
    assert judge_case(case, turn("Среднее с 8 до 12 — 15 м/мин.", span), span.output).verdict == "mismatch"
    assert judge_case(case, turn("Среднее с 8 до 12 — 12 м/мин.", span), span.output).verdict == "match"
    eight = series_call(("2026-10-10T04:00:00+05:00", NOW_END), True)
    eight.output["items"][0]["profile"] = {"first": 8.0, "last": 9.0, "change": 1.0}
    text = "За 8-часовой период: в начале 9, в конце 9."
    assert judge_case(CASES["profile_last_8h"], turn(text, eight), eight.output).verdict == "mismatch"


def test_closed_period_moved_between_turn_and_calc_is_note():                     # m-3 ревью Б1
    call = period_call("prev_shift")                                              # ход: смена 22–06, закрыта
    calc = period_call("prev_shift", span=("2026-10-10T06:00:00+05:00", "2026-10-10T14:00:00+05:00"), avg=80.0)
    assert calc.output["period"]["open"] is False                                 # калькулятор — уже смена 06–14
    r = judge_case(CASES["avg_prev_shift"], turn("Среднее 81,2.", call), calc.output)
    assert (r.verdict, r.notes) == ("match", ["calc_moved"])



# --- итоговая доводка Д9б: Minor прогонщика и случаев окна (m-6, m-7 ревью Б1) ---

def test_jsonl_lines_carry_run_label(tmp_path):                                   # m-6 ревью Б1
    runner, chat, stand = match_runner(tmp_path)
    try:
        runner.run()
        runner.run()                                                              # повтор в тот же OUT
    finally:
        runner.close()
    lines = [json.loads(x) for x in (tmp_path / "out" / "match.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 4 and all(datetime.fromisoformat(x["run"]).tzinfo is not None for x in lines)
    assert lines[0]["run"] == lines[1]["run"] != lines[2]["run"] == lines[3]["run"]


def test_node_tags_take_backquoted_name_without_digits():                         # m-7 ревью Б1
    text = _agent_text(["20FAKE_201_PV", "FAKE_SPEED_PV", "20FAKE_210_RUN"])
    assert node_tags(text) == ["20FAKE_201_PV", "FAKE_SPEED_PV", "20FAKE_210_RUN"]


def test_place_of_boundary_11_ok_12_refused():             # Д15б: _place_of — PLACE_MAX (11), не магическое 10
    from pcbk_bench.match import _place_of
    assert _place_of(11, "place") == 11
    with pytest.raises(ValueError, match="от 1 до 11"):
        _place_of(12, "place")
