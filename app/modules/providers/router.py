"""Providers and their models: the LLM catalogue plus the free/paid preference
order used to pick a provider.

Every row is scoped to the signed-in user.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import get_current_client, get_current_user
from app.modules.providers.schemas import (
    ModelCreate,
    ModelUpdate,
    ProviderCreate,
    ProviderUpdate,
)
from app.modules.providers.service import (
    MODELS,
    PROVIDERS,
    fetch,
    preference_sort,
    public_preferences,
    with_models,
)

router = APIRouter(prefix="/providers", tags=["providers"])
models_router = APIRouter(prefix="/models", tags=["providers"])


@router.get("")
def list_providers(
    user: dict = Depends(get_current_user), client=Depends(get_current_client)
) -> list:
    """List providers with their models and key counts, in preference order."""
    providers = preference_sort(fetch(client, user["id"], PROVIDERS))
    return with_models(client, user["id"], providers)


@router.get("/preferences")
def provider_preferences(
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
    tier: str | None = Query(default=None, pattern="^(free|paid)$"),
    active_only: bool = Query(default=True),
) -> list:
    """The ordered provider preference list, optionally for one tier only.

    The first entry is the provider that should be tried first for that tier.
    """
    providers = preference_sort(fetch(client, user["id"], PROVIDERS))
    if active_only:
        providers = [p for p in providers if p.get("is_active")]
    if tier:
        providers = [p for p in providers if p.get(f"is_{tier}")]
    return public_preferences(providers)


@router.post("", status_code=201)
def create_provider(
    payload: ProviderCreate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Register a provider (e.g. OpenRouter, OpenAI)."""
    existing = fetch(client, user["id"], PROVIDERS)
    if any(p.get("slug") == payload.slug for p in existing):
        raise HTTPException(status_code=409, detail="A provider with this slug already exists")

    row = payload.model_dump() | {"user_id": user["id"], "is_active": True}
    response = client.table(PROVIDERS).insert(row).execute()
    if not response.data:
        raise HTTPException(status_code=500, detail="Failed to create provider")
    provider = response.data[0]
    provider["models"] = []
    provider["key_count"] = 0
    return provider


@router.patch("/{provider_id}")
def update_provider(
    provider_id: int,
    payload: ProviderUpdate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Update a provider's details, tier flags or preference priority."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    response = (
        client.table(PROVIDERS)
        .update(updates)
        .eq("id", provider_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Provider not found")
    return response.data[0]


@router.delete("/{provider_id}", status_code=204)
def delete_provider(
    provider_id: int,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> None:
    """Delete a provider (its models and keys cascade)."""
    response = (
        client.table(PROVIDERS).delete().eq("id", provider_id).eq("user_id", user["id"]).execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Provider not found")


@router.post("/{provider_id}/models", status_code=201)
def create_model(
    provider_id: int,
    payload: ModelCreate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Add a model to a provider (free and/or paid)."""
    owned = [p for p in fetch(client, user["id"], PROVIDERS) if p.get("id") == provider_id]
    if not owned:
        raise HTTPException(status_code=404, detail="Provider not found")

    row = payload.model_dump() | {
        "user_id": user["id"],
        "provider_id": provider_id,
        "is_active": True,
    }
    response = client.table(MODELS).insert(row).execute()
    if not response.data:
        raise HTTPException(status_code=500, detail="Failed to create model")
    return response.data[0]


@router.get("/{provider_id}/models")
def list_models(
    provider_id: int,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> list:
    """List one provider's models."""
    models = [
        model
        for model in fetch(client, user["id"], MODELS)
        if model.get("provider_id") == provider_id
    ]
    return sorted(models, key=lambda m: str(m.get("name") or ""))


@models_router.patch("/{model_id}")
def update_model(
    model_id: int,
    payload: ModelUpdate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Update a model's details or tier flags."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    response = (
        client.table(MODELS).update(updates).eq("id", model_id).eq("user_id", user["id"]).execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Model not found")
    return response.data[0]


@models_router.delete("/{model_id}", status_code=204)
def delete_model(
    model_id: int,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> None:
    """Delete a model."""
    response = client.table(MODELS).delete().eq("id", model_id).eq("user_id", user["id"]).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Model not found")
