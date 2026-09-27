# PAS Backend

Basic FastAPI application ready for deployment to FastAPI Cloud.

## Run locally

```bash
fastapi dev
```

Then open http://127.0.0.1:8000/docs for the interactive API docs.

## Deploy to FastAPI Cloud

```bash
fastapi deploy
```

If you aren't logged in yet, first run:

```bash
fastapi login
```

## Project files

- `main.py` — FastAPI app (`main:app` entrypoint)
- `pyproject.toml` — dependencies (`fastapi[standard]`), Python version, and the FastAPI Cloud entrypoint
- `.python-version` — pins Python 3.12 for FastAPI Cloud
