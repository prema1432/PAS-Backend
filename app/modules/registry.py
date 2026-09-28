"""The module registry: what gets mounted, and at which API version.

Each feature module declares its own prefix and tags on its `APIRouter`. The
registry adds the one prefix a module should not pick for itself — the API
version — and mounts the lot, so adding a feature is a package plus one line
here.

Versioning
----------
A module's `version` is the URL surface it answers on:``/api/<version>`` for
feature modules (``/api/v1`` by default, from `API_VERSION`), or ``None`` for
the deployment routes (`/`, `/info`, `/health` and the docs) which must never
move, because monitoring and bookmarks point at them.

Publishing a breaking change is therefore additive: bump `API_VERSION`, write a
``v2`` router, and keep the ``v1`` entries below pointing at the old routers
(with ``version="v1"``) so existing clients keep working. `mounted_versions()`
reports every surface a deployment still serves.
"""

import logging
from typing import NamedTuple

from fastapi import APIRouter, FastAPI

from app.core.config import settings
from app.modules.analytics.router import router as analytics_router
from app.modules.api_keys.router import router as api_keys_router
from app.modules.audit_logs.router import router as audit_logs_router
from app.modules.auth.router import router as auth_router
from app.modules.billing.router import router as billing_router
from app.modules.customer_portal.router import router as customer_portal_router
from app.modules.customers.router import router as customers_router
from app.modules.providers.router import models_router as models_router
from app.modules.providers.router import router as providers_router
from app.modules.sessions.router import router as sessions_router
from app.modules.system.router import router as system_router

logger = logging.getLogger(__name__)

#: The version every module is mounted at unless it says otherwise.
API_VERSION = settings.api_version


class ModuleSpec(NamedTuple):
    """One mounted module.

    ``version`` is the API surface it answers on — ``API_VERSION`` for feature
    modules — or ``None`` to serve it at the root (unversioned).
    """

    name: str
    router: APIRouter
    version: str | None = API_VERSION


# Order matters only for path precedence within an app, which each module
# already handles (e.g. `/providers/preferences` is declared before
# `/providers/{provider_id}`).
MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec("system", system_router, version=None),
    ModuleSpec("auth", auth_router),
    ModuleSpec("customers", customers_router),
    ModuleSpec("customer_portal", customer_portal_router),
    ModuleSpec("providers", providers_router),
    ModuleSpec("providers.models", models_router),
    ModuleSpec("api_keys", api_keys_router),
    ModuleSpec("billing", billing_router),
    ModuleSpec("sessions", sessions_router),
    ModuleSpec("analytics", analytics_router),
    ModuleSpec("audit_logs", audit_logs_router),
)


def prefix_for(version: str | None) -> str:
    """URL prefix a module is mounted under (``""`` when unversioned)."""
    return f"/api/{version}" if version else ""


def mounted_versions() -> list[str]:
    """Every API version this deployment still serves, newest first."""
    return sorted({spec.version for spec in MODULES if spec.version}, reverse=True)


def mount(application: FastAPI) -> None:
    """Attach every module to ``application`` under its version prefix."""
    for spec in MODULES:
        prefix = prefix_for(spec.version)
        application.include_router(spec.router, prefix=prefix)
        logger.debug("Mounted module %s at %s", spec.name, prefix or "/")
