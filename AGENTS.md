# AGENTS.md

Quick reference for AI coding agents working in this repository. Full details in [CLAUDE.md](CLAUDE.md).

## Stack

- Python 3.12, FastAPI (`fastapi[standard]`), Supabase (Python client), pydantic-settings
- Layout: `app/` package (routers in `app/api/routes/`, services in `app/services/`), tests in `tests/`
- Entrypoint: `main.py` shim -> `app.main:app`
- Deploy target: FastAPI Cloud (`fastapi deploy`)

## Commands

| Task | Command |
|---|---|
| Install (uv) | `uv venv --python 3.12 && uv pip install -p .venv -e .` |
| Dev server | `fastapi dev` (activate `.venv` first) |
| Tests | `.venv/bin/pytest` |
| Lint + format | `ruff check --fix . && ruff format .` |
| Pre-commit (all files) | `pre-commit run --all-files` |
| Deploy | `fastapi deploy` |

## Rules

1. Never commit `.env` or any real secret. Use `.env.example` as the template and set secrets via `fastapi cloud env set --secret`.
2. Supabase access goes through `get_supabase_client()` from `app/services/supabase_client.py` — don't call `create_client` elsewhere.
3. The app must keep starting without Supabase credentials (lazy client init). `/health` must always respond.
4. Run `pre-commit run --all-files` before committing; fix ruff findings rather than bypassing hooks.
5. New endpoints: explicit Pydantic request/response models (in `app/schemas.py`), docstrings on handlers, add router in `app/api/routes/`.
6. New env vars: update `app/config.py`, `.env.example`, and document in CLAUDE.md.
