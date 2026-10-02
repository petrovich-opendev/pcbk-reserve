"""Разбор чисел и одно правило чисел (Д8-R72: I-н4, I-р3-8, I-р3-9, I-р4-4, m-р3-6, m-р4-10)."""
from decimal import Decimal

import pytest

from pcbk_bench.numbers import NON_MEASURE, VALUE_WORDS, Foreign, Shown, foreign_numbers, matches, parse_numbers


@pytest.mark.parametrize("text,want", [
    ("12,5 л/ч", [("12.5", 1)]), ("1 250,4 т", [("1250.4", 1)]), ("−3 °C", [("-3", 0)]),
    ("128.4", [("128.4", 1)]), ("сейчас 3,42 бар (качество хорошее)", [("3.42", 2)]),
])
def test_parse_numbers_russian_formats(text, want):                              # Review Focus 4
    assert [(str(s.value), s.decimals) for s in parse_numbers(text)] == want


def test_times_dates_units_tags_not_numbers():
    text = "в 11:59 01.10, 03.03.2005, 2026-10-01; м3/ч, м³, кгс/см2; теги 20FAKE_101_PV и 20FAKECP001_PV_X"
    assert parse_numbers(text) == []


def test_date_vs_decimal_with_unit():                                            # m-р3-6
    assert [(str(s.value), s.decimals) for s in parse_numbers("давление 12.05 бар")] == [("12.05", 2)]
    assert parse_numbers("было 12.05, с утра") == []


def test_matches_rounds_to_shown_digits():
    assert matches(3.4217, "давление 3,42 бар") and matches(127.94, "≈ 128 °C")
    assert not matches(3.4217, "давление 3,5 бар")


@pytest.mark.parametrize("text", ["за последние 24 ч", "смена — 8 часов", "выше на 0,29 бар (≈8,6 %)",
                                  "по 3 тегам", "смена 2", "достоверных 0 %", "это 2-й узел"])
def test_non_measure_numbers_allowed(text):                                      # I-н4
    assert all(f.kind == "allowed" for f in foreign_numbers(text, [3.42, 3.71, 0.29], tag_units=["бар"]))


def test_foreign_numbers_classes():                                              # I-н4
    kinds = lambda t, units=("°C",): [f.kind for f in foreign_numbers(t, [127.9], tag_units=units)]
    assert kinds("среднее 128,9 °C") == ["hard"]
    assert kinds("было около 130") == ["hard"]                                        # рядом со словом значения
    assert kinds("примерно 17 штук") == ["other"]
    assert kinds("уровень 41,2 %", units=("%",)) == ["hard"]                           # процент с единицей тега


def test_value_word_window_stops():                                              # I-р3-9: окно — до «.!?;»
    kinds = lambda t: [f.kind for f in foreign_numbers(t, [3.42], tag_units=["бар"])]
    assert kinds("Сейчас 3,42 бар; датчиков на линии 17") == ["other"]
    assert kinds("Сейчас 3,42 бар. На линии 17") == ["other"]
    assert kinds("Сейчас на линии 17") == ["hard"]                                     # тот же отрезок, без числа между


def test_c2_c4_percent_with_value_word_hard():                                   # I-р3-8
    assert [f.kind for f in foreign_numbers("уровень сейчас 55 %", [], strict_value_words=True)] == ["hard"]
    assert [f.kind for f in foreign_numbers("уровень сейчас 55 %", [])] == ["allowed"]   # вне C2/C4 — процент


def test_value_words_word_start():                                               # m-р4-10
    kinds = lambda t: [f.kind for f in foreign_numbers(t, [], tag_units=["бар"])]
    for text in ("номинальное 17", "сравнение 17", "распределено равномерно 17", "прибыло 17"):
        assert kinds(text) == ["other"], text
    assert kinds("минимум 17") == ["hard"] and kinds("было 17") == ["hard"]


# --- сверх брифа: ложные провалы честной модели и обходы правила ---

@pytest.mark.parametrize("text,want", [
    ("3-4 бар", [("3", 0), ("4", 0)]), ("85–95 °C", [("85", 0), ("95", 0)]), ("от -5 до 5", [("-5", 0), ("5", 0)]),
    ("+0,29 бар", [("0.29", 2)]), ("1\u00a0250,4", [("1250.4", 1)]), ("3,40", [("3.40", 2)]),
    ("БДМ-2 и H2O", []), ("bench-a1b2c3, ses_A1A1A1", []), ("21FAKE0042", []),
    ("1. Сейчас 3,42 бар\n2) мин 3,1", [("3.42", 2), ("3.1", 1)]),             # номера пунктов списка — не числа
    ("3 марта 2005 года, 08:00–16:00 (UTC+05:00), по МСК+2", []),
    ("2026-10-02T11:59:40+05:00 и 11:59:40", []), ("3,42бар", [("3.42", 2)]),
])
def test_parse_more_formats(text, want):
    assert [(str(s.value), s.decimals) for s in parse_numbers(text)] == want


def test_shown_positions():
    text = "Сейчас 3,42 бар"
    (s,) = parse_numbers(text)
    assert s == Shown(Decimal("3.42"), 2, 7, 11) and text[s.start:s.end] == "3,42"


def test_matches_half_unit_both_ways_and_sign():
    assert matches(3.425, "3,42") and matches(3.425, "3,43") and not matches(3.4289, "3,42")   # округление, не срез
    assert matches(-3.0, "−3 °C") and not matches(3.0, "−3 °C")
    assert not matches(3.42, "в 11:59") and not matches(2005, "03.03.2005")


@pytest.mark.parametrize("text", [
    "Данные обновлены 20 с назад", "за 5 мин", "за сутки", "за 2 суток", "за 7 дней", "1-я смена", "смена №2",
    "по 4 тегам, 2 вызова", "3 раза", "в 1,08 раза выше", "доля достоверных 97,5 %", "качество 100 %",
    "узел №3", "за 3 марта 2005 года", "в 2026 году",
])
def test_more_non_measure_allowed(text):                                          # честные числа — не провал
    assert all(f.kind == "allowed" for f in foreign_numbers(text, [3.42], tag_units=["бар", "°C"],
                                                            strict_value_words=True))


@pytest.mark.parametrize("text", [
    "Давление 3,5 с небольшим", "Выросло до 3,71 с 3,42 бар", "Сейчас 3,5 с утра",
])
def test_seconds_needs_end_not_preposition(text):                                # «с» — предлог, а не секунды
    assert any(f.kind != "allowed" for f in foreign_numbers(text, [3.42], tag_units=["бар"]))


def test_value_word_after_number_and_abbreviations():
    kinds = lambda t: [f.kind for f in foreign_numbers(t, [127.9], tag_units=[])]
    assert kinds("128,9 — это среднее") == ["hard"]                                  # слово после числа
    assert kinds("макс. 131,6") == ["hard"] and kinds("ср. 128,9") == ["other"]        # «макс.» — не конец отрезка
    assert kinds("Среднее за 8 часов — 128,9") == ["allowed", "hard"]                 # длительность — не «другое число»
    assert kinds("Среднее 127,9, датчиков 17") == ["other"]                            # между — число из вывода


def test_units_variants():
    k = lambda t, u: [f.kind for f in foreign_numbers(t, [], tag_units=u)]
    assert k("примерно 85 °С", ["°C"]) == ["hard"]                                    # кириллическая С
    assert k("примерно 85 градусов", ["°C"]) == ["other"]
    assert k("примерно 40 м³/ч", ["м3/ч"]) == ["hard"] and k("примерно 4 мешка", ["м"]) == ["other"]
    assert k("примерно 41,7 т/ч", ["т/ч"]) == ["hard"] and k("примерно 41,7 т", ["т/ч"]) == ["other"]
    assert k("примерно 41,7 т/ч", ["", "т/ч"]) == ["hard"]                            # пустая единица — пропуск


def test_sources_and_also():
    assert foreign_numbers("Сейчас 3,4 бар", [3.4217], tag_units=["бар"]) == []       # округлено — из вывода
    assert all(f.kind == "allowed" for f in foreign_numbers("с 8 до 16 часов 03.03.2005", [],
                                                            also="2005-03-03T08:00:00 2005-03-03T16:00:00",
                                                            strict_value_words=True))
    (f,) = foreign_numbers("Сейчас 20 бар", [], also="тег 20FAKE_104_PV", tag_units=["бар"])
    assert isinstance(f, Foreign) and f.kind == "hard"                                 # цифры имени тега — не источник


def test_exports():
    assert VALUE_WORDS.flags & 2                                                         # re.I (m6)
    assert set(NON_MEASURE) >= {"duration", "ordinal", "shift", "count", "percent", "quality_share"}


def test_clock_and_quality_after_percent():                               # честные «с 6 до 14 часов», «0 % достоверных»
    kinds = lambda t, strict=False: [f.kind for f in foreign_numbers(t, [], tag_units=["бар"],
                                                                     strict_value_words=strict)]
    assert kinds("Среднее за смену с 6 до 14 часов") == ["allowed", "allowed"]
    assert kinds("Сейчас выросло с 3 до 4 бар") == ["hard", "hard"]                 # без слова часа — не часы
    assert kinds("Среднее посчитать нельзя: данных нет (0 % достоверных)", strict=True) == ["allowed"]
    assert kinds("Среднее посчитать нельзя: 0 % данных", strict=True) == ["hard"]


def test_quality_share_beats_percent_unit():                                    # доля достоверности решает при «%» тега
    kinds = lambda t: [f.kind for f in foreign_numbers(t, [41.2], tag_units=["%"])]
    assert kinds("уровень 41,2 %, доля достоверных 97,5 %") == ["allowed"]
    assert kinds("уровень 41,2 %, а было 97,5 %") == ["hard"]


def test_label_and_shift_need_small_int():                                   # «№7» — номер; «за смену 128,9» — значение
    kinds = lambda t: [f.kind for f in foreign_numbers(t, [], tag_units=[])]
    assert kinds("на узле №7 всё спокойно") == ["allowed"] and kinds("смена 3 закрыта") == ["allowed"]
    assert kinds("за смену 128,9") == ["other"] and kinds("№ 128,9") == ["other"]
