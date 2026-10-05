"""
Pydantic models for the Customer document and API schemas.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def normalize_indian_phone(v: Any) -> str:
    """
    Validate and normalize Indian mobile phone numbers.
    - Strips whitespace, hyphens, dots, and parentheses.
    - Removes leading +91, 91, or 0.
    - Validates that the 10-digit number starts with 6, 7, 8, or 9 (rejects 0-5).
    - Returns standardized E.164 format: '+91XXXXXXXXXX'.
    """
    if not isinstance(v, str):
        raise ValueError("Phone number must be a string.")

    cleaned = re.sub(r"[\s\-\.\(\)]", "", v.strip())
    if cleaned.startswith("+91"):
        cleaned = cleaned[3:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    elif cleaned.startswith("0") and len(cleaned) == 11:
        cleaned = cleaned[1:]

    if not re.match(r"^[0-9]{10}$", cleaned):
        raise ValueError(
            "Invalid phone number format. Indian mobile numbers must be 10 digits (e.g. +919876543210 or 9876543210)."
        )

    if cleaned[0] not in ("6", "7", "8", "9"):
        raise ValueError(
            f"Invalid mobile number '{cleaned}'. Indian mobile numbers must start with 6, 7, 8, or 9 (not 1 to 5)."
        )

    return f"+91{cleaned}"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    self_ = "self"      # customer self-registered
    admin = "admin"     # created by an admin


class PaymentType(str, Enum):
    free = "free"
    paid = "paid"


class PaymentStatus(str, Enum):
    completed = "completed"
    pending = "pending"
    manual = "manual"
    failed = "failed"


class RechargeSource(str, Enum):
    manual = "manual"
    admin = "admin"
    gateway = "gateway"


# ---------------------------------------------------------------------------
# MongoDB document model (what gets stored / read from the DB)
# ---------------------------------------------------------------------------

class CustomerDocument(BaseModel):
    """Full customer record as stored in MongoDB."""

    phone_number: str                           # unique, indexed (format: +91XXXXXXXXXX)
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

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: Any) -> str:
        return normalize_indian_phone(v)


# ---------------------------------------------------------------------------
# API request / response schemas
# ---------------------------------------------------------------------------

class CustomerLoginRequest(BaseModel):
    """Single input field for the login endpoint."""
    phone_number: str = Field(
        ...,
        description="10-digit Indian mobile number (e.g. +919876543210 or 9876543210, must start with 6, 7, 8, or 9)",
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

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: Any) -> str:
        return normalize_indian_phone(v)


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
    phone_number: str = Field(
        ...,
        description="10-digit Indian mobile number (e.g. +919876543210 or 9876543210, must start with 6, 7, 8, or 9)",
        examples=["+919876543210"],
    )
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    device_id: Optional[str] = Field(default=None, max_length=256)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: Any) -> str:
        return normalize_indian_phone(v)


class OTPVerifyResponse(BaseModel):
    message: str
    jwt_token: str


class CustomerProfileResponse(BaseModel):
    """Response for GET /customer/me — everything except OTP fields."""
    phone_number: str
    source: SourceType
    payment_type: PaymentType
    activation_date: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    referral_code_used: Optional[str] = None
    referral_code_generated: Optional[str] = None
    time_remaining_seconds: int
    time_expiry: Optional[datetime] = None
    last_login: Optional[datetime] = None
    login_session_id: Optional[str] = None
    device_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RechargeDocument(BaseModel):
    """Recharge/Payment transaction document linked to a Customer."""
    customer_id: Optional[str] = None
    phone_number: str
    amount: float = 0.0
    time_delta_seconds: int = 0
    previous_time_remaining_seconds: int = 0
    new_time_remaining_seconds: int = 0
    previous_payment_type: str = "free"
    new_payment_type: str = "paid"
    source: str = "manual"
    payment_status: str = "completed"
    notes: Optional[str] = None
    created_by: Optional[str] = "admin"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: Any) -> str:
        return normalize_indian_phone(v)


class CustomerRechargeRequest(BaseModel):
    """Request payload for recharging / adjusting customer time and plan."""
    time_delta_seconds: int = Field(..., description="Seconds to add (+) or subtract (-)")
    amount: float = Field(default=0.0, ge=0.0, description="Payment amount (currency)")
    payment_type: PaymentType = Field(default=PaymentType.paid, description="Target payment plan (e.g. free to paid)")
    source: str = Field(default="manual", description="Payment source, e.g. manual")
    payment_status: str = Field(default="completed", description="Payment status, e.g. completed, manual")
    notes: Optional[str] = Field(default=None, max_length=500)
    created_by: Optional[str] = Field(default="admin", max_length=100)


class CustomerCreateRequest(BaseModel):
    """Payload for creating a new customer via admin."""
    phone_number: str = Field(
        ...,
        description="10-digit Indian mobile number (e.g. +919876543210 or 9876543210, must start with 6, 7, 8, or 9)",
        examples=["+919876543210"],
    )
    otp: Optional[str] = Field(default=None, max_length=6)
    source: SourceType = Field(default=SourceType.admin)
    payment_type: PaymentType = Field(default=PaymentType.free)
    time_remaining_seconds: int = Field(default=1800, ge=0)
    referral_code_generated: Optional[str] = None
    device_id: Optional[str] = None

    @field_validator("phone_number", mode="before")
    @classmethod
    def validate_phone(cls, v: Any) -> str:
        return normalize_indian_phone(v)


class CustomerUpdateRequest(BaseModel):
    """Payload for updating an existing customer via admin."""
    otp: Optional[str] = None
    source: Optional[SourceType] = None
    payment_type: Optional[PaymentType] = None
    time_remaining_seconds: Optional[int] = Field(default=None, ge=0)
    referral_code_generated: Optional[str] = None
    device_id: Optional[str] = None
    time_expiry: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Admin Models
# ---------------------------------------------------------------------------

class AdminRole(str, Enum):
    superadmin = "superadmin"
    admin = "admin"
    moderator = "moderator"


class AdminDocument(BaseModel):
    """Admin record stored in MongoDB 'admins' collection."""
    email: str                                  # unique, indexed
    password_hash: str                          # bcrypt hashed
    name: str = "Admin"
    role: AdminRole = AdminRole.admin
    is_active: bool = True
    last_login: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AdminLoginRequest(BaseModel):
    """Admin login request with email and password."""
    email: str = Field(..., description="Admin email address", examples=["admin@pas.com"])
    password: str = Field(..., min_length=4, description="Plaintext password to authenticate")


class AdminLoginResponse(BaseModel):
    """JWT response for authenticated admin."""
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    admin: dict


class AdminCreateRequest(BaseModel):
    """Payload to create an admin account."""
    email: str = Field(..., min_length=5, max_length=100)
    password: str = Field(..., min_length=6, description="Raw password to be crypto-hashed")
    name: str = Field(default="Admin", max_length=100)
    role: AdminRole = Field(default=AdminRole.admin)
    is_active: bool = True


class AdminUpdateRequest(BaseModel):
    """Payload to update an admin account."""
    name: Optional[str] = Field(default=None, max_length=100)
    role: Optional[AdminRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=6, description="Optional new password to hash")

