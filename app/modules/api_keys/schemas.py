"""Request/response models for the api_keys module."""

from pydantic import BaseModel, Field

from app.core.types import Tier


class ApiKeyCreate(BaseModel):
    """Request body for storing a provider API key.

    ``key`` is the only place the plaintext secret is ever accepted; it is
    encrypted before storage and never echoed back.
    """

    label: str = Field(min_length=1, max_length=60)
    key: str = Field(min_length=8, max_length=500)
    provider_id: int | None = None
    tier: Tier = "free"


class ApiKeyUpdate(BaseModel):
    """Request body for updating an API key's metadata (never its value)."""

    label: str | None = Field(default=None, min_length=1, max_length=60)
    provider_id: int | None = None
    tier: Tier | None = None
    is_active: bool | None = None


class ApiKeyOut(BaseModel):
    """An API key as returned by the API — the secret itself is never included."""

    id: int
    provider_id: int | None
    provider_name: str | None = None
    label: str
    key_masked: str
    tier: Tier
    is_active: bool
    last_used_at: str | None = None
    created_at: str | None = None
