"""Mirrors windows/TokenTracker.Windows.Core/Models.cs."""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cost import ClaudeExtraUsage


class Provider(str, enum.Enum):
    CLAUDE = "claude"
    CODEX = "codex"

    @property
    def display_name(self) -> str:
        return "Claude" if self is Provider.CLAUDE else "Codex"


class UsageSource(str, enum.Enum):
    # A measurement this app took itself, from the provider's usage endpoint.
    API = "Api"
    # Claude Code's status line, relayed by the bridge. Real data, observed at a
    # time this app did not choose, so it never enters history.
    STATUS_LINE = "StatusLine"
    STALE_CACHE = "StaleCache"
    UNAVAILABLE = "Unavailable"


class DisplayMode(str, enum.Enum):
    LOWEST_REMAINING = "LowestRemaining"
    BOTH = "Both"
    CODEX_ONLY = "CodexOnly"
    CLAUDE_ONLY = "ClaudeOnly"


class ProviderLabelStyle(str, enum.Enum):
    ABBREVIATION = "Abbreviation"
    ICON = "Icon"


class AppLanguage(str, enum.Enum):
    SYSTEM = "System"
    ENGLISH = "English"
    KOREAN = "Korean"


class ForecastWindow(str, enum.Enum):
    FIVE_HOUR = "FiveHour"
    SEVEN_DAY = "SevenDay"


@dataclass(frozen=True)
class ProviderUsage:
    provider: Provider
    remaining_5h: int | None
    remaining_7d: int | None
    reset_at_5h: datetime | None
    reset_at_7d: datetime | None
    source: UsageSource
    error: str | None
    plan: str | None
    updated_at: datetime
    # Money the usage response carried. None keeps it off screen: an account
    # that does not pay past its plan never sees any.
    extra_usage: "ClaudeExtraUsage | None" = None
    credit_balance: "Decimal | None" = None
    credit_limit_reached: bool | None = None

    @property
    def is_available(self) -> bool:
        return self.remaining_5h is not None or self.remaining_7d is not None

    @property
    def display_name(self) -> str:
        return self.provider.display_name

    def with_(self, **changes) -> "ProviderUsage":
        return replace(self, **changes)

    @staticmethod
    def unavailable(provider: Provider, error: str, now: datetime) -> "ProviderUsage":
        return ProviderUsage(provider, None, None, None, None, UsageSource.UNAVAILABLE, error, None, now)


@dataclass(frozen=True)
class UsageSnapshot:
    claude: ProviderUsage
    codex: ProviderUsage
    updated_at: datetime

    def usage(self, provider: Provider) -> ProviderUsage:
        return self.claude if provider is Provider.CLAUDE else self.codex
