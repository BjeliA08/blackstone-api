import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.orm import Session
from .auth import decode_token
from .database import get_db
from .models import Operator, OperatorRole

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


def require_director(current: Operator = Depends(get_current_operator)) -> Operator:
    if current.role not in (OperatorRole.director, OperatorRole.admin):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Director role required")
    return current
