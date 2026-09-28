"""System endpoints: the dashboard page, service info, health and the docs.

These are the only unauthenticated reads in the app (`/`, `/info`, `/health`).
The module is deliberately mounted **unversioned** (the registry passes
``version=None``): monitoring, bookmarks and the static UI must keep working
across an API version bump, and Swagger needs its schema at the root.

Interactive docs stay disabled unless DOCS_USERNAME/DOCS_PASSWORD are set and
then sit behind HTTP Basic auth — FastAPI's default `docs_url`/`openapi_url`
must stay off.
"""

from importlib import resources

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.clients.supabase import is_supabase_configured
from app.core.config import settings
from app.core.dependencies import require_docs_auth
from app.modules.system.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_docs_auth)])
def openapi_schema(request: Request) -> JSONResponse:
    """OpenAPI schema (behind docs auth)."""
    return JSONResponse(
        get_openapi(
            title=settings.app_name,
            version=settings.app_version,
            description="Backend API for PAS",
            routes=request.app.routes,
        )
    )


@router.get("/docs", include_in_schema=False, dependencies=[Depends(require_docs_auth)])
def swagger_ui() -> HTMLResponse:
    """Swagger UI (behind docs auth)."""
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{settings.app_name} docs")


@router.get("/redoc", include_in_schema=False, dependencies=[Depends(require_docs_auth)])
def redoc() -> HTMLResponse:
    """ReDoc UI (behind docs auth)."""
    return get_redoc_html(openapi_url="/openapi.json", title=f"{settings.app_name} docs")


@router.get("/")
def index() -> HTMLResponse:
    """Serve the single-file HTML dashboard."""
    return HTMLResponse(resources.files("app.static").joinpath("index.html").read_text("utf-8"))


@router.get("/info")
def info(request: Request) -> dict:
    """Service info (JSON) endpoint, including version discovery.

    ``version`` is the release and ``api_version``/``api_base`` the API surface
    clients should call; ``api_versions`` lists every surface this deployment
    still serves, so a client can tell whether its own version is alive.
    """
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "api_version": settings.api_version,
        "api_base": settings.api_prefix,
        "api_versions": getattr(request.app.state, "api_versions", [settings.api_version]),
        "status": "ok",
        "docs": "/docs" if settings.docs_enabled else None,
    }


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Health check endpoint for monitoring (always public)."""
    return HealthResponse(
        status="healthy",
        supabase="configured" if is_supabase_configured() else "not-configured",
        version=settings.app_version,
        api_version=settings.api_version,
    )
