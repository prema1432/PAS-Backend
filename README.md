# PAS Backend

FastAPI application with Supabase integration, ready for deployment to FastAPI Cloud.

## Project structure

```
.
├── app/
│   ├── main.py             # App factory + `app` (entrypoint app.main:app)
│   ├── core/               # Shared plumbing, grouped by concern
│   │   ├── config.py       # Settings (env vars / .env)
│   │   ├── dependencies.py # docs auth + the caller's session/user/client
│   │   ├── clock.py        # one UTC timestamp format
│   │   ├── types.py        # aliases shared by more than one module
│   │   ├── clients/        # outbound I/O: supabase.py (client factory)
│   │   ├── security/       # jwt, crypto, otp, rate_limit
│   │   └── http/           # ip, geolocation, user_agent — request introspection
│   ├── modules/            # One self-contained package per feature
│   │   ├── registry.py     # MODULES — every router the app mounts
│   │   ├── system/         # /, /info, /health, docs
│   │   ├── auth/           # sign-in/refresh/out, session cookies, login events
│   │   ├── customers/      # customers, OTPs, plans, recharges
│   │   ├── providers/      # LLM catalogue + models + preference order
│   │   ├── api_keys/       # provider keys (encrypted at rest)
│   │   ├── billing/        # payments ledger, minutes, auto-recharge
│   │   ├── customer_portal/ # phone+OTP customer self-service (anonymous)
│   │   ├── sessions/       # per-customer usage sessions
│   │   └── analytics/      # dashboard aggregation
│   └── static/index.html   # Single-file dashboard UI (light + dark)
├── tests/                  # pytest suite (Supabase stubbed in-memory)
├── scripts/                # Repo checks behind the pre-commit hooks
├── Makefile                # Self-documenting dev shortcuts (`make help`)
├── supabase/migrations/    # SQL migrations (tables, RLS, audit trigger)
├── pyproject.toml          # Deps, pytest & ruff config, FastAPI entrypoint
└── .pre-commit-config.yaml # Ruff + repo hygiene hooks + the checks in scripts/
```

Every module owns its slice: `router.py` (prefix + tags), `schemas.py` and
`service.py`. Adding a feature means adding a folder and one line in
`app/modules/registry.py` — see [CLAUDE.md](CLAUDE.md) for the conventions.
Dependencies point one way: a feature may use `app/core/`, `core/` never imports
a feature (except the one edge in `dependencies.py`, which needs the auth
session helpers).

## Versioning

The API is versioned in the URL: data routes live under **`/api/v1`**
(`API_VERSION`, changeable per environment), while the deployment routes — `/`,
`/info`, `/health`, `/docs` — stay unversioned so monitoring and Swagger survive
a version bump.

```bash
curl http://127.0.0.1:8012/info     # {version, api_version, api_base, api_versions}
curl http://127.0.0.1:8012/health   # {status, supabase, version, api_version}
```

The **release version** has exactly one home, `app/__init__.py.__version__`: the
packaging metadata reads it at build time and the app reports it, and
`tests/test_versioning.py` fails if they ever disagree. Bumping the API version
is additive — keep the old routers mounted with `version="v1"` in the registry
and both surfaces answer at once.

## Make targets

`make` (or `make help`) lists everything; every recipe drives `.venv` by path, so
there is nothing to activate first.

| Target | What it does |
|---|---|
| `make install` | Create `.venv` and install exactly what `uv.lock` pins (project + dev tools) |
| `make sync` | Reinstall the locked dependencies and the editable project (after a pull) |
| `make env` | Create `.env` from `.env.example` if it is missing |
| `make dev` | Auto-reloading dev server (`PORT=8012` to change the port) |
| `make run` | Server without reload, as in production |
| `make health` | Curl `/health` of a running server |
| `make test` | Test suite, no coverage gate (`FILE=tests/test_billing.py` for one file) |
| `make coverage` | Test suite + the 100% coverage gate (same flags as `verify`) |
| `make lint` / `make format` | Ruff lint (auto-fix) and format |
| `make precommit` | Run every pre-commit hook over all files |
| `make verify` | Everything CI would: hooks, tests at 100% coverage, `uv lock --check` |
| `make migrate FILE=...` / `make migrate-all` | Apply one / all SQL files in `supabase/migrations` |
| `make db-tables` / `make db-shell` | List tables / open a `psql` session |
| `make secrets` / `make js-check` | Fail on committed-looking credentials / syntax-check the inline JS |
| `make clean` / `make deploy` | Drop caches / deploy to FastAPI Cloud |

Database targets need a connection string (they never read one from `.env`, so
the password stays out of the repo):

```bash
make migrate-all DB_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres'
```

## Run locally

```bash
make dev            # or: make run PORT=8012
```

Then open http://127.0.0.1:8000 for the dashboard. API docs live at `/docs` but
require HTTP Basic auth (`DOCS_USERNAME` / `DOCS_PASSWORD`) and are disabled when
those vars are unset.

> First-time setup: `make install`, then `make env` and fill in your Supabase credentials.

## Tests & hooks

```bash
make verify              # hooks + test suite at 100% coverage + lockfile check
make coverage            # the test suite plus the coverage gate
make test                # just the test suite (239 tests, no network needed)
make precommit           # lint + format + hygiene checks over every file
```

The suite needs no Supabase project and no network: `tests/fakes.py` provides
in-memory stand-ins for the client, wired in through FastAPI dependency
overrides. **Coverage of `app/` must stay at 100%** — `make coverage` and
`make verify` fail on any uncovered line, so a new branch arrives with a test or
not at all. See the "Tests and coverage" section of `CLAUDE.md` for the details
and for how the three fake write shapes (real row / empty / raise) map onto the
status codes the routers must return. The pre-commit run is also available as
the raw commands `.venv/bin/pytest` and `.venv/bin/pre-commit run --all-files`.
Keep the `ruff-pre-commit` rev in `.pre-commit-config.yaml` in step with the
`ruff` version pinned in `uv.lock` — `make lint`/`make format` use the latter.

Alongside the hygiene hooks (JSON/YAML/TOML, `.py` parsing, line endings,
private keys) four checks in `scripts/` enforce this project's own rules, so a
rule that used to live only in a document now fails a commit:

```bash
make secrets             # no .env, no JWT/anon token, no password in a URL in a tracked file
make js-check            # app/static/index.html is the whole dashboard: it must still parse
```

`make verify` covers both, plus the checks that data routes use the per-caller
Supabase client and that routers never hard-code the `/api/v1` prefix. `js-check`
needs `node` on PATH (so does working on `index.html`). See "Pre-commit hooks" in
`CLAUDE.md` for what each hook refuses and why.

## Deploy to FastAPI Cloud

```bash
make deploy          # fastapi deploy
```
