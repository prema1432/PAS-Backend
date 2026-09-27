# PAS Backend

FastAPI application with Supabase integration, ready for deployment to FastAPI Cloud.

## Project structure

```
.
├── app/                    # Application package
│   ├── main.py             # FastAPI app (routers, middleware, health)
│   ├── config.py           # pydantic-settings Settings (env vars / .env)
│   ├── schemas.py          # Shared Pydantic request/response models
│   ├── api/
│   │   └── routes/         # Routers: items, todos
│   └── services/
│       └── supabase_client.py  # Lazy cached Supabase client
├── tests/                  # pytest suite (Supabase stubbed in-memory)
├── main.py                 # Shim re-exporting app.main:app for FastAPI Cloud
├── pyproject.toml          # Deps, pytest & ruff config, FastAPI entrypoint
└── .pre-commit-config.yaml # Ruff + repo hygiene hooks
```

## Run locally

```bash
source .venv/bin/activate   # activate the virtualenv first
fastapi dev
```

Or without activating: `.venv/bin/fastapi dev`

Then open http://127.0.0.1:8000/docs for the interactive API docs.

> First-time setup: copy `.env.example` to `.env` and fill in your Supabase credentials.

## Tests & hooks

```bash
.venv/bin/pytest                 # run the test suite
pre-commit run --all-files       # lint + format + hygiene checks
```

## Deploy to FastAPI Cloud

```bash
source .venv/bin/activate
fastapi deploy
```
