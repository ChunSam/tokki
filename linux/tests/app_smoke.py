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

from tokki import app  # noqa: E402
from gi.repository import GLib, Gtk  # noqa: E402


def drive(a):
    a._pause(None)
    a.render()
    a._tick()
    a.refresh_now()
    a.render()
    a._set("Language", app.AppLanguage.ENGLISH)
    a.render()
    a.open_window()
    a.render()
    a.window.hide()
    return False


a = app.TokkiApp()
GLib.timeout_add(1500, drive, a)
GLib.timeout_add(5000, Gtk.main_quit)
Gtk.main()
print("app smoke passed:", a.indicator.get_label())
