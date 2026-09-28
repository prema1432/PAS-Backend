"""Aggregate customers and login events into dashboard analytics.

Kept as pure functions over already-fetched rows so they are cheap to test and
carry no database or HTTP concerns.
"""

from collections import Counter
from datetime import date, timedelta

from app.core.http.ip import is_private_ip

UNKNOWN = "Unknown"
LOCAL_NETWORK = "Local / private network"
DEFAULT_WINDOW_DAYS = 14


def _location_label(event: dict) -> str:
    """Human-readable location, falling back to a network explanation."""
    parts = [event.get("city"), event.get("region"), event.get("country")]
    parts = [part for part in parts if part]
    if parts:
        return ", ".join(parts)
    if is_private_ip(event.get("ip")):
        return LOCAL_NETWORK
    return UNKNOWN


def _bucket(counter: Counter, limit: int | None = None) -> list[dict]:
    """Turn a Counter into a sorted [{label, count}] list."""
    items = counter.most_common(limit)
    return [{"label": label, "count": count} for label, count in items]


def _day_key(event: dict) -> str | None:
    created = event.get("created_at") or ""
    return created[:10] if len(created) >= 10 else None


def _hour_key(event: dict) -> int | None:
    created = event.get("created_at") or ""
    if len(created) < 13:
        return None
    try:
        return int(created[11:13])
    except ValueError:
        return None


def summarize(
    customers: list[dict],
    events: list[dict],
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: date | None = None,
) -> dict:
    """Build the analytics payload for one account."""
    today = today or date.today()

    active = [c for c in customers if c.get("is_active")]
    paid = [c for c in customers if c.get("plan") == "paid"]

    locations = Counter(_location_label(event) for event in events)
    countries = Counter(event.get("country") or UNKNOWN for event in events)
    # Unique country name + ISO code pairs, ranked by sign-ins: the Countries
    # view lists the pair, not bare names.
    country_pairs: Counter = Counter()
    for event in events:
        name = event.get("country")
        if name:
            country_pairs[(name, event.get("country_code") or UNKNOWN)] += 1
        else:
            country_pairs[(UNKNOWN, UNKNOWN)] += 1
    browsers = Counter(
        " ".join(filter(None, [event.get("browser") or UNKNOWN, event.get("browser_version")]))
        for event in events
    )
    systems = Counter(
        " ".join(filter(None, [event.get("os") or UNKNOWN, event.get("os_version")]))
        for event in events
    )
    devices = Counter(event.get("device_type") or UNKNOWN for event in events)
    plans = Counter(customer.get("plan") or UNKNOWN for customer in customers)
    statuses = Counter("Active" if c.get("is_active") else "Inactive" for c in customers)
    ips = Counter(event.get("ip") for event in events if event.get("ip"))

    # Zero-filled daily series so the chart keeps a stable shape.
    per_day = Counter(filter(None, (_day_key(event) for event in events)))
    series = []
    for offset in range(window_days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        series.append({"date": day, "count": per_day.get(day, 0)})

    per_hour = Counter(filter(None, (_hour_key(event) for event in events)))

    return {
        "totals": {
            "customers": len(customers),
            "active_customers": len(active),
            "inactive_customers": len(customers) - len(active),
            "paid_customers": len(paid),
            "free_customers": len(customers) - len(paid),
            "sign_ins": len(events),
            "unique_locations": len([key for key in locations if key != UNKNOWN]),
            "unique_ips": len(ips),
            "countries": len([key for key in countries if key != UNKNOWN]),
        },
        "by_location": _bucket(locations, 8),
        "by_country": _bucket(countries, 8),
        "by_country_code": [
            {"country": name, "code": code, "count": count}
            for (name, code), count in country_pairs.most_common(8)
        ],
        "by_browser": _bucket(browsers, 6),
        "by_os": _bucket(systems, 6),
        "by_device": _bucket(devices),
        "by_plan": _bucket(plans),
        "by_status": _bucket(statuses),
        "top_ips": _bucket(ips, 5),
        "by_day": series,
        "by_hour": [
            {"label": f"{hour:02d}:00", "count": per_hour.get(hour, 0)} for hour in range(24)
        ],
    }
