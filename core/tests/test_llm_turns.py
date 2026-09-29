"""Предел обращений к модели на ход: новый ход, продолжение после сжатия, повтор, подагент, возврат шага."""
import pytest

from helpers import FakeMono
from pcbk_core.llm.request import TurnMark
from pcbk_core.llm.turns import COMPACTION_CONTINUE, TURN_CALLS, TURN_IDLE_S, TurnLimiter, TurnLimitReached

M1, M1_AGAIN_LATER = TurnMark("user", "h1", 2), TurnMark("user", "h1", 6)
OTHER = TurnMark("other", None, 3)


def test_turn_counts_steps_and_new_turn_on_new_mark():
    t = TurnLimiter(limit=3, monotonic=FakeMono(0.0))
    assert [t.step("student-01", "ses_a", m) for m in (M1, OTHER, OTHER)] == [1, 2, 3]
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", OTHER)                # подсказка OpenCode о шагах — тоже шаг
    assert t.step("student-01", "ses_a", M1_AGAIN_LATER) == 1   # тот же текст позже — новый ход


def test_compaction_continue_is_not_a_new_turn():                                # Review Focus 7
    t = TurnLimiter(limit=3, monotonic=FakeMono(0.0))
    t.step("student-01", "ses_a", M1); t.step("student-01", "ses_a", OTHER); t.step("student-01", "ses_a", OTHER)
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", TurnMark("continue", "h1", 5))        # сжатие посреди хода, в хвосте тот же вопрос
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", TurnMark("continue", None, 3))        # в хвосте вопроса нет


def test_compaction_on_first_step_of_new_question_is_new_turn():                 # раунд 2
    t = TurnLimiter(limit=16, monotonic=FakeMono(0.0))
    t.step("student-01", "ses_a", M1)
    for _ in range(13):
        t.step("student-01", "ses_a", OTHER)                                   # прошлый ход — на шаге 14
    assert t.step("student-01", "ses_a", TurnMark("continue", "h2", 4)) == 1  # новый вопрос в хвосте после сжатия
    assert t.step("student-01", "ses_a", OTHER) == 2


def test_refund_returns_step():
    t = TurnLimiter(limit=3, monotonic=FakeMono(0.0))
    t.step("student-01", "ses_a", M1); t.refund("student-01", "ses_a")
    assert t.step("student-01", "ses_a", M1) == 1                              # повтор после 502 — снова шаг 1


def test_retry_of_first_step_does_not_reset():                                   # Review Focus 7
    t = TurnLimiter(limit=3, monotonic=FakeMono(0.0))
    assert [t.step("student-01", "ses_a", M1) for _ in range(3)] == [1, 2, 3]
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", M1)


def test_subagent_counts_into_parent():                                          # Review Focus 7
    t = TurnLimiter(limit=3, monotonic=FakeMono(0.0))
    t.step("student-01", "ses_a", M1)
    assert t.step("student-01", "ses_child", TurnMark("user", "h2", 2), parent="ses_a") == 2   # подагент хода не начинает
    assert t.step("student-01", "ses_child", OTHER, parent="ses_a") == 3
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", OTHER)


def test_turns_are_per_caller_and_session():
    t = TurnLimiter(limit=2, monotonic=FakeMono(0.0))
    t.step("student-01", "ses_a", M1); t.step("student-01", "ses_a", OTHER)
    assert t.step("student-02", "ses_a", OTHER) == 1 and t.step("student-01", "ses_b", OTHER) == 1


def test_idle_turn_forgotten():
    mono = FakeMono(0.0)
    t = TurnLimiter(limit=2, idle_s=60, monotonic=mono)
    t.step("ops", None, M1); t.step("ops", None, OTHER); mono.advance(61)
    assert t.step("ops", None, OTHER) == 1


# --- сверх брифа: развилки реализации, закреплённые тестами ---

def test_defaults_and_refused_step_number():
    assert (TURN_CALLS, TURN_IDLE_S) == (16, 3600.0)
    assert COMPACTION_CONTINUE == ("Continue if you have next steps, or stop and ask for clarification "
                                   "if you are unsure how to proceed.")         # session/compaction.ts:531
    t = TurnLimiter(monotonic=FakeMono(0.0))
    for _ in range(16):
        t.step("ops", None, M1)
    with pytest.raises(TurnLimitReached) as e:
        t.step("ops", None, OTHER)
    assert e.value.step == 17 and not any(ch.isdigit() for ch in str(e.value))


def test_refund_not_below_zero_and_unknown_key_is_noop():
    t = TurnLimiter(limit=2, monotonic=FakeMono(0.0))
    t.refund("ops", "ses_x")                                                    # ключа нет — ничего
    t.step("ops", "ses_a", M1); t.refund("ops", "ses_a"); t.refund("ops", "ses_a")
    assert t.step("ops", "ses_a", OTHER) == 1
    t.step("student-01", "ses_a", M1)
    t.step("student-01", "ses_c", OTHER, parent="ses_a"); t.refund("student-01", "ses_c", parent="ses_a")
    assert t.step("student-01", "ses_a", OTHER) == 2                           # возврат подагента — в ход родителя


def test_new_turn_after_refused_step_and_idle_keeps_active_keys():
    mono = FakeMono(0.0)
    t = TurnLimiter(limit=1, idle_s=60, monotonic=mono)
    t.step("ops", "ses_a", M1)
    with pytest.raises(TurnLimitReached):
        t.step("ops", "ses_a", OTHER)
    assert t.step("ops", "ses_a", TurnMark("user", "h2", 4)) == 1              # новый вопрос после отказа
    mono.advance(59)
    with pytest.raises(TurnLimitReached):
        t.step("ops", "ses_a", OTHER)                                          # тронут до срока — ход жив
    mono.advance(59)
    with pytest.raises(TurnLimitReached):
        t.step("ops", "ses_a", OTHER)                                          # отказ тоже трогает ход
