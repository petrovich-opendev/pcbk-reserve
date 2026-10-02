"""Шаблоны SQL к историану: только литералы, без LIKE и без параметров драйвера."""
import importlib.util
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import pcbk_core.data.sql as sql_module
from pcbk_core.data.sql import (DATE_FMT, HAS_LAST, MAX_NAMES, PIN_INTERPOLATION, SERIES_COLUMNS, SERIES_MAX_POINTS,
                                SERIES_MAX_TAGS, SERIES_MIN_POINTS, SHAPE, STATE_COLUMNS, SUMMARY_COLUMNS,
                                TEMPLATE_FORMS, WW_CONSTANTS, SummaryShape, allowed_literal, catalog_sql, clock_sql,
                                lit_dt, lit_name, literals, live_all_sql, live_sql, names_in, plain, scan_literals,
                                series_sql, state_sql, summary_form, summary_sql, template_of)

SAMPLES = [clock_sql(), catalog_sql(), live_all_sql(), live_sql(["20FAKE_001_PV", "20FAKE_002_SP"])]


def test_no_driver_parameters_no_like_single_statement():
    for s in SAMPLES:
        assert not re.search(r"\?|%s|%\(|@", s) and ";" not in s
        assert not re.search(r"\bLIKE\b|\bOR\b", s, re.I) and not re.search(r"\bww\w+\s+IN\b", s, re.I)


@pytest.mark.parametrize("bad", ["A'B", "A B", "20FAKE_001_PV\n", "", "ТЕГ_01", "x" * 129, "A;B", "A.B", "$Sys"])
def test_name_literal_rejects(bad):
    with pytest.raises(ValueError):
        lit_name(bad)


def test_live_sql_names_in_and_literals():
    s = live_sql(["20FAKE_001_PV", "20FAKE_002_SP"])
    assert s.endswith("FROM Live WHERE TagName IN ('20FAKE_001_PV', '20FAKE_002_SP')")
    assert names_in(s) == literals(s) == ("20FAKE_001_PV", "20FAKE_002_SP")
    assert names_in(catalog_sql()) == literals(catalog_sql()) == ()
    assert literals("SELECT 1 WHERE a = 'x''y'") == ("x'y",)
    with pytest.raises(ValueError):
        live_sql([f"20FAKE_{i:03d}_PV" for i in range(17)])


# сверх брифа: разбор литералов, на который опираются ворота


def test_live_sql_rejects_empty_and_bare_string():
    for bad in ([], "20FAKE_001_PV"):
        with pytest.raises(ValueError):
            live_sql(bad)


def test_scan_marks_literals_inside_tagname_in():
    s = "SELECT 1 FROM Live WHERE Description = 'a' AND TagName IN ('20FAKE_001_PV') AND x = 'TagName IN (''b'')'"
    assert scan_literals(s) == (("a", False), ("20FAKE_001_PV", True), ("TagName IN ('b')", False))
    assert names_in("SELECT 1 WHERE TagName NOT IN ('20FAKE_001_PV')") == ()         # NOT IN — не список имён
    assert names_in("SELECT 1 WHERE TagName IN ('20FAKE_001_PV', x)") == ()          # не только литералы


def test_plain_rejects_what_breaks_literal_parsing():
    assert all(plain(s) for s in SAMPLES)
    for bad in ("SELECT 1 -- '", "SELECT 1 /* ' */", 'SELECT "x"', "SELECT [a'b]", "SELECT 1; SELECT 2",
                "SELECT 1 WHERE a = 'open"):
        assert not plain(bad)


# ревью задачи 3: ворота сверяют форму инструкции, а не только литералы

Q = live_sql(["20FAKE_001_PV"])
BYPASSES = [
    "SELECT TagName, DateTime, Value, Quality FROM Live WHERE NOT TagName IN ('20FAKE_001_PV')",
    Q + " OR 1 = 1",
    Q + " OR TagName = CHAR(49)",
    Q + " SELECT TagName, DateTime, Value, Quality FROM Live WHERE Value IS NOT NULL",   # вторая инструкция без ;
    Q + " -- x",
    Q + " /* x */",
    "SELECT TagName FROM Live WHERE TagName = '20FAKE_001_PV'",
    Q + "\n",
]


def test_every_template_has_its_form():
    assert [template_of(s) for s in SAMPLES] == ["clock", "catalog", "live_all", "live"]
    assert template_of(live_sql([f"20FAKE_{i:03d}_PV" for i in range(MAX_NAMES)])) == "live"


@pytest.mark.parametrize("sql", BYPASSES)
def test_bypasses_match_no_template(sql):
    assert template_of(sql) is None


# Д3б, задача 2: сводка с датами; форма и SQL — из одних констант (F1)

T6, T14 = datetime(2026, 10, 1, 6), datetime(2026, 10, 1, 14)


def test_summary_template_rules():
    s = summary_sql(["20FAKE_001_PV"], T6, T14)
    assert not re.search(r"\?|%s|@|;|\bLIKE\b|\bOR\b", s) and not re.search(r"\bww\w+\s+IN\b", s)
    assert "WHERE TagName IN ('20FAKE_001_PV') AND StartDateTime >= " in s and "wwCycleCount = 1" in s
    assert ("wwInterpolationType = 'STAIRSTEP'" in s) is (PIN_INTERPOLATION == "STAIRSTEP")
    assert "wwResolution" not in s and names_in(s) == ("20FAKE_001_PV",)


def test_allowed_literals():
    assert allowed_literal(lit_dt(T6).strip("'")) and allowed_literal("STAIRSTEP")
    assert not allowed_literal("16FAKE_009_PV") and not allowed_literal("Delta")
    assert not any(allowed_literal(v) for v in date_variants(DATE_FMT))            # ревью I1


# ревью I1: цифры не ASCII (\d и strptime их принимают) и вольности strptime — не дата по формату
_ARABIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_FULLWIDTH = str.maketrans("0123456789", "０１２３４５６７８９")


def date_variants(fmt: str) -> list[str]:
    r"""Подделки даты T6 по fmt: те же числа, но текст не тот, что пишет lit_dt.

    strptime берёт цифры не ASCII в %Y и во второй цифре %S (там у него \d), форма на \d — везде.
    """
    good = T6.strftime(fmt)                                       # «… 06:00:00.000»
    body, tail = good[:-4], good[-4:]                             # «.000» — буквальная часть формата
    year_ar, year_fw = "2026".translate(_ARABIC), "2026".translate(_FULLWIDTH)
    out = [good.replace("2026", year_ar, 1).replace(":00.000", ":0٥.000"),   # пример ревью
           good.replace("2026", year_fw, 1),
           good.replace(":00.000", ":0٥.000"),                    # одна цифра не ASCII
           body.translate(_ARABIC) + tail, body.translate(_FULLWIDTH) + tail,
           good.replace(" ", "  ", 1), good.replace(" 06:", " 6:")]  # лишний пробел, час без нуля
    assert good not in out and len(set(out)) == len(out)
    return out


# F1: форма и SQL — из одних констант; каждое сочетание, которое может выбрать окно владельца
FMTS = ("%Y-%m-%d %H:%M:%S.000", "%Y%m%d %H:%M:%S.000")
SHAPES = [SummaryShape(f, pin, last) for f in FMTS for pin in (None, "STAIRSTEP") for last in (False, True)]


@pytest.mark.parametrize("shape", SHAPES, ids=repr)
def test_summary_form_built_from_same_constants(shape):
    s = summary_sql(["20FAKE_001_PV", "20FAKE_004_PV"], T6, T14, shape)
    form = summary_form(shape)
    assert form.fullmatch(s)
    one = lambda sh: summary_sql(["20FAKE_001_PV"], T6, T14, sh)
    tampered = [one(replace(shape, date_fmt=FMTS[1 - FMTS.index(shape.date_fmt)])),    # дата не по формату
                one(replace(shape, pin=None if shape.pin else "STAIRSTEP")),              # хвост интерполяции
                one(replace(shape, has_last=not shape.has_last)),                         # другие столбцы
                s.replace("TagName IN (", "TagName NOT IN ("), s + " OR 1=1",
                s.replace("wwCycleCount = 1", "wwCycleCount = 100"),
                s.replace(" AND wwCycleCount = 1", " AND wwRetrievalMode = 'Delta' AND wwCycleCount = 1")]
    start = lit_dt(T6, shape.date_fmt)
    tampered += [s.replace(start, f"'{v}'") for v in date_variants(shape.date_fmt)]   # ревью I1
    assert all(t != s for t in tampered) and not any(form.fullmatch(t) for t in tampered)


def test_summary_form_registered_from_module_constants():
    assert TEMPLATE_FORMS["summary"].pattern == summary_form().pattern
    assert template_of(summary_sql(["20FAKE_001_PV"], T6, T14)) == "summary"
    assert SUMMARY_COLUMNS == SHAPE.columns and ("Last" in SUMMARY_COLUMNS) is HAS_LAST
    assert (SHAPE.date_fmt, SHAPE.pin) == (DATE_FMT, PIN_INTERPOLATION)


def test_date_literal():
    want = {"%Y%m%d %H:%M:%S.000": "'20261001 06:00:00.000'",
            "%Y-%m-%d %H:%M:%S.000": "'2026-10-01 06:00:00.000'"}[DATE_FMT]
    assert lit_dt(datetime(2026, 10, 1, 6, 0, 0, 999_999)) == want
    with pytest.raises(ValueError):
        lit_dt(datetime(2026, 10, 1, 6, tzinfo=timezone.utc))


# ревью M1: значение интерполяции вне WW_CONSTANTS и неизвестная директива даты — ValueError при импорте


@pytest.mark.parametrize("line, bad", [(r"PIN_INTERPOLATION: str \| None = .*", 'PIN_INTERPOLATION: str | None = "Delta"'),
                                       (r"PIN_INTERPOLATION: str \| None = .*",
                                        'PIN_INTERPOLATION: str | None = "STAIRSTEP\' OR 1=1 --"'),
                                       (r"DATE_FMT: str = .*", 'DATE_FMT: str = "%y-%m-%d %H:%M:%S.000"')])
def test_bad_probe_constants_fail_at_import(tmp_path, line, bad):
    source = Path(sql_module.__file__).read_text(encoding="utf-8")
    patched, n = re.subn(rf"(?m)^{line}$", lambda _: bad, source, count=1)
    assert n == 1
    path = tmp_path / "sql_patched.py"
    path.write_text(patched, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("sql_patched", path)
    with pytest.raises(ValueError):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


def test_pin_outside_ww_constants_refused():
    for bad in ("Delta", "STAIRSTEP' OR 1=1 --"):
        with pytest.raises(ValueError):
            summary_form(SummaryShape(pin=bad))
        with pytest.raises(ValueError):
            summary_sql(["20FAKE_001_PV"], T6, T14, SummaryShape(pin=bad))


# Д9а, задача А2: ряд на сетке Cyclic и наработка StateSummaryHistory (Д9-R84, Д9-R85)

S, E = datetime(2026, 10, 1, 4), datetime(2026, 10, 1, 12)


def test_series_sql_form():                                                       # Review Focus 1
    sql = series_sql(["20FAKE_001_PV", "20FAKE_004_PV"], S, E, 24)
    assert sql == ("SELECT TagName, DateTime, Value, Quality FROM AnalogHistory WHERE TagName IN ('20FAKE_001_PV', "
                   "'20FAKE_004_PV') AND DateTime >= '2026-10-01 04:00:00.000' AND DateTime <= "
                   "'2026-10-01 12:00:00.000' AND wwRetrievalMode = 'Cyclic' AND wwCycleCount = 24")
    assert template_of(sql) == "series" and plain(sql)
    assert literals(sql)[-1] == "Cyclic" and allowed_literal("Cyclic")


def test_state_sql_form():
    sql = state_sql(["25FAKE_007_CLS"], datetime(2026, 9, 30), datetime(2026, 10, 1))
    assert sql == ("SELECT TagName, vValue, StateTimeTotal, StateTimePercent, StateCount FROM StateSummaryHistory "
                   "WHERE TagName IN ('25FAKE_007_CLS') AND StartDateTime >= '2026-09-30 00:00:00.000' "
                   "AND EndDateTime <= '2026-10-01 00:00:00.000' AND wwCycleCount = 1")
    assert template_of(sql) == "state"


@pytest.mark.parametrize("points", [0, 1, 97, -1])
def test_series_points_out_of_range(points):
    with pytest.raises(ValueError):
        series_sql(["20FAKE_001_PV"], S, E, points)


def test_new_forms_reject_variants():                                             # Review Focus 1
    base = series_sql(["20FAKE_001_PV"], S, E, 24)
    state = state_sql(["25FAKE_007_CLS"], S, E)
    for bad in (base.replace("wwCycleCount = 24", "wwCycleCount = 97"), base.replace("'Cyclic'", "'Full'"),
                base + " OR 1 = 1", base.replace("TagName IN", "TagName NOT IN"),
                base.replace(" AND wwRetrievalMode", " AND wwResolution = 60000 AND wwRetrievalMode"),
                base + "; SELECT 1", state.replace("wwCycleCount = 1", "wwCycleCount = 2"),
                state.replace("StateSummaryHistory", "AnalogSummaryHistory")):
        assert template_of(bad) is None, bad


# сверх брифа: форма ряда — ровно числа 2–96 без нулей впереди, строкой и логическим SQL не строится;
# столбцы, представления и имена — те же правила, что у live и сводки


def test_series_form_points_exactly_two_to_ninety_six():
    base = series_sql(["20FAKE_001_PV"], S, E, 24)
    good = [n for n in range(-5, 200) if template_of(base.replace("wwCycleCount = 24", f"wwCycleCount = {n}"))]
    assert good == list(range(SERIES_MIN_POINTS, SERIES_MAX_POINTS + 1)) == list(range(2, 97))
    for bad in ("024", "2.0", "1e1", "24 ", " 24", "+24", "٢٤"):
        assert template_of(base.replace("wwCycleCount = 24", f"wwCycleCount = {bad}")) is None, bad
    for bad_points in ("24", 24.0, True):
        with pytest.raises(ValueError):
            series_sql(["20FAKE_001_PV"], S, E, bad_points)


def test_new_templates_names_and_columns():
    assert (SERIES_COLUMNS, STATE_COLUMNS) == (("DateTime", "Value", "Quality"),
                                               ("vValue", "StateTimeTotal", "StateTimePercent", "StateCount"))
    assert WW_CONSTANTS == {"STAIRSTEP", "Cyclic"} and not allowed_literal("cyclic") and not allowed_literal("Full")
    four = [f"20FAKE_{i:03d}_PV" for i in range(SERIES_MAX_TAGS)]
    assert names_in(series_sql(four, S, E, 72)) == tuple(four)
    assert template_of(state_sql([f"25FAKE_{i:03d}_CLS" for i in range(MAX_NAMES)], S, E)) == "state"
    for bad in ([], "20FAKE_001_PV", four + ["20FAKE_099_PV"]):
        with pytest.raises(ValueError):
            series_sql(bad, S, E, 24)
    for bad in ([], "25FAKE_007_CLS", [f"25FAKE_{i:03d}_CLS" for i in range(MAX_NAMES + 1)]):
        with pytest.raises(ValueError):
            state_sql(bad, S, E)
    with pytest.raises(ValueError):
        series_sql(["20FAKE_001_PV' OR 1=1 --"], S, E, 24)
    with pytest.raises(ValueError):
        state_sql(["25FAKE_007_CLS"], S.replace(tzinfo=timezone.utc), E)
    for s in (series_sql(["20FAKE_001_PV"], S, E, 24), state_sql(["25FAKE_007_CLS"], S, E)):
        assert not re.search(r"\?|%s|@|;|\bLIKE\b|\bOR\b", s) and not re.search(r"\bww\w+\s+IN\b", s)
