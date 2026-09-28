"""Request/response models for the customer portal.

The portal is the customers' own login: they sign in with their **phone number
and the 6-digit OTP their owner generated for them**, and the API answers with
their own details only — never the owner's account, and never the OTP itself.
"""

from pydantic import BaseModel, Field, field_validator

from app.modules.customers.schemas import PHONE_DIGITS_PATTERN, normalize_phone

#: Indian mobiles only, same rule as the customers module (the portal looks the
#: customer up by phone, so both sides must speak the same shape).
PHONE_PATTERN = PHONE_DIGITS_PATTERN


class CustomerLoginRequest(BaseModel):
    """Request body for the phone + OTP sign-in."""

    phone: str = Field(pattern=PHONE_PATTERN, examples=["+91 98765 43210"])
    otp: str = Field(pattern=r"^[0-9]{6}$", examples=["123456"])

    @field_validator("phone", mode="before")
    @classmethod
    def _canonical_phone(cls, value: object) -> object:
        """Strip, default the +91 code and match the stored form before the pattern."""
        return normalize_phone(value)

    @field_validator("otp", mode="before")
    @classmethod
    def _strip_otp(cls, value: object) -> object:
        """Real users type padded values; trim before the pattern runs."""
        return value.strip() if isinstance(value, str) else value


class CustomerProfile(BaseModel):
    """Everything the signed-in customer may see about themselves.

    This is a deliberate allow-list: the owner's identity (`user_id`), the OTP,
    auto-recharge credentials and the audit columns never appear here.
    """

    id: int
    name: str
    email: str
    phone: str
    is_active: bool
    plan: str
    remaining_minutes: int
    free_minutes: int
    paid_minutes: int
