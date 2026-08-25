import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session
from .auth import decode_token
from .database import get_db
from .models import Operator, OperatorRole, OperatorRoleAssignment, Role

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
