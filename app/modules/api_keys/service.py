"""API keys service: the only place a stored key row is serialised.

Keys are encrypted at rest, so there is nothing to "strip" before returning a
row — the payload is built field by field from an allow-list. That way a
sensitive column added to the table later cannot leak by default, and the
plaintext secret is never part of any code path that produces a response.
"""

API_KEYS = "api_keys"
PROVIDERS = "providers"
LIMIT = 500


def mask(hint: str | None) -> str:
    """Display form of a stored key hint."""
    return f"••••{hint}" if hint else "••••"


def fetch(client, user_id: str, table: str) -> list:
    """All rows of a table owned by the signed-in user."""
    return client.table(table).select("*").eq("user_id", user_id).limit(LIMIT).execute().data


def public_key(key: dict, provider_name: str | None) -> dict:
    """Serialise a key row from an allow-list of non-secret columns."""
    return {
        "id": key.get("id"),
        "provider_id": key.get("provider_id"),
        "provider_name": provider_name,
        "label": key.get("label"),
        "key_masked": mask(key.get("key_hint")),
        "tier": key.get("tier") or "free",
        "is_active": bool(key.get("is_active", True)),
        "last_used_at": key.get("last_used_at"),
        "created_at": key.get("created_at"),
    }


def decorate(client, user_id: str, keys: list[dict]) -> list[dict]:
    """Mask each key and attach its provider name."""
    names = {p.get("id"): p.get("name") for p in fetch(client, user_id, PROVIDERS)}
    return [public_key(key, names.get(key.get("provider_id"))) for key in keys]
