"""Mirrors ClaudeCliRefresh.cs.

The access token lives ~8h and only Claude Code may renew it: its refresh token
is single-use, so spending it here would sign the CLI out, and Anthropic's terms
bar third-party apps from holding Claude credentials of their own. What remains
is running the unmodified CLI, which renews the stored token while it starts.
The launch runs in a folder Claude Code has never been told to trust, so it stops
at the trust prompt and never reaches hooks, plugins or a session.

On Linux the CLI needs a terminal to take its interactive path, so it gets a
pseudo-terminal nobody reads, the counterpart of the hidden console on Windows.
"""

from __future__ import annotations

import enum
import os
import pty
import select
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import paths
from .expiry import is_expired
from .parser import read_claude_credential

TIMEOUT = timedelta(seconds=15)
RETRY_AFTER_REFRESH = timedelta(minutes=5)
RETRY_AFTER_FAILURE = timedelta(minutes=30)

ARGUMENTS = (
    "--strict-mcp-config",
    "--settings", '{"remoteControlAtStartup":false,"disableAllHooks":true}',
)


class Outcome(str, enum.Enum):
    REFRESHED = "Refreshed"
    UNCHANGED = "Unchanged"
    CLI_NOT_FOUND = "CliNotFound"
    LAUNCH_FAILED = "LaunchFailed"


@dataclass(frozen=True)
class Attempt:
    attempted_at: datetime
    outcome: Outcome


def next_attempt(attempt: Attempt) -> datetime:
    wait = RETRY_AFTER_REFRESH if attempt.outcome is Outcome.REFRESHED else RETRY_AFTER_FAILURE
    return attempt.attempted_at + wait


def is_due(last: Attempt | None, now: datetime) -> bool:
    return last is None or now >= next_attempt(last)


def launch_environment(base: dict[str, str], binary_directory: str) -> dict[str, str]:
    """The app's environment minus anything pointing the CLI at other credentials
    or another config directory; the binary's directory first on PATH."""
    env = {k: v for k, v in base.items() if not k.upper().startswith(("ANTHROPIC_", "CLAUDE"))}
    path = env.get("PATH", "")
    env["PATH"] = binary_directory + os.pathsep + path if path else binary_directory
    # Renewing a token is no reason to replace the user's installation.
    env["DISABLE_AUTOUPDATER"] = "1"
    env.setdefault("TERM", "xterm-256color")
    return env


def candidate_paths(home: Path, path_variable: str | None) -> list[Path]:
    """The native installer's location first, then PATH."""
    out = [home / ".local" / "bin" / "claude"]
    for directory in (path_variable or "").split(os.pathsep):
        directory = directory.strip().strip('"')
        if directory:
            out.append(Path(directory) / "claude")
    seen, unique = set(), []
    for candidate in out:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def locate(home: Path | None = None) -> Path | None:
    for candidate in candidate_paths(home or paths.home_directory(), os.environ.get("PATH")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class ClaudeCliRefresher:
    """Locked so two overlapping refreshes cannot start two CLIs."""

    def __init__(self, home: Path | None = None, working_directory: Path | None = None):
        self._home = home
        self._workdir = working_directory or paths.claude_cli_refresh_directory()
        self._lock = threading.Lock()
        self._in_flight = False
        self.last_attempt: Attempt | None = None

    def refresh_if_due(self, now: datetime) -> bool:
        """True when the credential store was rewritten, so it is worth reading again."""
        with self._lock:
            if self._in_flight or not is_due(self.last_attempt, now):
                return False
            self._in_flight = True
        outcome = Outcome.LAUNCH_FAILED
        try:
            outcome = self._attempt()
        finally:
            with self._lock:
                self.last_attempt = Attempt(now, outcome)
                self._in_flight = False
        return outcome is Outcome.REFRESHED

    def _is_renewed(self, credentials: Path, before: float | None) -> bool:
        after = _mtime(credentials)
        if after is None or after == before:
            return False
        try:
            credential = read_claude_credential(self._home)
        except Exception:
            # Caught mid-write: not renewed yet.
            return False
        return not is_expired(credential.expires_at, datetime.now(timezone.utc))

    def _attempt(self) -> Outcome:
        binary = locate(self._home)
        if binary is None:
            return Outcome.CLI_NOT_FOUND
        credentials = paths.claude_credentials_path(self._home)
        before = _mtime(credentials)
        try:
            self._workdir.mkdir(parents=True, exist_ok=True)
            master, slave = pty.openpty()
        except OSError:
            return Outcome.LAUNCH_FAILED
        try:
            process = subprocess.Popen(
                [str(binary), *ARGUMENTS],
                cwd=self._workdir,
                env=launch_environment(dict(os.environ), str(binary.parent)),
                stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            os.close(master)
            os.close(slave)
            return Outcome.LAUNCH_FAILED
        os.close(slave)
        refreshed = False
        try:
            deadline = time.monotonic() + TIMEOUT.total_seconds()
            while time.monotonic() < deadline:
                if self._is_renewed(credentials, before):
                    refreshed = True
                    break
                if process.poll() is not None:
                    break
                # Drain the terminal so the CLI never blocks on a full buffer.
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        os.read(master, 65536)
                    except OSError:
                        pass
            refreshed = refreshed or self._is_renewed(credentials, before)
            if refreshed and process.poll() is None:
                # Let a write that is still settling finish before the process goes.
                time.sleep(1)
        finally:
            _stop(process)
            os.close(master)
        return Outcome.REFRESHED if refreshed else Outcome.UNCHANGED


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, PermissionError):
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            pass
