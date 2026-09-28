# AGENTS.md

Quick reference for AI coding agents working in this repository. Full details in [CLAUDE.md](CLAUDE.md).

## Stack

- Python 3.12, FastAPI (`fastapi[standard]`), Supabase (Python client), pydantic-settings
- Layout: module-wise — shared plumbing in `app/core/` grouped by concern (`clients/`, `security/`, `http/`), one self-contained package per feature in `app/modules/<module>/` (`router.py` + `schemas.py` + `service.py`), mounted via `app/modules/registry.py`; tests in `tests/`; pre-commit checks in `scripts/`
- Versioning: feature routes live under `/api/v1` (config `API_VERSION`), deployment routes (`/`, `/info`, `/health`, docs) stay unversioned; the release version is `app/__init__.py.__version__` alone
- Tables: `profiles`, `customers`, `login_events`, `providers`, `provider_models`, `api_keys`, `payments`, `customer_sessions` — all with `created_at/updated_at/created_by/updated_by` maintained by the `set_audit_fields` trigger
- Entrypoint: `app.main:app`, declared in `[tool.fastapi]` and passed explicitly by the Makefile. There is no root `main.py` shim — do not add one.
- Deploy target: FastAPI Cloud (`fastapi deploy`, i.e. `make deploy`)
- `Makefile` — the entry point for local work: setup, run, test, lint, migrations, deploy

## Commands

Prefer `make`: `make help` lists every target and nothing needs activating.

| Task | Command |
|---|---|
| Install | `make install` (or `uv venv --python 3.12 --allow-existing && uv sync --locked && uv pip install -p .venv -e . --no-deps`) |
| Dev server | `make dev` (`PORT=8012` to change the port) / `make run` without reload |
| Tests | `make test` (or `.venv/bin/pytest`) — 239 tests, no network |
| Coverage | `make coverage` — the suite plus the 100% gate over `app/` |
| Full gate | `make verify` (hooks + tests at 100% coverage + `uv lock --check`) |
| Lint + format | `make lint && make format` (or `ruff check --fix . && ruff format .`) |
| Pre-commit (all files) | `make precommit` — 18 hooks; `make secrets` and `make js-check` run two of them alone |
| Migrations | `make migrate FILE=supabase/migrations/<name>.sql` / `make migrate-all` (needs `DB_URL`) |
| Deploy | `make deploy` |

## Rules

1. Never commit `.env` or any real secret. Use `.env.example` as the template and set secrets via `fastapi cloud env set --secret`.
2. Supabase auth flows use `get_supabase_client()`; **data routes must depend on `get_current_client`** (per-request, token-bound) so RLS runs as the caller. Don't call `create_client` in routes.
3. The app must keep starting without Supabase credentials (lazy client init). `/health` must always respond.
4. Run `make verify` (or `pre-commit run --all-files` + `make coverage`) before committing; fix ruff findings rather than bypassing hooks. Coverage of `app/` is gated at **100%** (`fail_under` in `pyproject.toml`), so new code needs tests; reserve `# pragma: no cover` for genuinely unreachable defensive branches and say why in a comment. The `ruff-pre-commit` rev must stay in step with the `ruff` version in `uv.lock`. Rules 1, 2 and 5 are also enforced by hooks in `scripts/` (see the "Pre-commit hooks" section of CLAUDE.md) — a new rule belongs there, with a test in `tests/test_precommit_scripts.py`.
5. New endpoints: add a module package under `app/modules/<module>/` (router with its own `prefix`/`tags` — the `/api/v1` prefix comes from the registry, never hard-code it; explicit Pydantic models in its `schemas.py`, logic in its `service.py`), register the router in `app/modules/registry.py`, and put docstrings on the handlers.
6. New env vars: update `app/core/config.py`, `.env.example`, and document in CLAUDE.md.
7. Auth: resolve the caller via `get_current_user` (cookie-based). Never store or log passwords; Supabase Auth hashes them. Scope every DB row to `user_id` and keep RLS enabled.
   - Every new endpoint must authenticate unless it is `/`, `/info`, `/health`, `/auth/login`, `/auth/signup` or `/auth/logout`. `tests/test_security.py` sweeps the OpenAPI schema and asserts an anonymous caller gets 401 from every other route, so a new route is covered automatically.
   - Docs routes are intentionally disabled without `DOCS_USERNAME`/`DOCS_PASSWORD`; do not re-enable FastAPI's default `docs_url`/`openapi_url`.
8. Audit fields are trigger-managed — never write `created_by/updated_by/created_at/updated_at` from app code, and give new tables the same four columns plus the trigger.
9. Provider API keys are encrypted at rest (`APP_ENCRYPTION_KEY`); never return one, log one, or store one in plaintext. Without the env var, key storage returns 503.
10. Roles live in `public.profiles` (default `customer`; the default admin is `CEO`). Never expose roles in the UI or API responses. The auth modal intentionally has no X button — it closes via Esc or backdrop click.
