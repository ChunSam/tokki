"""Mirrors CacheStore.cs and UsageSnapshotCachePolicy."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from decimal import Decimal, InvalidOperation

from . import paths
from .cost import ClaudeExtraUsage, CostAmount
from .models import Provider, ProviderUsage, UsageSnapshot, UsageSource
from .parser import parse_iso

CACHE_VERSION = 1


def _usage_to_dict(usage: ProviderUsage) -> dict:
    def iso(value):
        return value.isoformat() if value else None

    return {
        "Provider": usage.provider.value,
        "RemainingPercent5h": usage.remaining_5h,
        "RemainingPercent7d": usage.remaining_7d,
        "ResetAt5h": iso(usage.reset_at_5h),
        "ResetAt7d": iso(usage.reset_at_7d),
        "Source": usage.source.value,
        "Error": usage.error,
        "Plan": usage.plan,
        "UpdatedAt": usage.updated_at.isoformat(),
        "ExtraUsage": _extra_to_dict(usage.extra_usage),
        "CreditBalance": None if usage.credit_balance is None else str(usage.credit_balance),
        "CreditLimitReached": usage.credit_limit_reached,
    }


def _amount_to_dict(amount: CostAmount | None):
    return None if amount is None else {"Value": str(amount.value), "Currency": amount.currency}


def _amount_from_dict(raw) -> CostAmount | None:
    if not isinstance(raw, dict):
        return None
    try:
        return CostAmount(Decimal(str(raw["Value"])), str(raw.get("Currency") or "USD"))
    except (KeyError, InvalidOperation):
        return None


def _extra_to_dict(extra: ClaudeExtraUsage | None):
    if extra is None:
        return None
    return {"Used": _amount_to_dict(extra.used), "Limit": _amount_to_dict(extra.limit), "Severity": extra.severity}


def _extra_from_dict(raw) -> ClaudeExtraUsage | None:
    if not isinstance(raw, dict):
        return None
    used = _amount_from_dict(raw.get("Used"))
    if used is None:
        return None
    severity = raw.get("Severity")
    return ClaudeExtraUsage(used, _amount_from_dict(raw.get("Limit")), severity if isinstance(severity, str) else None)


def _decimal_or_none(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_from_dict(raw: dict) -> ProviderUsage:
    updated = parse_iso(raw["UpdatedAt"])
    if updated is None:
        raise ValueError("no UpdatedAt")
    return ProviderUsage(
        Provider(raw["Provider"]),
        _int_or_none(raw.get("RemainingPercent5h")),
        _int_or_none(raw.get("RemainingPercent7d")),
        parse_iso(raw.get("ResetAt5h")),
        parse_iso(raw.get("ResetAt7d")),
        UsageSource(raw["Source"]),
        raw.get("Error"),
        raw.get("Plan"),
        updated,
        _extra_from_dict(raw.get("ExtraUsage")),
        _decimal_or_none(raw.get("CreditBalance")),
        raw.get("CreditLimitReached") if isinstance(raw.get("CreditLimitReached"), bool) else None,
    )


class CacheStore:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.snapshot_cache_path()

    def load(self, max_age: timedelta, now: datetime) -> UsageSnapshot | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("Version") != CACHE_VERSION:
                return None
            snap = raw["Snapshot"]
            updated = parse_iso(snap["UpdatedAt"])
            if updated is None or now - updated > max_age:
                return None
            return UsageSnapshot(_usage_from_dict(snap["Claude"]), _usage_from_dict(snap["Codex"]), updated)
        except Exception:
            return None

    def save(self, snapshot: UsageSnapshot) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps({
                "Version": CACHE_VERSION,
                "Snapshot": {
                    "Claude": _usage_to_dict(snapshot.claude),
                    "Codex": _usage_to_dict(snapshot.codex),
                    "UpdatedAt": snapshot.updated_at.isoformat(),
                },
            }, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass


def _can_reuse(usage: ProviderUsage, now: datetime, max_age: timedelta) -> bool:
    age = now - usage.updated_at
    return usage.is_available and usage.source is not UsageSource.UNAVAILABLE and timedelta(0) <= age <= max_age


def apply_cache_policy(
    current: UsageSnapshot,
    stale: UsageSnapshot | None,
    now: datetime,
    claude_enabled: bool = True,
    codex_enabled: bool = True,
    max_age: timedelta = timedelta(hours=12),
) -> UsageSnapshot:
    """A provider that got no new value keeps its last good one, marked as cached,
    so a failure never turns into an empty menu."""
    if stale is None:
        return current
    claude, codex = current.claude, current.codex
    if claude_enabled and not claude.is_available and _can_reuse(stale.claude, now, max_age):
        claude = stale.claude.with_(source=UsageSource.STALE_CACHE, error=claude.error)
    if codex_enabled and not codex.is_available and _can_reuse(stale.codex, now, max_age):
        codex = stale.codex.with_(source=UsageSource.STALE_CACHE, error=codex.error)
    return UsageSnapshot(claude, codex, current.updated_at)
