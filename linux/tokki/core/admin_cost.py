"""Mirrors AdminCost.cs, AdminKeyStore.cs and CostDisplay.cs, plus the cost
periods, aggregator and Admin API parsers from Cost.cs.

An organization's Admin API key can manage the whole organization, so it is
kept in the desktop keyring (libsecret), never a file, and nothing here ever
prints more than its last four characters. A provider's key is read only while
its API cost is switched on.
"""

from __future__ import annotations

import enum
import json
import os
import random
import threading
import urllib.parse
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol

from . import paths
from .alerts import AlertCandidate
from .cost import BUDGET_THRESHOLDS, CostAmount, budget_percent, cost_compact, cost_text, fetch_due, month_key, next_fetch, reached_thresholds
from .expiry import token_fingerprint
from .models import AppLanguage, Provider
from .parser import parse_iso
from .ratelimit import RateLimitState, RateLimitStore, UsageHttpError

PROVIDERS = (Provider.CLAUDE, Provider.CODEX)


# --- the local zone -------------------------------------------------------------------

def local_zone() -> tzinfo:
    """The device's zone with its rules, so DST days come out 23 or 25 hours long.
    A fixed offset is the last resort."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    candidates = []
    if os.environ.get("TZ", "").strip(":"):
        candidates.append(os.environ["TZ"].strip(":"))
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        with open("/etc/timezone", encoding="utf-8") as handle:
            candidates.append(handle.read().strip())
    except OSError:
        pass
    for name in candidates:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return datetime.now().astimezone().tzinfo or timezone.utc


# --- periods ---------------------------------------------------------------------------

def _local_midnight(day: date, zone: tzinfo) -> datetime:
    """The first instant of a local day. A zone that springs forward at midnight
    has no 00:00, so the day starts at its first valid minute; of two midnights
    the earlier one counts (fold=0). Returned in UTC: Python subtracts two times
    in the same zone by wall clock, which would make a DST day 24 hours long."""
    local = datetime(day.year, day.month, day.day)
    for _ in range(180):
        candidate = local.replace(tzinfo=zone, fold=0)
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local:
            return candidate.astimezone(timezone.utc)
        local += timedelta(minutes=1)
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)


def day_start(now: datetime, zone: tzinfo) -> datetime:
    return _local_midnight(now.astimezone(zone).date(), zone)


def month_start(now: datetime, zone: tzinfo) -> datetime:
    local = now.astimezone(zone)
    return _local_midnight(date(local.year, local.month, 1), zone)


def request_range(now: datetime, zone: tzinfo) -> tuple[datetime, datetime]:
    start = month_start(now, zone)
    return start, (now if now > start else start)


# --- pages and reports ---------------------------------------------------------------------

@dataclass(frozen=True)
class CostLineItem:
    name: str
    amount: CostAmount


def top_items(items: list[CostLineItem], limit: int = 3) -> list[CostLineItem]:
    """The largest, after adding up those that share a name across pages."""
    totals: dict[str, CostLineItem] = {}
    for item in items:
        held = totals.get(item.name)
        totals[item.name] = CostLineItem(item.name, CostAmount(
            (held.amount.value if held else Decimal(0)) + item.amount.value, item.amount.currency))
    ranked = sorted((i for i in totals.values() if i.amount.value > 0), key=lambda i: (-i.amount.value, i.name))
    return ranked[:limit]


class FreshnessKind(str, enum.Enum):
    LIVE = "Live"
    REFRESHED_AT = "RefreshedAt"
    NOT_TALLIED = "NotTallied"


@dataclass(frozen=True)
class CostFreshness:
    kind: FreshnessKind
    refreshed_at: datetime | None = None


LIVE = CostFreshness(FreshnessKind.LIVE)
NOT_TALLIED = CostFreshness(FreshnessKind.NOT_TALLIED)


@dataclass(frozen=True)
class CostBucket:
    start: datetime
    end: datetime
    amount: CostAmount


@dataclass(frozen=True)
class CostPage:
    buckets: list[CostBucket]
    items: list[CostLineItem]
    next_page: str | None
    freshness: CostFreshness


@dataclass(frozen=True)
class CostReport:
    month_to_date: CostAmount | None
    today: CostAmount | None
    freshness: CostFreshness


BOUNDARY_TOLERANCE = timedelta(hours=1)


def _overlaps(bucket: CostBucket, start: datetime, end: datetime) -> bool:
    return bucket.end > start and bucket.start < end


def report(buckets: list[CostBucket], freshness: CostFreshness, now: datetime, zone: tzinfo) -> CostReport:
    if freshness.kind is FreshnessKind.NOT_TALLIED:
        return CostReport(None, None, freshness)
    m_start, d_start = month_start(now, zone), day_start(now, zone)
    currency = buckets[0].amount.currency if buckets else "USD"
    # A watermark from before the 1st covers none of this month.
    month_tallied = freshness.kind is not FreshnessKind.REFRESHED_AT or freshness.refreshed_at > m_start
    month = CostAmount(sum((b.amount.value for b in buckets if _overlaps(b, m_start, now)), Decimal(0)), currency) if month_tallied else None
    todays = [b for b in buckets if _overlaps(b, d_start, now)]
    # A bucket that starts well before local midnight cannot be told apart from yesterday.
    straddles = any(d_start - b.start > BOUNDARY_TOLERANCE for b in todays)
    tallied = freshness.kind is not FreshnessKind.REFRESHED_AT or freshness.refreshed_at > d_start
    today = CostAmount(sum((b.amount.value for b in todays), Decimal(0)), currency) if tallied and not straddles else None
    return CostReport(month, today, freshness)


def _load_exact(text: str):
    try:
        root = json.loads(text, parse_float=Decimal)
    except (ValueError, TypeError):
        return None
    return root if isinstance(root, dict) else None


def _dec(parent, name) -> Decimal | None:
    value = parent.get(name) if isinstance(parent, dict) else None
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value)) if not isinstance(value, Decimal) else value
    except Exception:
        return None
    return number if number.is_finite() else None


def _str(parent, name) -> str | None:
    value = parent.get(name) if isinstance(parent, dict) else None
    return value if isinstance(value, str) else None


def _next_page(root) -> str | None:
    return _str(root, "next_page") if root.get("has_more") is True else None


def parse_anthropic_page(text: str) -> CostPage | None:
    """Analytics API: amounts are cents as decimal strings."""
    root = _load_exact(text)
    if root is None or not isinstance(root.get("data"), list):
        return None
    buckets, items = [], []
    for row in root["data"]:
        cost = row.get("cost") if isinstance(row, dict) else None
        cents = _dec(cost, "amount") if isinstance(cost, dict) else None
        if cents is None:
            continue
        amount = CostAmount(cents / 100, _str(cost, "currency") or "USD")
        start, end = parse_iso(_str(row, "starting_at")), parse_iso(_str(row, "ending_at"))
        if start and end:
            buckets.append(CostBucket(start, end, amount))
        group = row.get("group")
        model = _str(group, "model") if isinstance(group, dict) else None
        if model:
            items.append(CostLineItem(model, amount))
    refreshed = parse_iso(_str(root, "data_refreshed_at"))
    freshness = CostFreshness(FreshnessKind.REFRESHED_AT, refreshed) if refreshed else NOT_TALLIED
    return CostPage(buckets, items, _next_page(root), freshness)


def parse_openai_page(text: str) -> CostPage | None:
    """Organization costs: dollars as numbers, under `results` or `result`."""
    root = _load_exact(text)
    if root is None or not isinstance(root.get("data"), list):
        return None
    buckets, items = [], []
    for row in root["data"]:
        if not isinstance(row, dict):
            continue
        start, end = row.get("start_time"), row.get("end_time")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            continue
        total, currency = Decimal(0), "USD"
        for name in ("results", "result"):
            results = row.get(name)
            if not isinstance(results, list):
                continue
            for result in results:
                money = result.get("amount") if isinstance(result, dict) else None
                value = _dec(money, "value") if isinstance(money, dict) else None
                if value is None:
                    continue
                currency = (_str(money, "currency") or currency).upper()
                total += value
                line = _str(result, "line_item")
                if line:
                    items.append(CostLineItem(line, CostAmount(value, currency)))
            break
        buckets.append(CostBucket(datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(end, timezone.utc), CostAmount(total, currency)))
    return CostPage(buckets, items, _next_page(root), LIVE)


# --- requests ---------------------------------------------------------------------------------

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/organizations/analytics/cost"
OPENAI_ENDPOINT = "https://api.openai.com/v1/organization/costs"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build(endpoint: str, page: str | None, *query: tuple[str, str]) -> str:
    pairs = [f"{k}={v}" for k, v in query]
    if page is not None:
        pairs.append("page=" + urllib.parse.quote(page, safe=""))
    return endpoint + "?" + "&".join(pairs)


def _covers_an_hour(start: datetime, end: datetime) -> bool:
    return int(end.timestamp()) // 3600 > int(start.timestamp()) // 3600


def anthropic_hourly(start: datetime, end: datetime, page: str | None) -> str | None:
    if not _covers_an_hour(start, end):
        return None
    return _build(ANTHROPIC_ENDPOINT, page, ("starting_at", _iso(start)), ("ending_at", _iso(end)), ("time_bucket_width", "1h"), ("limit", "1000"))


def anthropic_by_model(start: datetime, end: datetime, page: str | None) -> str | None:
    if not _covers_an_hour(start, end):
        return None
    return _build(ANTHROPIC_ENDPOINT, page, ("starting_at", _iso(start)), ("ending_at", _iso(end)), ("group_by%5B%5D", "model"), ("limit", "100"))


def openai_days(start: datetime, end: datetime, page: str | None) -> str | None:
    s, e = int(start.timestamp()), int(end.timestamp())
    if e <= s:
        return None
    return _build(OPENAI_ENDPOINT, page, ("start_time", str(s)), ("end_time", str(e)), ("bucket_width", "1d"), ("group_by%5B%5D", "line_item"), ("limit", "31"))


def request_headers(provider: Provider, key: str) -> dict[str, str]:
    if provider is Provider.CLAUDE:
        return {"x-api-key": key, "anthropic-version": "2023-06-01", "anthropic-beta": "analytics-usage-cost-2026-08-24", "Accept": "application/json"}
    return {"Authorization": "Bearer " + key, "Accept": "application/json"}


# --- keys ---------------------------------------------------------------------------------------

def key_suffix(key: str) -> str:
    """`…ab12`: all of a key settings and diagnostics may show."""
    return "…" + key[-4:] if len(key) > 8 else "…"


def key_account(provider: Provider) -> str:
    return "anthropic-admin" if provider is Provider.CLAUDE else "openai-admin"


def organization(provider: Provider) -> str:
    return "Anthropic" if provider is Provider.CLAUDE else "OpenAI"


def key_prefix(provider: Provider) -> str:
    return "sk-ant-admin" if provider is Provider.CLAUDE else "sk-admin-"


def looks_like_admin_key(key: str, provider: Provider) -> bool:
    return key.strip().startswith(key_prefix(provider))


class AdminKeyStore(Protocol):
    def key(self, provider: Provider) -> str | None: ...
    def save(self, provider: Provider, key: str) -> bool: ...
    def delete(self, provider: Provider) -> None: ...


class MemoryKeyStore:
    def __init__(self):
        self.keys: dict[Provider, str] = {}
        self.reads = 0

    def key(self, provider):
        self.reads += 1
        value = self.keys.get(provider, "").strip()
        return value or None

    def save(self, provider, key):
        self.keys[provider] = key.strip()
        return True

    def delete(self, provider):
        self.keys.pop(provider, None)


class _NoKeyStore:
    def key(self, provider):
        return None

    def save(self, provider, key):
        return False

    def delete(self, provider):
        pass


class SecretKeyStore:
    """The desktop keyring through libsecret, under the service the macOS
    keychain uses: `Token Tracker`, account `anthropic-admin` / `openai-admin`."""

    SERVICE = "Token Tracker"

    def __init__(self):
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        self._secret = Secret
        self._schema = Secret.Schema.new(
            "org.freedesktop.Secret.Generic", Secret.SchemaFlags.DONT_MATCH_NAME,
            {"service": Secret.SchemaAttributeType.STRING, "account": Secret.SchemaAttributeType.STRING})

    def _attributes(self, provider: Provider) -> dict[str, str]:
        return {"service": self.SERVICE, "account": key_account(provider)}

    def key(self, provider):
        try:
            value = self._secret.password_lookup_sync(self._schema, self._attributes(provider), None)
        except Exception:
            return None
        return value.strip() if value and value.strip() else None

    def save(self, provider, key):
        label = f"Token Tracker ({provider.display_name} Admin key)"
        try:
            return bool(self._secret.password_store_sync(
                self._schema, self._attributes(provider), self._secret.COLLECTION_DEFAULT, label, key.strip(), None))
        except Exception:
            return False

    def delete(self, provider):
        try:
            self._secret.password_clear_sync(self._schema, self._attributes(provider), None)
        except Exception:
            pass


# --- the client ------------------------------------------------------------------------------------

class FailureKind(str, enum.Enum):
    KEY_REJECTED = "KeyRejected"
    RATE_LIMITED = "RateLimited"
    FAILED = "Failed"


@dataclass(frozen=True)
class CostFailure:
    kind: FailureKind
    message: str


@dataclass(frozen=True)
class ProviderCost:
    provider: Provider
    month_to_date: CostAmount | None
    today: CostAmount | None
    top: list[CostLineItem]
    data_refreshed_at: datetime | None
    measured_at: datetime


@dataclass(frozen=True)
class CostFetchResult:
    cost: ProviderCost | None
    failure: CostFailure | None


MAX_PAGES = 10
DEFAULT_RATE_LIMIT_COOLDOWN = timedelta(minutes=5)
COST_TIMEOUT_SECONDS = 15


def line_item_group(name: str) -> str:
    """OpenAI splits a model into `gpt-6, input` and so on; they add up under it."""
    comma = name.find(", ")
    return name if comma < 0 else name[:comma]


def item_display(name: str) -> str:
    """`claude-opus-5-5` is `Opus 5.5`; other names stay as they are."""
    prefix = "claude-"
    if not name.startswith(prefix):
        return name
    parts = [p for p in name[len(prefix):].split("-") if p]
    family = next((p for p in parts if p[0].isalpha()), None)
    if family is None:
        return name
    version = [p for p in parts if len(p) < 8 and p.isdigit()]
    title = family[0].upper() + family[1:]
    return title if not version else title + " " + ".".join(version)


class _InvalidResponse(Exception):
    pass


class AdminCostClient:
    def __init__(self, provider: Provider, transport=None, rate_limit_path: Path | None = None, rng: random.Random | None = None):
        from .client import urllib_transport
        self.provider = provider
        self._transport = transport or (lambda url, headers: urllib_transport(url, headers))
        self._limit = RateLimitState(RateLimitStore(rate_limit_path or paths.app_data_directory() / f"{provider.value}-cost-rate-limit.json"), rng)

    @property
    def service_name(self) -> str:
        return "Anthropic Admin API" if self.provider is Provider.CLAUDE else "OpenAI Admin API"

    def fetch(self, key: str, now: datetime, zone: tzinfo) -> CostFetchResult:
        fingerprint = token_fingerprint(key)
        limited = self._limit.current_error(self.service_name, fingerprint, now)
        if limited:
            return CostFetchResult(None, CostFailure(FailureKind.RATE_LIMITED, str(limited)))
        try:
            cost = self._fetch_cost(key, now, zone)
            self._limit.clear(now)
            return CostFetchResult(cost, None)
        except UsageHttpError as error:
            if error.status == 429:
                self._limit.back_off(error.retry_after or DEFAULT_RATE_LIMIT_COOLDOWN, fingerprint, now)
                limited = self._limit.current_error(self.service_name, fingerprint, now)
                return CostFetchResult(None, CostFailure(FailureKind.RATE_LIMITED, str(limited or error)))
            if error.status in (401, 403):
                return CostFetchResult(None, CostFailure(FailureKind.KEY_REJECTED, str(error)))
            return CostFetchResult(None, CostFailure(FailureKind.FAILED, str(error)))
        except _InvalidResponse:
            return CostFetchResult(None, CostFailure(FailureKind.FAILED, "Invalid response"))
        except TimeoutError:
            return CostFetchResult(None, CostFailure(FailureKind.FAILED, "Timed out contacting " + self.service_name))
        except Exception as error:
            return CostFetchResult(None, CostFailure(FailureKind.FAILED, str(error) or type(error).__name__))

    def _fetch_cost(self, key: str, now: datetime, zone: tzinfo) -> ProviderCost:
        start, end = request_range(now, zone)
        if self.provider is Provider.CLAUDE:
            totals = self._all_pages(key, parse_anthropic_page, lambda page: anthropic_hourly(start, end, page), now)
        else:
            totals = self._all_pages(key, parse_openai_page, lambda page: openai_days(start, end, page), now)
        rep = report(totals.buckets, totals.freshness, now, zone)
        items = [CostLineItem(line_item_group(i.name), i.amount) for i in totals.items]
        if self.provider is Provider.CLAUDE:
            # The split is a query of its own, worth asking only once the month has something.
            items = []
            if rep.month_to_date is not None:
                items = self._all_pages(key, parse_anthropic_page, lambda page: anthropic_by_model(start, end, page), now).items
        refreshed = rep.freshness.refreshed_at if rep.freshness.kind is FreshnessKind.REFRESHED_AT else None
        return ProviderCost(self.provider, rep.month_to_date, rep.today, top_items(items), refreshed, now)

    def _all_pages(self, key: str, parse: Callable[[str], CostPage | None], url: Callable[[str | None], str | None], now: datetime) -> CostPage:
        from .client import retry_after
        nxt = url(None)
        if nxt is None:
            return CostPage([], [], None, NOT_TALLIED)
        buckets, items, freshness = [], [], None
        for _ in range(MAX_PAGES):
            response = self._transport(nxt, request_headers(self.provider, key))
            if not 200 <= response.status < 300:
                raise UsageHttpError(response.status, self.service_name, retry_after(response.headers, now))
            page = parse(response.body)
            if page is None:
                raise _InvalidResponse()
            buckets += page.buckets
            items += page.items
            freshness = freshness or page.freshness
            if page.next_page is None:
                return CostPage(buckets, items, None, freshness)
            nxt = url(page.next_page)
            if nxt is None:
                raise _InvalidResponse()
        # A partial month would read as a smaller bill, so it is no reading at all.
        raise _InvalidResponse()


def usable(cost: ProviderCost, now: datetime, zone: tzinfo) -> ProviderCost | None:
    """Money spent stays spent: the month stands until the local month turns,
    today until the local date does."""
    if month_start(cost.measured_at, zone) != month_start(now, zone):
        return None
    return cost if day_start(cost.measured_at, zone) == day_start(now, zone) else replace(cost, today=None)


# --- the cost cache ------------------------------------------------------------------------------------

def _amount_dict(amount: CostAmount | None):
    return None if amount is None else {"Value": str(amount.value), "Currency": amount.currency}


def _amount_from(raw) -> CostAmount | None:
    if not isinstance(raw, dict):
        return None
    try:
        return CostAmount(Decimal(str(raw["Value"])), str(raw.get("Currency") or "USD"))
    except Exception:
        return None


class CostCacheStore:
    """The last good reading per provider, tied to its key by fingerprint only."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.app_data_directory() / "cost-cache.json"

    def load(self) -> list[tuple[str, ProviderCost]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("Version") != 1:
                return []
            out = []
            for entry in raw["Entries"]:
                c = entry["Cost"]
                measured = parse_iso(c["MeasuredAt"])
                if measured is None:
                    continue
                top = [CostLineItem(i["Name"], a) for i in c.get("TopItems", []) if (a := _amount_from(i.get("Amount")))]
                out.append((entry["KeyFingerprint"], ProviderCost(
                    Provider(c["Provider"]), _amount_from(c.get("MonthToDate")), _amount_from(c.get("Today")),
                    top, parse_iso(c.get("DataRefreshedAt")), measured)))
            return out
        except Exception:
            return []

    def save(self, entries: list[tuple[str, ProviderCost]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps({"Version": 1, "Entries": [{
                "KeyFingerprint": fp,
                "Cost": {
                    "Provider": c.provider.value,
                    "MonthToDate": _amount_dict(c.month_to_date),
                    "Today": _amount_dict(c.today),
                    "TopItems": [{"Name": i.name, "Amount": _amount_dict(i.amount)} for i in c.top],
                    "DataRefreshedAt": c.data_refreshed_at.isoformat() if c.data_refreshed_at else None,
                    "MeasuredAt": c.measured_at.isoformat(),
                },
            } for fp, c in entries]}, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass


# --- the service ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CostState:
    enabled: bool = False
    key_suffix: str | None = None
    cost: ProviderCost | None = None
    failure: CostFailure | None = None
    last_attempt_at: datetime | None = None
    next_fetch_at: datetime | None = None
    key_fingerprint: str | None = field(default=None, repr=False)

    @property
    def is_cached(self) -> bool:
        return self.cost is not None and self.failure is not None


class AdminCostService:
    """Each provider on its own schedule, apart from the usage poll; the last good
    reading carries through failures. One caller at a time."""

    def __init__(self, key_store: AdminKeyStore | None = None, clients: dict[Provider, AdminCostClient] | None = None, cache_store: CostCacheStore | None = None):
        self._keys = key_store
        self._clients = clients if clients is not None else {p: AdminCostClient(p) for p in PROVIDERS}
        self._cache = cache_store or CostCacheStore()
        self._cache_loaded = False
        self._lock = threading.Lock()
        self.states: dict[Provider, CostState] = {}

    def _key_store(self) -> AdminKeyStore:
        # Created on first use, so a user who never turns this on never touches the keyring.
        if self._keys is None:
            try:
                self._keys = SecretKeyStore()
            except (ImportError, ValueError) as error:
                # No libsecret: no key can be kept, so API cost reads as "no key".
                print(f"tokki: keyring unavailable: {error}", file=__import__("sys").stderr)
                self._keys = _NoKeyStore()
        return self._keys

    def refresh_if_due(self, enabled: dict[Provider, bool], now: datetime, zone: tzinfo) -> dict[Provider, CostState]:
        with self._lock:
            self._load_cache(now, zone)
            for provider in PROVIDERS:
                self._refresh(provider, enabled.get(provider, False), now, zone)
            return dict(self.states)

    def _refresh(self, provider: Provider, on: bool, now: datetime, zone: tzinfo) -> None:
        state = self.states.get(provider, CostState())
        state = replace(state, cost=usable(state.cost, now, zone) if state.cost else None)
        if not on:
            # Off forgets the schedule, so switching back on fetches at once.
            self.states[provider] = CostState(cost=state.cost, key_fingerprint=state.key_fingerprint)
            return
        state = replace(state, enabled=True)
        key = self._key_store().key(provider)
        if key is None:
            self.states[provider] = replace(state, key_suffix=None, failure=None, next_fetch_at=None)
            return
        fingerprint = token_fingerprint(key)
        if state.key_fingerprint != fingerprint:
            # Another key may be another organization: nothing of the old one carries over.
            state = CostState(enabled=True, key_fingerprint=fingerprint)
        state = replace(state, key_suffix=key_suffix(key))
        client = self._clients.get(provider)
        if not fetch_due(state.next_fetch_at, now) or client is None:
            self.states[provider] = state
            return
        result = client.fetch(key, now, zone)
        state = replace(state, last_attempt_at=now)
        if result.cost is not None:
            state = replace(state, cost=result.cost, failure=None, next_fetch_at=next_fetch(now, True))
        else:
            state = replace(state, failure=result.failure, next_fetch_at=next_fetch(now, False))
        self.states[provider] = state
        self._save_cache()

    def _load_cache(self, now: datetime, zone: tzinfo) -> None:
        if self._cache_loaded:
            return
        self._cache_loaded = True
        for fingerprint, cost in self._cache.load():
            held = usable(cost, now, zone)
            if held:
                self.states[held.provider] = CostState(cost=held, key_fingerprint=fingerprint)

    def _save_cache(self) -> None:
        self._cache.save([(s.key_fingerprint, s.cost) for p in PROVIDERS
                          if (s := self.states.get(p)) and s.cost is not None and s.key_fingerprint])


def diagnostics_lines(provider: Provider, state: CostState) -> list[str]:
    """Never the key: its last four characters at most."""
    name = provider.display_name + " API cost"
    if not state.enabled:
        return [name + ": off"]
    if state.key_suffix is None:
        return [name + ": on, no Admin key"]
    lines = [name + ": on, key " + state.key_suffix]
    if state.cost is not None:
        c = state.cost
        lines.append(name + " month: " + (cost_text(c.month_to_date) if c.month_to_date else "not tallied"))
        lines.append(name + " today: " + (cost_text(c.today) if c.today else "--"))
        if c.data_refreshed_at:
            lines.append(name + " complete up to: " + _iso(c.data_refreshed_at))
        if c.top:
            lines.append(name + " top: " + ", ".join(i.name + " " + cost_text(i.amount) for i in c.top))
        lines.append(name + " measured: " + _iso(c.measured_at))
    kind = {FailureKind.KEY_REJECTED: "keyRejected", FailureKind.RATE_LIMITED: "rateLimited"}
    outcome = (kind.get(state.failure.kind, "failed") + ": " + state.failure.message) if state.failure else "ok"
    lines.append(name + " last fetch: " + (_iso(state.last_attempt_at) + " " + outcome if state.last_attempt_at else "none"))
    return lines


# --- what reaches the screen --------------------------------------------------------------------------------

def shows(subscription_extra: bool, api_cost_enabled: bool, has_admin_key: bool) -> bool:
    return subscription_extra or (api_cost_enabled and has_admin_key)


class CostStatus(str, enum.Enum):
    OK = "Ok"
    LOADING = "Loading"
    CACHED = "Cached"
    KEY_REJECTED = "KeyRejected"
    RATE_LIMITED = "RateLimited"
    FAILED = "Failed"


@dataclass(frozen=True)
class AdminCostView:
    status: CostStatus
    status_title: str
    month_label: str
    month_value: str
    budget_percent: int | None
    budget_text: str | None
    detail: str | None
    as_of: str | None
    failure_message: str | None

    @property
    def is_budget_warning(self) -> bool:
        return (self.budget_percent or 0) >= BUDGET_THRESHOLDS[0]


def status_of(state: CostState) -> CostStatus:
    # A rejected key outranks a cached reading: it will not mend itself.
    if state.failure and state.failure.kind is FailureKind.KEY_REJECTED:
        return CostStatus.KEY_REJECTED
    if state.is_cached:
        return CostStatus.CACHED
    if state.failure and state.failure.kind is FailureKind.RATE_LIMITED:
        return CostStatus.RATE_LIMITED
    if state.failure:
        return CostStatus.FAILED
    return CostStatus.LOADING if state.cost is None else CostStatus.OK


_STATUS_KEYS = {
    CostStatus.OK: "status_ok", CostStatus.LOADING: "cost_loading", CostStatus.CACHED: "status_using_cached_data",
    CostStatus.KEY_REJECTED: "cost_admin_key_rejected", CostStatus.RATE_LIMITED: "status_rate_limited", CostStatus.FAILED: "cost_fetch_failed",
}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def time_text(when: datetime, now: datetime, zone: tzinfo, language: AppLanguage) -> str:
    """`14:20` today, `Nov 2 22:10` / `11월 2일 22:10` on another day."""
    local, local_now = when.astimezone(zone), now.astimezone(zone)
    clock = local.strftime("%H:%M")
    if local.date() == local_now.date():
        return clock
    if language is AppLanguage.KOREAN:
        return f"{local.month}월 {local.day}일 {clock}"
    return f"{_MONTHS[local.month - 1]} {local.day} {clock}"


def view(state: CostState, budget: int, now: datetime, zone: tzinfo, text, language: AppLanguage) -> AdminCostView | None:
    """None unless switched on and with a key."""
    if not shows(False, state.enabled, state.key_suffix is not None):
        return None
    c = state.cost
    status = status_of(state)
    month_value = "--"
    if c is not None:
        month_value = cost_text(c.month_to_date) if c.month_to_date else text("cost_not_yet_tallied")
    b_percent = b_text = None
    if budget > 0 and c is not None and c.month_to_date is not None:
        b_percent = budget_percent(c.month_to_date.value, Decimal(budget)) or 0
        b_text = text("cost_budget", limit=cost_compact(CostAmount(Decimal(budget))), percent=b_percent)
    parts = []
    if c is not None and c.month_to_date is not None:
        parts.append(text("cost_today") + " " + (cost_text(c.today) if c.today else text("cost_not_yet_tallied")))
        if c.month_to_date.value > 0:
            for item in c.top[:2]:
                share = budget_percent(item.amount.value, c.month_to_date.value) or 0
                parts.append(f"{item_display(item.name)} {share}%")
    as_of = text("cost_as_of", time=time_text(c.data_refreshed_at, now, zone, language)) if c and c.data_refreshed_at else None
    return AdminCostView(status, text(_STATUS_KEYS[status]), text("cost_this_month"), month_value, b_percent, b_text,
                         " · ".join(parts) if parts else None, as_of, state.failure.message if state.failure else None)


@dataclass(frozen=True)
class MenuBarCost:
    text: str
    is_warning: bool


def menu_bar_entries(limits_shown: dict[Provider, bool], states: dict[Provider, CostState], budgets: dict[Provider, int]) -> dict[Provider, MenuBarCost]:
    """Only a provider whose limits are off and whose API cost is on with a key
    puts its month's spend in the panel."""
    out = {}
    for provider in PROVIDERS:
        state = states.get(provider, CostState())
        api_shown = shows(False, state.enabled, state.key_suffix is not None)
        if limits_shown.get(provider, True) or not api_shown:
            continue
        month = state.cost.month_to_date if state.cost else None
        percent = (budget_percent(month.value, Decimal(budgets.get(provider, 0))) or 0) if month else 0
        out[provider] = MenuBarCost("--" if month is None else cost_compact(month), percent >= BUDGET_THRESHOLDS[0])
    return out


# --- budget alerts -----------------------------------------------------------------------------------------

def budget_alert_id(provider: Provider, threshold: int, now: datetime, zone: tzinfo) -> str:
    return f"{provider.value}-cost-{threshold}-{month_key(now, zone)}"


def budget_alerts(states: dict[Provider, CostState], budgets: dict[Provider, int], sent: set[str], now: datetime, zone: tzinfo, text) -> list[AlertCandidate]:
    """The highest threshold reached, once a month. A jump past both says 100%
    and counts 80% as said."""
    out = []
    for provider in PROVIDERS:
        state = states.get(provider, CostState())
        budget = budgets.get(provider, 0)
        month = state.cost.month_to_date if state.cost else None
        if budget <= 0 or not shows(False, state.enabled, state.key_suffix is not None) or month is None:
            continue
        ids = [budget_alert_id(provider, t, now, zone) for t in reached_thresholds(month.value, Decimal(budget))]
        if not ids or ids[-1] in sent:
            continue
        sent.update(ids)
        percent = budget_percent(month.value, Decimal(budget)) or 0
        out.append(AlertCandidate(ids[-1], text("cost_budget_alert_title"), text(
            "cost_budget_alert_body", organization=organization(provider), amount=cost_text(month),
            budget=cost_compact(CostAmount(Decimal(budget))), percent=percent)))
    return out
