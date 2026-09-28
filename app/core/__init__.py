"""Shared core: configuration, dependencies and cross-cutting utilities.

Nothing in here knows about a feature. Feature code lives in `app.modules.*`
and may import from `app.core.*`; the reverse (core importing a module) happens
in exactly one place — session resolution needs the Supabase Auth calls — and is
kept to a single import edge.
"""
