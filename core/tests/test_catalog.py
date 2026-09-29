"""Каталог тегов в памяти: счётчики, свойства тега, набор свежести, разбор имён и поиск."""
from datetime import datetime

import pytest

from fakes import CATALOG_ROWS, LIVE_ROWS, WHITELIST
from pcbk_core.data.catalog import Catalog, Lookup, SearchResult, TagInfo, norm


def test_catalog_counts_and_freshest():
    cat = Catalog.from_rows(CATALOG_ROWS, LIVE_ROWS, WHITELIST | {"20FAKE_404_PV"})
    assert (cat.loaded, cat.total, cat.whitelisted, cat.missing) == (True, 13, 6, 1)
    assert cat.freshest(8) == ("20FAKE_004_PV", "20FAKE_001_PV", "20FAKE_002_SP")   # 16FAKE — не в списке


def test_empty_catalog():
    assert Catalog.empty().loaded is False and Catalog.empty().freshest() == ()


def test_live_counts_whitelist_tags_only():
    cat = Catalog.from_rows(CATALOG_ROWS, LIVE_ROWS, WHITELIST)
    # живые из белого списка: 001, 002, 004 и дискретный 007; 16FAKE_009 живой, но вне списка
    assert cat.live == 4
    empty = Catalog.empty()
    assert (empty.total, empty.whitelisted, empty.live, empty.missing) == (0, 0, 0, 0)


def test_tag_info_fields_and_unit_none_literal():
    cat = Catalog.from_rows(CATALOG_ROWS, LIVE_ROWS, WHITELIST)
    assert cat.lookup("20FAKE_001_PV").info == TagInfo(
        name="20FAKE_001_PV", description="Факт. знач. - расход массы", kind="analog", unit="л/час",
        min_eu=0.0, max_eu=100.0, live=True, live_time=datetime(2026, 10, 1, 11, 59, 18))
    assert cat.lookup("20FAKE_002_SP").info.unit is None                  # литерал None в Unit — «не задана»
    closed = cat.lookup("25FAKE_007_CLS").info
    assert (closed.kind, closed.unit, closed.min_eu, closed.max_eu, closed.live) == ("discrete", None, None, None, True)
    level = cat.lookup("20FAKE_003_PV").info
    assert (level.live, level.live_time) == (False, None)
    assert cat.lookup("20FAKE_404_PV") == Lookup("unknown", None, ())


def test_unit_blank_markers_and_null_value_is_not_live():
    rows = [("20FAKE_001_PV", None, 1, 0, 10, "  "), ("20FAKE_002_PV", "d", 1, 0, 10, "null"),
            ("20FAKE_003_PV", "d", 1, 0, 10, " мбар ")]
    live = [("20FAKE_001_PV", datetime(2026, 10, 1, 11, 0), None, 0)]
    cat = Catalog.from_rows(rows, live, frozenset({"20FAKE_001_PV", "20FAKE_002_PV", "20FAKE_003_PV"}))
    first = cat.lookup("20FAKE_001_PV").info
    assert (first.description, first.unit, first.min_eu, first.live) == ("", None, 0.0, False)
    assert cat.lookup("20FAKE_002_PV").info.unit is None and cat.lookup("20FAKE_003_PV").info.unit == "мбар"
    assert cat.freshest() == ()


def test_freshest_limit_ties_and_newest_live_row():
    rows = [(f"20FAKE_{i:03d}_PV", "", 1, 0.0, 1.0, "None") for i in range(1, 12)]
    t = datetime(2026, 10, 1, 11, 0)
    live = [(f"20FAKE_{i:03d}_PV", t.replace(minute=i), 1.0, 0) for i in range(1, 12)]
    live += [("20FAKE_001_PV", t.replace(minute=59), 1.0, 0),      # у тега две строки — берётся новая
             ("20FAKE_002_PV", t.replace(minute=11), 1.0, 0)]      # равная метка — порядок по имени
    cat = Catalog.from_rows(rows, live, frozenset(r[0] for r in rows))
    got = cat.freshest()
    assert len(got) == 8 and got[:3] == ("20FAKE_001_PV", "20FAKE_002_PV", "20FAKE_011_PV")
    assert cat.freshest(1) == ("20FAKE_001_PV",) and cat.freshest(0) == ()


def test_unknown_tag_type_and_duplicate_rows_skipped():
    rows = [("20FAKE_001_PV", "первая", 1, 0.0, 1.0, "None"), ("20FAKE_001_PV", "вторая", 1, 0.0, 1.0, "None"),
            ("20FAKE_002_PV", "строковый", 3, None, None, None)]
    cat = Catalog.from_rows(rows, [], frozenset({"20FAKE_001_PV", "20FAKE_002_PV"}))
    assert (cat.total, cat.whitelisted, cat.missing) == (1, 1, 1)
    assert cat.lookup("20FAKE_001_PV").info.description == "первая"


# --- Д3б, задача 4: разбор имён и поиск по белому списку ---

@pytest.fixture
def cat():
    return Catalog.from_rows(CATALOG_ROWS, LIVE_ROWS, WHITELIST)


def test_lookup_statuses_and_no_metadata_outside_list(cat):
    assert (cat.lookup("  20fake_001_pv ").status, cat.lookup("  20fake_001_pv ").info.name) == ("ok", "20FAKE_001_PV")
    unl = cat.lookup("20FAKE_005_LMN")
    assert (unl.status, unl.info) == ("unlisted", None)
    miss = cat.lookup("20FAKE_001")
    assert miss.status == "unknown" and miss.info is None and "20FAKE_001_PV" in miss.similar


def test_search_words_yo_all_words_and_similar(cat):
    assert [t.name for t in cat.search("РАСХОД массы").matches] == ["20FAKE_001_PV", "20FAKE_002_SP"]
    assert [t.name for t in cat.search("емкости").matches] == ["20FAKE_003_PV"]    # в описании — «ёмкости»
    r = cat.search("расход клапан")
    assert r.similar and {"20FAKE_001_PV", "25FAKE_007_CLS"} <= {t.name for t in r.matches}
    assert cat.search("вне участка").matches == () and cat.search("системный").matches == ()


# --- сверх брифа ---

def test_norm_lower_yo_and_spaces():
    assert norm("  Уровень\tв   ЁМКОСТИ\n") == "уровень в емкости" and norm("") == ""


def test_lookup_similar_by_stem_whitelist_only_and_capped():
    rows = [(f"20FAKE_{i:03d}_PV", "", 1, 0.0, 1.0, "None") for i in range(1, 9)]
    rows += [("20FAKE_001_SP", "", 1, 0.0, 1.0, "None"), ("20FAKE_001_LMN", "", 1, 0.0, 1.0, "None")]
    listed = frozenset(r[0] for r in rows) - {"20FAKE_001_LMN"}
    cat = Catalog.from_rows(rows, [], listed | {"20FAKE_404_PV"})
    typo = cat.lookup("20fake_001_xx")                       # та же основа 20FAKE_001, хвост с ошибкой
    assert typo == Lookup("unknown", None, ("20FAKE_001_PV", "20FAKE_001_SP"))   # _LMN вне списка — не виден
    assert len(cat.lookup("20FAKE_0").similar) == 5          # «содержит введённое» — не больше 5
    assert cat.lookup("20FAKE_404_PV").status == "unknown"   # в списке, но нет в каталоге
    assert cat.lookup("   ") == Lookup("unknown", None, ())
    assert cat.lookup("X' OR 1=1--") == Lookup("unknown", None, ())
    assert cat.lookup("20FAKE_001_LMN") == Lookup("unlisted", None, ())


def test_search_order_limit_total_and_whitelist_only(cat):
    rows = [("20FAKE_001_PV_B", "", 1, 0.0, 1.0, "None"), ("20FAKE_001_PV", "", 1, 0.0, 1.0, "None"),
            ("10FAKE_020FAKE_001_PV", "", 1, 0.0, 1.0, "None"), ("20FAKE_001_PV_A", "", 1, 0.0, 1.0, "None")]
    order = Catalog.from_rows(rows, [], frozenset(r[0] for r in rows))
    got = order.search("20fake_001_pv")
    assert [t.name for t in got.matches] == ["20FAKE_001_PV", "20FAKE_001_PV_A", "20FAKE_001_PV_B",
                                             "10FAKE_020FAKE_001_PV"]            # точное, с начала, по имени
    assert (got.similar, got.total) == (False, 4)
    limited = order.search("fake", limit=2)
    assert (len(limited.matches), limited.total) == (2, 4)
    everything = cat.search("fake")
    assert {t.name for t in everything.matches} == WHITELIST and everything.total == 6
    assert cat.search("регулятор").matches == ()                                  # 20FAKE_005_LMN вне списка
    assert cat.search("   ") == SearchResult((), True, 0)


def test_search_similar_ranked_by_words_and_by_stem(cat):
    r = cat.search("факт давление клапан")
    names = [t.name for t in r.matches]
    assert r.similar and names[0] == "20FAKE_004_PV"                              # два слова из трёх
    assert set(names) == {"20FAKE_001_PV", "20FAKE_003_PV", "20FAKE_004_PV", "25FAKE_007_CLS"}
    stem = cat.search("20FAKE_004_XX")                          # похоже на имя: основа 20FAKE_004
    assert (stem.similar, [t.name for t in stem.matches], stem.total) == (True, ["20FAKE_004_PV"], 1)
    assert cat.search("20FAKE_005_XX") == SearchResult((), True, 0)               # основа только у тега вне списка
