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
