"""Tests for the Supabase client factory and its configuration guards.

The app must start (and serve `/health`) without Supabase credentials, so the
failure mode matters as much as the happy path: a missing configuration is a
clean `SupabaseNotConfiguredError`, never a crash at import time.
"""

import pytest

import app.core.clients.supabase as supabase_module
from app.core.clients.supabase import (
    SupabaseNotConfiguredError,
    get_client_for_token,
    get_supabase_client,
    is_supabase_configured,
)
from app.core.config import settings


@pytest.fixture()
def unconfigured(monkeypatch):
    """Pretend the credentials are missing, as on a fresh clone."""
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_anon_key", "")
    get_supabase_client.cache_clear()
    yield
    get_supabase_client.cache_clear()


@pytest.fixture()
def configured(monkeypatch):
    """Pretend the credentials are set."""
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_anon_key", "anon-key")
    get_supabase_client.cache_clear()
    yield
    get_supabase_client.cache_clear()


class _Recorder:
    """Captures the token PostgREST was authorised with."""

    def __init__(self) -> None:
        self.tokens: list[str] = []

    def auth(self, token: str) -> None:
        self.tokens.append(token)


class _FakeClient:
    def __init__(self) -> None:
        self.postgrest = _Recorder()


def test_is_supabase_configured_follows_the_credentials(unconfigured):
    assert is_supabase_configured() is False


def test_is_supabase_configured_with_credentials(configured):
    assert is_supabase_configured() is True


def test_get_supabase_client_says_what_is_missing(unconfigured):
    with pytest.raises(SupabaseNotConfiguredError) as excinfo:
        get_supabase_client()

    message = str(excinfo.value)
    assert "SUPABASE_URL" in message
    assert "SUPABASE_ANON_KEY" in message


def test_get_supabase_client_is_created_once(configured, monkeypatch):
    """The auth-flow client is cached; a per-request client would leak sessions."""
    created: list[tuple[str, str]] = []

    def build(url: str, key: str):
        created.append((url, key))
        return _FakeClient()

    monkeypatch.setattr(supabase_module, "create_client", build)

    assert get_supabase_client() is get_supabase_client()
    assert created == [("https://project.supabase.co", "anon-key")]


def test_get_client_for_token_refuses_without_credentials(unconfigured):
    with pytest.raises(SupabaseNotConfiguredError):
        get_client_for_token("the-jwt")


def test_get_client_for_token_binds_the_callers_jwt(configured, monkeypatch):
    """RLS only runs as the caller if the caller's token reaches PostgREST."""
    fake = _FakeClient()
    monkeypatch.setattr(supabase_module, "create_client", lambda url, key: fake)

    client = get_client_for_token("the-jwt")

    assert client is fake
    assert fake.postgrest.tokens == ["the-jwt"]
    # A fresh client per request: the token must not leak into the next one.
    assert get_client_for_token("another-jwt") is fake
    assert fake.postgrest.tokens == ["the-jwt", "another-jwt"]
