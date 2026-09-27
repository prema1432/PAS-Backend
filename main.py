"""PAS Backend - FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="PAS Backend",
    description="Backend API for PAS",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict:
    """Service info root endpoint."""
    return {
        "name": "PAS Backend",
        "version": "0.1.0",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health")
def health() -> dict:
    """Health check endpoint for monitoring."""
    return {"status": "healthy"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str | None = None) -> dict:
    """Example endpoint returning a single item."""
    return {"item_id": item_id, "q": q}
