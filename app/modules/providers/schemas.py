"""Request/response models for the providers module (providers and models)."""

from typing import Literal

from pydantic import BaseModel, Field

# Slug for a provider, e.g. "openrouter".
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,40}$"


class ProviderCreate(BaseModel):
    """Request body for registering an LLM provider."""

    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(pattern=SLUG_PATTERN)
    base_url: str | None = Field(default=None, max_length=300)
    is_free: bool = True
    is_paid: bool = False
    preferred_tier: Literal["free", "paid"] = "free"
    priority: int = Field(default=100, ge=0, le=10_000)


class ProviderUpdate(BaseModel):
    """Request body for updating a provider (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, max_length=300)
    is_free: bool | None = None
    is_paid: bool | None = None
    preferred_tier: Literal["free", "paid"] | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None


class ModelOut(BaseModel):
    """A provider model as returned by the API."""

    id: int
    provider_id: int
    name: str
    is_free: bool
    is_paid: bool
    context_window: int | None
    is_active: bool


class ProviderOut(BaseModel):
    """A provider as returned by the API, with its models and key count."""

    id: int
    name: str
    slug: str
    base_url: str | None
    is_free: bool
    is_paid: bool
    preferred_tier: Literal["free", "paid"]
    priority: int
    is_active: bool
    models: list[ModelOut] = []
    key_count: int = 0


class ModelCreate(BaseModel):
    """Request body for adding a model to a provider."""

    name: str = Field(min_length=1, max_length=160)
    is_free: bool = True
    is_paid: bool = False
    context_window: int | None = Field(default=None, gt=0)


class ModelUpdate(BaseModel):
    """Request body for updating a provider model (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    is_free: bool | None = None
    is_paid: bool | None = None
    context_window: int | None = Field(default=None, gt=0)
    is_active: bool | None = None
