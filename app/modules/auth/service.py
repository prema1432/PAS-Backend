"""Supabase Auth operations: sign-up, sign-in, token refresh, token -> user.

Passwords are never stored or logged by this app — Supabase hashes them
(bcrypt). What we keep is the pair of tokens Supabase returns: a short-lived
JWT access token and a long-lived refresh token (see `session.py` for how they
are stored in cookies).
"""

from dataclasses import dataclass

from app.core.clients.supabase import get_supabase_client
from app.core.security.jwt import expires_at as jwt_expires_at
from supabase import Client


class InvalidCredentialsError(Exception):
    """Credentials rejected, or a token/refresh token that is no longer valid."""


@dataclass(frozen=True)
class AuthSession:
    """What a successful authentication hands back."""

    user_id: str
    email: str
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None


def _to_session(response) -> AuthSession:
    """Map a Supabase auth response onto :class:`AuthSession`."""
    session = response.session
    user = response.user
    if not session or not user:
        raise InvalidCredentialsError("Invalid email or password")
    access_token = session.access_token
    expires_at = getattr(session, "expires_at", None)
    if expires_at is None:
        expires_at = jwt_expires_at(access_token)
    return AuthSession(
        user_id=user.id,
        email=user.email or "",
        access_token=access_token,
        refresh_token=session.refresh_token,
        expires_at=expires_at,
    )


def sign_in(email: str, password: str, client: Client) -> AuthSession:
    """Validate credentials and return the caller's token pair.

    Raises InvalidCredentialsError on wrong email/password.
    """
    try:
        response = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:  # AuthApiError etc. from Supabase
        raise InvalidCredentialsError(str(exc)) from exc
    return _to_session(response)


def refresh_session(refresh_token: str, client: Client | None = None) -> AuthSession:
    """Exchange a refresh token for a brand-new token pair.

    Supabase rotates refresh tokens: the one passed in is invalidated, so the
    new pair must be stored. Raises InvalidCredentialsError when the token has
    been used already, revoked, or has expired.
    """
    client = client or get_supabase_client()
    try:
        response = client.auth.refresh_session(refresh_token)
    except Exception as exc:  # expired / revoked / reused token
        raise InvalidCredentialsError("Session expired, please sign in again") from exc
    return _to_session(response)


def get_user_from_token(access_token: str, client: Client) -> dict:
    """Validate an access token with Supabase and return {id, email}.

    Raises InvalidCredentialsError if the token is missing/expired/invalid.
    """
    try:
        user = client.auth.get_user(access_token).user
    except Exception as exc:  # supabase raises various httpx/auth errors
        raise InvalidCredentialsError("Invalid or expired session") from exc
    if not user:
        raise InvalidCredentialsError("Invalid or expired session")
    return {"id": user.id, "email": user.email or ""}
