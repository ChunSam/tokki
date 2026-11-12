"""Renders the details window's two tabs to PNGs and fails on any drawing error.

Needs a display (CI runs it under xvfb-run) and the packaged GTK bindings,
python3-gi-cairo included: without it every custom bar and chart fails to draw,
which no unit test can see.

    xvfb-run -a python3 linux/tests/gui_smoke.py OUT_DIR
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import cairo  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402

from tokki.core import history  # noqa: E402
from tokki.core.cost import ClaudeExtraUsage, CostAmount  # noqa: E402
from tokki.core.models import AppLanguage, Provider, ProviderUsage, UsageSnapshot, UsageSource  # noqa: E402
from tokki.core.settings import AppSettings  # noqa: E402
from tokki.i18n import Localizer  # noqa: E402
from tokki.window import TokkiWindow  # noqa: E402

ERRORS: list[str] = []


def _record_error(*args):
    ERRORS.append(" ".join(str(a) for a in args))


def _hook(kind, value, tb):
    # PyGObject reports exceptions raised in signal handlers, draw included, here.
    _record_error(kind.__name__, value)
    sys.__excepthook__(kind, value, tb)


sys.excepthook = _hook


class FakeApp:
    """The slice of TokkiApp the window reads and calls."""

    def __init__(self, language: AppLanguage):
        from decimal import Decimal
        now = datetime.now(timezone.utc)
        self.settings = AppSettings()
        self.settings.Language = language
        self.changes: list[tuple[str, object]] = []
        claude = ProviderUsage(Provider.CLAUDE, 34, 72, now + timedelta(hours=2), now + timedelta(days=3), UsageSource.API,
                               None, "pro", now,
                               extra_usage=ClaudeExtraUsage(CostAmount(Decimal("4.10")), CostAmount(Decimal("5")), "normal"))
        codex = ProviderUsage(Provider.CODEX, None, 8, None, now + timedelta(days=1), UsageSource.STALE_CACHE,
                              "Credentials expired for Codex API", "plus", now - timedelta(hours=1))
        self.snapshot = UsageSnapshot(claude, codex, now)
        self.history = []
        for i in range(144):
            at = now - timedelta(minutes=10 * (144 - i))
            self.history.append(history.HistoryEntry(at, UsageSnapshot(
                claude.with_(remaining_5h=max(5, 100 - (i % 30) * 3), updated_at=at),
                codex.with_(remaining_7d=20 - i // 12, source=UsageSource.API, updated_at=at), at)))

    @property
    def text(self):
        return Localizer(self.settings.Language).text

    def _set(self, key, value, refresh=False):
        self.changes.append((key, value))
        setattr(self.settings, key, value)

    def _set_interval(self, seconds):
        self._set("RefreshIntervalSeconds", seconds)

    def _set_check_for_updates(self, on):
        self._set("CheckForUpdates", on)

    def _set_autostart(self, on):
        self._set("LaunchAtLogin", on)

    def refresh_now(self):
        self.changes.append(("refresh", None))


def pump():
    for _ in range(50):
        while Gtk.events_pending():
            Gtk.main_iteration()
        GLib.usleep(10_000)


def shoot(widget: Gtk.Widget, path: Path) -> None:
    alloc = widget.get_allocation()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, alloc.width, alloc.height)
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    widget.draw(cr)
    surface.write_to_png(str(path))


def main(out_dir: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for language, suffix in ((AppLanguage.KOREAN, "ko"), (AppLanguage.ENGLISH, "en")):
        app = FakeApp(language)
        window = TokkiWindow(app)
        window.resize(460, 640)
        window.show_all()
        pump()
        shoot(window.notebook, out / f"overview-{suffix}.png")
        window.notebook.set_current_page(1)
        pump()
        shoot(window.notebook, out / f"settings-{suffix}.png")
        window.destroy()
        pump()
    if ERRORS:
        print("drawing errors:", *ERRORS, sep="\n  ", file=sys.stderr)
        return 1
    print("gui smoke passed:", ", ".join(sorted(p.name for p in out.glob("*.png"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "gui-smoke"))
