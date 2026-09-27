"""Todos CRUD routes backed by Supabase."""

from fastapi import APIRouter, Depends, HTTPException

from app.schemas import TodoCreate, TodoUpdate
from app.services.supabase_client import get_supabase_client

router = APIRouter()


@router.get("")
def list_todos(client=Depends(get_supabase_client)) -> list:
    """List todos from the Supabase `todos` table."""
    response = client.table("todos").select("*").order("id").execute()
    return response.data


@router.post("", status_code=201)
def create_todo(payload: TodoCreate, client=Depends(get_supabase_client)) -> dict:
    """Create a todo in the Supabase `todos` table."""
    response = client.table("todos").insert(payload.model_dump()).execute()
    if not response.data:
        raise HTTPException(status_code=500, detail="Failed to create todo")
    return response.data[0]


@router.patch("/{todo_id}")
def update_todo(todo_id: int, payload: TodoUpdate, client=Depends(get_supabase_client)) -> dict:
    """Update a todo in the Supabase `todos` table."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    response = client.table("todos").update(updates).eq("id", todo_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Todo not found")
    return response.data[0]


@router.delete("/{todo_id}", status_code=204)
def delete_todo(todo_id: int, client=Depends(get_supabase_client)) -> None:
    """Delete a todo from the Supabase `todos` table."""
    response = client.table("todos").delete().eq("id", todo_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Todo not found")
