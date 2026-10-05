"""
JWT helpers + password hashing + FastAPI dependencies for protected routes.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Crypto-hash a plaintext password using bcrypt with random salt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt crypto hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def create_customer_tokens(phone_number: str, session_id: str) -> dict:
    """
    Create both an Access Token (15 mins) and a Refresh Token (30 days) for a customer.
    """
    now = datetime.now(tz=timezone.utc)
    access_exp = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_exp = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    access_payload = {
        "sub": phone_number,
        "sid": session_id,
        "type": "access",
        "iat": now,
        "exp": access_exp,
    }
    refresh_payload = {
        "sub": phone_number,
        "sid": session_id,
        "type": "refresh",
        "iat": now,
        "exp": refresh_exp,
    }
    access_token = jwt.encode(access_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": int(settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60),
    }


def create_jwt(phone_number: str, session_id: str) -> str:
    """Create a signed 15-minute Access JWT for the given customer."""
    tokens = create_customer_tokens(phone_number, session_id)
    return tokens["access_token"]


def create_admin_jwt(email: str, role: str = "admin", name: str = "") -> str:
    """Create a signed JWT for an authenticated admin."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": email.lower().strip(),
        "role": role,
        "name": name,
        "type": "admin",
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and verify a JWT; raises jose.JWTError on failure."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


async def get_current_customer(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """
    FastAPI dependency — extracts and validates customer Bearer token and
    verifies that the session matches the single active device in MongoDB.
    """
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not credentials:
        raise exc
    try:
        payload = decode_jwt(credentials.credentials)
        phone = payload.get("sub")
        token_type = payload.get("type")
        if not phone or token_type not in ("access", "customer"):
            raise exc

        from app.database import get_db
        try:
            db = get_db()
            cust = await db["customers"].find_one({"phone_number": phone})
            if cust:
                active_sid = cust.get("login_session_id")
                token_sid = payload.get("sid")
                if not active_sid or active_sid != token_sid:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Session invalidated. Your account was logged in from another device or forced logout.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                # Check if balance is exhausted
                remaining_sec = cust.get("time_remaining_seconds", 0)
                if remaining_sec <= 0:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="No time remaining on this account (0 mins). Please recharge your subscription to continue.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

                # Check if subscription period expired
                expiry = cust.get("time_expiry")
                if expiry:
                    now = datetime.now(tz=timezone.utc)
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    if now > expiry:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Your subscription has expired. Please recharge your account.",
                            headers={"WWW-Authenticate": "Bearer"},
                        )
        except HTTPException:
            raise
        except Exception:
            pass

        return payload
    except JWTError:
        raise exc


def get_current_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """
    FastAPI dependency — extracts and validates the admin Bearer token.
    Returns the decoded JWT payload dict on success.
    Raises HTTP 401 on missing/invalid/expired token.
    """
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired admin token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not credentials:
        raise exc
    try:
        payload = decode_jwt(credentials.credentials)
        if not payload.get("sub") or payload.get("type") != "admin":
            raise exc
        return payload
    except JWTError:
        raise exc
