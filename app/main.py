"""
PAS Backend — FastAPI entry point.

Startup / shutdown lifecycle manages the MongoDB connection pool.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.database import close, connect
from app.routers import admin, customer


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
app.include_router(admin.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Redirect root to admin dashboard."""
    return HTMLResponse(content='<meta http-equiv="refresh" content="0;url=/admin"/>')
