"""Shared FastAPI dependencies: docs auth, the caller's session and user.

Every data route depends on `get_current_user` (identity) and/or
`get_current_client` (a Supabase client bound to the caller's JWT, so Postgres
RLS runs as that user). Both build on `get_current_session`, which FastAPI
caches per request — so an expired token is refreshed at most once per request.
"""

import secrets

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.clients.supabase import get_client_for_token
from app.core.config import settings
from app.modules.auth.session import Session, resolve_session

_docs_basic = HTTPBasic(auto_error=False)


def require_docs_auth(
    credentials: HTTPBasicCredentials | None = Depends(_docs_basic),
) -> None:
    """Guard the API docs behind HTTP Basic auth.

    Docs are disabled entirely (404) unless DOCS_USERNAME and DOCS_PASSWORD are
    configured, so the schema is never served anonymously.
    """
    if not settings.docs_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    valid_user = bool(credentials) and secrets.compare_digest(
        credentials.username, settings.docs_username
    )
    valid_password = bool(credentials) and secrets.compare_digest(
        credentials.password, settings.docs_password
    )
    if not (valid_user and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def get_current_session(request: Request, response: Response) -> Session:
    """Resolve the caller's session, transparently refreshing a stale JWT.

    Raises 401 when there is no usable access or refresh token.
    """
    return resolve_session(request, response)


def get_current_user(session: Session = Depends(get_current_session)) -> dict:
    """The signed-in user as ``{"id": ..., "email": ...}``."""
    return {"id": session.user_id, "email": session.email}


def get_current_client(session: Session = Depends(get_current_session)):
    """Supabase client bound to the caller's token (RLS runs as that user)."""
    return get_client_for_token(session.access_token)
