"""Billing service: the payments ledger, plan minutes and dashboard figures.

Two halves, both free of FastAPI imports:

*Balance mutations* — the customer's ``remaining_minutes`` is the authoritative
balance (stored, not derived), and every change to it is accompanied by a row in
``payments`` so the ledger explains the balance.

*Aggregation* — pure functions over already-fetched rows, which keeps the
dashboard maths cheap to unit test.
"""

from collections import Counter, defaultdict
from datetime import date, timedelta

CUSTOMERS = "customers"
PAYMENTS = "payments"

DEFAULT_WINDOW_DAYS = 14
DEFAULT_CURRENCY = "INR"


# --- balance mutations ------------------------------------------------------


def set_balance(client, user_id: str, customer_id: int, remaining_minutes: int) -> dict | None:
    """Set a customer's remaining minutes (never below zero)."""
    balance = max(0, int(remaining_minutes))
    rows = (
        client.table(CUSTOMERS)
        .update({"remaining_minutes": balance})
        .eq("id", customer_id)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def record_payment(
    client,
    user_id: str,
    customer_id: int,
    minutes: int,
    amount: float,
    mode: str = "manual",
    currency: str = "INR",
    reference: str | None = None,
    note: str | None = None,
    status: str = "paid",
) -> dict:
    """Insert a payment ledger row."""
    row = {
        "user_id": user_id,
        "customer_id": customer_id,
        "minutes": int(minutes),
        "amount": float(amount),
        "currency": currency.upper(),
        "mode": mode,
        "status": status,
        "reference": reference,
        "note": note,
    }
    response = client.table(PAYMENTS).insert(row).execute()
    return response.data[0] if response.data else row


def apply_recharge(
    client,
    user_id: str,
    customer: dict,
    minutes: int,
    amount: float,
    mode: str = "manual",
    currency: str = "INR",
    reference: str | None = None,
    note: str | None = None,
) -> dict:
    """Record a payment and add its minutes to the customer's balance."""
    payment = record_payment(
        client,
        user_id,
        customer["id"],
        minutes=minutes,
        amount=amount,
        mode=mode,
        currency=currency,
        reference=reference,
        note=note,
    )
    updated = set_balance(
        client,
        user_id,
        customer["id"],
        int(customer.get("remaining_minutes") or 0) + int(minutes),
    )
    return {"payment": payment, "customer": updated}


def update_payment(
    client,
    user_id: str,
    payment_id: int,
    changes: dict,
) -> dict | None:
    """Edit an owner-scoped payment row; ``None`` when it does not exist."""
    rows = (
        client.table(PAYMENTS)
        .update(changes)
        .eq("id", payment_id)
        .eq("user_id", user_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def auto_recharge_if_needed(client, user_id: str, customer: dict) -> dict | None:
    """Top the customer up automatically when the balance is exhausted.

    Only fires for customers with ``auto_recharge`` enabled and a configured
    top-up size; the resulting ledger row is tagged ``mode="auto"`` so automatic
    and manual payments stay distinguishable.
    """
    if not customer.get("auto_recharge"):
        return None
    if int(customer.get("remaining_minutes") or 0) > 0:
        return None
    minutes = int(customer.get("auto_recharge_minutes") or 0)
    if minutes <= 0:
        return None
    return apply_recharge(
        client,
        user_id,
        customer,
        minutes=minutes,
        amount=float(customer.get("auto_recharge_amount") or 0),
        mode="auto",
        note="Automatic recharge — balance reached zero",
    )


# --- aggregation ------------------------------------------------------------


def _as_float(value) -> float:
    """Coerce a numeric column (Decimal, int, str, None) into a float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value) -> int:
    """Coerce a numeric column into an int, defaulting to 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _day_key(row: dict) -> str | None:
    """``YYYY-MM-DD`` prefix of ``created_at`` (or ``started_at``)."""
    stamp = row.get("created_at") or row.get("started_at") or ""
    return stamp[:10] if len(stamp) >= 10 else None


def _bucket(counter: Counter, limit: int | None = None) -> list[dict]:
    """Turn a Counter into a sorted ``[{label, count}]`` list."""
    return [{"label": label, "count": count} for label, count in counter.most_common(limit)]


def summarize_billing(
    customers: list[dict],
    payments: list[dict],
    sessions: list[dict],
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
) -> dict:
    """Build the billing/session payload for one account."""
    today = today or date.today()

    by_customer_id = {customer.get("id"): customer for customer in customers}

    amount_by_customer: dict = defaultdict(float)
    minutes_paid_by_customer: dict = defaultdict(int)
    session_count: Counter = Counter()
    minutes_used: Counter = Counter()

    for payment in payments:
        customer_id = payment.get("customer_id")
        amount_by_customer[customer_id] += _as_float(payment.get("amount"))
        minutes_paid_by_customer[customer_id] += _as_int(payment.get("minutes"))

    for session in sessions:
        customer_id = session.get("customer_id")
        session_count[customer_id] += 1
        minutes_used[customer_id] += _as_int(session.get("minutes_used"))

    # Per-customer rollup: the dashboard's "every customer, their sessions and
    # their remaining time" table.
    per_customer = []
    for customer in customers:
        customer_id = customer.get("id")
        per_customer.append(
            {
                "id": customer_id,
                "name": customer.get("name"),
                "plan": customer.get("plan"),
                "is_active": bool(customer.get("is_active")),
                "remaining_minutes": _as_int(customer.get("remaining_minutes")),
                "session_count": session_count.get(customer_id, 0),
                "minutes_used": minutes_used.get(customer_id, 0),
                "minutes_paid": minutes_paid_by_customer.get(customer_id, 0),
                "amount": round(amount_by_customer.get(customer_id, 0.0), 2),
            }
        )
    per_customer.sort(key=lambda row: (-row["session_count"], str(row["name"] or "")))

    total_amount = round(sum(_as_float(p.get("amount")) for p in payments), 2)
    modes = Counter(payment.get("mode") or "manual" for payment in payments)
    statuses = Counter(payment.get("status") or "paid" for payment in payments)

    # Zero-filled series so charts keep a stable shape.
    revenue_per_day: Counter = Counter()
    minutes_per_day: Counter = Counter()
    sessions_per_day: Counter = Counter()
    for payment in payments:
        day = _day_key(payment)
        if day:
            revenue_per_day[day] += _as_float(payment.get("amount"))
            minutes_per_day[day] += _as_int(payment.get("minutes"))
    for session in sessions:
        day = _day_key(session)
        if day:
            sessions_per_day[day] += 1

    series = []
    for offset in range(window_days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        series.append(
            {
                "date": day,
                "amount": round(revenue_per_day.get(day, 0.0), 2),
                "minutes": minutes_per_day.get(day, 0),
                "sessions": sessions_per_day.get(day, 0),
            }
        )

    remaining_total = sum(_as_int(c.get("remaining_minutes")) for c in customers)
    low_balance = [
        c for c in customers if c.get("is_active") and _as_int(c.get("remaining_minutes")) <= 0
    ]

    currencies = Counter(payment.get("currency") or DEFAULT_CURRENCY for payment in payments)

    return {
        "totals": {
            "total_amount": total_amount,
            "currency": currencies.most_common(1)[0][0] if currencies else DEFAULT_CURRENCY,
            "total_minutes_recharged": sum(_as_int(p.get("minutes")) for p in payments),
            "total_minutes_remaining": remaining_total,
            "total_sessions": len(sessions),
            "active_sessions": sum(1 for s in sessions if s.get("status") == "active"),
            "total_minutes_used": sum(_as_int(s.get("minutes_used")) for s in sessions),
            "auto_payments": modes.get("auto", 0),
            "manual_payments": modes.get("manual", 0),
            "paid_payments": statuses.get("paid", 0),
            "paying_customers": sum(1 for cid in amount_by_customer if cid in by_customer_id),
            "customers_low_balance": len(low_balance),
        },
        "by_customer": per_customer,
        "by_mode": _bucket(modes),
        "by_status": _bucket(statuses),
        "by_day": series,
    }
