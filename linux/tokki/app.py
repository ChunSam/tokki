"""The GTK tray app: an AppIndicator with a label and a menu.

Mirrors the role of windows/TokenTracker.Windows/TrayAppContext.cs: poll on a
timer, run the cache policy, notify, render. All fetching happens on a worker
thread; everything that touches GTK runs on the main loop.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator  # noqa: E402

try:
    gi.require_version("Notify", "0.7")
    from gi.repository import Notify  # noqa: E402
except (ValueError, ImportError):
    Notify = None

from . import VERSION, autostart, diagnostics
from .core import alerts, cost, display, forecast, history, issues, pause, paths, update_check
from .core.cache import CacheStore, apply_cache_policy
from .core.cli_refresh import ClaudeCliRefresher
from .core.client import UsageClient
from .core.models import AppLanguage, DisplayMode, ForecastWindow, Provider, ProviderLabelStyle, ProviderUsage, UsageSnapshot, UsageSource
from .core.settings import SettingsStore, paused_until
from .i18n import Localizer

RESOURCES = Path(__file__).resolve().parent / "resources"
REFRESH_CHOICES = (60, 120, 300, 600, 900)
FIVE_HOUR_THRESHOLDS = (0, 10, 20, 30, 50)
SEVEN_DAY_THRESHOLDS = (0, 5, 10, 20, 30)
RESET_ALERT_MINUTES = (0, 5, 10, 15, 30, 60)
HISTORY_RETENTION_DAYS = (3, 7, 14, 30)
COUNTDOWN_TICK_SECONDS = 30
# After the machine wakes, wait for the network to come back before fetching.
WAKE_REFRESH_DELAY_SECONDS = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SingleInstance:
    """A second launch exits instead of adding a second icon."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(path, "w")
        try:
            fcntl.flock(self._handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.acquired = False


class TokkiApp:
    def __init__(self):
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        self.cache_store = CacheStore()
        self.history_store = history.HistoryStore()
        self.history = self.history_store.load()
        self.client = UsageClient(cli_refresher=ClaudeCliRefresher())
        self.snapshot: UsageSnapshot | None = self.cache_store.load(self._stale_tolerance(), _now())
        self.delivered_alerts: set[str] = set()
        # Each provider's last reading with numbers, for telling when a low window
        # has refilled. Seeded from the cache so a reset during downtime counts.
        self.last_readings = alerts.last_readings({}, self.snapshot) if self.snapshot else {}
        self.refreshing = False
        self.poll_source: int | None = None
        self.wake_source: int | None = None
        self.checking_for_updates = False
        self.next_update_check_at: datetime | None = None
        self.available_update: str | None = None
        self.last_success: datetime | None = None
        self.window = None
        # Autostart lives in the desktop's own folder; the file is the truth.
        self.settings.LaunchAtLogin = autostart.is_enabled()

        self.indicator = AppIndicator.Indicator.new(
            "tokki", str(RESOURCES / "tokki-tray.svg"), AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Tokki")
        if Notify is not None:
            Notify.init("Tokki")

        self.render()
        self.schedule_polling()
        self.watch_for_wake()
        GLib.timeout_add_seconds(COUNTDOWN_TICK_SECONDS, self._tick)
        GLib.idle_add(self.refresh)

    # --- helpers ---------------------------------------------------------------

    @property
    def text(self):
        return Localizer(self.settings.Language).text

    def _stale_tolerance(self) -> timedelta:
        return timedelta(hours=max(1, self.settings.StaleToleranceHours))

    def save_settings(self) -> None:
        try:
            self.settings_store.save(self.settings)
        except OSError as error:
            print(f"tokki: could not save settings: {error}", file=sys.stderr)

    # --- polling ---------------------------------------------------------------

    def schedule_polling(self) -> None:
        if self.poll_source is not None:
            GLib.source_remove(self.poll_source)
        self.poll_source = GLib.timeout_add_seconds(self.settings.RefreshIntervalSeconds, self._poll)

    def _poll(self) -> bool:
        self.refresh()
        return True

    def _tick(self) -> bool:
        self.render_label()
        if self.is_paused():
            # Keep the menu's pause countdown fresh, and lift a lapsed pause.
            self.indicator.set_menu(self.build_menu())
        return True

    def is_paused(self) -> bool:
        return pause.is_paused(paused_until(self.settings), _now())

    def watch_for_wake(self) -> None:
        """logind says PrepareForSleep(false) on resume; refresh a little after."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as error:
            print(f"tokki: no system bus, no refresh on wake: {error}", file=sys.stderr)
            return
        bus.signal_subscribe(
            "org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
            "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE, self._on_prepare_for_sleep,
        )
        self._system_bus = bus

    def _on_prepare_for_sleep(self, _bus, _sender, _path, _iface, _signal, params) -> None:
        going_to_sleep = params.unpack()[0]
        if going_to_sleep:
            return
        if self.wake_source is not None:
            GLib.source_remove(self.wake_source)
        self.wake_source = GLib.timeout_add_seconds(WAKE_REFRESH_DELAY_SECONDS, self._wake_refresh)

    def _wake_refresh(self) -> bool:
        self.wake_source = None
        self.refresh()
        return False

    def refresh_now(self, *_args) -> None:
        """A manual refresh is an explicit request to fetch, so it lifts a pause."""
        if self.is_paused():
            self.settings.PollPausedUntil = None
            self.save_settings()
        self.refresh()

    def refresh(self, *_args) -> bool:
        if self.refreshing:
            return False
        if self.is_paused():
            # Skip the network fetch while paused.
            self.render()
            return False
        self.refreshing = True
        claude, codex = self.settings.ClaudeEnabled, self.settings.CodexEnabled
        renew = self.settings.RenewClaudeTokenWithCli

        def work():
            try:
                snapshot = self.client.refresh(claude=claude, codex=codex, renew_claude=renew)
            except Exception as error:  # never let a fetch bug kill the tray
                now = _now()
                snapshot = UsageSnapshot(
                    ProviderUsage.unavailable(Provider.CLAUDE, str(error), now),
                    ProviderUsage.unavailable(Provider.CODEX, str(error), now),
                    now,
                )
            GLib.idle_add(self._apply, snapshot)

        threading.Thread(target=work, name="tokki-refresh", daemon=True).start()
        return False

    def _apply(self, current: UsageSnapshot) -> bool:
        self.refreshing = False
        now = _now()
        merged = apply_cache_policy(
            current, self.snapshot, now,
            claude_enabled=self.settings.ClaudeEnabled,
            codex_enabled=self.settings.CodexEnabled,
            max_age=self._stale_tolerance(),
        )
        self.snapshot = merged
        # When the provider measured, not when this poll ran.
        measured = [u.updated_at for u in (current.claude, current.codex) if u.source in (UsageSource.API, UsageSource.STATUS_LINE)]
        if measured:
            self.last_success = max(measured)
        if merged.claude.is_available or merged.codex.is_available:
            self.cache_store.save(merged)
        # Only this poll's API measurements enter the record.
        self.history = self.history_store.append(history.measurements_only(current), self.settings.HistoryRetentionDays, now)
        self.notify(merged, now)
        self.render()
        self.check_for_updates_if_due()
        return False

    # --- update check -------------------------------------------------------------

    def check_for_updates_if_due(self) -> None:
        """Rides the refresh; update_check.is_due keeps it to once a day."""
        now = _now()
        if not self.settings.CheckForUpdates or self.checking_for_updates or not update_check.is_due(self.next_update_check_at, now):
            return
        self.checking_for_updates = True

        def work():
            latest = update_check.fetch_latest_version()
            GLib.idle_add(self._apply_update_check, now, latest)

        threading.Thread(target=work, name="tokki-update-check", daemon=True).start()

    def _apply_update_check(self, attempted_at: datetime, latest: str | None) -> bool:
        self.checking_for_updates = False
        self.next_update_check_at = update_check.next_check(attempted_at, latest is not None)
        if latest is not None and self.settings.CheckForUpdates:
            self.available_update = latest if update_check.is_newer(latest, VERSION) else None
            self.render()
        return False

    # --- notifications -----------------------------------------------------------

    def notify(self, snapshot: UsageSnapshot, now: datetime) -> None:
        # Kept current while notifications are off, so turning them back on does
        # not compare against a reading from hours ago.
        previous = self.last_readings
        self.last_readings = alerts.last_readings(previous, snapshot)
        if not self.settings.NotificationsEnabled:
            self.delivered_alerts = set()
            return
        settings = alerts.AlertSettings(
            True,
            self.settings.FiveHourAlertThreshold,
            self.settings.SevenDayAlertThreshold,
            self.settings.ResetAlertMinutes,
            alerts.stalled_threshold(self.settings.RefreshIntervalSeconds),
        )
        active = alerts.candidates(snapshot, settings, now, self.text)
        active += alerts.freed_up_candidates(previous, snapshot, settings, self.text)
        active += self._forecast_alerts(snapshot)
        fresh, self.delivered_alerts = alerts.to_deliver(active, self.delivered_alerts)
        fresh += self._cost_alerts(snapshot, now)
        if Notify is None:
            return
        for candidate in fresh:
            try:
                Notify.Notification.new(candidate.title, candidate.body, "tokki").show()
            except GLib.Error as error:
                print(f"tokki: notification failed: {error}", file=sys.stderr)

    def _cost_alerts(self, snapshot: UsageSnapshot, now: datetime) -> list[alerts.AlertCandidate]:
        """Once per local month each, remembered in settings across relaunches."""
        result = cost.evaluate_alerts(snapshot, self.settings.DeliveredCostAlerts, self.settings.NotificationsEnabled, now, self.text)
        delivered = sorted(result.delivered)
        if delivered != self.settings.DeliveredCostAlerts:
            self.settings.DeliveredCostAlerts = delivered
            self.save_settings()
        return result.alerts

    def _forecast_alerts(self, snapshot: UsageSnapshot) -> list[alerts.AlertCandidate]:
        if not self.settings.DepletionAlertEnabled:
            return []
        inputs = []
        for provider in (Provider.CLAUDE, Provider.CODEX):
            usage = snapshot.usage(provider)
            for window, reset_at in ((ForecastWindow.FIVE_HOUR, usage.reset_at_5h), (ForecastWindow.SEVEN_DAY, usage.reset_at_7d)):
                result = forecast.forecast(self.history, provider, window, reset_at, snapshot.updated_at)
                if result:
                    inputs.append(forecast.ForecastAlertInput(provider, window, result, reset_at))
        return forecast.forecast_alerts(inputs, True, self.text)

    # --- rendering -----------------------------------------------------------------

    def render(self) -> None:
        self.render_label()
        menu = self.build_menu()
        self.indicator.set_menu(menu)
        if self.window is not None and self.window.get_visible():
            self.window.refresh_overview()

    def open_window(self, *_args) -> None:
        from .window import TokkiWindow
        if self.window is None:
            self.window = TokkiWindow(self)
        else:
            # Settings may have moved through the menu since it was last open.
            self.window.rebuild()
        self.window.show_all()
        self.window.present()

    def render_label(self) -> None:
        now = _now()
        label = display.tray_label(
            self.snapshot, self.settings.DisplayMode, self.settings.ProviderLabelStyle,
            self.settings.ShowResetCountdown, now, self.text("now"),
        )
        self.indicator.set_label(label, "Cdx 100% 6d23h · Cl 100% 4h59m")
        icon = "tokki-tray-warning.svg" if display.tray_shows_warning(self.snapshot, self.settings.DisplayMode) else "tokki-tray.svg"
        self.indicator.set_icon_full(str(RESOURCES / icon), "Tokki")

    def _provider_lines(self, usage: ProviderUsage, enabled: bool, now: datetime) -> list[str]:
        text = self.text
        if not enabled:
            return [f"{usage.display_name}: {text('disabled')}"]
        reported = usage.is_available
        five = display.format_window_reset(usage.remaining_5h, usage.reset_at_5h, now, text, reported)
        seven = display.format_window_reset(usage.remaining_7d, usage.reset_at_7d, now, text, reported)
        if usage.source is UsageSource.STALE_CACHE:
            # A cached reading's reset times keep ticking while its value stands still.
            five = seven = text("source_cache")
        line = (
            f"{usage.display_name}{' (' + usage.plan + ')' if usage.plan else ''}  "
            f"5h {display.format_percent(usage.remaining_5h)} · {five}   "
            f"7d {display.format_percent(usage.remaining_7d)} · {seven}"
        )
        lines = [line]
        found = issues.issue(usage, text)
        if found.kind is not issues.IssueKind.OK:
            lines.append(f"    {found.title}" + (f" — {found.recovery}" if found.recovery else ""))
            stale = issues.stale_line(usage.provider, found, issues.CredentialSource.detect(paths.claude_credentials_path()), now, text)
            if stale:
                lines.append("    " + stale)
        money = cost.subscription_line(usage, text)
        if money:
            lines.append(f"    {money.label}: {money.value}")
        if self.settings.ShowForecast:
            window = display.preferred_forecast_window(usage)
            reset_at = usage.reset_at_5h if window is ForecastWindow.FIVE_HOUR else usage.reset_at_7d
            forecast_line = forecast.forecast_menu_line(forecast.forecast(self.history, usage.provider, window, reset_at, now), window, text)
            if forecast_line:
                lines.append("    " + forecast_line)
            pace_line = forecast.pace_menu_line(usage, text)
            if pace_line:
                lines.append("    " + pace_line)
        return lines

    def build_menu(self) -> Gtk.Menu:
        text = self.text
        menu = Gtk.Menu()
        now = _now()

        def info(label: str) -> None:
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(False)
            menu.append(item)

        if self.available_update and self.settings.CheckForUpdates:
            # Opens the release page; installing the new .deb stays manual.
            update = Gtk.MenuItem(label=text("update_available", version="v" + self.available_update) + " — " + text("download_update"))
            update.connect("activate", lambda *_: self._open(update_check.RELEASES_PAGE))
            menu.append(update)
            menu.append(Gtk.SeparatorMenuItem())

        if self.snapshot is None:
            info(text("loading"))
        else:
            for provider, enabled in ((Provider.CLAUDE, self.settings.ClaudeEnabled), (Provider.CODEX, self.settings.CodexEnabled)):
                for line in self._provider_lines(self.snapshot.usage(provider), enabled, now):
                    info(line)
            local = self.snapshot.updated_at.astimezone().strftime("%H:%M")
            info(text("updated_at", time=local))

        details = Gtk.MenuItem(label=text("open_details"))
        details.connect("activate", lambda *_: GLib.idle_add(lambda: (self.open_window(), False)[1]))
        menu.append(details)
        # A middle click on the panel item opens the window too.
        self.indicator.set_secondary_activate_target(details)

        menu.append(Gtk.SeparatorMenuItem())
        refresh = Gtk.MenuItem(label=text("refresh_now"))
        refresh.connect("activate", self.refresh_now)
        menu.append(refresh)
        self._append_pause_controls(menu, now)
        menu.append(Gtk.SeparatorMenuItem())

        menu.append(self._radio_submenu(text("display"), [
            (text("display_lowest"), DisplayMode.LOWEST_REMAINING),
            (text("display_both"), DisplayMode.BOTH),
            (text("display_claude"), DisplayMode.CLAUDE_ONLY),
            (text("display_codex"), DisplayMode.CODEX_ONLY),
        ], self.settings.DisplayMode, lambda value: self._set("DisplayMode", value)))
        menu.append(self._radio_submenu(text("label_style"), [
            (text("label_abbreviation"), ProviderLabelStyle.ABBREVIATION),
            (text("label_full"), ProviderLabelStyle.ICON),
        ], self.settings.ProviderLabelStyle, lambda value: self._set("ProviderLabelStyle", value)))
        menu.append(self._radio_submenu(text("refresh_interval"), [
            (text("minutes", n=seconds // 60), seconds) for seconds in REFRESH_CHOICES
        ], self.settings.RefreshIntervalSeconds, self._set_interval))

        providers = Gtk.MenuItem(label=text("providers"))
        provider_menu = Gtk.Menu()
        provider_menu.append(self._check("Claude", self.settings.ClaudeEnabled, lambda on: self._set("ClaudeEnabled", on, refresh=True)))
        provider_menu.append(self._check("Codex", self.settings.CodexEnabled, lambda on: self._set("CodexEnabled", on, refresh=True)))
        providers.set_submenu(provider_menu)
        menu.append(providers)

        menu.append(self._check(text("show_countdown"), self.settings.ShowResetCountdown, lambda on: self._set("ShowResetCountdown", on)))
        menu.append(self._check(text("notifications"), self.settings.NotificationsEnabled, lambda on: self._set("NotificationsEnabled", on)))
        menu.append(self._alert_thresholds_menu())
        menu.append(self._history_menu())
        menu.append(self._check(text("renew_claude"), self.settings.RenewClaudeTokenWithCli, lambda on: self._set("RenewClaudeTokenWithCli", on)))
        menu.append(self._radio_submenu(text("language"), [
            (text("language_system"), AppLanguage.SYSTEM),
            ("English", AppLanguage.ENGLISH),
            ("한국어", AppLanguage.KOREAN),
        ], self.settings.Language, lambda value: self._set("Language", value)))
        menu.append(self._check(text("launch_at_login"), self.settings.LaunchAtLogin, self._set_autostart))
        menu.append(self._check(text("check_for_updates"), self.settings.CheckForUpdates, self._set_check_for_updates))

        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self._diagnostics_menu())
        folder = Gtk.MenuItem(label=text("open_data_folder"))
        folder.connect("activate", lambda *_: self._open_folder())
        menu.append(folder)
        info(f"Tokki {VERSION}")
        quit_item = Gtk.MenuItem(label=text("quit"))
        quit_item.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _append_pause_controls(self, menu: Gtk.Menu, now: datetime) -> None:
        text = self.text
        until = paused_until(self.settings)
        if pause.is_paused(until, now):
            left = text("pause_until_resumed") if pause.is_indefinite(until, now) else pause.duration_text(pause.remaining(until, now).total_seconds())
            item = Gtk.MenuItem(label=f"{text('updates_paused')}: {left}")
            item.set_sensitive(False)
            menu.append(item)
            resume = Gtk.MenuItem(label=text("resume_now"))
            resume.connect("activate", lambda *_: self._pause(None, resume=True))
            menu.append(resume)
        root = Gtk.MenuItem(label=text("pause_polling"))
        submenu = Gtk.Menu()
        for label, duration in ((text("pause_1h"), timedelta(hours=1)), (text("pause_3h"), timedelta(hours=3)), (text("pause_until_resumed"), None)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _w, d=duration: self._pause(d))
            submenu.append(item)
        root.set_submenu(submenu)
        menu.append(root)

    def _pause(self, duration: timedelta | None, resume: bool = False) -> None:
        if resume:
            self.settings.PollPausedUntil = None
        else:
            self.settings.PollPausedUntil = pause.pause_until(duration, _now()).isoformat()
        self.save_settings()
        GLib.idle_add(lambda: (self.render(), False)[1])
        if resume:
            GLib.idle_add(self.refresh)

    def _history_menu(self) -> Gtk.MenuItem:
        text = self.text
        root = Gtk.MenuItem(label=text("history"))
        submenu = Gtk.Menu()

        def info(label: str) -> None:
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(False)
            submenu.append(item)

        info(history.trend_summary(self.history, self.snapshot, text) if self.snapshot else text("not_enough_history"))
        for provider in (Provider.CLAUDE, Provider.CODEX):
            window = display.preferred_forecast_window(self.snapshot.usage(provider)) if self.snapshot else ForecastWindow.FIVE_HOUR
            line = history.sparkline_text(history.sparkline_series(self.history, provider, window))
            if line:
                info(f"{provider.display_name} {forecast.short_label(window)} {line}")
        submenu.append(Gtk.SeparatorMenuItem())
        submenu.append(self._check(text("show_forecast"), self.settings.ShowForecast, lambda on: self._set("ShowForecast", on)))
        submenu.append(self._radio_submenu(text("history_retention"), [
            (text("days", n=d), d) for d in HISTORY_RETENTION_DAYS
        ], self.settings.HistoryRetentionDays, lambda v: self._set("HistoryRetentionDays", v)))
        export = Gtk.MenuItem(label=text("export_history_csv"))
        export.connect("activate", lambda *_: GLib.idle_add(self._export_csv))
        submenu.append(export)
        root.set_submenu(submenu)
        return root

    def _diagnostics_menu(self) -> Gtk.MenuItem:
        text = self.text
        root = Gtk.MenuItem(label=text("diagnostics"))
        submenu = Gtk.Menu()
        copy = Gtk.MenuItem(label=text("copy_diagnostics"))
        copy.connect("activate", lambda *_: self._copy_diagnostics())
        submenu.append(copy)
        submenu.append(Gtk.SeparatorMenuItem())
        for label, target in ((text("open_claude_credentials"), paths.claude_credentials_path()), (text("open_codex_auth"), paths.codex_auth_path())):
            item = Gtk.MenuItem(label=label)
            # The folder, not the file: opening the file would show the token.
            item.connect("activate", lambda _w, t=target: self._open(str(t.parent)))
            item.set_sensitive(target.parent.is_dir())
            submenu.append(item)
        root.set_submenu(submenu)
        return root

    def diagnostics_text(self) -> str:
        return diagnostics.diagnostics_text(
            self.settings, self.history, self.snapshot, self.last_success,
            self.client.cli_refresher.last_attempt if self.client.cli_refresher else None, _now())

    def _copy_diagnostics(self) -> None:
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self.diagnostics_text(), -1)
        if Notify is not None:
            try:
                Notify.Notification.new("Tokki", self.text("diagnostics_copied"), "tokki").show()
            except GLib.Error:
                pass

    def _export_csv(self) -> bool:
        text = self.text
        dialog = Gtk.FileChooserDialog(title=text("export_history_csv").rstrip(".…"), action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(text("cancel"), Gtk.ResponseType.CANCEL, text("save"), Gtk.ResponseType.ACCEPT)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(f"tokki-history-{_now().astimezone():%Y%m%d}.csv")
        dialog.set_current_folder(str(Path.home()))
        try:
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                target = Path(dialog.get_filename())
                target.write_text(self.history_store.csv_string(), encoding="utf-8")
        except OSError as error:
            print(f"tokki: could not export history: {error}", file=sys.stderr)
        finally:
            dialog.destroy()
        return False

    def _alert_thresholds_menu(self) -> Gtk.MenuItem:
        text = self.text

        def percent(value: int) -> str:
            return text("disabled") if value == 0 else f"{value}%"

        parent = Gtk.MenuItem(label=text("alert_thresholds"))
        submenu = Gtk.Menu()
        submenu.append(self._radio_submenu(text("five_hour_threshold"), [
            (percent(v), v) for v in FIVE_HOUR_THRESHOLDS
        ], self.settings.FiveHourAlertThreshold, lambda v: self._set("FiveHourAlertThreshold", v)))
        submenu.append(self._radio_submenu(text("seven_day_threshold"), [
            (percent(v), v) for v in SEVEN_DAY_THRESHOLDS
        ], self.settings.SevenDayAlertThreshold, lambda v: self._set("SevenDayAlertThreshold", v)))
        submenu.append(self._radio_submenu(text("reset_alert_minutes"), [
            (text("disabled") if v == 0 else text("minutes_before", n=v), v) for v in RESET_ALERT_MINUTES
        ], self.settings.ResetAlertMinutes, lambda v: self._set("ResetAlertMinutes", v)))
        submenu.append(self._check(text("depletion_alert_toggle"), self.settings.DepletionAlertEnabled, lambda on: self._set("DepletionAlertEnabled", on)))
        parent.set_submenu(submenu)
        parent.set_sensitive(self.settings.NotificationsEnabled)
        return parent

    def _check(self, label: str, active: bool, on_change) -> Gtk.CheckMenuItem:
        item = Gtk.CheckMenuItem(label=label)
        item.set_active(active)
        item.connect("toggled", lambda widget: on_change(widget.get_active()))
        return item

    def _radio_submenu(self, label: str, choices, current, on_change) -> Gtk.MenuItem:
        parent = Gtk.MenuItem(label=label)
        submenu = Gtk.Menu()
        group = None
        for title, value in choices:
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, title)
            group = item
            item.set_active(value == current)
            item.connect("toggled", lambda widget, v=value: widget.get_active() and on_change(v))
            submenu.append(item)
        parent.set_submenu(submenu)
        return parent

    # --- setting changes ---------------------------------------------------------------

    def _set(self, key: str, value, refresh: bool = False) -> None:
        if getattr(self.settings, key) == value:
            return
        setattr(self.settings, key, value)
        self.save_settings()
        # Rebuild after GTK finishes delivering this activation.
        GLib.idle_add(lambda: (self.render(), False)[1])
        if refresh:
            GLib.idle_add(self.refresh)

    def _set_interval(self, seconds: int) -> None:
        if self.settings.RefreshIntervalSeconds == seconds:
            return
        self._set("RefreshIntervalSeconds", seconds)
        self.schedule_polling()

    def _set_autostart(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except OSError as error:
            print(f"tokki: could not change autostart: {error}", file=sys.stderr)
        self._set("LaunchAtLogin", autostart.is_enabled())

    def _set_check_for_updates(self, enabled: bool) -> None:
        if not enabled:
            self.available_update = None
        self._set("CheckForUpdates", enabled)
        if enabled:
            self.next_update_check_at = None
            GLib.idle_add(lambda: (self.check_for_updates_if_due(), False)[1])

    def _open(self, target: str) -> None:
        try:
            subprocess.Popen(["xdg-open", target], start_new_session=True)
        except OSError as error:
            print(f"tokki: could not open {target}: {error}", file=sys.stderr)

    def _open_folder(self) -> None:
        folder = paths.app_data_directory()
        folder.mkdir(parents=True, exist_ok=True)
        self._open(str(folder))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        print(f"Tokki {VERSION}")
        return 0
    instance = SingleInstance(paths.app_data_directory() / "tokki.lock")
    if not instance.acquired:
        print("tokki: already running", file=sys.stderr)
        return 0
    os.environ.setdefault("NO_AT_BRIDGE", "1")
    TokkiApp()
    Gtk.main()
    return 0
