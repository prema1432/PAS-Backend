# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Project Overview

PAS Backend is a FastAPI application that integrates with Supabase. It is designed for deployment to FastAPI Cloud. Python 3.12+.

## Commands

```bash
# Install dependencies (uv recommended)
uv venv --python 3.12
uv pip install -p .venv -e .

# Run the dev server (http://127.0.0.1:8000, docs at /docs)
fastapi dev

# Tests
.venv/bin/pytest

# Lint and format
ruff check --fix .
ruff format .

# Pre-commit hooks (first time) and run on all files
pre-commit install
pre-commit run --all-files

# Deploy to FastAPI Cloud
fastapi login    # once
fastapi deploy
```

## Architecture

Layered package layout:

- `app/main.py` — FastAPI app (`main:app` via root shim). Wires middleware, the Supabase-not-configured 503 handler, `/` (service info), `/health`, and routers.
- `app/config.py` — `pydantic-settings` `Settings` class reading env vars and a local `.env` file. Access via the module-level `settings` singleton.
- `app/schemas.py` — shared Pydantic request/response models (`TodoCreate`, `TodoUpdate`, `HealthResponse`).
- `app/api/routes/` — one router module per resource (`items.py`, `todos.py`), mounted with prefixes in `app/main.py`.
- `app/services/supabase_client.py` — lazy, cached Supabase client factory (`get_supabase_client`, used as a FastAPI dependency). Raises `SupabaseNotConfiguredError` (handled as HTTP 503 in `app/main.py`) if credentials are missing; `is_supabase_configured()` for checks without instantiating. The app intentionally starts without Supabase configured so `/health` still works.
- `main.py` — root shim re-exporting `app.main:app` (FastAPI Cloud entrypoint). Do not add code here.
- `tests/` — pytest suite; the Supabase client dependency is overridden with an in-memory fake in `tests/conftest.py`.

## Supabase Integration

- Credentials come from env vars: `SUPABASE_URL` and `SUPABASE_ANON_KEY` (see `.env.example`).
- On FastAPI Cloud, set secrets with:
  ```bash
  fastapi cloud env set SUPABASE_URL <url>
  fastapi cloud env set --secret SUPABASE_ANON_KEY <key>
  ```
- The client uses the **anon key** and relies on Supabase Row Level Security (RLS). Never commit the service role key; if a route needs it, load it as a separate env var and keep it out of any client-facing response.
- The `todos` table is the reference integration: columns `id (int, primary key)`, `title (text)`, `completed (bool)`, plus timestamps.

## Conventions

- Formatting/linting is enforced by ruff via pre-commit; run `pre-commit run --all-files` before pushing.
- Route handlers return plain dicts/Pydantic models; keep request/response models explicit in `app/schemas.py` (or a route-local schema module) for new endpoints.
- New env vars: add to `app/config.py` `Settings`, document in `.env.example`, and set on FastAPI Cloud for deploys.
- New resources: add a router module in `app/api/routes/`, mount it in `app/main.py`, add tests in `tests/`.
