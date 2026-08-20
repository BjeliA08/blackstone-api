import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..deps import get_current_operator, require_director
from ..business import narrows_shift, shifts_are_consecutive
from ..models import (AvailabilityEntry, AvailabilityPeriod, AvailabilityStatus,
                      AvailabilitySubmission, CoverageType, Operator, Site,
                      SiteShift)
from ..schemas import (AvailabilityEntryOut, AvailabilityPeriodCreate,
                       AvailabilityPeriodOut, AvailabilityPeriodPatch,
                       AvailabilitySubmissionIn, AvailabilitySubmissionOut,
                       AvailabilitySubmissionWithOperator,
                       AvailabilitySummaryCell, AvailabilitySummaryOperator,
                       MissingOperatorOut, SiteShiftCreate, SiteShiftOut,
                       SiteShiftPatch)

router = APIRouter(tags=["availability"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_period_or_404(db: Session, period_id: uuid.UUID) -> AvailabilityPeriod:
    period = db.get(AvailabilityPeriod, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="Availability period not found")
    return period


def _build_site_shift_out(ss: SiteShift) -> SiteShiftOut:
    return SiteShiftOut(
        id=ss.id,
        site_id=ss.site_id,
        site_slug=ss.site.slug if ss.site else None,
        site_name=ss.site.name if ss.site else None,
        shift_name=ss.shift_name,
        start_time=ss.start_time,
        end_time=ss.end_time,
        sort_order=ss.sort_order,
        active=ss.active,
    )


def _derive_coverage_types(db: Session, entries) -> list[CoverageType]:
    """An entry is partial_fallback when it is the *second* of two consecutive
    available shifts at the same site on the same day and the operator's limits
    cover less than that shift. Derived server-side so the stored value cannot
    disagree with the times, whatever a client sends.
    """
    shifts = (
        db.query(SiteShift)
        .filter(SiteShift.active.is_(True))
        .order_by(SiteShift.sort_order)
        .all()
    )
    # (site_id, shift_name) -> (start, end)
    windows = {
        (ss.site_id, ss.shift_name): (ss.start_time, ss.end_time)
        for ss in shifts
        if ss.start_time and ss.end_time
    }

    available_keys = {
        (e.site_id, e.date, e.shift_name) for e in entries if e.available
    }

    result: list[CoverageType] = []
    for e in entries:
        win = windows.get((e.site_id, e.shift_name))
        if not e.available or not win:
            result.append(CoverageType.full)
            continue

        start, end = win
        if not narrows_shift(start, end, e.earliest_start, e.latest_end):
            result.append(CoverageType.full)
            continue

        # Narrowed — only a fallback if it directly follows another shift the
        # operator also offered that day at this site.
        follows_available_shift = any(
            other_name != e.shift_name
            and (e.site_id, e.date, other_name) in available_keys
            and shifts_are_consecutive(other_win[1], start)
            for (sid, other_name), other_win in windows.items()
            if sid == e.site_id and other_win[0] and other_win[1]
        )
        result.append(
            CoverageType.partial_fallback if follows_available_shift else CoverageType.full
        )
    return result


def _build_submission_out(sub: AvailabilitySubmission) -> AvailabilitySubmissionOut:
    return AvailabilitySubmissionOut(
        id=sub.id,
        operator_id=sub.operator_id,
        period_id=sub.period_id,
        submitted_at=sub.submitted_at,
        updated_at=sub.updated_at,
        entries=[AvailabilityEntryOut.model_validate(e) for e in sub.entries],
    )


# ── Site shift configuration ────────────────────────────────────────────────────

@router.get("/availability/site-shifts", response_model=list[SiteShiftOut])
def list_site_shifts(
    include_inactive: bool = False,
    _: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Which shifts each site runs. Any signed-in operator needs this to fill in
    their availability grid, so it is not director-gated."""
    q = (
        db.query(SiteShift)
        .options(joinedload(SiteShift.site))
        .join(Site, SiteShift.site_id == Site.id)
        .filter(Site.active.is_(True))
    )
    if not include_inactive:
        q = q.filter(SiteShift.active.is_(True))
    rows = q.order_by(Site.name, SiteShift.sort_order, SiteShift.shift_name).all()
    return [_build_site_shift_out(ss) for ss in rows]


@router.post("/availability/site-shifts", response_model=SiteShiftOut, status_code=201)
def create_site_shift(
    body: SiteShiftCreate,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    site = db.query(Site).filter(Site.slug == body.site_slug).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{body.site_slug}' not found")

    name = body.shift_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Shift name cannot be empty")

    existing = (
        db.query(SiteShift)
        .filter(SiteShift.site_id == site.id, SiteShift.shift_name == name)
        .first()
    )
    if existing:
        # Re-adding a name that was removed earlier reactivates it rather than
        # colliding with the unique constraint.
        if existing.active:
            raise HTTPException(status_code=409, detail=f"{site.name} already has a '{name}' shift")
        existing.active = True
        existing.sort_order = body.sort_order
        existing.start_time = body.start_time
        existing.end_time = body.end_time
        db.commit()
        db.refresh(existing)
        return _build_site_shift_out(existing)

    ss = SiteShift(site_id=site.id, shift_name=name, sort_order=body.sort_order,
                   start_time=body.start_time, end_time=body.end_time)
    db.add(ss)
    db.commit()
    db.refresh(ss)
    return _build_site_shift_out(ss)


@router.patch("/availability/site-shifts/{shift_id}", response_model=SiteShiftOut)
def patch_site_shift(
    shift_id: uuid.UUID,
    body: SiteShiftPatch,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    ss = db.get(SiteShift, shift_id)
    if not ss:
        raise HTTPException(status_code=404, detail="Shift not found")
    if body.shift_name is not None:
        name = body.shift_name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Shift name cannot be empty")
        ss.shift_name = name
    if body.clear_times:
        ss.start_time = None
        ss.end_time = None
    else:
        if body.start_time is not None:
            ss.start_time = body.start_time
        if body.end_time is not None:
            ss.end_time = body.end_time
    if body.sort_order is not None:
        ss.sort_order = body.sort_order
    if body.active is not None:
        ss.active = body.active
    db.commit()
    db.refresh(ss)
    return _build_site_shift_out(ss)


@router.delete("/availability/site-shifts/{shift_id}", status_code=204)
def delete_site_shift(
    shift_id: uuid.UUID,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    """Retires a shift. Kept as a row so historical availability that
    references it stays readable."""
    ss = db.get(SiteShift, shift_id)
    if ss:
        ss.active = False
        db.commit()


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

    valid_site_ids = {s.id for s in db.query(Site).filter(Site.active.is_(True)).all()}
    coverage = _derive_coverage_types(db, body.entries)
    for idx, entry in enumerate(body.entries):
        if entry.site_id not in valid_site_ids:
            raise HTTPException(status_code=400,
                                detail=f"Unknown site on entry for {entry.date} {entry.shift_name}")
        db.add(AvailabilityEntry(
            submission_id=sub.id,
            site_id=entry.site_id,
            date=entry.date,
            shift_name=entry.shift_name,
            available=entry.available,
            earliest_start=entry.earliest_start,
            latest_end=entry.latest_end,
            note=entry.note,
            coverage_type=coverage[idx],
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

    slug_by_site = {s.id: s.slug for s in db.query(Site).all()}

    cells: dict[tuple, list[AvailabilitySummaryOperator]] = {}
    for sub in subs:
        operator_name = sub.operator.full_name if sub.operator else "Unknown"
        for entry in sub.entries:
            if not entry.available:
                continue
            key = (entry.date, entry.site_id, entry.shift_name)
            cells.setdefault(key, []).append(AvailabilitySummaryOperator(
                operator_id=sub.operator_id,
                operator_name=operator_name,
                earliest_start=entry.earliest_start,
                latest_end=entry.latest_end,
                note=entry.note,
                coverage_type=entry.coverage_type,
            ))

    return [
        AvailabilitySummaryCell(
            date=d,
            site_id=site_id,
            site_slug=slug_by_site.get(site_id),
            shift_name=shift,
            available_operators=sorted(ops, key=lambda o: o.coverage_type != CoverageType.full),
            full_count=sum(1 for o in ops if o.coverage_type == CoverageType.full),
            partial_count=sum(1 for o in ops if o.coverage_type == CoverageType.partial_fallback),
        )
        for (d, site_id, shift), ops in sorted(
            cells.items(), key=lambda kv: (kv[0][0], slug_by_site.get(kv[0][1]) or "", kv[0][2])
        )
    ]
