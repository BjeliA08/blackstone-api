"""Contractor invoice generation.

Pulls an operator's worked hours for a period the same way the rest of the
app understands "worked" — actual check-in/out where it exists, scheduled
times otherwise — and turns them into line items with the operator's pay
rate snapshotted at generation time.
"""
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Optional

from sqlalchemy import extract
from sqlalchemy.orm import Session, joinedload

from .business import shift_duration_hours
from .models import Assignment, CheckIn, CheckInStatus, Operator, Shift, ShiftStatus, Site


@dataclass
class LineItemData:
    site: Site
    date: date
    shift_name: str
    start_time: Optional[time]
    end_time: Optional[time]
    hours: Decimal
    rate: Decimal
    amount: Decimal


def worked_hours_for_assignment(assignment: Assignment) -> Decimal:
    """Actual check-in/out duration where recorded, scheduled duration otherwise."""
    ci = assignment.check_in
    start, end = assignment.start_time, assignment.end_time
    if ci and ci.status == CheckInStatus.checked_out and ci.actual_check_in and ci.actual_check_out:
        hours = (ci.actual_check_out - ci.actual_check_in).total_seconds() / 3600
    else:
        hours = shift_duration_hours(start, end) if start and end else 0.0
    return Decimal(str(round(hours, 2)))


def build_line_items(db: Session, operator: Operator, month: int, year: int) -> list[LineItemData]:
    """Every accepted, approved shift this operator worked in the period."""
    rows: list[Assignment] = (
        db.query(Assignment)
        .join(Shift, Assignment.shift_id == Shift.id)
        .options(joinedload(Assignment.check_in), joinedload(Assignment.shift).joinedload(Shift.site))
        .filter(
            Assignment.operator_id == operator.id,
            Assignment.accepted.is_(True),
            Shift.status == ShiftStatus.approved,
            extract("month", Shift.date) == month,
            extract("year", Shift.date) == year,
        )
        .all()
    )

    rate = operator.pay_rate or Decimal("0")
    items: list[LineItemData] = []
    for a in rows:
        hours = worked_hours_for_assignment(a)
        items.append(LineItemData(
            site=a.shift.site,
            date=a.shift.date,
            shift_name=a.shift.shift_name,
            start_time=a.start_time,
            end_time=a.end_time,
            hours=hours,
            rate=rate,
            amount=(hours * rate).quantize(Decimal("0.01")),
        ))

    items.sort(key=lambda i: (i.date, i.site.name if i.site else ""))
    return items
