"""IP primitives: extract the caller's address, classify it, version it.

The three functions here are the whole vocabulary this project has for IP
addresses, kept together because callers almost always want more than one of
them: the rate limiter reads the address, the audit log records its version, and
geolocation refuses to look up anything private.
"""

import ipaddress

from fastapi import Request


def client_ip(request: Request) -> str | None:
    """Best-effort client IP, honouring the usual proxy headers.

    Behind a proxy the socket address is the proxy, so the forwarded headers are
    checked first (Cloudflare, then the nginx/FastAPI Cloud pair) and only the
    first entry of a list is used.
    """
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else None


def is_private_ip(ip: str | None) -> bool:
    """True for loopback, private, link-local or otherwise non-public IPs.

    Unparseable values are treated as private: there is no point asking a
    geolocation service about something that is not an address.
    """
    if not ip:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return bool(
        address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
    )


def ip_version(ip: str | None) -> str:
    """Classify an IP address as IPv4 / IPv6 / unknown."""
    if not ip:
        return "unknown"
    return "IPv6" if ":" in ip else "IPv4"
