"""Request/response models for the audit-log listing."""

from typing import Literal

from pydantic import BaseModel, Field


class AuditLogOut(BaseModel):
    """One entry of the audit trail."""

    id: int
    table_name: str
    row_id: str
    action: Literal["insert", "update", "delete"]
    changed_fields: list[str] = Field(default_factory=list)
    created_at: str | None = None
