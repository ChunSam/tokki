"""Launch at login through the freedesktop autostart folder."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from .core import paths


def launch_command() -> str:
    # The .deb installs /usr/bin/tokki; a source checkout runs the module.
    installed = Path("/usr/bin/tokki")
    if installed.exists():
        return str(installed)
    package_root = Path(__file__).resolve().parent.parent
    return f"env PYTHONPATH={shlex.quote(str(package_root))} {shlex.quote(sys.executable)} -m tokki"


def desktop_entry(command: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Tokki\n"
        "Comment=Claude Code and Codex usage limits in the tray\n"
        f"Exec={command}\n"
        "Icon=tokki\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def is_enabled(path: Path | None = None) -> bool:
    return (path or paths.autostart_path()).is_file()


def set_enabled(enabled: bool, path: Path | None = None) -> None:
    target = path or paths.autostart_path()
    if enabled:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(desktop_entry(launch_command()), encoding="utf-8")
        os.replace(temp, target)
    else:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
