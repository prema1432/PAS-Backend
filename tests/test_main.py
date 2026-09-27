"""Smoke tests for core endpoints and the todos CRUD (Supabase stubbed)."""


def test_root_serves_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "PAS Backend" in response.text
    assert "<script" in response.text


def test_info(client):
    response = client.get("/info")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "PAS Backend"
    assert body["status"] == "ok"
    assert body["docs"] == "/docs"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"


def test_read_item(client):
    response = client.get("/items/7?q=hello")
    assert response.status_code == 200
    assert response.json() == {"item_id": 7, "q": "hello"}


def test_list_todos(client, fake_todos):
    response = client.get("/todos")
    assert response.status_code == 200
    assert response.json() == fake_todos


def test_create_todo(client, fake_todos):
    response = client.post("/todos", json={"title": "New task"})
    assert response.status_code == 201
    created = response.json()
    assert created["title"] == "New task"
    assert created["completed"] is False
    assert len(fake_todos) == 3


def test_create_todo_rejects_empty_title(client):
    response = client.post("/todos", json={"title": ""})
    assert response.status_code == 422


def test_update_todo(client, fake_todos):
    response = client.patch("/todos/1", json={"completed": True})
    assert response.status_code == 200
    assert response.json()["completed"] is True


def test_update_todo_not_found(client):
    response = client.patch("/todos/999", json={"completed": True})
    assert response.status_code == 404


def test_update_todo_no_fields(client):
    response = client.patch("/todos/1", json={})
    assert response.status_code == 400


def test_delete_todo(client, fake_todos):
    response = client.delete("/todos/2")
    assert response.status_code == 204
    assert len(fake_todos) == 1


def test_delete_todo_not_found(client):
    response = client.delete("/todos/999")
    assert response.status_code == 404
