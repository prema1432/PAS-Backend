"""Request/response models for the sessions module."""

from typing import Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    """Request body for starting a customer session."""

    customer_id: int
    provider_id: int | None = None
    model: str | None = Field(default=None, max_length=160)


class SessionEndRequest(BaseModel):
    """Request body for ending a customer session.

    ``minutes_used`` is charged against the customer's remaining balance.
    """

    minutes_used: int = Field(default=0, ge=0, le=1_000_000)


class SessionOut(BaseModel):
    """A customer session as returned by the API."""

    id: int
    customer_id: int
    provider_id: int | None
    model: str | None
    status: Literal["active", "ended"]
    minutes_used: int
    started_at: str | None
    ended_at: str | None
