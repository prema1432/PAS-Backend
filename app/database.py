"""
Async MongoDB connection pool using Motor.

The client is created once at startup and shared across all requests.
Motor internally maintains a connection pool; maxPoolSize and
serverSelectionTimeoutMS are tuned for a small-to-medium workload.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    """Return the shared Motor client (must call connect() first)."""
    if _client is None:
        raise RuntimeError("Database not connected. Call connect() during startup.")
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[settings.MONGODB_DB]


async def connect() -> None:
    """Open the connection pool.  Called from app lifespan."""
    global _client
    _client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        maxPoolSize=20,
        minPoolSize=2,
        serverSelectionTimeoutMS=5_000,
    )
    # Ping to verify connectivity — warn but don't crash so docs remain accessible
    try:
        await _client.admin.command("ping")
        print("✅  MongoDB connected successfully.")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  MongoDB ping failed: {exc}")
        print("    Server will start but DB operations will fail until the URI is corrected.")


async def close() -> None:
    """Close the connection pool.  Called from app lifespan."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
