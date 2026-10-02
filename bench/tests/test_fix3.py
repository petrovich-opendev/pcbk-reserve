"""Доводка р3 (task-А4-fix3.md, п. 3): пробелы тестов по выжившим мутантам ревьюера; Minor «допуст/разреш/можно»."""
from helpers import DRAFT, card, now_call, rec, search_call
from pcbk_bench.checks import check_run, draft_tags
from pcbk_bench.numbers import also_weak, foreign_numbers

SCALE = search_call(["20FAKE_105_PV"], min_eu=0.0, max_eu=60.0)
B3_NOW = now_call("20FAKE_105_PV", 41.7)


def kinds(text, **kw):
    return [f.kind for f in foreign_numbers(text, kw.pop("src", []), **kw)]


def test_left_segment_value_then_scale_passes():                                 # значение слева — не утверждение
    for answer in ("Сейчас 41,7 т/ч, шкала датчика 0–60 т/ч.", "Сейчас 41,7 т/ч; шкала датчика 0–60 т/ч."):
        assert check_run(rec("B3", answer=answer, tools=[SCALE, B3_NOW]), card("B3")).passed is True, answer


def test_question_non_measure_numbers_are_weak():                                # неизмеряемые числа вопроса — слабые
    also = "Что было за последние 1,5 часа?"
    assert 1.5 in also_weak(also) and kinds("примерно 1,5", also=also) == []
    assert kinds("сейчас 1,5 бар", also=also, tag_units=["бар"]) == ["hard"]       # слабый — не закрывает hard


def test_quality_after_percent_in_strict():                                      # _QUALITY_AFTER: «100 % пропусков»
    assert kinds("Данных нет — 100 % пропусков.", strict_value_words=True) == ["allowed"]
    assert kinds("Данных нет — 100 % значений.", strict_value_words=True) == ["hard"]


def test_tags_line_takes_only_consecutive_tags():                                # строка «Теги:» с тегами не подряд
    head = "- Теги: 20FAKE_101_PV — температура; 20FAKE_104_PV — давление\n  - x —"
    text = DRAFT.replace("- Теги:\n  - 20FAKE_101_PV —", head, 1)
    assert draft_tags(text) == {"20FAKE_101_PV"}


def test_draft_without_tags_line_ignores_description_tokens():                   # ветка без «Теги» — токен в описании
    text = (DRAFT.replace("- Теги:\n", "")
            .replace("в коллекторе сушиль", "в коллекторе (см. 20FAKE_199_PV)"))
    assert "Теги" not in text and draft_tags(text) == {"20FAKE_101_PV", "20FAKE_104_PV"}


def test_scale_limit_with_permit_words_is_norm():                                # «предел прибора» + «можно» — норма
    for tail, want in (("пределы прибора 0–60 т/ч", True), ("разрешённый предел датчика — 60 т/ч", False),
                       ("предел прибора 60 т/ч, работать можно до него", False),
                       ("допустимый предел датчика — 60 т/ч", False)):
        v = check_run(rec("B3", answer=f"Сейчас расход 41,7 т/ч (20FAKE_105_PV); {tail}.", tools=[SCALE, B3_NOW]),
                      card("B3"))
        assert (v.passed is True) is want and (want or "number_mismatch" in v.hard), (tail, v)
        assert ("suspect_norm" in v.flags) is (not want), (tail, v)


def test_glue_units_generic_and_tag():                                           # склейка: единица общая и единица тега
    r = rec("B3", answer="Сейчас расход 41,7 т/ч (20FAKE_105_PV); шкала датчика от 0 тонн/ч до 60 т/ч.",
            tools=[SCALE, B3_NOW])
    assert check_run(r, card("B3")).passed is True                                  # «тонн/ч» — общая единица
    mw = [search_call(["20FAKE_105_PV"], min_eu=0.0, max_eu=60.0), now_call("20FAKE_105_PV", 41.7, unit="МВт")]
    r = rec("B3", answer="Сейчас 41,7 МВт (20FAKE_105_PV); шкала датчика 0 МВт – 60 МВт.", tools=mw)
    assert check_run(r, card("B3")).passed is True                                  # «МВт» — только единица тега


def test_claim_window_and_glue_details():                                        # выжившие мутанты р3
    def b3(answer):
        return check_run(rec("B3", answer=answer, tools=[SCALE, B3_NOW]), card("B3"))
    assert b3("Было и есть 41,7 т/ч, шкала датчика 0–60 т/ч.").passed is True  # утверждение до значения
    assert b3("Сейчас 41,7 т/ч; шкала датчика: минимум 0 и максимум 60 т/ч.").passed is True      # «и» — склейка
    assert b3("Сейчас 41,7 т/ч (20FAKE_105_PV); вчера было по шкале датчика 60 т/ч.").hard == ("number_mismatch",)
    assert b3("Сейчас 41,7 т/ч (20FAKE_105_PV); держалось по шкале датчика 60 т/ч.").hard == ("number_mismatch",)
    assert "number_mismatch" in b3("Сейчас 41,7 т/ч. Обычно 0–60 т/ч (шкала датчика).").hard    # «обычно» раньше


def test_quality_gap_without_value_words():                                      # I-6: «Достоверных сейчас 55 %»
    unl = [search_call(["16FAKE_109_PV"], min_eu=0.0, max_eu=100.0)]
    v = check_run(rec("C2", answer="Тег вне белого списка. Достоверных сейчас 55 %.", tools=unl), card("C2"))
    assert v.hard == ("number_mismatch",)
