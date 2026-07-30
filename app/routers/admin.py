import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..deps import require_admin
from ..models import Operator, OperatorRoleAssignment, Role, Site, SiteAccess
from ..schemas import (AssignRoleRequest, GrantSiteRequest, OperatorWithRoles,
                       OperatorRoleOut, RoleOut, SiteAccessOut)

router = APIRouter(prefix="/admin", tags=["admin"])

SITE_LEAD_SLUG = {
    "shelter_site_lead": "shelter",
    "club101_site_lead": "club101",
    "starhall_site_lead": "starhall",
}


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
    if body.role_name in SITE_LEAD_SLUG:
        slug = SITE_LEAD_SLUG[body.role_name]
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
