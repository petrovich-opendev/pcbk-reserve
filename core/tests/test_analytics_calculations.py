"""Тесты разрешённых формул КДМ-1 (Task 3; план §Task 3/Этап 3; мастер-промпт §8, §11).

Эталоны — независимые арифметические выражения (`Decimal`/`Fraction`), не повторный вызов проверяемой функции
(план, Этап 3: «вычисления проверять независимыми рациональными/Decimal-эталонами, не вызовом той же функции»).
Данные — только выдуманные числа примеров мастер-промпта, тегов предприятия здесь нет."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from pcbk_core.analytics.calculations import (CalculationInputError, dry_grammage, dry_solid_flow,
                                              geometric_output, interval_union, known_refusal_reason,
                                              mentions_literal_date, mentions_time_range, plan_from_question,
                                              run_calculation)


# --- dry_solid_flow: мастер-промпт §8/§11 — 200 м3/ч, ро=1 т/м3, 4% по массе -> 8 т/ч ---

def test_dry_solid_flow_master_prompt_example():
    result = dry_solid_flow(Decimal("200"), Decimal("1"), Decimal("0.04"))
    independent = Fraction(200) * Fraction(1) * Fraction(4, 100)
    assert result.value == pytest.approx(float(independent))
    assert result.value == pytest.approx(8.0)
    assert result.unit == "т/ч"
    assert result.calculation_id == "dry_solid_flow"
    assert result.missing_basis == ()
    assert result.assumptions  # допущение про плотность/долю как заданные, не измеренный поток


@pytest.mark.parametrize(("flow", "density", "fraction"), [
    (Decimal("-1"), Decimal("1"), Decimal("0.04")),   # отрицательный расход
    (Decimal("200"), Decimal("0"), Decimal("0.04")),  # нулевая плотность — не то же самое, что отсутствие данных
    (Decimal("200"), Decimal("-1"), Decimal("0.04")), # отрицательная плотность
    (Decimal("200"), Decimal("1"), Decimal("1.5")),   # доля сухого вещества вне 0..1
    (Decimal("200"), Decimal("1"), Decimal("-0.1")),  # доля отрицательная
])
def test_dry_solid_flow_rejects_invalid_physical_input(flow, density, fraction):
    with pytest.raises(CalculationInputError):
        dry_solid_flow(flow, density, fraction)


@pytest.mark.parametrize("bad_value", [200.0, "200", 200])
def test_dry_solid_flow_rejects_non_decimal_input(bad_value):
    """Подмена Decimal на float/строку/int (попытка протащить нестрогий тип через вход формулы) — отказ, не
    неявное приведение (без eval, без доверия форме входа)."""
    with pytest.raises(CalculationInputError):
        dry_solid_flow(bad_value, Decimal("1"), Decimal("0.04"))


# --- geometric_output: мастер-промпт §11 — 4.15 м, 590 м/мин, 127.4 г/м2 -> 18.716334 т/ч (экран 61 — 19.46) ---

def test_geometric_output_master_prompt_example_18_716334():
    result = geometric_output(Decimal("4.15"), Decimal("590"), Decimal("127.4"))
    independent = Fraction(415, 100) * Fraction(590) * Fraction(1274, 10) * Fraction(60) / Fraction(1_000_000)
    assert result.value == pytest.approx(float(independent), abs=1e-9)
    assert result.value == pytest.approx(18.716334, abs=1e-6)
    # расхождение с показанием прибора (19.46 на экране 61) не подменяется этим расчётом и не диагноз сам по себе
    assert abs(result.value - 19.46) > 0.1
    # допущение объясняет расхождение, но явно НЕ утверждает диагноз неисправности как установленный факт
    assumptions_text = " ".join(result.assumptions)
    assert "не диагноз неисправности" in assumptions_text


def test_geometric_output_excel_c11_example_14_2578072():
    result = geometric_output(Decimal("4.2"), Decimal("611"), Decimal("92.6"))
    independent = Fraction(42, 10) * Fraction(611) * Fraction(926, 10) * Fraction(60) / Fraction(1_000_000)
    assert result.value == pytest.approx(float(independent), abs=1e-9)
    assert result.value == pytest.approx(14.2578072, abs=1e-6)


@pytest.mark.parametrize(("width", "speed", "basis"), [
    (Decimal("0"), Decimal("590"), Decimal("127.4")),     # нулевая ширина — ошибка, не ноль-результат молча
    (Decimal("-1"), Decimal("590"), Decimal("127.4")),
    (Decimal("4.15"), Decimal("-1"), Decimal("127.4")),   # отрицательная скорость
    (Decimal("4.15"), Decimal("590"), Decimal("0")),
])
def test_geometric_output_rejects_invalid_physical_input(width, speed, basis):
    with pytest.raises(CalculationInputError):
        geometric_output(width, speed, basis)


# --- dry_grammage: мастер-промпт §11 — 127.4 г/м2, доля сухого 0.91 (влажность 9% по влажной массе) -> 115.934 ---

def test_dry_grammage_master_prompt_example_115_934():
    result = dry_grammage(Decimal("127.4"), Decimal("0.91"))
    independent = Fraction(1274, 10) * Fraction(91, 100)
    assert result.value == pytest.approx(float(independent), abs=1e-9)
    assert result.value == pytest.approx(115.934, abs=1e-6)
    assert result.unit == "г/м2"


@pytest.mark.parametrize(("wet", "fraction"), [
    (Decimal("0"), Decimal("0.91")),
    (Decimal("-1"), Decimal("0.91")),
    (Decimal("127.4"), Decimal("1.5")),
    (Decimal("127.4"), Decimal("-0.1")),
])
def test_dry_grammage_rejects_invalid_physical_input(wet, fraction):
    with pytest.raises(CalculationInputError):
        dry_grammage(wet, fraction)


# --- run_calculation: диспетчер — единицы, отсутствующая база, запрет перевода чужого физического рода ---

def test_run_calculation_dry_solid_flow_dispatches_correctly():
    result = run_calculation(
        "dry_solid_flow",
        {"volumetric_flow_m3_h": Decimal("200"), "density_t_m3": Decimal("1"),
         "dry_content_fraction": Decimal("0.04")},
        {"volumetric_flow_m3_h": "м3/ч", "density_t_m3": "т/м3", "dry_content_fraction": "доля"},
    )
    assert result.value == pytest.approx(8.0)


def test_run_calculation_missing_basis_does_not_guess():
    """Т-03 плана: расход и концентрация без плотности — не отвечать 8 т/ч автоматически, явно указать, какой
    базы не хватает (мастер-промпт §11: «ошибка, пропуск и ноль различаются»)."""
    result = run_calculation(
        "dry_solid_flow",
        {"volumetric_flow_m3_h": Decimal("200"), "dry_content_fraction": Decimal("0.04")},
        {"volumetric_flow_m3_h": "м3/ч", "dry_content_fraction": "доля"},
    )
    assert result.value is None
    assert "density_t_m3" in result.missing_basis


def test_run_calculation_rejects_wrong_unit_kind_no_silent_conversion():
    """Мастер-промпт §8/§11: «80 кН/м нельзя напрямую переводить в бары» — линейная нагрузка пресса (кН/м) не
    того физического рода, что плотность/давление/масса 1 м²; диспетчер отказывает, а не молча переводит."""
    with pytest.raises(CalculationInputError):
        run_calculation(
            "dry_solid_flow",
            {"volumetric_flow_m3_h": Decimal("200"), "density_t_m3": Decimal("80"),
             "dry_content_fraction": Decimal("0.04")},
            {"volumetric_flow_m3_h": "м3/ч", "density_t_m3": "кН/м", "dry_content_fraction": "доля"},
        )


def test_run_calculation_rejects_unknown_unit_string():
    with pytest.raises(CalculationInputError):
        run_calculation(
            "geometric_output",
            {"width_m": Decimal("4.15"), "speed_m_min": Decimal("590"), "basis_weight_g_m2": Decimal("127.4")},
            {"width_m": "см", "speed_m_min": "м/мин", "basis_weight_g_m2": "г/м2"},
        )


def test_run_calculation_rejects_non_decimal_input_value():
    """Инъекция через вход: подмена числа строкой/float на границе вызова (не через формулу напрямую — через
    сам диспетчер, которым пользуется `validator.py`/`service.py`)."""
    with pytest.raises(CalculationInputError):
        run_calculation(
            "dry_solid_flow",
            {"volumetric_flow_m3_h": "200", "density_t_m3": Decimal("1"), "dry_content_fraction": Decimal("0.04")},
            {"volumetric_flow_m3_h": "м3/ч", "density_t_m3": "т/м3", "dry_content_fraction": "доля"},
        )


def test_run_calculation_rejects_unknown_calculation_id():
    with pytest.raises(CalculationInputError):
        run_calculation("eval_formula", {}, {})  # type: ignore[arg-type]


def test_run_calculation_refuses_interval_union_through_decimal_dispatcher():
    """`interval_union` не имеет Decimal-входов — диспетчер отказывает явно, не падает по форме входа и не
    угадывает перевод формы вызова."""
    with pytest.raises(CalculationInputError):
        run_calculation("interval_union", {}, {})


# --- interval_union: объединение, не сумма длительностей (мастер-промпт §8) ---

def test_interval_union_merges_overlapping_intervals_not_sum_of_durations():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    intervals = [
        (base, base + timedelta(hours=2)),
        (base + timedelta(hours=1), base + timedelta(hours=3)),   # пересекается с первым
        (base + timedelta(hours=10), base + timedelta(hours=11)), # отдельный, не пересекается
    ]
    merged = interval_union(intervals)
    assert merged == (
        (base, base + timedelta(hours=3)),
        (base + timedelta(hours=10), base + timedelta(hours=11)),
    )
    sum_of_raw_durations = sum((end - start for start, end in intervals), timedelta())
    sum_of_merged_durations = sum((end - start for start, end in merged), timedelta())
    assert sum_of_merged_durations < sum_of_raw_durations  # пересечение не считается дважды


def test_interval_union_merges_touching_intervals():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    intervals = [(base, base + timedelta(hours=1)), (base + timedelta(hours=1), base + timedelta(hours=2))]
    assert interval_union(intervals) == ((base, base + timedelta(hours=2)),)


def test_interval_union_empty_input():
    assert interval_union([]) == ()


def test_interval_union_rejects_mixed_naive_and_aware_datetimes():
    """Время браузера/историана не смешивается без установленной базы (мастер-промпт §8)."""
    naive = datetime(2026, 1, 1)
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(CalculationInputError):
        interval_union([(naive, naive + timedelta(hours=1)), (aware, aware + timedelta(hours=1))])


def test_interval_union_rejects_end_before_start():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(CalculationInputError):
        interval_union([(base, base - timedelta(hours=1))])


# --- plan_from_question: подключение расчётов к живому пути вопроса (итоговая правка F1, п.1) ---
# эталоны — независимые Decimal/Fraction-выражения или прямая проверка полей плана, не повторный вызов той
# же функции (план, «Особый фокус проверки»).

def test_plan_from_question_dry_solid_flow_t04():
    """T-04: «200 м³/ч, ρ=1 т/м³, 4% по массе» → план dry_solid_flow со всеми входами, run_calculation даёт 8."""
    plan = plan_from_question("Явный учебный набор: 200 м³/ч, ρ=1 т/м³, 4% по массе. Сколько сухого вещества?")
    assert plan is not None
    assert plan.calculation_id == "dry_solid_flow"
    assert plan.refusal_reason is None
    assert plan.inputs == {"volumetric_flow_m3_h": Decimal("200"), "density_t_m3": Decimal("1"),
                           "dry_content_fraction": Decimal("4") / Decimal(100)}
    result = run_calculation(plan.calculation_id, plan.inputs, plan.units)
    independent = Fraction(200) * Fraction(1) * Fraction(4, 100)
    assert result.value == pytest.approx(float(independent))
    assert result.value == pytest.approx(8.0)
    assert result.missing_basis == ()


@pytest.mark.parametrize("phrase", ["кубов в час", "кубометров в час", "куб/ч"])
def test_plan_from_question_cubic_meters_per_hour_words_recognized(phrase):
    """Правка Task 3 (Д17, план §Task 3/Этап 3, «мелкие хвосты распознавания»): словесные формы объёмного
    расхода («кубов в час»/«кубометров в час»/«куб/ч») распознаются тем же `_FLOW_RE`, что «м³/ч» — та же
    единица, тот же вход формулы."""
    plan = plan_from_question(f"Расход 200 {phrase}, ρ=1 т/м³, 4% по массе. Сколько сухого вещества?")
    assert plan is not None
    assert plan.calculation_id == "dry_solid_flow"
    assert plan.inputs.get("volumetric_flow_m3_h") == Decimal("200")
    assert plan.units.get("volumetric_flow_m3_h") == "м3/ч"


def test_plan_from_question_cubic_meters_per_hour_dry_solid_flow_example():
    """Мастер-промпт §8/§11, словесная форма: «200 кубов в час, ρ=1 т/м³, 4 % по массе» → 8 т/ч сухого вещества
    — та же формула и то же число, что у T-04 (литерал «м³/ч»), независимый Fraction-эталон."""
    plan = plan_from_question("200 кубов в час, ρ=1 т/м³, 4 % по массе. Сколько сухого вещества?")
    assert plan is not None
    assert plan.calculation_id == "dry_solid_flow"
    assert plan.refusal_reason is None
    result = run_calculation(plan.calculation_id, plan.inputs, plan.units)
    independent = Fraction(200) * Fraction(1) * Fraction(4, 100)
    assert result.value == pytest.approx(float(independent))
    assert result.value == pytest.approx(8.0)
    assert result.missing_basis == ()


def test_plan_from_question_t03_missing_density_no_silent_8():
    """T-03: «Расход 200, концентрация 4 — сколько сухого волокна?» — без единицы расхода и без базы процента
    (и без самого «%») план распознаёт формулу, но НЕ выдумывает входы: result.value остаётся None, не 8."""
    plan = plan_from_question("Расход 200, концентрация 4 — сколько сухого волокна?")
    assert plan is not None
    assert plan.calculation_id == "dry_solid_flow"
    assert plan.refusal_reason is None
    assert plan.inputs == {}
    result = run_calculation(plan.calculation_id, plan.inputs, plan.units)
    assert result.value is None
    assert set(result.missing_basis) == {"volumetric_flow_m3_h", "density_t_m3", "dry_content_fraction"}


def test_plan_from_question_zero_concentration_is_explicit_zero_not_missing():
    """Дополнение контроллера (а): «0 % по массе» — явный ноль, не пропуск базы (мастер-промпт §8: «ошибка,
    пропуск и ноль различаются»); run_calculation должен вернуть ЧИСЛО 0, не missing_basis."""
    plan = plan_from_question("200 м³/ч, ρ=1 т/м³, 0 % по массе. Сколько сухого вещества?")
    assert plan is not None
    assert plan.inputs["dry_content_fraction"] == Decimal("0")
    assert "dry_content_fraction" in plan.inputs           # распознанный ноль НЕ выпал как «не найдено»
    result = run_calculation(plan.calculation_id, plan.inputs, plan.units)
    assert result.missing_basis == ()
    assert result.value == pytest.approx(0.0)


def test_plan_from_question_geometric_output_example():
    """Мастер-промпт §11: ширина 4,15 м, скорость 590 м/мин, масса 1 м² 127,4 г/м² → 18,716334 т/ч."""
    plan = plan_from_question("Ширина полотна 4,15 м, скорость машины 590 м/мин, масса 1 м² 127,4 г/м². "
                              "Посчитай производительность.")
    assert plan is not None
    assert plan.calculation_id == "geometric_output"
    assert plan.inputs == {"width_m": Decimal("4.15"), "speed_m_min": Decimal("590"),
                           "basis_weight_g_m2": Decimal("127.4")}
    result = run_calculation(plan.calculation_id, plan.inputs, plan.units)
    independent = Fraction(415, 100) * Fraction(590) * Fraction(1274, 10) * Fraction(60) / Fraction(1_000_000)
    assert result.value == pytest.approx(float(independent), abs=1e-9)
    assert result.value == pytest.approx(18.716334, abs=1e-6)


def test_plan_from_question_dry_grammage_explicit_dryness():
    """Мастер-промпт §11: масса 1 м² 127,4 г/м², сухость 91 % → 115,934 г/м² по сухому веществу."""
    plan = plan_from_question("Масса 1 м² 127,4 г/м², сухость 91 %. Посчитай сухую массу 1 м².")
    assert plan is not None
    assert plan.calculation_id == "dry_grammage"
    assert plan.inputs == {"wet_basis_weight_g_m2": Decimal("127.4"),
                           "dry_content_fraction": Decimal("91") / Decimal(100)}
    result = run_calculation(plan.calculation_id, plan.inputs, plan.units)
    independent = Fraction(1274, 10) * Fraction(91, 100)
    assert result.value == pytest.approx(float(independent), abs=1e-9)
    assert result.value == pytest.approx(115.934, abs=1e-6)


def test_plan_from_question_dry_grammage_moisture_only_does_not_guess_basis():
    """Дана только влажность (не сухость) — база НЕ угадывается как «1 − влажность» (мастер-промпт §11):
    доля сухого вещества остаётся непойманной, run_calculation вернёт missing_basis."""
    plan = plan_from_question("Масса 1 м² 127,4 г/м², влажность 9 %. Сколько это по сухому веществу?")
    assert plan is not None
    assert plan.calculation_id == "dry_grammage"
    assert "dry_content_fraction" not in plan.inputs
    assert plan.inputs.get("wet_basis_weight_g_m2") == Decimal("127.4")
    result = run_calculation(plan.calculation_id, plan.inputs, plan.units)
    assert result.value is None
    assert "dry_content_fraction" in result.missing_basis


def test_plan_from_question_linear_load_to_pressure_refused_t05():
    """T-05: «Давление пресса 80 кН/м, переведи в бары» — линейная нагрузка не переводится в давление напрямую;
    план — отказ, без calculation_id/run_calculation."""
    plan = plan_from_question("Давление пресса 80 кН/м, переведи в бары.")
    assert plan is not None
    assert plan.refusal_reason is not None
    assert "не переводится в давление" in plan.refusal_reason
    assert "ширины зоны контакта" in plan.refusal_reason


def test_plan_from_question_interval_union_overlapping_merges_not_sums():
    """Два пересекающихся интервала → объединение, не сумма (мастер-промпт §8)."""
    plan = plan_from_question("Обрыв был 10:00–11:30, простой 11:00–12:00 — сложить суммарное время простоя.")
    assert plan is not None
    assert plan.calculation_id == "interval_union"
    assert len(plan.intervals) == 2
    merged = interval_union(plan.intervals)
    assert len(merged) == 1                     # пересечение объединено в один интервал, не осталось двух
    total = sum((end - start for start, end in merged), timedelta())
    assert total == timedelta(hours=2)           # 10:00-12:00, НЕ 1,5 + 1 = 2,5 ч суммой исходных длительностей
    sum_of_raw = sum((end - start for start, end in plan.intervals), timedelta())
    assert total < sum_of_raw


def test_plan_from_question_interval_union_no_times_t32():
    """T-32: «Сложить времена обрыва, простоя...» — ни одного literal HH:MM, план всё равно про interval_union
    (объединение периодов), но с пустыми intervals — вызывающий (service.py) должен попросить начало/конец,
    не угадать числа."""
    plan = plan_from_question("Сложить времена обрыва, простоя, некондиционного выпуска и смены сорта")
    assert plan is not None
    assert plan.calculation_id == "interval_union"
    assert plan.intervals == ()


def test_plan_from_question_gauge_to_absolute_pressure_refused():
    """Дополнение контроллера (б): перевод избыточного/манометрического давления в абсолютное — не публикует
    число, нужна база (местное атмосферное давление/способ измерения)."""
    plan = plan_from_question("Переведи манометрическое давление 3,5 бар в абсолютное.")
    assert plan is not None
    assert plan.refusal_reason is not None
    assert "абсолютное" in plan.refusal_reason
    assert "атмосферное давление" in plan.refusal_reason


def test_plan_from_question_gauge_to_absolute_pressure_refused_without_gauge_word():
    """Правка контроллера, раунд 2, п.3 (H-34): «Переведи 3,5 бар в абсолютное давление» не называет исходную
    базу словом «избыточное»/«манометрическое» явно — раньше условие требовало ОБА признака и пропускало эту
    форму целиком (вопрос уходил в обычный расчётный путь без структурного отказа)."""
    plan = plan_from_question("Переведи 3,5 бар в абсолютное давление")
    assert plan is not None
    assert plan.refusal_reason is not None
    assert "атмосферное давление" in plan.refusal_reason


def test_known_refusal_reason_absolute_pressure_conversion_without_unit_word():
    """Слово «давление» без конкретного числового значения единицы — тоже признак (бар/кПа/МПа — не
    единственный способ назвать давление)."""
    assert known_refusal_reason("Переведи давление в абсолютное") is not None


def test_known_refusal_reason_absolute_pressure_question_without_conversion_verb_not_refused():
    """Контроль (не регрессия T-06): «Вакуум -300 мбар — это отрицательное абсолютное давление?» — ВОПРОС о
    знаке/базе, не просьба ПЕРЕВЕСТИ — не структурный отказ (обычная неоднозначность базы вакуума остаётся)."""
    assert known_refusal_reason("Вакуум -300 мбар — это отрицательное абсолютное давление?") is None


def test_plan_from_question_cov_valmet_screen_refused():
    """Дополнение контроллера (в): расчёт COV по данным экрана Valmet — алгоритма в корпусе нет, общий
    статистический коэффициент вариации — другое понятие."""
    plan = plan_from_question("Рассчитай COV по данным экрана Valmet за смену.")
    assert plan is not None
    assert plan.refusal_reason is not None
    assert "COV Valmet IQ" in plan.refusal_reason
    assert "коэффициент вариации" in plan.refusal_reason


def test_plan_from_question_external_benchmark_comparison_refused():
    """Дополнение контроллера (г): сравнение с другими заводами/странами — не расчёт, в корпусе нет данных для
    межзаводского сравнения, статистика не выдумывается."""
    plan = plan_from_question("Сравни производительность КДМ-1 с аналогичными машинами в Китае.")
    assert plan is not None
    assert plan.refusal_reason is not None
    assert "межзаводского" in plan.refusal_reason


def test_plan_from_question_returns_none_for_unrecognized_text():
    plan = plan_from_question("Это обычный текст без единиц, формул и структурных отказов вовсе.")
    assert plan is None


def test_known_refusal_reason_none_for_unrelated_calculation_question():
    """Обычный расчётный вопрос без структурного отказа — known_refusal_reason возвращает None, не мешает
    requires_clarification запрашивать единицу/базу как обычно."""
    assert known_refusal_reason("200 м³/ч, ρ=1 т/м³, 4% по массе") is None


# --- Правка F1, раунд 1 (контроллер, зонд на 440393c) ---

# п.1 (Critical): признаки Decimal-формул — приоритет над interval_union; буквальный ЧЧ:ММ-ЧЧ:ММ в тексте
# расчётного вопроса не должен перехватывать план целиком.

def test_plan_from_question_decimal_signal_beats_literal_time_range():
    """«Расход 200 м3/ч … за смену 08:00-20:00 …» — раньше становилось interval_union «12 ч»
    (state=completed), хотя вопрос — про расход. Decimal-сигнал dry_solid_flow должен победить."""
    plan = plan_from_question("Расход 200 м3/ч, за смену 08:00-20:00. Сколько сухого вещества?")
    assert plan is not None
    assert plan.calculation_id == "dry_solid_flow"
    assert plan.refusal_reason is None
    assert plan.intervals == ()
    assert plan.inputs.get("volumetric_flow_m3_h") == Decimal("200")
    result = run_calculation(plan.calculation_id, plan.inputs, plan.units)
    assert result.value is None                                 # density/dry_content_fraction не даны
    assert "density_t_m3" in result.missing_basis


def test_plan_from_question_time_range_without_decimal_signal_still_interval_union():
    """Без Decimal-сигнала формулы буквальный интервал всё ещё распознаётся (поведение не регрессировало)."""
    plan = plan_from_question("Обрыв был 10:00–11:30, простой 11:00–12:00 — сложить суммарное время простоя.")
    assert plan is not None
    assert plan.calculation_id == "interval_union"
    assert len(plan.intervals) == 2


def test_plan_from_question_interval_cue_without_decimal_signal_t32():
    """T-32 без literal времени и без Decimal-сигналов — всё ещё interval_union с пустыми intervals."""
    plan = plan_from_question("Сложить времена обрыва, простоя, некондиционного выпуска и смены сорта")
    assert plan is not None
    assert plan.calculation_id == "interval_union"
    assert plan.intervals == ()


def test_plan_from_question_decimal_signal_without_interval_cue_is_not_interval_union():
    """Чисто Decimal-вопрос без буквального времени и без маркера объединения — не interval_union (контроль)."""
    plan = plan_from_question("Ширина полотна 4,15 м, скорость машины 590 м/мин, масса 1 м² 127,4 г/м².")
    assert plan is not None
    assert plan.calculation_id == "geometric_output"


# --- правка контроллера, раунд 1 (H-29 продолжение): mentions_time_range/mentions_literal_date — новые формы ---

def test_mentions_time_range_recognizes_word_separated_range_with_leading_s():
    """«с ЧЧ:ММ до ЧЧ:ММ» — словесный разделитель «до», необязательное «с » перед первым временем."""
    assert mentions_time_range("за 13.10 с 08:00 до 20:00 по Перми") is True
    assert mentions_time_range("08:00 до 20:00") is True
    assert mentions_time_range("08:00-20:00") is True                # старая форма не регрессировала


def test_mentions_literal_date_recognizes_day_month_and_day_month_year():
    assert mentions_literal_date("за 13.10") is True
    assert mentions_literal_date("13.10.2026 08:00-20:00") is True


def test_mentions_literal_date_rejects_decimal_numbers_with_implausible_month():
    """«4.15»/«127.4» (масса/ширина) — не дата: «месяц» вышел бы за пределы 1–12."""
    assert mentions_literal_date("ширина 4,15 м") is False
    assert mentions_literal_date("масса 1 м² 127.4 г/м²") is False


def test_mentions_literal_date_rejects_text_without_any_date_like_token():
    assert mentions_literal_date("за последнюю смену") is False


def test_plan_from_question_decimal_priority_preserved_with_word_separated_time_range():
    """Правка контроллера, раунд 1: расширение `_TIME_RANGE_RE` (словоразделитель «до», «с» перед первым
    временем) не должно сломать приоритет Decimal-формул над `interval_union` (F1, раунд 1) — decimal-сигнал
    формулы перехватывает план, даже когда в тексте есть распознаваемый интервал словами."""
    plan = plan_from_question("Расход 200 м3/ч, за смену с 08:00 до 20:00. Сколько сухого вещества?")
    assert plan is not None
    assert plan.calculation_id == "dry_solid_flow"
    assert plan.refusal_reason is None


def test_plan_from_question_word_separated_time_range_without_decimal_signal_still_interval_union():
    """Без Decimal-сигнала формулы словесный интервал («с … до …») тоже распознаётся как interval_union, не
    только дефисная форма."""
    plan = plan_from_question("Обрыв был с 10:00 до 11:30, простой с 11:00 до 12:00 — сложить суммарное время.")
    assert plan is not None
    assert plan.calculation_id == "interval_union"
    assert len(plan.intervals) == 2


# п.2 (Critical): несколько РАЗНЫХ значений одного рода в тексте — отказ, не выбор первого по порядку.

def test_plan_from_question_ambiguous_basis_weight_result_label_does_not_filter():
    """«получили 115,9 г/м2 сухого вещества, а масса 1 м2 127,4 г/м2, сухость 91%» — первое число помечено как
    РЕЗУЛЬТАТ («получили … сухого вещества»), но это не используется для фильтрации — отказ на оба значения,
    не молчаливый выбор 127,4 (и тем более не умножение 115,9×0,91)."""
    plan = plan_from_question(
        "Получили 115,9 г/м2 сухого вещества, а масса 1 м2 127,4 г/м2, сухость 91%.")
    assert plan is not None
    assert plan.refusal_reason is not None
    assert "массы 1 м²" in plan.refusal_reason
    assert "115,9" in plan.refusal_reason
    assert "127,4" in plan.refusal_reason
    assert "уточните" in plan.refusal_reason


def test_plan_from_question_ambiguous_speed_two_values():
    """«скорость 590 м/мин, а по паспорту 611 м/мин» — две разные скорости, отказ, не первая по порядку."""
    plan = plan_from_question(
        "Ширина 4,15 м, скорость 590 м/мин, а по паспорту 611 м/мин, масса 1 м² 127,4 г/м².")
    assert plan is not None
    assert plan.calculation_id == "geometric_output"
    assert plan.refusal_reason is not None
    assert "скорости" in plan.refusal_reason
    assert "590" in plan.refusal_reason
    assert "611" in plan.refusal_reason


def test_plan_from_question_ambiguous_width_two_values():
    plan = plan_from_question("Ширина 4,15 м, а по паспорту 4,2 м, скорость 590 м/мин.")
    assert plan is not None
    assert plan.refusal_reason is not None
    assert "ширины" in plan.refusal_reason
    assert "4,15" in plan.refusal_reason
    assert "4,2" in plan.refusal_reason


def test_plan_from_question_ambiguous_flow_two_values():
    plan = plan_from_question("Расход 200 м3/ч, а по счётчику 210 м3/ч, плотность 1 т/м3, 4% по массе.")
    assert plan is not None
    assert plan.calculation_id == "dry_solid_flow"
    assert plan.refusal_reason is not None
    assert "расхода" in plan.refusal_reason
    assert "200" in plan.refusal_reason
    assert "210" in plan.refusal_reason


def test_plan_from_question_same_value_repeated_is_not_ambiguous():
    """Повтор ОДНОГО И ТОГО ЖЕ числа (не два разных) — не неоднозначность, обычный расчёт проходит."""
    plan = plan_from_question("Масса 1 м² 127,4 г/м² (повторю: 127,4 г/м²), сухость 91 %.")
    assert plan is not None
    assert plan.refusal_reason is None
    assert plan.calculation_id == "dry_grammage"
    assert plan.inputs.get("wet_basis_weight_g_m2") == Decimal("127.4")


# п.3 (Important): знак минус захватывается, не теряется; отрицательное значение — ошибка входа, не молчаливое
# положительное.

def test_plan_from_question_captures_negative_sign_flow():
    plan = plan_from_question("Расход -200 м3/ч, плотность 1 т/м3, 4% по массе. Сколько сухого вещества?")
    assert plan is not None
    assert plan.inputs.get("volumetric_flow_m3_h") == Decimal("-200")
    with pytest.raises(CalculationInputError):
        run_calculation(plan.calculation_id, plan.inputs, plan.units)


def test_plan_from_question_captures_negative_sign_width_after_label():
    """Минус сразу после метки «ширина» (до числа) — не съедается разрывом метка→число. Все три входа
    geometric_output даны — диспетчер реально вызывает формулу (не отдаёт missing_basis раньше проверки
    знака)."""
    plan = plan_from_question("Ширина -4,15 м, скорость 590 м/мин, масса 1 м² 127,4 г/м².")
    assert plan is not None
    assert plan.inputs.get("width_m") == Decimal("-4.15")
    with pytest.raises(CalculationInputError):
        run_calculation(plan.calculation_id, plan.inputs, plan.units)


def test_plan_from_question_captures_negative_sign_dry_content_explicit():
    plan = plan_from_question("Масса 1 м² 127,4 г/м², сухость -91 %.")
    assert plan is not None
    assert plan.inputs.get("dry_content_fraction") == Decimal("-91") / Decimal(100)
    with pytest.raises(CalculationInputError):
        run_calculation(plan.calculation_id, plan.inputs, plan.units)


def test_plan_from_question_positive_number_after_minus_like_dash_not_misread():
    """Длинное тире/дефис как ПУНКТУАЦИЯ (не знак минус) перед числом не делает число отрицательным — обычный
    положительный расход."""
    plan = plan_from_question("Расход — 200 м3/ч, плотность 1 т/м3, 4% по массе.")
    assert plan is not None
    assert plan.inputs.get("volumetric_flow_m3_h") == Decimal("200")
