import secrets
from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from ..auth import (create_access_token, create_device_refresh_token,
                    create_refresh_token, decode_refresh_token, decode_token,
                    hash_password, verify_password)
from ..config import settings
from ..database import get_db
from ..deps import get_current_operator
from ..identity import find_usable_code
from ..models import (OnboardingStatus, Operator, OperatorRole,
                      OperatorRoleAssignment, Role, Site, SiteAccess)
from ..schemas import (LoginRequest, OperatorOut, RefreshRequest,
                       SetPasswordRequest, SignupRequest, TokenResponse,
                       ValidateCodeRequest, ValidateCodeResult)

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
    if not body.remember_device:
        # Not a trusted device: no refresh token at all, so the session simply
        # ends when the access token does.
        return TokenResponse(access_token=create_access_token(sid))

    trust_until = datetime.now(timezone.utc) + timedelta(days=settings.DEVICE_TRUST_DAYS)
    return TokenResponse(
        access_token=create_access_token(sid),
        refresh_token=create_device_refresh_token(sid, trust_until),
        device_trust_expires_at=trust_until,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    """Mint a fresh access token for a remembered device.

    The device's original expiry rides along unchanged — refreshing extends the
    session, never the trust window.
    """
    try:
        operator_id, device_expiry = decode_refresh_token(body.refresh_token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Sign in again on this device")

    import uuid
    op = db.get(Operator, uuid.UUID(operator_id))
    if not op or not op.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Operator not found")

    sid = str(op.id)
    if device_expiry is None:
        return TokenResponse(access_token=create_access_token(sid),
                             refresh_token=create_refresh_token(sid))

    return TokenResponse(
        access_token=create_access_token(sid),
        refresh_token=create_device_refresh_token(sid, device_expiry),
        device_trust_expires_at=device_expiry,
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


# ── Signup ────────────────────────────────────────────────────────────────────

GENERIC_CODE_ERROR = "That invite code is not valid. Ask your director for a new one."


@router.post("/signup/validate-code", response_model=ValidateCodeResult)
def validate_invite_code(body: ValidateCodeRequest, db: Session = Depends(get_db)):
    """Checked before the form is shown. Returns only true/false — never why,
    so a stranger cannot map which codes exist."""
    return ValidateCodeResult(valid=find_usable_code(db, body.code) is not None)


@router.post("/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    """Create an account against an invite code. There is no path to an
    account without one."""
    code = find_usable_code(db, body.code)
    if not code:
        raise HTTPException(status_code=400, detail=GENERIC_CODE_ERROR)

    first_name = body.first_name.strip()
    last_name = body.last_name.strip()
    phone = body.phone_number.strip()
    if not first_name or not last_name:
        raise HTTPException(status_code=400, detail="First and last name are both required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number is required")

    if db.query(Operator).filter(Operator.phone_number == phone).first():
        raise HTTPException(status_code=409, detail="That phone number is already registered")

    op = Operator(
        first_name=first_name,
        last_name=last_name,
        phone_number=phone,
        password_hash=hash_password(body.password),
        role=OperatorRole.operator,
        active=True,
        onboarding_status=OnboardingStatus.profile_pending,
        invited_by=code.created_by,
    )
    db.add(op)
    db.flush()

    # Apply whatever the code pre-assigned, so a new hire arrives configured.
    if code.intended_role:
        role = db.query(Role).filter(Role.name == code.intended_role).first()
        if role:
            db.add(OperatorRoleAssignment(operator_id=op.id, role_id=role.id))
    for slug in (code.intended_site_access or []):
        site = db.query(Site).filter(Site.slug == slug).first()
        if site:
            db.add(SiteAccess(operator_id=op.id, site_id=site.id))

    code.use_count += 1
    db.commit()
    db.refresh(op)

    sid = str(op.id)
    return TokenResponse(
        access_token=create_access_token(sid),
        refresh_token=create_refresh_token(sid),
    )
