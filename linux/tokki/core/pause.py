"""Mirrors PauseController.cs. A paused poll skips the network fetch entirely;
the pause instant is persisted, so a restart during a pause stays paused."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# A pause farther out than this reads as "until I resume", with no countdown.
INDEFINITE_THRESHOLD = timedelta(days=365)

# What "until I resume" stores: far enough out to never lapse, and still a
# datetime that survives arithmetic and an ISO round trip.
INDEFINITE = datetime(9999, 12, 31, tzinfo=timezone.utc)


def is_paused(paused_until: datetime | None, now: datetime) -> bool:
    return paused_until is not None and paused_until > now


def remaining(paused_until: datetime | None, now: datetime) -> timedelta:
    if paused_until is None or paused_until <= now:
        return timedelta(0)
    return paused_until - now


def is_indefinite(paused_until: datetime | None, now: datetime) -> bool:
    return paused_until is not None and paused_until - now > INDEFINITE_THRESHOLD


def pause_until(duration: timedelta | None, now: datetime) -> datetime:
    return INDEFINITE if duration is None else now + duration


def duration_text(seconds: float) -> str:
    """UsageForecaster.DurationText."""
    total_minutes = int(seconds // 60)
    if total_minutes <= 0:
        return "<1m"
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours = total_minutes // 60
    if hours < 24:
        return f"{hours}h {total_minutes % 60}m"
    return f"{hours // 24}d {hours % 24}h"
