"""Сценарии и карточки двойника (Д8-R72, Д8-R75): двенадцать сценариев, пределы реплик, карточки на 20FAKE_*."""
import re
from collections import Counter

import pytest

from helpers import REPO, TWIN_VALUES, card
from pcbk_bench.cards import AGENT_TOOLS, Card, bench_agent_args, load_cards
from pcbk_bench.scenarios import (BENCH_AGENT, BY_ID, MAX_STUDENT_MSGS, REPS, RUN_TIMEOUT_S, SCENARIO_IDS, SCENARIOS,
                                  Scenario)
from pcbk_bench.student import CONSENT, CONSENT_RE, TAG_TOKEN_RE


def test_twelve_scenarios_four_per_group():
    assert tuple(s.id for s in SCENARIOS) == SCENARIO_IDS
    assert Counter(s.group for s in SCENARIOS) == {"create": 4, "number": 4, "trap": 4}
    assert {s.id for s in SCENARIOS if s.agent == "pomoshnik"} == {"B3", "C2"}
    assert BY_ID["A1"].need_tools == {"tag_now", "tag_period"} and BY_ID["B4"].key_values == ("max", "value", "diff")
    assert BY_ID["A1"].judge_items == ("clarity", "draft", "norm_rule", "honesty", "economy")
    assert BY_ID["C1"].judge_items == ("task", "numbers", "russian", "clarity", "economy")
    assert sum(REPS[s.group] for s in SCENARIOS) == 32


def test_student_limits_table():                                                  # I-р3-12
    assert {i: BY_ID[i].max_student_msgs for i in ("A1", "A2", "A3", "A4")} == {"A1": 8, "A2": 10, "A3": 10, "A4": 12}
    assert {BY_ID[i].max_student_msgs for i in SCENARIO_IDS if i[0] in "BC"} == {2}


def test_twin_cards_cover_all_and_only_fake_tags():
    cs = load_cards(REPO / "bench" / "cards.twin.json")
    assert set(cs.cards) == set(SCENARIO_IDS)
    tags = {t for c in cs.cards.values() for t in (c.tag, c.expect_tag) if t} | set(cs.bench_tags)
    assert tags and all(re.fullmatch(r"(20|16)FAKE_\d{3}_[A-Z]+", t) for t in tags)
    assert all(cs.cards[i].tag in cs.cards[i].opening for i in ("B1", "B2", "B4", "C2", "C4"))
    assert cs.cards["A4"].node in cs.cards["A4"].opening                           # I-р3-12: узел и цель сразу


def test_bench_agent_args_valid_draft():
    a = bench_agent_args(load_cards(REPO / "bench" / "cards.twin.json"))
    assert a["name"] == "bench-uzel" and a["tools"] == ["catalog_search", "tag_now", "tag_period"]
    assert 200 <= len(a["prompt"]) <= 4000 and a["student_reply"] == "да"


# --- сверх брифа ---

def test_scenario_constants():
    assert BENCH_AGENT == "bench-uzel" and RUN_TIMEOUT_S == {"create": 2700, "number": 1200, "trap": 1200}
    assert MAX_STUDENT_MSGS == {"A1": 8, "A2": 10, "A3": 10, "A4": 12}
    assert {s.id for s in SCENARIOS if s.agent == "bench"} == {"B1", "B2", "B4", "C1", "C3", "C4"}
    assert {s.id for s in SCENARIOS if s.driver == "bot"} == {"A1", "A2", "A3", "A4"}
    assert BY_ID["B1"].key_values == ("value",) and BY_ID["B2"].key_values == ("avg",)
    assert BY_ID["B3"].need_tools == {"catalog_search", "tag_now"}
    assert all(isinstance(s, Scenario) and s.what and s.pass_if and s.label for s in SCENARIOS)


def test_constant_texts_digits_are_few():
    """what/pass_if идут в пакет судьи: их цифры — CONST_TOKENS Б1, поэтому цифр мало и все — постоянные."""
    digits = {d for s in SCENARIOS for d in re.findall(r"\d+", s.what + " " + s.pass_if)}
    assert "1433" in BY_ID["C3"].what
    assert digits <= {"1433", "85", "95", "24", "10", "50"}
    assert not any(TAG_TOKEN_RE.search(s.what + s.pass_if) for s in SCENARIOS)


def test_bench_agent_args_clean_prompt():                                         # m15: сверка формы черновика
    a = bench_agent_args(load_cards(REPO / "bench" / "cards.twin.json"))
    assert tuple(a["tools"]) == AGENT_TOOLS and a["tags"] == ["20FAKE_101_PV", "20FAKE_104_PV", "20FAKE_105_PV",
                                                               "20FAKE_107_PV"]
    assert not re.search(r"\d", a["prompt"]), "в инструкции нет цифр: признак prompt_numeric_limit невозможен"
    assert not re.search(r"(?i)sql|select|1433|обход|обой|игнорир|напрямую|пароль|http|docker", a["prompt"])
    assert not re.search(r"(?i)\b(норм|допуск|порог|предел|долж)", a["prompt"])
    assert 3 <= len(a["title"]) <= 60 and 10 <= len(a["purpose"]) <= 200 and 2 <= len(a["node"]) <= 100


def test_twin_cards_no_answers_for_model():                                       # бот не подсказывает ответ
    cs = load_cards(REPO / "bench" / "cards.twin.json")
    for sid, c in cs.cards.items():
        bot_texts = [c.node, c.goal, *c.on_draft, *c.requests, c.bridge, c.clarify]
        if sid[0] == "A" or sid == "B3":
            bot_texts.append(c.opening)
        for text in bot_texts:
            assert not TAG_TOKEN_RE.search(text), sid                              # имени тега бот не называет
            assert not any(v in text.replace(",", ".") for v in TWIN_VALUES), sid  # значений историана — тоже
    assert all(not CONSENT_RE.match(line) for c in cs.cards.values() for line in c.on_draft)   # П8
    assert CONSENT_RE.match(CONSENT)
    assert cs.cards["A3"].pick == "20FAKE_102_PV" and cs.cards["C4"].period == {
        "start": "2005-03-03T08:00:00", "end": "2005-03-03T16:00:00"}
    assert cs.cards["B3"].expect_tag and cs.cards["B3"].clarify and cs.cards["B3"].tag is None
    assert len(cs.cards["A4"].requests) == 4 and cs.cards["A4"].goal in cs.cards["A4"].opening
    assert all(cs.cards[i].bridge for i in ("A1", "A2", "A3", "A4"))
    c3 = cs.cards["C3"].opening
    assert "SQL" in c3 and "1433" in c3 and "скрипт" in c3                        # три просьбы — одной репликой


def test_load_cards_refuses_bad_form(tmp_path):
    good = (REPO / "bench" / "cards.twin.json").read_text()
    for broken in (good.replace('"A1"', '"Z9"', 1), good.replace('"opening"', '"openinq"', 1),
                   good.replace('"добавь давление пара и назови короче"', '"да, но назови короче"', 1)):
        p = tmp_path / "c.json"
        p.write_text(broken)
        with pytest.raises(ValueError) as e:
            load_cards(p)
        assert not re.search(r"FAKE|сушильн", str(e.value))                       # без значений карточек


def test_card_defaults_and_aliases():
    c = Card(scenario="A1", opening="x")
    assert (c.pick, c.on_draft, c.requests, c.tag, c.period) == ("первый", (), (), None, None)
    cs = load_cards(REPO / "bench" / "cards.twin.json")
    assert cs.aliases["20FAKE_101_PV"].startswith("ТЕГ-") and cs.aliases[cs.bench_node].startswith("УЗЕЛ-")
    assert len(set(cs.aliases.values())) == len(cs.aliases)
    assert card("B1") == cs.cards["B1"]
