"""The details window: an overview tab with each provider's bars, pace mark and
24-hour chart, and a settings tab for what the tray menu cannot hold well.

The tray menu stays the primary surface, as on every Ubuntu indicator. Wayland
does not let an app place its window, so this opens wherever the shell puts it.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from gi.repository import Gtk

from . import autostart
from .core import cost, display, forecast, history, issues, paths
from .core.models import AppLanguage, DisplayMode, ForecastWindow, Provider, ProviderLabelStyle, ProviderUsage, UsageSource

REFRESH_CHOICES = (60, 120, 300, 600, 900, 1800)
CHART_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bar_color(remaining: int | None):
    if remaining is None:
        return (0.6, 0.6, 0.6)
    if remaining <= 10:
        return (0.86, 0.24, 0.22)
    if remaining <= 30:
        return (0.96, 0.6, 0.1)
    return (0.25, 0.65, 0.35)


class UsageBar(Gtk.DrawingArea):
    """Remaining share of one window, with a tick where an even pace would stand."""

    def __init__(self, remaining: int | None, expected: int | None, muted: bool):
        super().__init__()
        self.remaining, self.expected, self.muted = remaining, expected, muted
        self.set_size_request(220, 10)
        self.set_hexpand(True)
        self.connect("draw", self._draw)

    def _draw(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        radius = height / 2

        def rounded(x, w):
            cr.new_sub_path()
            cr.arc(x + w - radius, radius, radius, -math.pi / 2, math.pi / 2)
            cr.arc(x + radius, radius, radius, math.pi / 2, 3 * math.pi / 2)
            cr.close_path()

        rounded(0, width)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
        cr.fill()
        if self.remaining is not None and self.remaining > 0:
            fill = max(height, width * self.remaining / 100)
            rounded(0, fill)
            r, g, b = _bar_color(self.remaining)
            cr.set_source_rgba(r, g, b, 0.45 if self.muted else 1.0)
            cr.fill()
        if self.expected is not None:
            x = width * self.expected / 100
            cr.set_source_rgba(0.1, 0.1, 0.1, 0.8)
            cr.set_line_width(2)
            cr.move_to(x, 0)
            cr.line_to(x, height)
            cr.stroke()
        return False


class HistoryChart(Gtk.DrawingArea):
    """Remaining % over the last day, measurements only."""

    def __init__(self, points: list[tuple[datetime, int]], now: datetime, empty_text: str):
        super().__init__()
        self.points, self.now, self.empty_text = points, now, empty_text
        self.set_size_request(300, 70)
        self.set_hexpand(True)
        self.connect("draw", self._draw)

    def _draw(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        style = widget.get_style_context()
        fg = style.get_color(Gtk.StateFlags.NORMAL)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.15)
        cr.set_line_width(1)
        for level in (0, 50, 100):
            y = height - 2 - (height - 4) * level / 100
            cr.move_to(0, y)
            cr.line_to(width, y)
            cr.stroke()
        if len(self.points) < 2:
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            cr.move_to(6, height / 2 + 4)
            cr.show_text(self.empty_text)
            return False
        start = self.now - timedelta(hours=CHART_HOURS)
        span = (self.now - start).total_seconds()

        def xy(point):
            at, value = point
            x = width * max(0.0, (at - start).total_seconds()) / span
            y = height - 2 - (height - 4) * value / 100
            return x, y

        cr.set_source_rgba(0.2, 0.5, 0.9, 1)
        cr.set_line_width(2)
        cr.move_to(*xy(self.points[0]))
        for point in self.points[1:]:
            cr.line_to(*xy(point))
        cr.stroke()
        return False


class TokkiWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Tokki")
        self.app = app
        self.set_default_size(460, 560)
        self.set_icon_name("tokki")
        self.connect("delete-event", lambda *_: self.hide() or True)
        self.notebook = Gtk.Notebook()
        self.add(self.notebook)
        self.rebuild()

    @property
    def text(self):
        return self.app.text

    # --- building ------------------------------------------------------------------

    def rebuild(self) -> None:
        """Both tabs, for a language change; the overview alone refreshes on polls."""
        current = self.notebook.get_current_page()
        for child in self.notebook.get_children():
            self.notebook.remove(child)
        self.overview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        overview = Gtk.ScrolledWindow()
        overview.add(self.overview_box)
        self.notebook.append_page(overview, Gtk.Label(label=self.text("overview")))
        settings = Gtk.ScrolledWindow()
        settings.add(self._settings_page())
        self.notebook.append_page(settings, Gtk.Label(label=self.text("settings")))
        self.refresh_overview()
        self.notebook.show_all()
        if current >= 0:
            self.notebook.set_current_page(current)

    def refresh_overview(self) -> None:
        for child in self.overview_box.get_children():
            self.overview_box.remove(child)
        snapshot = self.app.snapshot
        now = _now()
        if snapshot is None:
            self.overview_box.pack_start(Gtk.Label(label=self.text("loading")), False, False, 0)
        else:
            for provider, enabled in ((Provider.CLAUDE, self.app.settings.ClaudeEnabled), (Provider.CODEX, self.app.settings.CodexEnabled)):
                if enabled:
                    self.overview_box.pack_start(self._card(snapshot.usage(provider), now), False, False, 0)
        footer = Gtk.Box(spacing=8)
        updated = self.text("updated_at", time=snapshot.updated_at.astimezone().strftime("%H:%M")) if snapshot else ""
        footer.pack_start(Gtk.Label(label=updated, xalign=0), True, True, 0)
        refresh = Gtk.Button(label=self.text("refresh_now"))
        refresh.connect("clicked", lambda *_: self.app.refresh_now())
        footer.pack_end(refresh, False, False, 0)
        self.overview_box.pack_end(footer, False, False, 0)
        self.overview_box.show_all()

    def _card(self, usage: ProviderUsage, now: datetime) -> Gtk.Widget:
        text = self.text
        frame = Gtk.Frame()
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{usage.display_name}</b>" + (f"  <small>{usage.plan}</small>" if usage.plan else ""))
        frame.set_label_widget(title)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
        frame.add(box)
        cached = usage.source is UsageSource.STALE_CACHE
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        for row, (window, remaining, reset_at) in enumerate((
            (ForecastWindow.FIVE_HOUR, usage.remaining_5h, usage.reset_at_5h),
            (ForecastWindow.SEVEN_DAY, usage.remaining_7d, usage.reset_at_7d),
        )):
            pace = forecast.pace_reading(usage, window)
            reset_text = text("source_cache") if cached else display.format_window_reset(remaining, reset_at, now, text, usage.is_available)
            grid.attach(Gtk.Label(label=forecast.short_label(window), xalign=0), 0, row, 1, 1)
            grid.attach(UsageBar(remaining, pace.expected_remaining_percent if pace else None, cached), 1, row, 1, 1)
            grid.attach(Gtk.Label(label=display.format_percent(remaining), xalign=1, width_chars=5), 2, row, 1, 1)
            grid.attach(Gtk.Label(label=reset_text, xalign=0, width_chars=10), 3, row, 1, 1)
        box.pack_start(grid, False, False, 0)

        lines = []
        found = issues.issue(usage, text)
        if found.kind is not issues.IssueKind.OK:
            lines.append(found.title + (f" — {found.recovery}" if found.recovery else ""))
            stale = issues.stale_line(usage.provider, found, issues.CredentialSource.detect(paths.claude_credentials_path()), now, text)
            if stale:
                lines.append(stale)
        if self.app.settings.ShowForecast:
            window = display.preferred_forecast_window(usage)
            reset_at = usage.reset_at_5h if window is ForecastWindow.FIVE_HOUR else usage.reset_at_7d
            for line in (forecast.forecast_menu_line(forecast.forecast(self.app.history, usage.provider, window, reset_at, now), window, text),
                         forecast.pace_menu_line(usage, text)):
                if line:
                    lines.append(line)
        money = cost.subscription_line(usage, text)
        if money:
            lines.append(f"{money.label}: {money.value}")
        for line in lines:
            label = Gtk.Label(label=line, xalign=0)
            label.set_line_wrap(True)
            label.set_max_width_chars(60)
            box.pack_start(label, False, False, 0)

        window = display.preferred_forecast_window(usage)
        cutoff = now - timedelta(hours=CHART_HOURS)
        points = [(e.recorded_at, v) for e in self.app.history
                  if e.recorded_at >= cutoff and (v := history.remaining(e.snapshot.usage(usage.provider), window)) is not None]
        caption = Gtk.Label(xalign=0)
        caption.set_markup(f"<small>{text('history_trend').rstrip(':')} · {forecast.short_label(window)}</small>")
        box.pack_start(caption, False, False, 0)
        box.pack_start(HistoryChart(points, now, text("not_enough_history")), False, False, 0)
        return frame

    # --- settings ----------------------------------------------------------------------

    def _settings_page(self) -> Gtk.Widget:
        text = self.text
        s = self.app.settings
        grid = Gtk.Grid(column_spacing=12, row_spacing=8, margin=16)
        row = [0]

        def header(label: str) -> None:
            widget = Gtk.Label(xalign=0)
            widget.set_markup(f"<b>{label}</b>")
            if row[0] > 0:
                widget.set_margin_top(10)
            grid.attach(widget, 0, row[0], 2, 1)
            row[0] += 1

        def labelled(label: str, widget: Gtk.Widget) -> None:
            grid.attach(Gtk.Label(label=label, xalign=0, hexpand=True), 0, row[0], 1, 1)
            grid.attach(widget, 1, row[0], 1, 1)
            row[0] += 1

        def check(label: str, active: bool, changed) -> Gtk.CheckButton:
            widget = Gtk.CheckButton(label=label)
            widget.set_active(active)
            widget.connect("toggled", lambda w: changed(w.get_active()))
            grid.attach(widget, 0, row[0], 2, 1)
            row[0] += 1
            return widget

        def combo(label: str, choices, current, changed) -> None:
            widget = Gtk.ComboBoxText()
            values = [value for _, value in choices]
            for title, _ in choices:
                widget.append_text(title)
            widget.set_active(values.index(current) if current in values else 0)
            widget.connect("changed", lambda w: w.get_active() >= 0 and changed(values[w.get_active()]))
            labelled(label, widget)

        def number(label: str, value: int, low: int, high: int, unit: str, changed) -> None:
            box = Gtk.Box(spacing=6)
            spin = Gtk.SpinButton.new_with_range(low, high, 1)
            spin.set_value(value)
            spin.connect("value-changed", lambda w: changed(int(w.get_value())))
            box.pack_start(spin, False, False, 0)
            box.pack_start(Gtk.Label(label=unit), False, False, 0)
            labelled(label, box)

        header(text("general"))
        combo(text("display"), [
            (text("display_lowest"), DisplayMode.LOWEST_REMAINING), (text("display_both"), DisplayMode.BOTH),
            (text("display_claude"), DisplayMode.CLAUDE_ONLY), (text("display_codex"), DisplayMode.CODEX_ONLY),
        ], s.DisplayMode, lambda v: self.app._set("DisplayMode", v))
        combo(text("label_style"), [
            (text("label_abbreviation"), ProviderLabelStyle.ABBREVIATION), (text("label_full"), ProviderLabelStyle.ICON),
        ], s.ProviderLabelStyle, lambda v: self.app._set("ProviderLabelStyle", v))
        intervals = sorted(set(REFRESH_CHOICES) | {s.RefreshIntervalSeconds})
        combo(text("refresh_interval"), [(text("minutes", n=v // 60) if v % 60 == 0 else f"{v}s", v) for v in intervals],
              s.RefreshIntervalSeconds, self.app._set_interval)
        combo(text("language"), [
            (text("language_system"), AppLanguage.SYSTEM), ("English", AppLanguage.ENGLISH), ("한국어", AppLanguage.KOREAN),
        ], s.Language, self._set_language)
        check(text("show_countdown"), s.ShowResetCountdown, lambda on: self.app._set("ShowResetCountdown", on))
        check(text("show_forecast"), s.ShowForecast, lambda on: self.app._set("ShowForecast", on))
        check(text("check_for_updates"), s.CheckForUpdates, self.app._set_check_for_updates)
        check(text("launch_at_login"), autostart.is_enabled(), self.app._set_autostart)

        header(text("providers"))
        check("Claude", s.ClaudeEnabled, lambda on: self.app._set("ClaudeEnabled", on, refresh=True))
        check(text("renew_claude"), s.RenewClaudeTokenWithCli, lambda on: self.app._set("RenewClaudeTokenWithCli", on))
        check("Codex", s.CodexEnabled, lambda on: self.app._set("CodexEnabled", on, refresh=True))

        header(text("notifications"))
        check(text("notifications_enabled"), s.NotificationsEnabled, lambda on: self.app._set("NotificationsEnabled", on))
        number(text("five_hour_threshold"), s.FiveHourAlertThreshold, 0, 100, "%", lambda v: self.app._set("FiveHourAlertThreshold", v))
        number(text("seven_day_threshold"), s.SevenDayAlertThreshold, 0, 100, "%", lambda v: self.app._set("SevenDayAlertThreshold", v))
        number(text("reset_alert_minutes"), s.ResetAlertMinutes, 0, 1440, text("unit_minutes"), lambda v: self.app._set("ResetAlertMinutes", v))
        check(text("depletion_alert_toggle"), s.DepletionAlertEnabled, lambda on: self.app._set("DepletionAlertEnabled", on))

        header(text("history"))
        number(text("history_retention"), s.HistoryRetentionDays, 1, 365, text("unit_days"), lambda v: self.app._set("HistoryRetentionDays", v))
        number(text("stale_tolerance"), s.StaleToleranceHours, 1, 48, text("unit_hours"), lambda v: self.app._set("StaleToleranceHours", v))
        hint = Gtk.Label(xalign=0)
        hint.set_markup(f"<small>{text('settings_hint')}</small>")
        hint.set_line_wrap(True)
        hint.set_margin_top(12)
        grid.attach(hint, 0, row[0], 2, 1)
        return grid

    def _set_language(self, value: AppLanguage) -> None:
        self.app._set("Language", value)
        self.set_title("Tokki")
        # Rebuild after this signal returns; the combo is about to be destroyed.
        from gi.repository import GLib
        GLib.idle_add(lambda: (self.rebuild(), False)[1])
