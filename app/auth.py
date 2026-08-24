from datetime import datetime, timedelta, timezone
from typing import Literal
import bcrypt
from jose import JWTError, jwt
from .config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _make_token(sub: str, kind: Literal["access", "refresh"], expires_delta: timedelta) -> str:
    payload = {
        "sub": sub,
        "kind": kind,
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(operator_id: str) -> str:
    return _make_token(operator_id, "access",
                       timedelta(hours=settings.ACCESS_TOKEN_EXPIRE_HOURS))


def create_refresh_token(operator_id: str) -> str:
    return _make_token(operator_id, "refresh",
                       timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))


def decode_token(token: str, expected_kind: Literal["access", "refresh"]) -> str:
    """Decode and validate a JWT. Returns the operator id (sub) or raises JWTError."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise
    if payload.get("kind") != expected_kind:
        raise JWTError("Wrong token type")
    sub = payload.get("sub")
    if not sub:
        raise JWTError("Missing sub")
    return sub


def create_device_refresh_token(operator_id: str, device_expires_at: datetime) -> str:
    """Refresh token for a remembered device.

    `dev_exp` is the absolute moment the device stops being trusted. It is
    carried forward unchanged on every refresh, so an operator who uses the app
    daily still gets sent back to the login screen on schedule — a sliding
    window would mean "remember this device" quietly never expired.
    """
    return jwt.encode(
        {
            "sub": operator_id,
            "kind": "refresh",
            "exp": device_expires_at,
            "dev_exp": int(device_expires_at.timestamp()),
        },
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_refresh_token(token: str) -> tuple[str, datetime | None]:
    """Returns (operator_id, device_trust_expiry). Raises JWTError if the token
    is invalid or the device's trust window has run out."""
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("kind") != "refresh":
        raise JWTError("Wrong token type")
    sub = payload.get("sub")
    if not sub:
        raise JWTError("Missing sub")

    dev_exp_raw = payload.get("dev_exp")
    if dev_exp_raw is None:
        return sub, None

    dev_exp = datetime.fromtimestamp(int(dev_exp_raw), tz=timezone.utc)
    if datetime.now(timezone.utc) >= dev_exp:
        raise JWTError("Device trust expired")
    return sub, dev_exp
