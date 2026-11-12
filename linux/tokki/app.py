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
from gi.repository import GLib, Gtk  # noqa: E402

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

from . import VERSION, autostart
from .core import alerts, display, paths
from .core.cache import CacheStore, apply_cache_policy
from .core.cli_refresh import ClaudeCliRefresher
from .core.client import UsageClient
from .core.models import AppLanguage, DisplayMode, Provider, ProviderLabelStyle, ProviderUsage, UsageSnapshot, UsageSource
from .core.settings import SettingsStore
from .i18n import Localizer

RESOURCES = Path(__file__).resolve().parent / "resources"
REFRESH_CHOICES = (60, 120, 300, 600, 900)
COUNTDOWN_TICK_SECONDS = 30


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
        self.client = UsageClient(cli_refresher=ClaudeCliRefresher())
        self.snapshot: UsageSnapshot | None = self.cache_store.load(self._stale_tolerance(), _now())
        self.delivered_alerts: set[str] = set()
        self.refreshing = False
        self.poll_source: int | None = None
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
        return True

    def refresh(self, *_args) -> bool:
        if self.refreshing:
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
        if merged.claude.is_available or merged.codex.is_available:
            self.cache_store.save(merged)
        self.notify(merged, now)
        self.render()
        return False

    # --- notifications -----------------------------------------------------------

    def notify(self, snapshot: UsageSnapshot, now: datetime) -> None:
        active = alerts.candidates(
            snapshot, self.settings.NotificationsEnabled,
            self.settings.FiveHourAlertThreshold, self.settings.SevenDayAlertThreshold, now,
        )
        fresh, self.delivered_alerts = alerts.to_deliver(active, self.delivered_alerts)
        if Notify is None:
            return
        for candidate in fresh:
            try:
                Notify.Notification.new(self.text(candidate.title_key), candidate.body, "tokki").show()
            except GLib.Error as error:
                print(f"tokki: notification failed: {error}", file=sys.stderr)

    # --- rendering -----------------------------------------------------------------

    def render(self) -> None:
        self.render_label()
        self.indicator.set_menu(self.build_menu())

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
        if usage.source is UsageSource.STATUS_LINE:
            lines.append("    " + text("source_statusline"))
        if usage.error:
            lines.append("    " + usage.error[:90])
        return lines

    def build_menu(self) -> Gtk.Menu:
        text = self.text
        menu = Gtk.Menu()
        now = _now()

        def info(label: str) -> None:
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(False)
            menu.append(item)

        if self.snapshot is None:
            info(text("loading"))
        else:
            for provider, enabled in ((Provider.CLAUDE, self.settings.ClaudeEnabled), (Provider.CODEX, self.settings.CodexEnabled)):
                for line in self._provider_lines(self.snapshot.usage(provider), enabled, now):
                    info(line)
            local = self.snapshot.updated_at.astimezone().strftime("%H:%M")
            info(text("updated_at", time=local))

        menu.append(Gtk.SeparatorMenuItem())
        refresh = Gtk.MenuItem(label=text("refresh_now"))
        refresh.connect("activate", self.refresh)
        menu.append(refresh)
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
        menu.append(self._check(text("renew_claude"), self.settings.RenewClaudeTokenWithCli, lambda on: self._set("RenewClaudeTokenWithCli", on)))
        menu.append(self._radio_submenu(text("language"), [
            (text("language_system"), AppLanguage.SYSTEM),
            ("English", AppLanguage.ENGLISH),
            ("한국어", AppLanguage.KOREAN),
        ], self.settings.Language, lambda value: self._set("Language", value)))
        menu.append(self._check(text("launch_at_login"), self.settings.LaunchAtLogin, self._set_autostart))

        menu.append(Gtk.SeparatorMenuItem())
        folder = Gtk.MenuItem(label=text("open_data_folder"))
        folder.connect("activate", lambda *_: self._open_folder())
        menu.append(folder)
        info(f"Tokki {VERSION}")
        quit_item = Gtk.MenuItem(label=text("quit"))
        quit_item.connect("activate", lambda *_: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

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

    def _open_folder(self) -> None:
        folder = paths.app_data_directory()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(folder)], start_new_session=True)
        except OSError as error:
            print(f"tokki: could not open {folder}: {error}", file=sys.stderr)


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
