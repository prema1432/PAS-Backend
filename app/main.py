"""PAS Backend - FastAPI application entrypoint.

Exposes the FastAPI `app` instance (entrypoint: app.main:app).

The app itself is deliberately thin: it owns the cross-cutting concerns (CORS,
rate limiting, hardening headers, CORS/credential policy and the 503 fallback
when Supabase is unconfigured) and mounts the feature modules from
`app.modules.registry`. Everything a feature needs — routes, schemas, services —
lives inside that feature's package.

Security posture:
- Every data endpoint requires an authenticated session (no anonymous APIs);
  session resolution, including access-token refresh, lives in app.core.dependencies.
- Interactive docs and the OpenAPI schema are disabled unless DOCS_USERNAME /
  DOCS_PASSWORD are set, and then sit behind HTTP Basic auth.
- Per-IP rate limiting, tighter for sign-in/sign-up.
- CORS origins come from configuration, never a hard-coded wildcard in prod.
- The API is versioned in the URL (/api/v1/...); the deployment routes (/,
  /info, /health, docs) stay unversioned. See app/modules/registry.py.
"""

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.clients.supabase import SupabaseNotConfiguredError
from app.core.config import settings
from app.core.http.ip import client_ip
from app.core.security.rate_limit import auth_limiter, default_limiter  # noqa: F401
from app.modules import registry
from app.modules.auth.session import clear_session_cookies

# --- Rate limiting ---------------------------------------------------------
# The limiter instances are built in app.core.security.rate_limit (from settings,
# at import time) so feature modules can meter their own endpoints without
# importing this module. Re-exported here because the middleware below and a
# handful of tests reach for them as `main_module.default_limiter`.

# Endpoints that get the tighter credential-stuffing budget. Matched on the
# suffix so they keep their budget under any API version prefix (/api/v1/...).
# /portal/login belongs here: the customer's phone + OTP pair is a credential
# too, and the portal router additionally checks the limiter itself so a guess
# is metered even when the middleware budget is not the one that fires.
_SENSITIVE_SUFFIXES = ("/auth/login", "/auth/signup", "/portal/login", "/portal/me")


def _limiter_for(path: str):
    """Pick the rate limiter for a request path (resolved per request)."""
    return auth_limiter if path.endswith(_SENSITIVE_SUFFIXES) else default_limiter


async def security_middleware(request: Request, call_next):
    """Apply rate limits, then add hardening headers to every response."""
    limiter = _limiter_for(request.url.path)
    allowed, retry_after = limiter.check(client_ip(request) or "unknown")
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please slow down."},
            headers={"Retry-After": str(retry_after)},
        )

    response = await call_next(request)

    if response.status_code == HTTPStatus.UNAUTHORIZED:
        # A 401 means the session cookies the caller sent did not work, so the
        # browser is told to drop them. Without this a dead refresh token would
        # be replayed (and re-sent to Supabase Auth) on every later request.
        clear_session_cookies(response)

    if request.url.path.startswith(settings.api_prefix):
        # Which API surface answered, so a client can confirm the version it hit.
        response.headers.setdefault("X-API-Version", settings.api_version)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    return response


async def supabase_not_configured_handler(
    request: Request, exc: SupabaseNotConfiguredError
) -> JSONResponse:
    """Return a clean 503 instead of a raw 500 when Supabase env vars are missing."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def create_app() -> FastAPI:
    """Build the application: middleware, shared handlers, then the modules."""
    application = FastAPI(
        title=settings.app_name,
        description="Backend API for PAS",
        version=settings.app_version,
        # Docs are registered by the system module so they can be authenticated.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # A wildcard cannot be combined with credentials per spec, so credentials are
    # only enabled once explicit origins are configured.
    origins = settings.cors_origin_list
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        # Custom response headers a cross-origin client is allowed to read.
        expose_headers=["X-API-Version", "Retry-After"],
    )

    application.middleware("http")(security_middleware)
    application.add_exception_handler(SupabaseNotConfiguredError, supabase_not_configured_handler)

    # Every feature module, mounted under the API version prefix by the registry.
    registry.mount(application)
    # Which API surfaces are served, for /info to advertise (clients can discover
    # whether an older version they depend on is still answering).
    application.state.api_versions = registry.mounted_versions()

    return application


app = create_app()
