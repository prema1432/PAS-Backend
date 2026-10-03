"""
JWT helpers + FastAPI dependency for protected routes.
"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

_bearer = HTTPBearer()


def create_jwt(phone_number: str, session_id: str) -> str:
    """Create a signed JWT for the given customer."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": phone_number,
        "sid": session_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and verify a JWT; raises jose.JWTError on failure."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


def get_current_customer(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """
    FastAPI dependency — extracts and validates the Bearer token.
    Returns the decoded JWT payload dict on success.
    Raises HTTP 401 on missing/invalid/expired token.
    """
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_jwt(credentials.credentials)
        if not payload.get("sub"):
            raise exc
        return payload
    except JWTError:
        raise exc
