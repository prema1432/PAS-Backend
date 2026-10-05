"""
Customer router — single login endpoint (two-step in one API).

Step 1: POST /customer/login  with phone_number only  → generates OTP, returns it.
Step 2: POST /customer/login  with phone_number + otp → validates OTP, returns JWT.

New customers get a JWT immediately on Step 1 (no OTP needed).
"""

import random
import string
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import create_customer_tokens, create_jwt, decode_jwt, get_current_customer
from app.config import settings
from app.database import get_db
from app.login_event import record_login_event
from app.models import (
    CustomerDocument,
    CustomerHeartbeatRequest,
    CustomerHeartbeatResponse,
    CustomerLoginRequest,
    CustomerLoginResponse,
    CustomerProfileResponse,
    CustomerRefreshTokenRequest,
    CustomerRefreshTokenResponse,
    PaymentType,
    SourceType,
)

router = APIRouter(prefix="/customer", tags=["customer"])

COLLECTION = "customers"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))


def _generate_referral_code(phone_number: str) -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"PAS{phone_number[-4:]}{suffix}"


def _otp_expiry() -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(seconds=settings.OTP_EXPIRE_SECONDS)


def _subscription_active(doc: dict) -> bool:
    remaining = doc.get("time_remaining_seconds", 0)
    if remaining <= 0:
        return False
    expiry = doc.get("time_expiry")
    if expiry:
        now = datetime.now(tz=timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if now > expiry:
            return False
    return True


def _check_otp(doc: dict, submitted: str) -> None:
    """Raise HTTPException if OTP is wrong."""
    stored = doc.get("otp")

    if not stored or stored != submitted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP.",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=CustomerLoginResponse,
    summary="Customer login — new or existing",
    status_code=status.HTTP_200_OK,
)
async def customer_login(request: Request, body: CustomerLoginRequest) -> CustomerLoginResponse:
    """
    **Single endpoint** for the full login flow.

    **Step 1** — `phone_number` only, leave `otp` empty:
    - New number → account created, OTP returned + JWT issued immediately.
    - Existing, active → fresh OTP returned (`jwt_token` is null).
    - Existing, expired → HTTP 403.

    **Step 2** — `phone_number` + `otp` from Step 1:
    - Valid OTP → JWT returned, login complete.
    - Wrong / expired OTP → HTTP 400.
    """
    db = get_db()
    col = db[COLLECTION]
    now = datetime.now(tz=timezone.utc)
    phone_filter = {"$in": [body.phone_number, body.phone_number.replace("+91", "")]}
    existing = await col.find_one({"phone_number": phone_filter})

    # ------------------------------------------------------------------
    # NEW customer — create record, consume 5 mins, issue JWT
    # ------------------------------------------------------------------
    if existing is None:
        otp = _generate_otp()
        session_id = str(uuid.uuid4())
        initial_seconds = 1800  # 30 mins trial
        deduct_seconds = 300    # 5 mins consumed on login
        remaining_seconds = initial_seconds - deduct_seconds

        new_doc = CustomerDocument(
            phone_number=body.phone_number,
            otp=otp,
            otp_expires_at=_otp_expiry(),
            source=SourceType.self_,
            payment_type=PaymentType.free,
            activation_date=now,
            created_by=body.phone_number,
            updated_by=body.phone_number,
            referral_code_generated=_generate_referral_code(body.phone_number),
            time_remaining_seconds=remaining_seconds,
            time_expiry=now + timedelta(days=10),
            last_login=now,
            login_session_id=session_id,
            device_id=body.device_id,
        )
        await col.insert_one(new_doc.model_dump())

        # Log usage history
        await db["usage_history"].insert_one({
            "phone_number": body.phone_number,
            "action": "initial_login_consumption",
            "description": f"New account registered. 5 minutes (300s) consumed on device ({body.device_id or 'unknown'}).",
            "minutes_consumed": round(deduct_seconds / 60, 1),
            "seconds_consumed": deduct_seconds,
            "previous_time_remaining_seconds": initial_seconds,
            "new_time_remaining_seconds": remaining_seconds,
            "device_id": body.device_id,
            "session_id": session_id,
            "ip_address": request.client.host if request.client else "unknown",
            "created_at": now,
        })

        tokens = create_customer_tokens(body.phone_number, session_id)
        await record_login_event(
            request=request,
            customer_id=str(new_doc.model_dump().get("_id", session_id)),
            phone_number=body.phone_number,
            session_id=session_id,
        )
        return CustomerLoginResponse(
            is_new_customer=True,
            message="New account created. 5 minutes deducted from subscription balance.",
            otp=otp,
            jwt_token=tokens["access_token"],
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_type=tokens["token_type"],
            expires_in=tokens["expires_in"],
        )

    # ------------------------------------------------------------------
    # EXISTING customer — check subscription and available minutes
    # ------------------------------------------------------------------
    if not _subscription_active(existing):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Free/Paid subscription has expired. Please recharge.",
        )

    if existing.get("time_remaining_seconds", 0) <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No available minutes remaining (0 mins). Please recharge your subscription to log in.",
        )

    # Step 2: OTP provided — validate, force logout previous device, consume 5 mins, issue JWT
    if body.otp is not None:
        _check_otp(existing, body.otp)

        current_seconds = existing.get("time_remaining_seconds", 0)
        if current_seconds <= 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No available minutes remaining (0 mins). Please recharge your subscription to log in.",
            )

        # Force logout previous session if exists
        old_sid = existing.get("login_session_id")
        if old_sid:
            await db["usage_history"].insert_one({
                "phone_number": body.phone_number,
                "action": "device_switch_force_logout",
                "description": f"Previous active session ({old_sid[:8]}…) force-logged out due to login on device ({body.device_id or 'unknown'}).",
                "minutes_consumed": 0,
                "seconds_consumed": 0,
                "previous_time_remaining_seconds": current_seconds,
                "new_time_remaining_seconds": current_seconds,
                "device_id": body.device_id,
                "session_id": old_sid,
                "ip_address": request.client.host if request.client else "unknown",
                "created_at": now,
            })

        # Deduct 5 minutes (300 seconds)
        deduct_seconds = min(current_seconds, 300)
        new_seconds = max(0, current_seconds - deduct_seconds)
        session_id = str(uuid.uuid4())

        await col.update_one(
            {"phone_number": body.phone_number},
            {
                "$set": {
                    "last_login": now,
                    "login_session_id": session_id,
                    "time_remaining_seconds": new_seconds,
                    "device_id": body.device_id or existing.get("device_id"),
                    "updated_by": body.phone_number,
                    "updated_at": now,
                    "otp": None,
                }
            },
        )

        # Log consumption in usage history
        await db["usage_history"].insert_one({
            "phone_number": body.phone_number,
            "action": "login_consumption",
            "description": f"Login verified. 5 minutes (300s) consumed on device ({body.device_id or 'unknown'}).",
            "minutes_consumed": round(deduct_seconds / 60, 1),
            "seconds_consumed": deduct_seconds,
            "previous_time_remaining_seconds": current_seconds,
            "new_time_remaining_seconds": new_seconds,
            "device_id": body.device_id,
            "session_id": session_id,
            "ip_address": request.client.host if request.client else "unknown",
            "created_at": now,
        })

        tokens = create_customer_tokens(body.phone_number, session_id)
        await record_login_event(
            request=request,
            customer_id=str(existing.get("_id", "")),
            phone_number=body.phone_number,
            session_id=session_id,
        )
        return CustomerLoginResponse(
            is_new_customer=False,
            message="Login successful. 5 minutes deducted from subscription balance.",
            otp=body.otp,
            jwt_token=tokens["access_token"],
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            token_type=tokens["token_type"],
            expires_in=tokens["expires_in"],
        )

    # Step 1: No OTP submitted — only generate if DB has no OTP stored
    stored_otp = existing.get("otp")

    if stored_otp is None:
        otp = _generate_otp()
        await col.update_one(
            {"phone_number": body.phone_number},
            {
                "$set": {
                    "otp": otp,
                    "otp_expires_at": _otp_expiry(),
                    "updated_by": body.phone_number,
                    "updated_at": now,
                    "device_id": body.device_id or existing.get("device_id"),
                }
            },
        )
    else:
        otp = stored_otp

    return CustomerLoginResponse(
        is_new_customer=False,
        message="OTP sent. Call this endpoint again with the otp field to receive your JWT.",
        otp=otp,
        jwt_token=None,
    )


@router.get(
    "/me",
    response_model=CustomerProfileResponse,
    summary="Get current customer profile",
    status_code=status.HTTP_200_OK,
)
async def get_me(
    token: dict = Depends(get_current_customer),
) -> CustomerProfileResponse:
    """
    Returns the full profile of the authenticated customer.

    Requires a valid **Bearer token** in the `Authorization` header.
    """
    phone_number: str = token["sub"]

    db = get_db()
    clean_phone = phone_number.replace("+91", "").strip()
    phone_filter = {"$in": [phone_number, clean_phone, f"+91{clean_phone}"]}
    doc = await db[COLLECTION].find_one({"phone_number": phone_filter})

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    return CustomerProfileResponse(
        phone_number=doc["phone_number"],
        source=doc.get("source", SourceType.self_),
        payment_type=doc.get("payment_type", PaymentType.free),
        activation_date=doc.get("activation_date"),
        created_by=doc.get("created_by"),
        updated_by=doc.get("updated_by"),
        referral_code_used=doc.get("referral_code_used"),
        referral_code_generated=doc.get("referral_code_generated"),
        time_remaining_seconds=doc.get("time_remaining_seconds", 0),
        time_expiry=doc.get("time_expiry"),
        last_login=doc.get("last_login"),
        login_session_id=doc.get("login_session_id"),
        device_id=doc.get("device_id"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


@router.post(
    "/heartbeat",
    response_model=CustomerHeartbeatResponse,
    summary="Decrement remaining time and sync session",
    status_code=status.HTTP_200_OK,
)
async def customer_heartbeat(
    request: Request,
    body: CustomerHeartbeatRequest = CustomerHeartbeatRequest(),
    token: dict = Depends(get_current_customer),
) -> CustomerHeartbeatResponse:
    """
    Called periodically by the client to report elapsed seconds and decrement
    time_remaining_seconds in the database.
    """
    phone_number: str = token["sub"]
    db = get_db()
    col = db[COLLECTION]
    now = datetime.now(tz=timezone.utc)

    clean_phone = phone_number.replace("+91", "").strip()
    phone_filter = {"$in": [phone_number, clean_phone, f"+91{clean_phone}"]}
    doc = await col.find_one({"phone_number": phone_filter})

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    current_seconds = doc.get("time_remaining_seconds", 0)
    if current_seconds <= 0:
        return CustomerHeartbeatResponse(
            phone_number=doc["phone_number"],
            time_remaining_seconds=0,
            time_remaining_minutes=0.0,
            is_active=False,
            message="Subscription time balance exhausted (0s remaining).",
        )

    deduct = min(current_seconds, max(1, body.seconds_consumed))
    new_seconds = max(0, current_seconds - deduct)

    set_update = {
        "time_remaining_seconds": new_seconds,
        "updated_at": now,
        "last_login": now,
    }
    if new_seconds <= 0:
        set_update["login_session_id"] = None

    await col.update_one(
        {"_id": doc["_id"]},
        {"$set": set_update},
    )

    # Log usage history
    await db["usage_history"].insert_one({
        "phone_number": doc["phone_number"],
        "action": "session_heartbeat_consumption",
        "description": f"Session heartbeat: consumed {deduct}s ({round(deduct / 60, 1)}m) for activity '{body.activity or 'live_session'}'.",
        "minutes_consumed": round(deduct / 60, 1),
        "seconds_consumed": deduct,
        "previous_time_remaining_seconds": current_seconds,
        "new_time_remaining_seconds": new_seconds,
        "device_id": body.device_id or doc.get("device_id"),
        "session_id": token.get("sid", doc.get("login_session_id")),
        "ip_address": request.client.host if request.client else "unknown",
        "created_at": now,
    })

    return CustomerHeartbeatResponse(
        phone_number=doc["phone_number"],
        time_remaining_seconds=new_seconds,
        time_remaining_minutes=round(new_seconds / 60, 1),
        is_active=new_seconds > 0,
        message="Subscription balance active." if new_seconds > 0 else "Subscription time balance exhausted (0 mins remaining). Please recharge.",
    )


@router.post(
    "/refresh",
    response_model=CustomerRefreshTokenResponse,
    summary="Refresh customer access token (15 mins)",
    status_code=status.HTTP_200_OK,
)
async def refresh_customer_token(body: CustomerRefreshTokenRequest) -> CustomerRefreshTokenResponse:
    """
    Exchange a valid 30-day refresh token for a fresh 15-minute access token.
    """
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_jwt(body.refresh_token)
    except Exception:
        raise exc

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provided token is not a valid refresh token.",
        )

    phone_number = payload.get("sub")
    token_sid = payload.get("sid")
    if not phone_number:
        raise exc

    db = get_db()
    clean_phone = phone_number.replace("+91", "").strip()
    phone_filter = {"$in": [phone_number, clean_phone, f"+91{clean_phone}"]}
    customer = await db[COLLECTION].find_one({"phone_number": phone_filter})

    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")

    # Ensure session matches single active device
    active_sid = customer.get("login_session_id")
    if not active_sid or active_sid != token_sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or customer logged in from another device.",
        )

    # Check if subscription active
    if not _subscription_active(customer) or customer.get("time_remaining_seconds", 0) <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription expired or zero balance remaining.",
        )

    tokens = create_customer_tokens(customer["phone_number"], token_sid)
    return CustomerRefreshTokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=tokens["expires_in"],
        message="Access token refreshed successfully (15 mins).",
    )




