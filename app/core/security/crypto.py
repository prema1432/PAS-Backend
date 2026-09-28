"""Encryption for provider API keys.

Provider keys are the most sensitive thing this app stores: they authorise
spending on third-party accounts. They are encrypted with Fernet (AES-128-CBC +
HMAC) before they ever reach the database, and the plaintext is never returned by
any endpoint — responses carry a ``sk-...4f2a`` style hint instead.

The key material comes from ``APP_ENCRYPTION_KEY``; any string is accepted and
hashed into a valid Fernet key, so operators do not have to generate a
specifically-formatted value.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

HINT_LENGTH = 4


class EncryptionNotConfiguredError(RuntimeError):
    """Raised when APP_ENCRYPTION_KEY is missing but encryption is required."""


class DecryptionError(RuntimeError):
    """Raised when a stored ciphertext cannot be decrypted."""


def _fernet(key_material: str) -> Fernet:
    """Build a Fernet instance from arbitrary key material."""
    digest = hashlib.sha256(key_material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, key_material: str) -> str:
    """Encrypt ``value`` into an opaque token."""
    if not key_material:
        raise EncryptionNotConfiguredError(
            "APP_ENCRYPTION_KEY is not set; refusing to store a provider API key."
        )
    return _fernet(key_material).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, key_material: str) -> str:
    """Decrypt a token produced by :func:`encrypt_secret`."""
    if not key_material:
        raise EncryptionNotConfiguredError(
            "APP_ENCRYPTION_KEY is not set; cannot decrypt a stored API key."
        )
    try:
        return _fernet(key_material).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise DecryptionError(
            "Stored API key could not be decrypted — was APP_ENCRYPTION_KEY rotated?"
        ) from exc


def mask_secret(value: str) -> str:
    """Return a display hint: the last few characters only."""
    if not value:
        return "••••"
    tail = value[-HINT_LENGTH:] if len(value) > HINT_LENGTH else value
    return f"••••{tail}"


def encrypt_api_key(value: str, key_material: str = "") -> tuple[str, str]:
    """Return ``(ciphertext, hint)`` for a provider API key.

    The hint is only the trailing characters, safe to display and to store in a
    non-encrypted column.
    """
    key_material = key_material or settings.app_encryption_key
    return encrypt_secret(value, key_material), (value[-HINT_LENGTH:] if value else "")
