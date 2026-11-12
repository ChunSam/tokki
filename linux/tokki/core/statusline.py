"""Mirrors ClaudeStatusLineBridge.cs.

The status-line command re-runs on the CLI's own schedule, so being written
again is not evidence of being measured again: freshness uses `measured_at`
only, never `written_at`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import paths
from .expiry import remaining_percent
from .models import Provider, ProviderUsage, UsageSource
from .parser import _number, from_unix

VERSION = 1
FRESHNESS_WINDOW = timedelta(minutes=30)
CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class StatusLineRecord:
    measured_at: datetime
    written_at: datetime
    used_5h: float | None
    reset_at_5h: datetime | None
    used_7d: float | None
    reset_at_7d: datetime | None


def _window(limits, name, now):
    window = limits.get(name) if isinstance(limits, dict) else None
    if not isinstance(window, dict):
        return None
    used = _number(window, "used_percentage")
    reset = _number(window, "resets_at")
    if used is None or reset is None:
        return None
    reset_at = from_unix(reset)
    # A window whose reset has passed describes an instance that no longer exists.
    if reset_at <= now:
        return None
    return min(max(used, 0.0), 100.0), reset_at


def parse(text: str, now: datetime) -> StatusLineRecord | None:
    try:
        root = json.loads(text)
        if not isinstance(root, dict):
            return None
        version = _number(root, "version")
        if version is None or int(version) != VERSION:
            return None
        measured = _number(root, "measured_at")
        if measured is None:
            return None
        measured_at = from_unix(measured)
        if measured_at - now > CLOCK_SKEW:
            return None
        written = _number(root, "written_at")
        written_at = from_unix(written) if written is not None else measured_at
        limits = root.get("rate_limits")
        five = _window(limits, "five_hour", now)
        seven = _window(limits, "seven_day", now)
        if five is None and seven is None:
            return None
        return StatusLineRecord(
            measured_at, written_at,
            five[0] if five else None, five[1] if five else None,
            seven[0] if seven else None, seven[1] if seven else None,
        )
    except Exception:
        return None


def usage(record: StatusLineRecord | None, api_error: str | None, now: datetime) -> ProviderUsage | None:
    """A stale record yields None so the cache policy, with its own label, carries
    the last good value instead of this presenting an old measurement as live."""
    if record is None:
        return None
    age = now - record.measured_at
    if age > FRESHNESS_WINDOW or age < -CLOCK_SKEW:
        return None
    return ProviderUsage(
        Provider.CLAUDE,
        remaining_percent(record.used_5h),
        remaining_percent(record.used_7d),
        record.reset_at_5h,
        record.reset_at_7d,
        UsageSource.STATUS_LINE,
        api_error,
        None,
        # The measurement time, not now, so a re-read reading keeps aging.
        record.measured_at,
    )


class StatusLineStore:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.claude_statusline_path()

    def read(self, now: datetime) -> StatusLineRecord | None:
        try:
            return parse(self.path.read_text(encoding="utf-8"), now)
        except OSError:
            return None
