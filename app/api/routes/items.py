"""Example items routes (kept as a minimal reference endpoint)."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/{item_id}")
def read_item(item_id: int, q: str | None = None) -> dict:
    """Example endpoint returning a single item."""
    return {"item_id": item_id, "q": q}
