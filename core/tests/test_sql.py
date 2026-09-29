"""Шаблоны SQL к историану: только литералы, без LIKE и без параметров драйвера."""
import re

import pytest

from pcbk_core.data.sql import (MAX_NAMES, catalog_sql, clock_sql, lit_name, literals, live_all_sql, live_sql,
                                names_in, plain, scan_literals, template_of)

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
