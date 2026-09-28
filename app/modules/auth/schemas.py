"""Request/response models for the auth module."""

from pydantic import BaseModel, EmailStr, Field


class AuthRequest(BaseModel):
    """Request body for sign-up and sign-in."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    """Public shape of an authenticated user.

    ``access_token_expires_at`` is the unix timestamp of the JWT's `exp` claim
    (None when it cannot be read). The client uses it to refresh just before the
    token lapses instead of waiting for a 401.
    """

    id: str
    email: str
    access_token_expires_at: int | None = None
