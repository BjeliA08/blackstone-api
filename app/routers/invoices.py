"""Director/Admin side of contractor invoicing: review, approve, pay, and the
two numbers a director actually needs — site margin and coverage variance.

Invoicing and margin visibility only. No tax remittance, no payroll, no
receivables tracking beyond invoice status.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from ..business import shift_duration_hours
from ..clock import utc_now_naive
from ..database import get_db
from ..deps import require_director
from ..models import (Assignment, CheckIn, CheckInStatus, Invoice,
                      InvoiceLineItem, InvoiceStatus, Operator, Shift,
                      ShiftStatus, Site)
from ..schemas import (CoverageVarianceOut, InvoiceDetailOut, InvoiceOut,
                       SiteFinancialOut)
from .me import build_invoice_detail

router = APIRouter(prefix="/invoices", tags=["invoices"])


def _parse_period(period: str) -> tuple[int, int]:
    try:
        year_s, month_s = period.split("-")
        year, month = int(year_s), int(month_s)
        if not (1 <= month <= 12):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="Period must be formatted YYYY-MM")
    return month, year


def _build_invoice_out(inv: Invoice) -> InvoiceOut:
    return InvoiceOut(
        id=inv.id, operator_id=inv.operator_id,
        operator_name=inv.operator.full_name if inv.operator else None,
        period_month=inv.period_month, period_year=inv.period_year,
        status=inv.status, submitted_at=inv.submitted_at,
        approved_at=inv.approved_at, approved_by=inv.approved_by,
        paid_at=inv.paid_at, marked_paid_by=inv.marked_paid_by,
        total_hours=inv.total_hours, total_amount=inv.total_amount,
        gst_amount=inv.gst_amount, created_at=inv.created_at,
    )


@router.get("", response_model=list[InvoiceOut])
def list_invoices(
    status: Optional[InvoiceStatus] = None,
    period: Optional[str] = None,
    site: Optional[str] = None,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    q = db.query(Invoice).options(joinedload(Invoice.operator))
    if status:
        q = q.filter(Invoice.status == status)
    if period:
        month, year = _parse_period(period)
        q = q.filter(Invoice.period_month == month, Invoice.period_year == year)
    if site:
        q = (
            q.join(InvoiceLineItem, InvoiceLineItem.invoice_id == Invoice.id)
            .join(Site, Site.id == InvoiceLineItem.site_id)
            .filter(Site.slug == site)
            .distinct()
        )
    rows = q.order_by(Invoice.period_year.desc(), Invoice.period_month.desc()).all()
    return [_build_invoice_out(i) for i in rows]


@router.get("/site-summary", response_model=list[SiteFinancialOut])
def site_summary(
    period: str,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    """Per site: billable hours, what the client is billed, what operators
    are paid, and the margin between them — generated from whatever invoices
    exist for the period, regardless of their approval stage."""
    month, year = _parse_period(period)
    sites = db.query(Site).order_by(Site.name).all()

    out: list[SiteFinancialOut] = []
    for s in sites:
        items = (
            db.query(InvoiceLineItem)
            .join(Invoice, Invoice.id == InvoiceLineItem.invoice_id)
            .filter(InvoiceLineItem.site_id == s.id,
                    Invoice.period_month == month, Invoice.period_year == year)
            .all()
        )
        if not items:
            continue
        hours = sum((i.hours for i in items), Decimal("0"))
        pay_amount = sum((i.amount for i in items), Decimal("0"))
        bill_amount = (hours * s.bill_rate).quantize(Decimal("0.01")) if s.bill_rate else Decimal("0")
        out.append(SiteFinancialOut(
            site_id=s.id, site_name=s.name, site_slug=s.slug,
            period_month=month, period_year=year,
            billable_hours=hours, bill_amount=bill_amount,
            pay_amount=pay_amount, margin=bill_amount - pay_amount,
        ))
    return out


@router.get("/coverage-variance", response_model=list[CoverageVarianceOut])
def coverage_variance(
    period: str,
    site: Optional[str] = None,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    """Scheduled hours (what was staffed) versus actual hours (real
    check-in/out data only — a missed shift counts as zero actual, not the
    scheduled fallback used elsewhere) so a director can see billed-vs-worked."""
    month, year = _parse_period(period)
    sites_q = db.query(Site).order_by(Site.name)
    if site:
        sites_q = sites_q.filter(Site.slug == site)
    sites = sites_q.all()

    out: list[CoverageVarianceOut] = []
    for s in sites:
        assignments = (
            db.query(Assignment)
            .join(Shift, Assignment.shift_id == Shift.id)
            .options(joinedload(Assignment.check_in))
            .filter(
                Shift.site_id == s.id, Shift.status == ShiftStatus.approved,
                Assignment.operator_id.isnot(None),
            )
            .filter(Shift.date >= date(year, month, 1))
            .filter(Shift.date < date(year + (1 if month == 12 else 0), (month % 12) + 1, 1))
            .all()
        )
        if not assignments:
            continue

        scheduled = Decimal("0")
        actual = Decimal("0")
        for a in assignments:
            if a.start_time and a.end_time:
                scheduled += Decimal(str(round(shift_duration_hours(a.start_time, a.end_time), 2)))
            ci = a.check_in
            if ci and ci.status == CheckInStatus.checked_out and ci.actual_check_in and ci.actual_check_out:
                actual += Decimal(str(round(
                    (ci.actual_check_out - ci.actual_check_in).total_seconds() / 3600, 2)))

        variance = actual - scheduled
        pct = (variance / scheduled * 100).quantize(Decimal("0.1")) if scheduled > 0 else None
        out.append(CoverageVarianceOut(
            site_id=s.id, site_name=s.name, site_slug=s.slug,
            period_month=month, period_year=year,
            scheduled_hours=scheduled, actual_hours=actual,
            variance_hours=variance, variance_pct=pct,
        ))
    return out


@router.get("/{invoice_id}", response_model=InvoiceDetailOut)
def get_invoice(
    invoice_id: uuid.UUID,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return build_invoice_detail(inv)


@router.post("/{invoice_id}/approve", response_model=InvoiceDetailOut)
def approve_invoice(
    invoice_id: uuid.UUID,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status != InvoiceStatus.submitted:
        raise HTTPException(status_code=409,
                           detail=f"Cannot approve an invoice that is {inv.status.value} — it must be submitted first")
    inv.status = InvoiceStatus.approved
    inv.approved_at = utc_now_naive()
    inv.approved_by = current.id
    db.commit()
    db.refresh(inv)
    return build_invoice_detail(inv)


@router.post("/{invoice_id}/paid", response_model=InvoiceDetailOut)
def mark_invoice_paid(
    invoice_id: uuid.UUID,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.status != InvoiceStatus.approved:
        raise HTTPException(status_code=409,
                           detail=f"Cannot mark paid an invoice that is {inv.status.value} — it must be approved first")
    inv.status = InvoiceStatus.paid
    inv.paid_at = utc_now_naive()
    inv.marked_paid_by = current.id
    db.commit()
    db.refresh(inv)
    return build_invoice_detail(inv)
