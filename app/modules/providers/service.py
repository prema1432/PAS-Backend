"""Providers service: catalogue reads and the free/paid preference ordering.

The provider order is what the rest of the system uses to decide which LLM to
call: lower ``priority`` first, then name. A provider's models and the number of
keys configured for it are attached for the API keys screen.
"""

from collections import Counter

PROVIDERS = "providers"
MODELS = "provider_models"
API_KEYS = "api_keys"
LIMIT = 500


def fetch(client, user_id: str, table: str) -> list:
    """All rows of a table owned by the signed-in user."""
    return client.table(table).select("*").eq("user_id", user_id).limit(LIMIT).execute().data


def preference_sort(providers: list[dict]) -> list[dict]:
    """Order by priority (lower first), then name — the fallback order."""
    return sorted(providers, key=lambda p: (p.get("priority") or 0, str(p.get("name") or "")))


def with_models(client, user_id: str, providers: list[dict]) -> list[dict]:
    """Attach each provider's models and how many keys it has configured."""
    models = fetch(client, user_id, MODELS)
    keys = fetch(client, user_id, API_KEYS)

    by_provider: dict = {}
    for model in models:
        by_provider.setdefault(model.get("provider_id"), []).append(model)
    key_counts = Counter(key.get("provider_id") for key in keys)

    for provider in providers:
        provider["models"] = sorted(
            by_provider.get(provider["id"], []), key=lambda m: str(m.get("name") or "")
        )
        provider["key_count"] = key_counts.get(provider["id"], 0)
    return providers


def public_preferences(providers: list[dict]) -> list[dict]:
    """The slim preference-list projection (the first entry is tried first)."""
    return [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "slug": p.get("slug"),
            "is_free": bool(p.get("is_free")),
            "is_paid": bool(p.get("is_paid")),
            "preferred_tier": p.get("preferred_tier"),
            "priority": p.get("priority"),
        }
        for p in providers
    ]
