"""Billing routes: the payments ledger and the amount/minutes dashboard."""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_current_client, get_current_user
from app.modules.billing.schemas import PaymentUpdate
from app.modules.billing.service import CUSTOMERS, PAYMENTS, summarize_billing, update_payment

router = APIRouter(prefix="/billing", tags=["billing"])

SESSIONS = "customer_sessions"
ROW_LIMIT = 1000


def _fetch(client, user_id: str, table: str, newest_first: bool = True) -> list:
    """All owner-scoped rows of a table, newest first by default."""
    query = client.table(table).select("*").eq("user_id", user_id)
    if newest_first:
        query = query.order("id", desc=True)
    return query.limit(ROW_LIMIT).execute().data


@router.get("/summary")
def billing_summary(
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
    window_days: int = Query(default=14, ge=1, le=90),
) -> dict:
    """Totals, per-customer sessions/minutes and the revenue series."""
    return summarize_billing(
        _fetch(client, user["id"], CUSTOMERS, newest_first=False),
        _fetch(client, user["id"], PAYMENTS),
        _fetch(client, user["id"], SESSIONS),
        window_days=window_days,
    )


@router.get("/payments")
def list_payments(
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
    customer_id: int | None = Query(default=None),
    mode: str | None = Query(default=None, pattern="^(auto|manual)$"),
) -> list:
    """List payments user-wise (newest first), optionally filtered."""
    payments = _fetch(client, user["id"], PAYMENTS)
    if customer_id is not None:
        payments = [p for p in payments if p.get("customer_id") == customer_id]
    if mode:
        payments = [p for p in payments if p.get("mode") == mode]

    names = {
        customer.get("id"): customer.get("name")
        for customer in _fetch(client, user["id"], CUSTOMERS, newest_first=False)
    }
    for payment in payments:
        payment["customer_name"] = names.get(payment.get("customer_id"))
    return payments


@router.patch("/payments/{payment_id}")
def edit_payment(
    payment_id: int,
    payload: PaymentUpdate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Correct an editable field of a payment row (amount, minutes, mode, status, note)."""
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Nothing to update")

    updated = update_payment(client, user["id"], payment_id, changes)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Payment not found")

    names = {
        customer.get("id"): customer.get("name")
        for customer in _fetch(client, user["id"], CUSTOMERS, newest_first=False)
    }
    updated["customer_name"] = names.get(updated.get("customer_id"))
    return updated
