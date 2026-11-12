"""Mirrors UsageForecast.cs and UsagePace.cs."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from .alerts import AlertCandidate
from .expiry import clamp_percent
from .history import HistoryEntry, remaining
from .models import ForecastWindow, Provider, ProviderUsage, UsageSource
from .pause import duration_text

MINIMUM_SPAN = timedelta(minutes=10)
MAXIMUM_SAMPLE_AGE = timedelta(minutes=30)


def short_label(window: ForecastWindow) -> str:
    return "5h" if window is ForecastWindow.FIVE_HOUR else "7d"


def window_duration(window: ForecastWindow) -> timedelta:
    return timedelta(hours=5) if window is ForecastWindow.FIVE_HOUR else timedelta(days=7)


@dataclass(frozen=True)
class Forecast:
    burn_per_hour: float
    seconds_to_empty: float
    empty_at: datetime
    will_empty_before_reset: bool


def forecast(entries: list[HistoryEntry], provider: Provider, window: ForecastWindow, reset_at: datetime | None, now: datetime) -> Forecast | None:
    """When this window runs out at its recent burn rate, from stored history
    only. None without enough signal."""
    points = sorted(
        ((e.recorded_at, r) for e in entries if (r := remaining(e.snapshot.usage(provider), window)) is not None),
        key=lambda p: p[0],
    )
    if len(points) < 2:
        return None
    # Trim to the current window instance: drop everything before the last
    # upward jump (a reset), or a reset reads as negative consumption.
    start = 0
    for i in range(1, len(points)):
        if points[i][1] > points[i - 1][1]:
            start = i
    segment = points[start:]
    if len(segment) < 2:
        return None
    (t0, r0), (t1, r1) = segment[0], segment[-1]
    elapsed = (t1 - t0).total_seconds()
    if elapsed < MINIMUM_SPAN.total_seconds():
        return None
    if (now - t1).total_seconds() > MAXIMUM_SAMPLE_AGE.total_seconds():
        return None
    drop = r0 - r1
    if drop <= 0:
        return None
    burn = drop / (elapsed / 3600.0)
    if burn <= 0:
        return None
    seconds = r1 / burn * 3600.0
    empty_at = now + timedelta(seconds=seconds)
    return Forecast(burn, seconds, empty_at, reset_at is not None and empty_at < reset_at)


def forecast_menu_line(result: Forecast | None, window: ForecastWindow, text) -> str | None:
    if result is None:
        return None
    line = f"{text('forecast_label')}: ~{duration_text(result.seconds_to_empty)}"
    if window is ForecastWindow.SEVEN_DAY:
        line += f" ({short_label(window)})"
    if result.will_empty_before_reset:
        line += f" · {text('forecast_before_reset')}"
    return line


@dataclass(frozen=True)
class ForecastAlertInput:
    provider: Provider
    window: ForecastWindow
    forecast: Forecast
    reset_at: datetime | None


def forecast_alerts(inputs: list[ForecastAlertInput], enabled: bool, text) -> list[AlertCandidate]:
    if not enabled:
        return []
    out = []
    for item in inputs:
        if not item.forecast.will_empty_before_reset or item.reset_at is None:
            continue
        label = short_label(item.window)
        out.append(AlertCandidate(
            f"{item.provider.value}-{label}-empty-before-reset-{int(item.reset_at.timestamp())}",
            text("depletion_alert_title"),
            text("depletion_alert_body", provider=item.provider.display_name, window=label, time=duration_text(item.forecast.seconds_to_empty)),
        ))
    return out


# --- Pace -----------------------------------------------------------------------------

class Pace(str, enum.Enum):
    AHEAD = "Ahead"
    ON_TRACK = "OnTrack"
    BEHIND = "Behind"


@dataclass(frozen=True)
class PaceReading:
    pace: Pace
    expected_remaining_percent: int


PACE_TOLERANCE = 10


def pace_at(remaining_percent: int | None, reset_at: datetime | None, window: ForecastWindow, measured_at: datetime) -> PaceReading | None:
    if remaining_percent is None or reset_at is None:
        return None
    left = reset_at - measured_at
    duration = window_duration(window)
    if left <= timedelta(0) or left > duration:
        return None
    expected = 100.0 * left.total_seconds() / duration.total_seconds()
    gap = remaining_percent - expected
    pace = Pace.ON_TRACK
    if gap < -PACE_TOLERANCE:
        pace = Pace.AHEAD
    elif gap > PACE_TOLERANCE:
        pace = Pace.BEHIND
    return PaceReading(pace, clamp_percent(expected))


def pace_reading(usage: ProviderUsage, window: ForecastWindow) -> PaceReading | None:
    """A cached reading gets none: its percentage belongs to a moment the window
    has since moved on from."""
    if usage.source not in (UsageSource.API, UsageSource.STATUS_LINE):
        return None
    if window is ForecastWindow.FIVE_HOUR:
        return pace_at(usage.remaining_5h, usage.reset_at_5h, window, usage.updated_at)
    return pace_at(usage.remaining_7d, usage.reset_at_7d, window, usage.updated_at)


_PACE_KEYS = {Pace.AHEAD: "pace_ahead", Pace.ON_TRACK: "pace_on_track", Pace.BEHIND: "pace_behind"}


def pace_menu_line(usage: ProviderUsage, text) -> str | None:
    parts = []
    for window in (ForecastWindow.FIVE_HOUR, ForecastWindow.SEVEN_DAY):
        reading = pace_reading(usage, window)
        if reading:
            parts.append(f"{short_label(window)} {text(_PACE_KEYS[reading.pace])}")
    if not parts:
        return None
    return text("pace") + ": " + " · ".join(parts)
