"""Request/response models for the customers module."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

# Indian mobile numbers: exactly 10 digits, and the first digit must be 6–9
# (9xxxx, 8xxxx, 7xxxx, 6xxxx — never 0–5). Users may type the number bare or
# with the +91 prefix; both are normalised below to "+91XXXXXXXXXX".
PHONE_DIGITS_PATTERN = r"^(?:\+?91)?[6-9][0-9]{9}$"

#: The only supported country calling code (India).
DEFAULT_COUNTRY_CODE = "+91"


def normalize_phone(raw: object) -> object:
    """Store every mobile in the canonical form ``+91XXXXXXXXXX``.

    Accepts ``9876543210``, ``98765 43210``-style spacing, ``+919876543210``
    and ``91 98765 43210`` alike; the pattern check that follows guarantees
    the result is an Indian mobile, so this only has to squeeze separators
    out (and leave non-strings alone so they fail validation, not here).
    """
    if not isinstance(raw, str):
        return raw
    digits = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return f"{DEFAULT_COUNTRY_CODE}{digits}"


class CustomerCreate(BaseModel):
    """Request body for adding a customer."""

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str = Field(pattern=PHONE_DIGITS_PATTERN)

    @field_validator("phone", mode="before")
    @classmethod
    def _canonical_phone(cls, value: object) -> object:
        """Normalise (strip, +91 default) before the pattern check runs."""
        return normalize_phone(value)


class CustomerUpdate(BaseModel):
    """Request body for updating a customer (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, pattern=PHONE_DIGITS_PATTERN)
    is_active: bool | None = None
    plan: Literal["free", "paid"] | None = None
    auto_recharge: bool | None = None
    auto_recharge_amount: float | None = Field(default=None, ge=0)
    auto_recharge_minutes: int | None = Field(default=None, ge=0)

    @field_validator("phone", mode="before")
    @classmethod
    def _canonical_phone(cls, value: object) -> object:
        return normalize_phone(value)


class CustomerOut(BaseModel):
    """A customer as returned by the API."""

    id: int
    name: str
    email: str
    phone: str
    otp: str
    is_active: bool
    plan: Literal["free", "paid"]
    remaining_minutes: int
    auto_recharge: bool
    auto_recharge_amount: float
    auto_recharge_minutes: int
