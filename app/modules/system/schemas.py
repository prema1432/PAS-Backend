"""Response models for the system module."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Payload returned by the health check endpoint.

    Carries both versions on purpose: `version` identifies the release that is
    running (useful right after a deploy) and `api_version` the API surface it
    serves, which is what a client's URLs depend on.
    """

    status: str
    supabase: str
    version: str
    api_version: str
