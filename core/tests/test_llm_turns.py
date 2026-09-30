"""Предел обращений к модели на ход: новый ход, продолжение после сжатия, повтор, подагент, возврат шага."""
import pytest

from helpers import FakeMono
from pcbk_core.llm.models import select_models
from pcbk_core.llm.request import SUMMARY_PROMPT, SYNTHETIC_ATTACHMENT_PROMPT, TurnMark, rebuild_body, turn_mark
from pcbk_core.llm.turns import COMPACTION_CONTINUE, TURN_CALLS, TURN_IDLE_S, TurnLimiter, TurnLimitReached

M1, M1_AGAIN_LATER = TurnMark("user", "h1", 2), TurnMark("user", "h1", 6)
OTHER = TurnMark("other", None, 3)
SYS = {"role": "system", "content": "s"}
PLAN = "<system-reminder>\n# Plan Mode - System Reminder\n…\n</system-reminder>\n"   # session/prompt/plan.txt


def user(*texts):
    return {"role": "user", "content": [{"type": "text", "text": t} for t in texts]}


def call(i):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "pcbk_tag_now", "arguments": "{}"}}]}


def result(i):
    return {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"}


def steps_of(i):
    return [m for k in range(1, i) for m in (call(k), result(k))]


def test_turn_counts_steps_and_new_turn_on_new_mark():
    t = TurnLimiter(limit=3, monotonic=FakeMono(0.0))
    assert [t.step("student-01", "ses_a", m) for m in (M1, OTHER, OTHER)] == [1, 2, 3]
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", OTHER)                # подсказка OpenCode о шагах — тоже шаг
    assert t.step("student-01", "ses_a", M1_AGAIN_LATER) == 1   # тот же текст позже — новый ход


def test_compaction_continue_is_not_a_new_turn():                                # Review Focus 7
    t = TurnLimiter(limit=3, monotonic=FakeMono(0.0))
    t.step("student-01", "ses_a", M1); t.step("student-01", "ses_a", OTHER); t.step("student-01", "ses_a", OTHER)
    # сжатие посреди хода, в хвосте тот же вопрос: перед продолжением — ответ инструмента (доводка ревью I1)
    mid = turn_mark({"messages": [user(SUMMARY_PROMPT), {"role": "assistant", "content": "сводка"}, user("вопрос"),
                                  call(1), result(1), user(COMPACTION_CONTINUE)]})
    assert mid == TurnMark("continue", None, 6)
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", mid)
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


# --- доводка по ревью задачи 3, I1: тела, как их шлёт OpenCode 1.18.33 ---

def test_mid_turn_compaction_with_plan_reminder_keeps_counting():
    """PROMPT_PLAN дописывается к последнему `user` на каждом шаге (session/reminders.ts) и не хранится: после
    сжатия посреди хода вопрос в хвосте — без него, напоминание — у продолжения. Счёт идёт дальше."""
    t = TurnLimiter(monotonic=FakeMono(0.0))
    marks = [turn_mark({"messages": [SYS, user("вопрос", PLAN), *steps_of(i)]}) for i in range(1, 16)]
    assert [t.step("student-01", "ses_a", m) for m in marks] == list(range(1, 16))
    squeezed = [user(SUMMARY_PROMPT), {"role": "assistant", "content": "сводка"}, user("вопрос"), call(14), result(14)]
    cont = turn_mark({"messages": [*squeezed, user(COMPACTION_CONTINUE, PLAN)]})
    assert t.step("student-01", "ses_a", cont) == 16
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", turn_mark({"messages": [*squeezed, user(COMPACTION_CONTINUE), call(16), result(16)]}))


def test_same_short_question_with_compaction_on_first_step_is_new_turn():
    """Ход «да» дошёл до шага 16; новый «да», и OpenCode сжимает на его первом шаге — новый ход с шага 1."""
    t = TurnLimiter(monotonic=FakeMono(0.0))
    marks = [turn_mark({"messages": [SYS, user("да"), *steps_of(i)]}) for i in range(1, 17)]
    assert [t.step("student-01", "ses_a", m) for m in marks] == list(range(1, 17))
    squeezed = [user(SUMMARY_PROMPT), {"role": "assistant", "content": "сводка"}, call(15), result(15),
                {"role": "assistant", "content": "итог"}, user("да")]
    first = turn_mark({"messages": [*squeezed, user(COMPACTION_CONTINUE, PLAN)]})
    assert first.kind == "continue" and first.text_hash == marks[0].text_hash      # тот же текст, что у прошлого хода
    assert t.step("student-01", "ses_a", first) == 1
    assert t.step("student-01", "ses_a", turn_mark({"messages": [*squeezed, user(COMPACTION_CONTINUE), call(1),
                                                                 result(1)]})) == 2


# --- доводка по ревью задачи 3, раунд 2 ---

def test_same_continue_mark_repeated_counts_up():
    """Та же метка «продолжение прямо после вопроса» (повтор OpenCode) ход не сбрасывает: 1…7."""
    t = TurnLimiter(monotonic=FakeMono(0.0))
    for _ in range(5):
        t.step("student-01", "ses_a", M1)                                     # прошлый ход — на шаге 5
    first = TurnMark("continue", "h2", 7)
    assert [t.step("student-01", "ses_a", first) for _ in range(7)] == [1, 2, 3, 4, 5, 6, 7]


def test_tool_media_steps_do_not_start_turns():
    """Вложения ответа инструмента OpenCode шлёт отдельным `user` в конце тела — это шаги хода, не вопросы."""
    t = TurnLimiter(monotonic=FakeMono(0.0))
    media = {"role": "user", "content": [{"type": "text", "text": SYNTHETIC_ATTACHMENT_PROMPT},
                                         {"type": "text", "text": "[вложение не передаётся на учебном стенде]"}]}
    marks = [turn_mark({"messages": [SYS, user("вопрос"), *steps_of(i)] + ([media] if i > 1 else [])})
             for i in range(1, 17)]
    assert [t.step("student-01", "ses_a", m) for m in marks] == list(range(1, 17))
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", turn_mark({"messages": [SYS, user("вопрос"), *steps_of(17), media]}))


def test_attachment_only_user_counts_as_step():                                  # решение контроллера, раунд 2
    """15 шагов, затем `user` с одним вложением (после замены — одна заглушка) — шаг 16, 17-й — отказ."""
    policy = select_models("")["z-ai/glm-5.3"]
    image = {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}
    mark = lambda msgs: turn_mark(rebuild_body({"messages": msgs}, policy, caller="student-01", session=None))
    t = TurnLimiter(monotonic=FakeMono(0.0))
    assert [t.step("student-01", "ses_a", mark([SYS, user("вопрос"), *steps_of(i)])) for i in range(1, 16)] == \
           list(range(1, 16))
    assert t.step("student-01", "ses_a", mark([SYS, user("вопрос"), *steps_of(15), image])) == 16
    with pytest.raises(TurnLimitReached):
        t.step("student-01", "ses_a", mark([SYS, user("вопрос"), *steps_of(15), image, call(16), result(16)]))
