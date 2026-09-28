"""Supabase client factory.

The client is created lazily on first use so the app can start (and serve
non-database endpoints like /health) even when Supabase credentials are not
configured yet. Reuses a single client instance per configuration.
"""

from functools import cache

from app.core.config import settings
from supabase import Client, create_client


class SupabaseNotConfiguredError(RuntimeError):
    """Raised when Supabase credentials are missing."""


@cache
def get_supabase_client() -> Client:
    """Return a cached Supabase client.

    Raises SupabaseNotConfiguredError (handled as HTTP 503) if credentials
    are missing.
    """
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise SupabaseNotConfiguredError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY "
            "environment variables (see .env.example), or set them on FastAPI Cloud "
            "with: fastapi cloud env set --secret SUPABASE_ANON_KEY <key>"
        )
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def is_supabase_configured() -> bool:
    """Check whether Supabase credentials are present without creating a client."""
    return bool(settings.supabase_url and settings.supabase_anon_key)


def get_client_for_token(access_token: str) -> Client:
    """Return a client whose data requests run as the token's owner.

    Data routes must use this instead of the shared cached client: sending the
    caller's JWT makes Postgres RLS evaluate as that user (`auth.uid()`), so one
    request can never read or write another user's rows. The client is created
    per request on purpose - the token must not leak across requests.
    """
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise SupabaseNotConfiguredError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY "
            "environment variables (see .env.example)."
        )
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client
