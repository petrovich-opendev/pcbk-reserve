"""Шаблоны SQL к историану: только литералы, без LIKE и без параметров драйвера."""
import re
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from pcbk_core.data.sql import (DATE_FMT, HAS_LAST, MAX_NAMES, PIN_INTERPOLATION, SHAPE, SUMMARY_COLUMNS,
                                TEMPLATE_FORMS, SummaryShape, allowed_literal, catalog_sql, clock_sql, lit_dt,
                                lit_name, literals, live_all_sql, live_sql, names_in, plain, scan_literals,
                                summary_form, summary_sql, template_of)

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
    assert not any(form.fullmatch(t) for t in tampered)


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
