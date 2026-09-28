"""Audit-log endpoint tests: the read-only trail view."""

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix

TEST_USER_ID = "test-user-0001"


def _seed_log(stores, log_id, table, row_id, action, fields=None, user_id=TEST_USER_ID):
    stores["audit_logs"].append(
        {
            "id": log_id,
            "user_id": user_id,
            "table_name": table,
            "row_id": row_id,
            "action": action,
            "changed_fields": fields or [],
            "created_at": "2026-09-28T10:00:00+00:00",
        }
    )


def test_audit_logs_are_listed_newest_first(client, stores):
    _seed_log(stores, 1, "customers", "1", "insert")
    _seed_log(stores, 2, "customers", "1", "update", ["phone"])

    rows = client.get(f"{API}/audit-logs").json()

    assert [row["id"] for row in rows] == [2, 1]
    assert rows[0]["changed_fields"] == ["phone"]


def test_audit_logs_can_be_filtered(client, stores):
    _seed_log(stores, 1, "customers", "1", "insert")
    _seed_log(stores, 2, "payments", "7", "insert")
    _seed_log(stores, 3, "customers", "2", "delete")

    assert [r["id"] for r in client.get(f"{API}/audit-logs?table_name=customers").json()] == [3, 1]
    assert [r["id"] for r in client.get(f"{API}/audit-logs?action=insert").json()] == [2, 1]
    assert [r["id"] for r in client.get(f"{API}/audit-logs?row_id=7").json()] == [2]


def test_audit_log_filters_validate(client):
    assert client.get(f"{API}/audit-logs?action=nonsense").status_code == 422


def test_audit_logs_are_scoped_to_the_owner(client, stores):
    _seed_log(stores, 1, "customers", "1", "insert")
    _seed_log(stores, 2, "customers", "9", "insert", user_id="other-user")

    rows = client.get(f"{API}/audit-logs").json()

    assert [row["id"] for row in rows] == [1]


def test_audit_log_projection_is_an_allow_list(client, stores):
    """user_id and audit columns never leave the API."""
    _seed_log(stores, 1, "customers", "1", "insert")

    row = client.get(f"{API}/audit-logs").json()[0]

    assert set(row) == {"id", "table_name", "row_id", "action", "changed_fields", "created_at"}


def test_audit_logs_require_authentication():
    anonymous = TestClient(app)
    assert anonymous.get(f"{API}/audit-logs").status_code == 401
