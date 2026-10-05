"""
Run once to create MongoDB indexes.

    uv run python scripts/create_indexes.py
"""

import asyncio

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings


async def main() -> None:
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB]
    col = db["customers"]

    # Unique index on phone_number (primary lookup key)
    await col.create_index("phone_number", unique=True)

    # TTL index to auto-expire OTPs from MongoDB (optional housekeeping)
    await col.create_index("otp_expires_at", expireAfterSeconds=0)

    # Login events indexes
    events = db["login_events"]
    await events.create_index("customer_id")
    await events.create_index("phone_number")
    await events.create_index("timestamp")

    # Recharges indexes
    recharges = db["recharges"]
    await recharges.create_index("customer_id")
    await recharges.create_index("phone_number")
    await recharges.create_index("created_at")

    print("Indexes created successfully.")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
