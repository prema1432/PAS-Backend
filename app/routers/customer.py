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

from fastapi import APIRouter, HTTPException, Request, status

from app.auth import create_jwt
from app.config import settings
from app.database import get_db
from app.login_event import record_login_event
from app.models import (
    CustomerDocument,
    CustomerLoginRequest,
    CustomerLoginResponse,
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
    expiry = doc.get("time_expiry")
    remaining = doc.get("time_remaining_seconds", 0)
    if expiry is None and remaining == 0:
        return True  # free account, no expiry set yet
    now = datetime.now(tz=timezone.utc)
    if expiry:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if now > expiry:
            return False
    if remaining < 0:
        return False
    return True


def _check_otp(doc: dict, submitted: str) -> None:
    """Raise HTTPException if OTP is wrong or expired."""
    stored = doc.get("otp")
    otp_exp = doc.get("otp_expires_at")

    if not stored or stored != submitted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP.",
        )
    if otp_exp is not None:
        if otp_exp.tzinfo is None:
            otp_exp = otp_exp.replace(tzinfo=timezone.utc)
        if datetime.now(tz=timezone.utc) > otp_exp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OTP has expired. Request a new one.",
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

    existing = await col.find_one({"phone_number": body.phone_number})

    # ------------------------------------------------------------------
    # NEW customer — create record, issue JWT immediately
    # ------------------------------------------------------------------
    if existing is None:
        otp = _generate_otp()
        session_id = str(uuid.uuid4())

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
            time_remaining_seconds=0,
            time_expiry=None,
            last_login=now,
            login_session_id=session_id,
            device_id=body.device_id,
        )
        await col.insert_one(new_doc.model_dump())

        token = create_jwt(body.phone_number, session_id)
        await record_login_event(
            request=request,
            customer_id=str(new_doc.model_dump().get("_id", session_id)),
            phone_number=body.phone_number,
            session_id=session_id,
        )
        return CustomerLoginResponse(
            is_new_customer=True,
            message="New account created. OTP sent (deliver via SMS in production).",
            otp=otp,
            jwt_token=token,
        )

    # ------------------------------------------------------------------
    # EXISTING customer — check subscription
    # ------------------------------------------------------------------
    if not _subscription_active(existing):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Free/Paid limit has expired. Please recharge.",
        )

    # Step 2: OTP provided — validate and issue JWT
    if body.otp is not None:
        _check_otp(existing, body.otp)

        session_id = str(uuid.uuid4())
        await col.update_one(
            {"phone_number": body.phone_number},
            {
                "$set": {
                    "last_login": now,
                    "login_session_id": session_id,
                    "device_id": body.device_id or existing.get("device_id"),
                    "updated_by": body.phone_number,
                    "updated_at": now,
                }
            },
        )
        token = create_jwt(body.phone_number, session_id)
        await record_login_event(
            request=request,
            customer_id=str(existing.get("_id", "")),
            phone_number=body.phone_number,
            session_id=session_id,
        )
        return CustomerLoginResponse(
            is_new_customer=False,
            message="Login successful.",
            otp=body.otp,
            jwt_token=token,
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


