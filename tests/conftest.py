"""Shared pytest fixtures."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.supabase_client import get_supabase_client


class FakeResponse:
    """Stand-in for supabase PostgrestAPIResponse."""

    def __init__(self, data):
        self.data = data


class FakeTable:
    """In-memory stand-in for the supabase `todos` table builder."""

    def __init__(self, todos: list):
        self._todos = todos
        self._filters: dict = {}
        self._payload: dict | None = None

    def select(self, *_):
        return self

    def order(self, *_):
        return self

    def insert(self, payload: dict):
        self._payload = payload
        return self

    def update(self, payload: dict):
        self._payload = payload
        return self

    def delete(self):
        self._payload = {"__delete__": True}
        return self

    def eq(self, column: str, value):
        self._filters[column] = value
        return self

    def execute(self):
        if self._payload and self._payload.get("__delete__"):
            before = len(self._todos)
            self._todos[:] = [t for t in self._todos if t["id"] != self._filters.get("id")]
            return FakeResponse(self._todos[: before - len(self._todos)])
        if self._payload and self._filters:
            updated = []
            for todo in self._todos:
                if todo["id"] == self._filters.get("id"):
                    todo.update(self._payload)
                    updated.append(todo)
            return FakeResponse(updated)
        if self._payload:  # insert
            todo = {"id": max((t["id"] for t in self._todos), default=0) + 1, **self._payload}
            self._todos.append(todo)
            return FakeResponse([todo])
        # select
        if "id" in self._filters:
            return FakeResponse([t for t in self._todos if t["id"] == self._filters["id"]])
        return FakeResponse(list(self._todos))


class FakeSupabaseClient:
    """Stand-in for the supabase Client: `.table(name)` returns a builder."""

    def __init__(self, todos: list):
        self._todos = todos

    def table(self, name: str) -> FakeTable:
        return FakeTable(self._todos)


@pytest.fixture()
def fake_todos():
    """Seed data shared by the fake table and assertions."""
    return [
        {"id": 1, "title": "First", "completed": False},
        {"id": 2, "title": "Second", "completed": True},
    ]


@pytest.fixture()
def client(monkeypatch, fake_todos):
    """TestClient with get_supabase_client overridden to the in-memory fake."""
    client_stub = FakeSupabaseClient(fake_todos)

    def fake_get_client():
        return client_stub

    app.dependency_overrides[get_supabase_client] = fake_get_client
    yield TestClient(app)
    app.dependency_overrides.pop(get_supabase_client, None)
