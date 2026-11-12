"""Mirrors ProviderRateLimitState / ProviderRateLimitStore in UsageClient.cs.

A 429 cooldown is persisted so a relaunch during it does not re-fire a
still-limited request, and it binds only the credentials that earned it: signing
in again takes effect at once.
"""

from __future__ import annotations

import json
import os
import random
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .expiry import JITTER_FRACTION, cooldown
from .parser import parse_iso


def format_retry_after(retry_after: timedelta) -> str:
    seconds = max(0, -(-int(retry_after.total_seconds() * 1000) // 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes = -(-seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    return f"{hours}h" if rest == 0 else f"{hours}h {rest}m"


class UsageHttpError(Exception):
    def __init__(self, status: int, service: str, retry_after: timedelta | None = None):
        self.status = status
        self.service = service
        self.retry_after = retry_after
        message = f"HTTP {status} from {service}"
        if status == 429 and retry_after is not None:
            message += f"; retrying after {format_retry_after(retry_after)}"
        super().__init__(message)


@dataclass(frozen=True)
class RateLimitRecord:
    retry_allowed_at: datetime
    failure_count: int
    fingerprint: str | None


class RateLimitStore:
    """Same field names as the Windows record."""

    def __init__(self, path: Path):
        self.path = path

    def load(self, now: datetime) -> RateLimitRecord | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            retry = parse_iso(raw.get("RetryAllowedAt"))
            if retry is None or retry <= now:
                return None
            count = raw.get("FailureCount", 0)
            count = count if isinstance(count, int) and not isinstance(count, bool) else 0
            fingerprint = raw.get("Fingerprint")
            return RateLimitRecord(retry, max(0, count), fingerprint if isinstance(fingerprint, str) else None)
        except Exception:
            return None

    def save(self, record: RateLimitRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps({
                "RetryAllowedAt": record.retry_allowed_at.isoformat(),
                "FailureCount": record.failure_count,
                "Fingerprint": record.fingerprint,
            }), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


class RateLimitState:
    def __init__(self, store: RateLimitStore, rng: random.Random | None = None):
        self._store = store
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._loaded = False
        self._retry_allowed_at: datetime | None = None
        self._failure_count = 0
        self._fingerprint: str | None = None

    def _ensure_loaded(self, now: datetime) -> None:
        if self._loaded:
            return
        self._loaded = True
        record = self._store.load(now)
        if record:
            self._retry_allowed_at = record.retry_allowed_at
            self._failure_count = record.failure_count
            self._fingerprint = record.fingerprint

    def _reset(self) -> None:
        self._failure_count = 0
        self._retry_allowed_at = None
        self._fingerprint = None
        self._store.clear()

    def current_error(self, service: str, fingerprint: str, now: datetime) -> UsageHttpError | None:
        with self._lock:
            self._ensure_loaded(now)
            if self._retry_allowed_at is None:
                return None
            # A record for other (or unnamed) credentials cannot be shown to apply
            # to the token in hand; give that token a fresh attempt.
            if self._fingerprint != fingerprint:
                self._reset()
                return None
            remaining = self._retry_allowed_at - now
            if remaining <= timedelta(0):
                self._retry_allowed_at = None
                self._store.clear()
                return None
            return UsageHttpError(429, service, remaining)

    def back_off(self, retry_after: timedelta, fingerprint: str, now: datetime) -> None:
        with self._lock:
            self._ensure_loaded(now)
            if self._fingerprint != fingerprint:
                self._failure_count = 0
                self._fingerprint = fingerprint
            wait = cooldown(retry_after, self._failure_count, self._rng.random() * JITTER_FRACTION)
            self._failure_count += 1
            self._retry_allowed_at = now + wait
            self._store.save(RateLimitRecord(self._retry_allowed_at, self._failure_count, self._fingerprint))

    def clear(self, now: datetime) -> None:
        with self._lock:
            self._ensure_loaded(now)
            self._reset()
