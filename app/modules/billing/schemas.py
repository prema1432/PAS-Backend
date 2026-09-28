"""Request/response models for the billing module."""

from typing import Literal

from pydantic import BaseModel, Field


class RechargeRequest(BaseModel):
    """Request body for recharging a customer's plan minutes."""

    minutes: int = Field(gt=0, le=1_000_000)
    amount: float = Field(default=0, ge=0, le=10_000_000)
    mode: Literal["auto", "manual"] = "manual"
    currency: str = Field(default="INR", min_length=3, max_length=3)
    reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)


class PaymentUpdate(BaseModel):
    """Editable fields of a payment row (owner-side correction from the dashboard)."""

    amount: float | None = Field(default=None, ge=0, le=10_000_000)
    minutes: int | None = Field(default=None, gt=0, le=1_000_000)
    mode: Literal["auto", "manual"] | None = None
    status: str | None = Field(default=None, min_length=2, max_length=20)
    reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)


class PaymentOut(BaseModel):
    """A payment/recharge record."""

    id: int
    customer_id: int
    amount: float
    currency: str
    minutes: int
    mode: Literal["auto", "manual"]
    status: str
    reference: str | None = None
    note: str | None = None
    created_at: str | None = None
