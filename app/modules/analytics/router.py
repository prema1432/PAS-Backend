"""Analytics routes: dashboards for customers, login activity and billing."""

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_current_client, get_current_user
from app.modules.analytics.service import summarize
from app.modules.billing.service import CUSTOMERS, PAYMENTS, summarize_billing

router = APIRouter(prefix="/analytics", tags=["analytics"])

SESSIONS = "customer_sessions"
EVENT_LIMIT = 1000
CUSTOMER_LIMIT = 1000


@router.get("/summary")
def summary(
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
    window_days: int = Query(default=14, ge=1, le=90),
) -> dict:
    """Location-wise login analytics plus customer dashboard figures."""
    customers = (
        client.table(CUSTOMERS)
        .select("*")
        .eq("user_id", user["id"])
        .limit(CUSTOMER_LIMIT)
        .execute()
        .data
    )
    events = (
        client.table("login_events")
        .select("*")
        .eq("user_id", user["id"])
        .order("id", desc=True)
        .limit(EVENT_LIMIT)
        .execute()
        .data
    )
    payments = (
        client.table(PAYMENTS)
        .select("*")
        .eq("user_id", user["id"])
        .order("id", desc=True)
        .limit(EVENT_LIMIT)
        .execute()
        .data
    )
    sessions = (
        client.table(SESSIONS)
        .select("*")
        .eq("user_id", user["id"])
        .order("id", desc=True)
        .limit(EVENT_LIMIT)
        .execute()
        .data
    )

    payload = summarize(customers, events, window_days=window_days)
    # Money, minutes and per-customer session counts live with the dashboard
    # figures so the Overview tab needs a single request.
    payload["billing"] = summarize_billing(customers, payments, sessions, window_days=window_days)
    return payload
