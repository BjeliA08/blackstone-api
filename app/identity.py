"""Operator identity: licence status, and who is allowed to see what.

Licence numbers and expiry dates are personal compliance data. They are
excluded from the serialized payload entirely when the requester does not
qualify — never sent to the client and hidden in the UI, which would leave
them in the response body for anyone reading the network tab.
"""
import secrets
import string
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .clock import local_today

from .models import (InviteCode, LicenceStatus, Operator, OperatorRole,
                     OperatorRoleAssignment, Role)

EXPIRING_SOON_DAYS = 30

# No ambiguous characters — these get read aloud and typed by hand.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def licence_status(op: Operator, today: Optional[date] = None) -> LicenceStatus:
    """What a Director scanning the roster needs to know at a glance."""
    if not op.security_licence_expiry:
        return LicenceStatus.missing
    today = today or local_today()
    days_left = (op.security_licence_expiry - today).days
    if days_left < 0:
        return LicenceStatus.expired
    if days_left <= EXPIRING_SOON_DAYS:
        return LicenceStatus.expiring_soon
    return LicenceStatus.valid


def role_names(db: Session, op: Operator) -> set[str]:
    """Roles from the assignment table plus the legacy enum column — an
    operator can hold director/admin through either."""
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


def can_view_licence(viewer_roles: set[str], viewer_id: uuid.UUID,
                     target_id: uuid.UUID) -> bool:
    """Admin, Directors, and the operator looking at their own profile."""
    if viewer_id == target_id:
        return True
    return bool(viewer_roles & {"admin", "director"})


# Photos follow the same rule, with one addition: a deactivated operator's
# photo stays visible to Admin and Directors only, never to peers.
def can_view_photo(viewer_roles: set[str], viewer_id: uuid.UUID,
                   target: Operator) -> bool:
    if viewer_id == target.id:
        return True
    if not target.active:
        # A departed operator's face is no longer anyone's business but
        # Admin's and the Directors'.
        return bool(viewer_roles & {"admin", "director"})
    # Active operators' photos exist so colleagues can identify them on site.
    return True


def generate_code(db: Session) -> str:
    """Short, human-readable, and unique."""
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if not db.query(InviteCode).filter(InviteCode.code == code).first():
            return code
    raise RuntimeError("Could not allocate an unused invite code")


def find_usable_code(db: Session, code: str) -> Optional[InviteCode]:
    """Returns the code only if it is genuinely usable right now.

    Callers must give the same generic failure for every reason — telling a
    stranger whether a code exists, is expired, or is used up is free
    reconnaissance.
    """
    row = db.query(InviteCode).filter(InviteCode.code == code.strip().upper()).first()
    if not row:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return row if row.usable_at(now) else None
