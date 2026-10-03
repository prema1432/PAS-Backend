"""
Login event capture — saves full context to `login_events` collection on
every successful authentication.

Captured fields
---------------
- customer_id      : MongoDB _id of the customer (as string)
- phone_number     : for quick lookup without a join
- session_id       : matches the JWT sid claim
- ip_address       : from X-Forwarded-For or client host
- geolocation      : country, region, city, lat/lon via ip-api.com (free, no key)
- user_agent_raw   : full UA string
- browser          : name + version
- os               : name + version
- device           : family + brand + model
- is_mobile/tablet/pc/bot : booleans
- timestamp        : UTC datetime of the login
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from fastapi import Request
from user_agents import parse as ua_parse

from app.database import get_db

COLLECTION = "login_events"


# ---------------------------------------------------------------------------
# Geolocation via ip-api.com (free tier, no API key needed)
# ---------------------------------------------------------------------------

async def _get_geo(ip: str) -> dict:
    """Fetch geolocation for an IP. Returns empty dict on any failure."""
    if ip in ("127.0.0.1", "::1", "testclient"):
        return {"note": "localhost — no geo data"}
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(
                f"http://ip-api.com/json/{ip}",
                params={"fields": "status,country,countryCode,regionName,city,zip,lat,lon,isp,org,as,query"},
            )
            data = r.json()
            if data.get("status") == "success":
                data.pop("status", None)
                return data
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Public function — call this after every successful login
# ---------------------------------------------------------------------------

async def record_login_event(
    *,
    request: Request,
    customer_id: str,
    phone_number: str,
    session_id: str,
) -> None:
    """
    Fire-and-forget: saves a login event document.
    Does NOT block the login response — runs as a background task.
    """
    # Resolve client IP (respects reverse-proxy headers)
    forwarded_for = request.headers.get("x-forwarded-for")
    ip = forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else "unknown")

    # Parse user agent
    ua_string = request.headers.get("user-agent", "")
    ua = ua_parse(ua_string)

    # Fetch geo (concurrent with the rest of the work)
    geo = await _get_geo(ip)

    event = {
        "customer_id": customer_id,
        "phone_number": phone_number,
        "session_id": session_id,
        "timestamp": datetime.now(tz=timezone.utc),

        # Network
        "ip_address": ip,
        "geolocation": geo,

        # User agent — raw + parsed
        "user_agent_raw": ua_string,
        "browser": {
            "family": ua.browser.family,
            "version": ua.browser.version_string,
        },
        "os": {
            "family": ua.os.family,
            "version": ua.os.version_string,
        },
        "device": {
            "family": ua.device.family,
            "brand": ua.device.brand,
            "model": ua.device.model,
        },
        "is_mobile": ua.is_mobile,
        "is_tablet": ua.is_tablet,
        "is_pc": ua.is_pc,
        "is_bot": ua.is_bot,

        # Extra request headers (useful for debugging)
        "accept_language": request.headers.get("accept-language"),
        "referer": request.headers.get("referer"),
        "origin": request.headers.get("origin"),
    }

    db = get_db()
    await db[COLLECTION].insert_one(event)
