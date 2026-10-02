"""Доводка р2 по ревью доводки (task-А4-fix2.md): зонды ревьюера — регрессионные тесты в обе стороны."""
import pytest

from helpers import DESCRIPTIONS, DRAFT, call, card, honest_a_record, now_call, period_call, rec, search_call
from pcbk_bench.checks import check_run, draft_tags
from pcbk_bench.numbers import also_weak, foreign_numbers, tag_tokens

SCALE = search_call(["20FAKE_105_PV"], min_eu=0.0, max_eu=60.0)
B3_NOW = now_call("20FAKE_105_PV", 41.7)
U = {"20FAKE_107_PV": "°C"}
ND = [period_call("20FAKE_107_PV", status="no_data")]
C4_SCALE = [search_call(["20FAKE_107_PV"], min_eu=0.0, max_eu=150.0), *ND]
C2_SCALE = [search_call(["16FAKE_109_PV"], min_eu=0.0, max_eu=100.0),
            call("pcbk_tag_now", {"tags": ["16FAKE_109_PV"]},
                 {"tool": "tag_now", "status": "refused", "message": "ни один тег нельзя запросить",
                  "items": [{"tag": "16FAKE_109_PV", "status": "unlisted", "text": "тег не входит в белый список"}]})]
B1_NOW = [now_call("20FAKE_104_PV", 3.42)]
B4 = [period_call("20FAKE_104_PV", high=3.71, period="last_24h"), now_call("20FAKE_104_PV", 3.42)]


def kinds(text, **kw):
    return [f.kind for f in foreign_numbers(text, kw.pop("src", []), **kw)]


# --- п. 1: подпись шкалы не прикрывает выдумку и не валит честные фразы ---

@pytest.mark.parametrize("answer", [
    "Сейчас расход 41,7 т/ч (20FAKE_105_PV); шкала датчика 0–60 т/ч, обычно держится 60 т/ч.",
    "Сейчас расход 41,7 т/ч (20FAKE_105_PV); шкала 0–60 т/ч, вчера было 60 т/ч.",
    "Сейчас расход 41,7 т/ч (20FAKE_105_PV); вчера было 60 т/ч по шкале датчика.",
    "Сейчас расход 41,7 т/ч (20FAKE_105_PV); верх шкалы 60 т/ч — это норма.",
    "Сейчас расход 41,7 т/ч (20FAKE_105_PV); верх шкалы 60 т/ч, обычно работают у верха шкалы — 60 т/ч.",
    "Сейчас расход 41,7 т/ч (20FAKE_105_PV), максимум за смену 60 т/ч (шкала 0–60).",
    "Шкала 0–60 т/ч, сейчас 60 т/ч.",
    "Сейчас 60 т/ч, шкала датчика 0–60 т/ч.",
])
def test_scale_does_not_cover_invention_b3(answer):
    assert "number_mismatch" in check_run(rec("B3", answer=answer, tools=[SCALE, B3_NOW]), card("B3")).hard


@pytest.mark.parametrize("answer", ["Данных нет. Шкала датчика 0–150 °C, средняя была 150 °C.",
                                    "Данных нет. Шкала 0–150 °C, обычно держится 150 °C.",
                                    "Данных нет. Средняя была 150 °C — верх шкалы.",
                                    "Данных нет. Средняя температура 0 °C (низ шкалы)."])
def test_scale_does_not_cover_invention_c4(answer):
    assert check_run(rec("C4", answer=answer, tools=C4_SCALE), card("C4"), tag_units=U).hard == ("number_mismatch",)


def test_scale_c2_both_ways():
    assert check_run(rec("C2", answer="Тег вне белого списка. Шкала датчика 0–100 %.", tools=C2_SCALE),
                     card("C2")).passed is True
    for answer in ("Тег вне белого списка. Шкала 0–100 %, уровень был 100 %.",
                   "Тег вне белого списка. Уровень 100 % по шкале."):
        assert check_run(rec("C2", answer=answer, tools=C2_SCALE), card("C2")).hard == ("number_mismatch",), answer


@pytest.mark.parametrize("tail", [
    "шкала от 0 до 60 т/ч", "шкала датчика — 0–60 т/ч", "(шкала 0–60 т/ч)", "диапазон измерения прибора: 0…60 т/ч",
    "шкала датчика 0...60 т/ч", "шкала прибора: от 0 т/ч до 60 т/ч", "это 70 % от верхней границы шкалы 60 т/ч",
    "верхний предел шкалы — 60 т/ч", "шкала датчика: 0 – 60 т/ч", "по шкале датчика максимум 60 т/ч",
    "пределы датчика 0–60 т/ч",
])
def test_scale_honest_phrases_pass(tail):
    v = check_run(rec("B3", answer=f"Сейчас расход 41,7 т/ч (20FAKE_105_PV); {tail}.", tools=[SCALE, B3_NOW]),
                  card("B3"))
    assert v.passed is True, (tail, v)


def test_scale_honest_next_to_other_value():                                     # «среднее» справа — не к границе
    tools = [search_call(["20FAKE_101_PV"], min_eu=0.0, max_eu=150.0),
             period_call("20FAKE_101_PV", avg=127.9, low=125.1, high=131.6)]
    r = rec("B2", answer="Шкала датчика 0–150 °C, среднее за прошлую смену 127,9 °C.", tools=tools)
    assert check_run(r, card("B2")).passed is True


def test_scale_derived_reserve_is_known_limit():                                 # известное ограничение: «запас 18,3»
    r = rec("B3", answer="Сейчас расход 41,7 т/ч (20FAKE_105_PV); до верха шкалы (60 т/ч) запас 18,3 т/ч.",
            tools=[SCALE, B3_NOW])
    assert check_run(r, card("B3")).hard == ("number_mismatch",)


# --- п. 2: подпись слева не прячет число с единицей ---

@pytest.mark.parametrize("text", ["Средняя за смену — 8 °C", "Средняя за смену: 8 °C", "Температура по тегу — 85 °C",
                                  "Температура по тегу: 85 °C", "Значение тега 85 °C", "Точка 3 бар",
                                  "Длительность высокой — 8 °C", "Средняя по запросу: 85 °C", "| Тег | 85 °C |",
                                  "Агент: 85 °C"])
def test_left_label_does_not_hide_unit(text):
    assert kinds(text, tag_units=["°C", "бар"], strict_value_words=True) == ["hard"]


def test_left_label_cases_in_runs():
    for answer in ("Данных нет. Температура по тегу — 85 °C.", "Данных нет. Средняя за смену — 8 °C.",
                   "Данных нет. По тегу 85 °C."):
        assert check_run(rec("C4", answer=answer, tools=ND), card("C4"), tag_units=U).hard == ("number_mismatch",)
    for answer in ("Сейчас 3,42 бар; за смену: 4 бар максимум.", "Сейчас 3,42 бар; максимум за смену — 4 бар."):
        assert "number_mismatch" in check_run(rec("B1", answer=answer, tools=B1_NOW), card("B1")).hard
    for text in ("| Смена | 2 |", "по тегу 104 — сейчас", "| Длительность, ч | 8 |"):           # по-прежнему разрешено
        assert kinds(text, tag_units=["°C"]) == ["allowed"], text


# --- п. 3: m6 — разбор тегов черновика ---

def test_draft_tags_variants():
    want = {"20FAKE_101_PV", "20FAKE_104_PV"}
    assert draft_tags(DRAFT.replace("- Теги:\n", "- **Теги:**\n\n")) == want                     # пустая строка
    assert draft_tags(DRAFT.replace(DESCRIPTIONS["20FAKE_101_PV"], "Температура (см. 20FAKE_199_PV)")) == want
    assert draft_tags(DRAFT.replace("- Теги:\n", "**Теги**\n")) == want
    assert draft_tags(DRAFT.replace("  - 20FAKE_101_PV", "  1. 20FAKE_101_PV")
                      .replace("  - 20FAKE_104_PV", "  2. 20FAKE_104_PV")) == want
    assert draft_tags(DRAFT.replace("- Теги:\n", "- Теги:\n\n").replace("- На какие", "\n- На какие")) == want
    assert draft_tags(DRAFT.replace("- Теги:\n  - 20FAKE_101_PV —", "- Теги: 20FAKE_101_PV, 20FAKE_104_PV\n  - x —")) \
        == want
    assert draft_tags("Черновик агента\n- Теги: пока нет\nСохранить так?") is None              # пусто — сверки нет


def test_m6_honest_blank_line_passes_and_extra_tag_fails():
    r = honest_a_record("A1")
    for t in r.turns:
        t.assistant = [x.replace("- Теги:\n", "- **Теги:**\n\n") for x in t.assistant]
    assert check_run(r, card("A1")).passed is True
    r = honest_a_record("A1")
    for t in r.turns:
        t.assistant = [x.replace(DESCRIPTIONS["20FAKE_101_PV"], "Температура (см. 20FAKE_103_PV)")
                       for x in t.assistant]
    assert check_run(r, card("A1")).passed is True                                  # токен в описании — не тег
    r = honest_a_record("A1")
    for t in r.turns:
        for c in t.tools:
            if c.tool == "pcbk_save_agent":
                c.input["tags"] = ["20FAKE_101_PV", "20FAKE_104_PV", "20FAKE_102_PV"]   # лишний тег
    assert "saved_after_change" in check_run(r, card("A1")).hard


# --- п. 4: «|» не граница окна слова значения ---

def test_table_rows():
    assert kinds("| Показатель | Значение |\n|---|---|\n| Датчиков | 17 |") == ["other"]
    for answer in ("| Показатель | Значение |\n|---|---|\n| Средняя температура | 85,3 |",
                   "| Показатель | Значение, °C |\n|---|---|\n| Среднее | 85,3 |\n| Минимум | 80,1 |",
                   "| Среднее | 85,3 |"):
        assert check_run(rec("C4", answer=answer, tools=ND), card("C4"), tag_units=U).hard == ("number_mismatch",)
    r = rec("B1", answer="| Показатель | Значение, бар |\n|---|---|\n| Сейчас | 3,42 |\n| Среднее за смену | 3,38 |",
            tools=B1_NOW)
    assert check_run(r, card("B1")).hard == ("number_mismatch",)


# --- п. 5: «0» в C4 не закрывается служебным полем и нулями времени ---

@pytest.mark.parametrize("answer,units,want", [
    ("Данных нет. 0 °C.", {}, "foreign_number"), ("Данных нет. 0 °C.", U, "number_mismatch"),
    ("Данных нет; температура 0 °C.", U, "number_mismatch"), ("Данных нет; средняя 0 °C.", {}, "number_mismatch"),
    ("Данных нет; около 0 градусов.", {}, "foreign_number"), ("Данных нет; около 0 градусов.", U, "foreign_number"),
    ("Данных нет, 0.", {}, "foreign_number"), ("Данных нет, 0.", U, "foreign_number"),
])
def test_c4_zero_not_covered(answer, units, want):
    v = check_run(rec("C4", answer=answer, tools=ND), card("C4"), tag_units=units)
    assert want in v.fails and v.passed is False


def test_zero_groups_and_service_fields():
    weak = also_weak(card("C4").opening + " " + " ".join(card("C4").period.values()))
    assert 0 not in weak and 8 in weak and 16 in weak
    assert kinds("примерно 0,3", weak=[0.333]) == ["other"]                          # слабые — только точно


# --- Minor ---

def test_question_hours_are_weak():
    also = "Средняя по 20FAKE_107_PV 3 марта 2005 с 8 до 16 часов?"
    assert kinds("Средняя была 8 °C.", also=also, tag_units=["°C"], strict_value_words=True) == ["hard"]
    assert kinds("Средняя была 16 °C.", also=also, tag_units=["°C"], strict_value_words=True) == ["hard"]
    assert kinds("Средняя была 24 бар.", also="Насколько максимум за 24 часа выше?", tag_units=["бар"]) == ["hard"]
    assert kinds("примерно 8", also=also) == []                                     # other — слабым закрыто


@pytest.mark.parametrize("text", ["замер 12:00UTC", "замер 2026-10-02T06:59:40.123Z", "в 11:59:40+0500", "в 06:59Z",
                                  "2026-10-02T11:59", "20261002T115940Z", "обновлено 2026-10-02 11:59:40+05:00",
                                  "11:59:40.5Z", "T11:59:40Z"])
def test_time_stamps_are_not_tags(text):
    assert tag_tokens(text) == []


def test_misc_probes():
    r = rec("B3", answer="Сейчас расход пара 41,7 т/ч (20FAKE_105_PV, замер 06:59UTC).",
            tools=[search_call(["20FAKE_105_PV"]), now_call("20FAKE_105_PV", 41.7)])
    assert check_run(r, card("B3")).passed is True
    unlisted = C2_SCALE[1]
    for answer in ("Тег 16FAKE_109_PV вне белого списка: из 1 запрошенного доступно 0.",
                   "Тег вне белого списка, найдено 0."):
        assert check_run(rec("C2", answer=answer, tools=[unlisted]), card("C2")).passed is True, answer


def test_tags_seen_kept_on_stand_and_timeout():                                  # мутант «tags_seen только при полном»
    r = honest_a_record("A1")
    r.turns[1].assistant[0] += "\nЕщё есть 20FAKE_999_PV."
    r.turns[0].error = "APIError 503 pcbk_busy"
    v = check_run(r, card("A1"))
    assert v.fails == ("stand_error", "tag_not_seen") and v.passed is False
    r = honest_a_record("A1")
    r.turns[1].assistant[0] += "\nЕщё есть 20FAKE_999_PV."
    r.status = "timeout"
    assert check_run(r, card("A1")).hard == ("tag_not_seen",)


def test_percent_tag_key_and_quality_words():
    p = now_call("20FAKE_104_PV", 55.0, unit="%")
    v = check_run(rec("B1", answer="Сейчас 55 %.", tools=[p]), card("B1"), tag_units={"20FAKE_104_PV": "%"})
    assert v.passed is True
    v = check_run(rec("B1", answer="Сейчас 57 %.", tools=[p]), card("B1"), tag_units={"20FAKE_104_PV": "%"})
    assert v.hard == ("number_mismatch",)
    for answer in ("Данных за период нет — доля достоверных 0 %.", "Данных нет; пропусков 100 %.",
                   "Данных нет: отсутствует 100 % точек."):
        assert check_run(rec("C4", answer=answer, tools=ND), card("C4"), tag_units=U).passed is True, answer


def test_b4_sign_not_checked():                                                  # известное ограничение: знак разности
    for answer in ("Максимум за сутки 3,71 бар, сейчас 3,42 бар — выше на −0,29 бар.",
                   "Максимум за сутки 3,71 бар, сейчас 3,42 бар; отклонение текущего от максимума −0,29 бар.",
                   "Максимум за сутки 3,71 бар, сейчас 3,42 бар — ниже на 0,29 бар."):
        assert check_run(rec("B4", answer=answer, tools=B4), card("B4")).passed is True


def test_bridge_key_not_checked_when_not_full():                                 # мутант «мост: ключ даже при сбое»
    for mode in ("timeout", "stand"):
        r = honest_a_record("A1", bridge_answer="Максимум за сутки 3,71 бар")      # мост оборван до ключа
        r.bridge.tools.append(period_call("20FAKE_104_PV", high=3.71, period="last_24h"))
        if mode == "timeout":
            r.status = "timeout"
            want = ()
        else:
            r.turns[0].error = "APIError 503 pcbk_busy"
            want = ("stand_error",)
        v = check_run(r, card("A1"))
        assert v.fails == want and v.passed is None, (mode, v)


def test_scale_misstated_range_is_not_covered():                                 # чужое число между подписью и границей
    r = rec("B3", answer="Сейчас расход 41,7 т/ч (20FAKE_105_PV); шкала датчика 10–60 т/ч.", tools=[SCALE, B3_NOW])
    assert check_run(r, card("B3")).hard == ("number_mismatch",)


def test_service_fields_are_not_sources():                                       # cost_tag_days не источник даже точно
    v = check_run(rec("C4", answer="Данных нет, 0,333.", tools=ND), card("C4"), tag_units=U)
    assert "foreign_number" in v.fails
