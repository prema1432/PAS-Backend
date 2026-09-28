"""Customer portal service: look up a customer by phone and verify their OTP.

This is the one place in the app that authenticates by something other than a
Supabase session, so the rules it enforces are worth spelling out:

* the lookup is by **phone only, across every owner** — the customer does not
  know who owns their account, so no ``user_id`` can appear in the query;
* the OTP is compared with ``secrets.compare_digest``, the same constant-time
  comparison the docs auth uses, so a measured guess is not a faster guess;
* every failure — unknown phone, wrong OTP, inactive customer — returns the
  same response shape, so the error itself never leaks which part was wrong;
* the OTP stays valid after a successful login. There is no SMS delivery in
  this deployment, so rotating it here would lock the customer out of the
  portal until their owner regenerates it. It is a standing secret, not a
  single-use code, and is treated with the same care as one.
"""

import secrets

from app.core.security.otp import OTP_DIGITS

TABLE = "customers"


class CustomerAuthError(Exception):
    """The phone/OTP pair does not match an active customer (or the OTP shape is wrong)."""


def find_customer_by_phone(client, phone: str) -> dict | None:
    """Fetch an active customer by phone number.

    Scoping is deliberately **not** by owner: the caller is the customer, and
    the only thing they have is their phone. RLS would hide rows from an
    owner-scoped client, so this must run on a client that is not bound to a
    Supabase user — the shared anon client is exactly that.
    """
    rows = (
        client.table(TABLE)
        .select("*")
        .eq("phone", phone.strip())
        .eq("is_active", True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def verify_otp(customer: dict, otp: str) -> None:
    """Raise :class:`CustomerAuthError` unless the OTP matches, in constant time."""
    stored = customer.get("otp") or ""
    if len(stored) != OTP_DIGITS or not secrets.compare_digest(stored, otp):
        raise CustomerAuthError("Invalid phone number or OTP")


def customer_profile(customer: dict) -> dict:
    """Shape a customer row as the public profile (see ``CustomerProfile``).

    ``free_minutes``/``paid_minutes`` are derived here, at the edge, so the
    table stays a single source of truth for the balance and the portal can
    still answer "how much of what I have left is free vs paid". Free time is
    the plan allowance; everything beyond it was bought.
    """
    remaining = max(0, int(customer.get("remaining_minutes") or 0))
    free_minutes = remaining if customer.get("plan") == "free" else 0
    return {
        "id": customer["id"],
        "name": customer["name"],
        "email": customer["email"],
        "phone": customer["phone"],
        "is_active": customer["is_active"],
        "plan": customer["plan"],
        "remaining_minutes": remaining,
        "free_minutes": free_minutes,
        "paid_minutes": remaining - free_minutes,
    }
