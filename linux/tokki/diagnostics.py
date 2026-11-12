"""Mirrors windows/TokenTracker.Windows/DiagnosticsReporter.cs. English only, so a
pasted report reads the same to whoever gets it. Never prints a secret: Admin
keys show their last four characters at most."""

from __future__ import annotations

import os
import platform
from datetime import datetime, timezone

from . import VERSION
from .core import cli_refresh, history, issues, paths
from .core.cost import subscription_line
from .core.display import format_percent
from .core.models import AppLanguage, ProviderUsage, UsageSnapshot
from .core.pause import duration_text
from .core.settings import AppSettings
from .core.statusline import StatusLineStore
from .i18n import Localizer


def _iso(value: datetime | None) -> str:
    return "--" if value is None else value.astimezone(timezone.utc).isoformat()


def _os_description() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"') + " " + platform.release()
    except OSError:
        pass
    return platform.platform()


def _usage_lines(usage: ProviderUsage) -> list[str]:
    en = Localizer(AppLanguage.ENGLISH).text
    found = issues.issue(usage, en)
    money = subscription_line(usage, en)
    name = usage.display_name
    return [
        f"{name} source: {usage.source.value}",
        f"{name} status: {found.kind.value}",
        f"{name} 5h remaining: {format_percent(usage.remaining_5h)}",
        f"{name} 7d remaining: {format_percent(usage.remaining_7d)}",
        f"{name} 5h reset: {_iso(usage.reset_at_5h)}",
        f"{name} 7d reset: {_iso(usage.reset_at_7d)}",
        f"{name} plan: {usage.plan or '--'}",
        f"{name} money past plan: {'off' if money is None else money.label + ' ' + money.value}",
        f"{name} technical error: {found.technical_detail or '--'}",
    ]


def diagnostics_text(
    settings: AppSettings,
    entries: list[history.HistoryEntry],
    snapshot: UsageSnapshot | None,
    last_success: datetime | None,
    cli_attempt: cli_refresh.Attempt | None,
    now: datetime,
    extra_lines: list[str] | None = None,
) -> str:
    en = Localizer(AppLanguage.ENGLISH).text
    trend = history.trend_summary(entries, snapshot, en) if snapshot else en("not_enough_history")
    lines = [
        "Tokki Diagnostics",
        f"Generated: {_iso(now)}",
        f"App version: {VERSION}",
        f"OS: {_os_description()}",
        f"Architecture: {platform.machine()}",
        f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', 'unknown')} ({os.environ.get('XDG_SESSION_TYPE', 'unknown')})",
        f"Display mode: {settings.DisplayMode.value}",
        f"Provider labels: {settings.ProviderLabelStyle.value}",
        f"Refresh interval: {settings.RefreshIntervalSeconds}s",
        f"Claude enabled: {settings.ClaudeEnabled}",
        f"Codex enabled: {settings.CodexEnabled}",
        f"Language: {settings.Language.value}",
        f"Notifications enabled: {settings.NotificationsEnabled}",
        f"5h alert threshold: {settings.FiveHourAlertThreshold}%",
        f"7d alert threshold: {settings.SevenDayAlertThreshold}%",
        f"Reset alert window: {settings.ResetAlertMinutes}m",
        f"History retention: {settings.HistoryRetentionDays}d",
        f"History entries: {len(entries)}",
        f"History trend: {trend}",
        f"Last successful update: {'none' if last_success is None else _iso(last_success)}",
    ]

    # Not just whether the file is there: an age well past the ~8h token life
    # says nothing is writing the store any more.
    credentials = paths.claude_credentials_path()
    source = issues.CredentialSource.detect(credentials)
    age = source.age(now)
    lines += [
        f"Claude credential source: {source.kind}",
        f"Claude credential last written: {'unknown' if source.last_written_at is None else _iso(source.last_written_at)}",
        f"Claude credential age: {'unknown' if age is None else duration_text(age.total_seconds())}",
        f"Claude credentials file exists: {credentials.is_file()}",
        f"Claude CLI renewal: {'on' if settings.RenewClaudeTokenWithCli else 'off'}",
        f"Claude CLI: {cli_refresh.locate() or 'not found'}",
        f"Claude CLI last renewal: {'none' if cli_attempt is None else _iso(cli_attempt.attempted_at) + ' ' + cli_attempt.outcome.value}",
        f"Codex auth file exists: {paths.codex_auth_path().is_file()}",
    ]
    lines += extra_lines or []

    store = StatusLineStore()
    lines += [f"Claude status line bridge: {store.path}", f"Claude status line file exists: {store.path.is_file()}"]
    record = store.read(now)
    if record is None:
        lines.append("Claude status line record: none")
    else:
        lines += [
            f"Claude status line measured: {_iso(record.measured_at)}",
            f"Claude status line measurement age: {duration_text((now - record.measured_at).total_seconds())}",
            f"Claude status line last written: {_iso(record.written_at)}",
        ]

    if snapshot is None:
        lines.append("Snapshot: none")
    else:
        lines.append(f"Snapshot updated: {_iso(snapshot.updated_at)}")
        lines += _usage_lines(snapshot.claude) + _usage_lines(snapshot.codex)
    return "\n".join(lines)
