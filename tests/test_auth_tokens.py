"""Tests for the JWT access token + refresh token session flow.

The Supabase Auth boundary is faked here so the flow can be exercised without
network access: what matters is which cookies are written, when the refresh
token is spent (Supabase rotates it, so each one is single-use), and that an
expired access token is renewed transparently.

The TestClient runs over https on purpose: session cookies are `Secure`, so a
plain-http client would never send them back and every request would look
anonymous.
"""

import base64
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.modules.auth.router as auth_router
import app.modules.auth.service as auth_service
import app.modules.auth.session as auth_session
from app.core.config import settings
from app.core.security.jwt import decode_claims, expires_at, is_expired
from app.main import app
from app.modules.auth.session import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE
from app.modules.registry import MODULES, prefix_for

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix
HTTPS_BASE_URL = "https://testserver"


def _b64(payload: dict) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def make_jwt(exp_delta: int = 3600, sub: str = "user-1", email: str = "user@example.com") -> str:
    """A JWT-shaped token with a real `exp` claim (unsigned: claims only).

    The unique `jti` keeps two tokens minted in the same second distinguishable.
    """
    header = _b64({"alg": "HS256", "typ": "JWT"})
    claims = {
        "sub": sub,
        "email": email,
        "exp": int(time.time()) + exp_delta,
        "jti": uuid4().hex,
    }
    return f"{header}.{_b64(claims)}.sig"


def cleared_cookies(response) -> set[str]:
    """Cookie names the response asked the browser to drop."""
    return {
        header.split("=")[0]
        for header in response.headers.get_list("set-cookie")
        if "Max-Age=0" in header
    }


# --- fakes for the Supabase Auth boundary ----------------------------------


class FakeUser:
    def __init__(self, uid: str, email: str) -> None:
        self.id = uid
        self.email = email


class FakeSession:
    def __init__(self, access_token: str, refresh_token: str) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at(access_token)
        self.expires_in = 3600


class FakeAuthResponse:
    def __init__(self, session=None, user=None) -> None:
        self.session = session
        self.user = user


class FakeAdmin:
    """Mirrors `client.auth.admin`: revocation takes the caller's JWT."""

    def __init__(self, revoked: list[str]) -> None:
        self._revoked = revoked

    def sign_out(self, jwt: str, scope: str = "global") -> None:
        self._revoked.append(jwt)


class FakeAuth:
    """Records what Supabase Auth was asked to do."""

    def __init__(self) -> None:
        self.refreshed: list[str] = []
        self.revoked: list[str] = []
        self.signed_up: list[str] = []
        self.admin = FakeAdmin(self.revoked)

    def sign_in_with_password(self, credentials: dict) -> FakeAuthResponse:
        if credentials["password"] == "wrong-password":
            raise RuntimeError("Invalid login credentials")
        return FakeAuthResponse(
            FakeSession(make_jwt(), "refresh-1"),
            FakeUser("user-1", credentials["email"]),
        )

    def refresh_session(self, refresh_token: str) -> FakeAuthResponse:
        if not refresh_token.startswith("refresh-"):
            raise RuntimeError("Invalid Refresh Token")
        self.refreshed.append(refresh_token)
        return FakeAuthResponse(
            FakeSession(make_jwt(), "refresh-next"), FakeUser("user-1", "user@example.com")
        )

    def get_user(self, access_token: str) -> FakeAuthResponse:
        if access_token == "revoked":
            raise RuntimeError("invalid jwt")
        return FakeAuthResponse(None, FakeUser("user-1", "user@example.com"))


class FakeAuthClient:
    def __init__(self) -> None:
        self.auth = FakeAuth()


@pytest.fixture()
def auth_client(monkeypatch):
    """A TestClient with the Supabase Auth boundary faked out."""
    fake = FakeAuthClient()
    for module in (auth_router, auth_service, auth_session):
        monkeypatch.setattr(module, "get_supabase_client", lambda: fake)
    # Login events are a database write; not what this file is testing.
    monkeypatch.setattr(auth_router, "record_login", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_router, "get_client_for_token", lambda token: fake)
    return TestClient(app, base_url=HTTPS_BASE_URL), fake.auth


def sign_in(client) -> None:
    response = client.post(
        f"{API}/auth/login", json={"email": "user@example.com", "password": "password123"}
    )
    assert response.status_code == 200


# --- JWT claim helpers -----------------------------------------------------


def test_decode_claims_reads_the_payload():
    claims = decode_claims(make_jwt(sub="abc", email="a@b.c"))
    assert claims["sub"] == "abc"
    assert claims["email"] == "a@b.c"


def test_decode_claims_gives_up_on_opaque_values():
    for token in (None, "", "opaque-token", "a.b.c", "a.not-base64!.c"):
        assert decode_claims(token) == {}


def test_expires_at_returns_none_without_an_exp_claim():
    assert expires_at(make_jwt()) is not None
    assert expires_at("opaque-token") is None


def test_is_expired_uses_a_leeway_and_tolerates_unknown_tokens():
    assert is_expired(make_jwt(exp_delta=-60)) is True
    assert is_expired(make_jwt(exp_delta=3600)) is False
    # Inside the 30 second leeway: refresh now rather than risk a 401 mid-flight.
    assert is_expired(make_jwt(exp_delta=5)) is True
    # No readable exp: Supabase stays the authority on validity.
    assert is_expired("opaque-token") is False
    assert is_expired(None) is False


# --- sign-in, refresh, sign-out -------------------------------------------


def test_login_issues_both_tokens_as_hardened_cookies(auth_client):
    client, _ = auth_client
    response = client.post(
        f"{API}/auth/login", json={"email": "user@example.com", "password": "password123"}
    )
    headers = response.headers.get_list("set-cookie")
    access_header = next(h for h in headers if h.startswith(ACCESS_TOKEN_COOKIE))

    assert response.status_code == 200
    assert response.cookies[ACCESS_TOKEN_COOKIE]
    assert response.cookies[REFRESH_TOKEN_COOKIE] == "refresh-1"
    assert "HttpOnly" in access_header and "Secure" in access_header
    assert "samesite=lax" in access_header.lower()
    # The client is told when the JWT lapses so it can refresh before it does.
    assert response.json()["access_token_expires_at"] == expires_at(
        response.cookies[ACCESS_TOKEN_COOKIE]
    )


def test_refresh_rotates_the_token_pair(auth_client):
    client, fake = auth_client
    sign_in(client)
    first_access = client.cookies[ACCESS_TOKEN_COOKIE]

    response = client.post(f"{API}/auth/refresh")

    assert response.status_code == 200
    assert fake.refreshed == ["refresh-1"]  # spent exactly once
    assert response.cookies[ACCESS_TOKEN_COOKIE] != first_access
    assert response.cookies[REFRESH_TOKEN_COOKIE] == "refresh-next"
    # The rotated pair really works.
    assert client.get(f"{API}/auth/me").status_code == 200


def test_refresh_without_a_cookie_is_rejected_and_clears_the_session(auth_client):
    client, fake = auth_client
    response = client.post(f"{API}/auth/refresh")

    assert response.status_code == 401
    assert fake.refreshed == []
    assert cleared_cookies(response) == {ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE}


def test_refresh_with_a_dead_token_clears_the_session(auth_client):
    client, fake = auth_client
    client.cookies.set(REFRESH_TOKEN_COOKIE, "garbage")

    response = client.post(f"{API}/auth/refresh")

    assert response.status_code == 401
    assert fake.refreshed == []
    assert cleared_cookies(response) == {ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE}


def test_logout_revokes_the_session_and_drops_both_cookies(auth_client):
    client, fake = auth_client
    sign_in(client)
    access_token = client.cookies[ACCESS_TOKEN_COOKIE]

    response = client.post(f"{API}/auth/logout")

    assert response.status_code == 204
    assert fake.revoked == [access_token]  # a stolen cookie can't be replayed
    assert cleared_cookies(response) == {ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE}
    assert client.get(f"{API}/auth/me").status_code == 401


# --- sign-up is gone ---------------------------------------------------------


def test_signup_is_gone(auth_client):
    """Accounts are provisioned in Supabase; the API no longer signs anyone up."""
    client, fake = auth_client
    response = client.post(
        f"{API}/auth/signup", json={"email": "new@example.com", "password": "password123"}
    )

    assert response.status_code in (404, 405)
    assert fake.signed_up == []
    assert response.cookies.get(ACCESS_TOKEN_COOKIE) is None


# --- rejected credentials and degraded Supabase -----------------------------


def test_login_with_the_wrong_password_is_401(auth_client):
    client, _ = auth_client
    response = client.post(
        f"{API}/auth/login", json={"email": "user@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid login credentials"
    assert response.cookies.get(ACCESS_TOKEN_COOKIE) is None


def test_refresh_without_supabase_configuration_is_503(auth_client, monkeypatch):
    """A deploy missing its credentials must say so, not surface a raw 500."""
    from app.core.clients.supabase import SupabaseNotConfiguredError

    client, _ = auth_client
    client.cookies.set(REFRESH_TOKEN_COOKIE, "refresh-1")

    def unconfigured(*args, **kwargs):
        raise SupabaseNotConfiguredError("Supabase is not configured. Set SUPABASE_URL.")

    monkeypatch.setattr(auth_router, "rotate_session", unconfigured)
    response = client.post(f"{API}/auth/refresh")

    assert response.status_code == 503
    assert "Supabase is not configured" in response.json()["detail"]


def test_logout_still_clears_the_cookies_when_revocation_fails(auth_client, monkeypatch):
    """Supabase being unreachable must not trap the user in a signed-in state."""
    client, fake = auth_client
    sign_in(client)

    def broken(jwt, scope="global"):
        raise RuntimeError("network is down")

    monkeypatch.setattr(fake.admin, "sign_out", broken)
    response = client.post(f"{API}/auth/logout")

    assert response.status_code == 204
    assert cleared_cookies(response) == {ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE}


# --- transparent refresh ---------------------------------------------------


def test_expired_access_token_is_refreshed_transparently(auth_client):
    client, fake = auth_client
    client.cookies.set(ACCESS_TOKEN_COOKIE, make_jwt(exp_delta=-60))
    client.cookies.set(REFRESH_TOKEN_COOKIE, "refresh-1")

    response = client.get(f"{API}/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"
    assert fake.refreshed == ["refresh-1"]
    # The fresh pair is handed back to the browser on the same response.
    assert response.cookies[ACCESS_TOKEN_COOKIE]
    assert response.cookies[REFRESH_TOKEN_COOKIE] == "refresh-next"


def test_valid_access_token_is_used_without_a_refresh(auth_client):
    client, fake = auth_client
    client.cookies.set(ACCESS_TOKEN_COOKIE, make_jwt(exp_delta=3600))

    response = client.get(f"{API}/auth/me")

    assert response.status_code == 200
    assert fake.refreshed == []  # no token spent
    assert response.headers.get_list("set-cookie") == []  # nothing re-issued


def test_revoked_but_unexpired_token_is_refreshed(auth_client):
    """A revoked token still carries a valid-looking exp claim."""
    client, fake = auth_client
    client.cookies.set(ACCESS_TOKEN_COOKIE, "revoked")
    client.cookies.set(REFRESH_TOKEN_COOKIE, "refresh-1")

    response = client.get(f"{API}/auth/me")

    assert response.status_code == 200
    assert fake.refreshed == ["refresh-1"]


def test_session_without_cookies_is_401(auth_client):
    client, _ = auth_client
    assert client.get(f"{API}/auth/me").status_code == 401


def test_rejected_token_without_a_refresh_cookie_is_401(auth_client):
    """Unreadable-but-unexpired JWT and nothing to refresh with: sign in again."""
    client, fake = auth_client
    client.cookies.set(ACCESS_TOKEN_COOKIE, "revoked")

    response = client.get(f"{API}/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired session"
    assert fake.refreshed == []  # there was no refresh token to spend
    assert cleared_cookies(response) == {ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE}


def test_stale_session_with_a_dead_refresh_token_is_401(auth_client):
    client, fake = auth_client
    client.cookies.set(ACCESS_TOKEN_COOKIE, make_jwt(exp_delta=-60))
    client.cookies.set(REFRESH_TOKEN_COOKIE, "garbage")

    response = client.get(f"{API}/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Session expired, please sign in again"
    assert fake.refreshed == []  # rejected before Supabase saw it as usable


# --- module wiring ---------------------------------------------------------


def test_every_documented_module_route_is_mounted():
    """A module added to the registry is actually reachable on the app."""
    app_paths = set(app.openapi()["paths"])
    module_paths = {
        prefix_for(module.version) + route.path
        for module in MODULES
        for route in module.router.routes
        if route.include_in_schema
    }
    assert module_paths <= app_paths


def test_supabase_auth_api_contract():
    """Pin the supabase-py Auth calls the session flow depends on.

    `refresh_session` must accept the refresh token as an argument, and
    revocation must take the caller's *JWT* — handing a refresh token to
    `sign_out` silently revokes nothing, which is exactly the bug this guards.
    """
    from inspect import signature

    from supabase import create_client

    # Construction only: create_client makes no request here.
    auth = create_client("https://example.supabase.co", "anon-key").auth
    assert "refresh_token" in signature(auth.refresh_session).parameters
    assert "jwt" in signature(auth.admin.sign_out).parameters
    assert "scope" in signature(auth.admin.sign_out).parameters


def test_registry_covers_every_feature_module():
    names = {module.name for module in MODULES}
    assert {
        "system",
        "auth",
        "customers",
        "providers",
        "api_keys",
        "billing",
        "sessions",
        "analytics",
    } <= names
