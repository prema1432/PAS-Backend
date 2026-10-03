"""
Pydantic models for the Customer document and API schemas.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    self_ = "self"      # customer self-registered
    admin = "admin"     # created by an admin


class PaymentType(str, Enum):
    free = "free"
    paid = "paid"


# ---------------------------------------------------------------------------
# MongoDB document model (what gets stored / read from the DB)
# ---------------------------------------------------------------------------

class CustomerDocument(BaseModel):
    """Full customer record as stored in MongoDB."""

    phone_number: str                           # unique, indexed
    otp: Optional[str] = None                  # current 6-digit OTP (hashed or plain)
    otp_expires_at: Optional[datetime] = None  # when the OTP expires

    source: SourceType = SourceType.self_
    payment_type: PaymentType = PaymentType.free

    activation_date: Optional[datetime] = None
    created_by: Optional[str] = None           # phone / admin id
    updated_by: Optional[str] = None

    referral_code_used: Optional[str] = None   # referral code customer entered at signup
    referral_code_generated: Optional[str] = None  # this customer's own referral code

    # Subscription window
    time_remaining_seconds: int = 0            # seconds of access left
    time_expiry: Optional[datetime] = None     # absolute expiry timestamp

    last_login: Optional[datetime] = None
    login_session_id: Optional[str] = None
    device_id: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# API request / response schemas
# ---------------------------------------------------------------------------

class CustomerLoginRequest(BaseModel):
    """Single input field for the login endpoint."""
    phone_number: str = Field(
        ...,
        min_length=7,
        max_length=15,
        pattern=r"^\+?[0-9]{7,15}$",
        examples=["+919876543210"],
    )
    device_id: Optional[str] = Field(default=None, max_length=256)
    otp: Optional[str] = Field(
        default=None,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="Submit the 6-digit OTP received in the first step to get a JWT.",
    )


class CustomerLoginResponse(BaseModel):
    """
    Returned for both new and existing customers.

    - new customer  : otp is returned (send via SMS in production), jwt_token is None
                      until OTP is validated via a separate verify step *OR* you send
                      the token pre-auth (depends on product decision — here we send
                      it so the client can proceed immediately on new registration).
    - existing customer within validity: otp is returned for front-end to verify.
    - expired subscription: raises HTTP 403.
    """
    is_new_customer: bool
    message: str
    otp: str                        # 6-digit OTP (in production deliver via SMS, not API)
    jwt_token: Optional[str] = None # issued only when subscription is active or new


class OTPVerifyRequest(BaseModel):
    phone_number: str = Field(..., pattern=r"^\+?[0-9]{7,15}$")
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    device_id: Optional[str] = Field(default=None, max_length=256)


class OTPVerifyResponse(BaseModel):
    message: str
    jwt_token: str
