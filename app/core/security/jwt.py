"""Read the public claims of a JWT access token.

Supabase issues the JWT and verifies its signature on every data request, so
nothing here is a security check. These helpers only decode the base64 payload
so the app can tell *when* an access token is about to expire and refresh it
proactively — one saved round-trip per expired request, and it keeps expired
tokens from being sent to PostgREST at all.

Everything degrades gracefully: an opaque (non-JWT) token simply yields no
claims, and the caller falls back to validating it with Supabase.
"""

import base64
import binascii
import json
import time

# Refresh a little before the real expiry so a request in flight cannot land
# just after the token dies.
DEFAULT_LEEWAY_SECONDS = 30


def decode_claims(token: str | None) -> dict:
    """Return the JWT payload as a dict (empty when it cannot be decoded)."""
    if not token or token.count(".") != 2:
        return {}
    payload = token.split(".")[1]
    # JWT uses base64url without padding.
    padded = payload + "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        claims = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def expires_at(token: str | None) -> int | None:
    """Unix expiry time of a token, or None when the token carries no `exp`."""
    value = decode_claims(token).get("exp")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def seconds_until_expiry(token: str | None) -> float | None:
    """Seconds left before `exp` (negative once expired, None when unknown)."""
    expiry = expires_at(token)
    return None if expiry is None else expiry - time.time()


def is_expired(token: str | None, leeway_seconds: int = DEFAULT_LEEWAY_SECONDS) -> bool:
    """True when the token is expired or expires within the leeway window.

    A token without a readable `exp` is reported as *not* expired: we have no
    evidence it is stale, and Supabase remains the authority on validity.
    """
    remaining = seconds_until_expiry(token)
    return remaining is not None and remaining <= leeway_seconds
