"""Billing endpoints: recharges, the payments ledger and customer sessions."""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix

TEST_USER_ID = "test-user-0001"


def _seed_session(stores, session_id, minutes_used, customer_id=1, status="ended"):
    stores["customer_sessions"].append(
        {
            "id": session_id,
            "user_id": TEST_USER_ID,
            "customer_id": customer_id,
            "provider_id": None,
            "model": None,
            "status": status,
            "minutes_used": minutes_used,
            "started_at": "2026-09-27T09:00:00+00:00",
            "created_at": "2026-09-27T09:00:00+00:00",
        }
    )


# --- recharges -------------------------------------------------------------
def test_recharge_adds_minutes_and_records_a_payment(client, stores):
    response = client.post(
        f"{API}/customers/1/recharge", json={"minutes": 60, "amount": 499, "mode": "manual"}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["customer"]["remaining_minutes"] == 180  # 120 seeded + 60
    assert body["payment"]["minutes"] == 60
    assert body["payment"]["amount"] == 499
    assert body["payment"]["mode"] == "manual"
    assert body["payment"]["status"] == "paid"
    assert len(stores["payments"]) == 1


def test_recharge_can_be_tagged_automatic(client, stores):
    client.post(f"{API}/customers/1/recharge", json={"minutes": 30, "amount": 0, "mode": "auto"})
    assert stores["payments"][0]["mode"] == "auto"


def test_recharge_validation(client):
    assert client.post(f"{API}/customers/1/recharge", json={"minutes": 0}).status_code == 422
    assert client.post(f"{API}/customers/1/recharge", json={"minutes": -5}).status_code == 422
    assert (
        client.post(f"{API}/customers/1/recharge", json={"minutes": 10, "amount": -1}).status_code
        == 422
    )


def test_recharge_rejects_unknown_and_unowned_customers(client):
    assert client.post(f"{API}/customers/999/recharge", json={"minutes": 10}).status_code == 404
    # Customer 2 belongs to another account.
    assert client.post(f"{API}/customers/2/recharge", json={"minutes": 10}).status_code == 404


# --- payments ledger -------------------------------------------------------
def test_payments_list_includes_the_customer_name(client):
    client.post(f"{API}/customers/1/recharge", json={"minutes": 30, "amount": 100})

    rows = client.get(f"{API}/billing/payments").json()
    assert len(rows) == 1
    assert rows[0]["customer_name"] == "Ada Lovelace"


def test_payments_can_be_filtered_by_mode_and_customer(client):
    client.post(
        f"{API}/customers/1/recharge", json={"minutes": 30, "amount": 100, "mode": "manual"}
    )
    client.post(f"{API}/customers/1/recharge", json={"minutes": 10, "amount": 0, "mode": "auto"})

    assert len(client.get(f"{API}/billing/payments?mode=manual").json()) == 1
    assert len(client.get(f"{API}/billing/payments?mode=auto").json()) == 1
    assert len(client.get(f"{API}/billing/payments?customer_id=1").json()) == 2
    assert client.get(f"{API}/billing/payments?customer_id=99").json() == []
    assert client.get(f"{API}/billing/payments?mode=nonsense").status_code == 422


# --- editing payments ------------------------------------------------------
def test_payment_can_be_edited_from_the_dashboard(client, stores):
    payment = client.post(
        f"{API}/customers/1/recharge", json={"minutes": 30, "amount": 100}
    ).json()["payment"]

    response = client.patch(
        f"{API}/billing/payments/{payment['id']}",
        json={"amount": 150, "note": "corrected by cash", "status": "paid"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == 150
    assert body["note"] == "corrected by cash"
    assert body["customer_name"] == "Ada Lovelace"
    assert stores["payments"][0]["amount"] == 150


def test_payment_edit_ignores_unknown_fields_and_empty_bodies(client):
    payment = client.post(f"{API}/customers/1/recharge", json={"minutes": 10, "amount": 5}).json()[
        "payment"
    ]

    # Unknown/immutable fields are ignored, so an only-unknown patch is a 400.
    assert (
        client.patch(f"{API}/billing/payments/{payment['id']}", json={"customer_id": 2}).status_code
        == 400
    )
    assert (
        client.patch(f"{API}/billing/payments/{payment['id']}", json={"status": "refunded"})
    ).status_code == 200


def test_payment_edit_rejects_invalid_values(client):
    payment = client.post(f"{API}/customers/1/recharge", json={"minutes": 10, "amount": 5}).json()[
        "payment"
    ]

    assert (
        client.patch(f"{API}/billing/payments/{payment['id']}", json={"amount": -1}).status_code
        == 422
    )
    assert (
        client.patch(f"{API}/billing/payments/{payment['id']}", json={"minutes": 0}).status_code
        == 422
    )
    assert (
        client.patch(f"{API}/billing/payments/{payment['id']}", json={"mode": "nonsense"})
    ).status_code == 422


def test_payment_edit_is_scoped_to_the_owner(client):
    assert client.patch(f"{API}/billing/payments/999", json={"amount": 5}).status_code == 404
    assert (
        client.patch(f"{API}/billing/payments/999", json={"amount": 5}).json()["detail"]
        == "Payment not found"
    )


def test_payment_edit_requires_authentication():
    anonymous = TestClient(app)
    assert anonymous.patch(f"{API}/billing/payments/1", json={"amount": 5}).status_code == 401


# --- summary ---------------------------------------------------------------
def test_billing_summary_reflects_money_minutes_and_sessions(client, stores):
    _seed_session(stores, 1, 20, customer_id=1)
    client.post(f"{API}/customers/1/recharge", json={"minutes": 60, "amount": 250})

    summary = client.get(f"{API}/billing/summary").json()
    assert summary["totals"]["total_amount"] == 250.0
    assert summary["totals"]["total_minutes_recharged"] == 60
    assert summary["totals"]["total_sessions"] == 1
    assert summary["totals"]["manual_payments"] == 1

    ada = next(row for row in summary["by_customer"] if row["name"] == "Ada Lovelace")
    assert ada["session_count"] == 1
    assert ada["minutes_used"] == 20
    assert ada["remaining_minutes"] == 180
    assert ada["amount"] == 250.0


def test_billing_summary_window_is_bounded(client):
    assert client.get(f"{API}/billing/summary?window_days=0").status_code == 422
    assert client.get(f"{API}/billing/summary?window_days=91").status_code == 422
    assert len(client.get(f"{API}/billing/summary?window_days=3").json()["by_day"]) == 3


# --- sessions --------------------------------------------------------------
def test_session_lifecycle_charges_minutes(client):
    started = client.post(f"{API}/sessions", json={"customer_id": 1, "model": "openai/gpt-4o-mini"})
    assert started.status_code == 201
    session = started.json()
    assert session["status"] == "active"
    assert session["minutes_used"] == 0

    ended = client.post(f"{API}/sessions/{session['id']}/end", json={"minutes_used": 25})
    assert ended.status_code == 200
    body = ended.json()
    assert body["session"]["status"] == "ended"
    assert body["session"]["minutes_used"] == 25
    assert body["customer"]["remaining_minutes"] == 95  # 120 - 25
    assert body["auto_recharge"] is None


def test_a_session_cannot_be_ended_twice(client):
    session_id = client.post(f"{API}/sessions", json={"customer_id": 1}).json()["id"]
    assert (
        client.post(f"{API}/sessions/{session_id}/end", json={"minutes_used": 1}).status_code == 200
    )
    assert (
        client.post(f"{API}/sessions/{session_id}/end", json={"minutes_used": 1}).status_code == 400
    )


def test_balance_never_goes_negative(client):
    session_id = client.post(f"{API}/sessions", json={"customer_id": 1}).json()["id"]
    body = client.post(f"{API}/sessions/{session_id}/end", json={"minutes_used": 10_000}).json()
    assert body["customer"]["remaining_minutes"] == 0


def test_session_without_minutes_is_payment_required(client):
    customer = client.post(
        f"{API}/customers",
        json={"name": "Zero Balance", "email": "zero@example.com", "phone": "90000 00001"},
    ).json()

    response = client.post(f"{API}/sessions", json={"customer_id": customer["id"]})
    assert response.status_code == 402


def test_inactive_customer_cannot_start_a_session(client):
    client.patch(f"{API}/customers/1", json={"is_active": False})
    assert client.post(f"{API}/sessions", json={"customer_id": 1}).status_code == 400


def test_sessions_are_scoped_to_the_owner(client):
    assert client.post(f"{API}/sessions", json={"customer_id": 2}).status_code == 404
    assert client.post(f"{API}/sessions", json={"customer_id": 999}).status_code == 404

    session_id = client.post(f"{API}/sessions", json={"customer_id": 1}).json()["id"]
    assert client.get(f"{API}/sessions?customer_id=2").json() == []
    rows = client.get(f"{API}/sessions?customer_id=1").json()
    assert [row["id"] for row in rows] == [session_id]


def test_starting_a_session_that_did_not_land_is_a_server_error(client, empty_writes):
    """The customer has minutes, but the insert returned no row: do not claim success."""
    response = client.post(f"{API}/sessions", json={"customer_id": 1})

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to start session"


def test_ending_an_unknown_session_is_404(client):
    response = client.post(f"{API}/sessions/9999/end", json={"minutes_used": 5})

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_ending_a_session_whose_customer_is_gone_is_404(client, stores):
    """The session survived a deleted customer; there is nothing to charge."""
    _seed_session(stores, 7, 0, customer_id=999, status="active")

    response = client.post(f"{API}/sessions/7/end", json={"minutes_used": 5})

    assert response.status_code == 404
    assert response.json()["detail"] == "Customer not found"


def test_ending_a_session_that_did_not_land_is_404(client, stores, empty_writes):
    """The update matched no row (RLS filtered it, or it vanished) — report it."""
    _seed_session(stores, 8, 0, customer_id=1, status="active")

    response = client.post(f"{API}/sessions/8/end", json={"minutes_used": 5})

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_sessions_can_be_filtered_by_status(client, stores):
    _seed_session(stores, 5, 12, status="ended")
    client.post(f"{API}/sessions", json={"customer_id": 1})

    ended = client.get(f"{API}/sessions?status=ended").json()
    active = client.get(f"{API}/sessions?status=active").json()
    assert [row["id"] for row in ended] == [5]
    assert len(active) == 1
    assert active[0]["status"] == "active"


# --- automatic recharge ----------------------------------------------------
def test_auto_recharge_tops_up_when_the_balance_hits_zero(client, stores):
    client.patch(
        f"{API}/customers/1",
        json={"auto_recharge": True, "auto_recharge_minutes": 60, "auto_recharge_amount": 499},
    )
    session_id = client.post(f"{API}/sessions", json={"customer_id": 1}).json()["id"]
    body = client.post(f"{API}/sessions/{session_id}/end", json={"minutes_used": 120}).json()

    assert body["auto_recharge"] is not None
    assert body["customer"]["remaining_minutes"] == 60

    auto = [payment for payment in stores["payments"] if payment["mode"] == "auto"]
    assert len(auto) == 1
    assert auto[0]["minutes"] == 60
    assert auto[0]["amount"] == 499


def test_auto_recharge_stays_off_while_minutes_remain(client, stores):
    client.patch(f"{API}/customers/1", json={"auto_recharge": True, "auto_recharge_minutes": 60})
    session_id = client.post(f"{API}/sessions", json={"customer_id": 1}).json()["id"]
    body = client.post(f"{API}/sessions/{session_id}/end", json={"minutes_used": 10}).json()

    assert body["auto_recharge"] is None
    assert body["customer"]["remaining_minutes"] == 110
    assert stores["payments"] == []


def test_auto_recharge_needs_a_configured_top_up(client, stores):
    client.patch(f"{API}/customers/1", json={"auto_recharge": True, "auto_recharge_minutes": 0})
    session_id = client.post(f"{API}/sessions", json={"customer_id": 1}).json()["id"]
    body = client.post(f"{API}/sessions/{session_id}/end", json={"minutes_used": 500}).json()

    assert body["auto_recharge"] is None
    assert body["customer"]["remaining_minutes"] == 0
    assert stores["payments"] == []


def test_auto_recharge_settings_are_updatable(client):
    patched = client.patch(
        f"{API}/customers/1",
        json={"auto_recharge": True, "auto_recharge_minutes": 30, "auto_recharge_amount": 199},
    ).json()
    assert patched["auto_recharge"] is True
    assert patched["auto_recharge_minutes"] == 30
    assert patched["auto_recharge_amount"] == 199


# --- authentication --------------------------------------------------------
def test_billing_routes_require_authentication():
    anonymous = TestClient(app)
    assert anonymous.get(f"{API}/billing/summary").status_code == 401
    assert anonymous.get(f"{API}/billing/payments").status_code == 401
    assert anonymous.get(f"{API}/sessions").status_code == 401
    assert anonymous.post(f"{API}/sessions", json={"customer_id": 1}).status_code == 401
    assert anonymous.post(f"{API}/sessions/1/end", json={"minutes_used": 1}).status_code == 401
    assert anonymous.post(f"{API}/customers/1/recharge", json={"minutes": 10}).status_code == 401
