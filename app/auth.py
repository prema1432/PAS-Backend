"""
JWT helpers.
"""

from datetime import datetime, timedelta, timezone

from jose import jwt

from app.config import settings


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
