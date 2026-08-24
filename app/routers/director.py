import uuid
from datetime import date, datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from ..business import (hours_for_operator_month, is_missed_check_in,
                         is_missed_check_out)
from ..auth import hash_password
from ..clock import local_today
from ..database import get_db
from ..deps import get_current_operator, require_director
from ..routers.me import serialize_operator
from ..models import (Assignment, CheckIn, CheckInStatus, Operator,
                      OperatorRole, Shift, ShiftStatus, Site)
from ..schemas import (AssignmentOut, CheckInStatusRow, HoursSummary,
                       OperatorCreate, OperatorOut, OperatorPatch,
                       ShiftOut, SiteOut)
import secrets

router = APIRouter(tags=["director"])


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


# ── Sites ─────────────────────────────────────────────────────────────────────

@router.get("/sites", response_model=list[SiteOut])
def list_sites(
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    return db.query(Site).order_by(Site.name).all()


@router.get("/sites/{slug}/shifts", response_model=list[ShiftOut])
def site_shifts(
    slug: str,
    shift_date: Optional[date] = Query(None, alias="date"),
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = db.query(Site).filter(Site.slug == slug).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    q = (
        db.query(Shift)
        .filter(Shift.site_id == site.id, Shift.status == ShiftStatus.approved)
        .options(joinedload(Shift.assignments).joinedload(Assignment.operator),
                 joinedload(Shift.site))
    )
    if shift_date:
        q = q.filter(Shift.date == shift_date)

    return [_build_shift_out(s) for s in q.order_by(Shift.date, Shift.shift_name).all()]


# ── Operators ─────────────────────────────────────────────────────────────────

@router.get("/operators", response_model=list[OperatorOut])
def list_operators(
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    return db.query(Operator).order_by(Operator.full_name).all()


@router.post("/operators", response_model=OperatorOut, status_code=201)
def create_operator(
    body: OperatorCreate,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    existing = db.query(Operator).filter(Operator.phone_number == body.phone_number).first()
    if existing:
        raise HTTPException(status_code=409, detail="Phone number already registered")

    setup_code = secrets.token_hex(4).upper()  # 8-char hex code to DM to operator
    first_name, _, last_name = body.full_name.strip().partition(" ")
    op = Operator(
        first_name=first_name,
        last_name=last_name,
        phone_number=body.phone_number,
        discord_id=body.discord_id,
        role=body.role,
        active=body.active,
        setup_code=setup_code,
    )
    db.add(op)
    db.commit()
    db.refresh(op)
    # Return includes setup_code in a custom way — director sees it once to relay via DM
    # (The OperatorOut schema omits it, so we add it as a header or embed in a wrapper)
    # For now: included in response body via an extended dict so director can copy it
    result = OperatorOut.model_validate(op)
    # Attach setup_code to the response dict manually
    response_dict = result.model_dump()
    response_dict["setup_code"] = setup_code
    return response_dict


@router.patch("/operators/{operator_id}", response_model=OperatorOut)
def patch_operator(
    operator_id: uuid.UUID,
    body: OperatorPatch,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    op = db.get(Operator, operator_id)
    if not op:
        raise HTTPException(status_code=404, detail="Operator not found")

    if body.full_name is not None:
        op.full_name = body.full_name
    if body.role is not None:
        op.role = body.role
    if body.active is not None:
        op.active = body.active
    if body.discord_id is not None:
        op.discord_id = body.discord_id

    db.commit()
    db.refresh(op)
    return op


@router.get("/operators/{operator_id}/hours", response_model=HoursSummary)
def operator_hours(
    operator_id: uuid.UUID,
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2020),
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    op = db.get(Operator, operator_id)
    if not op:
        raise HTTPException(status_code=404, detail="Operator not found")

    total = hours_for_operator_month(db, operator_id, month, year)
    return HoursSummary(
        operator_id=operator_id,
        operator_name=op.full_name,
        month=month,
        year=year,
        total_hours=total,
        shift_count=0,  # simplified — full count query omitted for brevity
    )


# ── Check-ins ─────────────────────────────────────────────────────────────────

@router.get("/check-ins/today", response_model=list[CheckInStatusRow])
def check_ins_today(
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    today = local_today()

    assignments: list[Assignment] = (
        db.query(Assignment)
        .join(Shift)
        .filter(
            Shift.date == today,
            Shift.status == ShiftStatus.approved,
            Assignment.operator_id.isnot(None),
        )
        .options(
            joinedload(Assignment.operator),
            joinedload(Assignment.shift).joinedload(Shift.site),
            joinedload(Assignment.check_in),
        )
        .all()
    )

    rows = []
    for a in assignments:
        ci = a.check_in
        pending_ci = CheckIn(
            assignment_id=a.id,
            operator_id=a.operator_id,
            scheduled_start=a.start_time,
            scheduled_end=a.end_time,
            status=CheckInStatus.pending,
        ) if not ci else ci

        rows.append(CheckInStatusRow(
            assignment_id=a.id,
            operator_name=a.operator.full_name if a.operator else "Unknown",
            site_name=a.shift.site.name if a.shift.site else "",
            shift_name=a.shift.shift_name,
            scheduled_start=a.start_time,
            scheduled_end=a.end_time,
            actual_check_in=ci.actual_check_in if ci else None,
            actual_check_out=ci.actual_check_out if ci else None,
            status=ci.status if ci else CheckInStatus.pending,
            missed_check_in=is_missed_check_in(pending_ci),
            missed_check_out=is_missed_check_out(pending_ci) if ci else False,
        ))

    return rows


# ── Roster ────────────────────────────────────────────────────────────────────

@router.get("/roster", response_model=list[OperatorOut])
def roster(
    include_inactive: bool = False,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """The company roster, straight from the database rather than the old
    spreadsheet. Visible to any signed-in operator so people can see who they
    work with; licence number and expiry are still stripped out for anyone
    who is not Admin, a Director, or the operator themselves.
    """
    q = db.query(Operator)
    if not include_inactive:
        q = q.filter(Operator.active.is_(True))
    ops = q.order_by(Operator.first_name, Operator.last_name).all()
    return [serialize_operator(db, o, current) for o in ops]
