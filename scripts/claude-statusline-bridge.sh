#!/bin/sh
# Tokki status-line bridge (macOS / Linux).
#
# Claude Code's status line is handed, on stdin, the 5-hour and 7-day usage
# percentages for the account. Tokki cannot ask for those itself once the
# access token Claude Code stored has lapsed, because only the CLI refreshes it.
# This script records just those numbers to a local file that Tokki
# reads, and then runs whatever status-line command you already had, with the
# same stdin, so your status line keeps printing exactly what it printed before.
#
# Install by pointing `statusLine.command` in ~/.claude/settings.json at this
# script, with your existing command after `--`:
#
#   "statusLine": {
#     "type": "command",
#     "command": "/path/to/claude-statusline-bridge.sh -- node /your/statusline.js"
#   }
#
# With no command after `--` the bridge prints nothing, which is a status line
# that shows only Claude Code's own footer badges.
#
# What is written (and nothing else): the two usage percentages, the instant each
# window resets, and two timestamps. No token, no session id, no transcript path,
# no prompt text — the rest of the payload is read and dropped. The file is the
# caller's own, mode 0600.
#
# Override the destination with TOKEN_TRACKER_STATUSLINE_FILE (used by the tests).
# Set TOKEN_TRACKER_STATUSLINE_DEBUG=1 to have recording failures explain
# themselves on stderr instead of staying silent. The status line reads stdout,
# so this never changes what it shows.
#
# Requires python3 (or python) for JSON parsing. Without one the bridge records
# nothing and still runs your command, so the status line never breaks.

set -u

# Everything after the first `--` is the command to run; without a `--`, every
# argument is. Supporting both means a pasted command works either way.
if [ "${1:-}" = "--" ]; then
    shift
fi

PAYLOAD=$(cat)

# The folder keeps the app's pre-Tokki name; see AppPaths.cacheDirectory.
# Linux keeps it under XDG_CONFIG_HOME, where the Linux app reads (linux/tokki/core/paths.py).
if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
    DEFAULT_OUT="$HOME/Library/Application Support/Token Tracker/claude-statusline.json"
else
    case "${XDG_CONFIG_HOME:-}" in
        /*) CONFIG_HOME=$XDG_CONFIG_HOME ;;
        *) CONFIG_HOME="$HOME/.config" ;;
    esac
    DEFAULT_OUT="$CONFIG_HOME/Token Tracker/claude-statusline.json"
fi
OUT=${TOKEN_TRACKER_STATUSLINE_FILE:-"$DEFAULT_OUT"}

PYTHON=$(command -v python3 || command -v python || true)
# Silence is right for a status line, but a bridge that quietly does nothing
# leaves whoever installed it with no signal at all. Opt in to the reason.
if [ -n "${TOKEN_TRACKER_STATUSLINE_DEBUG:-}" ]; then
    RECORDER_STDERR=/dev/stderr
else
    RECORDER_STDERR=/dev/null
fi

if [ -n "$PYTHON" ]; then
    # Recording must never break the status line: no stdout, and no failure
    # escapes. Worst case the file is left as it was.
    printf '%s' "$PAYLOAD" | "$PYTHON" -c '
import json
import os
import stat
import sys
import tempfile
import time

OUT = sys.argv[1]
NOW = int(time.time())
VERSION = 1


def window(raw):
    """used_percentage + resets_at, or None. Both are required: without the
    reset instant there is no way to tell a live window from one that has
    rolled over, and Claude Code drops a window once its reset passes."""
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percentage")
    resets = raw.get("resets_at")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    if isinstance(resets, bool) or not isinstance(resets, (int, float)):
        return None
    if used != used or resets != resets:  # NaN
        return None
    if resets <= NOW:
        return None
    return {"used_percentage": float(used), "resets_at": int(resets)}


def stored():
    try:
        with open(OUT, "rb") as handle:
            record = json.load(handle)
    except Exception:
        return None
    if not isinstance(record, dict) or record.get("version") != VERSION:
        return None
    return record


def merge(old, new):
    """(window, is_new_measurement).

    A status-line command re-runs on the CLI s own schedule, so an identical
    payload is a replay, not a new measurement — the whole point of this file is
    that a replay must not look fresh. Within one window instance
    used_percentage only climbs, so a lower value is an older reading (a second
    session rendering its own slightly staler copy) and must not overwrite a
    newer one. A different resets_at is a different window instance: always new.
    """
    if new is None:
        return old, False
    if old is None:
        return new, True
    if old.get("resets_at") != new.get("resets_at"):
        return new, True
    if new["used_percentage"] > old.get("used_percentage", -1):
        return new, True
    return old, False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict):
        return

    limits = payload.get("rate_limits")
    limits = limits if isinstance(limits, dict) else {}
    record = stored()
    old = record.get("rate_limits") if record else {}
    old = old if isinstance(old, dict) else {}

    windows = {}
    advanced = False
    for name in ("five_hour", "seven_day"):
        merged, is_new = merge(window(old.get(name)), window(limits.get(name)))
        advanced = advanced or is_new
        if merged is not None:
            windows[name] = merged

    if not windows:
        return

    measured = record.get("measured_at") if record else None
    if advanced or not isinstance(measured, (int, float)) or isinstance(measured, bool):
        measured = NOW

    # Nothing moved. The only field left to refresh is written_at, which exists
    # for diagnostics ("the bridge is running, the data is just old") and never
    # for freshness, so once a minute is plenty — a status line can re-render
    # several times a second, and this is not worth that many writes.
    written = record.get("written_at") if record else None
    if (not advanced
            and isinstance(written, (int, float))
            and not isinstance(written, bool)
            and 0 <= NOW - written < 60):
        return

    out = {
        "version": VERSION,
        "provider": "claude",
        "measured_at": int(measured),
        "written_at": NOW,
        "rate_limits": windows,
    }

    directory = os.path.dirname(OUT) or "."
    try:
        os.makedirs(directory, 0o700)
    except OSError:
        pass
    handle, temp = tempfile.mkstemp(dir=directory, prefix=".claude-statusline-", suffix=".tmp")
    try:
        os.write(handle, json.dumps(out, indent=2).encode("utf-8") + b"\n")
    finally:
        os.close(handle)
    os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
    # Rename, so a reader never sees a half-written file and two sessions
    # finishing at once cannot interleave. The loser simply loses its update.
    os.replace(temp, OUT)


main()
' "$OUT" >/dev/null 2>"$RECORDER_STDERR" || true
fi

# Your status line, unchanged: same stdin, its stdout, its exit status.
if [ "$#" -gt 0 ]; then
    printf '%s' "$PAYLOAD" | "$@"
fi
