"""Customers service: owner-scoped customer lookups.

The customers table is the hub of the domain — sessions, recharges and billing
all need to read a customer row — so the lookup is shared through this module
instead of being re-implemented in each router. Every query is scoped to the
signed-in user; RLS enforces the same rule in the database.
"""

TABLE = "customers"


def get_customer(client, user_id: str, customer_id: int) -> dict | None:
    """Fetch one owner-scoped customer (``None`` when it does not exist)."""
    rows = (
        client.table(TABLE)
        .select("*")
        .eq("id", customer_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None
