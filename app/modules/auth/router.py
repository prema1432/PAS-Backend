"""Authentication routes: sign-up, sign-in, refresh, sign-out, current user, activity.

The endpoints here are the only ones an anonymous caller may reach (plus the
landing page, ``/info`` and ``/health``). Everything about tokens — issuing,
rotating, clearing — lives in ``session.py``.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.clients.supabase import (
    SupabaseNotConfiguredError,
    get_client_for_token,
    get_supabase_client,
)
from app.core.dependencies import get_current_client, get_current_user
from app.core.security.jwt import expires_at
from app.modules.auth.login_events import record_login
from app.modules.auth.schemas import AuthRequest, UserOut
from app.modules.auth.service import (
    InvalidCredentialsError,
    sign_in,
    sign_up,
)
from app.modules.auth.session import (
    ACCESS_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
    clear_session_cookies,
    rotate_session,
    set_session_cookies,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_payload(session) -> UserOut:
    """Shape a resolved session as the public user model."""
    return UserOut(
        id=session.user_id,
        email=session.email,
        access_token_expires_at=session.expires_at,
    )


@router.post("/signup", response_model=UserOut, status_code=201)
def signup(payload: AuthRequest, request: Request, response: Response) -> UserOut:
    """Create an account and start a session (sets the access + refresh cookies)."""
    try:
        user_id = sign_up(payload.email, payload.password, get_supabase_client())
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Sign in right away so the user leaves with a valid session.
    try:
        session = sign_in(payload.email, payload.password, get_supabase_client())
    except InvalidCredentialsError as exc:
        # Email confirmation may be required in project settings; the account
        # exists but cannot start a session yet.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account created. Confirm your email before signing in.",
        ) from exc

    record_login(request, user_id, get_client_for_token(session.access_token))
    set_session_cookies(response, session)
    return UserOut(
        id=user_id,
        email=payload.email,
        access_token_expires_at=session.expires_at,
    )


@router.post("/login", response_model=UserOut)
def login(payload: AuthRequest, request: Request, response: Response) -> UserOut:
    """Validate email/password and start a session (sets both cookies)."""
    try:
        session = sign_in(payload.email, payload.password, get_supabase_client())
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    record_login(request, session.user_id, get_client_for_token(session.access_token))
    set_session_cookies(response, session)
    return _user_payload(session)


@router.post("/refresh", response_model=UserOut)
def refresh(request: Request, response: Response) -> UserOut:
    """Exchange the refresh token for a fresh access token (rotates both cookies).

    Called by the dashboard before the JWT lapses, and by any 401 retry. When
    the refresh token is gone, revoked or expired the caller gets 401 (and the
    middleware drops the dead cookies) — they have to sign in again.
    """
    refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Not signed in")

    try:
        session = rotate_session(refresh_token, response)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except SupabaseNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _user_payload(session)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    """End the session: revoke it at Supabase and clear both cookies.

    Revocation needs the *access* token (Supabase's logout endpoint cancels the
    user's refresh tokens for the given scope), so a refresh-token cookie copied
    off the wire cannot be replayed once the user has signed out.
    """
    access_token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if access_token:
        try:
            get_supabase_client().auth.admin.sign_out(access_token, "global")
        except Exception:  # never block sign-out on a revocation failure
            logger.warning("Could not revoke session at Supabase", exc_info=True)
    clear_session_cookies(response)


@router.get("/me", response_model=UserOut)
def me(request: Request, user: dict = Depends(get_current_user)) -> UserOut:
    """Return the currently signed-in user and when their access token lapses."""
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    return UserOut(
        id=user["id"],
        email=user["email"],
        access_token_expires_at=expires_at(token),
    )


@router.get("/activity")
def activity(user: dict = Depends(get_current_user), client=Depends(get_current_client)) -> list:
    """List the caller's recent sign-ins with device and location details."""
    response = (
        client.table("login_events")
        .select("*")
        .eq("user_id", user["id"])
        .order("id", desc=True)
        .limit(25)
        .execute()
    )
    return response.data
