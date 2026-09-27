"""Compatibility shim so FastAPI Cloud can boot `main:app`.

The real application lives in the `app` package (app/main.py). This file only
re-exports it; add nothing here.
"""

from app.main import app

__all__ = ["app"]
