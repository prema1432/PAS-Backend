"""Timestamps in the format Supabase/Postgres expects.

Kept in one place so every module writes the same UTC ISO-8601 shape instead of
each one reaching for `datetime.now()` and picking its own format.
"""

from datetime import UTC, datetime


def now_iso() -> str:
    """Current UTC timestamp, ISO-8601 with an offset, e.g. 2026-09-28T06:12:00+00:00."""
    return datetime.now(UTC).isoformat()
