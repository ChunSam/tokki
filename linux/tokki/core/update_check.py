"""Mirrors UpdateCheck.cs. The app is installed by hand from GitHub Releases, so
the most it does is say a newer version exists and open the page that has it."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

LATEST_RELEASE_API = "https://api.github.com/repos/ChunSam/tokki/releases/latest"
RELEASES_PAGE = "https://github.com/ChunSam/tokki/releases/latest"

# Once a day is plenty, and far inside GitHub's 60 unauthenticated requests an hour.
INTERVAL = timedelta(hours=24)
# After a failed check, try again within the hour rather than waiting out the day.
RETRY_INTERVAL = timedelta(hours=1)


def _normalized(version: str) -> str:
    trimmed = version.strip()
    return trimmed[1:] if trimmed[:1] in ("v", "V") else trimmed


def _components(version: str) -> list[int] | None:
    parts = _normalized(version).split(".")
    if not 1 <= len(parts) <= 4:
        return None
    numbers = []
    for part in parts:
        if not part or not all("0" <= c <= "9" for c in part):
            return None
        numbers.append(int(part))
    return numbers


def latest_version(text: str) -> str | None:
    """The version `releases/latest` names, without its `v`. Drafts, prereleases
    and tags that are not plain dotted numbers are None."""
    try:
        root = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(root, dict) or root.get("draft") is True or root.get("prerelease") is True:
        return None
    tag = root.get("tag_name")
    if not isinstance(tag, str) or _components(tag) is None:
        return None
    return _normalized(tag)


def is_newer(candidate: str, current: str) -> bool:
    """Number by number, so 1.1.10 is newer than 1.1.9. A version either side
    cannot read is never newer: an unknown build should not nag."""
    nxt, installed = _components(candidate), _components(current)
    if nxt is None or installed is None:
        return False
    for index in range(max(len(nxt), len(installed))):
        lhs = nxt[index] if index < len(nxt) else 0
        rhs = installed[index] if index < len(installed) else 0
        if lhs != rhs:
            return lhs > rhs
    return False


def next_check(attempt_at: datetime, succeeded: bool) -> datetime:
    return attempt_at + (INTERVAL if succeeded else RETRY_INTERVAL)


def is_due(next_check_at: datetime | None, now: datetime) -> bool:
    return next_check_at is None or now >= next_check_at


def fetch_latest_version(timeout: float = 10) -> str | None:
    """None for any failure; a missed check is retried later, never shown."""
    request = urllib.request.Request(LATEST_RELEASE_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Tokki",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return latest_version(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError):
        return None
