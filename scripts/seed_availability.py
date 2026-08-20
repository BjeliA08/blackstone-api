"""
Dev/test seed for the availability feature. Site-aware: it reads each site's
configured shifts from site_shifts rather than assuming a fixed set.

Creates three periods so every UI state can be exercised:
  * an OPEN period with submissions from many operators (coverage grid)
  * a CLOSED period with one submission (operator read-only view)
  * a CLOSED period with none (missing-submissions edge case)

Safe to re-run — submissions are replaced, not duplicated.
"""
import random
import sys
import uuid
from datetime import datetime, timedelta, time, timezone

sys.path.insert(0, ".")

from app.database import SessionLocal
from app.models import (AvailabilityEntry, AvailabilityPeriod,
                        AvailabilityStatus, AvailabilitySubmission, Operator,
                        Site, SiteShift)

OPEN_PERIOD = (9, 2026)
CLOSED_WITH_SUB = (8, 2026)
CLOSED_EMPTY = (7, 2026)

OPERATOR_LIMIT = 20
READONLY_TEST_OPERATOR = "Andriy Bjeli"

NOTES = [
    "Prefer not to work overnight with the Club101 crew",
    "Need a 15 minute buffer before this shift",
    "Short notice only",
    "Away the first week — flagging in case it matters",
    "Happy to cover the parkade if needed",
]


def days_in_month(month: int, year: int) -> int:
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return (nxt - datetime(year, month, 1)).days


def rand_time(base_hour: int, spread: int) -> time:
    h = max(0, min(23, base_hour + random.randint(-spread, spread)))
    return time(h, random.choice([0, 15, 30, 45]))


def upsert_period(db, month, year, status, opens_at, closes_at) -> AvailabilityPeriod:
    p = (db.query(AvailabilityPeriod)
           .filter(AvailabilityPeriod.month == month, AvailabilityPeriod.year == year)
           .first())
    if not p:
        p = AvailabilityPeriod(id=uuid.uuid4(), month=month, year=year,
                               opens_at=opens_at, closes_at=closes_at, status=status)
        db.add(p)
        db.flush()
        print(f"created period {month}/{year} ({status.value})")
    else:
        p.status, p.opens_at, p.closes_at = status, opens_at, closes_at
        print(f"updated period {month}/{year} ({status.value})")
    return p


def seed_submission(db, operator, period, shifts_by_site, now) -> int:
    sub = (db.query(AvailabilitySubmission)
             .filter(AvailabilitySubmission.operator_id == operator.id,
                     AvailabilitySubmission.period_id == period.id)
             .first())
    if sub:
        for e in list(sub.entries):
            db.delete(e)
        db.flush()
        sub.updated_at = now
    else:
        sub = AvailabilitySubmission(id=uuid.uuid4(), operator_id=operator.id,
                                     period_id=period.id, submitted_at=now, updated_at=now)
        db.add(sub)
        db.flush()

    n = 0
    for site_id, shift_names in shifts_by_site.items():
        # Each operator favours some sites over others, so coverage varies.
        site_rate = random.uniform(0.2, 0.75)
        for day in range(1, days_in_month(period.month, period.year) + 1):
            d = datetime(period.year, period.month, day).date()
            for shift in shift_names:
                if random.random() >= site_rate:
                    continue
                db.add(AvailabilityEntry(
                    id=uuid.uuid4(), submission_id=sub.id, site_id=site_id,
                    date=d, shift_name=shift, available=True,
                    earliest_start=rand_time(8, 3) if random.random() < 0.25 else None,
                    latest_end=rand_time(20, 3) if random.random() < 0.25 else None,
                    note=random.choice(NOTES) if random.random() < 0.08 else None,
                ))
                n += 1
    return n


def main():
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    shifts_by_site: dict[uuid.UUID, list[str]] = {}
    rows = (db.query(SiteShift).join(Site, SiteShift.site_id == Site.id)
              .filter(SiteShift.active.is_(True), Site.active.is_(True))
              .order_by(SiteShift.sort_order).all())
    for ss in rows:
        shifts_by_site.setdefault(ss.site_id, []).append(ss.shift_name)

    if not shifts_by_site:
        print("No site shifts configured — run `alembic upgrade head` first. Aborting.")
        return

    for sid, names in shifts_by_site.items():
        site = db.get(Site, sid)
        print(f"  {site.slug}: {names}")

    # ── Open period, many operators ──────────────────────────────────────────
    open_p = upsert_period(db, *OPEN_PERIOD, AvailabilityStatus.open,
                           now - timedelta(days=2), now + timedelta(days=5))
    operators = (db.query(Operator).filter(Operator.active.is_(True))
                   .order_by(Operator.full_name).limit(OPERATOR_LIMIT).all())
    for op in operators:
        print(f"  open  {op.full_name}: {seed_submission(db, op, open_p, shifts_by_site, now)} entries")

    # ── Closed period with one submission (read-only view) ───────────────────
    closed_p = upsert_period(db, *CLOSED_WITH_SUB, AvailabilityStatus.closed,
                             now - timedelta(days=40), now - timedelta(days=20))
    solo = db.query(Operator).filter(Operator.full_name == READONLY_TEST_OPERATOR).first()
    if solo:
        print(f"  closed {solo.full_name}: {seed_submission(db, solo, closed_p, shifts_by_site, now)} entries")

    # ── Closed period with no submissions at all ─────────────────────────────
    upsert_period(db, *CLOSED_EMPTY, AvailabilityStatus.closed,
                  now - timedelta(days=70), now - timedelta(days=50))

    db.commit()
    db.close()
    print("Done.")


if __name__ == "__main__":
    main()
