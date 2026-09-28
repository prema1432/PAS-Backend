"""Capture and store everything we can learn about a login request.

Recording must never break authentication: any failure here is swallowed and
returned as None so a missing column or a geolocation outage cannot block a
user from signing in.
"""

import logging

from fastapi import Request

from app.core.http.geolocation import lookup_location
from app.core.http.ip import client_ip, ip_version
from app.core.http.user_agent import parse_user_agent
from supabase import Client

__all__ = ["build_event", "record_login"]

logger = logging.getLogger(__name__)

TABLE = "login_events"


def build_event(request: Request, user_id: str) -> dict:
    """Assemble a login_events row from the request and its client IP."""
    ip = client_ip(request)
    agent = parse_user_agent(request.headers.get("user-agent"))
    location = lookup_location(ip)

    return {
        "user_id": user_id,
        "ip": ip,
        "ip_version": ip_version(ip),
        "forwarded_for": request.headers.get("x-forwarded-for"),
        "user_agent": request.headers.get("user-agent"),
        "language": request.headers.get("accept-language"),
        "referer": request.headers.get("referer"),
        "origin": request.headers.get("origin"),
        "accept_encoding": request.headers.get("accept-encoding"),
        "method": request.method,
        "path": request.url.path,
        **agent,
        **location,
    }


def record_login(request: Request, user_id: str, client: Client) -> dict | None:
    """Insert a login event; returns the stored row or None if it failed."""
    row = build_event(request, user_id)
    try:
        response = client.table(TABLE).insert(row).execute()
        return response.data[0] if response.data else None
    except Exception:  # pragma: no cover - defensive: never block sign-in
        logger.warning("Could not record login event for user %s", user_id, exc_info=True)
        return None
