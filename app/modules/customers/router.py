"""Customers routes: CRUD, active toggle, plan change, OTP regeneration and
plan recharges.

Every row is scoped to the signed-in user.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_current_client, get_current_user
from app.core.security.otp import generate_otp
from app.modules.billing.schemas import RechargeRequest
from app.modules.billing.service import apply_recharge
from app.modules.customers.schemas import CustomerCreate, CustomerUpdate
from app.modules.customers.service import TABLE, get_customer

router = APIRouter(prefix="/customers", tags=["customers"])


def _own_rows(client, user_id: str):
    """Base query scoped to the signed-in user."""
    return client.table(TABLE).select("*").eq("user_id", user_id)


@router.get("")
def list_customers(
    user: dict = Depends(get_current_user), client=Depends(get_current_client)
) -> list:
    """List the signed-in user's customers (newest first)."""
    response = _own_rows(client, user["id"]).order("id", desc=True).execute()
    return response.data


@router.post("", status_code=201)
def create_customer(
    payload: CustomerCreate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Add a customer with a freshly generated 6-digit OTP."""
    row = payload.model_dump() | {
        "user_id": user["id"],
        "otp": generate_otp(),
        "is_active": True,
        "plan": "free",
    }
    try:
        response = client.table(TABLE).insert(row).execute()
    except Exception as exc:  # unique violation on (user_id, email) or (user_id, phone)
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            if "phone" in str(exc).lower():
                raise HTTPException(
                    status_code=409, detail="A customer with this phone number already exists"
                ) from exc
            raise HTTPException(
                status_code=409, detail="A customer with this email already exists"
            ) from exc
        raise
    if not response.data:
        raise HTTPException(status_code=500, detail="Failed to create customer")
    return response.data[0]


@router.patch("/{customer_id}")
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Update a customer's details, active state or plan."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        response = (
            client.table(TABLE)
            .update(updates)
            .eq("id", customer_id)
            .eq("user_id", user["id"])
            .execute()
        )
    except Exception as exc:  # unique violation on (user_id, email) or (user_id, phone)
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            detail = (
                "A customer with this phone number already exists"
                if "phone" in str(exc).lower()
                else "A customer with this email already exists"
            )
            raise HTTPException(status_code=409, detail=detail) from exc
        raise
    if not response.data:
        raise HTTPException(status_code=404, detail="Customer not found")
    return response.data[0]


@router.post("/{customer_id}/otp")
def regenerate_otp(
    customer_id: int,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Issue a new 6-digit OTP for a customer."""
    response = (
        client.table(TABLE)
        .update({"otp": generate_otp()})
        .eq("id", customer_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Customer not found")
    return response.data[0]


@router.post("/{customer_id}/recharge")
def recharge_customer(
    customer_id: int,
    payload: RechargeRequest,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Recharge a customer's plan: record the payment and add its minutes."""
    customer = get_customer(client, user["id"], customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return apply_recharge(
        client,
        user["id"],
        customer,
        minutes=payload.minutes,
        amount=payload.amount,
        mode=payload.mode,
        currency=payload.currency,
        reference=payload.reference,
        note=payload.note,
    )


@router.delete("/{customer_id}", status_code=204)
def delete_customer(
    customer_id: int,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> None:
    """Delete a customer."""
    response = (
        client.table(TABLE).delete().eq("id", customer_id).eq("user_id", user["id"]).execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Customer not found")
