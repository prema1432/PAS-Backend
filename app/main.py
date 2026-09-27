"""PAS Backend - FastAPI application entrypoint.

Exposes the FastAPI `app` instance (entrypoint: app.main:app).
"""

from importlib import resources

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.routes import items, todos
from app.config import settings
from app.schemas import HealthResponse
from app.services.supabase_client import (
    SupabaseNotConfiguredError,
    is_supabase_configured,
)

app = FastAPI(
    title=settings.app_name,
    description="Backend API for PAS",
    version=settings.app_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SupabaseNotConfiguredError)
async def supabase_not_configured_handler(
    request: Request, exc: SupabaseNotConfiguredError
) -> JSONResponse:
    """Return a clean 503 instead of a raw 500 when Supabase env vars are missing."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/")
def index() -> HTMLResponse:
    """Serve the single-file HTML dashboard."""
    return HTMLResponse(resources.files("app.static").joinpath("index.html").read_text("utf-8"))


@app.get("/info")
def info() -> dict:
    """Service info (JSON) endpoint."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Health check endpoint for monitoring."""
    return HealthResponse(
        status="healthy",
        supabase="configured" if is_supabase_configured() else "not-configured",
    )


# Routers
app.include_router(items.router, prefix="/items", tags=["items"])
app.include_router(todos.router, prefix="/todos", tags=["todos"])
