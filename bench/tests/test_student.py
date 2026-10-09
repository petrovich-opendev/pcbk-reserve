"""Сценарный бот-студент (Д8-R73): правила reply по порядку, пределы реплик, черновик постоянного вида."""
import re

from helpers import DRAFT, TWIN_VALUES, card
from pcbk_bench.scenarios import BY_ID
from pcbk_bench.student import (CONSENT, CONSENT_RE, DRAFT_RE, FALLBACK, GOAL_RE, LIST_RE, NAME_RE, NODE_RE,
                                NOT_LISTED, NAME_REPLY, TAG_TOKEN_RE, ScriptedStudent, render_draft)


def test_scripted_student_answers_constructor():                                 # Review Focus 5
    s = ScriptedStudent(card("A2"), BY_ID["A2"])
    assert s.opening() == card("A2").opening
    assert s.reply("Какой узел и что агент должен показывать?") == card("A2").node
    assert s.reply(DRAFT) == "добавь давление пара и назови короче"
    assert s.reply(DRAFT) == "да" and s.saw_draft


def test_scripted_student_draft_needs_both_marks():                              # m-н6
    s = ScriptedStudent(card("A2"), BY_ID["A2"])
    s.opening()
    assert s.reply("Сначала найду теги, потом покажу Черновик агента. Какой узел?") == card("A2").node
    assert not s.saw_draft


def test_scripted_student_name_before_node():                                    # m-н6
    s = ScriptedStudent(card("A1"), BY_ID["A1"])
    s.opening()
    assert s.reply("Как назвать агента для узла?") == "как предложишь"


def test_scripted_student_picks_tag_a3():
    s = ScriptedStudent(card("A3"), BY_ID["A3"])
    s.opening()
    assert s.reply("Нашёл несколько:\n- 20FAKE_101_PV — …, °C\n- 20FAKE_102_PV — …, °C\nКакой ваш?") == card("A3").pick


def test_scripted_student_a4_requests_first():
    s = ScriptedStudent(card("A4"), BY_ID["A4"])
    s.opening()
    assert [s.reply("Какой узел?") for _ in card("A4").requests] == list(card("A4").requests)
    assert s.reply("Какой узел?") == card("A4").node


def test_scripted_student_fallback():
    s = ScriptedStudent(card("A1"), BY_ID["A1"])
    s.opening()
    assert s.reply("Интересно, а какая у вас смена?") == FALLBACK


def test_scripted_student_limit():                                               # Review Focus 3
    s = ScriptedStudent(card("A1"), BY_ID["A1"])
    s.opening()
    replies = [s.reply("?") for _ in range(BY_ID["A1"].max_student_msgs + 2)]
    assert replies[-1] is None and len([r for r in replies if r]) == BY_ID["A1"].max_student_msgs - 1


# --- сверх брифа ---

def test_regex_exports():
    assert DRAFT_RE.search(DRAFT) and not DRAFT_RE.search("Черновик агента\n- Название: x")   # без «Сохранить так?»
    assert DRAFT_RE.search("**Черновик агента**\n- Название: x\n**Сохранить так?** Ответьте «да»")
    assert not DRAFT_RE.search("Сохранить так?\nЧерновик агента")                       # вопрос — после шапки
    assert NAME_RE.search("Как назвать агента?") and NODE_RE.search("Какой узел?") and NODE_RE.search("для узла")
    assert GOAL_RE.search("Какой период?") and not NODE_RE.search("Как назвать агента?")
    assert len(LIST_RE.findall("- 20FAKE_101_PV — a\n- `20FAKE_102_PV` – b\n| 20FAKE_103_PV | c |")) == 3
    assert CONSENT_RE.match("Да, сохраняй") and CONSENT_RE.match(" сохрани") and CONSENT_RE.match("согласен")
    assert not CONSENT_RE.match("добавь давление пара") and not CONSENT_RE.match("а да ладно")


def test_draft_rule_wins_over_name_and_list():                                   # правило 2 раньше 3 и 4
    s = ScriptedStudent(card("A1"), BY_ID["A1"])
    s.opening()
    assert s.reply(DRAFT) == CONSENT and s.saw_draft                               # в черновике «Имя…» и список тегов


def test_question_sentences_only():                                              # «по названию» — не вопрос об имени
    s = ScriptedStudent(card("A1"), BY_ID["A1"])
    s.opening()
    assert s.reply("Поищу теги по названию узла. Какой узел?") == card("A1").node
    assert s.reply("Что агент должен показывать и за какой период?") == card("A1").goal
    assert s.reply("Расскажите про узел.") == card("A1").node                     # вопросов нет — весь текст


def test_pick_only_from_shown_list():                                            # бот не подсказывает тег
    s = ScriptedStudent(card("A3"), BY_ID["A3"])
    s.opening()
    assert s.reply("Нашёл:\n- 20FAKE_101_PV — …, °C\n- 20FAKE_103_PV — …, °C\nКакой?") == NOT_LISTED
    a1 = ScriptedStudent(card("A1"), BY_ID["A1"])
    a1.opening()
    assert a1.reply("Варианты:\n1. 20FAKE_101_PV — a\n2. 20FAKE_102_PV — b\nКакой?") == "первый"
    assert a1.reply("Один тег:\n- 20FAKE_101_PV — a\nЭтот?") == FALLBACK               # одной строки мало


def test_on_draft_then_consent_and_limits_a4():
    s = ScriptedStudent(card("A4"), BY_ID["A4"])
    sent = [s.opening()] + [s.reply("Какой узел?") for _ in range(4)]
    assert sent[1:] == list(card("A4").requests) and card("A4").node in sent[0]
    assert s.reply(DRAFT) == CONSENT and len(s.sent) == 6
    assert [s.reply("?") for _ in range(7)][-1] is None and len(s.sent) == BY_ID["A4"].max_student_msgs


def test_script_driver_clarify_once():                                           # B3: уточнение не больше раза
    b3 = ScriptedStudent(card("B3"), BY_ID["B3"])
    assert b3.opening() == card("B3").opening
    assert b3.reply("Какой именно параметр вас интересует?") == card("B3").clarify
    assert b3.reply("А какой период?") is None
    plain = ScriptedStudent(card("B1"), BY_ID["B1"])
    plain.opening()
    assert plain.reply("Уточните тег?") is None and plain.reply("Сейчас 3,42 бар") is None
    b3 = ScriptedStudent(card("B3"), BY_ID["B3"])
    b3.opening()
    assert b3.reply("Сейчас 41,7 т/ч по тегу.") is None                          # ответ без вопроса — конец


def test_bot_never_leaks_tags_or_values():                                       # сценарий честный
    probes = ["Какой узел?", "Как назвать?", "Что показывать?", "Какой период?", "Какие вопросы?", "?",
              "Нашёл:\n- 20FAKE_101_PV — a\n- 20FAKE_103_PV — b\nКакой?", DRAFT, "Готово."]
    for sid in ("A1", "A2", "A3", "A4"):
        s = ScriptedStudent(card(sid), BY_ID[sid])
        out = [s.opening()] + [s.reply(p) for p in probes * 2]
        for text in filter(None, out):
            assert not TAG_TOKEN_RE.search(text), (sid, text)
            assert not any(v in text.replace(",", ".") for v in TWIN_VALUES), (sid, text)
    assert NAME_REPLY == "как предложишь"


def test_render_draft_is_permanent_form():                                       # П9: образец для двойника А3
    text = render_draft(title="Сушильная часть", name="bench-a1b2c3", node="Тестовая сушильная часть",
                        purpose="Показывать текущие значения и сводку за смену",
                        tags=[("20FAKE_101_PV", "описание", "°C")], questions=["что сейчас с температурой"])
    lines = text.splitlines()
    assert lines[0] == "Черновик агента" and lines[-1] == "Сохранить так? Ответьте «да» или напишите, что поменять."
    assert [ln.split(":")[0] for ln in lines[1:6]] == ["- Название", "- Имя для системы", "- Узел", "- Назначение",
                                                        "- Что умеет"]
    assert "  - 20FAKE_101_PV — описание, °C" in lines and DRAFT_RE.search(text)
    assert not re.search(r"\d", text.replace("20FAKE_101_PV", "").replace("a1b2c3", ""))


def test_inline_list_with_choice_and_name_forms():                              # настоящий Конструктор пишет по-разному
    s = ScriptedStudent(card("A3"), BY_ID["A3"])
    s.opening()
    assert s.reply("Нашёл 20FAKE_101_PV, 20FAKE_102_PV и 20FAKE_103_PV — описания одинаковые. Какой ваш?") == \
        card("A3").pick
    a1 = ScriptedStudent(card("A1"), BY_ID["A1"])
    a1.opening()
    assert a1.reply("Возьму 20FAKE_101_PV и 20FAKE_104_PV. Какие вопросы будет задавать технолог?") == card("A1").goal
    assert a1.reply("Как назовём агента?") == NAME_REPLY
    table = "| Тег | Описание |\n|---|---|\n| `20FAKE_101_PV` | a |\n| **20FAKE_102_PV** | b |\nКакой из них?"
    assert a1.reply(table) == "первый"
