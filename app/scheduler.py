"""Schedule generation from submitted availability.

Rules this module enforces, in order of precedence:

1. A `partial_fallback` offer is NEVER used while any `full` candidate exists
   for that slot. This is absolute — it outranks hours balancing and every
   other consideration.
2. An operator is never double-booked into overlapping times on a day.
3. Where a partial offer is used, the uncovered tail of the shift becomes its
   own open slot, so the gap is visible instead of looking filled.
4. Among equally-eligible candidates, the one with the fewest hours assigned
   so far wins, which spreads hours evenly.

Everything is written as draft for a director to review.
"""
from __future__ import annotations

import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import time
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from .business import (Candidate, covered_window, minutes_to_time,
                       select_candidates, shift_duration_hours,
                       shift_window_minutes, times_overlap)
from .models import (Assignment, AvailabilityEntry, AvailabilityPeriod,
                     AvailabilitySubmission, CoverageType, Operator, Shift,
                     ShiftStatus, Site, SiteAccess, SiteShift)

REMAINDER_POSITION = "Uncovered remainder"
DEFAULT_POSITION = "Security Operator"


@dataclass
class UnfilledSlot:
    date: Date
    site_slug: str
    shift_name: str
    slot_index: int
    reason: str


@dataclass
class PartialFill:
    date: Date
    site_slug: str
    shift_name: str
    operator_name: str
    covered_start: time
    covered_end: time
    remainder_start: time
    remainder_end: time

    def describe(self) -> str:
        return (
            f"Partial coverage — {self.covered_start:%H%M}-{self.covered_end:%H%M}, "
            f"{self.remainder_start:%H%M}-{self.remainder_end:%H%M} uncovered"
        )


@dataclass
class GenerationResult:
    period_id: uuid.UUID
    shifts_created: int = 0
    slots_total: int = 0
    slots_filled: int = 0
    partial_fills: list[PartialFill] = field(default_factory=list)
    unfilled: list[UnfilledSlot] = field(default_factory=list)
    hours_by_operator: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def slots_open(self) -> int:
        return self.slots_total - self.slots_filled


@dataclass
class _Booking:
    """One block of time an operator already holds on a given day."""
    start: time
    end: time


class _OperatorLoad:
    """Running hours and bookings, so balancing and overlap checks are cheap."""

    def __init__(self) -> None:
        self.hours: dict[uuid.UUID, float] = {}
        self.bookings: dict[tuple[uuid.UUID, Date], list[_Booking]] = {}

    def hours_for(self, op_id: uuid.UUID) -> float:
        return self.hours.get(op_id, 0.0)

    def conflicts(self, op_id: uuid.UUID, day: Date, start: time, end: time) -> bool:
        for b in self.bookings.get((op_id, day), []):
            if times_overlap(start, end, b.start, b.end):
                return True
        return False

    def add(self, op_id: uuid.UUID, day: Date, start: time, end: time) -> None:
        self.hours[op_id] = self.hours.get(op_id, 0.0) + shift_duration_hours(start, end)
        self.bookings.setdefault((op_id, day), []).append(_Booking(start, end))


def _month_dates(month: int, year: int) -> list[Date]:
    return [Date(year, month, d) for d in range(1, monthrange(year, month)[1] + 1)]


def generate_schedule(
    db: Session,
    period: AvailabilityPeriod,
    respect_site_access: bool = True,
    replace_existing_drafts: bool = True,
) -> GenerationResult:
    result = GenerationResult(period_id=period.id)

    sites = db.query(Site).filter(Site.active.is_(True)).all()
    site_by_id = {s.id: s for s in sites}

    shift_defs = (
        db.query(SiteShift)
        .filter(SiteShift.active.is_(True))
        .order_by(SiteShift.sort_order)
        .all()
    )
    shift_defs = [ss for ss in shift_defs if ss.start_time and ss.end_time]
    if not shift_defs:
        result.warnings.append(
            "No site shifts have start and end times set — nothing can be scheduled. "
            "Set them under Scheduling → Site Shifts."
        )
        return result

    # site access, only consulted when the caller asks for it
    access: dict[uuid.UUID, set[uuid.UUID]] = {}
    for row in db.query(SiteAccess).all():
        access.setdefault(row.operator_id, set()).add(row.site_id)

    operators = {
        o.id: o for o in db.query(Operator).filter(Operator.active.is_(True)).all()
    }

    # availability: (date, site_id, shift_name) -> list of entries
    offers: dict[tuple, list[tuple[AvailabilityEntry, Operator]]] = {}
    subs = (
        db.query(AvailabilitySubmission)
        .options(joinedload(AvailabilitySubmission.entries))
        .filter(AvailabilitySubmission.period_id == period.id)
        .all()
    )
    for sub in subs:
        op = operators.get(sub.operator_id)
        if not op:
            continue
        for e in sub.entries:
            if not e.available:
                continue
            offers.setdefault((e.date, e.site_id, e.shift_name), []).append((e, op))

    if not offers:
        result.warnings.append("No availability has been submitted for this period.")

    if replace_existing_drafts:
        _clear_draft_shifts(db, period)

    load = _OperatorLoad()
    dates = _month_dates(period.month, period.year)

    for day in dates:
        for ss in shift_defs:
            site = site_by_id.get(ss.site_id)
            if not site:
                continue

            shift = Shift(
                id=uuid.uuid4(),
                site_id=site.id,
                date=day,
                shift_name=ss.shift_name,
                status=ShiftStatus.draft,
            )
            db.add(shift)
            db.flush()
            result.shifts_created += 1

            pool = list(offers.get((day, site.id, ss.shift_name), []))
            slot_index = 0

            for _ in range(site.slot_count):
                result.slots_total += 1
                chosen, reason = _pick(
                    pool, ss, day, site, load, access, respect_site_access
                )

                if chosen is None:
                    db.add(Assignment(
                        shift_id=shift.id, slot_index=slot_index,
                        operator_id=None,
                        start_time=ss.start_time, end_time=ss.end_time,
                        position=DEFAULT_POSITION, accepted=False,
                    ))
                    result.unfilled.append(UnfilledSlot(
                        date=day, site_slug=site.slug, shift_name=ss.shift_name,
                        slot_index=slot_index, reason=reason,
                    ))
                    slot_index += 1
                    continue

                entry, op, cov_start, cov_end = chosen
                pool = [(e, o) for (e, o) in pool if o.id != op.id]

                db.add(Assignment(
                    shift_id=shift.id, slot_index=slot_index,
                    operator_id=op.id,
                    start_time=cov_start, end_time=cov_end,
                    position=DEFAULT_POSITION, accepted=False,
                ))
                load.add(op.id, day, cov_start, cov_end)
                result.slots_filled += 1
                slot_index += 1

                # A partial fill leaves a tail that must be visible as its own
                # open slot, never silently dropped.
                if entry.coverage_type == CoverageType.partial_fallback:
                    tail = _remainder(ss, cov_start, cov_end)
                    if tail:
                        r_start, r_end = tail
                        db.add(Assignment(
                            shift_id=shift.id, slot_index=slot_index,
                            operator_id=None,
                            start_time=r_start, end_time=r_end,
                            position=REMAINDER_POSITION, accepted=False,
                        ))
                        result.slots_total += 1
                        result.unfilled.append(UnfilledSlot(
                            date=day, site_slug=site.slug, shift_name=ss.shift_name,
                            slot_index=slot_index,
                            reason="Uncovered remainder after partial fallback coverage",
                        ))
                        result.partial_fills.append(PartialFill(
                            date=day, site_slug=site.slug, shift_name=ss.shift_name,
                            operator_name=op.full_name,
                            covered_start=cov_start, covered_end=cov_end,
                            remainder_start=r_start, remainder_end=r_end,
                        ))
                        slot_index += 1

    db.commit()
    result.hours_by_operator = {
        operators[op_id].full_name: round(h, 2)
        for op_id, h in load.hours.items()
        if op_id in operators
    }
    return result


def _clear_draft_shifts(db: Session, period: AvailabilityPeriod) -> None:
    """Drop previously generated drafts for this month so regenerating does not
    stack duplicates. Approved shifts are never touched."""
    dates = _month_dates(period.month, period.year)
    stale = (
        db.query(Shift)
        .filter(Shift.date.in_(dates), Shift.status == ShiftStatus.draft)
        .all()
    )
    for s in stale:
        db.delete(s)  # assignments cascade
    db.flush()


def _remainder(ss: SiteShift, cov_start: time, cov_end: time) -> Optional[tuple[time, time]]:
    s, e = shift_window_minutes(ss.start_time, ss.end_time)
    cov_hi = covered_window(ss.start_time, ss.end_time, cov_start, cov_end)[1]
    if cov_hi >= e:
        return None
    return minutes_to_time(cov_hi), minutes_to_time(e)


def _pick(
    pool: list[tuple[AvailabilityEntry, Operator]],
    ss: SiteShift,
    day: Date,
    site: Site,
    load: _OperatorLoad,
    access: dict[uuid.UUID, set[uuid.UUID]],
    respect_site_access: bool,
) -> tuple[Optional[tuple[AvailabilityEntry, Operator, time, time]], str]:
    """Choose one operator for a slot, or explain why nobody could take it."""
    if not pool:
        return None, "Nobody offered availability for this shift"

    blocked_access = 0
    blocked_conflict = 0
    eligible: list[tuple[AvailabilityEntry, Operator, time, time]] = []

    for entry, op in pool:
        if respect_site_access and site.id not in access.get(op.id, set()):
            blocked_access += 1
            continue

        lo, hi = covered_window(ss.start_time, ss.end_time, entry.earliest_start, entry.latest_end)
        cov_start, cov_end = minutes_to_time(lo), minutes_to_time(hi)
        if hi <= lo:
            continue

        if load.conflicts(op.id, day, cov_start, cov_end):
            blocked_conflict += 1
            continue

        eligible.append((entry, op, cov_start, cov_end))

    if not eligible:
        if blocked_access:
            return None, f"{blocked_access} operator(s) offered this shift but lack site access"
        if blocked_conflict:
            return None, f"{blocked_conflict} operator(s) offered this shift but are already booked"
        return None, "Nobody offered availability for this shift"

    # Rule 1, absolute: full candidates exclude fallback ones entirely.
    ranked = select_candidates([
        Candidate(
            operator_id=op.id,
            operator_name=op.full_name,
            coverage_type=entry.coverage_type.value,
            earliest_start=entry.earliest_start,
            latest_end=entry.latest_end,
        )
        for entry, op, _, _ in eligible
    ])
    allowed_ids = {c.operator_id for c in ranked}
    tier = [x for x in eligible if x[1].id in allowed_ids]

    # Rule 4: within the surviving tier, even out hours.
    tier.sort(key=lambda x: (load.hours_for(x[1].id), x[1].full_name))
    best = tier[0]

    # Hours protection: never let a partial fill cost an operator hours.
    entry, op, cov_start, cov_end = best
    if entry.coverage_type == CoverageType.partial_fallback:
        gained = shift_duration_hours(cov_start, cov_end)
        if gained <= 0:
            return None, "Partial offer would add no hours"

    return best, ""
