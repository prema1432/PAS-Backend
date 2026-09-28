"""Smoke tests for core endpoints and auth."""

import pytest
from fastapi.testclient import TestClient

from app.core.clients.supabase import SupabaseNotConfiguredError
from app.core.config import settings
from app.core.dependencies import get_current_client
from app.main import app

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix


def test_root_serves_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "PAS Backend" in response.text
    assert "<script" in response.text


def test_dashboard_has_no_todo_ui(client):
    """The todos feature is gone: no markup, copy or API calls should remain."""
    html = client.get("/").text
    assert "todo" not in html.lower()
    assert "/todos" not in html
    assert "task" not in html.lower()


def test_dashboard_exposes_the_new_sidebar_views(client):
    """API keys and Billing are reachable from the sidebar."""
    html = client.get("/").text
    for marker in ["view-apikeys", "view-billing", "API keys", "Billing", "Auto-recharge"]:
        assert marker in html


def test_info(client):
    response = client.get("/info")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "PAS Backend"
    assert body["status"] == "ok"
    # The docs link is only advertised when docs auth is configured.
    assert body["docs"] in (None, "/docs")


def test_info_advertises_docs_when_enabled(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "docs_username", "docs-admin")
    monkeypatch.setattr(settings, "docs_password", "s3cret-docs")
    assert client.get("/info").json()["docs"] == "/docs"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_todos_endpoint_is_gone(client):
    """The router was removed, so the path no longer exists."""
    assert client.get("/todos").status_code == 404


@pytest.fixture()
def anon_client():
    """Client without the current-user override: requests are unauthenticated."""
    app.dependency_overrides[get_current_client] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_client, None)


def test_me_requires_auth(anon_client):
    assert anon_client.get(f"{API}/auth/me").status_code == 401


def test_auth_me_returns_override_user(client):
    response = client.get(f"{API}/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "test@example.com"


def test_current_client_is_bound_to_the_callers_access_token(monkeypatch):
    """`get_current_client` must hand PostgREST the caller's JWT, not the shared one."""
    import app.core.dependencies as dependencies
    from app.modules.auth.session import Session

    seen: list[str] = []
    monkeypatch.setattr(dependencies, "get_client_for_token", seen.append)

    session = Session(user_id="user-1", email="user@example.com", access_token="the-jwt")
    dependencies.get_current_client(session)

    assert seen == ["the-jwt"]


def test_missing_supabase_configuration_is_a_clean_503(client):
    """A deploy without credentials answers 503 rather than a raw 500."""

    def unconfigured():
        raise SupabaseNotConfiguredError("Supabase is not configured. Set SUPABASE_URL.")

    app.dependency_overrides[get_current_client] = unconfigured
    try:
        response = client.get(f"{API}/customers")
    finally:
        app.dependency_overrides.pop(get_current_client, None)

    assert response.status_code == 503
    assert "Supabase is not configured" in response.json()["detail"]
    # The shared handler is reused, so nothing leaked as a server error.
    assert response.headers["x-content-type-options"] == "nosniff"
