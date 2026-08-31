"""Site selection hub.

Every visibility rule is applied here. A site the requester is not entitled
to see is never serialized, so hiding a card in the UI is presentation
rather than security.
"""
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from .chat import _role_names
from ..business import shift_window_minutes
from ..clock import local_now_naive
from ..database import get_db
from ..deps import get_current_operator
from ..models import (Assignment, ChatChannel, ChatMessage, ChatRead, Operator,
                      Shift, ShiftStatus, Site, SiteAccess, SiteFeature,
                      SiteShift, SiteStatus, SiteType)
from ..schemas import SiteCardOut, SiteSummary

router = APIRouter(tags=["sites"])

URGENT_WINDOW_HOURS = 24


def _now() -> datetime:
    """Local wall-clock: shift windows are Edmonton time, not UTC."""
    return local_now_naive()


def _minutes_now(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _current_shift(db: Session, site: Site, now: datetime):
    """The shift covering this moment, if any — overnight-aware."""
    defs = (
        db.query(SiteShift)
        .filter(SiteShift.site_id == site.id, SiteShift.active.is_(True))
        .order_by(SiteShift.sort_order)
        .all()
    )
    m = _minutes_now(now)
    for ss in defs:
        if not ss.start_time or not ss.end_time:
            continue
        start, end = shift_window_minutes(ss.start_time, ss.end_time)
        # An overnight shift that began yesterday still covers us now.
        if start <= m < end or (end > 1440 and m + 1440 < end and m + 1440 >= start):
            return ss
    return None


def _summary(db: Session, site: Site, operator: Operator, now: datetime) -> SiteSummary:
    today = now.date()
    s = SiteSummary()

    current = _current_shift(db, site, now)
    if current:
        s.current_shift_name = current.shift_name
        shift = (
            db.query(Shift)
            .filter(Shift.site_id == site.id, Shift.date == today,
                    Shift.shift_name == current.shift_name,
                    Shift.status == ShiftStatus.approved)
            .first()
        )
        if shift:
            slots = shift.assignments
            s.posts_required = len(slots)
            s.on_post = sum(1 for a in slots if a.operator_id is not None)
        else:
            s.posts_required = current.posts_required_on(today.weekday(), site.slot_count)

    # Unfilled slots from today onwards — what someone could still pick up.
    open_rows = (
        db.query(Shift.date, Assignment.start_time)
        .join(Assignment, Assignment.shift_id == Shift.id)
        .filter(Shift.site_id == site.id, Shift.date >= today,
                Shift.status == ShiftStatus.approved,
                Assignment.operator_id.is_(None))
        .all()
    )
    s.open_contracts = len(open_rows)
    cutoff = now + timedelta(hours=URGENT_WINDOW_HOURS)
    for d, start in open_rows:
        starts_at = datetime.combine(d, start or time(0, 0))
        if now <= starts_at <= cutoff:
            s.urgent_contracts += 1

    # Unread messages in this site's channel, for this operator.
    channel = db.query(ChatChannel).filter(ChatChannel.site_id == site.id).first()
    if channel:
        read = (
            db.query(ChatRead)
            .filter(ChatRead.channel_id == channel.id, ChatRead.operator_id == operator.id)
            .first()
        )
        q = db.query(func.count(ChatMessage.id)).filter(ChatMessage.channel_id == channel.id)
        if read:
            q = q.filter(ChatMessage.created_at > read.last_read_at)
        s.unread_messages = q.scalar() or 0

    return s


@router.get("/me/sites", response_model=list[SiteCardOut])
def my_sites(
    include_archived: bool = False,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Sites this operator may select, each with a live summary.

    Operators get only sites they hold access for, and only while those sites
    are active or upcoming — an ended temporary site disappears from their
    selector. Directors and Admin keep seeing ended sites so historical hours
    and billing stay reachable. Archived sites are Admin-only.
    """
    today = _now().date()
    roles = _role_names(db, current)
    is_admin = "admin" in roles
    privileged = is_admin or "director" in roles

    sites = db.query(Site).filter(Site.active.is_(True)).order_by(Site.name).all()

    granted = {
        r[0] for r in
        db.query(SiteAccess.site_id).filter(SiteAccess.operator_id == current.id).all()
    }

    features_by_site: dict[uuid.UUID, list[str]] = {}
    for row in db.query(SiteFeature).filter(SiteFeature.enabled.is_(True)).all():
        features_by_site.setdefault(row.site_id, []).append(row.feature_key.value)

    now = _now()
    cards: list[SiteCardOut] = []
    for site in sites:
        status = site.effective_status(today)

        if status == SiteStatus.archived:
            if not (is_admin and include_archived):
                continue
        elif privileged:
            pass  # directors and admin see active, upcoming and ended
        else:
            if site.id not in granted:
                continue
            if status not in (SiteStatus.active, SiteStatus.upcoming):
                continue

        days_remaining = (site.ends_on - today).days if site.ends_on else None

        cards.append(SiteCardOut(
            id=site.id, name=site.name, slug=site.slug, color=site.color,
            site_type=site.site_type, status=status,
            starts_on=site.starts_on, ends_on=site.ends_on,
            days_remaining=days_remaining,
            description=site.description, slot_count=site.slot_count,
            summary=_summary(db, site, current, now),
            features=features_by_site.get(site.id, []),
        ))

    return cards
