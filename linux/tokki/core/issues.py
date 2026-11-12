"""Mirrors UsageIssueFormatter.cs and CredentialSource.cs: what went wrong, in
words, and what to do about it."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Provider, ProviderUsage, UsageSource
from .pause import duration_text


class IssueKind(str, enum.Enum):
    OK = "Ok"
    DISABLED = "Disabled"
    RATE_LIMITED = "RateLimited"
    MISSING_CREDENTIALS = "MissingCredentials"
    EXPIRED_CREDENTIALS = "ExpiredCredentials"
    INVALID_RESPONSE = "InvalidResponse"
    TIMED_OUT = "TimedOut"
    NETWORK = "Network"
    HTTP_STATUS = "HttpStatus"
    USING_CACHED_DATA = "UsingCachedData"
    STATUS_LINE = "StatusLine"
    UNAVAILABLE = "Unavailable"


@dataclass(frozen=True)
class UsageIssue:
    kind: IssueKind
    title: str
    detail: str
    recovery: str | None
    technical_detail: str | None


_KEYS = {
    IssueKind.OK: "status_ok",
    IssueKind.DISABLED: "status_disabled_provider",
    IssueKind.RATE_LIMITED: "status_rate_limited",
    IssueKind.MISSING_CREDENTIALS: "status_missing_credentials",
    IssueKind.EXPIRED_CREDENTIALS: "status_expired_credentials",
    IssueKind.INVALID_RESPONSE: "status_invalid_response",
    IssueKind.TIMED_OUT: "status_timed_out",
    IssueKind.NETWORK: "status_network_issue",
    IssueKind.HTTP_STATUS: "status_http_error",
    IssueKind.USING_CACHED_DATA: "status_using_cached_data",
    IssueKind.STATUS_LINE: "status_from_status_line",
    IssueKind.UNAVAILABLE: "status_unavailable",
}


def kind_of(error: str) -> IssueKind:
    lower = error.strip().lower()
    if lower == "disabled":
        return IssueKind.DISABLED
    if "http 429" in lower or "too many requests" in lower:
        return IssueKind.RATE_LIMITED
    if "missing credentials" in lower:
        return IssueKind.MISSING_CREDENTIALS
    # 401/403 from either usage endpoint is, in practice, a lapsed token.
    if "credentials expired" in lower or "http 401" in lower or "http 403" in lower:
        return IssueKind.EXPIRED_CREDENTIALS
    if "invalid response" in lower:
        return IssueKind.INVALID_RESPONSE
    if "timed out" in lower:
        return IssueKind.TIMED_OUT
    if "network error" in lower:
        return IssueKind.NETWORK
    if lower.startswith("http "):
        return IssueKind.HTTP_STATUS
    return IssueKind.UNAVAILABLE


_RECOVERY_KEYS = {
    IssueKind.DISABLED: "recovery_enable_provider",
    IssueKind.RATE_LIMITED: "recovery_wait_for_cooldown",
    IssueKind.MISSING_CREDENTIALS: "recovery_check_credentials",
    IssueKind.INVALID_RESPONSE: "recovery_update_or_try_later",
    IssueKind.TIMED_OUT: "recovery_check_network",
    IssueKind.NETWORK: "recovery_check_network",
    IssueKind.HTTP_STATUS: "recovery_refresh_later",
    IssueKind.UNAVAILABLE: "recovery_refresh_later",
}


def _recovery(error: str | None, provider: Provider, text) -> str | None:
    if not error or not error.strip():
        return None
    kind = kind_of(error)
    if kind is IssueKind.EXPIRED_CREDENTIALS:
        # Name only the command the user actually has to run.
        return text("recovery_sign_in_again_claude" if provider is Provider.CLAUDE else "recovery_sign_in_again_codex")
    key = _RECOVERY_KEYS.get(kind)
    return text(key) if key else None


def issue(usage: ProviderUsage, text) -> UsageIssue:
    def make(kind: IssueKind, recovery, technical):
        return UsageIssue(kind, text(_KEYS[kind]), text(_KEYS[kind] + "_detail"), recovery, technical)

    if usage.source is UsageSource.STATUS_LINE:
        return make(IssueKind.STATUS_LINE, _recovery(usage.error, usage.provider, text), usage.error)
    if usage.source is UsageSource.STALE_CACHE:
        return make(IssueKind.USING_CACHED_DATA,
                    _recovery(usage.error, usage.provider, text) or text("recovery_try_again_later"), usage.error)
    if not usage.error or not usage.error.strip():
        if usage.is_available:
            return make(IssueKind.OK, None, None)
        return make(IssueKind.UNAVAILABLE, text("recovery_refresh_later"), None)
    return make(kind_of(usage.error), _recovery(usage.error, usage.provider, text), usage.error)


# --- the credential store -------------------------------------------------------------------

STALE_AFTER = timedelta(hours=8)


@dataclass(frozen=True)
class CredentialSource:
    """Attributes only, never the secret. Linux has no keychain for Claude Code,
    so the file is the store."""
    kind: str  # "CredentialsFile" or "None", the Windows names
    last_written_at: datetime | None

    @staticmethod
    def detect(path: Path) -> "CredentialSource":
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return CredentialSource("None", None)
        return CredentialSource("CredentialsFile", datetime.fromtimestamp(mtime, tz=timezone.utc))

    def age(self, now: datetime) -> timedelta | None:
        return None if self.last_written_at is None else now - self.last_written_at


def stale_line(provider: Provider, found: UsageIssue, source: CredentialSource, now: datetime, text) -> str | None:
    """Nothing has written the Claude store for longer than a token lives: the
    fix is to run the CLI once, not to wait."""
    if provider is not Provider.CLAUDE or not found.technical_detail or not found.technical_detail.strip():
        return None
    if kind_of(found.technical_detail) is not IssueKind.EXPIRED_CREDENTIALS:
        return None
    age = source.age(now)
    if age is None or age < STALE_AFTER:
        return None
    return text("credential_stale", age=duration_text(age.total_seconds()))
