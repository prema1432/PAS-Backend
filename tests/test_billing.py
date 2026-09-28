"""Unit tests for the pure billing/session aggregation helpers."""

from datetime import date

from app.modules.billing.service import summarize_billing

TODAY = date(2026, 9, 28)


def _customers():
    return [
        {"id": 1, "name": "Ada", "plan": "paid", "is_active": True, "remaining_minutes": 30},
        {"id": 2, "name": "Bob", "plan": "free", "is_active": True, "remaining_minutes": 0},
    ]


def _payments():
    return [
        {
            "id": 1,
            "customer_id": 1,
            "amount": 500,
            "minutes": 120,
            "mode": "manual",
            "status": "paid",
            "currency": "INR",
            "created_at": "2026-09-27T10:00:00+00:00",
        },
        {
            "id": 2,
            "customer_id": 1,
            "amount": 250,
            "minutes": 60,
            "mode": "auto",
            "status": "paid",
            "currency": "INR",
            "created_at": "2026-09-28T10:00:00+00:00",
        },
    ]


def _sessions():
    return [
        {
            "id": 1,
            "customer_id": 1,
            "status": "ended",
            "minutes_used": 45,
            "started_at": "2026-09-27T09:00:00+00:00",
        },
        {
            "id": 2,
            "customer_id": 1,
            "status": "active",
            "minutes_used": 0,
            "started_at": "2026-09-28T09:00:00+00:00",
        },
        {
            "id": 3,
            "customer_id": 2,
            "status": "ended",
            "minutes_used": 10,
            "started_at": "2026-09-28T11:00:00+00:00",
        },
    ]


def test_totals_combine_payments_and_sessions():
    result = summarize_billing(_customers(), _payments(), _sessions(), 3, TODAY)
    totals = result["totals"]
    assert totals["total_amount"] == 750.0
    assert totals["currency"] == "INR"
    assert totals["total_minutes_recharged"] == 180
    assert totals["total_minutes_remaining"] == 30
    assert totals["total_minutes_used"] == 55
    assert totals["total_sessions"] == 3
    assert totals["active_sessions"] == 1
    assert totals["auto_payments"] == 1
    assert totals["manual_payments"] == 1
    assert totals["paid_payments"] == 2
    assert totals["paying_customers"] == 1
    assert totals["customers_low_balance"] == 1


def test_per_customer_rollup_is_ordered_by_session_count():
    rows = summarize_billing(_customers(), _payments(), _sessions(), 3, TODAY)["by_customer"]
    assert [row["name"] for row in rows] == ["Ada", "Bob"]

    ada, bob = rows
    assert ada["session_count"] == 2
    assert ada["minutes_used"] == 45
    assert ada["minutes_paid"] == 180
    assert ada["remaining_minutes"] == 30
    assert ada["amount"] == 750.0

    assert bob["session_count"] == 1
    assert bob["minutes_used"] == 10
    assert bob["amount"] == 0.0


def test_daily_series_is_zero_filled_and_dated():
    series = summarize_billing(_customers(), _payments(), _sessions(), 3, TODAY)["by_day"]
    assert [row["date"] for row in series] == ["2026-09-26", "2026-09-27", "2026-09-28"]
    assert series[0] == {"date": "2026-09-26", "amount": 0.0, "minutes": 0, "sessions": 0}
    assert series[1]["amount"] == 500.0
    assert series[1]["minutes"] == 120
    assert series[1]["sessions"] == 1
    assert series[2]["amount"] == 250.0
    assert series[2]["sessions"] == 2


def test_modes_and_statuses_are_bucketed():
    result = summarize_billing(_customers(), _payments(), _sessions(), 3, TODAY)
    assert dict((row["label"], row["count"]) for row in result["by_mode"]) == {
        "manual": 1,
        "auto": 1,
    }
    assert result["by_status"] == [{"label": "paid", "count": 2}]


def test_empty_inputs_produce_zeroed_figures():
    result = summarize_billing([], [], [], 2, TODAY)
    assert result["totals"]["total_amount"] == 0.0
    assert result["totals"]["total_sessions"] == 0
    assert result["totals"]["customers_low_balance"] == 0
    assert result["by_customer"] == []
    assert len(result["by_day"]) == 2


def test_missing_numeric_columns_do_not_crash():
    customers = [{"id": 7, "name": "No Balance"}]
    payments = [{"customer_id": 7, "amount": None, "minutes": None, "mode": None}]
    sessions = [{"customer_id": 7, "minutes_used": None, "status": None}]

    result = summarize_billing(customers, payments, sessions, 1, TODAY)
    assert result["totals"]["total_amount"] == 0.0
    assert result["totals"]["manual_payments"] == 1
    row = result["by_customer"][0]
    assert row["remaining_minutes"] == 0
    assert row["session_count"] == 1
