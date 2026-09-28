"""Tests for API versioning and for the release version's single source.

Two separate things are versioned and they must not be confused:

* the **release** (`app.__version__` → packaging, `/info`, `/health`), which
  changes on every deploy;
* the **API version** (`/api/v1`), which is the URL clients depend on and must
  only ever change deliberately.

The API version is pinned here on purpose: bumping it silently would break every
existing client, so it should fail a test first.
"""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import app as app_package
from app.core.config import Settings, settings
from app.main import app
from app.modules import registry
from app.modules.registry import API_VERSION, MODULES, ModuleSpec, mounted_versions, prefix_for

#: The API surface this deployment serves. Deliberately a literal.
DEFAULT_API_VERSION = "v1"

DEPLOYMENT_PATHS = ("/", "/info", "/health")


@pytest.fixture()
def anon():
    return TestClient(app)


# --- API versioning --------------------------------------------------------


def test_default_api_version_is_pinned():
    assert settings.api_version == API_VERSION == DEFAULT_API_VERSION
    assert settings.api_prefix == f"/api/{DEFAULT_API_VERSION}"


def test_prefix_for_builds_the_url_surface():
    assert prefix_for(None) == ""
    assert prefix_for("v1") == "/api/v1"
    assert prefix_for("v2") == "/api/v2"


def test_api_prefix_comes_from_configuration():
    assert Settings(api_version="v2").api_prefix == "/api/v2"
    # A stray slash must not produce "//api".
    assert Settings(api_version="/v1/").api_prefix == "/api/v1"


def test_feature_routes_live_under_the_version_prefix(anon):
    documented = app.openapi()["paths"]
    assert f"{settings.api_prefix}/customers" in documented
    assert "/customers" not in documented
    assert anon.get(f"{settings.api_prefix}/customers").status_code == 401


def test_unversioned_api_paths_are_gone(anon):
    """The old unversioned surface does not linger as an alias."""
    for path in ("/customers", "/providers", "/api-keys", "/billing/summary", "/sessions"):
        assert anon.get(path).status_code == 404, path


def test_unknown_api_version_is_not_served(anon):
    assert anon.get("/api/v2/customers").status_code == 404
    assert anon.get("/api/customers").status_code == 404


def test_deployment_routes_stay_unversioned(anon):
    """Monitoring and bookmarks must survive an API version bump."""
    for path in DEPLOYMENT_PATHS:
        assert anon.get(path).status_code == 200, path


def test_system_module_is_the_only_unversioned_one():
    versions = {spec.name: spec.version for spec in MODULES}
    assert versions["system"] is None
    assert all(v == API_VERSION for name, v in versions.items() if name != "system")


def test_mounted_versions_reports_every_live_surface():
    assert mounted_versions() == [DEFAULT_API_VERSION]


def test_a_second_version_can_be_published_alongside_the_first(monkeypatch):
    """Publishing v2 is additive: v1 keeps answering for existing clients."""
    v2 = APIRouter(prefix="/ping", tags=["v2"])

    @v2.get("")
    def ping() -> dict:  # pragma: no cover - trivial handler
        return {"ok": True}

    monkeypatch.setattr(
        registry,
        "MODULES",
        (
            ModuleSpec("v1.ping", registry.MODULES[1].router, version="v1"),
            ModuleSpec("v2.ping", v2, version="v2"),
        ),
    )
    application = FastAPI()
    registry.mount(application)
    client = TestClient(application)

    assert client.get("/api/v1/auth/me").status_code == 401  # v1 still serves
    assert client.get("/api/v2/ping").status_code == 200
    assert mounted_versions() == ["v2", "v1"]


# --- HTTP surface of the versioning ---------------------------------------


def test_info_advertises_the_versions(anon):
    body = anon.get("/info").json()
    assert body["api_version"] == settings.api_version
    assert body["api_base"] == settings.api_prefix
    assert body["api_versions"] == [settings.api_version]
    assert body["version"] == app_package.__version__


def test_health_reports_both_versions(anon):
    body = anon.get("/health").json()
    assert body["status"] == "healthy"
    assert body["version"] == app_package.__version__
    assert body["api_version"] == settings.api_version


def test_versioned_responses_carry_the_api_version_header(anon):
    response = anon.get(f"{settings.api_prefix}/customers")
    assert response.headers["x-api-version"] == settings.api_version


def test_deployment_routes_do_not_claim_an_api_version(anon):
    assert "x-api-version" not in anon.get("/health").headers


def test_auth_paths_keep_the_tighter_rate_limit_under_the_prefix():
    """The credential-stuffing budget must follow the endpoints into /api/v1.

    Regression guard: matching the old unversioned paths exactly left sign-in
    on the loose general budget, silently disabling the protection.
    """
    import app.main as main_module

    for suffix in ("/auth/login", "/auth/signup", "/portal/login", "/portal/me"):
        assert (
            main_module._limiter_for(f"{settings.api_prefix}{suffix}") is main_module.auth_limiter
        )
    for path in (f"{settings.api_prefix}/customers", "/health", "/", "/info"):
        assert main_module._limiter_for(path) is main_module.default_limiter


def test_cors_exposes_the_version_header(anon):
    """A cross-origin client could not read the header otherwise."""
    response = anon.get("/health", headers={"Origin": "https://anything.example"})
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-api-version" in exposed


# --- the release version has one source -----------------------------------


def test_release_version_is_not_duplicated_in_pyproject():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "app.__version__"}


def test_settings_default_to_the_code_version():
    assert settings.app_version == app_package.__version__


def test_installed_metadata_matches_the_code_version():
    try:
        packaged = installed_version("pas-backend")
    except PackageNotFoundError:  # pragma: no cover - tests without an install
        pytest.skip("project metadata is not installed in this environment")
    assert packaged == app_package.__version__
