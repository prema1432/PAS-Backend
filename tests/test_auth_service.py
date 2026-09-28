"""Unit tests for the Supabase Auth wrapper.

The HTTP tests in `test_auth_tokens.py` cover the session flow; these pin the
error mapping underneath it, where every failure mode has to become an
`InvalidCredentialsError` the routes can translate (or be allowed through, as
when Supabase is not configured at all).
"""

import pytest

from app.modules.auth.service import (
    InvalidCredentialsError,
    get_user_from_token,
    refresh_session,
    sign_in,
    sign_up,
)


class FakeUser:
    def __init__(self, uid: str = "user-1", email: str | None = "person@example.com") -> None:
        self.id = uid
        self.email = email


class FakeSession:
    def __init__(
        self,
        access_token: str = "jwt",
        refresh_token: str = "refresh-1",
        expires_at: int | None = 123,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at


class FakeResponse:
    def __init__(self, session=None, user=None) -> None:
        self.session = session
        self.user = user


class FakeAuth:
    """A scripted Supabase Auth surface: one answer, or one error."""

    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    def _reply(self):
        if self.error is not None:
            raise self.error
        return self.response

    def sign_up(self, credentials):
        return self._reply()

    def sign_in_with_password(self, credentials):
        return self._reply()

    def refresh_session(self, token):
        return self._reply()

    def get_user(self, token):
        return self._reply()


class FakeClient:
    def __init__(self, auth: FakeAuth) -> None:
        self.auth = auth


# --- sign-up ---------------------------------------------------------------


def test_sign_up_returns_the_new_user_id():
    auth = FakeAuth(FakeResponse(user=FakeUser("new-user")))
    assert sign_up("a@b.c", "password123", FakeClient(auth)) == "new-user"


def test_sign_up_maps_a_rejected_password():
    auth = FakeAuth(error=RuntimeError("Password should be at least 6 characters"))
    with pytest.raises(InvalidCredentialsError, match="at least 6"):
        sign_up("a@b.c", "short", FakeClient(auth))


def test_sign_up_without_a_user_is_rejected():
    auth = FakeAuth(FakeResponse(user=None))
    with pytest.raises(InvalidCredentialsError, match="Sign-up failed"):
        sign_up("a@b.c", "password123", FakeClient(auth))


# --- sign-in ---------------------------------------------------------------


def test_sign_in_returns_the_token_pair():
    auth = FakeAuth(FakeResponse(FakeSession(), FakeUser()))
    session = sign_in("a@b.c", "password123", FakeClient(auth))

    assert session.user_id == "user-1"
    assert session.access_token == "jwt"
    assert session.refresh_token == "refresh-1"
    assert session.expires_at == 123


def test_sign_in_without_a_session_is_rejected():
    auth = FakeAuth(FakeResponse(session=None, user=FakeUser()))
    with pytest.raises(InvalidCredentialsError, match="Invalid email or password"):
        sign_in("a@b.c", "password123", FakeClient(auth))


def test_sign_in_maps_auth_api_errors():
    auth = FakeAuth(error=RuntimeError("Invalid login credentials"))
    with pytest.raises(InvalidCredentialsError, match="Invalid login credentials"):
        sign_in("a@b.c", "password123", FakeClient(auth))


def test_sign_in_falls_back_to_the_jwt_expiry(monkeypatch):
    """Not every auth response carries `expires_at`; the JWT always carries `exp`."""
    from app.modules.auth import service

    monkeypatch.setattr(service, "jwt_expires_at", lambda token: 4242)
    auth = FakeAuth(FakeResponse(FakeSession(expires_at=None), FakeUser()))

    assert sign_in("a@b.c", "password123", FakeClient(auth)).expires_at == 4242


def test_sign_in_tolerates_a_blank_email():
    auth = FakeAuth(FakeResponse(FakeSession(), FakeUser(email=None)))
    assert sign_in("a@b.c", "password123", FakeClient(auth)).email == ""


# --- refresh ---------------------------------------------------------------


def test_refresh_session_uses_the_shared_client_when_none_is_passed(monkeypatch):
    from app.modules.auth import service

    fake = FakeClient(FakeAuth(FakeResponse(FakeSession(refresh_token="refresh-2"), FakeUser())))
    monkeypatch.setattr(service, "get_supabase_client", lambda: fake)

    assert refresh_session("refresh-1").refresh_token == "refresh-2"


def test_refresh_session_reports_a_spent_token():
    auth = FakeAuth(error=RuntimeError("Invalid Refresh Token"))
    with pytest.raises(InvalidCredentialsError, match="Session expired"):
        refresh_session("already-used", FakeClient(auth))


def test_refresh_session_lets_a_missing_configuration_escape(monkeypatch):
    """No credentials is a deployment problem (503), not a bad password (401)."""
    from app.core.clients.supabase import SupabaseNotConfiguredError
    from app.modules.auth import service

    def unconfigured():
        raise SupabaseNotConfiguredError("Supabase is not configured.")

    monkeypatch.setattr(service, "get_supabase_client", unconfigured)

    with pytest.raises(SupabaseNotConfiguredError):
        refresh_session("refresh-1")


# --- token -> user --------------------------------------------------------


def test_get_user_from_token_maps_errors():
    with pytest.raises(InvalidCredentialsError, match="Invalid or expired session"):
        get_user_from_token("jwt", FakeClient(FakeAuth(error=RuntimeError("bad jwt"))))


def test_get_user_from_token_rejects_a_missing_user():
    with pytest.raises(InvalidCredentialsError, match="Invalid or expired session"):
        get_user_from_token("jwt", FakeClient(FakeAuth(FakeResponse(user=None))))


def test_get_user_from_token_returns_identity():
    auth = FakeAuth(FakeResponse(user=FakeUser("abc", "someone@example.com")))
    assert get_user_from_token("jwt", FakeClient(auth)) == {
        "id": "abc",
        "email": "someone@example.com",
    }
