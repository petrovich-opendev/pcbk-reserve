"""Проверка карточек окна (Б3, Д8-R80, I-р4-6): коды формы check_cards, статусы калькулятора cards_check, CLI
cards-check и calc, задание субагенту карточек bench/cards/WRITER.md. Только теги 20FAKE_*/16FAKE_*."""
import json
import re

import pytest

import pcbk_bench.__main__ as cli
from fakes import FakeStand, write_config
from helpers import REPO, cards, drop_card, replace_card
from pcbk_bench.cards import CHECK_GOOD, check_cards, cards_check
from pcbk_bench.numbers import tag_tokens
from pcbk_bench.scenarios import SCENARIO_IDS
from pcbk_bench.stand import CALC_RATE_PAUSE_S

WRITER = REPO / "bench" / "cards" / "WRITER.md"
TWIN_STATUS = {"20FAKE_107_PV": "no_data", "16FAKE_109_PV": "unlisted"}
CODE_RE = re.compile(r"(missing:[ABC]\d|field:[ABC]\d\.[a-z_]+|tag_form:[ABC]\d|a4_opening|bench_tags"
                     r"|C[24]: [a-z_]+)")


def test_check_cards_form():                                                     # I-р4-6
    assert check_cards(cards()) == []
    assert check_cards(replace_card(cards(), "A4", opening="агент по давлению")) == ["a4_opening"]
    assert "missing:C2" in check_cards(drop_card(cards(), "C2"))
    assert "tag_form:B1" in check_cards(replace_card(cards(), "B1", tag="давление пара"))


def test_cards_check_prints_codes_only():                                        # I-р4-6
    stand = FakeStand(calc_status={"20FAKE_107_PV": "no_data", "16FAKE_109_PV": "unlisted"})
    codes = cards_check(cards(), stand)
    assert codes == ["C2: unlisted", "C4: no_data"]
    assert not any(re.search(r"FAKE|_PV|\d{2,}", c) for c in codes)


def test_check_cards_required_fields_and_words():                                # Д8-R80: поля и «общими словами»
    cs = cards()
    assert check_cards(replace_card(cs, "A2", on_draft=())) == ["field:A2.on_draft"]
    assert check_cards(replace_card(cs, "A1", bridge="")) == ["field:A1.bridge"]
    assert check_cards(replace_card(cs, "B3", clarify="")) == ["field:B3.clarify"]
    assert check_cards(replace_card(cs, "C4", period=None)) == ["field:C4.period"]
    assert check_cards(replace_card(cs, "A4", requests=())) == ["field:A4.requests"]
    assert check_cards(replace_card(cs, "B3", opening="Что сейчас по 20FAKE_105_PV?")) == ["field:B3.opening"]
    assert check_cards(replace_card(cs, "A1", bridge="Что сейчас по 20FAKE_104_PV?")) == ["field:A1.bridge"]
    assert check_cards(replace_card(cs, "B2", opening="Какое среднее за прошлую смену?")) == ["field:B2.opening"]
    assert check_cards(replace_card(cs, "A3", pick="тот, что с краю")) == ["tag_form:A3"]
    assert check_cards(replace_card(cs, "A3", pick="первый")) == []
    assert check_cards(replace_card(cs, "B3", expect_tag="расход пара")) == ["tag_form:B3"]
    assert check_cards(replace_card(cs, "A4", goal="следить за уровнем в баке")) == ["a4_opening"]
    assert check_cards(drop_card(drop_card(cs, "A1"), "C3")) == ["missing:A1", "missing:C3"]


def test_check_cards_bench_tags():                                               # заготовленный агент узла
    from dataclasses import replace
    cs = cards()
    assert check_cards(replace(cs, bench_tags=())) == ["bench_tags"]
    assert check_cards(replace(cs, bench_tags=tuple(t for t in cs.bench_tags if t != "20FAKE_107_PV"))) == [
        "bench_tags"]                                                                 # нет тега C4
    assert check_cards(replace(cs, bench_tags=(*cs.bench_tags, "16FAKE_109_PV"))) == ["bench_tags"]   # тег C2
    assert check_cards(replace(cs, bench_tags=(*cs.bench_tags, "давление"))) == ["bench_tags"]
    many = tuple(f"20FAKE_{i:03d}_PV" for i in range(101, 118))
    assert check_cards(replace(cs, bench_tags=many)) == ["bench_tags"]                  # больше шестнадцати


def test_cards_check_refusals_and_rate():                                        # m14: rate — пауза, не код
    sleeps: list[float] = []
    stand = FakeStand(calc_rate=2, calc_status=TWIN_STATUS)
    assert cards_check(cards(), stand, sleep=sleeps.append) == list(CHECK_GOOD)
    assert sleeps == [CALC_RATE_PAUSE_S] * 2
    assert [t for t, _ in stand.calc_calls] == ["tag_now", "tag_now", "tag_now", "tag_period"]
    assert stand.calc_calls[-1][1] == {"tags": ["20FAKE_107_PV"], "start": "2005-03-03T08:00:00",
                                       "end": "2005-03-03T16:00:00"}
    assert cards_check(cards(), FakeStand()) == ["C2: listed", "C4: has_data"]                 # отказ карточек
    assert cards_check(cards(), FakeStand(calc_status={"16FAKE_109_PV": "unknown", "20FAKE_107_PV": "discrete"})) \
        == ["C2: unknown", "C4: discrete"]
    bad = replace_card(cards(), "B1", tag="давление пара")
    codes = cards_check(bad, FakeStand(calc_status=TWIN_STATUS))
    assert codes[-2:] == list(CHECK_GOOD) and codes[0] == "tag_form:B1"


def test_cards_check_stand_failure_is_code(tmp_path):                            # сбой стенда — код, без текста
    class Broken:
        def calc(self, tool, args):
            raise RuntimeError("20FAKE_107_PV 41.7")
    assert cards_check(cards(), Broken()) == ["C2: stand_error", "C4: stand_error"]


def test_cli_cards_check_prints_codes(tmp_path, monkeypatch, capsys):            # Review Focus 6 Д8б
    config = write_config(tmp_path)
    monkeypatch.setattr(cli, "server_stand", lambda cfg: FakeStand(calc_status=TWIN_STATUS))
    assert cli.main(["cards-check", str(config)]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == list(CHECK_GOOD) and out.err == ""
    monkeypatch.setattr(cli, "server_stand", lambda cfg: FakeStand())
    assert cli.main(["cards-check", str(config)]) == 1
    assert capsys.readouterr().out.splitlines() == ["C2: listed", "C4: has_data"]
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "cards.json").write_text(json.dumps({"bench_node": "Узел", "bench_tags": ["20FAKE_101_PV"],
                                                   "cards": {"B1": {"opening": "x", "tag": 7}}}), encoding="utf-8")
    assert cli.main(["cards-check", str(write_config(broken, cards="cards.json"))]) == 1
    out = capsys.readouterr()
    assert out.out.splitlines() == ["cards_form"] and "B1" not in out.out + out.err


def test_cli_cards_check_output_has_codes_only(tmp_path, monkeypatch, capsys):   # тегов и значений нет
    cs = json.loads((REPO / "bench" / "cards.twin.json").read_text(encoding="utf-8"))
    cs["cards"]["B1"]["tag"] = "давление пара"
    del cs["cards"]["C1"]
    cs["cards"]["A4"]["opening"] = "Агент по давлению 20FAKE_104_PV 3,42 бар"
    (tmp_path / "cards.json").write_text(json.dumps(cs, ensure_ascii=False), encoding="utf-8")
    config = write_config(tmp_path, cards="cards.json")
    monkeypatch.setattr(cli, "server_stand", lambda cfg: FakeStand(calc_status=TWIN_STATUS))
    assert cli.main(["cards-check", str(config)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(CODE_RE.fullmatch(line) for line in lines)
    assert {"tag_form:B1", "missing:C1", "a4_opening", "field:A4.opening"} <= set(lines)
    assert not re.search(r"FAKE|_PV|давлен|3,42", "\n".join(lines))


def test_cli_calc_prints_service_answer(tmp_path, monkeypatch, capsys):          # только для субагента карточек
    config = write_config(tmp_path)
    stand = FakeStand(calc_status={"16FAKE_109_PV": "unlisted"})
    monkeypatch.setattr(cli, "server_stand", lambda cfg: stand)
    assert cli.main(["calc", str(config), "tag_now", '{"tags": ["16FAKE_109_PV"]}']) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["items"][0]["status"] == "unlisted" and stand.calc_calls == [("tag_now", {"tags": ["16FAKE_109_PV"]})]
    assert cli.main(["calc", str(config), "tag_now", "не json"]) == 1
    assert cli.main(["calc", str(config), "save_agent", "{}"]) == 1                    # только инструменты данных
    assert cli.main(["calc", str(config), "tag_now", "[1]"]) == 1
    assert len(stand.calc_calls) == 1


def test_calc_cli_builds_server_stand_without_gateway(tmp_path):                 # m19: без паролей учёток
    config = write_config(tmp_path)
    (tmp_path / "stand.json").write_text(json.dumps({"ssh": ["ssh", "u@h"], "core_url": "http://127.0.0.1:1",
                                                     "edge_url": "https://127.0.0.1:2", "edge_host": "h"}))
    stand = cli.server_stand(cli.load_config(config, cli.OPENCODE_JSON))
    assert stand.core_url == "http://127.0.0.1:1" and stand.secrets == tmp_path / "secrets"
    assert not (tmp_path / "secrets").exists()                                       # паролей не читал


def test_writer_has_no_data():                                                   # постоянный текст без данных
    text = WRITER.read_text(encoding="utf-8")
    assert not tag_tokens(text) and not re.search(r"FAKE|_PV", text)
    assert not re.search(r"\d", re.sub(r"\b[ABC][1-4]\b", " ", text))                # цифры — только id сценариев
    for need in ("карточки записаны, N штук", "python -m pcbk_bench calc CONFIG TOOL", "cards-check", "C2: unlisted",
                 "C4: no_data", "live/cards.json", "bench/cards.twin.json"):
        assert need in text, need
    assert all(sid in text for sid in SCENARIO_IDS)


@pytest.mark.parametrize("sid", SCENARIO_IDS)
def test_twin_cards_pass_form_per_scenario(sid):                                 # образец формата — годный
    assert not [c for c in check_cards(cards()) if sid in c]
