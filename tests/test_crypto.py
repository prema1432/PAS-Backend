"""Tests for provider API key encryption and masking."""

import pytest

from app.core.config import settings
from app.core.security.crypto import (
    DecryptionError,
    EncryptionNotConfiguredError,
    decrypt_secret,
    encrypt_api_key,
    encrypt_secret,
    mask_secret,
)

KEY = "unit-test-key-material"
SECRET = "sk-or-v1-abcdef0123456789"


def test_round_trip_recovers_the_secret():
    token = encrypt_secret(SECRET, KEY)
    assert token != SECRET
    assert SECRET not in token
    assert decrypt_secret(token, KEY) == SECRET


def test_ciphertext_is_randomised():
    """Fernet embeds a random IV, so the same input never yields the same token."""
    assert encrypt_secret("same-value", KEY) != encrypt_secret("same-value", KEY)


def test_encrypting_without_a_key_is_refused():
    with pytest.raises(EncryptionNotConfiguredError):
        encrypt_secret(SECRET, "")


def test_decrypting_without_a_key_is_refused():
    with pytest.raises(EncryptionNotConfiguredError):
        decrypt_secret("token", "")


def test_wrong_key_cannot_decrypt():
    token = encrypt_secret(SECRET, KEY)
    with pytest.raises(DecryptionError):
        decrypt_secret(token, "a-different-key")


def test_mask_reveals_only_the_tail():
    assert mask_secret(SECRET) == "••••6789"
    assert mask_secret("") == "••••"


def test_encrypt_api_key_returns_hint_and_ciphertext():
    ciphertext, hint = encrypt_api_key(SECRET, KEY)
    assert hint == "6789"
    assert ciphertext != SECRET
    assert decrypt_secret(ciphertext, KEY) == SECRET


def test_encrypt_api_key_falls_back_to_settings(monkeypatch):
    monkeypatch.setattr(settings, "app_encryption_key", "key-from-settings")
    ciphertext, hint = encrypt_api_key("sk-live-9999")
    assert hint == "9999"
    assert decrypt_secret(ciphertext, "key-from-settings") == "sk-live-9999"
