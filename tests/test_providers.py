"""Tests for the provider/model catalogue and the free-paid preference list."""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix


def test_create_and_list_a_provider(client):
    created = client.post(f"{API}/providers", json={"name": "OpenRouter", "slug": "openrouter"})
    assert created.status_code == 201
    body = created.json()
    assert body["slug"] == "openrouter"
    assert body["is_free"] is True
    assert body["models"] == []
    assert body["key_count"] == 0

    listed = client.get(f"{API}/providers").json()
    assert [provider["slug"] for provider in listed] == ["openrouter"]


def test_duplicate_slug_is_rejected(client):
    client.post(f"{API}/providers", json={"name": "OpenRouter", "slug": "openrouter"})
    again = client.post(f"{API}/providers", json={"name": "Duplicate", "slug": "openrouter"})
    assert again.status_code == 409


def test_slug_and_name_are_validated(client):
    assert (
        client.post(f"{API}/providers", json={"name": "Bad", "slug": "Not A Slug"}).status_code
        == 422
    )
    assert client.post(f"{API}/providers", json={"name": "", "slug": "fine"}).status_code == 422


def test_models_are_attached_to_their_provider(client):
    provider = client.post(
        f"{API}/providers", json={"name": "OpenRouter", "slug": "openrouter"}
    ).json()
    provider_id = provider["id"]

    model = client.post(
        f"{API}/providers/{provider_id}/models",
        json={"name": "openai/gpt-4o-mini", "context_window": 128000},
    )
    assert model.status_code == 201
    assert model.json()["provider_id"] == provider_id

    listed = client.get(f"{API}/providers").json()
    assert [m["name"] for m in listed[0]["models"]] == ["openai/gpt-4o-mini"]
    assert listed[0]["models"][0]["context_window"] == 128000

    single = client.get(f"{API}/providers/{provider_id}/models").json()
    assert [m["name"] for m in single] == ["openai/gpt-4o-mini"]


def test_model_for_an_unknown_provider_is_404(client):
    assert client.post(f"{API}/providers/999/models", json={"name": "ghost"}).status_code == 404


def test_preference_list_orders_by_priority_and_filters_by_tier(client):
    client.post(f"{API}/providers", json={"name": "Second", "slug": "second", "priority": 50})
    client.post(f"{API}/providers", json={"name": "First", "slug": "first", "priority": 10})
    client.post(
        f"{API}/providers",
        json={
            "name": "PaidOnly",
            "slug": "paidonly",
            "priority": 1,
            "is_free": False,
            "is_paid": True,
            "preferred_tier": "paid",
        },
    )

    free = client.get(f"{API}/providers/preferences?tier=free").json()
    assert [provider["slug"] for provider in free] == ["first", "second"]
    assert free[0]["priority"] == 10

    paid = client.get(f"{API}/providers/preferences?tier=paid").json()
    assert [provider["slug"] for provider in paid] == ["paidonly"]

    everything = client.get(f"{API}/providers/preferences").json()
    assert [provider["slug"] for provider in everything] == ["paidonly", "first", "second"]


def test_inactive_providers_drop_out_of_preferences(client):
    provider = client.post(f"{API}/providers", json={"name": "Off", "slug": "off"}).json()
    assert (
        client.patch(f"{API}/providers/{provider['id']}", json={"is_active": False}).status_code
        == 200
    )

    assert client.get(f"{API}/providers/preferences").json() == []
    assert len(client.get(f"{API}/providers/preferences?active_only=false").json()) == 1


def test_update_and_delete_a_provider(client):
    provider = client.post(
        f"{API}/providers", json={"name": "OpenRouter", "slug": "openrouter"}
    ).json()

    patched = client.patch(
        f"{API}/providers/{provider['id']}", json={"name": "OpenRouter EU", "priority": 5}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "OpenRouter EU"

    assert client.patch(f"{API}/providers/{provider['id']}", json={}).status_code == 400
    assert client.delete(f"{API}/providers/{provider['id']}").status_code == 204
    assert client.get(f"{API}/providers").json() == []
    assert client.delete(f"{API}/providers/{provider['id']}").status_code == 404


def test_models_can_be_updated_and_deleted(client):
    provider = client.post(
        f"{API}/providers", json={"name": "OpenRouter", "slug": "openrouter"}
    ).json()
    model = client.post(
        f"{API}/providers/{provider['id']}/models", json={"name": "meta/llama-3"}
    ).json()

    patched = client.patch(f"{API}/models/{model['id']}", json={"is_paid": True, "is_free": False})
    assert patched.status_code == 200
    assert patched.json()["is_paid"] is True

    assert client.delete(f"{API}/models/{model['id']}").status_code == 204
    assert client.get(f"{API}/providers/{provider['id']}/models").json() == []
    assert client.delete(f"{API}/models/{model['id']}").status_code == 404


def test_provider_routes_require_authentication():
    anonymous = TestClient(app)
    assert anonymous.get(f"{API}/providers").status_code == 401
    assert anonymous.get(f"{API}/providers/preferences").status_code == 401
    assert anonymous.post(f"{API}/providers", json={"name": "x", "slug": "xy"}).status_code == 401
    assert anonymous.patch(f"{API}/providers/1", json={"name": "y"}).status_code == 401
    assert anonymous.delete(f"{API}/providers/1").status_code == 401
    assert anonymous.get(f"{API}/providers/1/models").status_code == 401
    assert anonymous.post(f"{API}/providers/1/models", json={"name": "m"}).status_code == 401
    assert anonymous.patch(f"{API}/models/1", json={"name": "m"}).status_code == 401


# --- storage failures ------------------------------------------------------

RAW_PROVIDER = {
    "id": 7,
    "user_id": "test-user-0001",
    "name": "OpenRouter",
    "slug": "openrouter",
    "base_url": None,
    "is_free": True,
    "is_paid": False,
    "preferred_tier": "free",
    "priority": 100,
    "is_active": True,
}


def test_provider_write_that_stores_nothing_is_a_server_error(client, empty_writes):
    response = client.post(f"{API}/providers", json={"name": "Ghost", "slug": "ghost"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to create provider"


def test_model_write_that_stores_nothing_is_a_server_error(client, stores, empty_writes):
    stores["providers"].append(dict(RAW_PROVIDER))

    response = client.post(f"{API}/providers/7/models", json={"name": "meta/llama-3"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to create model"


def test_updating_an_unknown_provider_is_404(client):
    response = client.patch(f"{API}/providers/999", json={"name": "nowhere"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Provider not found"


def test_model_patch_needs_fields_and_an_existing_row(client):
    empty = client.patch(f"{API}/models/1", json={})
    assert empty.status_code == 400

    missing = client.patch(f"{API}/models/999", json={"is_free": False})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Model not found"
