"""Tests for the customer portal: phone + OTP sign-in and the profile it returns.

The portal is the one anonymous surface in the app that answers with real data,
so these tests pin the two things that make it safe: the credential is the
**phone + OTP pair**, checked in constant time, and the answer is an explicit
allow-list that can never carry the owner's identity, the OTP itself or the
audit columns. Every failure mode answers identically, so probing the endpoint
tells a caller nothing about which half of the pair was wrong.
"""

import secrets

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.dependencies import get_current_client
from app.core.security.otp import generate_otp
from app.main import app

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix

LOGIN = {"phone": "98765 43210", "otp": "123456"}  # Ada, seeded as +919876543210
WRONG_OTP = {"phone": LOGIN["phone"], "otp": "000000"}
UNKNOWN_PHONE = {"phone": "90000 99999", "otp": "123456"}

#: The fields the profile is allowed to carry. Anything else in a response is a leak.
PROFILE_FIELDS = {
    "id",
    "name",
    "email",
    "phone",
    "is_active",
    "plan",
    "remaining_minutes",
    "free_minutes",
    "paid_minutes",
}


@pytest.fixture()
def portal(stores):
    """TestClient for the anonymous portal, with the shared client overridden.

    The portal looks customers up by phone *across owners*, so it must not run
    through the token-bound client — it uses the shared anon client, which here
    is the in-memory fake. No current-user override: these requests are
    deliberately unauthenticated.
    """
    from app.core.clients.supabase import get_supabase_client

    stub = type(stores)  # noqa: F841 - readability below
    from tests.fakes import FakeSupabaseClient

    fake = FakeSupabaseClient(stores)
    app.dependency_overrides[get_supabase_client] = lambda: fake
    app.dependency_overrides.pop(get_current_client, None)
    yield TestClient(app)
    app.dependency_overrides.pop(get_supabase_client, None)


def test_login_with_phone_and_otp_returns_the_profile(portal):
    response = portal.post(f"{API}/portal/login", json=LOGIN)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == PROFILE_FIELDS  # an allow-list, not a row dump
    assert body["name"] == "Ada Lovelace"
    assert body["email"] == "ada@example.com"
    assert body["phone"] == "+919876543210"  # the canonical stored form
    assert body["plan"] == "free"
    assert body["remaining_minutes"] == 120


def test_the_profile_never_carries_the_otp_or_the_owner(portal):
    """The OTP is a standing credential; echoing it would defeat the login."""
    body = portal.post(f"{API}/portal/login", json=LOGIN).json()

    assert "otp" not in body
    assert "user_id" not in body
    assert LOGIN["otp"] not in str(body)


def test_paid_plan_splits_the_balance_into_free_and_paid_minutes(portal, stores):
    """Free time is the plan allowance on a paid plan; everything else was bought."""
    stores["customers"][0]["plan"] = "paid"

    body = portal.post(f"{API}/portal/login", json=LOGIN).json()

    assert body["plan"] == "paid"
    assert body["remaining_minutes"] == 120
    assert body["free_minutes"] == 0
    assert body["paid_minutes"] == 120


def test_free_plan_counts_the_whole_balance_as_free_minutes(portal):
    body = portal.post(f"{API}/portal/login", json=LOGIN).json()

    assert body["free_minutes"] == 120
    assert body["paid_minutes"] == 0


def test_a_wrong_otp_is_401_and_says_nothing_about_which_half_failed(portal):
    right = portal.post(f"{API}/portal/login", json=WRONG_OTP)
    unknown = portal.post(f"{API}/portal/login", json=UNKNOWN_PHONE)

    assert right.status_code == unknown.status_code == 401
    assert right.json() == unknown.json()  # identical bodies: no oracle


def test_an_unknown_phone_is_401(portal):
    assert portal.post(f"{API}/portal/login", json=UNKNOWN_PHONE).status_code == 401


def test_an_inactive_customer_cannot_sign_in(portal, stores):
    """Deactivating a customer must cut off their portal access immediately."""
    stores["customers"][0]["is_active"] = False

    assert portal.post(f"{API}/portal/login", json=LOGIN).status_code == 401


def test_the_otp_is_compared_in_constant_time(portal, stores, monkeypatch):
    """The same comparison the docs auth uses — a measured guess is not faster."""
    seen: list[str] = []
    real_compare = secrets.compare_digest

    def spy(left, right):
        seen.append(str(left))
        return real_compare(left, right)

    monkeypatch.setattr("app.modules.customer_portal.service.secrets.compare_digest", spy)
    portal.post(f"{API}/portal/login", json=WRONG_OTP)

    assert seen == [stores["customers"][0]["otp"]]


def test_malformed_bodies_are_422_not_401(portal):
    """A bad shape is a client error; it must not look like a failed login."""
    assert portal.post(f"{API}/portal/login", json={"phone": "123"}).status_code == 422
    assert portal.post(f"{API}/portal/login", json=LOGIN | {"otp": "12345"}).status_code == 422
    assert portal.post(f"{API}/portal/login", json=LOGIN | {"otp": "abcdef"}).status_code == 422


def test_me_answers_with_the_current_balance(portal, stores):
    login = portal.post(f"{API}/portal/login", json=LOGIN)
    stores["customers"][0]["remaining_minutes"] = 45

    body = portal.post(f"{API}/portal/me", json=LOGIN).json()

    assert body == login.json() | {"remaining_minutes": 45, "free_minutes": 45}


def test_phone_lookup_ignores_whitespace_and_matches_the_stored_value(portal):
    """`find_customer_by_phone` strips, so '  +91…' finds the same row."""
    padded = portal.post(f"{API}/portal/login", json=LOGIN | {"phone": "  98765 43210  "})
    assert padded.status_code == 200
    assert padded.json()["phone"] == "+919876543210"  # the canonical stored form


def test_me_rejects_the_same_way_login_does(portal):
    """Unknown phone and wrong OTP on /me answer with the identical 401 body."""
    unknown = portal.post(f"{API}/portal/me", json=LOGIN | {"phone": "91111 11111"})
    wrong_otp = portal.post(f"{API}/portal/me", json=LOGIN | {"otp": "654321"})
    assert unknown.status_code == wrong_otp.status_code == 401
    assert unknown.json() == wrong_otp.json()


def test_portal_login_carries_the_tight_rate_budget(portal, monkeypatch):
    """The portal's own limiter check must use the auth limiter, not the loose one."""
    import app.modules.customer_portal.router as router_module
    from app.core.security.rate_limit import SlidingWindowRateLimiter

    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
    monkeypatch.setattr(router_module, "auth_limiter", limiter)

    assert portal.post(f"{API}/portal/login", json=LOGIN).status_code == 200
    blocked = portal.post(f"{API}/portal/login", json=LOGIN)

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_the_middleware_gives_the_portal_the_auth_budget_too():
    """`_SENSITIVE_SUFFIXES` must name the portal paths (regression guard)."""
    import app.main as main_module

    for suffix in ("/portal/login", "/portal/me"):
        assert (
            main_module._limiter_for(f"{settings.api_prefix}{suffix}") is main_module.auth_limiter
        )


def test_otp_regeneration_by_the_owner_is_what_rotates_the_credential(portal, stores):
    """No SMS delivery exists, so the OTP is a standing secret the owner rotates."""
    new_otp = generate_otp()
    stores["customers"][0]["otp"] = new_otp

    stale = portal.post(f"{API}/portal/login", json=LOGIN)
    fresh = portal.post(f"{API}/portal/login", json=LOGIN | {"otp": new_otp})

    assert stale.status_code == 401  # the old code stops working the moment...
    assert fresh.status_code == 200  # ...the owner issues a new one
