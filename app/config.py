"""Application settings loaded from environment variables / .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings for PAS Backend.

    Values are read from environment variables; a local `.env` file is loaded
    automatically in development. FastAPI Cloud environment variables set via
    `fastapi cloud env set` are picked up the same way.
    """

    app_name: str = "PAS Backend"
    app_version: str = "0.2.0"

    supabase_url: str = ""
    supabase_anon_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
