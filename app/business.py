"""
Business logic ported from the Discord bot.
All overnight-aware — a shift whose end_time <= start_time crosses midnight.
"""
from datetime import date, datetime, time, timezone
from typing import Optional
from .models import Assignment, CheckIn, CheckInStatus


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


def is_missed_check_in(check_in: CheckIn) -> bool:
    """Shift started 30+ minutes ago with no actual check-in."""
    if check_in.actual_check_in or check_in.status != CheckInStatus.pending:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.date()
    scheduled = datetime.combine(today, check_in.scheduled_start)
    return (now - scheduled).total_seconds() >= 30 * 60


def is_missed_check_out(check_in: CheckIn) -> bool:
    """Shift ended 60+ minutes ago with no check-out."""
    if check_in.actual_check_out or check_in.status == CheckInStatus.checked_out:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
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
