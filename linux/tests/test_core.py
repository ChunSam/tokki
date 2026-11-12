"""Core tests for the Linux port. Same truth tables as the Swift and C# smoke
tests where a counterpart exists. Run: python3 -m unittest discover -s linux/tests"""

from __future__ import annotations

import base64
import json
import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tokki import autostart  # noqa: E402
from tokki.core import alerts, cache, cli_refresh, cost, display, expiry, forecast, history, issues, parser, pause, ratelimit, settings, statusline, update_check  # noqa: E402
from tokki.core.client import HttpResponse, UsageClient, retry_after  # noqa: E402
from tokki.core.models import (  # noqa: E402
    AppLanguage, DisplayMode, ForecastWindow, Provider, ProviderLabelStyle, ProviderUsage, UsageSnapshot, UsageSource,
)
from tokki.i18n import Localizer  # noqa: E402

NOW = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)


def usage(provider=Provider.CLAUDE, five=None, seven=None, reset5=None, reset7=None, source=UsageSource.API, updated=NOW, error=None):
    return ProviderUsage(provider, five, seven, reset5, reset7, source, error, None, updated)


def jwt(claims: dict) -> str:
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


class ExpiryTests(unittest.TestCase):
    def test_skew(self):
        self.assertFalse(expiry.is_expired(None, NOW))
        self.assertTrue(expiry.is_expired(NOW + timedelta(seconds=60), NOW))
        self.assertFalse(expiry.is_expired(NOW + timedelta(seconds=61), NOW))
        self.assertTrue(expiry.is_expired(NOW - timedelta(hours=1), NOW))

    def test_jwt(self):
        self.assertEqual(expiry.jwt_expiry(jwt({"exp": 1_800_000_000})), datetime.fromtimestamp(1_800_000_000, timezone.utc))
        self.assertIsNone(expiry.jwt_expiry(jwt({"sub": "x"})))
        self.assertIsNone(expiry.jwt_expiry("not-a-jwt"))
        self.assertIsNone(expiry.jwt_expiry(""))

    def test_fingerprint_is_order_independent_and_matches_fnv(self):
        self.assertEqual(expiry.token_fingerprint("a", "b"), expiry.token_fingerprint("b", "a"))
        self.assertNotEqual(expiry.token_fingerprint("a"), expiry.token_fingerprint("b"))
        # FNV-1a 64 of the empty string is the offset basis.
        self.assertEqual(expiry.token_fingerprint(""), "cbf29ce484222325")

    def test_backoff(self):
        self.assertEqual(expiry.cooldown(timedelta(0), 0, 0), timedelta(seconds=120))
        self.assertEqual(expiry.cooldown(timedelta(0), 1, 0), timedelta(seconds=240))
        self.assertEqual(expiry.cooldown(timedelta(0), 10, 0), timedelta(seconds=1800))
        # An explicit longer Retry-After is honored past the cap.
        self.assertEqual(expiry.cooldown(timedelta(seconds=3600), 3, 0), timedelta(seconds=3600))
        self.assertEqual(expiry.cooldown(timedelta(0), 0, 5.0), timedelta(seconds=144))

    def test_percent_rounds_away_from_zero(self):
        self.assertEqual(expiry.remaining_percent(57.5), 43)  # 42.5 -> 43
        self.assertEqual(expiry.remaining_percent(150), 0)
        self.assertEqual(expiry.remaining_percent(-5), 100)
        self.assertIsNone(expiry.remaining_percent(None))


class ParserTests(unittest.TestCase):
    def test_claude(self):
        body = json.dumps({
            "five_hour": {"utilization": 30.2, "resets_at": "2026-05-27T14:00:00Z"},
            "seven_day": {"utilization": "91", "resets_at": "2026-05-30T00:00:00+00:00"},
        })
        result = parser.parse_claude_usage(body, NOW, "max")
        self.assertEqual((result.remaining_5h, result.remaining_7d), (70, 9))
        self.assertEqual(result.reset_at_5h, datetime(2026, 5, 27, 14, tzinfo=timezone.utc))
        self.assertEqual(result.plan, "max")
        self.assertIs(result.source, UsageSource.API)

    def test_claude_without_windows_is_invalid(self):
        with self.assertRaises(ValueError):
            parser.parse_claude_usage(json.dumps({"five_hour": None}), NOW)

    def test_codex_maps_windows_by_length(self):
        body = json.dumps({"plan_type": "plus", "rate_limit": {
            "primary_window": {"used_percent": 40, "reset_at": 1_780_000_000, "limit_window_seconds": 604800},
        }})
        result = parser.parse_codex_usage(body, NOW)
        self.assertIsNone(result.remaining_5h)
        self.assertEqual(result.remaining_7d, 60)
        self.assertEqual(result.plan, "plus")

    def test_codex_positional_fallback(self):
        body = json.dumps({"rate_limit": {
            "primary_window": {"used_percent": 10},
            "secondary_window": {"used_percent": 20},
        }})
        result = parser.parse_codex_usage(body, NOW)
        self.assertEqual((result.remaining_5h, result.remaining_7d), (90, 80))

    def test_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").write_text(json.dumps(
                {"claudeAiOauth": {"accessToken": "tok", "expiresAt": 1_800_000_000_000, "subscriptionType": "pro"}}))
            credential = parser.read_claude_credential(home)
            self.assertEqual(credential.plan, "pro")
            self.assertEqual(credential.expires_at, datetime.fromtimestamp(1_800_000_000, timezone.utc))
            (home / ".codex").mkdir()
            (home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {"access_token": "", "account_id": "a"}}))
            with self.assertRaises(ValueError):
                parser.read_codex_auth(home)


class DisplayTests(unittest.TestCase):
    def test_seven_day_wins_at_ten_percent(self):
        u = usage(five=50, seven=10, reset5=NOW + timedelta(hours=1), reset7=NOW + timedelta(days=2))
        self.assertEqual(display.display_percent(u), 10)
        self.assertEqual(display.display_reset_at(u), NOW + timedelta(days=2))
        self.assertTrue(display.is_seven_day_warning(u))
        u = usage(five=50, seven=11)
        self.assertEqual(display.display_percent(u), 50)
        self.assertFalse(display.is_seven_day_warning(u))

    def test_codex_without_5h_shows_7d_without_warning(self):
        u = usage(Provider.CODEX, seven=60)
        self.assertEqual(display.display_percent(u), 60)
        self.assertFalse(display.is_seven_day_warning(u))

    def test_status_title(self):
        snap = UsageSnapshot(usage(five=70), usage(Provider.CODEX, seven=40), NOW)
        self.assertEqual(display.status_title(snap, DisplayMode.LOWEST_REMAINING), "AI 40%")
        self.assertEqual(display.status_title(snap, DisplayMode.BOTH), "Cdx 40% · Cl 70%")
        self.assertEqual(display.status_title(snap, DisplayMode.CLAUDE_ONLY, ProviderLabelStyle.ICON), "Claude 70%")
        self.assertEqual(display.status_title(None, DisplayMode.BOTH), "AI --")

    def test_format_reset(self):
        self.assertEqual(display.format_reset(NOW + timedelta(minutes=42, seconds=30), NOW, "now"), "42m")
        self.assertEqual(display.format_reset(NOW + timedelta(hours=3, minutes=5), NOW, "now"), "3h 5m")
        self.assertEqual(display.format_reset(NOW + timedelta(days=2, hours=4), NOW, "now", compact=True), "2d4h")
        self.assertEqual(display.format_reset(NOW - timedelta(seconds=1), NOW, "now"), "now")

    def test_countdown_only_for_live_readings(self):
        reset = NOW + timedelta(hours=1, minutes=2)
        self.assertEqual(display.countdown_suffix(usage(five=50, reset5=reset), True, NOW, "now"), " 1h2m")
        self.assertEqual(display.countdown_suffix(usage(five=50, reset5=reset, source=UsageSource.STATUS_LINE), True, NOW, "now"), " 1h2m")
        self.assertEqual(display.countdown_suffix(usage(five=50, reset5=reset, source=UsageSource.STALE_CACHE), True, NOW, "now"), "")
        self.assertEqual(display.countdown_suffix(usage(five=50, reset5=reset), False, NOW, "now"), "")

    def test_tray_label_countdown_follows_shown_window(self):
        claude = usage(five=80, seven=5, reset5=NOW + timedelta(hours=2), reset7=NOW + timedelta(days=3))
        codex = usage(Provider.CODEX, seven=50, reset7=NOW + timedelta(days=1, hours=1))
        snap = UsageSnapshot(claude, codex, NOW)
        label = display.tray_label(snap, DisplayMode.LOWEST_REMAINING, ProviderLabelStyle.ABBREVIATION, True, NOW, "now")
        self.assertEqual(label, "AI 5% 3d0h")
        self.assertTrue(display.tray_shows_warning(snap, DisplayMode.LOWEST_REMAINING))

    def test_window_reset_texts(self):
        text = Localizer(AppLanguage.ENGLISH).text
        self.assertEqual(display.format_window_reset(40, None, NOW, text), "starts on first use")
        self.assertEqual(display.format_window_reset(None, None, NOW, text, provider_reported=True), "not reported")
        self.assertEqual(display.format_window_reset(None, None, NOW, text), "--")


class CacheTests(unittest.TestCase):
    def test_policy_reuses_recent_success_only(self):
        stale = UsageSnapshot(usage(five=60, updated=NOW - timedelta(hours=2)), usage(Provider.CODEX, seven=30, updated=NOW - timedelta(hours=13)), NOW)
        current = UsageSnapshot(
            ProviderUsage.unavailable(Provider.CLAUDE, "boom", NOW),
            ProviderUsage.unavailable(Provider.CODEX, "boom", NOW), NOW)
        merged = cache.apply_cache_policy(current, stale, NOW)
        self.assertIs(merged.claude.source, UsageSource.STALE_CACHE)
        self.assertEqual(merged.claude.remaining_5h, 60)
        self.assertEqual(merged.claude.error, "boom")
        self.assertIs(merged.codex.source, UsageSource.UNAVAILABLE)  # older than 12h

    def test_disabled_provider_is_not_filled(self):
        stale = UsageSnapshot(usage(five=60), usage(Provider.CODEX, seven=30), NOW)
        current = UsageSnapshot(ProviderUsage.unavailable(Provider.CLAUDE, "Disabled", NOW), usage(Provider.CODEX, seven=20), NOW)
        merged = cache.apply_cache_policy(current, stale, NOW, claude_enabled=False)
        self.assertFalse(merged.claude.is_available)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.CacheStore(Path(tmp) / "c.json")
            snap = UsageSnapshot(usage(five=60, reset5=NOW + timedelta(hours=1)), usage(Provider.CODEX, seven=30), NOW)
            store.save(snap)
            self.assertEqual(store.load(timedelta(hours=12), NOW + timedelta(hours=1)), snap)
            self.assertIsNone(store.load(timedelta(hours=12), NOW + timedelta(hours=13)))


class StatusLineTests(unittest.TestCase):
    def record(self, measured, **limits):
        return json.dumps({"version": 1, "measured_at": measured.timestamp(), "written_at": NOW.timestamp(), "rate_limits": limits})

    def test_fresh_reading(self):
        text = self.record(NOW - timedelta(minutes=5), five_hour={"used_percentage": 25, "resets_at": (NOW + timedelta(hours=2)).timestamp()})
        result = statusline.usage(statusline.parse(text, NOW), "expired", NOW)
        self.assertEqual(result.remaining_5h, 75)
        self.assertIs(result.source, UsageSource.STATUS_LINE)
        self.assertEqual(result.error, "expired")
        self.assertEqual(result.updated_at, NOW - timedelta(minutes=5))

    def test_age_uses_measured_not_written(self):
        text = self.record(NOW - timedelta(minutes=31), five_hour={"used_percentage": 25, "resets_at": (NOW + timedelta(hours=2)).timestamp()})
        self.assertIsNone(statusline.usage(statusline.parse(text, NOW), None, NOW))

    def test_rejects_passed_reset_future_measurement_and_wrong_version(self):
        passed = self.record(NOW, five_hour={"used_percentage": 25, "resets_at": (NOW - timedelta(seconds=1)).timestamp()})
        self.assertIsNone(statusline.parse(passed, NOW))
        future = self.record(NOW + timedelta(minutes=6), five_hour={"used_percentage": 25, "resets_at": (NOW + timedelta(hours=2)).timestamp()})
        self.assertIsNone(statusline.parse(future, NOW))
        self.assertIsNone(statusline.parse(json.dumps({"version": 2, "measured_at": NOW.timestamp()}), NOW))


class RateLimitTests(unittest.TestCase):
    def test_cooldown_binds_fingerprint_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rl.json"
            state = ratelimit.RateLimitState(ratelimit.RateLimitStore(path), random.Random(0))
            state.back_off(timedelta(seconds=30), "fp1", NOW)
            self.assertIsNotNone(state.current_error("Claude API", "fp1", NOW + timedelta(seconds=60)))
            # A relaunch reads it back.
            again = ratelimit.RateLimitState(ratelimit.RateLimitStore(path))
            self.assertIsNotNone(again.current_error("Claude API", "fp1", NOW + timedelta(seconds=60)))
            # New credentials get a fresh attempt and drop the record.
            self.assertIsNone(again.current_error("Claude API", "fp2", NOW + timedelta(seconds=60)))
            self.assertFalse(path.exists())

    def test_cooldown_expires(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ratelimit.RateLimitState(ratelimit.RateLimitStore(Path(tmp) / "rl.json"), random.Random(0))
            state.back_off(timedelta(0), "fp", NOW)
            self.assertIsNone(state.current_error("Claude API", "fp", NOW + timedelta(seconds=145)))

    def test_retry_after_formats(self):
        self.assertEqual(ratelimit.format_retry_after(timedelta(seconds=30)), "30s")
        self.assertEqual(ratelimit.format_retry_after(timedelta(seconds=61)), "2m")
        self.assertEqual(ratelimit.format_retry_after(timedelta(hours=2)), "2h")
        self.assertEqual(retry_after({"retry-after": "120"}, NOW), timedelta(seconds=120))
        self.assertEqual(retry_after({"retry-after": "Wed, 27 May 2026 12:05:00 GMT"}, NOW), timedelta(minutes=5))
        self.assertIsNone(retry_after({}, NOW))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def write_claude(self, expires_at: datetime):
        (self.home / ".claude" / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "tok", "expiresAt": int(expires_at.timestamp() * 1000)}}))

    def client(self, responses):
        def transport(url, headers):
            self.calls.append((url, headers))
            return responses.pop(0)
        return UsageClient(
            transport=transport, home=self.home,
            claude_rate_limit_path=self.home / "c.json", codex_rate_limit_path=self.home / "x.json",
            statusline_path=self.home / "sl.json", clock=lambda: NOW)

    def test_expired_token_makes_no_request(self):
        self.write_claude(NOW - timedelta(minutes=1))
        result = self.client([]).fetch_claude()
        self.assertEqual(self.calls, [])
        self.assertTrue(result.error.startswith("Credentials expired for Claude API"))

    def test_expired_token_falls_back_to_status_line(self):
        self.write_claude(NOW - timedelta(minutes=1))
        (self.home / "sl.json").write_text(json.dumps({"version": 1, "measured_at": NOW.timestamp(), "rate_limits": {
            "seven_day": {"used_percentage": 50, "resets_at": (NOW + timedelta(days=1)).timestamp()}}}))
        result = self.client([]).fetch_claude()
        self.assertIs(result.source, UsageSource.STATUS_LINE)
        self.assertEqual(result.remaining_7d, 50)

    def test_success_and_429_backoff(self):
        self.write_claude(NOW + timedelta(hours=4))
        ok = HttpResponse(200, {}, json.dumps({"five_hour": {"utilization": 10}}))
        limited = HttpResponse(429, {"retry-after": "60"}, "")
        client = self.client([ok, limited])
        self.assertEqual(client.fetch_claude().remaining_5h, 90)
        self.assertEqual(self.calls[0][1]["anthropic-beta"], "oauth-2025-04-20")
        self.assertIn("HTTP 429 from Claude API", client.fetch_claude().error)
        # The cooldown holds: no third request.
        self.assertIn("HTTP 429", client.fetch_claude().error)
        self.assertEqual(len(self.calls), 2)

    def test_codex_headers(self):
        (self.home / ".codex" / "auth.json").write_text(json.dumps({"tokens": {
            "access_token": jwt({"exp": int((NOW + timedelta(days=1)).timestamp())}), "account_id": "acct"}}))
        ok = HttpResponse(200, {}, json.dumps({"rate_limit": {"primary_window": {"used_percent": 5, "limit_window_seconds": 18000}}}))
        result = self.client([ok]).fetch_codex()
        self.assertEqual(result.remaining_5h, 95)
        self.assertEqual(self.calls[0][1]["ChatGPT-Account-Id"], "acct")

    def test_disabled_provider_is_not_fetched(self):
        snap = self.client([]).refresh(claude=False, codex=False)
        self.assertEqual(self.calls, [])
        self.assertFalse(snap.claude.is_available)


class SettingsTests(unittest.TestCase):
    def test_round_trip_keeps_unknown_keys_and_floors_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"DisplayMode": "Both", "RefreshIntervalSeconds": 30, "ClaudeCostEnabled": True, "Language": "Klingon"}))
            store = settings.SettingsStore(path)
            loaded = store.load()
            self.assertIs(loaded.DisplayMode, DisplayMode.BOTH)
            self.assertEqual(loaded.RefreshIntervalSeconds, 60)
            self.assertIs(loaded.Language, AppLanguage.SYSTEM)
            store.save(loaded)
            raw = json.loads(path.read_text())
            self.assertTrue(raw["ClaudeCostEnabled"])
            self.assertEqual(raw["DisplayMode"], "Both")

    def test_corrupt_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{nope")
            self.assertEqual(settings.SettingsStore(path).load().RefreshIntervalSeconds, 300)


class AlertTests(unittest.TestCase):
    """Same truth tables as the alert section of windows/TokenTracker.Windows.Tests."""

    EN = staticmethod(Localizer(AppLanguage.ENGLISH).text)
    KO = staticmethod(Localizer(AppLanguage.KOREAN).text)

    def ids(self, snapshot, settings, now=NOW):
        return [c.id for c in alerts.candidates(snapshot, settings, now, self.EN)]

    def test_low_alerts_fire_once_until_cleared(self):
        snap = UsageSnapshot(usage(five=15, seven=50), usage(Provider.CODEX, seven=8, reset7=NOW + timedelta(days=1)), NOW)
        active = alerts.candidates(snap, alerts.AlertSettings(True, 20, 10, 0), NOW, self.EN)
        self.assertEqual([c.id for c in active], ["claude-5h-low", "codex-7d-low"])
        self.assertEqual(active[0].title, "5h usage is low")
        self.assertEqual(active[0].body, "Claude 5h 15% <= 20%")
        fresh, delivered = alerts.to_deliver(active, set())
        self.assertEqual(len(fresh), 2)
        fresh, delivered = alerts.to_deliver(active, delivered)
        self.assertEqual(fresh, [])
        fresh, delivered = alerts.to_deliver([], delivered)
        self.assertEqual(delivered, set())

    def test_low_alert_skips_reset_window_and_disabled(self):
        snap = UsageSnapshot(usage(five=5, reset5=NOW - timedelta(minutes=1)), usage(Provider.CODEX), NOW)
        self.assertEqual(self.ids(snap, alerts.AlertSettings(True, 20, 10, 0)), [])
        snap = UsageSnapshot(usage(five=5), usage(Provider.CODEX), NOW)
        self.assertEqual(self.ids(snap, alerts.AlertSettings(False, 20, 10, 10)), [])

    def reset_bodies(self, unused5h, source=UsageSource.API, threshold=20, text=None):
        snap = UsageSnapshot(
            usage(five=unused5h, seven=90, reset5=NOW + timedelta(minutes=5), reset7=NOW + timedelta(days=3), source=source),
            ProviderUsage.unavailable(Provider.CODEX, "Disabled", NOW), NOW)
        found = alerts.candidates(snap, alerts.AlertSettings(True, threshold, 10, 10), NOW, text or self.EN)
        return " | ".join(c.body for c in found if "-reset-" in c.id)

    def test_reset_warnings(self):
        self.assertEqual(self.reset_bodies(60), "Claude 5h resets in 5m with 60% unused")
        self.assertEqual(self.reset_bodies(60, text=self.KO), "Claude 5h 5m 후 리셋 — 60% 남음")
        self.assertEqual(self.reset_bodies(20), "")
        self.assertEqual(self.reset_bodies(21), "Claude 5h resets in 5m with 21% unused")
        self.assertEqual(self.reset_bodies(1, threshold=0), "Claude 5h resets in 5m with 1% unused")
        self.assertEqual(self.reset_bodies(0, threshold=0), "")
        self.assertEqual(self.reset_bodies(None), "")
        self.assertEqual(self.reset_bodies(60, UsageSource.STALE_CACHE), "")
        self.assertEqual(self.reset_bodies(60, UsageSource.STATUS_LINE), "Claude 5h resets in 5m with 60% unused")

    def stalled_ids(self, age, source, stalled_after):
        snap = UsageSnapshot(
            usage(five=55, seven=70, source=source, updated=NOW - age),
            usage(Provider.CODEX, five=80, seven=90), NOW)
        return self.ids(snap, alerts.AlertSettings(True, 0, 0, 0, stalled_after))

    def test_stalled(self):
        self.assertEqual(self.stalled_ids(timedelta(minutes=90), UsageSource.STALE_CACHE, timedelta(minutes=30)), ["claude-stalled"])
        self.assertEqual(self.stalled_ids(timedelta(minutes=10), UsageSource.STALE_CACHE, timedelta(minutes=30)), [])
        self.assertEqual(self.stalled_ids(timedelta(minutes=90), UsageSource.API, timedelta(minutes=30)), [])
        self.assertEqual(self.stalled_ids(timedelta(minutes=90), UsageSource.STALE_CACHE, timedelta(0)), [])
        self.assertEqual(alerts.stalled_threshold(300), timedelta(minutes=30))
        self.assertEqual(alerts.stalled_threshold(1800), timedelta(minutes=90))
        snap = UsageSnapshot(usage(five=55, source=UsageSource.STALE_CACHE, updated=NOW - timedelta(minutes=95)), usage(Provider.CODEX), NOW)
        body = alerts.candidates(snap, alerts.AlertSettings(True, 0, 0, 0, timedelta(minutes=30)), NOW, self.KO)[0].body
        self.assertEqual(body, "Claude 마지막 측정 1h35m 전")

    # Freed up: a window that had run low, measured again after its reset.
    FREED_NOW = datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc)
    FREED = alerts.AlertSettings(True, 20, 10, 0)

    def reading(self, five, reset5, at, source=UsageSource.API):
        return ProviderUsage(Provider.CLAUDE, five, 80, reset5, None, source, None, None, at)

    def claude_only(self, claude):
        return UsageSnapshot(claude, ProviderUsage.unavailable(Provider.CODEX, "Disabled", claude.updated_at), claude.updated_at)

    def freed_ids(self, before, after, settings=None):
        previous = {Provider.CLAUDE: before} if before else {}
        return [c.id for c in alerts.freed_up_candidates(previous, self.claude_only(after), settings or self.FREED, self.EN)]

    def test_freed_up(self):
        now = self.FREED_NOW
        passed = now - timedelta(seconds=60)
        low = self.reading(8, passed, now - timedelta(seconds=600))
        refilled = self.reading(100, None, now)
        self.assertEqual(self.freed_ids(low, refilled), [f"claude-5h-freed-{int(passed.timestamp())}"])
        self.assertEqual(self.freed_ids(self.reading(50, passed, now - timedelta(seconds=600)), refilled), [])
        self.assertEqual(self.freed_ids(
            self.reading(8, now + timedelta(seconds=600), now - timedelta(seconds=600)),
            self.reading(30, now + timedelta(seconds=600), now)), [])
        self.assertEqual(self.freed_ids(low, self.reading(100, None, now, UsageSource.STALE_CACHE)), [])
        self.assertEqual(self.freed_ids(low, self.reading(15, None, now)), [])
        self.assertEqual(self.freed_ids(None, refilled), [])
        self.assertEqual(self.freed_ids(low, refilled, alerts.AlertSettings(True, 0, 10, 0)), [])
        body = alerts.freed_up_candidates({Provider.CLAUDE: low}, self.claude_only(refilled), self.FREED, self.KO)[0].body
        self.assertEqual(body, "Claude 5h 리셋됨 — 잔량 100%")

    def test_last_readings(self):
        now = self.FREED_NOW
        passed = now - timedelta(seconds=60)
        low = self.reading(8, passed, now - timedelta(seconds=600))
        refilled = self.reading(100, None, now)
        cached = self.reading(8, passed, now, UsageSource.STALE_CACHE)
        seeded = alerts.last_readings({}, self.claude_only(cached))
        self.assertEqual(seeded[Provider.CLAUDE].remaining_5h, 8)
        self.assertNotIn(Provider.CODEX, seeded)
        self.assertEqual(alerts.last_readings({Provider.CLAUDE: low}, self.claude_only(cached))[Provider.CLAUDE], low)
        self.assertEqual(alerts.last_readings({Provider.CLAUDE: low}, self.claude_only(refilled))[Provider.CLAUDE], refilled)


class CliRefreshTests(unittest.TestCase):
    def test_schedule(self):
        self.assertTrue(cli_refresh.is_due(None, NOW))
        done = cli_refresh.Attempt(NOW, cli_refresh.Outcome.REFRESHED)
        self.assertFalse(cli_refresh.is_due(done, NOW + timedelta(minutes=4)))
        self.assertTrue(cli_refresh.is_due(done, NOW + timedelta(minutes=5)))
        failed = cli_refresh.Attempt(NOW, cli_refresh.Outcome.UNCHANGED)
        self.assertFalse(cli_refresh.is_due(failed, NOW + timedelta(minutes=29)))

    def test_environment_strips_credentials(self):
        env = cli_refresh.launch_environment({"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "x", "CLAUDE_CONFIG_DIR": "/y", "HOME": "/h"}, "/opt/claude")
        self.assertEqual(env["PATH"], "/opt/claude" + os.pathsep + "/usr/bin")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")
        self.assertEqual(env["HOME"], "/h")

    def test_candidates(self):
        found = cli_refresh.candidate_paths(Path("/h"), "/a:/b:/a")
        self.assertEqual(found, [Path("/h/.local/bin/claude"), Path("/a/claude"), Path("/b/claude")])

    def test_missing_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("PATH")
            os.environ["PATH"] = tmp
            try:
                refresher = cli_refresh.ClaudeCliRefresher(home=Path(tmp), working_directory=Path(tmp) / "w")
                self.assertFalse(refresher.refresh_if_due(NOW))
                self.assertIs(refresher.last_attempt.outcome, cli_refresh.Outcome.CLI_NOT_FOUND)
                # Not due again for 30 minutes.
                self.assertFalse(refresher.refresh_if_due(NOW + timedelta(minutes=1)))
            finally:
                os.environ["PATH"] = old or ""


class PauseTests(unittest.TestCase):
    """PauseController truth table."""

    def test_states(self):
        self.assertFalse(pause.is_paused(None, NOW))
        self.assertTrue(pause.is_paused(NOW + timedelta(minutes=1), NOW))
        self.assertFalse(pause.is_paused(NOW, NOW))
        self.assertEqual(pause.remaining(NOW + timedelta(hours=1), NOW), timedelta(hours=1))
        self.assertEqual(pause.remaining(NOW - timedelta(hours=1), NOW), timedelta(0))
        self.assertFalse(pause.is_indefinite(NOW + timedelta(hours=3), NOW))
        self.assertTrue(pause.is_indefinite(pause.pause_until(None, NOW), NOW))
        self.assertEqual(pause.pause_until(timedelta(hours=3), NOW), NOW + timedelta(hours=3))

    def test_duration_text(self):
        self.assertEqual(pause.duration_text(30), "<1m")
        self.assertEqual(pause.duration_text(59 * 60), "59m")
        self.assertEqual(pause.duration_text(2 * 3600 + 5 * 60), "2h 5m")
        self.assertEqual(pause.duration_text(50 * 3600), "2d 2h")

    def test_pause_survives_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = settings.SettingsStore(Path(tmp) / "s.json")
            loaded = store.load()
            self.assertIsNone(settings.paused_until(loaded))
            loaded.PollPausedUntil = pause.pause_until(None, NOW).isoformat()
            store.save(loaded)
            again = store.load()
            self.assertTrue(pause.is_paused(settings.paused_until(again), NOW))
            self.assertTrue(pause.is_indefinite(settings.paused_until(again), NOW))
            (Path(tmp) / "s.json").write_text(json.dumps({"PollPausedUntil": "garbage"}))
            self.assertIsNone(settings.paused_until(store.load()))
            (Path(tmp) / "s.json").write_text(json.dumps({"PollPausedUntil": 5}))
            self.assertIsNone(store.load().PollPausedUntil)


class UpdateCheckTests(unittest.TestCase):
    """UpdateCheck truth table from the Windows smoke tests."""

    def test_latest_version(self):
        self.assertEqual(update_check.latest_version('{"tag_name":"v1.1.17","draft":false,"prerelease":false}'), "1.1.17")
        self.assertIsNone(update_check.latest_version('{"tag_name":"v1.2.0","prerelease":true}'))
        self.assertIsNone(update_check.latest_version('{"tag_name":"v1.2.0","draft":true}'))
        self.assertIsNone(update_check.latest_version('{"tag_name":"v1.2.0-beta"}'))
        self.assertIsNone(update_check.latest_version('{"message":"Not Found"}'))
        self.assertIsNone(update_check.latest_version("not json"))

    def test_is_newer(self):
        self.assertTrue(update_check.is_newer("1.1.10", "1.1.9"))
        self.assertFalse(update_check.is_newer("1.1.9", "1.1.10"))
        self.assertFalse(update_check.is_newer("1.2", "1.2.0"))
        self.assertTrue(update_check.is_newer("1.2.0.1", "1.2"))
        self.assertFalse(update_check.is_newer("1.2.0", "unknown"))
        self.assertFalse(update_check.is_newer("v1.3.1", "1.3.1"))

    def test_schedule(self):
        self.assertTrue(update_check.is_due(None, NOW))
        self.assertEqual(update_check.next_check(NOW, True), NOW + timedelta(hours=24))
        self.assertEqual(update_check.next_check(NOW, False), NOW + timedelta(hours=1))
        self.assertFalse(update_check.is_due(NOW + timedelta(hours=1), NOW))


class HistoryTests(unittest.TestCase):
    """UsageHistoryStore / policy / formatter truth tables from the Windows tests."""

    EN = staticmethod(Localizer(AppLanguage.ENGLISH).text)

    def test_measurements_only(self):
        cached = usage(five=63, seven=80, source=UsageSource.STALE_CACHE)
        recorded = history.measurements_only(UsageSnapshot(cached, usage(Provider.CODEX, five=91, seven=99), NOW))
        self.assertIsNone(recorded.claude.remaining_5h)
        self.assertIsNone(recorded.claude.remaining_7d)
        self.assertEqual(recorded.codex.remaining_5h, 91)
        self.assertIs(recorded.claude.source, UsageSource.STALE_CACHE)
        relayed = history.measurements_only(UsageSnapshot(usage(five=50, source=UsageSource.STATUS_LINE), usage(Provider.CODEX), NOW))
        self.assertIsNone(relayed.claude.remaining_5h)

    def test_trend_uses_per_provider_baseline(self):
        cached = usage(five=63, seven=80, source=UsageSource.STALE_CACHE)
        gap = history.HistoryEntry(NOW - timedelta(hours=2), history.measurements_only(
            UsageSnapshot(cached, usage(Provider.CODEX, five=91, seven=99), NOW - timedelta(hours=2))))
        base = history.HistoryEntry(NOW - timedelta(hours=1), UsageSnapshot(
            usage(five=90, seven=95), usage(Provider.CODEX, five=91, seven=99), NOW - timedelta(hours=1)))
        current = UsageSnapshot(usage(five=63, seven=80), usage(Provider.CODEX, five=91, seven=99), NOW)
        self.assertIn("Claude 5h -27", history.trend_summary([gap, base], current, self.EN))

    def test_store_trend_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = history.HistoryStore(Path(tmp) / "h.json")
            snap = UsageSnapshot(usage(five=63, seven=80), usage(Provider.CODEX, five=91, seven=99), NOW)
            store.append(snap, 7, NOW)
            entries = store.append(snap, 7, NOW + timedelta(hours=1))
            self.assertEqual(len(entries), 2)
            self.assertEqual(store.load(), entries)
            self.assertEqual(history.trend_summary(entries, snap, self.EN), "24h trend: Claude 5h 0% 7d 0% Codex 5h 0% 7d 0%")
            missing = UsageSnapshot(usage(five=40), usage(Provider.CODEX, five=90, seven=99), NOW)
            self.assertEqual(
                history.trend_summary([history.HistoryEntry(NOW, missing)], snap, self.EN),
                "24h trend: Claude 5h +23% 7d -- Codex 5h +1% 7d 0%")
            csv = store.csv_string()
            self.assertIn("recorded_at,provider,remaining_5h", csv)
            self.assertIn("claude,63,80", csv)
            # A poll within a minute replaces the last entry; old entries age out.
            store.append(snap, 7, NOW + timedelta(hours=1, seconds=30))
            self.assertEqual(len(store.load()), 2)
            self.assertEqual(len(store.append(snap, 1, NOW + timedelta(days=1, hours=2))), 1)
        self.assertEqual(history.trend_summary([], UsageSnapshot(usage(), usage(Provider.CODEX), NOW), self.EN), "Not enough history yet")

    def test_sparkline(self):
        entries = [history.HistoryEntry(NOW + timedelta(minutes=i), UsageSnapshot(usage(five=v), usage(Provider.CODEX), NOW)) for i, v in enumerate([100, 50, 0, None])]
        self.assertEqual(history.sparkline_series(entries, Provider.CLAUDE, ForecastWindow.FIVE_HOUR), [100, 50, 0])
        self.assertEqual(history.sparkline_text([100, 50, 0]), "█▅▁")
        self.assertEqual(history.sparkline_text([40]), "")
        many = [history.HistoryEntry(NOW + timedelta(minutes=i), UsageSnapshot(usage(five=i), usage(Provider.CODEX), NOW)) for i in range(40)]
        self.assertEqual(history.sparkline_series(many, Provider.CLAUDE, ForecastWindow.FIVE_HOUR, 20)[:3], [0, 2, 4])


class ForecastTests(unittest.TestCase):
    """UsageForecaster / UsagePace truth tables from the Windows tests."""

    EN = staticmethod(Localizer(AppLanguage.ENGLISH).text)

    def entry(self, seconds_ago, claude5h, claude7d=100):
        at = NOW - timedelta(seconds=seconds_ago)
        return history.HistoryEntry(at, UsageSnapshot(usage(five=claude5h, seven=claude7d, updated=at), usage(Provider.CODEX, five=100, seven=100, updated=at), at))

    def fc(self, entries, reset_at=None):
        return forecast.forecast(entries, Provider.CLAUDE, ForecastWindow.FIVE_HOUR, reset_at, NOW)

    def test_forecast(self):
        steady = [self.entry(3600, 60), self.entry(1800, 50), self.entry(0, 40)]
        result = self.fc(steady, NOW + timedelta(hours=3))
        self.assertEqual(int(result.burn_per_hour), 20)
        self.assertEqual(int(result.seconds_to_empty), 7200)
        self.assertTrue(result.will_empty_before_reset)
        self.assertFalse(self.fc(steady, NOW + timedelta(hours=1)).will_empty_before_reset)
        reset = self.fc([self.entry(3600, 30), self.entry(1800, 80), self.entry(900, 70), self.entry(0, 60)])
        self.assertEqual(int(reset.seconds_to_empty), 5400)
        self.assertEqual(int(reset.burn_per_hour), 40)
        self.assertIsNone(self.fc([self.entry(3600, 40), self.entry(0, 50)]))
        self.assertIsNone(self.fc([self.entry(300, 60), self.entry(0, 50)]))
        self.assertIsNone(self.fc([self.entry(10800, 60), self.entry(7200, 40)]))

    def test_menu_line_and_alert(self):
        steady = self.fc([self.entry(3600, 60), self.entry(1800, 50), self.entry(0, 40)], NOW + timedelta(hours=3))
        self.assertEqual(forecast.forecast_menu_line(steady, ForecastWindow.FIVE_HOUR, self.EN), "Projected depletion: ~2h 0m · empties before reset")
        self.assertEqual(forecast.forecast_menu_line(steady, ForecastWindow.SEVEN_DAY, self.EN), "Projected depletion: ~2h 0m (7d) · empties before reset")
        self.assertIsNone(forecast.forecast_menu_line(None, ForecastWindow.SEVEN_DAY, self.EN))
        reset = NOW + timedelta(hours=3)
        item = forecast.ForecastAlertInput(Provider.CLAUDE, ForecastWindow.FIVE_HOUR, steady, reset)
        found = forecast.forecast_alerts([item], True, self.EN)
        self.assertEqual([c.id for c in found], [f"claude-5h-empty-before-reset-{int(reset.timestamp())}"])
        self.assertEqual(found[0].body, "Claude 5h: ~2h 0m → 0% (before reset)")
        self.assertEqual(forecast.forecast_alerts([item], False, self.EN), [])
        early = self.fc([self.entry(3600, 60), self.entry(1800, 50), self.entry(0, 40)], NOW + timedelta(hours=1))
        self.assertEqual(forecast.forecast_alerts([forecast.ForecastAlertInput(Provider.CLAUDE, ForecastWindow.FIVE_HOUR, early, NOW + timedelta(hours=1))], True, self.EN), [])

    def pace(self, remaining, reset_in, window=ForecastWindow.FIVE_HOUR):
        reset_at = NOW + timedelta(seconds=reset_in) if reset_in is not None else None
        return forecast.pace_at(remaining, reset_at, window, NOW)

    def test_pace(self):
        P = forecast.Pace
        self.assertIs(self.pace(30, 9000).pace, P.AHEAD)
        self.assertIs(self.pace(50, 9000).pace, P.ON_TRACK)
        self.assertIs(self.pace(41, 9000).pace, P.ON_TRACK)
        self.assertIs(self.pace(39, 9000).pace, P.AHEAD)
        self.assertIs(self.pace(75, 9000).pace, P.BEHIND)
        self.assertEqual(self.pace(50, 9000).expected_remaining_percent, 50)
        self.assertIs(self.pace(80, 302_400, ForecastWindow.SEVEN_DAY).pace, P.BEHIND)
        self.assertIsNone(self.pace(50, None))
        self.assertIsNone(self.pace(50, -60))
        self.assertIsNone(self.pace(50, 20_000))
        self.assertIsNone(self.pace(None, 9000))

    def test_pace_line(self):
        paced = usage(five=30, seven=75, reset5=NOW + timedelta(seconds=9000), reset7=NOW + timedelta(seconds=302_400))
        self.assertEqual(forecast.pace_menu_line(paced, Localizer(AppLanguage.KOREAN).text), "페이스: 5h 빠름 · 7d 여유")
        self.assertEqual(forecast.pace_menu_line(paced, self.EN), "Pace: 5h ahead · 7d under")
        self.assertIsNone(forecast.pace_menu_line(paced.with_(source=UsageSource.STALE_CACHE), self.EN))
        self.assertIs(forecast.pace_reading(paced.with_(source=UsageSource.STATUS_LINE), ForecastWindow.FIVE_HOUR).pace, forecast.Pace.AHEAD)

    def test_preferred_window(self):
        self.assertIs(display.preferred_forecast_window(usage(five=50, seven=80)), ForecastWindow.FIVE_HOUR)
        self.assertIs(display.preferred_forecast_window(usage(Provider.CODEX, seven=80)), ForecastWindow.SEVEN_DAY)
        self.assertIs(display.preferred_forecast_window(usage()), ForecastWindow.FIVE_HOUR)


class CostTests(unittest.TestCase):
    """Subscription money, formatting and money alerts: Windows truth tables."""

    EN = staticmethod(Localizer(AppLanguage.ENGLISH).text)
    KO = staticmethod(Localizer(AppLanguage.KOREAN).text)
    PRO = """{"extra_usage":{"is_enabled":false,"monthly_limit":null,"used_credits":null,"utilization":null,"currency":null,"decimal_places":null,"user_disabled":true},
         "spend":{"used":{"amount_minor":0,"currency":"USD","exponent":2},"limit":null,"percent":0,"severity":"normal","enabled":false}}"""
    PLUS = """{"plan_type":"plus","rate_limit":{"primary_window":{"used_percent":10,"limit_window_seconds":18000,"reset_at":1793700000}},
         "credits":{"has_credits":false,"unlimited":false,"overage_limit_reached":false,"balance":"0"}}"""

    def test_parsers(self):
        D = cost.Decimal
        self.assertIsNone(cost.claude_extra(self.PRO))
        on = cost.claude_extra('{"spend":{"enabled":true,"used":{"amount_minor":186,"currency":"USD","exponent":2},"limit":{"amount_minor":500,"currency":"USD","exponent":2},"severity":"normal"}}')
        self.assertEqual(on.used.value, D("1.86"))
        self.assertEqual(on.limit.value, D("5"))
        self.assertIsNone(cost.claude_extra('{"extra_usage":{"is_enabled":true,"used_credits":186}}'))
        self.assertIsNone(cost.claude_extra('{"spend":{"enabled":true,"used":{"amount_minor":5,"currency":"USD","exponent":9}}}'))
        self.assertIsNone(cost.claude_extra('{"five_hour":{"utilization":10}}'))
        self.assertIsNone(cost.codex_credits('{"credits":{"has_credits":false,"balance":"0"}}'))
        self.assertEqual(cost.codex_credits('{"credits":{"has_credits":true,"balance":"18.40"}}'), D("18.4"))
        self.assertIsNone(cost.codex_credits('{"credits":{"has_credits":true,"balance":"lots"}}'))

    def test_formatting(self):
        def usd(text):
            return cost.cost_text(cost.CostAmount(cost.Decimal(text)))
        self.assertEqual(usd("0"), "$0.00")
        self.assertEqual(usd("0.004"), "<$0.01")
        self.assertEqual(usd("12.345"), "$12.35")
        self.assertEqual(usd("999.99"), "$999.99")
        self.assertEqual(usd("1234.5"), "$1.2K")
        self.assertEqual(usd("1250"), "$1.3K")
        self.assertEqual(usd("3400000"), "$3.4M")
        self.assertEqual(usd("-2.5"), "-$2.50")
        self.assertEqual(cost.cost_text(cost.CostAmount(cost.Decimal("12.3"), "eur")), "12.30 EUR")
        self.assertEqual(cost.credits_text(cost.Decimal("18.4")), "18.40")
        compact = lambda t: cost.cost_compact(cost.CostAmount(cost.Decimal(t)))  # noqa: E731
        self.assertEqual(compact("42.18"), "$42")
        self.assertEqual(compact("42.5"), "$43")
        self.assertEqual(compact("3.07"), "$3.07")
        self.assertEqual(compact("1234"), "$1.2K")

    def test_budget_and_month(self):
        from zoneinfo import ZoneInfo
        D = cost.Decimal
        self.assertEqual(cost.budget_percent(D("79.99"), D(100)), 79)
        self.assertEqual(cost.reached_thresholds(D(80), D(100)), [80])
        self.assertEqual(cost.reached_thresholds(D(100), D(100)), [80, 100])
        self.assertEqual(cost.reached_thresholds(D(150), D(100)), [80, 100])
        self.assertIsNone(cost.budget_percent(D(50), D(0)))
        at = datetime(2026, 11, 30, 16, tzinfo=timezone.utc)
        self.assertEqual(cost.month_key(at, ZoneInfo("Asia/Seoul")), "2026-12")
        self.assertEqual(cost.month_key(at, timezone.utc), "2026-11")
        self.assertEqual(cost.next_fetch(NOW, True), NOW + timedelta(minutes=10))
        self.assertEqual(cost.next_fetch(NOW, False), NOW + timedelta(minutes=5))
        self.assertTrue(cost.fetch_due(None, NOW))
        self.assertFalse(cost.fetch_due(NOW + timedelta(seconds=1), NOW))

    def test_subscription_lines(self):
        pro = parser.parse_claude_usage('{"five_hour":{"utilization":20},"seven_day":{"utilization":40},' + self.PRO[1:], NOW)
        self.assertIsNone(pro.extra_usage)
        self.assertIsNone(cost.subscription_line(pro, self.EN))
        paying = parser.parse_claude_usage('{"five_hour":{"utilization":20},"spend":{"enabled":true,"used":{"amount_minor":186,"currency":"USD","exponent":2},"limit":{"amount_minor":500,"currency":"USD","exponent":2},"severity":"normal"}}', NOW)
        line = cost.subscription_line(paying, self.KO)
        self.assertEqual((line.label, line.value, line.percent), ("추가 사용량", "$1.86 / $5.00", 37))
        no_limit = paying.with_(extra_usage=cost.ClaudeExtraUsage(cost.CostAmount(cost.Decimal("1.86")), None, None))
        self.assertEqual(cost.subscription_line(no_limit, self.EN).value, "$1.86")
        self.assertIsNone(cost.subscription_line(no_limit, self.EN).percent)
        other = paying.with_(extra_usage=cost.ClaudeExtraUsage(cost.CostAmount(cost.Decimal(2)), cost.CostAmount(cost.Decimal(5), "EUR"), None))
        self.assertEqual(cost.subscription_line(other, self.EN).value, "$2.00")
        plus = parser.parse_codex_usage(self.PLUS, NOW)
        self.assertIsNone(plus.credit_balance)
        self.assertIsNone(cost.subscription_line(plus, self.EN))
        credits = parser.parse_codex_usage(self.PLUS.replace('"has_credits":false,"unlimited":false,"overage_limit_reached":false,"balance":"0"', '"has_credits":true,"balance":"18.4"'), NOW)
        line = cost.subscription_line(credits, self.EN)
        self.assertEqual((line.label, line.value, line.percent), ("Credit balance", "18.40", None))

    def test_money_rides_cache_not_history(self):
        paying = parser.parse_claude_usage('{"five_hour":{"utilization":20},"spend":{"enabled":true,"used":{"amount_minor":186,"currency":"USD","exponent":2},"severity":"warning"}}', NOW)
        snap = UsageSnapshot(paying, parser.parse_codex_usage(self.PLUS.replace('"has_credits":false', '"has_credits":true').replace('"overage_limit_reached":false', '"overage_limit_reached":true').replace('"balance":"0"', '"balance":"3.5"'), NOW), NOW)
        with tempfile.TemporaryDirectory() as tmp:
            store = cache.CacheStore(Path(tmp) / "c.json")
            store.save(snap)
            self.assertEqual(store.load(timedelta(hours=1), NOW), snap)
        recorded = history.measurements_only(snap)
        self.assertIsNone(recorded.claude.extra_usage)
        self.assertIsNone(recorded.codex.credit_balance)
        self.assertIsNone(recorded.codex.credit_limit_reached)

    def evaluate(self, snapshot, delivered=(), notifications=True, at=None):
        return cost.evaluate_alerts(snapshot, list(delivered), notifications, at or datetime(2026, 11, 15, tzinfo=timezone.utc), self.KO, timezone.utc)

    def claude_spend(self, spend):
        return parser.parse_claude_usage('{"five_hour":{"utilization":20},"spend":' + spend + '}', NOW)

    def test_money_alerts(self):
        plus = parser.parse_codex_usage(self.PLUS, NOW)
        warning = self.claude_spend('{"enabled":true,"used":{"amount_minor":410,"currency":"USD","exponent":2},"limit":{"amount_minor":500,"currency":"USD","exponent":2},"severity":"Warning"}')
        result = self.evaluate(UsageSnapshot(warning, plus, NOW))
        self.assertEqual([a.id for a in result.alerts], ["claude-extra-warning-2026-11"])
        self.assertEqual(result.alerts[0].title, "Claude 추가 사용량")
        self.assertEqual(result.alerts[0].body, "추가 사용량이 $4.10 / $5.00입니다.")
        self.assertEqual(self.evaluate(UsageSnapshot(warning, plus, NOW), result.delivered).alerts, [])
        normal = self.claude_spend('{"enabled":true,"used":{"amount_minor":100,"currency":"USD","exponent":2},"severity":"normal"}')
        self.assertEqual(self.evaluate(UsageSnapshot(normal, plus, NOW)).alerts, [])
        pro = self.claude_spend('{"used":{"amount_minor":0,"currency":"USD","exponent":2},"limit":null,"percent":0,"severity":"warning","enabled":false}')
        self.assertEqual(self.evaluate(UsageSnapshot(pro, plus, NOW)).alerts, [])
        self.assertIsNone(plus.credit_limit_reached)
        limited = parser.parse_codex_usage(self.PLUS.replace('"has_credits":false', '"has_credits":true').replace('"overage_limit_reached":false', '"overage_limit_reached":true'), NOW)
        self.assertTrue(limited.credit_limit_reached)
        credits = self.evaluate(UsageSnapshot(pro, limited, NOW))
        self.assertEqual([a.id for a in credits.alerts], ["codex-credits-limit-2026-11"])
        self.assertEqual(credits.alerts[0].body, "Codex 크레딧 초과 사용 한도에 도달했습니다.")
        no_credits = parser.parse_codex_usage(self.PLUS.replace('"overage_limit_reached":false', '"overage_limit_reached":true'), NOW)
        self.assertIsNone(no_credits.credit_limit_reached)
        off = self.evaluate(UsageSnapshot(warning, plus, NOW), ["claude-extra-warning-2026-11"], notifications=False)
        self.assertEqual(off.alerts, [])
        self.assertEqual(off.delivered, {"claude-extra-warning-2026-11"})
        december = self.evaluate(None, ["claude-cost-100-2026-11"], at=datetime(2026, 12, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(december.delivered, set())


class IssueTests(unittest.TestCase):
    """UsageIssueFormatter and CredentialStoreAdvice truth tables."""

    EN = staticmethod(Localizer(AppLanguage.ENGLISH).text)

    def test_kinds(self):
        K = issues.IssueKind
        self.assertIs(issues.kind_of("Disabled"), K.DISABLED)
        self.assertIs(issues.kind_of("HTTP 429 from Claude API; retrying after 5m"), K.RATE_LIMITED)
        self.assertIs(issues.kind_of("Missing credentials"), K.MISSING_CREDENTIALS)
        self.assertIs(issues.kind_of("Credentials expired for Codex API"), K.EXPIRED_CREDENTIALS)
        self.assertIs(issues.kind_of("HTTP 401 from Claude API"), K.EXPIRED_CREDENTIALS)
        self.assertIs(issues.kind_of("HTTP 403 from Codex API"), K.EXPIRED_CREDENTIALS)
        self.assertIs(issues.kind_of("HTTP 500 from Claude API"), K.HTTP_STATUS)
        self.assertIs(issues.kind_of("Timed out contacting Claude API"), K.TIMED_OUT)
        self.assertIs(issues.kind_of("Network error: [Errno -2] Name or service not known"), K.NETWORK)

    def test_issue_texts(self):
        expired = ProviderUsage.unavailable(Provider.CLAUDE, "Credentials expired for Claude API", NOW)
        found = issues.issue(expired, self.EN)
        self.assertEqual(found.title, "Saved token expired")
        self.assertIn("/login", found.recovery)
        codex = issues.issue(ProviderUsage.unavailable(Provider.CODEX, "HTTP 401 from Codex API", NOW), self.EN)
        self.assertIn("codex login", codex.recovery)
        cached = issues.issue(usage(five=50, source=UsageSource.STALE_CACHE, error="Timed out contacting Claude API"), self.EN)
        self.assertIs(cached.kind, issues.IssueKind.USING_CACHED_DATA)
        self.assertEqual(cached.recovery, "Check your network connection and try again.")
        self.assertIs(issues.issue(usage(five=50), self.EN).kind, issues.IssueKind.OK)
        relayed = issues.issue(usage(five=50, source=UsageSource.STATUS_LINE, error="Credentials expired for Claude API"), self.EN)
        self.assertIs(relayed.kind, issues.IssueKind.STATUS_LINE)
        self.assertIn("/login", relayed.recovery)

    def test_stale_store_line(self):
        store = issues.CredentialSource("CredentialsFile", NOW)
        expired = issues.issue(ProviderUsage.unavailable(Provider.CLAUDE, "HTTP 401", NOW), self.EN)
        self.assertEqual(issues.stale_line(Provider.CLAUDE, expired, store, NOW + timedelta(hours=48), self.EN), "Credentials not refreshed in 2d 0h")
        self.assertIsNone(issues.stale_line(Provider.CLAUDE, expired, store, NOW + issues.STALE_AFTER - timedelta(minutes=1), self.EN))
        limited = issues.issue(ProviderUsage.unavailable(Provider.CLAUDE, "HTTP 429", NOW), self.EN)
        self.assertIsNone(issues.stale_line(Provider.CLAUDE, limited, store, NOW + timedelta(hours=48), self.EN))
        codex = issues.issue(ProviderUsage.unavailable(Provider.CODEX, "HTTP 401", NOW), self.EN)
        self.assertIsNone(issues.stale_line(Provider.CODEX, codex, store, NOW + timedelta(hours=48), self.EN))
        carried = issues.issue(usage(five=5, source=UsageSource.STALE_CACHE, error="credentials expired"), self.EN)
        self.assertIsNotNone(issues.stale_line(Provider.CLAUDE, carried, store, NOW + timedelta(hours=48), self.EN))
        self.assertIsNone(issues.stale_line(Provider.CLAUDE, expired, issues.CredentialSource("None", None), NOW + timedelta(hours=48), self.EN))

    def test_diagnostics_text_has_no_secret(self):
        from tokki import diagnostics
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                snap = UsageSnapshot(usage(five=50, seven=80), ProviderUsage.unavailable(Provider.CODEX, "HTTP 401 from Codex API", NOW), NOW)
                report = diagnostics.diagnostics_text(settings.AppSettings(), [], snap, NOW, None, NOW)
            finally:
                if old is None:
                    del os.environ["XDG_CONFIG_HOME"]
                else:
                    os.environ["XDG_CONFIG_HOME"] = old
        self.assertTrue(report.startswith("Tokki Diagnostics"))
        self.assertIn("Claude 5h remaining: 50%", report)
        self.assertIn("Codex status: ExpiredCredentials", report)
        self.assertIn("Claude money past plan: off", report)


class LocalizationTests(unittest.TestCase):
    def test_every_english_key_has_korean(self):
        from tokki import i18n
        self.assertEqual(set(i18n.EN), set(i18n.KO))
        self.assertEqual(Localizer(AppLanguage.KOREAN).text("minutes", n=5), "5분")


class AutostartTests(unittest.TestCase):
    def test_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "autostart" / "tokki.desktop"
            autostart.set_enabled(True, path)
            self.assertTrue(autostart.is_enabled(path))
            self.assertIn("Exec=", path.read_text())
            autostart.set_enabled(False, path)
            self.assertFalse(autostart.is_enabled(path))


if __name__ == "__main__":
    unittest.main()
