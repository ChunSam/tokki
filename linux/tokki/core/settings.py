"""Mirrors SettingsStore.cs. Key names match the Windows AppSettings JSON so the
settings stay 1:1 across ports; keys this port does not use yet are kept as read
so a later version finds them."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from datetime import datetime
from typing import Optional

from . import paths
from .models import AppLanguage, DisplayMode, ProviderLabelStyle

MINIMUM_REFRESH_SECONDS = 60


@dataclass
class AppSettings:
    DisplayMode: DisplayMode = DisplayMode.LOWEST_REMAINING
    ProviderLabelStyle: ProviderLabelStyle = ProviderLabelStyle.ABBREVIATION
    RefreshIntervalSeconds: int = 300
    ClaudeEnabled: bool = True
    CodexEnabled: bool = True
    LaunchAtLogin: bool = False
    Language: AppLanguage = AppLanguage.SYSTEM
    NotificationsEnabled: bool = False
    FiveHourAlertThreshold: int = 20
    SevenDayAlertThreshold: int = 10
    ResetAlertMinutes: int = 10
    StaleToleranceHours: int = 12
    ShowResetCountdown: bool = True
    RenewClaudeTokenWithCli: bool = True
    # Ask GitHub once a day whether a newer release is out.
    CheckForUpdates: bool = True
    # ISO 8601 instant polling resumes; None is not paused. Read through
    # paused_until() so a malformed value means "not paused".
    PollPausedUntil: Optional[str] = None
    extra: dict = field(default_factory=dict, repr=False)


_ENUMS = {"DisplayMode": DisplayMode, "ProviderLabelStyle": ProviderLabelStyle, "Language": AppLanguage}


def _known_fields():
    return [f for f in fields(AppSettings) if f.name != "extra"]


def from_dict(raw: dict) -> AppSettings:
    settings = AppSettings()
    known = {f.name: f for f in _known_fields()}
    for key, value in raw.items():
        if key not in known:
            settings.extra[key] = value
            continue
        default = getattr(settings, key)
        try:
            if key in _ENUMS:
                value = _ENUMS[key](value)
            elif key == "PollPausedUntil":
                if value is not None and not isinstance(value, str):
                    continue
            elif isinstance(default, bool):
                if not isinstance(value, bool):
                    continue
            elif isinstance(default, int):
                if isinstance(value, bool) or not isinstance(value, int):
                    continue
        except ValueError:
            continue
        setattr(settings, key, value)
    # Never poll faster than the floor, whatever an older file says.
    settings.RefreshIntervalSeconds = max(MINIMUM_REFRESH_SECONDS, settings.RefreshIntervalSeconds)
    return settings


def paused_until(settings: AppSettings) -> datetime | None:
    from .parser import parse_iso
    return parse_iso(settings.PollPausedUntil) if settings.PollPausedUntil else None


def to_dict(settings: AppSettings) -> dict:
    out = dict(settings.extra)
    for f in _known_fields():
        value = getattr(settings, f.name)
        out[f.name] = value.value if f.name in _ENUMS else value
    return out


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or paths.settings_path()

    def load(self) -> AppSettings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return from_dict(raw) if isinstance(raw, dict) else AppSettings()
        except Exception:
            return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(to_dict(settings), indent=2), encoding="utf-8")
        os.replace(temp, self.path)
