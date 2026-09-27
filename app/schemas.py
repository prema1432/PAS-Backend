"""Pydantic request/response schemas shared across routes."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Payload returned by the health check endpoint."""

    status: str
    supabase: str


class TodoCreate(BaseModel):
    """Request body for creating a todo."""

    title: str = Field(min_length=1, max_length=200)
    completed: bool = False


class TodoUpdate(BaseModel):
    """Request body for updating a todo."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    completed: bool | None = None
