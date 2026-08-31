import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session
from .auth import decode_token
from .database import get_db
from .models import (Operator, OperatorRole, OperatorRoleAssignment, Role,
                     Site, SiteFeature, SiteFeatureKey)

bearer = HTTPBearer()


def get_current_operator(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Operator:
    token = credentials.credentials
    try:
        operator_id = decode_token(token, "access")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    op = db.get(Operator, uuid.UUID(operator_id))
    if not op or not op.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Operator not found or inactive")
    return op


def require_director(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
) -> Operator:
    if current.role in (OperatorRole.director, OperatorRole.admin):
        return current
    has_director_or_admin = (
        db.query(OperatorRoleAssignment)
        .join(Role)
        .filter(OperatorRoleAssignment.operator_id == current.id,
                Role.name.in_(["admin", "director"]))
        .first()
    )
    if not has_director_or_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Director role required")
    return current


def require_valor_director(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
) -> Operator:
    """Valor Collective planning is gated separately from the general
    director role — a site Director is not automatically a Valor Director."""
    if current.role == OperatorRole.admin:
        return current
    has_access = (
        db.query(OperatorRoleAssignment)
        .join(Role)
        .filter(OperatorRoleAssignment.operator_id == current.id,
                Role.name.in_(["admin", "valor_director"]))
        .first()
    )
    if not has_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Valor Director role required")
    return current


def require_admin(
    current: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
) -> Operator:
    has_admin = (
        db.query(OperatorRoleAssignment)
        .join(Role)
        .filter(OperatorRoleAssignment.operator_id == current.id, Role.name == "admin")
        .first()
    )
    if not has_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current


def site_feature_enabled(db: Session, site_id, feature_key: SiteFeatureKey) -> bool:
    """Absence of a row means enabled — every site should get a full
    row-set at creation time, but this fails open rather than locking a
    site out of everything if a row is ever missing."""
    row = (
        db.query(SiteFeature)
        .filter(SiteFeature.site_id == site_id, SiteFeature.feature_key == feature_key)
        .first()
    )
    return row is None or row.enabled


def require_site_feature(db: Session, site: Site, feature_key: SiteFeatureKey) -> None:
    """Raises 403 if this site has the feature explicitly disabled."""
    if not site_feature_enabled(db, site.id, feature_key):
        raise HTTPException(status_code=403,
                           detail=f"{feature_key.value} is not enabled for {site.name}")
