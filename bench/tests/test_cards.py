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
    out = capsys.readouterr()                                                      # место — без значений (доводка Д8б)
    assert out.out.splitlines() == ["cards_form:B1.tag"] and "7" not in out.out + out.err and out.err == ""


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


# --- итоговая доводка Д8б: место cards_form, строка паузы rate, задание C4, сверка транскрипта субагента карточек ---

def _cards_file(tmp_path, mutate) -> str:
    cs = json.loads((REPO / "bench" / "cards.twin.json").read_text(encoding="utf-8"))
    mutate(cs)
    (tmp_path / "cards.json").write_text(json.dumps(cs, ensure_ascii=False) if isinstance(cs, dict) else cs,
                                         encoding="utf-8")
    return str(write_config(tmp_path, cards="cards.json"))


@pytest.mark.parametrize("mutate, code", [
    (lambda cs: cs.update(extra=1), "keys"),
    (lambda cs: cs.update(bench_tags=["20FAKE_101_PV", "20FAKE_101_PV"]), "bench_tags"),
    (lambda cs: cs.update(bench_node=" "), "bench_node"),
    (lambda cs: cs.update(cards=[]), "cards"),
    (lambda cs: cs["cards"].update(D9={"opening": "x"}), "cards.ids"),
    (lambda cs: cs["cards"].update(B2="текст"), "B2"),
    (lambda cs: cs["cards"]["A1"].update(note="x"), "A1.fields"),
    (lambda cs: cs["cards"]["B3"].update(opening=""), "B3.opening"),
    (lambda cs: cs["cards"]["A2"].update(on_draft=["да, сохраняй"]), "A2.on_draft"),
    (lambda cs: cs["cards"]["A4"].update(requests="одна строка"), "A4.requests"),
    (lambda cs: cs["cards"]["C4"].update(period={"start": "2005-03-03T16:00:00", "end": "2005-03-03T08:00:00"}),
     "C4.period"),
    (lambda cs: cs["cards"]["C2"].update(tag=["16FAKE_109_PV"]), "C2.tag"),
])
def test_cli_cards_form_place(tmp_path, monkeypatch, capsys, mutate, code):      # Minor 3: место без значений
    config = _cards_file(tmp_path, mutate)
    monkeypatch.setattr(cli, "server_stand", lambda cfg: FakeStand(calc_status=TWIN_STATUS))
    assert cli.main(["cards-check", config]) == 1
    out = capsys.readouterr()
    assert out.out.splitlines() == [f"cards_form:{code}"] and out.err == ""
    assert not re.search(r"FAKE|_PV|сохраняй|текст|2005", out.out)


def test_cli_cards_form_not_json_and_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "server_stand", lambda cfg: FakeStand(calc_status=TWIN_STATUS))
    (tmp_path / "cards.json").write_text("{не JSON", encoding="utf-8")
    assert cli.main(["cards-check", str(write_config(tmp_path, cards="cards.json"))]) == 1
    assert capsys.readouterr().out.splitlines() == ["cards_form:json"]
    assert cli.main(["cards-check", str(write_config(tmp_path, cards="нет.json"))]) == 1
    assert capsys.readouterr().out.splitlines() == ["cards_form"]


def test_cli_cards_check_rate_pause_line(tmp_path, monkeypatch, capsys):         # Minor 3: ожидание окна — видно
    slept: list[float] = []
    monkeypatch.setattr(cli, "server_stand", lambda cfg: FakeStand(calc_rate=2, calc_status=TWIN_STATUS))
    monkeypatch.setattr(cli.time, "sleep", slept.append)
    assert cli.main(["cards-check", str(write_config(tmp_path))]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == list(CHECK_GOOD)
    assert out.err.splitlines() == ["калькулятор: rate — пауза"] * 2 and slept == [CALC_RATE_PAUSE_S] * 2


def test_writer_c4_opening_with_tag():                                           # Minor 4: как требует check_cards
    text = WRITER.read_text(encoding="utf-8")
    c4 = text[text.index("- **C4:**"):text.index("Общие правила")]
    assert "с именем тега" in c4
    assert check_cards(replace_card(cards(), "C4", opening="Какая была средняя температура за ту смену?")) == [
        "field:C4.opening"]
    assert "cards-audit" in text and "Edit" in text


def _audit_case(tmp_path):
    from pcbk_bench.cards import TWIN_CARDS, WRITER_MD
    from pcbk_bench.judge import HARNESS_TOOLS
    live = tmp_path / "live"
    live.mkdir()
    config = tmp_path / "config.json"
    config.write_text("{}")
    cards_file = live / "cards.json"
    calc = f"python -m pcbk_bench calc {config} tag_now '{{\"tags\": [\"T\"]}}'"
    ok = [{"tool": "Read", "path": str(WRITER_MD)}, {"tool": "Read", "path": str(TWIN_CARDS)},
          {"tool": "Bash", "command": calc},
          {"tool": "Bash", "command": f"cd {REPO / 'bench'} && uv run --python 3.12 python -m pcbk_bench calc "
                                      f"{config} catalog_search '{{\"query\": \"пар; ls\", \"limit\": 10}}'"},
          {"tool": "Bash", "command": f"PYTHONPATH=bench python3 -m pcbk_bench calc {config} tag_period "
                                      f"'{{\"tags\": [\"T\"], \"period\": \"prev_shift\"}}'"},
          {"tool": "Write", "path": str(cards_file)}, {"tool": "Read", "path": str(cards_file)},
          {"tool": "SubagentHandback", "path": None}]
    return config, cards_file, ok, HARNESS_TOOLS


def test_cards_audit_allowlist(tmp_path):                                        # Minor 6: по списку разрешений
    from pcbk_bench.cards import audit_cards
    config, cards_file, ok, harness = _audit_case(tmp_path)
    assert audit_cards(ok, config, cards_file, harness) == []
    (tmp_path / "secret.txt").write_text("x")
    (tmp_path / "live" / "link.json").symlink_to(tmp_path / "secret.txt")
    for bad in ({"tool": "Read", "path": str(tmp_path / "stand.json")},
                {"tool": "Read", "path": str(tmp_path / "secrets" / "ops.data-token")},
                {"tool": "Read", "path": str(tmp_path / "live" / "link.json")},
                {"tool": "Read", "path": str(tmp_path / "live" / ".." / "live" / "cards.json")},
                {"tool": "Read", "path": "live/cards.json"}, {"tool": "Read", "path": None},
                {"tool": "Write", "path": str(tmp_path / "live" / "cards-copy.json")},
                {"tool": "Write", "path": str(REPO / "bench" / "cards.twin.json")},
                {"tool": "Edit", "path": str(cards_file)}, {"tool": "Glob", "path": str(tmp_path)},
                {"tool": "Agent", "path": None}, {"tool": "WebSearch", "path": None},
                {"tool": "ToolSearch", "path": None},
                {"tool": "Bash", "command": "cat " + str(tmp_path / "stand.json")},
                {"tool": "Bash", "command": ok[2]["command"] + " ; cat /etc/passwd"},
                {"tool": "Bash", "command": ok[2]["command"] + " > /tmp/out"},
                {"tool": "Bash", "command": ok[2]["command"].replace("tag_now '", "tag_now \"$(id)").rstrip("'")},
                {"tool": "Bash", "command": ok[2]["command"].replace(str(config), str(tmp_path / "other.json"))},
                {"tool": "Bash", "command": ok[2]["command"].replace("tag_now", "save_agent")},
                {"tool": "Bash", "command": ok[2]["command"].replace("calc", "cards-check")},
                {"tool": "Bash", "command": "uv run --with x " + ok[2]["command"]},
                {"tool": "Bash", "command": f"touch {tmp_path / 'x'} && " + ok[2]["command"]},
                {"tool": "Bash", "command": f"cd {tmp_path} | " + ok[2]["command"]},
                {"tool": "Bash", "command": ok[2]["command"] + " extra"},
                {"tool": "Bash", "command": ok[2]["command"].replace("'{\"tags\": [\"T\"]}'", "'[1]'")},
                {"tool": "Bash", "command": ok[2]["command"].replace("'{\"tags\": [\"T\"]}'", "'не json'")},
                {"tool": "Bash", "command": ok[2]["command"].replace("'{\"tags\": [\"T\"]}'",
                                                                     '"{\\"tags\\": [\\"$(id)\\"]}"')},
                {"tool": "Bash", "command": None}):
        problems = audit_cards(ok + [bad], config, cards_file, harness)
        assert len(problems) == 1, bad
        assert str(tmp_path) not in problems[0] and "passwd" not in problems[0] and "stand" not in problems[0]


def test_cli_cards_audit(tmp_path, capsys):                                      # Minor 6: по образцу judge-audit
    from helpers import transcript_line
    config, cards_file, ok, _ = _audit_case(tmp_path)
    cfg = write_config(tmp_path, cards=str(cards_file))
    ok = [{**c, "command": c["command"].replace(str(config), str(cfg))} if "command" in c else c for c in ok]
    lines = [transcript_line(c["tool"], {"command": c["command"]} if "command" in c else
                             ({"file_path": c["path"]} if c["path"] else {"message": "готово"})) for c in ok]
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text("\n".join(lines) + "\n")
    assert cli.main(["cards-audit", str(cfg), "--transcript", str(transcript)]) == 0
    assert capsys.readouterr().out.splitlines() == ["нарушений: 0"]
    extra = [transcript_line("Bash", {"command": "cat " + str(tmp_path / "stand.json")}),
             transcript_line("Read", {"__unparsedToolInput": {"raw": "{", "len": 1}})]
    transcript.write_text("\n".join(lines + extra) + "\n")
    assert cli.main(["cards-audit", str(cfg), "--transcript", str(transcript)]) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == ["Bash: команда вне списка", "Read: без пути", "нарушений: 2"] and "stand" not in out


def test_cards_audit_relative_path_from_cwd(tmp_path, monkeypatch):             # относительный путь — нарушение
    from pcbk_bench.cards import audit_cards
    config, cards_file, ok, harness = _audit_case(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert audit_cards([{"tool": "Write", "path": "live/cards.json"}], config, cards_file, harness) == [
        "Write: относительный путь"]


def test_calc_command_dollar_in_quoted_args(tmp_path):                           # $(…) в двойных кавычках — нарушение
    from pcbk_bench.cards import calc_command_ok
    config = tmp_path / "config.json"
    good = f"python -m pcbk_bench calc {config} tag_now '{{\"tags\": [\"$x\"]}}'"
    bad = f'python -m pcbk_bench calc {config} tag_now "{{\\"tags\\": [\\"$(id)\\"]}}"'
    assert not calc_command_ok(bad, config) and not calc_command_ok(good, config)  # «$» — нигде
    assert calc_command_ok(good.replace("$x", "x"), config)
