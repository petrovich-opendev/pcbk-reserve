"""Свежесть историана: возраст по росту наибольшей метки набора и по часам историана."""
from datetime import datetime, timedelta, timezone

from pcbk_core.data.freshness import ERROR_TEXTS, FreshnessTracker
from pcbk_core.data.historian import HistClock

CLOCK = HistClock(local=datetime(2026, 10, 1, 12, 0, 0), utc=datetime(2026, 10, 1, 7, 0, 0))
W = datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)


def rows(*stamps):
    return [(f"20FAKE_{i:03d}_PV", s, 1.0, 0) for i, s in enumerate(stamps)]


def test_age_from_historian_clock():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 59, 18)), 1, mono=100.0, wall=W)
    assert (f.to_json(100.0)["age_s"], f.to_json(110.0)["age_s"]) == (42.0, 52.0)


def test_future_stamps_use_time_since_advance():                               # Review Focus 5
    f, ahead = FreshnessTracker(), datetime(2026, 10, 1, 12, 0, 31)
    f.observe(CLOCK, rows(ahead), 1, mono=100.0, wall=W)
    assert (f.to_json(100.0)["age_s"], f.to_json(100.0)["skew_s"]) == (0.0, -31.0)
    f.observe(CLOCK, rows(ahead), 1, mono=130.0, wall=W)                        # не выросла
    assert f.to_json(160.0)["age_s"] == 60.0
    f.observe(CLOCK, rows(ahead + timedelta(seconds=30)), 1, mono=190.0, wall=W)
    assert f.to_json(191.0)["age_s"] == 1.0


def test_drill_freeze_stamps_ages():                                           # Review Focus 5
    f, t = FreshnessTracker(freeze_stamps=True), datetime(2026, 10, 1, 12, 0, 31)
    for i, mono in enumerate((100.0, 130.0, 160.0)):
        f.observe(CLOCK, rows(t + timedelta(seconds=30 * i)), 1, mono=mono, wall=W)
    assert f.to_json(220.0)["age_s"] == 120.0                                   # как у замершей подачи


def test_old_data_at_start_is_old_at_once():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 0)), 1, mono=5.0, wall=W)
    assert f.to_json(5.0)["age_s"] == 3600.0


def test_error_texts():
    f = FreshnessTracker()
    assert f.to_json(0.0)["checked_at"] is None
    f.fail("auth", 8, mono=1.0, wall=W)
    assert f.to_json(1.0)["error_text"] == "историан отклонил вход — проверьте bdrv.env и базу, перезапустите службу"
    f.observe(CLOCK, [], 8, mono=2.0, wall=W)
    assert f.to_json(2.0)["error"] == "no_rows"


# --- сверх брифа ---

def test_empty_tracker_json_shape():
    assert FreshnessTracker().to_json(5.0) == {"checked_at": None, "age_s": None, "skew_s": None,
                                               "error": None, "error_text": None, "tags": 0}


def test_max_stamp_of_set_and_checked_at_in_utc():
    f = FreshnessTracker()
    wall = datetime(2026, 10, 1, 12, 0, 5, tzinfo=timezone(timedelta(hours=5)))
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 50), datetime(2026, 10, 1, 11, 59, 30),
                          datetime(2026, 10, 1, 11, 55)), 3, mono=10.0, wall=wall)
    j = f.to_json(10.0)
    assert (j["age_s"], j["skew_s"], j["tags"], j["error"], j["error_text"]) == (30.0, 0.0, 3, None, None)
    assert j["checked_at"] == "2026-10-01T07:00:05+00:00"


def test_fail_keeps_stamps_and_age_keeps_growing():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 59, 50)), 8, mono=100.0, wall=W)
    later = W + timedelta(seconds=30)
    f.fail("connect", 8, mono=130.0, wall=later)
    j = f.to_json(130.0)
    assert (j["age_s"], j["skew_s"], j["error"], j["error_text"]) == (40.0, 0.0, "connect", "нет связи с историаном")
    assert j["checked_at"] == later.isoformat()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 59, 55)), 8, mono=160.0, wall=W)     # связь вернулась
    assert (f.to_json(160.0)["error"], f.to_json(160.0)["error_text"], f.to_json(160.0)["age_s"]) == (None, None, 5.0)


def test_no_rows_keeps_past_stamps():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 59, 0)), 8, mono=100.0, wall=W)
    f.observe(CLOCK, [("20FAKE_001_PV", None, 1.0, 0)], 8, mono=110.0, wall=W)          # меток нет — как строк нет
    j = f.to_json(110.0)
    assert (j["error"], j["error_text"], j["age_s"]) == ("no_rows", "нет меток по тегам свежести", 70.0)


def test_stamp_going_back_is_not_advance():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 12, 0, 20)), 8, mono=100.0, wall=W)
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 12, 0, 10)), 8, mono=130.0, wall=W)     # набор сменился
    assert f.to_json(130.0)["age_s"] == 30.0
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 12, 0, 15)), 8, mono=160.0, wall=W)     # рост от прошлой
    assert f.to_json(160.0)["age_s"] == 0.0


def test_aware_stamp_is_compared_in_historian_zone():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 6, 59, 0, tzinfo=timezone.utc)), 1, mono=1.0, wall=W)
    assert f.to_json(1.0)["age_s"] == 60.0


def test_age_rounded_to_tenth():
    f = FreshnessTracker()
    f.observe(CLOCK, rows(datetime(2026, 10, 1, 11, 59, 59, 960000)), 1, mono=1.0, wall=W)
    assert (f.to_json(1.0)["age_s"], f.to_json(3.26)["age_s"]) == (0.0, 2.3)


def test_error_texts_cover_all_codes():
    assert set(ERROR_TEXTS) == {"connect", "timeout", "query", "auth", "catalog", "no_tags", "no_rows"}
    assert all(isinstance(t, str) and t for t in ERROR_TEXTS.values())
    assert max(len(t) for t in ERROR_TEXTS.values()) <= 80                      # сторож показывает 80 знаков
