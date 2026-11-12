"""Mirrors windows/TokenTracker.Windows.Core/AppPaths.cs.

The folder keeps the app's old name, "Token Tracker", like the other ports, so
the status-line bridge and the docs name one place on every platform.
"""

from __future__ import annotations

import os
from pathlib import Path


def home_directory() -> Path:
    return Path.home()


def config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(configured) if configured and os.path.isabs(configured) else home_directory() / ".config"


def app_data_directory() -> Path:
    return config_home() / "Token Tracker"


def settings_path() -> Path:
    return app_data_directory() / "settings.json"


def snapshot_cache_path() -> Path:
    return app_data_directory() / "usage-cache.json"


def claude_rate_limit_state_path() -> Path:
    return app_data_directory() / "claude-rate-limit.json"


def codex_rate_limit_state_path() -> Path:
    return app_data_directory() / "codex-rate-limit.json"


def claude_statusline_path() -> Path:
    """Written by scripts/claude-statusline-bridge.sh, read here."""
    return app_data_directory() / "claude-statusline.json"


def claude_cli_refresh_directory() -> Path:
    """Where Claude Code is started to renew its token. Nothing ever answers its
    trust prompt here, so a launch stops at that prompt instead of running hooks,
    plugins or a session."""
    return app_data_directory() / "claude-cli-refresh"


def codex_auth_path(home: Path | None = None) -> Path:
    return (home or home_directory()) / ".codex" / "auth.json"


def claude_credentials_path(home: Path | None = None) -> Path:
    return (home or home_directory()) / ".claude" / ".credentials.json"


def autostart_path() -> Path:
    return config_home() / "autostart" / "tokki.desktop"
