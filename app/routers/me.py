import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Optional
from fastapi import (APIRouter, Depends, File, HTTPException, Query,
                     UploadFile, status)
from sqlalchemy import extract
from sqlalchemy.orm import Session, joinedload
from ..business import (hours_for_operator_month, is_missed_check_in,
                         is_missed_check_out, operator_has_overlap)
from ..database import get_db
from ..deps import get_current_operator, require_site_feature
from ..clock import local_now_naive, local_today, utc_now_naive
from ..config import settings
from ..documents import signed_attachment_url, upload_attachment
from ..identity import can_view_licence, licence_status, role_names
from ..invoicing import build_line_items
from ..photos import CLIENT_PHOTO_FOLDER, purge_photo, signed_url, upload_photo
from ..models import (Assignment, AvailabilityPeriod, AvailabilityStatus,
                      AvailabilitySubmission, ChatChannel, ChatChannelType,
                      ChatMessage, CheckIn, CheckInStatus,
                      ClientProfile, Division, DivisionOperator, Invoice,
                      InvoiceLineItem, InvoiceStatus, LicenceStatus,
                      OnboardingStatus, Operator, Shift, ShiftStatus, Site,
                      SiteAccess, SiteFeatureKey)
from ..schemas import (ActivityRowOut, AssignmentOut, ChatMessageOut, CheckInOut,
                       ClientProfileOut, ClientProfilePatch, DivisionOut,
                       HistoryRowOut, HoursSummary, InvoiceDetailOut,
                       InvoiceOut, InvoicePreviewOut, InvoiceLineItemOut,
                       MeOverviewOut, NextShiftOut, OperatorOut,
                       OutstandingAction, PhotoUrlOut, ProfilePatch, ShiftOut)

router = APIRouter(prefix="/me", tags=["me"])


def _build_shift_out(shift: Shift) -> ShiftOut:
    return ShiftOut(
        id=shift.id,
        site_id=shift.site_id,
        site_name=shift.site.name if shift.site else None,
        date=shift.date,
        shift_name=shift.shift_name,
        status=shift.status,
        assignments=[AssignmentOut.from_orm_with_name(a) for a in shift.assignments],
    )


@router.get("/shifts", response_model=list[ShiftOut])
def my_shifts(
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    q = (
        db.query(Shift)
        .join(Assignment)
        .filter(Assignment.operator_id == current.id, Shift.status == ShiftStatus.approved)
        .options(joinedload(Shift.assignments).joinedload(Assignment.operator),
                 joinedload(Shift.site))
    )
    if from_date:
        q = q.filter(Shift.date >= from_date)
    if to_date:
        q = q.filter(Shift.date <= to_date)

    return [_build_shift_out(s) for s in q.order_by(Shift.date).all()]


@router.get("/shifts/today", response_model=list[ShiftOut])
def my_shifts_today(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    today = local_today()
    shifts = (
        db.query(Shift)
        .join(Assignment)
        .filter(Assignment.operator_id == current.id,
                Shift.status == ShiftStatus.approved,
                Shift.date == today)
        .options(joinedload(Shift.assignments).joinedload(Assignment.operator),
                 joinedload(Shift.site))
        .all()
    )
    return [_build_shift_out(s) for s in shifts]


@router.get("/hours", response_model=HoursSummary)
def my_hours(
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2020),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    total = hours_for_operator_month(db, current.id, month, year)
    shift_count = (
        db.query(CheckIn)
        .join(Assignment).join(Shift)
        .filter(CheckIn.operator_id == current.id,
                CheckIn.status == CheckInStatus.checked_out,
                db.query(Shift).filter(Shift.id == Assignment.shift_id).scalar_subquery())
        .count()
    )
    return HoursSummary(
        operator_id=current.id,
        operator_name=current.full_name,
        month=month,
        year=year,
        total_hours=total,
        shift_count=shift_count,
    )


@router.post("/check-in", response_model=CheckInOut)
def check_in(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    today = local_today()
    now = utc_now_naive()   # recorded instant, not a wall-clock comparison

    # Find the operator's assignment for today that hasn't been checked in yet
    assignment: Assignment | None = (
        db.query(Assignment)
        .join(Shift)
        .filter(
            Assignment.operator_id == current.id,
            Shift.date == today,
            Shift.status == ShiftStatus.approved,
        )
        .options(joinedload(Assignment.shift).joinedload(Shift.site), joinedload(Assignment.check_in))
        .order_by(Assignment.start_time)
        .first()
    )

    if not assignment:
        raise HTTPException(status_code=404, detail="No approved shift found for today")
    if assignment.shift and assignment.shift.site:
        require_site_feature(db, assignment.shift.site, SiteFeatureKey.check_in_check_out)

    if assignment.check_in and assignment.check_in.status == CheckInStatus.checked_in:
        raise HTTPException(status_code=400, detail="Already checked in to this shift")
    if assignment.check_in and assignment.check_in.status == CheckInStatus.checked_out:
        raise HTTPException(status_code=400, detail="Already checked out of this shift")

    if assignment.check_in:
        ci = assignment.check_in
    else:
        ci = CheckIn(
            assignment_id=assignment.id,
            operator_id=current.id,
            scheduled_start=assignment.start_time,
            scheduled_end=assignment.end_time,
        )
        db.add(ci)

    ci.actual_check_in = now
    ci.status = CheckInStatus.checked_in
    db.commit()
    db.refresh(ci)
    return ci


@router.post("/check-out", response_model=CheckInOut)
def check_out(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    ci: CheckIn | None = (
        db.query(CheckIn)
        .options(joinedload(CheckIn.assignment).joinedload(Assignment.shift).joinedload(Shift.site))
        .filter(
            CheckIn.operator_id == current.id,
            CheckIn.status == CheckInStatus.checked_in,
        )
        .order_by(CheckIn.actual_check_in.desc())
        .first()
    )

    if not ci:
        raise HTTPException(status_code=404, detail="No active check-in found")
    if ci.assignment and ci.assignment.shift and ci.assignment.shift.site:
        require_site_feature(db, ci.assignment.shift.site, SiteFeatureKey.check_in_check_out)

    ci.actual_check_out = now
    ci.status = CheckInStatus.checked_out
    db.commit()
    db.refresh(ci)
    return ci


@router.post("/shifts/{assignment_id}/accept", response_model=AssignmentOut)
def accept_shift(
    assignment_id: uuid.UUID,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    a: Assignment | None = db.get(Assignment, assignment_id)

    if not a or a.operator_id != current.id:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if a.accepted:
        raise HTTPException(status_code=400, detail="Already accepted")

    a.accepted = True
    db.commit()
    db.refresh(a)
    return AssignmentOut.from_orm_with_name(a)


@router.post("/contracts/{assignment_id}/claim", response_model=AssignmentOut)
def claim_contract(
    assignment_id: uuid.UUID,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    a: Assignment | None = (
        db.query(Assignment)
        .options(joinedload(Assignment.shift).joinedload(Shift.site))
        .filter(Assignment.id == assignment_id)
        .first()
    )

    if not a or a.operator_id is not None:
        raise HTTPException(status_code=404, detail="Open contract not found")

    if a.shift.status != ShiftStatus.approved:
        raise HTTPException(status_code=400, detail="Shift is not approved")

    # Overlap check
    if a.start_time and a.end_time:
        if operator_has_overlap(db, current.id, a.shift.date, a.start_time, a.end_time):
            raise HTTPException(status_code=409,
                                detail="You already have a shift that overlaps this time")

    a.operator_id = current.id
    a.accepted = True
    db.commit()
    db.refresh(a)
    return AssignmentOut.from_orm_with_name(a)


# ── Own profile ───────────────────────────────────────────────────────────────

def serialize_operator(db, target: Operator, viewer: Operator) -> OperatorOut:
    """Licence data is omitted from the payload entirely unless the viewer
    qualifies — not merely hidden by the UI."""
    roles = role_names(db, viewer)
    show_licence = can_view_licence(roles, viewer.id, target.id)
    return OperatorOut(
        id=target.id,
        first_name=target.first_name,
        last_name=target.last_name,
        full_name=target.full_name,
        phone_number=target.phone_number,
        discord_id=target.discord_id,
        role=target.role,
        active=target.active,
        created_at=target.created_at,
        is_admin="admin" in role_names(db, target),
        onboarding_status=target.onboarding_status,
        profile_complete=target.profile_complete,
        has_photo=bool(target.photo_key),
        security_licence_number=target.security_licence_number if show_licence else None,
        security_licence_expiry=target.security_licence_expiry if show_licence else None,
        licence_status=licence_status(target) if show_licence else None,
        pay_rate=target.pay_rate if show_licence else None,
    )


@router.get("/profile", response_model=OperatorOut)
def my_profile(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    return serialize_operator(db, current, current)


@router.patch("/profile", response_model=OperatorOut)
def patch_my_profile(
    body: ProfilePatch,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """An operator maintains their own licence details. Names are deliberately
    not editable here — a name change goes through an Admin."""
    if body.security_licence_number is not None:
        number = body.security_licence_number.strip()
        if not number:
            raise HTTPException(status_code=400, detail="Licence number cannot be empty")
        current.security_licence_number = number

    if body.security_licence_expiry is not None:
        today = local_today()
        if body.security_licence_expiry < today:
            raise HTTPException(
                status_code=400,
                detail="That licence has already expired. Speak to a Director rather than "
                       "entering an expired date.",
            )
        current.security_licence_expiry = body.security_licence_expiry

    # Onboarding completes only when everything required is present.
    if (current.onboarding_status == OnboardingStatus.profile_pending
            and current.profile_complete):
        current.onboarding_status = OnboardingStatus.active
        current.activated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(current)
    return serialize_operator(db, current, current)


@router.post("/profile/photo", response_model=OperatorOut)
async def upload_my_photo(
    file: UploadFile = File(...),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Own profile only — there is no endpoint for uploading someone else's face."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="No image was received")

    previous = current.photo_key
    current.photo_key = upload_photo(current.id, raw, file.content_type)

    if (current.onboarding_status == OnboardingStatus.profile_pending
            and current.profile_complete):
        current.onboarding_status = OnboardingStatus.active
        current.activated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(current)

    # Replacing a photo should not leave the old face lying around.
    if previous and previous != current.photo_key:
        purge_photo(previous)

    return serialize_operator(db, current, current)


@router.get("/profile/photo-url", response_model=PhotoUrlOut)
def my_photo_url(
    current: Operator = Depends(get_current_operator),
):
    if not current.photo_key:
        raise HTTPException(status_code=404, detail="No photo on file")
    return PhotoUrlOut(url=signed_url(current.photo_key),
                       expires_in=settings.PHOTO_URL_TTL_SECONDS)


# ── Personal operations ───────────────────────────────────────────────────────
#
# Everything below is scoped to the JWT's operator and takes no operator
# parameter, so there is no route by which one person reaches another's record.

HOURS_THRESHOLD = 160


def _my_assignments(db: Session, operator_id):
    return (
        db.query(Assignment)
        .join(Shift, Assignment.shift_id == Shift.id)
        .filter(Assignment.operator_id == operator_id,
                Shift.status == ShiftStatus.approved)
        .options(joinedload(Assignment.shift).joinedload(Shift.site))
    )


def _activity_row(a: Assignment) -> ActivityRowOut:
    site = a.shift.site
    return ActivityRowOut(
        kind="claimed" if a.accepted else "assigned",
        date=a.shift.date,
        site_slug=site.slug if site else "",
        site_name=site.name if site else "Unknown",
        site_color=site.color if site else "#666666",
        shift_name=a.shift.shift_name,
        start_time=a.start_time,
        end_time=a.end_time,
        accepted=a.accepted,
    )


def _outstanding_for(db: Session, op: Operator, today) -> list[OutstandingAction]:
    """Everything this operator has left undone. The hub card leans on this,
    so it is the mechanism by which someone finds out they forgot something."""
    actions: list[OutstandingAction] = []

    status = licence_status(op, today)
    if status == LicenceStatus.expired:
        actions.append(OutstandingAction(
            kind="licence", severity="critical",
            title="Security licence expired",
            detail="Expired " + op.security_licence_expiry.strftime("%d %b %Y")
                   + ". Speak to a Director.",
            route="/me/profile",
        ))
    elif status == LicenceStatus.expiring_soon:
        days = (op.security_licence_expiry - today).days
        plural = "" if days == 1 else "s"
        actions.append(OutstandingAction(
            kind="licence", severity="caution",
            title="Security licence expiring",
            detail=str(days) + " day" + plural + " left, renew before "
                   + op.security_licence_expiry.strftime("%d %b") + ".",
            route="/me/profile",
        ))
    elif status == LicenceStatus.missing:
        actions.append(OutstandingAction(
            kind="licence", severity="caution",
            title="No licence on file",
            detail="Add your security licence number and expiry date.",
            route="/me/profile",
        ))

    open_periods = (
        db.query(AvailabilityPeriod)
        .filter(AvailabilityPeriod.status == AvailabilityStatus.open)
        .all()
    )
    for p in open_periods:
        submitted = (
            db.query(AvailabilitySubmission)
            .filter(AvailabilitySubmission.operator_id == op.id,
                    AvailabilitySubmission.period_id == p.id)
            .first()
        )
        if submitted:
            continue
        label = datetime(p.year, p.month, 1).strftime("%B %Y")
        days_left = (p.closes_at.date() - today).days
        plural = "" if days_left == 1 else "s"
        actions.append(OutstandingAction(
            kind="availability",
            severity="critical" if days_left <= 2 else "caution",
            title="Availability not submitted for " + label,
            detail=("Closes today." if days_left <= 0
                    else "Closes in " + str(days_left) + " day" + plural + "."),
            route="/me/availability",
        ))

    unaccepted = (
        _my_assignments(db, op.id)
        .filter(Shift.date >= today, Assignment.accepted.is_(False))
        .count()
    )
    if unaccepted:
        plural = "" if unaccepted == 1 else "s"
        actions.append(OutstandingAction(
            kind="unaccepted_shift", severity="caution",
            title=str(unaccepted) + " shift" + plural + " not accepted",
            detail="Confirm you are working these.",
            route="/me/shifts",
        ))

    return actions


@router.get("/overview", response_model=MeOverviewOut)
def my_overview(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Answers 'what do I need to do' in one payload."""
    today = local_today()
    now = local_now_naive()
    now_min = now.hour * 60 + now.minute

    upcoming = (
        _my_assignments(db, current.id)
        .filter(Shift.date >= today)
        .order_by(Shift.date, Assignment.start_time)
        .all()
    )

    next_shift = None
    for a in upcoming:
        # A shift that already finished earlier today is not the next one.
        if a.shift.date == today and a.start_time and a.end_time:
            start_min = a.start_time.hour * 60 + a.start_time.minute
            end_min = a.end_time.hour * 60 + a.end_time.minute
            if end_min > start_min and end_min <= now_min:
                continue
        next_shift = a
        break

    next_out = None
    if next_shift:
        site = next_shift.shift.site
        starts_at = datetime.combine(next_shift.shift.date,
                                     next_shift.start_time or time(0, 0))
        next_out = NextShiftOut(
            assignment_id=next_shift.id,
            site_slug=site.slug if site else "",
            site_name=site.name if site else "Unknown",
            site_color=site.color if site else "#666666",
            date=next_shift.shift.date,
            shift_name=next_shift.shift.shift_name,
            start_time=next_shift.start_time,
            end_time=next_shift.end_time,
            position=next_shift.position,
            accepted=next_shift.accepted,
            hours_until=round((starts_at - now).total_seconds() / 3600, 1),
        )

    month_hours = hours_for_operator_month(db, current.id, today.month, today.year)
    month_count = (
        _my_assignments(db, current.id)
        .filter(extract("month", Shift.date) == today.month,
                extract("year", Shift.date) == today.year)
        .count()
    )

    recent = (
        _my_assignments(db, current.id)
        .order_by(Shift.date.desc())
        .limit(8)
        .all()
    )

    return MeOverviewOut(
        operator_id=current.id,
        full_name=current.full_name,
        has_photo=bool(current.photo_key),
        next_shift=next_out,
        month_hours=month_hours,
        month_shift_count=month_count,
        hours_threshold=HOURS_THRESHOLD,
        outstanding=_outstanding_for(db, current, today),
        recent_activity=[_activity_row(a) for a in recent],
    )


@router.get("/history", response_model=list[HistoryRowOut])
def my_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """The operator's own check-in record — what was scheduled against what was
    logged. Reported as recorded times, not scored."""
    rows = (
        db.query(CheckIn)
        .join(Assignment, CheckIn.assignment_id == Assignment.id)
        .join(Shift, Assignment.shift_id == Shift.id)
        .filter(CheckIn.operator_id == current.id)
        .options(joinedload(CheckIn.assignment)
                 .joinedload(Assignment.shift)
                 .joinedload(Shift.site))
        .order_by(Shift.date.desc())
        .offset(offset).limit(limit)
        .all()
    )

    out: list[HistoryRowOut] = []
    for ci in rows:
        shift = ci.assignment.shift if ci.assignment else None
        site = shift.site if shift else None
        delta = None
        if ci.actual_check_in and shift:
            scheduled = datetime.combine(shift.date, ci.scheduled_start)
            delta = int(round((ci.actual_check_in - scheduled).total_seconds() / 60))
        out.append(HistoryRowOut(
            check_in_id=ci.id,
            date=shift.date if shift else local_today(),
            site_name=site.name if site else "Unknown",
            site_color=site.color if site else "#666666",
            shift_name=shift.shift_name if shift else "",
            scheduled_start=ci.scheduled_start,
            scheduled_end=ci.scheduled_end,
            actual_check_in=ci.actual_check_in,
            actual_check_out=ci.actual_check_out,
            status=ci.status,
            notes=ci.notes,
            start_delta_minutes=delta,
        ))
    return out


@router.get("/activity", response_model=list[ActivityRowOut])
def my_activity(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Shifts this operator claimed or was assigned, newest first.

    Handoffs are absent because no handoff feature exists yet — there is no
    model for offering a shift to another operator.
    """
    rows = (
        _my_assignments(db, current.id)
        .order_by(Shift.date.desc())
        .offset(offset).limit(limit)
        .all()
    )
    return [_activity_row(a) for a in rows]


# ── Invoices (self-scoped) ───────────────────────────────────────────────────

def _parse_period(period: str) -> tuple[int, int]:
    try:
        year_s, month_s = period.split("-")
        year, month = int(year_s), int(month_s)
        if not (1 <= month <= 12):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="Period must be formatted YYYY-MM")
    return month, year


def _build_line_item_out(li: InvoiceLineItem) -> InvoiceLineItemOut:
    return InvoiceLineItemOut(
        id=li.id, site_id=li.site_id,
        site_name=li.site.name if li.site else None,
        site_slug=li.site.slug if li.site else None,
        date=li.date, shift_name=li.shift_name,
        start_time=li.start_time, end_time=li.end_time,
        hours=li.hours, rate=li.rate, amount=li.amount,
    )


def build_invoice_detail(inv: Invoice) -> InvoiceDetailOut:
    return InvoiceDetailOut(
        id=inv.id, operator_id=inv.operator_id,
        operator_name=inv.operator.full_name if inv.operator else None,
        period_month=inv.period_month, period_year=inv.period_year,
        status=inv.status, submitted_at=inv.submitted_at,
        approved_at=inv.approved_at, approved_by=inv.approved_by,
        paid_at=inv.paid_at, marked_paid_by=inv.marked_paid_by,
        total_hours=inv.total_hours, total_amount=inv.total_amount,
        gst_amount=inv.gst_amount, created_at=inv.created_at,
        line_items=[_build_line_item_out(li) for li in inv.line_items],
    )


@router.get("/invoices", response_model=list[InvoiceOut])
def my_invoices(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Invoice)
        .filter(Invoice.operator_id == current.id)
        .order_by(Invoice.period_year.desc(), Invoice.period_month.desc())
        .all()
    )
    return [InvoiceOut.model_validate(i) for i in rows]


@router.get("/invoices/{period}/preview", response_model=InvoicePreviewOut)
def preview_invoice(
    period: str,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Ephemeral — nothing is written until /submit. Safe to call repeatedly
    while the period is still open."""
    month, year = _parse_period(period)
    existing = (
        db.query(Invoice)
        .filter(Invoice.operator_id == current.id,
                Invoice.period_month == month, Invoice.period_year == year)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409,
                           detail=f"An invoice for {period} was already {existing.status.value}")
    if current.pay_rate is None:
        raise HTTPException(status_code=400, detail="No pay rate is on file for you yet — ask a Director.")

    items = build_line_items(db, current, month, year)
    total_hours = sum((i.hours for i in items), Decimal("0"))
    total_amount = sum((i.amount for i in items), Decimal("0"))
    return InvoicePreviewOut(
        period_month=month, period_year=year,
        total_hours=total_hours, total_amount=total_amount,
        line_items=[InvoiceLineItemOut(
            id=uuid.uuid4(), site_id=i.site.id,
            site_name=i.site.name if i.site else None,
            site_slug=i.site.slug if i.site else None,
            date=i.date, shift_name=i.shift_name,
            start_time=i.start_time, end_time=i.end_time,
            hours=i.hours, rate=i.rate, amount=i.amount,
        ) for i in items],
    )


@router.post("/invoices/{period}/submit", response_model=InvoiceDetailOut, status_code=201)
def submit_invoice(
    period: str,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    month, year = _parse_period(period)
    existing = (
        db.query(Invoice)
        .filter(Invoice.operator_id == current.id,
                Invoice.period_month == month, Invoice.period_year == year)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409,
                           detail=f"An invoice for {period} was already {existing.status.value}")
    if current.pay_rate is None:
        raise HTTPException(status_code=400, detail="No pay rate is on file for you yet — ask a Director.")

    items = build_line_items(db, current, month, year)
    if not items:
        raise HTTPException(status_code=400, detail=f"No worked shifts found for {period}")

    total_hours = sum((i.hours for i in items), Decimal("0"))
    total_amount = sum((i.amount for i in items), Decimal("0"))

    invoice = Invoice(
        operator_id=current.id, period_month=month, period_year=year,
        status=InvoiceStatus.submitted, submitted_at=utc_now_naive(),
        total_hours=total_hours, total_amount=total_amount,
    )
    db.add(invoice)
    db.flush()
    for i in items:
        db.add(InvoiceLineItem(
            invoice_id=invoice.id, site_id=i.site.id, date=i.date,
            shift_name=i.shift_name, start_time=i.start_time, end_time=i.end_time,
            hours=i.hours, rate=i.rate, amount=i.amount,
        ))
    db.commit()
    db.refresh(invoice)
    return build_invoice_detail(invoice)


MAX_INVOICE_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB


@router.post("/invoices/upload", response_model=ChatMessageOut, status_code=201)
async def upload_invoice(
    site_id: uuid.UUID,
    period_month: int = Query(..., ge=1, le=12),
    period_year: int = Query(..., ge=2020),
    file: UploadFile = File(...),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Operators submit their own invoice as a file rather than the app
    computing one. It's posted into the Directors channel as an attachment
    tagged with site + period, so a Director can browse by site then month
    without scrolling the raw feed (see GET /chat/directors/invoices)."""
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    has_access = (
        db.query(SiteAccess)
        .filter(SiteAccess.operator_id == current.id, SiteAccess.site_id == site_id)
        .first()
    )
    if not has_access:
        raise HTTPException(status_code=403, detail="You do not have access to this site")
    require_site_feature(db, site, SiteFeatureKey.invoicing)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="No file was received")
    if len(raw) > MAX_INVOICE_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File is too large (max 15 MB)")
    filename = file.filename or "invoice"

    channel = (
        db.query(ChatChannel)
        .filter(ChatChannel.channel_type == ChatChannelType.directors)
        .first()
    )
    if not channel:
        raise HTTPException(status_code=500, detail="Directors channel is not configured")

    attachment_key = upload_attachment(str(current.id), filename, raw)

    msg = ChatMessage(
        channel_id=channel.id, operator_id=current.id,
        body=f"📄 Invoice uploaded — {site.name} — {period_year}-{period_month:02d}",
        created_at=local_now_naive(),
        attachment_key=attachment_key, attachment_filename=filename,
        attachment_site_id=site.id,
        attachment_period_month=period_month, attachment_period_year=period_year,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    return ChatMessageOut(
        id=msg.id, channel_id=msg.channel_id, operator_id=msg.operator_id,
        operator_name=current.full_name,
        operator_photo_url=signed_url(current.photo_key) if current.photo_key else None,
        body=msg.body, created_at=msg.created_at,
        attachment_filename=msg.attachment_filename,
        attachment_url=signed_attachment_url(attachment_key, filename),
        attachment_site_slug=site.slug,
        attachment_period_month=period_month, attachment_period_year=period_year,
    )


@router.get("/invoices/uploads", response_model=list[ChatMessageOut])
def my_invoice_uploads(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """This operator's own upload history, newest first."""
    rows = (
        db.query(ChatMessage)
        .options(joinedload(ChatMessage.attachment_site))
        .filter(ChatMessage.operator_id == current.id, ChatMessage.attachment_key.isnot(None))
        .order_by(ChatMessage.created_at.desc())
        .all()
    )
    photo_url = signed_url(current.photo_key) if current.photo_key else None
    return [
        ChatMessageOut(
            id=m.id, channel_id=m.channel_id, operator_id=m.operator_id,
            operator_name=current.full_name, operator_photo_url=photo_url,
            body=m.body, created_at=m.created_at,
            attachment_filename=m.attachment_filename,
            attachment_url=signed_attachment_url(m.attachment_key, m.attachment_filename),
            attachment_site_slug=m.attachment_site.slug if m.attachment_site else None,
            attachment_period_month=m.attachment_period_month,
            attachment_period_year=m.attachment_period_year,
        )
        for m in rows
    ]


@router.get("/roles", response_model=list[str])
def my_role_names(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Every role name this operator holds, including custom ones like
    valor_director — the legacy single `role` enum on OperatorOut can't
    express that, so the frontend needs this to gate role-specific portals."""
    return sorted(role_names(db, current))


@router.get("/divisions", response_model=list[DivisionOut])
def my_divisions(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Which divisions (e.g. Valor Collective) this operator can see, so the
    hub knows whether to show that card at all. Director/Admin see every
    division regardless of a membership row."""
    roles = role_names(db, current)
    if roles & {"admin", "director"}:
        return db.query(Division).order_by(Division.name).all()
    return (
        db.query(Division)
        .join(DivisionOperator, DivisionOperator.division_id == Division.id)
        .filter(DivisionOperator.operator_id == current.id, DivisionOperator.active.is_(True))
        .order_by(Division.name)
        .all()
    )


# ── Client profile (self-managed, Valor Collective) ─────────────────────────
# Entirely separate from the internal profile — no real name, licence number,
# or pay rate ever appears here. The operator fully controls publish state.

def _get_or_create_client_profile(db: Session, operator_id: uuid.UUID) -> ClientProfile:
    cp = db.query(ClientProfile).filter(ClientProfile.operator_id == operator_id).first()
    if not cp:
        cp = ClientProfile(operator_id=operator_id)
        db.add(cp)
        db.commit()
        db.refresh(cp)
    return cp


def _build_client_profile_out(cp: ClientProfile) -> ClientProfileOut:
    return ClientProfileOut(
        id=cp.id, operator_id=cp.operator_id, headline=cp.headline, bio=cp.bio,
        skills=cp.skills or [], years_experience=cp.years_experience,
        has_photo=bool(cp.photo_key),
        photo_url=signed_url(cp.photo_key) if cp.photo_key else None,
        visible=cp.visible, updated_at=cp.updated_at,
    )


@router.get("/client-profile", response_model=ClientProfileOut)
def my_client_profile(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    return _build_client_profile_out(_get_or_create_client_profile(db, current.id))


@router.put("/client-profile", response_model=ClientProfileOut)
def update_client_profile(
    body: ClientProfilePatch,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    cp = _get_or_create_client_profile(db, current.id)
    if body.headline is not None:
        cp.headline = body.headline
    if body.bio is not None:
        cp.bio = body.bio
    if body.skills is not None:
        cp.skills = body.skills
    if body.years_experience is not None:
        cp.years_experience = body.years_experience
    cp.updated_at = utc_now_naive()
    db.commit()
    db.refresh(cp)
    return _build_client_profile_out(cp)


@router.post("/client-profile/publish", response_model=ClientProfileOut)
def publish_client_profile(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    cp = _get_or_create_client_profile(db, current.id)
    cp.visible = True
    cp.updated_at = utc_now_naive()
    db.commit()
    db.refresh(cp)
    return _build_client_profile_out(cp)


@router.post("/client-profile/unpublish", response_model=ClientProfileOut)
def unpublish_client_profile(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    cp = _get_or_create_client_profile(db, current.id)
    cp.visible = False
    cp.updated_at = utc_now_naive()
    db.commit()
    db.refresh(cp)
    return _build_client_profile_out(cp)


@router.post("/client-profile/photo", response_model=ClientProfileOut)
async def upload_client_profile_photo(
    file: UploadFile = File(...),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """A client-facing photo the operator chooses — a distinct storage key
    from their internal profile photo, never mixed up with it."""
    cp = _get_or_create_client_profile(db, current.id)
    raw = await file.read()
    old_key = cp.photo_key
    cp.photo_key = upload_photo(current.id, raw, file.content_type, folder=CLIENT_PHOTO_FOLDER)
    cp.updated_at = utc_now_naive()
    db.commit()
    db.refresh(cp)
    if old_key:
        purge_photo(old_key)
    return _build_client_profile_out(cp)
