import uuid
from datetime import date, datetime, timezone
from typing import Optional
from fastapi import (APIRouter, Depends, File, HTTPException, Query,
                     UploadFile, status)
from sqlalchemy.orm import Session, joinedload
from ..business import (hours_for_operator_month, is_missed_check_in,
                         is_missed_check_out, operator_has_overlap)
from ..database import get_db
from ..deps import get_current_operator
from ..config import settings
from ..identity import can_view_licence, licence_status, role_names
from ..photos import purge_photo, signed_url, upload_photo
from ..models import (Assignment, CheckIn, CheckInStatus, OnboardingStatus,
                      Operator, Shift, ShiftStatus)
from ..schemas import (AssignmentOut, CheckInOut, HoursSummary, OperatorOut,
                       PhotoUrlOut, ProfilePatch, ShiftOut)

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
    today = datetime.now(timezone.utc).date()
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
    today = datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Find the operator's assignment for today that hasn't been checked in yet
    assignment: Assignment | None = (
        db.query(Assignment)
        .join(Shift)
        .filter(
            Assignment.operator_id == current.id,
            Shift.date == today,
            Shift.status == ShiftStatus.approved,
        )
        .options(joinedload(Assignment.shift), joinedload(Assignment.check_in))
        .order_by(Assignment.start_time)
        .first()
    )

    if not assignment:
        raise HTTPException(status_code=404, detail="No approved shift found for today")

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
        .filter(
            CheckIn.operator_id == current.id,
            CheckIn.status == CheckInStatus.checked_in,
        )
        .order_by(CheckIn.actual_check_in.desc())
        .first()
    )

    if not ci:
        raise HTTPException(status_code=404, detail="No active check-in found")

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
        today = datetime.now(timezone.utc).date()
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
