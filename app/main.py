"""
PAS Backend — FastAPI entry point.

Startup / shutdown lifecycle manages the MongoDB connection pool.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import close, connect
from app.routers import customer


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await connect()
    yield
    # --- shutdown ---
    await close()


app = FastAPI(
    title="PAS Backend API",
    version="0.1.0",
    description="Personal AI Assistant — customer authentication service.",
    lifespan=lifespan,
)

app.include_router(customer.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}
