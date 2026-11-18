"""Runs the real tray app for a few seconds and drives its menu paths. Run with
G_DEBUG=fatal-criticals so any Gtk-CRITICAL, such as AppIndicator's checks on
the secondary target, aborts the run.

    G_DEBUG=fatal-criticals dbus-run-session -- xvfb-run -a python3 linux/tests/app_smoke.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# No network, no real settings: an empty home and config.
home = tempfile.mkdtemp()
os.environ["HOME"] = home
os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
# The logo label's drawings go here; keep them off a real session's.
os.environ["XDG_RUNTIME_DIR"] = os.path.join(home, "run")
os.makedirs(os.environ["XDG_RUNTIME_DIR"], mode=0o700)

from tokki import app  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402


failures = []


def drive(a):
    # GLib reports an exception raised in a callback and carries on; record it
    # so the run fails.
    try:
        _drive(a)
    except Exception as error:
        failures.append(error)
        raise
    return False


def _drive(a):
    a._pause(None)
    a.render()
    a._tick()
    a.refresh_now()
    a.render()
    a._set("Language", app.AppLanguage.ENGLISH)
    a.render()
    # The logo style draws the label into an image and empties the text label;
    # switching back removes the image and restores the text.
    a._set("ProviderLabelStyle", app.ProviderLabelStyle.ICON)
    a.render()
    assert a.label_image is not None and a.label_image.exists(), a.label_image
    assert a.indicator.get_label() in ("", None), a.indicator.get_label()
    drawn = a.label_image
    a._set("ProviderLabelStyle", app.ProviderLabelStyle.ABBREVIATION)
    a.render()
    assert a.label_image is None and not drawn.exists()
    assert a.indicator.get_label(), "text label restored"
    a.open_window()
    a.render()
    a.window.hide()
    return False


a = app.TokkiApp()
GLib.timeout_add(1500, drive, a)
GLib.timeout_add(5000, Gtk.main_quit)
Gtk.main()
if failures:
    sys.exit(f"app smoke failed: {failures[0]!r}")
print("app smoke passed:", a.indicator.get_label())
