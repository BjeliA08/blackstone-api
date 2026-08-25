"""Text-only operations comms.

Channel membership is computed on every request from roles and site access —
never stored — so it stays correct the moment someone's role or site grant
changes. Access is enforced here on every endpoint; the frontend hiding a
channel is presentation, not security.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..deps import get_current_operator
from ..models import (ChatChannel, ChatChannelType, ChatMessage, ChatRead,
                      OperationRole, Operator, OperatorRole,
                      OperatorRoleAssignment, Role, SiteAccess)
from ..schemas import (ChatChannelOut, ChatMessageCreate, ChatMessageOut,
                       ChatReadResult)

router = APIRouter(prefix="/chat", tags=["chat"])

SITE_LEAD_ROLES = {"shelter_site_lead", "club101_site_lead", "starhall_site_lead"}

MAX_BODY_CHARS = 4000

# Sites first, then the narrowing group channels.
_TYPE_ORDER = {
    ChatChannelType.site: 0,
    ChatChannelType.operation: 1,
    ChatChannelType.site_leads: 2,
    ChatChannelType.directors: 3,
    ChatChannelType.admin: 4,
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _role_names(db: Session, op: Operator) -> set[str]:
    """Roles from the assignment table, plus the legacy enum column — an
    operator can be a director/admin via either, and both must count."""
    names = {
        r[0] for r in
        db.query(Role.name)
        .join(OperatorRoleAssignment, OperatorRoleAssignment.role_id == Role.id)
        .filter(OperatorRoleAssignment.operator_id == op.id)
        .all()
    }
    if op.role == OperatorRole.admin:
        names.add("admin")
    elif op.role == OperatorRole.director:
        names.add("director")
    return names


def _can_access(channel: ChatChannel, roles: set[str], site_ids: set[uuid.UUID],
                operation_ids: set[uuid.UUID]) -> bool:
    if "admin" in roles:
        return True  # Admin sees every channel, by design.
    if channel.channel_type == ChatChannelType.site:
        return channel.site_id is not None and channel.site_id in site_ids
    if channel.channel_type == ChatChannelType.site_leads:
        return bool(roles & SITE_LEAD_ROLES)
    if channel.channel_type == ChatChannelType.directors:
        return "director" in roles
    if channel.channel_type == ChatChannelType.admin:
        return False  # admin-only, and admins already returned True above
    if channel.channel_type == ChatChannelType.operation:
        # A Valor Director plans every operation, so they get every channel —
        # the same relationship admin has to every other channel type. A
        # plain operator only gets in if they're actually assigned a role.
        if "valor_director" in roles:
            return True
        return channel.operation_id is not None and channel.operation_id in operation_ids
    return False


def _operation_ids_for(db: Session, op: Operator) -> set[uuid.UUID]:
    return {
        r[0] for r in
        db.query(OperationRole.operation_id).filter(OperationRole.operator_id == op.id).all()
    }


def _accessible(db: Session, op: Operator) -> list[ChatChannel]:
    roles = _role_names(db, op)
    site_ids = {
        r[0] for r in db.query(SiteAccess.site_id).filter(SiteAccess.operator_id == op.id).all()
    }
    operation_ids = _operation_ids_for(db, op)
    channels = db.query(ChatChannel).options(joinedload(ChatChannel.site)).all()
    allowed = [c for c in channels if _can_access(c, roles, site_ids, operation_ids)]
    allowed.sort(key=lambda c: (_TYPE_ORDER.get(c.channel_type, 9), c.name.lower()))
    return allowed


def _channel_or_403(db: Session, op: Operator, slug: str) -> ChatChannel:
    channel = db.query(ChatChannel).filter(ChatChannel.slug == slug).first()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    roles = _role_names(db, op)
    site_ids = {
        r[0] for r in db.query(SiteAccess.site_id).filter(SiteAccess.operator_id == op.id).all()
    }
    operation_ids = _operation_ids_for(db, op)
    if not _can_access(channel, roles, site_ids, operation_ids):
        raise HTTPException(status_code=403, detail="You do not have access to this channel")
    return channel


def _mark_read(db: Session, channel_id: uuid.UUID, operator_id: uuid.UUID) -> datetime:
    now = _now()
    row = (
        db.query(ChatRead)
        .filter(ChatRead.channel_id == channel_id, ChatRead.operator_id == operator_id)
        .first()
    )
    if row:
        row.last_read_at = now
    else:
        db.add(ChatRead(channel_id=channel_id, operator_id=operator_id, last_read_at=now))
    return now


# ── Channels ──────────────────────────────────────────────────────────────────

@router.get("/channels", response_model=list[ChatChannelOut])
def list_channels(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Channels this operator can see, each with an unread count. Cheap enough
    to poll on a short interval."""
    channels = _accessible(db, current)
    if not channels:
        return []

    ids = [c.id for c in channels]
    reads = {
        r.channel_id: r.last_read_at
        for r in db.query(ChatRead).filter(
            ChatRead.operator_id == current.id, ChatRead.channel_id.in_(ids)
        ).all()
    }

    counts = dict(
        db.query(ChatMessage.channel_id, func.count(ChatMessage.id))
        .filter(ChatMessage.channel_id.in_(ids))
        .group_by(ChatMessage.channel_id)
        .all()
    )
    unread_counts = {}
    for cid in ids:
        last_read = reads.get(cid)
        if last_read is None:
            unread_counts[cid] = counts.get(cid, 0)
        else:
            unread_counts[cid] = (
                db.query(func.count(ChatMessage.id))
                .filter(ChatMessage.channel_id == cid, ChatMessage.created_at > last_read)
                .scalar() or 0
            )

    latest = dict(
        db.query(ChatMessage.channel_id, func.max(ChatMessage.created_at))
        .filter(ChatMessage.channel_id.in_(ids))
        .group_by(ChatMessage.channel_id)
        .all()
    )

    return [
        ChatChannelOut(
            id=c.id, slug=c.slug, name=c.name,
            channel_type=c.channel_type,
            site_slug=c.site.slug if c.site else None,
            unread_count=unread_counts.get(c.id, 0),
            last_message_at=latest.get(c.id),
        )
        for c in channels
    ]


# ── Messages ──────────────────────────────────────────────────────────────────

@router.get("/channels/{slug}/messages", response_model=list[ChatMessageOut])
def list_messages(
    slug: str,
    before: Optional[datetime] = Query(None, description="Return messages older than this timestamp"),
    limit: int = Query(50, ge=1, le=200),
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Newest first, cursor-paginated backwards through history."""
    channel = _channel_or_403(db, current, slug)

    q = (
        db.query(ChatMessage)
        .options(joinedload(ChatMessage.operator))
        .filter(ChatMessage.channel_id == channel.id)
    )
    if before is not None:
        q = q.filter(ChatMessage.created_at < before)

    rows = q.order_by(ChatMessage.created_at.desc()).limit(limit).all()
    return [
        ChatMessageOut(
            id=m.id, channel_id=m.channel_id, operator_id=m.operator_id,
            operator_name=m.operator.full_name if m.operator else "Unknown",
            body=m.body, created_at=m.created_at,
        )
        for m in rows
    ]


@router.post("/channels/{slug}/messages", response_model=ChatMessageOut, status_code=201)
def send_message(
    slug: str,
    body: ChatMessageCreate,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    channel = _channel_or_403(db, current, slug)

    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(text) > MAX_BODY_CHARS:
        raise HTTPException(status_code=400,
                            detail=f"Message is too long (max {MAX_BODY_CHARS} characters)")

    msg = ChatMessage(
        channel_id=channel.id, operator_id=current.id, body=text, created_at=_now(),
    )
    db.add(msg)
    # Sending implies having seen everything before it, so your own message
    # never shows up as unread to you.
    _mark_read(db, channel.id, current.id)
    db.commit()
    db.refresh(msg)

    return ChatMessageOut(
        id=msg.id, channel_id=msg.channel_id, operator_id=msg.operator_id,
        operator_name=current.full_name, body=msg.body, created_at=msg.created_at,
    )


@router.post("/channels/{slug}/read", response_model=ChatReadResult)
def mark_read(
    slug: str,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    channel = _channel_or_403(db, current, slug)
    now = _mark_read(db, channel.id, current.id)
    db.commit()
    return ChatReadResult(channel_id=channel.id, last_read_at=now)
