"""Provider API keys.

The plaintext key is accepted once, encrypted immediately, and never returned by
any endpoint — responses only ever carry a masked hint. Storing keys requires
``APP_ENCRYPTION_KEY``; without it these endpoints report 503 rather than writing
a secret in the clear.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.clients.supabase import SupabaseNotConfiguredError
from app.core.dependencies import get_current_client, get_current_user
from app.core.security.crypto import EncryptionNotConfiguredError, encrypt_api_key
from app.modules.api_keys.schemas import ApiKeyCreate, ApiKeyUpdate
from app.modules.api_keys.service import API_KEYS, PROVIDERS, decorate, fetch

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.get("")
def list_api_keys(
    user: dict = Depends(get_current_user), client=Depends(get_current_client)
) -> list:
    """List stored API keys — masked, never the secret itself."""
    keys = fetch(client, user["id"], API_KEYS)
    keys.sort(key=lambda k: (str(k.get("label") or ""), k.get("id") or 0))
    return decorate(client, user["id"], keys)


@router.post("", status_code=201)
def create_api_key(
    payload: ApiKeyCreate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Store a provider API key, encrypted at rest."""
    if payload.provider_id is not None:
        owned = [
            p for p in fetch(client, user["id"], PROVIDERS) if p.get("id") == payload.provider_id
        ]
        if not owned:
            raise HTTPException(status_code=404, detail="Provider not found")

    try:
        ciphertext, hint = encrypt_api_key(payload.key)
    except EncryptionNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    row = {
        "user_id": user["id"],
        "provider_id": payload.provider_id,
        "label": payload.label,
        "tier": payload.tier,
        "key_ciphertext": ciphertext,
        "key_hint": hint,
        "is_active": True,
    }
    try:
        response = client.table(API_KEYS).insert(row).execute()
    except SupabaseNotConfiguredError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not response.data:
        raise HTTPException(status_code=500, detail="Failed to store API key")

    return decorate(client, user["id"], [response.data[0]])[0]


@router.patch("/{key_id}")
def update_api_key(
    key_id: int,
    payload: ApiKeyUpdate,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> dict:
    """Update a key's label, provider, tier or active flag — never its value."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    response = (
        client.table(API_KEYS).update(updates).eq("id", key_id).eq("user_id", user["id"]).execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="API key not found")
    return decorate(client, user["id"], [response.data[0]])[0]


@router.delete("/{key_id}", status_code=204)
def delete_api_key(
    key_id: int,
    user: dict = Depends(get_current_user),
    client=Depends(get_current_client),
) -> None:
    """Delete a stored API key."""
    response = client.table(API_KEYS).delete().eq("id", key_id).eq("user_id", user["id"]).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="API key not found")
