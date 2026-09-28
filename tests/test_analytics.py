"""Tests for the analytics aggregation and summary endpoint."""

from datetime import date

from app.core.config import settings
from app.modules.analytics.service import summarize

#: Data routes live on the versioned API surface; tests spell the prefix out.
API = settings.api_prefix

CUSTOMERS = [
    {"id": 1, "plan": "free", "is_active": True},
    {"id": 2, "plan": "paid", "is_active": True},
    {"id": 3, "plan": "paid", "is_active": False},
]

EVENTS = [
    {
        "id": 3,
        "ip": "8.8.8.8",
        "city": "San Jose",
        "region": "California",
        "country": "United States",
        "browser": "Chrome",
        "browser_version": "141.0.0.0",
        "os": "macOS",
        "device_type": "Desktop",
        "created_at": "2026-09-27T18:00:00+00:00",
    },
    {
        "id": 2,
        "ip": "9.9.9.9",
        "city": "San Jose",
        "region": "California",
        "country": "United States",
        "browser": "Chrome",
        "browser_version": "141.0.0.0",
        "os": "macOS",
        "device_type": "Desktop",
        "created_at": "2026-09-27T09:30:00+00:00",
    },
    {
        "id": 1,
        "ip": "127.0.0.1",
        "browser": "Safari",
        "os": "iOS",
        "device_type": "Mobile",
        "created_at": "2026-09-25T22:15:00+00:00",
    },
]


def test_totals_count_customers_and_sign_ins():
    result = summarize(CUSTOMERS, EVENTS, today=date(2026, 9, 27))
    totals = result["totals"]
    assert totals["customers"] == 3
    assert totals["active_customers"] == 2
    assert totals["inactive_customers"] == 1
    assert totals["paid_customers"] == 2
    assert totals["free_customers"] == 1
    assert totals["sign_ins"] == 3
    assert totals["unique_ips"] == 3
    assert totals["countries"] == 1


def test_locations_are_ranked_and_private_ips_labelled():
    result = summarize(CUSTOMERS, EVENTS, today=date(2026, 9, 27))
    labels = [row["label"] for row in result["by_location"]]
    assert labels[0] == "San Jose, California, United States"
    assert result["by_location"][0]["count"] == 2
    assert "Local / private network" in labels


def test_country_codes_pair_names_with_iso_codes():
    """by_country_code lists unique (country, ISO code) pairs, ranked by sign-ins."""
    events = [
        {"country": "India", "country_code": "IN", "created_at": "2026-09-27T18:00:00+00:00"},
        {"country": "India", "country_code": "IN", "created_at": "2026-09-27T19:00:00+00:00"},
        {
            "country": "United States",
            "country_code": "US",
            "created_at": "2026-09-27T09:00:00+00:00",
        },
        {"created_at": "2026-09-27T10:00:00+00:00"},  # no country at all
    ]
    result = summarize([], events, today=date(2026, 9, 27))

    ranked = result["by_country_code"]
    assert ranked[0] == {"country": "India", "code": "IN", "count": 2}
    assert sorted(ranked[1:], key=lambda row: row["country"]) == [
        {"country": "United States", "code": "US", "count": 1},
        {"country": "Unknown", "code": "Unknown", "count": 1},
    ]


def test_daily_series_is_zero_filled_and_ordered():
    result = summarize(CUSTOMERS, EVENTS, window_days=4, today=date(2026, 9, 27))
    series = result["by_day"]
    assert [row["date"] for row in series] == [
        "2026-09-24",
        "2026-09-25",
        "2026-09-26",
        "2026-09-27",
    ]
    assert [row["count"] for row in series] == [0, 1, 0, 2]


def test_device_browser_and_plan_breakdowns():
    result = summarize(CUSTOMERS, EVENTS, today=date(2026, 9, 27))
    assert result["by_device"][0] == {"label": "Desktop", "count": 2}
    assert result["by_browser"][0] == {"label": "Chrome 141.0.0.0", "count": 2}
    assert result["by_plan"] == [
        {"label": "paid", "count": 2},
        {"label": "free", "count": 1},
    ]
    assert result["by_status"] == [
        {"label": "Active", "count": 2},
        {"label": "Inactive", "count": 1},
    ]


def test_hourly_buckets_cover_a_full_day():
    result = summarize(CUSTOMERS, EVENTS, today=date(2026, 9, 27))
    hours = result["by_hour"]
    assert len(hours) == 24
    assert hours[18]["count"] == 1
    assert hours[9]["count"] == 1
    assert hours[0]["count"] == 0


def test_empty_account_yields_zeroed_shape():
    result = summarize([], [], window_days=2, today=date(2026, 9, 27))
    assert result["totals"]["sign_ins"] == 0
    assert result["by_location"] == []
    assert result["by_day"] == [
        {"date": "2026-09-26", "count": 0},
        {"date": "2026-09-27", "count": 0},
    ]


def test_unknown_location_falls_back_for_public_ip():
    # A public IP with no geolocation data is "Unknown", not "Local network".
    events = [{"id": 1, "ip": "1.1.1.1", "created_at": "2026-09-27T10:00:00+00:00"}]
    result = summarize([], events, today=date(2026, 9, 27))
    assert result["by_location"][0]["label"] == "Unknown"
    assert result["totals"]["unique_locations"] == 0


def test_analytics_endpoint_returns_summary(client, stores):
    response = client.get(f"{API}/analytics/summary")
    assert response.status_code == 200
    body = response.json()
    # One customer and one login event belong to the signed-in user.
    assert body["totals"]["customers"] == 1
    assert body["totals"]["sign_ins"] == 1
    assert body["by_location"][0]["label"] == "Bengaluru, Karnataka, India"
    assert "by_day" in body and len(body["by_day"]) == 14


def test_analytics_endpoint_window_is_configurable(client):
    body = client.get(f"{API}/analytics/summary?window_days=7").json()
    assert len(body["by_day"]) == 7


def test_analytics_endpoint_rejects_bad_window(client):
    assert client.get(f"{API}/analytics/summary?window_days=0").status_code == 422
    assert client.get(f"{API}/analytics/summary?window_days=999").status_code == 422


def test_analytics_requires_auth():
    from fastapi.testclient import TestClient

    from app.core.dependencies import get_current_client
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_current_client] = lambda: None
    try:
        assert TestClient(fastapi_app).get(f"{API}/analytics/summary").status_code == 401
    finally:
        fastapi_app.dependency_overrides.pop(get_current_client, None)


def test_hour_buckets_ignore_unusable_timestamps():
    """A missing or unparsable hour is dropped, not guessed at."""
    events = [
        {"created_at": ""},
        {"created_at": "2026-09-27Tab:00:00+00:00"},
        {"created_at": "2026-09-27T09:15:00+00:00"},
    ]

    result = summarize([], events)

    assert len(result["by_hour"]) == 24
    assert result["by_hour"][9]["count"] == 1
    assert sum(bucket["count"] for bucket in result["by_hour"]) == 1
