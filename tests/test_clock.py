"""Shift times are Edmonton wall-clock; stored timestamps are UTC instants.

Confusing the two made the hub report the Evening shift at 10:47 in the
morning, because UTC was six hours ahead of the sites.
"""
import sys
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

from app.business import shift_window_minutes
from app.clock import company_tz, local_minutes_now, local_now, local_today, utc_now_naive

EDM = ZoneInfo("America/Edmonton")
MORNING = (time(7, 0), time(15, 0))
EVENING = (time(15, 0), time(23, 0))


def _covers(window, minutes: int) -> bool:
    lo, hi = shift_window_minutes(*window)
    return lo <= minutes < hi


def test_company_timezone_is_edmonton():
    assert str(company_tz()) == "America/Edmonton"


def test_local_now_is_not_utc_during_mdt():
    # Edmonton is never at UTC, so these must differ.
    assert local_now().utcoffset() != timezone.utc.utcoffset(None)


def test_summer_offset_is_mdt():
    assert datetime(2026, 8, 24, 12, tzinfo=EDM).utcoffset().total_seconds() == -6 * 3600


def test_winter_offset_is_mst():
    # The switch is handled by the zone, not by us.
    assert datetime(2026, 1, 15, 12, tzinfo=EDM).utcoffset().total_seconds() == -7 * 3600


def test_the_original_bug_case():
    """10:47 Edmonton is Morning. The same instant in UTC is 16:47, which
    falls in Evening — that mismatch was the bug."""
    local_minutes = 10 * 60 + 47
    utc_minutes = 16 * 60 + 47
    assert _covers(MORNING, local_minutes)
    assert not _covers(EVENING, local_minutes)
    assert _covers(EVENING, utc_minutes)      # what it used to do
    assert not _covers(MORNING, utc_minutes)


def test_local_minutes_now_matches_local_clock():
    n = local_now()
    assert local_minutes_now() == n.hour * 60 + n.minute


def test_local_today_matches_local_date():
    assert local_today() == local_now().date()


def test_stored_instants_remain_utc():
    """Recording when something happened must not drift with the local zone."""
    drift = abs((utc_now_naive() - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
    assert drift < 2


def test_late_evening_local_is_still_today():
    """A 23:00 Edmonton shift belongs to today, even though UTC has rolled over."""
    late = datetime(2026, 8, 24, 23, 30, tzinfo=EDM)
    assert late.date().day == 24
    assert late.astimezone(timezone.utc).day == 25   # why UTC gave the wrong day
