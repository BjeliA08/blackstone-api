import secrets
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy.orm import Session
from ..auth import (create_access_token, create_refresh_token,
                    decode_token, hash_password, verify_password)
from ..database import get_db
from ..deps import get_current_operator
from ..models import Operator
from ..schemas import (LoginRequest, OperatorOut, RefreshRequest,
                       SetPasswordRequest, TokenResponse)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    op: Operator | None = db.query(Operator).filter(
        Operator.phone_number == body.phone_number
    ).first()

    if not op or not op.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not op.password_hash or not verify_password(body.password, op.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    sid = str(op.id)
    return TokenResponse(
        access_token=create_access_token(sid),
        refresh_token=create_refresh_token(sid),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    try:
        operator_id = decode_token(body.refresh_token, "refresh")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    import uuid
    op = db.get(Operator, uuid.UUID(operator_id))
    if not op or not op.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Operator not found")

    sid = str(op.id)
    return TokenResponse(
        access_token=create_access_token(sid),
        refresh_token=create_refresh_token(sid),
    )


@router.get("/me", response_model=OperatorOut)
def me(current: Operator = Depends(get_current_operator), db: Session = Depends(get_db)):
    from ..models import OperatorRoleAssignment, Role
    has_admin = (
        db.query(OperatorRoleAssignment)
        .join(Role)
        .filter(OperatorRoleAssignment.operator_id == current.id, Role.name == "admin")
        .first()
    )
    result = OperatorOut.model_validate(current)
    result.is_admin = bool(has_admin)
    return result


@router.post("/set-password", status_code=status.HTTP_204_NO_CONTENT)
def set_password(body: SetPasswordRequest, db: Session = Depends(get_db)):
    """
    First-time password setup. Requires the temporary setup_code delivered via Discord DM.
    Clears the code after use so it cannot be replayed.
    """
    op: Operator | None = db.query(Operator).filter(
        Operator.phone_number == body.phone_number
    ).first()

    if not op or not op.setup_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No pending setup for this phone number")

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(op.setup_code, body.setup_code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid setup code")

    op.password_hash = hash_password(body.new_password)
    op.setup_code = None
    db.commit()
