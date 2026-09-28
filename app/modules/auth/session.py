"""Session cookies: the access token, the refresh token, and their rotation.

A session is two HttpOnly cookies:

``pas_access_token``
    The short-lived Supabase-signed **JWT** that everything else authenticates
    with. Sent with every request, so it is kept small and short-lived.

``pas_refresh_token``
    The long-lived **refresh token**. Only ever sent to ``POST /auth/refresh``
    (and the transparent refresh path below); exchanging it mints a new access
    token. Supabase rotates it on every exchange, so a stolen copy stops working
    the moment the real client refreshes.

Both cookies are HttpOnly (scripts cannot read them), SameSite=Lax and Secure —
the browser is the only place the tokens live, so XSS cannot exfiltrate them.
"""

from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status

from app.core.clients.supabase import get_supabase_client
from app.core.security.jwt import expires_at as jwt_expires_at
from app.core.security.jwt import is_expired
from app.modules.auth.service import (
    AuthSession,
    InvalidCredentialsError,
    get_user_from_token,
)
from app.modules.auth.service import (
    refresh_session as exchange_refresh_token,
)

ACCESS_TOKEN_COOKIE = "pas_access_token"
REFRESH_TOKEN_COOKIE = "pas_refresh_token"

# The access cookie matches Supabase's default JWT lifetime (1 hour); the
# refresh cookie outlives it by a month. Max-age is only a browser hint — the
# real expiry is inside the tokens, which Supabase enforces.
ACCESS_TOKEN_MAX_AGE = 60 * 60
REFRESH_TOKEN_MAX_AGE = 60 * 60 * 24 * 30


@dataclass(frozen=True)
class Session:
    """The caller's resolved identity and tokens for one request."""

    user_id: str
    email: str
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None


def _unauthorized(detail: str = "Not signed in") -> HTTPException:
    """A 401 with the standard shape used across the app."""
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def set_session_cookies(response: Response, session: AuthSession) -> None:
    """Write both session cookies (the refresh cookie only when there is one)."""
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        session.access_token,
        max_age=ACCESS_TOKEN_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    if session.refresh_token:
        response.set_cookie(
            REFRESH_TOKEN_COOKIE,
            session.refresh_token,
            max_age=REFRESH_TOKEN_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=True,
        )


def clear_session_cookies(response: Response) -> None:
    """Remove both session cookies."""
    response.delete_cookie(ACCESS_TOKEN_COOKIE)
    response.delete_cookie(REFRESH_TOKEN_COOKIE)


def rotate_session(refresh_token: str, response: Response) -> Session:
    """Trade a refresh token for a new token pair and store it in cookies.

    Raises InvalidCredentialsError when the refresh token is no longer usable;
    the resulting 401 makes the middleware drop the dead cookies.
    """
    renewed = exchange_refresh_token(refresh_token)
    set_session_cookies(response, renewed)
    return Session(
        user_id=renewed.user_id,
        email=renewed.email,
        access_token=renewed.access_token,
        refresh_token=renewed.refresh_token,
        expires_at=renewed.expires_at,
    )


def resolve_session(request: Request, response: Response) -> Session:
    """Resolve the caller from their cookies, refreshing the JWT when needed.

    Order of operations:

    1. A valid, unexpired access token is used as-is.
    2. A missing or expired access token is exchanged for a new pair using the
       refresh cookie, and the fresh cookies are written onto this response.
    3. Anything else is a 401. The middleware expires both session cookies on a
       401, so a dead refresh token is never replayed request after request.

    Raises HTTPException(401) when there is no usable session.
    """
    access_token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)

    # Read the JWT locally first: an expired token is not worth a round-trip.
    if access_token and not is_expired(access_token):
        try:
            user = get_user_from_token(access_token, get_supabase_client())
        except InvalidCredentialsError as exc:
            # Locally unexpired but rejected upstream (revoked, bad signature,
            # clock skew). Only the refresh token can fix that.
            if not refresh_token:
                raise _unauthorized("Invalid or expired session") from exc
            user = None
        if user is not None:
            return Session(
                user_id=user["id"],
                email=user["email"],
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=jwt_expires_at(access_token),
            )

    if refresh_token:
        try:
            return rotate_session(refresh_token, response)
        except InvalidCredentialsError as exc:
            raise _unauthorized(str(exc)) from exc

    raise _unauthorized()
