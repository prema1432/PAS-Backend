"""Audit-log service: owner-scoped reads over the append-only trail.

The table itself is trigger-fed (`write_audit_log` in the migration); this
service only reads it, so the trail cannot be edited from the API.
"""

TABLE = "audit_logs"


def public_log(row: dict) -> dict:
    """Allow-list projection of an audit row (never the raw record)."""
    return {
        "id": row.get("id"),
        "table_name": row.get("table_name"),
        "row_id": row.get("row_id"),
        "action": row.get("action"),
        "changed_fields": row.get("changed_fields") or [],
        "created_at": row.get("created_at"),
    }
