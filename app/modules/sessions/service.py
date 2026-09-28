"""Sessions service: owner-scoped session reads.

Ending a session is a read-modify-write across two modules (charge the customer
balance, maybe auto-recharge), so the lookup lives here rather than in the
router and the mutation logic lives in the billing service.
"""

SESSIONS = "customer_sessions"
LIMIT = 500


def get_session(client, user_id: str, session_id: int) -> dict | None:
    """Fetch one owner-scoped session (``None`` when it does not exist)."""
    rows = (
        client.table(SESSIONS)
        .select("*")
        .eq("id", session_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None
