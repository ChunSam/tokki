"""Mirrors the money parts of Cost.cs and CostAlerts.cs.

Amounts are Decimal, never float: a month of hourly buckets added in binary
floating point drifts by whole cents. Money only ever reaches the screen for an
account that actually pays this way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from .alerts import AlertCandidate
from .models import Provider, ProviderUsage, UsageSnapshot


@dataclass(frozen=True)
class CostAmount:
    value: Decimal
    currency: str = "USD"

    def __post_init__(self):
        object.__setattr__(self, "currency", self.currency.upper())


@dataclass(frozen=True)
class ClaudeExtraUsage:
    used: CostAmount
    limit: CostAmount | None
    severity: str | None


# --- JSON helpers -------------------------------------------------------------------

def _decimal(parent, name) -> Decimal | None:
    if not isinstance(parent, dict) or name not in parent:
        return None
    value = parent[name]
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    return None


def _string(parent, name) -> str | None:
    value = parent.get(name) if isinstance(parent, dict) else None
    return value if isinstance(value, str) else None


def _is_true(parent, name) -> bool:
    return isinstance(parent, dict) and parent.get(name) is True


def _minor_units(parent, name) -> CostAmount | None:
    """`{"amount_minor": 186, "currency": "USD", "exponent": 2}` is $1.86."""
    money = parent.get(name) if isinstance(parent, dict) else None
    minor = _decimal(money, "amount_minor")
    exponent = money.get("exponent") if isinstance(money, dict) else None
    if minor is None or isinstance(exponent, bool) or not isinstance(exponent, int) or not 0 <= exponent <= 6:
        return None
    return CostAmount(minor / (Decimal(10) ** exponent), _string(money, "currency") or "USD")


def _load(text: str):
    try:
        root = json.loads(text)
    except (ValueError, TypeError):
        return None
    return root if isinstance(root, dict) else None


# --- subscription money ------------------------------------------------------------------

def claude_extra(text: str) -> ClaudeExtraUsage | None:
    """From /api/oauth/usage. On when `spend` or `extra_usage` says so; the
    amount comes from `spend.used`, the one field that states its unit."""
    root = _load(text)
    if root is None:
        return None
    spend = root.get("spend") if isinstance(root.get("spend"), dict) else None
    extra = root.get("extra_usage") if isinstance(root.get("extra_usage"), dict) else None
    enabled = (spend is not None and _is_true(spend, "enabled")) or (extra is not None and _is_true(extra, "is_enabled"))
    used = _minor_units(spend, "used") if spend is not None else None
    if not enabled or used is None:
        return None
    return ClaudeExtraUsage(used, _minor_units(spend, "limit"), _string(spend, "severity"))


def _codex_credits_object(text: str):
    root = _load(text)
    credits = root.get("credits") if root else None
    return credits if isinstance(credits, dict) and _is_true(credits, "has_credits") else None


def codex_credits(text: str) -> Decimal | None:
    """The credit balance, in credits rather than dollars."""
    credits = _codex_credits_object(text)
    return _decimal(credits, "balance") if credits else None


def codex_credit_limit_reached(text: str) -> bool | None:
    credits = _codex_credits_object(text)
    return True if credits and _is_true(credits, "overage_limit_reached") else None


# --- formatting -------------------------------------------------------------------------

def _fixed(value: Decimal, places: int) -> str:
    quantum = Decimal(1).scaleb(-places)
    return str(value.quantize(quantum, rounding=ROUND_HALF_UP))


def cost_text(amount: CostAmount) -> str:
    """`$12.34`, `$1.2K`, `$3.4M`, `<$0.01`; other currencies as `12.34 EUR`."""
    magnitude = abs(amount.value)
    sign = "-" if amount.value < 0 else ""
    if magnitude >= Decimal(1_000_000):
        number = _fixed(magnitude / Decimal(1_000_000), 1) + "M"
    elif magnitude >= Decimal(1000):
        number = _fixed(magnitude / Decimal(1000), 1) + "K"
    elif Decimal(0) < magnitude < Decimal("0.01"):
        return "<$0.01" if amount.currency == "USD" else "<0.01 " + amount.currency
    else:
        number = _fixed(magnitude, 2)
    return sign + "$" + number if amount.currency == "USD" else sign + number + " " + amount.currency


def cost_compact(amount: CostAmount) -> str:
    """Whole dollars from $10 (`$42`), cents below, `$1.2K` from a thousand."""
    magnitude = abs(amount.value)
    if magnitude < Decimal(10) or magnitude >= Decimal(1000):
        return cost_text(amount)
    sign = "-" if amount.value < 0 else ""
    number = str(magnitude.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return sign + "$" + number if amount.currency == "USD" else sign + number + " " + amount.currency


def credits_text(value: Decimal) -> str:
    return _fixed(value, 2)


# --- budget -------------------------------------------------------------------------------

BUDGET_THRESHOLDS = (80, 100)


def budget_percent(spent: Decimal, budget: Decimal) -> int | None:
    """Whole percent of the budget spent, rounded down. None with no budget."""
    if budget <= 0:
        return None
    return int((spent * 100 / budget).to_integral_value(rounding=ROUND_FLOOR))


def reached_thresholds(spent: Decimal, budget: Decimal) -> list[int]:
    percent = budget_percent(spent, budget)
    return [] if percent is None else [t for t in BUDGET_THRESHOLDS if percent >= t]


# --- periods --------------------------------------------------------------------------------

def month_key(now: datetime, tz: tzinfo | None = None) -> str:
    """`2026-11`, for keys that last one local month."""
    local = now.astimezone(tz)
    return f"{local.year:04d}-{local.month:02d}"


# --- the subscription line ------------------------------------------------------------------

@dataclass(frozen=True)
class SubscriptionCostLine:
    label: str
    value: str
    percent: int | None


def subscription_line(usage: ProviderUsage, text) -> SubscriptionCostLine | None:
    """None unless the account pays past its plan."""
    extra = usage.extra_usage
    if extra is not None:
        value = cost_text(extra.used)
        percent = None
        if extra.limit is not None and extra.limit.currency == extra.used.currency:
            value += " / " + cost_text(extra.limit)
            percent = budget_percent(extra.used.value, extra.limit.value)
        return SubscriptionCostLine(text("cost_extra_usage"), value, percent)
    if usage.credit_balance is not None:
        return SubscriptionCostLine(text("cost_credit_balance"), credits_text(usage.credit_balance), None)
    return None


# --- money alerts ------------------------------------------------------------------------------

@dataclass
class CostAlertResult:
    alerts: list[AlertCandidate]
    delivered: set[str] = field(default_factory=set)


def _extra_usage_alert(usage: ProviderUsage, month: str, sent: set[str], text) -> AlertCandidate | None:
    """Claude's own word for how close extra usage is, once no longer `normal`.
    The word is in the id, so a step further up is news too."""
    severity = (usage.extra_usage.severity or "").lower() if usage.extra_usage else ""
    if not severity or severity == "normal":
        return None
    level = "".join(c for c in severity if c.isalpha() or c == "_")
    alert_id = f"claude-extra-{level}-{month}"
    line = subscription_line(usage, text)
    if not level or alert_id in sent or line is None:
        return None
    sent.add(alert_id)
    return AlertCandidate(alert_id, text("extra_usage_alert_title"), text("extra_usage_alert_body", value=line.value))


def _credit_limit_alert(usage: ProviderUsage, month: str, sent: set[str], text) -> AlertCandidate | None:
    alert_id = f"codex-credits-limit-{month}"
    if usage.credit_limit_reached is not True or alert_id in sent:
        return None
    sent.add(alert_id)
    return AlertCandidate(alert_id, text("credit_limit_alert_title"), text("credit_limit_alert_body"))


def evaluate_alerts(
    snapshot: UsageSnapshot | None,
    delivered: list[str] | set[str],
    notifications_enabled: bool,
    now: datetime,
    text,
    tz: tzinfo | None = None,
    extra_alerts=None,
) -> CostAlertResult:
    """Each id ends in its local month, which forgets months gone by and lets each
    thing be said again in a new one. `extra_alerts(sent, month)` adds the budget
    alerts once Admin cost is in."""
    month = month_key(now, tz)
    sent = {i for i in delivered if i.endswith("-" + month)}
    # Off sends nothing and records nothing.
    if not notifications_enabled:
        return CostAlertResult([], sent)
    alerts: list[AlertCandidate] = list(extra_alerts(sent, month) if extra_alerts else [])
    if snapshot is not None:
        for found in (_extra_usage_alert(snapshot.claude, month, sent, text), _credit_limit_alert(snapshot.codex, month, sent, text)):
            if found:
                alerts.append(found)
    return CostAlertResult(alerts, sent)


# --- fetch policy (Admin cost) ------------------------------------------------------------------

FETCH_INTERVAL = timedelta(minutes=10)
FETCH_RETRY_INTERVAL = timedelta(minutes=5)


def next_fetch(attempt_at: datetime, succeeded: bool) -> datetime:
    return attempt_at + (FETCH_INTERVAL if succeeded else FETCH_RETRY_INTERVAL)


def fetch_due(next_fetch_at: datetime | None, now: datetime) -> bool:
    return next_fetch_at is None or now >= next_fetch_at
