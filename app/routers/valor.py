"""Valor Collective — close protection services division.

Sits alongside site-based operations, not inside them. Division membership
(division_operators) is a separate grant from site_access: no operator is
CP-qualified by default, and being CP-qualified grants nothing at a site.

threat_notes on an operation is Director/Admin only, enforced here by simply
never putting it on the object handed to anyone else — not by hiding it in
the UI.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from ..clock import utc_now_naive
from ..database import get_db
from ..deps import get_current_operator, require_director
from ..identity import role_names
from ..models import (ClientProfile, Division, DivisionOperator, Operation,
                      OperationRole, OperationStatus, Operator)
from ..photos import signed_url
from ..schemas import (ClientProfileOut, DivisionOperatorCreate,
                       DivisionOperatorOut, OperationCreate, OperationOut,
                       OperationPatch, OperationRoleCreate, OperationRoleOut,
                       OperationRolePatch, RosterPackageMember, RosterPackageOut)

router = APIRouter(prefix="/valor", tags=["valor"])

DIVISION_SLUG = "valor-collective"


def _division(db: Session) -> Division:
    d = db.query(Division).filter(Division.slug == DIVISION_SLUG).first()
    if not d:
        raise HTTPException(status_code=404, detail="Valor Collective is not configured")
    return d


def _is_privileged(db: Session, current: Operator) -> bool:
    roles = role_names(db, current)
    return bool(roles & {"admin", "director"})


def is_division_member(db: Session, operator_id: uuid.UUID, division_id: uuid.UUID) -> bool:
    return bool(
        db.query(DivisionOperator)
        .filter(DivisionOperator.operator_id == operator_id,
                DivisionOperator.division_id == division_id,
                DivisionOperator.active.is_(True))
        .first()
    )


def _require_access(db: Session, current: Operator, division: Division) -> bool:
    """Returns whether the caller is privileged (director/admin). Raises if
    they have neither division membership nor privilege."""
    privileged = _is_privileged(db, current)
    if not privileged and not is_division_member(db, current.id, division.id):
        raise HTTPException(status_code=403, detail="Not a member of Valor Collective")
    return privileged


def _build_operation_out(op_row: Operation, include_threat_notes: bool) -> OperationOut:
    return OperationOut(
        id=op_row.id, division_id=op_row.division_id,
        client_name=op_row.client_name, operation_name=op_row.operation_name,
        status=op_row.status, starts_at=op_row.starts_at, ends_at=op_row.ends_at,
        location=op_row.location, brief=op_row.brief,
        threat_notes=op_row.threat_notes if include_threat_notes else None,
        created_by=op_row.created_by, created_at=op_row.created_at,
        roles=[OperationRoleOut(
            id=r.id, operation_id=r.operation_id, role_name=r.role_name,
            operator_id=r.operator_id,
            operator_name=r.operator.full_name if r.operator else None,
            confirmed=r.confirmed,
        ) for r in op_row.roles],
    )


def _build_client_profile_out(cp: ClientProfile, with_photo_url: bool = False) -> ClientProfileOut:
    return ClientProfileOut(
        id=cp.id, operator_id=cp.operator_id,
        operator_name=cp.operator.full_name if cp.operator else None,
        headline=cp.headline, bio=cp.bio,
        skills=cp.skills or [], years_experience=cp.years_experience,
        has_photo=bool(cp.photo_key),
        photo_url=signed_url(cp.photo_key) if (with_photo_url and cp.photo_key) else None,
        visible=cp.visible, updated_at=cp.updated_at,
    )


# ── CP operator ───────────────────────────────────────────────────────────────

@router.get("/operations", response_model=list[OperationOut])
def my_operations(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    division = _division(db)
    privileged = _require_access(db, current, division)

    q = (
        db.query(Operation)
        .options(joinedload(Operation.roles).joinedload(OperationRole.operator))
        .filter(Operation.division_id == division.id)
    )
    if not privileged:
        q = q.join(OperationRole, OperationRole.operation_id == Operation.id).filter(
            OperationRole.operator_id == current.id
        ).distinct()
    rows = q.order_by(Operation.starts_at.desc()).all()
    return [_build_operation_out(o, privileged) for o in rows]


@router.get("/operations/{operation_id}", response_model=OperationOut)
def get_operation(
    operation_id: uuid.UUID,
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    division = _division(db)
    privileged = _require_access(db, current, division)

    op_row = (
        db.query(Operation)
        .options(joinedload(Operation.roles).joinedload(OperationRole.operator))
        .filter(Operation.id == operation_id, Operation.division_id == division.id)
        .first()
    )
    if not op_row:
        raise HTTPException(status_code=404, detail="Operation not found")

    if not privileged and not any(r.operator_id == current.id for r in op_row.roles):
        raise HTTPException(status_code=403, detail="You are not assigned to this operation")

    return _build_operation_out(op_row, privileged)


@router.get("/roster", response_model=list[DivisionOperatorOut])
def cp_roster(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    division = _division(db)
    _require_access(db, current, division)

    rows = (
        db.query(DivisionOperator)
        .options(joinedload(DivisionOperator.operator))
        .filter(DivisionOperator.division_id == division.id, DivisionOperator.active.is_(True))
        .all()
    )
    return [DivisionOperatorOut(
        id=r.id, operator_id=r.operator_id,
        operator_name=r.operator.full_name if r.operator else None,
        division_id=r.division_id, cp_qualifications=r.cp_qualifications,
        active=r.active, added_at=r.added_at,
    ) for r in rows]


# ── Director / Admin ─────────────────────────────────────────────────────────

@router.post("/division-operators", response_model=DivisionOperatorOut, status_code=201)
def add_division_operator(
    body: DivisionOperatorCreate,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    division = _division(db)
    target = db.get(Operator, body.operator_id)
    if not target:
        raise HTTPException(status_code=404, detail="Operator not found")

    existing = (
        db.query(DivisionOperator)
        .filter(DivisionOperator.operator_id == body.operator_id,
                DivisionOperator.division_id == division.id)
        .first()
    )
    if existing:
        if existing.active:
            raise HTTPException(status_code=409, detail=f"{target.full_name} is already on the roster")
        existing.active = True
        existing.cp_qualifications = body.cp_qualifications
        db.commit()
        db.refresh(existing)
        row = existing
    else:
        row = DivisionOperator(
            operator_id=body.operator_id, division_id=division.id,
            cp_qualifications=body.cp_qualifications,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    return DivisionOperatorOut(
        id=row.id, operator_id=row.operator_id, operator_name=target.full_name,
        division_id=row.division_id, cp_qualifications=row.cp_qualifications,
        active=row.active, added_at=row.added_at,
    )


@router.delete("/division-operators/{row_id}", status_code=204)
def remove_division_operator(
    row_id: uuid.UUID,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    """Retired rather than deleted, so operation-role history stays readable."""
    row = db.get(DivisionOperator, row_id)
    if row and row.active:
        row.active = False
        db.commit()


@router.post("/operations", response_model=OperationOut, status_code=201)
def create_operation(
    body: OperationCreate,
    current: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    division = _division(db)
    op_row = Operation(
        division_id=division.id, client_name=body.client_name,
        operation_name=body.operation_name, status=body.status,
        starts_at=body.starts_at, ends_at=body.ends_at,
        location=body.location, brief=body.brief, threat_notes=body.threat_notes,
        created_by=current.id, created_at=utc_now_naive(),
    )
    db.add(op_row)
    db.commit()
    db.refresh(op_row)
    return _build_operation_out(op_row, True)


@router.patch("/operations/{operation_id}", response_model=OperationOut)
def patch_operation(
    operation_id: uuid.UUID,
    body: OperationPatch,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    op_row = db.get(Operation, operation_id)
    if not op_row:
        raise HTTPException(status_code=404, detail="Operation not found")
    for field in ("client_name", "operation_name", "status", "starts_at",
                 "ends_at", "location", "brief", "threat_notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(op_row, field, value)
    db.commit()
    db.refresh(op_row)
    return _build_operation_out(op_row, True)


@router.post("/operations/{operation_id}/roles", response_model=OperationRoleOut, status_code=201)
def add_operation_role(
    operation_id: uuid.UUID,
    body: OperationRoleCreate,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    op_row = db.get(Operation, operation_id)
    if not op_row:
        raise HTTPException(status_code=404, detail="Operation not found")
    role = OperationRole(operation_id=operation_id, role_name=body.role_name)
    db.add(role)
    db.commit()
    db.refresh(role)
    return OperationRoleOut(
        id=role.id, operation_id=role.operation_id, role_name=role.role_name,
        operator_id=None, operator_name=None, confirmed=False,
    )


@router.patch("/operations/{operation_id}/roles/{role_id}", response_model=OperationRoleOut)
def patch_operation_role(
    operation_id: uuid.UUID,
    role_id: uuid.UUID,
    body: OperationRolePatch,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    division = _division(db)
    role = (
        db.query(OperationRole)
        .filter(OperationRole.id == role_id, OperationRole.operation_id == operation_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if body.unassign:
        role.operator_id = None
        role.confirmed = False
    elif body.operator_id is not None:
        if not is_division_member(db, body.operator_id, division.id):
            raise HTTPException(status_code=400,
                               detail="That operator is not on the Valor Collective roster")
        role.operator_id = body.operator_id
    if body.confirmed is not None:
        role.confirmed = body.confirmed

    db.commit()
    db.refresh(role)
    return OperationRoleOut(
        id=role.id, operation_id=role.operation_id, role_name=role.role_name,
        operator_id=role.operator_id,
        operator_name=role.operator.full_name if role.operator else None,
        confirmed=role.confirmed,
    )


@router.get("/client-profiles", response_model=list[ClientProfileOut])
def list_client_profiles(
    visible: Optional[bool] = Query(None),
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    q = db.query(ClientProfile).options(joinedload(ClientProfile.operator))
    if visible is not None:
        q = q.filter(ClientProfile.visible == visible)
    return [_build_client_profile_out(cp, with_photo_url=True) for cp in q.all()]


@router.get("/operations/{operation_id}/roster-package", response_model=RosterPackageOut)
def roster_package(
    operation_id: uuid.UUID,
    _: Operator = Depends(require_director),
    db: Session = Depends(get_db),
):
    """The confirmed team with their published client profiles attached —
    structured for handing to a client. Anyone not confirmed, or without a
    published profile, is left out rather than shown half-finished."""
    op_row = (
        db.query(Operation)
        .options(joinedload(Operation.roles).joinedload(OperationRole.operator))
        .filter(Operation.id == operation_id)
        .first()
    )
    if not op_row:
        raise HTTPException(status_code=404, detail="Operation not found")

    members: list[RosterPackageMember] = []
    for r in op_row.roles:
        if not r.confirmed or not r.operator_id:
            continue
        cp = db.query(ClientProfile).filter(
            ClientProfile.operator_id == r.operator_id, ClientProfile.visible.is_(True)
        ).first()
        if not cp:
            continue
        members.append(RosterPackageMember(
            role_name=r.role_name, operator_id=r.operator_id,
            headline=cp.headline, bio=cp.bio, skills=cp.skills or [],
            years_experience=cp.years_experience, has_photo=bool(cp.photo_key),
            photo_url=signed_url(cp.photo_key) if cp.photo_key else None,
        ))

    return RosterPackageOut(
        operation_id=op_row.id, client_name=op_row.client_name,
        operation_name=op_row.operation_name, starts_at=op_row.starts_at,
        ends_at=op_row.ends_at, location=op_row.location, members=members,
    )
