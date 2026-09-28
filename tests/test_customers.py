"""Tests for the customers API (OTP, active toggle, free/paid plan)."""

import re

import pytest

from app.core.config import settings
from app.core.security.otp import generate_otp

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix


def test_otp_is_six_digits():
    for _ in range(50):
        otp = generate_otp()
        assert re.fullmatch(r"[0-9]{6}", otp)


def test_list_customers_scoped_to_user(client):
    response = client.get(f"{API}/customers")
    assert response.status_code == 200
    rows = response.json()
    assert [r["name"] for r in rows] == ["Ada Lovelace"]  # other user's row hidden


def test_list_customers_newest_first(client, fake_customers):
    client.post(
        f"{API}/customers",
        json={"name": "Newest", "email": "newest@example.com", "phone": "90000 11111"},
    )
    rows = client.get(f"{API}/customers").json()
    assert rows[0]["name"] == "Newest"


def test_create_customer_defaults(client, fake_customers):
    response = client.post(
        f"{API}/customers",
        json={"name": "Grace Hopper", "email": "grace@example.com", "phone": "9820011122"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Grace Hopper"
    assert created["is_active"] is True
    assert created["plan"] == "free"
    assert re.fullmatch(r"[0-9]{6}", created["otp"])
    assert created["user_id"] == "test-user-0001"


def test_phone_is_stored_with_the_default_country_code(client):
    """Bare 10-digit input is stored canonically as +91XXXXXXXXXX."""
    created = client.post(
        f"{API}/customers",
        json={"name": "Ada", "email": "ada2@example.com", "phone": "98765 43211"},
    ).json()
    assert created["phone"] == "+919876543211"


def test_indian_mobile_validation(client):
    """Exactly 10 digits starting 6-9; 0-5 starts and other shapes are 422."""
    base = {"name": "V", "email": "v@example.com"}
    for bad in (
        "0123456789",
        "1234567890",
        "5123456789",
        "98765432",
        "98765432109876",
        "abcdefghij",
        9876543210,  # not even a string
    ):
        response = client.post(f"{API}/customers", json=base | {"phone": bad})
        assert response.status_code == 422, bad
    # The three shapes all normalise to the same number: the first one creates
    # it, the rest collide with it (proving they canonicalise alike).
    first = client.post(f"{API}/customers", json=base | {"phone": "9988776655"})
    assert first.status_code == 201
    assert first.json()["phone"] == "+919988776655"
    for index, same in enumerate(("+919988776655", "91 99887 76655")):
        assert (
            client.post(
                f"{API}/customers", json=base | {"phone": same, "email": f"x{index}@example.com"}
            ).status_code
            == 409
        ), same
    for index, good in enumerate(("7987654321", "6987654321")):
        response = client.post(
            f"{API}/customers", json=base | {"phone": good, "email": f"v{index}@example.com"}
        )
        assert response.status_code == 201, good


def test_create_customer_rejects_bad_email(client):
    response = client.post(
        f"{API}/customers",
        json={"name": "Bad", "email": "not-an-email", "phone": "90000 11111"},
    )
    assert response.status_code == 422


def test_create_customer_rejects_bad_phone(client):
    response = client.post(
        f"{API}/customers",
        json={"name": "Bad", "email": "bad@example.com", "phone": "abc"},
    )
    assert response.status_code == 422


def test_create_customer_rejects_empty_name(client):
    response = client.post(
        f"{API}/customers",
        json={"name": "", "email": "empty@example.com", "phone": "90000 11111"},
    )
    assert response.status_code == 422


def test_toggle_active(client, fake_customers):
    response = client.patch(f"{API}/customers/1", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert fake_customers[0]["is_active"] is False


def test_switch_plan_to_paid(client, fake_customers):
    response = client.patch(f"{API}/customers/1", json={"plan": "paid"})
    assert response.status_code == 200
    assert response.json()["plan"] == "paid"


def test_rejects_unknown_plan(client):
    response = client.patch(f"{API}/customers/1", json={"plan": "enterprise"})
    assert response.status_code == 422


def test_regenerate_otp_changes_code(client, fake_customers):
    before = fake_customers[0]["otp"]
    response = client.post(f"{API}/customers/1/otp")
    assert response.status_code == 200
    new_otp = response.json()["otp"]
    assert re.fullmatch(r"[0-9]{6}", new_otp)
    assert new_otp != before


def test_cannot_touch_other_users_customer(client, fake_customers):
    assert client.patch(f"{API}/customers/2", json={"is_active": False}).status_code == 404
    assert client.post(f"{API}/customers/2/otp").status_code == 404
    assert client.delete(f"{API}/customers/2").status_code == 404


def test_delete_customer(client, fake_customers):
    response = client.delete(f"{API}/customers/1")
    assert response.status_code == 204
    assert [c["id"] for c in fake_customers] == [2]


def test_customers_require_auth(stores):
    """Without the current-user override the endpoint is protected."""
    from fastapi.testclient import TestClient

    from app.core.dependencies import get_current_client
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_current_client] = lambda: None
    try:
        assert TestClient(fastapi_app).get(f"{API}/customers").status_code == 401
    finally:
        fastapi_app.dependency_overrides.pop(get_current_client, None)


# --- storage failures ------------------------------------------------------

NEW_CUSTOMER = {"name": "Grace Hopper", "email": "grace@example.com", "phone": "9820011122"}


def test_duplicate_email_is_a_conflict_not_a_crash(client, write_error):
    """The unique index on (user_id, email) surfaces as a 409."""
    write_error(
        Exception('duplicate key value violates unique constraint "customers_user_id_email_key"')
    )

    response = client.post(f"{API}/customers", json=NEW_CUSTOMER)

    assert response.status_code == 409
    assert response.json()["detail"] == "A customer with this email already exists"


def test_duplicate_email_reported_by_sqlstate_is_also_a_conflict(client, write_error):
    write_error(RuntimeError("23505: duplicate key value"))
    assert client.post(f"{API}/customers", json=NEW_CUSTOMER).status_code == 409


def test_duplicate_email_in_the_fake_is_409(client):
    """The fake enforces the (user_id, email) unique index like live Postgres."""
    first = client.post(f"{API}/customers", json=NEW_CUSTOMER)
    assert first.status_code == 201
    again = client.post(f"{API}/customers", json=NEW_CUSTOMER)
    assert again.status_code == 409
    assert again.json()["detail"] == "A customer with this email already exists"


def test_duplicate_phone_in_the_fake_is_409(client):
    """Phone numbers are unique per owner too — same 409, phone-specific message."""
    client.post(f"{API}/customers", json=NEW_CUSTOMER)
    other_email = NEW_CUSTOMER | {"email": "someone-else@example.com"}

    response = client.post(f"{API}/customers", json=other_email)

    assert response.status_code == 409
    assert response.json()["detail"] == "A customer with this phone number already exists"


def test_editing_a_customer_to_a_taken_phone_is_409(client):
    """PATCH cannot move a customer onto a phone number another customer holds."""
    client.post(f"{API}/customers", json=NEW_CUSTOMER)
    second = client.post(
        f"{API}/customers",
        json={"name": "Edsger Dijkstra", "email": "edsger@example.com", "phone": "9820022222"},
    ).json()

    response = client.patch(
        f"{API}/customers/{second['id']}", json={"phone": NEW_CUSTOMER["phone"]}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "A customer with this phone number already exists"


def test_the_same_phone_and_email_are_blocked_for_another_owner(client, stores):
    """Uniqueness is global: no two customers anywhere may share contact details."""
    # Owner 2's seeded row already holds a distinct phone/email; give it owner 1's
    # own details to prove the collision fires across owners.
    stores["customers"][1]["email"] = "ada@example.com"

    response = client.post(
        f"{API}/customers",
        json={"name": "Ada Clone", "email": "ada@example.com", "phone": "9111111111"},
    )
    assert response.status_code == 409  # email taken by another owner's customer

    phone_taken = client.post(
        f"{API}/customers",
        json={"name": "Phone Clone", "email": "fresh@example.com", "phone": "9000000000"},
    )
    assert phone_taken.status_code == 409  # phone taken by another owner's customer

    # Distinct contact details are still fine.
    fresh = client.post(f"{API}/customers", json=NEW_CUSTOMER)
    assert fresh.status_code == 201


def test_customer_write_that_stores_nothing_is_a_server_error(client, empty_writes):
    response = client.post(f"{API}/customers", json=NEW_CUSTOMER)

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to create customer"


def test_unexpected_storage_failures_are_not_swallowed(client, write_error):
    """Only recognised failures get a friendly status; the rest must surface."""
    write_error(RuntimeError("connection reset by peer"))

    with pytest.raises(RuntimeError, match="connection reset"):
        client.post(f"{API}/customers", json=NEW_CUSTOMER)

    with pytest.raises(RuntimeError, match="connection reset"):
        client.patch(f"{API}/customers/1", json={"name": "Ada Lovelace"})


def test_patching_with_no_fields_is_rejected(client):
    response = client.patch(f"{API}/customers/1", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == "No fields to update"
