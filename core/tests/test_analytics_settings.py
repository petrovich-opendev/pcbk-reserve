"""Настройки аналитики КДМ-1 (`analytics/settings.py`) — разбор ANALYTICS_* по отдельности (Task 0; правка
итогового ревью F4b/F6). Фокус этого файла по политике передачи в LLM (`ANALYTICS_LLM_EXPORT_POLICY`):

- F4b (H-40, Ruling 24 контроллера 14.10) сделала настройку честной — `approved_subset` раньше принималось
  молча, но ничего не включало; стало явной ошибкой настройки.
- F6 (решение владельца, 14.10.2026, дословно): «4. Разрешен полный контент в тч внутренний и данные с
  БДРВ»; повторно, тем же днём: «Разрашею передачу полного контента в модель!» — владелец (заказчик и
  администратор стенда ПЦБК) дважды явно разрешил `approved_subset`; настройка принимает её как обычное
  допустимое значение, наравне с `public_only`/`none`."""
import pytest

from pcbk_core.analytics.settings import (
    ANALYTICS_REVIEWERS_MODES,
    AnalyticsSettings,
    DEFAULT_ANALYTICS_REVIEWERS,
    DEFAULT_LLM_EXPORT_POLICY,
    LLM_EXPORT_POLICIES,
)


def test_llm_export_policies_constant_lists_all_three_values():
    """F6: все три значения контракта (`models.LlmExportPolicy`) теперь включаемы настройкой — решение
    владельца о составе передаваемых данных принято и датировано (см. докстринг `settings.py`)."""
    assert LLM_EXPORT_POLICIES == frozenset({"public_only", "approved_subset", "none"})


def test_llm_export_policy_default_is_public_only():
    settings = AnalyticsSettings.from_env({})
    assert settings.ANALYTICS_LLM_EXPORT_POLICY == DEFAULT_LLM_EXPORT_POLICY == "public_only"


@pytest.mark.parametrize("value", ["public_only", "approved_subset", "none"])
def test_llm_export_policy_accepts_all_implemented_values(value):
    settings = AnalyticsSettings.from_env({"ANALYTICS_LLM_EXPORT_POLICY": value})
    assert settings.ANALYTICS_LLM_EXPORT_POLICY == value


def test_llm_export_policy_unknown_value_is_explicit_config_error():
    """Настоящая опечатка (не одно из трёх значений) — обычная ошибка настройки по списку допустимых
    значений, список теперь включает и `approved_subset` (он больше не отдельная причина отказа, F6)."""
    with pytest.raises(ValueError) as exc:
        AnalyticsSettings.from_env({"ANALYTICS_LLM_EXPORT_POLICY": "whatever"})
    assert "ANALYTICS_LLM_EXPORT_POLICY" in str(exc.value)
    assert "approved_subset" in str(exc.value)          # теперь в списке допустимых — не скрыто от технолога


@pytest.mark.parametrize("raw", [" approved_subset", "approved_subset ", "APPROVED_SUBSET", "Approved_Subset",
                                 " APPROVED_SUBSET \t"])
def test_llm_export_policy_accepts_approved_subset_regardless_of_case_and_whitespace(raw):
    """Строгий разбор (брифа задания F6, п.1) — `strip().lower()` до сравнения: иной регистр/пробелы вокруг
    значения узнаются и нормализуются, не падают в ошибку «неизвестное значение» (приём сохранён из правки
    контроллера F4b, раунд 1, но теперь ведёт к успешному разбору, не к отказу)."""
    settings = AnalyticsSettings.from_env({"ANALYTICS_LLM_EXPORT_POLICY": raw})
    assert settings.ANALYTICS_LLM_EXPORT_POLICY == "approved_subset"


@pytest.mark.parametrize("raw", ["aproved_subset", "approved-subset", "public", "publiconly"])
def test_llm_export_policy_real_typo_is_explicit_config_error_not_approved_subset(raw):
    """Настоящая опечатка (отличная от approved_subset/public_only/none даже после strip/lower) — явная
    ошибка настройки, не тихий откат ни на умолчание, ни на approved_subset."""
    with pytest.raises(ValueError, match="ANALYTICS_LLM_EXPORT_POLICY"):
        AnalyticsSettings.from_env({"ANALYTICS_LLM_EXPORT_POLICY": raw})


def test_llm_export_policy_typo_does_not_silently_enable_other_fields():
    """`valid_from_env` — запасной путь (приём `GatewaySettings.valid_from_env`): поле с ошибкой отбрасывается
    и заменяется умолчанием, прочие корректные поля (`ANALYTICS_ENABLED`) не страдают."""
    settings = AnalyticsSettings.valid_from_env({"ANALYTICS_ENABLED": "true",
                                                 "ANALYTICS_LLM_EXPORT_POLICY": "aproved_subset"})
    assert settings.ANALYTICS_ENABLED is True
    assert settings.ANALYTICS_LLM_EXPORT_POLICY == DEFAULT_LLM_EXPORT_POLICY


def test_llm_export_policy_approved_subset_survives_valid_from_env():
    """F6: `approved_subset` — больше не ошибка, `valid_from_env` отдаёт его как есть (а не откатывается на
    умолчание, как было до решения владельца)."""
    settings = AnalyticsSettings.valid_from_env({"ANALYTICS_ENABLED": "true",
                                                 "ANALYTICS_LLM_EXPORT_POLICY": "approved_subset"})
    assert settings.ANALYTICS_ENABLED is True
    assert settings.ANALYTICS_LLM_EXPORT_POLICY == "approved_subset"


# --- ANALYTICS_REVIEWERS (F5-brief, решение владельца 14.10.2026: student без утверждения преподавателя) ---

def test_reviewers_modes_constant_lists_exactly_grants_and_all_students():
    assert ANALYTICS_REVIEWERS_MODES == frozenset({"grants", "all_students"})


def test_reviewers_default_is_grants():
    settings = AnalyticsSettings.from_env({})
    assert settings.ANALYTICS_REVIEWERS == DEFAULT_ANALYTICS_REVIEWERS == "grants"


@pytest.mark.parametrize("value", ["grants", "all_students"])
def test_reviewers_accepts_both_modes(value):
    settings = AnalyticsSettings.from_env({"ANALYTICS_REVIEWERS": value})
    assert settings.ANALYTICS_REVIEWERS == value


def test_reviewers_typo_is_explicit_config_error_not_silent_default():
    """Строгий разбор (F5-brief, п.1): опечатка — явная ошибка настройки, не тихий откат на grants."""
    with pytest.raises(ValueError, match="ANALYTICS_REVIEWERS"):
        AnalyticsSettings.from_env({"ANALYTICS_REVIEWERS": "all_student"})


def test_reviewers_typo_does_not_silently_enable_other_fields():
    settings = AnalyticsSettings.valid_from_env({"ANALYTICS_ENABLED": "true",
                                                 "ANALYTICS_REVIEWERS": "AllStudents"})
    assert settings.ANALYTICS_ENABLED is True
    assert settings.ANALYTICS_REVIEWERS == DEFAULT_ANALYTICS_REVIEWERS
