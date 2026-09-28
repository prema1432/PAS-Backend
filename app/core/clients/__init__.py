"""Outbound clients (the Supabase client and its per-caller variants).

Import the factory, not the library: `app.core.clients.supabase` is the only
module that knows how Supabase is configured, and data routes must use
`get_client_for_token` so Postgres RLS runs as the caller.
"""
