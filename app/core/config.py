"""Application settings loaded from environment variables / .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __version__


class Settings(BaseSettings):
    """Typed settings for PAS Backend.

    Values are read from environment variables; a local `.env` file is loaded
    automatically in development. FastAPI Cloud environment variables set via
    `fastapi cloud env set` are picked up the same way.
    """

    app_name: str = "PAS Backend"
    # The release version, defaulting to the code's own __version__ so the two
    # cannot drift. Only override this to label a build (e.g. a preview deploy).
    app_version: str = __version__

    # --- API versioning ---
    # The URL segment that carries the versioned API surface: "/api/<api_version>".
    # Everything except the deployment routes (/, /info, /health, docs) is served
    # under it, so a breaking change can ship as "v2" while "v1" keeps answering.
    api_version: str = "v1"

    supabase_url: str = ""
    supabase_anon_key: str = ""

    # --- CORS ---
    # Comma-separated list of allowed origins. "*" allows any origin (the
    # credential-less default, fine for local development). In production set
    # the exact origins, e.g. "https://app.example.com,https://admin.example.com".
    cors_origins: str = "*"

    # --- API docs protection ---
    # When either is empty the interactive docs (and the OpenAPI schema) are
    # disabled entirely. Set both to publish them behind HTTP Basic auth.
    docs_username: str = ""
    docs_password: str = ""

    # --- Rate limiting (per client IP, per minute) ---
    rate_limit_per_minute: int = 120
    # Sign-in / sign-up get a much tighter budget to blunt credential stuffing.
    auth_rate_limit_per_minute: int = 8

    # --- Provider API key encryption ---
    # Any high-entropy string; it is hashed into a Fernet key. When empty,
    # storing provider API keys is refused (the rest of the app still works).
    app_encryption_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def api_prefix(self) -> str:
        """URL prefix of the versioned API, e.g. ``/api/v1``."""
        return f"/api/{self.api_version.strip('/')}"

    @property
    def cors_origin_list(self) -> list[str]:
        """Allowed CORS origins as a list."""
        if not self.cors_origins or self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def docs_enabled(self) -> bool:
        """Docs are only served when credentials are configured."""
        return bool(self.docs_username and self.docs_password)


settings = Settings()
