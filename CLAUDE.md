# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Project Overview

PAS Backend is a FastAPI application that integrates with Supabase. It is designed for deployment to FastAPI Cloud. Python 3.12+.

## Commands

```bash
make help        # every target, self-documenting (recommended entry point)

# Install dependencies exactly as uv.lock pins them (uv recommended)
make install     # = uv venv --python 3.12 --allow-existing
                 #   && uv sync --locked
                 #   && uv pip install -p .venv -e . --no-deps
make sync        # re-run the last two steps after a pull

# Run the dev server (http://127.0.0.1:8000 by default, docs at /docs)
make dev         # auto-reload; PORT=8012 to change the port
make run         # no reload, as production runs it

# Tests
make test                        # or .venv/bin/pytest (239 tests, no network)
make test FILE=tests/test_billing.py
make coverage                    # same suite + the 100% coverage gate

# Lint and format (same ruff version as the pre-commit hook, via uv.lock)
make lint && make format

# Everything CI would run: hooks + tests at 100% coverage + uv lock --check
make verify

# Pre-commit hooks
make precommit-install   # once per clone
make precommit           # run on all files

# Database migrations (DB_URL / SUPABASE_DB_URL, never stored in .env)
make migrate FILE=supabase/migrations/20260928_providers_billing_sessions.sql
make migrate-all
make db-tables

# Deploy to FastAPI Cloud
fastapi login    # once
make deploy
```

Notes on the toolchain:

- `make install` deliberately splits `uv sync --locked` (exact locked versions)
  from `uv pip install -e . --no-deps` (the editable project): `pyproject.toml`
  has no `[build-system]`, so `uv sync` treats the project as virtual and would
  otherwise leave `app` uninstalled.
- Keep `.pre-commit-config.yaml`'s `ruff-pre-commit` rev equal to the `ruff`
  version pinned in `uv.lock` (currently 0.16.9). The Makefile's lint/format
  targets use the venv copy, so a mismatch would let `make format` and the hook
  disagree.

## Architecture

Module-wise package layout: `app/core/` holds everything shared (grouped by
concern), and each feature owns a whole vertical slice under
`app/modules/<module>/`. The ASGI entrypoint is `app.main:app` — there is no
root shim.

```
app/
├── main.py                  # App factory + `app`: CORS, rate limiting, headers, mounts modules
├── core/                    # Cross-cutting plumbing (no feature knowledge)
│   ├── config.py            # pydantic-settings Settings (env vars / .env) + `settings`
│   ├── dependencies.py      # require_docs_auth, get_current_session/user/client
│   ├── clock.py             # now_iso() — one UTC timestamp format
│   ├── types.py             # aliases shared by >1 module (Tier)
│   ├── clients/
│   │   └── supabase.py      # client factory: get_supabase_client, get_client_for_token
│   ├── security/            # primitives, not policy
│   │   ├── jwt.py           # reads JWT claims locally (expiry checks before refreshing)
│   │   ├── crypto.py        # Fernet encryption for provider API keys
│   │   ├── otp.py           # CSPRNG 6-digit OTPs
│   │   └── rate_limit.py    # in-memory sliding-window limiter
│   └── http/                # what a request says about its caller
│       ├── ip.py            # client_ip(request), is_private_ip(ip), ip_version(ip)
│       ├── geolocation.py   # best-effort IP geolocation (ipwho.is)
│       └── user_agent.py    # dependency-free UA parser
└── modules/
    ├── registry.py          # MODULES — the mounted routers, in path-precedence order
    ├── system/              # /, /info, /health, /docs, /redoc, /openapi.json
    ├── auth/                # router, schemas, service (Supabase Auth), session, login_events
    ├── customers/           # router, schemas, service (get_customer)
    ├── providers/           # router (providers + /models), schemas, service
    ├── api_keys/            # router, schemas, service (masking — never the secret)
    ├── billing/             # router, schemas, service (ledger + aggregation + recharge)
    ├── customer_portal/     # router, schemas, service (phone+OTP self-service, anonymous)
    ├── sessions/            # router, schemas, service
    ├── analytics/           # router, service (pure aggregation)
    ├── audit_logs/          # read-only trail over the trigger-fed audit table
```

The dependency direction is one-way: `modules/<feature>` → `core/`, never the
reverse, and never a sibling's router. Inside `core/`, the three packages group
by *concern* rather than by who calls them — `clients/` is outbound I/O,
`security/` is authentication and abuse control, `http/` is request
introspection — so a new helper has one obvious home.

Rules of the layout:

- A module is self-contained: `router.py` (declares its own `prefix` + `tags`),
  `schemas.py` (only its own models) and `service.py` (business logic, no FastAPI
  imports). A module may add a module-specific file of its own — `auth/` has
  `session.py` (cookies) and `login_events.py` (the audit row) — but the three
  standard names mean "this is the router / the models / the logic". Adding a
  feature = a new folder plus one line in `registry.py`.
- Modules may import another module's **service** (e.g. sessions uses the billing
  recharge helpers and `customers.service.get_customer`) but never its router.
- `app/core/dependencies.py` imports the auth session helpers; that is the only
  core → module edge, and it exists because session resolution needs Supabase Auth.
- `Makefile` — the local entry point (`make help`): install/sync, dev server, tests, lint, `verify`, migrations, deploy. Recipes call `.venv/bin/...` directly, so no activation is needed.
- `tests/` — pytest suite; Supabase + current-user dependencies are overridden in `tests/conftest.py`.
- `scripts/` — the repository checks behind the local pre-commit hooks (standard library only, one file per rule). See "Pre-commit hooks" below.

## Authentication

- Email/password auth via Supabase Auth (GoTrue). Routes (all under the `/api/v1` prefix): `POST /auth/signup`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`.
- **Two cookies, two tokens** (`app/modules/auth/session.py`), both HttpOnly + SameSite=Lax + Secure, so no script can read or forge them:
  - `pas_access_token` — the Supabase-signed **JWT access token** (1h), sent with every request and the only thing that authenticates a call.
  - `pas_refresh_token` — the long-lived **refresh token** (30-day cookie), used only to mint a new JWT.
- `POST /auth/refresh` exchanges the refresh token for a new pair and rewrites both cookies. Supabase **rotates** refresh tokens, so each one is single-use — a copy stops working the moment the real client refreshes.
- `get_current_session` (`app/core/dependencies.py`) resolves the caller: a live JWT is used as-is; a missing/expired one is refreshed transparently by `resolve_session`, which writes the new cookies onto the same response (dependencies are cached per request, so this happens at most once). Nothing in `app/core` verifies the JWT signature — Supabase does that on every data call via PostgREST.
- `app/core/security/jwt.py` only reads the public claims, so an expiring token is detected locally (30s leeway) instead of paying for a doomed round-trip.
- Any **401 causes the middleware to expire both cookies**, so a dead refresh token is never replayed (and re-sent to Supabase Auth) request after request.
- `POST /auth/logout` revokes the user's sessions at Supabase via `auth.admin.sign_out(access_token, "global")` — note it takes the **access token**, not the refresh token — then clears the cookies. Revocation failure is logged, never surfaced.
- `GET /auth/me` returns `{id, email, access_token_expires_at}` so the UI can schedule its next refresh.
- The dashboard (`app/static/index.html`) refreshes on a timer derived from `access_token_expires_at`, and on any unexpected 401 refreshes once and replays the request. Concurrent refreshes share a single in-flight promise so the single-use refresh token is never spent twice.
- Data tables are per-user: rows carry `user_id`; RLS policies restrict all operations to `auth.uid() = user_id` (see `supabase/migrations/`).
- The modal has no X close button by design; it closes with Esc or a backdrop click. Do not add an X button.

## API versioning

Two versions exist and they are not the same thing:

- **Release version** — `app/__init__.py.__version__`, the single source of truth. Packaging reads it (`dynamic = ["version"]` + `[tool.setuptools.dynamic]` in `pyproject.toml`), `Settings.app_version` defaults to it, and `/info`/`/health` report it. Bump it there and nowhere else; `tests/test_versioning.py` fails if the three drift apart.
- **API version** — `API_VERSION` (default `v1`), exposed as the `api_prefix` (`/api/v1`). This is the URL a client writes down, so it changes only deliberately.

How it works:

- `app/modules/registry.py` is the authority. Each `ModuleSpec` carries a `version`: the API version for feature modules, `None` for the unversioned deployment routes. `mount()` prefixes each router accordingly and `mounted_versions()` reports every live surface.
- **Feature routes are versioned**: `/api/v1/customers`, `/api/v1/auth/login`, … The OpenAPI schema and Swagger show the versioned paths, so the docs are never ambiguous about the surface.
- **Deployment routes stay unversioned**: `/` (the UI), `/info`, `/health`, `/docs`, `/redoc`, `/openapi.json`. Monitoring, bookmarks and Swagger must survive a version bump; there is no `/api/v1/health`.
- `/info` advertises `api_version`, `api_base` and `api_versions` (every surface still served), so a client can check whether the version it depends on is alive. `/health` reports both versions.
- Responses under the API prefix carry `X-API-Version` (exposed to cross-origin clients via CORS `expose_headers`).
- The dashboard keeps a single `API_BASE` constant and calls `api("/customers")`; nothing else in the UI knows the version.

**Publishing a breaking change** is additive: write the new routers, bump `API_VERSION`, and keep the old specs in `MODULES` pinned with `version="v1"` so both surfaces answer. `tests/test_versioning.py::test_a_second_version_can_be_published_alongside_the_first` proves the registry supports this; the unversioned surface is asserted to 404 so old paths never linger as accidental aliases.

## Audit columns (every table)

Every table carries `created_at`, `updated_at`, `created_by`, `updated_by` (the latter two are `uuid` FKs to `auth.users`, nullable for system writes).

- A `set_audit_fields()` trigger (BEFORE INSERT OR UPDATE) keeps them honest: on insert it stamps `created_at`/`created_by` (defaulting to `auth.uid()`) and mirrors `updated_by`; on update it **freezes `created_*`** and refreshes `updated_at` plus `updated_by` with the acting user.
- Nullable so seeds, migrations and the `handle_new_user` auth trigger still work.
- Never set these columns from application code — the trigger owns them.
- New tables must get the same four columns and the trigger (see `20260927_audit_fields.sql`).

## Data access pattern (important)

- `get_supabase_client()` is a cached client used only for auth flows (sign up/in, token checks). Never use it for data queries.
- Data routes depend on `get_current_client`, which creates a client per request and calls `postgrest.auth(token)` so Postgres RLS evaluates as that user (`auth.uid()`). This is what keeps one user's requests from reading/writing another's rows — the shared cached client would leak whichever session logged in last.
- Queries still add an explicit `.eq("user_id", ...)` filter as defence in depth.

## Customers

- `public.customers` holds `name`, `email`, `phone`, `otp` (6 digits), `is_active`, `plan` (`free` | `paid`), the **remaining-minutes balance** (`remaining_minutes`) and the auto-recharge settings (`auto_recharge`, `auto_recharge_minutes`, `auto_recharge_amount`), scoped per owner by RLS.
- Routes: `GET/POST /customers`, `PATCH /customers/{id}` (details, active toggle, plan, auto-recharge), `POST /customers/{id}/otp` (new OTP), `POST /customers/{id}/recharge`, `DELETE /customers/{id}`.
- OTPs come from `generate_otp()` (CSPRNG). Duplicate emails per owner return 409.

## Providers, models and API keys

- `public.providers` is the LLM catalogue (e.g. OpenRouter): `name`, `slug` (unique per owner), `base_url`, `is_free`/`is_paid`, `preferred_tier`, `priority`, `is_active`.
- `priority` drives the **preference list** (`GET /providers/preferences?tier=free|paid`) — lower is tried first, so the first entry is the provider to use for that tier.
- `public.provider_models` holds per-provider models with their own free/paid flags and optional context window.
- Routes: `GET/POST /providers`, `PATCH/DELETE /providers/{id}`, `GET /providers/preferences`, `GET/POST /providers/{id}/models`, `PATCH/DELETE /models/{id}`.
- `public.api_keys` stores provider credentials. Routes: `GET/POST /api-keys`, `PATCH/DELETE /api-keys/{id}`.

## Billing and customer sessions

- `public.payments` is the recharge ledger: `amount`, `currency`, `minutes`, `mode` (`auto` | `manual`), `status`, optional `reference`/`note`.
- `public.customer_sessions` records one row per customer session: `provider_id`, `model`, `status` (`active` | `ended`), `minutes_used`, `started_at`, `ended_at`.
- `POST /customers/{id}/recharge` records a payment and adds its minutes to `remaining_minutes`; that balance is authoritative (stored, not derived) so the customer table can show it without an aggregate query.
- `POST /sessions` starts a session (404 if unowned, 400 if inactive, **402 if the balance is zero**); `POST /sessions/{id}/end` charges `minutes_used` (never below zero) and then calls `auto_recharge_if_needed`: when `auto_recharge` is on and the balance hits zero, a **`mode="auto"` payment** is written and the configured top-up minutes are added. `GET /sessions` lists them (filter by `customer_id`/`status`).
- `GET /billing/payments` lists the ledger user-wise (with `customer_name`), filterable by `customer_id`/`mode`; `GET /billing/summary` returns totals, the per-customer rollup (session counts, minutes used, remaining, amount) and a zero-filled daily series.
- `PATCH /billing/payments/{id}` lets the owner correct an editable field of a ledger row (`amount`, `minutes`, `mode`, `status`, `note`) from the dashboard's ✏️ button on the payments table. Unknown/immutable fields are ignored — an only-unknown patch is a 400 "Nothing to update"; unknown or unowned ids are 404.
- Aggregation lives in `app/modules/billing/service.py` (pure functions, like the analytics service), next to the database-touching helpers (`set_balance`, `record_payment`, `apply_recharge`, `update_payment`, `auto_recharge_if_needed`).

## Login activity

- Every successful `POST /auth/login` and `/auth/signup` records a row in `public.login_events`: IP (+ `x-forwarded-for`/`cf-connecting-ip`/`x-real-ip`), IP version, raw User-Agent, parsed browser/version/OS/device/bot flag, city/region/country/continent/postal/coordinates/timezone/ISP, language, referer, origin, method, path.
- `GET /auth/activity` returns the caller's last 25 sign-ins (token-bound client + RLS).
- Recording is best-effort: failures are logged, never surfaced, so tracking can't block sign-in.
- The sidebar shows a compact "This session" card; the Activity view lists the history and a modal shows every captured field.

## Security

- **Every data endpoint requires a session.** Under the `/api/v1` prefix, `/customers*`, `/providers*`, `/models/*`, `/api-keys*`, `/billing/*`, `/sessions*`, `/analytics/summary`, `/auth/me` and `/auth/activity` return 401 without the auth cookie. The only anonymous routes are `/` (login UI), `/info`, `/health` and the auth set `/auth/login`, `/auth/signup`, `/auth/logout`. `POST /auth/refresh` needs no access token either — it authenticates with the refresh cookie and returns 401 without one, so the sweep covers it like any other route. `tests/test_security.py` enumerates the OpenAPI schema and asserts 401 from every other operation, so new routes are covered without editing a list.
- **Docs are locked down.** Swagger, ReDoc and `/openapi.json` are registered manually in `app/main.py` and return **404 unless `DOCS_USERNAME` + `DOCS_PASSWORD` are set**; when set they require HTTP Basic auth (`require_docs_auth` in `app/core/dependencies.py`); the routes themselves live in `app/modules/system/router.py`.
- **Rate limiting** is a dependency-free in-memory sliding window (`app/core/security/rate_limit.py`) applied as middleware: `RATE_LIMIT_PER_MINUTE` (default 120) for everything, `AUTH_RATE_LIMIT_PER_MINUTE` (default 8) for the sign-in/sign-up endpoints. Those are matched on the path *suffix* (`_SENSITIVE_SUFFIXES` in `app/main.py`) so the tight budget survives an API version prefix — never match them as exact paths. Blocked requests get `429` with `Retry-After`. Per-process only — move to Redis before running more than one instance.
- **CORS** is configured from `CORS_ORIGINS` (comma-separated; `*` by default). Credentials are only enabled when explicit origins are configured, because browsers reject `*` + credentials. Methods and headers are allow-listed.
- **Hardening headers** on every response: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, plus HSTS over HTTPS.
- Session cookies are `HttpOnly`, `SameSite=Lax`, `Secure`; passwords are hashed by Supabase (bcrypt) and never logged.
- **Provider API keys are encrypted at rest.** `app/core/security/crypto.py` encrypts with Fernet using a key derived from `APP_ENCRYPTION_KEY`; only `api_keys.key_ciphertext` and a 4-character `key_hint` are stored. Responses are built from an explicit allow-list (`public_key()` in `app/modules/api_keys/service.py`) so the secret — or any future sensitive column — cannot leak. Storing a key without `APP_ENCRYPTION_KEY` returns **503** rather than writing plaintext, and the plaintext is never logged.

## Audit logs

- `public.audit_logs` is an **append-only** trail written by the `write_audit_log` trigger (migration `20260928_audit_logs.sql`) on every insert/update/delete of `customers`, `providers`, `provider_models`, `api_keys`, `payments` and `customer_sessions`. Each row records `user_id`, `table_name`, `row_id`, `action` and the `changed_fields` of the change; no-op updates are not logged.
- The API can only **read** the trail: `GET /audit-logs` (owner-scoped, newest first, filterable by `table_name`/`action`/`row_id`). No insert/update/delete routes exist, and RLS grants no writes, so the trail cannot be edited from the app.
- The dashboard's "🛡️ Audit logs" sidebar view lists the trail with table/action filters.

## Analytics

- `GET /analytics/summary?window_days=14` returns `totals`, `by_location`, `by_country`, `by_country_code`, `by_browser`, `by_os`, `by_device`, `by_plan`, `by_status`, `top_ips`, `by_day` (zero-filled) and `by_hour`.
- The payload also carries a `billing` block (totals, per-customer sessions/minutes, daily amounts) so the Overview tab needs a single request; `GET /billing/summary` returns the same block on its own.
- Aggregation lives in `app/modules/analytics/service.py` (and `app/modules/billing/service.py`) as pure functions over already-fetched rows, so it is unit-testable without a database; the route only fetches (owner-scoped, capped at 1000 rows each).
- The Analytics view renders KPI cards and dependency-free CSS/SVG-free charts (horizontal bars, column charts).

## Roles

- `public.profiles` holds `id`, `email`, `role` (`customer` | `support` | `admin` | `CEO`).
- New signups get `customer` automatically via the `on_auth_user_created` trigger; pre-existing users were backfilled to `customer`.
- The default admin account (`premanath@pas.com`) holds the hidden `CEO` role.
- **Roles are server-side only: never render a role in the UI and never include it in `/auth/me` or any client-facing payload.** Read it server-side when authorization checks are added.
- Note: Supabase free tier rate-limits confirmation emails per hour; disable "Confirm email" in dashboard Auth settings for local testing, or create users via the dashboard.

## Tests and coverage

The suite lives in `tests/` and runs entirely offline: no Supabase project, no
network. Two deliberate choices keep it that way, and both are load-bearing.

- **Supabase is replaced at the boundary, not mocked with a library.**
  `tests/fakes.py` holds small in-memory stand-ins (`FakeTable`,
  `FakeSupabaseClient`, `WriteEmptyClient`, `WriteErrorClient`) and
  `tests/conftest.py` wires them in through `app.dependency_overrides` — so the
  routers, services and validation under test are the real ones. Fakes live in a
  module rather than in `conftest.py` so any test file can import them directly.
- **Writes have three distinct shapes, and the routers must answer them
  differently:** a real row, an empty result (the row was deleted meanwhile, or
  RLS filtered it — a 500, never a fake success) and a raise (a unique violation
  — a 4xx). `WriteEmptyClient` and `WriteErrorClient` exist so each path is
  reachable without a database.
- Auth-flow tests that need cookies use
  `TestClient(app, base_url="https://testserver")`: the session cookies are
  `Secure`, so an `http://` client would never send them back and every request
  would look anonymous (see `tests/test_auth_tokens.py`).

**Coverage is a gate, not a report.** `pyproject.toml` sets
`[tool.coverage.run] source = ["app"]` and `fail_under = 100`, which
`make coverage` and `make verify` enforce: **any** uncovered line under `app/`
fails the build. `make test` skips measurement entirely, so it stays a fast
inner loop — use it while iterating and `make coverage` before you are done. A
new branch therefore arrives with a test, or the gate stops the commit; there is
no drifting floor to renegotiate. `# pragma: no cover` is reserved for branches
that genuinely cannot run in this process (a defensive `except` around a "never
block sign-in" write, an insert that cannot be reached without credentials) and
always carries a comment saying why — never to silence a line that a test could
reach.

## Pre-commit hooks

`make verify` runs `.pre-commit-config.yaml` with `--all-files`, so everything in
that file *is* the gate rather than advice. Alongside the usual hygiene (trailing
whitespace, end-of-file, YAML/TOML/JSON, `check-ast`, large files, merge markers,
case conflicts, LF endings, `debug-statements`, `detect-private-key`) and ruff
check + format, four hooks encode numbered rules from `AGENTS.md`:

| Hook | Rule it enforces | Manual run |
|---|---|---|
| `no-credentials` | 1 — no `.env`, no secret in a tracked file | `make secrets` |
| `client-boundaries` | 2 — `create_client` only in `app/core/clients/supabase.py`; `get_supabase_client` only in `app/core/` + `app/modules/auth/` | — |
| `api-prefix` | 5 — the `/api/<version>` prefix comes from the registry, never a router | — |
| `inline-js` | the single-file dashboard still parses | `make js-check` |

(`check-json` currently reports "no files to check" — the repository ships no
`.json` file yet; it is there for the first one that arrives.)

The checks live in `scripts/` and run with `language: python`, so they are
hermetic: standard library only, nothing installed from `.venv`, no third-party
dependency to keep in step. `tests/test_precommit_scripts.py` pins both halves of
a useful hook — it passes the real tree, and it fails on the thing it exists to
catch. Three deliberate details, each of which caused a real problem before it
was added:

- `no-credentials` refuses a staged `.env` **by name**, because a gitignored file
  is one `git add -f` away from being published, and it tolerates placeholders
  (`<password>`, `change-me-…`, `$DB_URL`) and never prints the value it matched —
  a hook that echoes the credential has just written it to another log. The old
  shell version failed the tree on the README's own example connection string,
  which is why the rules now live in a script with tests.
- `client-boundaries` and `api-prefix` parse the **AST**, so this repository's own
  prose about `get_supabase_client` or `/api/v1` is not a violation.
- `inline-js` requires node (it is required to work on `index.html` at all) and
  says so instead of skipping, and it translates node's script line number back
  to the line in `index.html` — otherwise the report points at line 900 of a
  2,540-line page with no way to find it.

Nothing runs these on `git commit` until `make precommit-install` has been run
once per clone; `make verify` and `make precommit` do not need it.

## Supabase Integration

- Credentials come from env vars: `SUPABASE_URL` and `SUPABASE_ANON_KEY` (see `.env.example`).
- On FastAPI Cloud, set secrets with:
  ```bash
  fastapi cloud env set SUPABASE_URL <url>
  fastapi cloud env set --secret SUPABASE_ANON_KEY <key>
  ```
- The client uses the **anon key** and relies on Supabase Row Level Security (RLS). Never commit the service role key; if a route needs it, load it as a separate env var and keep it out of any client-facing response.
- Tables: `profiles` (roles), `customers`, `login_events`, `providers`, `provider_models`, `api_keys`, `payments`, `customer_sessions`.

## Conventions

- Formatting/linting is enforced by ruff via pre-commit; run `pre-commit run --all-files` before pushing.
- Route handlers return plain dicts/Pydantic models; keep request/response models explicit in the owning module's `schemas.py` for new endpoints.
- New env vars: add to `app/core/config.py` `Settings`, document in `.env.example`, and set on FastAPI Cloud for deploys.
- New resources: add a package under `app/modules/<name>/` (`router.py` with its own prefix/tags, `schemas.py`, `service.py`), register its router in `app/modules/registry.py`, add tests in `tests/`. New endpoints are authenticated by default (see the OpenAPI 401 sweep in `tests/test_security.py`).
- **UI**: `app/static/index.html` is one self-contained file (no CDN, CSS-only charts) with a light **and** dark theme. Colours come from the CSS custom properties in `:root` / `[data-theme="light"]` — never hard-code a hex value in a rule, or one of the two themes will break. The header script picks the stored `pas-theme` (falling back to `prefers-color-scheme`), the header button toggles it, and the choice is persisted.
