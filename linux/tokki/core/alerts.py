"""Mirrors UsageAlertEvaluator.cs: low balance, reset soon, stalled collection
and freed-up alerts.

`text` is a Localizer.text-like callable, so this stays free of the UI. Ids are
stable per condition: the app sends each once while it holds and again only
after it has cleared.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .models import Provider, ProviderUsage, UsageSnapshot, UsageSource

Text = Callable[..., str]
LIVE_SOURCES = (UsageSource.API, UsageSource.STATUS_LINE)


@dataclass(frozen=True)
class AlertSettings:
    enabled: bool
    five_hour_threshold: int
    seven_day_threshold: int
    reset_warning_minutes: int
    stalled_after: timedelta = timedelta(0)

    def __post_init__(self):
        object.__setattr__(self, "five_hour_threshold", max(0, min(100, self.five_hour_threshold)))
        object.__setattr__(self, "seven_day_threshold", max(0, min(100, self.seven_day_threshold)))
        object.__setattr__(self, "reset_warning_minutes", max(0, self.reset_warning_minutes))
        if self.stalled_after < timedelta(0):
            object.__setattr__(self, "stalled_after", timedelta(0))


@dataclass(frozen=True)
class AlertCandidate:
    id: str
    title: str
    body: str


def stalled_threshold(refresh_interval_seconds: int) -> timedelta:
    """Three missed polls, with a 30-minute floor."""
    return timedelta(seconds=max(30 * 60, refresh_interval_seconds * 3))


def _window_has_reset(reset_at: datetime | None, now: datetime) -> bool:
    # A balance describes only the window it was measured in.
    return reset_at is not None and reset_at <= now


def _stalled(usage: ProviderUsage, settings: AlertSettings, now: datetime, text: Text):
    """A cached reading that has aged past the threshold. A provider showing "--"
    is left alone: it cannot be mistaken for a live number."""
    if settings.stalled_after <= timedelta(0) or usage.source is not UsageSource.STALE_CACHE:
        return None
    age = now - usage.updated_at
    if age <= settings.stalled_after:
        return None
    minutes = max(0, int(age.total_seconds() // 60))
    ago = f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60}m"
    return AlertCandidate(
        f"{usage.provider.value}-stalled",
        text("stalled_alert_title"),
        text("stalled_alert_body", provider=usage.display_name, time=ago),
    )


def _reset_alerts(usage: ProviderUsage, settings: AlertSettings, now: datetime, text: Text):
    """Worth warning only while there is something left to lose; the number has
    to be a measurement, since the body states it."""
    if settings.reset_warning_minutes <= 0 or usage.source not in LIVE_SOURCES:
        return
    window = timedelta(minutes=settings.reset_warning_minutes)
    for label, reset_at, unused, threshold in (
        ("5h", usage.reset_at_5h, usage.remaining_5h, settings.five_hour_threshold),
        ("7d", usage.reset_at_7d, usage.remaining_7d, settings.seven_day_threshold),
    ):
        if reset_at is None or unused is None or unused <= threshold:
            continue
        left = reset_at - now
        if left <= timedelta(0) or left > window:
            continue
        minutes = max(0, math.ceil(left.total_seconds() / 60))
        yield AlertCandidate(
            f"{usage.provider.value}-{label}-reset-{int(reset_at.timestamp())}",
            text("reset_alert_title"),
            text("reset_alert_body", provider=usage.display_name, window=label, time=f"{minutes}m", percent=unused),
        )


def _provider_candidates(usage: ProviderUsage, settings: AlertSettings, now: datetime, text: Text):
    if not usage.is_available:
        return
    stalled = _stalled(usage, settings, now, text)
    if stalled:
        yield stalled
    pid = usage.provider.value
    five, seven = settings.five_hour_threshold, settings.seven_day_threshold
    # Bodies are symbols only, so they read the same in every language.
    if usage.remaining_5h is not None and five > 0 and usage.remaining_5h <= five and not _window_has_reset(usage.reset_at_5h, now):
        yield AlertCandidate(f"{pid}-5h-low", text("five_hour_alert_title"), f"{usage.display_name} 5h {usage.remaining_5h}% <= {five}%")
    if usage.remaining_7d is not None and seven > 0 and usage.remaining_7d <= seven and not _window_has_reset(usage.reset_at_7d, now):
        yield AlertCandidate(f"{pid}-7d-low", text("seven_day_alert_title"), f"{usage.display_name} 7d {usage.remaining_7d}% <= {seven}%")
    yield from _reset_alerts(usage, settings, now, text)


def candidates(snapshot: UsageSnapshot, settings: AlertSettings, now: datetime, text: Text) -> list[AlertCandidate]:
    if not settings.enabled:
        return []
    out: list[AlertCandidate] = []
    for provider in (Provider.CLAUDE, Provider.CODEX):
        out.extend(_provider_candidates(snapshot.usage(provider), settings, now, text))
    return out


def freed_up_candidates(
    previous: dict[Provider, ProviderUsage],
    current: UsageSnapshot,
    settings: AlertSettings,
    text: Text,
) -> list[AlertCandidate]:
    """A window that had run low has reset and refilled. Fires on the one poll
    that sees the change; by the next, the refilled reading is `previous`."""
    if not settings.enabled:
        return []
    out: list[AlertCandidate] = []
    for provider in (Provider.CLAUDE, Provider.CODEX):
        after = current.usage(provider)
        before = previous.get(provider)
        if before is None or after.source not in LIVE_SOURCES:
            continue
        for label, threshold, was, reset_at, now_percent in (
            ("5h", settings.five_hour_threshold, before.remaining_5h, before.reset_at_5h, after.remaining_5h),
            ("7d", settings.seven_day_threshold, before.remaining_7d, before.reset_at_7d, after.remaining_7d),
        ):
            if (threshold <= 0 or was is None or was > threshold
                    or reset_at is None or reset_at > after.updated_at
                    or now_percent is None or now_percent <= threshold or now_percent <= was):
                continue
            out.append(AlertCandidate(
                f"{provider.value}-{label}-freed-{int(reset_at.timestamp())}",
                text("freed_up_alert_title"),
                text("freed_up_alert_body", provider=after.display_name, window=label, percent=now_percent),
            ))
    return out


def last_readings(previous: dict[Provider, ProviderUsage], snapshot: UsageSnapshot) -> dict[Provider, ProviderUsage]:
    """A measurement replaces the entry; a cached reading only fills an empty one,
    so an outage does not erase the low balance a reset is measured against."""
    readings = dict(previous)
    for provider in (Provider.CLAUDE, Provider.CODEX):
        usage = snapshot.usage(provider)
        if not usage.is_available:
            continue
        if usage.source in LIVE_SOURCES or provider not in readings:
            readings[provider] = usage
    return readings


def to_deliver(active: list[AlertCandidate], delivered: set[str]) -> tuple[list[AlertCandidate], set[str]]:
    """Which candidates are new, and the delivered set to keep: an alert that has
    cleared drops out, so it can fire again the next time."""
    fresh = [c for c in active if c.id not in delivered]
    return fresh, {c.id for c in active}
