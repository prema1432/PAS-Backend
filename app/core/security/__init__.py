"""Authentication, encryption and abuse-control primitives.

Everything in here is a building block, not a policy: token claims (`jwt`),
at-rest encryption (`crypto`), one-time passwords (`otp`) and the request rate
limiter (`rate_limit`). The policies that use them live in `app/core/`
(credentials, docs auth) and `app/modules/auth/` (sessions, cookies).
"""
