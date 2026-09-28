"""Tests for the security layer: rate limits, docs auth, CORS, API auth."""

import re

import pytest
from fastapi.testclient import TestClient

from app.core.clients.supabase import get_supabase_client
from app.core.config import settings
from app.core.dependencies import get_current_client, get_current_user
from app.core.security.rate_limit import SlidingWindowRateLimiter
from app.main import app


class FakeClock:
    """Controllable monotonic clock for window tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- limiter unit tests ----------------------------------------------------
def test_limiter_allows_up_to_limit_then_blocks():
    limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60)
    assert [limiter.check("ip")[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.check("ip")
    assert allowed is False
    assert retry_after >= 1


def test_limiter_is_per_key():
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
    assert limiter.check("a")[0] is True
    assert limiter.check("b")[0] is True
    assert limiter.check("a")[0] is False


def test_window_slides_after_expiry():
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=clock)
    assert limiter.check("ip")[0] is True
    assert limiter.check("ip")[0] is True
    assert limiter.check("ip")[0] is False
    clock.advance(61)
    assert limiter.check("ip")[0] is True


def test_reset_clears_history():
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
    assert limiter.check("ip")[0] is True
    limiter.reset("ip")
    assert limiter.check("ip")[0] is True


def test_limiter_counts_and_forgets_tracked_keys():
    limiter = SlidingWindowRateLimiter(limit=5, window_seconds=60)
    assert limiter.tracked_keys() == 0

    limiter.check("a")
    limiter.check("b")
    assert limiter.tracked_keys() == 2

    limiter.reset()
    assert limiter.tracked_keys() == 0


# --- middleware behaviour --------------------------------------------------
@pytest.fixture()
def tight_default_limiter(monkeypatch):
    """Replace the default limiter with a tiny one for a test."""
    import app.main as main_module

    limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60)
    monkeypatch.setattr(main_module, "default_limiter", limiter)
    monkeypatch.setattr(main_module, "auth_limiter", limiter)
    limiter.reset()
    return limiter


def test_rate_limit_returns_429_with_retry_after(tight_default_limiter):
    limiter = tight_default_limiter
    limiter.reset()
    client = TestClient(app)
    for _ in range(3):
        assert client.get("/health").status_code == 200
    blocked = client.get("/health")
    assert blocked.status_code == 429
    assert blocked.json()["detail"].startswith("Too many requests")
    assert int(blocked.headers["Retry-After"]) >= 1


def test_rate_limit_is_keyed_by_client_ip(tight_default_limiter):
    tight_default_limiter.reset()
    client = TestClient(app)
    for _ in range(3):
        client.get("/health")
    # A different client IP gets its own budget.
    fresh = client.get("/health", headers={"x-forwarded-for": "203.0.113.77"})
    assert fresh.status_code == 200


# --- hardening headers -----------------------------------------------------
def test_security_headers_present(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


# --- API docs protection ---------------------------------------------------
def test_docs_are_hidden_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "docs_username", "")
    monkeypatch.setattr(settings, "docs_password", "")
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_docs_require_basic_auth(monkeypatch):
    monkeypatch.setattr(settings, "docs_username", "docs-admin")
    monkeypatch.setattr(settings, "docs_password", "s3cret-docs")
    client = TestClient(app)

    assert client.get("/docs").status_code == 401
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/docs", auth=("docs-admin", "wrong")).status_code == 401

    ok = client.get("/docs", auth=("docs-admin", "s3cret-docs"))
    assert ok.status_code == 200
    assert "swagger" in ok.text.lower()

    redoc = client.get("/redoc", auth=("docs-admin", "s3cret-docs"))
    assert redoc.status_code == 200

    schema = client.get("/openapi.json", auth=("docs-admin", "s3cret-docs"))
    assert schema.status_code == 200
    assert f"{settings.api_prefix}/customers" in schema.json()["paths"]


def test_info_hides_docs_link_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "docs_username", "")
    monkeypatch.setattr(settings, "docs_password", "")
    assert TestClient(app).get("/info").json()["docs"] is None


# --- every API requires authentication ------------------------------------
# The only routes an anonymous caller may reach: the unversioned deployment
# routes plus the auth endpoints that exist to create a session.
PUBLIC_PATHS = frozenset(
    {
        "/",
        "/info",
        "/health",
        f"{settings.api_prefix}/auth/login",
        f"{settings.api_prefix}/auth/logout",
        # The customer portal: phone + OTP is the credential, no session needed.
        f"{settings.api_prefix}/portal/login",
        f"{settings.api_prefix}/portal/me",
    }
)


def _anonymous_routes() -> list[tuple[str, str]]:
    """Every documented operation, with path params filled in.

    Built from the OpenAPI schema so a newly added route is covered the moment
    it is registered — no list to keep in sync.
    """
    cases: list[tuple[str, str]] = []
    for path, operations in sorted(app.openapi()["paths"].items()):
        if path in PUBLIC_PATHS:
            continue
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        cases.extend((method.upper(), concrete) for method in sorted(operations))
    return cases


@pytest.mark.parametrize("method,path", _anonymous_routes())
def test_api_endpoints_require_auth(method, path):
    """No data endpoint answers an anonymous caller."""
    for dependency in (get_supabase_client, get_current_client, get_current_user):
        app.dependency_overrides.pop(dependency, None)
    response = TestClient(app).request(method, path)
    assert response.status_code == 401, f"{method} {path} answered anonymously"


def test_route_sweep_covers_every_documented_operation():
    """Guard the guard: the sweep must actually enumerate the API surface."""
    expected = sum(
        len(operations)
        for path, operations in app.openapi()["paths"].items()
        if path not in PUBLIC_PATHS
    )
    assert len(_anonymous_routes()) == expected > 20


def test_public_endpoints_stay_open():
    """Only the landing page, info and health are anonymous."""
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/info").status_code == 200
    assert client.get("/health").status_code == 200


# --- CORS ------------------------------------------------------------------
def test_cors_wildcard_default_has_no_credentials():
    from app.core.config import Settings

    config = Settings(cors_origins="*")
    assert config.cors_origin_list == ["*"]
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "https://anything.example"})
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_cors_origins_are_configurable():
    from app.core.config import Settings

    config = Settings(cors_origins="https://app.example.com, https://admin.example.com")
    assert config.cors_origin_list == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


def test_cors_preflight_allows_configured_origin():
    client = TestClient(app)
    response = client.options(
        f"{settings.api_prefix}/customers",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-methods" in response.headers
