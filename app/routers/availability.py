import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..deps import get_current_operator, require_director
from ..models import (AvailabilityEntry, AvailabilityPeriod, AvailabilityStatus,
                      AvailabilitySubmission, Operator)
from ..schemas import (AvailabilityEntryOut, AvailabilityPeriodCreate,
                       AvailabilityPeriodOut, AvailabilityPeriodPatch,
                       AvailabilitySubmissionIn, AvailabilitySubmissionOut,
                       AvailabilitySubmissionWithOperator,
                       AvailabilitySummaryCell, AvailabilitySummaryOperator,
                       MissingOperatorOut)

router = APIRouter(tags=["availability"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_period_or_404(db: Session, period_id: uuid.UUID) -> AvailabilityPeriod:
    period = db.get(AvailabilityPeriod, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="Availability period not found")
    return period


def _build_submission_out(sub: AvailabilitySubmission) -> AvailabilitySubmissionOut:
    return AvailabilitySubmissionOut(
        id=sub.id,
        operator_id=sub.operator_id,
        period_id=sub.period_id,
        submitted_at=sub.submitted_at,
        updated_at=sub.updated_at,
        entries=[AvailabilityEntryOut.model_validate(e) for e in sub.entries],
    )


# ── Operator (self-scoped) ──────────────────────────────────────────────────────

@router.get("/me/availability/periods", response_model=list[AvailabilityPeriodOut])
def my_open_periods(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Periods I can currently submit for, plus any closed period I already
    have a submission on (so it can be shown read-only rather than hidden)."""
    my_submitted_period_ids = {
        row[0] for row in
        db.query(AvailabilitySubmission.period_id)
        .filter(AvailabilitySubmission.operator_id == current.id)
        .all()
    }
    periods = (
        db.query(AvailabilityPeriod)
        .filter(AvailabilityPeriod.status != AvailabilityStatus.draft)
        .order_by(AvailabilityPeriod.year, AvailabilityPeriod.month)
        .all()
    )
    return [
        p for p in periods
        if p.status == AvailabilityStatus.open or p.id in my_submitted_period_ids
    ]


@router.get("/me/availability/{period_id}", response_model=Optional[AvailabilitySubmissionOut])
def my_submission(
    period_id: uuid.UUID,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    _get_period_or_404(db, period_id)
    sub = (
        db.query(AvailabilitySubmission)
        .options(joinedload(AvailabilitySubmission.entries))
        .filter(
            AvailabilitySubmission.operator_id == current.id,
            AvailabilitySubmission.period_id == period_id,
        )
        .first()
    )
    return _build_submission_out(sub) if sub else None


@router.put("/me/availability/{period_id}", response_model=AvailabilitySubmissionOut)
def upsert_my_submission(
    period_id: uuid.UUID,
    body: AvailabilitySubmissionIn,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    period = _get_period_or_404(db, period_id)
    if period.status != AvailabilityStatus.open:
        raise HTTPException(status_code=400, detail="This availability period is not open for submissions")
    if _now() > period.closes_at:
        raise HTTPException(status_code=400, detail="The submission deadline for this period has passed")

    sub = (
        db.query(AvailabilitySubmission)
        .filter(
            AvailabilitySubmission.operator_id == current.id,
            AvailabilitySubmission.period_id == period_id,
        )
        .first()
    )
    now = _now()
    if not sub:
        sub = AvailabilitySubmission(
            operator_id=current.id,
            period_id=period_id,
            submitted_at=now,
            updated_at=now,
        )
        db.add(sub)
        db.flush()
    else:
        sub.updated_at = now
        for e in list(sub.entries):
            db.delete(e)
        db.flush()

    for entry in body.entries:
        db.add(AvailabilityEntry(
            submission_id=sub.id,
            date=entry.date,
            shift_name=entry.shift_name,
            available=entry.available,
            earliest_start=entry.earliest_start,
            latest_end=entry.latest_end,
            note=entry.note,
        ))

    db.commit()
    db.refresh(sub)
    return _build_submission_out(sub)


@router.delete("/me/availability/{period_id}", status_code=204)
def withdraw_my_submission(
    period_id: uuid.UUID,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    period = _get_period_or_404(db, period_id)
    if period.status == AvailabilityStatus.open and _now() > period.closes_at:
        raise HTTPException(status_code=400, detail="The submission deadline for this period has passed")

    sub = (
        db.query(AvailabilitySubmission)
        .filter(
            AvailabilitySubmission.operator_id == current.id,
            AvailabilitySubmission.period_id == period_id,
        )
        .first()
    )
    if sub:
        db.delete(sub)
        db.commit()


# ── Director / Admin ─────────────────────────────────────────────────────────────

@router.get("/availability/periods", response_model=list[AvailabilityPeriodOut])
def list_periods(
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    return (
        db.query(AvailabilityPeriod)
        .order_by(AvailabilityPeriod.year.desc(), AvailabilityPeriod.month.desc())
        .all()
    )


@router.post("/availability/periods", response_model=AvailabilityPeriodOut, status_code=201)
def create_period(
    body: AvailabilityPeriodCreate,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    period = AvailabilityPeriod(
        month=body.month,
        year=body.year,
        opens_at=body.opens_at,
        closes_at=body.closes_at,
        status=body.status,
    )
    db.add(period)
    db.commit()
    db.refresh(period)
    return period


@router.patch("/availability/periods/{period_id}", response_model=AvailabilityPeriodOut)
def patch_period(
    period_id: uuid.UUID,
    body: AvailabilityPeriodPatch,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    period = _get_period_or_404(db, period_id)
    if body.opens_at is not None:
        period.opens_at = body.opens_at
    if body.closes_at is not None:
        period.closes_at = body.closes_at
    if body.status is not None:
        period.status = body.status
    db.commit()
    db.refresh(period)
    return period


@router.get("/availability/periods/{period_id}/submissions",
           response_model=list[AvailabilitySubmissionWithOperator])
def period_submissions(
    period_id: uuid.UUID,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    _get_period_or_404(db, period_id)
    subs = (
        db.query(AvailabilitySubmission)
        .options(joinedload(AvailabilitySubmission.entries),
                 joinedload(AvailabilitySubmission.operator))
        .filter(AvailabilitySubmission.period_id == period_id)
        .all()
    )
    return [
        AvailabilitySubmissionWithOperator(
            **_build_submission_out(s).model_dump(),
            operator_name=s.operator.full_name if s.operator else "Unknown",
        )
        for s in subs
    ]


@router.get("/availability/periods/{period_id}/missing", response_model=list[MissingOperatorOut])
def period_missing(
    period_id: uuid.UUID,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    _get_period_or_404(db, period_id)
    submitted_ids = {
        row[0] for row in
        db.query(AvailabilitySubmission.operator_id)
        .filter(AvailabilitySubmission.period_id == period_id)
        .all()
    }
    operators = db.query(Operator).filter(Operator.active.is_(True)).order_by(Operator.full_name).all()
    return [
        MissingOperatorOut(operator_id=op.id, operator_name=op.full_name)
        for op in operators
        if op.id not in submitted_ids
    ]


@router.get("/availability/periods/{period_id}/summary", response_model=list[AvailabilitySummaryCell])
def period_summary(
    period_id: uuid.UUID,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    _get_period_or_404(db, period_id)
    subs = (
        db.query(AvailabilitySubmission)
        .options(joinedload(AvailabilitySubmission.entries),
                 joinedload(AvailabilitySubmission.operator))
        .filter(AvailabilitySubmission.period_id == period_id)
        .all()
    )

    cells: dict[tuple, list[AvailabilitySummaryOperator]] = {}
    for sub in subs:
        operator_name = sub.operator.full_name if sub.operator else "Unknown"
        for entry in sub.entries:
            if not entry.available:
                continue
            key = (entry.date, entry.shift_name)
            cells.setdefault(key, []).append(AvailabilitySummaryOperator(
                operator_id=sub.operator_id,
                operator_name=operator_name,
                earliest_start=entry.earliest_start,
                latest_end=entry.latest_end,
                note=entry.note,
            ))

    return [
        AvailabilitySummaryCell(date=d, shift_name=shift, available_operators=ops)
        for (d, shift), ops in sorted(cells.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]
