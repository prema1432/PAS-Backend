"""One-time passwords (6 digits) generated with a CSPRNG."""

import secrets

OTP_DIGITS = 6


def generate_otp(digits: int = OTP_DIGITS) -> str:
    """Return a random numeric OTP, zero-padded to `digits` characters."""
    upper_bound = 10**digits
    return f"{secrets.randbelow(upper_bound):0{digits}d}"
