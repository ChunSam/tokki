"""Mirrors UsageParser.cs, CodexWindowMapper.cs and CredentialReader.cs."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .expiry import epoch_milliseconds, jwt_expiry, remaining_percent
from .models import Provider, ProviderUsage, UsageSource

PLAN_KEYS = ("plan_type", "planType", "subscription_type", "subscriptionType", "tier", "rate_limit_tier", "rateLimitTier")


# --- helpers ------------------------------------------------------------------

def _obj(element, name):
    if isinstance(element, dict):
        value = element.get(name)
        if isinstance(value, dict):
            return value
    return None


def _number(element, name) -> float | None:
    if not isinstance(element, dict) or name not in element:
        return None
    value = element[name]
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _string(element, name) -> str | None:
    if isinstance(element, dict):
        value = element.get(name)
        if isinstance(value, str):
            return value
    return None


def _plan(element) -> str | None:
    for key in PLAN_KEYS:
        value = _string(element, key)
        if value and value.strip():
            return value
    return None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def from_unix(seconds: float) -> datetime:
    return datetime.fromtimestamp(round(seconds * 1000) / 1000, tz=timezone.utc)


# --- Codex window mapping --------------------------------------------------------

@dataclass(frozen=True)
class CodexRateWindow:
    used_percent: float | None
    reset_at: datetime | None
    window_seconds: float | None


# Windows shorter than a day are the 5h lane; a day or longer is weekly. Since
# 2026-07 the API can report the weekly window as primary_window, so position
# alone would put weekly usage in the 5h lane.
LANE_BOUNDARY_SECONDS = 24 * 60 * 60


def map_codex_windows(primary: CodexRateWindow | None, secondary: CodexRateWindow | None):
    five_hour = seven_day = None
    for window, positional_is_five_hour in ((primary, True), (secondary, False)):
        if window is None:
            continue
        if window.window_seconds is not None:
            is_five_hour = window.window_seconds < LANE_BOUNDARY_SECONDS
        else:
            is_five_hour = positional_is_five_hour
        if is_five_hour:
            five_hour = five_hour or window
        else:
            seven_day = seven_day or window
    return five_hour, seven_day


def _codex_window(element) -> CodexRateWindow | None:
    if not isinstance(element, dict):
        return None
    reset = _number(element, "reset_at")
    return CodexRateWindow(
        _number(element, "used_percent"),
        datetime.fromtimestamp(int(reset), tz=timezone.utc) if reset is not None else None,
        _number(element, "limit_window_seconds"),
    )


# --- usage responses -------------------------------------------------------------

def parse_codex_usage(text: str, updated_at: datetime) -> ProviderUsage:
    root = json.loads(text)
    rate_limit = root["rate_limit"]
    five_hour, seven_day = map_codex_windows(
        _codex_window(_obj(rate_limit, "primary_window")),
        _codex_window(_obj(rate_limit, "secondary_window")),
    )
    return ProviderUsage(
        Provider.CODEX,
        remaining_percent(five_hour.used_percent if five_hour else None),
        remaining_percent(seven_day.used_percent if seven_day else None),
        five_hour.reset_at if five_hour else None,
        seven_day.reset_at if seven_day else None,
        UsageSource.API,
        None,
        _string(root, "plan_type"),
        updated_at,
    )


def _claude_remaining(window) -> int | None:
    used = _number(window, "utilization")
    return None if used is None else remaining_percent(min(max(used, 0.0), 100.0))


def parse_claude_usage(text: str, updated_at: datetime, fallback_plan: str | None = None) -> ProviderUsage:
    root = json.loads(text)
    five_hour = _obj(root, "five_hour")
    seven_day = _obj(root, "seven_day")
    remaining_5h = _claude_remaining(five_hour)
    remaining_7d = _claude_remaining(seven_day)
    if remaining_5h is None and remaining_7d is None:
        raise ValueError("Invalid response")
    return ProviderUsage(
        Provider.CLAUDE,
        remaining_5h,
        remaining_7d,
        parse_iso(_string(five_hour, "resets_at")),
        parse_iso(_string(seven_day, "resets_at")),
        UsageSource.API,
        None,
        _plan(root) or fallback_plan,
        updated_at,
    )


# --- stored credentials ------------------------------------------------------------

@dataclass(frozen=True)
class CodexAuth:
    access_token: str
    account_id: str
    expires_at: datetime | None


@dataclass(frozen=True)
class ClaudeCredential:
    access_token: str
    plan: str | None
    expires_at: datetime | None


def read_codex_auth(home: Path | None = None) -> CodexAuth:
    root = json.loads(paths.codex_auth_path(home).read_text(encoding="utf-8"))
    tokens = root["tokens"]
    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not access_token or not str(access_token).strip() or not account_id or not str(account_id).strip():
        raise ValueError("Missing Codex credentials")
    return CodexAuth(access_token, account_id, jwt_expiry(access_token))


def read_claude_credential(home: Path | None = None) -> ClaudeCredential:
    root = json.loads(paths.claude_credentials_path(home).read_text(encoding="utf-8"))
    oauth = root["claudeAiOauth"]
    token = oauth.get("accessToken")
    if not token or not str(token).strip():
        raise ValueError("Missing Claude credentials")
    return ClaudeCredential(token, _plan(oauth), epoch_milliseconds(oauth.get("expiresAt")))
