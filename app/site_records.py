"""Site Records aggregation — Director/Admin only.

Not a new source of truth: this reads shifts, assignments, check-ins,
invoices and operator compliance fields that already exist, and presents
them as one unified record set per site. The only thing this module adds
that didn't exist before is the export log itself (see models.RecordExport).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from .identity import licence_status
from .models import (Assignment, CheckIn, CheckInStatus, Invoice,
                     InvoiceLineItem, Shift, ShiftStatus, Site)
from .schemas import (RecordAssignmentOut, RecordCheckInOut,
                      RecordComplianceOut, RecordIncidentOut, RecordInvoiceOut,
                      RecordShiftOut, SiteRecordsOut)
from .sos_sheet import fetch_sos_entries


def coverage_shifts(db: Session, site: Site, start: date, end: date) -> list[RecordShiftOut]:
    rows = (
        db.query(Shift)
        .options(joinedload(Shift.assignments).joinedload(Assignment.operator))
        .filter(Shift.site_id == site.id, Shift.status == ShiftStatus.approved,
                Shift.date >= start, Shift.date <= end)
        .order_by(Shift.date, Shift.shift_name)
        .all()
    )
    out = []
    for shift in rows:
        assignments = [RecordAssignmentOut(
            operator_id=a.operator_id,
            operator_name=a.operator.full_name if a.operator else None,
            position=a.position, start_time=a.start_time, end_time=a.end_time,
            accepted=a.accepted,
        ) for a in shift.assignments]
        out.append(RecordShiftOut(
            date=shift.date, shift_name=shift.shift_name,
            slots_total=len(shift.assignments),
            slots_filled=sum(1 for a in shift.assignments if a.operator_id is not None),
            assignments=assignments,
        ))
    return out


def checkin_records(db: Session, site: Site, start: date, end: date) -> list[RecordCheckInOut]:
    rows = (
        db.query(CheckIn)
        .join(Assignment, CheckIn.assignment_id == Assignment.id)
        .join(Shift, Assignment.shift_id == Shift.id)
        .filter(Shift.site_id == site.id, Shift.date >= start, Shift.date <= end)
        .options(joinedload(CheckIn.operator), joinedload(CheckIn.assignment).joinedload(Assignment.shift))
        .order_by(Shift.date)
        .all()
    )
    out = []
    for ci in rows:
        shift = ci.assignment.shift if ci.assignment else None
        if not shift:
            continue
        late_in = None
        if ci.actual_check_in:
            scheduled_in = datetime.combine(shift.date, ci.scheduled_start)
            late_in = int(round((ci.actual_check_in - scheduled_in).total_seconds() / 60))
        late_out = None
        if ci.actual_check_out:
            scheduled_out = datetime.combine(shift.date, ci.scheduled_end)
            late_out = int(round((ci.actual_check_out - scheduled_out).total_seconds() / 60))
        out.append(RecordCheckInOut(
            date=shift.date, shift_name=shift.shift_name,
            operator_name=ci.operator.full_name if ci.operator else "Unknown",
            scheduled_start=ci.scheduled_start, scheduled_end=ci.scheduled_end,
            actual_check_in=ci.actual_check_in, actual_check_out=ci.actual_check_out,
            status=ci.status,
            late_in_minutes=late_in if late_in and late_in > 0 else None,
            late_out_minutes=late_out if late_out and late_out > 0 else None,
            notes=ci.notes, gps_captured=False,
        ))
    return out


def invoice_records(db: Session, site: Site, start: date, end: date,
                    include_rates: bool) -> list[RecordInvoiceOut]:
    """Grouped by (operator, invoice) — the hours and, if included, the
    amount this specific site contributed to that invoice."""
    rows = (
        db.query(InvoiceLineItem)
        .join(Invoice, Invoice.id == InvoiceLineItem.invoice_id)
        .filter(InvoiceLineItem.site_id == site.id,
                InvoiceLineItem.date >= start, InvoiceLineItem.date <= end)
        .options(joinedload(InvoiceLineItem.invoice).joinedload(Invoice.operator))
        .all()
    )
    grouped: dict = {}
    for li in rows:
        inv = li.invoice
        key = inv.id
        if key not in grouped:
            grouped[key] = {
                "operator_name": inv.operator.full_name if inv.operator else "Unknown",
                "period_month": inv.period_month, "period_year": inv.period_year,
                "status": inv.status, "hours": Decimal("0"), "amount": Decimal("0"),
            }
        grouped[key]["hours"] += li.hours
        grouped[key]["amount"] += li.amount

    return [RecordInvoiceOut(
        operator_name=g["operator_name"], period_month=g["period_month"],
        period_year=g["period_year"], status=g["status"],
        site_hours=float(g["hours"]),
        site_amount=float(g["amount"]) if include_rates else None,
    ) for g in grouped.values()]


def compliance_records(db: Session, site: Site, start: date, end: date) -> list[RecordComplianceOut]:
    """Operators who actually worked the site in the period, and their
    licence status as of now — a compliance section is read at the moment
    it's pulled, not frozen to how things stood during the period."""
    rows = (
        db.query(Assignment)
        .join(Shift, Assignment.shift_id == Shift.id)
        .filter(Shift.site_id == site.id, Shift.date >= start, Shift.date <= end,
                Assignment.operator_id.isnot(None))
        .options(joinedload(Assignment.operator))
        .all()
    )
    seen = {}
    for a in rows:
        op = a.operator
        if not op or op.id in seen:
            continue
        seen[op.id] = RecordComplianceOut(
            operator_name=op.full_name,
            licence_status=licence_status(op),
            licence_expiry=op.security_licence_expiry,
        )
    return sorted(seen.values(), key=lambda c: c.operator_name)


def incident_records(db: Session, site: Site, start: date, end: date) -> list[RecordIncidentOut]:
    from .deps import site_feature_enabled
    from .models import SiteFeatureKey
    if not site_feature_enabled(db, site.id, SiteFeatureKey.sos):
        return []
    entries = fetch_sos_entries(site.name, start, end)
    return [RecordIncidentOut(
        date=date.fromisoformat(e["date"]), incident_type=e["incident_type"],
        operator_name=e.get("operator_name"), summary=e.get("summary", ""),
    ) for e in entries]


def build_site_records(db: Session, site: Site, start: date, end: date,
                       include_rates: bool = False) -> SiteRecordsOut:
    return SiteRecordsOut(
        site_id=site.id, site_name=site.name, site_slug=site.slug,
        period_start=start, period_end=end, include_rates=include_rates,
        shifts=coverage_shifts(db, site, start, end),
        check_ins=checkin_records(db, site, start, end),
        invoices=invoice_records(db, site, start, end, include_rates),
        incidents=incident_records(db, site, start, end),
        compliance=compliance_records(db, site, start, end),
    )
