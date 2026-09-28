"""Customer portal: customers sign in with their own phone number + OTP.

This is the customers' own API, separate from the owner dashboard. The owner
manages customers (and their OTPs) under ``/customers``; the customer then uses
those two values here to see **only their own** profile, balance and plan.

There are no session cookies on this surface: the login answers with the
profile and nothing else. That is deliberate for a first cut — the endpoints
below are read-only, so there is nothing to protect with a session yet. When a
write appears here, it must arrive with a real session (a customer token table
or Supabase anonymous auth), not with the OTP replayed as a credential.

Unlike every other route in this application, these endpoints are reachable
without a Supabase session — the phone + OTP pair **is** the credential. That
makes them the most exposed surface in the app, so they carry the same tight
rate-limit budget as password sign-in (see ``app/main.py``).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.clients.supabase import get_supabase_client
from app.core.http.ip import client_ip
from app.core.security.rate_limit import auth_limiter
from app.modules.customer_portal.schemas import CustomerLoginRequest, CustomerProfile
from app.modules.customer_portal.service import (
    CustomerAuthError,
    customer_profile,
    find_customer_by_phone,
    verify_otp,
)

router = APIRouter(prefix="/portal", tags=["customer-portal"])


def _too_many_attempts(retry_after: int) -> HTTPException:
    """A 429 shaped like the middleware's, so clients see one behaviour."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please slow down.",
        headers={"Retry-After": str(retry_after)},
    )


@router.post("/login", response_model=CustomerProfile)
def portal_login(
    payload: CustomerLoginRequest,
    request: Request,
    client=Depends(get_supabase_client),
) -> CustomerProfile:
    """Sign a customer in with their phone number and OTP; return their profile."""
    allowed, retry_after = auth_limiter.check(client_ip(request) or "unknown")
    if not allowed:
        raise _too_many_attempts(retry_after)

    try:
        customer = find_customer_by_phone(client, payload.phone)
        if customer is None:
            raise CustomerAuthError("Invalid phone number or OTP")
        verify_otp(customer, payload.otp)
    except CustomerAuthError as exc:
        # One message for "no such customer" and "wrong OTP": guessing one must
        # not be distinguishable from guessing the other.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return CustomerProfile(**customer_profile(customer))


@router.post("/me", response_model=CustomerProfile)
def portal_me(
    payload: CustomerLoginRequest,
    client=Depends(get_supabase_client),
) -> CustomerProfile:
    """Re-read the signed-in customer's profile (their phone + OTP is the credential).

    Same authentication as ``/portal/login``; this is the "show me my current
    balance" call, so it stays cheap and stateless until the portal grows a
    real session.
    """
    customer = find_customer_by_phone(client, payload.phone)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid phone number or OTP"
        )
    try:
        verify_otp(customer, payload.otp)
    except CustomerAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return CustomerProfile(**customer_profile(customer))
