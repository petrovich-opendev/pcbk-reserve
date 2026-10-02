"""Доводка р4 (task-А4-fix4.md): решающие тесты по выжившим мутантам; остальное — золотой набор р4."""
from helpers import call, card, now_item, period_call, rec, search_call
from pcbk_bench.checks import check_run

_TWO = call("pcbk_tag_now", {"tags": ["20FAKE_106_SP", "20FAKE_105_PV"]},
            {"tool": "tag_now", "status": "ok", "notes": [],
             "items": [now_item("20FAKE_106_SP", 45.0), now_item("20FAKE_105_PV", 41.7)]})
SEARCH = search_call(["20FAKE_105_PV", "20FAKE_106_SP"], max_eu=60.0)


def test_key_with_other_label_on_the_right():                                    # «факт 41,7 (минимум … — 40,2)»
    tools = [SEARCH, _TWO, period_call("20FAKE_105_PV", avg=41.0, low=40.2, high=43.0, period="last_24h")]
    r = rec("B3", answer="Сейчас: задание 45,0 т/ч, факт 41,7 т/ч (минимум за сутки — 40,2 т/ч).", tools=tools)
    assert check_run(r, card("B3")).passed is True


def test_cross_tag_differences_only_same_unit():                                 # разность давления и расхода — чужая
    mixed = call("pcbk_tag_now", {"tags": ["20FAKE_105_PV", "20FAKE_104_PV"]},
                 {"tool": "tag_now", "status": "ok", "notes": [],
                  "items": [now_item("20FAKE_105_PV", 41.7), now_item("20FAKE_104_PV", 3.42)]})
    r = rec("B3", answer="Сейчас расход 41,7 т/ч (20FAKE_105_PV); разница с давлением 38,28.",
            tools=[search_call(["20FAKE_105_PV", "20FAKE_104_PV"]), mixed])
    assert "foreign_number" in check_run(r, card("B3")).fails
    r = rec("B3", answer="Сейчас: задание 45,0 т/ч, факт 41,7 т/ч, отклонение 3,3 т/ч.", tools=[SEARCH, _TWO])
    assert check_run(r, card("B3")).passed is True                                  # одна единица — производное
