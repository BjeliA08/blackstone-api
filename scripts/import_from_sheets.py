"""
One-time import script: reads Employees, site schedule tabs, and Check In Log
from Google Sheets and populates the Postgres database.

Usage:
    python -m scripts.import_from_sheets \
        --credentials path/to/credentials.json \
        --scheduling-sheet-id <id> \
        --operations-sheet-id <id>

Run from the blackstone-api directory with the .env present.
"""
import argparse
import re
import sys
import os
from datetime import date, datetime, time
from pathlib import Path

# Allow running as a module from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import gspread
from google.oauth2.service_account import Credentials
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import (Assignment, Base, CheckIn, CheckInStatus, Operator,
                         OperatorRole, Shift, ShiftStatus, Site)

# ── Name normalization ────────────────────────────────────────────────────────
# Mirrors the normalizeOperatorName map from the desktop app.
# Add any additional aliases here before running.
NAME_ALIASES: dict[str, str] = {
    "nimrat": "Nimratpal Singh",
    "nimratpal": "Nimratpal Singh",
    "nimratpal singh": "Nimratpal Singh",
    "tyler": "Tyler Whalen",
    "tyler whalen": "Tyler Whalen",
    # Add more as needed — key is lowercased raw name, value is canonical
}

SITE_CONFIG = [
    {"name": "Shelter",  "slug": "shelter",  "slot_count": 3,  "color": "#3E7CB1",
     "schedule_tab": "Shelter Schedule",  "hours_tab": "Shelter Hours Tracker"},
    {"name": "Club101",  "slug": "club101",  "slot_count": 6,  "color": "#7D5BA6",
     "schedule_tab": "Club101 Schedule",  "hours_tab": "Club101 Hours Tracker"},
    {"name": "Starhall", "slug": "starhall", "slot_count": 5,  "color": "#C4923E",
     "schedule_tab": "Starhall Schedule", "hours_tab": "Starhall Hours Tracker"},
]


def normalize_name(raw: str) -> str:
    stripped = raw.strip()
    lower = stripped.lower()
    if lower in NAME_ALIASES:
        return NAME_ALIASES[lower]
    # Title-case as fallback
    return " ".join(w.capitalize() for w in stripped.split())


def parse_time(raw: str) -> time | None:
    raw = str(raw).strip()
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%H%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            pass
    return None


def parse_date(raw) -> date | None:
    raw = str(raw).strip()
    # Handle Excel serial numbers
    if re.match(r"^\d{5}$", raw):
        from datetime import timedelta
        return (date(1899, 12, 30) + timedelta(days=int(raw)))
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def get_or_create_operator(db: Session, name_cache: dict, full_name: str) -> Operator | None:
    if not full_name or full_name.upper() in ("OPEN", ""):
        return None
    normalized = normalize_name(full_name)
    if normalized in name_cache:
        return name_cache[normalized]
    op = db.query(Operator).filter(Operator.full_name == normalized).first()
    if not op:
        _fn, _, _ln = normalized.partition(' ')
        op = Operator(first_name=_fn, last_name=_ln, phone_number=f"IMPORT_{normalized.replace(' ', '_')}",
                      role=OperatorRole.operator, active=True)
        db.add(op)
        db.flush()
        print(f"  Created operator: {normalized}")
    name_cache[normalized] = op
    return op


def import_employees(db: Session, ws, name_cache: dict):
    print("\n=== Importing Employees ===")
    rows = ws.get_all_records()
    for row in rows:
        full_name = normalize_name(str(row.get("Full Name", "")).strip())
        if not full_name:
            continue
        discord_id = str(row.get("Discord ID", "")).strip() or None
        active = str(row.get("Active", "")).strip().lower() == "yes"
        role_raw = str(row.get("Role", "")).strip().lower()
        role = OperatorRole.director if "director" in role_raw else OperatorRole.operator

        existing = db.query(Operator).filter(Operator.full_name == full_name).first()
        if existing:
            existing.discord_id = discord_id or existing.discord_id
            existing.active = active
            existing.role = role
            name_cache[full_name] = existing
        else:
            phone = f"IMPORT_{full_name.replace(' ', '_')}"
            _fn, _, _ln = full_name.partition(' ')
            op = Operator(first_name=_fn, last_name=_ln, phone_number=phone,
                          discord_id=discord_id, role=role, active=active)
            db.add(op)
            db.flush()
            name_cache[full_name] = op
            print(f"  + {full_name}")

    db.commit()
    print(f"  Done. {len(name_cache)} operators in cache.")


def import_schedule_tab(db: Session, ws, site: Site, name_cache: dict):
    print(f"\n=== Importing {site.name} Schedule ===")
    rows = ws.get_all_records()
    slot_count = site.slot_count
    created = updated = skipped = 0

    for row in rows:
        status_raw = str(row.get("Status", "")).strip().lower()
        if status_raw != "approved":
            skipped += 1
            continue

        shift_date = parse_date(row.get("Date", ""))
        if not shift_date:
            skipped += 1
            continue

        shift_name = str(row.get("Shift", "")).strip()
        if not shift_name:
            skipped += 1
            continue

        # Find or create the shift
        shift = db.query(Shift).filter(
            Shift.site_id == site.id,
            Shift.date == shift_date,
            Shift.shift_name == shift_name,
        ).first()

        if not shift:
            shift = Shift(site_id=site.id, date=shift_date,
                          shift_name=shift_name, status=ShiftStatus.approved)
            db.add(shift)
            db.flush()
            created += 1
        else:
            updated += 1

        # Upsert assignments
        existing_slots = {a.slot_index: a for a in shift.assignments}

        for s in range(1, slot_count + 1):
            op_raw = str(row.get(f"Operator {s}", "")).strip()
            op = get_or_create_operator(db, name_cache, op_raw) if op_raw else None
            position = str(row.get(f"Position {s}", "")).strip() or None
            start = parse_time(str(row.get(f"Start {s}", "")))
            end = parse_time(str(row.get(f"End {s}", "")))
            accepted = str(row.get(f"Accepted {s}", "")).strip().lower() == "yes"

            if s in existing_slots:
                a = existing_slots[s]
            else:
                a = Assignment(shift_id=shift.id, slot_index=s)
                db.add(a)

            a.operator_id = op.id if op else None
            a.position = position
            a.start_time = start
            a.end_time = end
            a.accepted = accepted

        db.flush()

    db.commit()
    print(f"  Created: {created}  Updated: {updated}  Skipped: {skipped}")


def import_check_ins(db: Session, ws, name_cache: dict):
    print("\n=== Importing Check In Log ===")
    rows = ws.get_all_records()
    created = skipped = 0

    for row in rows:
        op_name = normalize_name(str(row.get("Operator", "")).strip())
        if not op_name:
            skipped += 1
            continue

        op = name_cache.get(op_name) or db.query(Operator).filter(
            Operator.full_name == op_name).first()
        if not op:
            skipped += 1
            continue

        shift_date = parse_date(row.get("Date", ""))
        shift_name = str(row.get("Shift", "")).strip()
        if not shift_date or not shift_name:
            skipped += 1
            continue

        # Find the matching assignment
        assignment = (
            db.query(Assignment)
            .join(Shift)
            .filter(
                Assignment.operator_id == op.id,
                Shift.date == shift_date,
                Shift.shift_name == shift_name,
            )
            .first()
        )

        if not assignment:
            skipped += 1
            continue

        if db.query(CheckIn).filter(CheckIn.assignment_id == assignment.id).first():
            skipped += 1
            continue

        sched_start = parse_time(str(row.get("Scheduled Start", "") or row.get("Start", "")))
        sched_end = parse_time(str(row.get("Scheduled End", "") or row.get("End", "")))
        if not sched_start or not sched_end:
            skipped += 1
            continue

        check_in_raw = str(row.get("Check In", "")).strip()
        check_out_raw = str(row.get("Check Out", "")).strip()
        actual_in = datetime.strptime(check_in_raw, "%Y-%m-%d %H:%M:%S") if check_in_raw else None
        actual_out = datetime.strptime(check_out_raw, "%Y-%m-%d %H:%M:%S") if check_out_raw else None

        if actual_out:
            ci_status = CheckInStatus.checked_out
        elif actual_in:
            ci_status = CheckInStatus.checked_in
        else:
            ci_status = CheckInStatus.pending

        ci = CheckIn(
            assignment_id=assignment.id,
            operator_id=op.id,
            scheduled_start=sched_start,
            scheduled_end=sched_end,
            actual_check_in=actual_in,
            actual_check_out=actual_out,
            status=ci_status,
            notes=str(row.get("Notes", "")).strip() or None,
        )
        db.add(ci)
        created += 1

    db.commit()
    print(f"  Created: {created}  Skipped: {skipped}")


def main():
    parser = argparse.ArgumentParser(description="Import Blackstone data from Google Sheets to Postgres")
    parser.add_argument("--credentials", required=True, help="Path to service account credentials.json")
    parser.add_argument("--scheduling-sheet-id", required=True)
    parser.add_argument("--operations-sheet-id", required=True)
    args = parser.parse_args()

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(args.credentials, scopes=scopes)
    gc = gspread.authorize(creds)

    scheduling_wb = gc.open_by_key(args.scheduling_sheet_id)
    operations_wb = gc.open_by_key(args.operations_sheet_id)

    db: Session = SessionLocal()
    name_cache: dict[str, Operator] = {}

    try:
        # Seed sites
        print("\n=== Seeding Sites ===")
        for cfg in SITE_CONFIG:
            site = db.query(Site).filter(Site.slug == cfg["slug"]).first()
            if not site:
                site = Site(name=cfg["name"], slug=cfg["slug"],
                            slot_count=cfg["slot_count"], color=cfg["color"])
                db.add(site)
                print(f"  + {cfg['name']}")
        db.commit()

        # Employees
        employees_ws = operations_wb.worksheet("Employees")
        import_employees(db, employees_ws, name_cache)

        # Site schedules
        for cfg in SITE_CONFIG:
            site = db.query(Site).filter(Site.slug == cfg["slug"]).first()
            ws = scheduling_wb.worksheet(cfg["schedule_tab"])
            import_schedule_tab(db, ws, site, name_cache)

        # Check-in log
        checkin_ws = operations_wb.worksheet("Check In Log")
        import_check_ins(db, checkin_ws, name_cache)

        print("\n✅ Import complete.")

    except Exception as e:
        db.rollback()
        print(f"\n❌ Import failed: {e!r}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
