"""Audit-log routes: who changed what, and when."""

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import get_current_client, get_current_user
from app.modules.audit_logs.schemas import AuditLogOut
from app.modules.audit_logs.service import TABLE, public_log

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])

ROW_LIMIT = 500


@router.get("")
def list_audit_logs(
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
    table_name: str | None = Query(default=None, max_length=40),
    action: str | None = Query(default=None, pattern="^(insert|update|delete)$"),
    row_id: str | None = Query(default=None, max_length=40),
) -> list[AuditLogOut]:
    """List the signed-in user's audit trail, newest first, optionally filtered."""
    query = (
        client.table(TABLE)
        .select("*")
        .eq("user_id", user["id"])
        .order("id", desc=True)
        .limit(ROW_LIMIT)
    )
    if table_name:
        query = query.eq("table_name", table_name)
    if action:
        query = query.eq("action", action)
    if row_id:
        query = query.eq("row_id", row_id)
    return [public_log(row) for row in query.execute().data]
