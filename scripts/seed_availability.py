"""
Dev/test seed: creates one open availability period and random submissions
for 5 real operators, so the new availability feature can be clicked through
end-to-end before it goes live. Safe to run against the live DB — these
tables are brand new and nothing else reads them yet.
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

TARGET_OPERATOR_NAMES = [
    "Andriy Bjeli",
    "Bhavdeep Gill",
    "Diego Lopez",
    "Josh Handley",
    "Miralem Sadic",
]

PERIOD_MONTH = 9
PERIOD_YEAR = 2026

NOTES = [
    "Prefer not to work overnight with Club101 crew",
    "Available but need 15 min buffer before shift",
    "Can cover if short-notice only",
    "Out of town first week — flagging in case it matters",
]


def days_in_month(month: int, year: int) -> int:
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - datetime(year, month, 1)).days


def random_time(base_hour: int, spread: int) -> time:
    h = max(0, min(23, base_hour + random.randint(-spread, spread)))
    m = random.choice([0, 15, 30, 45])
    return time(h, m)


def main():
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    operators = (
        db.query(Operator)
        .filter(Operator.full_name.in_(TARGET_OPERATOR_NAMES))
        .all()
    )
    found_names = {o.full_name for o in operators}
    missing = set(TARGET_OPERATOR_NAMES) - found_names
    if missing:
        print(f"WARNING: could not find operators: {missing}")
    if not operators:
        print("No matching operators found — aborting.")
        return

    period = (
        db.query(AvailabilityPeriod)
        .filter(AvailabilityPeriod.month == PERIOD_MONTH, AvailabilityPeriod.year == PERIOD_YEAR)
        .first()
    )
    if not period:
        period = AvailabilityPeriod(
            id=uuid.uuid4(),
            month=PERIOD_MONTH,
            year=PERIOD_YEAR,
            opens_at=now - timedelta(days=2),
            closes_at=now + timedelta(days=5),
            status=AvailabilityStatus.open,
        )
        db.add(period)
        db.flush()
        print(f"Created period {PERIOD_MONTH}/{PERIOD_YEAR} — id {period.id}")
    else:
        period.status = AvailabilityStatus.open
        period.closes_at = now + timedelta(days=5)
        print(f"Reusing existing period {PERIOD_MONTH}/{PERIOD_YEAR} — id {period.id}")

    day_count = days_in_month(PERIOD_MONTH, PERIOD_YEAR)

    for op in operators:
        existing = (
            db.query(AvailabilitySubmission)
            .filter(AvailabilitySubmission.operator_id == op.id, AvailabilitySubmission.period_id == period.id)
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
                id=uuid.uuid4(),
                operator_id=op.id,
                period_id=period.id,
                submitted_at=now,
                updated_at=now,
            )
            db.add(sub)
            db.flush()

        entry_count = 0
        for day in range(1, day_count + 1):
            d = datetime(PERIOD_YEAR, PERIOD_MONTH, day).date()
            for shift in SHIFT_NAMES:
                available = random.random() < 0.55
                if not available:
                    continue
                earliest_start = None
                latest_end = None
                if random.random() < 0.3:
                    earliest_start = random_time(8, 3)
                if random.random() < 0.3:
                    latest_end = random_time(20, 3)
                note = random.choice(NOTES) if random.random() < 0.12 else None
                db.add(AvailabilityEntry(
                    id=uuid.uuid4(),
                    submission_id=sub.id,
                    date=d,
                    shift_name=shift,
                    available=True,
                    earliest_start=earliest_start,
                    latest_end=latest_end,
                    note=note,
                ))
                entry_count += 1
        print(f"  {op.full_name}: {entry_count} available entries")

    db.commit()
    db.close()
    print("Done.")


if __name__ == "__main__":
    main()
