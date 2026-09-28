"""Tests for login activity capture: user agent parsing, IPs, and scoping."""

import httpx
import pytest
from starlette.requests import Request

import app.core.http.geolocation as geolocation
from app.core.config import settings
from app.core.http.geolocation import lookup_location
from app.core.http.ip import client_ip, ip_version, is_private_ip
from app.core.http.user_agent import parse_user_agent
from app.modules.auth.login_events import build_event, record_login
from tests.fakes import FakeSupabaseClient, WriteEmptyClient

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix

CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
EDGE_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
)


def test_parses_chrome_on_macos():
    parsed = parse_user_agent(CHROME_MAC)
    assert parsed["browser"] == "Chrome"
    assert parsed["browser_version"] == "141.0.0.0"
    assert parsed["os"] == "macOS"
    assert parsed["device_type"] == "Desktop"
    assert parsed["is_bot"] is False


def test_parses_iphone_safari_as_mobile():
    parsed = parse_user_agent(SAFARI_IPHONE)
    assert parsed["browser"] == "Safari"
    assert parsed["os"] == "iOS"
    assert parsed["device_type"] == "Mobile"


def test_edge_is_not_reported_as_chrome():
    parsed = parse_user_agent(EDGE_WINDOWS)
    assert parsed["browser"] == "Edge"
    assert parsed["os"] == "Windows"


def test_curl_is_flagged_as_bot():
    parsed = parse_user_agent("curl/8.7.1")
    assert parsed["is_bot"] is True
    assert parsed["device_type"] == "Bot"


def test_missing_user_agent_is_unknown():
    parsed = parse_user_agent(None)
    assert parsed["browser"] == "Unknown"
    assert parsed["device_type"] == "Unknown"


def test_ip_version_detection():
    assert ip_version("203.0.113.9") == "IPv4"
    assert ip_version("2001:db8::1") == "IPv6"
    assert ip_version(None) == "unknown"


def test_private_and_loopback_ips_are_recognised():
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "::1", "not-an-ip", None):
        assert is_private_ip(ip) is True
    assert is_private_ip("8.8.8.8") is False


def test_private_ip_skips_geolocation():
    """Local development addresses should not trigger a network lookup."""
    assert lookup_location("127.0.0.1")["city"] is None
    assert lookup_location("192.168.0.4")["country"] is None


def test_client_ip_prefers_proxy_headers():
    class FakeRequest:
        def __init__(self, headers, host="10.0.0.1"):
            self.headers = headers
            self.client = type("C", (), {"host": host})()

    assert client_ip(FakeRequest({"x-forwarded-for": "203.0.113.9, 10.0.0.1"})) == "203.0.113.9"
    assert client_ip(FakeRequest({"cf-connecting-ip": "198.51.100.7"})) == "198.51.100.7"
    assert client_ip(FakeRequest({})) == "10.0.0.1"


def test_activity_lists_current_users_signins(client):
    response = client.get(f"{API}/auth/activity")
    assert response.status_code == 200
    rows = response.json()
    assert [r["id"] for r in rows] == [1]  # other user's event hidden
    assert rows[0]["city"] == "Bengaluru"
    assert rows[0]["browser"] == "Chrome"


def test_activity_is_newest_first(client, stores):
    stores["login_events"].insert(
        0,
        {
            "id": 99,
            "user_id": "test-user-0001",
            "ip": "203.0.113.50",
            "browser": "Firefox",
            "is_bot": False,
            "created_at": "2026-09-27T20:00:00+00:00",
        },
    )
    rows = client.get(f"{API}/auth/activity").json()
    assert rows[0]["id"] == 99


def test_activity_requires_auth(stores):
    from fastapi.testclient import TestClient

    from app.core.dependencies import get_current_client
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_current_client] = lambda: None
    try:
        assert TestClient(fastapi_app).get(f"{API}/auth/activity").status_code == 401
    finally:
        fastapi_app.dependency_overrides.pop(get_current_client, None)


# --- geolocation (the network branch) --------------------------------------


class _GeoResponse:
    """Stand-in for an httpx response: only `.json()` is used."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_lookup_maps_a_successful_geolocation(monkeypatch):
    payload = {
        "success": True,
        "city": "Bengaluru",
        "region": "Karnataka",
        "country": "India",
        "country_code": "IN",
        "continent": "Asia",
        "postal": "560001",
        "latitude": 12.97,
        "longitude": 77.59,
        "timezone": {"id": "Asia/Kolkata"},
        "connection": {"isp": "Example ISP"},
    }
    monkeypatch.setattr(geolocation.httpx, "get", lambda *args, **kwargs: _GeoResponse(payload))

    location = geolocation.lookup_location("8.8.8.8")

    assert location["city"] == "Bengaluru"
    assert location["country_code"] == "IN"
    assert location["timezone"] == "Asia/Kolkata"
    assert location["isp"] == "Example ISP"


def test_lookup_returns_empty_details_when_the_lookup_fails(monkeypatch):
    def unreachable(*args, **kwargs):
        raise httpx.ConnectError("network is down")

    monkeypatch.setattr(geolocation.httpx, "get", unreachable)

    assert geolocation.lookup_location("8.8.8.8") == geolocation.EMPTY_LOCATION


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "message": "reserved range"},
        {"ip": "8.8.8.8", "error": True},
        ["not", "a", "dict"],
    ],
)
def test_lookup_ignores_unusable_answers(monkeypatch, payload):
    monkeypatch.setattr(geolocation.httpx, "get", lambda *args, **kwargs: _GeoResponse(payload))
    assert geolocation.lookup_location("8.8.8.8") == geolocation.EMPTY_LOCATION


def test_lookup_tolerates_a_missing_timezone_and_isp(monkeypatch):
    payload = {"success": True, "city": "Nowhere", "timezone": None, "connection": None}
    monkeypatch.setattr(geolocation.httpx, "get", lambda *args, **kwargs: _GeoResponse(payload))

    location = geolocation.lookup_location("8.8.8.8")

    assert location["city"] == "Nowhere"
    assert location["timezone"] is None
    assert location["isp"] is None


def test_private_addresses_never_trigger_a_lookup(monkeypatch):
    def explode(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("a private address must not be looked up")

    monkeypatch.setattr(geolocation.httpx, "get", explode)
    assert geolocation.lookup_location("10.0.0.1") == geolocation.EMPTY_LOCATION


def _request(
    headers: dict | None = None, host: str = "10.0.0.1", path: str | None = None
) -> Request:
    """A minimal Starlette request: enough of a scope for the event builder."""
    path = path or f"{API}/auth/login"
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": raw_headers,
            "client": (host, 51234),
            "server": ("testserver", 443),
        }
    )


# --- the login_events row --------------------------------------------------


def test_build_event_captures_everything_the_request_offers():
    request = _request(
        headers={
            "user-agent": CHROME_MAC,
            "accept-language": "en-IN,en;q=0.9",
            "referer": "https://app.example.com/login",
            "origin": "https://app.example.com",
            "accept-encoding": "gzip, br",
        }
    )

    row = build_event(request, "user-1")

    assert row["user_id"] == "user-1"
    assert row["ip"] == "10.0.0.1"
    assert row["ip_version"] == "IPv4"
    assert row["forwarded_for"] is None
    assert row["user_agent"] == CHROME_MAC
    assert row["language"] == "en-IN,en;q=0.9"
    assert row["referer"] == "https://app.example.com/login"
    assert row["origin"] == "https://app.example.com"
    assert row["accept_encoding"] == "gzip, br"
    assert row["method"] == "POST"
    assert row["path"] == f"{API}/auth/login"
    # ...plus the parsed device details, which must win over nothing.
    assert row["browser"] == "Chrome"
    assert row["device_type"] == "Desktop"
    assert row["is_bot"] is False
    # A private address is never geolocated, so the fields stay empty.
    assert row["city"] is None
    assert row["country"] is None


def test_build_event_merges_the_geolocated_details(monkeypatch):
    payload = {"success": True, "city": "Bengaluru", "country": "India", "country_code": "IN"}
    monkeypatch.setattr(geolocation.httpx, "get", lambda *args, **kwargs: _GeoResponse(payload))

    row = build_event(_request(headers={"x-forwarded-for": "8.8.8.8"}), "user-2")

    assert row["ip"] == "8.8.8.8"  # the proxy header wins over the socket
    assert row["forwarded_for"] == "8.8.8.8"
    assert row["city"] == "Bengaluru"
    assert row["country"] == "India"
    assert row["country_code"] == "IN"


def test_record_login_stores_the_event():
    events: list[dict] = []

    stored = record_login(_request(), "user-1", FakeSupabaseClient({"login_events": events}))

    assert stored is not None
    assert stored["user_id"] == "user-1"
    assert events == [stored]


def test_record_login_reports_nothing_when_the_row_did_not_land():
    """A silently-filtered insert must not be mistaken for a recorded sign-in."""
    client = WriteEmptyClient({"login_events": []})
    assert record_login(_request(), "user-1", client) is None


def test_tablets_are_recognised():
    ipad = "Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
    android_tablet = (
        "Mozilla/5.0 (Linux; Android 13; SM-X200 Tablet) AppleWebKit/537.36 "
        "Chrome/141.0.0.0 Safari/537.36"
    )

    assert parse_user_agent(ipad)["device_type"] == "Tablet"
    assert parse_user_agent(android_tablet)["device_type"] == "Tablet"
