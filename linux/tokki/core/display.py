"""Mirrors DisplayFormatter.cs (the parts the tray uses).

The number shown and the countdown beside it must come from the same window:
`display_window` decides, and both read from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import DisplayMode, ForecastWindow, Provider, ProviderLabelStyle, ProviderUsage, UsageSnapshot, UsageSource


def display_window(usage: ProviderUsage) -> ForecastWindow | None:
    # A 7d window at 10% or less wins over the 5h number.
    if usage.remaining_7d is not None and usage.remaining_7d <= 10:
        return ForecastWindow.SEVEN_DAY
    if usage.remaining_5h is not None:
        return ForecastWindow.FIVE_HOUR
    return ForecastWindow.SEVEN_DAY if usage.remaining_7d is not None else None


def display_percent(usage: ProviderUsage) -> int | None:
    window = display_window(usage)
    if window is ForecastWindow.FIVE_HOUR:
        return usage.remaining_5h
    if window is ForecastWindow.SEVEN_DAY:
        return usage.remaining_7d
    return None


def display_reset_at(usage: ProviderUsage) -> datetime | None:
    window = display_window(usage)
    if window is ForecastWindow.FIVE_HOUR:
        return usage.reset_at_5h
    if window is ForecastWindow.SEVEN_DAY:
        return usage.reset_at_7d
    return None


def preferred_forecast_window(usage: ProviderUsage) -> ForecastWindow:
    """The 5h window when it reports, else 7d (Codex's normal state since the
    5h limit went away), so Codex still gets a forecast and sparkline."""
    if usage.remaining_5h is not None:
        return ForecastWindow.FIVE_HOUR
    return ForecastWindow.SEVEN_DAY if usage.remaining_7d is not None else ForecastWindow.FIVE_HOUR


def is_seven_day_warning(usage: ProviderUsage) -> bool:
    return usage.remaining_7d is not None and usage.remaining_7d <= 10


def provider_label(provider: Provider, style: ProviderLabelStyle) -> str:
    if style is ProviderLabelStyle.ABBREVIATION:
        return "Cdx" if provider is Provider.CODEX else "Cl"
    return "Codex" if provider is Provider.CODEX else "Claude"


def format_percent(value: int | None) -> str:
    return "--" if value is None else f"{value}%"


def _split(remaining: timedelta):
    total_minutes = int(remaining.total_seconds() // 60)
    days, rest = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    return days, hours, minutes


def format_reset(date: datetime | None, now: datetime, now_text: str, compact: bool = False) -> str | None:
    if date is None:
        return None
    remaining = date - now
    if remaining <= timedelta(0):
        return now_text
    days, hours, minutes = _split(remaining)
    gap = "" if compact else " "
    if remaining < timedelta(hours=1):
        return f"{minutes}m"
    if remaining < timedelta(days=1):
        return f"{hours}h{gap}{minutes}m"
    return f"{days}d{gap}{hours}h"


def format_window_reset(remaining: int | None, reset_at: datetime | None, now: datetime, text, provider_reported: bool = False) -> str:
    """`text` is a Localizer.text-like callable. A balance without a reset is a
    window not yet opened, not a failed read."""
    if reset_at is not None:
        return format_reset(reset_at, now, text("now"))
    if remaining is not None:
        return text("window_not_open")
    if provider_reported:
        return text("window_not_reported")
    return "--"


def countdown_suffix(usage: ProviderUsage | None, show: bool, now: datetime, now_text: str) -> str:
    """A cached reading's reset keeps ticking while its value stands still, so no
    time is shown for it. A status-line reading has a live window and keeps one."""
    if not show or usage is None:
        return ""
    if usage.source not in (UsageSource.API, UsageSource.STATUS_LINE):
        return ""
    text = format_reset(display_reset_at(usage), now, now_text, compact=True)
    return "" if text is None else " " + text


def _lowest(snapshot: UsageSnapshot) -> ProviderUsage | None:
    candidates = [u for u in (snapshot.claude, snapshot.codex) if display_percent(u) is not None]
    return min(candidates, key=display_percent) if candidates else None


def status_title(snapshot: UsageSnapshot | None, mode: DisplayMode, style: ProviderLabelStyle = ProviderLabelStyle.ABBREVIATION, costs=None) -> str:
    """`costs` holds the providers tracked by API cost alone (menu_bar_entries):
    those show their month's spend where the percentage would be."""
    if snapshot is None:
        return "AI --"
    spend = costs or {}

    def segment(provider: Provider) -> str:
        label = provider_label(provider, style)
        if provider in spend:
            return label + " " + spend[provider].text
        return label + " " + format_percent(display_percent(snapshot.usage(provider)))

    if mode is DisplayMode.LOWEST_REMAINING:
        lowest = _lowest(snapshot)
        if lowest is not None:
            return f"AI {display_percent(lowest)}%"
        if not spend:
            return "AI --"
        return " · ".join(segment(p) for p in (Provider.CLAUDE, Provider.CODEX) if p in spend)
    if mode is DisplayMode.BOTH:
        return segment(Provider.CODEX) + " · " + segment(Provider.CLAUDE)
    if mode is DisplayMode.CODEX_ONLY:
        return segment(Provider.CODEX)
    if mode is DisplayMode.CLAUDE_ONLY:
        return segment(Provider.CLAUDE)
    return "AI --"


@dataclass(frozen=True)
class TraySegment:
    """One piece of the panel label. `provider` set means the provider's logo;
    `separator` the " · " between providers; otherwise `text`, drawn in the
    warning colour when `warning` is set (a low 7d balance, an over-budget spend)."""
    text: str = ""
    warning: bool = False
    provider: Provider | None = None
    separator: bool = False


def tray_segments(
    snapshot: UsageSnapshot | None,
    mode: DisplayMode,
    style: ProviderLabelStyle,
    show_countdown: bool,
    now: datetime,
    now_text: str,
    costs=None,
) -> list[TraySegment]:
    """The panel label in pieces, mirroring StatusItemRenderer.statusSegments: the
    logo style puts a provider's logo where the short name would be. The countdown
    stays in the base colour even beside a red number."""
    if snapshot is None:
        return [TraySegment("AI --")]
    spend = costs or {}

    def suffix(usage) -> list[TraySegment]:
        text = countdown_suffix(usage, show_countdown, now, now_text)
        return [TraySegment(text)] if text else []

    def label(provider: Provider) -> TraySegment:
        if style is ProviderLabelStyle.ICON:
            return TraySegment(provider=provider)
        return TraySegment(provider_label(provider, style) + " ")

    def segment(provider: Provider) -> list[TraySegment]:
        if provider in spend:
            entry = spend[provider]
            return [label(provider), TraySegment(entry.text, entry.is_warning)]
        usage = snapshot.usage(provider)
        percent = TraySegment(format_percent(display_percent(usage)), is_seven_day_warning(usage))
        return [label(provider), percent] + suffix(usage)

    def joined(providers) -> list[TraySegment]:
        out: list[TraySegment] = []
        for index, provider in enumerate(providers):
            if index:
                out.append(TraySegment(separator=True))
            out += segment(provider)
        return out

    if mode is DisplayMode.LOWEST_REMAINING:
        lowest = _lowest(snapshot)
        if lowest is not None:
            warning = tray_shows_warning(snapshot, mode)
            return [TraySegment("AI "), TraySegment(f"{display_percent(lowest)}%", warning)] + suffix(lowest)
        if not spend:
            return [TraySegment("AI --")]
        return joined([p for p in (Provider.CLAUDE, Provider.CODEX) if p in spend])
    if mode is DisplayMode.BOTH:
        return joined([Provider.CODEX, Provider.CLAUDE])
    if mode is DisplayMode.CODEX_ONLY:
        return joined([Provider.CODEX])
    if mode is DisplayMode.CLAUDE_ONLY:
        return joined([Provider.CLAUDE])
    return [TraySegment("AI --")]


def segments_text(segments: list[TraySegment]) -> str:
    """The segments as plain text: a logo reads as the provider's full name."""
    parts = []
    for piece in segments:
        if piece.separator:
            parts.append(" · ")
        elif piece.provider is not None:
            parts.append(provider_label(piece.provider, ProviderLabelStyle.ICON) + " ")
        else:
            parts.append(piece.text)
    return "".join(parts)


def tray_label(
    snapshot: UsageSnapshot | None,
    mode: DisplayMode,
    style: ProviderLabelStyle,
    show_countdown: bool,
    now: datetime,
    now_text: str,
    costs=None,
) -> str:
    """The panel label. GNOME's indicators have no tooltip, so the countdown the
    Windows tooltip carries goes after each number here, pointing at the reset of
    the window that number came from."""
    return segments_text(tray_segments(snapshot, mode, style, show_countdown, now, now_text, costs))


def tray_shows_warning(snapshot: UsageSnapshot | None, mode: DisplayMode) -> bool:
    if snapshot is None:
        return False
    if mode is DisplayMode.CODEX_ONLY:
        return is_seven_day_warning(snapshot.codex)
    if mode is DisplayMode.CLAUDE_ONLY:
        return is_seven_day_warning(snapshot.claude)
    percents = [p for p in (display_percent(snapshot.codex), display_percent(snapshot.claude)) if p is not None]
    if not percents:
        return False
    lowest = min(percents)
    return any(display_percent(u) == lowest and is_seven_day_warning(u) for u in (snapshot.codex, snapshot.claude))
