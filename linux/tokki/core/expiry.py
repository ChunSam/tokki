"""Mirrors CredentialExpiry.cs, RateLimitBackoff.cs and PercentMath.cs.

Both providers store a short-lived OAuth access token that only their own CLI
refreshes; Tokki just reads it. Sending an already-expired token to Anthropic's
usage endpoint earns a 429 that the backoff would then mistake for a real quota
cooldown, so expiry is judged locally first and the request is never made.
"""

from __future__ import annotations

import base64
import json
import math
from datetime import datetime, timedelta, timezone

# Treat a token as expired slightly early so one that lapses between the check
# and the response is still reported as expired, not as an HTTP error.
SKEW = timedelta(seconds=60)


def is_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    return expires_at - now <= SKEW


def jwt_expiry(token: str | None) -> datetime | None:
    """The `exp` claim of a JWT, read without verifying the signature."""
    if not token or not token.strip():
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = parts[1].replace("-", "+").replace("_", "/")
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.b64decode(payload))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except Exception:
        return None


def epoch_milliseconds(value) -> datetime | None:
    """Milliseconds-since-epoch is how Claude Code records `expiresAt`."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def token_fingerprint(*tokens: str) -> str:
    """FNV-1a over the sorted tokens, same as TokenFingerprint.Of. Not a security
    boundary: it only has to change when the token does."""
    data = "\0".join(sorted(tokens)).encode("utf-8")
    value = 0xCBF29CE484222325
    for byte in data:
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return format(value, "x")


# --- RateLimitBackoff ---------------------------------------------------------

MINIMUM_COOLDOWN = timedelta(seconds=120)
MAXIMUM_COOLDOWN = timedelta(seconds=1800)
JITTER_FRACTION = 0.2


def cooldown(retry_after: timedelta, failure_count: int, jitter: float) -> timedelta:
    """Exponential, capped, with additive jitter; never below an explicit longer
    Retry-After."""
    base = retry_after if retry_after > MINIMUM_COOLDOWN else MINIMUM_COOLDOWN
    exponent = min(max(failure_count, 0), 20)
    grown = base * (2 ** exponent)
    capped = grown if grown < MAXIMUM_COOLDOWN else MAXIMUM_COOLDOWN
    escalated = capped if capped > base else base
    clamped = min(max(jitter, 0.0), JITTER_FRACTION)
    return escalated + escalated * clamped


# --- PercentMath ----------------------------------------------------------------

def clamp_percent(value: float) -> int:
    # Away from zero, like MidpointRounding.AwayFromZero (Python's round is banker's).
    rounded = math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)
    return min(100, max(0, int(rounded)))


def remaining_percent(used_percent: float | None) -> int | None:
    return None if used_percent is None else clamp_percent(100.0 - used_percent)
