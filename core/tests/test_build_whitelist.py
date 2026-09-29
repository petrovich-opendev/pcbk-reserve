"""Правило белого списка d3-1, запись файла и построитель — на синтетике, без историана."""
import pytest

from fakes import CATALOG_ROWS, EXTRA, WHITELIST, FakeHistorian
from helpers import write
from pcbk_core.data import build_whitelist
from pcbk_core.data.build_whitelist import RULE_VERSION, main, select_whitelist, summary_lines, write_list
from pcbk_core.data.historian import HistorianError
from pcbk_core.data.names import load_names
from pcbk_core.data.sql import catalog_sql

ENV = "BDRV_HOST=h\nBDRV_USER=u\nBDRV_PW=<test-pw>\n"


def test_select_whitelist_rule_d3_1():
    assert select_whitelist(CATALOG_ROWS, EXTRA) == sorted(WHITELIST)


def test_lmn_excluded_also_from_extra():
    got = select_whitelist(CATALOG_ROWS, EXTRA)
    assert "20FAKE_005_LMN" not in got and "QFAKE_011_LMN" not in got


def test_base_replaces_section_rule():                # выбор владельца: прежний список + правила 4–6
    assert select_whitelist(CATALOG_ROWS, frozenset(), base=frozenset({"20FAKE_001_PV", "20FAKE_005_LMN"})) \
           == ["20FAKE_001_PV"]


def test_write_list_refuses_empty_and_is_atomic(tmp_path):
    p = tmp_path / "whitelist.txt"
    write_list(str(p), ["20FAKE_001_PV"], {"rule": RULE_VERSION})
    assert oct(p.stat().st_mode & 0o777) == "0o444" and "# rule: d3-1" in p.read_text()
    with pytest.raises(ValueError):
        write_list(str(p), [], {"rule": RULE_VERSION})
    assert load_names(str(p)) == {"20FAKE_001_PV"}


def test_load_names_rejects_bad_line(tmp_path):
    p = tmp_path / "w"
    p.write_text("# шапка\n20FAKE_001_PV\n\n20FAKE 002\n")
    with pytest.raises(ValueError, match="строка 4"):
        load_names(str(p))


def test_load_names_skips_comments_blank_and_missing_file(tmp_path):
    p = tmp_path / "w"
    p.write_text("﻿# шапка\n  20FAKE_001_PV  \r\n\n   \n# 20FAKE_BAD LINE\n20FAKE_001_PV\nQFAKE_010\n")
    assert load_names(str(p)) == frozenset({"20FAKE_001_PV", "QFAKE_010"})
    with pytest.raises(ValueError, match="^строка 1: недопустимое имя$"):
        load_names(write(tmp_path, "$FAKE_SYS\n"))
    with pytest.raises(FileNotFoundError):
        load_names(str(tmp_path / "нет-файла"))


def test_rule4_ignores_case_rule5_only_state_tails():
    rows = [("20FAKE_013_lmn", "", 1, 0.0, 1.0, "None"), ("20FAKE_014_M3", "", 1, 0.0, 1.0, "None"),
            ("20FAKE_015_th", "", 1, 0.0, 1.0, "None"), ("20FAKE_016_MV", "", 1, 0.0, 1.0, "None"),
            ("21FAKE_017_RUN", "", 2, None, None, None), ("22FAKE_018_PV", "", 2, None, None, None),
            ("21FAKE_023_run", "", 2, None, None, None),       # правило 5 — с регистром, как в прежнем правиле
            ("23FAKE_019_ALM", "", 1, 0.0, 1.0, "None"),       # аналоговый с хвостом состояния — правило 4 не режет
            ("26FAKE_020_PV", "", 1, 0.0, 1.0, "None"), ("2FAKE_021_PV", "", 1, 0.0, 1.0, "None"),
            ("24FAKE_022_PV", "", 3, None, None, None)]        # не аналоговый и не дискретный
    assert select_whitelist(rows, frozenset()) == ["20FAKE_016_MV", "21FAKE_017_RUN", "23FAKE_019_ALM"]


def test_extra_and_base_need_catalog_and_rule_1():
    rows = [("QFAKE_010", "", 1, 0.0, 1.0, "None"), ("QFAKE.BAD", "", 1, 0.0, 1.0, "None")]
    assert select_whitelist(rows, frozenset({"QFAKE_010", "QFAKE.BAD", "QFAKE_GONE"})) == ["QFAKE_010"]
    # при base «участок» больше не работает, extra — работает
    assert select_whitelist(CATALOG_ROWS, frozenset({"QFAKE_010"}), base=frozenset({"20FAKE_404_PV"})) \
           == ["QFAKE_010"]


def test_rule5_applies_to_extra_and_base():         # правило 6: дискретные из extra и base — только состояния
    rows = [("QFAKE_DIAG", "", 2, None, None, None), ("QFAKE_PUMP_RUN", "", 2, None, None, None)]
    both = frozenset({"QFAKE_DIAG", "QFAKE_PUMP_RUN"})
    assert select_whitelist(rows, both) == ["QFAKE_PUMP_RUN"]
    assert select_whitelist(rows, frozenset(), base=both) == ["QFAKE_PUMP_RUN"]


def test_summary_counts_section_names_without_letter_third():
    count = "участок 20–25 без буквы третьим знаком: {}"
    selected = select_whitelist(CATALOG_ROWS, EXTRA)
    assert count.format(0) in summary_lines(CATALOG_ROWS, EXTRA, None, selected)
    rows = CATALOG_ROWS + [("209FAKE_001_PV", "", 1, 0.0, 1.0, "None"), ("20_FAKE_002_PV", "", 1, 0.0, 1.0, "None"),
                           ("269FAKE_003_PV", "", 1, 0.0, 1.0, "None")]    # 26 — вне участка, не считается
    selected = select_whitelist(rows, EXTRA)
    assert "209FAKE_001_PV" in selected and "20_FAKE_002_PV" in selected
    lines = summary_lines(rows, EXTRA, None, selected)
    assert count.format(2) in lines and not any("FAKE" in line for line in lines)
    only_digit = summary_lines(rows, EXTRA, None, [n for n in selected if n != "20_FAKE_002_PV"])
    assert count.format(1) in only_digit


def test_write_list_replaces_read_only_file_and_checks_input(tmp_path):
    p = tmp_path / "whitelist.txt"
    write_list(str(p), ["20FAKE_001_PV"], {"rule": RULE_VERSION})
    write_list(str(p), ["20FAKE_002_SP", "QFAKE_010"], {"rule": RULE_VERSION, "section": "base"})
    text = p.read_text()
    assert text.startswith("# rule: d3-1\n# section: base\n") and text.endswith("QFAKE_010\n")
    assert load_names(str(p)) == {"20FAKE_002_SP", "QFAKE_010"}
    for names, header in ((["20FAKE 001"], {"rule": RULE_VERSION}),
                          (["20FAKE_001_PV"], {"rule": "d3-1\n20FAKE_BAD"}),
                          (["20FAKE_001_PV"], {"Bad Key": "x"})):
        with pytest.raises(ValueError):
            write_list(str(p), names, header)
    assert load_names(str(p)) == {"20FAKE_002_SP", "QFAKE_010"}
    assert [f.name for f in tmp_path.iterdir()] == ["whitelist.txt"]      # временных файлов не осталось


def _run(monkeypatch, tmp_path, fake, *extra_args, extra_text="QFAKE_010\nQFAKE_011_LMN\nQFAKE_GONE\n"):
    seen = []

    def fake_tds_query(cfg):
        seen.append(cfg)
        return fake

    monkeypatch.setattr(build_whitelist, "tds_query", fake_tds_query)
    extra = tmp_path / "whitelist.extra"
    extra.write_text(extra_text)
    out = tmp_path / "whitelist.txt"
    code = main(["--out", str(out), "--extra", str(extra), "--env-file", write(tmp_path, ENV), *extra_args])
    return code, out, seen


def test_main_builds_list_with_one_catalog_query_and_prints_only_counts(monkeypatch, tmp_path, capsys):
    fake = FakeHistorian()
    code, out, seen = _run(monkeypatch, tmp_path, fake)
    assert code == 0 and fake.calls == [[catalog_sql()]] and fake.timeouts == [120.0]
    assert seen[0].password == "<test-pw>"
    assert load_names(str(out)) == WHITELIST and oct(out.stat().st_mode & 0o777) == "0o444"
    head = out.read_text().splitlines()[:2]
    assert head == ["# rule: d3-1", "# section: 20-25"]
    printed = capsys.readouterr()
    text = printed.out + printed.err
    assert "FAKE" not in text and "<test-pw>" not in text                  # только числа, без имён
    for line in ("каталог: всего 13, аналоговых 10, дискретных 3",
                 "whitelist.extra: найдено 2, нет в каталоге 1",
                 "отсечено правилом 1 (имя вне [A-Za-z0-9_]): 2",
                 "отсечено правилом 4 (служебные хвосты аналоговых): 3 — "
                 "_LMN 2, _TH 0, _HMI 0, _SP_HMI 1, _MV1 0, _m3 0",
                 "отсечено правилом 5 (дискретные не состояния): 1",
                 "белый список: 6 — аналоговых 5, дискретных 1",
                 "участок 20–25 без буквы третьим знаком: 0",
                 "по хвостам: _PV 3, _SP 1, _CLS 1, без хвоста 1"):
        assert line in printed.out.splitlines()


def test_main_base_variant(monkeypatch, tmp_path, capsys):
    base = tmp_path / "prior.txt"
    base.write_text("20FAKE_001_PV\n20FAKE_005_LMN\n16FAKE_009_PV\n20FAKE_404_PV\n")
    code, out, _ = _run(monkeypatch, tmp_path, FakeHistorian(), "--base", str(base), extra_text="")
    assert code == 0 and load_names(str(out)) == {"20FAKE_001_PV", "16FAKE_009_PV"}
    assert out.read_text().splitlines()[:2] == ["# rule: d3-1", "# section: base"]
    printed = capsys.readouterr().out.splitlines()
    assert "прежний список (--base): найдено 3, нет в каталоге 1" in printed
    assert "whitelist.extra: найдено 0, нет в каталоге 0" in printed


def test_main_empty_result_keeps_old_file(monkeypatch, tmp_path, capsys):
    out = tmp_path / "whitelist.txt"
    write_list(str(out), ["20FAKE_001_PV"], {"rule": RULE_VERSION})
    before = out.read_text()
    outside = lambda statements, timeout: [[("16FAKE_009_PV", "", 1, 0.0, 1.0, "None")]]  # noqa: E731
    code, _, _ = _run(monkeypatch, tmp_path, outside, extra_text="")
    assert code == 1 and out.read_text() == before
    assert "пустой результат — файл не перезаписан" in capsys.readouterr().err


def test_main_historian_error_prints_code_only(monkeypatch, tmp_path, capsys):
    fake = FakeHistorian(fail=HistorianError("auth", "Login failed for user 'FAKEUSER'", kind="OperationalError",
                                             msg_no=18456))
    code, out, _ = _run(monkeypatch, tmp_path, fake)
    err = capsys.readouterr().err
    assert code == 1 and not out.exists()
    assert "auth OperationalError msg_no=18456" in err and "FAKEUSER" not in err


def test_main_bad_list_fails_before_historian(monkeypatch, tmp_path, capsys):
    fake = FakeHistorian()
    code, out, seen = _run(monkeypatch, tmp_path, fake, extra_text="QFAKE_010\nQFAKE 011\n")
    err = capsys.readouterr().err
    assert code == 1 and fake.calls == [] and seen == [] and not out.exists()
    assert "whitelist.extra: строка 2: недопустимое имя" in err
