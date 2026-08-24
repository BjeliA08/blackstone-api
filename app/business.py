"""
Business logic ported from the Discord bot.
All overnight-aware — a shift whose end_time <= start_time crosses midnight.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Optional
from .models import Assignment, CheckIn, CheckInStatus


@dataclass
class Candidate:
    """A single operator's offer for one slot, as the selection rule sees it."""
    operator_id: object
    operator_name: str
    coverage_type: str  # "full" | "partial_fallback"
    earliest_start: Optional[time] = None
    latest_end: Optional[time] = None


def _to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def shift_duration_hours(start: time, end: time) -> float:
    """Decimal hours for a shift, handling overnight crossings."""
    s, e = _to_minutes(start), _to_minutes(end)
    if e <= s:
        e += 1440
    return (e - s) / 60


def times_overlap(s1: time, e1: time, s2: time, e2: time) -> bool:
    """True if two time ranges overlap (overnight-aware)."""
    a1, b1 = _to_minutes(s1), _to_minutes(e1)
    a2, b2 = _to_minutes(s2), _to_minutes(e2)

    if b1 <= a1:
        b1 += 1440
    if b2 <= a2:
        b2 += 1440

    # Check overlap in three alignments to handle midnight wraps
    for offset in (0, 1440, -1440):
        if a1 < b2 + offset and a2 + offset < b1:
            return True
    return False


def operator_has_overlap(
    db,
    operator_id,
    shift_date: date,
    start: time,
    end: time,
    exclude_assignment_id=None,
) -> bool:
    """Return True if the operator already has an assignment on shift_date that overlaps start-end."""
    from sqlalchemy.orm import Session
    from .models import Shift
    from sqlalchemy import and_

    existing: list[Assignment] = (
        db.query(Assignment)
        .join(Shift)
        .filter(
            and_(
                Assignment.operator_id == operator_id,
                Shift.date == shift_date,
                Assignment.start_time.isnot(None),
                Assignment.end_time.isnot(None),
            )
        )
        .all()
    )

    for a in existing:
        if exclude_assignment_id and a.id == exclude_assignment_id:
            continue
        if times_overlap(start, end, a.start_time, a.end_time):
            return True
    return False


def shift_window_minutes(start: time, end: time) -> tuple[int, int]:
    """Shift bounds in minutes from midnight, with the end pushed past 1440 when
    the shift crosses midnight (Overnight 2300-0700 -> (1380, 1860))."""
    s, e = _to_minutes(start), _to_minutes(end)
    if e <= s:
        e += 1440
    return s, e


def _bound_within(shift_start_min: int, shift_end_min: int, t: time) -> int:
    """Place a wall-clock time onto the shift's timeline, accounting for a
    shift that runs past midnight."""
    m = _to_minutes(t)
    if m < shift_start_min:
        m += 1440
    return m


def narrows_shift(
    shift_start: time,
    shift_end: time,
    earliest_start: Optional[time],
    latest_end: Optional[time],
) -> bool:
    """True if the operator's limits cover less than the whole shift."""
    s, e = shift_window_minutes(shift_start, shift_end)
    if latest_end is not None and _bound_within(s, e, latest_end) < e:
        return True
    if earliest_start is not None and _bound_within(s, e, earliest_start) > s:
        return True
    return False


def shifts_are_consecutive(first_end: time, second_start: time) -> bool:
    """Back-to-back shifts: the second starts exactly when the first ends."""
    return _to_minutes(first_end) == _to_minutes(second_start)


def covered_window(
    shift_start: time,
    shift_end: time,
    earliest_start: Optional[time],
    latest_end: Optional[time],
) -> tuple[int, int]:
    """The minutes range an operator actually covers within a shift."""
    s, e = shift_window_minutes(shift_start, shift_end)
    lo = _bound_within(s, e, earliest_start) if earliest_start else s
    hi = _bound_within(s, e, latest_end) if latest_end else e
    return max(lo, s), min(hi, e)


def minutes_to_time(m: int) -> time:
    """Wrap minutes-from-midnight (possibly >1440) back to a clock time."""
    m %= 1440
    return time(m // 60, m % 60)


def select_candidates(candidates: list["Candidate"]) -> list["Candidate"]:
    """Order a slot's candidate pool.

    The rule is absolute: a partial_fallback offer is never returned while any
    full candidate exists, regardless of hours, seniority or any other ranking.
    Partial offers are only surfaced once the full pool is empty.
    """
    full = [c for c in candidates if c.coverage_type == "full"]
    if full:
        return full
    return [c for c in candidates if c.coverage_type == "partial_fallback"]


def remainder_after_partial(
    shift_start: time,
    shift_end: time,
    covered_start: time,
    covered_end: time,
) -> Optional[tuple[time, time]]:
    """The uncovered tail of a shift left by a partial assignment, or None if
    the shift is fully covered. This is what must become its own open slot —
    without it a director sees a name on the slot and assumes it is filled.
    """
    s, e = shift_window_minutes(shift_start, shift_end)
    cov_lo = _bound_within(s, e, covered_start)
    cov_hi = _bound_within(s, e, covered_end)
    if cov_hi >= e:
        return None
    return minutes_to_time(cov_hi), minutes_to_time(e)


def is_missed_check_in(check_in: CheckIn) -> bool:
    """Shift started 30+ minutes ago with no actual check-in."""
    if check_in.actual_check_in or check_in.status != CheckInStatus.pending:
        return False
    from .clock import local_now_naive
    now = local_now_naive()
    today = now.date()
    scheduled = datetime.combine(today, check_in.scheduled_start)
    return (now - scheduled).total_seconds() >= 30 * 60


def is_missed_check_out(check_in: CheckIn) -> bool:
    """Shift ended 60+ minutes ago with no check-out."""
    if check_in.actual_check_out or check_in.status == CheckInStatus.checked_out:
        return False
    from .clock import local_now_naive
    now = local_now_naive()
    today = now.date()
    scheduled_end = datetime.combine(today, check_in.scheduled_end)
    # Overnight: if end < start, end is on the next day
    if check_in.scheduled_end <= check_in.scheduled_start:
        from datetime import timedelta
        scheduled_end += timedelta(days=1)
    return (now - scheduled_end).total_seconds() >= 60 * 60


def hours_for_operator_month(db, operator_id, month: int, year: int) -> float:
    """Sum decimal hours for all checked-out shifts in the given month/year."""
    from .models import CheckIn, CheckInStatus, Assignment, Shift
    from sqlalchemy import and_, extract

    rows: list[CheckIn] = (
        db.query(CheckIn)
        .join(Assignment)
        .join(Shift)
        .filter(
            and_(
                CheckIn.operator_id == operator_id,
                CheckIn.status == CheckInStatus.checked_out,
                extract("month", Shift.date) == month,
                extract("year", Shift.date) == year,
            )
        )
        .all()
    )

    total = 0.0
    for ci in rows:
        if ci.actual_check_in and ci.actual_check_out:
            delta = ci.actual_check_out - ci.actual_check_in
            total += delta.total_seconds() / 3600
        else:
            total += shift_duration_hours(ci.scheduled_start, ci.scheduled_end)
    return round(total, 2)
