import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..deps import require_admin, require_director
from ..identity import generate_code
from ..routers.me import serialize_operator
from ..models import (InviteCode, Operator, OperatorRoleAssignment, Role,
                      Site, SiteAccess)
from ..schemas import (AssignRoleRequest, GrantSiteRequest, InviteCodeCreate,
                       InviteCodeOut, OperatorOut, OperatorWithRoles,
                       OperatorRoleOut, RatePatch, RoleOut, SiteAccessOut,
                       SiteOut)

router = APIRouter(prefix="/admin", tags=["admin"])

SITE_LEAD_SUFFIX = "_site_lead"


def site_lead_slug(role_name: str) -> str | None:
    """"{slug}_site_lead" is the convention for a site's lead role — derived
    from the name so a new site (via Site Builder) gets this for free,
    rather than needing an entry added to a fixed dict."""
    if role_name.endswith(SITE_LEAD_SUFFIX):
        return role_name[: -len(SITE_LEAD_SUFFIX)]
    return None


def _build_operator_with_roles(op: Operator) -> OperatorWithRoles:
    roles = [
        OperatorRoleOut(
            id=r.id,
            role_id=r.role_id,
            role_name=r.role.name if r.role else None,
            assigned_at=r.assigned_at,
        )
        for r in op.operator_roles
    ]
    accesses = [
        SiteAccessOut(
            id=a.id,
            site_id=a.site_id,
            site_name=a.site.name if a.site else None,
            granted_at=a.granted_at,
        )
        for a in op.site_accesses
    ]
    return OperatorWithRoles(
        id=op.id,
        full_name=op.full_name,
        phone_number=op.phone_number,
        discord_id=op.discord_id,
        active=op.active,
        roles=roles,
        site_accesses=accesses,
        pay_rate=op.pay_rate,
    )


def _load_operator(db: Session, operator_id: uuid.UUID) -> Operator:
    op = (
        db.query(Operator)
        .options(
            joinedload(Operator.operator_roles).joinedload(OperatorRoleAssignment.role),
            joinedload(Operator.site_accesses).joinedload(SiteAccess.site),
        )
        .filter(Operator.id == operator_id)
        .first()
    )
    if not op:
        raise HTTPException(status_code=404, detail="Operator not found")
    return op


# ── Roles list ────────────────────────────────────────────────────────────────

@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    _: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return db.query(Role).order_by(Role.name).all()


# ── Operator listing ──────────────────────────────────────────────────────────

@router.get("/operators", response_model=list[OperatorWithRoles])
def list_operators_with_roles(
    _: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ops = (
        db.query(Operator)
        .options(
            joinedload(Operator.operator_roles).joinedload(OperatorRoleAssignment.role),
            joinedload(Operator.site_accesses).joinedload(SiteAccess.site),
        )
        .order_by(Operator.full_name)
        .all()
    )
    return [_build_operator_with_roles(op) for op in ops]


@router.get("/operators/{operator_id}/roles", response_model=OperatorWithRoles)
def get_operator_roles(
    operator_id: uuid.UUID,
    _: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _build_operator_with_roles(_load_operator(db, operator_id))


# ── Assign / remove role ──────────────────────────────────────────────────────

@router.post("/operators/{operator_id}/roles", response_model=OperatorWithRoles)
def assign_role(
    operator_id: uuid.UUID,
    body: AssignRoleRequest,
    admin: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    op = _load_operator(db, operator_id)

    role = db.query(Role).filter(Role.name == body.role_name).first()
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{body.role_name}' not found")

    existing = db.query(OperatorRoleAssignment).filter(
        OperatorRoleAssignment.operator_id == operator_id,
        OperatorRoleAssignment.role_id == role.id,
    ).first()
    if not existing:
        db.add(OperatorRoleAssignment(
            operator_id=operator_id,
            role_id=role.id,
            assigned_at=datetime.now(timezone.utc).replace(tzinfo=None),
            assigned_by=admin.id,
        ))

    # Auto-grant site access for site lead roles
    slug = site_lead_slug(body.role_name)
    if slug:
        site = db.query(Site).filter(Site.slug == slug).first()
        if site:
            existing_access = db.query(SiteAccess).filter(
                SiteAccess.operator_id == operator_id,
                SiteAccess.site_id == site.id,
            ).first()
            if not existing_access:
                db.add(SiteAccess(
                    operator_id=operator_id,
                    site_id=site.id,
                    granted_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    granted_by=admin.id,
                ))

    db.commit()
    return _build_operator_with_roles(_load_operator(db, operator_id))


@router.delete("/operators/{operator_id}/roles/{role_id}", response_model=OperatorWithRoles)
def remove_role(
    operator_id: uuid.UUID,
    role_id: uuid.UUID,
    _: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(OperatorRoleAssignment).filter(
        OperatorRoleAssignment.operator_id == operator_id,
        OperatorRoleAssignment.role_id == role_id,
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return _build_operator_with_roles(_load_operator(db, operator_id))


# ── Grant / revoke site access ────────────────────────────────────────────────

@router.post("/operators/{operator_id}/site-access", response_model=OperatorWithRoles)
def grant_site_access(
    operator_id: uuid.UUID,
    body: GrantSiteRequest,
    admin: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _load_operator(db, operator_id)

    site = db.query(Site).filter(Site.slug == body.site_slug).first()
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{body.site_slug}' not found")

    existing = db.query(SiteAccess).filter(
        SiteAccess.operator_id == operator_id,
        SiteAccess.site_id == site.id,
    ).first()
    if not existing:
        db.add(SiteAccess(
            operator_id=operator_id,
            site_id=site.id,
            granted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            granted_by=admin.id,
        ))
        db.commit()

    return _build_operator_with_roles(_load_operator(db, operator_id))


@router.delete("/operators/{operator_id}/site-access/{site_id}", response_model=OperatorWithRoles)
def revoke_site_access(
    operator_id: uuid.UUID,
    site_id: uuid.UUID,
    _: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.query(SiteAccess).filter(
        SiteAccess.operator_id == operator_id,
        SiteAccess.site_id == site_id,
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return _build_operator_with_roles(_load_operator(db, operator_id))


# ── Invite codes ──────────────────────────────────────────────────────────────

def _code_status(c: InviteCode, now: datetime) -> str:
    if c.revoked:
        return "revoked"
    if c.expires_at is not None and now > c.expires_at:
        return "expired"
    if c.use_count >= c.max_uses:
        return "used_up"
    return "active"


def _invite_out(c: InviteCode, now: datetime) -> InviteCodeOut:
    return InviteCodeOut(
        id=c.id, code=c.code,
        created_by_name=c.creator.full_name if c.creator else None,
        created_at=c.created_at, expires_at=c.expires_at,
        max_uses=c.max_uses, use_count=c.use_count,
        uses_remaining=max(c.max_uses - c.use_count, 0),
        revoked=c.revoked, status=_code_status(c, now),
        intended_role=c.intended_role,
        intended_site_access=c.intended_site_access,
    )


@router.get("/invite-codes", response_model=list[InviteCodeOut])
def list_invite_codes(
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = (
        db.query(InviteCode)
        .options(joinedload(InviteCode.creator))
        .order_by(InviteCode.created_at.desc())
        .all()
    )
    return [_invite_out(c, now) for c in rows]


@router.post("/invite-codes", response_model=InviteCodeOut, status_code=201)
def create_invite_code(
    body: InviteCodeCreate,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if body.max_uses < 1:
        raise HTTPException(status_code=400, detail="A code must allow at least one use")
    if body.intended_role:
        if not db.query(Role).filter(Role.name == body.intended_role).first():
            raise HTTPException(status_code=404, detail=f"Role '{body.intended_role}' not found")
    for slug in (body.intended_site_access or []):
        if not db.query(Site).filter(Site.slug == slug).first():
            raise HTTPException(status_code=404, detail=f"Site '{slug}' not found")

    code = InviteCode(
        code=generate_code(db),
        created_by=current.id,
        created_at=now,
        expires_at=(now + timedelta(days=body.expires_in_days)) if body.expires_in_days else None,
        max_uses=body.max_uses,
        intended_role=body.intended_role,
        intended_site_access=body.intended_site_access or None,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    return _invite_out(code, now)


@router.delete("/invite-codes/{code_id}", status_code=204)
def revoke_invite_code(
    code_id: uuid.UUID,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    """Revoked rather than deleted, so the record of who issued it survives."""
    row = db.get(InviteCode, code_id)
    if row and not row.revoked:
        row.revoked = True
        db.commit()


# ── Rates ─────────────────────────────────────────────────────────────────────
# Admin-only. Changing a rate here never touches already-generated invoice
# line items — those keep the rate that was in effect when they were made.

@router.patch("/operators/{operator_id}/rate", response_model=OperatorOut)
def set_operator_pay_rate(
    operator_id: uuid.UUID,
    body: RatePatch,
    current: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    op = db.get(Operator, operator_id)
    if not op:
        raise HTTPException(status_code=404, detail="Operator not found")
    op.pay_rate = body.rate
    db.commit()
    db.refresh(op)
    return serialize_operator(db, op, current)


@router.patch("/sites/{site_id}/rate", response_model=SiteOut)
def set_site_bill_rate(
    site_id: uuid.UUID,
    body: RatePatch,
    _: Operator = Depends(require_admin),
    db: Session = Depends(get_db),
):
    site = db.get(Site, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    site.bill_rate = body.rate
    db.commit()
    db.refresh(site)
    return site
