"""Best-effort IP geolocation.

Uses the free, key-less https://ipwho.is endpoint. Lookups never raise and
never block for long: a failed lookup just means the location fields stay
empty, which the session log tolerates. Private and unparseable addresses are
refused locally by `app.core.http.ip`, so no lookup is ever made for them.
"""

import httpx

from app.core.http.ip import is_private_ip

LOOKUP_URL = "https://ipwho.is/{ip}"
TIMEOUT_SECONDS = 4.0

EMPTY_LOCATION: dict = {
    "city": None,
    "region": None,
    "country": None,
    "country_code": None,
    "continent": None,
    "postal": None,
    "latitude": None,
    "longitude": None,
    "timezone": None,
    "isp": None,
}


def lookup_location(ip: str | None) -> dict:
    """Return approximate location details for an IP (empty dict values if unknown)."""
    if is_private_ip(ip):
        return dict(EMPTY_LOCATION)

    try:
        response = httpx.get(
            LOOKUP_URL.format(ip=ip),
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": "pas-backend/0.2"},
        )
        data = response.json()
    except Exception:  # network error, timeout, bad JSON - never break login
        return dict(EMPTY_LOCATION)

    if not isinstance(data, dict) or not data.get("success", True) or "error" in data:
        return dict(EMPTY_LOCATION)

    timezone = data.get("timezone") or {}
    connection = data.get("connection") or {}
    return {
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
        "country_code": data.get("country_code"),
        "continent": data.get("continent"),
        "postal": data.get("postal"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": timezone.get("id") if isinstance(timezone, dict) else None,
        "isp": connection.get("isp") if isinstance(connection, dict) else None,
    }
