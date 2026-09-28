"""API key storage: encrypted at rest, masked in every response."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security.crypto import decrypt_secret
from app.main import app

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix

SECRET = "sk-or-v1-abcdef0123456789"
KEY_MATERIAL = "unit-test-key-material"


@pytest.fixture()
def encryption(monkeypatch):
    """Enable encryption for the duration of a test."""
    monkeypatch.setattr(settings, "app_encryption_key", KEY_MATERIAL)
    return KEY_MATERIAL


def test_storing_a_key_returns_only_a_mask(client, encryption):
    response = client.post(f"{API}/api-keys", json={"label": "main", "key": SECRET, "tier": "free"})
    assert response.status_code == 201

    body = response.json()
    assert body["key_masked"] == "••••6789"
    assert "key_ciphertext" not in body
    assert SECRET not in response.text


def test_the_stored_value_is_ciphertext_not_the_secret(stores, client, encryption):
    client.post(f"{API}/api-keys", json={"label": "main", "key": SECRET})

    stored = stores["api_keys"][0]
    assert stored["key_ciphertext"] != SECRET
    assert SECRET not in stored["key_ciphertext"]
    assert stored["key_hint"] == "6789"
    # ...and it is recoverable only with the configured key.
    assert decrypt_secret(stored["key_ciphertext"], encryption) == SECRET


def test_listing_never_leaks_the_secret(client, stores, encryption):
    client.post(f"{API}/api-keys", json={"label": "main", "key": SECRET})

    response = client.get(f"{API}/api-keys")
    assert response.status_code == 200
    assert SECRET not in response.text
    assert response.json()[0]["key_masked"] == "••••6789"


def test_storage_is_refused_without_an_encryption_key(client, stores, monkeypatch):
    monkeypatch.setattr(settings, "app_encryption_key", "")

    response = client.post(f"{API}/api-keys", json={"label": "main", "key": SECRET})
    assert response.status_code == 503
    assert "APP_ENCRYPTION_KEY" in response.json()["detail"]
    assert stores["api_keys"] == []


def test_key_for_an_unknown_provider_is_404(client, encryption):
    response = client.post(f"{API}/api-keys", json={"label": "x", "key": SECRET, "provider_id": 42})
    assert response.status_code == 404


def test_provider_name_is_attached_to_the_listing(client, encryption):
    provider = client.post(
        f"{API}/providers", json={"name": "OpenRouter", "slug": "openrouter"}
    ).json()
    client.post(
        f"{API}/api-keys", json={"label": "main", "key": SECRET, "provider_id": provider["id"]}
    )

    assert client.get(f"{API}/api-keys").json()[0]["provider_name"] == "OpenRouter"
    assert client.get(f"{API}/providers").json()[0]["key_count"] == 1


def test_keys_can_be_toggled_and_deleted(client, encryption):
    created = client.post(f"{API}/api-keys", json={"label": "main", "key": SECRET}).json()

    patched = client.patch(f"{API}/api-keys/{created['id']}", json={"is_active": False})
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False

    assert client.patch(f"{API}/api-keys/{created['id']}", json={}).status_code == 400
    assert client.delete(f"{API}/api-keys/{created['id']}").status_code == 204
    assert client.get(f"{API}/api-keys").json() == []
    assert client.delete(f"{API}/api-keys/{created['id']}").status_code == 404


def test_a_fresh_key_has_no_last_used_timestamp(client, encryption):
    created = client.post(f"{API}/api-keys", json={"label": "main", "key": SECRET}).json()
    assert created["last_used_at"] is None


def test_api_key_routes_require_authentication():
    anonymous = TestClient(app)
    assert anonymous.get(f"{API}/api-keys").status_code == 401
    assert anonymous.post(f"{API}/api-keys", json={"label": "x", "key": SECRET}).status_code == 401
    assert anonymous.patch(f"{API}/api-keys/1", json={"is_active": False}).status_code == 401
    assert anonymous.delete(f"{API}/api-keys/1").status_code == 401


def test_a_key_write_that_stores_nothing_is_a_server_error(client, encryption, empty_writes):
    """A write that returns no row must not be reported as success."""
    response = client.post(f"{API}/api-keys", json={"label": "main", "key": SECRET})
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to store API key"


def test_updating_an_unknown_key_is_404(client):
    response = client.patch(f"{API}/api-keys/999", json={"label": "renamed"})
    assert response.status_code == 404
    assert response.json()["detail"] == "API key not found"
