"""Mirrors UsageHistoryStore.cs (store, policy, formatter) and Sparkline.cs."""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths
from .cache import _usage_from_dict, _usage_to_dict
from .models import ForecastWindow, Provider, ProviderUsage, UsageSnapshot, UsageSource
from .parser import parse_iso

MERGE_WINDOW = timedelta(minutes=1)


@dataclass(frozen=True)
class HistoryEntry:
    recorded_at: datetime
    snapshot: UsageSnapshot


def history_path() -> Path:
    return paths.app_data_directory() / "usage-history.json"


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or history_path()

    def load(self) -> list[HistoryEntry]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = []
            for item in raw:
                recorded = parse_iso(item["RecordedAt"])
                snap = item["Snapshot"]
                updated = parse_iso(snap["UpdatedAt"])
                if recorded is None or updated is None:
                    continue
                entries.append(HistoryEntry(recorded, UsageSnapshot(
                    _usage_from_dict(snap["Claude"]), _usage_from_dict(snap["Codex"]), updated)))
            return sorted(entries, key=lambda e: e.recorded_at)
        except Exception:
            return []

    def append(self, snapshot: UsageSnapshot, retention_days: int, now: datetime) -> list[HistoryEntry]:
        """Returns the entries now on disk, so the caller need not read them back."""
        entries = self.load()
        entry = HistoryEntry(now, snapshot)
        # Polls a minute apart replace each other instead of piling up.
        if entries and now - entries[-1].recorded_at < MERGE_WINDOW:
            entries[-1] = entry
        else:
            entries.append(entry)
        cutoff = now - timedelta(days=max(1, retention_days))
        kept = [e for e in entries if e.recorded_at >= cutoff]
        self._save(kept)
        return kept

    def _save(self, entries: list[HistoryEntry]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps([{
                "RecordedAt": e.recorded_at.isoformat(),
                "Snapshot": {
                    "Claude": _usage_to_dict(e.snapshot.claude),
                    "Codex": _usage_to_dict(e.snapshot.codex),
                    "UpdatedAt": e.snapshot.updated_at.isoformat(),
                },
            } for e in entries]), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            pass

    def csv_string(self) -> str:
        return csv_string(self.load())


def measurements_only(snapshot: UsageSnapshot) -> UsageSnapshot:
    """History records measurements, not re-displays of one: a cached or relayed
    reading would flatten the sparkline and tell the forecast usage stopped."""
    def only(usage: ProviderUsage) -> ProviderUsage:
        # Money is not part of the record: every poll would copy the same balance.
        without_money = usage.with_(extra_usage=None, credit_balance=None, credit_limit_reached=None)
        if usage.source is UsageSource.API:
            return without_money
        return without_money.with_(remaining_5h=None, remaining_7d=None)

    return UsageSnapshot(only(snapshot.claude), only(snapshot.codex), snapshot.updated_at)


def remaining(usage: ProviderUsage, window: ForecastWindow) -> int | None:
    return usage.remaining_5h if window is ForecastWindow.FIVE_HOUR else usage.remaining_7d


def _delta(previous: int | None, latest: int | None) -> str:
    if previous is None or latest is None:
        return "--"
    delta = latest - previous
    return f"+{delta}%" if delta > 0 else f"{delta}%"


def _provider_trend(provider: Provider, entries: list[HistoryEntry], current: UsageSnapshot) -> str:
    now_usage = current.usage(provider)
    # Per-provider baseline: an entry with no reading for this provider is skipped.
    baseline = next((e.snapshot.usage(provider) for e in entries if e.snapshot.usage(provider).is_available), None)
    five = _delta(baseline.remaining_5h if baseline else None, now_usage.remaining_5h)
    seven = _delta(baseline.remaining_7d if baseline else None, now_usage.remaining_7d)
    return f"{now_usage.display_name} 5h {five} 7d {seven}"


def trend_summary(entries: list[HistoryEntry], current: UsageSnapshot, text, window: timedelta = timedelta(days=1)) -> str:
    cutoff = current.updated_at - window
    in_window = [e for e in entries if e.recorded_at >= cutoff]
    if not in_window:
        return text("not_enough_history")
    return " ".join([text("history_trend"), _provider_trend(Provider.CLAUDE, in_window, current), _provider_trend(Provider.CODEX, in_window, current)])


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat()


def csv_string(entries: list[HistoryEntry]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["recorded_at", "provider", "remaining_5h", "remaining_7d", "reset_5h", "reset_7d", "source", "plan", "error"])
    for entry in entries:
        for usage in (entry.snapshot.claude, entry.snapshot.codex):
            writer.writerow([
                _iso(entry.recorded_at), usage.provider.value,
                "" if usage.remaining_5h is None else usage.remaining_5h,
                "" if usage.remaining_7d is None else usage.remaining_7d,
                _iso(usage.reset_at_5h), _iso(usage.reset_at_7d),
                usage.source.value, usage.plan or "", usage.error or "",
            ])
    return out.getvalue()


# --- Sparkline ------------------------------------------------------------------

BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline_series(entries: list[HistoryEntry], provider: Provider, window: ForecastWindow, max_points: int = 20) -> list[int]:
    values = [v for v in (remaining(e.snapshot.usage(provider), window) for e in sorted(entries, key=lambda e: e.recorded_at)) if v is not None]
    if max_points <= 0 or len(values) <= max_points:
        return values
    result = []
    for bucket in range(max_points):
        start = bucket * len(values) // max_points
        end = max(start + 1, (bucket + 1) * len(values) // max_points)
        chunk = values[start:end]
        result.append(int(sum(chunk) / len(chunk)))
    return result


def sparkline_text(series: list[int]) -> str:
    if len(series) < 2:
        return ""
    return "".join(BLOCKS[min(len(BLOCKS) - 1, max(0, min(100, v)) * len(BLOCKS) // 100)] for v in series)
