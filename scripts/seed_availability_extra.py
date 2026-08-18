"""
Extra dev/test seed on top of seed_availability.py:
  1. A CLOSED period (August 2026, already past) with a real submission from
     Andriy Bjeli — to click through the operator's read-only view.
  2. A CLOSED period (July 2026) with NO submissions at all — an edge case
     for the missing-submissions list on an already-closed period.
  3. Bulks up the existing OPEN Sept 2026 period to 20 operators so the
     coverage summary grid has real variance to look at.
"""
import random
import sys
import uuid
from datetime import datetime, timedelta, time, timezone

sys.path.insert(0, ".")

from app.database import SessionLocal
from app.models import (AvailabilityEntry, AvailabilityPeriod,
                        AvailabilityStatus, AvailabilitySubmission, Operator)
from app.constants import SHIFT_NAMES

OPEN_PERIOD = (9, 2026)
CLOSED_WITH_SUB_PERIOD = (8, 2026)
CLOSED_EMPTY_PERIOD = (7, 2026)

STRESS_TEST_OPERATOR_COUNT = 20

NOTES = [
    "Prefer not to work overnight with Club101 crew",
    "Available but need 15 min buffer before shift",
    "Can cover if short-notice only",
    "Out of town first week — flagging in case it matters",
    "Can only do Starhall this month",
]


def days_in_month(month: int, year: int) -> int:
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return (nxt - datetime(year, month, 1)).days


def random_time(base_hour: int, spread: int) -> time:
    h = max(0, min(23, base_hour + random.randint(-spread, spread)))
    m = random.choice([0, 15, 30, 45])
    return time(h, m)


def get_or_create_period(db, month, year, status, opens_at, closes_at):
    period = (
        db.query(AvailabilityPeriod)
        .filter(AvailabilityPeriod.month == month, AvailabilityPeriod.year == year)
        .first()
    )
    if not period:
        period = AvailabilityPeriod(
            id=uuid.uuid4(), month=month, year=year,
            opens_at=opens_at, closes_at=closes_at, status=status,
        )
        db.add(period)
        db.flush()
        print(f"Created period {month}/{year} ({status.value}) — id {period.id}")
    else:
        period.status = status
        period.opens_at = opens_at
        period.closes_at = closes_at
        print(f"Updated existing period {month}/{year} ({status.value}) — id {period.id}")
    return period


def seed_submission(db, operator, period, now, availability_rate=0.55):
    existing = (
        db.query(AvailabilitySubmission)
        .filter(AvailabilitySubmission.operator_id == operator.id, AvailabilitySubmission.period_id == period.id)
        .first()
    )
    if existing:
        for e in list(existing.entries):
            db.delete(e)
        db.flush()
        sub = existing
        sub.updated_at = now
    else:
        sub = AvailabilitySubmission(
            id=uuid.uuid4(), operator_id=operator.id, period_id=period.id,
            submitted_at=now, updated_at=now,
        )
        db.add(sub)
        db.flush()

    day_count = days_in_month(period.month, period.year)
    entry_count = 0
    for day in range(1, day_count + 1):
        d = datetime(period.year, period.month, day).date()
        for shift in SHIFT_NAMES:
            if random.random() >= availability_rate:
                continue
            earliest_start = random_time(8, 3) if random.random() < 0.3 else None
            latest_end = random_time(20, 3) if random.random() < 0.3 else None
            note = random.choice(NOTES) if random.random() < 0.1 else None
            db.add(AvailabilityEntry(
                id=uuid.uuid4(), submission_id=sub.id, date=d, shift_name=shift,
                available=True, earliest_start=earliest_start, latest_end=latest_end, note=note,
            ))
            entry_count += 1
    return entry_count


def main():
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # ── 1. Closed period WITH a submission (Andriy Bjeli), for the read-only view ──
    closed_month, closed_year = CLOSED_WITH_SUB_PERIOD
    closed_period = get_or_create_period(
        db, closed_month, closed_year, AvailabilityStatus.closed,
        opens_at=now - timedelta(days=40), closes_at=now - timedelta(days=20),
    )
    andriy = db.query(Operator).filter(Operator.full_name == "Andriy Bjeli").first()
    if andriy:
        count = seed_submission(db, andriy, closed_period, now, availability_rate=0.5)
        print(f"  Andriy Bjeli (closed, read-only test): {count} entries")

    # ── 2. Closed period with NO submissions (edge case for missing-list) ──
    empty_month, empty_year = CLOSED_EMPTY_PERIOD
    get_or_create_period(
        db, empty_month, empty_year, AvailabilityStatus.closed,
        opens_at=now - timedelta(days=70), closes_at=now - timedelta(days=50),
    )

    # ── 3. Bulk up the open period for a real coverage grid ──
    open_month, open_year = OPEN_PERIOD
    open_period = (
        db.query(AvailabilityPeriod)
        .filter(AvailabilityPeriod.month == open_month, AvailabilityPeriod.year == open_year)
        .first()
    )
    if open_period:
        active_ops = (
            db.query(Operator)
            .filter(Operator.active.is_(True))
            .order_by(Operator.full_name)
            .limit(STRESS_TEST_OPERATOR_COUNT)
            .all()
        )
        for op in active_ops:
            rate = random.uniform(0.35, 0.75)  # vary coverage day to day
            count = seed_submission(db, op, open_period, now, availability_rate=rate)
            print(f"  {op.full_name} (open, coverage stress test): {count} entries")

    db.commit()
    db.close()
    print("Done.")


if __name__ == "__main__":
    main()
