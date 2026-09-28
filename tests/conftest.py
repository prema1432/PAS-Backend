"""Shared pytest fixtures."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.clients.supabase import get_supabase_client
from app.core.dependencies import get_current_client, get_current_user
from app.main import app
from tests.fakes import FakeSupabaseClient, WriteEmptyClient, WriteErrorClient

TEST_USER = {"id": "test-user-0001", "email": "test@example.com"}
OTHER_USER_ID = "other-user"


@pytest.fixture(autouse=True)
def fresh_rate_limiters():
    """Give every test a clean request budget.

    The rate limiters are module-level and keyed by client IP, while TestClient
    always presents the same address — without this, a long test session would
    trip the limit and unrelated tests would start seeing 429s.
    """
    main_module.default_limiter.reset()
    main_module.auth_limiter.reset()
    yield
    main_module.default_limiter.reset()
    main_module.auth_limiter.reset()


@pytest.fixture()
def stores():
    """In-memory table data shared with assertions."""
    return {
        "login_events": [
            {
                "id": 1,
                "user_id": TEST_USER["id"],
                "ip": "203.0.113.9",
                "ip_version": "IPv4",
                "city": "Bengaluru",
                "region": "Karnataka",
                "country": "India",
                "country_code": "IN",
                "browser": "Chrome",
                "browser_version": "141.0.0.0",
                "os": "macOS",
                "device_type": "Desktop",
                "is_bot": False,
                "created_at": "2026-09-27T18:00:00+00:00",
                "user_agent": "Mozilla/5.0 (Macintosh) Chrome/141.0.0.0",
            },
            {
                "id": 2,
                "user_id": OTHER_USER_ID,
                "ip": "198.51.100.4",
                "browser": "Firefox",
                "is_bot": False,
                "created_at": "2026-09-27T19:00:00+00:00",
            },
        ],
        "customers": [
            {
                "id": 1,
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "phone": "+919876543210",
                "otp": "123456",
                "is_active": True,
                "plan": "free",
                "remaining_minutes": 120,
                "auto_recharge": False,
                "auto_recharge_amount": 0,
                "auto_recharge_minutes": 0,
                "user_id": TEST_USER["id"],
            },
            {
                "id": 2,
                "name": "Other Owner Customer",
                "email": "other@example.com",
                "phone": "+919000000000",
                "otp": "654321",
                "is_active": True,
                "plan": "paid",
                "remaining_minutes": 500,
                "auto_recharge": False,
                "auto_recharge_amount": 0,
                "auto_recharge_minutes": 0,
                "user_id": OTHER_USER_ID,
            },
        ],
        "providers": [],
        "provider_models": [],
        "api_keys": [],
        "payments": [],
        "customer_sessions": [],
        "audit_logs": [],
    }


@pytest.fixture()
def fake_customers(stores):
    """Customers owned by the test user."""
    return stores["customers"]


@pytest.fixture()
def client(stores):
    """TestClient with Supabase and current-user dependencies overridden."""
    client_stub = FakeSupabaseClient(stores)
    app.dependency_overrides[get_supabase_client] = lambda: client_stub
    app.dependency_overrides[get_current_client] = lambda: client_stub
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    yield TestClient(app)
    app.dependency_overrides.pop(get_supabase_client, None)
    app.dependency_overrides.pop(get_current_client, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def empty_writes(client, stores):
    """Reads work, writes come back empty — the routers' "didn't land" 500 path."""
    stub = WriteEmptyClient(stores)
    app.dependency_overrides[get_current_client] = lambda: stub
    return stub


@pytest.fixture()
def write_error(client, stores):
    """Install a data client whose writes raise, and hand back the installer.

    Usage: ``error = write_error(RuntimeError("duplicate key ..."))``. Reads keep
    working, so a test can seed state and then make exactly the write fail.
    """

    def install(error: Exception) -> Exception:
        stub = WriteErrorClient(stores, error)
        app.dependency_overrides[get_current_client] = lambda: stub
        return error

    return install
