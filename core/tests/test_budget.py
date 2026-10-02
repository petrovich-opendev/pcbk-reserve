"""Бюджет вызова: 16 тегов, окно до 31 суток, до 24 тего-суток (для одного тега — 31)."""
from datetime import datetime, timedelta

import pytest

from pcbk_core.data.budget import check_budget, cost_tag_days

T6 = datetime(2026, 10, 1, 6)


def test_budget_contract():
    assert check_budget(16, T6, T6 + timedelta(days=1)) is None
    assert "16 тегов" in check_budget(17, T6, T6 + timedelta(hours=1))
    assert check_budget(1, T6, T6 + timedelta(days=31)) is None
    assert "31 суток" in check_budget(1, T6, T6 + timedelta(days=31, seconds=1))
    assert check_budget(2, T6, T6 + timedelta(days=12)) is None
    assert "24,08" in check_budget(2, T6, T6 + timedelta(days=12, hours=1))
    assert cost_tag_days(3, T6, T6 + timedelta(minutes=10)) == pytest.approx(3 / 24)
