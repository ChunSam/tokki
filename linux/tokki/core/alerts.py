"""The low-balance part of UsageAlertEvaluator.cs.

Candidate ids are stable per provider and window, so the app can send each one
once while it holds and again only after it has cleared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import Provider, ProviderUsage, UsageSnapshot


@dataclass(frozen=True)
class AlertCandidate:
    id: str
    title_key: str
    body: str


def _window_has_reset(reset_at: datetime | None, now: datetime) -> bool:
    # A balance describes only the window it was measured in; once that window
    # has reset, the number belongs to a window that already refilled.
    return reset_at is not None and reset_at <= now


def _candidates(usage: ProviderUsage, five_hour_threshold: int, seven_day_threshold: int, now: datetime):
    if not usage.is_available:
        return
    five = max(0, min(100, five_hour_threshold))
    seven = max(0, min(100, seven_day_threshold))
    pid = usage.provider.value
    # Bodies are symbols only, so they read the same in every language.
    if usage.remaining_5h is not None and five > 0 and usage.remaining_5h <= five and not _window_has_reset(usage.reset_at_5h, now):
        yield AlertCandidate(f"{pid}-5h-low", "five_hour_alert_title", f"{usage.display_name} 5h {usage.remaining_5h}% <= {five}%")
    if usage.remaining_7d is not None and seven > 0 and usage.remaining_7d <= seven and not _window_has_reset(usage.reset_at_7d, now):
        yield AlertCandidate(f"{pid}-7d-low", "seven_day_alert_title", f"{usage.display_name} 7d {usage.remaining_7d}% <= {seven}%")


def candidates(snapshot: UsageSnapshot, enabled: bool, five_hour_threshold: int, seven_day_threshold: int, now: datetime) -> list[AlertCandidate]:
    if not enabled:
        return []
    out: list[AlertCandidate] = []
    for provider in (Provider.CLAUDE, Provider.CODEX):
        out.extend(_candidates(snapshot.usage(provider), five_hour_threshold, seven_day_threshold, now))
    return out


def to_deliver(active: list[AlertCandidate], delivered: set[str]) -> tuple[list[AlertCandidate], set[str]]:
    """Which candidates are new, and the delivered set to keep: an alert that has
    cleared drops out, so it can fire again the next time the balance runs low."""
    fresh = [c for c in active if c.id not in delivered]
    return fresh, {c.id for c in active}
