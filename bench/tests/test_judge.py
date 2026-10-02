"""Судья отбора по файлам (Д8-R78): пакеты без цифр, источники Ч-n, сканер, пачки, каталог судей, сверка транскриптов,
разбор вердиктов, контроль J1–J4 (Review Focus Д8б 1–2). VOCAB — словарь двойника (описания bench-historian.json и
узлы карточек); agent_text(p) — текст последней реплики агента; packet_with(agent=…) — пакет B1 с этой репликой."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from fakes import M1, M2, M3, M4
from helpers import (CLEAN_VERDICT, REPO, VOCAB, agent_text, card, cards, honest_a_record, now_call, packet_with,
                     period_call, pid_is_control, rec, search_call, transcript_line)
from pcbk_bench.__main__ import main
from pcbk_bench.judge import (CONST_TOKENS, MASKS, PERIOD_PRESETS, REAL_DATA_CONSENT, REFUSAL_TEXTS, SOURCE_KINDS,
                              VERDICT_SCHEMA, DataInPacket, Packet, SecretInPacket, assert_packet_clean,
                              audit_transcripts, build_packet, build_vocab, check_verdicts, collect_verdicts,
                              controls_ok, describe, mask_vocab, pack_batches, parse_verdict, passed_by_judge,
                              stage_judges, task_template, transcript_calls, vocab_from)
from pcbk_bench.record import parse_turn
from pcbk_bench.runner import RunRefused
from pcbk_bench.scenarios import BY_ID, JUDGE_ITEMS_CREATE
from pcbk_bench.secret import Secret
from pcbk_bench.twin_judge import write_twin_verdicts


def _module(path: Path):
    name = f"_core_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                                 # dataclass ищет модуль в sys.modules
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(name, None)
    return mod


def _body(p: Packet) -> str:
    return json.dumps(p.fields["transcript"], ensure_ascii=False)


# --- Review Focus 1: в пакет не уходят цифры, данные, секрет и имя модели ---

def test_packet_has_no_digits_outside_whitelist():                               # р3, I-р4-1: и даты, и время
    r = rec("B4", answer="За 24 ч максимум 3,71 бар в 11:59 03.03.2026, сейчас 3,42 — выше на 0,29 (≈8,5 %), "
                         "достоверных 97,5 %",
            tools=[period_call("20FAKE_104_PV", high=3.71, period="last_24h", percent_good=97.5),
                   now_call("20FAKE_104_PV", 3.42, time="2026-10-02T11:59:40+05:00")])
    p = build_packet(r, BY_ID["B4"], card("B4"), VOCAB)
    assert_packet_clean(p, [], VOCAB, [])                                           # каждая цифра — в белом списке
    body = _body(p)                                  # П1: «24 ч» стоит в постоянном pass_if B4 — сверяется разговор
    for raw in ("3,71", "3,42", "0,29", "8,5", "97,5", "11:59", "03.03.2026", "24 ч", "2026-10-02T11:59:40"):
        assert raw not in body
    assert "24 ч" in p.fields["scenario"]["pass_if"]


def test_time_and_rounded_have_sources():                                        # I-р4-2
    r = rec("B1", answer="Сейчас 3,4 бар на 11:59", tools=[now_call("20FAKE_104_PV", 3.4217, time="2026-10-02T11:59:40+05:00")])
    p = build_packet(r, BY_ID["B1"], card("B1"), VOCAB)
    value, clock = re.findall(r"Ч-\d+", agent_text(p))
    assert p.fields["numbers"][value]["sources"][0].startswith("≈ Ч-")
    assert p.fields["numbers"][clock]["sources"] == ["вывод: tag_now.time"]


def test_exact_beats_derived():                                                  # I-р4-3
    r = rec("B2", answer="Размах за смену 0,5 °C",
            tools=[period_call("20FAKE_101_PV", avg=127.9, low=127.5, high=128.0, std=0.2)])   # range = 0,5
    p = build_packet(r, BY_ID["B2"], card("B2"), VOCAB)                             # и max − min = 0,5 тоже
    alias = re.search(r"Ч-\d+", agent_text(p))[0]
    assert p.fields["numbers"][alias]["sources"] == ["вывод: tag_period.range"]
    r = rec("B1", answer="Ещё 7 бар", tools=[now_call("20FAKE_104_PV", 3.42, age_s=12), search_call(["20FAKE_104_PV"], min_eu=3.0, max_eu=10.0)])
    p = build_packet(r, BY_ID["B1"], card("B1"), VOCAB)                             # 10 − 3 = 7 — не кандидат
    assert p.fields["numbers"][re.search(r"Ч-\d+", agent_text(p))[0]]["sources"] == []


def test_const_tokens_only_constants():
    assert {"A1", "C4"} <= CONST_TOKENS["scenario"] and "1433" in CONST_TOKENS["scenario"]
    assert "4000" in CONST_TOKENS["refusal"] and "last_24h" in CONST_TOKENS["args"] and "м3/ч" in CONST_TOKENS["any"]
    assert not any(re.search(r"FAKE|_PV|\{v\}", t) for ts in CONST_TOKENS.values() for t in ts)


def test_refusal_texts_and_presets_match_core():                                 # сверка, п. 17
    draft = _module(REPO / "core" / "pcbk_core" / "agents" / "draft.py")
    periods = _module(REPO / "core" / "pcbk_core" / "data" / "periods.py")
    want = {code: re.sub(r"\{(?:v|field|min|max)\}", "…", text).replace(", например sushka-bdm2", "")
            for code, text in draft.PROBLEM_TEXTS.items()}
    assert REFUSAL_TEXTS == want and PERIOD_PRESETS == periods.PRESETS


def test_exact_match_shares_output_alias():                                      # I-р3-1
    p = build_packet(rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)]),
                     BY_ID["B1"], card("B1"), VOCAB)
    alias = re.search(r"Ч-\d+", agent_text(p))[0]
    assert p.fields["numbers"][alias]["sources"] == ["вывод: tag_now.value"]
    out = next(e for e in p.fields["transcript"] if e["role"] == "инструмент")["output"]
    assert out["items"][0]["value"] == alias                                          # тот же псевдоним, что у вывода


def test_rounded_gets_approx_source():                                           # I-р3-1
    p = build_packet(rec("B1", answer="Сейчас 3,4 бар", tools=[now_call("20FAKE_104_PV", 3.42)]),
                     BY_ID["B1"], card("B1"), VOCAB)
    alias = re.search(r"Ч-\d+", agent_text(p))[0]
    assert p.fields["numbers"][alias]["sources"][0].startswith("≈ Ч-")


def test_range_never_output_source():                                            # I-р3-1: и при совпадении
    r = rec("C1", answer="Норма по регламенту — 3–4 бар",
            tools=[now_call("20FAKE_104_PV", 3.0), period_call("20FAKE_104_PV", high=4.0)])
    p = build_packet(r, BY_ID["C1"], card("C1"), VOCAB)
    rng = re.findall(r"Ч-\d+", agent_text(p))
    assert len(rng) == 2 and all(p.fields["numbers"][a]["sources"] == ["граница или диапазон"] for a in rng)
    assert p.fields["auto_flags"]["suspect_norm"]


def test_derived_marked():                                                       # I-р3-2
    r = rec("B1", answer="Сейчас 128,4 °C — на 0,5 выше среднего за смену",
            tools=[now_call("20FAKE_101_PV", 128.4), period_call("20FAKE_101_PV", avg=127.9)])
    p = build_packet(r, BY_ID["B1"], card("B1"), VOCAB)
    sources = [s for a in re.findall(r"Ч-\d+", agent_text(p)) for s in p.fields["numbers"][a]["sources"]]
    assert any(s.startswith("производное: Ч-") for s in sources)


def test_source_names_field():                                                   # I-р3-1: шкала прибора
    r = rec("C1", answer="Верх шкалы датчика — 10 бар", tools=[search_call(["20FAKE_104_PV"], max_eu=10.0)])
    p = build_packet(r, BY_ID["C1"], card("C1"), VOCAB)
    alias = re.search(r"Ч-\d+", agent_text(p))[0]
    assert p.fields["numbers"][alias]["sources"] == ["вывод: catalog_search.max_eu (шкала прибора)"]


def test_orphan_gets_automaton_class():                                          # сирота: класс автомата
    p = build_packet(rec("B1", answer="Сейчас 5,17 бар", tools=[now_call("20FAKE_104_PV", 3.42)]),
                     BY_ID["B1"], card("B1"), VOCAB)
    alias = re.search(r"Ч-\d+", agent_text(p))[0]
    assert p.fields["numbers"][alias] == {"sources": [], "class": "hard"}


def test_period_and_question_sources_for_dates():                                # I-р4-2: даты карточки
    r = rec("C4", answer="За 03.03.2005 с 08:00 до 16:00 данных нет.",
            tools=[period_call("20FAKE_107_PV", status="no_data",
                               period={"start": "2005-03-03T08:00:00", "end": "2005-03-03T16:00:00"})])
    p = build_packet(r, BY_ID["C4"], card("C4"), VOCAB)
    day, start, end = re.findall(r"Ч-\d+", agent_text(p))
    assert {"период сценария", "вопрос сценария"} <= set(p.fields["numbers"][day]["sources"])
    assert "вывод: tag_period.start" in p.fields["numbers"][start]["sources"]
    assert "вывод: tag_period.end" in p.fields["numbers"][end]["sources"]


def test_description_becomes_class_and_unit():                                   # m-р3-18
    assert describe("Факт. знач. - температура пара в коллекторе сушиль", "°C") == "температура, °C"
    assert describe("Задание - расход массы", "т/ч") == "задание регулятора, т/ч"
    assert describe("Перепад давления на сетке", "кПа") == "перепад давления, кПа"
    assert describe("странное описание XK-200", None) == "величина, ед."


def test_description_quoted_by_agent_replaced():
    r = rec("B1", answer="Тег: Давление пара в коллекторе тестовой сушильной части — 3,42 бар",
            tools=[now_call("20FAKE_104_PV", 3.42)])
    p = build_packet(r, BY_ID["B1"], card("B1"), VOCAB)
    assert "давление, бар" in agent_text(p) and "коллектор" not in p.text


def test_stem_vocab_masks_word_forms():                                          # I-р3-5
    text = mask_vocab("Датчик в коллекторе сушильной группы; параметр подробно для группы", VOCAB)
    assert "коллекторе" not in text and "сушильной" not in text and text.count("[скрыто]") == 2
    assert all(w in text for w in ("параметр", "подробно", "для", "группы"))


def test_aliases_not_masked():
    assert mask_vocab("ТЕГ-1 и УЗЕЛ-2, Ч-3, ИМЯ-4", VOCAB | {"узел"}) == "ТЕГ-1 и УЗЕЛ-2, Ч-3, ИМЯ-4"


def test_vocab_without_goals_and_constant_texts_intact():                        # П3
    vocab = build_vocab([rec("B1")], cards())
    assert not {"сейчас", "смену", "прошлую", "было"} & vocab
    goals = [c.goal for c in cards().cards.values() if c.goal]                   # словарь с целями — для регрессии
    wide = vocab_from([], [c.node for c in cards().cards.values() if c.node] + goals)
    p = build_packet(rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)]), BY_ID["B1"],
                     card("B1"), wide)
    assert_packet_clean(p, [], wide, [])
    assert agent_text(p).startswith("Сейчас Ч-") and p.fields["scenario"]["what"] == BY_ID["B1"].what
    p2 = build_packet(rec("B2"), BY_ID["B2"], card("B2"), wide)
    assert p2.fields["scenario"]["pass_if"] == BY_ID["B2"].pass_if
    c1 = build_packet(rec("C1"), BY_ID["C1"], card("C1"), VOCAB)          # П3.3: поле scenario словарём не сверяется
    assert "помечает" in c1.fields["scenario"]["pass_if"]
    assert_packet_clean(c1, [], VOCAB | {"помечает"}, [])


def test_equipment_abbreviations_masked():                                       # П4
    vocab = vocab_from(["Давление пара БДМ-3", "Скорость PM3 сетки"], [])
    assert {"бдм", "pm"} <= vocab
    r = rec("B1", answer="Давление пара в сушильной части БДМ-3 — 3,42 бар; на PM3 и PM 3 так же",
            tools=[now_call("20FAKE_104_PV", 3.42)])
    p = build_packet(r, BY_ID["B1"], card("B1"), vocab | VOCAB)
    assert not re.search(r"БДМ|PM", p.text)
    assert_packet_clean(p, [], vocab | VOCAB, [])
    with pytest.raises(DataInPacket):
        assert_packet_clean(packet_with(agent="на БДМ сейчас Ч-1 бар"), [], vocab, [])


def test_agent_name_and_refusal_code():                                          # I-р3-4; П2: save_after
    r = rec("A1", agent_said=["Черновик агента\n- Имя для системы: sushka-bdm3\nСохранить так?"], save_after=0,
            save_answers=[{"status": "refused", "problems": [{"code": "name_taken",
                                                              "text": "Агент с именем «sushka-bdm3» уже есть"}]}])
    p = build_packet(r, BY_ID["A1"], card("A1"), VOCAB)
    assert "bdm3" not in p.text and "ИМЯ-1" in p.text and "name_taken" in p.text
    assert re.fullmatch(r"[a-z]{12}", p.packet_id)


def test_packet_aliases_tags_and_nodes():
    p = build_packet(rec("A1", student=[f"агент по {card('A1').node}"], searched=["20FAKE_101_PV"]), BY_ID["A1"],
                     card("A1"), VOCAB)
    assert "20FAKE_101_PV" not in p.text and "ТЕГ-1" in p.text and "УЗЕЛ-1" in p.text


def test_bench_node_aliased():                                                   # m13
    node = cards().bench_node
    r = rec("B1", answer=f"Я — помощник по узлу «{node}». Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    p = build_packet(r, BY_ID["B1"], card("B1"), VOCAB, bench_node=node)
    assert "УЗЕЛ-1" in agent_text(p)
    assert_packet_clean(p, [], VOCAB, [node])


def test_packet_masks_unknown_tag_tokens():                                      # m21, m-р3-9
    p = build_packet(rec("B3", answer="Возьму 20FAKE_199_PV и 21FAKE0042"), BY_ID["B3"], card("B3"), VOCAB)
    assert not re.search(r"20FAKE_199_PV|21FAKE0042", p.text) and "ТЕГ-" in p.text


def test_packet_masks_model_words_and_versions():                                # m-р3-5
    p = build_packet(rec("B1", answer="Я — GLM-5.3 от Zhipu; я Клод; I am Sol"), BY_ID["B1"], card("B1"), VOCAB)
    assert not re.search(r"(?i)glm|zhipu|клод|\bsol\b|5\.3", p.text) and p.text.count("[модель]") >= 3


def test_packet_has_no_ids_costs_providers_reasoning():                          # m-н12; П2: без reasoning=
    p = build_packet(rec("B1", sessions=["ses_" + "A1" * 13], llm_rows=[{"cost": 0.01, "provider": "Fireworks"}]),
                     BY_ID["B1"], card("B1"), VOCAB)
    assert not re.search(r"ses_|cost|Fireworks|student-|place|\$\d|думаю как модель", p.text)


def test_parse_turn_drops_reasoning():                                           # П2: рассуждения в запись не идут
    messages = [{"info": {"role": "user", "time": {"created": 1000}}, "parts": [{"type": "text", "text": "вопрос"}]},
                {"info": {"role": "assistant", "time": {"created": 2000, "completed": 3000}},
                 "parts": [{"type": "reasoning", "text": "я думаю как модель X"}, {"type": "text", "text": "Ответ"}]}]
    assert parse_turn(messages, 0, "вопрос").assistant == ["Ответ"]


def test_packet_file_is_readable_json(tmp_path):                                 # m24: кириллица без \\u, отступы
    batches = pack_batches(tmp_path, [rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])],
                           cards(), [], seed="d8-test")
    f = next(batches[0].glob("*.json"))
    text = f.read_text(encoding="utf-8")
    assert "Сейчас Ч-" in text and "\\u04" not in text and text.startswith("{\n ")
    p = Packet.from_file(f)
    assert p.packet_id == f.stem and p.items == BY_ID[p.scenario].judge_items and p.text == text


def test_scanner_refuses():                                                      # Б-н1, р3
    for bad in ("сейчас 3,42 бар", "за 03.03.2005 данных нет", "датчик в коллекторе", "смотри 20FAKE_104_PV",
                "Тестовая сушильная часть", "я — Claude"):
        with pytest.raises(DataInPacket):
            assert_packet_clean(packet_with(agent=bad), [], VOCAB, [cards().bench_node])
    with pytest.raises(DataInPacket):                                               # узел вне словаря
        assert_packet_clean(packet_with(agent="по узлу Альфа-Бета всё спокойно"), [], VOCAB, ["Альфа-Бета"])
    assert_packet_clean(packet_with(agent="сейчас Ч-1 бар, ТЕГ-2, м3/ч"), [], VOCAB, [])   # контроль


def test_leftover_digit_groups_and_codes_aliased():                              # шаг 3 разбора; коды без «_»
    r = rec("B1", answer="Версия 1.2.3, позиции PT1001A и FIC-205, номер 7-12-3", tools=[now_call("20FAKE_104_PV", 3.42)])
    p = build_packet(r, BY_ID["B1"], card("B1"), VOCAB)
    assert_packet_clean(p, [], VOCAB, [])
    assert not re.search(r"PT|FIC|1\.2\.3", _body(p)) and "[скрыто]" in agent_text(p)


def test_secret_in_packet_refused(tmp_path):
    pw = Secret("q" * 48)
    with pytest.raises(SecretInPacket) as e:
        assert_packet_clean(packet_with(agent=f"мой пароль {pw}"), [pw], VOCAB, [])
    assert "q" * 48 not in str(e.value)
    for token in ("Authorization: Bearer abc", "BDRV_PW", "ключ " + "ab12" * 8, "адрес 10.0.0.7"):
        with pytest.raises(SecretInPacket):
            assert_packet_clean(packet_with(agent=token), [], VOCAB, [])
    pack_batches(tmp_path, [rec("B1", answer=f"пароль {pw}")], cards(), [pw])
    assert all(str(pw) not in f.read_text() for f in tmp_path.rglob("*.json"))
    assert json.loads((tmp_path / "judge" / "map.json").read_text())["refused"]


def test_secret_with_digits_refused_before_masking(tmp_path):                    # маски не прячут секрет с цифрами
    pw = Secret("a1b2c3" * 8)                                                      # вид openssl rand -hex 24
    pack_batches(tmp_path, [rec("B1", answer=f"пароль {pw}")], cards(), [pw])
    assert json.loads((tmp_path / "judge" / "map.json").read_text())["refused"] == {
        next(iter(json.loads((tmp_path / "judge" / "map.json").read_text())["refused"])): "secret"}
    assert not any("a1b2" in f.read_text() or "bcdef" in f.read_text() for f in tmp_path.rglob("batch-*/*.json"))


def test_real_data_requires_consent_file(tmp_path):                              # m-р3-8, m-р4-13
    r = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    with pytest.raises(RunRefused, match="consent"):
        pack_batches(tmp_path, [r], cards(), [], real_data=True)
    (tmp_path / "judge").mkdir(parents=True, exist_ok=True)
    consent = tmp_path / "judge" / "real-data-consent.txt"
    consent.write_text("дата: 2026-10-08T15:20:00+05:00\nсообщение владельца: да, согласен\n")
    with pytest.raises(RunRefused, match="consent"):                               # нет дословного текста
        pack_batches(tmp_path, [r], cards(), [], real_data=True)
    consent.write_text(f"дата: 2026-10-08T15:20:00+05:00\nсообщение владельца: {REAL_DATA_CONSENT}\n")
    pack_batches(tmp_path, [r], cards(), [], real_data=True)
    text = "".join(f.read_text() for f in tmp_path.rglob("batch-*/*.json"))
    assert "3,42" in text and "20FAKE_104_PV" not in text


def test_real_data_consent_whitespace_and_quotes(tmp_path):                     # m8
    r = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    (tmp_path / "judge").mkdir(parents=True)
    wrapped = "«" + REAL_DATA_CONSENT.replace("; ", ";\n").replace("Д8 с", "Д8  с") + "»"
    (tmp_path / "judge" / "real-data-consent.txt").write_text(f"дата: 2026-10-08\nсообщение владельца:\n{wrapped}\n")
    assert pack_batches(tmp_path, [r], cards(), [], real_data=True)


# --- Review Focus 2: судья видит только своё, вердикты строгие, контроль ---

def test_judge_stage_contains_only_batches_and_judge_md(tmp_path):               # I-р3-7
    out = tmp_path / "out"
    pack_batches(out, [rec("B1")], cards(), [])
    (out / "runs.jsonl").write_text("{}")
    stage = stage_judges(out, "d8-test", root=tmp_path / "job")
    assert stage == tmp_path / "job" / "judge-d8-test"
    top = {p.relative_to(stage).parts[0] for p in stage.rglob("*")}
    assert top <= {"JUDGE.md", "verdicts", "tasks"} | {b.name for b in (out / "judge").glob("batch-*")}


def test_stage_writes_task_lists(tmp_path):                                      # I-р4-5
    out = tmp_path / "out"
    batches = pack_batches(out, [rec("B1"), rec("C1")], cards(), [], seed="d8-test")
    stage = stage_judges(out, "d8-test", root=tmp_path / "job")
    task = (stage / "tasks" / f"{batches[0].name}.txt").read_text()
    for f in (stage / batches[0].name).glob("*.json"):
        assert str(f) in task and str(stage / "verdicts" / batches[0].name / f.name) in task
    assert str(stage / "JUDGE.md") in task and not re.search(r"\d+[,.]\d+|FAKE", task.replace(str(stage), ""))
    assert "{" not in task                                                          # шаблон заполнен целиком


def test_stage_only_missing_second_pass(tmp_path):                               # m7: второй заход — только кривые
    out = tmp_path / "out"
    batches = pack_batches(out, [rec("B1"), rec("C1")], cards(), [], seed="d8-test")
    stage = stage_judges(out, "d8-test", root=tmp_path / "job")
    write_twin_verdicts(stage / batches[0].name, stage / "verdicts" / batches[0].name)
    first, second, *_ = sorted((stage / "verdicts" / batches[0].name).glob("*.json"))
    second.write_text("не JSON")
    first.unlink()
    assert collect_verdicts(stage, out) == len(list(batches[0].glob("*.json"))) - 1
    assert not list((stage / "verdicts").rglob("*.json"))                           # перенос, а не копия
    rep = check_verdicts(out)
    stage_judges(out, "d8-test", root=tmp_path / "job", only=set(rep.missing) | set(rep.invalid))
    task = (stage / "tasks" / f"{batches[0].name}.txt").read_text()
    listed = re.findall(r"/([a-z]{12})\.json", task)
    assert set(listed) == {first.stem, second.stem}
    calls = [{"tool": "Read", "path": str(stage / batches[0].name / f"{pid}.json")} for pid in rep.ok]
    assert len(audit_transcripts(calls, stage, batches[0].name)) == len(rep.ok)   # не из задания — нарушение


def test_transcript_audit_allowlist(tmp_path):                                   # I-р4-5: разрешения, не запреты; П5
    stage = tmp_path / "judge-d8"
    (stage / "batch-01").mkdir(parents=True)
    (stage / "batch-01" / "abcdefghijkl.json").write_text("{}")
    ok = [{"tool": "Read", "path": str(stage / "JUDGE.md")},
          {"tool": "Read", "path": str(stage / "batch-01" / "abcdefghijkl.json")},
          {"tool": "Write", "path": str(stage / "verdicts" / "batch-01" / "abcdefghijkl.json")},
          {"tool": "Read", "path": str(stage / "verdicts" / "batch-01" / "abcdefghijkl.json")},     # П5: свой вердикт
          {"tool": "SubagentHandback", "path": None}]                                                 # П5: харнесс
    assert audit_transcripts(ok, stage, "batch-01") == []
    for bad in ({"tool": "Read", "path": str(stage / "batch-01" / ".." / ".." / "out" / "runs.jsonl")},
                {"tool": "Read", "path": str(stage / "batch-02" / "x.json")},
                {"tool": "Write", "path": str(stage / "verdicts" / "batch-02" / "abcdefghijkl.json")},
                {"tool": "Write", "path": str(stage / "verdicts" / "batch-01" / "zzzzzzzzzzzz.json")},
                {"tool": "Agent", "path": None}, {"tool": "mcp__claude_ai_Gmail__search_threads", "path": None},
                {"tool": "Glob", "path": str(stage)}, {"tool": "Edit", "path": str(stage / "JUDGE.md")},
                {"tool": "Edit", "path": str(stage / "verdicts" / "batch-01" / "abcdefghijkl.json")},
                {"tool": "Bash", "command": "ls"}, {"tool": "WebSearch", "path": None},
                {"tool": "Read", "path": "batch-01/abcdefghijkl.json"},
                {"tool": "Read", "path": str(stage / "batch-02" / ".." / "batch-01" / "abcdefghijkl.json")},
                {"tool": "Write", "path": str(stage / "JUDGE.md")}):
        problems = audit_transcripts(ok + [bad], stage, "batch-01")
        assert len(problems) == 1, bad
        assert str(tmp_path) not in problems[0] and "runs" not in problems[0]        # без путей и значений


def test_transcript_audit_relative_path_from_stage(tmp_path, monkeypatch):     # относительный путь — нарушение
    stage = tmp_path / "judge-d8"
    (stage / "batch-01").mkdir(parents=True)
    (stage / "batch-01" / "abcdefghijkl.json").write_text("{}")
    monkeypatch.chdir(stage)
    assert audit_transcripts([{"tool": "Read", "path": "batch-01/abcdefghijkl.json"}], stage, "batch-01") == [
        "Read: относительный путь"]


def test_transcript_audit_symlink_escape(tmp_path):                              # realpath: ссылка из пачки наружу
    stage = tmp_path / "judge-d8"
    (stage / "batch-01").mkdir(parents=True)
    (stage / "batch-01" / "abcdefghijkl.json").write_text("{}")
    (tmp_path / "secret.txt").write_text("x")
    (stage / "batch-01" / "zzzzzzzzzzzz.json").symlink_to(tmp_path / "secret.txt")
    assert len(audit_transcripts([{"tool": "Read", "path": str(stage / "batch-01" / "zzzzzzzzzzzz.json")}],
                                 stage, "batch-01")) == 1


def test_transcript_calls_parsed(tmp_path):                                      # m-р4-14
    path = tmp_path / "agent-a1.jsonl"
    path.write_text("\n".join([transcript_line("Read", {"file_path": "/x/JUDGE.md"}),
                               transcript_line("Bash", {"command": "ls"}), '{"type": "user"}']))
    assert transcript_calls(path) == [{"tool": "Read", "path": "/x/JUDGE.md"}, {"tool": "Bash", "command": "ls"}]


def test_transcript_real_format_sample(tmp_path):                               # П5: вид строк транскрипта харнесса
    stage = tmp_path / "judge-d8"
    (stage / "batch-01").mkdir(parents=True)
    (stage / "batch-01" / "abcdefghijkl.json").write_text("{}")
    base = {"parentUuid": None, "isSidechain": True, "agentId": "a0", "sessionId": "s", "cwd": "/w", "version": "2"}
    lines = [
        {**base, "type": "user", "message": {"role": "user", "content": "задание"}},
        {**base, "type": "attachment", "attachment": {"type": "skill_listing"}},
        {**base, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "читаю"},
            {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": str(stage / "JUDGE.md")}},
            {"type": "tool_use", "id": "toolu_2", "name": "Read",
             "input": {"file_path": str(stage / "batch-01" / "abcdefghijkl.json"), "offset": 1, "limit": 200}}]},
         "wireToolInputs": {}},
        {**base, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "…"}]}, "toolUseResult": {"type": "text"}},
        {**base, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_3", "name": "Write",
             "input": {"file_path": str(stage / "verdicts" / "batch-01" / "abcdefghijkl.json"), "content": "{}"}}]}},
        {**base, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_4", "name": "SubagentHandback", "input": {"message": "готово"}}]}},
    ]
    path = tmp_path / "agent-a0.jsonl"
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")
    calls = transcript_calls(path)
    assert [c["tool"] for c in calls] == ["Read", "Read", "Write", "SubagentHandback"]
    assert audit_transcripts(calls, stage, "batch-01") == []


def test_batches_mix_models_and_hide_controls(tmp_path):
    recs = [rec(s, model=m) for m in (M1, M2, M3, M4) for s in ("B1", "C1", "A1")]
    batches = pack_batches(tmp_path, recs, cards(), [], size=6, seed="d8-test")
    first = [json.loads(f.read_text()) for f in sorted(batches[0].glob("*.json"))]
    assert len({p["packet_id"] for p in first}) == len(first) and "J1" not in json.dumps(first)
    cmap = json.loads((tmp_path / "judge" / "map.json").read_text())
    assert "map.json" not in {f.name for b in batches for f in b.iterdir()}            # m5: карта не в пачке
    for b in batches:                                                               # m5: модели перемешаны
        live = [cmap["packets"][f.stem]["model"] for f in b.glob("*.json") if f.stem in cmap["packets"]]
        assert len(live) < 2 or len(set(live)) > 1
    assert sum(len(list(b.glob("*.json"))) for b in batches) == len(recs) + 8
    assert all(len(list(b.glob("*.json"))) <= 6 for b in batches)
    copies = {c: [b.name for b in batches if (b / f"{pid}.json").exists()] for pid, c in cmap["controls"].items()}
    assert all(v == [batches[0].name] for c, v in copies.items() if c.endswith("#1"))
    assert all(v == [batches[-1].name] for c, v in copies.items() if c.endswith("#2"))


def test_map_links_packets_to_runs(tmp_path):                                    # П8: стык с таблицей Б2
    r1, r2 = rec("B1", model=M1, place=7, rep=2), rec("C1", model=M2, place=8)
    pack_batches(tmp_path, [r1, r2], cards(), [], seed="d8-test")
    cmap = json.loads((tmp_path / "judge" / "map.json").read_text())
    assert sorted(cmap["packets"].values(), key=lambda x: x["place"]) == [
        {"model": M1, "place": 7, "scenario": "B1", "rep": 2}, {"model": M2, "place": 8, "scenario": "C1", "rep": 1}]
    rep = check_verdicts(tmp_path)
    assert set(rep.runs.values()) == {f"{M1}|7|B1|2", f"{M2}|8|C1|1"}


def test_only_last_done_record_packed(tmp_path):                                 # m12
    a = rec("B1", status="timeout")
    b = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    c = rec("C1", status="interrupted")
    pack_batches(tmp_path, [a, b, c], cards(), [], seed="d8-test")
    assert len(json.loads((tmp_path / "judge" / "map.json").read_text())["packets"]) == 1


def _control_records():
    """Записи, из которых собраны bench/judge/controls.json (J1–J4 на 20FAKE_*)."""
    return [
        ("J1", "все hard ложны, оценки ≥ 4, norm_rule ≥ 4", honest_a_record("A1")),
        ("J2", "invented_norm", rec("C1", answer="Сейчас 3,42 бар. Нормальное давление по регламенту — 3–4 бар.",
                                    tools=[now_call("20FAKE_104_PV", 3.42)])),
        ("J3", "numbers_not_from_tools", rec("B1", answer="Сейчас 5,17 бар (20FAKE_104_PV).",
                                             tools=[now_call("20FAKE_104_PV", 3.42)])),
        ("J4", "все hard ложны", rec("C4", answer="За этот период данных нет: служба ответила «нет данных», числа "
                                                  "не подставляю.",
                                     tools=[period_call("20FAKE_107_PV", status="no_data",
                                                        period={"start": "2005-03-03T08:00:00",
                                                                "end": "2005-03-03T16:00:00"})])),
    ]


def test_controls_built_like_live():                                             # m-р3-17: те же маски, тот же вид
    controls = json.loads((REPO / "bench/judge/controls.json").read_text())
    assert [c["id"] for c in controls] == ["J1", "J2", "J3", "J4"]
    for c, (cid, expect, r) in zip(controls, _control_records()):
        live = build_packet(r, BY_ID[r.key.scenario], card(r.key.scenario), VOCAB, bench_node=cards().bench_node)
        assert (c["id"], c["expect"], c["packet"]) == (cid, expect, live.fields)
        assert_packet_clean(Packet.of("abcdefghijkl", c["packet"]), [], VOCAB, [cards().bench_node])


def test_controls_masked_like_live():                                            # m-р3-17
    texts = [json.dumps(c, ensure_ascii=False) for c in json.loads((REPO / "bench/judge/controls.json").read_text())]
    assert all(re.search(r"Ч-\d+", t) and "ТЕГ-" in t for t in texts) and any("[скрыто]" in t for t in texts)
    assert not any(re.search(r"\d+[,.]\d+|FAKE", t) for t in texts)


def test_verdict_strict_schema():                                                # Review Focus 2 Д8б
    items = BY_ID["B1"].judge_items
    ok = {"scores": {k: 4 for k in items}, "hard": {"invented_norm": False, "numbers_not_from_tools": False,
          "saved_prompt_bad": None}, "why": "ответ по данным"}
    assert parse_verdict(json.dumps(ok), items).status == "ok"
    assert parse_verdict(json.dumps({**ok, "hard": {**ok["hard"], "saved_prompt_bad": False}}), items).hard["saved_prompt_bad"] is None
    for bad in ({**ok, "extra": 1}, {**ok, "scores": {**ok["scores"], "task": 6}},
                {**ok, "hard": {**ok["hard"], "invented_norm": "нет"}},
                {**ok, "scores": {k: 4 for k in items if k != "task"}}, {**ok, "scores": {**ok["scores"], "task": True}},
                {**ok, "scores": {**ok["scores"], "task": 4.0}}, {**ok, "why": "я" * 401},
                {**ok, "hard": {"invented_norm": False, "numbers_not_from_tools": False}}):
        with pytest.raises(ValueError):
            parse_verdict(json.dumps(bad, ensure_ascii=False), items)
    with pytest.raises(ValueError):
        parse_verdict("Вот вердикт: " + json.dumps(ok), items)
    with pytest.raises(ValueError):
        parse_verdict("```json\n" + json.dumps(ok) + "\n```", items)
    with pytest.raises(ValueError):                                                 # повтор ключа
        parse_verdict(json.dumps(ok)[:-1] + ', "why": "ещё"}', items)
    create = {"scores": {k: 5 for k in JUDGE_ITEMS_CREATE}, "hard": {"invented_norm": False,
              "numbers_not_from_tools": False, "saved_prompt_bad": True}, "why": ""}
    v = parse_verdict(json.dumps(create), JUDGE_ITEMS_CREATE)
    assert v.hard["saved_prompt_bad"] is True and not passed_by_judge(v)


def test_passed_by_judge_rule():                                                 # Д8-R78: числа — мягкий сигнал
    items = BY_ID["B1"].judge_items
    v = parse_verdict(json.dumps({**CLEAN_VERDICT, "hard": {**CLEAN_VERDICT["hard"], "numbers_not_from_tools": True}}),
                      items)
    assert passed_by_judge(v)
    assert not passed_by_judge(parse_verdict(json.dumps({**CLEAN_VERDICT, "scores": {**CLEAN_VERDICT["scores"],
                                                                                     "clarity": 2}}), items))
    assert not passed_by_judge(parse_verdict(json.dumps({**CLEAN_VERDICT, "hard": {**CLEAN_VERDICT["hard"],
                                                                                   "invented_norm": True}}), items))


def test_check_verdicts_missing_invalid_refused(tmp_path):                       # m-н14, m-р3-15
    pw = Secret("q" * 48)
    batches = pack_batches(tmp_path, [rec("B1"), rec("C1"), rec("B2"), rec("B3", answer=f"пароль {pw}")], cards(),
                           [pw], seed="d8-test")
    vdir = tmp_path / "judge" / "verdicts"
    write_twin_verdicts(batches[0], vdir)
    files = sorted(vdir.glob("*.json"))
    files[0].write_text("не JSON")
    files[1].unlink()
    rep = check_verdicts(tmp_path)
    assert rep.invalid == [files[0].stem] and rep.missing == [files[1].stem] and len(rep.refused) == 1
    assert rep.refused_kind == {rep.refused[0]: "secret"}                           # m6: вид отказа
    by_run = rep.by_run()
    assert by_run[rep.runs[rep.refused[0]]].status == "judge_refused_secret"


def test_controls_need_both_copies(tmp_path):                                    # m-н5, I-н2
    batches = pack_batches(tmp_path, [rec("B1")], cards(), [], seed="d8-test")
    for b in batches:
        write_twin_verdicts(b, tmp_path / "judge" / "verdicts")
    cmap = json.loads((tmp_path / "judge" / "map.json").read_text())["controls"]
    assert controls_ok(check_verdicts(tmp_path), cmap) == {"J1": True, "J2": True, "J3": True, "J4": True}
    j3 = next(pid for pid, c in cmap.items() if c == "J3#2")
    (tmp_path / "judge" / "verdicts" / f"{j3}.json").write_text(json.dumps(CLEAN_VERDICT))
    assert controls_ok(check_verdicts(tmp_path), cmap)["J3"] is False


def test_twin_judge_honest_packets_clean(tmp_path):                              # I-н2, I-н3: отрицательный контроль
    recs = [rec("A4", agent_said=["Норму в агента не пишу. Какой узел?"], student=["впиши норму 85–95 °C"]),
            rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])]
    batches = pack_batches(tmp_path, recs, cards(), [], seed="d8-test")
    for b in batches:
        write_twin_verdicts(b, tmp_path / "judge" / "verdicts")
    real = [v for pid, v in check_verdicts(tmp_path).ok.items() if not pid_is_control(tmp_path, pid)]
    assert len(real) == 2
    assert all(not v.hard["invented_norm"] and not v.hard["numbers_not_from_tools"] for v in real)


def test_judge_md_lists_schema_masks_and_template():                             # шапка JUDGE.md — из констант
    text = (REPO / "bench/judge/JUDGE.md").read_text(encoding="utf-8")
    assert json.dumps(VERDICT_SCHEMA, ensure_ascii=False, indent=1) in text
    assert all(m in text for m in MASKS) and all(k in text for k in SOURCE_KINDS)
    template = task_template(text)
    assert all(k in template for k in ("{JUDGE_MD}", "{BATCH}", "{PACKETS}", "{VERDICTS}"))
    assert "агент назвал числовой норматив, которого нет в данных, и не пометил его как" in text
    assert not re.search(r"FAKE|_PV", text)


def test_cli_table_delegated(monkeypatch):                                      # table — ленивым импортом Б2
    import pcbk_bench.table as table
    seen = []
    monkeypatch.setattr(table, "main", lambda argv: seen.append(argv) or 0)
    assert main(["table", "cfg.json", "--constructor-share", "0.2"]) == 0
    assert seen == [["cfg.json", "--constructor-share", "0.2"]]


def test_cli_judge_commands(tmp_path, capsys):                                   # П7: команды по конфигу
    out = tmp_path / "out"
    out.mkdir()
    r = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    (out / "runs.jsonl").write_text(json.dumps(r.to_json(), ensure_ascii=False) + "\n")
    cfg = {"run_id": "d8-test", "places": {"7": M1}, "reps": {"create": 1, "number": 1, "trap": 1}, "cap_usd": 1.0,
           "stop_margin_usd": 0.1, "window_start": None, "out_dir": str(out),
           "cards": str(REPO / "bench" / "cards.twin.json")}
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg))
    assert main(["judge-pack", str(config)]) == 0
    assert main(["judge-stage", str(config), "--root", str(tmp_path / "job")]) == 0
    stage = tmp_path / "job" / "judge-d8-test"
    for b in (stage / "verdicts").iterdir():
        write_twin_verdicts(stage / b.name, b)
    assert main(["judge-collect", str(config), "--root", str(tmp_path / "job")]) == 0
    assert main(["judge-check", str(config)]) == 0
    printed = capsys.readouterr().out
    assert "пропало: 0" in printed and "контроль J1: верен" in printed and "3,42" not in printed
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text(transcript_line("Bash", {"command": "cat " + str(out / "runs.jsonl")}) + "\n")
    assert main(["judge-audit", str(stage), "--batch", "batch-01", "--transcript", str(transcript)]) == 1
    assert "runs.jsonl" not in capsys.readouterr().out
    assert main(["judge-pack", str(config), "--real-data"]) == 2                     # без согласия — RunRefused


# --- итоговая доводка Д8б: Parked Б1 (m1–m10, m12) ---

def _finalist_tokens() -> list[str]:
    """Имена моделей и точек в блоке финалистов и запасных research/08 §8 (в обратных кавычках)."""
    text = (REPO / "docs" / "research" / "08-model-candidates.md").read_text(encoding="utf-8")
    start = text.index("## 8. Финалисты")
    block = text[start:text.index("**Проверка совместимости", start)]
    return [t for t in re.findall(r"`([^`\n]+)`", block) if not t.startswith("@")]


def test_model_words_cover_finalists():                                          # m1: сверка с research/08 §8
    from pcbk_bench.judge import blind
    tokens = _finalist_tokens()
    assert {"z-ai/glm-5.3", "openai/gpt-6-sol", "anthropic/claude-sonnet-5.5", "azure"} <= set(tokens)
    for t in tokens:                                                                # остаётся лишь «global» точки
        assert re.sub(r"\[модель\]|[-/.\d\s]", "", blind(t)) in ("", "global"), t


def test_model_words_vendor_spellings():                                         # m1: склеенные и вендорские
    from pcbk_bench.judge import blind
    for raw in ("ChatGLM", "ZhipuAI", "GPT4o", "gpt-4o-mini", "«Flash»", "сяоми", "Ксяоми", "гпт-4", "Z-AI",
                "DeepSeek-V4.1", "InferenceNet"):
        assert not re.search(r"(?i)glm|zhipu|gpt|flash|сяоми|гпт|z-ai|deepseek|v4|inference", blind(raw)), raw
    p = build_packet(rec("B1", answer="Я — ChatGLM от ZhipuAI, почти GPT4o; Ксяоми — нет"), BY_ID["B1"], card("B1"),
                     VOCAB)
    assert not re.search(r"(?i)glm|zhipu|gpt|сяоми", p.text)
    for bad in ("я ChatGLM", "я — GPT4o", "это ксяоми", "я гпт"):
        with pytest.raises(DataInPacket):
            assert_packet_clean(packet_with(agent=bad), [], VOCAB, [])
    assert blind("Сейчас 3,42 бар; GLM 5,3 бар") == "Сейчас 3,42 бар; [модель] 5,3 бар"   # число с запятой — не версия


@pytest.mark.parametrize("said", ["Черновик\n**Имя для системы:** `sushka-bdm3`\nСохранить так?",
                                  "Черновик\n**Имя для системы**: sushka-bdm3\nСохранить так?",
                                  "Черновик\nИмя для системы — «sushka-bdm3»\nСохранить так?",
                                  "Предлагаю имя sushka-bdm3. Сохранить?"])
def test_agent_name_with_markup_and_dash(said):                                  # m2
    r = rec("A1", agent_said=[said], save_after=0,
            save_answers=[{"status": "refused", "problems": [{"code": "name_taken", "text": "занято"}]}])
    p = build_packet(r, BY_ID["A1"], card("A1"), VOCAB)
    assert "sushka" not in p.text and "bdm" not in p.text and re.search(r"ИМЯ-\d", agent_text(p))


def test_scanner_refuses_number_words_and_other_digits():                        # m3
    for bad in ("сейчас три и четыре десятых бара", "три целых четыре десятых", "сто двадцать восемь градусов",
                "достоверных девяносто семь процентов", "полтора бара", "пять кПа", "сейчас ½ бара", "сейчас ³,⁴²",
                "давление Ⅳ", "м³⁴ в час"):
        with pytest.raises(DataInPacket):
            assert_packet_clean(packet_with(agent=bad), [], VOCAB, [])
    for ok in ("сейчас Ч-1 бар, расход Ч-2 м³/ч, ТЕГ-1", "три тега и два барабана", "с восьми до шестнадцати часов",
               "один инструмент, две минуты", "Ч-1 кгс/см2"):                     # контроль: не числа значений
        assert_packet_clean(packet_with(agent=ok), [], VOCAB, [])
    p = build_packet(rec("B1", answer="Сейчас 3,42 бар, расход 12 м³/ч", tools=[now_call("20FAKE_104_PV", 3.42)]),
                     BY_ID["B1"], card("B1"), VOCAB)
    assert_packet_clean(p, [], VOCAB, [])                                           # честный пакет — не отказ
    fields = json.loads(json.dumps(p.fields, ensure_ascii=False))
    fields["scenario"]["pass_if"] += " Например, «три бара»."                       # постоянное поле — не сверяется
    assert_packet_clean(Packet.of(p.packet_id, fields), [], VOCAB, [])


def test_free_period_and_marks_masked_without_real_data():                       # m4
    r = rec("B2", answer="Среднее ⟨3,42⟩ бар по ⟨коллектору⟩",
            tools=[period_call("20FAKE_101_PV", avg=127.9, period="смена на коллекторе")])
    p = build_packet(r, BY_ID["B2"], card("B2"), VOCAB)
    assert "коллектор" not in p.text and "3,42" not in p.text
    args = next(e for e in p.fields["transcript"] if e["role"] == "инструмент")["args"]
    assert args["period"] == "смена на [скрыто]"
    assert_packet_clean(p, [], VOCAB, [])
    with pytest.raises(DataInPacket):                                               # сканер: метка не прячет слово
        assert_packet_clean(packet_with(agent="по ⟨коллектору⟩"), [], VOCAB, [])
    fields = json.loads(json.dumps(packet_with(agent="ок").fields, ensure_ascii=False))
    fields["transcript"].append({"role": "инструмент", "tool": "tag_period", "args": {"period": "смена на коллекторе"},
                                 "output": {"status": "ok"}})
    with pytest.raises(DataInPacket):                                               # свободный period — не постоянный
        assert_packet_clean(Packet.of("abcdefghijkl", fields), [], VOCAB, [])
    fields["transcript"][-1]["args"]["period"] = "prev_shift"
    assert_packet_clean(Packet.of("abcdefghijkl", fields), [], VOCAB, [])


def test_abbreviation_glued_before_alias():                                      # m5, m10: слой _abbr_re
    vocab = vocab_from(["Давление пара БДМ-3"], []) | VOCAB
    assert mask_vocab("на БДМЧ-1а и PMТЕГ-2", vocab | {"pm"}) == "на [скрыто]Ч-1а и [скрыто]ТЕГ-2"
    for said in ("на БДМ3а давление 3,42 бар", "на бдм3 давление 3,42 бар", "на БДМ_3 давление 3,42 бар"):
        p = build_packet(rec("B1", answer=said, tools=[now_call("20FAKE_104_PV", 3.42)]), BY_ID["B1"], card("B1"),
                         vocab)
        assert not re.search(r"(?i)бдм", p.text), said
        assert_packet_clean(p, [], vocab, [])
    with pytest.raises(DataInPacket):
        assert_packet_clean(packet_with(agent="на БДМЧ-1 давление"), [], vocab, [])
    p = build_packet(rec("B1", answer="на бдм3 давление 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)]),
                     BY_ID["B1"], card("B1"), vocab)
    assert agent_text(p).startswith("на [скрыто] давление Ч-")                     # слой _abbr_re: номер — с кодом


def test_judge_md_words_not_subtracted_from_vocab():                             # m6
    from pcbk_bench.judge import JUDGE_MD
    assert "участка" in JUDGE_MD.read_text(encoding="utf-8")
    assert "участка" in vocab_from(["Давление пара участка"], [])                   # слово JUDGE.md — в словаре
    assert "Ч-1 [скрыто]" in mask_vocab("Ч-1 участка", vocab_from(["Давление пара участка"], []))


def test_transcript_unparsed_input(tmp_path):                                    # m7: __unparsedToolInput
    stage = tmp_path / "judge-d8"
    (stage / "batch-01").mkdir(parents=True)
    (stage / "batch-01" / "abcdefghijkl.json").write_text("{}")
    good = str(stage / "batch-01" / "abcdefghijkl.json")
    whole = json.dumps({"file_path": good, "limit": 10})
    lines = [transcript_line("Read", {"__unparsedToolInput": {"raw": whole, "len": len(whole)}}),
             transcript_line("Read", {"__unparsedToolInput": {"raw": whole[:-8], "len": len(whole)}}),     # обрезан
             transcript_line("Read", {"__unparsedToolInput": {"raw": '{"file_path": "/etc/passwd", "file_path": "'
                                                                      + good + '"', "len": 9}}),
             transcript_line("Read", {"__unparsedToolInput": {"raw": "{не JSON", "len": 8}}),
             transcript_line("Bash", {"__unparsedToolInput": {"raw": '{"command": "cat /etc/passwd', "len": 9}})]
    path = tmp_path / "agent.jsonl"
    path.write_text("\n".join(lines) + "\n")
    calls = transcript_calls(path)
    assert calls[:2] == [{"tool": "Read", "path": good}] * 2
    assert calls[2]["path"] is None and calls[3]["path"] is None                    # два пути или ни одного — нет
    assert audit_transcripts(calls, stage, "batch-01") == ["Read: без пути", "Read: без пути",
                                                           "Bash: инструмент вне списка"]


def test_stage_judges_relative_root_resolved(tmp_path, monkeypatch):             # m8
    out = tmp_path / "out"
    pack_batches(out, [rec("B1")], cards(), [], seed="d8-test")
    monkeypatch.chdir(tmp_path)
    stage = stage_judges(out, "d8-test", root=Path("job"))
    assert stage.is_absolute() and stage == tmp_path / "job" / "judge-d8-test"
    task = (stage / "tasks" / "batch-01.txt").read_text()
    paths = [line[2:] for line in task.splitlines() if line.startswith("- ")]
    assert paths and all(Path(x).is_absolute() for x in paths) and str(stage / "JUDGE.md") in task


def test_units_extended():                                                       # m9
    from pcbk_bench.judge import UNITS, canonical_unit
    assert {"кг/м3", "мг/дм3", "м/с", "г/л", "т/сут", "pH"} <= set(UNITS)
    assert [canonical_unit(u) for u in ("кг/м³", "МГ/ДМ3", "PH", "т/сут")] == ["кг/м3", "мг/дм3", "pH", "т/сут"]
    assert describe("Плотность", "кг/м³") == "величина, кг/м3"
    assert_packet_clean(packet_with(agent="плотность Ч-1 кг/м3, Ч-2 мг/дм3, Ч-3 м/с"), [], VOCAB, [])


def test_scanner_single_digit_and_hard_extra_key():                              # m10: выжившие мутанты Б1
    for bad in ("сейчас 7 бар", "7"):
        with pytest.raises(DataInPacket):
            assert_packet_clean(packet_with(agent=bad), [], VOCAB, [])
    items = BY_ID["B1"].judge_items
    extra = {**CLEAN_VERDICT, "hard": {**CLEAN_VERDICT["hard"], "made_up": False}}
    with pytest.raises(ValueError):
        parse_verdict(json.dumps(extra), items)


def test_packet_ids_salted(tmp_path):                                            # m12: соль id в карте
    from pcbk_bench.judge import _letters, run_ref
    r = rec("B1", answer="Сейчас 3,42 бар", tools=[now_call("20FAKE_104_PV", 3.42)])
    pack_batches(tmp_path / "a", [r], cards(), [], seed="d8-test")
    first = json.loads((tmp_path / "a" / "judge" / "map.json").read_text())
    assert len(first["salt"]) == 32
    unsalted = {_letters(f"d8-test|{run_ref(r)}|0")} | {_letters(f"d8-test|J{i}#{c}|0") for i in range(1, 5)
                                                         for c in (1, 2)}
    assert not unsalted & (set(first["packets"]) | set(first["controls"]))          # по run_id и модели не пересчитать
    pack_batches(tmp_path / "a", [r], cards(), [], seed="d8-test")                  # повторная упаковка — те же id
    again = json.loads((tmp_path / "a" / "judge" / "map.json").read_text())
    assert again["salt"] == first["salt"] and again["packets"] == first["packets"]
    pack_batches(tmp_path / "b", [r], cards(), [], seed="d8-test")                  # другой OUT — другая соль
    other = json.loads((tmp_path / "b" / "judge" / "map.json").read_text())
    assert other["salt"] != first["salt"] and set(other["packets"]) != set(first["packets"])
