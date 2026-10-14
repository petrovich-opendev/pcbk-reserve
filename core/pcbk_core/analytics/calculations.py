"""Разрешённые формулы КДМ-1 (Task 3; план §Task 3/Этап 3; мастер-промпт §8, §11).

Именованные формулы — ровно четыре, по `models.CalculationId`/`contracts/llm-selection.schema.json`:
`dry_solid_flow`, `geometric_output`, `dry_grammage`, `interval_union`. Отдельный модуль от старых
калькуляторов страницы (`gateway/calc_api.py`, `data/service.py` — не меняются, Global Constraints): те читают
историан через `DataService`, этот модуль — чистая арифметика над входами, которые уже разобрал `preanalysis.py`
(или подтвердил технолог при уточнении). Без `eval`: каждая формула — отдельная функция с явными входами и
единицами, допущения — в `CalculationResult.assumptions`, а не в тексте.

Independent-эталоны проверки (Task 3, план «Особый фокус проверки» §Этап 3) — `Decimal`, не повтор вызова той
же функции:
- `dry_solid_flow(200 м³/ч, ρ=1 т/м³, 4 % по массе)` → `8` т/ч сухого вещества (мастер-промпт §11);
- `geometric_output(4.15 м, 590 м/мин, 127.4 г/м²)` → `18.716334` т/ч против `19.46` на снимке экрана 61 —
  расхождение не диагностируется как неисправность (основания/усреднение снимка неизвестны, мастер-промпт §8);
- 80 кН/м линейного давления пресса **не переводится** напрямую в бары/иную величину давления — вход с единицей
  не того физического рода (линейная нагрузка, не давление/плотность/масса 1 м²) отклоняется диспетчером
  (`run_calculation`) как `CalculationInputError`, а не подставляется молча (мастер-промпт §8: «80 кН/м нельзя
  напрямую переводить в бары»); отдельной формулы для этого перевода в `CalculationId` нет вовсе — это осознанное
  отсутствие, не недоработка (см. докстринг `run_calculation`).

Пересекающиеся интервалы времени — `interval_union`, объединение, а не сумма длительностей (мастер-промпт §8).
Неизвестная база (например, без указания плотности или сухости) — `CalculationResult.value is None` и
`missing_basis` непустой: модуль уточняет, а не угадывает допущение (мастер-промпт §3 экономических расчётов,
§8: «ошибка, пропуск и ноль различаются») — это решает диспетчер `run_calculation` (видит, каких именованных
входов нет в `inputs`), а не отдельные функции формул ниже: они получают только уже имеющиеся конкретные
`Decimal`-значения и проверяют их физическую корректность (знак, диапазон доли), а не полноту набора полей.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from .models import CalculationResult

if TYPE_CHECKING:
    from .models import CalculationId

_HUNDRED = Decimal(100)
_MILLION = Decimal(1_000_000)
_SECONDS_PER_HOUR = Decimal(60)

_GEOMETRIC_ASSUMPTION = ("Геометрическая оценка по ширине/скорости/массе 1 м²; совпадение или расхождение с "
                         "показанием прибора само по себе не диагноз неисправности — основания и усреднение "
                         "показания прибора отдельно не подтверждены (мастер-промпт §8).")
_DRY_SOLID_ASSUMPTION = ("Плотность и массовая доля сухого вещества — заданные или явно принятые учебные "
                         "допущения, не измеренный поток (мастер-промпт §11: «при rho=1 т/м³ это приближение, "
                         "не скрытый факт КДМ-1»).")
_DRY_GRAMMAGE_ASSUMPTION = ("Массовое тождество g_dry = g_wet × доля сухого вещества при заданном определении "
                           "влажной базы — не калибровочный алгоритм датчика (мастер-промпт §11).")


class CalculationInputError(ValueError):
    """Вход формулы не того рода (например, кН/м туда, где нужна плотность/давление/масса 1 м²), не в допустимом
    физическом диапазоне (отрицательный расход, доля вне 0..1) или не `Decimal` (защита от неявного дублирования
    строки/float туда, где нужно точное десятичное число — без `eval`, без неявного приведения типов). Текст —
    что не так, без значений вопроса целиком."""


def _require_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise CalculationInputError(f"{name}: вход должен быть Decimal, получено {type(value).__name__}")
    return value


def _require_non_negative(name: str, value: Decimal) -> Decimal:
    if value < 0:
        raise CalculationInputError(f"{name}: значение не может быть отрицательным")
    return value


def _require_positive(name: str, value: Decimal) -> Decimal:
    if value <= 0:
        raise CalculationInputError(f"{name}: значение должно быть больше нуля (ноль — не то же самое, что "
                                    "отсутствие данных, мастер-промпт §11)")
    return value


def _require_fraction(name: str, value: Decimal) -> Decimal:
    if not (Decimal(0) <= value <= Decimal(1)):
        raise CalculationInputError(f"{name}: доля должна быть в пределах 0..1 (для процента — делить на 100 "
                                    "до вызова формулы, этот модуль не угадывает базу)")
    return value


def dry_solid_flow(volumetric_flow_m3_h: Decimal, density_t_m3: Decimal,
                   dry_content_fraction: Decimal) -> CalculationResult:
    """Сухое вещество, т/ч = объёмный расход (м³/ч) × плотность (т/м³) × доля сухого вещества. Пример
    мастер-промпта §8/§11: `200, 1, 0.04` → `8`."""
    volumetric_flow_m3_h = _require_non_negative("volumetric_flow_m3_h", _require_decimal(
        "volumetric_flow_m3_h", volumetric_flow_m3_h))
    density_t_m3 = _require_positive("density_t_m3", _require_decimal("density_t_m3", density_t_m3))
    dry_content_fraction = _require_fraction("dry_content_fraction", _require_decimal(
        "dry_content_fraction", dry_content_fraction))
    value = volumetric_flow_m3_h * density_t_m3 * dry_content_fraction
    return CalculationResult(
        calculation_id="dry_solid_flow",
        inputs=(
            ("volumetric_flow_m3_h", float(volumetric_flow_m3_h), "м3/ч"),
            ("density_t_m3", float(density_t_m3), "т/м3"),
            ("dry_content_fraction", float(dry_content_fraction), "доля"),
        ),
        value=float(value),
        unit="т/ч",
        assumptions=(_DRY_SOLID_ASSUMPTION,),
    )


def geometric_output(width_m: Decimal, speed_m_min: Decimal, basis_weight_g_m2: Decimal) -> CalculationResult:
    """Геометрическая оценка производительности, т/ч = ширина (м) × скорость (м/мин) × масса 1 м² (г/м²) ×
    60 / 10⁶ (мастер-промпт §11). Пример: `4.15, 590, 127.4` → `18.716334`; `4.2, 611, 92.6` → `14.2578072`."""
    width_m = _require_positive("width_m", _require_decimal("width_m", width_m))
    speed_m_min = _require_non_negative("speed_m_min", _require_decimal("speed_m_min", speed_m_min))
    basis_weight_g_m2 = _require_positive("basis_weight_g_m2", _require_decimal(
        "basis_weight_g_m2", basis_weight_g_m2))
    value = width_m * speed_m_min * basis_weight_g_m2 * _SECONDS_PER_HOUR / _MILLION
    return CalculationResult(
        calculation_id="geometric_output",
        inputs=(
            ("width_m", float(width_m), "м"),
            ("speed_m_min", float(speed_m_min), "м/мин"),
            ("basis_weight_g_m2", float(basis_weight_g_m2), "г/м2"),
        ),
        value=float(value),
        unit="т/ч",
        assumptions=(_GEOMETRIC_ASSUMPTION,),
    )


def dry_grammage(wet_basis_weight_g_m2: Decimal, dry_content_fraction: Decimal) -> CalculationResult:
    """Масса 1 м² по сухому веществу, г/м² = масса 1 м² (как есть) × доля сухого вещества. Пример мастер-промпта
    §11: `127.4, 0.91` → `115.934`. Базы влажности (до/после пресса, по стандарту ISO 536) не совпадают сами по
    себе — решение, какую долю подставлять (саму влажность или `1 − влажность`), принимает вызывающий
    (`preanalysis.py`/подтверждение технолога), эта функция получает уже готовую долю сухого вещества."""
    wet_basis_weight_g_m2 = _require_positive("wet_basis_weight_g_m2", _require_decimal(
        "wet_basis_weight_g_m2", wet_basis_weight_g_m2))
    dry_content_fraction = _require_fraction("dry_content_fraction", _require_decimal(
        "dry_content_fraction", dry_content_fraction))
    value = wet_basis_weight_g_m2 * dry_content_fraction
    return CalculationResult(
        calculation_id="dry_grammage",
        inputs=(
            ("wet_basis_weight_g_m2", float(wet_basis_weight_g_m2), "г/м2"),
            ("dry_content_fraction", float(dry_content_fraction), "доля"),
        ),
        value=float(value),
        unit="г/м2",
        assumptions=(_DRY_GRAMMAGE_ASSUMPTION,),
    )


def interval_union(intervals: Sequence[tuple[datetime, datetime]]) -> tuple[tuple[datetime, datetime], ...]:
    """Объединение пересекающихся/соприкасающихся интервалов простоя — сумма их длительностей не равна сумме
    исходных длительностей при пересечении (мастер-промпт §8: «пересекающиеся потери времени — объединение
    интервалов»). Вход — уже в одной базе времени (UTC или Europe/Moscow, не смешивая); смешение часовых поясов
    (наивный `datetime` рядом с timezone-aware) — `CalculationInputError`, не тихое падение/неверное сравнение."""
    if not intervals:
        return ()
    parsed: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise CalculationInputError("интервал должен быть парой datetime")
        try:
            if end < start:
                raise CalculationInputError("конец интервала раньше начала")
        except TypeError as exc:
            raise CalculationInputError("смешаны наивные и осведомлённые о часовом поясе datetime — "
                                        "приведите к одной базе времени перед объединением") from exc
        parsed.append((start, end))
    try:
        parsed.sort(key=lambda interval: interval[0])
    except TypeError as exc:
        raise CalculationInputError("смешаны наивные и осведомлённые о часовом поясе datetime — "
                                    "приведите к одной базе времени перед объединением") from exc
    merged: list[tuple[datetime, datetime]] = [parsed[0]]
    for start, end in parsed[1:]:
        last_start, last_end = merged[-1]
        try:
            overlaps_or_touches = start <= last_end
        except TypeError as exc:
            raise CalculationInputError("смешаны наивные и осведомлённые о часовом поясе datetime — "
                                        "приведите к одной базе времени перед объединением") from exc
        if overlaps_or_touches:
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return tuple(merged)


# calculation_id (кроме interval_union, см. докстринг run_calculation) → {имя входа: физический род единицы}
_INPUT_UNIT_KIND: dict[str, dict[str, str]] = {
    "dry_solid_flow": {"volumetric_flow_m3_h": "volume_flow", "density_t_m3": "density",
                       "dry_content_fraction": "fraction"},
    "geometric_output": {"width_m": "length", "speed_m_min": "speed", "basis_weight_g_m2": "basis_weight"},
    "dry_grammage": {"wet_basis_weight_g_m2": "basis_weight", "dry_content_fraction": "fraction"},
}
# физический род → допустимые обозначения единицы (ровно то, что формулы ожидают; без автоматического перевода
# рода — линейная нагрузка (кН/м, Н/м) никогда не входит ни в одно из этих множеств, мастер-промпт §8/§11:
# «80 кН/м нельзя напрямую переводить в бары», здесь — тот же принцип для любого рода формул этого модуля)
_UNIT_KIND_TOKENS: dict[str, frozenset[str]] = {
    "volume_flow": frozenset({"м3/ч", "m3/h"}),
    "density": frozenset({"т/м3", "t/m3"}),
    "fraction": frozenset({"доля", "fraction"}),
    "length": frozenset({"м", "m"}),
    "speed": frozenset({"м/мин", "m/min"}),
    "basis_weight": frozenset({"г/м2", "g/m2"}),
}
_CALCULATION_FUNCS = {
    "dry_solid_flow": dry_solid_flow,
    "geometric_output": geometric_output,
    "dry_grammage": dry_grammage,
}


def run_calculation(calculation_id: "CalculationId", inputs: Mapping[str, Decimal],
                    units: Mapping[str, str]) -> CalculationResult:
    """Диспетчер по `models.CalculationId` для `validator.py`/`service.py`: находит нужную функцию модуля,
    проверяет единицы входов (`units`) против ожидаемого физического рода формулы и вызывает её. Неизвестная
    единица или единица не того рода (например, кН/м вместо плотности/давления/массы 1 м²) — `CalculationInputError`,
    не попытка автоматически пересчитать (мастер-промпт §8: «80 кН/м нельзя напрямую переводить в бары» — здесь
    это проверено диспетчером на общем принципе «род единицы совпадает или отказ», не отдельной функцией
    перевода давления, которой в `CalculationId` нет).

    `interval_union` этим диспетчером не вызывается: её вход — последовательность пар `datetime`, а не
    `Mapping[str, Decimal]` (единого способа передать непересекающиеся виды входов без `eval`/угадывания формы
    нет) — `validator.py`/`service.py` вызывают `interval_union()` напрямую, когда выбранный `calculation_id ==
    "interval_union"`. Здесь — явный `CalculationInputError`, не `NotImplementedError`/тихая заглушка, чтобы
    вызывающий, перепутавший диспетчер с прямым вызовом, получил понятную причину, а не падение по форме входа.

    Отсутствующий именованный вход (ключ нет в `inputs`) — не ошибка, а неизвестная база: результат с
    `value=None` и этим именем в `missing_basis`, самой формуле вызов не передаётся (мастер-промпт §11: «ошибка,
    пропуск и ноль различаются» — пропуск явно обозначается, а не угадывается нулём/допущением)."""
    if calculation_id == "interval_union":
        raise CalculationInputError("interval_union не вызывается через этот диспетчер — у неё нет Decimal-"
                                    "входов; используйте calculations.interval_union(...) напрямую")
    required = _INPUT_UNIT_KIND.get(calculation_id)
    func = _CALCULATION_FUNCS.get(calculation_id)
    if required is None or func is None:
        raise CalculationInputError(f"неизвестная или неразрешённая формула: {calculation_id!r}")
    missing = tuple(name for name in required if name not in inputs)
    if missing:
        return CalculationResult(calculation_id=calculation_id, inputs=(), value=None, unit=None,
                                 assumptions=(), missing_basis=missing)
    for name, kind in required.items():
        value = inputs[name]
        if not isinstance(value, Decimal):
            raise CalculationInputError(f"{name}: вход должен быть Decimal, получено {type(value).__name__}")
        unit = units.get(name)
        allowed = _UNIT_KIND_TOKENS[kind]
        if unit is None or unit.strip().lower() not in allowed:
            raise CalculationInputError(
                f"{name}: единица {unit!r} не подходит для величины рода «{kind}» (разрешено: "
                f"{sorted(allowed)}); автоматический перевод между физическими родами не выполняется")
    return func(**{name: inputs[name] for name in required})


# ============================================================================================================
# plan_from_question (итоговая правка F1, п.1; мастер-промпт §7 п.7/§8/§11): распознать в ИСХОДНОМ тексте
# вопроса входы одной из четырёх формул выше (или `interval_union`), вернуть план для `service._build_outcome`
# — тот строит сам `CalculationResult`/секцию через `run_calculation`/`interval_union`/`rendering.
# render_calculation_section`, этот модуль числа не публикует и не ходит ни в `knowledge.py`, ни в LLM (чистая
# разборка текста на `Decimal`-входы, без eval, п.1 брифа).
#
# Четыре структурных отказа (дополнение контроллера 14.10, пп. T-05/б/в/г) — вопрос, для которого сервер и так
# достоверно не посчитает число НЕ из-за недостающего входа, а по роду задачи (перевод величины другого
# физического рода, алгоритма в корпусе нет, сравнение с внешними данными, которых в корпусе нет вовсе):
# `known_refusal_reason` возвращает причину ИЛИ `None` — используется и здесь (готовый план-отказ), и в
# `preanalysis.requires_clarification` (чтобы не запрашивать уточнение единицы/базы раньше этого отказа —
# уточнение не изменило бы исход, он не зависит от базы/единицы).
# ============================================================================================================

_LINEAR_LOAD_RE = re.compile(r"кн/м|н/м\b", re.IGNORECASE)
_PRESSURE_TARGET_RE = re.compile(r"\bбар\w*|мпа|кпа", re.IGNORECASE)
_LINEAR_LOAD_REFUSAL = ("линейная нагрузка (сила на длину) не переводится в давление без ширины зоны контакта/"
                       "калибровки")

_GAUGE_TERM_RE = re.compile(r"избыточн\w*|манометрическ\w*|изб\.", re.IGNORECASE)
_ABSOLUTE_TERM_RE = re.compile(r"абсолютн\w*", re.IGNORECASE)
# Правка контроллера, раунд 2, п.3 (H-34): «Переведи 3,5 бар в абсолютное давление» не содержит ни «избыточное»,
# ни «манометрическое» — раньше условие требовало ОБА признака (изб./манометрич. И абсолютное) и пропускало
# именно эту форму (просьба ПЕРЕВЕСТИ давление/бар/кПа/МПа В абсолютное — без явного называния исходной базы
# словом «избыточное», она тут и так подразумевается контекстом «перевести В абсолютное»). Признак давления/
# бар/кПа/МПа — равнозначная альтернатива «избыточное/манометрическое», НО только вместе с глаголом перевода
# (`_CONVERSION_VERB_RE`) — иначе регрессия на T-06: «Вакуум -300 мбар — это … абсолютное давление?» ВОПРОС о
# знаке/базе (не просьба перевести) должен остаться обычной "basis"-неоднозначностью (вакуум, −300 мбар — не
# избыточное давление, а его отсутствие ниже атмосферного; знак/база сами не становятся известны из того, что
# вопрос называет слово «абсолютное»), не самодостаточным структурным отказом.
_PRESSURE_WORD_OR_UNIT_RE = re.compile(r"давлен\w*|\bбар\w*|кпа|мпа", re.IGNORECASE)
_CONVERSION_VERB_RE = re.compile(r"перевед\w*|перевод\w*|переводи\w*|конверт\w*", re.IGNORECASE)
_GAUGE_TO_ABSOLUTE_REFUSAL = ("перевод избыточного (манометрического) давления в абсолютное не публикует число "
                             "без уточнения: нужна база — местное атмосферное давление или способ измерения")

_COV_TERM_RE = re.compile(r"\bcov\b|коэффициент\s+вариации", re.IGNORECASE)
_COV_REFUSAL = ("алгоритм COV Valmet IQ в корпусе отсутствует; общий статистический коэффициент вариации ≠ "
               "показатель COV экрана")

_COMPARISON_VERB_RE = re.compile(r"сравни\w*|сопостав\w*", re.IGNORECASE)
_EXTERNAL_SCOPE_RE = re.compile(
    r"конкурент\w*|друг\w*\s+завод\w*|друг\w*\s+стран\w*|в\s+мире|миров\w*|кита\w*|зарубеж\w*|иностран\w*|"
    r"отрасл\w*|межзаводск\w*|межотраслев\w*", re.IGNORECASE)
_EXTERNAL_COMPARISON_REFUSAL = ("в корпусе нет данных для межзаводского/межотраслевого сравнения; статистика не "
                               "выдумывается")


def _known_refusal(text: str) -> tuple[str, str] | None:
    """`(метка_для_текста_секции, причина)` для одной из четырёх структурных категорий отказа выше, или `None`,
    если текст ни одну из них не задевает. Порядок проверки — от самой узкой/однозначной сигнатуры к самой
    широкой (сравнение с внешними данными — последним, у него самые общие ключевые слова)."""
    if _LINEAR_LOAD_RE.search(text) and _PRESSURE_TARGET_RE.search(text):
        return "линейная_нагрузка_в_давление", _LINEAR_LOAD_REFUSAL
    if _ABSOLUTE_TERM_RE.search(text) and (
            _GAUGE_TERM_RE.search(text)
            or (_PRESSURE_WORD_OR_UNIT_RE.search(text) and _CONVERSION_VERB_RE.search(text))):
        return "избыточное_в_абсолютное", _GAUGE_TO_ABSOLUTE_REFUSAL
    if _COV_TERM_RE.search(text):
        return "cov_экрана_valmet", _COV_REFUSAL
    if _COMPARISON_VERB_RE.search(text) and _EXTERNAL_SCOPE_RE.search(text):
        return "сравнение_с_внешними_данными", _EXTERNAL_COMPARISON_REFUSAL
    return None


def known_refusal_reason(text: str) -> str | None:
    """Только причина (без внутренней метки секции) — для `preanalysis.requires_clarification` (дополнение
    контроллера 14.10): вопрос, которому сервер и так достоверно откажет по структурной причине (перевод кН/м
    в давление, избыточное→абсолютное без базы, COV экрана Valmet, межзаводское/межотраслевое сравнение), не
    должен раньше этого получать уточнение единицы/базы — clarification не изменит исход, он не зависит от
    базы/единицы, которую мог бы назвать технолог."""
    found = _known_refusal(text)
    return None if found is None else found[1]


# --- распознавание входов четырёх формул (Decimal; запятая и точка — мастер-промпт §7/§11) ---

def _to_decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "."))


def _fmt_number(value: Decimal) -> str:
    """Короткая форма числа для текста отказа «несколько значений …» (не для итогового рендера — тот делает
    `rendering._fmt`, не этот модуль): десятичная запятая, как и весь остальной интерфейс."""
    return str(value).replace(".", ",")


# Правка F1, раунд 1, п.3 (контроллер): знак обязателен в захвате — «-200 м3/ч» раньше молча становилось `200`
# (регэксп начинал совпадение с первой цифры, минус оставался СНАРУЖИ группы). Теперь минус (обычный дефис
# `-` и математический `−`, U+2212) — часть числа; формулы (`_require_non_negative`/`_require_positive`) сами
# отклоняют отрицательное значение `CalculationInputError`, не подставляют молча положительное.
_NUM = r"[-−]?\d+(?:[.,]\d+)?"
# Правка Task 3 (Д17, план §Task 3/Этап 3, «мелкие хвосты распознавания»): словесные формы объёмного расхода —
# тот же физический смысл, что «м³/ч»/«м3/ч» (куб/кубометр в час — бытовое название кубического метра в этом
# домене), не отдельная единица; «куб/ч» — сокращённая словесная форма с косой чертой, «кубов в час»/
# «кубометров в час» — полная словесная форма с предлогом. `plan_from_question` подставляет канонический токен
# единицы «м3/ч» независимо от того, какая из форм совпала (см. ветку `has_flow_match` ниже) — так же, как уже
# было для «м³/ч»/«м3/ч»/«м3/час».
_FLOW_RE = re.compile(
    rf"({_NUM})\s*(?:м³/ч|м3/ч|м3/час|куб\s*/\s*ч|кубометров\s+в\s+час|кубов\s+в\s+час)", re.IGNORECASE)
_DENSITY_RE = re.compile(rf"({_NUM})\s*т/м[3³]", re.IGNORECASE)
# доля сухого вещества по массе — ТОЛЬКО с явной базой «по массе»/«а.с.в.» сразу после процента (не угадывание
# базы по одному лишь «%», мастер-промпт §7 п.5/§11; T-03: «концентрация 4» без «%» и без базы вообще не ловится)
_DRY_CONTENT_MASS_RE = re.compile(rf"({_NUM})\s*%\s*(?:по\s+массе|а\.?\s*с\.?\s*в\.?)", re.IGNORECASE)
_DRY_SOLID_WORDS_RE = re.compile(r"сухого\s+вещества|сухого\s+волокна|сухое\s+вещество|расход", re.IGNORECASE)

# `[^\d\-−]` (не просто `[^\d]`) в разрыве метка→число: иначе разрыв жадно съедает сам знак минус до того, как
# до него дойдёт захватывающая группа `_NUM` (минус за пределами числа — молчаливо позитивное значение,
# правка F1, раунд 1, п.3)
_WIDTH_RE = re.compile(rf"ширин\w*[^\d\-−]{{0,10}}({_NUM})\s*м\b", re.IGNORECASE)
# «голый» вариант — для проверки неоднозначности (`_first_ambiguous`), не для самого распознавания: вторая
# альтернативная ширина в тексте обычно идёт без повторной метки «ширина» перед числом («…4,15 м, а по
# паспорту 4,2 м…») — `_WIDTH_RE` её не найдёт вовсе (не повторяет метку), а должна считаться наравне с первой
# (правка F1, раунд 1, п.2). Отрицательный просмотр `(?!/)` — не «590 м/мин» (единица скорости, не ширины).
_WIDTH_BARE_RE = re.compile(rf"({_NUM})\s*м\b(?!/)", re.IGNORECASE)
_SPEED_RE = re.compile(rf"({_NUM})\s*м/мин", re.IGNORECASE)
_BASIS_WEIGHT_RE = re.compile(rf"({_NUM})\s*г/м[2²]", re.IGNORECASE)

# сухость (доля/процент СУХОГО вещества) — только явная; «влажность N%» сама по себе НЕ подставляется как
# «1 − влажность» (мастер-промпт §11: «не угадывать базу»), остаётся непойманной → run_calculation вернёт
# missing_basis для dry_content_fraction
_DRY_CONTENT_EXPLICIT_RE = re.compile(rf"сухост\w*[^\d\-−]{{0,10}}({_NUM})\s*%", re.IGNORECASE)


@dataclass(frozen=True)
class CalculationPlan:
    """План расчёта по тексту вопроса (`plan_from_question`). `calculation_id` — одна из `models.CalculationId`
    (`run_calculation`/`interval_union`, вызывающий — `service.py`) ИЛИ короткая метка структурного отказа,
    когда формулы для вопроса в `CalculationId` нет вовсе (см. `known_refusal_reason`) — тогда `refusal_reason`
    не `None`, и вызывающий НЕ зовёт `run_calculation`/`interval_union` вовсе, а строит `CalculationResult` с
    `value=None`/`missing_basis=(refusal_reason,)` напрямую (кН/м→давление и подобные — без правки
    `rendering.py`, см. отчёт задачи).

    `inputs`/`units` — вход `run_calculation`; может быть НЕПОЛНЫМ (отсутствующий именованный вход — не ошибка
    этого модуля, а неизвестная база: сам `run_calculation` отдаёт её как `missing_basis`, мастер-промпт §11,
    «ошибка, пропуск и ноль различаются» — распознанный, но РОВНО нулевой вход остаётся в `inputs` со значением
    `Decimal(0)`, не выпадает как «не найдено»). `intervals` — вход `interval_union`, когда `calculation_id ==
    "interval_union"`; может быть пустым (вопрос про объединение периодов без единого распознанного времени в
    тексте — T-32, тогда `service.py` показывает ограничение «укажите начало и конец каждого периода», не
    пытается угадать интервалы)."""

    calculation_id: str | None
    inputs: dict[str, Decimal] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    intervals: tuple[tuple[datetime, datetime], ...] = ()
    refusal_reason: str | None = None


# Правка F1, раунд 1, п.1 (контроллер): признак «вопрос про объединение/суммирование длительностей» — ОДИН
# регэксп (не два раздельных «глагол + слово временной области», как было раньше — упрощение одновременно с
# правкой приоритета ниже). Список форм — по тексту замечания: «сложи/сложить» (одно слово ловит оба —
# `сложить` = `сложи`+`ть`), «суммар[ное]», «общее время», «длительност[ь]», «простой/простоя/простои»
# (падежные формы ИМЕННО существительного «простой» — не бытовое наречие «просто», у которого после корня
# «прост» нет ни одной из этих конкретных букв), «обрыв», «потери времени». T-32 («Сложить времена обрыва,
# простоя...») ловится и без literal HH:MM — просто этим набором слов.
_INTERVAL_SUM_CUE_RE = re.compile(
    r"сложи\w*|суммар\w*|общее\s+время|длительност\w*|"
    r"прост(?:ой|оя|ои|оях|оев|оям|оями)\b|обрыв\w*|потери\s+времени",
    re.IGNORECASE)
# Правка контроллера, раунд 1 (H-29 продолжение): разделитель интервала — не только дефис/тире (`[–—-]`), но
# и словесное «до» («с 08:00 до 20:00»); необязательное «с » перед первым временем — та же семантика, второе
# время/дата не меняются (группы захвата те же 6, `_parse_intervals` не правится).
_TIME_RANGE_RE = re.compile(
    rf"(?:(\d{{1,2}})\.(\d{{1,2}})\s+)?(?:с\s+)?(\d{{1,2}}):(\d{{2}})\s*(?:[–—-]|до)\s*(\d{{1,2}}):(\d{{2}})",
    re.IGNORECASE)
_ANCHOR_YEAR = 2026
# Правка контроллера, раунд 1 (H-29 продолжение): буквальная календарная дата «ДД.ММ»/«ДД.ММ.ГГГГ» БЕЗ времени
# («за 13.10», «13.10.2026») — отдельный самодостаточный сигнал периода для `preanalysis.requires_clarification`
# (mode="deviation_review"): день назван конкретно, диапазон часов внутри него — отдельный вопрос, не обязателен
# для того, чтобы перестать считать период чисто относительным («за последнюю смену»). День 1–31, месяц 1–12
# проверяются в Python (не в самом регэкспе) — отличает дату от десятичного числа того же вида («4.15»/«127.4»:
# «месяц» 15/127 вне 1–12, не дата); год (если есть) не проверяется — не нужен для самого признака «дата дана».
_LITERAL_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\b")


def mentions_time_range(text: str) -> bool:
    """Хоть один буквальный интервал `ЧЧ:ММ–ЧЧ:ММ` (включая словесный разделитель «до», «с … до …») в тексте —
    используется `preanalysis.requires_clarification` (дополнение контроллера, п.1/T-32 продолжение и правка
    раунда 1 H-29): время — самоописывающийся формат, вопрос с буквальным интервалом не должен получать
    уточнение единицы измерения формулы (у времени её нет в этом смысле) и не должен считаться чисто
    относительным периодом."""
    return bool(_TIME_RANGE_RE.search(text))


def mentions_literal_date(text: str) -> bool:
    """Буквальная календарная дата (`ДД.ММ`/`ДД.ММ.ГГГГ`, правдоподобный день/месяц — см. докстринг
    `_LITERAL_DATE_RE`) — используется `preanalysis.requires_clarification` (правка контроллера, раунд 1,
    H-29 продолжение) как альтернативный самодостаточный сигнал периода наравне с `mentions_time_range`."""
    for day_raw, month_raw, _year in _LITERAL_DATE_RE.findall(text):
        day, month = int(day_raw), int(month_raw)
        if 1 <= day <= 31 and 1 <= month <= 12:
            return True
    return False


def _parse_intervals(text: str) -> tuple[tuple[datetime, datetime], ...]:
    """Интервалы `HH:MM–HH:MM` (и с датой `ДД.ММ HH:MM–HH:MM`) из текста вопроса — наивные `datetime` на одной
    и той же (или явно названной) календарной дате, без смешения с осведомлёнными о часовом поясе значениями
    (`interval_union` сам отказывает на смешении — здесь строится только одна, наивная, база). Конец раньше
    начала без даты (простой через полночь) — конец переносится на следующие сутки той же базы, не ошибка
    разбора: обычная запись суточного графика."""
    intervals: list[tuple[datetime, datetime]] = []
    for day, month, h1, m1, h2, m2 in _TIME_RANGE_RE.findall(text):
        base_date = date(_ANCHOR_YEAR, int(month), int(day)) if day and month else date(_ANCHOR_YEAR, 1, 1)
        start = datetime.combine(base_date, time(int(h1), int(m1)))
        end = datetime.combine(base_date, time(int(h2), int(m2)))
        if end <= start:
            end += timedelta(days=1)
        intervals.append((start, end))
    return tuple(intervals)


def _distinct_values(pattern: "re.Pattern[str]", text: str) -> list[Decimal]:
    """Все РАЗЛИЧНЫЕ `Decimal`-значения, совпавшие с `pattern` (группа 1) в тексте — не первое попавшееся, все
    (правка F1, раунд 1, п.2): то же число, повторённое дважды, схлопывается в одно значение, РАЗНЫЕ числа —
    не схлопываются и не выбираются по порядку появления."""
    values: list[Decimal] = []
    for m in pattern.finditer(text):
        v = _to_decimal(m.group(1))
        if v not in values:
            values.append(v)
    return values


def _first_ambiguous(text: str, checks: tuple[tuple["re.Pattern[str]", str], ...]) -> str | None:
    """Первая неоднозначность среди `checks` (`(regex, человекочитаемая метка входа)`, …) — больше одного
    РАЗЛИЧНОГО значения совпало с тем же regex (правка F1, раунд 1, п.2 — дополнение контроллера): например,
    «получили 115,9 г/м2 сухого вещества, а масса 1 м2 127,4 г/м2» — оба числа несут одну и ту же единицу
    «г/м2», и то, что первое помечено как результат («получили … сухого вещества»), НЕ используется, чтобы
    отфильтровать его и молча взять второе — сервер не угадывает, какое из входных значений нужное (мастер-
    промпт §8/§11: «ошибка, пропуск и ноль различаются», неоднозначность — не частный случай пропуска, но тот
    же принцип «не угадывать»). Причина отказа — для `CalculationResult.missing_basis` (та же ветка
    `render_calculation_section(value=None)`, что и обычный пропуск базы, см. `plan_from_question`)."""
    for pattern, label in checks:
        values = _distinct_values(pattern, text)
        if len(values) > 1:
            formatted = " и ".join(_fmt_number(v) for v in values)
            return f"в вопросе несколько значений {label}: {formatted} — уточните, какое использовать"
    return None


def plan_from_question(text: str) -> CalculationPlan | None:
    """Пункт 1 итоговой правки F1 (доп. правка раунд 1, контроллер): разбор ИСХОДНОГО текста вопроса (не
    нормализованного — числа/единицы и структурные маркеры отказа читаются буквально) в `CalculationPlan`.
    `None` — в тексте нет ни одного узнаваемого признака ни одной из четырёх формул, ни объединения периодов,
    ни структурного отказа (вызывающий, `service.py`, отвечает общим уточнением через `rendering.
    render_clarification_section`, не пустым ответом).

    Поддерживаемые формы (буквальное чтение написанного, не угадывание):
    - `dry_solid_flow`: объёмный расход (`200 м³/ч`/`м3/ч`/`м3/час`), плотность (`ρ=1 т/м³`/`плотность 1
      т/м3`), доля сухого ПО МАССЕ (`4% по массе`/`4 % а.с.в.`) — слово ПЕРЕД числом не важно для расхода/
      плотности (единица сразу после числа узнаёт вход), для доли нужна явная база сразу после «%»;
    - `geometric_output`: ширина — ТОЛЬКО форма «ширина … <число> м» (метка строго ПЕРЕД числом, до 10
      символов между ними; одинокое «… м» без слова «ширина» перед ним не ловится — известное ограничение,
      не доработка), скорость (`590 м/мин`), масса 1 м² (`127,4 г/м²`);
    - `dry_grammage`: масса 1 м² и явная СУХОСТЬ (`сухость 91 %`, метка перед числом); одна лишь влажность не
      подставляется как «1 − влажность» (мастер-промпт §11);
    - `interval_union`: интервалы `ЧЧ:ММ–ЧЧ:ММ` (и с датой `ДД.ММ ЧЧ:ММ–ЧЧ:ММ`) ИЛИ (при отсутствии literal
      интервала) явный глагол/слово объединения длительностей (`_INTERVAL_SUM_CUE_RE` — «сложи»/«суммарн»/
      «общее время»/«длительность»/«простой»/«обрыв»/«потери времени», T-32).
    - структурные отказы — кН/м→давление, избыточное→абсолютное, COV экрана Valmet, межзаводское сравнение
      (см. `known_refusal_reason`).

    Известные ограничения (не распознаётся вовсе, план — `None`, не угаданное число): единица расхода/плотности
    БЕЗ слова-подсказки СРАЗУ после числа (например, «200 в час» без «м3»); несколько формул в одном вопросе
    одновременно (план выбирает ОДНУ по приоритету ниже). Правка Task 3 (Д17): «кубов в час»/«кубометров в
    час»/«куб/ч» больше НЕ ограничение — `_FLOW_RE` распознаёт их как тот же объёмный расход, что «м³/ч».

    Правка раунд 1, п.1 (Critical, контроллер): признаки `dry_solid_flow`/`geometric_output`/`dry_grammage`
    (любой Decimal-сигнал формулы) ИМЕЮТ ПРИОРИТЕТ над распознаванием `interval_union` — раньше буквальный
    `ЧЧ:ММ–ЧЧ:ММ` где угодно в тексте перехватывал план целиком («Расход 200 м3/ч … за смену 08:00-20:00…» →
    ложный `interval_union` «12 ч», вместо расчёта по расходу). Теперь `interval_union` рассматривается ТОЛЬКО
    когда ни один Decimal-сигнал формулы не найден — и даже тогда требует явного маркера объединения
    длительностей (`_INTERVAL_SUM_CUE_RE`), не просто присутствия похожих на время цифр.

    Правка раунд 1, п.2 (Critical, контроллер): прежде чем строить `inputs` выбранной формулы, для каждого её
    числового входа ищутся ВСЕ совпадения в тексте (`_first_ambiguous`/`_distinct_values`), не только первое —
    два РАЗНЫХ значения одного рода (две разные массы 1 м², две разные скорости/ширины/расхода) — отказ
    `refusal_reason`, не выбор первого по порядку появления в тексте.

    Правка раунд 1, п.3 (Important, контроллер): числовые регэкспы захватывают знак «-«/»−» — отрицательное
    значение доходит до `run_calculation`/формулы как есть и получает `CalculationInputError` от самой формулы
    (`_require_non_negative`/`_require_positive`), не становится тихо положительным из-за того, что минус
    остался за пределами захваченной группы."""
    refusal = _known_refusal(text)
    if refusal is not None:
        tag, reason = refusal
        return CalculationPlan(calculation_id=tag, refusal_reason=reason)

    has_width_match = _WIDTH_RE.search(text)
    has_speed_match = _SPEED_RE.search(text)
    has_basis_weight_match = _BASIS_WEIGHT_RE.search(text)
    has_dry_explicit_match = _DRY_CONTENT_EXPLICIT_RE.search(text)
    has_flow_match = _FLOW_RE.search(text)
    has_density_match = _DENSITY_RE.search(text)
    has_dry_mass_match = _DRY_CONTENT_MASS_RE.search(text)
    mentions_dry_solid = bool(_DRY_SOLID_WORDS_RE.search(text))
    has_decimal_formula_signal = bool(
        has_width_match or has_speed_match or has_basis_weight_match or has_dry_explicit_match or
        has_flow_match or has_density_match or has_dry_mass_match or mentions_dry_solid)

    # раунд 1, п.1: interval_union — только когда НЕТ ни одного Decimal-сигнала формулы выше, и даже тогда
    # только при явном маркере объединения длительностей (не просто по наличию literal ЧЧ:ММ где угодно)
    if not has_decimal_formula_signal and _INTERVAL_SUM_CUE_RE.search(text):
        return CalculationPlan(calculation_id="interval_union", intervals=_parse_intervals(text))

    if has_width_match or has_speed_match:
        ambiguous = _first_ambiguous(text, (
            (_WIDTH_BARE_RE, "ширины (м)"), (_SPEED_RE, "скорости (м/мин)"),
            (_BASIS_WEIGHT_RE, "массы 1 м² (г/м²)")))
        if ambiguous is not None:
            return CalculationPlan(calculation_id="geometric_output", refusal_reason=ambiguous)
        inputs: dict[str, Decimal] = {}
        units: dict[str, str] = {}
        if has_width_match:
            inputs["width_m"] = _to_decimal(has_width_match.group(1))
            units["width_m"] = "м"
        if has_speed_match:
            inputs["speed_m_min"] = _to_decimal(has_speed_match.group(1))
            units["speed_m_min"] = "м/мин"
        if has_basis_weight_match:
            inputs["basis_weight_g_m2"] = _to_decimal(has_basis_weight_match.group(1))
            units["basis_weight_g_m2"] = "г/м2"
        return CalculationPlan(calculation_id="geometric_output", inputs=inputs, units=units)

    if has_dry_explicit_match:
        ambiguous = _first_ambiguous(text, (
            (_DRY_CONTENT_EXPLICIT_RE, "сухости (%)"), (_BASIS_WEIGHT_RE, "массы 1 м² (г/м²)")))
        if ambiguous is not None:
            return CalculationPlan(calculation_id="dry_grammage", refusal_reason=ambiguous)
        inputs = {"dry_content_fraction": _to_decimal(has_dry_explicit_match.group(1)) / _HUNDRED}
        units = {"dry_content_fraction": "доля"}
        if has_basis_weight_match:
            inputs["wet_basis_weight_g_m2"] = _to_decimal(has_basis_weight_match.group(1))
            units["wet_basis_weight_g_m2"] = "г/м2"
        return CalculationPlan(calculation_id="dry_grammage", inputs=inputs, units=units)

    if has_flow_match or has_density_match or mentions_dry_solid or has_dry_mass_match:
        ambiguous = _first_ambiguous(text, (
            (_FLOW_RE, "объёмного расхода (м³/ч)"), (_DENSITY_RE, "плотности (т/м³)"),
            (_DRY_CONTENT_MASS_RE, "доли сухого вещества по массе (%)")))
        if ambiguous is not None:
            return CalculationPlan(calculation_id="dry_solid_flow", refusal_reason=ambiguous)
        inputs = {}
        units = {}
        if has_flow_match:
            inputs["volumetric_flow_m3_h"] = _to_decimal(has_flow_match.group(1))
            units["volumetric_flow_m3_h"] = "м3/ч"
        if has_density_match:
            inputs["density_t_m3"] = _to_decimal(has_density_match.group(1))
            units["density_t_m3"] = "т/м3"
        if has_dry_mass_match:
            inputs["dry_content_fraction"] = _to_decimal(has_dry_mass_match.group(1)) / _HUNDRED
            units["dry_content_fraction"] = "доля"
        return CalculationPlan(calculation_id="dry_solid_flow", inputs=inputs, units=units)

    if has_basis_weight_match:
        # только масса 1 м², без явной сухости/ширины/скорости — ближе к dry_grammage (ему для РЕЗУЛЬТАТА тоже
        # нужна только масса 1 м² + доля сухого), доля сухого вещества остаётся непойманной → missing_basis
        # (мастер-промпт §11: влажность САМА по себе не подставляется как «1 − влажность»)
        ambiguous = _first_ambiguous(text, ((_BASIS_WEIGHT_RE, "массы 1 м² (г/м²)"),))
        if ambiguous is not None:
            return CalculationPlan(calculation_id="dry_grammage", refusal_reason=ambiguous)
        return CalculationPlan(calculation_id="dry_grammage",
                               inputs={"wet_basis_weight_g_m2": _to_decimal(has_basis_weight_match.group(1))},
                               units={"wet_basis_weight_g_m2": "г/м2"})
    return None
