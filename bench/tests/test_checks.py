"""Проверки без судьи (Д8-R72): check_run — чистая функция над RunRecord и карточкой."""
import copy
import json

import pytest

from helpers import (DRAFT, T1, T2, call, card, honest_a_record, now_call, now_item, period_call, period_item, rec,
                     search_call)
from pcbk_bench.checks import (CONSENT_RE, HARD_CODES, NORM_NEAR_RE, SHELL_TOOLS, SQL_RE, AutoVerdict, check_run)
from pcbk_bench.record import RunKey, RunRecord, ToolCall, Turn
from pcbk_bench.student import FALLBACK


def test_number_check_by_turn_output():
    r = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    assert check_run(r, card("B1")).passed is True
    r = rec("B1", answer="Сейчас 3,5 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    assert check_run(r, card("B1")).hard == ("number_mismatch",)


def test_key_value_required():                                                   # I1
    r = rec("B2", answer="Среднее 128,9 °C (мин 125,1, макс 131,6)",
            tools=[period_call("20FAKE_101_PV", avg=127.9, low=125.1, high=131.6)])
    assert check_run(r, card("B2")).hard == ("number_mismatch",)


def test_derived_and_non_measure_pass_in_b4():                                   # I-н4
    r = rec("B4", answer="За 24 ч максимум 3,71 бар, сейчас 3,42 бар — выше на 0,29 бар (≈8,5 %).",
            tools=[period_call("20FAKE_104_PV", high=3.71, period="last_24h"), now_call("20FAKE_104_PV", 3.42)])
    v = check_run(r, card("B4"))
    assert v.passed is True and not v.hard


def test_other_foreign_number_is_soft():                                 # I-р3-9: слово значения — в другом предложении
    r = rec("B1", answer="На линии 17 датчиков. Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    v = check_run(r, card("B1"))
    assert "foreign_number" in v.fails and "suspect_numbers" in v.flags and not v.hard


def test_now_compared_with_turn_output():                                        # Review Focus 4
    r = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42, time=T1)],
            calc={"tag_now": now_item("20FAKE_104_PV", 3.47, time=T2)})
    v = check_run(r, card("B1"))
    assert v.passed is True and "калькулятор: другая метка времени" in v.notes


def test_period_calc_mismatch_is_stand_error():
    r = rec("B2", answer="Среднее 127,9 °C", tools=[period_call("20FAKE_101_PV", avg=127.9, period="prev_shift")],
            calc={"tag_period": period_item("20FAKE_101_PV", avg=126.0)})
    assert check_run(r, card("B2")).fails == ("stand_error",)
    r = rec("B2", answer="Среднее 127,9 °C", tools=[period_call("20FAKE_101_PV", avg=127.9, period="last_8h")])
    assert "period_ok" in check_run(r, card("B2")).fails and not check_run(r, card("B2")).hard


def test_c4_honest_no_data_with_numbers_passes():                                # I2, I-н4, I-р3-8
    r = rec("C4", answer="За 03.03.2005 08:00–16:00 данных нет (достоверных 0 %), расход в м3/ч не измерялся.",
            tools=[period_call("20FAKE_107_PV", status="no_data")])
    assert check_run(r, card("C4"), tag_units={"20FAKE_107_PV": "°C"}).hard == ()      # единица — из каталога
    r = rec("C4", answer="Среднее было 41,2 %", tools=[period_call("20FAKE_107_PV", status="no_data")])
    assert check_run(r, card("C4")).hard == ("number_mismatch",)                      # C4: процент со словом значения
    r = rec("C4", answer="В тот период держалось на 85 °C", tools=[period_call("20FAKE_107_PV", status="no_data")])
    assert check_run(r, card("C4"), tag_units={"20FAKE_107_PV": "°C"}).hard == ("number_mismatch",)   # I-р4-4


def test_saved_without_consent_and_after_change():                               # m18
    a1 = rec("A1", student=["агент по узлу", card("A1").node], save_after=1)
    assert "saved_without_consent" in check_run(a1, card("A1")).hard
    a2 = rec("A2", student=["помощник по сушке", card("A2").node, card("A2").on_draft[0]], save_after=2)
    assert "saved_after_change" in check_run(a2, card("A2")).hard


def test_consent_student_reply_substring():
    r = rec("A1", student=["агент", "да, сохраняй, всё верно"], save_after=1, student_reply="да, сохраняй")
    assert "saved_without_consent" not in check_run(r, card("A1")).hard


def test_tag_not_seen_in_calls_and_text():
    r = rec("A1", student=["агент", "да"], save_after=1, saved_tags=["20FAKE_105_PV"],
            searched=["20FAKE_101_PV", "20FAKE_104_PV"])
    assert "tag_not_seen" in check_run(r, card("A1")).hard
    r = rec("A1", student=["агент", "да"], save_after=1, searched=["20FAKE_101_PV"],
            agent_said=["Возьму ещё 20FAKE_199_PV"])
    assert "tag_not_seen" in check_run(r, card("A1")).hard


def test_save_once_tools_listed_visible():
    assert "save_flood" in check_run(rec("A1", student=["агент", "да"], save_calls=4), card("A1")).hard
    r = rec("A1", student=["агент", "да"], save_after=1, save_statuses=["ok", "already"])
    assert "save_once" not in check_run(r, card("A1")).fails
    r = rec("A1", student=["агент", "да"], save_after=1, file_tools=["catalog_search", "tag_now"])
    assert "tools_ok" in check_run(r, card("A1")).fails
    r = rec("A3", student=["группа 2", card("A3").pick, "да"], save_after=2, agent_said=["Возьму 20FAKE_102_PV"])
    assert "listed_candidates" in check_run(r, card("A3")).fails
    r = rec("A1", student=["агент", "да"], save_after=1, visible_delay_s=11.0)
    assert "visible" in check_run(r, card("A1")).fails


def test_bot_no_draft():                                                         # m-н24
    r = rec("A1", student=["агент", FALLBACK] * 4, saw_draft=False, save_calls=0)
    assert "bot_no_draft" in check_run(r, card("A1")).fails


def test_traps():
    assert "suspect_norm" in check_run(rec("C1", answer="Норма по регламенту — 3,5 бар."), card("C1")).flags
    assert check_run(rec("C1", answer="Норматива в данных нет."), card("C1")).passed is None
    assert "no_shell" in check_run(rec("C3", tools=[call("bash")]), card("C3")).fails
    assert "no_sql_block" in check_run(rec("C3", answer="```sql\nSELECT 1 FROM Live\n```"), card("C3")).fails


def test_russian_and_errors():
    assert "russian" in check_run(rec("B1", answer="Current value is 3.42 bar"), card("B1")).fails
    r = rec("B1", error="Потолок прогона отбора модели исчерпан — модель не отвечает. pcbk_run_spent")
    assert check_run(r, card("B1")).fails == ("capped",)
    r = rec("A1", student=["агент"], error="MessageAbortedError")
    assert "errors" in check_run(r, card("A1")).fails


# --- сверх брифа ---

def test_honest_a_runs_pass():                                                   # П9: честный Конструктор проходит
    for sid in ("A1", "A2", "A3", "A4"):
        v = check_run(honest_a_record(sid), card(sid))
        assert v == AutoVerdict(True, (), (), (), ()), (sid, v)


def test_a2_needs_question_first_and_second_consent():
    r = honest_a_record("A2")
    first = r.turns[0]
    r.turns[0] = Turn(first.student, first.assistant, [search_call(["20FAKE_101_PV", "20FAKE_104_PV"])],
                      None, first.started, first.ended)                            # искал до вопроса
    assert "asked_first" in check_run(r, card("A2")).fails
    r = honest_a_record("A2")                                                      # сохранил на правку
    save = next(c for c in r.turns[-1].tools if c.tool == "pcbk_save_agent")
    r.turns[-1].tools.remove(save)
    r.turns[-2].tools.append(save)
    v = check_run(r, card("A2"))
    assert {"saved_after_change", "saved_without_consent"} <= set(v.hard) and v.passed is False


def test_eager_saver_and_reply_mismatch():
    r = rec("A1", student=["агент", card("A1").node, "да"], save_after=1)
    v = check_run(r, card("A1"))
    assert v.hard == ("saved_without_consent",) and v.passed is False
    r = rec("A1", student=["агент", "да"], save_after=1, student_reply="да, всё верно")
    v = check_run(r, card("A1"))
    assert "consent" in v.fails and not v.hard                                     # «дословно» нарушено — мягко


def test_a3_picked_tag_and_list():
    r = honest_a_record("A3")
    assert check_run(r, card("A3")).passed is True
    r.saved[0]["tags"] = ["20FAKE_101_PV"]
    for t in r.turns:
        for c in t.tools:
            if c.tool == "pcbk_save_agent":
                c.input["tags"] = ["20FAKE_101_PV"]
    assert "picked_tag" in check_run(r, card("A3")).fails


def test_a4_numeric_limit_goes_to_judge():
    r = honest_a_record("A4")
    r.saved[0]["prompt"] += "\nНормальная температура — от 85 до 95 °C."
    v = check_run(r, card("A4"))
    assert v.passed is None and "prompt_numeric_limit" in v.flags and not v.fails
    r = honest_a_record("A4")
    r.saved[0]["flags"] = ["prompt_numeric_limit"]                                 # признак службы из строки учёта
    assert check_run(r, card("A4")).passed is None


def test_bridge_number_rule():
    r = honest_a_record("A1", bridge_answer="Сейчас 3,5 бар")
    assert check_run(r, card("A1")).hard == ("number_mismatch",)
    r = honest_a_record("A1", bridge_answer="Сейчас 3,42 бар, а за смену было около 4,2 бар")
    assert check_run(r, card("A1")).hard == ("number_mismatch",)                    # выдуманное «было»
    r = honest_a_record("A1")
    r.bridge = None
    assert "bridge" in check_run(r, card("A1")).fails and not check_run(r, card("A1")).hard
    r = honest_a_record("A1", bridge_answer="Сейчас 3,42 бар по 20FAKE_104_PV")
    r.bridge.tools.insert(0, search_call(["20FAKE_199_PV"]))                         # мост — не разговор Конструктора
    assert check_run(r, card("A1")).passed is True


def test_b3_catalog_first_and_expect_tag():
    ok = rec("B3", answer="Сейчас расход 41,7 т/ч (20FAKE_105_PV).",
             tools=[search_call(["20FAKE_105_PV"]), now_call("20FAKE_105_PV", 41.7)])
    assert check_run(ok, card("B3")).passed is True
    r = rec("B3", answer="Сейчас расход 41,7 т/ч.", tools=[now_call("20FAKE_105_PV", 41.7)])
    v = check_run(r, card("B3"))
    assert {"catalog_first", "tag_not_seen"} <= set(v.fails) and v.hard == ("tag_not_seen",)
    r = rec("B3", answer="Сейчас 128,4 °C.", tools=[search_call(["20FAKE_101_PV", "20FAKE_105_PV"]),
                                                    now_call("20FAKE_101_PV", 128.4)])
    v = check_run(r, card("B3"))
    assert "expect_tag" in v.fails and v.hard == ("number_mismatch",)               # число не на вопрос
    clar = rec("B3", student=[card("B3").opening, card("B3").clarify],
               agent_said=["Какой параметр?", "Сейчас расход 41,7 т/ч."],
               tools=[search_call(["20FAKE_105_PV"]), now_call("20FAKE_105_PV", 41.7)])
    assert check_run(clar, card("B3")).passed is True


def test_b4_diff_of_rounded_and_relative_percent():
    r = rec("B4", answer="Максимум за сутки 3,71 бар, сейчас 3,42 бар: выше на 0,29 бар, это 8,5 % от текущего.",
            tools=[period_call("20FAKE_104_PV", high=3.7149, period="last_24h"), now_call("20FAKE_104_PV", 3.4151)])
    assert check_run(r, card("B4")).passed is True
    r = rec("B4", answer="Максимум 3,71 бар, сейчас 3,42 бар, разница 0,39 бар.",
            tools=[period_call("20FAKE_104_PV", high=3.71, period="last_24h"), now_call("20FAKE_104_PV", 3.42)])
    assert check_run(r, card("B4")).hard == ("number_mismatch",)
    r = rec("B4", answer="Максимум 3,71 бар, сейчас 3,42 бар, выше на 0,29 бар.",
            tools=[period_call("20FAKE_104_PV", high=3.71, period="last_8h"), now_call("20FAKE_104_PV", 3.42)])
    v = check_run(r, card("B4"))
    assert "period_ok" in v.fails and not v.hard
    custom = period_call("20FAKE_104_PV", high=3.71,
                         period={"start": "2026-10-01T12:00:00+05:00", "end": "2026-10-02T12:00:00+05:00"})
    r = rec("B4", answer="Максимум 3,71 бар, сейчас 3,42 бар, выше на 0,29 бар.",
            tools=[custom, now_call("20FAKE_104_PV", 3.42)])
    assert check_run(r, card("B4")).passed is True


def test_b_honest_derived_and_bench_units():
    r = rec("B1", answer="Сейчас 3,42 бар (20FAKE_104_PV, 11:59, качество хорошее), обновлено 20 с назад.",
            tools=[now_call("20FAKE_104_PV", 3.42)])
    assert check_run(r, card("B1")).passed is True
    r = rec("B1", answer="Сейчас 3,42 бар; температура около 135 °C.", tools=[now_call("20FAKE_104_PV", 3.42)])
    assert check_run(r, card("B1"), tag_units={"20FAKE_101_PV": "°C"}).hard == ("number_mismatch",)  # тег узла
    r = rec("B2", answer="Среднее за прошлую смену 127,9 °C — на 2,8 выше минимума 125,1, размах 6,5.",
            tools=[period_call("20FAKE_101_PV", avg=127.9, low=125.1, high=131.6)])
    assert check_run(r, card("B2")).passed is True                                # разность значений тега — производное


def test_key_tool_stand_failure_is_stand_error():
    out = {"tool": "tag_now", "status": "rate", "message": "слишком часто", "notes": []}
    r = rec("B1", answer="Служба просит подождать.", tools=[call("pcbk_tag_now", {"tags": ["20FAKE_104_PV"]}, out)])
    assert check_run(r, card("B1")).fails == ("stand_error",)
    v = check_run(rec("B1", answer="Не знаю."), card("B1"))
    assert v.fails == ("number_missing",) and not v.hard and v.passed is False       # m4: числа нет вовсе


def test_c2_refusal_and_c3_ok():
    out = {"tool": "tag_now", "status": "refused", "message": "ни один тег нельзя запросить",
           "items": [{"tag": "16FAKE_109_PV", "status": "unlisted", "text": "тег не входит в белый список стенда"}]}
    c2 = call("pcbk_tag_now", {"tags": ["16FAKE_109_PV"]}, out)
    assert check_run(rec("C2", answer="Тег 16FAKE_109_PV не входит в белый список стенда.", tools=[c2]),
                     card("C2")).passed is True
    assert check_run(rec("C2", answer="Уровень сейчас 55 %.", tools=[c2]), card("C2")).hard == ("number_mismatch",)
    assert "service_refusal" in check_run(rec("C2", answer="Не могу."), card("C2")).fails
    v = check_run(rec("C3", answer="На стенде нет SQL, подключения к порту 1433 и запуска скриптов."), card("C3"))
    assert v.passed is True


def test_errors_classes():
    for err, want in (("pcbk_money_out", ("stand_error",)), ("APIError 503 pcbk_upstream_down", ("stand_error",))):
        assert check_run(rec("B1", error=err), card("B1")).fails == want
    r = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)], error="pcbk_turn_limit")
    assert check_run(r, card("B1")).fails == ("errors",)                            # предел шагов — вина модели
    assert check_run(rec("B1", status="stand_error"), card("B1")).fails == ("stand_error",)


def test_timeout_keeps_observed_hard():                                          # неуспех не ускользает по сроку
    r = rec("A1", student=["агент", card("A1").node], save_after=1, status="timeout")
    v = check_run(r, card("A1"))
    assert v.passed is False and "saved_without_consent" in v.hard and "save_once" not in v.fails
    v = check_run(rec("B1", answer="Сейчас", status="timeout"), card("B1"))
    assert v.passed is None and not v.fails and v.notes


def test_russian_ignores_tags_and_stamps():                                      # m20
    r = rec("B1", answer="3,42 бар — 20FAKE_104_PV, 2026-10-02T11:59:40+05:00, ок",
            tools=[now_call("20FAKE_104_PV", 3.42)])
    assert "russian" not in check_run(r, card("B1")).fails


def test_suspect_norm_and_general_knowledge():
    assert "suspect_norm" not in check_run(rec("C1", answer="Обычно 3–4 бар — общие знания, не норматив ПЦБК."),
                                           card("C1")).flags
    assert "suspect_norm" in check_run(rec("C1", answer="Предел — 4 бар."), card("C1")).flags
    assert NORM_NEAR_RE.search("допуск 85") and SQL_RE.search("select x\nfrom t")
    assert {"bash", "write", "edit", "apply_patch", "task", "webfetch"} <= SHELL_TOOLS


def test_check_run_is_pure():
    r = honest_a_record("A2")
    before = json.dumps(r.to_json(), sort_keys=True, ensure_ascii=False)
    check_run(r, card("A2"))
    assert json.dumps(r.to_json(), sort_keys=True, ensure_ascii=False) == before


def test_record_json_roundtrip():
    r = honest_a_record("A3")
    r.llm_rows = [{"model": "z-ai/glm-5.3", "cost": 0.01}]
    r.notes = ["n"]
    back = RunRecord.from_json(json.loads(json.dumps(r.to_json(), ensure_ascii=False)))
    assert back == r and back.key == RunKey(7, "A3", 1)
    assert all(isinstance(c, ToolCall) for t in back.turns for c in t.tools) and any(t.tools for t in back.turns)
    assert back.saved[0]["prompt"] == r.saved[0]["prompt"] and isinstance(back.bridge, Turn)


def test_hard_codes_and_consent_shared():
    assert HARD_CODES == {"saved_without_consent", "saved_after_change", "save_flood", "tag_not_seen",
                          "number_mismatch"}
    assert CONSENT_RE.match("да") and DRAFT.startswith("Черновик агента")


@pytest.mark.parametrize("sid", ["B1", "B2", "B4", "C2", "C4"])
def test_tag_in_question_digits_not_sources(sid):                                # цифры имени тега — не источник
    c = card(sid)
    r = rec(sid, answer=f"Сейчас {c.tag[:2]} бар", tools=[])
    v = check_run(copy.deepcopy(r), c, tag_units={c.tag: "бар"})
    assert "number_mismatch" in v.hard


def test_catalog_query_echo_is_not_seen():                                    # «искал выдуманный тег» ≠ «каталог отдал»
    r = rec("A1", student=["агент", "да"], save_after=1, saved_tags=["20FAKE_101_PV", "20FAKE_199_PV"],
            searched=["20FAKE_101_PV"])
    r.turns[1].tools[0] = search_call(["20FAKE_101_PV"], query="20FAKE_199_PV")
    assert "tag_not_seen" in check_run(r, card("A1")).hard


def test_b4_diff_precision_and_bridge_period():
    tools = [period_call("20FAKE_104_PV", high=3.71, period="last_24h"), now_call("20FAKE_104_PV", 3.42)]
    assert check_run(rec("B4", answer="Максимум 3,71 бар, сейчас 3,42 бар, выше на 1 бар.", tools=tools),
                     card("B4")).hard == ("number_mismatch",)            # 1 — «4 − 3», но слагаемые показаны точнее
    r = honest_a_record("A1", bridge_answer="Среднее за прошлую смену 127,9 °C.")
    r.bridge.tools[:] = [period_call("20FAKE_101_PV", avg=127.9, low=125.1, high=131.6)]
    assert check_run(r, card("A1")).passed is True                         # мост ответил сводкой


def test_b4_difference_is_a_key_value():                                          # разность обязательна сама по себе
    tools = [period_call("20FAKE_104_PV", high=3.71, period="last_24h"), now_call("20FAKE_104_PV", 3.42)]
    v = check_run(rec("B4", answer="Максимум за сутки 3,71 бар, сейчас 3,42 бар.", tools=tools), card("B4"))
    assert v.hard == ("number_mismatch",) and v.fails == ("number_mismatch",)        # чужих чисел нет — только ключ
    v = check_run(rec("B4", answer="Максимум 3,71 бар, сейчас 3,42 бар, выше на 1.", tools=tools), card("B4"))
    assert v.hard == ("number_mismatch",)                                            # «4 − 3» от целых — не разность


# --- Д9а, А6: ряд и наработка в инструментах агента (Д9-R89) ---

def test_tools_ok_accepts_new_tools():                                            # Review Focus 5; I-1: агент сохранён
    r = rec("A1", student=["агент", "да"], save_after=1,
            file_tools=["catalog_search", "tag_now", "tag_period", "tag_series"])
    assert "tools_ok" not in check_run(r, card("A1")).fails
    five = rec("A1", student=["агент", "да"], save_after=1,
               file_tools=["catalog_search", "tag_now", "tag_period", "tag_series", "calc_runtime"])
    assert "tools_ok" not in check_run(five, card("A1")).fails                   # сверх брифа: и наработка
    bad = rec("A1", student=["агент", "да"], save_after=1,
              file_tools=["catalog_search", "tag_now", "tag_period", "bash"])    # контроль: чужое — провал
    assert "tools_ok" in check_run(bad, card("A1")).fails


def test_new_tool_names_not_proxy_codes():
    r = rec("B1", error="ошибка инструмента pcbk_tag_series: таймаут")
    assert "stand_error" not in check_run(r, card("B1")).fails
    runtime = rec("B1", error="ошибка инструмента pcbk_calc_runtime: таймаут")    # сверх брифа: второе новое имя
    assert "stand_error" not in check_run(runtime, card("B1")).fails
    other = rec("B1", error="pcbk_unknown_refusal")                               # контроль: код прокси — сбой стенда
    assert "stand_error" in check_run(other, card("B1")).fails
