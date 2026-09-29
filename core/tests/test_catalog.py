"""Каталог тегов в памяти: счётчики, свойства тега и набор свежести."""
from datetime import datetime

from fakes import CATALOG_ROWS, LIVE_ROWS, WHITELIST
from pcbk_core.data.catalog import Catalog, TagInfo


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
    assert cat._tags.get("20FAKE_001_PV") == TagInfo(
        name="20FAKE_001_PV", description="Факт. знач. - расход массы", kind="analog", unit="л/час",
        min_eu=0.0, max_eu=100.0, live=True, live_time=datetime(2026, 10, 1, 11, 59, 18))
    assert cat._tags.get("20FAKE_002_SP").unit is None                  # литерал None в Unit — «не задана»
    closed = cat._tags.get("25FAKE_007_CLS")
    assert (closed.kind, closed.unit, closed.min_eu, closed.max_eu, closed.live) == ("discrete", None, None, None, True)
    level = cat._tags.get("20FAKE_003_PV")
    assert (level.live, level.live_time) == (False, None)
    assert cat._tags.get("20FAKE_404_PV") is None


def test_unit_blank_markers_and_null_value_is_not_live():
    rows = [("20FAKE_001_PV", None, 1, 0, 10, "  "), ("20FAKE_002_PV", "d", 1, 0, 10, "null"),
            ("20FAKE_003_PV", "d", 1, 0, 10, " мбар ")]
    live = [("20FAKE_001_PV", datetime(2026, 10, 1, 11, 0), None, 0)]
    cat = Catalog.from_rows(rows, live, frozenset({"20FAKE_001_PV", "20FAKE_002_PV", "20FAKE_003_PV"}))
    first = cat._tags.get("20FAKE_001_PV")
    assert (first.description, first.unit, first.min_eu, first.live) == ("", None, 0.0, False)
    assert cat._tags.get("20FAKE_002_PV").unit is None and cat._tags.get("20FAKE_003_PV").unit == "мбар"
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
    assert cat._tags.get("20FAKE_001_PV").description == "первая"
