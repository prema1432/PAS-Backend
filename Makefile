PORT ?= 8000
HOST ?= 127.0.0.1

.PHONY: help dev run kill-port sync clean

help:
	@echo "Available commands:"
	@echo "  make dev        - Kill port $(PORT) if in use and start FastAPI server with auto-reload"
	@echo "  make run        - Kill port $(PORT) if in use and start FastAPI server (production mode)"
	@echo "  make kill-port  - Kill any process currently using port $(PORT)"
	@echo "  make sync       - Sync dependencies using uv"

kill-port:
	@echo "Freeing port $(PORT) if occupied..."
	@-lsof -ti tcp:$(PORT) | xargs kill -9 2>/dev/null || true

dev: kill-port
	@echo "Starting development server on http://$(HOST):$(PORT)..."
	uv run uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

run: kill-port
	@echo "Starting server on http://$(HOST):$(PORT)..."
	uv run uvicorn app.main:app --host $(HOST) --port $(PORT)

sync:
	uv sync
