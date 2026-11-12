"""Mirrors UsageClient.cs. Blocking; the app calls it from a worker thread."""

from __future__ import annotations

import email.utils
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import paths
from . import statusline
from .cli_refresh import ClaudeCliRefresher
from .expiry import is_expired, token_fingerprint
from .models import Provider, ProviderUsage, UsageSnapshot
from .parser import parse_claude_usage, parse_codex_usage, read_claude_credential, read_codex_auth
from .ratelimit import RateLimitState, RateLimitStore, UsageHttpError

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
DEFAULT_RATE_LIMIT_COOLDOWN = timedelta(minutes=5)
TIMEOUT_SECONDS = 10
USER_AGENT = "TokenTrackerLinux/1.0"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: str


Transport = Callable[[str, dict[str, str]], HttpResponse]


def urllib_transport(url: str, headers: dict[str, str]) -> HttpResponse:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return HttpResponse(response.status, {k.lower(): v for k, v in response.headers.items()}, response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code, {k.lower(): v for k, v in (error.headers or {}).items()}, "")


def retry_after(headers: dict[str, str], now: datetime) -> timedelta | None:
    value = (headers.get("retry-after") or "").strip()
    if not value:
        return None
    if value.isdigit():
        return timedelta(seconds=int(value))
    try:
        date = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return max(timedelta(0), date - now)


def expired_message(service: str, expired_at: datetime | None) -> str:
    prefix = f"Credentials expired for {service}"
    if expired_at is None:
        return prefix
    return f"{prefix} (expired {expired_at.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ})"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UsageClient:
    def __init__(
        self,
        transport: Transport = urllib_transport,
        home: Path | None = None,
        claude_rate_limit_path: Path | None = None,
        codex_rate_limit_path: Path | None = None,
        statusline_path: Path | None = None,
        cli_refresher: ClaudeCliRefresher | None = None,
        clock: Callable[[], datetime] = _now,
    ):
        self._transport = transport
        self._home = home
        self._clock = clock
        self._claude_limit = RateLimitState(RateLimitStore(claude_rate_limit_path or paths.claude_rate_limit_state_path()))
        self._codex_limit = RateLimitState(RateLimitStore(codex_rate_limit_path or paths.codex_rate_limit_state_path()))
        self._statusline = statusline.StatusLineStore(statusline_path)
        self.cli_refresher = cli_refresher

    def refresh(self, claude: bool = True, codex: bool = True, renew_claude: bool = False) -> UsageSnapshot:
        now = self._clock()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claude_future = pool.submit(self.fetch_claude, renew_claude) if claude else None
            codex_future = pool.submit(self.fetch_codex) if codex else None
            claude_usage = claude_future.result() if claude_future else ProviderUsage.unavailable(Provider.CLAUDE, "Disabled", now)
            codex_usage = codex_future.result() if codex_future else ProviderUsage.unavailable(Provider.CODEX, "Disabled", now)
        return UsageSnapshot(claude_usage, codex_usage, self._clock())

    def _get(self, url: str, headers: dict[str, str], service: str) -> str:
        response = self._transport(url, headers)
        if not 200 <= response.status < 300:
            raise UsageHttpError(response.status, service, retry_after(response.headers, self._clock()))
        return response.body

    def fetch_codex(self) -> ProviderUsage:
        now = self._clock()
        try:
            auth = read_codex_auth(self._home)
        except Exception as error:
            return ProviderUsage.unavailable(Provider.CODEX, str(error) or type(error).__name__, now)

        # `codex login` refreshes this token; an expired one only earns a bare 401.
        if is_expired(auth.expires_at, now):
            self._codex_limit.clear(now)
            return ProviderUsage.unavailable(Provider.CODEX, expired_message("Codex API", auth.expires_at), now)

        fingerprint = token_fingerprint(auth.access_token)
        limited = self._codex_limit.current_error("Codex API", fingerprint, now)
        if limited:
            return ProviderUsage.unavailable(Provider.CODEX, str(limited), now)

        try:
            body = self._get(CODEX_USAGE_URL, {
                "Accept": "application/json",
                "Authorization": f"Bearer {auth.access_token}",
                "ChatGPT-Account-Id": auth.account_id,
                "User-Agent": USER_AGENT,
            }, "Codex API")
            usage = parse_codex_usage(body, self._clock())
            self._codex_limit.clear(now)
            return usage
        except UsageHttpError as error:
            if error.status == 429:
                self._codex_limit.back_off(error.retry_after or DEFAULT_RATE_LIMIT_COOLDOWN, fingerprint, self._clock())
                limited = self._codex_limit.current_error("Codex API", fingerprint, self._clock())
                return ProviderUsage.unavailable(Provider.CODEX, str(limited or error), now)
            return ProviderUsage.unavailable(Provider.CODEX, str(error), now)
        except TimeoutError:
            return ProviderUsage.unavailable(Provider.CODEX, "Timed out contacting Codex API", now)
        except Exception as error:
            return ProviderUsage.unavailable(Provider.CODEX, _describe(error), now)

    def fetch_claude(self, renew_expired_token: bool = False) -> ProviderUsage:
        """The API first, because only that is a measurement taken now; the
        status-line bridge when the API has nothing."""
        api = self._fetch_claude_api(renew_expired_token)
        if api.is_available:
            return api
        return statusline.usage(self._statusline.read(self._clock()), api.error, self._clock()) or api

    def _fetch_claude_api(self, renew: bool) -> ProviderUsage:
        now = self._clock()
        try:
            credential = read_claude_credential(self._home)
        except Exception as error:
            return ProviderUsage.unavailable(Provider.CLAUDE, str(error) or type(error).__name__, now)

        if renew and is_expired(credential.expires_at, now) and self.cli_refresher and self.cli_refresher.refresh_if_due(now):
            try:
                credential = read_claude_credential(self._home)
            except Exception as error:
                return ProviderUsage.unavailable(Provider.CLAUDE, str(error) or type(error).__name__, now)
            now = self._clock()

        # Spending a request on a lapsed token returns a 401, or a 429 the
        # backoff would mistake for a quota cooldown.
        if is_expired(credential.expires_at, now):
            self._claude_limit.clear(now)
            return ProviderUsage.unavailable(Provider.CLAUDE, expired_message("Claude API", credential.expires_at), now)

        fingerprint = token_fingerprint(credential.access_token)
        limited = self._claude_limit.current_error("Claude API", fingerprint, now)
        if limited:
            return ProviderUsage.unavailable(Provider.CLAUDE, str(limited), now)

        try:
            body = self._get(CLAUDE_USAGE_URL, {
                "Accept": "application/json",
                "Authorization": f"Bearer {credential.access_token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": USER_AGENT,
            }, "Claude API")
            usage = parse_claude_usage(body, self._clock(), credential.plan)
            self._claude_limit.clear(now)
            return usage
        except UsageHttpError as error:
            if error.status == 429:
                self._claude_limit.back_off(error.retry_after or DEFAULT_RATE_LIMIT_COOLDOWN, fingerprint, self._clock())
                limited = self._claude_limit.current_error("Claude API", fingerprint, self._clock())
                return ProviderUsage.unavailable(Provider.CLAUDE, str(limited or error), now)
            return ProviderUsage.unavailable(Provider.CLAUDE, str(error), now)
        except TimeoutError:
            return ProviderUsage.unavailable(Provider.CLAUDE, "Timed out contacting Claude API", now)
        except Exception as error:
            return ProviderUsage.unavailable(Provider.CLAUDE, _describe(error), now)


def _describe(error: Exception) -> str:
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, TimeoutError):
            return "Timed out"
        return f"Network error: {reason}"
    return str(error) or type(error).__name__
