"""Per-customer sessions.

Starting a session requires an active customer with time left. Ending one charges
``minutes_used`` against the balance and, when the customer has auto-recharge
enabled and the balance hits zero, tops them up automatically.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.clock import now_iso
from app.core.dependencies import get_current_client, get_current_user
from app.modules.billing.service import auto_recharge_if_needed, set_balance
from app.modules.customers.service import get_customer
from app.modules.sessions.schemas import SessionCreate, SessionEndRequest
from app.modules.sessions.service import LIMIT, SESSIONS, get_session

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("")
def list_sessions(
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
    customer_id: int | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(active|ended)$"),
) -> list:
    """List the signed-in user's customer sessions (newest first)."""
    rows = (
        client.table(SESSIONS)
        .select("*")
        .eq("user_id", user["id"])
        .order("id", desc=True)
        .limit(LIMIT)
        .execute()
        .data
    )
    if customer_id is not None:
        rows = [row for row in rows if row.get("customer_id") == customer_id]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    return rows


@router.post("", status_code=201)
def start_session(
    payload: SessionCreate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Start a session for a customer that still has time left."""
    customer = get_customer(client, user["id"], payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer.get("is_active"):
        raise HTTPException(status_code=400, detail="Customer is inactive")
    if int(customer.get("remaining_minutes") or 0) <= 0:
        raise HTTPException(status_code=402, detail="Customer has no remaining minutes")

    row = {
        "user_id": user["id"],
        "customer_id": payload.customer_id,
        "provider_id": payload.provider_id,
        "model": payload.model,
        "status": "active",
        "minutes_used": 0,
        "started_at": now_iso(),
    }
    response = client.table(SESSIONS).insert(row).execute()
    if not response.data:
        raise HTTPException(status_code=500, detail="Failed to start session")
    return response.data[0]


@router.post("/{session_id}/end")
def end_session(
    session_id: int,
    payload: SessionEndRequest,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """End a session, charge its minutes and auto-recharge if needed."""
    session = get_session(client, user["id"], session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("status") == "ended":
        raise HTTPException(status_code=400, detail="Session already ended")

    customer = get_customer(client, user["id"], session["customer_id"])
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    updated = (
        client.table(SESSIONS)
        .update({"status": "ended", "ended_at": now_iso(), "minutes_used": payload.minutes_used})
        .eq("id", session_id)
        .eq("user_id", user["id"])
        .execute()
        .data
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")

    remaining = max(0, int(customer.get("remaining_minutes") or 0) - payload.minutes_used)
    charged = set_balance(client, user["id"], customer["id"], remaining) or customer
    auto_recharge = auto_recharge_if_needed(client, user["id"], charged)

    return {
        "session": updated[0],
        "customer": auto_recharge["customer"] if auto_recharge else charged,
        "auto_recharge": auto_recharge,
    }
